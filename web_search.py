"""
Jarvis Web Research Module

Primary:  Headless Chrome via browser/agent.py (Google search + JS scraping)
Fallback: DuckDuckGo API (ddgs) if browser fails

Public API:
  is_web_query(text)            → bool
  search(query, num)            → list[{title, url, snippet}]
  scrape(url)                   → clean text
  research(query)               → {query, sources, context, elapsed}
  web_answer(query, use_cloud)  → (answer_text, sources)
"""

from __future__ import annotations

import logging
import re
import time

log = logging.getLogger("jarvis.web")

# ── Intent detection ──────────────────────────────────────────────────────

_WEB_TRIGGERS = [
    r"\b(latest|recent|current|today|now|live|real.?time|breaking|trending)\b",
    r"\b(search|look up|google|find|browse|check online|what.?s happening)\b",
    r"\b(news|price of|stock|crypto|bitcoin|btc|eth|forex|weather|score|match)\b",
    r"\b(who (is|won|leads)|what (is|are) the (current|latest|new))\b",
    r"\b(release date|launched|announced|update|version \d|changelog)\b",
]
_WEB_PATTERN = re.compile("|".join(_WEB_TRIGGERS), re.IGNORECASE)


def is_web_query(text: str) -> bool:
    return bool(_WEB_PATTERN.search(text))


# ── Search — browser first, ddgs fallback ────────────────────────────────

def search(query: str, num: int = 5) -> list[dict]:
    """Google via headless Chrome. Falls back to DuckDuckGo API on error."""
    try:
        from browser.agent import google_search
        results = google_search(query, num=num)
        if results:
            log.info("search('%s'): %d results via Chrome", query, len(results))
            return results
        log.warning("Chrome search returned 0 results, falling back to ddgs")
    except Exception as exc:
        log.warning("Chrome search failed (%s), falling back to ddgs", exc)

    # Fallback: ddgs
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=num))
        return [
            {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in raw
        ]
    except Exception as exc2:
        log.error("ddgs fallback also failed: %s", exc2)
        return []


# ── Scrape — browser for JS-heavy pages, trafilatura for static ───────────

def scrape(url: str, max_chars: int = 3500) -> str:
    """
    Fetch full page content.
    Uses headless Chrome so JS-rendered content (SPAs, paywalled previews) is captured.
    """
    try:
        from browser.agent import open_url
        text = open_url(url)
        if text and len(text) > 100:
            return text[:max_chars]
    except Exception as exc:
        log.debug("Browser scrape failed for %s: %s", url, exc)

    # Fallback: trafilatura (static HTML, no JS)
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        text = trafilatura.extract(downloaded or "", include_tables=True, no_fallback=False)
        return (text or "")[:max_chars]
    except Exception as exc2:
        log.debug("trafilatura fallback failed for %s: %s", url, exc2)
        return ""


# ── Research pipeline ─────────────────────────────────────────────────────

def research(query: str, num_results: int = 3, scrape_top: int = 2) -> dict:
    """
    Full pipeline: search → scrape top pages → build AI context block.
    Returns {query, sources, context, elapsed}.
    """
    t0 = time.time()
    try:
        results = search(query, num=num_results)
        sources = []

        for i, r in enumerate(results):
            full_text = ""
            if i < scrape_top and r.get("url"):
                full_text = scrape(r["url"])
            sources.append({
                "title":     r.get("title", ""),
                "url":       r.get("url", ""),
                "snippet":   r.get("snippet", ""),
                "full_text": full_text,
            })
    finally:
        # Close the headless browser after each research task → zero idle CPU.
        # Per-task open/close is the right trade-off for an occasional-query bot;
        # a resident chromium would otherwise sit warm burning CPU between calls.
        try:
            from browser.agent import quit_browser
            quit_browser()
        except Exception:
            pass

    ctx_parts = [f"Web search results for: {query}\n"]
    for i, s in enumerate(sources, 1):
        ctx_parts.append(f"[{i}] {s['title']}")
        ctx_parts.append(f"URL: {s['url']}")
        ctx_parts.append(s["full_text"][:1500] if s["full_text"] else s["snippet"])
        ctx_parts.append("")

    elapsed = round(time.time() - t0, 1)
    log.info("research('%s'): %d sources in %.1fs", query, len(sources), elapsed)

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
    search → scrape → AI → short Telegram-ready reply.
    Returns (answer_text, sources).
    """
    data = research(query, num_results=num_results)
    sources = data["sources"]

    if not sources:
        return "Couldn't find anything online for that, Sir.", []

    system = (
        "You are Jarvis, Mike's personal AI assistant.\n"
        "Using ONLY the web search results below, answer the question.\n"
        "Rules:\n"
        "- Open with 'Sir,' exactly once\n"
        "- 3-5 bullet points max, each under 15 words\n"
        "- Lead with the single most important fact\n"
        "- Include one source URL at the end if it adds value\n"
        "- Never say 'based on results' or 'according to'\n"
        "- No fluff\n\n"
        f"Web Context:\n{data['context'][:4500]}"
    )

    try:
        if use_cloud:
            import claude_client
            answer, _ = claude_client.get_client().query(system=system, user=query)
        else:
            import re as _re
            import ollama as _ollama
            resp = _ollama.Client(host="http://localhost:11434").chat(
                model="deepseek-r1:7b",
                messages=[{"role": "user", "content": f"{system}\n\nQuestion: {query}"}],
                options={"num_ctx": 4096, "num_predict": 512, "temperature": 0.3},
            )
            answer = _re.sub(r"<think>.*?</think>", "", resp["message"]["content"], flags=_re.DOTALL).strip()
    except Exception as exc:
        log.error("web_answer AI call failed: %s", exc)
        lines = ["Here's what I found online, Sir:\n"]
        for s in sources[:3]:
            if s["snippet"]:
                lines.append(f"• {s['snippet'][:200]}")
        answer = "\n".join(lines)

    return answer, sources
