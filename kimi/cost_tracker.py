"""
Kimi API cost tracker — reads kimi_api.log, aggregates by day/month.
Run: python3 ~/.jarvis/kimi/cost_tracker.py [--audit] [--today] [--month]
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
LOG_FILE    = JARVIS_HOME / "logs" / "kimi_api.log"
COST_FILE   = JARVIS_HOME / "data" / "kimi_cost_history.jsonl"

INPUT_PRICE  = 0.95   # per million tokens
OUTPUT_PRICE = 4.00


def _load_entries() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    entries = []
    for line in LOG_FILE.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def today_cost() -> dict:
    now = datetime.now(timezone.utc)
    today_start = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp())
    entries = [e for e in _load_entries() if e.get("ts", 0) >= today_start]
    inp  = sum(e.get("input_tokens", 0) for e in entries)
    out  = sum(e.get("output_tokens", 0) for e in entries)
    cost = inp / 1_000_000 * INPUT_PRICE + out / 1_000_000 * OUTPUT_PRICE
    return {"calls": len(entries), "input_tokens": inp, "output_tokens": out, "cost_usd": round(cost, 4)}


def month_cost() -> dict:
    now = datetime.now(timezone.utc)
    month_start = int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp())
    entries = [e for e in _load_entries() if e.get("ts", 0) >= month_start]
    inp  = sum(e.get("input_tokens", 0) for e in entries)
    out  = sum(e.get("output_tokens", 0) for e in entries)
    cost = inp / 1_000_000 * INPUT_PRICE + out / 1_000_000 * OUTPUT_PRICE
    return {"calls": len(entries), "input_tokens": inp, "output_tokens": out, "cost_usd": round(cost, 4)}


def full_audit() -> dict:
    entries = _load_entries()
    by_day: dict[str, dict] = {}
    for e in entries:
        day = datetime.fromtimestamp(e.get("ts", 0), tz=timezone.utc).strftime("%Y-%m-%d")
        r = by_day.setdefault(day, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        r["calls"] += 1
        r["input_tokens"]  += e.get("input_tokens", 0)
        r["output_tokens"] += e.get("output_tokens", 0)
        r["cost_usd"] = round(
            r["cost_usd"]
            + e.get("input_tokens", 0) / 1_000_000 * INPUT_PRICE
            + e.get("output_tokens", 0) / 1_000_000 * OUTPUT_PRICE,
            6,
        )
    return by_day


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kimi API cost tracker")
    parser.add_argument("--today",  action="store_true")
    parser.add_argument("--month",  action="store_true")
    parser.add_argument("--audit",  action="store_true")
    args = parser.parse_args()

    if args.today or not (args.month or args.audit):
        print("=== TODAY ===")
        print(json.dumps(today_cost(), indent=2))

    if args.month:
        print("=== THIS MONTH ===")
        print(json.dumps(month_cost(), indent=2))

    if args.audit:
        print("=== FULL AUDIT (by day) ===")
        print(json.dumps(full_audit(), indent=2))
