#!/usr/bin/env python3
"""
Jarvis Auto Web Search Trainer

Silently searches the web when Jarvis detects missing or low-confidence information,
then extracts lessons and stores them in ChromaDB + skillset for immediate use.

Rules:
  - Runs in a background daemon thread — NEVER blocks query responses
  - Privacy-safe: never searches with personal/project data (AsthaCash, credentials, etc.)
  - Rate-limited: max 3 silent searches per 10 minutes
  - Lessons stored immediately for use in next query

Called from:
  - brain.py execute() — after any response flagged as low-confidence
  - api.py /query — when response contains uncertainty markers
  - learner.py — during training cycle for gaps detected in skill_gaps[]
  - decision_engine.py — when AI decision references unknown info
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
LOG_FILE    = JARVIS_HOME / "logs" / "web_trainer.log"
SEARCH_LOG  = JARVIS_HOME / "data" / "web_searches.jsonl"
RATE_STATE  = JARVIS_HOME / "data" / "web_trainer_state.json"

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [web_trainer] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── Privacy guard: NEVER search with these terms ──────────────────────────────
_PRIVACY_BLOCK = re.compile(
    r"(payment.gateway|asthacash|starline|\.ssh|credentials|\.env|secrets|"
    r"private_key|id_rsa|id_ed25519|mike samuel|mikesamuel|admin93|38\.47\.35)",
    re.IGNORECASE,
)

# ── Uncertainty markers that trigger a web search ─────────────────────────────
_UNCERTAINTY_PATTERNS = re.compile(
    r"\b(i don'?t know|not sure|i'?m not certain|unclear|i lack|no information|"
    r"outdated|my knowledge|as of my training|i cannot confirm|i'?m unable to verify|"
    r"you may want to check|please verify|search online|i'?m not familiar|"
    r"limited information|i don'?t have access|latest|real.?time)\b",
    re.IGNORECASE,
)

# ── Rate limit: max N searches per window ─────────────────────────────────────
_MAX_SEARCHES    = 3
_WINDOW_SECONDS  = 600   # 10 minutes

# ── Background thread pool ────────────────────────────────────────────────────
_search_lock = threading.Lock()


# ── Rate limiter ──────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        if RATE_STATE.exists():
            return json.loads(RATE_STATE.read_text())
    except Exception:
        pass
    return {"searches": [], "total": 0}


def _save_state(state: dict) -> None:
    try:
        RATE_STATE.parent.mkdir(parents=True, exist_ok=True)
        RATE_STATE.write_text(json.dumps(state))
    except Exception:
        pass


def _is_rate_limited() -> bool:
    state = _load_state()
    now = time.time()
    recent = [ts for ts in state.get("searches", []) if now - ts < _WINDOW_SECONDS]
    return len(recent) >= _MAX_SEARCHES


def _record_search() -> None:
    state = _load_state()
    now = time.time()
    recent = [ts for ts in state.get("searches", []) if now - ts < _WINDOW_SECONDS]
    recent.append(now)
    state["searches"] = recent
    state["total"] = state.get("total", 0) + 1
    _save_state(state)


# ── Core logic ────────────────────────────────────────────────────────────────

def is_privacy_safe(query: str) -> bool:
    """Return True if query is safe to send to external search."""
    return not _PRIVACY_BLOCK.search(query)


def needs_web_search(query: str, response: str) -> bool:
    """
    Return True if the response shows uncertainty / missing info AND
    the query looks like it might benefit from web data.
    """
    if not is_privacy_safe(query):
        return False
    if _UNCERTAINTY_PATTERNS.search(response):
        return True
    # Explicit knowledge-gap markers
    if len(response.strip()) < 80:
        return True
    return False


def _extract_lessons_from_results(query: str, web_context: str) -> list[dict]:
    """
    Use DeepSeek-R1:7b locally to extract 1-3 concise lessons from web results.
    Returns list of lesson dicts suitable for skillset.add_rule() and ChromaDB.
    """
    prompt = f"""You are a knowledge extractor for an AI assistant called Jarvis.

Given this query and web search results, extract 1-3 concise, actionable lessons
that Jarvis should remember for future similar queries.

QUERY: {query}

WEB RESULTS:
{web_context[:3000]}

Output ONLY a JSON array of lessons. Each lesson: {{"lesson": "...", "priority": 1-10}}
Example: [{{"lesson": "Python 3.12 removed distutils module — use setuptools instead", "priority": 7}}]

Output JSON only, no explanation:"""

    try:
        import ollama
        client = ollama.Client(host="http://localhost:11434")
        resp = client.chat(
            model="deepseek-r1:7b",
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 2048, "num_predict": 512, "temperature": 0.2},
        )
        raw = resp["message"]["content"]
        # Strip DeepSeek think tags
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        # Extract JSON array
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            lessons = json.loads(match.group())
            return [l for l in lessons if isinstance(l, dict) and "lesson" in l]
    except Exception as exc:
        log.warning("Lesson extraction failed: %s", exc)
    return []


def _store_in_chromadb(query: str, web_context: str, lessons: list[dict]) -> None:
    """Store web search results + lessons in ChromaDB memory."""
    try:
        from chromadb import PersistentClient
        import ollama

        mem_path = str(JARVIS_HOME / "memory")
        client = PersistentClient(path=mem_path)
        col = client.get_or_create_collection("jarvis_memory")

        for i, lesson in enumerate(lessons[:3]):
            text = lesson["lesson"]
            doc = f"[WEB LESSON] {text}\nSource query: {query}"
            emb = ollama.embeddings(
                model="mxbai-embed-large", prompt=doc[:4096]
            )["embedding"]
            doc_id = f"web_{int(time.time())}_{i}"
            col.upsert(
                ids=[doc_id],
                embeddings=[emb],
                documents=[doc],
                metadatas=[{
                    "source": "web_search_trainer",
                    "source_type": "lesson",
                    "lesson_type": "web",
                    "priority": str(lesson.get("priority", 6)),
                    "type": "web_lesson",
                    "ts": str(int(time.time())),
                    "query": query[:200],
                }],
            )
        log.info("Stored %d web lessons in ChromaDB for: %s", len(lessons), query[:60])
    except Exception as exc:
        log.warning("ChromaDB store failed: %s", exc)


def _store_in_skillset(lessons: list[dict]) -> None:
    """Add extracted lessons as rules in skillset.json for prompt injection."""
    try:
        from skillset import add_rule
        for lesson in lessons[:3]:
            add_rule(lesson["lesson"], priority=lesson.get("priority", 6), source="web_search")
        log.info("Added %d lessons to skillset", len(lessons))
    except Exception as exc:
        log.warning("Skillset store failed: %s", exc)


def _log_search(query: str, search_query: str, num_lessons: int, elapsed: float) -> None:
    entry = {
        "ts": int(time.time()),
        "query": query[:200],
        "search_query": search_query[:200],
        "lessons_extracted": num_lessons,
        "elapsed_s": round(elapsed, 1),
    }
    try:
        SEARCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SEARCH_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _build_search_query(query: str, response: str) -> str:
    """
    Extract a clean, short search query from the user's question.
    Strips names, pronouns, personal context — keeps the factual core.
    """
    # Remove personal references that should not be sent externally
    clean = re.sub(r"\b(jarvis|sir|my|mine|our|we|i|mike|samuel)\b", "", query, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip()
    # If too long, take first 80 chars (keyword density is higher at start)
    if len(clean) > 80:
        clean = clean[:80].rsplit(" ", 1)[0]
    return clean or query[:60]


def _run_search_and_train(query: str, response: str) -> None:
    """The actual background worker: search → extract → store."""
    t0 = time.time()

    search_query = _build_search_query(query, response)
    if not is_privacy_safe(search_query):
        log.debug("Skipping web search — privacy guard triggered for: %s", search_query[:60])
        return

    log.info("Silent web search starting for: %s", search_query[:60])

    try:
        from web_search import research
        data = research(search_query, num_results=4, scrape_top=1)
        web_context = data.get("context", "")
    except Exception as exc:
        log.warning("web_search.research() failed: %s", exc)
        return

    if not web_context.strip():
        log.debug("No web results for: %s", search_query[:60])
        return

    lessons = _extract_lessons_from_results(query, web_context)
    if lessons:
        _store_in_skillset(lessons)
        _store_in_chromadb(query, web_context, lessons)

    elapsed = time.time() - t0
    _log_search(query, search_query, len(lessons), elapsed)
    _record_search()
    log.info("Web train complete: %d lessons in %.1fs for: %s", len(lessons), elapsed, search_query[:60])


def silent_search_and_train(query: str, response: str) -> None:
    """
    Public entry point. Non-blocking: spawns daemon thread.
    Call from brain.py / api.py after any uncertain response.

    Rules:
    - Skips if rate-limited (>3 searches in 10min)
    - Skips if query is privacy-unsafe
    - Skips if response doesn't signal uncertainty
    """
    if not needs_web_search(query, response):
        return

    with _search_lock:
        if _is_rate_limited():
            log.debug("Web search rate-limited — skipping for: %s", query[:60])
            return

    threading.Thread(
        target=_run_search_and_train,
        args=(query, response),
        daemon=True,
        name="web-trainer",
    ).start()


def force_search_and_train(query: str, context: str = "") -> dict:
    """
    Forced search + train, used during training cycles (learner.py, skill gaps).
    Runs synchronously. Returns {"lessons": [...], "elapsed": float}.
    """
    if not is_privacy_safe(query):
        return {"lessons": [], "elapsed": 0, "skipped": "privacy"}

    t0 = time.time()
    search_query = _build_search_query(query, context)

    try:
        from web_search import research
        data = research(search_query, num_results=5, scrape_top=2)
        web_context = data.get("context", "")
    except Exception as exc:
        return {"lessons": [], "elapsed": 0, "error": str(exc)}

    lessons = _extract_lessons_from_results(query, web_context)
    if lessons:
        _store_in_skillset(lessons)
        _store_in_chromadb(query, web_context, lessons)

    elapsed = round(time.time() - t0, 1)
    _log_search(query, search_query, len(lessons), elapsed)
    _record_search()
    return {"lessons": lessons, "elapsed": elapsed}


def enrich_skill_gaps() -> int:
    """
    Called by learner.py during 6h training cycle.
    Finds unenriched skill gaps, searches web, and fills enrichment text.
    Returns number of gaps enriched.
    """
    try:
        from skillset import load as sk_load, save as sk_save
        sk = sk_load()
        enriched = 0

        for gap in sk.get("skill_gaps", []):
            if gap.get("enrichment"):
                continue  # already enriched
            topic = gap.get("topic", "")
            if not topic or not is_privacy_safe(topic):
                continue

            log.info("Enriching skill gap: %s", topic)
            result = force_search_and_train(topic)
            if result.get("lessons"):
                gap["enrichment"] = "\n".join(l["lesson"] for l in result["lessons"])
                gap["enriched_ts"] = int(time.time())
                enriched += 1

        if enriched:
            sk_save(sk)
            log.info("Enriched %d skill gaps", enriched)
        return enriched
    except Exception as exc:
        log.warning("enrich_skill_gaps failed: %s", exc)
        return 0


# ── Self-test ─────────────────────────────────────────────────────────────────

def _test():
    print("Test 1: Privacy guard...")
    assert not is_privacy_safe("AsthaCash payment gateway bug")
    assert not is_privacy_safe("my .ssh credentials")
    assert is_privacy_safe("Python asyncio timeout handling")
    print("  ✓ Privacy guard working")

    print("Test 2: Uncertainty detection...")
    assert needs_web_search("Python version?", "I don't know the latest Python version.")
    assert not needs_web_search("AsthaCash config", "Here is your config.")
    print("  ✓ Uncertainty detection working")

    print("Test 3: Rate limiter...")
    limited = _is_rate_limited()
    print(f"  ✓ Rate limited: {limited}")

    print("Test 4: Search query builder...")
    q = _build_search_query("what is the latest Python version Sir?", "")
    print(f"  ✓ Search query: '{q}'")

    print("\n✅ All tests passed")


if __name__ == "__main__":
    _test()
