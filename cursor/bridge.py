"""
Cursor IDE bridge — parse session files and enrich with Kimi K2.6 analysis.

Session files may be JSON, JSONL, or SQLite (older Cursor versions use leveldb).
We handle JSON/JSONL; SQLite is noted but delegated to the existing cursor_monitor.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

log = logging.getLogger("jarvis.cursor.bridge")

JARVIS_HOME    = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
ENRICHMENT_LOG = JARVIS_HOME / "data" / "cursor_enriched.jsonl"
MIN_SESSION_CHARS = 500


def process_session_file(path: Path) -> dict | None:
    """Parse a Cursor session file, enrich with Kimi, store in ChromaDB."""
    try:
        session = _parse_session(path)
    except Exception as e:
        log.error("Failed to parse session %s: %s", path, e)
        return None

    if not session or len(json.dumps(session)) < MIN_SESSION_CHARS:
        return None

    enriched = _enrich_with_ai(session)
    _store_in_chromadb(enriched, str(path))

    if _is_architectural(session):
        _send_telegram_alert(enriched)

    with open(ENRICHMENT_LOG, "a") as f:
        f.write(json.dumps(enriched) + "\n")

    return enriched


def _parse_session(path: Path) -> dict:
    """Parse JSON or JSONL session files."""
    text = path.read_text(encoding="utf-8", errors="replace")

    if path.suffix == ".jsonl":
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return {"type": "jsonl", "entries": entries, "file": path.name}

    try:
        data = json.loads(text)
        return {"type": "json", "data": data, "file": path.name}
    except json.JSONDecodeError:
        # Treat as plain log
        return {"type": "log", "content": text[:5000], "file": path.name}


def _enrich_with_ai(session: dict) -> dict:
    """Extract structured insights from a Cursor session using Claude CLI."""
    try:
        import sys
        sys.path.insert(0, str(JARVIS_HOME))
        from claude_client import get_client, _check_privacy

        session_text = json.dumps(session)[:8000]
        _check_privacy(session_text)

        client = get_client()
        prompt = f"""Analyze this Cursor IDE session and extract structured insights.

Session data:
{session_text}

Extract:
1. What was the developer trying to achieve?
2. Key patterns or approaches used
3. Any bugs or issues encountered
4. Architectural decisions made (if any)
5. Relevance to broader project goals

Return JSON: {{"goal": "", "patterns": [], "issues": [], "decisions": [], "project_relevance": ""}}"""

        result = client.extract_json(
            prompt,
            system="You are a code analysis assistant. Return valid JSON only.",
        )
        session["analysis"] = result
        session["enriched_ts"] = int(time.time())
    except ValueError as e:
        log.warning("Privacy check blocked Cursor session enrichment: %s", e)
        session["analysis"] = {"blocked": "privacy_boundary"}
    except Exception as e:
        log.error("AI enrichment failed: %s", e)
        session["analysis"] = {"error": str(e)[:200]}

    return session


def _is_architectural(session: dict) -> bool:
    """Detect if a session contains significant architectural decisions."""
    analysis = session.get("analysis", {})
    decisions = analysis.get("decisions", [])
    return len(decisions) > 0


def _store_in_chromadb(enriched: dict, source_path: str) -> None:
    """Store enriched session in ChromaDB."""
    try:
        import chromadb

        analysis = enriched.get("analysis", {})
        doc = (
            f"Cursor session [{enriched.get('file', '')}]: "
            f"Goal: {analysis.get('goal', '')}. "
            f"Patterns: {', '.join(analysis.get('patterns', [])[:3])}. "
            f"Decisions: {', '.join(str(d) for d in analysis.get('decisions', [])[:2])}."
        )[:1500]

        client = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
        col    = client.get_or_create_collection("jarvis_memory")
        col.add(
            documents=[doc],
            metadatas=[{
                "source": "cursor_session",
                "priority": 3,
                "type": "cursor",
                "ts": str(int(time.time())),
                "file": enriched.get("file", ""),
                "oc_sync_status": "pending",
            }],
            ids=[f"cursor_{int(time.time())}_{hash(source_path) % 100000}"],
        )
        log.debug("Stored cursor session in ChromaDB.")
    except Exception as e:
        log.error("ChromaDB store failed: %s", e)


def _send_telegram_alert(enriched: dict) -> None:
    """Alert Mike about a significant architectural decision via Telegram."""
    analysis  = enriched.get("analysis", {})
    decisions = analysis.get("decisions", [])
    if not decisions:
        return

    msg = (
        "🏗️ *Cursor IDE — Architectural Decision Detected*\n\n"
        f"**Goal:** {analysis.get('goal', 'unknown')}\n"
        f"**Decisions:**\n" + "\n".join(f"• {d}" for d in decisions[:3])
    )
    try:
        from openclaw.bridge import send_via_openclaw
        send_via_openclaw(msg)
    except Exception as e:
        log.error("Telegram alert failed: %s", e)


def trigger_cursor_action(action: str, params: dict) -> None:
    """Trigger an action in Cursor IDE — uses subprocess with explicit args (no shell=True)."""
    if action == "open_file":
        file_path = str(params.get("file", ""))
        line      = str(params.get("line", "1"))
        if file_path:
            subprocess.run(["cursor", "--goto", f"{file_path}:{line}"], check=False)
    else:
        log.warning("Unknown Cursor action: %s", action)
