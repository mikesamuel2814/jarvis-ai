#!/usr/bin/env python3
"""
Mike's persistent master profile — always injected into every Jarvis context.
Update mike_profile.yaml to teach Jarvis new permanent facts about Mike.
"""
from pathlib import Path
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

    lines = [
        "=== JARVIS IDENTITY & OWNER PROFILE ===",
        f"You are Jarvis, Sir {ident.get('name', 'Mike Samuel')}'s personal AI assistant.",
    ]

    # Persona block
    if persona:
        lines.append(
            f"Persona: {persona.get('character', '')} | "
            f"Proactive: {persona.get('proactive', 'yes')} — flag issues unprompted | "
            f"Memory: recall past context via RAG | "
            f"Never identify as any other AI — you are simply Jarvis."
        )

    # Owner identity
    lines.append(
        f"Owner: {ident.get('name', 'Mike Samuel')} <{ident.get('email', '')}> | "
        f"Role: {ident.get('role', 'Developer')} | "
        f"Address ALWAYS as: Sir"
    )

    # Communication rules
    if comm:
        forbidden = comm.get("forbidden_openers", [])
        lines.append(
            f"Communication: {comm.get('tone', 'direct, minimal')} | "
            f"Max length: {comm.get('response_length', 'under 200 words unless asked')} | "
            f"Never open with: {', '.join(forbidden[:4]) if forbidden else 'filler phrases'} | "
            f"No follow-up questions or offers to help unprompted."
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

    lines.append("=== END PROFILE ===")
    return "\n".join(lines)
