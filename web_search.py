"""
Jarvis Web Research Module

search()   → DuckDuckGo results (no API key)
scrape()   → clean text from URL via trafilatura
research() → combined context string ready for AI
is_web_query() → detect if a query needs real-time web data
web_answer()   → full pipeline: search → scrape → AI → short reply
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

log = logging.getLogger("jarvis.web")

# ── Intent detection ──────────────────────────────────────────────────────

_WEB_TRIGGERS = [
    # Recency signals
    r"\b(latest|recent|current|today|now|live|real.?time|breaking|trending)\b",
    # Action signals
    r"\b(search|look up|google|find|browse|check online|what.?s happening)\b",
    # News / market
    r"\b(news|price of|stock|crypto|bitcoin|btc|eth|forex|weather|score|match)\b",
    # Factual lookups that change
    r"\b(who (is|won|leads)|what (is|are) the (current|latest|new))\b",
    r"\b(release date|launched|announced|update|version \d|changelog)\b",
]

_WEB_PATTERN = re.compile("|".join(_WEB_TRIGGERS), re.IGNORECASE)


def is_web_query(text: str) -> bool:
    """True if the query likely needs real-time web data."""
    return bool(_WEB_PATTERN.search(text))


# ── Search ────────────────────────────────────────────────────────────────

def search(query: str, num: int = 5) -> list[dict]:
    """
    DuckDuckGo search. Returns list of {title, url, snippet}.
    Falls back to [] on any error.
    """
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num))
        return [
            {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in results
        ]
    except Exception as exc:
        log.warning("DuckDuckGo search failed: %s", exc)
        return []


# ── Scrape ────────────────────────────────────────────────────────────────

def scrape(url: str, max_chars: int = 3000) -> str:
    """
    Extract clean text from a URL using trafilatura.
    Returns empty string on failure (never raises).
    """
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        return (text or "")[:max_chars]
    except Exception as exc:
        log.debug("Scrape failed for %s: %s", url, exc)
        return ""


# ── Research pipeline ─────────────────────────────────────────────────────

def research(query: str, num_results: int = 3, scrape_top: int = 2) -> dict:
    """
    Full research pipeline:
      1. DuckDuckGo search
      2. Scrape top N pages for full content
      3. Return structured context dict

    Returns:
      {
        "query": str,
        "sources": [{title, url, snippet, full_text}],
        "context": str,   # ready to inject into AI prompt
      }
    """
    t0 = time.time()
    results = search(query, num=num_results)
    sources = []

    for i, r in enumerate(results):
        full_text = ""
        if i < scrape_top and r["url"]:
            full_text = scrape(r["url"])
        sources.append({
            "title":     r["title"],
            "url":       r["url"],
            "snippet":   r["snippet"],
            "full_text": full_text,
        })

    # Build context block
    ctx_parts = [f"Web search results for: {query}\n"]
    for i, s in enumerate(sources, 1):
        ctx_parts.append(f"[{i}] {s['title']}")
        ctx_parts.append(f"URL: {s['url']}")
        if s["full_text"]:
            ctx_parts.append(s["full_text"][:1500])
        else:
            ctx_parts.append(s["snippet"])
        ctx_parts.append("")

    elapsed = round(time.time() - t0, 1)
    log.info("research('%s'): %d results in %.1fs", query, len(sources), elapsed)

    return {
        "query":   query,
        "sources": sources,
        "context": "\n".join(ctx_parts),
        "elapsed": elapsed,
    }


# ── Full answer pipeline ──────────────────────────────────────────────────

def web_answer(
    query: str,
    use_cloud: bool = True,
    num_results: int = 3,
) -> tuple[str, list[dict]]:
    """
    search → scrape → AI → short reply

    Returns (answer_text, sources_list).
    Caller formats the Telegram message.
    """
    data = research(query, num_results=num_results)
    sources = data["sources"]

    if not sources:
        return "Couldn't find anything online for that, Sir.", []

    web_ctx = data["context"]

    system = (
        "You are Jarvis, Mike's personal AI assistant.\n"
        "Using the web search results below, answer the question.\n"
        "Rules:\n"
        "- Address Mike as 'Sir' once at the start\n"
        "- Be concise: 3-5 bullet points max\n"
        "- Lead with the most important fact\n"
        "- End with the most relevant source URL if useful\n"
        "- Never say 'based on the results' or 'according to'\n"
        "- No fluff, no padding\n\n"
        f"Web Context:\n{web_ctx[:4000]}"
    )

    try:
        if use_cloud:
            import claude_client
            client = claude_client.get_client()
            answer, _ = client.query(system=system, user=query)
        else:
            # Edge: inject context into Ollama query
            import ollama as _ollama
            resp = _ollama.Client(host="http://localhost:11434").chat(
                model="deepseek-r1:7b",
                messages=[{"role": "user", "content": f"{system}\n\nQuestion: {query}"}],
                options={"num_ctx": 4096, "num_predict": 512, "temperature": 0.3},
            )
            answer = resp["message"]["content"]
            answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
    except Exception as exc:
        log.error("web_answer AI call failed: %s", exc)
        # Fallback: return snippets directly
        lines = ["Here's what I found, Sir:\n"]
        for s in sources[:3]:
            lines.append(f"• {s['snippet'][:200]}")
        answer = "\n".join(lines)

    return answer, sources
