#!/usr/bin/env python3
"""
Jarvis Kali-Tools Execution Engine — authorized pentesting runner.

For AUTHORIZED security testing on Mike Samuel's own infrastructure only.
Every tool call is scope-validated, tiered, audited, and report-logged.

Tiering policy (Mike: "auto recon, approve rest"):
    A tool runs at AUTO only if it is passive AND the target is in-scope.
    Everything else escalates to APPROVE. Out-of-scope targets are NOT
    refused — they escalate to APPROVE with a ⚠️ banner (Mike: "anything,
    confirm each"). run_tool itself is the actual runner; approval gating
    happens upstream in executor/action_flow.
"""

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("jarvis.kali_tools")

# ── Paths ──────────────────────────────────────────────────────────────────────
JARVIS_HOME = Path.home() / ".jarvis"
CONFIG_DIR = JARVIS_HOME / "config"
AUTHORIZED_TARGETS_FILE = CONFIG_DIR / "authorized_targets.txt"
SCANS_DIR = JARVIS_HOME / "scans"
LOGS_DIR = JARVIS_HOME / "logs"
AUDIT_LOG = LOGS_DIR / "kali_audit.log"

# ── Tier constants (match executor.py by value) ────────────────────────────────
AUTO = "auto"
CONFIRM = "confirm"
APPROVE = "approve"

# ── Scope defaults (always in-scope) ───────────────────────────────────────────
DEFAULT_IN_SCOPE = {
    "localhost",
    "127.0.0.1",
    "::1",
    "38.47.35.16",            # Mike's VPS
    "scanme.nmap.org",        # sanctioned public test host
    "testphp.vulnweb.com",    # sanctioned public test host
}

# Shell metacharacters that must never appear in operator-supplied opts.
_OPTS_FORBIDDEN = set(";|&$`><\n\r")

# ── Wordlist resolution (verify presence; fall back to seclists) ───────────────
def _pick_wordlist() -> str:
    """Return a directory/web wordlist path that actually exists on this host."""
    candidates = [
        "/usr/share/wordlists/dirb/common.txt",
        "/usr/share/seclists/Discovery/Web-Content/common.txt",
        "/usr/share/seclists/Discovery/Web-Content/raft-small-words.txt",
    ]
    for path in candidates:
        if Path(path).is_file():
            return path
    # Last-resort default (kept even if missing so templates remain stable;
    # run_tool still executes and the tool will report its own error).
    return "/usr/share/wordlists/dirb/common.txt"


_WORDLIST = _pick_wordlist()

# ── Tool registry ──────────────────────────────────────────────────────────────
# name -> {desc, cmd, tier, timeout, category, needs_target, passive}
# tier here is the BASE/intended tier; effective_tier() computes the runtime tier.
KALI_TOOLS: dict[str, dict] = {
    # ── L1 recon (passive=True) ───────────────────────────────────────────────
    "whois": {
        "desc": "WHOIS registration lookup", "cmd": "whois {arg}",
        "tier": AUTO, "timeout": 60, "category": "recon",
        "needs_target": True, "passive": True,
    },
    "dig": {
        "desc": "DNS records (ANY) via dig", "cmd": "dig +short ANY {arg}",
        "tier": AUTO, "timeout": 60, "category": "recon",
        "needs_target": True, "passive": True,
    },
    "nslookup": {
        "desc": "DNS lookup via nslookup", "cmd": "nslookup {arg}",
        "tier": AUTO, "timeout": 60, "category": "recon",
        "needs_target": True, "passive": True,
    },
    "host": {
        "desc": "DNS lookup via host", "cmd": "host {arg}",
        "tier": AUTO, "timeout": 60, "category": "recon",
        "needs_target": True, "passive": True,
    },
    "theharvester": {
        "desc": "OSINT email/subdomain harvest", "cmd": "theHarvester -d {arg} -b duckduckgo,bing -l 200",
        "tier": AUTO, "timeout": 120, "category": "recon",
        "needs_target": True, "passive": True,
    },
    "searchsploit": {
        "desc": "Search Exploit-DB offline", "cmd": "searchsploit {arg}",
        "tier": AUTO, "timeout": 60, "category": "recon",
        "needs_target": True, "passive": True,
    },
    "nmap_ping": {
        "desc": "Host discovery (ping sweep, no port scan)", "cmd": "nmap -sn {arg}",
        "tier": AUTO, "timeout": 60, "category": "recon",
        "needs_target": True, "passive": True,
    },

    # ── L2 scan/enum (passive=False) ──────────────────────────────────────────
    "nmap_quick": {
        "desc": "Fast scan of top 100 ports", "cmd": "nmap -T4 -F {arg}",
        "tier": APPROVE, "timeout": 300, "category": "scan",
        "needs_target": True, "passive": False,
    },
    "nmap_service": {
        "desc": "Service/version + default scripts", "cmd": "nmap -T4 -sV -sC {arg}",
        "tier": APPROVE, "timeout": 300, "category": "scan",
        "needs_target": True, "passive": False,
    },
    "nmap_full": {
        "desc": "Full TCP port scan (all 65535)", "cmd": "nmap -T4 -p- {arg}",
        "tier": APPROVE, "timeout": 900, "category": "scan",
        "needs_target": True, "passive": False,
    },
    "masscan": {
        "desc": "Mass port scan (ports 1-1000)", "cmd": "masscan {arg} -p1-1000 --rate 1000",
        "tier": APPROVE, "timeout": 300, "category": "scan",
        "needs_target": True, "passive": False,
    },
    "enum4linux": {
        "desc": "SMB/Windows enumeration", "cmd": "enum4linux -a {arg}",
        "tier": APPROVE, "timeout": 300, "category": "scan",
        "needs_target": True, "passive": False,
    },
    "smb_list": {
        "desc": "List SMB shares (null session)", "cmd": "smbclient -L {arg} -N",
        "tier": APPROVE, "timeout": 300, "category": "scan",
        "needs_target": True, "passive": False,
    },
    "whatweb": {
        "desc": "Web technology fingerprint", "cmd": "whatweb {arg}",
        "tier": APPROVE, "timeout": 300, "category": "scan",
        "needs_target": True, "passive": False,
    },

    # ── L3 web (passive=False) ────────────────────────────────────────────────
    "nikto": {
        "desc": "Web server vulnerability scan", "cmd": "nikto -h {arg}",
        "tier": APPROVE, "timeout": 600, "category": "web",
        "needs_target": True, "passive": False,
    },
    "gobuster_dir": {
        "desc": "Directory brute-force (gobuster)", "cmd": "gobuster dir -u {arg} -w " + _WORDLIST + " -q",
        "tier": APPROVE, "timeout": 600, "category": "web",
        "needs_target": True, "passive": False,
    },
    "ffuf": {
        "desc": "Directory fuzzing (ffuf)", "cmd": "ffuf -u {arg}/FUZZ -w " + _WORDLIST + " -s",
        "tier": APPROVE, "timeout": 600, "category": "web",
        "needs_target": True, "passive": False,
    },
    "wpscan": {
        "desc": "WordPress vulnerability scan", "cmd": "wpscan --url {arg} --no-banner",
        "tier": APPROVE, "timeout": 600, "category": "web",
        "needs_target": True, "passive": False,
    },
    "nuclei": {
        "desc": "Template-based vuln scan (nuclei)", "cmd": "nuclei -u {arg} -silent",
        "tier": APPROVE, "timeout": 600, "category": "web",
        "needs_target": True, "passive": False,
    },
    "sqlmap": {
        "desc": "SQL injection test (sqlmap)", "cmd": "sqlmap -u {arg} --batch --level 1 --risk 1",
        "tier": APPROVE, "timeout": 600, "category": "web",
        "needs_target": True, "passive": False,
    },

    # ── L4 intrusive/exploit (passive=False, tier always APPROVE) ─────────────
    "hydra": {
        "desc": "Online password brute-force (opts carry service/userlist)",
        "cmd": "hydra {opts} {arg}",
        "tier": APPROVE, "timeout": 900, "category": "exploit",
        "needs_target": True, "passive": False,
    },
    "john": {
        "desc": "Offline password cracking (John the Ripper)", "cmd": "john {opts} {arg}",
        "tier": APPROVE, "timeout": 900, "category": "exploit",
        "needs_target": False, "passive": False,
    },
    "hashcat": {
        "desc": "GPU password cracking (hashcat)", "cmd": "hashcat {opts} {arg}",
        "tier": APPROVE, "timeout": 900, "category": "exploit",
        "needs_target": False, "passive": False,
    },
    "msf_resource": {
        "desc": "Metasploit resource/command runner (opts = command string)",
        "cmd": "msfconsole -q -x {opts}",
        "tier": APPROVE, "timeout": 900, "category": "exploit",
        "needs_target": False, "passive": False,
    },
    "nmap_vuln": {
        "desc": "Nmap vuln NSE scripts", "cmd": "nmap -sV --script vuln {arg}",
        "tier": APPROVE, "timeout": 900, "category": "exploit",
        "needs_target": True, "passive": False,
    },
}

# ── Concurrency: only one heavy scan at a time ────────────────────────────────
_SCAN_LOCK = threading.Lock()


# ── Scope validation ───────────────────────────────────────────────────────────
def _load_authorized_targets() -> set[str]:
    """Read authorized_targets.txt (create-tolerant). Missing file = defaults only."""
    targets: set[str] = set()
    try:
        if AUTHORIZED_TARGETS_FILE.is_file():
            for line in AUTHORIZED_TARGETS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    targets.add(line.lower())
    except Exception as exc:  # never raise to caller
        logger.warning("could not read authorized targets: %s", exc)
    return targets


def _normalize_target(target: str) -> str:
    """Strip scheme/path/port/credentials so a URL and a bare host compare equal."""
    t = (target or "").strip()
    if not t:
        return ""
    # Drop scheme.
    if "://" in t:
        t = t.split("://", 1)[1]
    # Drop path / query / fragment.
    for sep in ("/", "?", "#"):
        if sep in t:
            t = t.split(sep, 1)[0]
    # Drop credentials user:pass@host.
    if "@" in t:
        t = t.rsplit("@", 1)[1]
    # Drop port — but preserve bare IPv6 (contains ':' and no dotted host).
    if t.startswith("["):  # [::1]:80 form
        t = t[1:].split("]", 1)[0]
    elif t.count(":") == 1:  # host:port
        t = t.split(":", 1)[0]
    return t.strip().lower()


def validate_scope(target: str) -> dict:
    """
    Decide whether a target is within authorized scope.

    Returns {"in_scope": bool, "reason": str, "normalized": str}.
    """
    normalized = _normalize_target(target)
    if not normalized:
        # No target (e.g. john/hashcat/msf) — treated as in-scope; nothing to attack remotely.
        return {"in_scope": True, "reason": "no target supplied (local-only tool)", "normalized": ""}

    if normalized in DEFAULT_IN_SCOPE:
        return {"in_scope": True, "reason": "default in-scope target", "normalized": normalized}

    authorized = _load_authorized_targets()
    if normalized in authorized:
        return {"in_scope": True, "reason": "listed in authorized_targets.txt", "normalized": normalized}

    return {
        "in_scope": False,
        "reason": "not in default scope or authorized_targets.txt",
        "normalized": normalized,
    }


# ── Tier computation ───────────────────────────────────────────────────────────
def effective_tier(tool: str, target: str) -> str:
    """
    Compute runtime tier: AUTO only if tool is passive AND target in-scope,
    otherwise APPROVE. Unknown tools default to APPROVE.
    """
    meta = KALI_TOOLS.get(tool)
    if meta is None:
        return APPROVE
    scope = validate_scope(target)
    if meta.get("passive") and scope["in_scope"]:
        return AUTO
    return APPROVE


# ── Helpers ────────────────────────────────────────────────────────────────────
def _base_binary(cmd_template: str) -> str:
    """Extract the base executable name from a command template."""
    try:
        return shlex.split(cmd_template)[0]
    except ValueError:
        return cmd_template.split()[0] if cmd_template.split() else ""


def _opts_is_safe(opts: str) -> bool:
    """Reject operator opts containing shell metacharacters / newlines."""
    return not any(ch in _OPTS_FORBIDDEN for ch in (opts or ""))


def _audit(tool: str, target: str, tier: str, in_scope: bool, returncode: int) -> None:
    """Append one JSON line to the audit log. Never raises."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "tool": tool,
            "target": target,
            "tier": tier,
            "in_scope": in_scope,
            "returncode": returncode,
        }
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning("audit write failed: %s", exc)


def _write_report(tool: str, content: str) -> str:
    """Write full output to ~/.jarvis/scans/<ts>_<tool>.txt. Returns path (or "")."""
    try:
        SCANS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_tool = re.sub(r"[^A-Za-z0-9_.-]", "_", tool)
        path = SCANS_DIR / f"{ts}_{safe_tool}.txt"
        path.write_text(content, encoding="utf-8", errors="ignore")
        return str(path)
    except Exception as exc:
        logger.warning("report write failed: %s", exc)
        return ""


# ── Main runner ────────────────────────────────────────────────────────────────
def run_tool(tool: str, target: str = "", opts: str = "") -> dict:
    """
    Execute a Kali tool. Scope-validated, tiered, audited, and report-logged.

    Returns:
        {success, summary, report_path, tier, via, tool, target,
         in_scope, returncode}
    """
    def _fail(summary: str, tier: str, in_scope: bool, returncode: int = -1,
              report_path: str = "") -> dict:
        return {
            "success": False, "summary": summary, "report_path": report_path,
            "tier": tier, "via": "kali", "tool": tool, "target": target,
            "in_scope": in_scope, "returncode": returncode,
        }

    meta = KALI_TOOLS.get(tool)
    if meta is None:
        _audit(tool, target, APPROVE, False, -1)
        return _fail(f"Sir, unknown tool '{tool}'. Use list_tools() to browse the registry.",
                     APPROVE, False)

    # 1. Scope + tier.
    scope = validate_scope(target)
    in_scope = scope["in_scope"]
    tier = effective_tier(tool, target)
    banner = ""
    if not in_scope and target:
        banner = ("⚠️ OUT OF SCOPE — explicit approval required (target not in "
                  "authorized scope, Sir).\n")
        tier = APPROVE

    # Required-target check.
    if meta.get("needs_target") and not (target and target.strip()):
        _audit(tool, target, tier, in_scope, -1)
        return _fail(f"{banner}Sir, tool '{tool}' requires a target.", tier, in_scope)

    # 2. Binary presence.
    base = _base_binary(meta["cmd"])
    if not shutil.which(base):
        _audit(tool, target, tier, in_scope, -1)
        return _fail(f"{banner}Sir, required binary '{base}' is not installed or not on PATH.",
                     tier, in_scope)

    # 3. Opts safety + substitution.
    if opts and not _opts_is_safe(opts):
        _audit(tool, target, tier, in_scope, -1)
        return _fail(f"{banner}Sir, refused: opts contain shell metacharacters "
                     f"({''.join(sorted(_OPTS_FORBIDDEN - set(chr(10)+chr(13))))} or newline).",
                     tier, in_scope)

    cmd = meta["cmd"]
    cmd = cmd.replace("{arg}", shlex.quote(target) if target else "")
    cmd = cmd.replace("{opts}", opts or "")
    cmd = cmd.strip()

    # 4. Execute (heavy scans hold the lock; passive L1 may skip it).
    timeout = int(meta.get("timeout", 300))
    use_lock = not meta.get("passive")
    logger.info("kali run: tool=%s tier=%s in_scope=%s cmd=%s", tool, tier, in_scope, cmd)

    def _execute() -> dict:
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            output = (result.stdout or "") + (result.stderr or "")
            report_path = _write_report(tool, output)
            _audit(tool, target, tier, in_scope, result.returncode)
            summary = banner + (output[:1500] if output else "(no output)")
            return {
                "success": result.returncode == 0,
                "summary": summary,
                "report_path": report_path,
                "tier": tier, "via": "kali", "tool": tool, "target": target,
                "in_scope": in_scope, "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired as exc:
            partial = ""
            if exc.stdout:
                partial += exc.stdout.decode(errors="ignore") if isinstance(exc.stdout, bytes) else exc.stdout
            if exc.stderr:
                partial += exc.stderr.decode(errors="ignore") if isinstance(exc.stderr, bytes) else exc.stderr
            note = f"\n[timed out after {timeout}s]"
            report_path = _write_report(tool, partial + note)
            _audit(tool, target, tier, in_scope, -2)
            return _fail(banner + (partial[:1500] if partial else "") + f"\nSir, {tool} timed out after {timeout}s.",
                         tier, in_scope, returncode=-2, report_path=report_path)
        except Exception as exc:
            _audit(tool, target, tier, in_scope, -1)
            return _fail(f"{banner}Sir, {tool} failed to run: {exc}", tier, in_scope)

    if use_lock:
        with _SCAN_LOCK:
            return _execute()
    return _execute()


# ── Registry browse ────────────────────────────────────────────────────────────
_CATEGORY_LEVEL = {"recon": 1, "scan": 2, "web": 3, "exploit": 4}


def list_tools(level: int | None = None) -> list[dict]:
    """
    Return registry entries for /kali help. If `level` (1-4) is given, filter to
    that level (recon=1, scan=2, web=3, exploit=4). Otherwise return all.
    """
    out: list[dict] = []
    for name, meta in KALI_TOOLS.items():
        lvl = _CATEGORY_LEVEL.get(meta["category"], 0)
        if level is not None and lvl != level:
            continue
        out.append({
            "name": name,
            "desc": meta["desc"],
            "category": meta["category"],
            "level": lvl,
            "tier": meta["tier"],
            "timeout": meta["timeout"],
            "needs_target": meta["needs_target"],
            "passive": meta["passive"],
        })
    out.sort(key=lambda d: (d["level"], d["name"]))
    return out


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) >= 2:
        _t = sys.argv[1]
        _tgt = sys.argv[2] if len(sys.argv) > 2 else ""
        _o = sys.argv[3] if len(sys.argv) > 3 else ""
        print(json.dumps(run_tool(_t, _tgt, _o), indent=2))
    else:
        print(f"{len(KALI_TOOLS)} tools registered:")
        for entry in list_tools():
            print(f"  L{entry['level']} [{entry['tier']:>7}] {entry['name']:<14} {entry['desc']}")
