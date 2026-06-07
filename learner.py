#!/usr/bin/env python3
"""
Jarvis Learner — brain self-improvement from interaction feedback.
Reads interactions.jsonl, stores lessons + golden examples in ChromaDB.
Runs inside train.sh (every 6h) and standalone via: python3 learner.py
"""
import os; os.environ.setdefault("PYTHONUNBUFFERED", "1")

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# chromadb and ollama imported lazily inside run_learning() to avoid
# segfault when called with --stats (which doesn't need them)

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
sys.path.insert(0, str(JARVIS_HOME))

# Never import from indexer — it has top-level chromadb import which segfaults here
import yaml as _yaml

TRAINING_LOG_FOR_LOG = JARVIS_HOME / "logs" / "training.log"


def load_config() -> dict:
    cfg = JARVIS_HOME / "config" / "jarvis.yaml"
    return _yaml.safe_load(cfg.read_text()) if cfg.exists() else {}


def log(msg: str) -> None:
    print(msg, flush=True)
    try:
        TRAINING_LOG_FOR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TRAINING_LOG_FOR_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass

INTERACTIONS_LOG = JARVIS_HOME / "data" / "interactions.jsonl"
GOLDEN_LOG       = JARVIS_HOME / "data" / "golden_examples.jsonl"
CORRECTIONS_LOG  = JARVIS_HOME / "data" / "corrections.jsonl"
LESSONS_LOG      = JARVIS_HOME / "data" / "lessons.jsonl"
DECISION_STATE   = JARVIS_HOME / "data" / "decision_state.json"
ROUTING_LOG_V3   = JARVIS_HOME / "data" / "routing_decisions_v3.jsonl"
TRAINING_LOG     = TRAINING_LOG_FOR_LOG
SKILLS_DIR       = JARVIS_HOME / "skills"
SKILL_GAPS_LOG   = SKILLS_DIR / "gaps.jsonl"
SKILLS_LOG       = SKILLS_DIR / "skills.jsonl"
INBOX_DIR        = JARVIS_HOME / "inbox"
INBOX_PROCESSED  = INBOX_DIR / "processed"


def _read_jsonl(path: Path) -> list[dict]:
    """Read a jsonl file, tolerating missing files / bad lines."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _entry_dt(entry: dict) -> datetime:
    """Best-effort timestamp from an interaction record (ISO or epoch)."""
    ts = entry.get("timestamp")
    if ts:
        try:
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            pass
    epoch = entry.get("ts")
    if isinstance(epoch, (int, float)):
        try:
            return datetime.fromtimestamp(epoch)
        except (ValueError, OSError):
            pass
    return datetime(2000, 1, 1)

_STRIP_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _STRIP_RE.sub("", text)).strip()


def load_interactions(days: int = 30) -> list[dict]:
    cutoff = datetime.now() - timedelta(days=days)
    results = []
    for entry in _read_jsonl(INTERACTIONS_LOG):
        if _entry_dt(entry) >= cutoff:
            results.append(entry)
    return results


def _chunk_id(prefix: str, text: str) -> str:
    return prefix + "_" + hashlib.md5(text.encode()).hexdigest()[:16]


def _embed(text: str, embed_model: str) -> list[float] | None:
    try:
        import ollama  # noqa: PLC0415
        # keep_alive=0 unloads the embedding model from VRAM immediately after
        # use, freeing space for deepseek-r1:7b (6GB VRAM constraint).
        return ollama.embeddings(model=embed_model, prompt=text[:4096], keep_alive=0)["embedding"]
    except Exception:
        return None


def upsert_lesson(collection, embed_model: str, text: str, lesson_type: str, timestamp: str):
    emb = _embed(text, embed_model)
    if emb is None:
        return
    cid = _chunk_id("lesson", text)
    collection.upsert(
        ids=[cid],
        embeddings=[emb],
        documents=[text],
        metadatas=[{
            "source_type": "lesson",
            "lesson_type": lesson_type,
            "source": "learner",
            "timestamp": timestamp,
            "priority": "high",
        }],
    )


def upsert_golden(collection, embed_model: str, query: str, response: str, timestamp: str):
    text = f"Q: {query}\nA: {response}"
    emb = _embed(text, embed_model)
    if emb is None:
        return
    cid = _chunk_id("golden", text)
    collection.upsert(
        ids=[cid],
        embeddings=[emb],
        documents=[text],
        metadatas=[{
            "source_type": "golden",
            "source": "learner",
            "timestamp": timestamp,
            "priority": "high",
        }],
    )


def analyze_bad_interaction(query: str, response: str, correction: str | None) -> str | None:
    prompt = (
        f"You are analyzing a failed response from Jarvis, Mike Samuel's personal AI assistant.\n"
        f"Mike is a full-stack developer. He wants: direct answers, no fluff, technical depth, "
        f"always addressed as Sir.\n\n"
        f"Failed query: {query}\n"
        f"Bad response: {response}\n"
        + (f"Mike's correction: {correction}\n" if correction else "")
        + "\nExtract 1-3 concrete rules Jarvis should follow. Format as short rules:\n"
        '- "When asked about X, always Y"\n'
        '- "Never say Z when Mike asks about W"\n'
        '- "Always include code path when discussing project files"\n\n'
        "Rules only. No explanation."
    )
    try:
        import ollama  # noqa: PLC0415
        resp = ollama.chat(
            model="deepseek-r1:7b",
            messages=[
                {"role": "system", "content": "Extract concise rules from a failed AI response for Jarvis. No preamble."},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.2, "num_predict": 256, "num_ctx": 1024},
            keep_alive=0,
        )
        text = _strip(resp["message"]["content"]).strip()
        if len(text) > 10:
            return text
    except Exception:
        pass
    return None


def summarize_good_interactions(interactions: list[dict]) -> str | None:
    """Ask DeepSeek to identify patterns in what Jarvis got right."""
    rated_good = [i for i in interactions if i.get("rating") == "good"]
    if not rated_good:
        return None

    examples = "\n".join(
        f"- Query: {e.get('query','')[:80]}"
        for e in rated_good[:20]
    )
    prompt = (
        "These are queries where Jarvis (Mike Samuel's personal AI) gave a good response.\n\n"
        f"{examples}\n\n"
        "In 3-5 bullet points, what types of queries does Jarvis handle well? "
        "Be specific (e.g. 'Code debugging questions', 'VPS deployment commands'). "
        "Bullet points only."
    )
    try:
        import ollama  # noqa: PLC0415
        resp = ollama.chat(
            model="deepseek-r1:7b",
            messages=[
                {"role": "system", "content": "Identify patterns in successful AI responses. No preamble."},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.3, "num_predict": 256, "num_ctx": 1024},
            keep_alive=0,
        )
        return _strip(resp["message"]["content"]).strip()
    except Exception:
        return None


def check_memory_quality(collection) -> dict:
    """Sample ChromaDB and assess memory quality."""
    total = collection.count()
    if total == 0:
        return {"total": 0, "avg_len": 0, "sources": {}, "quality": "poor"}

    sample_size = min(20, total)
    try:
        result = collection.get(limit=sample_size, include=["documents", "metadatas"])
    except Exception:
        return {"total": total, "avg_len": 0, "sources": {}, "quality": "poor"}

    docs = result.get("documents", [])
    metas = result.get("metadatas", [])

    avg_len = int(sum(len(d) for d in docs) / len(docs)) if docs else 0

    sources: dict[str, int] = {}
    oldest = None
    for meta in metas:
        st = meta.get("source_type", "unknown")
        sources[st] = sources.get(st, 0) + 1
        ts_str = meta.get("indexed_at") or meta.get("timestamp", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if oldest is None or ts < oldest:
                    oldest = ts
            except Exception:
                pass

    if avg_len >= 300 and total >= 100:
        quality = "good"
    elif avg_len >= 100 and total >= 20:
        quality = "fair"
    else:
        quality = "poor"

    return {
        "total": total,
        "avg_len": avg_len,
        "sources": sources,
        "oldest_chunk": oldest.isoformat() if oldest else "unknown",
        "quality": quality,
    }


def print_stats():
    """Print interaction statistics for --stats flag. Lazy / no chromadb."""
    all_interactions = load_interactions(days=365)
    good  = sum(1 for i in all_interactions if i.get("rating") == "good")
    bad   = sum(1 for i in all_interactions if i.get("rating") == "bad")
    unrated = sum(1 for i in all_interactions if not i.get("rating"))

    golden = len(_read_jsonl(GOLDEN_LOG))
    corrections = len(_read_jsonl(CORRECTIONS_LOG))
    lessons = len(_read_jsonl(LESSONS_LOG))

    last_training = "never"
    last_training_file = JARVIS_HOME / "data" / "last_training.txt"
    if last_training_file.exists():
        last_training = last_training_file.read_text().strip()

    print("=" * 42)
    print("  Jarvis Learner Stats")
    print("=" * 42)
    print(f"  Total interactions (last 365d): {len(all_interactions)}")
    print(f"  Good:    {good}")
    print(f"  Bad:     {bad}")
    print(f"  Unrated: {unrated}")
    print(f"  Golden examples:    {golden}")
    print(f"  Corrections:        {corrections}")
    print(f"  Lesson candidates:  {lessons}")
    print(f"  Last training: {last_training}")

    # Routing-accuracy summary (no chromadb needed — pure correlation).
    rf = analyze_routing_feedback(all_interactions)
    if rf["per_tier"]:
        print("  -- v3 routing --")
        for t, s in sorted(rf["per_tier"].items(),
                           key=lambda kv: -kv[1]["n"]):
            print(f"    {t:7} n={s['n']:<3} good={s['good']} bad={s['bad']} "
                  f"bad_rate={s['bad_rate']:.0%}")
        for ins in rf["insights"]:
            print(f"    ⚠ {ins[:96]}")
    print("=" * 42)


def detect_skill_gaps(interactions: list[dict]) -> int:
    """Log topics where Jarvis failed 3+ times as skills needing training."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    bad = [i for i in interactions if i.get("rating") in ("bad", "thumbs_down", -1, 0)]
    topic_fails: dict[str, int] = {}
    keywords = ["code", "deploy", "error", "system", "memory", "project",
                "web", "kali", "docker", "nginx", "vps", "ssh", "git",
                "identity", "jarvis", "weather", "voice", "action"]
    for entry in bad:
        q = entry.get("query", "").lower()
        matched = next((kw for kw in keywords if kw in q), "general")
        topic_fails[matched] = topic_fails.get(matched, 0) + 1
        gap = {"date": datetime.now().isoformat()[:10], "topic": matched,
               "query": entry.get("query", "")[:150], "gap_type": "wrong_answer"}
        try:
            with open(SKILL_GAPS_LOG, "a") as f:
                f.write(json.dumps(gap) + "\n")
        except Exception:
            pass
    promoted = 0
    for topic, count in topic_fails.items():
        if count >= 3:
            skill = {"date": datetime.now().isoformat()[:10], "topic": topic,
                     "fail_count": count, "status": "needs_training"}
            try:
                with open(SKILLS_LOG, "a") as f:
                    f.write(json.dumps(skill) + "\n")
            except Exception:
                pass
            promoted += 1
    return promoted


def auto_digest_inbox(collection, embed_model: str) -> int:
    """Index any files dropped into ~/.jarvis/inbox/ into ChromaDB knowledge."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    INBOX_PROCESSED.mkdir(parents=True, exist_ok=True)
    digested = 0
    for fpath in INBOX_DIR.iterdir():
        if fpath.suffix.lower() not in (".txt", ".md", ".json") or not fpath.is_file():
            continue
        try:
            text = fpath.read_text(errors="replace")[:20000]
            chunks = [text[i:i+800] for i in range(0, len(text), 700)]
            for chunk in chunks:
                if len(chunk.strip()) < 20:
                    continue
                emb = _embed(chunk, embed_model)
                if emb is None:
                    continue
                cid = _chunk_id("inbox", chunk)
                collection.upsert(ids=[cid], embeddings=[emb], documents=[chunk],
                                  metadatas=[{"source_type": "inbox", "source": str(fpath.name),
                                              "indexed_at": datetime.now().isoformat()}])
            fpath.rename(INBOX_PROCESSED / fpath.name)
            log(f"  [inbox] digested {fpath.name} ({len(chunks)} chunks)")
            digested += 1
        except Exception as e:
            log(f"  [inbox] failed {fpath.name}: {e}")
    return digested


# Valid v3 brain tiers (kept inline so this module needs no heavy imports).
V3_TIERS = {"nano", "edge", "cursor", "hybrid", "kimi", "cloud", "swarm"}
# Cost ordering, cheapest → most expensive. Used to spot over-/under-routing.
TIER_COST = {"nano": 0, "edge": 1, "cursor": 2, "hybrid": 3,
             "swarm": 4, "kimi": 5, "cloud": 6}


def analyze_routing_feedback(interactions: list[dict],
                             collection=None, embed_model: str = "") -> dict:
    """Correlate v3 routing decisions with response outcomes to find systematic
    mis-routes. Builds per-tier good/bad stats, joins the routing-decision log
    for confidence, and emits actionable insights + a memory lesson.

    Returns {"per_tier": {...}, "insights": [...], "lessons_added": int}.
    """
    # Per-tier outcome stats from interactions that carry a real v3 tier.
    per_tier: dict[str, dict] = {}
    for it in interactions:
        tier = (it.get("tier") or "").lower()
        if tier not in V3_TIERS:
            continue
        s = per_tier.setdefault(
            tier, {"n": 0, "good": 0, "bad": 0, "resp_len_sum": 0})
        s["n"] += 1
        rating = it.get("rating")
        if rating == "good":
            s["good"] += 1
        elif rating == "bad":
            s["bad"] += 1
        s["resp_len_sum"] += len(it.get("response", "") or "")

    for t, s in per_tier.items():
        rated = s["good"] + s["bad"]
        s["bad_rate"] = round(s["bad"] / rated, 3) if rated else 0.0
        s["avg_resp_len"] = round(s["resp_len_sum"] / s["n"], 1) if s["n"] else 0

    # Join the routing-decision log (confidence) to bad outcomes by query prefix.
    decisions = _read_jsonl(ROUTING_LOG_V3)
    bad_queries = {(it.get("query", "") or "")[:60].lower()
                   for it in interactions if it.get("rating") == "bad"}
    lowconf_bad = 0
    conf_sum = {"good": 0.0, "good_n": 0}
    for d in decisions:
        snip = (d.get("query_snippet", "") or "").lower()
        conf = float(d.get("confidence", 0.0))
        # snippet starts with "Request: <query>" for synthesised calls; strip it
        probe = snip.replace("request: ", "")[:60]
        if any(probe and probe.startswith(bq[:40]) for bq in bad_queries):
            if conf < 0.6:
                lowconf_bad += 1

    insights: list[str] = []
    for t, s in sorted(per_tier.items(), key=lambda kv: -kv[1]["bad_rate"]):
        rated = s["good"] + s["bad"]
        if rated >= 3 and s["bad_rate"] >= 0.4:
            insights.append(
                f"Tier '{t}' has a high failure rate ({s['bad_rate']:.0%} of "
                f"{rated} rated). Review its routing patterns — queries landing "
                f"here may belong on a different tier.")
        # Under-routing: cheap tier producing very long answers often means the
        # query was harder than the router judged (should have escalated).
        if t in ("nano", "edge") and s["avg_resp_len"] > 1200 and s["n"] >= 3:
            insights.append(
                f"Tier '{t}' is producing long answers (avg "
                f"{s['avg_resp_len']:.0f} chars) — these queries may warrant "
                f"escalation to CLOUD/HYBRID; consider tightening its patterns.")
    if lowconf_bad >= 2:
        insights.append(
            f"{lowconf_bad} low-confidence (<0.6) routes scored 'bad' — the "
            f"classifier is guessing on these; add explicit patterns or seed "
            f"intents for them.")

    # Persist insights into decision_state.json for the dashboard / briefing.
    try:
        state: dict = {}
        if DECISION_STATE.exists():
            state = json.loads(DECISION_STATE.read_text())
        state["routing_feedback"] = {
            "per_tier": per_tier,
            "insights": insights,
            "updated": datetime.now().isoformat(),
        }
        DECISION_STATE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log(f"  [routing] could not write decision_state.json: {e}")

    # Store the top insight as a memory lesson so the brain self-corrects.
    lessons_added = 0
    if insights and collection is not None and embed_model:
        lesson = "ROUTING INSIGHT: " + " ".join(insights[:2])
        upsert_lesson(collection, embed_model, lesson, "routing",
                      datetime.now().isoformat())
        lessons_added = 1

    return {"per_tier": per_tier, "insights": insights,
            "lessons_added": lessons_added}


def run_learning():
    import chromadb  # noqa: PLC0415 — lazy import avoids segfault in --stats mode
    import ollama  # noqa: PLC0415

    os.chdir(str(Path(__file__).parent))

    config = load_config()
    mem_cfg = config.get("memory", {}) if isinstance(config, dict) else {}
    embed_model = mem_cfg.get("embedding_model", "mxbai-embed-large")
    mem_path = mem_cfg.get("path", str(JARVIS_HOME / "memory"))
    client = chromadb.PersistentClient(path=mem_path)
    collection = client.get_or_create_collection(
        name="jarvis_memory",
        embedding_function=None,   # We always supply explicit embeddings (1024-dim mxbai).
        metadata={"hnsw:space": "cosine"},
    )

    interactions = load_interactions(days=30)

    # Pull rated/corrected signals from BOTH the interactions log (updated in
    # place by rapid_learner) and the dedicated artifact files (golden /
    # corrections / lesson candidates) that rapid_learner appends to.
    rated_good = [i for i in interactions if i.get("rating") == "good"]
    rated_bad  = [i for i in interactions if i.get("rating") == "bad"]

    golden_entries = _read_jsonl(GOLDEN_LOG)
    correction_entries = _read_jsonl(CORRECTIONS_LOG)
    lesson_candidates = _read_jsonl(LESSONS_LOG)

    # Dedupe golden by interaction id, merging interactions.jsonl good + golden file
    good_by_id: dict[str, dict] = {}
    for e in rated_good + golden_entries:
        good_by_id[e.get("id") or _chunk_id("g", json.dumps(e, sort_keys=True))] = e
    good_all = list(good_by_id.values())

    if not (interactions or golden_entries or correction_entries or lesson_candidates):
        log("Learner: no interactions to learn from yet.")
        return

    log(
        f"Learner: {len(interactions)} interactions — {len(rated_good)} good, "
        f"{len(rated_bad)} bad | files: {len(golden_entries)} golden, "
        f"{len(correction_entries)} corrections, {len(lesson_candidates)} lesson candidates"
    )

    lessons_added = 0
    golden_added = 0

    # Pre-extracted lesson candidates (from rapid_learner corrections/queue) →
    # upsert directly. These already carry a 'principle' string.
    for cand in lesson_candidates:
        text = cand.get("principle") or cand.get("text") or ""
        if not text:
            continue
        ltype = cand.get("category", "learned")
        ts = cand.get("timestamp") or str(cand.get("ts", ""))
        upsert_lesson(collection, embed_model, text, ltype, ts)
        log(f"  [lesson] {text[:90]}")
        lessons_added += 1

    # Explicit corrections (correction file + any inline-corrected interactions)
    corrected = correction_entries + [i for i in interactions if i.get("correction")]
    seen_corr: set[str] = set()
    for entry in corrected:
        corr = entry.get("correction")
        if not corr:
            continue
        query = entry.get("query", "")
        key = _chunk_id("c", f"{query}|{corr}")
        if key in seen_corr:
            continue
        seen_corr.add(key)
        lesson_text = f"CORRECTION: When asked '{query[:120]}', the correct answer is: {corr}"
        upsert_lesson(collection, embed_model, lesson_text, "correction",
                      entry.get("timestamp", "") or str(entry.get("ts", "")))
        log(f"  [lesson] {lesson_text[:90]}")
        lessons_added += 1

    # Bad interactions (no correction) → extract lessons via LLM
    for entry in rated_bad:
        if entry.get("correction"):
            continue  # already handled as correction
        lesson = analyze_bad_interaction(
            entry.get("query", ""),
            entry.get("response", ""),
            None,
        )
        if lesson:
            upsert_lesson(collection, embed_model, lesson, "learned",
                          entry.get("timestamp", "") or str(entry.get("ts", "")))
            log(f"  [lesson] {lesson[:90]}")
            lessons_added += 1

    # Good interactions / golden examples → golden RAG entries
    for entry in good_all:
        q = entry.get("query", "")
        r = entry.get("response", "")
        if q and r and len(r) > 8:
            upsert_golden(collection, embed_model, q, r,
                          entry.get("timestamp", "") or str(entry.get("ts", "")))
            log(f"  [golden] {q[:60]}")
            golden_added += 1

    # Summarise what Jarvis is doing well → persist in decision_state.json
    good_patterns = summarize_good_interactions(interactions)
    if good_patterns:
        log(f"  [patterns] {good_patterns[:120]}")
        try:
            state: dict = {}
            if DECISION_STATE.exists():
                state = json.loads(DECISION_STATE.read_text())
            state["good_patterns"] = good_patterns
            state["good_patterns_updated"] = datetime.now().isoformat()
            DECISION_STATE.write_text(json.dumps(state, indent=2))
        except Exception as e:
            log(f"  [patterns] Could not write decision_state.json: {e}")

    # Memory quality check
    quality = check_memory_quality(collection)
    log(f"  [quality] total={quality['total']} avg_len={quality['avg_len']} "
        f"sources={quality['sources']} quality={quality['quality']}")
    try:
        TRAINING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TRAINING_LOG, "a") as f:
            f.write(f"[{datetime.now().isoformat()}] quality_check={json.dumps(quality)}\n")
    except Exception:
        pass

    # Auto-detect skill gaps from bad interactions
    gaps_promoted = detect_skill_gaps(interactions)
    if gaps_promoted:
        log(f"  [skills] {gaps_promoted} topics promoted to needs_training")

    # Routing-accuracy feedback: correlate v3 tier choices with outcomes and
    # surface systematic mis-routes (self-tuning router).
    routing_lessons = 0
    try:
        rf = analyze_routing_feedback(interactions, collection, embed_model)
        routing_lessons = rf["lessons_added"]
        lessons_added += routing_lessons
        for ins in rf["insights"]:
            log(f"  [routing] {ins[:120]}")
        if not rf["insights"] and rf["per_tier"]:
            log(f"  [routing] {len(rf['per_tier'])} tiers analysed — no systematic mis-routes")
    except Exception as _rf:
        log(f"  [routing] feedback analysis skipped: {_rf}")

    # Digest any files dropped into ~/.jarvis/inbox/
    inbox_count = auto_digest_inbox(collection, embed_model)
    if inbox_count:
        log(f"  [inbox] {inbox_count} files digested into memory")

    # Save a session summary chunk to memory
    summary = (
        f"Training session {datetime.now().isoformat()[:10]}: "
        f"+{lessons_added} lessons, +{golden_added} golden examples, "
        f"{gaps_promoted} skill gaps flagged, {inbox_count} inbox files ingested. "
        f"Memory total: {collection.count()} chunks."
    )
    emb = _embed(summary, embed_model)
    if emb is not None:
        collection.upsert(
            ids=[_chunk_id("session", summary)],
            embeddings=[emb], documents=[summary],
            metadatas=[{"source_type": "session_summary", "date": datetime.now().isoformat()[:10]}],
        )

    # Enrich any skill gaps using silent web search
    try:
        from web_search_trainer import enrich_skill_gaps
        gaps_enriched = enrich_skill_gaps()
        if gaps_enriched:
            log(f"  [web] {gaps_enriched} skill gaps enriched via web search")
    except Exception as _we:
        log(f"  [web] skill gap enrichment skipped: {_we}")

    # Nightly self-reflection: analyse response quality and add improvement rules
    try:
        from thinking_engine import nightly_self_reflection
        reflection = nightly_self_reflection()
        if reflection.get("rules_added", 0) > 0:
            log(f"  [reflect] Self-reflection added {reflection['rules_added']} rules. "
                f"Avg score: {reflection.get('avg_score', '?')}")
    except Exception as _re:
        log(f"  [reflect] self-reflection skipped: {_re}")

    log(f"Learner: done. +{lessons_added} lessons, +{golden_added} golden, {inbox_count} inbox. Memory: {collection.count()} chunks total.")


if __name__ == "__main__":
    if "--stats" in sys.argv:
        print_stats()
    else:
        run_learning()
