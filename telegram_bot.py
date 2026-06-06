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

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
TOKEN_FILE = JARVIS_HOME / "config" / "telegram.json"
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis.yaml"
LOG_FILE = JARVIS_HOME / "logs" / "telegram_bot.log"
HISTORY_FILE = JARVIS_HOME / "data" / "telegram_history.json"

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
    if not TOKEN_FILE.exists():
        log.error(f"Token file not found: {TOKEN_FILE}")
        sys.exit(1)
    with open(TOKEN_FILE) as f:
        data = json.load(f)
    token = data.get("bot_token", "")
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        log.error("Bot token not set in ~/.jarvis/config/telegram.json")
        sys.exit(1)
    return token


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


def _to_legacy_markdown(text: str) -> str:
    # Telegram legacy Markdown uses single * for bold; **bold** 400s.
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
    payload = {
        "query": text,
        "context_results": 5,
        "history": history,
    }

    # Route through Claude planner for non-casual messages that may need actions.
    # Casual chat goes straight to /query (faster, no Claude overhead).
    endpoint = "/query" if _is_casual(text) else "/claude-plan"

    try:
        resp = requests.post(f"{API_BASE}{endpoint}", json=payload, timeout=180)
        # Retry once on 503 — model may still be loading
        if resp.status_code == 503:
            import time
            time.sleep(8)
            resp = requests.post(f"{API_BASE}{endpoint}", json=payload, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        answer = data.get("response", "No response received.")
        iid = data.get("interaction_id")
        add_to_history(uid, "user", text)
        add_to_history(uid, "assistant", answer)
        return answer, iid
    except requests.exceptions.ConnectionError:
        return "Jarvis API is not running. Try: sudo systemctl start jarvis", None
    except requests.exceptions.Timeout:
        return "Jarvis took too long to respond. The model may be busy.", None
    except Exception as e:
        return f"Error: {e}", None


def _bar(pct: float, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def get_stats() -> str:
    """Jarvis brain stats card."""
    try:
        resp = requests.get(f"{API_BASE}/stats", timeout=10)
        resp.raise_for_status()
        d = resp.json()
        chunks = d.get("memory_chunks", 0)
        model = d.get("primary_model", "N/A")
        breakdown = d.get("source_type_breakdown", {})
        top = sorted(breakdown.items(), key=lambda x: -x[1])
        src_lines = "  ".join(f"`{k}:{v}`" for k, v in top)
        return (
            "*Jarvis Brain*\n"
            f"Model: `{model}`\n"
            f"Memory: `{chunks:,}` chunks\n"
            f"Sources: {src_lines}"
        )
    except Exception as e:
        return f"Stats unavailable: {e}"


def get_health() -> str:
    try:
        resp = requests.get(f"{API_BASE}/health", timeout=10)
        resp.raise_for_status()
        d = resp.json()
        ok = d.get("status") == "healthy"
        icon = "✅" if ok else "⚠️"
        return (
            f"{icon} *Jarvis* — {d.get('status', 'unknown')}\n"
            f"Ollama: `{d.get('ollama')}` | Memory: `{d.get('memory_chunks', 0):,}` chunks\n"
            f"Model: `{d.get('model')}`"
        )
    except Exception as e:
        return f"Health check failed: {e}"


def get_sysinfo() -> str:
    """Full system stats card — minimal, one screen."""
    try:
        si = requests.get(f"{API_BASE}/sysinfo", timeout=15).json()
        st = requests.get(f"{API_BASE}/stats", timeout=10).json()
        hl = requests.get(f"{API_BASE}/health", timeout=10).json()

        cpu = si.get("cpu", {})
        ram = si.get("ram", {})
        disk = si.get("disk", {})
        gpu = si.get("gpu", {})
        net = si.get("network", {})
        osinfo = si.get("os", {})
        jarvis = si.get("jarvis", {})

        svc_ok = hl.get("status") == "healthy"
        svc_icon = "✅" if svc_ok else "⚠️"

        chunks = st.get("memory_chunks", 0)
        model = st.get("primary_model", "N/A")
        breakdown = st.get("source_type_breakdown", {})
        top2 = sorted(breakdown.items(), key=lambda x: -x[1])[:2]
        src = "  ".join(f"`{k}:{v}`" for k, v in top2)

        lines = [
            f"*Jarvis Stats* {svc_icon}",
            "",
            "*Brain*",
            f"  Model: `{model}`  Memory: `{chunks:,}` chunks",
            f"  {src}",
            "",
            "*Hardware*",
        ]

        if cpu:
            lines.append(
                f"  CPU `{cpu.get('usage_percent', '?')}%` {_bar(cpu.get('usage_percent', 0))}  "
                f"`{cpu.get('cores_physical', '?')}c/{cpu.get('cores_logical', '?')}t`"
            )
        if ram:
            lines.append(
                f"  RAM `{ram.get('used_gb', '?')}/{ram.get('total_gb', '?')} GB` "
                f"{_bar(ram.get('usage_percent', 0))}"
            )
        if disk:
            lines.append(
                f"  Disk `{disk.get('used_gb', '?')}/{disk.get('total_gb', '?')} GB` "
                f"{_bar(disk.get('usage_percent', 0))}"
            )
        if gpu:
            vram_pct = round(gpu.get("vram_used_mb", 0) / max(gpu.get("vram_total_mb", 1), 1) * 100)
            lines.append(
                f"  GPU `{gpu.get('utilization_percent', '?')}%` {_bar(gpu.get('utilization_percent', 0))}  "
                f"VRAM `{gpu.get('vram_used_mb', '?')}/{gpu.get('vram_total_mb', '?')} MB`  "
                f"`{gpu.get('temp_c', '?')}°C`"
            )

        if net:
            ts = net.get("tailscale0", "")
            local = net.get("eth0") or net.get("wlan0", "")
            net_line = "  "
            if local:
                net_line += f"LAN `{local}`"
            if ts:
                net_line += f"  VPN `{ts}`"
            if net_line.strip():
                lines += ["", "*Network*", net_line]

        uptime = osinfo.get("uptime_hours", "?")
        lines += ["", f"_Uptime {uptime}h — {osinfo.get('hostname', 'kali')}_"]

        return "\n".join(lines)
    except Exception as e:
        return f"Could not fetch stats: {e}"


def trigger_index() -> str:
    try:
        resp = requests.post(f"{API_BASE}/index", json={}, timeout=300)
        resp.raise_for_status()
        d = resp.json()
        return f"Indexing complete. Total chunks: {d.get('chunks', 'N/A')}"
    except Exception as e:
        return f"Indexing error: {e}"


HELP_TEXT = (
    "Jarvis Commands:\n"
    "/start — Welcome\n"
    "/help — This help\n"
    "/stats — Memory stats\n"
    "/sysinfo — Live system info\n"
    "/health — Service health\n"
    "/index — Re-index your work\n"
    "/clear — Clear chat history\n"
    "/remember KEY VALUE — Save a personal fact\n"
    "/voice on|off — Toggle voice audio responses\n"
    "/correct TEXT — Correct Jarvis's last answer (trains brain)\n"
    "/learn — Run brain training now\n"
    "/selfcheck — Full system self-check + health report\n"
    "/approve ID — Approve a pending action\n"
    "/deny ID — Deny a pending action\n"
    "/actions — List available actions\n"
    "/pending — Show pending approvals\n"
    "/task DESC — Delegate task to Claude Code (background, results here)\n"
    "/exec CMD — Smart dispatch: detect action or plan + execute\n\n"
    "After each response, tap 👍 or 👎 to train Jarvis.\n\n"
    "Actions (type naturally):\n"
    "  restart jarvis | restart bot | restart ollama\n"
    "  show processes | disk space | memory usage\n"
    "  gpu status | show logs | tailscale status\n\n"
    "Or just chat — Jarvis remembers your conversation."
)


def split_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        parts.append(text[:limit])
        text = text[limit:]
    return parts


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
    lines, out = raw.splitlines(), []
    for line in lines:
        l = line.strip()
        if any(k in l for k in ("Driver", "CUDA", "GPU 0", "MiB", "WAT", "%", "Temp")):
            out.append(l)
    return "\n".join(out[:12]) if out else raw


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
            json={"action": action, "arg": arg},
            timeout=90,
        )
        d = resp.json()
        if d.get("status") == "pending":
            return f"⏳ Approval required (ID: `{d['request_id']}`)\nReply /approve {d['request_id']} or /deny {d['request_id']}"
        output = d.get("output", "")
        ok = d.get("success", True)
        icon = "✅" if ok else "❌"
        if not output:
            return f"{icon} Done."
        return f"{icon} {_format_action_output(action, output)}"
    except Exception as e:
        return f"Action error: {e}"


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

    async def send(update: Update, text: str):
        for part in split_message(text):
            await update.message.reply_text(_to_legacy_markdown(part), parse_mode="Markdown")

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        _cache_chat_id(uid)
        _histories[uid] = deque(maxlen=MAX_HISTORY)
        await send(update, f"Jarvis online. I'm {OWNER}'s personal AI assistant.\n\n{HELP_TEXT}")

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, HELP_TEXT)

    async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, get_stats())

    async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, get_health())

    async def sysinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.chat.send_action("typing")
        await send(update, get_sysinfo())

    async def index_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, "Indexing started, this may take a few minutes...")
        await send(update, trigger_index())

    async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        _histories[uid] = deque(maxlen=MAX_HISTORY)
        _save_histories()
        await send(update, "Conversation history cleared.")

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
        await send(update, f"Remembered: *{key}* = {val}")

    async def actions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from executor import ACTIONS, AUTO, CONFIRM, APPROVE
        lines = ["Available actions:\n"]
        for tier, label in [(AUTO, "⚡ Auto"), (CONFIRM, "🔔 Confirm"), (APPROVE, "🔐 Approve")]:
            items = [f"  `{k}` — {v['desc']}" for k, v in ACTIONS.items() if v["tier"] == tier]
            if items:
                lines.append(f"*{label}*")
                lines.extend(items)
        await send(update, "\n".join(lines))

    async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from permissions import list_pending
        pending = list_pending()
        if not pending:
            await send(update, "No pending approvals.")
            return
        lines = ["Pending approvals:"]
        for req in pending:
            lines.append(f"  `{req['id']}` — {req['description']}")
        await send(update, "\n".join(lines))

    async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        if not args:
            await send(update, "Usage: /approve REQUEST_ID")
            return
        req_id = args[0].upper()
        await update.message.chat.send_action("typing")
        try:
            resp = requests.post(f"{API_BASE}/approve/{req_id}", timeout=90)
            d = resp.json()
            output = d.get("output", "")
            ok = d.get("success", True)
            icon = "✅" if ok else "❌"
            reply = f"{icon} Executed\n```\n{output[:2000]}\n```" if output else f"{icon} Done."
            await send(update, reply)
        except Exception as e:
            await send(update, f"Error: {e}")

    async def deny_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        if not args:
            await send(update, "Usage: /deny REQUEST_ID")
            return
        req_id = args[0].upper()
        try:
            requests.post(f"{API_BASE}/deny/{req_id}", timeout=10)
            await send(update, f"❌ Denied: {req_id}")
        except Exception as e:
            await send(update, f"Error: {e}")

    async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
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
                resp = requests.post(f"{API_BASE}/approve/{req_id}", timeout=90)
                d = resp.json()
                output = d.get("output", "")
                ok = d.get("success", True)
                icon = "✅" if ok else "❌"
                reply = f"{icon} Done\n```\n{output[:1500]}\n```" if output else f"{icon} Done."
                await query.edit_message_text(reply, parse_mode="Markdown")
            except Exception as e:
                await query.edit_message_text(f"Error: {e}")
        elif data.startswith("deny_"):
            req_id = data.split("_", 1)[1]
            try:
                requests.post(f"{API_BASE}/deny/{req_id}", timeout=10)
                await query.edit_message_text(f"❌ Denied")
            except Exception as e:
                await query.edit_message_text(f"Error: {e}")

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
                    d = requests.get(f"{API_BASE}/selfcheck", timeout=20).json()
                    overall = d.get("overall", "unknown")
                    icon = "✅" if overall == "ok" else "⚠️"
                    lines = [f"{icon} *Jarvis Self-Check* — {overall.upper()}\n"]
                    for k, v in d.get("checks", {}).items():
                        ci = "✅" if str(v) in ("ok", "True") else ("⚠️" if str(v).isdigit() else "❌")
                        lines.append(f"{ci} `{k}`: {v}")
                    reply = "\n".join(lines)
                except Exception as e:
                    reply = f"Self-check failed: {e}"
            elif cmd == "learn":
                try:
                    requests.post(f"{API_BASE}/learn", timeout=10)
                    reply = "🧠 Brain training started. Check /stats in a minute."
                except Exception as e:
                    reply = f"Error: {e}"
            elif cmd == "index":
                reply = "🔄 Indexing started..."
                await query.message.reply_text(reply)
                reply = trigger_index()
            elif cmd == "actions":
                from executor import ACTIONS, AUTO, CONFIRM, APPROVE
                auto = [k for k, v in ACTIONS.items() if v["tier"] == AUTO]
                conf = [k for k, v in ACTIONS.items() if v["tier"] == CONFIRM]
                appr = [k for k, v in ACTIONS.items() if v["tier"] == APPROVE]
                reply = (f"*Actions ({len(ACTIONS)} total)*\n\n"
                         f"*⚡ AUTO ({len(auto)})*\n" + "  ".join(f"`{a}`" for a in auto) + "\n\n"
                         f"*🔔 CONFIRM ({len(conf)})*\n" + "  ".join(f"`{a}`" for a in conf) + "\n\n"
                         f"*🔐 APPROVE ({len(appr)})*\n" + "  ".join(f"`{a}`" for a in appr))
            elif cmd == "pending":
                try:
                    d = requests.get(f"{API_BASE}/pending", timeout=10).json() if hasattr(requests, 'x') else None
                    import json as _j, pathlib as _p
                    pf = _p.Path("/home/kali/.jarvis/data/pending_approvals.json")
                    pending = _j.loads(pf.read_text()) if pf.exists() else {}
                    if pending:
                        lines = ["*Pending Approvals*\n"]
                        for rid, req in pending.items():
                            lines.append(f"• `{rid}` — {req.get('action', '?')}")
                        reply = "\n".join(lines)
                    else:
                        reply = "No pending approvals."
                except Exception as e:
                    reply = f"Error: {e}"
            else:
                reply = f"Unknown command: {cmd}"
            await query.message.reply_text(_to_legacy_markdown(reply), parse_mode="Markdown")

        elif data.startswith("act_"):
            action = data[4:]
            await query.message.chat.send_action("typing")
            result = run_action_via_api(action)
            await query.message.reply_text(_to_legacy_markdown(result), parse_mode="Markdown")

    def _build_reference_keyboard() -> "InlineKeyboardMarkup":
        B = InlineKeyboardButton
        return InlineKeyboardMarkup([
            # ── Commands ─────────────────────────────────────────────────────
            [B("📊 Stats",    callback_data="cmd_stats"),
             B("🖥 Sysinfo",  callback_data="cmd_sysinfo"),
             B("❤️ Health",   callback_data="cmd_health"),
             B("🔍 Selfcheck",callback_data="cmd_selfcheck")],
            [B("📋 Actions",  callback_data="cmd_actions"),
             B("⏳ Pending",  callback_data="cmd_pending"),
             B("🧠 Learn",    callback_data="cmd_learn"),
             B("🔄 Index",    callback_data="cmd_index")],
            # ── AUTO: local system ────────────────────────────────────────────
            [B("ps",     callback_data="act_ps"),
             B("disk",   callback_data="act_disk"),
             B("memory", callback_data="act_memory"),
             B("uptime", callback_data="act_uptime")],
            [B("gpu",      callback_data="act_gpu"),
             B("services", callback_data="act_services"),
             B("ports",    callback_data="act_ports"),
             B("network",  callback_data="act_network")],
            [B("top5 cpu",    callback_data="act_top5_cpu"),
             B("top5 mem",    callback_data="act_top5_mem"),
             B("tailscale",   callback_data="act_tailscale"),
             B("AI models",   callback_data="act_ollama_models")],
            [B("git status",     callback_data="act_git_status_all"),
             B("nginx",          callback_data="act_nginx_status"),
             B("pm2",            callback_data="act_pm2_status"),
             B("jarvis logs",    callback_data="act_jarvis_logs_tail")],
            # ── AUTO: projects ────────────────────────────────────────────────
            [B("project status", callback_data="act_project_status"),
             B("docker ps",      callback_data="act_docker_ps"),
             B("vps disk",       callback_data="act_vps_disk"),
             B("vps ps",         callback_data="act_vps_ps")],
            [B("gw logs",   callback_data="act_pm2_logs_gateway"),
             B("sl logs",   callback_data="act_pm2_logs_starline"),
             B("git log gw",callback_data="act_git_log_gw"),
             B("git log sl",callback_data="act_git_log_sl")],
            # ── CONFIRM ───────────────────────────────────────────────────────
            [B("🔔 restart jarvis",    callback_data="act_restart_jarvis"),
             B("🔔 restart telegram",  callback_data="act_restart_telegram"),
             B("🔔 restart ollama",    callback_data="act_restart_ollama")],
            [B("🔔 reindex",           callback_data="act_reindex"),
             B("🔔 pull gw",           callback_data="act_git_pull_gw"),
             B("🔔 pull sl",           callback_data="act_git_pull_sl")],
            [B("🔔 build gw",          callback_data="act_npm_build_gw"),
             B("🔔 build sl",          callback_data="act_pnpm_build_sl"),
             B("🔔 vps pull gw",       callback_data="act_vps_git_pull_gw")],
            # ── APPROVE ───────────────────────────────────────────────────────
            [B("🔐 deploy vps",  callback_data="act_deploy_vps"),
             B("🔐 reboot",      callback_data="act_reboot"),
             B("🔐 update sys",  callback_data="act_update_system")],
        ])

    def _feedback_keyboard(iid: str) -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("👍", callback_data=f"good_{iid}"),
            InlineKeyboardButton("👎", callback_data=f"bad_{iid}"),
        ]])

    def _send_feedback(iid: str, rating: str) -> bool:
        try:
            r = requests.post(f"{API_BASE}/feedback", json={"interaction_id": iid, "rating": rating}, timeout=5)
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
            r = requests.post(f"{API_BASE}/correct", json={"interaction_id": iid, "correction": correction}, timeout=5)
            if r.ok:
                await send(update, f"Correction saved. Jarvis will learn from this.")
            else:
                await send(update, "Could not save correction.")
        except Exception as e:
            await send(update, f"Error: {e}")

    async def learn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, "Running brain training in background...")
        try:
            r = requests.post(f"{API_BASE}/learn", timeout=10)
            if r.ok:
                await send(update, "Brain training started. Check /stats in a minute.")
            else:
                await send(update, f"Error: {r.text}")
        except Exception as e:
            await send(update, f"Error: {e}")

    async def task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Delegate a task to Claude Code (background, results via Telegram)."""
        task = " ".join(context.args).strip() if context.args else ""
        if not task:
            await send(update, "Usage: /task <description>\nExample: /task fix the typo in gateway-admin index.js line 42")
            return
        await send(update, f"🤖 Queuing Claude Code task:\n`{task}`\n\nApproval required to run.")
        try:
            resp = requests.post(f"{API_BASE}/action", json={"action": "claude_task", "arg": task}, timeout=10)
            d = resp.json()
            req_id = d.get("request_id", "")
            if req_id:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Run Task", callback_data=f"approve_{req_id}"),
                    InlineKeyboardButton("❌ Cancel",   callback_data=f"deny_{req_id}"),
                ]])
                await update.message.reply_text(
                    f"🔐 *Claude Task* (ID: `{req_id}`)\n_{task[:200]}_\n\nResults sent here when complete.",
                    reply_markup=keyboard,
                    parse_mode="Markdown",
                )
            else:
                await send(update, d.get("output", "Task queued."))
        except Exception as e:
            await send(update, f"Error queuing task: {e}")

    async def exec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Smart action dispatch — detect action or plan via claude-plan."""
        query_text = " ".join(context.args).strip() if context.args else ""
        if not query_text:
            await send(update, "Usage: /exec <natural language command>\nExample: /exec check VPS status and restart gateway if down")
            return
        await update.message.chat.send_action("typing")
        try:
            resp = requests.post(f"{API_BASE}/claude-plan",
                                 json={"query": query_text}, timeout=60)
            d = resp.json()
            reply = d.get("response", "No response.")
            await send(update, reply)
        except Exception as e:
            await send(update, f"Exec error: {e}")

    async def web_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Real-time web search + AI analysis. Usage: /web <query>"""
        query_text = " ".join(context.args).strip() if context.args else ""
        if not query_text:
            await send(update, "Usage: /web <query>\nExample: /web latest bitcoin price")
            return
        thinking_msg = await update.message.reply_text("🔍 Searching the web, Sir...")
        try:
            from web_search import web_answer
            answer, sources = web_answer(query_text, use_cloud=True)
            if sources and "http" not in answer:
                links = "\n".join(f"[{i+1}] {s['url']}" for i, s in enumerate(sources[:2]) if s['url'])
                answer = answer + "\n\n" + links
            await thinking_msg.edit_text(_to_legacy_markdown(answer), parse_mode="Markdown")
        except Exception as e:
            await thinking_msg.edit_text(f"Web search error: {e}")

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
            from browser.agent import BrowserAgent
            agent = BrowserAgent.get()
            page_text = agent.open_url(url)
            title = agent.get_title()

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
            await thinking_msg.edit_text(_to_legacy_markdown(summary), parse_mode="Markdown")

            if want_shot:
                png = agent.screenshot()
                await update.message.reply_photo(photo=png, caption=f"📸 {title[:80]}")
        except Exception as e:
            await thinking_msg.edit_text(f"Browse error: {e}")

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
                out = res.get("output", "") or "Done."
                await send(update, f"✅ *{tool}*\n{out[:3500]}")
            else:
                await send(update, f"❌ {tool}: {res.get('reason', 'failed')}")
        except Exception as e:
            await send(update, f"OpenClaw error: {e}")

    async def selfcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send(update, "Running full system self-check...")
        try:
            r = requests.get(f"{API_BASE}/selfcheck", timeout=20)
            r.raise_for_status()
            d = r.json()
            overall = d.get("overall", "unknown")
            emoji = "✅" if overall == "ok" else "⚠️"
            lines = [f"{emoji} *Jarvis Self-Check* — {overall.upper()}\n"]
            for k, v in d.get("checks", {}).items():
                icon = "✅" if str(v) in ("ok", "True") else ("⚠️" if str(v).isdigit() else "❌")
                lines.append(f"{icon} `{k}`: {v}")
            issues = d.get("issues", [])
            if issues:
                lines.append("\n*Issues:*")
                for iss in issues:
                    lines.append(f"  • {iss}")
            await send(update, "\n".join(lines))
        except Exception as e:
            await send(update, f"Self-check failed: {e}")

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
                    await thinking_msg.edit_text(_to_legacy_markdown(part), parse_mode="Markdown")
                else:
                    await update.message.reply_text(_to_legacy_markdown(part), parse_mode="Markdown")
        except Exception as e:
            log.error(f"Photo analysis error: {e}")
            await thinking_msg.edit_text(f"Image analysis failed: {e}")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        text = update.message.text.strip()
        if not text:
            return
        _cache_chat_id(uid)

        # Check if it's an action command first (fast path, no LLM needed)
        from executor import detect_action, ACTIONS, AUTO, CONFIRM, APPROVE
        action = detect_action(text)
        if action:
            entry = ACTIONS[action]
            tier = entry["tier"]
            if tier == AUTO:
                await update.message.chat.send_action("typing")
                result_text = run_action_via_api(action)
                await send(update, result_text)
                return
            elif tier in (CONFIRM, APPROVE):
                tier_label = "⚡" if tier == CONFIRM else "🔐"
                msg = f"{tier_label} *{entry['desc']}*\n\nConfirm?"
                try:
                    resp = requests.post(
                        f"{API_BASE}/action",
                        json={"action": action, "arg": ""},
                        timeout=10,
                    )
                    d = resp.json()
                    req_id = d.get("request_id", "")
                    keyboard = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("✅ Yes", callback_data=f"approve_{req_id}"),
                            InlineKeyboardButton("❌ No",  callback_data=f"deny_{req_id}"),
                        ]
                    ])
                    await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")
                except Exception:
                    await send(update, run_action_via_api(action))
                return

        # Web search auto-detection — check before LLM call
        from web_search import is_web_query, web_answer
        if is_web_query(text):
            thinking_msg = await update.message.reply_text("🔍 Searching the web, Sir...")
            try:
                answer, sources = web_answer(text, use_cloud=True)
                if sources and "http" not in answer:
                    links = "\n".join(f"[{i+1}] {s['url']}" for i, s in enumerate(sources[:2]) if s['url'])
                    answer = answer + "\n\n" + links
                add_to_history(uid, "user", text)
                add_to_history(uid, "assistant", answer)
                parts = split_message(answer)
                for i, part in enumerate(parts):
                    if i == 0:
                        await thinking_msg.edit_text(_to_legacy_markdown(part), parse_mode="Markdown")
                    else:
                        await update.message.reply_text(_to_legacy_markdown(part), parse_mode="Markdown")
            except Exception as e:
                await thinking_msg.edit_text(f"Web search error: {e}")
            return

        # LLM query — send a placeholder first so user sees immediate feedback
        _trigger_training_if_due()
        thinking_msg = await update.message.reply_text("⏳")
        try:
            response, iid = query_jarvis(uid, text)
            if iid:
                _last_interaction[uid] = iid
            parts = split_message(response)
            for i, part in enumerate(parts):
                if i == 0:
                    await thinking_msg.edit_text(_to_legacy_markdown(part), parse_mode="Markdown")
                else:
                    await update.message.reply_text(_to_legacy_markdown(part), parse_mode="Markdown")
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
            await thinking_msg.edit_text(f"Error: {e}")

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
    app_bot.add_handler(CommandHandler("exec", exec_cmd))
    app_bot.add_handler(CommandHandler("web", web_cmd))
    app_bot.add_handler(CommandHandler("search", web_cmd))
    app_bot.add_handler(CommandHandler("browse", browse_cmd))
    app_bot.add_handler(CommandHandler("oc", oc_cmd))
    app_bot.add_handler(CommandHandler("openclaw", oc_cmd))
    app_bot.add_handler(CallbackQueryHandler(button_callback))
    app_bot.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app_bot.add_error_handler(error_handler)

    log.info(f"Jarvis Telegram bot starting (API: {API_BASE})")
    app_bot.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
