# Port 7070 Investigation Results

**Date:** 2026-06-07
**Finding:** SEC-05 — Unidentified listener on 0.0.0.0:7070

## Investigation

1. `ss -tlnp` showed LISTEN on 0.0.0.0:7070 but PID hidden from unprivileged view
2. `nmap -sV` reported `ssl/realserver?`
3. `openssl s_client -connect 127.0.0.1:7070` revealed certificate CN=**AnyDesk Client**

## Root Cause

Port 7070 is bound by **AnyDesk** remote desktop service. It is listening on all interfaces (0.0.0.0) with a self-signed TLS certificate.

## Risk Assessment

- AnyDesk provides unattended remote access if configured
- Listening on all interfaces exposes the AnyDesk protocol to the LAN
- If unattended access is enabled with a weak password, this is a significant attack vector

## Recommendations

1. Review `~/.anydesk/user.conf` for unattended access settings
2. Disable unattended access if not strictly required
3. If required, ensure strong password and 2FA
4. Consider firewalling port 7070 to localhost only if possible
5. AnyDesk can be configured to use custom ports; consider changing from default

## Status

- **Identified:** ✅ AnyDesk Client
- **Mitigation:** Requires manual review of AnyDesk configuration
- **Monitoring:** PortMonitor baseline should include this once baseline is captured
