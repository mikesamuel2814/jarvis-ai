"""
Jarvis v3 Network Tools (12 tools)
Category: network | Ranks: R0-R3 | Scopes: READ, NETWORK
"""

import os
import subprocess
from pathlib import Path
from typing import Optional

from ..decorator import jarvis_tool
from ..result import ToolResult

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
AUTHORIZED_TARGETS = JARVIS_HOME / "config" / "authorized_targets.txt"


def _load_authorized_targets() -> set:
    targets = set()
    if AUTHORIZED_TARGETS.exists():
        with open(AUTHORIZED_TARGETS) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    targets.add(line)
    return targets


def _run_cmd(cmd: list, timeout: int = 30) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        err = result.stderr.strip() or "Command failed"
        raise RuntimeError(err)
    return result.stdout


@jarvis_tool(
    name="ifconfig",
    description="Network interface configuration",
    rank="R0", scope="READ", category="network", tags=["network", "interfaces"]
)
def ifconfig() -> ToolResult:
    try:
        output = _run_cmd(["ifconfig"], timeout=10)
        return ToolResult.ok(output=output, tool_name="ifconfig")
    except FileNotFoundError:
        try:
            output = _run_cmd(["ip", "addr"], timeout=10)
            return ToolResult.ok(output=output, tool_name="ifconfig")
        except Exception as exc:
            return ToolResult.fail(error=str(exc), tool_name="ifconfig")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="ifconfig")


@jarvis_tool(
    name="ping_host",
    description="Ping a host",
    params={"host": {"type": "string", "required": True}, "count": {"type": "integer", "required": False, "default": 4}},
    rank="R0", scope="READ", category="network", tags=["network", "ping", "diagnostics"]
)
def ping_host(host: str, count: int = 4) -> ToolResult:
    try:
        output = _run_cmd(["ping", "-c", str(count), host], timeout=count * 5 + 5)
        return ToolResult.ok(output=output, tool_name="ping_host")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="ping_host")


@jarvis_tool(
    name="traceroute",
    description="Traceroute to a host",
    params={"host": {"type": "string", "required": True}},
    rank="R0", scope="READ", category="network", tags=["network", "traceroute", "diagnostics"]
)
def traceroute(host: str) -> ToolResult:
    try:
        output = _run_cmd(["traceroute", "-m", "30", host], timeout=60)
        return ToolResult.ok(output=output, tool_name="traceroute")
    except FileNotFoundError:
        try:
            output = _run_cmd(["tracepath", host], timeout=60)
            return ToolResult.ok(output=output, tool_name="traceroute")
        except Exception as exc:
            return ToolResult.fail(error=str(exc), tool_name="traceroute")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="traceroute")


@jarvis_tool(
    name="netstat",
    description="Network connections and statistics",
    rank="R0", scope="READ", category="network", tags=["network", "connections", "statistics"]
)
def netstat() -> ToolResult:
    try:
        output = _run_cmd(["netstat", "-tunapl"], timeout=15)
        return ToolResult.ok(output=output, tool_name="netstat")
    except FileNotFoundError:
        return ToolResult.fail(error="netstat not found", tool_name="netstat")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="netstat")


@jarvis_tool(
    name="ss_sockets",
    description="Socket statistics (modern netstat)",
    rank="R0", scope="READ", category="network", tags=["network", "sockets", "statistics"]
)
def ss_sockets() -> ToolResult:
    try:
        output = _run_cmd(["ss", "-tunapl"], timeout=15)
        return ToolResult.ok(output=output, tool_name="ss_sockets")
    except FileNotFoundError:
        return ToolResult.fail(error="ss not found", tool_name="ss_sockets")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="ss_sockets")


@jarvis_tool(
    name="curl_request",
    description="HTTP request via curl",
    params={
        "url": {"type": "string", "required": True},
        "method": {"type": "string", "required": False, "default": "GET"},
        "timeout": {"type": "integer", "required": False, "default": 30},
    },
    rank="R0", scope="READ", category="network", tags=["network", "http", "curl"]
)
def curl_request(url: str, method: str = "GET", timeout: int = 30) -> ToolResult:
    try:
        cmd = ["curl", "-sSL", "-m", str(timeout), "-X", method.upper()]
        cmd.append(url)
        output = _run_cmd(cmd, timeout=timeout + 5)
        return ToolResult.ok(output=output, tool_name="curl_request")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="curl_request")


@jarvis_tool(
    name="wget_download",
    description="Download file via wget",
    params={
        "url": {"type": "string", "required": True},
        "output_path": {"type": "string", "required": True},
    },
    rank="R0", scope="READ", category="network", tags=["network", "download", "wget"]
)
def wget_download(url: str, output_path: str) -> ToolResult:
    try:
        output = _run_cmd(["wget", "-O", output_path, url], timeout=120)
        return ToolResult.ok(output=output, tool_name="wget_download")
    except FileNotFoundError:
        return ToolResult.fail(error="wget not found", tool_name="wget_download")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="wget_download")


@jarvis_tool(
    name="dig_dns",
    description="DNS lookup via dig",
    params={"domain": {"type": "string", "required": True}, "record_type": {"type": "string", "required": False, "default": "A"}},
    rank="R0", scope="READ", category="network", tags=["network", "dns", "dig"]
)
def dig_dns(domain: str, record_type: str = "A") -> ToolResult:
    try:
        output = _run_cmd(["dig", "+short", domain, record_type.upper()], timeout=15)
        return ToolResult.ok(output=output, tool_name="dig_dns")
    except FileNotFoundError:
        return ToolResult.fail(error="dig not found", tool_name="dig_dns")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="dig_dns")


@jarvis_tool(
    name="whois_lookup",
    description="WHOIS domain lookup",
    params={"domain": {"type": "string", "required": True}},
    rank="R0", scope="READ", category="network", tags=["network", "whois", "domain"]
)
def whois_lookup(domain: str) -> ToolResult:
    try:
        output = _run_cmd(["whois", domain], timeout=30)
        return ToolResult.ok(output=output, tool_name="whois_lookup")
    except FileNotFoundError:
        return ToolResult.fail(error="whois not found", tool_name="whois_lookup")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="whois_lookup")


@jarvis_tool(
    name="nmap_scan",
    description="Nmap port scan",
    params={
        "host": {"type": "string", "required": True},
        "ports": {"type": "string", "required": False, "default": ""},
        "args": {"type": "string", "required": False, "default": "-sV"},
    },
    rank="R3", scope="NETWORK", category="network", tags=["network", "nmap", "scan", "security"]
)
def nmap_scan(host: str, ports: str = "", args: str = "-sV") -> ToolResult:
    try:
        authorized = _load_authorized_targets()
        if host not in authorized:
            return ToolResult.fail(
                error=f"Host '{host}' is not in authorized_targets.txt. Scan aborted.",
                tool_name="nmap_scan"
            )
        cmd = ["nmap"] + args.split()
        if ports:
            cmd += ["-p", ports]
        cmd.append(host)
        output = _run_cmd(cmd, timeout=300)
        return ToolResult.ok(output=output, tool_name="nmap_scan")
    except FileNotFoundError:
        return ToolResult.fail(error="nmap not found", tool_name="nmap_scan")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="nmap_scan")


@jarvis_tool(
    name="tcpdump_capture",
    description="Capture packets",
    params={
        "interface": {"type": "string", "required": True},
        "count": {"type": "integer", "required": False, "default": 10},
        "filter_expr": {"type": "string", "required": False, "default": ""},
        "timeout": {"type": "integer", "required": True},
    },
    rank="R3", scope="NETWORK", category="network", tags=["network", "tcpdump", "packets", "capture"]
)
def tcpdump_capture(interface: str, count: int = 10, filter_expr: str = "", timeout: int = 0) -> ToolResult:
    try:
        if timeout <= 0:
            return ToolResult.fail(error="timeout must be > 0", tool_name="tcpdump_capture")
        cmd = ["sudo", "tcpdump", "-i", interface, "-c", str(count), "-nn"]
        if filter_expr:
            cmd.append(filter_expr)
        output = _run_cmd(cmd, timeout=timeout + 10)
        return ToolResult.ok(output=output, tool_name="tcpdump_capture")
    except FileNotFoundError:
        return ToolResult.fail(error="tcpdump not found", tool_name="tcpdump_capture")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="tcpdump_capture")


@jarvis_tool(
    name="iptables_rules",
    description="Show iptables rules",
    rank="R0", scope="READ", category="network", tags=["network", "firewall", "iptables"]
)
def iptables_rules() -> ToolResult:
    try:
        output = _run_cmd(["sudo", "iptables", "-L", "-n", "-v"], timeout=15)
        return ToolResult.ok(output=output, tool_name="iptables_rules")
    except FileNotFoundError:
        return ToolResult.fail(error="iptables not found", tool_name="iptables_rules")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="iptables_rules")
