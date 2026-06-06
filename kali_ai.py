"""
Jarvis Kali AI — interpretation + planning layer for the Kali pentest toolkit.

Authorized security testing context (Mike Samuel's own Kali machine).

Public API:
  interpret(tool, raw_output, target="")   -> str   AI findings summary ("Sir, ...")
  plan_task(nl_request)                    -> list[dict]  ordered [{tool,target,opts,why}]
  suggest_next(tool, findings)             -> str   one-line recommended next step

Backends (each with graceful fallbacks, never raises to caller):
  interpret : claude_client -> Ollama deepseek-r1 -> regex heuristic
  plan_task : OpenClaw bridge -> claude_client -> Ollama -> deterministic heuristic
  suggest_next : heuristic rules (always) + optional model polish
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("jarvis.kali_ai")

# Keep prompts/output inside the 6GB-VRAM / num_ctx<=2048 envelope for Ollama.
_MAX_RAW = 6000
_OLLAMA_MODEL = "deepseek-r1:7b"

# ── Tool registry access ───────────────────────────────────────────────────


def _registry() -> dict:
    """Lazily fetch KALI_TOOLS. Returns {} if Agent A's module isn't ready."""
    try:
        from kali_tools import KALI_TOOLS  # noqa: PLC0415
        if isinstance(KALI_TOOLS, dict):
            return KALI_TOOLS
    except Exception as e:  # noqa: BLE001
        log.warning("kali_tools.KALI_TOOLS unavailable: %s", e)
    return {}


# Fallback names used by the deterministic planner. Each is filtered against the
# live registry before being returned, so unknown names are silently dropped.
_HEURISTIC_TOOLS = {
    "nmap_quick", "nmap_service", "whatweb", "nikto", "nuclei",
    "gobuster", "hydra", "whois", "dig", "theharvester", "wpscan",
    "dnsrecon", "sslscan", "sublist3r",
}


def _valid_tool(name: str, reg: dict) -> bool:
    return bool(name) and name in reg


# ── Backend helpers ─────────────────────────────────────────────────────────


def _strip_think(text: str) -> str:
    """Remove deepseek-r1 <think>...</think> reasoning blocks."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Drop a dangling unclosed <think> tail too.
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _try_claude(system: str, user: str) -> str | None:
    try:
        import claude_client  # noqa: PLC0415
        text, _ = claude_client.get_client().query(system=system, user=user)
        text = (text or "").strip()
        return text or None
    except Exception as e:  # noqa: BLE001
        log.warning("claude_client query failed: %s", e)
        return None


def _try_ollama(system: str, user: str, num_predict: int = 512) -> str | None:
    try:
        import ollama  # noqa: PLC0415
        client = ollama.Client(host="http://localhost:11434")
        resp = client.chat(
            model=_OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options={"num_ctx": 2048, "num_predict": num_predict},
        )
        text = _strip_think(resp.get("message", {}).get("content", ""))
        return text or None
    except Exception as e:  # noqa: BLE001
        log.warning("ollama deepseek fallback failed: %s", e)
        return None


# ── interpret ───────────────────────────────────────────────────────────────

_INTERPRET_SYS = (
    "You are Jarvis, Mike's security analyst. Summarize this {tool} output. "
    "Open with 'Sir,'. Be concise: bullet the key findings (open ports/services/"
    "versions, vulns + severity, interesting paths/creds-exposure). End with one "
    "'Recommended next:' line naming a logical next tool. No fluff, no 'Certainly'."
)


def interpret(tool: str, raw_output: str, target: str = "") -> str:
    """AI-summarize a tool's raw output into a concise 'Sir, ...' findings block."""
    raw = (raw_output or "")[:_MAX_RAW]
    if not raw.strip():
        return f"Sir, {tool} produced no output to analyze."

    system = _INTERPRET_SYS.format(tool=tool)
    user = f"Tool: {tool}\nTarget: {target or 'n/a'}\n\nRaw output:\n{raw}"

    text = _try_claude(system, user)
    if text:
        return text

    text = _try_ollama(system, user)
    if text:
        return text

    log.warning("interpret: all models unavailable, using heuristic for %s", tool)
    return _heuristic_summary(tool, raw, target)


def _heuristic_summary(tool: str, raw: str, target: str) -> str:
    """Regex-based findings when no model is reachable. Always returns something."""
    t = (tool or "").lower()
    lines = raw.splitlines()
    findings: list[str] = []
    next_tool = ""

    if "nmap" in t:
        for ln in lines:
            if re.search(r"^\s*\d+/(tcp|udp)\s+open", ln):
                findings.append(f"- {ln.strip()}")
        next_tool = "nmap_service" if "service" not in t else "nikto"
    elif "nikto" in t:
        for ln in lines:
            if ln.lstrip().startswith("+ ") or "OSVDB" in ln:
                findings.append(f"- {ln.strip()}")
        next_tool = "nuclei"
    elif "gobuster" in t or "dirb" in t or "feroxbuster" in t:
        for ln in lines:
            if re.search(r"\b(200|301|302|401|403)\b", ln):
                findings.append(f"- {ln.strip()}")
        next_tool = "nikto"
    elif "nuclei" in t:
        for ln in lines:
            if re.search(r"\[(critical|high|medium|low|info)\]", ln, re.I):
                findings.append(f"- {ln.strip()}")
        next_tool = "manual review"
    elif "whatweb" in t or "wpscan" in t:
        for ln in lines:
            if ln.strip():
                findings.append(f"- {ln.strip()}")
        next_tool = "nikto"
    else:
        # Generic: surface lines that look noteworthy.
        kw = re.compile(r"\b(open|vuln|cve|password|login|admin|found|"
                        r"critical|high|exposed)\b", re.I)
        for ln in lines:
            if kw.search(ln):
                findings.append(f"- {ln.strip()}")

    findings = findings[:25]
    body = "\n".join(findings) if findings else "- No structured findings parsed from output."
    tgt = f" on {target}" if target else ""
    rec = f"\nRecommended next: {next_tool}" if next_tool else ""
    return f"Sir, here is the {tool} summary{tgt} (heuristic — models offline):\n{body}{rec}"


# ── plan_task ───────────────────────────────────────────────────────────────

_TARGET_RE = re.compile(
    r"\b(?:https?://[^\s]+"                                  # URLs
    r"|(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?"                 # IPv4 / CIDR
    r"|(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\."   # hostnames
    r"(?:[a-zA-Z0-9-]+\.)*[a-zA-Z]{2,}))\b"
)

# Words that match the hostname pattern but are never real targets.
_TARGET_STOPWORDS = {"e.g", "i.e", "etc"}


def _extract_target(text: str) -> str:
    for m in _TARGET_RE.finditer(text or ""):
        cand = m.group(0).rstrip(".,;)")
        if cand.lower() in _TARGET_STOPWORDS:
            continue
        return cand
    return ""


def plan_task(nl_request: str) -> list[dict]:
    """Map an NL request to an ordered list of registry tools with targets+opts."""
    req = (nl_request or "").strip()
    reg = _registry()
    target = _extract_target(req)

    if not req:
        return []

    # 1) OpenClaw-first.
    plan = _plan_openclaw(req, reg, target)
    if plan:
        return plan

    # 2) claude_client (tool-aware JSON).
    plan = _plan_model(req, reg, target, backend="claude")
    if plan:
        return plan

    # 3) Ollama deepseek.
    plan = _plan_model(req, reg, target, backend="ollama")
    if plan:
        return plan

    # 4) Deterministic heuristic.
    return _plan_heuristic(req, reg, target)


def _plan_openclaw(req: str, reg: dict, target: str) -> list[dict]:
    try:
        from openclaw import bridge  # noqa: PLC0415
        if not bridge.openclaw_available():
            return []
        res = bridge.dispatch(req)
    except Exception as e:  # noqa: BLE001
        log.warning("openclaw plan failed: %s", e)
        return []

    if not res or not res.get("ok"):
        return []

    plan = _parse_plan_json(res.get("output", ""), reg, target)
    return plan


def _tool_catalog(reg: dict) -> str:
    if not reg:
        return "(tool registry unavailable)"
    parts = []
    for name, meta in reg.items():
        desc = ""
        if isinstance(meta, dict):
            desc = meta.get("desc") or meta.get("category") or ""
        parts.append(f"- {name}: {desc}".rstrip())
    return "\n".join(parts)


def _plan_model(req: str, reg: dict, target: str, backend: str) -> list[dict]:
    if not reg:
        return []  # can't constrain to real tools — defer to heuristic
    catalog = _tool_catalog(reg)
    system = (
        "You are Jarvis, Mike's pentest planner. Map the request to an ordered "
        "list of tools. Use ONLY these tool names:\n" + catalog + "\n\n"
        "Return STRICT JSON: an array of objects "
        '{"tool":<one of the names above>,"target":<ip/host/url>,'
        '"opts":<extra flags or "">,"why":<short reason>}. '
        "No prose, no markdown fences. If a target is given, reuse it for each step."
    )
    user = f"Request: {req}\nDetected target: {target or '(none — leave blank)'}"

    raw = _try_claude(system, user) if backend == "claude" else _try_ollama(system, user)
    if not raw:
        return []
    return _parse_plan_json(raw, reg, target)


def _parse_plan_json(raw: str, reg: dict, target: str) -> list[dict]:
    """Tolerantly parse a model/bridge JSON plan and filter to valid tools."""
    if not raw:
        return []
    text = raw.strip()
    # Strip code fences.
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()

    data = None
    try:
        data = json.loads(text)
    except Exception:
        # Grab the first JSON array substring.
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    if not isinstance(data, list):
        return []

    plan: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool", "")).strip()
        if not _valid_tool(name, reg):
            continue
        plan.append({
            "tool": name,
            "target": str(item.get("target") or target or "").strip(),
            "opts": str(item.get("opts") or "").strip(),
            "why": str(item.get("why") or "").strip(),
        })
    return plan


def _plan_heuristic(req: str, reg: dict, target: str) -> list[dict]:
    """Deterministic keyword routing. Filters to tools present in the registry."""
    r = req.lower()

    def chain(names: list[str], why: str) -> list[dict]:
        out = []
        for n in names:
            # If registry is empty (Agent A not ready), pass through known
            # heuristic names so the planner is still demonstrable/testable.
            if reg and not _valid_tool(n, reg):
                continue
            if not reg and n not in _HEURISTIC_TOOLS:
                continue
            out.append({"tool": n, "target": target, "opts": "", "why": why})
        return out

    if re.search(r"\b(web|http|https|website|url|vuln|nikto|wordpress|wp)\b", r):
        plan = chain(["nmap_quick", "whatweb", "nikto", "nuclei"],
                     "web vulnerability assessment")
    elif re.search(r"\b(brute|bruteforce|password|crack|login|hydra|ssh)\b", r):
        plan = chain(["nmap_quick", "hydra"], "credential / brute-force attack")
    elif re.search(r"\b(dns|domain|whois|recon|reconnaissance|harvest|enumerat)\b", r):
        plan = chain(["whois", "dig", "theharvester"], "domain reconnaissance")
    elif re.search(r"\b(port|scan|nmap|service|enumerat)\b", r):
        plan = chain(["nmap_quick", "nmap_service"], "port and service scan")
    else:
        plan = chain(["nmap_quick"], "default initial scan")

    # Last resort: if nothing matched the registry, offer any one passive/quick tool.
    if not plan and reg:
        for name in ("nmap_quick", "whois"):
            if _valid_tool(name, reg):
                plan = [{"tool": name, "target": target, "opts": "",
                         "why": "default scan"}]
                break
    return plan


# ── suggest_next ────────────────────────────────────────────────────────────


def suggest_next(tool: str, findings: str) -> str:
    """One-line recommended next step. Heuristic-first, cheap, never raises."""
    t = (tool or "").lower()
    f = (findings or "").lower()

    try:
        if "nmap" in t:
            if re.search(r"\b(80|443|8080|8443|http)\b", f):
                return ("Recommended next: nmap_service for version detection, "
                        "then nikto/whatweb on the web ports.")
            if re.search(r"\b22\b|ssh", f):
                return "Recommended next: nmap_service to fingerprint SSH, then hydra if brute-force is in scope."
            if "open" in f:
                return "Recommended next: nmap_service to enumerate versions on the open ports."
            return "Recommended next: nmap_service for a deeper service/version scan."
        if "whatweb" in t:
            return "Recommended next: nikto to probe the identified web stack for known issues."
        if "nikto" in t:
            return "Recommended next: nuclei for templated CVE checks, then gobuster for hidden paths."
        if "gobuster" in t or "dirb" in t:
            return "Recommended next: nikto/nuclei against the discovered paths."
        if "nuclei" in t:
            if re.search(r"\b(critical|high)\b", f):
                return "Recommended next: manual verification of the critical/high findings before reporting."
            return "Recommended next: gobuster for content discovery, then manual review."
        if "whois" in t or "dig" in t:
            return "Recommended next: theharvester / dnsrecon to enumerate subdomains and emails."
        if "hydra" in t:
            if "success" in f or "valid" in f or "found" in f:
                return "Recommended next: log in with the recovered credentials and assess post-auth surface."
            return "Recommended next: refine the wordlist/username list and retry, or pivot to another service."
    except Exception as e:  # noqa: BLE001
        log.warning("suggest_next heuristic error: %s", e)

    return "Recommended next: nmap_service for deeper enumeration of the target."
