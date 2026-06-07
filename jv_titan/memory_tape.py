#!/usr/bin/env python3
"""
JV Titan Memory Tape — Timeline-based permanent memory storage.

Organizes memories by temporal category:
  • PAST   — Historical events, life history, training data from Mike
  • PRESENT — Current activities, ongoing projects, real-time observations
  • FUTURE  — Goals, plans, scheduled tasks, aspirations

Each memory entry:
  {
    "id": uuid,
    "timestamp": unix_float,
    "category": "past" | "present" | "future",
    "source": "mike" | "jarvis" | "system" | "training",
    "content": str,
    "importance": 1-10,
    "tags": [str],
    "embedding_id": str,  # ChromaDB reference
  }

Stored in compressed binary chunks by date (YYYY-MM-DD.jvt).
"""

import hashlib
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .blueprint_encoder import (
    load_blueprint,
    load_memory_tape_chunk,
    save_blueprint,
    save_memory_tape_chunk,
    list_memory_tape_dates,
)

log = logging.getLogger("jv_titan.memory_tape")

# Lazy ChromaDB for semantic retrieval
_chroma_collection = None


def _get_chroma_collection():
    global _chroma_collection
    if _chroma_collection is None:
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(Path("/home/kali/.jarvis") / "memory"))
            _chroma_collection = client.get_or_create_collection("jv_titan_memories")
        except Exception as exc:
            log.debug("ChromaDB memory collection unavailable: %s", exc)
            _chroma_collection = False
    return _chroma_collection if _chroma_collection is not False else None


def _today_key() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def add_memory(
    content: str,
    category: str = "present",
    source: str = "mike",
    importance: int = 5,
    tags: Optional[List[str]] = None,
) -> dict:
    """Add a memory entry to the tape and index it semantically."""
    if not content or not content.strip():
        return {}

    entry = {
        "id": str(uuid.uuid4())[:12],
        "timestamp": time.time(),
        "category": category,
        "source": source,
        "content": content.strip(),
        "importance": max(1, min(10, importance)),
        "tags": tags or [],
    }

    # Save to daily binary chunk
    date_key = _today_key()
    chunk = load_memory_tape_chunk(date_key)
    chunk.append(entry)
    save_memory_tape_chunk(date_key, chunk)

    # Semantic index in ChromaDB
    col = _get_chroma_collection()
    if col is not None:
        try:
            col.add(
                ids=[entry["id"]],
                documents=[entry["content"]],
                metadatas=[{
                    "category": category,
                    "source": source,
                    "importance": importance,
                    "date": date_key,
                    "tags": ",".join(tags or []),
                }],
            )
        except Exception as exc:
            log.debug("Memory indexing failed: %s", exc)

    log.info("Memory recorded [%s|%s|%d]: %s...", category, source, importance, content[:60])
    return entry


def retrieve_memories(
    query: str = "",
    category: Optional[str] = None,
    n: int = 5,
    min_importance: int = 1,
) -> List[dict]:
    """Retrieve memories by semantic similarity or category."""
    results = []

    # Semantic search via ChromaDB
    col = _get_chroma_collection()
    if col is not None and query:
        try:
            where_filter = None
            if category:
                where_filter = {"category": category}
            chroma_results = col.query(
                query_texts=[query],
                n_results=n * 2,
                where=where_filter,
                include=["documents", "metadatas", "distances"],
            )
            docs = chroma_results.get("documents", [[]])[0]
            metas = chroma_results.get("metadatas", [[]])[0]
            dists = chroma_results.get("distances", [[]])[0]
            for doc, meta, dist in zip(docs, metas, dists):
                imp = meta.get("importance", 5)
                if imp >= min_importance:
                    results.append({
                        "content": doc,
                        "category": meta.get("category", "unknown"),
                        "source": meta.get("source", "unknown"),
                        "importance": imp,
                        "date": meta.get("date", "unknown"),
                        "distance": dist,
                    })
        except Exception as exc:
            log.debug("Semantic memory retrieval failed: %s", exc)

    # Fallback: scan recent binary chunks if semantic search empty
    if not results and category:
        for date_key in sorted(list_memory_tape_dates(), reverse=True)[:7]:
            chunk = load_memory_tape_chunk(date_key)
            for entry in chunk:
                if entry.get("category") == category and entry.get("importance", 5) >= min_importance:
                    results.append({
                        "content": entry["content"],
                        "category": entry["category"],
                        "source": entry["source"],
                        "importance": entry["importance"],
                        "date": date_key,
                        "distance": 0.0,
                    })
            if len(results) >= n:
                break

    # Deduplicate and sort by importance + recency
    seen = set()
    unique = []
    for r in results:
        key = r["content"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    unique.sort(key=lambda x: (-x["importance"], x.get("distance", 0)))
    return unique[:n]


def get_timeline_summary(categories: Optional[List[str]] = None, days: int = 7) -> str:
    """Generate a human-readable timeline summary."""
    cats = set(categories or ["past", "present", "future"])
    lines = []
    dates = sorted(list_memory_tape_dates(), reverse=True)[:days]
    for dk in dates:
        chunk = load_memory_tape_chunk(dk)
        day_entries = [e for e in chunk if e.get("category") in cats]
        if day_entries:
            lines.append(f"\n📅 {dk}")
            for e in sorted(day_entries, key=lambda x: x.get("timestamp", 0)):
                icon = {"past": "📜", "present": "⚡", "future": "🔮"}.get(e["category"], "•")
                imp = "★" * e.get("importance", 5)
                lines.append(f"  {icon} {e['content'][:100]} [{imp}]")
    return "\n".join(lines) if lines else "No memories in this timeline yet."


def format_memory_context(query: str = "", n: int = 3) -> str:
    """Format top-N relevant memories for prompt injection."""
    memories = retrieve_memories(query=query, n=n)
    if not memories:
        return ""
    lines = ["--- JV Titan MEMORY CONTEXT ---"]
    for m in memories:
        icon = {"past": "📜", "present": "⚡", "future": "🔮"}.get(m["category"], "•")
        lines.append(f"{icon} {m['content'][:150]}")
    lines.append("--- END MEMORY CONTEXT ---")
    return "\n".join(lines)
