"""
Jarvis v3 API Server
FastAPI on port 8182 (initially), preserves all v2 endpoints.
Feature-flag driven rollout.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
sys.path.insert(0, str(JARVIS_HOME))

SECRETS_FILE = JARVIS_HOME / "config" / "secrets.env"
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis_v3.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("jarvis.api_v3")

# ── Config & Feature Flags ──────────────────────────────────────────

V3_CONFIG: dict = {}


def _load_config():
    global V3_CONFIG
    try:
        if CONFIG_FILE.exists():
            V3_CONFIG = yaml.safe_load(CONFIG_FILE.read_text()) or {}
    except Exception as exc:
        log.warning("Could not load v3 config: %s", exc)
        V3_CONFIG = {}


_load_config()


def _feature_flag(key: str) -> bool:
    return V3_CONFIG.get("v3", {}).get(key, False)


def _load_secret(key: str, default: str = "") -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return default


API_KEY = _load_secret("JARVIS_API_KEY")
WEBHOOK_SECRET = _load_secret("JARVIS_WEBHOOK_SECRET")
log.info("API_KEY loaded (len=%d, prefix=%s)", len(API_KEY), API_KEY[:8] if API_KEY else "EMPTY")
API_PORT = int(os.environ.get("API_PORT", "8181"))
API_HOST = os.environ.get("JARVIS_HOST", "127.0.0.1")

# ── Lazy imports (avoid loading heavy modules at startup) ───────────

_tool_registry = None
_orchestrator = None
_brain_router = None
_guardian = None


_TOOL_MODULES = [
    "tools.core.system", "tools.core.process", "tools.core.file",
    "tools.core.network", "tools.core.security", "tools.core.security_audit",
    "tools.core.dev", "tools.core.database", "tools.core.docker",
    "tools.core.web", "tools.core.backup", "tools.core.automation", "tools.core.comms",
]


def get_tool_registry():
    global _tool_registry
    if _tool_registry is None:
        from tools.registry import ToolRegistry
        for mod in _TOOL_MODULES:
            __import__(mod)
        _tool_registry = ToolRegistry()
        _tool_registry.index_all()
    return _tool_registry


def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from orchestrator import Orchestrator
        worker_count = V3_CONFIG.get("swarm", {}).get("worker_count", 1)
        _orchestrator = Orchestrator(tool_registry=get_tool_registry(), worker_count=worker_count)
    return _orchestrator


def get_brain_router():
    global _brain_router
    if _brain_router is None:
        from brain_router import BrainRouter
        _brain_router = BrainRouter()
    return _brain_router


def get_guardian():
    global _guardian
    if _guardian is None:
        from guardian.monitor import GuardianMonitor
        _guardian = GuardianMonitor()
    return _guardian


# ── FastAPI App ─────────────────────────────────────────────────────

app = FastAPI(title="Jarvis 3.0 API", version="3.0.0")


# ── Auth ────────────────────────────────────────────────────────────

def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not API_KEY:
        return
    log.info("Auth check: received len=%d, expected len=%d, match=%s", len(x_api_key or ""), len(API_KEY), x_api_key == API_KEY)
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def verify_openclaw_signature(request: Request) -> None:
    if not WEBHOOK_SECRET:
        return
    sig_header = request.headers.get("X-Openclaw-Signature", "")
    if not sig_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Invalid signature format")
    body = getattr(request.state, "body", b"")
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    received = sig_header[len("sha256="):]
    if not hmac.compare_digest(expected, received):
        raise HTTPException(status_code=401, detail="Signature mismatch")


@app.middleware("http")
async def cache_request_body(request: Request, call_next):
    body = await request.body()
    request.state.body = body
    return await call_next(request)


# ── Autonomy Gate ───────────────────────────────────────────────────

def _check_autonomy(action_name: str, approved: bool = False) -> tuple[bool, str]:
    """
    Check if an action is allowed to auto-execute.
    Returns (allowed, reason).
    """
    if approved:
        return True, "explicitly approved"
    try:
        from autonomy import should_auto_execute
        return should_auto_execute(action_name)
    except Exception as exc:
        log.warning("Autonomy check failed for %s: %s", action_name, exc)
        return False, f"autonomy unavailable: {exc}"


# ── Request Models ──────────────────────────────────────────────────

class OrchestrateRequest(BaseModel):
    request: str
    allow_destructive: bool = False


class ToolExecuteRequest(BaseModel):
    tool: str = ""
    params: dict = {}
    approved: bool = False


class RouteRequest(BaseModel):
    query: str


class AskRequest(BaseModel):
    query: str
    context: str = ""
    history: list = []
    system_prompt: str = ""


class AgentRequest(BaseModel):
    task: str
    max_steps: int = 6
    allow_destructive: bool = False
    history: list = []


# ── Health ──────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "3.0.0",
        "v3_enabled": _feature_flag("enabled"),
        "time": datetime.utcnow().isoformat(),
    }


# ── v2-compatible endpoints ─────────────────────────────────────────

@app.get("/v2/actions")
def list_actions(x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)
    from tools.compat import list_v2_actions
    return {"actions": list_v2_actions()}


@app.post("/v2/action/{action_name}")
def run_v2_action(action_name: str, payload: dict = {}, x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)
    get_tool_registry()  # ensure tools are loaded
    from tools.compat import run_action
    result = run_action(action_name, arg=payload.get("arg", ""), approved=payload.get("approved", False))
    return result


# ── v2-compatible action endpoint (used by telegram_bot.py) ─────────

@app.post("/action")
def action_post(payload: dict = {}, x_api_key: str = Header(default=None)):
    """v2-compatible action endpoint. Returns {success, output, action, tier, request_id}."""
    require_api_key(x_api_key)
    get_tool_registry()  # ensure tools are loaded
    from tools.compat import run_action
    action_name = payload.get("action", "")
    arg = payload.get("arg", "")
    approved = payload.get("approved", False)

    # Autonomy gate: require approval for R2+ actions unless pre-approved/trusted
    auto_ok, auto_reason = _check_autonomy(action_name, approved=approved)
    if not auto_ok:
        import uuid
        return {
            "success": False,
            "output": f"⛔ {auto_reason}",
            "action": action_name,
            "tier": "R2+",
            "needs_approval": True,
            "request_id": str(uuid.uuid4())[:12],
            "status": "pending",
        }

    result = run_action(action_name, arg=arg, approved=approved)
    # Add request_id for pending approvals
    if result.get("needs_approval"):
        import uuid
        result["request_id"] = str(uuid.uuid4())[:12]
        result["status"] = "pending"
    return result


# ── v3 Tool endpoints ───────────────────────────────────────────────

@app.get("/v3/tools")
def list_tools(category: Optional[str] = None, x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)
    reg = get_tool_registry()
    tools = reg.list_tools(category=category)
    return {"tools": tools, "count": len(tools)}


# NOTE: this static route MUST be declared before /v3/tools/{tool_name},
# otherwise FastAPI matches "search" as a tool_name and returns 404.
@app.get("/v3/tools/search")
def search_tools(q: str, n: int = 10, x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)
    reg = get_tool_registry()
    results = reg.find_tools(q, n=n)
    return {"query": q, "results": results}


@app.get("/v3/tools/{tool_name}")
def get_tool(tool_name: str, x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)
    reg = get_tool_registry()
    meta = reg.get(tool_name)
    if not meta:
        raise HTTPException(status_code=404, detail="Tool not found")
    return meta


@app.post("/v3/tools/{tool_name}/execute")
def execute_tool(tool_name: str, req: ToolExecuteRequest, x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)

    # Autonomy gate: require approval for R2+ tools unless pre-approved/trusted
    auto_ok, auto_reason = _check_autonomy(tool_name, approved=req.approved)
    if not auto_ok:
        import uuid
        return {
            "success": False,
            "output": f"⛔ {auto_reason}",
            "tool": tool_name,
            "needs_approval": True,
            "request_id": str(uuid.uuid4())[:12],
            "status": "pending",
        }

    reg = get_tool_registry()
    result = reg.execute(tool_name, **req.params)
    return {
        "success": result.success,
        "output": result.output,
        "data": result.data,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }


# ── v3 Orchestrator ─────────────────────────────────────────────────

@app.post("/v3/orchestrate")
async def orchestrate(req: OrchestrateRequest, x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)
    if not _feature_flag("orchestrator"):
        raise HTTPException(status_code=503, detail="Orchestrator not enabled")

    orch = get_orchestrator()
    result = await orch.run(req.request, allow_destructive=req.allow_destructive)
    return {
        "answer": result.answer,
        "steps": result.steps,
        "tasks_dispatched": result.tasks_dispatched,
        "tasks_completed": result.tasks_completed,
        "tasks_failed": result.tasks_failed,
        "needs_approval": result.needs_approval,
        "permission_request_id": result.permission_request_id,
        "permission_keyboard": result.permission_keyboard,
        "elapsed_sec": result.elapsed_sec,
    }


@app.post("/v3/permission/{request_id}/{action}")
async def resolve_permission(request_id: str, action: str,
                             x_api_key: str = Header(default=None)):
    """Resolve a progressive-trust permission button press (allow / save / deny).
    Called by the Telegram bot when Sir taps a v3 permission button."""
    require_api_key(x_api_key)
    if action not in ("allow", "save", "deny"):
        raise HTTPException(status_code=400, detail="invalid action")
    handler = get_orchestrator().permission_handler
    res = await handler.handle(f"v3perm:{request_id}:{action}")
    return res


# ── v3 Brain Router ─────────────────────────────────────────────────

@app.post("/v3/route")
def route_query(req: RouteRequest, x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)
    if not _feature_flag("brain_router"):
        raise HTTPException(status_code=503, detail="Brain router not enabled")

    router = get_brain_router()
    tier = router.route(req.query)
    return {"tier": tier.value, "latency_ms": tier.latency_ms}


@app.post("/v3/ask")
async def ask(req: AskRequest, x_api_key: str = Header(default=None)):
    """Core conversational brain: route the query through the 7-tier router AND
    run real inference, with automatic tier fallback. Returns the answer."""
    require_api_key(x_api_key)
    if not _feature_flag("brain_router"):
        raise HTTPException(status_code=503, detail="Brain router not enabled")

    router = get_brain_router()
    try:
        resp = await router.execute(
            req.query, context=req.context, history=req.history or None,
            system_prompt=req.system_prompt,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"inference failed: {exc}")
    return {
        "answer": resp.content,
        "model": resp.model,
        "reasoning": resp.reasoning,
        "latency_ms": round(resp.latency_ms),
    }


@app.post("/agent")
async def agent_endpoint(req: AgentRequest, x_api_key: str = Header(default=None)):
    """Autonomous agent endpoint — plans, executes tools, and synthesizes answers.
    Delegates to jarvis_agent_v3.run_agent_v3()."""
    require_api_key(x_api_key)
    try:
        import uuid
        from jarvis_agent_v3 import run_agent_v3
        result = await run_agent_v3(
            task=req.task,
            max_steps=req.max_steps,
            allow_destructive=req.allow_destructive,
            history=req.history,
        )
        result["interaction_id"] = str(uuid.uuid4())
        return result
    except Exception as exc:
        log.exception("Agent endpoint failed")
        raise HTTPException(status_code=500, detail=f"Agent failed: {exc}")


# ── v3 Guardian ─────────────────────────────────────────────────────

@app.get("/v3/guardian/check")
def guardian_check(x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)
    if not _feature_flag("guardian"):
        raise HTTPException(status_code=503, detail="Guardian not enabled")

    guardian = get_guardian()
    alerts = guardian.check_all()
    return {"alerts": alerts, "count": len(alerts)}


# ── v3 Swarm Status ─────────────────────────────────────────────────

@app.get("/v3/swarm/status")
def swarm_status(x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)
    if not _feature_flag("nano_swarm"):
        raise HTTPException(status_code=503, detail="Nano swarm not enabled")

    # Return current swarm stats without starting it
    return {
        "workers": 50,
        "queue_size": 0,
        "status": "idle",
    }


# ── Bot-support endpoints (v2-compatible) ───────────────────────────

@app.post("/claude-plan")
def claude_plan(payload: dict = {}, x_api_key: str = Header(default=None)):
    """v2-compatible claude-plan endpoint."""
    require_api_key(x_api_key)
    query = payload.get("query", "")
    try:
        from claude_planner import plan_task
        response = plan_task(query)
        return {"response": response}
    except Exception as exc:
        log.warning("claude-plan error: %s", exc)
        return {"response": f"Planner error: {exc}"}


@app.post("/feedback")
def feedback_post(payload: dict = {}, x_api_key: str = Header(default=None)):
    """v2-compatible feedback endpoint."""
    require_api_key(x_api_key)
    # Store feedback for training pipeline
    feedback_file = JARVIS_HOME / "data" / "feedback.jsonl"
    feedback_file.parent.mkdir(parents=True, exist_ok=True)
    with open(feedback_file, "a") as f:
        f.write(json.dumps({"ts": time.time(), **payload}) + "\n")
    return {"ok": True}


@app.post("/learn")
def learn_post(x_api_key: str = Header(default=None)):
    """v2-compatible learn endpoint — triggers training."""
    require_api_key(x_api_key)
    try:
        subprocess.Popen(
            [sys.executable, str(JARVIS_HOME / "selftrain_v2.py")],
            cwd=str(JARVIS_HOME),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "message": "Training started in background."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/correct")
def correct_post(payload: dict = {}, x_api_key: str = Header(default=None)):
    """v2-compatible correction endpoint."""
    require_api_key(x_api_key)
    corrections_file = JARVIS_HOME / "data" / "corrections.jsonl"
    corrections_file.parent.mkdir(parents=True, exist_ok=True)
    with open(corrections_file, "a") as f:
        f.write(json.dumps({"ts": time.time(), **payload}) + "\n")
    return {"ok": True}


# ── Memory endpoints (v2-compatible) ────────────────────────────────

@app.get("/memory/query")
def memory_query(q: str, n: int = 8, x_api_key: str = Header(default=None)):
    require_api_key(x_api_key)
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(JARVIS_HOME / "memory"))
        col = client.get_or_create_collection("jarvis_memory")
        results = col.query(query_texts=[q], n_results=n, include=["documents", "metadatas", "distances"])
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        chunks = []
        for doc, meta, dist in zip(docs, metas, dists):
            chunks.append({"text": doc[:500], "source": meta.get("source", ""), "distance": dist})
        return {"query": q, "chunks": chunks}
    except Exception as exc:
        log.warning("Memory query failed: %s", exc)
        return {"query": q, "chunks": [], "error": str(exc)}


# ── Legacy v2 endpoint graft ────────────────────────────────────────
# The Telegram bot still depends on a set of v2 endpoints (/index, /stats,
# /sysinfo, /agent, /query, /approve, /deny, /learning-stats, /memory/save,
# /probe, /recall, /selfcheck, /skills, /voice/notify, …). The v3 cutover
# only reimplemented a subset, so those calls were 404ing. We graft the
# archived v2 app's routes onto this app for any path v3 doesn't already
# define — v3 routes keep precedence (they're registered first). Transitional
# until each endpoint is ported natively to v3.
def _graft_legacy_v2_routes() -> int:
    import importlib.util
    import sys as _sys
    v2_path = JARVIS_HOME / ".v2_archive" / "api_v2.py"
    if not v2_path.exists():
        log.warning("Legacy v2 archive not found at %s; bot endpoints may 404", v2_path)
        return 0
    spec = importlib.util.spec_from_file_location("jarvis_api_v2_legacy", v2_path)
    v2 = importlib.util.module_from_spec(spec)
    _sys.modules["jarvis_api_v2_legacy"] = v2
    spec.loader.exec_module(v2)
    existing = {(r.path, m) for r in app.routes
                for m in getattr(r, "methods", None) or []}
    grafted = 0
    for r in v2.app.routes:
        methods = getattr(r, "methods", None)
        if not methods:
            continue  # skip docs/openapi/static
        # Never override a v3 route; skip health (v3 owns it).
        if r.path == "/health" or all((r.path, m) in existing for m in methods):
            continue
        app.router.routes.append(r)
        grafted += 1
    return grafted


try:
    _n = _graft_legacy_v2_routes()
    log.info("Grafted %d legacy v2 routes onto v3 app", _n)
except Exception as _exc:  # noqa: BLE001
    log.error("Could not graft legacy v2 routes: %s", _exc)


# ── Main ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    log.info("Starting Jarvis v3 API on %s:%d", API_HOST, API_PORT)
    uvicorn.run(app, host=API_HOST, port=API_PORT)
