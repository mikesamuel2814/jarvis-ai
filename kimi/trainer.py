"""
Kimi cloud training pipeline — runs every 6 hours via train_v2.sh.

Tasks:
  1. Analyze accumulated routing decisions → extract patterns
  2. Extract structured lessons from thumbs-down interactions
  3. Generate OpenClaw skill suggestions from golden examples
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from kimi.client import KimiClient, get_client

log = logging.getLogger("jarvis.kimi.trainer")

JARVIS_HOME  = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
DECISIONS    = JARVIS_HOME / "data" / "routing_decisions.jsonl"
LESSONS      = JARVIS_HOME / "data" / "lessons.jsonl"
QUEUE        = JARVIS_HOME / "data" / "learning_queue.jsonl"
GOLDEN       = JARVIS_HOME / "data" / "golden_examples.jsonl"
SKILLS_OUT   = JARVIS_HOME / "data" / "extracted_skills.jsonl"


def _read_jsonl(path: Path, limit: int = 200) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text().strip().splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def analyze_routing_decisions(client: KimiClient) -> dict:
    """Ask Kimi to review recent routing decisions and flag misfires."""
    decisions = _read_jsonl(DECISIONS, limit=100)
    if not decisions:
        log.info("No routing decisions to analyze.")
        return {}

    prompt = f"""Analyze these Jarvis routing decisions (Edge/Hybrid/Cloud tier selection).
Identify:
1. Queries that were over-routed to Cloud (should have been Edge/Hybrid)
2. Queries that were under-routed to Edge (needed Cloud reasoning)
3. Patterns that could improve routing accuracy
4. Estimated cost savings if routing was optimal

Decisions (last 100):
{json.dumps(decisions, indent=2)}

Return JSON: {{"over_routed": [...], "under_routed": [...], "patterns": [...], "recommendations": [...]}}"""

    try:
        result = client.extract_json(prompt)
        log.info("Routing analysis complete: %d recommendations", len(result.get("recommendations", [])))
        return result
    except Exception as e:
        log.error("Routing analysis failed: %s", e)
        return {}


def extract_lessons_from_queue(client: KimiClient) -> int:
    """Process thumbs-down items in learning queue → structured lessons."""
    queue = _read_jsonl(QUEUE)
    bad   = [item for item in queue if item.get("rating") == "thumbs_down"]
    if not bad:
        log.info("No thumbs-down items in queue.")
        return 0

    extracted = 0
    for item in bad[:20]:  # Process max 20 per cycle to control cost
        prompt = f"""Analyze this failed Jarvis interaction and extract a structured lesson.

Query: {item.get("query", "")}
Response: {item.get("response", "")}
Correction: {item.get("correction", "")}

Extract:
1. What went wrong (category: wrong_fact / wrong_tone / missing_context / wrong_model_tier / other)
2. Correct principle or rule
3. How to apply this in future
4. Keywords that should trigger retrieval of this lesson

Return JSON: {{"category": "...", "principle": "...", "application": "...", "keywords": [...]}}"""

        try:
            lesson = client.extract_json(prompt, system="You are a learning system. Return valid JSON only.")
            lesson["source_query"]    = item.get("query", "")
            lesson["source_response"] = item.get("response", "")[:500]
            lesson["ts"]              = item.get("ts", 0)
            lesson["priority"]        = 5  # Lessons are high priority

            with open(LESSONS, "a") as f:
                f.write(json.dumps(lesson) + "\n")
            extracted += 1
        except Exception as e:
            log.error("Lesson extraction failed for item: %s", e)

    log.info("Extracted %d lessons from queue.", extracted)
    return extracted


def generate_skill_suggestions(client: KimiClient) -> int:
    """Scan golden examples → suggest new OpenClaw skill ideas."""
    golden = _read_jsonl(GOLDEN, limit=50)
    if not golden:
        return 0

    prompt = f"""Review these high-quality Jarvis interactions (user rated 👍).
Identify recurring patterns that could become reusable OpenClaw skills.

Interactions:
{json.dumps(golden[:30], indent=2)}

For each potential skill, return:
- name: slug (lowercase, hyphenated)
- description: what it does
- trigger_phrases: what user says to invoke it
- steps: what Jarvis should do
- required_tools: which Jarvis actions/endpoints it needs

Return JSON array of skill suggestions."""

    try:
        suggestions = client.extract_json(prompt, system="Return a JSON array of skill objects.")
        if not isinstance(suggestions, list):
            suggestions = suggestions.get("skills", [])

        with open(SKILLS_OUT, "a") as f:
            for skill in suggestions:
                f.write(json.dumps(skill) + "\n")

        log.info("Generated %d skill suggestions.", len(suggestions))
        return len(suggestions)
    except Exception as e:
        log.error("Skill generation failed: %s", e)
        return 0


def run_training_cycle() -> dict:
    """Full 6-hour training cycle."""
    log.info("Starting Kimi cloud training cycle...")
    client = get_client()

    routing = analyze_routing_decisions(client)
    lessons  = extract_lessons_from_queue(client)
    skills   = generate_skill_suggestions(client)
    cost     = client.session_cost()

    summary = {
        "routing_recommendations": len(routing.get("recommendations", [])),
        "lessons_extracted": lessons,
        "skills_suggested": skills,
        "session_cost": cost,
    }
    log.info("Training cycle complete: %s", summary)
    return summary


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    result = run_training_cycle()
    print(json.dumps(result, indent=2))
