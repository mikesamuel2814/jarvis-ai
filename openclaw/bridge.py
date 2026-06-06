"""
OpenClaw ↔ Jarvis bidirectional bridge.

Handles:
  - Pushing ChromaDB learnings to OpenClaw workspace (markdown files)
  - Pulling OpenClaw skill outputs back into ChromaDB
  - Sending Telegram alerts via OpenClaw gateway
  - Heartbeat event processing
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger("jarvis.openclaw.bridge")

JARVIS_HOME    = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
OPENCLAW_WS    = Path(os.environ.get("OPENCLAW_WORKSPACE",
                                      Path.home() / ".openclaw" / "workspace"))
SYNC_LOG       = JARVIS_HOME / "data" / "openclaw_sync.jsonl"
OC_GATEWAY_URL = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")


# ── ChromaDB → OpenClaw workspace ────────────────────────────────


def push_memory_to_openclaw(limit: int = 100) -> int:
    """
    Export pending ChromaDB chunks to OpenClaw workspace as markdown.
    Only syncs chunks with oc_sync_status == "pending".
    """
    try:
        import chromadb
    except ImportError:
        log.error("chromadb not installed")
        return 0

    mem_path = str(JARVIS_HOME / "memory")
    client   = chromadb.PersistentClient(path=mem_path)
    col      = client.get_or_create_collection("jarvis_memory")

    # Get pending chunks — ChromaDB returns dict of parallel lists
    result = col.get(where={"oc_sync_status": "pending"}, limit=limit)
    ids       = result.get("ids", [])
    documents = result.get("documents", [])
    metadatas = result.get("metadatas", [])

    if not ids:
        log.info("No pending chunks to sync to OpenClaw.")
        return 0

    mem_dir = OPENCLAW_WS / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)

    synced = 0
    for chunk_id, doc, meta in zip(ids, documents, metadatas):
        md = (
            f"# Memory: {chunk_id}\n\n"
            f"**Source:** {meta.get('source', 'unknown')}  \n"
            f"**Priority:** {meta.get('priority', 1)}  \n"
            f"**Type:** {meta.get('type', 'general')}  \n"
            f"**Timestamp:** {meta.get('ts', '')}  \n\n"
            f"## Content\n\n{doc}\n\n"
            f"---\n"
        )
        out_path = mem_dir / f"{chunk_id[:60]}.md"
        out_path.write_text(md, encoding="utf-8")

        # Mark as synced
        try:
            col.update(ids=[chunk_id], metadatas=[{**meta, "oc_sync_status": "synced"}])
            synced += 1
        except Exception as e:
            log.error("Failed to mark chunk %s as synced: %s", chunk_id, e)

    _log_sync({"direction": "push", "count": synced, "ts": int(time.time())})
    log.info("Pushed %d chunks to OpenClaw workspace.", synced)
    return synced


def pull_skills_from_openclaw() -> int:
    """
    Pull OpenClaw skill-generated content back into ChromaDB.
    Reads *.md files from the skills output directory.
    """
    try:
        import chromadb
    except ImportError:
        return 0

    skills_dir = OPENCLAW_WS / "skills" / "jarvis-system" / "outputs"
    if not skills_dir.exists():
        return 0

    client = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
    col    = client.get_or_create_collection("jarvis_memory")

    pulled = 0
    for md_file in skills_dir.glob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            doc_id  = f"oc_skill_{md_file.stem}"
            col.add(
                documents=[content[:2000]],
                metadatas=[{
                    "source": "openclaw_skill",
                    "priority": 3,
                    "type": "skill_output",
                    "ts": str(int(md_file.stat().st_mtime)),
                    "oc_sync_status": "synced",
                }],
                ids=[doc_id],
            )
            pulled += 1
        except Exception as e:
            log.error("Failed to import skill file %s: %s", md_file, e)

    _log_sync({"direction": "pull", "count": pulled, "ts": int(time.time())})
    return pulled


# ── Alert via OpenClaw ────────────────────────────────────────────


def send_via_openclaw(message: str, target: str | None = None) -> bool:
    """
    Send a message via OpenClaw gateway CLI.
    Fallback: send directly via Telegram bot token.
    """
    try:
        import subprocess
        cmd = ["openclaw", "message", "send", "--message", message]
        if target:
            cmd += ["--target", target]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return True
        log.warning("openclaw message send failed: %s", result.stderr)
    except Exception as e:
        log.error("OpenClaw send failed: %s", e)
    return False


# ── Sync logging ──────────────────────────────────────────────────


def _log_sync(entry: dict) -> None:
    SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(SYNC_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    pushed = push_memory_to_openclaw()
    pulled = pull_skills_from_openclaw()
    print(f"Sync complete: pushed={pushed}, pulled={pulled}")
