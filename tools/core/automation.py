"""
Jarvis v3 Automation Tools (6 tools)
Category: automation | Rank: R2-R3 | Scope: LOCAL/NETWORK
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional

from ..decorator import jarvis_tool
from ..result import ToolResult

# ── Scope validation for ssh_remote (reuses Jarvis authorized targets) ─────────
import kali_tools


def _ssh_scope_check(host: str) -> dict:
    return kali_tools.validate_scope(host)


@jarvis_tool(
    name="ansible_play",
    description="Run an Ansible playbook with an optional inventory file",
    params={
        "playbook": {"type": "string", "required": True},
        "inventory": {"type": "string", "required": False, "default": "localhost,"},
    },
    rank="R3",
    scope="NETWORK",
    category="automation",
    tags=["ansible", "playbook", "orchestration"],
)
def ansible_play(playbook: str, inventory: str = "localhost,") -> ToolResult:
    try:
        cmd = ["ansible-playbook", "-i", inventory, playbook]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="ansible_play")
        return ToolResult.ok(output=output, tool_name="ansible_play")
    except FileNotFoundError:
        return ToolResult.fail(error="ansible-playbook not found", tool_name="ansible_play")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="ansible_play")


@jarvis_tool(
    name="bash_script",
    description="Execute bash script content with an optional timeout",
    params={
        "script": {"type": "string", "required": True},
        "timeout": {"type": "integer", "required": False, "default": 60},
    },
    rank="R3",
    scope="LOCAL",
    category="automation",
    tags=["bash", "script", "shell"],
)
def bash_script(script: str, timeout: int = 60) -> ToolResult:
    try:
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="bash_script")
        return ToolResult.ok(output=output, tool_name="bash_script")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="bash_script")


@jarvis_tool(
    name="python_script",
    description="Execute Python script content with an optional timeout",
    params={
        "script": {"type": "string", "required": True},
        "timeout": {"type": "integer", "required": False, "default": 60},
    },
    rank="R2",
    scope="LOCAL",
    category="automation",
    tags=["python", "script", "code"],
)
def python_script(script: str, timeout: int = 60) -> ToolResult:
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="python_script")
        return ToolResult.ok(output=output, tool_name="python_script")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="python_script")


@jarvis_tool(
    name="expect_automate",
    description="Automate interactive commands using expect-style responses",
    params={
        "command": {"type": "string", "required": True},
        "responses": {"type": "object", "required": True},
    },
    rank="R2",
    scope="LOCAL",
    category="automation",
    tags=["expect", "interactive", "automation"],
)
def expect_automate(command: str, responses: dict) -> ToolResult:
    try:
        # Build an expect script dynamically
        expect_script_lines = [
            "spawn bash -c {",
            command,
            "}",
        ]
        for prompt, response in responses.items():
            expect_script_lines.append(f'expect "{prompt}"')
            expect_script_lines.append(f'send "{response}\\r"')
        expect_script_lines.append("expect eof")
        expect_script = "\n".join(expect_script_lines)

        result = subprocess.run(
            ["expect", "-"],
            input=expect_script,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="expect_automate")
        return ToolResult.ok(output=output, tool_name="expect_automate")
    except FileNotFoundError:
        return ToolResult.fail(error="expect not found", tool_name="expect_automate")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="expect_automate")


@jarvis_tool(
    name="xdotool_gui",
    description="Execute an X11 automation command with xdotool",
    params={
        "command": {"type": "string", "required": True},
    },
    rank="R2",
    scope="LOCAL",
    category="automation",
    tags=["xdotool", "x11", "gui", "automation"],
)
def xdotool_gui(command: str) -> ToolResult:
    try:
        cmd = ["xdotool"] + command.split()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="xdotool_gui")
        return ToolResult.ok(output=output, tool_name="xdotool_gui")
    except FileNotFoundError:
        return ToolResult.fail(error="xdotool not found", tool_name="xdotool_gui")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="xdotool_gui")


@jarvis_tool(
    name="ssh_remote",
    description="Run a command on a remote host via SSH (StrictHostKeyChecking=accept-new). Validates authorized scope.",
    params={
        "host": {"type": "string", "required": True},
        "user": {"type": "string", "required": True},
        "command": {"type": "string", "required": True},
    },
    rank="R3",
    scope="NETWORK",
    category="automation",
    tags=["ssh", "remote", "network"],
)
def ssh_remote(host: str, user: str, command: str) -> ToolResult:
    try:
        scope = _ssh_scope_check(host)
        if not scope.get("in_scope", False):
            return ToolResult.fail(
                error=f"Host {host} is not in authorized scope: {scope.get('reason')}",
                tool_name="ssh_remote",
            )
        cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "BatchMode=yes",
            f"{user}@{host}",
            command,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output, tool_name="ssh_remote")
        return ToolResult.ok(output=output, data={"scope": scope}, tool_name="ssh_remote")
    except FileNotFoundError:
        return ToolResult.fail(error="ssh not found", tool_name="ssh_remote")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="ssh_remote")
