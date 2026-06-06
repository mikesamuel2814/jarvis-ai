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


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _routing_summary() -> dict:
    """Per-tier counts + avg latency from routing_decisions.jsonl."""
    counts = {"edge": 0, "hybrid": 0, "cloud": 0}
    lat_sum = {"edge": 0.0, "hybrid": 0.0, "cloud": 0.0}
    lat_n = {"edge": 0, "hybrid": 0, "cloud": 0}
    overall_lat_sum = 0.0
    overall_lat_n = 0
    for d in _iter_jsonl(JARVIS_HOME / "data" / "routing_decisions.jsonl"):
        tier = d.get("tier", "hybrid")
        if tier not in counts:
            tier = "hybrid"
        counts[tier] += 1
        lat = d.get("latency_ms")
        if isinstance(lat, (int, float)):
            lat_sum[tier] += lat
            lat_n[tier] += 1
            overall_lat_sum += lat
            overall_lat_n += 1
    avg_latency = {
        t: round(lat_sum[t] / lat_n[t], 1) if lat_n[t] else 0.0 for t in counts
    }
    avg_latency["overall"] = (
        round(overall_lat_sum / overall_lat_n, 1) if overall_lat_n else 0.0
    )
    return {"counts": counts, "avg_latency_ms": avg_latency}


def _count_routing(tier: str) -> int:
    count = 0
    for d in _iter_jsonl(JARVIS_HOME / "data" / "routing_decisions.jsonl"):
        if d.get("tier") == tier:
            count += 1
    return count


def _feedback_summary() -> dict:
    """👍/👎 counts + ratio from interactions.jsonl (rating updated in place)."""
    up = down = unrated = 0
    for it in _iter_jsonl(JARVIS_HOME / "data" / "interactions.jsonl"):
        r = it.get("rating")
        if r == "good":
            up += 1
        elif r == "bad":
            down += 1
        else:
            unrated += 1
    rated = up + down
    return {
        "thumbs_up": up,
        "thumbs_down": down,
        "unrated": unrated,
        "up_ratio": round(up / rated, 3) if rated else 0.0,
    }


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

    routing  = _routing_summary()
    rc       = routing["counts"]
    edge     = rc["edge"]
    hybrid   = rc["hybrid"]
    cloud    = rc["cloud"]
    total    = edge + hybrid + cloud
    feedback = _feedback_summary()

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
            "avg_latency_ms":          routing["avg_latency_ms"],
        },
        "feedback": feedback,
        "learning": {
            "total_interactions":    _count_jsonl(interactions),
            "queue_size":            _count_jsonl(queue),
            "lessons_learned":       _count_jsonl(lessons),
            "sync_status":           _sync_status(),
        },
        "cost": _kimi_cost(),
    }


if __name__ == "__main__":
    print(json.dumps(dashboard(), indent=2))
