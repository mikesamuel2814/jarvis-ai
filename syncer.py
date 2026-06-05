#!/usr/bin/env python3
"""
Jarvis Repo Syncer — watches ~/.jarvis/ for code changes and auto-commits
+ pushes to both GitHub (origin) and GitLab (gitlab) remotes.

Runs as jarvis-sync.service (persistent daemon).
Debounce: waits 45s after last change before committing, to batch rapid edits.
"""

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

JARVIS_HOME = Path.home() / ".jarvis"
LOG_FILE = JARVIS_HOME / "logs" / "syncer.log"

# File extensions that trigger a sync
WATCH_EXTENSIONS = {".py", ".sh", ".yaml", ".yml", ".json", ".txt", ".md"}

# Paths to ignore even if extension matches
IGNORE_PATHS = {
    "data", "memory", "logs", "venv", "models", "training",
    "__pycache__", ".git",
}

# Don't commit telegram.json (has bot token)
IGNORE_FILES = {"telegram.json"}

DEBOUNCE_SECONDS = 45
REMOTES = ["origin", "gitlab"]

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_FILE)),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("syncer")


def _should_ignore(path: str) -> bool:
    parts = Path(path).relative_to(JARVIS_HOME).parts
    if any(p in IGNORE_PATHS for p in parts):
        return True
    if Path(path).name in IGNORE_FILES:
        return True
    return False


def _git(args: list, cwd=JARVIS_HOME) -> tuple[int, str]:
    r = subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def _get_changed_files() -> list[str]:
    _, out = _git(["status", "--short"])
    files = []
    for line in out.splitlines():
        if line.strip():
            files.append(line.strip().split()[-1])
    return files


def _classify_change(files: list[str]) -> str:
    """Rule-based fallback: infer a conventional-commit prefix from the file list."""
    names = [Path(f).name for f in files]
    if len(files) == 1:
        stem = Path(files[0]).stem
        return f"update: {stem}"
    # Group by type
    scripts = [n for n in names if n.endswith(".py")]
    configs = [n for n in names if n.endswith((".yaml", ".yml", ".json"))]
    docs    = [n for n in names if n.endswith(".md")]
    if scripts and not configs and not docs:
        return "update: " + ", ".join(s.replace(".py", "") for s in scripts[:3])
    if configs and not scripts:
        return "config: " + ", ".join(c for c in configs[:3])
    if docs and not scripts and not configs:
        return "docs: " + ", ".join(d for d in docs[:3])
    summary = ", ".join(names[:4])
    if len(names) > 4:
        summary += f" (+{len(names) - 4} more)"
    return f"update: {summary}"


def _diff_summary(files: list[str]) -> str:
    """Build a compact human-readable summary of what changed (no raw code)."""
    lines = []
    for f in files[:6]:
        _, d = _git(["diff", "--cached", "-U0", "--", f])
        added   = [l[1:].strip() for l in d.splitlines() if l.startswith("+") and not l.startswith("+++")]
        removed = [l[1:].strip() for l in d.splitlines() if l.startswith("-") and not l.startswith("---")]
        # Extract function/class names touched
        defs = [l for l in added + removed if l.startswith(("def ", "class ", "async def "))]
        name = Path(f).name
        if defs:
            fn_names = ", ".join(d.split("(")[0].split()[-1] for d in defs[:4])
            lines.append(f"{name}: modified {fn_names} (+{len(added)}/-{len(removed)} lines)")
        else:
            lines.append(f"{name}: +{len(added)}/-{len(removed)} lines")
    return "; ".join(lines)


def _llm_commit_message(files: list[str]) -> str | None:
    """Ask phi4-mini for a conventional commit message based on a diff summary."""
    try:
        import requests as _req
        summary = _diff_summary(files)
        prompt = (
            f"Write one git commit message (max 72 chars) in conventional commit format.\n"
            f"Changes: {summary}\n"
            f"Format: type(scope): short description\n"
            f"type must be one of: fix, feat, refactor, update, config, docs\n"
            f"Output the commit message only. No quotes. No period."
        )
        resp = _req.post(
            "http://localhost:11434/api/generate",
            json={"model": "phi4-mini", "prompt": prompt, "stream": False,
                  "options": {"temperature": 0.2, "num_predict": 30, "num_ctx": 256}},
            timeout=15,
        )
        if resp.status_code == 200:
            msg = resp.json().get("response", "").strip().splitlines()[0].strip().strip('"\'')
            if 10 < len(msg) <= 80:
                return msg
    except Exception:
        pass
    return None


def sync():
    """Stage, commit, and push all changes to both remotes."""
    log.info("Sync triggered — checking for changes...")

    _git(["add", "--all"])

    changed = _get_changed_files()
    # After `git add`, check staged changes
    rc, staged = _git(["diff", "--cached", "--name-only"])
    if not staged.strip():
        log.info("Nothing to commit.")
        return

    changed_names = staged.strip().splitlines()

    commit_msg = _llm_commit_message(changed_names)
    if not commit_msg:
        commit_msg = _classify_change(changed_names)

    rc, out = _git(["commit", "-m", commit_msg])
    if rc != 0:
        log.error(f"Commit failed: {out}")
        return
    log.info(f"Committed: {commit_msg}")

    for remote in REMOTES:
        rc, out = _git(["push", remote, "main"])
        if rc == 0:
            log.info(f"Pushed to {remote}")
        else:
            log.error(f"Push to {remote} failed: {out}")


class ChangeHandler(FileSystemEventHandler):
    def __init__(self):
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule_sync(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, sync)
            self._timer.daemon = True
            self._timer.start()

    def on_modified(self, event):
        if event.is_directory:
            return
        if _should_ignore(event.src_path):
            return
        if Path(event.src_path).suffix not in WATCH_EXTENSIONS:
            return
        rel = Path(event.src_path).relative_to(JARVIS_HOME)
        log.info(f"Changed: {rel} — sync in {DEBOUNCE_SECONDS}s")
        self._schedule_sync()

    on_created = on_modified
    on_deleted = on_modified


def initial_push():
    """On startup, push current state if there's anything uncommitted."""
    rc, out = _git(["status", "--short"])
    if out.strip():
        log.info("Uncommitted changes on startup — syncing now...")
        sync()
    else:
        log.info("Repo clean on startup — nothing to push.")


def main():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log.info("Jarvis Repo Syncer started — watching ~/.jarvis/")
    log.info(f"Remotes: {', '.join(REMOTES)} | Debounce: {DEBOUNCE_SECONDS}s")

    initial_push()

    handler = ChangeHandler()
    observer = Observer()
    observer.schedule(handler, str(JARVIS_HOME), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    log.info("Syncer stopped.")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        # One-shot mode for cron: commit + push if anything changed
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        sync()
    else:
        main()
