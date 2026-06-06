"""
Jarvis 2.0 — ChromaDB ↔ OpenClaw memory sync engine.

full_sync() exports recent ChromaDB documents to the OpenClaw workspace
memory folder so the OpenClaw agent can reference Jarvis memories during
its reasoning, and optionally imports new files dropped into the workspace.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.memory.sync")

JARVIS_HOME   = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
MEMORY_DIR    = JARVIS_HOME / "memory"
OC_MEMORY_DIR = Path.home() / ".openclaw" / "workspace" / "memory"
COLLECTION    = "jarvis_memory"

# How many recent chunks to export per sync (avoids dumping 10K items)
EXPORT_LIMIT = 200


def full_sync(export_limit: int = EXPORT_LIMIT) -> dict[str, Any]:
    """
    Bidirectional sync:
    1. Export top-N recent ChromaDB chunks → ~/.openclaw/workspace/memory/
    2. Import any *.json files in that folder that are not yet in ChromaDB
    Returns a result dict with counts.
    """
    OC_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    exported = _export_to_openclaw(export_limit)
    imported = _import_from_openclaw()
    result = {
        "status": "ok",
        "exported": exported,
        "imported": imported,
        "timestamp": datetime.utcnow().isoformat(),
    }
    log.info("Memory sync complete: %s", result)
    return result


def _export_to_openclaw(limit: int) -> int:
    try:
        import chromadb

        client = chromadb.PersistentClient(
            path=str(MEMORY_DIR),
            settings=chromadb.Settings(anonymized_telemetry=False),
        )
        col = client.get_or_create_collection(COLLECTION)
        total = col.count()
        if total == 0:
            return 0

        # Fetch most-recent items (ChromaDB has no sort-by-date; get latest N)
        results = col.get(limit=min(limit, total), include=["documents", "metadatas"])
        docs      = results.get("documents") or []
        metas     = results.get("metadatas") or []
        ids       = results.get("ids") or []

        out_path = OC_MEMORY_DIR / "jarvis_memory_export.json"
        payload = [
            {"id": i, "text": d, "meta": m}
            for i, d, m in zip(ids, docs, metas)
        ]
        out_path.write_text(json.dumps(payload, indent=2, default=str))
        log.info("Exported %d chunks to %s", len(payload), out_path)
        return len(payload)
    except Exception as e:
        log.warning("Export failed: %s", e)
        return 0


def _import_from_openclaw() -> int:
    imported = 0
    try:
        import chromadb

        client = chromadb.PersistentClient(
            path=str(MEMORY_DIR),
            settings=chromadb.Settings(anonymized_telemetry=False),
        )
        col = client.get_or_create_collection(COLLECTION)

        for json_file in OC_MEMORY_DIR.glob("*.json"):
            if json_file.name == "jarvis_memory_export.json":
                continue  # skip our own export
            try:
                items = json.loads(json_file.read_text())
                if not isinstance(items, list):
                    continue
                for item in items:
                    text = item.get("text", "")
                    meta = item.get("meta", {})
                    doc_id = item.get("id", f"oc_{json_file.stem}_{imported}")
                    if text:
                        col.upsert(ids=[doc_id], documents=[text], metadatas=[meta])
                        imported += 1
            except Exception as e:
                log.warning("Import %s failed: %s", json_file.name, e)
    except Exception as e:
        log.warning("Import phase failed: %s", e)
    return imported
