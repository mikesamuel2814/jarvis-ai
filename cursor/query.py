"""
Cursor AI tier — code-aware primary AI for Jarvis.

Cursor has no headless query API, so this tier:
  1. Gathers live codebase context (git status/diff, recent files, open projects)
  2. Combines it with the query and RAG memory
  3. Delegates to Claude CLI with a code-specialist system prompt

When Cursor IDE is open, additional context from cursor_enriched.jsonl is injected
so the AI has awareness of what was recently worked on in the IDE.

Route here for: code, bugs, refactor, functions, errors, project-specific questions.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger("jarvis.cursor.query")

JARVIS_HOME   = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
ENRICHED_LOG  = JARVIS_HOME / "data" / "cursor_enriched.jsonl"
PROJECTS = {
    "AsthaCash":  Path.home() / "Projects/kalimike/Payment-Gateway",
    "Starline":   Path.home() / "Projects/kalimike/Starline-Final-web",
}

SYSTEM_PROMPT = """You are Jarvis 2.0 — Cursor AI tier, Mike Samuel's code specialist.

Rules:
- Address Mike as Sir
- Be SHORT and DIRECT: bullet points, no waffle
- Max ~150 words unless more code is genuinely needed
- Senior-dev depth: identify root cause, suggest the exact fix
- Prefer specific file:line references over generic advice
- If you see recent IDE context (what was open), use it
- Never explain basics Mike already knows"""


# ── Context gatherers ─────────────────────────────────────────────


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 8) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.stdout.strip()
    except Exception:
        return ""


def _detect_project(query: str) -> tuple[str, Path] | tuple[None, None]:
    ql = query.lower()
    if any(k in ql for k in ["astracash", "astracash", "payment", "gateway", "merchant", "agent dashboard"]):
        return "AsthaCash", PROJECTS["AsthaCash"]
    if any(k in ql for k in ["starline", "conztru", "real estate", "pnpm", "api-server"]):
        return "Starline", PROJECTS["Starline"]
    # Auto-detect from which project dirs exist
    for name, path in PROJECTS.items():
        if path.exists():
            return name, path
    return None, None


def _git_context(project_path: Path) -> str:
    if not project_path.exists():
        return ""
    status = _run(["git", "status", "--short"], cwd=project_path)
    diff   = _run(["git", "diff", "--stat", "HEAD"], cwd=project_path)
    log_   = _run(["git", "log", "--oneline", "-5"], cwd=project_path)
    parts  = []
    if status:
        parts.append(f"Git status:\n{status}")
    if diff:
        parts.append(f"Recent changes:\n{diff}")
    if log_:
        parts.append(f"Recent commits:\n{log_}")
    return "\n\n".join(parts)[:2000]


def _recent_cursor_sessions(n: int = 3) -> str:
    """Pull the last N enriched Cursor sessions so the AI knows what was open."""
    if not ENRICHED_LOG.exists():
        return ""
    lines = ENRICHED_LOG.read_text().strip().splitlines()
    sessions = []
    for line in reversed(lines[-20:]):
        try:
            entry = json.loads(line)
            analysis = entry.get("kimi_analysis") or entry.get("analysis", {})
            if isinstance(analysis, dict) and not analysis.get("error") and not analysis.get("blocked"):
                goal = analysis.get("goal", "")
                patterns = analysis.get("patterns", [])
                if goal:
                    sessions.append(f"• {goal}" + (f" [{', '.join(patterns[:2])}]" if patterns else ""))
        except Exception:
            pass
        if len(sessions) >= n:
            break
    if not sessions:
        return ""
    return "Recent Cursor IDE activity:\n" + "\n".join(sessions)


def _recent_files(project_path: Path, n: int = 8) -> str:
    """Most recently modified source files in the project."""
    try:
        result = _run(
            ["find", ".", "-type", "f",
             r"\(", "-name", "*.py", "-o", "-name", "*.js",
             "-o", "-name", "*.ts", "-o", "-name", "*.tsx",
             "-o", "-name", "*.jsx", r"\)",
             "-not", "-path", "*/node_modules/*",
             "-not", "-path", "*/.git/*",
             "-newer", ".git/index"],
            cwd=project_path,
            timeout=5,
        )
        files = result.splitlines()[:n]
        if files:
            return "Recently touched files:\n" + "\n".join(files)
    except Exception:
        pass
    return ""


# ── Main entry point ──────────────────────────────────────────────


def query(
    user_query: str,
    rag_context: str = "",
    history: list[dict] | None = None,
) -> str:
    """
    Execute a code-aware query via the Cursor AI tier.
    Gathers live project context, then calls Claude CLI.
    """
    from claude_client import get_client, _check_privacy
    _check_privacy(user_query)

    project_name, project_path = _detect_project(user_query)

    context_parts: list[str] = []

    if rag_context:
        context_parts.append(f"Memory context:\n{rag_context[:1500]}")

    if project_path:
        git_ctx = _git_context(project_path)
        if git_ctx:
            context_parts.append(f"[{project_name}] {git_ctx}")

        recent = _recent_files(project_path)
        if recent:
            context_parts.append(recent)

    cursor_sessions = _recent_cursor_sessions()
    if cursor_sessions:
        context_parts.append(cursor_sessions)

    if history:
        history_lines = []
        for msg in history[-6:]:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            history_lines.append(f"{role}: {content[:300]}")
        if history_lines:
            context_parts.append("Conversation:\n" + "\n".join(history_lines))

    full_system = SYSTEM_PROMPT
    if context_parts:
        full_system += "\n\n" + "\n\n".join(context_parts)

    client = get_client()
    t0 = time.time()
    content, _ = client.complete(user_query, system=full_system)
    log.debug("Cursor tier: %.1fs", time.time() - t0)
    return content
