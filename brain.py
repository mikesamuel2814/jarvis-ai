"""
Jarvis 2.0 — Three-Tier Brain Router
Edge (Ollama) / Hybrid (Local + Kimi) / Cloud (Kimi K2.6)

All routing decisions are logged to routing_decisions.jsonl for accuracy tracking
and weekly evolution by selftrain_v2.py.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("jarvis.brain")

JARVIS_HOME    = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
CONFIG_FILE    = JARVIS_HOME / "config" / "jarvis_v2.yaml"
ROUTING_FILE   = JARVIS_HOME / "config" / "routing_rules.yaml"
PROFILE_FILE   = JARVIS_HOME / "config" / "mike_profile.yaml"
ROUTING_LOG    = JARVIS_HOME / "data" / "routing_decisions.jsonl"
ROUTING_LOG.parent.mkdir(parents=True, exist_ok=True)

# ── Tier enum ──────────────────────────────────────────────────────


class BrainTier(Enum):
    EDGE   = "edge"    # Local Ollama — zero cost, zero latency
    CURSOR = "cursor"  # Cursor AI tier — code-aware, live project context
    HYBRID = "hybrid"  # Local pre-analysis + Claude reasoning
    CLOUD  = "cloud"   # Claude Sonnet — deep reasoning (swap to Kimi when key available)


# ── Config loaders ────────────────────────────────────────────────


def _load_config() -> dict:
    try:
        return yaml.safe_load(CONFIG_FILE.read_text()) or {}
    except Exception:
        return {}


def _load_routing() -> dict:
    try:
        return yaml.safe_load(ROUTING_FILE.read_text()) or {}
    except Exception:
        return {}


def _load_profile() -> dict:
    try:
        return yaml.safe_load(PROFILE_FILE.read_text()) or {}
    except Exception:
        return {}


# ── Routing logic ─────────────────────────────────────────────────


def _word_match(text: str, patterns: list[str]) -> bool:
    """Match whole-word patterns (not substrings) against text."""
    tl = text.lower()
    for p in patterns:
        try:
            if re.search(p, tl):
                return True
        except re.error:
            if p.lower() in tl:
                return True
    return False


def route(
    query: str,
    history: list[dict] | None = None,
    context: dict | None = None,
) -> BrainTier:
    """
    Determine which tier should handle this query.

    Tier intent:
      EDGE   — casual chat, greetings, instant sysinfo/status, trivial one-liners.
      CURSOR — concrete code/ops on Mike's projects (fix, refactor, review, deploy, debug).
      CLOUD  — open-ended reasoning: why/how/explain, analysis, architecture, multi-step planning.
      HYBRID — local pre-analysis -> Cloud; fallback for ambiguous non-code prose.

    Evaluation order (cheapest / most specific first):
      1. Explicit prefix override
      2. Sysinfo fast-path -> Edge
      3. Edge casual/greeting patterns
      4. History depth threshold -> Cloud
      5. Cloud reasoning patterns (analytical verbs win over code actions)
      6. Cursor code/ops patterns
      7. Generic code keyword fallback (trivial+short -> Edge, else Cursor)
      8. Default: Hybrid
    """
    rules    = _load_routing().get("routing", {})
    e_rules  = rules.get("edge", {})
    cur_rules = rules.get("cursor", {})
    c_rules  = rules.get("cloud", {})

    ql = query.lower().strip()

    # ── 1. Explicit prefix ─────────────────────────────────────
    for pfx in e_rules.get("prefixes", ["!local", "!edge", "!fast"]):
        if ql.startswith(pfx):
            return BrainTier.EDGE
    for pfx in cur_rules.get("prefixes", ["!cursor", "!code"]):
        if ql.startswith(pfx):
            return BrainTier.CURSOR
    for pfx in c_rules.get("prefixes", ["!cloud", "!kimi", "!claude", "!deep"]):
        if ql.startswith(pfx):
            return BrainTier.CLOUD
    for pfx in rules.get("hybrid", {}).get("prefixes", ["!hybrid"]):
        if ql.startswith(pfx):
            return BrainTier.HYBRID

    # ── 2. Sysinfo fast-path -> Edge ───────────────────────────
    sysinfo_kws = e_rules.get("sysinfo_keywords", [
        "disk", "cpu", "ram", "memory", "vram", "uptime", "gpu",
        "temperature", "temp", "load", "processes", "services",
    ])
    sysinfo_verbs = e_rules.get("sysinfo_verbs", [
        "check", "show", "what", "how much", "how many",
        "status", "usage", "free", "list",
    ])
    if any(kw in ql for kw in sysinfo_kws) and any(v in ql for v in sysinfo_verbs):
        return BrainTier.EDGE

    # ── 3. Edge casual/greeting patterns ───────────────────────
    edge_patterns = e_rules.get("patterns", [
        r"^\s*(hi|hey|hello|yo|sup|howdy)\b",
        r"\bgood (morning|afternoon|evening|night)\b",
        r"\bhow are you\b", r"\bthank(s| you)\b",
        r"\bwhat time\b", r"\bping\b", r"\buptime\b",
        r"\brestart jarvis\b", r"\bclear (history|context|memory)\b",
    ])
    if _word_match(query, edge_patterns):
        return BrainTier.EDGE

    # ── 4. History depth -> Cloud ──────────────────────────────
    depth_threshold = c_rules.get("history_depth_threshold", 20)
    if history and len(history) > depth_threshold:
        return BrainTier.CLOUD

    # ── 5. Cloud reasoning patterns ────────────────────────────
    # Analytical intent (why/analyze/explain/architecture/should i)
    # wins even when the query also mentions code.
    cloud_patterns = c_rules.get("patterns", [
        r"\bwhy\b", r"\bhow (does|do|come|would|should|can i)\b",
        r"\banaly[sz]e\b", r"\banaly[sz]is\b", r"\bexplain\b",
        r"\bcompare\b", r"\bevaluate\b", r"\bassess\b", r"\bdiagnose\b",
        r"\broot cause\b", r"\barchitect(ure|ural)?\b",
        r"\bsecurity (audit|review)\b", r"\bshould i\b",
        r"\bbest (way|approach|practice)\b", r"\btrade-?offs?\b",
        r"\bentire codebase\b", r"\bfull project\b",
    ])
    if _word_match(query, cloud_patterns):
        return BrainTier.CLOUD

    # ── 6. Cursor code/ops patterns ────────────────────────────
    cursor_patterns = cur_rules.get("patterns", [])
    if _word_match(query, cursor_patterns):
        char_limit = e_rules.get("code_char_limit", 120)
        if len(query) < char_limit and "?" in query and not _is_actionable(ql):
            return BrainTier.EDGE  # trivial short code lookup -> local
        return BrainTier.CURSOR

    # ── 7. Generic code keyword fallback ───────────────────────
    code_kws = cur_rules.get("keywords", [
        "function", "class", "bug", "error", "fix", "code", "refactor",
        "import", "module", "component", "api", "endpoint", "database",
        "deploy", "build", "test", "lint", "typescript", "react", "node",
        "fastapi", "pm2", "webpack", "vite", "pnpm",
    ])
    if any(kw in ql for kw in code_kws):
        char_limit = e_rules.get("code_char_limit", 120)
        if len(query) < char_limit and "?" in query and not _is_actionable(ql):
            return BrainTier.EDGE  # trivial code question -> local
        return BrainTier.CURSOR

    # ── 8. Default ─────────────────────────────────────────────
    return BrainTier.HYBRID


def _is_actionable(ql: str) -> bool:
    """True if the query asks Jarvis to DO something to code/infra (not just look up)."""
    action_verbs = (
        "fix", "refactor", "rewrite", "implement", "deploy", "redeploy",
        "rollback", "add", "create", "write", "build", "review", "debug",
        "migrate", "restart", "merge", "update", "change", "remove", "delete",
    )
    return any(re.search(rf"\b{v}\b", ql) for v in action_verbs)


def log_routing_decision(
    query: str,
    tier: BrainTier,
    model: str,
    latency_ms: float | None = None,
) -> None:
    entry = {
        "ts": int(time.time()),
        "tier": tier.value,
        "model": model,
        "query_len": len(query),
        "query_snippet": query[:80],
    }
    if latency_ms is not None:
        entry["latency_ms"] = round(latency_ms, 1)
    try:
        with open(ROUTING_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ── Execution ─────────────────────────────────────────────────────

_ollama_lock = threading.Semaphore(1)


def _persona_block() -> str:
    """Dense system-prompt persona block (Sir rule, forbidden openers, projects, priorities)."""
    try:
        from profile import profile_prompt_block
        block = profile_prompt_block()
        if block:
            return block
    except Exception as exc:
        log.debug("persona block unavailable: %s", exc)
    # Minimal fallback so the Sir/persona contract never silently disappears.
    return (
        "You are Jarvis, Sir Mike Samuel's personal AI brain. Address him ALWAYS as 'Sir'. "
        "Direct, minimal, senior-dev depth; under 200 words unless asked. "
        "Never open with 'Certainly!', 'Of course!', 'Sure!', or 'Absolutely!'. "
        "Be proactive — flag risks. Keep services up; the payment gateway is money-critical."
    )


def query_edge(query: str, rag_context: str, history: list[dict] | None = None) -> str:
    """Local Ollama — zero cost, zero latency."""
    import ollama

    cfg = _load_config()
    ollama_cfg = cfg.get("ollama", {})
    opts = dict(ollama_cfg.get("options", {}))
    # Enforce 6GB-VRAM-safe ceiling regardless of config drift.
    opts.setdefault("num_keep", 256)
    opts.setdefault("num_predict", 1024)
    opts.setdefault("temperature", 0.3)
    opts["num_ctx"] = min(int(opts.get("num_ctx", 2048)), 2048)

    ql = query.lower()
    # Vision: contains image description request
    if any(kw in ql for kw in ["image", "photo", "picture", "screenshot", "describe this"]):
        model = ollama_cfg.get("models", {}).get("vision", "llava:7b")
    elif any(kw in ql for kw in ["code", "bug", "function", "class", "error", "fix"]):
        model = ollama_cfg.get("models", {}).get("code", "qwen2.5-coder:7b")
    elif any(kw in ql for kw in ["hello", "hi", "hey", "good", "morning", "weather"]):
        model = ollama_cfg.get("models", {}).get("chat", "phi4-mini")
    else:
        model = ollama_cfg.get("models", {}).get("reasoning", "deepseek-r1:7b")

    prompt = f"""{_persona_block()}

Relevant Context from Memory: {rag_context[:1500]}
Query: {query}"""

    messages = []
    if history:
        messages.extend(history[-10:])  # Last 10 turns
    messages.append({"role": "user", "content": prompt})

    t0 = time.time()
    with _ollama_lock:
        client = ollama.Client(
            host=ollama_cfg.get("host", "http://localhost:11434")
        )
        resp = client.chat(model=model, messages=messages, options=opts)
    content = resp["message"]["content"]
    latency = (time.time() - t0) * 1000
    log_routing_decision(query, BrainTier.EDGE, model, latency)
    return content


def query_cloud(
    query: str,
    rag_context: str,
    history: list[dict] | None = None,
    full_files: str | None = None,
    thinking: bool = True,
) -> str:
    """Claude Sonnet — deep reasoning. Swap import to kimi.client when MOONSHOT_API_KEY is set."""
    from claude_client import get_client, _check_privacy

    _check_privacy(query)

    system_parts = [
        _persona_block(),
        f"Local Memory Context:\n{rag_context[:3000]}",
    ]
    if full_files:
        system_parts.append(f"Codebase:\n{full_files[:10000]}")

    client = get_client()
    t0 = time.time()
    content, reasoning = client.query(
        system="\n\n".join(system_parts),
        user=query,
        thinking=thinking,
        history=history,
    )
    latency = (time.time() - t0) * 1000
    log_routing_decision(query, BrainTier.CLOUD, "claude-sonnet-4-6", latency)

    if reasoning:
        log.debug("Cloud reasoning trace (%d chars): %s...", len(reasoning), reasoning[:200])

    return content


def query_cursor(query: str, rag_context: str, history: list[dict] | None = None) -> str:
    """Cursor AI tier — code-aware queries with live project context."""
    from cursor.query import query as cursor_query
    t0 = time.time()
    content = cursor_query(query, rag_context=rag_context, history=history)
    latency = (time.time() - t0) * 1000
    log_routing_decision(query, BrainTier.CURSOR, "cursor+claude", latency)
    return content


def query_hybrid(query: str, rag_context: str, history: list[dict] | None = None) -> str:
    """
    Hybrid: local Ollama pre-analysis → Kimi deep reasoning.
    Strips DeepSeek think tags before passing to Kimi.
    """
    import ollama

    cfg = _load_config()
    ollama_cfg = cfg.get("ollama", {})
    opts = {"temperature": 0.2, "num_predict": 512, "num_ctx": 4096}

    pre_prompt = f"""Analyze this query briefly. Extract:
1. Key concepts (list)
2. What information is needed (list)
3. Suggested approach (one sentence)

Query: {query}
Context: {rag_context[:800]}

Return compact JSON: {{"concepts":[], "needs":[], "approach":""}}"""

    with _ollama_lock:
        client = ollama.Client(host=ollama_cfg.get("host", "http://localhost:11434"))
        local_resp = client.chat(
            model=ollama_cfg.get("models", {}).get("reasoning", "deepseek-r1:7b"),
            messages=[{"role": "user", "content": pre_prompt}],
            options=opts,
        )

    local_analysis = local_resp["message"]["content"]
    # Strip <think>...</think> tags from DeepSeek-R1 output
    local_analysis = re.sub(r"<think>.*?</think>", "", local_analysis, flags=re.DOTALL).strip()

    enriched_context = rag_context + "\n\nLocal Pre-Analysis:\n" + local_analysis[:600]
    log.debug("Hybrid: local pre-analysis done, calling Claude...")
    return query_cloud(query, enriched_context, history=history, thinking=True)


def execute(
    query: str,
    rag_context: str,
    history: list[dict] | None = None,
    force_tier: str | None = None,
) -> dict[str, Any]:
    """
    Route and execute a query. Returns {"response": str, "tier": str, "model": str}.
    """
    if force_tier:
        tier = BrainTier(force_tier.lower())
    else:
        tier = route(query, history=history)

    try:
        if tier == BrainTier.EDGE:
            response = query_edge(query, rag_context, history=history)
            model    = "ollama-local"
        elif tier == BrainTier.CURSOR:
            response = query_cursor(query, rag_context, history=history)
            model    = "cursor+claude"
        elif tier == BrainTier.CLOUD:
            response = query_cloud(query, rag_context, history=history)
            model    = "claude-sonnet-4-6"
        else:
            response = query_hybrid(query, rag_context, history=history)
            model    = "hybrid"
    except Exception as exc:
        log.error("Brain execution failed (tier=%s): %s. Falling back to Edge.", tier.value, exc)
        response = query_edge(query, rag_context, history=history)
        tier     = BrainTier.EDGE
        model    = "ollama-local (fallback)"

    return {"response": response, "tier": tier.value, "model": model}
