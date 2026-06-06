"""
Cursor IDE file watcher — detects new session files and triggers enrichment.
Runs as jarvis-cursor.service (systemd) or via cron.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger("jarvis.cursor.watcher")

JARVIS_HOME  = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
WATCH_PATHS  = [
    Path.home() / ".cursor" / "logs",
    Path.home() / ".config" / "Cursor" / "logs",
]
PROCESSED    = JARVIS_HOME / "data" / "cursor_processed.txt"

_processed_cache: set[str] = set()


def _load_processed() -> set[str]:
    if PROCESSED.exists():
        return set(PROCESSED.read_text().splitlines())
    return set()


def _mark_processed(path: str) -> None:
    with open(PROCESSED, "a") as f:
        f.write(path + "\n")
    _processed_cache.add(path)


class CursorEventHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix.lower() in {".jsonl", ".json", ".log"}:
            self._handle(p)

    def on_modified(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix.lower() in {".jsonl", ".json"}:
            self._handle(p)

    def _handle(self, path: Path) -> None:
        key = str(path)
        if key in _processed_cache:
            return
        if path.stat().st_size < 100:
            return
        log.info("New Cursor session file: %s", path)
        try:
            from cursor.bridge import process_session_file
            process_session_file(path)
            _mark_processed(key)
        except Exception as e:
            log.error("Failed to process %s: %s", path, e)


def run() -> None:
    global _processed_cache
    _processed_cache = _load_processed()

    observer = Observer()
    active = 0
    for watch_dir in WATCH_PATHS:
        if watch_dir.exists():
            observer.schedule(CursorEventHandler(), str(watch_dir), recursive=True)
            log.info("Watching: %s", watch_dir)
            active += 1

    if active == 0:
        log.warning("No Cursor log directories found. Will retry in 60s loop.")
        while True:
            time.sleep(60)
            for watch_dir in WATCH_PATHS:
                if watch_dir.exists():
                    log.info("Found Cursor dir: %s — restarting watcher.", watch_dir)
                    observer.schedule(CursorEventHandler(), str(watch_dir), recursive=True)
                    active += 1
            if active:
                break

    observer.start()
    log.info("Cursor watcher started (%d directories).", active)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    run()
