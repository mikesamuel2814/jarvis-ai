"""
Extract recurring patterns from golden examples and routing decisions.
Used by the weekly evolution cycle (selftrain_v2.py).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("jarvis.learner.patterns")

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
GOLDEN      = JARVIS_HOME / "data" / "golden_examples.jsonl"
DECISIONS   = JARVIS_HOME / "data" / "routing_decisions.jsonl"


def routing_accuracy() -> dict:
    """
    Compute routing accuracy from routing_decisions.jsonl.
    Accuracy is estimated by correlating tier with thumbs-up rate in interactions.
    """
    if not DECISIONS.exists():
        return {"edge": 0, "hybrid": 0, "cloud": 0, "total": 0}

    counts: dict[str, int] = {"edge": 0, "hybrid": 0, "cloud": 0}
    for line in DECISIONS.read_text().splitlines():
        try:
            d = json.loads(line)
            tier = d.get("tier", "hybrid")
            if tier in counts:
                counts[tier] += 1
        except json.JSONDecodeError:
            pass

    total = sum(counts.values())
    return {**counts, "total": total}


def extract_skill_patterns(limit: int = 100) -> list[dict]:
    """Return recurring query patterns from golden examples."""
    if not GOLDEN.exists():
        return []

    items = []
    for line in GOLDEN.read_text().splitlines()[-limit:]:
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    # Group by tier
    by_tier: dict[str, list] = {"edge": [], "hybrid": [], "cloud": []}
    for item in items:
        tier = item.get("tier", "hybrid")
        if tier in by_tier:
            by_tier[tier].append(item.get("query", "")[:120])

    patterns = []
    for tier, queries in by_tier.items():
        if queries:
            patterns.append({"tier": tier, "sample_queries": queries[:10], "count": len(queries)})
    return patterns
