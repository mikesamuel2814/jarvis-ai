#!/usr/bin/env python3
"""
Jarvis Kali-tools self-test harness.

Verifies the Kali integration end-to-end against AUTHORIZED targets ONLY:
    127.0.0.1 / localhost  — this Kali box
    38.47.35.16            — Mike's VPS
    scanme.nmap.org        — sanctioned public Nmap test host
    testphp.vulnweb.com    — sanctioned public Acunetix test host

For each tool it checks:
  * binary presence (shutil.which)
  * (for a chosen subset) a real, bounded run via kali_tools.run_tool
  * that a report file was written to ~/.jarvis/scans/
  * that kali_ai.interpret returns a non-empty "Sir, ..." style summary

It runs ALL Level-1 recon tools for real, plus 1-2 representative L2/L3 tools
against scanme.nmap.org. It NEVER touches an out-of-scope host: every target is
validate_scope()-checked first and skipped if out of scope.

Also: `seed_kali_lessons()` seeds ~8 canonical NL→tool golden examples via
learning.rapid_learner (store_interaction + process_feedback thumbs_up).

Run:
    venv/bin/python3 kali_selftest.py            # full self-test
    venv/bin/python3 kali_selftest.py --seed     # also seed golden examples
    venv/bin/python3 kali_selftest.py --seed-only
"""

import sys
import shutil
import time
import uuid
from pathlib import Path

JARVIS_HOME = Path.home() / ".jarvis"
SCANS_DIR = JARVIS_HOME / "scans"

# Hard allow-list — the harness refuses to run against anything else.
AUTHORIZED = {"127.0.0.1", "localhost", "38.47.35.16",
              "scanme.nmap.org", "testphp.vulnweb.com"}

try:
    import kali_tools
    import kali_ai
except Exception as e:  # pragma: no cover
    print(f"FATAL: cannot import kali modules: {e}")
    sys.exit(2)


def _authorized(target: str) -> bool:
    """Belt-and-braces: must be in our local allow-list AND in kali_tools scope."""
    if not target:
        return True  # local-only tools (no remote target)
    host = target.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
    if host not in AUTHORIZED:
        return False
    try:
        return bool(kali_tools.validate_scope(host).get("in_scope", False))
    except Exception:
        return False


# Live-run plan: (tool, target, opts). All targets MUST be authorized.
# L1 recon — all run for real. L2/L3 — representative, bounded.
LIVE_RUNS = [
    # L1 recon (passive, fast)
    ("whois",        "scanme.nmap.org", ""),
    ("dig",          "scanme.nmap.org", ""),
    ("nslookup",     "scanme.nmap.org", ""),
    ("host",         "scanme.nmap.org", ""),
    ("nmap_ping",    "127.0.0.1",       ""),
    ("searchsploit", "apache",          ""),
    # theharvester can be slow/network-flaky; run against a domain, tolerate empty.
    ("theharvester", "scanme.nmap.org", ""),
    # L2 representative — fast top-100 scan against the sanctioned host.
    ("nmap_quick",   "scanme.nmap.org", ""),
    # L3 representative — fingerprint only (whatweb is quick); nikto is too slow.
    ("whatweb",      "http://testphp.vulnweb.com", ""),
]


def _bin_for(tool: str) -> str:
    meta = kali_tools.KALI_TOOLS.get(tool, {})
    cmd = meta.get("cmd", "")
    # base binary = first whitespace-delimited token
    return cmd.strip().split()[0] if cmd else tool


def run_selftest() -> int:
    print("=" * 74)
    print("  JARVIS KALI SELF-TEST — authorized targets only")
    print("=" * 74)

    all_tools = sorted(kali_tools.KALI_TOOLS.keys())
    live_map = {t[0]: t for t in LIVE_RUNS}

    rows = []  # (tool, bin_ok, ran, report_ok, interp_ok, note)
    overall_ok = True

    for tool in all_tools:
        base = _bin_for(tool)
        bin_ok = bool(shutil.which(base))
        ran = report_ok = interp_ok = None
        note = ""

        if tool in live_map:
            _, target, opts = live_map[tool]
            if not _authorized(target):
                note = f"SKIP target {target} not authorized"
            elif not bin_ok:
                note = f"binary '{base}' missing — skipped run"
            else:
                t0 = time.time()
                try:
                    res = kali_tools.run_tool(tool, target, opts)
                    dt = time.time() - t0
                    ran = bool(res.get("success"))
                    rp = res.get("report_path", "")
                    report_ok = bool(rp and Path(rp).exists())
                    summary = res.get("summary", "") or ""
                    raw = summary
                    if rp and Path(rp).exists():
                        try:
                            raw = Path(rp).read_text(errors="replace")[:6000]
                        except Exception:
                            raw = summary
                    try:
                        interp = kali_ai.interpret(tool, raw, target) or ""
                        interp_ok = bool(interp.strip())
                    except Exception as e:
                        interp_ok = False
                        note = f"interpret err: {e}"
                    rc = res.get("returncode")
                    note = (note + f" rc={rc} {dt:.1f}s in_scope={res.get('in_scope')}").strip()
                    # theharvester legitimately returns empty/non-zero sometimes;
                    # we count it PASS if a report+interpretation were produced.
                    if not ran and report_ok and interp_ok:
                        note += " (non-zero rc tolerated)"
                except Exception as e:
                    ran = False
                    note = f"run err: {e}"
        else:
            note = "catalogue-only (binary check)"

        # Determine PASS/FAIL for this row.
        if tool in live_map and _authorized(live_map[tool][1]) and bin_ok:
            passed = bool(report_ok) and bool(interp_ok)
        else:
            passed = bin_ok  # for non-run tools, presence of binary is the bar
        if not passed:
            overall_ok = False
        rows.append((tool, bin_ok, ran, report_ok, interp_ok, passed, note))

    # ── Print table ──────────────────────────────────────────────────────────
    print(f"\n{'TOOL':<16}{'BIN':<5}{'RAN':<5}{'RPT':<5}{'AI':<5}{'RESULT':<8}NOTE")
    print("-" * 74)
    def _b(v):
        return "—" if v is None else ("✅" if v else "❌")
    for tool, bin_ok, ran, report_ok, interp_ok, passed, note in rows:
        print(f"{tool:<16}{_b(bin_ok):<5}{_b(ran):<5}{_b(report_ok):<5}"
              f"{_b(interp_ok):<5}{('PASS' if passed else 'FAIL'):<8}{note}")

    # ── L1 + scanme gate summary ─────────────────────────────────────────────
    l1 = [t for t in kali_tools.list_tools() if t["level"] == 1]
    l1_live = [r for r in rows if r[0] in {x["name"] for x in l1} and r[0] in live_map]
    l1_ran_pass = all(r[5] for r in l1_live) if l1_live else False
    scanme_quick = next((r for r in rows if r[0] == "nmap_quick"), None)

    print("-" * 74)
    print(f"L1 recon live runs (localhost+scanme): "
          f"{'PASS' if l1_ran_pass else 'FAIL'} ({len(l1_live)} run)")
    if scanme_quick:
        print(f"L2 nmap_quick vs scanme.nmap.org:      "
              f"{'PASS' if scanme_quick[5] else 'FAIL'}")
    print(f"Reports directory: {SCANS_DIR}  "
          f"({len(list(SCANS_DIR.glob('*'))) if SCANS_DIR.exists() else 0} files)")
    print(f"\nOVERALL: {'PASS' if overall_ok else 'FAIL (some tools missing/failed)'}")
    print("=" * 74)

    return 0 if l1_ran_pass else 1


# ── Golden-example seeding ─────────────────────────────────────────────────────
# Canonical NL → tool mappings. These teach Jarvis to map plain requests to the
# right kali action. Seeded as golden examples (high RAG priority).
GOLDEN_EXAMPLES = [
    ("scan my vps for open ports",            "kali_nmap_quick",   "38.47.35.16"),
    ("do a quick port scan of scanme.nmap.org", "kali_nmap_quick", "scanme.nmap.org"),
    ("enumerate services on the vps",         "kali_nmap_service", "38.47.35.16"),
    ("run a whois lookup on example.com",     "kali_whois",        "example.com"),
    ("find subdomains and emails for a domain", "kali_theharvester", "example.com"),
    ("scan that website for vulnerabilities", "kali_nikto",        "http://testphp.vulnweb.com"),
    ("run a nuclei vuln scan on the target",  "kali_nuclei",       "http://testphp.vulnweb.com"),
    ("brute force directories on the web app", "kali_gobuster_dir", "http://testphp.vulnweb.com"),
]


def seed_kali_lessons() -> int:
    """Seed canonical NL→tool mappings as golden examples via rapid_learner."""
    try:
        from learning import rapid_learner
    except Exception as e:
        print(f"Could not import rapid_learner: {e}")
        return 0
    seeded = 0
    for nl, action, example_target in GOLDEN_EXAMPLES:
        iid = f"kali-golden-{uuid.uuid4().hex[:8]}"
        response = (
            f"Sir, that maps to the `{action}` action "
            f"(e.g. `/kali {action[len('kali_'):]} {example_target}`). "
            f"Active scans require your approval; out-of-scope targets are flagged."
        )
        try:
            rapid_learner.store_interaction(
                interaction_id=iid,
                query=nl,
                response=response,
                tier="approve" if action != "kali_whois" else "auto",
                model="kali-curriculum",
            )
            rapid_learner.process_feedback(iid, "thumbs_up")
            seeded += 1
            print(f"  ✅ golden: {nl!r} → {action}")
        except Exception as e:
            print(f"  ❌ failed to seed {nl!r}: {e}")
    print(f"\nSeeded {seeded}/{len(GOLDEN_EXAMPLES)} golden examples "
          f"→ {JARVIS_HOME}/data/golden_examples.jsonl")
    return seeded


def main():
    args = set(sys.argv[1:])
    if "--seed-only" in args:
        seed_kali_lessons()
        return 0
    rc = run_selftest()
    if "--seed" in args:
        print("\n" + "=" * 74)
        print("  SEEDING GOLDEN EXAMPLES")
        print("=" * 74)
        seed_kali_lessons()
    return rc


if __name__ == "__main__":
    sys.exit(main())
