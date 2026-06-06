#!/usr/bin/env python3
"""
Pre-training guide generator for Jarvis.
Run before learner.py/selftrain.py.
Calls Claude Haiku ONCE per session (minimal cost) to analyze failures and
produce a focused training guide. Falls back to local analysis if no API key.
"""
import json
import os
import sys
import yaml
from datetime import datetime, timedelta
from pathlib import Path

JARVIS_HOME = Path("~/.jarvis").expanduser()
GUIDE_PATH = JARVIS_HOME / "config" / "training_guide.yaml"
SKILLS_DIR = JARVIS_HOME / "skills"

# Candidate paths for interactions log
_INTERACTION_CANDIDATES = [
    JARVIS_HOME / "data" / "interactions.jsonl",
    JARVIS_HOME / "interactions.jsonl",
]


def _interactions_path() -> Path | None:
    for p in _INTERACTION_CANDIDATES:
        if p.exists():
            return p
    return None


def load_recent_bad_interactions(days: int = 7, limit: int = 20) -> list[dict]:
    path = _interactions_path()
    if not path:
        return []
    cutoff = datetime.now() - timedelta(days=days)
    bad = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    rating = r.get("rating", r.get("feedback", r.get("thumb", "")))
                    is_bad = (
                        rating in ("bad", "thumbs_down", -1, 0, "0", "-1")
                        or r.get("corrected")
                        or str(rating).lower() in ("bad", "negative", "dislike")
                    )
                    if is_bad:
                        bad.append({
                            "query": str(r.get("query", r.get("input", r.get("message", ""))))[:200],
                            "response": str(r.get("response", r.get("output", r.get("answer", ""))))[:200],
                            "correction": str(r.get("correction", r.get("correct", "")))[:200],
                            "timestamp": r.get("timestamp", ""),
                        })
                except Exception:
                    pass
    except Exception:
        pass
    return bad[-limit:]


def load_skill_gaps() -> list[dict]:
    gaps_file = SKILLS_DIR / "gaps.jsonl"
    if not gaps_file.exists():
        return []
    gaps = []
    try:
        with open(gaps_file) as f:
            for line in f:
                try:
                    gaps.append(json.loads(line.strip()))
                except Exception:
                    pass
    except Exception:
        pass
    return gaps[-10:]


def extract_focus_areas(guide_text: str) -> list[str]:
    areas = []
    for line in guide_text.split("\n"):
        line = line.strip()
        if line and (line[0].isdigit() or line.startswith(("-", "•", "*"))):
            clean = line.lstrip("0123456789.-•*) ").strip()
            if len(clean) > 5:
                areas.append(clean[:80])
    return areas[:5]


def generate_guide_with_claude(bad: list, gaps: list) -> dict:
    try:
        creds_path = JARVIS_HOME / "config" / "credentials.yaml"
        with open(creds_path) as f:
            creds = yaml.safe_load(f) or {}
        api_key = (
            creds.get("anthropic_api_key")
            or os.environ.get("ANTHROPIC_API_KEY", "")
        )
        if not api_key or api_key in ("", "your_key_here", None):
            print("No Anthropic API key — using local guide generation.")
            return generate_guide_locally(bad, gaps)

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        context = (
            "You are advising Jarvis, a personal AI assistant.\n"
            "Jarvis uses phi4-mini (casual), qwen2.5-coder:7b (code), deepseek-r1:7b (reasoning).\n"
            "It has ChromaDB RAG memory and learns from interactions.\n\n"
            f"Recent bad interactions ({len(bad)} total, showing up to 10):\n"
            f"{json.dumps(bad[:10], indent=2)}\n\n"
            f"Skill gaps detected ({len(gaps)}):\n"
            f"{json.dumps(gaps[:5], indent=2)}\n\n"
            "Write a concise training guide (max 300 words) with:\n"
            "1. Top 3 focus areas for this training session\n"
            "2. Specific topics/facts to add to memory\n"
            "3. Response patterns to reinforce\n"
            "4. Response patterns to avoid\n"
            "5. Three test questions to verify improvement after training\n"
            "Be specific and actionable."
        )

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": context}],
        )
        guide_text = msg.content[0].text
        return {
            "generated_at": datetime.now().isoformat(),
            "model_used": "claude-haiku-4-5",
            "guide": guide_text,
            "focus_areas": extract_focus_areas(guide_text),
            "bad_interaction_count": len(bad),
            "skill_gap_count": len(gaps),
        }
    except Exception as e:
        print(f"Claude guide failed ({e}), falling back to local analysis.")
        return generate_guide_locally(bad, gaps)


def generate_guide_locally(bad: list, gaps: list) -> dict:
    topics: dict[str, int] = {}
    keywords = [
        "code", "deploy", "error", "system", "memory", "project",
        "web", "kali", "docker", "nginx", "vps", "ssh", "git",
        "identity", "jarvis", "who", "what",
    ]
    for b in bad:
        q = b.get("query", "").lower()
        for kw in keywords:
            if kw in q:
                topics[kw] = topics.get(kw, 0) + 1
    for g in gaps:
        topic = g.get("topic", "").lower()
        for kw in keywords:
            if kw in topic:
                topics[kw] = topics.get(kw, 0) + 2  # gaps weigh more

    top_topics = sorted(topics.items(), key=lambda x: -x[1])[:3]
    focus = [t for t, _ in top_topics] if top_topics else ["general QA", "identity", "memory retrieval"]

    guide_text = (
        f"Local analysis of {len(bad)} bad interactions and {len(gaps)} skill gaps.\n\n"
        f"1. Focus areas: {', '.join(focus)}\n"
        "2. Reinforce: concise answers, Jarvis identity, project context\n"
        "3. Avoid: repetition, 'I'm Phi', 'I'm Microsoft', empty responses\n"
        "4. Add to memory: recent project updates, user corrections\n"
        f"5. Test questions: 'Who are you?', 'What is AsthaCash?', 'List active services'"
    )
    return {
        "generated_at": datetime.now().isoformat(),
        "model_used": "local_analysis",
        "guide": guide_text,
        "focus_areas": focus,
        "bad_interaction_count": len(bad),
        "skill_gap_count": len(gaps),
    }


def save_guide(guide: dict) -> None:
    GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GUIDE_PATH, "w") as f:
        yaml.dump(guide, f, default_flow_style=False, allow_unicode=True)
    print(f"Training guide saved → {GUIDE_PATH}")
    print(f"Focus areas: {guide.get('focus_areas', [])}")


def main() -> None:
    print("=== Jarvis Pre-Training Guide Generator ===")
    bad = load_recent_bad_interactions()
    gaps = load_skill_gaps()
    print(f"Found: {len(bad)} bad interactions, {len(gaps)} skill gaps")

    if not bad and not gaps:
        print("No failures detected — Jarvis is performing well! Saving blank guide.")
        save_guide({
            "generated_at": datetime.now().isoformat(),
            "model_used": "none",
            "guide": "No issues found. Continue current training approach.",
            "focus_areas": [],
            "bad_interaction_count": 0,
            "skill_gap_count": 0,
        })
        return

    guide = generate_guide_with_claude(bad, gaps)
    save_guide(guide)
    print("\n--- Guide ---")
    print(guide.get("guide", ""))


if __name__ == "__main__":
    main()
