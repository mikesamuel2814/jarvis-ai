"""
Jarvis v3 Security Audit Tools (10 tools)
Category: security_audit | Rank: R0-R3 | Scope: READ
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Optional, List

from ..decorator import jarvis_tool
from ..result import ToolResult


@jarvis_tool(
    name="secrets_scan",
    description="Scan codebase for hardcoded secrets (truffleHog-style grep)",
    params={"path": {"type": "string", "required": False, "default": "/home/kali"}},
    rank="R0", scope="READ", category="security_audit", tags=["secrets", "code", "audit"]
)
def secrets_scan(path: str = "/home/kali") -> ToolResult:
    try:
        patterns = [
            r"password\s*[=:]\s*['\"][^'\"]+['\"]",
            r"api_key\s*[=:]\s*['\"][^'\"]+['\"]",
            r"token\s*[=:]\s*['\"][^'\"]+['\"]",
            r"secret\s*[=:]\s*['\"][^'\"]+['\"]",
            r"private_key",
            r"passwd\s*[=:]\s*['\"][^'\"]+['\"]",
        ]
        findings = []
        root = Path(path)
        if not root.exists():
            return ToolResult.fail(error=f"Path {path} does not exist.", tool_name="secrets_scan")
        for f in root.rglob("*"):
            if f.is_file() and f.stat().st_size < 5 * 1024 * 1024:  # skip files > 5MB
                try:
                    text = f.read_text(errors="ignore")
                    for pat in patterns:
                        for m in re.finditer(pat, text, re.IGNORECASE):
                            line_num = text[:m.start()].count("\n") + 1
                            findings.append(f"{f}:{line_num}: {m.group(0)[:80]}")
                except Exception:
                    pass
        output = "\n".join(findings[:200]) if findings else "No secrets found."
        return ToolResult.ok(output=output, data={"findings_count": len(findings)}, tool_name="secrets_scan")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="secrets_scan")


@jarvis_tool(
    name="file_permissions_audit",
    description="Audit file permissions for world-writable files",
    params={"path": {"type": "string", "required": False, "default": "/home/kali"}},
    rank="R0", scope="READ", category="security_audit", tags=["permissions", "audit", "filesystem"]
)
def file_permissions_audit(path: str = "/home/kali") -> ToolResult:
    try:
        result = subprocess.run(
            ["find", path, "-type", "f", "-perm", "-002", "-ls"],
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout if result.stdout else "No world-writable files found."
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="file_permissions_audit")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="file_permissions_audit")


@jarvis_tool(
    name="sudoers_audit",
    description="Audit sudoers configuration",
    rank="R0", scope="READ", category="security_audit", tags=["sudo", "privileges", "audit"]
)
def sudoers_audit() -> ToolResult:
    try:
        result = subprocess.run(
            ["cat", "/etc/sudoers"],
            capture_output=True, text=True, timeout=10
        )
        sudoers = result.stdout
        result2 = subprocess.run(
            ["ls", "-la", "/etc/sudoers.d/"],
            capture_output=True, text=True, timeout=10
        )
        ls_output = result2.stdout
        output = f"=== /etc/sudoers ===\n{sudoers}\n=== /etc/sudoers.d/ ===\n{ls_output}"
        return ToolResult.ok(output=output, tool_name="sudoers_audit")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="sudoers_audit")


@jarvis_tool(
    name="process_arg_audit",
    description="Audit process command lines for secrets",
    rank="R0", scope="READ", category="security_audit", tags=["process", "secrets", "audit"]
)
def process_arg_audit() -> ToolResult:
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.splitlines()
        suspicious = []
        keywords = ["password", "token", "api_key", "secret", "passwd"]
        for line in lines:
            low = line.lower()
            if any(k in low for k in keywords):
                suspicious.append(line)
        output = "\n".join(suspicious[:100]) if suspicious else "No suspicious process arguments found."
        return ToolResult.ok(output=output, data={"suspicious_count": len(suspicious)}, tool_name="process_arg_audit")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="process_arg_audit")


@jarvis_tool(
    name="port_listener_scan",
    description="Scan all listening ports",
    rank="R0", scope="READ", category="security_audit", tags=["network", "ports", "audit"]
)
def port_listener_scan() -> ToolResult:
    try:
        result = subprocess.run(
            ["ss", "-tulnp"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["netstat", "-tulnp"],
                capture_output=True, text=True, timeout=10
            )
        output = result.stdout if result.stdout else result.stderr
        return ToolResult.ok(output=output, tool_name="port_listener_scan")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="port_listener_scan")


@jarvis_tool(
    name="cors_config_audit",
    description="Audit CORS configuration in nginx/configs",
    params={"path": {"type": "string", "required": False, "default": "/etc/nginx"}},
    rank="R0", scope="READ", category="security_audit", tags=["cors", "nginx", "web", "audit"]
)
def cors_config_audit(path: str = "/etc/nginx") -> ToolResult:
    try:
        result = subprocess.run(
            ["grep", "-ri", "access-control-allow-origin", path],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout if result.stdout else "No CORS directives found."
        return ToolResult.ok(output=output, tool_name="cors_config_audit")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="cors_config_audit")


@jarvis_tool(
    name="ssh_config_audit",
    description="Audit SSH daemon configuration",
    rank="R0", scope="READ", category="security_audit", tags=["ssh", "hardening", "audit"]
)
def ssh_config_audit() -> ToolResult:
    try:
        result = subprocess.run(
            ["cat", "/etc/ssh/sshd_config"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout
        return ToolResult.ok(output=output, tool_name="ssh_config_audit")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="ssh_config_audit")


@jarvis_tool(
    name="remote_access_audit",
    description="Inventory remote access tools and config",
    rank="R0", scope="READ", category="security_audit", tags=["remote", "access", "audit"]
)
def remote_access_audit() -> ToolResult:
    try:
        tools = ["ssh", "telnet", "vnc", "rdp", "teamviewer", "anydesk"]
        found = []
        for tool in tools:
            result = subprocess.run(
                ["which", tool],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                found.append(f"{tool}: {result.stdout.strip()}")
        result2 = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--state=running"],
            capture_output=True, text=True, timeout=10
        )
        services = result2.stdout
        output = f"Installed remote access tools:\n" + "\n".join(found) + f"\n\nRunning services:\n{services}"
        return ToolResult.ok(output=output, data={"tools_found": found}, tool_name="remote_access_audit")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="remote_access_audit")


@jarvis_tool(
    name="api_key_health_check",
    description="Check API key validity (Moonshot, etc.)",
    rank="R0", scope="READ", category="security_audit", tags=["api", "key", "health"]
)
def api_key_health_check() -> ToolResult:
    try:
        key = os.environ.get("MOONSHOT_API_KEY", "")
        if not key:
            return ToolResult.ok(output="MOONSHOT_API_KEY not set.", data={"valid": False}, tool_name="api_key_health_check")
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-H", f"Authorization: Bearer {key}",
             "https://api.moonshot.cn/v1/models"],
            capture_output=True, text=True, timeout=30
        )
        code = result.stdout.strip()
        valid = code.startswith("2")
        return ToolResult.ok(
            output=f"Moonshot API response code: {code} ({'valid' if valid else 'invalid'})",
            data={"valid": valid, "code": code},
            tool_name="api_key_health_check"
        )
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="api_key_health_check")


@jarvis_tool(
    name="full_system_audit",
    description="Run full system security audit combining all above",
    rank="R3", scope="READ", category="security_audit", tags=["audit", "full", "system"]
)
def full_system_audit() -> ToolResult:
    try:
        results = []
        audits = [
            secrets_scan,
            file_permissions_audit,
            sudoers_audit,
            process_arg_audit,
            port_listener_scan,
            cors_config_audit,
            ssh_config_audit,
            remote_access_audit,
            api_key_health_check,
        ]
        for audit in audits:
            r = audit()
            results.append(f"=== {audit.__name__} ===\nSuccess: {r.success}\n{r.output}\n")
        output = "\n".join(results)
        return ToolResult.ok(output=output, tool_name="full_system_audit")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="full_system_audit")
