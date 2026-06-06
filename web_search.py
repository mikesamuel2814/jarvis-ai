"""
Jarvis Web Research Module

Strategy:
  Search  — DuckDuckGo (ddgs) PRIMARY, always reliable.
            ddgs.news() for news/trending queries → richer articles.
            Chrome optional for /browse <url> only (too unstable for search).
  Scrape  — trafilatura for static pages.
            Skip known JS-SPA domains (Binance, CoinDesk, CNBC…) — use
            their ddgs snippet instead, which is already good enough.

Public API:
  is_web_query(text)            → bool
  is_news_query(text)           → bool
  search(query, num)            → list[{title, url, snippet, date?}]
  scrape(url)                   → clean text (or "" for SPA domains)
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

_NEWS_TRIGGERS = re.compile(
    r"\b(news|trending|latest|breaking|headlines|today|crypto market|market update"
    r"|what.?s happening|what happened|recent events|current events)\b",
    re.IGNORECASE,
)

_WEATHER_TRIGGERS = re.compile(
    r"\b(weather|temperature|forecast|rain|sunny|cloudy|humidity|wind|storm|hot|cold)\b",
    re.IGNORECASE,
)
_WEATHER_CITY = re.compile(
    r"\b(?:weather|temperature|forecast)\b.*?\bin\b\s+([A-Za-z\s]{2,30}?)(?:\s+today|\s+now|\s*\?|$)",
    re.IGNORECASE,
)


def is_weather_query(text: str) -> bool:
    return bool(_WEATHER_TRIGGERS.search(text))


def get_weather(query: str) -> str:
    """Fetch weather from wttr.in — no JS, instant, no API key needed."""
    # Extract city name from query
    city = "auto"
    m = _WEATHER_CITY.search(query)
    if m:
        city = m.group(1).strip().replace(" ", "+")
    elif "dhaka" in query.lower():
        city = "Dhaka"
    elif "chittagong" in query.lower() or "chattogram" in query.lower():
        city = "Chittagong"

    try:
        import requests as _req
        # Format: location + condition + temp + humidity + wind
        url = f"https://wttr.in/{city}?format=%l:+%C+%t,+humidity+%h,+wind+%w"
        r = _req.get(url, timeout=8)
        if r.status_code == 200 and r.text.strip():
            return r.text.strip()
    except Exception as exc:
        log.warning("wttr.in failed: %s", exc)
    return ""

# Domains whose pages require heavy JS — scraping returns garbage or crashes.
# We use the ddgs snippet for these instead of scraping.
_SPA_DOMAINS = {
    "binance.com", "coinmarketcap.com", "coinbase.com", "kraken.com",
    "cnbc.com", "bloomberg.com", "reuters.com", "wsj.com", "ft.com",
    "coindesk.com", "cointelegraph.com", "investing.com", "tradingview.com",
    "twitter.com", "x.com", "instagram.com", "facebook.com", "reddit.com",
    "linkedin.com",
}


def is_web_query(text: str) -> bool:
    return bool(_WEB_PATTERN.search(text))


def is_news_query(text: str) -> bool:
    return bool(_NEWS_TRIGGERS.search(text))


def _is_spa(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lstrip("www.")
        return any(host == d or host.endswith("." + d) for d in _SPA_DOMAINS)
    except Exception:
        return False


# ── Search — DuckDuckGo primary ───────────────────────────────────────────

def search(query: str, num: int = 5) -> list[dict]:
    """
    DuckDuckGo search (news search for news queries, text search otherwise).
    Chrome is intentionally NOT used here — it crashes on SPA news/crypto sites.
    Chrome is reserved for /browse <url> explicit navigation only.
    """
    # News queries → ddgs.news() gives dated articles with real content
    if is_news_query(query):
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                raw = list(ddgs.news(query, max_results=num))
            results = [
                {
                    "title":   r.get("title", ""),
                    "url":     r.get("url", r.get("link", "")),
                    "snippet": r.get("body", r.get("excerpt", "")),
                    "date":    r.get("date", ""),
                    "source":  r.get("source", ""),
                    "image":   r.get("image", ""),
                }
                for r in raw
            ]
            if results:
                log.info("search('%s'): %d news results via ddgs.news()", query, len(results))
                return results
        except Exception as exc:
            log.warning("ddgs.news() failed (%s), falling back to ddgs.text()", exc)

    # General text search
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=num))
        results = [
            {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in raw
        ]
        log.info("search('%s'): %d results via ddgs.text()", query, len(results))
        return results
    except Exception as exc:
        log.error("ddgs.text() failed: %s", exc)
        return []


# ── Scrape — trafilatura for static pages, skip SPAs ─────────────────────

def scrape(url: str, max_chars: int = 3500) -> str:
    """
    Fetch page text via trafilatura (static HTML, no JS engine).
    Returns "" for known SPA domains — caller should use ddgs snippet instead.
    """
    if _is_spa(url):
        log.debug("scrape: skipping SPA domain %s", url)
        return ""

    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        text = trafilatura.extract(
            downloaded or "",
            include_tables=True,
            include_comments=False,
            no_fallback=False,
        )
        if text and len(text) > 100:
            log.debug("scrape('%s'): %d chars via trafilatura", url, len(text))
            return text[:max_chars]
    except Exception as exc:
        log.debug("trafilatura scrape failed for %s: %s", url, exc)

    return ""


# ── Research pipeline ─────────────────────────────────────────────────────

def research(query: str, num_results: int = 5, scrape_top: int = 2) -> dict:
    """
    search → (optionally scrape) → build AI context block.
    Returns {query, sources, context, elapsed}.
    """
    t0 = time.time()
    results = search(query, num=num_results)
    sources = []

    for i, r in enumerate(results):
        url = r.get("url", "")
        # Scrape non-SPA pages to get full content; SPAs use snippet only
        full_text = ""
        if i < scrape_top and url and not _is_spa(url):
            full_text = scrape(url)
        sources.append({
            "title":     r.get("title", ""),
            "url":       url,
            "snippet":   r.get("snippet", ""),
            "date":      r.get("date", ""),
            "source":    r.get("source", ""),
            "image":     r.get("image", ""),
            "full_text": full_text,
        })

    ctx_parts = [f"Web search results for: {query}\n"]
    for i, s in enumerate(sources, 1):
        header = f"[{i}] {s['title']}"
        if s.get("date"):
            header += f"  ({s['date'][:10]})"
        if s.get("source"):
            header += f"  — {s['source']}"
        ctx_parts.append(header)
        ctx_parts.append(f"URL: {s['url']}")
        if s.get("image"):
            ctx_parts.append(f"Image: {s['image']}")
        # Prefer full scraped text; fall back to snippet
        body = s["full_text"] or s["snippet"]
        ctx_parts.append(body[:2000] if body else "(no content)")
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
    num_results: int = 5,
) -> tuple[str, list[dict]]:
    """
    search → (scrape static pages) → AI synthesise → Telegram-ready reply.
    Returns (answer_text, sources).
    """
    # Weather short-circuit — wttr.in is instant and always accurate
    if is_weather_query(query):
        weather = get_weather(query)
        if weather:
            return f"Sir, {weather}", []

    data = research(query, num_results=num_results)
    sources = data["sources"]

    if not sources:
        return "Couldn't find anything online for that, Sir.", []

    system = (
        "You are Jarvis, Mike Samuel's personal AI assistant.\n"
        "Using ONLY the web search results below, answer the question concisely.\n"
        "Rules:\n"
        "- Open with 'Sir,' exactly once\n"
        "- 4-6 bullet points, each under 20 words\n"
        "- Lead with the single most important/surprising fact\n"
        "- Include dates where available\n"
        "- Include 1-2 source URLs at the end\n"
        "- Never say 'based on results' or 'according to'\n"
        "- No fluff, no filler\n\n"
        f"Web Context:\n{data['context'][:5000]}"
    )

    try:
        if use_cloud:
            import claude_client
            answer, _ = claude_client.get_client().query(system=system, user=query)
        else:
            import ollama as _ollama
            resp = _ollama.Client(host="http://localhost:11434").chat(
                model="phi4-mini",
                messages=[{"role": "user", "content": f"{system}\n\nQuestion: {query}"}],
                options={"num_ctx": 2048, "num_predict": 400, "temperature": 0.3},
            )
            raw = resp["message"]["content"]
            answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    except Exception as exc:
        log.error("web_answer AI call failed: %s", exc)
        # Graceful fallback — format the snippets ourselves
        lines = ["Sir, here's what I found:\n"]
        for s in sources[:4]:
            title = s.get("title", "").strip()
            snippet = (s.get("snippet") or s.get("full_text", "")).strip()
            date = f" ({s['date'][:10]})" if s.get("date") else ""
            url = s.get("url", "")
            if title or snippet:
                lines.append(f"• {title}{date} — {snippet[:160]}")
                if url:
                    lines.append(f"  {url}")
        answer = "\n".join(lines)

    return answer, sources
