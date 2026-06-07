#!/usr/bin/env python3
"""
JV Titan Blueprint Encoder
Compressed binary format for fast machine processing of consciousness,
memory, emotion, and decision states.

Format: msgpack with optional zstd compression for large payloads.
Extension: .jvt (Jarvis Titan Blueprint)
"""

import gzip
import logging
import time
from pathlib import Path
from typing import Any, Optional

JARVIS_HOME = Path("/home/kali/.jarvis")
DATA_DIR = JARVIS_HOME / "data" / "jv_titan"
DATA_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("jv_titan.blueprint")

# Lazy import msgpack — fail gracefully if missing
try:
    import msgpack
    _HAS_MSGPACK = True
except ImportError:
    _HAS_MSGPACK = False
    log.warning("msgpack not installed — falling back to JSON for blueprint storage")


def _encode(data: Any, compress: bool = True) -> bytes:
    """Encode Python object to binary blueprint bytes."""
    if _HAS_MSGPACK:
        payload = msgpack.packb(data, use_bin_type=True)
    else:
        import json
        payload = json.dumps(data, default=str).encode("utf-8")

    if compress and len(payload) > 512:
        payload = gzip.compress(payload, compresslevel=6)
        payload = b"\x01" + payload  # compression flag
    else:
        payload = b"\x00" + payload
    return payload


def _decode(payload: bytes) -> Any:
    """Decode binary blueprint bytes to Python object."""
    if not payload:
        return {}
    compressed = payload[0] == 1
    raw = payload[1:]
    if compressed:
        raw = gzip.decompress(raw)
    if _HAS_MSGPACK:
        return msgpack.unpackb(raw, raw=False)
    else:
        import json
        return json.loads(raw.decode("utf-8"))


def save_blueprint(name: str, data: Any, compress: bool = True) -> Path:
    """Save a blueprint chunk to disk. Returns path."""
    path = DATA_DIR / f"{name}.jvt"
    path.write_bytes(_encode(data, compress=compress))
    log.debug("Blueprint saved: %s (%d bytes)", path.name, path.stat().st_size)
    return path


def load_blueprint(name: str) -> Optional[Any]:
    """Load a blueprint chunk from disk. Returns None if missing."""
    path = DATA_DIR / f"{name}.jvt"
    if not path.exists():
        return None
    try:
        return _decode(path.read_bytes())
    except Exception as exc:
        log.warning("Failed to load blueprint %s: %s", name, exc)
        return None


def save_memory_tape_chunk(date_key: str, events: list) -> Path:
    """Save a daily memory tape chunk."""
    tape_dir = DATA_DIR / "memory_tape"
    tape_dir.mkdir(parents=True, exist_ok=True)
    path = tape_dir / f"{date_key}.jvt"
    path.write_bytes(_encode(events, compress=True))
    return path


def load_memory_tape_chunk(date_key: str) -> list:
    """Load a daily memory tape chunk."""
    path = DATA_DIR / "memory_tape" / f"{date_key}.jvt"
    if not path.exists():
        return []
    try:
        data = _decode(path.read_bytes())
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.warning("Failed to load memory tape %s: %s", date_key, exc)
        return []


def list_memory_tape_dates() -> list[str]:
    """Return all available memory tape date keys (YYYY-MM-DD)."""
    tape_dir = DATA_DIR / "memory_tape"
    if not tape_dir.exists():
        return []
    return sorted([p.stem for p in tape_dir.glob("*.jvt")])


def export_full_blueprint() -> bytes:
    """Export the entire JV Titan state as a single binary blob."""
    full_state = {
        "timestamp": time.time(),
        "consciousness": load_blueprint("consciousness") or {},
        "emotions": load_blueprint("emotions") or {},
        "growth": load_blueprint("growth") or {},
        "decisions": load_blueprint("decisions") or {},
        "persona": load_blueprint("persona") or {},
        "memory_dates": list_memory_tape_dates(),
    }
    return _encode(full_state, compress=True)


def import_full_blueprint(blob: bytes) -> bool:
    """Import a full JV Titan state from binary blob."""
    try:
        state = _decode(blob)
        save_blueprint("consciousness", state.get("consciousness", {}))
        save_blueprint("emotions", state.get("emotions", {}))
        save_blueprint("growth", state.get("growth", {}))
        save_blueprint("decisions", state.get("decisions", {}))
        save_blueprint("persona", state.get("persona", {}))
        log.info("Full blueprint imported (%d memory dates)", len(state.get("memory_dates", [])))
        return True
    except Exception as exc:
        log.error("Blueprint import failed: %s", exc)
        return False
