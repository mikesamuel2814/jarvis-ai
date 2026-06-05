#!/usr/bin/env python3
"""
Jarvis Cursor Monitor — watches Cursor IDE session/log directories
and captures AI interactions for Jarvis training data.
Run as a background daemon or via: python3 cursor_monitor.py
"""

import json
import logging
import os
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis.yaml"
OUTPUT_DIR = JARVIS_HOME / "data" / "cursor"
LOG_FILE = JARVIS_HOME / "logs" / "cursor_monitor.log"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [cursor_monitor] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# Known Cursor data locations (varies by OS/install)
CURSOR_PATHS = [
    Path.home() / ".config" / "Cursor" / "logs",
    Path.home() / ".config" / "Cursor" / "User" / "workspaceStorage",
    Path.home() / ".config" / "Cursor" / "User" / "globalStorage",
    Path.home() / ".cursor" / "logs",
    Path.home() / "snap" / "cursor" / "current" / ".config" / "Cursor",
    Path("/opt/cursor"),
    Path("/usr/share/cursor"),
]

# Additional watch paths the user can extend
EXTRA_PATHS_FILE = JARVIS_HOME / "config" / "cursor_watch_paths.txt"


def get_watch_paths():
    paths = []
    for p in CURSOR_PATHS:
        if p.exists():
            paths.append(p)
            log.info(f"Found Cursor path: {p}")

    if EXTRA_PATHS_FILE.exists():
        for line in EXTRA_PATHS_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                p = Path(line).expanduser()
                if p.exists() and p not in paths:
                    paths.append(p)
                    log.info(f"Added extra watch path: {p}")

    return paths


def is_interesting_file(path: Path) -> bool:
    interesting_exts = {".log", ".json", ".jsonl", ".txt", ".db"}
    interesting_names = {"history", "session", "conversation", "chat",
                         "ailog", "cursor", "copilot"}
    name_lower = path.name.lower()
    return (
        path.suffix.lower() in interesting_exts
        or any(n in name_lower for n in interesting_names)
    )


def extract_text_from_json(data, depth=0) -> str:
    if depth > 5:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return "\n".join(extract_text_from_json(i, depth + 1) for i in data)
    if isinstance(data, dict):
        parts = []
        for key in ("content", "text", "message", "prompt", "response",
                    "query", "answer", "userMessage", "assistantMessage"):
            if key in data:
                parts.append(f"{key}: {extract_text_from_json(data[key], depth + 1)}")
        return "\n".join(parts) if parts else ""
    return str(data)


def capture_file(src_path: Path):
    try:
        if src_path.stat().st_size == 0:
            return
        if src_path.stat().st_size > 50_000_000:  # skip files > 50MB
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = src_path.name.replace("/", "_")
        dest = OUTPUT_DIR / f"cursor_{ts}_{safe_name}"

        if src_path.suffix.lower() == ".json":
            try:
                text = src_path.read_text(errors="ignore")
                data = json.loads(text)
                extracted = extract_text_from_json(data)
                if len(extracted.strip()) < 50:
                    return
                dest = OUTPUT_DIR / f"cursor_{ts}_{src_path.stem}.txt"
                dest.write_text(
                    f"=== Jarvis Cursor Capture: {src_path} @ {ts} ===\n"
                    f"{extracted}\n"
                )
                log.info(f"Captured (JSON→txt): {src_path.name} → {dest.name}")
                return
            except json.JSONDecodeError:
                pass

        if src_path.suffix.lower() == ".jsonl":
            lines = []
            for line in src_path.read_text(errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    extracted = extract_text_from_json(obj)
                    if extracted.strip():
                        lines.append(extracted)
                except Exception:
                    lines.append(line)
            if not lines:
                return
            dest = OUTPUT_DIR / f"cursor_{ts}_{src_path.stem}.txt"
            dest.write_text(
                f"=== Jarvis Cursor Capture: {src_path} @ {ts} ===\n"
                + "\n---\n".join(lines) + "\n"
            )
            log.info(f"Captured (JSONL→txt): {src_path.name} → {dest.name}")
            return

        shutil.copy2(src_path, dest)
        log.info(f"Captured: {src_path.name} → {dest.name}")

    except PermissionError:
        pass
    except Exception as e:
        log.warning(f"Failed to capture {src_path}: {e}")


class CursorEventHandler(FileSystemEventHandler):
    def __init__(self):
        self._recently_processed = {}

    def _should_process(self, path_str):
        now = time.time()
        last = self._recently_processed.get(path_str, 0)
        if now - last < 5:
            return False
        self._recently_processed[path_str] = now
        # Clean old entries
        if len(self._recently_processed) > 1000:
            cutoff = now - 60
            self._recently_processed = {
                k: v for k, v in self._recently_processed.items() if v > cutoff
            }
        return True

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if is_interesting_file(path) and self._should_process(str(path)):
            log.debug(f"Modified: {path}")
            capture_file(path)

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if is_interesting_file(path) and self._should_process(str(path)):
            log.debug(f"Created: {path}")
            time.sleep(1)  # wait for write to finish
            capture_file(path)


def do_initial_scan(watch_paths):
    log.info("Running initial scan of Cursor directories...")
    count = 0
    for base in watch_paths:
        for fpath in base.rglob("*"):
            if fpath.is_file() and is_interesting_file(fpath):
                capture_file(fpath)
                count += 1
    log.info(f"Initial scan complete: {count} files processed")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Jarvis Cursor Monitor")
    parser.add_argument("--scan", action="store_true", help="Run initial scan only, then exit")
    parser.add_argument("--watch-path", type=str, help="Additional path to watch")
    args = parser.parse_args()

    if args.watch_path:
        p = Path(args.watch_path).expanduser()
        if p.exists():
            EXTRA_PATHS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(EXTRA_PATHS_FILE, "a") as f:
                f.write(f"{p}\n")
            log.info(f"Added watch path: {p}")

    watch_paths = get_watch_paths()
    if not watch_paths:
        log.warning("No Cursor directories found. Watching ~/.config/Cursor (will start when Cursor is installed).")
        fallback = Path.home() / ".config" / "Cursor"
        fallback.mkdir(parents=True, exist_ok=True)
        watch_paths = [fallback]

    log.info(f"Monitoring {len(watch_paths)} Cursor directories")

    if args.scan:
        do_initial_scan(watch_paths)
        return

    do_initial_scan(watch_paths)

    handler = CursorEventHandler()
    observer = Observer()
    for path in watch_paths:
        observer.schedule(handler, str(path), recursive=True)
        log.info(f"Watching: {path}")

    observer.start()
    log.info("Cursor monitor running. Press Ctrl+C to stop.")

    def shutdown(sig, frame):
        log.info("Shutting down cursor monitor...")
        observer.stop()
        observer.join()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    log.info("Cursor monitor stopped.")


if __name__ == "__main__":
    main()
