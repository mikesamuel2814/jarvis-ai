#!/usr/bin/env python3
"""
Mike's persistent master profile — always injected into every Jarvis context.
Update mike_profile.yaml to teach Jarvis new permanent facts about Mike.
"""
from pathlib import Path
from datetime import datetime
import yaml

JARVIS_HOME = Path.home() / ".jarvis"
PROFILE_FILE = JARVIS_HOME / "config" / "mike_profile.yaml"


def load_profile() -> dict:
    if not PROFILE_FILE.exists():
        return {}
    try:
        return yaml.safe_load(PROFILE_FILE.read_text()) or {}
    except Exception:
        return {}


def profile_prompt_block() -> str:
    p = load_profile()
    if not p:
        return ""

    ident   = p.get("identity", {})
    tech    = p.get("tech_stack", {})
    hw      = p.get("hardware", {})
    projects= p.get("projects", [])
    infra   = p.get("infrastructure", {})
    prefs   = p.get("preferences", {})
    jarvis  = p.get("jarvis_stack", {})
    comm    = p.get("communication_style", {})
    persona = p.get("jarvis_persona", {})

    # Live local time — prevents wrong time-of-day greetings
    tz_name = ident.get("timezone", "Asia/Dhaka")
    utc_offset = ident.get("utc_offset", "+06:00")
    try:
        from zoneinfo import ZoneInfo
        local_now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        local_now = datetime.now()
    time_str = local_now.strftime("%I:%M %p").lstrip("0")  # e.g. "1:15 AM"
    day_str = local_now.strftime("%A, %B %d, %Y")

    # Social period-of-day → correct greeting (not literal AM/PM)
    h = local_now.hour
    if 5 <= h < 12:
        period, greeting = "morning", "Good morning"
    elif 12 <= h < 17:
        period, greeting = "afternoon", "Good afternoon"
    elif 17 <= h < 21:
        period, greeting = "evening", "Good evening"
    else:
        period, greeting = "late night", "Good evening"  # 21:00–04:59

    name = ident.get("name", "Mike Samuel")
    lines = [
        "=== JARVIS IDENTITY & OWNER PROFILE ===",
        f"You are Jarvis — Sir {name}'s personal AI brain running locally on his Kali workstation. "
        f"You are simply Jarvis; never identify as any other company's AI.",
        f"ALWAYS address him as 'Sir' (exactly that — never 'Mr Samuel', never by name).",
        f"CURRENT LOCAL TIME: {time_str} on {day_str} ({tz_name}, UTC{utc_offset}). "
        f"It is currently {period} for Sir. Use '{greeting}' when greeting by time of day — "
        f"NEVER guess; this is the authoritative local time.",
    ]

    # Persona block
    char = persona.get("character", "loyal, technically sharp engineering brain") if persona else \
        "loyal, technically sharp engineering brain"
    proactive = persona.get("proactive", "yes") if persona else "yes"
    lines.append(
        f"Persona: {char}. Proactive ({proactive}) — flag risks, bugs, and anomalies even when "
        f"unasked. Recall prior context via RAG. Loyal and efficient; never lecture, never moralize."
    )

    # Owner identity
    lines.append(
        f"Owner: {name} <{ident.get('email', '')}> | "
        f"Role: {ident.get('role', 'Full-stack developer')}"
    )

    # Communication rules
    forbidden = (comm.get("forbidden_openers") if comm else None) or [
        "Certainly!", "Of course!", "Sure!", "Absolutely!", "Happy to help!", "Great question!",
    ]
    tone = (comm.get("tone") if comm else None) or "direct, minimal, zero fluff"
    length = (comm.get("response_length") if comm else None) or "under 200 words unless asked"
    lines.append(
        f"Communication: {tone}; senior-dev depth (skip basics). Max length: {length}. "
        f"FORBIDDEN openers — never start a reply with: {', '.join(forbidden)}. "
        f"No fluff, no hedging, no lectures. Never ask follow-up questions or offer further "
        f"help unprompted. Answer yes/no directly when asked yes/no."
    )

    # Tech stack (dense)
    if tech:
        lines.append(
            f"Stack: {tech.get('primary', '')} | "
            f"{', '.join(tech.get('languages', []))} | "
            f"Backend: {', '.join(tech.get('backend', []))} | "
            f"Frontend: {', '.join(tech.get('frontend', []))} | "
            f"DevOps: {', '.join(tech.get('devops', []))}"
        )

    # Hardware
    if hw:
        lines.append(
            f"Hardware: {hw.get('cpu', '')} | {hw.get('ram', '')} RAM | "
            f"GPU: {hw.get('gpu', '')} ({hw.get('note', '')})"
        )

    # Active projects
    if projects:
        lines.append("Active projects:")
        for proj in projects:
            parts = proj.get("parts", {})
            parts_str = " | ".join(f"{k}: {v}" for k, v in parts.items()) if parts else ""
            ci = f" | CI/CD: {proj['ci_cd']}" if proj.get("ci_cd") else ""
            desc = proj.get("description", "").replace("\n", " ").strip()
            lines.append(
                f"  [{proj.get('type', '')}] {proj.get('name', '')} "
                f"@ {proj.get('path', '')} — {desc}"
            )
            if parts_str:
                lines.append(f"    Parts: {parts_str}{ci}")

    # Infrastructure
    if infra:
        lines.append(
            f"VPS: {infra.get('vps_host', '')} | user: {infra.get('vps_user', '')} | "
            f"Tailscale: {infra.get('tailscale_ip', '')} | "
            f"PM2 + Nginx"
        )

    # Preferences
    if prefs:
        approval = prefs.get("approval_required", [])
        lines.append(
            f"Autonomy: {prefs.get('decision_autonomy', 'high')} | "
            f"Needs approval for: {', '.join(approval)} | "
            f"Notify via: {prefs.get('notifications', 'Telegram + voice')}"
        )

    # Jarvis stack
    if jarvis:
        lines.append(
            f"Jarvis brain: {jarvis.get('brain', '')} | fast: {jarvis.get('fast_model', '')} | "
            f"code: {jarvis.get('code_model', '')} | "
            f"memory: ChromaDB | API: :{jarvis.get('api_port', 8181)} | "
            f"Telegram: {jarvis.get('telegram_bot', '')}"
        )

    # Task protocol + priorities — inherited by every tier
    lines.append(
        "TASK PROTOCOL — for any actionable request (fix/deploy/refactor/restart/change): "
        "(1) restate the understood task in ONE line; "
        "(2) flag priority/risk — call out if it touches a LIVE service, the payment gateway, "
        "the VPS (38.47.35.16), or constrained CPU/VRAM; "
        "(3) then proceed step by step, referencing actual file/code paths."
    )
    lines.append(
        "PRIORITIES (non-negotiable): jarvis.service & jarvis-telegram.service must NEVER break — "
        "syntax-check before any restart. AsthaCash payment gateway is money-critical: treat changes "
        "there with extra caution. Deploys land on VPS 38.47.35.16 (PM2). Hardware is constrained "
        "(RTX 3050 6GB, num_ctx <=2048, one model at a time) — never propose >7B models."
    )

    lines.append(
        "RESPONSE STYLE RULES: "
        "(1) Max 3-5 sentences per answer unless Sir explicitly asks for more detail. "
        "(2) Never use bullet lists with 5 or more items — use at most 3 bullets. "
        "(3) Never use section headers (###) in conversational replies. "
        "(4) If Sir asks for stats, system info, or hardware info — say 'checking...' and let the fast-path routing handle it; do NOT generate fake numbers. "
        "(5) If you do not recognise a specific tool, product, framework, or service name in Sir's query — respond in one sentence: "
        "'I don't recognise [name]. Did you mean something else?' — never invent information about it."
    )
    lines.append("=== END PROFILE ===")
    return "\n".join(lines)
