#!/usr/bin/env python3
"""
Jarvis Core Thinking Engine — Next-Level Intelligence Layer

This is Jarvis's "prefrontal cortex" — it adds structured reasoning, goal tracking,
confidence scoring, proactive decision-making, and context accumulation on top of
the raw model calls in brain.py.

Architecture:
  1. ThinkingChain — multi-step reasoning (Observe → Analyze → Plan → Execute → Verify)
  2. ContextAccumulator — rolling window of Mike's session context, goals, patterns
  3. ConfidenceScorer — rates response quality, triggers web search / escalation
  4. ProactiveAdvisor — surfaces actionable insights Mike hasn't asked for yet
  5. GoalTracker — tracks Mike's active goals, nudges completion

Integration points:
  - api.py: wrap build_messages() to inject thinking context
  - brain.py: call think_before_answer() on CLOUD/HYBRID queries
  - decision_engine.py: call get_proactive_decisions() every 5min
  - telegram_bot.py: /think command for explicit deep reasoning
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any

JARVIS_HOME   = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
THINKING_DIR  = JARVIS_HOME / "data" / "thinking"
GOALS_FILE    = THINKING_DIR / "goals.json"
CONTEXT_FILE  = THINKING_DIR / "context_state.json"
DECISIONS_FILE = THINKING_DIR / "proactive_decisions.json"
LOG_FILE      = JARVIS_HOME / "logs" / "thinking.log"

THINKING_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [thinking] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

_lock = threading.Lock()

# ── Default Mike goals (seed — Jarvis learns more over time) ──────────────────
DEFAULT_GOALS = {
    "version": 1,
    "active": [
        {
            "id": "goal_asthacash",
            "title": "Keep AsthaCash Payment Gateway stable and growing",
            "description": "Monitor VPS health, uptime, agent connections, transaction volume",
            "priority": 10,
            "category": "business",
            "created": "2026-06-06",
            "tags": ["asthacash", "payment", "vps", "pm2"],
            "next_actions": [
                "Check PM2 process health daily",
                "Monitor WebSocket agent connections",
                "Review transaction logs weekly",
            ],
        },
        {
            "id": "goal_starline",
            "title": "Ship Starline/conztru real estate platform",
            "description": "CI/CD pipeline, deploy stability, feature delivery",
            "priority": 9,
            "category": "business",
            "created": "2026-06-06",
            "tags": ["starline", "conztru", "react", "postgresql", "deploy"],
            "next_actions": [
                "Ensure CI/CD pipeline runs green",
                "Review PostgreSQL performance",
                "Ship pending features",
            ],
        },
        {
            "id": "goal_jarvis",
            "title": "Develop Jarvis into a fully autonomous AI brain",
            "description": "Self-learning, proactive decisions, domain expertise",
            "priority": 8,
            "category": "ai",
            "created": "2026-06-06",
            "tags": ["jarvis", "ai", "learning", "autonomy"],
            "next_actions": [
                "Grow skillset.json rules organically",
                "Increase learned_safe actions over time",
                "Improve response quality and Sir persona consistency",
            ],
        },
        {
            "id": "goal_hardware",
            "title": "Upgrade to NVIDIA Project DIGITS / RTX Spark (128GB)",
            "description": "Current RTX 3050 6GB limits model size to 7B",
            "priority": 6,
            "category": "infrastructure",
            "created": "2026-06-06",
            "tags": ["hardware", "vram", "upgrade", "digits"],
            "next_actions": [
                "Research availability and pricing",
                "Plan migration of Ollama models",
            ],
        },
    ],
    "completed": [],
    "metadata": {"last_updated": datetime.utcnow().isoformat()},
}

DEFAULT_CONTEXT = {
    "version": 1,
    "session": {
        "start_ts": int(time.time()),
        "query_count": 0,
        "topics_discussed": [],
        "actions_taken": [],
        "errors_encountered": [],
    },
    "persistent": {
        "mike_mood": "neutral",  # detected from message tone
        "current_focus": None,   # inferred from recent queries
        "last_deploy": None,
        "last_system_check": None,
        "recent_alerts": [],
    },
    "patterns": {
        "peak_usage_hour": None,
        "most_asked_topics": {},
        "common_action_sequences": [],
    },
}


# ── Persistence helpers ───────────────────────────────────────────────────────

def _load_goals() -> dict:
    if GOALS_FILE.exists():
        try:
            return json.loads(GOALS_FILE.read_text())
        except Exception:
            pass
    g = DEFAULT_GOALS.copy()
    _save_goals(g)
    return g


def _save_goals(goals: dict) -> None:
    goals.setdefault("metadata", {})["last_updated"] = datetime.utcnow().isoformat()
    GOALS_FILE.write_text(json.dumps(goals, indent=2))


def _load_context() -> dict:
    if CONTEXT_FILE.exists():
        try:
            return json.loads(CONTEXT_FILE.read_text())
        except Exception:
            pass
    return DEFAULT_CONTEXT.copy()


def _save_context(ctx: dict) -> None:
    CONTEXT_FILE.write_text(json.dumps(ctx, indent=2))


def _load_decisions() -> list:
    if DECISIONS_FILE.exists():
        try:
            return json.loads(DECISIONS_FILE.read_text())
        except Exception:
            pass
    return []


def _save_decisions(decisions: list) -> None:
    # Keep only last 50 decisions
    DECISIONS_FILE.write_text(json.dumps(decisions[-50:], indent=2))


# ── Confidence Scorer ─────────────────────────────────────────────────────────

_WEAK_PATTERNS = re.compile(
    r"\b(i don'?t know|not sure|i'?m not certain|unclear|i lack|no information|"
    r"outdated|my knowledge|as of my (training|knowledge)|i cannot confirm|"
    r"i'?m unable to verify|please verify|search online|limited information|"
    r"i don'?t have access|i have no way|cannot determine)\b",
    re.IGNORECASE,
)

_HALLUCINATION_PATTERNS = re.compile(
    r"\b(certainly|absolutely|definitely|i'?m confident|without a doubt|"
    r"100% sure|guaranteed|i know for certain)\b",
    re.IGNORECASE,
)

_ACTIONABLE_PATTERNS = re.compile(
    r"\b(here'?s what|run|execute|check|use|try|install|configure|restart|"
    r"add|remove|update|deploy|fix|create|write)\b",
    re.IGNORECASE,
)


def score_response(query: str, response: str) -> dict:
    """
    Score a response on multiple dimensions.
    Returns dict with scores 0-10 and flags.
    """
    if not response:
        return {"total": 0, "flags": ["empty_response"]}

    flags = []
    r = response.strip()
    rl = r.lower()

    # Completeness: length relative to query complexity
    query_complexity = len(query.split())
    resp_words = len(r.split())
    if resp_words < 5:
        completeness = 1
        flags.append("too_short")
    elif resp_words < 15 and query_complexity > 5:
        completeness = 4
    else:
        completeness = min(10, resp_words // 20 + 5)

    # Persona: starts with or contains "Sir"
    persona = 10 if "Sir" in r[:30] else (6 if "Sir" in r else 2)
    if persona < 6:
        flags.append("missing_sir")

    # Uncertainty: penalise weak responses
    weak_matches = len(_WEAK_PATTERNS.findall(r))
    uncertainty = max(0, 10 - (weak_matches * 3))
    if weak_matches > 0:
        flags.append("uncertain")

    # Actionability: does it give Mike something to do?
    actionable_matches = len(_ACTIONABLE_PATTERNS.findall(r))
    actionability = min(10, actionable_matches * 2 + 3)

    # Over-confidence: penalise if model asserts things it can't know
    if _HALLUCINATION_PATTERNS.search(r) and weak_matches > 0:
        flags.append("potential_hallucination")

    # No filler openers
    if re.match(r"^(certainly|of course|sure|absolutely|happy to|great question)", rl):
        flags.append("filler_opener")
        persona -= 2

    total = (completeness * 0.3 + persona * 0.25 + uncertainty * 0.25 + actionability * 0.2)
    total = round(min(10, max(0, total)), 1)

    return {
        "total": total,
        "completeness": completeness,
        "persona": persona,
        "uncertainty": uncertainty,
        "actionability": actionability,
        "flags": flags,
        "needs_web_search": "uncertain" in flags,
        "needs_escalation": total < 4.0,
    }


# ── Context Accumulator ───────────────────────────────────────────────────────

_TOPIC_PATTERNS = {
    "asthacash": re.compile(r"\b(asthacash|payment.?gateway|gateway.?backend|agent|transaction|websocket)\b", re.IGNORECASE),
    "starline":  re.compile(r"\b(starline|conztru|real.?estate|api.?server|postgresql)\b", re.IGNORECASE),
    "jarvis":    re.compile(r"\b(jarvis|ollama|deepseek|chroma|memory|train|learn|skill)\b", re.IGNORECASE),
    "devops":    re.compile(r"\b(pm2|nginx|vps|ssh|docker|deploy|ci.?cd|github)\b", re.IGNORECASE),
    "code":      re.compile(r"\b(bug|fix|error|function|class|component|refactor|build)\b", re.IGNORECASE),
    "hardware":  re.compile(r"\b(gpu|vram|cpu|ram|disk|memory|nvidia|rtx)\b", re.IGNORECASE),
    "security":  re.compile(r"\b(kali|pentest|nmap|exploit|scan|vuln|attack|recon)\b", re.IGNORECASE),
}

_MOOD_POSITIVE = re.compile(r"\b(great|thanks|perfect|excellent|good job|nice|well done|awesome)\b", re.IGNORECASE)
_MOOD_URGENT   = re.compile(r"\b(urgent|asap|emergency|critical|broken|down|fix now|immediately)\b", re.IGNORECASE)
_MOOD_NEGATIVE = re.compile(r"\b(wrong|bad|terrible|awful|not working|failed|still broken)\b", re.IGNORECASE)


def update_context(query: str, response: str, score: dict) -> None:
    """Update rolling context state after each interaction. Non-blocking."""
    def _update():
        try:
            with _lock:
                ctx = _load_context()
                s = ctx["session"]
                s["query_count"] += 1

                # Detect topics
                topics = [t for t, p in _TOPIC_PATTERNS.items() if p.search(query)]
                for t in topics:
                    if t not in s["topics_discussed"]:
                        s["topics_discussed"].append(t)
                    ctx["patterns"]["most_asked_topics"][t] = \
                        ctx["patterns"]["most_asked_topics"].get(t, 0) + 1

                # Detect current focus (most recent topic)
                if topics:
                    ctx["persistent"]["current_focus"] = topics[0]

                # Detect mood
                if _MOOD_URGENT.search(query):
                    ctx["persistent"]["mike_mood"] = "urgent"
                elif _MOOD_POSITIVE.search(query):
                    ctx["persistent"]["mike_mood"] = "positive"
                elif _MOOD_NEGATIVE.search(query):
                    ctx["persistent"]["mike_mood"] = "frustrated"
                else:
                    ctx["persistent"]["mike_mood"] = "neutral"

                # Flag errors for tracking
                if score.get("needs_escalation"):
                    ctx["session"]["errors_encountered"].append({
                        "ts": int(time.time()),
                        "query": query[:80],
                        "score": score["total"],
                    })
                    ctx["session"]["errors_encountered"] = \
                        ctx["session"]["errors_encountered"][-20:]

                ctx["persistent"]["last_system_check"] = int(time.time())
                _save_context(ctx)
        except Exception as e:
            log.debug("update_context failed: %s", e)

    threading.Thread(target=_update, daemon=True).start()


def get_context_block() -> str:
    """Format current context state for injection into system prompt."""
    try:
        ctx = _load_context()
        p = ctx["persistent"]
        s = ctx["session"]

        parts = []
        if p.get("current_focus"):
            parts.append(f"Current focus: {p['current_focus']}")
        if p.get("mike_mood") and p["mike_mood"] != "neutral":
            parts.append(f"Mike's mood: {p['mike_mood']}")
        if s.get("topics_discussed"):
            parts.append(f"Session topics: {', '.join(s['topics_discussed'][-5:])}")
        if p.get("recent_alerts"):
            parts.append(f"Recent alerts: {'; '.join(p['recent_alerts'][-3:])}")

        top_topics = sorted(
            ctx["patterns"]["most_asked_topics"].items(),
            key=lambda x: -x[1]
        )[:3]
        if top_topics:
            parts.append(f"Mike's top interests: {', '.join(t for t,_ in top_topics)}")

        if not parts:
            return ""
        return "--- JARVIS CONTEXT STATE ---\n" + "\n".join(parts) + "\n--- END CONTEXT ---"
    except Exception:
        return ""


# ── Goal Tracker ──────────────────────────────────────────────────────────────

def get_goals_block() -> str:
    """Return relevant active goals for system prompt injection."""
    try:
        goals = _load_goals()
        active = goals.get("active", [])[:3]  # top 3 by priority
        if not active:
            return ""
        lines = ["--- MIKE'S ACTIVE GOALS (stay aligned) ---"]
        for g in active:
            lines.append(f"• [{g['category'].upper()}] {g['title']} (p{g['priority']})")
        lines.append("--- END GOALS ---")
        return "\n".join(lines)
    except Exception:
        return ""


def add_goal(title: str, description: str, priority: int = 5, category: str = "general") -> bool:
    """Add a new goal for Mike (called from /objective command or AI detection)."""
    try:
        with _lock:
            goals = _load_goals()
            import uuid
            goals["active"].append({
                "id": f"goal_{uuid.uuid4().hex[:8]}",
                "title": title,
                "description": description,
                "priority": priority,
                "category": category,
                "created": date.today().isoformat(),
                "tags": [],
                "next_actions": [],
            })
            goals["active"].sort(key=lambda g: -g["priority"])
            _save_goals(goals)
        return True
    except Exception as e:
        log.warning("add_goal failed: %s", e)
        return False


def complete_goal(goal_id: str) -> bool:
    """Mark a goal as completed."""
    try:
        with _lock:
            goals = _load_goals()
            active = goals.get("active", [])
            for i, g in enumerate(active):
                if g["id"] == goal_id:
                    g["completed_ts"] = datetime.utcnow().isoformat()
                    goals.setdefault("completed", []).append(g)
                    goals["active"].pop(i)
                    _save_goals(goals)
                    return True
        return False
    except Exception:
        return False


# ── Proactive Decision Engine ─────────────────────────────────────────────────

def get_proactive_decisions(system_state: dict | None = None) -> list[dict]:
    """
    Generate proactive decisions/suggestions Jarvis should surface to Mike.
    Called every 5 minutes from decision_engine.py.

    Returns list of: {"message": str, "priority": int, "action": str|None, "category": str}
    """
    decisions = []
    now = time.time()

    try:
        import psutil

        # 1. Disk pressure — warn at 80%, critical at 90%
        try:
            disk = psutil.disk_usage("/")
            if disk.percent >= 90:
                decisions.append({
                    "message": f"🔴 Disk CRITICAL: {disk.percent}% used ({disk.free // (1<<30)}GB free)",
                    "priority": 10,
                    "action": "disk",
                    "category": "system",
                    "cooldown_key": "disk_critical",
                })
            elif disk.percent >= 80:
                decisions.append({
                    "message": f"⚠️ Disk high: {disk.percent}% used. Consider cleanup.",
                    "priority": 7,
                    "action": "disk",
                    "category": "system",
                    "cooldown_key": "disk_warning",
                })
        except Exception:
            pass

        # 2. RAM pressure
        try:
            ram = psutil.virtual_memory()
            if ram.percent >= 90:
                decisions.append({
                    "message": f"🔴 RAM critical: {ram.percent}% used ({ram.available // (1<<30)}GB free)",
                    "priority": 9,
                    "action": "memory",
                    "category": "system",
                    "cooldown_key": "ram_critical",
                })
        except Exception:
            pass

        # 3. GPU VRAM saturation
        try:
            import subprocess
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                parts = r.stdout.strip().split(",")
                used, total = int(parts[0].strip()), int(parts[1].strip())
                pct = (used / total) * 100
                if pct >= 95:
                    decisions.append({
                        "message": f"⚠️ VRAM near-full: {used}/{total}MB ({pct:.0f}%). May OOM on next model load.",
                        "priority": 8,
                        "action": "gpu",
                        "category": "hardware",
                        "cooldown_key": "vram_warning",
                    })
        except Exception:
            pass

    except ImportError:
        pass

    # 4. Context-based suggestions
    try:
        ctx = _load_context()
        mood = ctx["persistent"].get("mike_mood", "neutral")
        focus = ctx["persistent"].get("current_focus")
        session_queries = ctx["session"].get("query_count", 0)

        # Suggest daily briefing if no briefing yet today
        last_check = ctx["persistent"].get("last_system_check", 0)
        hours_since = (now - last_check) / 3600
        if hours_since > 6 and session_queries == 0:
            decisions.append({
                "message": "📋 Morning briefing ready. Type /briefing for today's summary.",
                "priority": 5,
                "action": None,
                "category": "briefing",
                "cooldown_key": "morning_briefing",
            })

        # Frustrated mood — proactively offer help
        if mood == "urgent" or mood == "frustrated":
            if focus:
                decisions.append({
                    "message": f"🔍 Sir is dealing with something urgent on {focus}. Standing by for immediate assistance.",
                    "priority": 8,
                    "action": None,
                    "category": "support",
                    "cooldown_key": "urgent_mode",
                })
    except Exception:
        pass

    # 5. Goal-based nudges (once per day per goal)
    try:
        goals = _load_goals()
        for g in goals.get("active", [])[:2]:  # top 2 goals only
            if g.get("next_actions"):
                next_action = g["next_actions"][0]
                decisions.append({
                    "message": f"🎯 Goal reminder: *{g['title']}*\n→ Next action: {next_action}",
                    "priority": g["priority"] - 4,  # background priority
                    "action": None,
                    "category": "goals",
                    "cooldown_key": f"goal_{g['id']}",
                })
    except Exception:
        pass

    # Sort by priority
    decisions.sort(key=lambda d: -d.get("priority", 0))
    return decisions


# ── Thinking Chain (multi-step reasoning) ─────────────────────────────────────

def think_before_answer(query: str, rag_context: str = "") -> str:
    """
    Multi-step reasoning chain for complex queries.
    Returns enriched context block to prepend to rag_context.

    Steps: Observe → Classify → Retrieve Goals → Plan Response → Self-check

    This runs synchronously for CLOUD/HYBRID queries where latency is already high.
    EDGE queries skip this to stay fast.
    """
    try:
        goals_block = get_goals_block()
        ctx_block   = get_context_block()

        # Classify the query intent
        intent = _classify_intent(query)

        # Build thinking context
        parts = []
        if ctx_block:
            parts.append(ctx_block)
        if goals_block:
            parts.append(goals_block)

        # Add intent-specific guidance
        if intent == "decision":
            parts.append(
                "THINKING DIRECTIVE: Mike is asking for a decision/recommendation. "
                "Give ONE clear recommendation with brief reasoning. "
                "Consider his goals and current system state. "
                "Be decisive — he needs action, not analysis paralysis."
            )
        elif intent == "urgent_fix":
            parts.append(
                "THINKING DIRECTIVE: URGENT fix requested. "
                "Skip preamble. Give the exact fix command/steps first. "
                "Brief context after. Maximum 5 steps."
            )
        elif intent == "project_question":
            parts.append(
                "THINKING DIRECTIVE: This is about AsthaCash or Starline. "
                "Reference actual code paths, PM2 process names, and stack details. "
                "Be specific — Mike built this, he knows if you're vague."
            )
        elif intent == "planning":
            parts.append(
                "THINKING DIRECTIVE: Mike wants a plan. "
                "Give a numbered step list. Mark each step as [AUTO], [CONFIRM], or [APPROVE] "
                "based on Jarvis's ability to execute it. Offer to run [AUTO] steps immediately."
            )

        return "\n\n".join(parts)
    except Exception as e:
        log.debug("think_before_answer failed: %s", e)
        return ""


def _classify_intent(query: str) -> str:
    """Classify query intent for thinking directive selection."""
    ql = query.lower()

    if any(w in ql for w in ["should i", "what do you think", "recommend", "best way", "decide", "choose"]):
        return "decision"
    if any(w in ql for w in ["urgent", "asap", "broken", "down", "fix", "error", "crash"]):
        return "urgent_fix"
    if any(w in ql for w in ["asthacash", "starline", "payment", "conztru", "gateway", "agent"]):
        return "project_question"
    if any(w in ql for w in ["plan", "steps", "how to", "set up", "implement", "create", "build"]):
        return "planning"
    return "general"


# ── Daily Briefing Generator ──────────────────────────────────────────────────

def generate_briefing(include_web: bool = True) -> str:
    """
    Generate Sir's daily intelligence briefing.
    Synthesises: system health, projects, goals, recent patterns, web news.
    """
    sections = []
    now = datetime.now()
    sections.append(f"📋 *Jarvis Daily Briefing* — {now.strftime('%A, %d %b %Y %H:%M')} (Dhaka)")

    # 1. System health
    try:
        import psutil, subprocess
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        gpu_info = ""
        if r.returncode == 0:
            parts = r.stdout.strip().split(",")
            gpu_info = f" | GPU: {parts[0].strip()}/{parts[1].strip()}MB VRAM, {parts[2].strip()}°C"

        health_icon = "✅" if ram.percent < 80 and disk.percent < 80 else "⚠️"
        sections.append(
            f"\n{health_icon} *System Health*\n"
            f"  RAM: {ram.percent}% ({ram.available // (1<<30)}GB free) | "
            f"Disk: {disk.percent}% ({disk.free // (1<<30)}GB free){gpu_info}"
        )
    except Exception:
        sections.append("\n❓ *System Health*: check unavailable")

    # 2. Services
    try:
        import subprocess
        svcs = {"jarvis": "Jarvis API", "jarvis-telegram": "Telegram Bot", "ollama": "Ollama"}
        svc_status = []
        for svc, name in svcs.items():
            r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=3)
            icon = "✅" if r.stdout.strip() == "active" else "🔴"
            svc_status.append(f"{icon} {name}")
        sections.append("\n⚙️ *Services*\n  " + " | ".join(svc_status))
    except Exception:
        pass

    # 3. Active goals progress
    try:
        goals = _load_goals()
        active = goals.get("active", [])[:3]
        if active:
            lines = ["\n🎯 *Active Goals*"]
            for g in active:
                next_act = g["next_actions"][0] if g.get("next_actions") else "(no next action)"
                lines.append(f"  • {g['title']}\n    → {next_act}")
            sections.append("\n".join(lines))
    except Exception:
        pass

    # 4. Recent interaction stats
    try:
        with open(JARVIS_HOME / "data" / "interactions.jsonl") as f:
            interactions = [json.loads(l) for l in f if l.strip()]
        today_ts = int(time.time()) - 86400
        today_ints = [i for i in interactions if i.get("ts", 0) > today_ts]
        ctx = _load_context()
        top_topics = sorted(
            ctx["patterns"]["most_asked_topics"].items(),
            key=lambda x: -x[1]
        )[:3]
        topic_str = ", ".join(f"{t}({n})" for t,n in top_topics) if top_topics else "none yet"
        sections.append(
            f"\n📊 *Activity (24h)*\n"
            f"  Queries: {len(today_ints)} | Top topics: {topic_str}"
        )
    except Exception:
        pass

    # 5. Skill gaps
    try:
        from skillset import load as sk_load
        sk = sk_load()
        gaps = sk.get("skill_gaps", [])
        rules = len(sk.get("rules", []))
        learned_safe = len(sk["autonomy"].get("learned_safe", []))
        sections.append(
            f"\n🧠 *Jarvis Learning*\n"
            f"  Rules: {rules} | Learned-safe actions: {learned_safe} | "
            f"Skill gaps: {len(gaps)}"
        )
    except Exception:
        pass

    return "\n".join(sections)


# ── Self-Improvement Reflection ───────────────────────────────────────────────

def nightly_self_reflection() -> dict:
    """
    Run after midnight to analyse the day's interactions and improve.
    Identifies: response quality trends, recurring failures, gap topics.
    Saves improvement suggestions to skillset.
    """
    try:
        with open(JARVIS_HOME / "data" / "interactions.jsonl") as f:
            interactions = [json.loads(l) for l in f if l.strip()]

        today_ts = int(time.time()) - 86400
        today = [i for i in interactions if i.get("ts", 0) > today_ts]

        # Score all of today's responses
        scores = [score_response(i.get("query",""), i.get("response","")) for i in today]
        avg_score = sum(s["total"] for s in scores) / max(len(scores), 1)

        # Find low-quality responses
        low_quality = [(today[i], scores[i]) for i in range(len(today)) if scores[i]["total"] < 5.0]

        # Identify common flags
        all_flags: dict[str, int] = {}
        for s in scores:
            for f in s.get("flags", []):
                all_flags[f] = all_flags.get(f, 0) + 1

        # Extract improvement rules from failures
        new_rules = []
        if all_flags.get("missing_sir", 0) > 2:
            new_rules.append(("Always address Mike as 'Sir' at the start of every response", 10, "reflection"))
        if all_flags.get("too_short", 0) > 3:
            new_rules.append(("Provide complete answers — never give less than 2 sentences unless a yes/no question", 7, "reflection"))
        if all_flags.get("filler_opener", 0) > 1:
            new_rules.append(("Never open responses with: Certainly, Of course, Sure, Absolutely, Happy to help", 9, "reflection"))
        if all_flags.get("uncertain", 0) > 3:
            new_rules.append(("When uncertain about current data, always trigger a silent web search before next response", 8, "reflection"))

        # Store new rules
        rules_added = 0
        if new_rules:
            from skillset import add_rule
            for rule, priority, source in new_rules:
                if add_rule(rule, priority=priority, source=source):
                    rules_added += 1

        result = {
            "date": date.today().isoformat(),
            "interactions_today": len(today),
            "avg_score": round(avg_score, 1),
            "low_quality_count": len(low_quality),
            "flags": all_flags,
            "rules_added": rules_added,
        }
        log.info("Nightly reflection: avg_score=%.1f, %d rules added, flags=%s",
                 avg_score, rules_added, all_flags)
        return result
    except Exception as e:
        log.warning("nightly_self_reflection failed: %s", e)
        return {}


# ── Public API ────────────────────────────────────────────────────────────────

def get_full_thinking_context(query: str, rag_context: str = "") -> str:
    """
    Master function: return full thinking context for system prompt injection.
    Called from api.py build_system_prompt() and brain.py execute().
    """
    parts = []

    thinking = think_before_answer(query, rag_context)
    if thinking:
        parts.append(thinking)

    return "\n\n".join(p for p in parts if p)


# ── Self-test ─────────────────────────────────────────────────────────────────

def _test():
    print("Test 1: Score response...")
    s = score_response("what is disk usage", "Sir, disk usage is 45% — 120GB free.")
    assert s["total"] > 6, f"Good response scored too low: {s}"
    s2 = score_response("tell me about Python", "I don't know.")
    assert s2["total"] < 5, f"Bad response scored too high: {s2}"
    print(f"  ✓ Good: {s['total']}, Bad: {s2['total']}")

    print("Test 2: Intent classification...")
    assert _classify_intent("should I restart the gateway?") == "decision"
    assert _classify_intent("urgent: pm2 crashed") == "urgent_fix"
    assert _classify_intent("asthacash agent count") == "project_question"
    assert _classify_intent("how to set up nginx") == "planning"
    print("  ✓ All intents classified correctly")

    print("Test 3: Goals loaded...")
    goals = _load_goals()
    assert len(goals["active"]) >= 3
    print(f"  ✓ {len(goals['active'])} active goals")

    print("Test 4: Thinking context generated...")
    ctx = get_full_thinking_context("should I restart the payment gateway?")
    assert len(ctx) > 10
    print(f"  ✓ Context: {len(ctx)} chars")

    print("Test 5: Briefing generated...")
    briefing = generate_briefing(include_web=False)
    assert "Briefing" in briefing
    print(f"  ✓ Briefing: {len(briefing)} chars")

    print("Test 6: Proactive decisions...")
    decisions = get_proactive_decisions()
    print(f"  ✓ {len(decisions)} proactive decisions generated")
    for d in decisions[:3]:
        print(f"    p{d['priority']}: {d['message'][:60]}")

    print("\n✅ All thinking engine tests passed")


if __name__ == "__main__":
    _test()
