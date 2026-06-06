#!/usr/bin/env python3
"""
Jarvis 2.0 Metrics Dashboard
GET /metrics endpoint data and CLI output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))


def _count_jsonl(path: Path, filter_type: str | None = None) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        if filter_type:
            try:
                if json.loads(line).get("type") == filter_type:
                    count += 1
            except json.JSONDecodeError:
                pass
        else:
            count += 1
    return count


def _count_routing(tier: str) -> int:
    path = JARVIS_HOME / "data" / "routing_decisions.jsonl"
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text().splitlines():
        try:
            if json.loads(line).get("tier") == tier:
                count += 1
        except json.JSONDecodeError:
            pass
    return count


def _chroma_count() -> int:
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
        col = client.get_or_create_collection("jarvis_memory")
        return col.count()
    except Exception:
        return -1


def _kimi_cost() -> dict:
    try:
        from kimi.cost_tracker import today_cost, month_cost
        return {"today": today_cost(), "month": month_cost()}
    except Exception:
        return {}


def _sync_status() -> dict:
    state_file = JARVIS_HOME / "data" / "memory_sync_state.json"
    if not state_file.exists():
        return {"status": "never_synced"}
    return json.loads(state_file.read_text())


def dashboard() -> dict:
    interactions = JARVIS_HOME / "data" / "interactions.jsonl"
    queue        = JARVIS_HOME / "data" / "learning_queue.jsonl"
    golden       = JARVIS_HOME / "data" / "golden_examples.jsonl"
    lessons      = JARVIS_HOME / "data" / "lessons.jsonl"
    corrections  = JARVIS_HOME / "data" / "corrections.jsonl"
    decisions    = JARVIS_HOME / "data" / "routing_decisions.jsonl"

    edge   = _count_routing("edge")
    hybrid = _count_routing("hybrid")
    cloud  = _count_routing("cloud")
    total  = edge + hybrid + cloud

    return {
        "memory": {
            "total_chunks":          _chroma_count(),
            "golden_examples":       _count_jsonl(golden),
            "lessons_learned":       _count_jsonl(lessons),
            "corrections_applied":   _count_jsonl(corrections),
        },
        "brain": {
            "total_routing_decisions": total,
            "edge_queries":            edge,
            "hybrid_queries":          hybrid,
            "cloud_queries":           cloud,
            "edge_pct":                round(edge / max(total, 1) * 100, 1),
            "cloud_pct":               round(cloud / max(total, 1) * 100, 1),
        },
        "learning": {
            "total_interactions":    _count_jsonl(interactions),
            "queue_size":            _count_jsonl(queue),
            "sync_status":           _sync_status(),
        },
        "cost": _kimi_cost(),
    }


if __name__ == "__main__":
    print(json.dumps(dashboard(), indent=2))
