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

import chromadb
import ollama

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
sys.path.insert(0, str(JARVIS_HOME))

from indexer import load_config, log

INTERACTIONS_LOG = JARVIS_HOME / "data" / "interactions.jsonl"

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
        f"A user rated this AI response as BAD.\n"
        f"Query: {query}\n"
        f"Response given: {response}\n"
        + (f"Correct answer should be: {correction}\n" if correction else "")
        + "\nIn one sentence, what rule should the AI remember for next time? "
        "Start with 'When asked about' or 'When the user says'."
    )
    try:
        resp = ollama.chat(
            model="deepseek-r1:7b",
            messages=[
                {"role": "system", "content": "Extract a concise one-sentence rule from a failed AI response. No preamble."},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.2, "num_predict": 128, "num_ctx": 1024},
        )
        text = _strip(resp["message"]["content"]).strip()
        # Take only the first sentence
        sentence = text.split(".")[0].strip()
        if len(sentence) > 15:
            return sentence + "."
    except Exception:
        pass
    return None


def run_learning():
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

    log(f"Learner: done. +{lessons_added} lessons, +{golden_added} golden examples. Memory: {collection.count()} chunks total.")


if __name__ == "__main__":
    run_learning()
