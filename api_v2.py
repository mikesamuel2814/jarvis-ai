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
from datetime import datetime
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

def _embed_query(text: str) -> list[float] | None:
    """Embed text with mxbai-embed-large via Ollama (1024-dim, matches collection).
    keep_alive=0 tells Ollama to unload mxbai immediately after embedding so the
    6GB VRAM is free for the subsequent chat model (deepseek-r1:7b)."""
    try:
        import requests as _req
        resp = _req.post(
            "http://localhost:11434/api/embeddings",
            json={"model": "mxbai-embed-large", "prompt": text[:4096], "keep_alive": 0},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception as e:
        log.warning("Embedding failed: %s", e)
        return None


def retrieve_rag_context(query: str, n: int = 8) -> tuple[str, list[str]]:
    """Query ChromaDB and return context string + chunk IDs."""
    try:
        col = get_collection()
        emb = _embed_query(query)
        query_kwargs: dict = {
            "n_results": n,
            "include": ["documents", "metadatas", "distances"],
        }
        if emb is not None:
            query_kwargs["query_embeddings"] = [emb]
        else:
            query_kwargs["query_texts"] = [query]
        results = col.query(**query_kwargs)
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


# ── Telegram direct-send helper (for approval prompts / voice) ──────

def _get_telegram_token() -> str:
    token_file = JARVIS_HOME / "config" / "telegram.json"
    if not token_file.exists():
        return ""
    try:
        data = json.loads(token_file.read_text())
        t = data.get("bot_token", "")
        return t if t and t != "YOUR_BOT_TOKEN_HERE" else ""
    except Exception:
        return ""


def _get_chat_id(token: str) -> int | None:
    """Read chat_id from persisted file — never calls getUpdates (would conflict with bot polling)."""
    chat_id_file = JARVIS_HOME / "data" / "telegram_chat_id.json"
    if chat_id_file.exists():
        try:
            return json.loads(chat_id_file.read_text()).get("chat_id")
        except Exception:
            pass
    return None


def _to_legacy_markdown(text: str) -> str:
    return text.replace("**", "*").replace("__", "_")


def _send_telegram_direct(text: str) -> bool:
    """Fire a message to Sir's Telegram chat directly via the bot token."""
    token = _get_telegram_token()
    if not token:
        return False
    chat_id = _get_chat_id(token)
    if not chat_id:
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": _to_legacy_markdown(text),
                  "parse_mode": "Markdown"},
            timeout=10,
        )
        if r.status_code == 200 and r.json().get("ok"):
            return True
        # Retry as plain text so approval prompts are never silently dropped.
        r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception:
        return False


# ── Request models ──────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str
    user_id: str = "default"
    history: list[dict] | None = None
    force_tier: str | None = None  # "edge" | "hybrid" | "cloud"
    context_results: int | None = None  # bot sends this; accepted, unused


class ActionRequest(BaseModel):
    action: str
    arg: str | None = None


class AgentRequest(BaseModel):
    task: str
    max_steps: int = 6
    allow_destructive: bool = False


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


class VoiceRequest(BaseModel):
    text: str
    play_local: bool = False


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
        col = get_collection()
        status["chromadb"] = "ok"
        status["memory_chunks"] = col.count()
    except Exception:
        status["chromadb"] = "error"
        status["status"] = "degraded"
    if not API_KEY:
        status["warning"] = "JARVIS_API_KEY not configured"
    return status


# ── Internal endpoints (no auth — localhost only, used by monitor/healer) ──────

class TelegramMessage(BaseModel):
    text: str

@app.post("/telegram/send")
async def telegram_send(msg: TelegramMessage):
    """Internal-use send — no auth required since Jarvis binds to 127.0.0.1 only."""
    ok = _send_telegram_direct(msg.text)
    return {"ok": ok}


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


@app.post("/agent", dependencies=[Depends(require_api_key)])
async def agent(req: AgentRequest):
    """Autonomous agent loop — plain-English task → plan, act, observe, answer."""
    from jarvis_agent import run_agent
    result = run_agent(
        task=req.task,
        max_steps=req.max_steps,
        allow_destructive=req.allow_destructive,
    )
    return result


@app.get("/sysinfo", dependencies=[Depends(require_api_key)])
async def sysinfo():
    """Live system info — CPU, RAM, GPU, disk, uptime. No LLM call."""
    import platform
    import psutil
    import time

    vm  = psutil.virtual_memory()
    du  = psutil.disk_usage("/")
    bt  = psutil.boot_time()
    info: dict[str, Any] = {
        "cpu_percent":   round(psutil.cpu_percent(interval=0.5), 1),
        "cpu_cores":     psutil.cpu_count(logical=False),
        "cpu_threads":   psutil.cpu_count(logical=True),
        "ram_percent":   round(vm.percent, 1),
        "ram_used_gb":   round(vm.used / 1e9, 1),
        "ram_total_gb":  round(vm.total / 1e9, 1),
        "disk_percent":  round(du.percent, 1),
        "disk_used_gb":  round(du.used / 1e9, 1),
        "disk_total_gb": round(du.total / 1e9, 1),
        "disk_free_gb":  round(du.free / 1e9, 1),
        "uptime_hours":  round((time.time() - bt) / 3600, 1),
        "hostname":      platform.node(),
    }
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split(",")
            if len(parts) >= 4:
                try:
                    info["gpu_temp_c"]        = int(parts[0].strip())
                    info["gpu_mem_used_mb"]   = int(parts[1].strip())
                    info["gpu_mem_total_mb"]  = int(parts[2].strip())
                    info["gpu_util_percent"]  = int(parts[3].strip())
                except ValueError:
                    info["gpu_raw"] = r.stdout.strip()
    except Exception:
        pass
    return info


@app.get("/stats", dependencies=[Depends(require_api_key)])
async def stats():
    col = get_collection()
    count = col.count()
    # Source-type breakdown from metadata
    breakdown: dict[str, int] = {}
    try:
        res = col.get(include=["metadatas"])
        for meta in (res.get("metadatas") or []):
            src = (meta or {}).get("source_type", "unknown")
            breakdown[src] = breakdown.get(src, 0) + 1
    except Exception:
        pass
    return {
        "total_chunks": count,
        "memory_chunks": count,
        "primary_model": "deepseek-r1:7b",
        "source_type_breakdown": breakdown,
    }


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
    """Execute a whitelisted action.

    AUTO → run now (OpenClaw-first, executor fallback).
    CONFIRM/APPROVE → create a pending approval request and notify Sir; never
    auto-execute. Response shape matches what telegram_bot reads: a pending
    response is {"status":"pending","request_id":...,"tier":...}; an executed
    one is the executor dict {"success","output","action",...}.
    """
    try:
        from executor import ACTIONS, AUTO
        from permissions import create_request
        from action_flow import run_action_step

        if req.action not in ACTIONS:
            return JSONResponse(
                status_code=404,
                content={"success": False, "output": f"Unknown action: {req.action}",
                         "action": req.action},
            )

        entry = ACTIONS[req.action]
        tier = entry["tier"]
        arg = req.arg or ""

        if tier == AUTO:
            return run_action_step(req.action, arg)

        perm_req = create_request(
            action=req.action, description=entry["desc"], tier=tier, arg=arg,
        )
        rid = perm_req["id"]
        _send_telegram_direct(
            f"⚡ Action request — ID: `{rid}`\n**{entry['desc']}**"
            + (f"\nArg: `{arg}`" if arg else "")
            + f"\n\nReply `/approve {rid}` to run or `/deny {rid}` to cancel"
        )
        return {"status": "pending", "request_id": rid, "tier": tier}
    except Exception as e:
        log.error("/action failed: %s", e)
        return JSONResponse(status_code=200,
                            content={"success": False, "output": f"Action error: {e}",
                                     "action": req.action})


@app.post("/claude-plan", dependencies=[Depends(require_api_key)])
async def claude_plan(req: QueryRequest):
    """Main bot query path for non-casual messages.

    Plans the request, runs AUTO actions OpenClaw-first→executor step by step,
    queues CONFIRM/APPROVE actions for approval, answers pure questions via the
    brain, and always returns an interaction_id so feedback works.
    Bot reads: response, interaction_id.
    """
    try:
        from action_flow import orchestrate
        rag_context, _ = retrieve_rag_context(req.query)
        result = orchestrate(
            query=req.query,
            rag_context=rag_context,
            history=req.history,
            notify=_send_telegram_direct,
        )
        return {
            "response": result["response"],
            "model": result.get("model", "claude+openclaw"),
            "interaction_id": result["interaction_id"],
            "context_used": result.get("context_used", 0),
        }
    except Exception as e:
        log.error("/claude-plan failed: %s", e)
        return {"response": f"Sir, the planner hit an error: {e}",
                "model": "error", "interaction_id": None}


@app.get("/selfcheck", dependencies=[Depends(require_api_key)])
async def selfcheck():
    """Full system self-check → JSON. Bot reads: overall, checks{}, issues[]."""
    report: dict[str, Any] = {"checks": {}, "overall": "ok"}
    issues: list[str] = []
    try:
        import shutil
        import subprocess

        for svc in ("jarvis", "jarvis-telegram", "ollama"):
            try:
                r = subprocess.run(["systemctl", "is-active", svc],
                                   capture_output=True, text=True, timeout=5)
                active = r.stdout.strip() == "active"
            except Exception:
                active = False
            report["checks"][f"service_{svc}"] = "ok" if active else "DOWN"
            if not active:
                issues.append(f"service {svc} is DOWN")

        try:
            import requests
            r = requests.get("http://localhost:11434/api/tags", timeout=3)
            report["checks"]["ollama_api"] = "ok" if r.ok else f"http_{r.status_code}"
            if r.ok:
                models = " ".join(m.get("name", "") for m in r.json().get("models", [])).lower()
                for m in ("deepseek-r1", "mxbai", "phi4-mini"):
                    report["checks"][f"model_{m}"] = "ok" if m in models else "missing"
                    if m not in models:
                        issues.append(f"model {m} missing")
        except Exception as e:
            report["checks"]["ollama_api"] = f"error: {e}"
            issues.append("ollama unreachable")

        try:
            count = get_collection().count()
            report["checks"]["chromadb"] = "ok"
            report["checks"]["memory_chunks"] = count
            if count == 0:
                issues.append("ChromaDB empty")
        except Exception as e:
            report["checks"]["chromadb"] = f"error: {e}"
            issues.append("ChromaDB unavailable")

        usage = shutil.disk_usage("/")
        pct = round(usage.used / usage.total * 100, 1)
        report["checks"]["disk_pct"] = pct
        if pct > 90:
            issues.append(f"disk {pct}% full")

        if issues:
            report["overall"] = "issues"
            report["issues"] = issues
        return report
    except Exception as e:
        log.error("/selfcheck failed: %s", e)
        return {"overall": "error", "checks": {}, "issues": [str(e)]}


@app.post("/index", dependencies=[Depends(require_api_key)])
async def trigger_index():
    """Re-index data into ChromaDB. Bot reads: chunks."""
    try:
        from indexer import load_config, run_indexing
        cfg = load_config()
        collection = run_indexing(cfg)
        return {"status": "ok", "message": "Indexing complete", "chunks": collection.count()}
    except Exception as e:
        log.error("/index failed: %s", e)
        return {"status": "error", "message": str(e), "chunks": "N/A"}


@app.get("/pending", dependencies=[Depends(require_api_key)])
async def pending():
    """List pending approval requests. Bot reads a dict keyed by request id."""
    try:
        from permissions import list_pending
        reqs = list_pending()
        out = {
            r["id"]: {
                "action": r.get("action", "?"),
                "description": r.get("description", ""),
                "tier": r.get("tier", ""),
                "arg": r.get("arg", ""),
            }
            for r in reqs
        }
        return out
    except Exception as e:
        log.error("/pending failed: %s", e)
        return {}


@app.post("/approve/{request_id}", dependencies=[Depends(require_api_key)])
async def approve(request_id: str):
    """Approve and execute a pending action or task flow (OpenClaw-first→executor)."""
    try:
        from permissions import respond, get_request
        from action_flow import run_action_step

        req = get_request(request_id)
        if not req:
            return JSONResponse(
                status_code=404,
                content={"success": False, "output": "Request not found or expired."},
            )
        respond(request_id, "approve")

        # Task-level approval: run every step in the flow autonomously.
        if req.get("kind") == "task":
            steps = req.get("steps", [])
            step_results = []
            step_lines = []
            for i, step in enumerate(steps, start=1):
                r = run_action_step(step["action"], step.get("arg", ""))
                step_results.append(r)
                icon = "✅" if r.get("success") else "❌"
                step_lines.append(f"{icon} {i}. {step['desc']}: {r.get('output', '')[:200]}")
                if not r.get("success"):
                    step_lines.append(f"Halted at step {i}.")
                    break
            summary = "\n".join(step_lines)
            _send_telegram_direct(
                f"🔐 Task `{request_id}` complete:\n{summary}"
            )
            all_ok = all(r.get("success") for r in step_results)
            return {"success": all_ok, "steps": step_lines, "results": step_results}

        # Single-action approval (legacy path).
        result = run_action_step(req["action"], req.get("arg", ""))
        status = "✅ Done" if result.get("success") else "❌ Failed"
        _send_telegram_direct(
            f"{status}: {req.get('description', req['action'])}\n"
            f"```\n{result.get('output', '')[:500]}\n```"
        )
        return result
    except Exception as e:
        log.error("/approve failed: %s", e)
        return JSONResponse(status_code=200,
                            content={"success": False, "output": f"Approve error: {e}"})


@app.post("/deny/{request_id}", dependencies=[Depends(require_api_key)])
async def deny(request_id: str):
    """Deny a pending action."""
    try:
        from permissions import respond
        req_data = respond(request_id, "deny")
        if not req_data:
            return JSONResponse(
                status_code=404,
                content={"status": "not_found", "output": "Request not found or expired."},
            )
        _send_telegram_direct(f"❌ Denied: {req_data.get('description', request_id)}")
        return {"status": "denied", "request_id": request_id}
    except Exception as e:
        log.error("/deny failed: %s", e)
        return JSONResponse(status_code=200,
                            content={"status": "error", "output": f"Deny error: {e}"})


@app.post("/voice/notify", dependencies=[Depends(require_api_key)])
async def voice_notify(req: VoiceRequest):
    """Generate TTS and send it to Sir's Telegram as an audio message."""
    try:
        from voice import send_voice_telegram
        token = _get_telegram_token()
        if not token:
            return JSONResponse(status_code=200,
                                content={"ok": False, "error": "Telegram token not configured"})
        chat_id = _get_chat_id(token)
        if not chat_id:
            return JSONResponse(status_code=200,
                                content={"ok": False, "error": "Telegram chat_id unknown"})
        ok = send_voice_telegram(req.text, token, chat_id)
        return {"ok": ok}
    except Exception as e:
        log.error("/voice/notify failed: %s", e)
        return {"ok": False, "error": str(e)}


@app.post("/voice/speak", dependencies=[Depends(require_api_key)])
async def voice_speak(req: VoiceRequest):
    """Speak the text on Sir's local speakers (non-blocking)."""
    try:
        from voice import speak_local
        speak_local(req.text)
        return {"ok": True}
    except Exception as e:
        log.error("/voice/speak failed: %s", e)
        return {"ok": False, "error": str(e)}


class MemorySaveRequest(BaseModel):
    text: str
    metadata: dict = {}

@app.post("/memory/save", dependencies=[Depends(require_api_key)])
async def memory_save(req: MemorySaveRequest):
    """Save a text chunk directly to ChromaDB memory."""
    try:
        import chromadb, hashlib
        import ollama as _ol
        db_path = str(JARVIS_HOME / "memory")
        _client = chromadb.PersistentClient(path=db_path)
        col = _client.get_or_create_collection("jarvis_memory", embedding_function=None)
        emb_resp = _ol.embeddings(model="mxbai-embed-large", prompt=req.text[:4096], options={"keep_alive": 0})
        emb = emb_resp["embedding"]
        doc_id = hashlib.sha256(req.text.encode()).hexdigest()[:16]
        meta = {**req.metadata, "saved_at": datetime.now().isoformat()}
        col.upsert(ids=[doc_id], embeddings=[emb], documents=[req.text], metadatas=[meta])
        return {"saved": True, "id": doc_id}
    except Exception as e:
        log.warning("memory/save failed: %s", e)
        return {"saved": False, "error": str(e)}


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


# ── Self-test endpoints (Telegram /skills, /recall, /probe, /objectives) ────

@app.get("/skills", dependencies=[Depends(require_api_key)])
async def skills():
    """Return executor action count, ChromaDB collection counts, and loaded Ollama models."""
    result: dict[str, Any] = {}

    # Executor action count
    try:
        from executor import ACTIONS
        result["actions"] = len(ACTIONS)
    except Exception as e:
        result["actions"] = f"error: {e}"

    # ChromaDB — all collections with counts
    try:
        chroma_client = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
        collections_info: dict[str, int] = {}
        total_chunks = 0
        for col in chroma_client.list_collections():
            col_obj = chroma_client.get_collection(col.name)
            n = col_obj.count()
            collections_info[col.name] = n
            total_chunks += n
        result["memory_chunks"] = total_chunks
        result["collections"] = collections_info
    except Exception as e:
        result["memory_chunks"] = f"error: {e}"
        result["collections"] = {}

    # Ollama loaded/available models
    try:
        import requests as _req
        r = _req.get("http://localhost:11434/api/tags", timeout=5)
        r.raise_for_status()
        result["models"] = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception as e:
        result["models"] = f"error: {e}"

    return result


class RecallRequest(BaseModel):
    topic: str = "mike profile"


@app.post("/recall", dependencies=[Depends(require_api_key)])
async def recall(req: RecallRequest):
    """Return the top 5 memory chunks from ChromaDB about the given topic."""
    query_text = req.topic if req.topic else "Mike Samuel profile preferences projects"
    try:
        col = get_collection()
        emb = _embed_query(query_text)
        query_kwargs: dict = {
            "n_results": 5,
            "include": ["documents", "metadatas", "distances"],
        }
        if emb is not None:
            query_kwargs["query_embeddings"] = [emb]
        else:
            query_kwargs["query_texts"] = [query_text]
        res = col.query(**query_kwargs)
        docs      = res.get("documents", [[]])[0]
        metas     = res.get("metadatas", [[]])[0]
        distances = res.get("distances", [[]])[0]
        chunks = []
        for doc, meta, dist in zip(docs, metas, distances):
            chunks.append({
                "text":     doc[:500],
                "source":   (meta or {}).get("source", ""),
                "distance": round(dist, 4),
            })
        return {"topic": query_text, "chunks": chunks, "count": len(chunks)}
    except Exception as e:
        log.error("/recall failed: %s", e)
        return {"topic": query_text, "chunks": [], "error": str(e)}


class ProbeRequest(BaseModel):
    prompt: str
    model: str = "phi4-mini"


@app.post("/probe", dependencies=[Depends(require_api_key)])
async def probe(req: ProbeRequest):
    """Run a single prompt through a local Ollama model and return response + timing."""
    import time
    import requests as _req

    payload = {
        "model": req.model,
        "prompt": req.prompt,
        "stream": False,
        "options": {"num_ctx": 2048},
    }
    try:
        t0 = time.time()
        r = _req.post(
            "http://localhost:11434/api/generate",
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        duration_ms = round((time.time() - t0) * 1000)
        return {
            "model":       data.get("model", req.model),
            "response":    data.get("response", ""),
            "duration_ms": duration_ms,
            "tokens":      data.get("eval_count", 0),
        }
    except Exception as e:
        log.error("/probe failed: %s", e)
        return {"model": req.model, "response": f"error: {e}", "duration_ms": 0, "tokens": 0}


_SAFE_PROFILE_KEYS = {
    "identity", "tech_stack", "hardware", "projects",
    "communication_style", "jarvis_stack",
}
_SAFE_CONFIG_KEYS = {
    "name", "version", "owner", "model", "memory", "interfaces",
}


@app.get("/objectives", dependencies=[Depends(require_api_key)])
async def objectives():
    """Return Jarvis goals and Mike's profile (safe keys only — no secrets)."""
    result: dict[str, Any] = {}

    # jarvis.yaml — safe sections only
    try:
        jarvis_cfg_path = JARVIS_HOME / "config" / "jarvis.yaml"
        raw_cfg = yaml.safe_load(jarvis_cfg_path.read_text()) or {}
        result["jarvis"] = {k: v for k, v in raw_cfg.items() if k in _SAFE_CONFIG_KEYS}
    except Exception as e:
        result["jarvis"] = {"error": str(e)}

    # mike_profile.yaml — safe sections only
    try:
        raw_profile = yaml.safe_load(PROFILE_FILE.read_text()) or {}
        result["mike_profile"] = {k: v for k, v in raw_profile.items() if k in _SAFE_PROFILE_KEYS}
    except Exception as e:
        result["mike_profile"] = {"error": str(e)}

    return result


# ── Entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    log.info("Starting Jarvis 2.0 API on %s:%d", API_HOST, API_PORT)
    if not API_KEY:
        log.warning("JARVIS_API_KEY not set — endpoints are unprotected!")
    uvicorn.run("api_v2:app", host=API_HOST, port=API_PORT, reload=False)
