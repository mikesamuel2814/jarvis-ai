"""
Jarvis v3 Nano-Bot Swarm — Blackboard (Shared State)
"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

JARVIS_HOME = Path("/home/kali/.jarvis")


class Blackboard:
    """
    Thread-safe shared state store for nano-bots.
    In-memory with optional JSON persistence.
    """

    def __init__(self, persist_path: Optional[Path] = None):
        self._data: Dict[str, Any] = {}
        self._timestamps: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._persist = persist_path or (JARVIS_HOME / "data" / "blackboard.json")
        self._load()

    def _load(self):
        if self._persist.exists():
            try:
                with open(self._persist, "r") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    def _save(self):
        try:
            self._persist.parent.mkdir(parents=True, exist_ok=True)
            with open(self._persist, "w") as f:
                json.dump(self._data, f, indent=2, default=str)
        except Exception:
            pass

    def write(self, key: str, value: Any, persist: bool = True):
        with self._lock:
            self._data[key] = value
            self._timestamps[key] = time.time()
            if persist:
                self._save()

    def read(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def read_section(self, prefix: str) -> Dict[str, Any]:
        with self._lock:
            return {k: v for k, v in self._data.items() if k.startswith(prefix)}

    def delete(self, key: str):
        with self._lock:
            self._data.pop(key, None)
            self._timestamps.pop(key, None)
            self._save()

    def keys(self) -> list:
        with self._lock:
            return list(self._data.keys())

    def clear(self):
        with self._lock:
            self._data.clear()
            self._timestamps.clear()
            self._save()
