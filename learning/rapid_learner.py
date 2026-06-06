"""
Loop A — Real-time interaction storage + feedback processing.

Called immediately when a user queries (store_interaction) and when a user
rates a response (process_feedback: 👍/👎) or submits /correct.

Persistence (all under JARVIS_HOME/data/):
  interactions.jsonl    — every query/response pair, updated in place on feedback
  golden_examples.jsonl — 👍 responses (high RAG priority)
  corrections.jsonl     — explicit /correct submissions
  lessons.jsonl         — lesson candidates from 👎/corrections (learner.py upserts to ChromaDB)
  learning_queue.jsonl  — 👎 interactions queued for the 6h deep training cycle

Records carry: id, query, response, tier, model, timestamp, rating,
correction, rag_context_ids.

chromadb is imported lazily (top-level import segfaults in some cron contexts).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("jarvis.learner.rapid")

JARVIS_HOME    = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
DATA           = JARVIS_HOME / "data"
INTERACTIONS   = DATA / "interactions.jsonl"
GOLDEN         = DATA / "golden_examples.jsonl"
CORRECTIONS    = DATA / "corrections.jsonl"
LESSONS        = DATA / "lessons.jsonl"
QUEUE          = DATA / "learning_queue.jsonl"

# Config — which 👎 interactions are worth queuing for deep (Kimi) lesson extraction
QUEUE_MIN_TIER           = "cloud"
QUEUE_MIN_RESPONSE_CHARS = 400


def _now_iso() -> str:
    return datetime.now().isoformat()


def _ensure_dirs() -> None:
    DATA.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, obj: dict) -> None:
    _ensure_dirs()
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")


def _load_interaction(interaction_id: str) -> dict | None:
    if not INTERACTIONS.exists():
        return None
    for line in INTERACTIONS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("id") == interaction_id:
            return item
    return None


def _update_interaction(interaction_id: str, **updates) -> dict | None:
    """Rewrite interactions.jsonl, patching the matching record in place.

    Returns the updated record, or None if the id was not found.
    """
    if not INTERACTIONS.exists():
        return None
    updated: dict | None = None
    out_lines: list[str] = []
    for line in INTERACTIONS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            out_lines.append(line)
            continue
        if item.get("id") == interaction_id:
            item.update(updates)
            updated = item
        out_lines.append(json.dumps(item))
    if updated is not None:
        _ensure_dirs()
        INTERACTIONS.write_text("\n".join(out_lines) + ("\n" if out_lines else ""))
    return updated


def store_interaction(
    interaction_id: str,
    query: str,
    response: str,
    tier: str,
    model: str,
    rag_context_ids: list[str] | None = None,
) -> None:
    """Record every query/response pair for future training.

    Uses ISO `timestamp` (what learner.py expects) and seeds rating/correction
    so the record round-trips cleanly through process_feedback().
    """
    entry = {
        "id": interaction_id,
        "timestamp": _now_iso(),
        "ts": int(time.time()),
        "query": query,
        "response": response,
        "tier": tier,
        "model": model,
        "rating": None,
        "correction": None,
        "rag_context_ids": rag_context_ids or [],
    }
    _append_jsonl(INTERACTIONS, entry)
    log.info("Stored interaction: %s", interaction_id)


def process_feedback(
    interaction_id: str,
    rating: str,
    correction: str | None = None,
) -> dict:
    """Process user feedback.

    rating: "thumbs_up" | "thumbs_down" (legacy "good"/"bad"/"up"/"down" accepted)
    correction: optional free-text correction (treated as highest-priority signal)

    Always updates the source interaction record in place (when present) and
    produces the right downstream artifact:
      👍            → golden_examples.jsonl
      👎            → learning_queue.jsonl (+ lesson candidate)
      correction    → corrections.jsonl + lessons.jsonl (+ immediate ChromaDB upsert)
    """
    rating = (rating or "").lower()
    is_up   = rating in ("thumbs_up", "good", "up", "👍")
    is_down = rating in ("thumbs_down", "bad", "down", "👎")

    norm_rating = "good" if is_up else "bad" if is_down else rating

    interaction = _load_interaction(interaction_id)

    # A correction may arrive for an interaction we never stored (e.g. a
    # Telegram message). Synthesize a minimal record so the signal is never lost.
    if interaction is None:
        interaction = {
            "id": interaction_id,
            "timestamp": _now_iso(),
            "ts": int(time.time()),
            "query": "",
            "response": "",
            "tier": "unknown",
            "model": "unknown",
            "rating": None,
            "correction": None,
            "rag_context_ids": [],
        }
        synthesized = True
    else:
        synthesized = False

    # Patch the stored record in place (best effort).
    updates = {"rating": norm_rating, "feedback_ts": int(time.time())}
    if correction:
        updates["correction"] = correction
    if not synthesized:
        _update_interaction(interaction_id, **updates)
    interaction = {**interaction, **updates}

    if correction:
        return _store_correction(interaction, correction)
    if is_up:
        return _store_golden(interaction)
    if is_down:
        return _queue_for_lesson(interaction)
    return {"status": "unknown_rating", "rating": rating, "id": interaction_id}


def _store_golden(interaction: dict) -> dict:
    """Store as golden example (high RAG priority)."""
    golden = {
        **interaction,
        "rating": "good",
        "type": "golden",
        "priority": 5,
        "golden_ts": int(time.time()),
    }
    _append_jsonl(GOLDEN, golden)
    log.info("Stored golden example: %s", interaction.get("id"))
    return {"status": "golden_stored", "id": interaction.get("id")}


def _store_correction(interaction: dict, correction: str) -> dict:
    """Store explicit correction — highest priority. Also drops a lesson
    candidate and tries an immediate ChromaDB upsert so the very next query
    benefits even before the 6h cycle runs.
    """
    correction_entry = {
        **interaction,
        "rating": "bad",
        "type": "correction",
        "priority": 7,
        "correction": correction,
        "correction_ts": int(time.time()),
    }
    _append_jsonl(CORRECTIONS, correction_entry)

    # Lesson candidate — learner.py / queue_processor read lessons.jsonl
    lesson = {
        "ts": int(time.time()),
        "category": "correction",
        "priority": 7,
        "principle": (
            f"When asked '{interaction.get('query', '')[:120]}', "
            f"the correct answer is: {correction}"
        ),
        "application": f"query similar to: {interaction.get('query', '')[:120]}",
        "keywords": [w for w in interaction.get("query", "").lower().split() if len(w) > 3][:8],
        "source_id": interaction.get("id"),
    }
    _append_jsonl(LESSONS, lesson)

    # Immediate ChromaDB upsert (lazy imports — never crash the request).
    # Embed via ollama mxbai-embed-large so the vector matches the collection's
    # 1024-dim space; if embedding is unavailable the learner picks it up later.
    try:
        from chromadb import PersistentClient  # noqa: PLC0415
        import ollama  # noqa: PLC0415

        doc = f"CORRECTION: {interaction.get('query', '')} → {correction}"
        emb = ollama.embeddings(model="mxbai-embed-large", prompt=doc[:4096])["embedding"]

        mem_path = str(JARVIS_HOME / "memory")
        client = PersistentClient(path=mem_path)
        col = client.get_or_create_collection("jarvis_memory")
        col.upsert(
            ids=[f"corr_{interaction.get('id', int(time.time()))}"],
            embeddings=[emb],
            documents=[doc],
            metadatas=[{
                "source": "correction",
                "source_type": "lesson",
                "lesson_type": "correction",
                "priority": 7,
                "type": "correction",
                "ts": str(int(time.time())),
                "oc_sync_status": "pending",
            }],
        )
        log.info("Correction stored in ChromaDB immediately.")
    except Exception as e:
        log.warning("ChromaDB correction store deferred to learner: %s", e)

    return {"status": "correction_stored", "id": interaction.get("id")}


def _queue_for_lesson(interaction: dict) -> dict:
    """Queue 👎 interaction for deep lesson extraction (6h cycle), and drop a
    lightweight lesson candidate immediately.
    """
    queue_item = {
        **interaction,
        "rating": "bad",
        "queued_ts": int(time.time()),
    }
    _append_jsonl(QUEUE, queue_item)
    log.info("Queued for lesson extraction: %s", interaction.get("id"))

    high_value = (
        interaction.get("tier") == QUEUE_MIN_TIER
        or len(interaction.get("response", "")) >= QUEUE_MIN_RESPONSE_CHARS
    )
    return {
        "status": "queued_for_lesson",
        "high_value": high_value,
        "id": interaction.get("id"),
    }
