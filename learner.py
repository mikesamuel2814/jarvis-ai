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
DECISION_STATE   = JARVIS_HOME / "data" / "decision_state.json"
TRAINING_LOG     = TRAINING_LOG_FOR_LOG

_STRIP_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _STRIP_RE.sub("", text)).strip()


def load_interactions(days: int = 30) -> list[dict]:
    if not INTERACTIONS_LOG.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    results = []
    for line in INTERACTIONS_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry.get("timestamp", "2000-01-01"))
            if ts >= cutoff:
                results.append(entry)
        except Exception:
            continue
    return results


def _chunk_id(prefix: str, text: str) -> str:
    return prefix + "_" + hashlib.md5(text.encode()).hexdigest()[:16]


def _embed(text: str, embed_model: str) -> list[float] | None:
    try:
        import ollama  # noqa: PLC0415
        return ollama.embeddings(model=embed_model, prompt=text[:4096])["embedding"]
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
    """Print interaction statistics for --stats flag."""
    all_interactions = load_interactions(days=365)
    good  = sum(1 for i in all_interactions if i.get("rating") == "good")
    bad   = sum(1 for i in all_interactions if i.get("rating") == "bad")
    unrated = sum(1 for i in all_interactions if not i.get("rating"))

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
    print(f"  Last training: {last_training}")
    print("=" * 42)


def run_learning():
    import chromadb  # noqa: PLC0415 — lazy import avoids segfault in --stats mode
    import ollama  # noqa: PLC0415

    os.chdir(str(Path(__file__).parent))

    config = load_config()
    embed_model = config["memory"].get("embedding_model", "nomic-embed-text")
    client = chromadb.PersistentClient(path=config["memory"]["path"])
    collection = client.get_or_create_collection(
        name="jarvis_memory",
        metadata={"hnsw:space": "cosine"},
    )

    interactions = load_interactions(days=30)
    if not interactions:
        log("Learner: no interactions to learn from yet.")
        return

    rated_good = [i for i in interactions if i.get("rating") == "good"]
    rated_bad  = [i for i in interactions if i.get("rating") == "bad"]
    corrected  = [i for i in interactions if i.get("correction")]

    log(f"Learner: {len(interactions)} interactions — {len(rated_good)} good, {len(rated_bad)} bad, {len(corrected)} corrections")

    lessons_added = 0
    golden_added = 0

    # Corrections → highest priority lessons
    for entry in corrected:
        corr = entry["correction"]
        query = entry.get("query", "")
        lesson_text = f"CORRECTION: When asked '{query[:120]}', the correct answer is: {corr}"
        upsert_lesson(collection, embed_model, lesson_text, "correction", entry.get("timestamp", ""))
        log(f"  [lesson] {lesson_text[:90]}")
        lessons_added += 1

    # Bad interactions → extract lessons via LLM
    for entry in rated_bad:
        if entry.get("correction"):
            continue  # already handled as correction
        lesson = analyze_bad_interaction(
            entry.get("query", ""),
            entry.get("response", ""),
            None,
        )
        if lesson:
            upsert_lesson(collection, embed_model, lesson, "learned", entry.get("timestamp", ""))
            log(f"  [lesson] {lesson[:90]}")
            lessons_added += 1

    # Good interactions → golden examples
    for entry in rated_good:
        q = entry.get("query", "")
        r = entry.get("response", "")
        if q and r and len(r) > 8:
            upsert_golden(collection, embed_model, q, r, entry.get("timestamp", ""))
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

    log(f"Learner: done. +{lessons_added} lessons, +{golden_added} golden examples. Memory: {collection.count()} chunks total.")


if __name__ == "__main__":
    if "--stats" in sys.argv:
        print_stats()
    else:
        run_learning()
