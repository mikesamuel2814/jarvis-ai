"""
Jarvis v3 Docker & Container Tools (8 tools)
Category: docker | Rank: R0-R3 | Scope: READ, LOCAL, PRIVILEGED
"""

import subprocess
from typing import Optional

from ..decorator import jarvis_tool
from ..result import ToolResult


@jarvis_tool(
    name="docker_ps",
    description="List running Docker containers",
    rank="R0", scope="READ", category="docker", tags=["docker", "containers", "list"]
)
def docker_ps() -> ToolResult:
    try:
        result = subprocess.run(["docker", "ps", "--format", "table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"],
                                capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr.strip(), tool_name="docker_ps")
        return ToolResult.ok(output=result.stdout.strip(), tool_name="docker_ps")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="docker_ps")


@jarvis_tool(
    name="docker_logs",
    description="Get container logs (container, tail)",
    params={"container": {"type": "string", "required": True}, "tail": {"type": "integer", "required": False, "default": 100}},
    rank="R0", scope="READ", category="docker", tags=["docker", "logs", "container"]
)
def docker_logs(container: str, tail: int = 100) -> ToolResult:
    try:
        result = subprocess.run(["docker", "logs", "--tail", str(tail), container],
                                capture_output=True, text=True, timeout=30)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="docker_logs")
        return ToolResult.ok(output=output.strip(), tool_name="docker_logs")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="docker_logs")


@jarvis_tool(
    name="docker_exec",
    description="Execute command in container (container, command)",
    params={"container": {"type": "string", "required": True}, "command": {"type": "string", "required": False, "default": "sh"}},
    rank="R2", scope="LOCAL", category="docker", tags=["docker", "exec", "container"]
)
def docker_exec(container: str, command: str = "sh") -> ToolResult:
    try:
        result = subprocess.run(["docker", "exec", container] + command.split(),
                                capture_output=True, text=True, timeout=30)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="docker_exec")
        return ToolResult.ok(output=output.strip(), tool_name="docker_exec")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="docker_exec")


@jarvis_tool(
    name="docker_compose",
    description="Run docker compose command (file, command)",
    params={"file": {"type": "string", "required": False, "default": "docker-compose.yml"}, "command": {"type": "string", "required": True}},
    rank="R2", scope="LOCAL", category="docker", tags=["docker", "compose", "orchestration"]
)
def docker_compose(command: str, file: str = "docker-compose.yml") -> ToolResult:
    try:
        result = subprocess.run(["docker", "compose", "-f", file] + command.split(),
                                capture_output=True, text=True, timeout=120)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="docker_compose")
        return ToolResult.ok(output=output.strip(), tool_name="docker_compose")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="docker_compose")


@jarvis_tool(
    name="docker_network",
    description="List Docker networks",
    rank="R0", scope="READ", category="docker", tags=["docker", "network", "list"]
)
def docker_network() -> ToolResult:
    try:
        result = subprocess.run(["docker", "network", "ls"], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr.strip(), tool_name="docker_network")
        return ToolResult.ok(output=result.stdout.strip(), tool_name="docker_network")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="docker_network")


@jarvis_tool(
    name="docker_volume",
    description="List Docker volumes",
    rank="R0", scope="READ", category="docker", tags=["docker", "volume", "list"]
)
def docker_volume() -> ToolResult:
    try:
        result = subprocess.run(["docker", "volume", "ls"], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr.strip(), tool_name="docker_volume")
        return ToolResult.ok(output=result.stdout.strip(), tool_name="docker_volume")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="docker_volume")


@jarvis_tool(
    name="qemu_vm",
    description="QEMU VM status/start/stop (action, name)",
    params={"action": {"type": "string", "required": True}, "name": {"type": "string", "required": True}},
    rank="R3", scope="PRIVILEGED", category="docker", tags=["qemu", "vm", "virtualization"]
)
def qemu_vm(action: str, name: str) -> ToolResult:
    try:
        action = action.lower()
        if action == "status":
            cmd = ["virsh", "domstate", name]
        elif action == "start":
            cmd = ["virsh", "start", name]
        elif action == "stop":
            cmd = ["virsh", "shutdown", name]
        elif action == "destroy":
            cmd = ["virsh", "destroy", name]
        else:
            return ToolResult.fail(error=f"Unsupported QEMU action: {action}", tool_name="qemu_vm")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="qemu_vm")
        return ToolResult.ok(output=output.strip(), tool_name="qemu_vm")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="qemu_vm")


@jarvis_tool(
    name="lxc_container",
    description="LXC container list/start/stop (action, name)",
    params={"action": {"type": "string", "required": True}, "name": {"type": "string", "required": False, "default": ""}},
    rank="R2", scope="LOCAL", category="docker", tags=["lxc", "container", "virtualization"]
)
def lxc_container(action: str, name: str = "") -> ToolResult:
    try:
        action = action.lower()
        if action == "list":
            cmd = ["lxc", "list"]
        elif action == "start":
            cmd = ["lxc", "start", name]
        elif action == "stop":
            cmd = ["lxc", "stop", name]
        elif action == "info":
            cmd = ["lxc", "info", name]
        else:
            return ToolResult.fail(error=f"Unsupported LXC action: {action}", tool_name="lxc_container")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="lxc_container")
        return ToolResult.ok(output=output.strip(), tool_name="lxc_container")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="lxc_container")
