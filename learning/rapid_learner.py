"""
Loop A — Real-time feedback processing.

Called immediately when a user rates a response (👍/👎) or submits /correct.
High-value interactions are queued for the 6-hour Kimi training cycle.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("jarvis.learner.rapid")

JARVIS_HOME    = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
INTERACTIONS   = JARVIS_HOME / "data" / "interactions.jsonl"
GOLDEN         = JARVIS_HOME / "data" / "golden_examples.jsonl"
CORRECTIONS    = JARVIS_HOME / "data" / "corrections.jsonl"
QUEUE          = JARVIS_HOME / "data" / "learning_queue.jsonl"

# Config
QUEUE_MIN_TIER          = "cloud"
QUEUE_MIN_RESPONSE_CHARS = 400


def _load_interaction(interaction_id: str) -> dict | None:
    if not INTERACTIONS.exists():
        return None
    for line in INTERACTIONS.read_text().splitlines():
        try:
            item = json.loads(line)
            if item.get("id") == interaction_id:
                return item
        except json.JSONDecodeError:
            pass
    return None


def process_feedback(
    interaction_id: str,
    rating: str,
    correction: str | None = None,
) -> dict:
    """
    Process user feedback.

    rating: "thumbs_up" | "thumbs_down"
    correction: optional free-text correction (triggers highest-priority storage)
    """
    interaction = _load_interaction(interaction_id)
    if not interaction:
        log.warning("Interaction %s not found", interaction_id)
        return {"status": "not_found", "id": interaction_id}

    if correction:
        return _store_correction(interaction, correction)
    if rating == "thumbs_up":
        return _store_golden(interaction)
    if rating == "thumbs_down":
        return _queue_for_lesson(interaction)
    return {"status": "unknown_rating", "rating": rating}


def _store_golden(interaction: dict) -> dict:
    """Store as golden example (highest RAG priority)."""
    golden = {
        **interaction,
        "type": "golden",
        "priority": 5,
        "golden_ts": int(time.time()),
    }
    with open(GOLDEN, "a") as f:
        f.write(json.dumps(golden) + "\n")
    log.info("Stored golden example: %s", interaction.get("id"))
    return {"status": "golden_stored", "id": interaction.get("id")}


def _store_correction(interaction: dict, correction: str) -> dict:
    """Store explicit correction — highest priority, immediate."""
    from chromadb import PersistentClient

    correction_entry = {
        **interaction,
        "type": "correction",
        "priority": 7,  # Highest priority in RAG
        "correction": correction,
        "correction_ts": int(time.time()),
    }
    with open(CORRECTIONS, "a") as f:
        f.write(json.dumps(correction_entry) + "\n")

    # Immediately store in ChromaDB so next query benefits
    try:
        mem_path = str(JARVIS_HOME / "memory")
        client = PersistentClient(path=mem_path)
        col = client.get_or_create_collection("jarvis_memory")
        doc = f"CORRECTION: {interaction.get('query', '')} → {correction}"
        col.add(
            documents=[doc],
            metadatas=[{
                "source": "correction",
                "priority": 7,
                "type": "correction",
                "ts": str(int(time.time())),
                "oc_sync_status": "pending",
            }],
            ids=[f"corr_{interaction.get('id', int(time.time()))}"],
        )
        log.info("Correction stored in ChromaDB immediately.")
    except Exception as e:
        log.error("ChromaDB correction store failed: %s", e)

    return {"status": "correction_stored", "id": interaction.get("id")}


def _queue_for_lesson(interaction: dict) -> dict:
    """Queue thumbs-down interaction for Kimi lesson extraction (6h cycle)."""
    if (interaction.get("tier") == QUEUE_MIN_TIER or
            len(interaction.get("response", "")) >= QUEUE_MIN_RESPONSE_CHARS):
        queue_item = {
            **interaction,
            "rating": "thumbs_down",
            "queued_ts": int(time.time()),
        }
        with open(QUEUE, "a") as f:
            f.write(json.dumps(queue_item) + "\n")
        log.info("Queued for lesson extraction: %s", interaction.get("id"))
        return {"status": "queued_for_lesson"}
    return {"status": "skipped_low_value"}


def store_interaction(
    interaction_id: str,
    query: str,
    response: str,
    tier: str,
    model: str,
    rag_context_ids: list[str] | None = None,
) -> None:
    """Record every query/response pair for future training."""
    entry = {
        "id": interaction_id,
        "ts": int(time.time()),
        "query": query,
        "response": response,
        "tier": tier,
        "model": model,
        "rag_context_ids": rag_context_ids or [],
    }
    with open(INTERACTIONS, "a") as f:
        f.write(json.dumps(entry) + "\n")
