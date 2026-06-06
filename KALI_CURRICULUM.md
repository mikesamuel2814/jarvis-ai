# Jarvis Kali-Tools Curriculum

**Authorized penetration-testing capability for Mike Samuel's own Kali machine.**
This is the operator reference for Jarvis's Kali integration: the four engagement
levels, every tool, its canonical command and tier, what to *say* to invoke it,
and the scope/approval policy that governs every run.

> All testing is **authorized security testing** against targets Mike owns or that
> are explicitly sanctioned. Out-of-scope targets are gated behind explicit approval
> and clearly flagged. Jarvis never silently scans a host it isn't allowed to.

---

## How it fits together

| Layer | Module | Role |
|---|---|---|
| Tool catalogue + execution | `kali_tools.py` | `KALI_TOOLS`, `validate_scope`, `effective_tier`, `run_tool`, `list_tools` — scope-checks, runs, audits, and writes a report per scan |
| AI interpretation | `kali_ai.py` | `interpret(tool, raw_output, target)` turns raw output into a "Sir, ..." findings summary; `plan_task`, `suggest_next` |
| Action dispatch | `executor.py` | Each tool is registered as a `kali_<tool>` action; `run_action` routes to `_run_kali`, which calls `run_tool` then `interpret` |
| UX | `telegram_bot.py` | `/kali` (catalogue + run), `/scans` (recent reports) |
| Approval gating | `permissions.py` + API `/action` | APPROVE-tier tools and scope escalations prompt a Telegram approve/deny button |

Every tool is registered into `executor.ACTIONS` as **`kali_<toolname>`**
(e.g. `nmap_quick` → action `kali_nmap_quick`). The action's `cmd` is `None`
because execution always flows through `kali_tools.run_tool`, never the generic
shell path — that is what keeps scope validation, tiering, auditing, and report
logging in force.

---

## Scope & approval policy

A tool runs **automatically (no approval)** only when **both** are true:

1. The tool is **passive** (it only reads / queries, doesn't actively probe), **and**
2. The target is **in scope**.

Anything else escalates to **APPROVE** — which in Telegram means an explicit
approve/deny button. Specifically:

- **Active tools** (every L2/L3/L4 tool) are always APPROVE, even against in-scope hosts.
- **Out-of-scope targets** force APPROVE *and* a ⚠️ banner, even for passive L1 recon.
- Local-only tools with no remote target (`john`, `hashcat`, `msf_resource`) are
  treated as in-scope (nothing remote is being attacked).

### Default in-scope targets

```
localhost / 127.0.0.1     — this Kali box
38.47.35.16               — Mike's VPS
scanme.nmap.org           — sanctioned public test host (Nmap project)
testphp.vulnweb.com       — sanctioned public test host (Acunetix)
```

Additional authorized hosts can be listed in **`~/.jarvis/authorized_targets.txt`**
(one per line). Anything not in the defaults or that file is **out of scope** and
will be flagged + require approval.

### Where reports land

Every run writes a timestamped report to **`~/.jarvis/scans/`**. The interpreted
summary returned to Telegram ends with `📄 <report_path>`. List the last ~10 with
**`/scans`**.

### Defense in depth (executor)

The API `/action` endpoint gates on the *statically registered* tier. Because a
passive tool can escalate to APPROVE at runtime (out-of-scope target),
`executor._run_kali` re-computes `effective_tier(tool, target)` and, if it
escalates above the static tier, **creates a pending approval instead of running**.
So an out-of-scope `whois` (registered auto) still ends up behind the approve button.

---

## Level 1 — Recon (passive, AUTO)

Passive information gathering. Runs automatically against in-scope targets.

| Action | Tool | Canonical command | Tier | Purpose |
|---|---|---|---|---|
| `kali_whois` | whois | `whois {target}` | auto | WHOIS registration lookup |
| `kali_dig` | dig | `dig +short ANY {target}` | auto | DNS records (ANY) |
| `kali_nslookup` | nslookup | `nslookup {target}` | auto | DNS lookup |
| `kali_host` | host | `host {target}` | auto | DNS lookup via host |
| `kali_nmap_ping` | nmap | `nmap -sn {target}` | auto | Host discovery / ping sweep (no ports) |
| `kali_theharvester` | theHarvester | `theHarvester -d {target} -b duckduckgo,bing -l 200` | auto | OSINT email/subdomain harvest |
| `kali_searchsploit` | searchsploit | `searchsploit {term}` | auto | Offline Exploit-DB search |

**Natural language Mike can say:**
- "whois example.com" → `kali_whois`
- "dns lookup / dns records for X" → `kali_dig`
- "resolve host X" → `kali_host`
- "find subdomains / OSINT on X" → `kali_theharvester`
- "search exploits for apache 2.4" → `kali_searchsploit`

---

## Level 2 — Scan & Enumerate (active, APPROVE)

Active port/service/SMB enumeration. Always requires approval.

| Action | Tool | Canonical command | Tier | Purpose |
|---|---|---|---|---|
| `kali_nmap_quick` | nmap | `nmap -T4 -F {target}` | approve | Fast scan, top 100 ports |
| `kali_nmap_service` | nmap | `nmap -T4 -sV -sC {target}` | approve | Service/version + default NSE scripts |
| `kali_nmap_full` | nmap | `nmap -T4 -p- {target}` | approve | Full TCP scan, all 65535 ports |
| `kali_masscan` | masscan | `masscan {target} -p1-1000 --rate 1000` | approve | Mass port scan, ports 1-1000 |
| `kali_whatweb` | whatweb | `whatweb {target}` | approve | Web technology fingerprint |
| `kali_smb_list` | smbclient | `smbclient -L {target} -N` | approve | List SMB shares (null session) |
| `kali_enum4linux` | enum4linux | `enum4linux -a {target}` | approve | SMB/Windows enumeration |

**Natural language Mike can say:**
- "port scan X" / "scan ports on X" / "quick scan X" → `kali_nmap_quick`
- "service scan X" / "enumerate services on X" → `kali_nmap_service`
- "fingerprint web X" / "what web tech does X run" → `kali_whatweb`

---

## Level 3 — Web (active, APPROVE)

Active web application testing. Always requires approval.

| Action | Tool | Canonical command | Tier | Purpose |
|---|---|---|---|---|
| `kali_nikto` | nikto | `nikto -h {target}` | approve | Web server vulnerability scan |
| `kali_nuclei` | nuclei | `nuclei -u {target} -silent` | approve | Template-based vuln scan |
| `kali_gobuster_dir` | gobuster | `gobuster dir -u {target} -w /usr/share/wordlists/dirb/common.txt -q` | approve | Directory brute-force |
| `kali_ffuf` | ffuf | `ffuf -u {target}/FUZZ -w /usr/share/wordlists/dirb/common.txt -s` | approve | Directory fuzzing |
| `kali_sqlmap` | sqlmap | `sqlmap -u {target} --batch --level 1 --risk 1` | approve | SQL injection test |
| `kali_wpscan` | wpscan | `wpscan --url {target} --no-banner` | approve | WordPress vulnerability scan |

**Natural language Mike can say:**
- "web scan X" / "scan website X" → `kali_nikto`
- "vuln scan X" / "scan X for vulns" → `kali_nuclei`
- "dir bust X" / "find directories on X" → `kali_gobuster_dir`

---

## Level 4 — Intrusive / Exploit (APPROVE)

Exploitation and credential attacks. Always requires approval; some are local-only.

| Action | Tool | Canonical command | Tier | Purpose |
|---|---|---|---|---|
| `kali_nmap_vuln` | nmap | `nmap -sV --script vuln {target}` | approve | Nmap vuln NSE scripts |
| `kali_hydra` | hydra | `hydra {opts} {target}` | approve | Online password brute-force (opts carry service/userlist) |
| `kali_john` | john | `john {opts} {hashfile}` | approve | Offline password cracking (local) |
| `kali_hashcat` | hashcat | `hashcat {opts} {hashfile}` | approve | GPU password cracking (local) |
| `kali_msf_resource` | msfconsole | `msfconsole -q -x {opts}` | approve | Metasploit command/resource runner (opts = command string) |

**Note on opts:** L4 tools take their work in the **opts** part of the argument.
For `/kali`, everything after the target is forwarded verbatim as opts. For
local-only tools (`john`, `hashcat`, `msf_resource`) pass the hashfile/command via
opts and use a placeholder or local path as the "target".

---

## Telegram UX

### `/kali`
No args → prints the full catalogue grouped by level, with tier icons
(⚡ auto / 🔐 approval) and usage hint.

### `/kali <tool> <target> [opts]`
Runs the tool. The first token after the tool name is the **target**; everything
after that is passed through as **opts**. Examples:

```
/kali whois example.com
/kali nmap_quick scanme.nmap.org
/kali nmap_service 38.47.35.16
/kali nikto http://testphp.vulnweb.com
/kali gobuster_dir http://testphp.vulnweb.com
```

- **AUTO tools, in-scope** → runs immediately; Jarvis replies with the interpreted
  findings + `📄 report path`.
- **APPROVE tools** (all L2-L4) → replies with an approval request (ID + approve/deny);
  the scan only runs after Mike approves.
- **Out-of-scope target** → a ⚠️ banner is shown and approval is forced, even for L1.

### `/scans`
Lists the last ~10 files in `~/.jarvis/scans/` with name, size and modified time.

---

## Natural-language quick reference

These phrases route through `executor.NL_MAP` (so they work in normal chat / `/exec`,
not just `/kali`):

| Say | Runs |
|---|---|
| "whois X", "domain info for X" | `kali_whois` |
| "dns lookup X", "dns records for X" | `kali_dig` |
| "resolve host X" | `kali_host` |
| "find subdomains for X", "OSINT on X" | `kali_theharvester` |
| "search exploits for X" | `kali_searchsploit` |
| "port scan X", "scan ports on X", "quick scan X" | `kali_nmap_quick` |
| "service scan X", "enumerate services on X" | `kali_nmap_service` |
| "fingerprint web X", "web tech of X" | `kali_whatweb` |
| "web scan X", "scan website X" | `kali_nikto` |
| "vuln scan X", "scan X for vulns" | `kali_nuclei` |
| "dir bust X", "find directories on X", "gobuster X" | `kali_gobuster_dir` |

---

## Safety summary

1. Passive + in-scope is the **only** path that runs without approval.
2. Active scans always require approval.
3. Out-of-scope = ⚠️ banner + forced approval, no silent runs.
4. Every run is audited (`~/.jarvis/logs/executor.log`) and report-logged
   (`~/.jarvis/scans/`).
5. Scope is centrally defined in `kali_tools.py` (`DEFAULT_IN_SCOPE`) plus
   `~/.jarvis/authorized_targets.txt`. Injection safety on target/opts is handled
   inside `kali_tools.run_tool`.
