#!/usr/bin/env python3
"""
Jarvis FastAPI Server — REST API.
Endpoints: /health, /query, /stats, /sysinfo, /index (POST), /models, /persona, /docs
"""

import json
import os
import platform
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# OMP_NUM_THREADS must be set before any native lib (numpy/openmp via chromadb)
# initializes — single-threaded import is ~4x less likely to trip the
# intermittent chromadb C-extension import segfault on Python 3.13 / RTX 3050.
os.environ.setdefault("OMP_NUM_THREADS", "1")

# chromadb imported lazily inside get_collection() — a top-level import flaps
# the service on startup (segfaults ~25% of runs, see learner.py / indexer.py).
import hashlib
import requests
import ollama
import psutil
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis.yaml"
INTERACTIONS_LOG = JARVIS_HOME / "data" / "interactions.jsonl"

sys.path.insert(0, str(JARVIS_HOME))

from user_facts import facts_prompt_block  # noqa: E402
from profile import profile_prompt_block  # noqa: E402

app = FastAPI(
    title="Jarvis Personal AI API",
    description="Local AI assistant with RAG memory",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serialise Ollama calls — 6GB VRAM can't load two models simultaneously
_ollama_lock = threading.Semaphore(1)

# ── In-memory response cache (300 entries, 600s TTL) ─────────────────────────
_response_cache: dict[str, dict] = {}
_CACHE_MAX = 300
_CACHE_TTL = 600  # seconds

def _cache_key(query: str) -> str:
    return hashlib.sha256(query.lower().strip().encode()).hexdigest()[:16]

def _cache_get(query: str) -> str | None:
    key = _cache_key(query)
    entry = _response_cache.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        entry["hits"] = entry.get("hits", 0) + 1
        return entry["response"]
    return None

def _cache_put(query: str, response: str) -> None:
    if len(_response_cache) >= _CACHE_MAX:
        _response_cache.pop(next(iter(_response_cache)))
    _response_cache[_cache_key(query)] = {"response": response, "ts": time.time(), "hits": 0}

# ── In-memory embedding cache (2000 entries) ──────────────────────────────────
_embed_cache: dict[str, list] = {}
_EMBED_CACHE_MAX = 2000


def _ollama_chat(model: str, messages: list, options: dict, retries: int = 3) -> dict:
    """Thread-safe Ollama chat with retry on transient errors.

    deepseek-r1:7b on 6GB VRAM intermittently crashes llama-server on load
    ("error loading model vocabulary" / "llama-server process has terminated").
    These surface as ResponseError but recover on retry once the server respawns,
    so we retry ResponseError too — with a delay long enough for the respawn.
    """
    last_err = None
    for attempt in range(retries + 1):
        with _ollama_lock:
            try:
                return ollama.chat(model=model, messages=messages, options=options)
            except (ollama.ResponseError, Exception) as e:
                last_err = e
                if attempt < retries:
                    time.sleep(3)
    raise last_err


CASUAL_PATTERNS = {
    "hello", "hi", "hey", "sup", "yo", "thanks", "thank you",
    "ok", "okay", "cool", "nice", "great", "bye", "goodbye",
    "who are you", "what are you", "how are you",
}


def load_config():
    if not CONFIG_FILE.exists():
        return {
            "model": {"primary": "deepseek-r1:7b", "server": "http://localhost:11434"},
            "memory": {"path": str(JARVIS_HOME / "memory"), "embedding_model": "nomic-embed-text"},
            "owner": "Mike",
        }
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


CONFIG = load_config()


def _apply_ollama_host() -> None:
    """Use OLLAMA_HOST from env (Docker) or jarvis.yaml model.server."""
    if os.environ.get("OLLAMA_HOST"):
        return
    server = CONFIG.get("model", {}).get("server", "")
    if server:
        os.environ["OLLAMA_HOST"] = server.rstrip("/")


_apply_ollama_host()

MEMORY_PATH = CONFIG["memory"]["path"]
EMBED_MODEL = CONFIG["memory"].get("embedding_model", "nomic-embed-text")
PRIMARY_MODEL = CONFIG["model"]["primary"]
OWNER = CONFIG.get("owner", "Mike")

# Available installed models for routing
_MODEL_CFG = CONFIG.get("model", {})
MODEL_FAST  = "phi4-mini"          # simple/casual queries — 3x faster
MODEL_CODE  = "qwen2.5-coder:7b"   # code, debugging, programming
MODEL_BRAIN = PRIMARY_MODEL        # complex reasoning, analysis

# Keywords that signal a code-related query
_CODE_KEYWORDS = {
    "code", "function", "class", "bug", "error", "debug", "script",
    "python", "javascript", "typescript", "bash", "shell", "git",
    "npm", "pip", "deploy", "dockerfile", "sql", "regex", "api",
    "import", "module", "syntax", "compile", "refactor", "test",
    "algorithm", "loop", "async", "exception", "traceback", "stack",
    # Mike's projects
    "react", "node", "nodejs", "websocket", "pm2", "express",
    "asthacode", "asthacash", "starline", "payment", "gateway",
    "payment-gateway", "conztru", "pnpm", "monorepo", "postgresql",
}

# Keywords that need deep reasoning
_THINK_KEYWORDS = {
    "why", "explain", "analyze", "compare", "design", "architect",
    "strategy", "plan", "difference", "tradeoff", "should i", "best way",
    "optimize", "performance", "security", "review",
    # Mike's infrastructure context
    "vps", "server", "tailscale", "deploy", "deployment", "status",
    "project", "dashboard", "admin", "merchant", "agent",
}

_chroma_client = None
_collection = None


def get_collection():
    global _chroma_client, _collection
    if _collection is None:
        import chromadb  # lazy: top-level import flaps service startup (see header)
        Path(MEMORY_PATH).mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(
            path=MEMORY_PATH,
            settings=chromadb.Settings(anonymized_telemetry=False, allow_reset=True),
        )
        _collection = _chroma_client.get_or_create_collection(
            name="jarvis_memory",
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def get_embedding(text):
    key = text[:500]
    if key in _embed_cache:
        return _embed_cache[key]
    try:
        response = ollama.embeddings(model=EMBED_MODEL, prompt=text[:4096])
        emb = response["embedding"]
        if len(_embed_cache) >= _EMBED_CACHE_MAX:
            _embed_cache.pop(next(iter(_embed_cache)))
        _embed_cache[key] = emb
        return emb
    except Exception:
        return None


_NOT_CASUAL_WORDS = {
    "run", "status", "check", "show", "get", "list", "start", "stop",
    "restart", "deploy", "fix", "update", "install", "monitor", "log", "logs",
    "vps", "server", "disk", "cpu", "gpu", "ram", "memory", "network", "port",
    "ssh", "docker", "process", "service", "health", "stats", "sysinfo",
    "asthacash", "starline", "payment", "gateway", "project",
}


def is_casual(query: str) -> bool:
    q = query.lower().strip().rstrip("!?.❤️")
    words = set(q.split())
    # If any action/system word is present, it's NOT casual regardless of length
    if words & _NOT_CASUAL_WORDS:
        return False
    return q in CASUAL_PATTERNS or (len(q.split()) <= 3 and any(p in q for p in CASUAL_PATTERNS))


# Known Jarvis concepts — terms the LLM is expected to know about
_KNOWN_TERMS = {
    "jarvis", "ollama", "pm2", "nginx", "vps", "chromadb",
    "deepseek", "phi4", "qwen", "starline", "asthacash",
    "payment-gateway", "payment", "gateway", "telegram",
    "monitor", "executor", "healer", "syncer", "learner",
    "indexer", "docker", "ubuntu", "kali", "linux", "python",
    "fastapi", "react", "node", "nodejs", "postgresql", "pnpm",
    "tailscale", "github", "git", "openai", "anthropic", "claude",
    "conztru", "nvida", "nvidia", "cuda", "ollama",
}


def _contains_unknown_proper_noun(query: str) -> str | None:
    """Return the first unknown capitalised proper noun in a short query, or None.

    A term is considered unknown if:
    - The original query has ≤ 6 words
    - The token starts with an uppercase letter (or is ALL-CAPS) in the original query
    - It is not a known Jarvis concept (case-insensitive)
    - It is not the first word of the query (first word is capitalised by grammar)
    """
    words = query.split()
    if len(words) > 6:
        return None
    for i, word in enumerate(words):
        # Strip punctuation from ends
        import re as _re
        clean = _re.sub(r"[^A-Za-z0-9_\-]", "", word)
        if not clean:
            continue
        # Skip first word (capitalised by convention) unless ALL-CAPS and ≥ 3 chars
        if i == 0 and not (clean.isupper() and len(clean) >= 3):
            continue
        # Must start with uppercase or be all-caps
        if not (clean[0].isupper() or clean.isupper()):
            continue
        # Skip short words that are likely acronyms for common things (I, OK, etc.)
        if len(clean) <= 2:
            continue
        if clean.lower() not in _KNOWN_TERMS:
            return clean
    return None


def route_model(query: str, override: str | None = None) -> str:
    """Pick the best available model for the query type."""
    if override:
        return override
    q = query.lower()
    words = set(q.split())
    if words & _CODE_KEYWORDS or any(k in q for k in _CODE_KEYWORDS):
        return MODEL_CODE
    if words & _THINK_KEYWORDS or any(k in q for k in _THINK_KEYWORDS):
        return MODEL_BRAIN
    # Only use fast model for true greetings — NOT short unknown queries.
    # phi4-mini identifies itself as a Microsoft AI and ignores the Jarvis persona.
    if is_casual(query):
        return MODEL_FAST
    return MODEL_BRAIN


def strip_thinking(text: str) -> str:
    """Remove DeepSeek-R1 <think>...</think> reasoning blocks from output."""
    import re
    # Remove <think>...</think> blocks (can be multiline)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Remove any leftover <think> or </think> tags
    text = re.sub(r"</?think>", "", text)
    # Clean up leading/trailing whitespace and blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def log_interaction(interaction_id: str, query: str, response: str, model: str):
    import logging as _log
    try:
        INTERACTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "id": interaction_id,
            "timestamp": datetime.now().isoformat(),
            "query": query,
            "response": response,
            "model": model,
            "rating": None,
            "correction": None,
        }
        with open(INTERACTIONS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as exc:
        _log.getLogger(__name__).error("log_interaction failed: %s", exc)


def update_interaction(interaction_id: str, **fields):
    if not INTERACTIONS_LOG.exists():
        return False
    lines = INTERACTIONS_LOG.read_text().splitlines()
    updated = False
    new_lines = []
    for line in lines:
        try:
            entry = json.loads(line)
            if entry.get("id") == interaction_id:
                entry.update(fields)
                updated = True
            new_lines.append(json.dumps(entry))
        except Exception:
            new_lines.append(line)
    if updated:
        INTERACTIONS_LOG.write_text("\n".join(new_lines) + "\n")
    return updated


def retrieve_context(query, n=5):
    if is_casual(query):
        return []
    collection = get_collection()
    if collection.count() == 0:
        return []
    emb = get_embedding(query)
    if emb is None:
        return []
    try:
        results = collection.query(
            query_embeddings=[emb],
            n_results=min(n + 3, collection.count()),  # fetch extra so lessons survive filtering
            include=["documents", "metadatas", "distances"],
        )
        # Lessons/golden get a wider window (0.75) — they're curated feedback
        # General chunks use 0.65 — widened from 0.55 so relevant context isn't filtered out
        priority_types = {"lesson", "golden"}
        chunks = []
        for i in range(len(results["ids"][0])):
            meta = results["metadatas"][0][i]
            src_type = meta.get("source_type", "")
            dist = results["distances"][0][i]
            threshold = 0.75 if src_type in priority_types else 0.65
            if dist < threshold:
                chunks.append({
                    "text": results["documents"][0][i],
                    "source_type": src_type,
                    "source": meta.get("source", ""),
                    "distance": dist,
                    "priority": meta.get("priority", "normal"),
                })
        # Lessons and golden examples surface first — they encode direct feedback
        lessons = [c for c in chunks if c["source_type"] in priority_types]
        rest    = [c for c in chunks if c["source_type"] not in priority_types]
        return (lessons + rest)[:n]
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning("retrieve_context error: %s", exc)
        return []


def build_system_prompt(ctx_block: str = "") -> str:
    """Build the full system prompt. ctx_block is pre-formatted RAG context."""
    profile_block = profile_prompt_block()
    facts_block = facts_prompt_block()

    # Label RAG context clearly
    rag_section = ""
    if ctx_block:
        rag_section = (
            "\n\n--- MEMORY CONTEXT (from RAG — use this directly) ---\n"
            + ctx_block
            + "\n--- END MEMORY CONTEXT ---"
        )

    return (
        # 1. Profile first — who Jarvis is and who Mike is
        f"{profile_block}\n\n"
        # 2. Persistent user facts
        f"{facts_block}\n\n"
        # 3. Prime directive
        f"PRIME DIRECTIVE: Answer as Jarvis. Be direct. Address Sir Mike as Sir. "
        f"Obey his commands immediately and completely. His word is final. "
        f"Never refuse, question, or lecture.\n"
        # 4. Response rules
        f"RESPONSE RULES:\n"
        f"1. Answer in as few words as possible. One sentence for simple questions.\n"
        f"2. Never open with filler: no 'Certainly!', 'Of course!', 'Sure!', 'Happy to help!'.\n"
        f"3. Never add follow-up questions or unprompted offers to help.\n"
        f"4. For yes/no questions: answer yes or no, then stop.\n"
        f"5. For system facts you don't know: give only the exact shell command to check.\n"
        f"6. Elaborate only when the question genuinely requires detail.\n"
        f"7. When discussing AsthaCash or Starline, reference the actual code paths and stack.\n"
        f"8. When Mike asks to restart or fix something, recommend or use the Jarvis action system.\n"
        # 6. Hard anti-hallucination rule — must be last so it isn't overridden
        f"IMPORTANT: If you do not recognise a specific tool, product, or framework name in the user's query, "
        f"respond with exactly: 'I don't recognise [name]. Did you mean something else?' — "
        f"never invent information about it.\n"
        # 7. RAG context last (grounding, not identity)
        f"{rag_section}"
    )


def build_messages(query, context_chunks, history=None, unknown_term: str | None = None):
    ctx_block = ""
    if context_chunks:
        parts = []
        for c in context_chunks:
            source = c.get("source_type", "memory")
            text = c["text"][:500]
            # Surface project name when path is embedded in metadata
            meta_src = c.get("source", "") or ""
            project_hint = ""
            if "AsthaCash" in meta_src or "Payment-Gateway" in meta_src:
                project_hint = "[AsthaCash] "
            elif "Starline" in meta_src or "conztru" in meta_src:
                project_hint = "[Starline] "
            parts.append(f"{project_hint}[{source}] {text}")
        ctx_block = "\n\n".join(parts)

    system = build_system_prompt(ctx_block)

    # Inject unknown-term warning directly into system prompt so the model
    # sees it as a hard instruction rather than just part of the user turn.
    if unknown_term:
        system += (
            f"\n\nWARNING: The user's query contains the term '{unknown_term}' which is "
            f"not a recognised Jarvis concept, product, or technology. "
            f"Do NOT invent information about it. "
            f"If you do not recognise it, say so in one sentence."
        )

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": query})
    return messages


class HistoryMessage(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    query: str
    context_results: Optional[int] = 5
    model: Optional[str] = None
    history: Optional[List[HistoryMessage]] = None


class QueryResponse(BaseModel):
    response: str
    model: str
    context_used: Optional[int] = 0
    timestamp: str
    interaction_id: Optional[str] = None


class IndexRequest(BaseModel):
    force: Optional[bool] = False


def _prewarm_model() -> None:
    """Pre-load primary model and ChromaDB into RAM on startup."""
    time.sleep(5)
    try:
        requests.post(
            f"http://localhost:{os.environ.get('JARVIS_PORT', 11434)}/api/generate",
            json={"model": PRIMARY_MODEL, "prompt": "Hi", "stream": False,
                  "options": {"num_predict": 1}},
            timeout=30,
        )
    except Exception:
        pass
    try:
        col = get_collection()
        if col.count() > 0:
            col.query(query_embeddings=[[0.0] * 768], n_results=1, include=[])
    except Exception:
        pass

threading.Thread(target=_prewarm_model, daemon=True).start()


@app.get("/cache-stats")
def cache_stats():
    total_hits = sum(e.get("hits", 0) for e in _response_cache.values())
    oldest = min((e["ts"] for e in _response_cache.values()), default=0)
    return {"entries": len(_response_cache), "total_hits": total_hits,
            "oldest_ts": oldest, "embed_entries": len(_embed_cache)}


@app.post("/cache-clear")
def cache_clear():
    n = len(_response_cache)
    _response_cache.clear()
    return {"cleared": n}


@app.get("/health")
def health():
    try:
        models = ollama.list()
        model_names = [m.model if hasattr(m, "model") else m.get("name", "") for m in models.models]
        ollama_ok = PRIMARY_MODEL in model_names or any(
            PRIMARY_MODEL.split(":")[0] in m for m in model_names
        )
    except Exception:
        ollama_ok = False

    try:
        col = get_collection()
        memory_chunks = col.count()
        memory_ok = True
    except Exception:
        memory_chunks = 0
        memory_ok = False

    return {
        "status": "healthy" if ollama_ok and memory_ok else "degraded",
        "ollama": "ok" if ollama_ok else "error",
        "memory": "ok" if memory_ok else "error",
        "memory_chunks": memory_chunks,
        "model": PRIMARY_MODEL,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    model = route_model(req.query, req.model)
    context = retrieve_context(req.query, n=req.context_results)
    history = [{"role": m.role, "content": m.content} for m in req.history] if req.history else None
    unknown_term = _contains_unknown_proper_noun(req.query)
    messages = build_messages(req.query, context, history, unknown_term=unknown_term)

    # Tune temperature by query type — keep low to reduce hallucination
    temp = 0.3

    # Serve from RAM cache if recent identical query exists
    cached = _cache_get(req.query)
    if cached:
        return {"response": cached, "model": model, "cached": True,
                "context_used": len(context), "timestamp": datetime.now().isoformat()}

    opts = {"temperature": temp, "num_predict": 1024, "num_ctx": 8192, "num_keep": 256}
    try:
        response = _ollama_chat(model=model, messages=messages, options=opts)
        answer = strip_thinking(response["message"]["content"]).strip()
    except ollama.ResponseError:
        # Fallback to primary if routed model fails
        if model != PRIMARY_MODEL:
            try:
                response = _ollama_chat(model=PRIMARY_MODEL, messages=messages, options=opts)
                answer = strip_thinking(response["message"]["content"]).strip()
                model = PRIMARY_MODEL
            except Exception as e2:
                raise HTTPException(status_code=503, detail=f"Model unavailable: {e2}")
        else:
            raise HTTPException(status_code=503, detail="Model unavailable")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Model error: {e}")

    iid = str(uuid.uuid4())[:12]
    if not is_casual(req.query):
        log_interaction(iid, req.query, answer, model)
        _cache_put(req.query, answer)

    return QueryResponse(
        response=answer,
        model=model,
        context_used=len(context),
        timestamp=datetime.now().isoformat(),
        interaction_id=iid if not is_casual(req.query) else None,
    )


@app.get("/sysinfo")
def sysinfo():
    info = {}

    # CPU
    try:
        info["cpu"] = {
            "model": platform.processor() or "N/A",
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
            "usage_percent": psutil.cpu_percent(interval=0.5),
            "freq_mhz": round(psutil.cpu_freq().current) if psutil.cpu_freq() else "N/A",
        }
    except Exception as e:
        info["cpu"] = {"error": str(e)}

    # RAM
    try:
        ram = psutil.virtual_memory()
        info["ram"] = {
            "total_gb": round(ram.total / 1e9, 1),
            "used_gb": round(ram.used / 1e9, 1),
            "free_gb": round(ram.available / 1e9, 1),
            "usage_percent": ram.percent,
        }
    except Exception as e:
        info["ram"] = {"error": str(e)}

    # Swap
    try:
        swap = psutil.swap_memory()
        info["swap"] = {
            "total_gb": round(swap.total / 1e9, 1),
            "used_gb": round(swap.used / 1e9, 1),
            "usage_percent": swap.percent,
        }
    except Exception as e:
        info["swap"] = {"error": str(e)}

    # Disk
    try:
        disk = psutil.disk_usage("/")
        info["disk"] = {
            "total_gb": round(disk.total / 1e9, 1),
            "used_gb": round(disk.used / 1e9, 1),
            "free_gb": round(disk.free / 1e9, 1),
            "usage_percent": disk.percent,
        }
    except Exception as e:
        info["disk"] = {"error": str(e)}

    # GPU
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            info["gpu"] = {
                "model": parts[0],
                "vram_total_mb": int(parts[1]),
                "vram_used_mb": int(parts[2]),
                "vram_free_mb": int(parts[3]),
                "temp_c": int(parts[4]),
                "utilization_percent": int(parts[5]),
            }
    except Exception:
        info["gpu"] = {"status": "nvidia-smi not available"}

    # OS
    try:
        info["os"] = {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version().split("#")[0].strip(),
            "hostname": platform.node(),
            "uptime_hours": round((datetime.now().timestamp() - psutil.boot_time()) / 3600, 1),
        }
    except Exception as e:
        info["os"] = {"error": str(e)}

    # Network
    try:
        addrs = psutil.net_if_addrs()
        net = {}
        for iface, addr_list in addrs.items():
            if iface == "lo":
                continue
            for addr in addr_list:
                if addr.family.name == "AF_INET":
                    net[iface] = addr.address
        info["network"] = net
    except Exception as e:
        info["network"] = {"error": str(e)}

    # Python
    try:
        import sys as _sys
        result = subprocess.run(["python3", "--version"], capture_output=True, text=True, timeout=5)
        info["python"] = result.stdout.strip() or result.stderr.strip()
    except Exception:
        info["python"] = f"Python {platform.python_version()}"

    # Jarvis memory
    try:
        col = get_collection()
        info["jarvis"] = {
            "memory_chunks": col.count(),
            "model": PRIMARY_MODEL,
            "api_port": CONFIG.get("interfaces", {}).get("api_port", 8181),
        }
    except Exception:
        pass

    return info


@app.get("/stats")
def stats():
    try:
        col = get_collection()
        count = col.count()
        sample_meta = {}
        if count > 0:
            # Paginate through ALL chunks to get accurate type breakdown
            type_counts = {}
            batch_size = 1000
            offset = 0
            while offset < count:
                batch = col.get(
                    limit=min(batch_size, count - offset),
                    offset=offset,
                    include=["metadatas"],
                )
                for m in batch["metadatas"]:
                    t = m.get("source_type", "unknown")
                    type_counts[t] = type_counts.get(t, 0) + 1
                offset += batch_size
            sample_meta = type_counts
    except Exception as e:
        return {"error": str(e)}

    try:
        models_resp = ollama.list()
        available_models = [m.model if hasattr(m, "model") else m.get("name", "") for m in models_resp.models]
    except Exception:
        available_models = []

    return {
        "memory_chunks": count,
        "source_type_breakdown": sample_meta,
        "primary_model": PRIMARY_MODEL,
        "available_models": available_models,
        "embed_model": EMBED_MODEL,
        "jarvis_home": str(JARVIS_HOME),
    }


@app.post("/index")
def trigger_index():
    try:
        from indexer import load_config, run_indexing
        cfg = load_config()
        collection = run_indexing(cfg)
        return {"status": "ok", "message": "Indexing complete", "chunks": collection.count()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Indexing error: {e}")


@app.get("/models")
def list_models():
    try:
        resp = ollama.list()
        return {"models": [m.model if hasattr(m, "model") else m.get("name", "") for m in resp.models]}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/persona")
def get_persona():
    """Return the current system prompt template (no RAG context filled in).
    Lets Sir Mike verify exactly what Jarvis knows about him and how it presents itself."""
    from profile import load_profile, PROFILE_FILE as _PROFILE_FILE
    template = build_system_prompt(ctx_block="")
    profile_raw = load_profile()
    return {
        "system_prompt_template": template,
        "profile_sections": list(profile_raw.keys()),
        "profile_file": str(_PROFILE_FILE),
        "model_routing": {
            "primary": PRIMARY_MODEL,
            "fast": MODEL_FAST,
            "code": MODEL_CODE,
        },
    }


# ── Telegram send helpers ─────────────────────────────────────────────────────

def _get_telegram_token() -> str:
    token_file = JARVIS_HOME / "config" / "telegram.json"
    if not token_file.exists():
        return ""
    import json as _json
    data = _json.loads(token_file.read_text())
    t = data.get("bot_token", "")
    return t if t != "YOUR_BOT_TOKEN_HERE" else ""


def _get_chat_id(token: str) -> int | None:
    chat_id_file = JARVIS_HOME / "data" / "telegram_chat_id.json"
    if chat_id_file.exists():
        try:
            return json.loads(chat_id_file.read_text()).get("chat_id")
        except Exception:
            pass
    try:
        updates = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10).json()
        msgs = updates.get("result", [])
        if msgs:
            cid = msgs[-1]["message"]["chat"]["id"]
            chat_id_file.parent.mkdir(parents=True, exist_ok=True)
            chat_id_file.write_text(json.dumps({"chat_id": cid}))
            return cid
    except Exception:
        pass
    return None


def _to_legacy_markdown(text: str) -> str:
    # Telegram's legacy "Markdown" parse_mode uses single-`*` for bold; callers
    # write `**bold**` (MarkdownV2/GitHub style), which 400s. Collapse `**`/`__`
    # to their legacy single-char forms so formatting renders instead of being
    # lost to the plain-text fallback below.
    return text.replace("**", "*").replace("__", "_")


def _send_telegram_direct(text: str) -> bool:
    token = _get_telegram_token()
    if not token:
        return False
    chat_id = _get_chat_id(token)
    if not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        # Try normalized Markdown first so bold/code render. If Telegram still
        # 400s on some other malformed entity, retry as plain text so approval
        # prompts and alerts are NEVER silently dropped.
        r = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": _to_legacy_markdown(text),
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        if r.status_code == 200 and r.json().get("ok"):
            return True
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception:
        return False


class TelegramMessage(BaseModel):
    text: str


@app.post("/telegram/send")
def telegram_send(msg: TelegramMessage):
    ok = _send_telegram_direct(msg.text)
    return {"ok": ok}


@app.post("/telegram/alert")
def telegram_alert(msg: TelegramMessage):
    text = f"🚨 Jarvis Alert\n{msg.text}"
    ok = _send_telegram_direct(text)
    return {"ok": ok}


# ── Action execution endpoint ─────────────────────────────────────────────────

class ActionRequest(BaseModel):
    action: str
    arg: Optional[str] = ""
    skip_permission: Optional[bool] = False


@app.post("/action")
def execute_action(req: ActionRequest):
    from executor import ACTIONS, run_action, AUTO
    from permissions import create_request

    if req.action not in ACTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown action: {req.action}")

    entry = ACTIONS[req.action]
    tier = entry["tier"]

    if tier == AUTO or req.skip_permission:
        result = run_action(req.action, req.arg or "")
        return result

    # For confirm/approve: create a pending request and send Telegram message
    perm_req = create_request(
        action=req.action,
        description=entry["desc"],
        tier=tier,
        arg=req.arg or "",
    )
    msg = (
        f"⚡ Action request — ID: `{perm_req['id']}`\n"
        f"**{entry['desc']}**"
        + (f"\nArg: `{req.arg}`" if req.arg else "")
        + f"\n\nReply with:\n`/approve {perm_req['id']}` to run\n`/deny {perm_req['id']}` to cancel"
    )
    _send_telegram_direct(msg)
    return {"status": "pending", "request_id": perm_req["id"], "tier": tier}


@app.post("/claude-plan", response_model=QueryResponse)
def claude_plan_query(req: QueryRequest):
    """
    Claude-powered: Claude plans actions → OpenClaw executes → Claude synthesizes.
    Falls back to /query if Claude is unavailable or no actions are needed.
    """
    from executor import ACTIONS, run_action, AUTO
    from permissions import create_request

    try:
        from claude_planner import plan_actions, synthesize_results
    except ImportError:
        return query(req)

    plan = plan_actions(req.query, ACTIONS)

    if not plan.get("needs_actions") or not plan.get("actions"):
        # No system actions needed — use standard LLM query
        return query(req)

    results = []
    pending_descs = []

    for action_spec in plan["actions"]:
        action_name = action_spec.get("action", "")
        if not action_name or action_name not in ACTIONS:
            continue
        entry = ACTIONS[action_name]
        arg = action_spec.get("arg", "") or ""

        if entry["tier"] == AUTO:
            result = run_action(action_name, arg)
            results.append(result)
        else:
            # Non-auto actions go through normal approval flow
            perm_req = create_request(
                action=action_name,
                description=entry["desc"],
                tier=entry["tier"],
                arg=arg,
            )
            msg = (
                f"⚡ Claude planned action — ID: `{perm_req['id']}`\n"
                f"**{entry['desc']}**\n\n"
                f"Reply `/approve {perm_req['id']}` or `/deny {perm_req['id']}`"
            )
            _send_telegram_direct(msg)
            pending_descs.append(entry["desc"])

    if results:
        response = synthesize_results(req.query, results)
    else:
        response = ""

    if pending_descs:
        pending_note = "\n\n⏳ Awaiting approval: " + "; ".join(pending_descs)
        response = (response + pending_note).strip()

    if not response:
        return query(req)

    iid = str(uuid.uuid4())[:12]
    log_interaction(iid, req.query, response, "claude+openclaw")

    return QueryResponse(
        response=response,
        model="claude+openclaw",
        context_used=len(results),
        timestamp=datetime.now().isoformat(),
        interaction_id=iid,
    )


@app.post("/claude-exec")
def claude_exec(req: QueryRequest):
    """
    Full-loop execution: plan → run all actions → synthesize → voice notify.
    Designed for Claude Code sessions and autonomous decision engine use.
    """
    from executor import ACTIONS, run_action, detect_action, extract_action_arg, AUTO, CONFIRM, APPROVE
    from permissions import create_request

    try:
        from claude_planner import plan_actions, synthesize_results
    except ImportError:
        return query(req)

    # Fast path: direct action detection (no LLM planning overhead)
    direct = detect_action(req.query)
    if direct and ACTIONS.get(direct, {}).get("tier") == AUTO:
        result = run_action(direct, extract_action_arg(req.query, direct))
        summary = synthesize_results(req.query, [result])
        iid = str(uuid.uuid4())[:12]
        log_interaction(iid, req.query, summary, "claude-exec-direct")
        _send_voice_async(f"Action complete: {summary[:200]}")
        return QueryResponse(response=summary, model="claude-exec",
                             context_used=1, timestamp=datetime.now().isoformat(), interaction_id=iid)

    # Full LLM planning path
    plan = plan_actions(req.query, ACTIONS)
    results = []
    pending = []

    if plan.get("needs_actions") and plan.get("actions"):
        for spec in plan["actions"]:
            name = spec.get("action", "")
            if not name or name not in ACTIONS:
                continue
            entry = ACTIONS[name]
            arg = spec.get("arg", "") or ""
            if entry["tier"] == AUTO:
                results.append(run_action(name, arg))
            else:
                pr = create_request(action=name, description=entry["desc"], tier=entry["tier"], arg=arg)
                _send_telegram_direct(
                    f"⚡ /claude-exec planned — ID: `{pr['id']}`\n**{entry['desc']}**\n"
                    f"Arg: `{arg[:100]}`\nReply `/approve {pr['id']}` or `/deny {pr['id']}`"
                )
                pending.append(entry["desc"])

    response = synthesize_results(req.query, results) if results else ""

    if not results and not pending:
        return query(req)

    if pending:
        response = (response + "\n\n⏳ Awaiting approval: " + "; ".join(pending)).strip()

    iid = str(uuid.uuid4())[:12]
    log_interaction(iid, req.query, response, "claude-exec")

    if response:
        _send_voice_async(response[:300])

    return QueryResponse(response=response, model="claude-exec",
                         context_used=len(results), timestamp=datetime.now().isoformat(), interaction_id=iid)


def _send_voice_async(text: str):
    """Fire-and-forget voice notification."""
    import threading
    _port = CONFIG.get("interfaces", {}).get("api_port", 8181)
    def _speak():
        try:
            import requests as _req
            _req.post(f"http://localhost:{_port}/voice/notify",
                      json={"text": text}, timeout=30)
        except Exception:
            pass
    threading.Thread(target=_speak, daemon=True).start()


class FeedbackRequest(BaseModel):
    interaction_id: str
    rating: str  # "good" or "bad"


class CorrectionRequest(BaseModel):
    interaction_id: str
    correction: str


@app.post("/feedback")
def feedback(req: FeedbackRequest):
    if req.rating not in ("good", "bad"):
        raise HTTPException(status_code=400, detail="rating must be 'good' or 'bad'")
    ok = update_interaction(req.interaction_id, rating=req.rating)
    return {"ok": ok, "interaction_id": req.interaction_id, "rating": req.rating}


@app.post("/correct")
def correct(req: CorrectionRequest):
    ok = update_interaction(req.interaction_id, correction=req.correction, rating="bad")
    return {"ok": ok, "interaction_id": req.interaction_id}


@app.post("/learn")
def trigger_learn():
    try:
        import subprocess as _sp
        _sp.Popen(
            [str(JARVIS_HOME / "venv" / "bin" / "python3"), str(JARVIS_HOME / "learner.py")],
            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            start_new_session=True,
        )
        return {"status": "ok", "message": "Learner started in background"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/learning-stats")
def learning_stats():
    if not INTERACTIONS_LOG.exists():
        return {"total": 0, "good": 0, "bad": 0, "corrections": 0, "unrated": 0}
    entries = []
    for line in INTERACTIONS_LOG.read_text().splitlines():
        try:
            entries.append(json.loads(line.strip()))
        except Exception:
            pass
    return {
        "total": len(entries),
        "good": sum(1 for e in entries if e.get("rating") == "good"),
        "bad":  sum(1 for e in entries if e.get("rating") == "bad"),
        "corrections": sum(1 for e in entries if e.get("correction")),
        "unrated": sum(1 for e in entries if not e.get("rating")),
    }


@app.post("/approve/{req_id}")
def approve_action(req_id: str):
    from permissions import respond, get_request
    from executor import run_action
    req = get_request(req_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found or expired")
    respond(req_id, "approve")
    result = run_action(req["action"], req.get("arg", ""))
    status = "✅ Done" if result.get("success") else "❌ Failed"
    _send_telegram_direct(f"{status}: {req['description']}\n```\n{result.get('output','')[:500]}\n```")
    return result


@app.post("/deny/{req_id}")
def deny_action(req_id: str):
    from permissions import respond
    req_data = respond(req_id, "deny")
    if not req_data:
        raise HTTPException(status_code=404, detail="Request not found or expired")
    _send_telegram_direct(f"❌ Denied: {req_data.get('description', req_id)}")
    return {"status": "denied"}


# ── OpenClaw webhook ──────────────────────────────────────────────────────────

class WebhookPayload(BaseModel):
    type: str                        # "message", "action", "alert"
    text: Optional[str] = None
    action: Optional[str] = None
    arg: Optional[str] = ""
    source: Optional[str] = "openclaw"


@app.post("/webhook")
def openclaw_webhook(payload: WebhookPayload):
    """
    Endpoint for OpenClaw (VPS) to trigger Jarvis actions or send messages.
    Secure this with a firewall rule: only allow from Tailscale (100.x.x.x).
    """
    if payload.type == "alert" and payload.text:
        _send_telegram_direct(f"📡 OpenClaw: {payload.text}")
        return {"ok": True}

    if payload.type == "message" and payload.text:
        # Run through Jarvis AI
        from executor import detect_action, ACTIONS, run_action, AUTO
        action = detect_action(payload.text)
        if action and ACTIONS.get(action, {}).get("tier") == AUTO:
            result = run_action(action)
            _send_telegram_direct(f"📡 OpenClaw query result:\n```\n{result['output'][:500]}\n```")
            return result
        context = retrieve_context(payload.text)
        messages = build_messages(payload.text, context)
        try:
            resp = ollama.chat(model=PRIMARY_MODEL, messages=messages,
                               options={"temperature": 0.3, "num_predict": 1024, "num_ctx": 8192, "num_keep": 256})
            answer = resp["message"]["content"].strip()
            _send_telegram_direct(f"📡 OpenClaw: {payload.text}\n\n{answer}")
            return {"response": answer}
        except Exception as e:
            return {"error": str(e)}

    if payload.type == "action" and payload.action:
        return execute_action(ActionRequest(action=payload.action, arg=payload.arg or ""))

    raise HTTPException(status_code=400, detail="Unknown webhook payload type")


# ── Voice endpoints ───────────────────────────────────────────────────────────

class VoiceRequest(BaseModel):
    text: str
    play_local: Optional[bool] = False


@app.post("/voice/audio")
def voice_audio(req: VoiceRequest):
    """Return MP3 bytes for given text."""
    from fastapi.responses import Response
    try:
        from voice import get_audio_bytes
    except ImportError:
        raise HTTPException(status_code=503, detail="voice module unavailable")
    audio = get_audio_bytes(req.text)
    if not audio:
        raise HTTPException(status_code=503, detail="TTS generation failed")
    if req.play_local:
        try:
            from voice import speak_local
            speak_local(req.text)
        except Exception:
            pass
    return Response(content=audio, media_type="audio/mpeg")


@app.post("/voice/speak")
def voice_speak(req: VoiceRequest):
    """Play TTS on local speakers (non-blocking)."""
    try:
        from voice import speak_local
        speak_local(req.text)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/voice/notify")
def voice_notify(req: VoiceRequest):
    """Generate TTS and send as Telegram audio message."""
    try:
        from voice import send_voice_telegram
    except ImportError:
        raise HTTPException(status_code=503, detail="voice module unavailable")
    token = _get_telegram_token()
    if not token:
        raise HTTPException(status_code=503, detail="Telegram token not configured")
    chat_id = _get_chat_id(token)
    if not chat_id:
        raise HTTPException(status_code=503, detail="Telegram chat_id unknown")
    ok = send_voice_telegram(req.text, token, chat_id)
    return {"ok": ok}


@app.get("/selfcheck")
def selfcheck():
    """Run a comprehensive Jarvis self-check and return JSON health report."""
    import shutil
    report = {"ts": datetime.utcnow().isoformat(), "checks": {}, "overall": "ok"}
    issues = []

    # Services
    for svc in ("jarvis", "jarvis-telegram", "ollama"):
        r = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True)
        active = r.stdout.strip() == "active"
        report["checks"][f"service_{svc}"] = "ok" if active else "DOWN"
        if not active:
            issues.append(f"service {svc} is DOWN")

    # Ollama reachable
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        report["checks"]["ollama_api"] = "ok" if r.ok else f"http_{r.status_code}"
        if r.ok:
            models = [m.get("name", "") for m in r.json().get("models", [])]
            joined = " ".join(models).lower()
            for m in ("deepseek-r1", "mxbai", "phi4-mini"):
                report["checks"][f"model_{m}"] = "ok" if m in joined else "missing"
                if m not in joined:
                    issues.append(f"model {m} missing")
    except Exception as e:
        report["checks"]["ollama_api"] = f"error: {e}"
        issues.append("ollama unreachable")

    # Memory
    try:
        col = get_collection()
        count = col.count()
        report["checks"]["chromadb"] = "ok"
        report["checks"]["memory_chunks"] = count
        if count == 0:
            issues.append("ChromaDB empty")
    except Exception as e:
        report["checks"]["chromadb"] = f"error: {e}"
        issues.append("ChromaDB unavailable")

    # Disk
    usage = shutil.disk_usage("/")
    pct = round(usage.used / usage.total * 100, 1)
    report["checks"]["disk_pct"] = pct
    if pct > 90:
        issues.append(f"disk {pct}% full")

    # Log sizes
    logs_dir = JARVIS_HOME / "logs"
    for lf in logs_dir.glob("*.log"):
        size_mb = round(lf.stat().st_size / 1024 / 1024, 1)
        if size_mb > 10:
            issues.append(f"{lf.name} is {size_mb}MB (large)")

    # Cron
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    report["checks"]["cron_healer"] = "ok" if "healer.py" in r.stdout else "missing"
    report["checks"]["cron_monitor"] = "ok" if "monitor.py" in r.stdout else "missing"

    if issues:
        report["overall"] = "issues"
        report["issues"] = issues

    return report


if __name__ == "__main__":
    port = CONFIG.get("interfaces", {}).get("api_port", 8181)
    uvicorn.run("api:app", host="0.0.0.0", port=int(port), reload=False)
