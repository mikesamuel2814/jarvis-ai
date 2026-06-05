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

    ident = p.get("identity", {})
    tech = p.get("tech_stack", {})
    hw = p.get("hardware", {})
    projects = p.get("projects", [])
    infra = p.get("infrastructure", {})
    prefs = p.get("preferences", {})
    jarvis = p.get("jarvis_stack", {})

    lines = ["\n\n=== MIKE'S MASTER PROFILE (always authoritative) ==="]

    lines.append(
        f"Owner: {ident.get('name','Mike Samuel')} <{ident.get('email','')}> | "
        f"Address as: {ident.get('address_as','Sir')} | Role: {ident.get('role','Developer')}"
    )

    if tech:
        lines.append(
            f"Stack: {tech.get('primary','')} primary | "
            f"Languages: {', '.join(tech.get('languages',[]))} | "
            f"Backend: {', '.join(tech.get('backend',[]))} | "
            f"Frontend: {', '.join(tech.get('frontend',[]))}"
        )
        lines.append(f"DevOps: {', '.join(tech.get('devops',[]))}")

    if hw:
        lines.append(
            f"Hardware: {hw.get('cpu','')} | {hw.get('ram','')} RAM | "
            f"GPU: {hw.get('gpu','')} | {hw.get('note','')}"
        )

    if projects:
        lines.append("Active projects:")
        for proj in projects:
            parts_str = ""
            parts = proj.get("parts", {})
            if parts:
                parts_str = " | ".join(f"{k}: {v}" for k, v in parts.items())
            lines.append(
                f"  [{proj.get('type','')}] {proj.get('name','')} → {proj.get('path','')} | {parts_str}"
            )

    if infra:
        lines.append(
            f"VPS: {infra.get('vps_host','')} (user: {infra.get('vps_user','')}) | "
            f"Tailscale: {infra.get('tailscale_ip','')} | PM: {infra.get('process_manager','PM2')}"
        )

    if prefs:
        lines.append(f"Style: {prefs.get('communication','')} | Autonomy: {prefs.get('decision_autonomy','')}")
        approval = prefs.get("approval_required", [])
        if approval:
            lines.append(f"Needs approval for: {', '.join(approval)}")

    lines.append("=== END PROFILE ===")
    return "\n".join(lines)
