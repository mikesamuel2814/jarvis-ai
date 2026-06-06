"""
Loop B — 6-hour learning queue processor.

Reads learning_queue.jsonl, calls kimi/trainer.py to extract lessons,
re-embeds high-priority chunks, syncs to OpenClaw workspace.
Run via train_v2.sh every 6 hours.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("jarvis.learner.queue")

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
QUEUE       = JARVIS_HOME / "data" / "learning_queue.jsonl"
LESSONS     = JARVIS_HOME / "data" / "lessons.jsonl"
OC_SKILLS   = Path(os.environ.get("OPENCLAW_WORKSPACE",
                                   Path.home() / ".openclaw" / "workspace")) / "skills" / "jarvis-system"


def _read_queue() -> list[dict]:
    if not QUEUE.exists():
        return []
    items = []
    for line in QUEUE.read_text().splitlines():
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return items


def _clear_processed(processed_ids: set[str]) -> None:
    if not QUEUE.exists():
        return
    remaining = []
    for line in QUEUE.read_text().splitlines():
        try:
            item = json.loads(line)
            if item.get("id") not in processed_ids:
                remaining.append(line)
        except json.JSONDecodeError:
            pass
    QUEUE.write_text("\n".join(remaining) + ("\n" if remaining else ""))


def embed_lessons_in_chroma() -> int:
    """Push new lessons from lessons.jsonl into ChromaDB."""
    try:
        import chromadb
    except ImportError:
        log.error("chromadb not installed")
        return 0

    if not LESSONS.exists():
        return 0

    client = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
    col    = client.get_or_create_collection("jarvis_memory")

    embedded = 0
    for line in LESSONS.read_text().splitlines():
        try:
            lesson = json.loads(line)
            doc_id = f"lesson_{lesson.get('ts', 0)}_{embedded}"
            doc    = (
                f"LESSON [{lesson.get('category', 'general')}]: "
                f"{lesson.get('principle', '')} "
                f"(apply: {lesson.get('application', '')})"
            )
            col.add(
                documents=[doc],
                metadatas=[{
                    "source": "lesson",
                    "priority": lesson.get("priority", 5),
                    "type": "lesson",
                    "ts": str(lesson.get("ts", 0)),
                    "keywords": ",".join(lesson.get("keywords", [])),
                    "oc_sync_status": "pending",
                }],
                ids=[doc_id],
            )
            embedded += 1
        except Exception as e:
            log.error("Embed lesson failed: %s", e)

    log.info("Embedded %d lessons in ChromaDB.", embedded)
    return embedded


def sync_to_openclaw(lessons: list[dict]) -> None:
    """Write lesson summaries to OpenClaw workspace for Kimi access."""
    OC_SKILLS.mkdir(parents=True, exist_ok=True)
    lessons_md_path = OC_SKILLS / "learned_lessons.md"

    lines = ["# Jarvis Learned Lessons\n", f"_Updated: {__import__('datetime').datetime.utcnow().isoformat()}_\n\n"]
    for lesson in lessons[-50:]:  # Last 50 lessons only
        lines.append(f"## [{lesson.get('category', 'general')}] {lesson.get('principle', '')}\n")
        lines.append(f"- **Apply when:** {lesson.get('application', '')}\n")
        kws = lesson.get("keywords", [])
        if kws:
            lines.append(f"- **Keywords:** {', '.join(kws)}\n")
        lines.append("\n")

    lessons_md_path.write_text("".join(lines))
    log.info("Synced %d lessons to OpenClaw workspace.", len(lessons))


def run() -> dict:
    from kimi.trainer import extract_lessons_from_queue, generate_skill_suggestions
    from kimi.client import get_client

    queue = _read_queue()
    log.info("Queue has %d items.", len(queue))

    client = get_client()
    extracted = extract_lessons_from_queue(client)
    skills    = generate_skill_suggestions(client)
    embedded  = embed_lessons_in_chroma()

    # Read all lessons for OpenClaw sync
    all_lessons = []
    if LESSONS.exists():
        for line in LESSONS.read_text().splitlines():
            try:
                all_lessons.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    sync_to_openclaw(all_lessons)

    return {"extracted": extracted, "skills_suggested": skills, "embedded": embedded}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    result = run()
    print(json.dumps(result, indent=2))
