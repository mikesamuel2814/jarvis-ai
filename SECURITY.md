# Jarvis v3 Security Policy

**Version:** 3.0.0-milestone0  
**Date:** 2026-06-07  
**Owner:** Mike Samuel (`kali`)

---

## 1. Security Foundation Modules

Six modules implemented as Milestone 0:

| Module | File | Purpose |
|--------|------|---------|
| Secret Vault | `security/vault/vault.py` | AES-256-GCM encrypted SQLite storage |
| Scope Enforcer | `security/scope_enforcer.py` | READ / LOCAL / NETWORK / PRIVILEGED boundaries |
| Port Monitor | `security/port_monitor.py` | Baseline learning + anomaly detection |
| sudo Auditor | `security/sudo_auditor.py` | sudoers.d policy validation |
| IDS Hook | `security/ids_hook.py` | auth.log, SUID, SSH key, cron monitoring |
| Key Rotator | `security/key_rotator.py` | Rotation tracking + SSL expiry checks |

---

## 2. Audit Findings & Countermeasures

| ID | Severity | Finding | Status | Countermeasure |
|----|----------|---------|--------|----------------|
| SEC-01 | CRITICAL | Hardcoded secrets in `live-translation-backend/.env` | **Mitigated** | Secrets migrated to Vault; `.env` backed up; rotation required |
| SEC-02 | HIGH | `telegram.json` world-readable (664) | **Resolved** | File deleted; Vault is single source of truth |
| SEC-03 | HIGH | `/etc/sudoers.d/jarvis` NOPASSWD for apt/reboot | **Policy hardened** | `jarvis-sudoers-hardened` created; write ops require PASSWD |
| SEC-04 | HIGH | Cloudflared token in `ps` output | **Mitigated** | Token moved to `config/cloudflared.token` (600); wrapper script created |
| SEC-05 | HIGH | Unknown listener on 0.0.0.0:7070 | **Identified** | AnyDesk Client; review unattended access settings |
| SEC-06 | MED | Legacy `api.py` with CORS `*` | **Resolved** | Renamed to `api.py.deprecated` |
| SEC-07 | MED | `StrictHostKeyChecking=no` in executor | **Resolved** | Changed to `accept-new` (15 occurrences) |
| SEC-08 | MED | AnyDesk active; AppArmor not enforcing | **Documented** | Remote access audit script created |
| SEC-09 | LOW | `MOONSHOT_API_KEY=REPLACE_ME` | **Noted** | Requires manual key generation at platform.moonshot.ai |

---

## 3. Trust Model

Seven action ranks (R0-R6) with progressive trust:
- **R0-R1:** Auto-run (read-only, user-space)
- **R2-R3:** Ask first time, then "Allow & Save"
- **R4-R5:** Always ask (system/security-critical)
- **R6:** Typed confirmation + 10s countdown

Registry: `security/trust_registry.py` with JSON backup at `data/trust_registry.json`.

---

## 4. Secret Storage

All secrets referenced via `${VAULT:key_name}`.
- Master key: `~/.jarvis/vault/vault.key` (chmod 600)
- Database: `~/.jarvis/vault/vault.db` (AES-256-GCM field-level encryption)
- Audit log: `~/.jarvis/vault/audit.log`

---

## 5. Operational Security

1. Never commit `vault.key`, `vault.db`, or `secrets.env`.
2. Rotate all cloud secrets every 90 days (tracked by Key Rotator).
3. Run `sudo_auditor.py` before any system modification.
4. Capture port baseline after any legitimate network change.
5. Review `ids_hook.py` alerts daily.

---

*"Security is not a product, but a process." — Bruce Schneier*
