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
    summary = ", ".join(changed_names[:5])
    if len(changed_names) > 5:
        summary += f" (+{len(changed_names) - 5} more)"

    commit_msg = f"auto-sync: {summary}"
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
    main()
