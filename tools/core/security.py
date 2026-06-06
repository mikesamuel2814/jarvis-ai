"""
Jarvis v3 Security / Pentest Tools (15 tools)
Category: security | Rank: R0-R5 | Scope: READ/LOCAL/NETWORK/PRIVILEGED
"""

import os
import subprocess
from pathlib import Path
from typing import Optional

from ..decorator import jarvis_tool
from ..result import ToolResult

AUTHORIZED_TARGETS = {
    "api.asthacash.com",
    "asthacash.com",
    "starline.com",
    "conztru.com",
    "38.47.35.16",
    "100.110.210.103",
    "localhost",
    "127.0.0.1",
}


def _validate_target(target: str) -> bool:
    """Check if target host/IP is in authorized scope."""
    t = target.lower().strip()
    # Remove protocol and path
    if "://" in t:
        t = t.split("://", 1)[1]
    t = t.split("/")[0]
    # Remove port
    if ":" in t:
        t = t.split(":", 1)[0]
    if t in AUTHORIZED_TARGETS:
        return True
    for auth in AUTHORIZED_TARGETS:
        if t == auth or t.endswith("." + auth):
            return True
    return False


@jarvis_tool(
    name="nmap_full",
    description="Full nmap scan with service detection on authorized target",
    params={"target": {"type": "string", "required": True}},
    rank="R3", scope="NETWORK", category="security", tags=["pentest", "network", "scanning"]
)
def nmap_full(target: str) -> ToolResult:
    try:
        if not _validate_target(target):
            return ToolResult.fail(error=f"Target '{target}' not in authorized scope.", tool_name="nmap_full")
        result = subprocess.run(
            ["nmap", "-sV", "-sC", "-O", "-p-", target],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="nmap_full")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="nmap_full")


@jarvis_tool(
    name="nikto_scan",
    description="Nikto web vulnerability scan on authorized target",
    params={"target": {"type": "string", "required": True}},
    rank="R3", scope="NETWORK", category="security", tags=["pentest", "web", "vulnerability"]
)
def nikto_scan(target: str) -> ToolResult:
    try:
        if not _validate_target(target):
            return ToolResult.fail(error=f"Target '{target}' not in authorized scope.", tool_name="nikto_scan")
        result = subprocess.run(
            ["nikto", "-h", target],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="nikto_scan")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="nikto_scan")


@jarvis_tool(
    name="sqlmap_test",
    description="SQLMap SQL injection test on authorized target",
    params={
        "target": {"type": "string", "required": True},
        "extra_args": {"type": "string", "required": False, "default": ""},
    },
    rank="R3", scope="NETWORK", category="security", tags=["pentest", "sql", "injection"]
)
def sqlmap_test(target: str, extra_args: str = "") -> ToolResult:
    try:
        if not _validate_target(target):
            return ToolResult.fail(error=f"Target '{target}' not in authorized scope.", tool_name="sqlmap_test")
        cmd = ["sqlmap", "-u", target, "--batch"]
        if extra_args:
            cmd.extend(extra_args.split())
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="sqlmap_test")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="sqlmap_test")


@jarvis_tool(
    name="metasploit_console",
    description="Launch Metasploit console command",
    params={"command": {"type": "string", "required": True}},
    rank="R5", scope="PRIVILEGED", category="security", tags=["pentest", "exploit", "framework"]
)
def metasploit_console(command: str) -> ToolResult:
    try:
        result = subprocess.run(
            ["msfconsole", "-q", "-x", command],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="metasploit_console")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="metasploit_console")


@jarvis_tool(
    name="hydra_brute",
    description="Hydra brute force attack on authorized target",
    params={
        "service": {"type": "string", "required": True},
        "host": {"type": "string", "required": True},
        "userlist": {"type": "string", "required": True},
    },
    rank="R5", scope="PRIVILEGED", category="security", tags=["pentest", "bruteforce"]
)
def hydra_brute(service: str, host: str, userlist: str) -> ToolResult:
    try:
        if not _validate_target(host):
            return ToolResult.fail(error=f"Host '{host}' not in authorized scope.", tool_name="hydra_brute")
        result = subprocess.run(
            ["hydra", "-L", userlist, "-P", "/usr/share/wordlists/rockyou.txt", host, service],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="hydra_brute")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="hydra_brute")


@jarvis_tool(
    name="john_crack",
    description="John the Ripper password cracking",
    params={"hash_file": {"type": "string", "required": True}},
    rank="R3", scope="LOCAL", category="security", tags=["password", "cracking"]
)
def john_crack(hash_file: str) -> ToolResult:
    try:
        result = subprocess.run(
            ["john", hash_file],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="john_crack")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="john_crack")


@jarvis_tool(
    name="hashcat_gpu",
    description="Hashcat GPU hash cracking",
    params={
        "hash_file": {"type": "string", "required": True},
        "hash_type": {"type": "string", "required": False, "default": "0"},
    },
    rank="R3", scope="LOCAL", category="security", tags=["password", "cracking", "gpu"]
)
def hashcat_gpu(hash_file: str, hash_type: str = "0") -> ToolResult:
    try:
        result = subprocess.run(
            ["hashcat", "-m", hash_type, hash_file],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="hashcat_gpu")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="hashcat_gpu")


@jarvis_tool(
    name="gobuster_dir",
    description="Gobuster directory enumeration on authorized target",
    params={
        "target": {"type": "string", "required": True},
        "wordlist": {"type": "string", "required": False, "default": "/usr/share/wordlists/dirb/common.txt"},
    },
    rank="R3", scope="NETWORK", category="security", tags=["pentest", "enumeration", "web"]
)
def gobuster_dir(target: str, wordlist: str = "/usr/share/wordlists/dirb/common.txt") -> ToolResult:
    try:
        if not _validate_target(target):
            return ToolResult.fail(error=f"Target '{target}' not in authorized scope.", tool_name="gobuster_dir")
        url = target if target.startswith("http") else f"http://{target}"
        result = subprocess.run(
            ["gobuster", "dir", "-u", url, "-w", wordlist],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="gobuster_dir")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="gobuster_dir")


@jarvis_tool(
    name="wpscan",
    description="WPScan WordPress vulnerability scan on authorized target",
    params={"target": {"type": "string", "required": True}},
    rank="R3", scope="NETWORK", category="security", tags=["pentest", "wordpress", "web"]
)
def wpscan(target: str) -> ToolResult:
    try:
        if not _validate_target(target):
            return ToolResult.fail(error=f"Target '{target}' not in authorized scope.", tool_name="wpscan")
        url = target if target.startswith("http") else f"http://{target}"
        result = subprocess.run(
            ["wpscan", "--url", url],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="wpscan")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="wpscan")


@jarvis_tool(
    name="lynis_audit",
    description="Lynis system security audit",
    rank="R4", scope="PRIVILEGED", category="security", tags=["audit", "system", "hardening"]
)
def lynis_audit() -> ToolResult:
    try:
        result = subprocess.run(
            ["lynis", "audit", "system"],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="lynis_audit")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="lynis_audit")


@jarvis_tool(
    name="chkrootkit",
    description="chkrootkit rootkit detection",
    rank="R0", scope="READ", category="security", tags=["rootkit", "malware", "detection"]
)
def chkrootkit() -> ToolResult:
    try:
        result = subprocess.run(
            ["chkrootkit"],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="chkrootkit")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="chkrootkit")


@jarvis_tool(
    name="rkhunter",
    description="rkhunter rootkit hunter",
    rank="R0", scope="READ", category="security", tags=["rootkit", "malware", "detection"]
)
def rkhunter() -> ToolResult:
    try:
        result = subprocess.run(
            ["rkhunter", "--check", "--sk"],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="rkhunter")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="rkhunter")


@jarvis_tool(
    name="openvas_scan",
    description="OpenVAS vulnerability scan on authorized target",
    params={"target": {"type": "string", "required": True}},
    rank="R4", scope="NETWORK", category="security", tags=["pentest", "vulnerability", "scanning"]
)
def openvas_scan(target: str) -> ToolResult:
    try:
        if not _validate_target(target):
            return ToolResult.fail(error=f"Target '{target}' not in authorized scope.", tool_name="openvas_scan")
        result = subprocess.run(
            ["gvm-cli", "socket", "--xml", f"<get_targets/>"],
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="openvas_scan")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="openvas_scan")


@jarvis_tool(
    name="wireshark_tshark",
    description="Tshark packet analysis on network interface",
    params={
        "interface": {"type": "string", "required": False, "default": "any"},
        "duration": {"type": "integer", "required": False, "default": 30},
    },
    rank="R3", scope="NETWORK", category="security", tags=["pentest", "network", "packet"]
)
def wireshark_tshark(interface: str = "any", duration: int = 30) -> ToolResult:
    try:
        result = subprocess.run(
            ["tshark", "-i", interface, "-a", f"duration:{duration}", "-q"],
            capture_output=True, text=True, timeout=duration + 30
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="wireshark_tshark")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="wireshark_tshark")


@jarvis_tool(
    name="aircrack_ng",
    description="Aircrack-ng WiFi cracking",
    params={"cap_file": {"type": "string", "required": True}},
    rank="R5", scope="PRIVILEGED", category="security", tags=["pentest", "wifi", "cracking"]
)
def aircrack_ng(cap_file: str) -> ToolResult:
    try:
        result = subprocess.run(
            ["aircrack-ng", cap_file],
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult.ok(output=output, data={"returncode": result.returncode}, tool_name="aircrack_ng")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="aircrack_ng")
