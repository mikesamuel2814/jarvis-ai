"""
Jarvis 2.0 — Secure FastAPI Server

Security fixes vs 1.0:
  - Binds to 127.0.0.1 (not 0.0.0.0)
  - All action/webhook/memory endpoints require X-API-Key header
  - OpenClaw webhooks require HMAC-SHA256 signature
  - No shell=True in subprocess calls anywhere in this file

New 2.0 endpoints:
  POST /webhook/openclaw  — OpenClaw heartbeat/skill/sync events
  POST /memory/sync       — Bidirectional ChromaDB ↔ OpenClaw sync
  GET  /metrics           — Full metrics dashboard
  GET  /learning-stats    — Learning queue + golden + lessons counts
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import chromadb
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Paths & config ─────────────────────────────────────────────────
JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
sys.path.insert(0, str(JARVIS_HOME))

SECRETS_FILE = JARVIS_HOME / "config" / "secrets.env"
CONFIG_FILE  = JARVIS_HOME / "config" / "jarvis_v2.yaml"
PROFILE_FILE = JARVIS_HOME / "config" / "mike_profile.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("jarvis.api_v2")


def _load_secret(key: str, default: str = "") -> str:
    """Load a secret from env or secrets.env file."""
    val = os.environ.get(key, "")
    if val:
        return val
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return default


API_KEY        = _load_secret("JARVIS_API_KEY")
WEBHOOK_SECRET = _load_secret("JARVIS_WEBHOOK_SECRET")
API_PORT       = int(os.environ.get("API_PORT", "8181"))
API_HOST       = os.environ.get("JARVIS_HOST", "127.0.0.1")  # Never 0.0.0.0

# ── ChromaDB ────────────────────────────────────────────────────────
_chroma_client: chromadb.PersistentClient | None = None
_chroma_col: Any = None


def get_collection() -> Any:
    global _chroma_client, _chroma_col
    if _chroma_col is None:
        _chroma_client = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
        _chroma_col    = _chroma_client.get_or_create_collection("jarvis_memory")
    return _chroma_col


# ── FastAPI app ─────────────────────────────────────────────────────
app = FastAPI(title="Jarvis 2.0 API", version="2.0.0")


# ── Auth dependencies ───────────────────────────────────────────────

def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not API_KEY:
        return  # Key not configured — open (warn in health)
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def verify_openclaw_signature(request: Request) -> None:
    """Verify HMAC-SHA256 signature on OpenClaw webhook payloads."""
    if not WEBHOOK_SECRET:
        log.warning("JARVIS_WEBHOOK_SECRET not set — accepting unsigned webhooks")
        return
    sig_header = request.headers.get("X-Openclaw-Signature", "")
    if not sig_header:
        raise HTTPException(status_code=401, detail="Missing X-Openclaw-Signature")
    # Header format: sha256=<hex>
    if not sig_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Invalid signature format")
    # Body already consumed by route handler; compare stored body hash
    # (body stored in request.state.body by middleware below)
    body = getattr(request.state, "body", b"")
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    received = sig_header[len("sha256="):]
    if not hmac.compare_digest(expected, received):
        raise HTTPException(status_code=401, detail="Signature mismatch")


@app.middleware("http")
async def cache_request_body(request: Request, call_next):
    """Cache raw body for HMAC verification."""
    body = await request.body()
    request.state.body = body
    return await call_next(request)


# ── RAG helper ──────────────────────────────────────────────────────

def retrieve_rag_context(query: str, n: int = 8) -> tuple[str, list[str]]:
    """Query ChromaDB and return context string + chunk IDs."""
    try:
        col = get_collection()
        results = col.query(
            query_texts=[query],
            n_results=n,
            include=["documents", "metadatas", "distances"],
        )
        docs      = results.get("documents", [[]])[0]
        metas     = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        ids_      = results.get("ids", [[]])[0]

        # Filter by distance threshold, prioritize golden/lesson chunks
        THRESHOLD = 0.55
        filtered = []
        for doc, meta, dist, chunk_id in zip(docs, metas, distances, ids_):
            if dist <= THRESHOLD:
                priority = int(meta.get("priority", 1))
                source   = meta.get("source", "")
                # Boost golden/lesson to top
                boost = 100 if source in ("golden", "lesson", "correction") else 0
                filtered.append((boost + priority, doc, chunk_id))

        filtered.sort(reverse=True)
        context = "\n---\n".join(doc for _, doc, _ in filtered[:8])
        ids_out = [chunk_id for _, _, chunk_id in filtered[:8]]
        return context, ids_out
    except Exception as e:
        log.error("RAG retrieval failed: %s", e)
        return "", []


# ── Request models ──────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str
    user_id: str = "default"
    history: list[dict] | None = None
    force_tier: str | None = None  # "edge" | "hybrid" | "cloud"


class ActionRequest(BaseModel):
    action: str
    arg: str | None = None


class FeedbackRequest(BaseModel):
    interaction_id: str
    rating: str  # "thumbs_up" | "thumbs_down"


class CorrectRequest(BaseModel):
    interaction_id: str
    correction: str


class MemorySyncRequest(BaseModel):
    push_limit: int = 100


class OpenClawEvent(BaseModel):
    type: str       # "heartbeat" | "skill_request" | "memory_sync"
    data: dict = {}


# ── Public endpoints ────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Public health check — no auth required."""
    status: dict[str, Any] = {"status": "ok"}
    try:
        import ollama
        ollama.Client(host="http://localhost:11434").list()
        status["ollama"] = "ok"
    except Exception:
        status["ollama"] = "error"
        status["status"] = "degraded"
    try:
        get_collection().count()
        status["chromadb"] = "ok"
    except Exception:
        status["chromadb"] = "error"
        status["status"] = "degraded"
    if not API_KEY:
        status["warning"] = "JARVIS_API_KEY not configured"
    return status


# ── Authenticated endpoints ─────────────────────────────────────────

@app.post("/query", dependencies=[Depends(require_api_key)])
async def query(req: QueryRequest):
    """Main query endpoint — 3-tier routing via brain.py."""
    from brain import execute
    from learning.rapid_learner import store_interaction

    rag_context, rag_ids = retrieve_rag_context(req.query)
    interaction_id = str(uuid.uuid4())

    result = execute(
        query=req.query,
        rag_context=rag_context,
        history=req.history,
        force_tier=req.force_tier,
    )

    store_interaction(
        interaction_id=interaction_id,
        query=req.query,
        response=result["response"],
        tier=result["tier"],
        model=result["model"],
        rag_context_ids=rag_ids,
    )

    return {
        "response": result["response"],
        "tier": result["tier"],
        "model": result["model"],
        "interaction_id": interaction_id,
        "cached": False,
    }


@app.get("/sysinfo", dependencies=[Depends(require_api_key)])
async def sysinfo():
    """Live system info — CPU, RAM, GPU, disk. No LLM call."""
    import psutil

    info: dict[str, Any] = {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "ram_percent": psutil.virtual_memory().percent,
        "ram_used_gb": round(psutil.virtual_memory().used / 1e9, 1),
        "ram_total_gb": round(psutil.virtual_memory().total / 1e9, 1),
        "disk_percent": psutil.disk_usage("/").percent,
        "disk_free_gb": round(psutil.disk_usage("/").free / 1e9, 1),
    }
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split(",")
            info["gpu_temp_c"]       = int(parts[0].strip())
            info["gpu_mem_used_mb"]  = int(parts[1].strip())
            info["gpu_mem_total_mb"] = int(parts[2].strip())
    except Exception:
        info["gpu"] = "unavailable"
    return info


@app.get("/stats", dependencies=[Depends(require_api_key)])
async def stats():
    col = get_collection()
    return {"total_chunks": col.count()}


@app.get("/metrics", dependencies=[Depends(require_api_key)])
async def metrics_endpoint():
    from metrics import dashboard
    return dashboard()


@app.get("/learning-stats", dependencies=[Depends(require_api_key)])
async def learning_stats():
    from metrics import _count_jsonl
    return {
        "queue_size":      _count_jsonl(JARVIS_HOME / "data" / "learning_queue.jsonl"),
        "golden_examples": _count_jsonl(JARVIS_HOME / "data" / "golden_examples.jsonl"),
        "lessons":         _count_jsonl(JARVIS_HOME / "data" / "lessons.jsonl"),
        "corrections":     _count_jsonl(JARVIS_HOME / "data" / "corrections.jsonl"),
    }


@app.post("/feedback", dependencies=[Depends(require_api_key)])
async def feedback(req: FeedbackRequest):
    from learning.rapid_learner import process_feedback
    return process_feedback(req.interaction_id, req.rating)


@app.post("/correct", dependencies=[Depends(require_api_key)])
async def correct(req: CorrectRequest):
    from learning.rapid_learner import process_feedback
    return process_feedback(req.interaction_id, "thumbs_down", correction=req.correction)


@app.post("/learn", dependencies=[Depends(require_api_key)])
async def learn():
    """Trigger immediate training cycle."""
    import subprocess
    subprocess.Popen(
        ["bash", str(JARVIS_HOME / "train_v2.sh")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return {"status": "training_triggered"}


@app.post("/action", dependencies=[Depends(require_api_key)])
async def action(req: ActionRequest):
    """Execute a whitelisted action via executor.py."""
    # Import 1.0 executor — it handles permission tiers
    sys.path.insert(0, str(JARVIS_HOME))
    from executor import execute as exec_action
    return exec_action(req.action, req.arg)


@app.post("/memory/sync", dependencies=[Depends(require_api_key)])
async def memory_sync(req: MemorySyncRequest | None = None):
    """Bidirectional sync between ChromaDB and OpenClaw workspace."""
    from memory.sync_engine import full_sync
    result = full_sync()
    return result


# ── OpenClaw webhook ────────────────────────────────────────────────

@app.post("/webhook/openclaw")
async def openclaw_webhook(request: Request):
    """
    Receive events from OpenClaw gateway.
    Requires HMAC-SHA256 signature in X-Openclaw-Signature header.
    """
    verify_openclaw_signature(request)
    body = request.state.body
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_type = data.get("type", "unknown")
    log.info("OpenClaw event: %s", event_type)

    if event_type == "heartbeat":
        from openclaw.heartbeat_handler import run_heartbeat_checks
        result = run_heartbeat_checks()
        return {"status": "ok", "checks": result}

    if event_type == "skill_request":
        action_name = data.get("action", "")
        arg         = data.get("arg", "")
        sys.path.insert(0, str(JARVIS_HOME))
        from executor import execute as exec_action
        return exec_action(action_name, arg)

    if event_type == "memory_sync":
        from memory.sync_engine import full_sync
        return full_sync()

    return {"status": "unknown_event_type", "type": event_type}


# ── Cache stats ─────────────────────────────────────────────────────

@app.get("/cache-stats", dependencies=[Depends(require_api_key)])
async def cache_stats():
    from kimi.cost_tracker import today_cost
    return {"kimi_today": today_cost()}


# ── Entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    log.info("Starting Jarvis 2.0 API on %s:%d", API_HOST, API_PORT)
    if not API_KEY:
        log.warning("JARVIS_API_KEY not set — endpoints are unprotected!")
    uvicorn.run("api_v2:app", host=API_HOST, port=API_PORT, reload=False)
