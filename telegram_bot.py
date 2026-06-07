#!/usr/bin/env python3
"""
Jarvis Telegram Bot — mobile interface for Jarvis via Telegram.
Reads token from ~/.jarvis/config/telegram.json
Queries Jarvis API at localhost
"""

import asyncio
import base64
import json
import logging
import os
import re
import sys
import tempfile
from collections import deque
from pathlib import Path

import requests
import subprocess
import yaml

from user_facts import facts_prompt_block, load_facts, save_facts, remember_from_message

# JV Titan — Core Brain Protocol integration
sys.path.insert(0, str(JARVIS_HOME))
try:
    import jv_titan
    _HAS_TITAN = True
except Exception as _titan_exc:
    _HAS_TITAN = False

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis.yaml"
LOG_FILE = JARVIS_HOME / "logs" / "telegram_bot.log"
HISTORY_FILE = JARVIS_HOME / "data" / "telegram_history.json"
_SECRETS_FILE = JARVIS_HOME / "config" / "secrets.env"

# v3: token read from secrets.env (telegram.json deleted in Milestone 0)
def _load_telegram_token() -> str:
    """Read TELEGRAM_BOT_TOKEN from env or secrets.env."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if token:
        return token
    if _SECRETS_FILE.exists():
        for line in _SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    log.error("TELEGRAM_BOT_TOKEN not found in env or secrets.env")
    sys.exit(1)

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [telegram_bot] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

MAX_HISTORY = 30  # messages per user kept on disk
MAX_RESPONSE_CHARS = 8000  # hard cap — prevents duplicate spam from looping models

# Kali tool names — plain-text messages that match these are redirected to /kali
# rather than being dispatched through the NLP action detector (which would match
# substrings like "ps" inside "wpscan" and run the wrong action).
_KALI_TOOL_NAMES = {
    "nmap", "nmap_quick", "nmap_full", "nmap_service", "nmap_ping", "nmap_vuln",
    "nikto", "wpscan", "gobuster", "gobuster_dir", "ffuf", "sqlmap", "nuclei",
    "whatweb", "masscan", "enum4linux", "smb_list", "hydra", "hashcat", "john",
    "msf_resource", "metasploit", "searchsploit", "theharvester", "whois",
    "dig", "host", "nslookup", "dirb", "dirbuster", "burpsuite", "zap",
}

# Per-user conversation history — loaded from disk on startup
_histories: dict[int, deque] = {}


def _load_histories():
    if HISTORY_FILE.exists():
        try:
            raw = json.loads(HISTORY_FILE.read_text())
            for uid_str, msgs in raw.items():
                _histories[int(uid_str)] = deque(msgs, maxlen=MAX_HISTORY)
            log.info(f"Loaded history for {len(_histories)} user(s)")
        except Exception as e:
            log.warning(f"Could not load history: {e}")


def _save_histories():
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = {str(uid): list(dq) for uid, dq in _histories.items()}
        HISTORY_FILE.write_text(json.dumps(raw, indent=2))
    except Exception as e:
        log.warning(f"Could not save history: {e}")


_load_histories()


def load_token():
    """Back-compat: now reads from secrets.env via _load_telegram_token."""
    return _load_telegram_token()


def load_config():
    if not CONFIG_FILE.exists():
        return {"interfaces": {"api_port": 8181}, "owner": "Mike"}
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


CONFIG = load_config()
API_PORT = CONFIG.get("interfaces", {}).get("api_port", 8181)
VISION_MODEL = CONFIG.get("model", {}).get("vision", "llava:7b")
API_BASE = os.environ.get("JARVIS_API_URL", f"http://127.0.0.1:{API_PORT}")
OWNER = CONFIG.get("owner", "Mike")

_SECRETS_FILE = JARVIS_HOME / "config" / "secrets.env"


def _api_key() -> str:
    key = os.environ.get("JARVIS_API_KEY", "")
    if not key and _SECRETS_FILE.exists():
        for line in _SECRETS_FILE.read_text().splitlines():
            if line.startswith("JARVIS_API_KEY="):
                key = line.split("=", 1)[1].strip()
                break
    return key


def _ah() -> dict:
    """Auth headers for all authenticated Jarvis API calls."""
    k = _api_key()
    return {"X-API-Key": k} if k else {}


def get_history(uid: int) -> list:
    if uid not in _histories:
        _histories[uid] = deque(maxlen=MAX_HISTORY)
    return list(_histories[uid])


def add_to_history(uid: int, role: str, content: str):
    if uid not in _histories:
        _histories[uid] = deque(maxlen=MAX_HISTORY)
    _histories[uid].append({"role": role, "content": content})
    _save_histories()


SYSINFO_TRIGGERS = {
    "system info", "sysinfo", "system information", "systeminfo",
    "system stats", "system status", "machine info", "hardware info",
    "what's my system", "whats my system", "show system", "show stats",
    "full stats", "give me full stats", "system spec", "system specs",
    "python version", "python3 version", "check python", "what python",
    "current python", "python installed", "show python version",
    "stats", "jarvis stats", "openclaw stats", "brain stats", "memory stats",
    "claude stats", "all stats", "give stats", "show me stats",
}

# Any query that is just "<word> stats" or "stats <word>" routes to stats card
_STATS_RE = re.compile(r'\bstats?\b', re.IGNORECASE)


def is_sysinfo_request(text: str) -> bool:
    t = text.lower().strip().rstrip("?.!")
    if t in SYSINFO_TRIGGERS or any(t.startswith(p) for p in SYSINFO_TRIGGERS):
        return True
    # Catch any short query that contains the word "stats" (≤ 5 words)
    if _STATS_RE.search(t) and len(t.split()) <= 5:
        return True
    return False


def get_python_version() -> str:
    try:
        r = subprocess.run(["python3", "--version"], capture_output=True, text=True, timeout=5)
        return (r.stdout or r.stderr).strip()
    except Exception:
        return "unknown"


def answer_preference_question(text: str) -> str | None:
    """Answer from saved facts without calling the LLM."""
    t = text.lower().strip().rstrip("?.!")
    facts = load_facts()
    if not facts:
        return None
    if any(
        p in t
        for p in (
            "favourite language",
            "favorite language",
            "fav language",
            "fav program",
            "favorite program",
            "favourite program",
            "language i like",
            "language i prefer",
            "chosen language",
            "choosen language",
        )
    ):
        for key in ("programing_language", "programming_language", "language", "preference"):
            if key in facts:
                return facts[key]
    return None


_CASUAL_WORDS = {
    "hello", "hi", "hey", "sup", "yo", "thanks", "thank you", "ok", "okay",
    "cool", "nice", "great", "bye", "goodbye", "lol", "haha", "yes", "no",
    "sure", "fine", "good", "bad", "wow",
}

_ACTION_SIGNALS = {
    "check", "show", "get", "run", "list", "status", "restart", "stop",
    "start", "deploy", "install", "fix", "update", "monitor", "log", "logs",
    "what's", "whats", "how much", "how many", "is the", "are the", "ssh",
    "vps", "server", "docker", "container", "service", "process", "memory",
    "disk", "cpu", "gpu", "network", "port", "kill", "reboot",
}


def _is_casual(text: str) -> bool:
    words = set(text.lower().split())
    return (
        words & _CASUAL_WORDS and not words & _ACTION_SIGNALS
        or len(text.split()) <= 4 and not words & _ACTION_SIGNALS
    )


_last_interaction: dict[int, str] = {}  # uid → interaction_id
_voice_mode: dict[int, bool] = {}       # uid → voice on/off
_pending_action: dict[int, str] = {}    # uid → action name awaiting ForceReply arg

# Actions that need a parameter argument before executing
_NEEDS_ARG = {
    "shell", "ssh_cmd", "file_read", "file_list", "file_write",
    "kali_dig", "kali_host", "kali_nslookup", "kali_whois",
    "kali_nmap_ping", "kali_nmap_quick", "kali_nmap_full",
    "kali_nmap_service", "kali_nmap_vuln", "kali_masscan",
    "kali_enum4linux", "kali_smb_list", "kali_whatweb",
    "kali_nikto", "kali_gobuster_dir", "kali_ffuf",
    "kali_wpscan", "kali_nuclei", "kali_sqlmap",
    "kali_hydra", "kali_john", "kali_hashcat",
    "kali_theharvester", "kali_searchsploit", "kali_msf_resource",
}

VOICE_PREFS_FILE = JARVIS_HOME / "data" / "voice_prefs.json"


def _load_voice_prefs():
    try:
        if VOICE_PREFS_FILE.exists():
            prefs = json.loads(VOICE_PREFS_FILE.read_text())
            for uid_str, val in prefs.items():
                _voice_mode[int(uid_str)] = bool(val)
    except Exception:
        pass


def _save_voice_prefs():
    try:
        VOICE_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        VOICE_PREFS_FILE.write_text(json.dumps({str(k): v for k, v in _voice_mode.items()}))
    except Exception:
        pass


_load_voice_prefs()


def _is_voice_on(uid: int) -> bool:
    return _voice_mode.get(uid, False)


def _to_html(text: str) -> str:
    """Convert Markdown-flavoured text to Telegram HTML."""
    import sys as _sys
    _sys.path.insert(0, str(JARVIS_HOME))
    from fmt import strip_markdown
    return strip_markdown(text)


def _to_legacy_markdown(text: str) -> str:
    return text.replace("**", "*").replace("__", "_")


def _send_voice_for_response(text: str) -> bool:
    """Call Jarvis API to send TTS audio to Telegram."""
    try:
        r = requests.post(f"{API_BASE}/voice/notify", json={"text": text}, timeout=45)
        return r.ok
    except Exception:
        return False


def query_jarvis(uid: int, text: str) -> tuple[str, str | None]:
    # Route natural language sysinfo requests to the real endpoint
    if is_sysinfo_request(text):
        return get_sysinfo(), None

    remembered = remember_from_message(text)
    if remembered:
        _key, val = remembered
        add_to_history(uid, "user", text)
        add_to_history(uid, "assistant", val)
        return val, None

    pref = answer_preference_question(text)
    if pref:
        add_to_history(uid, "user", text)
        add_to_history(uid, "assistant", pref)
        return pref, None

    t = text.lower()
    if "python version" in t or "python3 version" in t or (
        "python" in t and "version" in t
    ):
        ver = get_python_version()
        add_to_history(uid, "user", text)
        add_to_history(uid, "assistant", ver)
        return ver, None

    history = get_history(uid)
    # v3: route ALL chat through the autonomous /agent endpoint.
    # The v3 agent handles intent classification, tool selection, scope/trust
    # gating, and synthesis — replacing the old /query + /claude-plan split.
    payload = {
        "task": text,
        "max_steps": 6,
        "allow_destructive": False,
        "history": history,
    }

    try:
        resp = requests.post(f"{API_BASE}/agent", json=payload, headers=_ah(), timeout=180)
        # Retry once on 503 — model may still be loading.
        if resp.status_code == 503:
            try:
                resp.json()
            except Exception:
                import time
                time.sleep(8)
                resp = requests.post(f"{API_BASE}/agent", json=payload, headers=_ah(), timeout=180)
        resp.raise_for_status()
        data = resp.json()
        answer = data.get("answer", "No response received.")
        # If the agent paused for approval, surface the message it prepared.
        if data.get("needs_approval") and not answer:
            answer = (
                f"Sir, to finish this I need your approval to run *{data['needs_approval']}*. "
                "Use /do if you want me to handle it, or /pending to see open requests."
            )
        # Hard cap: prevent looping model responses from spamming Telegram.
        if len(answer) > MAX_RESPONSE_CHARS:
            log.warning(
                "query_jarvis: response truncated from %d to %d chars (looping model?)",
                len(answer), MAX_RESPONSE_CHARS,
            )
            answer = answer[:MAX_RESPONSE_CHARS] + "\n\n… [truncated — response too long]"
        iid = data.get("interaction_id")
        add_to_history(uid, "user", text)
        add_to_history(uid, "assistant", answer)
        return answer, iid
    except requests.exceptions.ConnectionError:
        return "⚠️ Sorry Sir, Jarvis API is not running. Ask me to restart it if needed.", None
    except requests.exceptions.Timeout:
        return "⏳ Sorry Sir, Jarvis took too long to respond. The model may be busy — please try again.", None
    except Exception as e:
        log.warning("query_jarvis error: %s", e)
        return "⚠️ Sorry Sir, something went wrong processing your request. Please try again.", None


def _bar(pct: float, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _check_icon(key: str, val) -> str:
    """Return ✅/⚠️/❌ for a selfcheck key-value pair."""
    sv = str(val)
    if sv in ("ok", "True", "true", "healthy"):
        return "✅"
    if sv in ("error", "False", "false", "down", "fail"):
        return "❌"
    # Numeric values are informational — use thresholds for known keys
    try:
        n = float(sv)
        if "disk" in key:
            return "✅" if n < 85 else ("⚠️" if n < 95 else "❌")
        if "chunk" in key or "memory" in key:
            return "✅" if n > 0 else "⚠️"
        return "✅"  # any other positive number is fine
    except ValueError:
        return "⚠️"


_CHECK_LABELS = {
    "service_jarvis":          "🤖 Jarvis API",
    "service_jarvis-telegram": "📱 Telegram Bot",
    "service_ollama":          "🧠 Ollama",
    "ollama_api":              "🔌 Ollama API",
    "model_deepseek-r1":       "🤖 DeepSeek-R1",
    "model_mxbai":             "📐 Embed Model",
    "model_phi4-mini":         "⚡ Phi4-Mini",
    "chromadb":                "🗄️ ChromaDB",
    "memory_chunks":           "💾 Memory",
    "disk_pct":                "💽 Disk",
}


def get_stats() -> str:
    """Jarvis brain stats card (HTML)."""
    try:
        from fmt import bold, code, italic, progress_bar, esc
        resp = requests.get(f"{API_BASE}/stats", headers=_ah(), timeout=10)
        resp.raise_for_status()
        d = resp.json()
        chunks    = d.get("total_chunks", d.get("memory_chunks", 0))
        model     = d.get("primary_model", d.get("model", "deepseek-r1:7b"))
        uptime    = d.get("uptime_hours", "")
        breakdown = d.get("source_type_breakdown", {})
        top       = sorted(breakdown.items(), key=lambda x: -x[1])

        lines = [
            f"🧠 {bold('Jarvis Brain Stats')}\n",
            f"  Model   {code(model)}",
            f"  Memory  {code(f'{chunks:,} chunks')}",
        ]
        if uptime:
            lines.append(f"  Uptime  {code(f'{uptime}h')}")
        if top:
            src_parts = "  ".join(f"{code(k)} {v:,}" for k, v in top)
            lines.append(f"\n{bold('Memory Sources')}\n  {src_parts}")
        try:
            lr = requests.get(f"{API_BASE}/learning-stats", headers=_ah(), timeout=5)
            lr.raise_for_status()
            ld = lr.json()
            lines.append(
                f"\n📚 {bold('Learning')}\n"
                f"  Interactions  {code(str(ld.get('total_interactions', 0)))}\n"
                f"  Lessons       {code(str(ld.get('total_lessons', 0)))}\n"
                f"  Golden        {code(str(ld.get('golden_examples', 0)))}"
            )
        except Exception:
            pass
        return "\n".join(lines)
    except requests.exceptions.ConnectionError:
        return "⚠️ Sorry Sir, Jarvis API is not reachable right now."
    except Exception as e:
        log.warning("get_stats error: %s", e)
        return "⚠️ Sorry Sir, stats are temporarily unavailable."


def get_health() -> str:
    """Jarvis health card (HTML)."""
    try:
        from fmt import bold, code, esc
        resp = requests.get(f"{API_BASE}/health", timeout=10)
        resp.raise_for_status()
        d = resp.json()
        ok   = d.get("status") in ("healthy", "ok")
        icon = "✅" if ok else "⚠️"
        ollama = d.get("ollama", "?")
        chroma = d.get("chromadb", d.get("memory", "?"))
        chunks = d.get("memory_chunks", "")
        mem_str = f"  {code(f'{chunks:,} chunks')}" if chunks else ""
        uptime_str = ""
        try:
            si = requests.get(f"{API_BASE}/sysinfo", headers=_ah(), timeout=5).json()
            uh = si.get("uptime_hours")
            if uh is not None:
                uptime_str = f"\n⏱ Uptime: {code(f'{uh}h')}"
        except Exception:
            pass
        return (
            f"{icon} {bold('Jarvis')} — {code(d.get('status', 'unknown'))}\n\n"
            f"{'✅' if ollama=='ok' else '❌'} Ollama: {code(ollama)}\n"
            f"{'✅' if chroma=='ok' else '❌'} ChromaDB: {code(chroma)}{mem_str}"
            f"{uptime_str}"
        )
    except requests.exceptions.ConnectionError:
        return "❌ Sorry Sir, Jarvis API is not reachable right now."
    except Exception as e:
        log.warning("get_health error: %s", e)
        return "⚠️ Sorry Sir, health check is temporarily unavailable."


def get_sysinfo() -> str:
    """Full system stats card (HTML)."""
    try:
        from fmt import bold, code, italic, progress_bar, esc
        si = requests.get(f"{API_BASE}/sysinfo", headers=_ah(), timeout=15).json()
        st = requests.get(f"{API_BASE}/stats",   headers=_ah(), timeout=10).json()
        hl = requests.get(f"{API_BASE}/health",                 timeout=10).json()

        svc_ok   = hl.get("status") in ("ok", "healthy")
        svc_icon = "✅" if svc_ok else "⚠️"

        chunks    = st.get("total_chunks", st.get("memory_chunks", 0))
        model     = st.get("primary_model", "deepseek-r1:7b")
        breakdown = st.get("source_type_breakdown", {})
        top2      = sorted(breakdown.items(), key=lambda x: -x[1])[:3]
        src       = "  ".join(f"{code(k)} {v:,}" for k, v in top2)

        def _safe_float(v, default: float = 0.0) -> float:
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        cpu_pct  = si.get("cpu_percent", 0)
        cores    = si.get("cpu_cores", "?")
        threads  = si.get("cpu_threads", "?")
        ram_used = si.get("ram_used_gb", "?")
        ram_tot  = si.get("ram_total_gb", "?")
        ram_pct  = si.get("ram_percent", 0)
        d_used   = si.get("disk_used_gb", "?")
        d_tot    = si.get("disk_total_gb", "?")
        d_pct    = si.get("disk_percent", 0)
        gpu_util = si.get("gpu_util_percent")
        gpu_used = si.get("gpu_mem_used_mb")
        gpu_tot  = si.get("gpu_mem_total_mb")
        gpu_temp = si.get("gpu_temp_c")
        uptime   = si.get("uptime_hours", "?")
        hostname = si.get("hostname", "kali")

        lines = [
            f"{svc_icon} {bold('Jarvis System Status')}",
            "",
            f"🧠 {bold('Brain')}",
            f"  Model   {code(model)}",
            f"  Memory  {code(f'{chunks:,} chunks')}",
            (f"  Sources {src}") if src else "",
            "",
            f"🖥 {bold('Hardware')}",
            f"  CPU   {code(f'{cpu_pct}%')} {progress_bar(_safe_float(cpu_pct))}  {code(f'{cores}c/{threads}t')}",
            f"  RAM   {code(f'{ram_used}/{ram_tot} GB')} {progress_bar(_safe_float(ram_pct))}",
            f"  Disk  {code(f'{d_used}/{d_tot} GB')} {progress_bar(_safe_float(d_pct))}",
        ]
        if gpu_util is not None:
            vram_pct = round(_safe_float(gpu_used) / max(_safe_float(gpu_tot), 1) * 100) if gpu_used and gpu_tot else 0
            lines.append(
                f"  GPU   {code(f'{gpu_util}%')} {progress_bar(_safe_float(gpu_util))}"
            )
            lines.append(
                f"  VRAM  {code(f'{gpu_used}/{gpu_tot} MB')} {progress_bar(vram_pct)}"
                + (f"  🌡 {code(f'{gpu_temp}°C')}" if gpu_temp is not None else "")
            )

        lines += ["", italic(f"⏱ Uptime {uptime}h — {esc(str(hostname))}")]
        return "\n".join(line for line in lines if line is not None)
    except requests.exceptions.ConnectionError:
        return "⚠️ Sorry Sir, Jarvis API is not reachable right now."
    except Exception as e:
        log.warning("get_sysinfo error: %s", e)
        return "⚠️ Sorry Sir, system info is temporarily unavailable."


def trigger_index() -> str:
    try:
        resp = requests.post(f"{API_BASE}/index", json={}, headers=_ah(), timeout=300)
        resp.raise_for_status()
        d = resp.json()
        chunks = d.get("chunks", "N/A")
        return f"✅ Indexing complete — {chunks} chunks in memory."
    except requests.exceptions.ConnectionError:
        return "⚠️ Sorry Sir, Jarvis API is not reachable right now."
    except Exception as e:
        log.warning("trigger_index error: %s", e)
        return "⚠️ Sorry Sir, indexing encountered an error. Check logs for details."


HELP_TEXT = (
    "🤖 <b>Jarvis — Personal AI Assistant</b>\n"
    "<i>Welcome, Sir. Here's everything I can do:</i>\n\n"
    "━━━ <b>📊 Status &amp; Health</b> ━━━\n"
    "/stats — Brain &amp; memory stats\n"
    "/sysinfo — Live CPU / RAM / GPU / Disk\n"
    "/health — Service health check\n"
    "/selfcheck — Full system self-check\n\n"
    "━━━ <b>🔍 Web &amp; Research</b> ━━━\n"
    "/web <i>query</i> — Real-time web search + AI answer\n"
    "/search <i>query</i> — Alias for /web\n"
    "/weather <i>[city]</i> — Current weather\n"
    "/browse <i>url</i> — Open URL in headless browser\n\n"
    "━━━ <b>⚡ Actions &amp; Tasks</b> ━━━\n"
    "/do <i>task</i> — 🤖 Autonomous agent: plans, runs &amp; reports back\n"
    "/think <i>question</i> — Deep reasoning + confidence score\n"
    "/briefing — Daily intelligence briefing\n"
    "/exec <i>cmd</i> — Smart dispatch (plan + execute)\n"
    "/task <i>desc</i> — Delegate to Claude Code (async)\n"
    "/actions — List available actions by tier\n"
    "/pending — Show pending approvals\n"
    "/approve <i>ID</i> — Approve a pending action\n"
    "/deny <i>ID</i> — Deny a pending action\n\n"
    "━━━ <b>🛡 Security (Kali)</b> ━━━\n"
    "/kali — List all pentest tools (L1–L4)\n"
    "/kali <i>tool target</i> — Run a pentest tool\n"
    "/scans — List recent scan reports\n\n"
    "━━━ <b>🌐 OpenClaw</b> ━━━\n"
    "/oc — OpenClaw gateway status\n"
    "/oc <i>tool [args]</i> — Invoke an OpenClaw tool\n\n"
    "━━━ <b>🧪 Self-Test &amp; Diagnostics</b> ━━━\n"
    "/probe <i>[question]</i> — Test local AI (timing + tokens)\n"
    "/skills — Actions count, memory chunks, loaded models\n"
    "/recall <i>[topic]</i> — What Jarvis remembers about you\n"
    "/objectives — Your profile, projects &amp; Jarvis goals\n"
    "/benchmark <i>[q]</i> — All 3 models side-by-side\n\n"
    "━━━ <b>🧠 Learning &amp; Memory</b> ━━━\n"
    "/correct <i>text</i> — Correct last answer (trains brain)\n"
    "/learn — Run brain training now\n"
    "/index — Re-index your work\n\n"
    "━━━ <b>⚙️ Settings</b> ━━━\n"
    "/remember <i>key value</i> — Save a personal fact\n"
    "/voice on|off — Toggle voice audio responses\n"
    "/new — Fresh session (clear history + state)\n"
    "/clear — Clear chat history only\n"
    "/help — Show this message\n\n"
    "<i>💡 Tip: Tap 👍 or 👎 after any reply to train Jarvis.</i>\n"
    "<i>Drop files into ~/.jarvis/inbox/ to auto-train from them.</i>"
)


def split_message(text: str, limit: int = 4000) -> list[str]:
    import sys as _sys
    _sys.path.insert(0, str(JARVIS_HOME))
    from fmt import split_smart
    return split_smart(text, limit=limit)


def _trigger_training_if_due():
    """Run indexer in background if last training was >4h ago."""
    stamp = JARVIS_HOME / "data" / "last_training.txt"
    import time
    now = time.time()
    if stamp.exists():
        try:
            last = float(stamp.read_text().strip())
            if now - last < 4 * 3600:
                return
        except Exception:
            pass
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(str(now))
    train_script = JARVIS_HOME / "train.sh"
    if train_script.exists():
        subprocess.Popen(
            ["bash", str(train_script)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log.info("Training triggered in background.")


_IMAGE_SYSTEM = (
    "You are Jarvis, a personal AI assistant for Mike (sole user, Kali Linux). "
    "You are analyzing a screenshot or image from Mike's personal machine. "
    "Mike's active projects:\n"
    "  - AsthaCash: payment gateway — React admin dashboard (gateway-admin) + Node.js WebSocket backend (gateway-backend). "
    "    Pages: adminDashboard, agentDashboard, merchantDashboard, transaction, account.\n"
    "  - Starline-Final-web: real estate website — pnpm monorepo, React frontend (conztru), Express API (api-server), PostgreSQL.\n"
    "  - Payment-Gateway: parent repo for AsthaCash.\n"
    "When you see a UI screenshot, identify: which app/page it is (check against above), "
    "exact text/numbers visible, any errors or warnings, UI component names, and what action Mike might want. "
    "Never guess 'crypto wallet' or 'Ethereum' unless you clearly see ETH/blockchain UI elements. "
    "Be precise — read visible text literally."
)


def analyze_image_sync(img_bytes: bytes, prompt: str) -> str:
    """Run llava:7b on image bytes. Runs in a thread pool — do not call from async."""
    import ollama as _ollama
    resp = _ollama.generate(
        model=VISION_MODEL,
        prompt=prompt,
        system=_IMAGE_SYSTEM,
        images=[img_bytes],
    )
    return resp.response


_SVC_GROUPS = {
    "Jarvis":      {"jarvis", "jarvis-telegram", "jarvis-sync", "ollama"},
    "Web":         {"nginx", "apache2"},
    "Containers":  {"docker", "containerd"},
    "Display":     {"lightdm", "gdm", "xorg"},
}


def _fmt_services(raw: str) -> str:
    names = []
    for line in raw.splitlines():
        parts = line.split()
        if parts and parts[0].endswith(".service") and "running" in line:
            names.append(parts[0].replace(".service", ""))
    if not names:
        return raw

    grouped: dict[str, list[str]] = {g: [] for g in _SVC_GROUPS}
    grouped["System"] = []
    for n in sorted(names):
        safe = n.replace("_", "\\_")
        placed = False
        for grp, members in _SVC_GROUPS.items():
            if n in members:
                grouped[grp].append(safe)
                placed = True
                break
        if not placed:
            grouped["System"].append(safe)

    lines = [f"*Running Services* ({len(names)})"]
    for grp, svcs in grouped.items():
        if svcs:
            lines.append(f"\n*{grp}*")
            lines.append("  " + "  ·  ".join(svcs))
    return "\n".join(lines)


def _fmt_gpu(raw: str) -> str:
    temp = util = vram_used = vram_total = power = fan = None
    for line in raw.splitlines():
        # Stats row: | Fan  Temp  Perf  Pwr:Usage/Cap | Memory-Usage | GPU-Util |
        m = re.search(r'(\d+)%\s+(\d+)C.*?(\d+)W\s*/\s*(\d+)W.*?(\d+)MiB\s*/\s*(\d+)MiB.*?(\d+)%', line)
        if m:
            fan, temp, power, max_power = m.group(1), m.group(2), m.group(3), m.group(4)
            vram_used, vram_total, util = m.group(5), m.group(6), m.group(7)
            break
    if temp:
        vram_pct = round(int(vram_used) / max(int(vram_total), 1) * 100)
        return (
            f"*GPU — RTX 3050*\n"
            f"  Util  `{util}%` {_bar(int(util))}\n"
            f"  VRAM  `{vram_used}/{vram_total} MB` {_bar(vram_pct)}\n"
            f"  Temp  `{temp}°C`  Power `{power}/{max_power}W`  Fan `{fan}%`"
        )
    return raw


def _fmt_network(raw: str) -> str:
    lines, ifaces = raw.splitlines(), []
    cur = None
    for line in lines:
        m = re.match(r'^\d+: (\S+):', line)
        if m:
            cur = {"name": m.group(1).rstrip("@:"), "ips": [], "state": "UP" if "UP" in line else "DOWN"}
        elif cur and "inet " in line:
            ip = re.search(r'inet (\S+)', line)
            if ip:
                cur["ips"].append(ip.group(1).split("/")[0])
                ifaces.append(cur)
                cur = None
    if not ifaces:
        return raw
    lines_out = ["*Network*"]
    for i in ifaces:
        icon = "✅" if i["state"] == "UP" else "❌"
        lines_out.append(f"  {icon} `{i['name']}` — " + "  ".join(f"`{ip}`" for ip in i["ips"]))
    return "\n".join(lines_out)


def _fmt_ps_top(raw: str, title: str) -> str:
    rows = []
    for line in raw.strip().splitlines():
        parts = line.split(None, 10)
        if len(parts) >= 11:
            cpu, mem, cmd = parts[2], parts[3], parts[10][:35]
            rows.append(f"`{cpu:>5}%cpu` `{mem:>4}%mem`  {cmd}")
    if not rows:
        return raw
    return f"*{title}*\n" + "\n".join(rows[:5])


def _fmt_disk(raw: str) -> str:
    lines_out = ["*Disk Usage*"]
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        fs, size, used, avail, pct, mount = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        # skip tmpfs/virtual mounts
        if fs in ("tmpfs", "devtmpfs", "udev") or mount.startswith("/run") or mount.startswith("/dev/shm"):
            continue
        try:
            pct_n = int(pct.rstrip("%"))
            bar = _bar(pct_n, 8)
        except Exception:
            bar = ""
        lines_out.append(f"  `{mount}` {bar} `{used}/{size}` ({pct})")
    return "\n".join(lines_out) if len(lines_out) > 1 else raw


def _fmt_memory(raw: str) -> str:
    for line in raw.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            try:
                total, used, free = parts[1], parts[2], parts[3]
                pct = round(float(used.rstrip("Gi").rstrip("Mi")) / float(total.rstrip("Gi").rstrip("Mi")) * 100)
                return (f"*RAM*\n"
                        f"  Used:  `{used} / {total}` {_bar(pct)} `{pct}%`\n"
                        f"  Free:  `{free}`")
            except Exception:
                pass
    return raw


def _fmt_ollama_models(raw: str) -> str:
    lines_out = ["*AI Models*"]
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            name, size = parts[0], parts[2] + " " + parts[3] if len(parts) > 3 else parts[2]
            lines_out.append(f"  `{name}` — {size}")
    return "\n".join(lines_out) if len(lines_out) > 1 else raw


def _fmt_tailscale(raw: str) -> str:
    lines_out = ["*Tailscale*"]
    for line in raw.strip().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2 and re.match(r'^\d+\.\d+', parts[0]):
            ip, host = parts[0], parts[1]
            status = "❌" if "offline" in line else "✅"
            lines_out.append(f"  {status} `{ip}` — {host}")
    return "\n".join(lines_out) if len(lines_out) > 1 else raw


def _format_action_output(action: str, output: str) -> str:
    if action == "services":
        return _fmt_services(output)
    if action == "gpu":
        return f"*GPU*\n```\n{_fmt_gpu(output)}\n```"
    if action == "network":
        return _fmt_network(output)
    if action == "top5_cpu":
        return _fmt_ps_top(output, "Top CPU Processes")
    if action == "top5_mem":
        return _fmt_ps_top(output, "Top Memory Processes")
    if action == "disk":
        return _fmt_disk(output)
    if action == "memory":
        return _fmt_memory(output)
    if action == "ollama_models":
        return _fmt_ollama_models(output)
    if action == "tailscale":
        return _fmt_tailscale(output)
    return f"```\n{output[:3000]}\n```"


def run_action_via_api(action: str, arg: str = "") -> str:
    """Execute an action — OpenClaw first (read/introspection), executor fallback.

    OpenClaw's HTTP gateway safely serves read/introspection tools and hard-denies
    shell/mutating actions, so we try it first and fall back to the tiered executor
    (/action) for anything it doesn't serve. This keeps mutating actions on the
    permission-gated executor path while letting OpenClaw handle live introspection.
    """
    try:
        from openclaw.bridge import run_action as oc_run_action
        oc = oc_run_action(action, {"arg": arg} if arg else None)
        if oc.get("ok"):
            out = oc.get("output", "") or "Done."
            return f"✅ {_format_action_output(action, out)}"
        # ok=False with fallback=True → fall through to executor below
    except Exception as e:
        log.debug("OpenClaw run_action skipped for %s: %s", action, e)

    try:
        resp = requests.post(
            f"{API_BASE}/action",
            json={"action": action, "arg": arg, "silent": True},
            headers=_ah(),
            timeout=300,
        )
        d = resp.json()
        if d.get("status") == "pending":
            return f"⏳ PENDING:{d['request_id']}"
        output = d.get("output", "")
        ok = d.get("success", True)
        icon = "✅" if ok else "❌"
        if not output:
            return f"{icon} Done."
        return f"{icon} {_format_action_output(action, output)}"
    except requests.exceptions.ConnectionError:
        return "⚠️ Sorry Sir, Jarvis API is not reachable right now."
    except Exception as e:
        log.warning("run_action_via_api error for %s: %s", action, e)
        return "⚠️ Sorry Sir, the action could not be completed. Check logs for details."


async def _send_action_result(update, result: str, desc: str = "", arg: str = "") -> None:
    """Send action result — if pending, show Approve/Deny inline buttons."""
    if result.startswith("⏳ PENDING:"):
        req_id = result.split(":", 1)[1].strip()
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from fmt import code, esc
        label = desc or "Action"
        body = f"⚡ <b>Action request</b> — <code>{req_id}</code>\n{esc(label)}"
        if arg:
            body += f"\nArg: <code>{esc(arg)}</code>"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve",  callback_data=f"approve_{req_id}"),
            InlineKeyboardButton("❌ Deny",     callback_data=f"deny_{req_id}"),
        ]])
        await send(update, body, already_html=True, reply_markup=keyboard)
    else:
        await send(update, _to_html(result), already_html=True)


def _auto_save_interaction(uid: int, query: str, response: str) -> None:
    """Fire-and-forget: save interaction to Jarvis memory. Called as thread to avoid blocking."""
    try:
        from datetime import datetime as _dt
        payload = {
            "text": f"Q: {query[:500]}\nA: {response[:500]}",
            "metadata": {"type": "interaction", "uid": str(uid), "date": _dt.now().isoformat()[:10]}
        }
        requests.post(f"{API_BASE}/memory/save", json=payload, headers=_ah(), timeout=5)
    except Exception:
        pass


def main():
    try:
        from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )
    except ImportError:
        log.error("python-telegram-bot not installed.")
        sys.exit(1)

    token = load_token()

    # Save chat_id on first message for monitor/analyze to use
    def _cache_chat_id(uid: int):
        cache = JARVIS_HOME / "data" / "telegram_chat_id.json"
        if not cache.exists():
            import json as _j
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(_j.dumps({"chat_id": uid}))

    async def send(update: Update, text: str, parse_mode: str = "HTML",
                   reply_markup=None, already_html: bool = False):
        """Send text with HTML formatting by default.

        Pass already_html=True when the string was built with fmt helpers
        (bold/code/esc/etc.) and must NOT be run through _to_html() again —
        doing so would escape all the tags and show raw HTML to the user.

        Falls back to plain text (all tags stripped) if Telegram rejects the HTML.
        """
        if already_html or parse_mode != "HTML":
            converted = text
        else:
            converted = _to_html(text)
        # Safety cap — ensure no single send call can produce excessive chunks
        # (e.g. from action outputs or looping model responses that bypass query_jarvis).
        if len(converted) > MAX_RESPONSE_CHARS:
            log.warning("send(): text truncated from %d chars", len(converted))
            converted = converted[:MAX_RESPONSE_CHARS] + "\n\n… [truncated — response too long]"
        parts = split_message(converted)
        for i, part in enumerate(parts):
            kw = {}
            if reply_markup and i == len(parts) - 1:
                kw["reply_markup"] = reply_markup
            try:
                if update.message:
                    await update.message.reply_text(part, parse_mode=parse_mode, **kw)
            except Exception:
                # Fallback: strip all HTML tags and send as plain text
                plain = re.sub(r"<[^>]+>", "", part).strip()
                try:
                    if update.message:
                        await update.message.reply_text(plain or "…", **kw)
                except Exception:
                    pass

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        _cache_chat_id(uid)
        _histories[uid] = deque(maxlen=MAX_HISTORY)
        from fmt import bold, italic
        welcome = (
            f"👋 {bold('Jarvis is online, Sir!')}\n\n"
            f"{italic('Your personal AI brain is ready.')}\n"
            f"I can answer questions, run system commands, search the web, "
            f"analyse images, and manage your projects.\n\n"
            f"Use the quick-action buttons below, or type any command.\n\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
        )
        await send(update, welcome, already_html=True,
                   reply_markup=_build_reference_keyboard())
        await send(update, HELP_TEXT, already_html=True)

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, HELP_TEXT, already_html=True)

    async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, get_stats(), already_html=True)

    async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, get_health(), already_html=True)

    async def sysinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.chat.send_action("typing")
        await send(update, get_sysinfo(), already_html=True)

    async def index_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, "Indexing started, this may take a few minutes...")
        await send(update, trigger_index())

    async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        _histories[uid] = deque(maxlen=MAX_HISTORY)
        _save_histories()
        await send(update, "Conversation history cleared.")

    async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from fmt import bold
        uid = update.effective_user.id
        _histories[uid] = deque(maxlen=MAX_HISTORY)
        _save_histories()
        _last_interaction.pop(uid, None)
        await send(update,
            f"🔄 {bold('Fresh start, Sir.')}\n\n"
            f"Chat history cleared. I'm ready for new instructions.\n"
            f"Use /help to see what I can do.",
            already_html=True
        )

    async def remember_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save an explicit fact: /remember key value  or  /remember value (saves as preference)"""
        text = " ".join(context.args).strip() if context.args else ""
        if not text:
            await send(update, "Usage: /remember KEY VALUE\nExample: /remember editor vim")
            return
        parts = text.split(None, 1)
        if len(parts) == 2:
            key = re.sub(r"\s+", "_", parts[0].lower())
            val = parts[1].strip()
        else:
            key = "preference"
            val = parts[0].strip()
        facts = load_facts()
        facts[key] = val
        save_facts(facts)
        from fmt import bold, code, esc
        await send(update, f"✅ Remembered: {bold(esc(key))} = {code(val)}", already_html=True)

    async def actions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from executor import ACTIONS, AUTO, CONFIRM, APPROVE
        from fmt import bold, code, esc
        lines = [f"⚡ {bold('Available Actions')}\n"]
        for tier, label, icon in [(AUTO, "Auto", "⚡"), (CONFIRM, "Confirm", "🔔"), (APPROVE, "Approve", "🔐")]:
            items = [
                f"  {icon} {code(k)} — {esc(v['desc'])}"
                for k, v in ACTIONS.items()
                if v["tier"] == tier and not k.startswith("kali_")
            ]
            if items:
                lines.append(bold(f"{icon} {label}"))
                lines.extend(items)
                lines.append("")
        # Kali tools summary — full catalogue via /kali
        kali_auto  = [k for k, v in ACTIONS.items() if k.startswith("kali_") and v["tier"] == AUTO]
        kali_appr  = [k for k, v in ACTIONS.items() if k.startswith("kali_") and v["tier"] == APPROVE]
        lines.append(bold("🛡 Kali Pentest Tools"))
        lines.append(f"  ⚡ {len(kali_auto)} passive recon  🔐 {len(kali_appr)} active/exploit")
        lines.append(f"  Use {code('/kali')} for the full tool catalogue grouped by level.")
        lines.append("")
        text = "\n".join(lines)
        B = InlineKeyboardButton
        quick_keyboard = InlineKeyboardMarkup([
            [B("⚡ Processes", callback_data="act_ps"),
             B("💾 Disk",      callback_data="act_disk"),
             B("🧠 Memory",    callback_data="act_memory"),
             B("⏱ Uptime",    callback_data="act_uptime")],
            [B("🎮 GPU",       callback_data="act_gpu"),
             B("🔧 Services",  callback_data="act_services"),
             B("🌐 Ports",     callback_data="act_ports"),
             B("📡 Network",   callback_data="act_network")],
            [B("📈 Top CPU",   callback_data="act_top5_cpu"),
             B("📊 Top RAM",   callback_data="act_top5_mem"),
             B("🤖 AI Models", callback_data="act_ollama_models"),
             B("🔒 Tailscale", callback_data="act_tailscale")],
            [B("🚀 PM2",       callback_data="act_pm2_status"),
             B("📦 Git Status",callback_data="act_git_status_all"),
             B("📁 Projects",  callback_data="act_project_status"),
             B("🌍 Nginx",     callback_data="act_nginx_status")],
        ])
        await send(update, text, already_html=True, reply_markup=quick_keyboard)

    async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from permissions import list_pending
        from fmt import bold, code, esc
        pending = list_pending()
        if not pending:
            await send(update, "✅ No pending approvals, Sir.", already_html=True)
            return
        lines = [f"{bold('⏳ Pending Approvals')}\n"]
        for req in pending:
            lines.append(f"  • {code(req['id'])} — {esc(req.get('description', '?'))}")
        await send(update, "\n".join(lines), already_html=True)

    async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from fmt import bold, pre, esc
        args = context.args
        if not args:
            await send(update, "Usage: /approve <i>REQUEST_ID</i>", already_html=True)
            return
        req_id = args[0].upper()
        await update.message.chat.send_action("typing")
        try:
            resp = requests.post(f"{API_BASE}/approve/{req_id}", headers=_ah(), timeout=90)
            d = resp.json()
            output = d.get("output", "")
            ok = d.get("success", True)
            icon = "✅" if ok else "❌"
            if output:
                reply = f"{icon} {bold('Executed')}\n\n{pre(output[:2000])}"
            else:
                reply = f"{icon} {bold('Done.')}"
            await send(update, reply, already_html=True)
        except Exception as e:
            await send(update, f"⚠️ Sorry Sir, approval failed: {esc(str(e))}", already_html=True)

    async def deny_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from fmt import esc, code
        args = context.args
        if not args:
            await send(update, "Usage: /deny <i>REQUEST_ID</i>", already_html=True)
            return
        req_id = args[0].upper()
        try:
            requests.post(f"{API_BASE}/deny/{req_id}", headers=_ah(), timeout=10)
            await send(update, f"❌ Denied: {code(req_id)}", already_html=True)
        except Exception as e:
            log.warning("deny_cmd error: %s", e)
            await send(update, "⚠️ Sorry Sir, the denial request could not be sent.")

    async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        if data.startswith("v3perm:"):
            # Progressive-trust permission button: v3perm:<request_id>:<action>
            try:
                _, req_id, action = data.split(":", 2)
                resp = requests.post(f"{API_BASE}/v3/permission/{req_id}/{action}",
                                     headers=_ah(), timeout=120)
                msg = resp.json().get("message", "Done, Sir.")
                await query.edit_message_text(msg, parse_mode="Markdown")
            except requests.exceptions.ConnectionError:
                await query.edit_message_text("⚠️ Sorry Sir, Jarvis API is not reachable right now.")
            except Exception as e:
                log.warning("v3perm callback error: %s", e)
                await query.edit_message_text("⚠️ Sorry Sir, the permission could not be processed.")
            return
        if data.startswith("good_"):
            iid = data[5:]
            _send_feedback(iid, "good")
            await query.edit_message_text("👍 Noted. Jarvis will remember this.")
        elif data.startswith("bad_"):
            iid = data[4:]
            _send_feedback(iid, "bad")
            await query.edit_message_text("👎 Noted. Use /correct to tell Jarvis the right answer.")
        elif data.startswith("approve_"):
            req_id = data.split("_", 1)[1]
            try:
                resp = requests.post(f"{API_BASE}/approve/{req_id}", headers=_ah(), timeout=90)
                d = resp.json()
                output = d.get("output", "")
                ok = d.get("success", True)
                icon = "✅" if ok else "❌"
                from fmt import bold, pre
                reply = f"{icon} {bold('Done')}\n\n{pre(output[:1500])}" if output else f"{icon} {bold('Done.')}"
                await query.edit_message_text(reply, parse_mode="HTML")
            except requests.exceptions.ConnectionError:
                await query.edit_message_text("⚠️ Sorry Sir, Jarvis API is not reachable right now.")
            except Exception as e:
                log.warning("button approve error: %s", e)
                await query.edit_message_text("⚠️ Sorry Sir, the approval could not be processed.")
        elif data.startswith("deny_"):
            req_id = data.split("_", 1)[1]
            try:
                requests.post(f"{API_BASE}/deny/{req_id}", headers=_ah(), timeout=10)
                await query.edit_message_text("❌ Denied.")
            except Exception as e:
                log.warning("button deny error: %s", e)
                await query.edit_message_text("⚠️ Sorry Sir, the denial could not be processed.")

        elif data.startswith("cmd_"):
            cmd = data[4:]
            if cmd == "stats":
                reply = get_stats()
            elif cmd == "sysinfo":
                reply = get_sysinfo()
            elif cmd == "health":
                reply = get_health()
            elif cmd == "selfcheck":
                try:
                    d = requests.get(f"{API_BASE}/selfcheck", headers=_ah(), timeout=20).json()
                    overall = d.get("overall", "unknown")
                    icon = "✅" if overall == "ok" else ("⚠️" if overall == "degraded" else "❌")
                    from fmt import bold, code, esc
                    lines = [f"{icon} {bold('Jarvis Self-Check')} — {bold(esc(overall.upper()))}\n"]
                    checks = d.get("checks", {})
                    for k, v in checks.items():
                        label = _CHECK_LABELS.get(k, k)
                        lines.append(f"  {_check_icon(k, v)} {label}: {code(str(v))}")
                    if not checks:
                        lines.append("  <i>No check data returned.</i>")
                    issues = d.get("issues", [])
                    if issues:
                        lines.append(f"\n{bold('⚠️ Issues:')}")
                        for iss in issues:
                            lines.append(f"  • {esc(str(iss))}")
                    reply = "\n".join(lines)
                except requests.exceptions.ConnectionError:
                    reply = "⚠️ Sorry Sir, Jarvis API is not reachable right now."
                except Exception as e:
                    log.warning("button selfcheck error: %s", e)
                    reply = "⚠️ Sorry Sir, the self-check could not be completed."
            elif cmd == "learn":
                try:
                    requests.post(f"{API_BASE}/learn", headers=_ah(), timeout=10)
                    reply = "🧠 Brain training started, Sir. Check /stats in a minute."
                except Exception as e:
                    log.warning("button learn error: %s", e)
                    reply = "⚠️ Sorry Sir, brain training could not be started."
            elif cmd == "index":
                reply = "🔄 Indexing started, Sir…"
                await query.message.reply_text(reply)
                reply = trigger_index()
            elif cmd == "actions":
                from executor import ACTIONS, AUTO, CONFIRM, APPROVE
                from fmt import bold, code, esc
                auto = [k for k, v in ACTIONS.items() if v["tier"] == AUTO  and not k.startswith("kali_")]
                conf = [k for k, v in ACTIONS.items() if v["tier"] == CONFIRM and not k.startswith("kali_")]
                appr = [k for k, v in ACTIONS.items() if v["tier"] == APPROVE and not k.startswith("kali_")]
                kali_a = [k for k, v in ACTIONS.items() if k.startswith("kali_") and v["tier"] == AUTO]
                kali_p = [k for k, v in ACTIONS.items() if k.startswith("kali_") and v["tier"] == APPROVE]
                reply = (f"{bold(f'Actions ({len(ACTIONS)} total)')}\n\n"
                         f"{bold('⚡ AUTO')} ({len(auto)})\n" + "  ".join(code(a) for a in auto) + "\n\n"
                         f"{bold('🔔 CONFIRM')} ({len(conf)})\n" + "  ".join(code(a) for a in conf) + "\n\n"
                         f"{bold('🔐 APPROVE')} ({len(appr)})\n" + "  ".join(code(a) for a in appr) + "\n\n"
                         f"{bold('🛡 Kali Tools')} — ⚡ {len(kali_a)} passive  🔐 {len(kali_p)} active/exploit\n"
                         f"  Use {code('/kali')} for full tool catalogue.")
            elif cmd == "pending":
                try:
                    import json as _j, pathlib as _p
                    pf = _p.Path("/home/kali/.jarvis/data/pending_approvals.json")
                    pending = _j.loads(pf.read_text()) if pf.exists() else {}
                    if pending:
                        from fmt import bold, code, esc
                        lines = [f"{bold('⏳ Pending Approvals')}\n"]
                        for rid, req in pending.items():
                            lines.append(f"  • {code(rid)} — {esc(str(req.get('action', '?')))}")
                        reply = "\n".join(lines)
                    else:
                        reply = "✅ No pending approvals, Sir."
                except Exception as e:
                    log.warning("button pending error: %s", e)
                    reply = "⚠️ Sorry Sir, could not load pending approvals."
            else:
                from fmt import code
                reply = f"⚠️ Unknown command: {code(cmd)}"
            # reply is already valid HTML — do NOT run through _to_html()
            try:
                for part in split_message(reply):
                    await query.message.reply_text(part, parse_mode="HTML")
            except Exception:
                plain = re.sub(r"<[^>]+>", "", reply)
                await query.message.reply_text(plain or "Done.")

        elif data.startswith("act_"):
            from executor import ACTIONS, AUTO, CONFIRM, APPROVE
            from fmt import esc
            action = data[4:]
            entry = ACTIONS.get(action, {})
            tier = entry.get("tier", AUTO)
            desc = entry.get("desc", action)

            # Actions needing a parameter: send ForceReply prompt
            if action in _NEEDS_ARG:
                from telegram import ForceReply
                await query.message.reply_text(
                    f"⚙️ <b>{esc(desc)}</b>\n\nSir, please provide the argument for <code>{esc(action)}</code>:\n"
                    f"<i>Examples vary — e.g. for shell: <code>ls -la ~/.jarvis</code></i>",
                    parse_mode="HTML",
                    reply_markup=ForceReply(selective=True, input_field_placeholder=f"Argument for {action}…"),
                )
                _pending_action[query.from_user.id] = action
                await query.answer()
                return

            # CONFIRM/APPROVE tier: show inline confirmation keyboard
            if tier in (CONFIRM, APPROVE):
                tier_icon = "🔔" if tier == CONFIRM else "🔐"
                btn_label = "✅ Confirm" if tier == CONFIRM else "✅ Approve"
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(btn_label,   callback_data=f"confirm_act_{action}"),
                    InlineKeyboardButton("❌ Cancel",  callback_data="cancel_act"),
                ]])
                await query.message.reply_text(
                    f"{tier_icon} <b>{esc(desc)}</b>\n\nSir, confirm this action?",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                await query.answer()
                return

            # AUTO tier: execute immediately
            await query.message.chat.send_action("typing")
            result = run_action_via_api(action)
            if result.startswith("⏳ PENDING:"):
                req_id = result.split(":", 1)[1].strip()
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup as IKM
                kb = IKM([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{req_id}"),
                    InlineKeyboardButton("❌ Deny",    callback_data=f"deny_{req_id}"),
                ]])
                await query.message.reply_text(
                    f"⚡ <b>{esc(desc)}</b> — <code>{req_id}</code>",
                    parse_mode="HTML", reply_markup=kb)
                await query.answer()
                return
            result_html = _to_html(result)
            try:
                for part in split_message(result_html):
                    await query.message.reply_text(part, parse_mode="HTML")
            except Exception:
                # Fallback to plain text if HTML rendering fails
                plain = re.sub(r"<[^>]+>", "", result_html)
                await query.message.reply_text(plain or "Done.")
            await query.answer()

        elif data.startswith("confirm_act_"):
            action = data[len("confirm_act_"):]
            await query.message.chat.send_action("typing")
            result = run_action_via_api(action)
            result_html = _to_html(result)
            try:
                for part in split_message(result_html):
                    await query.message.reply_text(part, parse_mode="HTML")
            except Exception:
                plain = re.sub(r"<[^>]+>", "", result_html)
                await query.message.reply_text(plain or "Done.")
            await query.answer()

        elif data == "cancel_act":
            await query.edit_message_text("❌ Cancelled.", parse_mode="HTML")
            await query.answer()

    def _build_reference_keyboard() -> "InlineKeyboardMarkup":
        B = InlineKeyboardButton
        return InlineKeyboardMarkup([
            # ── Dashboard commands ────────────────────────────────────────────
            [B("📊 Stats",        callback_data="cmd_stats"),
             B("🖥️ System Info",  callback_data="cmd_sysinfo"),
             B("❤️ Health",       callback_data="cmd_health"),
             B("🔍 Self Check",   callback_data="cmd_selfcheck")],
            [B("⚡ Actions",      callback_data="cmd_actions"),
             B("⏳ Pending",      callback_data="cmd_pending"),
             B("🧠 Train Brain",  callback_data="cmd_learn"),
             B("🗂️ Re-Index",    callback_data="cmd_index")],
            # ── LOCAL SYSTEM ──────────────────────────────────────────────────
            [B("⚡ Processes",   callback_data="act_ps"),
             B("💾 Disk",        callback_data="act_disk"),
             B("🧠 Memory",      callback_data="act_memory"),
             B("⏱ Uptime",      callback_data="act_uptime")],
            [B("🎮 GPU",         callback_data="act_gpu"),
             B("🔧 Services",    callback_data="act_services"),
             B("🌐 Ports",       callback_data="act_ports"),
             B("📡 Network",     callback_data="act_network")],
            [B("📈 Top CPU",     callback_data="act_top5_cpu"),
             B("📊 Top RAM",     callback_data="act_top5_mem"),
             B("👤 Who Online",  callback_data="act_who"),
             B("⏰ Crontab",     callback_data="act_crontab")],
            [B("🔒 Tailscale",   callback_data="act_tailscale"),
             B("🤖 AI Models",   callback_data="act_ollama_models"),
             B("📦 Git Status",  callback_data="act_git_status_all"),
             B("🌍 Nginx",       callback_data="act_nginx_status")],
            # ── LOGS ──────────────────────────────────────────────────────────
            [B("📝 Jarvis Log",  callback_data="act_jarvis_logs_tail"),
             B("📝 API Log",     callback_data="act_logs_jarvis"),
             B("📱 Bot Log",     callback_data="act_logs_telegram"),
             B("🤖 Ollama Log",  callback_data="act_logs_ollama")],
            [B("🐳 Docker Log API", callback_data="act_docker_logs_api"),
             B("🐳 Docker Log Bot", callback_data="act_docker_logs_bot"),
             B("☁️ VPS Nginx Log",  callback_data="act_vps_nginx_logs"),
             B("☁️ VPS Services",   callback_data="act_vps_services")],
            # ── PROJECTS / VPS ────────────────────────────────────────────────
            [B("📁 Projects",    callback_data="act_project_status"),
             B("🚀 PM2",         callback_data="act_pm2_status"),
             B("☁️ VPS Disk",    callback_data="act_vps_disk"),
             B("☁️ VPS RAM",     callback_data="act_vps_free")],
            [B("☁️ VPS Procs",   callback_data="act_vps_ps"),
             B("🐳 Docker PS",   callback_data="act_docker_ps"),
             B("🐳 Docker Stats",callback_data="act_docker_stats"),
             B("🌐 VPS Nginx",   callback_data="act_nginx_status")],
            [B("💳 GW Logs",     callback_data="act_pm2_logs_gateway"),
             B("🏠 SL Logs",     callback_data="act_pm2_logs_starline"),
             B("💳 GW Diff",     callback_data="act_git_diff_gw"),
             B("🏠 SL Diff",     callback_data="act_git_diff_sl")],
            [B("💳 GW History",  callback_data="act_git_log_gw"),
             B("🏠 SL History",  callback_data="act_git_log_sl"),
             B("📂 File List",   callback_data="act_file_list"),
             B("📄 File Read",   callback_data="act_file_read")],
            # ── CONFIRM: RESTART / SERVICES ───────────────────────────────────
            [B("🔄 Restart Jarvis",   callback_data="act_restart_jarvis"),
             B("📱 Restart Bot",      callback_data="act_restart_telegram"),
             B("🤖 Restart Ollama",   callback_data="act_restart_ollama"),
             B("🔁 Restart Monitor",  callback_data="act_restart_monitor")],
            [B("⏹ Stop Jarvis",       callback_data="act_stop_jarvis"),
             B("🔄 Restart GW",       callback_data="act_restart_gateway"),
             B("🔄 Restart SL",       callback_data="act_restart_starline"),
             B("🔄 Restart Sync",     callback_data="act_restart_jarvis_sync")],
            [B("🔄 Restart Nginx",    callback_data="act_vps_restart_nginx"),
             B("🐳 Docker Up",        callback_data="act_docker_up"),
             B("🐳 Docker Down",      callback_data="act_docker_down"),
             B("🐳 Restart API",      callback_data="act_docker_restart_api")],
            [B("🐳 Restart Bot",      callback_data="act_docker_restart_bot"),
             B("🗑 Clear Memory DB",  callback_data="act_clear_history"),
             B("🗂 Reindex",          callback_data="act_reindex"),
             B("🔄 Restart Sync2",    callback_data="act_restart_jarvis_sync")],
            # ── CONFIRM: DEPLOY / BUILD ───────────────────────────────────────
            [B("⬇️ Pull Gateway",     callback_data="act_git_pull_gw"),
             B("⬇️ Pull Starline",    callback_data="act_git_pull_sl"),
             B("☁️ VPS Pull GW",      callback_data="act_vps_git_pull_gw"),
             B("☁️ VPS Pull SL",      callback_data="act_vps_git_pull_sl")],
            [B("📦 npm install GW",   callback_data="act_npm_install_gw"),
             B("📦 pnpm install SL",  callback_data="act_pnpm_install_sl"),
             B("🔨 Build Gateway",    callback_data="act_npm_build_gw"),
             B("🔨 Build Starline",   callback_data="act_pnpm_build_sl")],
            # ── APPROVE: SENSITIVE ────────────────────────────────────────────
            [B("🚀 Deploy VPS",       callback_data="act_deploy_vps"),
             B("💻 SSH Command",      callback_data="act_ssh_cmd"),
             B("🖥 Shell Command",    callback_data="act_shell"),
             B("🤖 Claude Task",      callback_data="act_claude_task")],
            [B("✏️ File Write",        callback_data="act_file_write"),
             B("♻️ Reboot",           callback_data="act_reboot"),
             B("⬆️ Update System",    callback_data="act_update_system"),
             B("❓ Help",             callback_data="cmd_actions")],
            # ── 🧪 SELF-TEST ──────────────────────────────────────────────────
            [B("🔬 Probe AI",         callback_data="cmd_probe"),
             B("📊 Skills",           callback_data="cmd_skills"),
             B("🧠 Recall",           callback_data="cmd_recall"),
             B("🎯 Objectives",       callback_data="cmd_objectives"),
             B("⏱ Benchmark",        callback_data="cmd_benchmark")],
        ])

    def _feedback_keyboard(iid: str) -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("👍 Good", callback_data=f"good_{iid}"),
            InlineKeyboardButton("👎 Needs Work", callback_data=f"bad_{iid}"),
        ]])

    def _send_feedback(iid: str, rating: str) -> bool:
        try:
            r = requests.post(f"{API_BASE}/feedback", json={"interaction_id": iid, "rating": rating}, headers=_ah(), timeout=5)
            return r.ok
        except Exception:
            return False

    async def voice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        arg = (context.args[0].lower() if context.args else "").strip()
        if arg == "on":
            _voice_mode[uid] = True
            _save_voice_prefs()
            await send(update, "Voice responses ON. Jarvis will send audio after each reply.")
        elif arg == "off":
            _voice_mode[uid] = False
            _save_voice_prefs()
            await send(update, "Voice responses OFF.")
        else:
            state = "ON" if _is_voice_on(uid) else "OFF"
            await send(update, f"Voice mode is {state}.\nUsage: /voice on | /voice off")

    async def correct_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        correction = " ".join(context.args).strip() if context.args else ""
        if not correction:
            await send(update, "Usage: /correct <what Jarvis should have said>")
            return
        iid = _last_interaction.get(uid)
        if not iid:
            await send(update, "No recent response to correct.")
            return
        try:
            r = requests.post(f"{API_BASE}/correct", json={"interaction_id": iid, "correction": correction}, headers=_ah(), timeout=5)
            if r.ok:
                await send(update, "✅ Correction saved, Sir. Jarvis will learn from this.")
            else:
                await send(update, "⚠️ Sorry Sir, the correction could not be saved right now.")
        except Exception as e:
            log.warning("correct_cmd error: %s", e)
            await send(update, "⚠️ Sorry Sir, the correction could not be saved. Please try again.")

    async def learn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, "Running brain training in background...")
        try:
            r = requests.post(f"{API_BASE}/learn", headers=_ah(), timeout=10)
            if r.ok:
                await send(update, "🧠 Brain training started, Sir. Check /stats in a minute.")
            else:
                log.warning("learn_cmd API error: %s %s", r.status_code, r.text[:200])
                await send(update, f"⚠️ Sorry Sir, brain training returned an error (HTTP {r.status_code}). Check logs.")
        except requests.exceptions.ConnectionError:
            await send(update, "⚠️ Sorry Sir, Jarvis API is not reachable right now.")
        except Exception as e:
            log.warning("learn_cmd error: %s", e)
            await send(update, "⚠️ Sorry Sir, brain training could not be started. Check logs for details.")

    async def task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Delegate a task to Claude Code (background, results via Telegram)."""
        from fmt import bold, italic, code, esc
        task = " ".join(context.args).strip() if context.args else ""
        if not task:
            await send(update,
                f"Usage: /task <i>description</i>\n"
                f"Example: <code>/task fix the typo in gateway-admin index.js line 42</code>",
                already_html=True,
            )
            return
        await send(update, f"🤖 Queuing Claude Code task:\n{code(task[:200])}\n\nApproval required to run.", already_html=True)
        try:
            resp = requests.post(f"{API_BASE}/action", json={"action": "claude_task", "arg": task}, headers=_ah(), timeout=10)
            d = resp.json()
            req_id = d.get("request_id", "")
            if req_id:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Run Now", callback_data=f"approve_{req_id}"),
                    InlineKeyboardButton("❌ Cancel",  callback_data=f"deny_{req_id}"),
                ]])
                await update.message.reply_text(
                    f"🔐 {bold('Claude Task')} <code>{req_id}</code>\n\n"
                    f"{italic(esc(task[:300]))}\n\n"
                    f"<i>Results will be sent here when complete.</i>",
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            else:
                await send(update, d.get("output", "✅ Task queued, Sir."))
        except Exception as e:
            log.warning("task_cmd error: %s", e)
            await send(update, f"⚠️ Sorry Sir, the task could not be queued. Please try again.")

    async def orchestrate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Run a task through the v3 master orchestrator (swarm + Guard).
        Renders [Allow][Allow & Save][Deny] buttons when a step needs approval."""
        from fmt import bold, code
        task = " ".join(context.args).strip() if context.args else ""
        if not task:
            await send(update,
                "Usage: /orchestrate <i>task</i>\n"
                "Example: <code>/orchestrate audit my server security</code>",
                already_html=True)
            return
        await send(update, f"🧠 Orchestrating:\n{code(task[:200])}", already_html=True)
        try:
            resp = requests.post(f"{API_BASE}/v3/orchestrate",
                                 json={"request": task}, headers=_ah(), timeout=180)
            d = resp.json()
        except requests.exceptions.ConnectionError:
            await send(update, "⚠️ Sorry Sir, Jarvis API is not reachable right now.")
            return
        except Exception as e:
            log.warning("orchestrate_cmd error: %s", e)
            await send(update, "⚠️ Sorry Sir, the orchestration failed.")
            return

        kb_rows = d.get("permission_keyboard") or []
        if d.get("needs_approval") and kb_rows:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(btn["text"], callback_data=btn["callback_data"])
                 for btn in row] for row in kb_rows
            ])
            await update.message.reply_text(d.get("answer", "Permission required, Sir."),
                                            reply_markup=keyboard, parse_mode="Markdown")
        else:
            ans = d.get("answer", "Done, Sir.")
            meta = (f"\n\n<i>{d.get('tasks_completed', 0)}/"
                    f"{d.get('tasks_dispatched', 0)} tasks · "
                    f"{d.get('elapsed_sec', 0):.1f}s</i>")
            await send(update, ans + meta, already_html=False)

    async def exec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Smart action dispatch — detect action or plan via claude-plan."""
        query_text = " ".join(context.args).strip() if context.args else ""
        if not query_text:
            await send(update, "Usage: /exec <natural language command>\nExample: /exec check VPS status and restart gateway if down")
            return
        await update.message.chat.send_action("typing")
        try:
            resp = requests.post(f"{API_BASE}/claude-plan",
                                 json={"query": query_text}, headers=_ah(), timeout=60)
            d = resp.json()
            reply = d.get("response", "No response.")
            await send(update, reply)
        except requests.exceptions.ConnectionError:
            await send(update, "⚠️ Sorry Sir, Jarvis API is not reachable right now.")
        except Exception as e:
            log.warning("exec_cmd error: %s", e)
            await send(update, "⚠️ Sorry Sir, the command could not be dispatched. Check logs for details.")

    async def web_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Real-time web search + AI analysis. Usage: /web <query>"""
        from fmt import bold, italic, link, code, esc, card as fmt_card, split_smart
        query_text = " ".join(context.args).strip() if context.args else ""
        if not query_text:
            await send(update,
                f"Usage: /web <i>query</i>\n"
                f"Examples:\n"
                f"  /web latest crypto market news\n"
                f"  /web bitcoin price today\n"
                f"  /web kali linux 2025 release",
                already_html=True,
            )
            return
        thinking_msg = await update.message.reply_text("🔍 Searching the web, Sir…")
        try:
            from web_search import web_answer
            answer, sources = web_answer(query_text, use_cloud=True)

            # ── 1. AI Summary ──────────────────────────────────────────────
            summary_html = _to_html(answer) if answer else "<i>No summary available.</i>"
            await thinking_msg.edit_text(summary_html, parse_mode="HTML")

            # ── 2. Source cards (up to 5) ──────────────────────────────────
            if sources:
                cards_html = f"🔗 {bold('Sources')}\n"
                for i, s in enumerate(sources[:5], 1):
                    title   = s.get("title", f"Source {i}") or f"Source {i}"
                    url     = s.get("url", "")
                    snippet = (s.get("snippet") or s.get("full_text") or "").strip()
                    date    = s.get("date", "")[:10] if s.get("date") else ""
                    source  = s.get("source", "")
                    meta    = " · ".join(filter(None, [
                        (f"📅 {date}" if date else ""),
                        (f"🌐 {source}" if source else ""),
                    ]))

                    # Truncate title at word boundary (≤100 chars)
                    if len(title) > 100:
                        title = title[:100].rsplit(" ", 1)[0] + "…"

                    if url:
                        header = bold(link(esc(title), url))
                    else:
                        header = bold(esc(title))

                    card_lines = [f"\n{header}"]
                    if meta:
                        card_lines.append(esc(meta))
                    if snippet:
                        card_lines.append(esc(snippet[:150]))
                    cards_html += "\n".join(card_lines)

                await update.message.reply_text(cards_html.strip(), parse_mode="HTML",
                    disable_web_page_preview=True)

            # ── 3. First image thumbnail (if available) ────────────────────
            for s in sources[:5]:
                img = s.get("image", "")
                if img and img.startswith("http"):
                    try:
                        caption = esc(s.get("title", "")[:200])
                        await context.bot.send_photo(
                            chat_id=update.effective_chat.id,
                            photo=img,
                            caption=caption,
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    break  # one image is enough

        except requests.exceptions.ConnectionError:
            await thinking_msg.edit_text("⚠️ Sorry Sir, the web search service is not reachable right now.")
        except Exception as e:
            log.warning("web_cmd error: %s", e)
            await thinking_msg.edit_text("⚠️ Sorry Sir, the web search encountered an error. Please try again.")

    async def weather_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Quick weather via wttr.in. Usage: /weather [city]"""
        city = " ".join(context.args).strip() if context.args else ""
        query = f"weather in {city}" if city else "weather today"
        from web_search import get_weather
        from fmt import code
        # get_weather() returns a pre-formatted HTML-ready string
        result = get_weather(query)
        if result:
            await send(update, f"Sir,\n{result}", already_html=True)
        else:
            await send(update,
                f"⚠️ Could not fetch weather, Sir. Try {code('/web weather in &lt;city&gt;')}",
                already_html=True)

    async def browse_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Open a URL in headless Chrome, summarise + optional screenshot."""
        args = context.args or []
        url = args[0] if args else ""
        want_shot = "--screenshot" in args or "-s" in args
        if not url or not url.startswith("http"):
            await send(update, "Usage: /browse <url> [--screenshot]\nExample: /browse https://github.com --screenshot")
            return
        thinking_msg = await update.message.reply_text(f"🌐 Opening {url} ...")
        try:
            from browser.agent import BrowserAgent, quit_browser
            agent = BrowserAgent.get()
            page = agent.fetch(url, want_screenshot=want_shot)
            page_text, title, png = page["text"], page["title"], page["png"]

            import claude_client
            system = (
                "You are Jarvis. Summarise this web page for Mike.\n"
                "Rules: open with 'Sir,', max 5 bullet points, highlight key facts/numbers, "
                "note any important links or actions available on the page. No fluff."
            )
            summary, _ = claude_client.get_client().query(
                system=system,
                user=f"Page title: {title}\n\nContent:\n{page_text[:4000]}",
            )
            await thinking_msg.edit_text(_to_html(summary), parse_mode="HTML")

            if want_shot and png:
                await update.message.reply_photo(photo=png, caption=f"📸 {title[:80]}")
        except Exception as e:
            log.warning("browse_cmd error: %s", e)
            await thinking_msg.edit_text("⚠️ Sorry Sir, the browser could not open that page. It may be unreachable or require JavaScript.")
        finally:
            try:
                quit_browser()  # close browser after each /browse → zero idle CPU
            except Exception:
                pass

    async def oc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """OpenClaw gateway — status or direct tool invoke.
        /oc            → gateway health
        /oc <tool>     → invoke an OpenClaw tool (e.g. /oc sessions_list)
        """
        args = context.args or []
        try:
            from openclaw import bridge as oc
            if not args:
                up = oc.openclaw_available()
                icon = "🟢" if up else "🔴"
                await send(update, f"{icon} OpenClaw gateway: {'live' if up else 'unavailable'}, Sir.")
                return
            tool = args[0]
            tool_args = {"arg": " ".join(args[1:])} if len(args) > 1 else None
            await update.message.chat.send_action("typing")
            res = oc.tool_invoke(tool, tool_args)
            if res.get("ok"):
                from fmt import bold, esc
                out = res.get("output", "") or "Done."
                await send(update, f"✅ {bold(esc(tool))}\n{esc(out[:3500])}", already_html=True)
            else:
                from fmt import bold, esc
                await send(update, f"❌ {bold(esc(tool))}: {esc(res.get('reason', 'failed'))}", already_html=True)
        except Exception as e:
            log.warning("oc_cmd error: %s", e)
            await send(update, "⚠️ Sorry Sir, OpenClaw encountered an error. It may not be running.")

    async def kali_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Kali pentest tools (authorized security testing).
        /kali                        → list tools by level
        /kali <tool> <target> [opts] → run a tool (approve-tier prompts a button)
        """
        args = context.args or []
        # No args → help / tool catalogue grouped by level
        if not args:
            try:
                from kali_tools import list_tools
                tools = list_tools()
            except Exception as e:
                log.warning("kali_cmd list_tools error: %s", e)
                await send(update, "⚠️ Sorry Sir, the Kali tools module is not available. Check that kali_tools.py is installed.")
                return
            level_names = {
                1: "🔍 L1 Recon (passive, ⚡ auto)",
                2: "📡 L2 Scan/Enum (active, 🔐 approve)",
                3: "🌐 L3 Web (active, 🔐 approve)",
                4: "💥 L4 Intrusive/Exploit (🔐 approve)",
            }
            by_level: dict[int, list] = {}
            for t in tools:
                by_level.setdefault(t.get("level", 0), []).append(t)
            from fmt import bold, code, esc
            lines = [f"Sir, {bold('Kali pentest tools')} (authorized targets only)\n"]
            for lvl in sorted(by_level):
                lines.append(bold(level_names.get(lvl, f"Level {lvl}")))
                for t in sorted(by_level[lvl], key=lambda x: x["name"]):
                    tier_icon = "⚡" if t["tier"] == "auto" else "🔐"
                    lines.append(f"  {tier_icon} {code(t['name'])} — {esc(t['desc'])}")
                lines.append("")
            lines.append(f"Usage: {code('/kali <tool> <target> [opts]')}")
            lines.append(f"Example: {code('/kali nmap_quick scanme.nmap.org')}")
            lines.append(f"Reports land in {code('~/.jarvis/scans/')} (see /scans).")
            await send(update, "\n".join(lines), already_html=True)
            return

        tool = args[0]
        if tool.startswith("kali_"):
            tool = tool[len("kali_"):]
        target = args[1] if len(args) > 1 else ""
        opts = " ".join(args[2:]) if len(args) > 2 else ""
        if not target:
            from fmt import code
            await send(update,
                f"Sir, I need a target.\nUsage: {code(f'/kali {tool} <target> [opts]')}",
                already_html=True)
            return

        # Scope pre-check so we can warn before anything runs.
        scope_warn = ""
        try:
            from kali_tools import validate_scope
            from fmt import bold, code, esc
            sc = validate_scope(target)
            if not sc.get("in_scope", True):
                scope_warn = (
                    f"⚠️ {bold('OUT OF SCOPE')} target {code(target)} — "
                    f"{esc(sc.get('reason',''))}\nApproval will be required.\n\n"
                )
        except Exception:
            pass

        action = f"kali_{tool}"
        arg = (target + " " + opts).strip()

        await update.message.chat.send_action("typing")
        from fmt import code, esc
        await send(update,
            f"{scope_warn}Sir, dispatching {code(tool)} against {code(target)}…",
            already_html=True)

        # Run the (potentially long) scan off the event loop so the bot stays responsive.
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, run_action_via_api, action, arg)
        except Exception as e:
            log.warning("kali_cmd run error: %s", e)
            await send(update, "⚠️ Sorry Sir, the scan could not be started. Check logs for details.")
            return

        # Show Approve/Deny buttons for pending actions, plain output otherwise.
        await _send_action_result(update, result, desc=ACTIONS.get(action, {}).get("desc", action), arg=arg)

    async def scans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List the last ~10 scan reports in ~/.jarvis/scans/."""
        from datetime import datetime
        scans_dir = JARVIS_HOME / "scans"
        from fmt import bold, code, esc
        if not scans_dir.exists():
            await send(update, f"Sir, no scans yet — {code('~/.jarvis/scans/')} is empty.", already_html=True)
            return
        files = sorted(
            [p for p in scans_dir.iterdir() if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:10]
        if not files:
            await send(update, "Sir, no scan reports found yet.")
            return
        lines = [f"Sir, {bold('latest scan reports')}\n"]
        for p in files:
            st = p.stat()
            size = st.st_size
            size_h = f"{size}B" if size < 1024 else (f"{size//1024}KB" if size < 1024*1024 else f"{size//(1024*1024)}MB")
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            lines.append(f"  📄 {code(p.name)} — {size_h}, {mtime}")
        lines.append(f"\nRead one with {code('/exec read file <path>')} or open in the file viewer.")
        await send(update, "\n".join(lines), already_html=True)

    async def selfcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from fmt import bold, code, italic, esc
        await send(update, "🔍 Running full system self-check, Sir…")
        try:
            r = requests.get(f"{API_BASE}/selfcheck", headers=_ah(), timeout=20)
            r.raise_for_status()
            d = r.json()
            overall = d.get("overall", "unknown")
            emoji   = "✅" if overall == "ok" else ("⚠️" if overall == "degraded" else "❌")
            lines   = [f"{emoji} {bold('Jarvis Self-Check')} — {bold(esc(overall.upper()))}\n"]
            checks = d.get("checks", {})
            if checks:
                for k, v in checks.items():
                    label = _CHECK_LABELS.get(k, k)
                    lines.append(f"  {_check_icon(k, v)} {label}: {code(str(v))}")
            else:
                lines.append("  <i>No check data returned.</i>")
            issues = d.get("issues", [])
            if issues:
                lines.append(f"\n{bold('⚠️ Issues Found:')}")
                for iss in issues:
                    lines.append(f"  • {esc(str(iss))}")
            else:
                lines.append(f"\n<i>No issues detected.</i>")
            await send(update, "\n".join(lines), already_html=True)
        except requests.exceptions.ConnectionError:
            await send(update, "⚠️ Sorry Sir, Jarvis API is not reachable right now.")
        except Exception as e:
            log.warning("selfcheck_cmd error: %s", e)
            await send(update, "⚠️ Sorry Sir, the self-check could not be completed. Check logs for details.")

    async def probe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Usage: /probe <question> — test local AI directly"""
        from fmt import esc
        q = " ".join(context.args) if context.args else "What is your name and purpose?"
        await send(update, "🔬 Probing local brain…")
        try:
            r = requests.post(f"{API_BASE}/probe", json={"prompt": q, "model": "phi4-mini"}, headers=_ah(), timeout=30)
            d = r.json()
            txt = (
                f"<b>🔬 Local Brain Probe</b>\n"
                f"<b>Model:</b> <code>{d['model']}</code>\n"
                f"<b>Time:</b> <code>{d['duration_ms']}ms</code>\n\n"
                f"<b>Q:</b> {esc(q)}\n\n"
                f"<b>A:</b> {esc(d['response'])}"
            )
            await send(update, txt, already_html=True)
        except Exception as e:
            await send(update, f"❌ Probe failed: {e}")

    async def skills_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show Jarvis capabilities summary"""
        from fmt import esc
        await send(update, "📊 Checking Jarvis skills…")
        try:
            r = requests.get(f"{API_BASE}/skills", headers=_ah(), timeout=15)
            d = r.json()
            lines = ["<b>🧠 Jarvis Skills & Knowledge</b>\n"]
            lines.append(f"⚡ <b>Actions:</b> <code>{d.get('actions', '?')}</code>")
            lines.append(f"💾 <b>Memory chunks:</b> <code>{d.get('memory_chunks', '?')}</code>")
            lines.append(f"🤖 <b>Models loaded:</b> <code>{', '.join(d.get('models', []))}</code>")
            if d.get('collections'):
                lines.append("\n<b>📚 Collections:</b>")
                for name, cnt in d['collections'].items():
                    lines.append(f"  · {esc(name)}: <code>{cnt}</code>")
            await send(update, "\n".join(lines), already_html=True)
        except Exception as e:
            await send(update, f"❌ Skills check failed: {e}")

    async def recall_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show what Jarvis remembers about Mike"""
        from fmt import esc
        topic = " ".join(context.args) if context.args else ""
        q = f"Tell me everything you remember about Mike Samuel — his projects, goals, preferences, and background. Topic: {topic}" if topic else "Tell me everything you remember about Mike Samuel — his projects, goals, preferences, and background."
        await send(update, "🔍 Searching memory…")
        try:
            r = requests.post(f"{API_BASE}/recall", json={"topic": topic or "Mike Samuel profile preferences projects"}, headers=_ah(), timeout=20)
            d = r.json()
            chunks = d.get("chunks", [])
            if not chunks:
                await send(update, "🤷 Nothing found. Try /index to rebuild memory.")
                return
            lines = [f"<b>🧠 Memory Recall{': ' + esc(topic) if topic else ''}</b>\n"]
            for i, c in enumerate(chunks[:5], 1):
                # c may be dict with 'text','source','distance' or just a string
                if isinstance(c, dict):
                    text = str(c.get("text", c.get("document", str(c))))[:300]
                    source = c.get("source", c.get("metadata", {}).get("source", ""))
                    dist = c.get("distance", "")
                    dist_str = f" <i>(score: {round(float(dist),2)})</i>" if dist != "" else ""
                    src_str = f" <code>{esc(str(source))}</code>" if source else ""
                    lines.append(f"<b>{i}.</b>{src_str}{dist_str}\n{esc(text)}")
                else:
                    lines.append(f"<b>{i}.</b> {esc(str(c)[:300])}")
            await send(update, "\n\n".join(lines), already_html=True)
        except Exception as e:
            await send(update, f"❌ Recall failed: {esc(str(e))}", already_html=True)

    async def objectives_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show Jarvis main objectives and Mike's profile"""
        from fmt import esc
        try:
            import yaml as _yaml
            profile_path = JARVIS_HOME / "config" / "mike_profile.yaml"
            with open(profile_path) as f:
                profile = _yaml.safe_load(f)
            r_ls = requests.get(f"{API_BASE}/learning-stats", headers=_ah(), timeout=8)
            ls = r_ls.json() if r_ls.ok else {}

            identity = profile.get("identity", {})
            hardware = profile.get("hardware", {})
            projects = profile.get("projects", [])
            jarvis_stack = profile.get("jarvis_stack", {})
            routing = profile.get("model_routing", {})

            lines = ["<b>🎯 Jarvis Objectives &amp; Profile</b>\n"]
            lines.append(f"👤 <b>Owner:</b> {esc(identity.get('name', 'Mike Samuel'))} · <code>{esc(identity.get('email', ''))}</code>")
            lines.append(f"🎭 <b>Role:</b> {esc(identity.get('role', ''))}")
            lines.append(f"🕐 <b>Timezone:</b> <code>{esc(identity.get('timezone', 'Asia/Dhaka'))}</code>\n")

            lines.append(f"🖥 <b>Hardware:</b> {esc(hardware.get('cpu', ''))} · {esc(hardware.get('ram', ''))} RAM · {esc(hardware.get('gpu', ''))}")
            if hardware.get('upgrade_planned'):
                lines.append(f"⬆️ <b>Upgrade:</b> {esc(hardware['upgrade_planned'])}\n")

            if projects:
                lines.append("<b>📁 Active Projects:</b>")
                for p in projects:
                    if isinstance(p, dict):
                        lines.append(f"  · <b>{esc(p.get('name',''))}</b> — {esc(p.get('type',''))} · VPS: <code>{esc(p.get('vps',''))}</code>")

            lines.append(f"\n🤖 <b>Brain:</b> {esc(jarvis_stack.get('brain', ''))} · Fast: <code>{esc(jarvis_stack.get('fast_model',''))}</code> · Code: <code>{esc(jarvis_stack.get('code_model',''))}</code>")
            lines.append(f"📱 <b>Telegram:</b> <code>{esc(jarvis_stack.get('telegram_bot','@MikePiJarvisBot'))}</code>")

            if ls:
                total = ls.get("total", "?"); good = ls.get("good", "?"); bad = ls.get("bad", "?")
                lines.append(f"\n📊 <b>Training:</b> <code>{total}</code> interactions · 👍 <code>{good}</code> · 👎 <code>{bad}</code>")

            await send(update, "\n".join(lines), already_html=True)
        except Exception as e:
            await send(update, f"❌ Objectives failed: {esc(str(e))}", already_html=True)

    async def benchmark_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Run a quick 3-model benchmark"""
        from fmt import esc
        q = " ".join(context.args) if context.args else "Describe Jarvis in one sentence."
        await send(update, f"⏱ Benchmarking all models on: <i>{esc(q)}</i>…", already_html=True)
        models = ["phi4-mini", "qwen2.5-coder:7b", "deepseek-r1:7b"]
        lines = [f"<b>⏱ Benchmark: {esc(q)}</b>\n"]
        for model in models:
            try:
                r = requests.post(f"{API_BASE}/probe", json={"prompt": q, "model": model}, headers=_ah(), timeout=60)
                d = r.json()
                resp = esc(d.get("response", "")[:200])
                ms = d.get("duration_ms", "?")
                lines.append(f"<b>🤖 {esc(model)}</b> (<code>{ms}ms</code>)\n{resp}\n")
            except Exception as e:
                lines.append(f"<b>🤖 {esc(model)}</b> — ❌ {esc(str(e))}\n")
        await send(update, "\n".join(lines), already_html=True)

    # ── JV Titan Commands ──────────────────────────────────────────────

    async def train_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Train JV Titan with life history: /train <your story>"""
        from fmt import esc, bold
        text = " ".join(context.args).strip() if context.args else ""
        if not text:
            await send(update,
                f"🧠 {bold('JV Titan Training')}\n\n"
                "Share your life history, preferences, or plans.\n"
                "Examples:\n"
                "  <code>/train I grew up in Dhaka and love spicy food</code>\n"
                "  <code>/train My goal is to ship Starline by July</code>\n"
                "  <code>/train I prefer direct answers, no fluff</code>",
                already_html=True)
            return
        if not _HAS_TITAN:
            await send(update, "⚠️ JV Titan module not available.")
            return
        thinking = await update.message.reply_text("🧠 JV Titan is absorbing your story…")
        try:
            result = jv_titan.train_mike_input(text)
            await thinking.delete()
            lines = [
                f"✅ {bold('Training absorbed')}",
                f"",
                f"  Category: {result['category']}",
                f"  Memories: {result['memories_added']}",
                f"  Facts: {result['facts_extracted']}",
                f"  XP: +{result['xp_gained']}",
            ]
            if result.get('new_milestones'):
                lines.append(f"  🎆 Milestone: {result['new_milestones'][0][0]}")
            await send(update, "\n".join(lines), already_html=True)
        except Exception as e:
            await thinking.edit_text(f"❌ Training error: {esc(str(e))}")

    async def titan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show JV Titan consciousness, emotion, and growth status."""
        from fmt import bold, code, esc
        if not _HAS_TITAN:
            await send(update, "⚠️ JV Titan module not available.")
            return
        try:
            lines = [f"🌟 {bold('JV Titan Status')}\n"]
            # Growth
            growth = jv_titan.get_growth_state()
            lines.append(f"  {bold('Level')} {code(str(growth.get('level', 1)))} / 100")
            lines.append(f"  {bold('XP')} {code(f\"{growth.get('total_xp', 0):,}\")}")
            lines.append(f"  {bold('Milestones')} {code(str(len(growth.get('milestones_achieved', []))))}")
            # Consciousness
            cs = jv_titan.get_consciousness_state()
            lines.append(f"\n  {bold('Awareness')} {code(f\"{cs.get('awareness_level', 0):.0%}\")}")
            lines.append(f"  {bold('Interactions')} {code(str(cs.get('total_interactions', 0)))}")
            lines.append(f"  {bold('Wake cycles')} {code(str(cs.get('wake_cycles', 0)))}")
            # Emotion
            em = jv_titan.get_emotion_state()
            lines.append(f"\n  {bold('Mood')} {code(em.get('current_mood', 'calm'))}")
            lines.append(f"  {bold('Mike valence')} {code(f\"{em.get('mike_valence', 0):+.2f}\")}")
            # Decision
            tier_level, tier_name = jv_titan.get_autonomy_tier()
            lines.append(f"\n  {bold('Autonomy')} {code(f'Tier {tier_level}: {tier_name}')}")
            # Persona
            style = jv_titan.get_communication_style()
            lines.append(f"\n  {bold('Communication')} {code(style)}")
            await send(update, "\n".join(lines), already_html=True)
        except Exception as e:
            await send(update, f"❌ Titan status error: {esc(str(e))}", already_html=True)

    async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Query JV Titan memory tape: /memory [topic]"""
        from fmt import bold, code, esc
        query = " ".join(context.args).strip() if context.args else ""
        if not _HAS_TITAN:
            await send(update, "⚠️ JV Titan module not available.")
            return
        try:
            if query:
                memories = jv_titan.retrieve_memories(query=query, n=5)
                if not memories:
                    await send(update, f"🤷 No memories found for '{esc(query)}'.")
                    return
                lines = [f"🧠 {bold('Memories for')} {code(esc(query))}\n"]
                for m in memories:
                    icon = {"past": "📜", "present": "⚡", "future": "🔮"}.get(m.get("category"), "•")
                    lines.append(f"  {icon} {esc(m['content'][:120])}")
                await send(update, "\n".join(lines), already_html=True)
            else:
                summary = jv_titan.get_timeline_summary(days=7)
                await send(update, f"🧠 {bold('Recent Memory Timeline')}\n\n{esc(summary)}", already_html=True)
        except Exception as e:
            await send(update, f"❌ Memory error: {esc(str(e))}", already_html=True)

    async def evolve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Trigger JV Titan evolution check."""
        from fmt import bold, esc
        if not _HAS_TITAN:
            await send(update, "⚠️ JV Titan module not available.")
            return
        try:
            from jv_titan import growth_tracker, decision_core
            xp_result = growth_tracker.add_xp("evolution_trigger", amount=5)
            tier_level, tier_name = decision_core.advance_autonomy()
            lines = [
                f"🌟 {bold('JV Titan Evolution')}",
                f"",
                f"  Level: {xp_result['level']}",
                f"  Total XP: {xp_result['xp']:,}",
                f"  Autonomy: Tier {tier_level} ({tier_name})",
            ]
            if xp_result.get('new_milestones'):
                lines.append(f"  🎆 Milestone: {xp_result['new_milestones'][0][0]}!")
            await send(update, "\n".join(lines), already_html=True)
        except Exception as e:
            await send(update, f"❌ Evolution error: {esc(str(e))}", already_html=True)

    async def blueprint_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Export JV Titan full blueprint as binary."""
        from fmt import bold, code, esc
        if not _HAS_TITAN:
            await send(update, "⚠️ JV Titan module not available.")
            return
        try:
            blob = jv_titan.export_full_blueprint()
            size_kb = len(blob) / 1024
            await send(update,
                f"📦 {bold('JV Titan Blueprint')}\n\n"
                f"  Size: {code(f'{size_kb:.1f} KB')}\n"
                f"  Format: compressed binary (msgpack+gzip)\n"
                f"  Saved to: {code('~/.jarvis/data/jv_titan/')}",
                already_html=True)
        except Exception as e:
            await send(update, f"❌ Blueprint error: {esc(str(e))}", already_html=True)

    async def briefing_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Generate Sir's daily intelligence briefing."""
        uid = update.effective_user.id
        _cache_chat_id(uid)
        thinking_msg = await update.message.reply_text("📋 Generating briefing…")
        try:
            from thinking_engine import generate_briefing
            briefing = generate_briefing(include_web=False)
            await thinking_msg.delete()
            await send(update, briefing)
        except Exception as e:
            await thinking_msg.edit_text(f"❌ Briefing failed: {e}")

    async def think_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Deep multi-step reasoning on a question. /think <question>"""
        uid = update.effective_user.id
        _cache_chat_id(uid)
        query = " ".join(context.args).strip() if context.args else ""
        if not query:
            await send(update, "Sir, provide a question: `/think <your question>`")
            return
        thinking_msg = await update.message.reply_text("🧠 Thinking deeply…")
        try:
            from thinking_engine import think_before_answer, get_proactive_decisions, score_response
            # Get thinking context
            thinking_ctx = think_before_answer(query)
            # Run via cloud tier for deep reasoning
            import requests as req_lib
            key = _ah()
            r = req_lib.post(
                f"{API_BASE}/query",
                json={"query": f"[DEEP THINK] {query}", "context_results": 8},
                headers=key,
                timeout=120,
            )
            if r.ok:
                resp = r.json().get("response", "")
                score = score_response(query, resp)
                score_badge = "🟢" if score["total"] >= 7 else ("🟡" if score["total"] >= 5 else "🔴")
                await thinking_msg.delete()
                await send(update, f"{resp}\n\n_{score_badge} Confidence: {score['total']}/10_")
            else:
                await thinking_msg.edit_text(f"❌ Think failed: {r.status_code}")
        except Exception as e:
            await thinking_msg.edit_text(f"❌ Error: {e}")

    async def do_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Autonomous agent: /do <task> — Jarvis plans, runs safe actions, reports back."""
        uid = update.effective_user.id
        _cache_chat_id(uid)
        task = " ".join(context.args).strip() if context.args else ""
        if not task:
            await send(update, "Sir, give me a task: `/do <what you want done>`\n"
                               "Example: `/do check if the payment gateway is running`")
            return
        thinking_msg = await update.message.reply_text("🤖 On it, Sir — working…")
        try:
            r = requests.post(
                f"{API_BASE}/agent",
                json={"task": task, "max_steps": 6, "allow_destructive": True},
                headers=_ah(),
                timeout=180,
            )
            if not r.ok:
                await thinking_msg.edit_text(f"❌ Agent failed: {r.status_code}")
                return
            d = r.json()
            answer = d.get("answer", "")
            actions = d.get("actions_run", [])
            elapsed = d.get("elapsed", 0)
            needs = d.get("needs_approval")

            footer = ""
            if actions:
                footer += f"\n\n_Ran: {', '.join(actions)} · {elapsed}s_"

            # v3 permission flow: use the permission keyboard from the agent
            perm_kb = d.get("permission_keyboard")
            if needs and perm_kb:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(btn["text"], callback_data=btn["callback_data"])
                     for btn in row]
                    for row in perm_kb
                ])
                await thinking_msg.delete()
                await send(update, answer + footer, already_html=True, reply_markup=keyboard)
                return

            if needs:
                # Legacy fallback: create approval request via /action
                try:
                    ar = requests.post(
                        f"{API_BASE}/action",
                        json={"action": needs, "arg": "", "silent": True},
                        headers=_ah(), timeout=10,
                    )
                    rid = ar.json().get("request_id", "")
                    if rid:
                        footer += f"\n\n⚡ Approve to continue: `/approve {rid}`"
                except Exception:
                    pass

            await thinking_msg.delete()
            await send(update, answer + footer)
        except Exception as e:
            await thinking_msg.edit_text(f"❌ Error: {e}")

    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        _cache_chat_id(uid)
        caption = (update.message.caption or "").strip() or "Describe this image in detail."
        thinking_msg = await update.message.reply_text("🔍 Analyzing image with llava:7b...")
        try:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
            await file.download_to_drive(tmp_path)
            with open(tmp_path, "rb") as f:
                img_bytes = f.read()
            Path(tmp_path).unlink(missing_ok=True)
            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(None, analyze_image_sync, img_bytes, caption)
            add_to_history(uid, "user", f"[Image] {caption}")
            add_to_history(uid, "assistant", answer)
            _trigger_training_if_due()
            for i, part in enumerate(split_message(answer)):
                if i == 0:
                    await thinking_msg.edit_text(_to_html(part), parse_mode="HTML")
                else:
                    await update.message.reply_text(_to_html(part), parse_mode="HTML")
        except Exception as e:
            log.error("Photo analysis error: %s", e)
            await thinking_msg.edit_text("⚠️ Sorry Sir, image analysis failed. The vision model may not be loaded.")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        text = update.message.text.strip()
        if not text:
            return
        _cache_chat_id(uid)

        # Check if this message is a ForceReply response to a pending action
        if uid in _pending_action and update.message.reply_to_message:
            from executor import ACTIONS
            action = _pending_action.pop(uid)
            arg = text.strip()
            await update.message.chat.send_action("typing")
            result = run_action_via_api(action, arg=arg)
            desc = ACTIONS.get(action, {}).get("desc", action)
            await _send_action_result(update, result, desc=desc, arg=arg)
            return

        # Intercept kali tool names typed as plain text → redirect to /kali usage hint.
        # This must run BEFORE detect_action() because NL_MAP has short entries like
        # "ps" that are substrings of tool names (e.g. "ps" ⊂ "wpscan") and would
        # fire the wrong action.
        from fmt import esc as _esc
        _msg_words = text.strip().lower().split()
        if _msg_words and _msg_words[0] in _KALI_TOOL_NAMES:
            _tool = _msg_words[0]
            await send(
                update,
                f"💡 Looks like you want to run <b>{_esc(_tool)}</b>.\n"
                f"Usage: <code>/kali {_esc(_tool)} &lt;target&gt; [opts]</code>\n"
                f"Example: <code>/kali {_esc(_tool)} scanme.nmap.org</code>",
                already_html=True,
            )
            return

        # Check if it's an action command first (fast path, no LLM needed)
        from executor import detect_action, ACTIONS, AUTO, CONFIRM, APPROVE
        action = detect_action(text)
        if action:
            entry = ACTIONS[action]
            tier = entry["tier"]
            if tier == AUTO:
                await update.message.chat.send_action("typing")
                result_text = run_action_via_api(action)
                await _send_action_result(update, result_text, desc=entry.get("desc", action))
                return
            elif tier in (CONFIRM, APPROVE):
                from fmt import bold, esc
                tier_label = "🔔" if tier == CONFIRM else "🔐"
                try:
                    resp = requests.post(
                        f"{API_BASE}/action",
                        json={"action": action, "arg": "", "silent": True},
                        headers=_ah(),
                        timeout=30,
                    )
                    d = resp.json()
                    req_id = d.get("request_id", "")
                    body = (
                        f"{tier_label} <b>Action request</b> — <code>{req_id}</code>\n"
                        f"{esc(entry['desc'])}"
                    )
                    keyboard = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✅ Approve",  callback_data=f"approve_{req_id}"),
                            InlineKeyboardButton("❌ Deny",     callback_data=f"deny_{req_id}"),
                        ]
                    ])
                    await send(update, body, already_html=True, reply_markup=keyboard)
                except Exception:
                    await send(update, run_action_via_api(action))
                return

        # Fix 3: Short unrecognised plain-text messages with no clear intent →
        # respond with a helpful nudge rather than guessing and firing a wrong action.
        # v3 update: let greetings and direct addresses through to the agent.
        _QUERY_WORDS = {"what", "why", "how", "where", "when", "who", "which", "is", "are",
                        "can", "could", "should", "does", "do", "will", "show", "tell", "?"}
        _GREETING_WORDS = {"hello", "hi", "hey", "good", "morning", "evening", "night",
                           "jarvis", "thanks", "thank", "ok", "okay", "yes", "no", "great",
                           "awesome", "nice", "cool", "welcome", "bye", "goodbye"}
        _words = set(text.lower().split())
        if (
            not action
            and len(text.split()) <= 2
            and "?" not in text
            and not _words & _QUERY_WORDS
            and not _words & _GREETING_WORDS
        ):
            await send(update,
                "I didn't catch that, Sir. Try a question, a /command, or /actions for the full list.")
            return

        # Web search auto-detection — check before LLM call
        from web_search import is_web_query, web_answer
        from fmt import bold, italic, link, esc
        if is_web_query(text):
            thinking_msg = await update.message.reply_text("🔍 Searching the web, Sir…")
            try:
                answer, sources = web_answer(text, use_cloud=True)
                add_to_history(uid, "user", text)
                add_to_history(uid, "assistant", answer)
                # Edit thinking msg with AI summary
                summary_html = _to_html(answer)
                await thinking_msg.edit_text(summary_html, parse_mode="HTML")
                # Source cards
                if sources:
                    cards = f"🔗 {bold('Sources')}\n"
                    for i, s in enumerate(sources[:4], 1):
                        title_s = s.get("title", f"Source {i}") or f"Source {i}"
                        url     = s.get("url", "")
                        snip    = (s.get("snippet") or s.get("full_text") or "").strip()
                        date    = s.get("date", "")[:10]
                        src     = s.get("source", "")
                        meta    = " · ".join(filter(None, [
                            (f"📅 {date}" if date else ""),
                            (f"🌐 {src}" if src else ""),
                        ]))
                        # Truncate title at word boundary (≤100 chars)
                        if len(title_s) > 100:
                            title_s = title_s[:100].rsplit(" ", 1)[0] + "…"
                        header  = bold(link(esc(title_s), url)) if url else bold(esc(title_s))
                        card_lines = [f"\n{header}"]
                        if meta:
                            card_lines.append(esc(meta))
                        if snip:
                            card_lines.append(esc(snip[:150]))
                        cards += "\n".join(card_lines)
                    await update.message.reply_text(cards.strip(), parse_mode="HTML",
                        disable_web_page_preview=True)
                # Image thumbnail
                for s in sources[:5]:
                    img = s.get("image", "")
                    if img and img.startswith("http"):
                        try:
                            await context.bot.send_photo(
                                chat_id=update.effective_chat.id,
                                photo=img,
                                caption=esc(s.get("title", "")[:200]),
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass
                        break
            except requests.exceptions.ConnectionError:
                await thinking_msg.edit_text("⚠️ Sorry Sir, the web search service is not reachable right now.")
            except Exception as e:
                log.warning("handle_message web search error: %s", e)
                await thinking_msg.edit_text("⚠️ Sorry Sir, the web search encountered an error. Please try again.")
            return

        # LLM query — send a placeholder first so user sees immediate feedback
        _trigger_training_if_due()
        thinking_msg = await update.message.reply_text("⏳")
        try:
            response, iid = query_jarvis(uid, text)
            if iid:
                _last_interaction[uid] = iid
            html_response = _to_html(response)
            parts = split_message(html_response)
            for i, part in enumerate(parts):
                try:
                    if i == 0:
                        await thinking_msg.edit_text(part, parse_mode="HTML")
                    else:
                        await update.message.reply_text(part, parse_mode="HTML")
                except Exception:
                    # Fallback: strip ALL HTML tags and send as plain text
                    plain = re.sub(r"<[^>]+>", "", part).strip()
                    if i == 0:
                        await thinking_msg.edit_text(plain or "…")
                    else:
                        await update.message.reply_text(plain or "…")
            # Show feedback buttons after the last part (only for logged interactions)
            if iid:
                await update.message.reply_text(
                    "Rate this response:",
                    reply_markup=_feedback_keyboard(iid),
                )
            # Send voice response if voice mode is on
            if _is_voice_on(uid) and response and not response.startswith("Error"):
                import asyncio as _asyncio
                _asyncio.get_event_loop().run_in_executor(None, _send_voice_for_response, response)
        except Exception as e:
            log.warning("handle_message LLM error: %s", e)
            await thinking_msg.edit_text("⚠️ Sorry Sir, something went wrong. Please try again.")

    async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
        log.error(f"Telegram error: {context.error}")

    app_bot = Application.builder().token(token).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("help", help_cmd))
    app_bot.add_handler(CommandHandler("stats", stats_cmd))
    app_bot.add_handler(CommandHandler("health", health_cmd))
    app_bot.add_handler(CommandHandler("sysinfo", sysinfo_cmd))
    app_bot.add_handler(CommandHandler("systeminfo", sysinfo_cmd))
    app_bot.add_handler(CommandHandler("index", index_cmd))
    app_bot.add_handler(CommandHandler("clear", clear_cmd))
    app_bot.add_handler(CommandHandler("new", new_cmd))
    app_bot.add_handler(CommandHandler("remember", remember_cmd))
    app_bot.add_handler(CommandHandler("voice", voice_cmd))
    app_bot.add_handler(CommandHandler("correct", correct_cmd))
    app_bot.add_handler(CommandHandler("learn", learn_cmd))
    app_bot.add_handler(CommandHandler("selfcheck", selfcheck_cmd))
    app_bot.add_handler(CommandHandler("actions", actions_cmd))
    app_bot.add_handler(CommandHandler("pending", pending_cmd))
    app_bot.add_handler(CommandHandler("approve", approve_cmd))
    app_bot.add_handler(CommandHandler("deny", deny_cmd))
    app_bot.add_handler(CommandHandler("task", task_cmd))
    app_bot.add_handler(CommandHandler("orchestrate", orchestrate_cmd))
    app_bot.add_handler(CommandHandler("exec", exec_cmd))
    app_bot.add_handler(CommandHandler("web", web_cmd))
    app_bot.add_handler(CommandHandler("search", web_cmd))
    app_bot.add_handler(CommandHandler("weather", weather_cmd))
    app_bot.add_handler(CommandHandler("browse", browse_cmd))
    app_bot.add_handler(CommandHandler("oc", oc_cmd))
    app_bot.add_handler(CommandHandler("openclaw", oc_cmd))
    app_bot.add_handler(CommandHandler("kali", kali_cmd))
    app_bot.add_handler(CommandHandler("scans", scans_cmd))
    app_bot.add_handler(CommandHandler("probe",      probe_cmd))
    app_bot.add_handler(CommandHandler("skills",     skills_cmd))
    app_bot.add_handler(CommandHandler("recall",     recall_cmd))
    app_bot.add_handler(CommandHandler("objectives", objectives_cmd))
    app_bot.add_handler(CommandHandler("briefing",   briefing_cmd))
    app_bot.add_handler(CommandHandler("think",      think_cmd))
    app_bot.add_handler(CommandHandler("do",         do_cmd))
    app_bot.add_handler(CommandHandler("benchmark",  benchmark_cmd))
    # JV Titan commands
    app_bot.add_handler(CommandHandler("train",     train_cmd))
    app_bot.add_handler(CommandHandler("titan",     titan_cmd))
    app_bot.add_handler(CommandHandler("memory",    memory_cmd))
    app_bot.add_handler(CommandHandler("evolve",    evolve_cmd))
    app_bot.add_handler(CommandHandler("blueprint", blueprint_cmd))
    app_bot.add_handler(CallbackQueryHandler(button_callback))
    app_bot.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app_bot.add_error_handler(error_handler)

    # Register command menu with Telegram (shows in the / menu)
    from telegram import BotCommand
    _BOT_COMMANDS = [
        BotCommand("help",      "Show all commands"),
        BotCommand("sysinfo",   "Live CPU / RAM / GPU / Disk"),
        BotCommand("health",    "Service health check"),
        BotCommand("selfcheck", "Full system self-check"),
        BotCommand("stats",     "Brain & memory stats"),
        BotCommand("web",       "Real-time web search + AI answer"),
        BotCommand("weather",   "Current weather"),
        BotCommand("browse",    "Open a URL in headless browser"),
        BotCommand("exec",      "Smart action dispatch"),
        BotCommand("task",      "Delegate task to Claude Code"),
        BotCommand("actions",   "List available actions"),
        BotCommand("pending",   "Show pending approvals"),
        BotCommand("kali",      "Run a Kali pentest tool"),
        BotCommand("scans",     "List recent scan reports"),
        BotCommand("correct",   "Correct last answer (trains brain)"),
        BotCommand("learn",     "Run brain training now"),
        BotCommand("index",     "Re-index your work"),
        BotCommand("remember",  "Save a personal fact"),
        BotCommand("voice",     "Toggle voice audio responses"),
        BotCommand("new",       "Fresh session — clear history, ready for new instructions"),
        BotCommand("clear",     "Clear chat history"),
        BotCommand("approve",    "Approve a pending action by ID"),
        BotCommand("deny",       "Deny a pending action by ID"),
        BotCommand("probe",      "Test local AI with a question"),
        BotCommand("skills",     "Show Jarvis skills and memory stats"),
        BotCommand("recall",     "What Jarvis remembers about you"),
        BotCommand("objectives", "Jarvis main goals and your profile"),
        BotCommand("benchmark",  "Benchmark all 3 local AI models"),
        BotCommand("train",      "Train JV Titan with your life story"),
        BotCommand("titan",      "JV Titan consciousness & growth status"),
        BotCommand("memory",     "Query JV Titan memory tape"),
        BotCommand("evolve",     "Trigger JV Titan evolution"),
        BotCommand("blueprint",  "Export JV Titan binary blueprint"),
    ]

    async def _post_init(application):
        await application.bot.set_my_commands(_BOT_COMMANDS)
        log.info("Bot command menu registered (%d commands)", len(_BOT_COMMANDS))

    app_bot.post_init = _post_init

    log.info(f"Jarvis Telegram bot starting (API: {API_BASE})")
    app_bot.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
