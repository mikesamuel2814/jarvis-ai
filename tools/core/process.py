"""
Jarvis v3 Process & Service Tools (12 tools)
Category: process | Ranks: R0/R3 | Scope: READ/LOCAL
"""

import subprocess
from pathlib import Path
from typing import Optional

from ..decorator import jarvis_tool
from ..result import ToolResult


@jarvis_tool(
    name="ps_list",
    description="List running processes with CPU and memory usage",
    rank="R0", scope="READ", category="process", tags=["process", "monitoring", "ps"]
)
def ps_list() -> ToolResult:
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                info = p.info
                procs.append(
                    f"{info['pid']:>6} {info['name']:<20} "
                    f"CPU:{info['cpu_percent'] or 0:>5.1f}% MEM:{info['memory_percent'] or 0:>5.1f}% {info['status']}"
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        output = "\n".join(["   PID NAME                 CPU%  MEM% STATUS"] + procs)
        return ToolResult.ok(output=output, data={"count": len(procs)}, tool_name="ps_list")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="ps_list")


@jarvis_tool(
    name="top_processes",
    description="Top N processes by CPU or memory usage",
    params={
        "n": {"type": "integer", "required": False, "default": 10},
        "sort_by": {"type": "string", "required": False, "default": "cpu"}
    },
    rank="R0", scope="READ", category="process", tags=["process", "monitoring", "top"]
)
def top_processes(n: int = 10, sort_by: str = "cpu") -> ToolResult:
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                info = p.info
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "cpu": info["cpu_percent"] or 0.0,
                    "mem": info["memory_percent"] or 0.0,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        key = "cpu" if sort_by.lower() in ("cpu", "cpu_percent") else "mem"
        procs.sort(key=lambda x: x[key], reverse=True)
        top = procs[:n]
        lines = [f"{'PID':>6} {'NAME':<20} {'CPU%':>6} {'MEM%':>6}"]
        for p in top:
            lines.append(f"{p['pid']:>6} {p['name']:<20} {p['cpu']:>6.1f} {p['mem']:>6.1f}")
        output = "\n".join(lines)
        return ToolResult.ok(output=output, data={"processes": top}, tool_name="top_processes")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="top_processes")


@jarvis_tool(
    name="kill_process",
    description="Kill a process by PID or name",
    params={
        "pid": {"type": "integer", "required": False},
        "name": {"type": "string", "required": False}
    },
    rank="R3", scope="LOCAL", category="process", tags=["process", "kill", "manage"]
)
def kill_process(pid: Optional[int] = None, name: Optional[str] = None) -> ToolResult:
    try:
        import psutil
        if pid is not None:
            p = psutil.Process(pid)
            pname = p.name()
            p.terminate()
            p.wait(timeout=5)
            return ToolResult.ok(output=f"Process {pid} ({pname}) terminated.", tool_name="kill_process")
        elif name is not None:
            killed = []
            for p in psutil.process_iter(["pid", "name"]):
                if p.info["name"] == name:
                    p.terminate()
                    killed.append(p.info["pid"])
            if killed:
                return ToolResult.ok(
                    output=f"Terminated {len(killed)} process(es) named '{name}': {killed}",
                    tool_name="kill_process"
                )
            else:
                return ToolResult.fail(error=f"No process named '{name}' found.", tool_name="kill_process")
        else:
            return ToolResult.fail(error="Specify either pid or name.", tool_name="kill_process")
    except psutil.NoSuchProcess:
        return ToolResult.fail(error=f"Process {pid} does not exist.", tool_name="kill_process")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="kill_process")


@jarvis_tool(
    name="nice_renice",
    description="Change process priority (nice value)",
    params={
        "pid": {"type": "integer", "required": True},
        "priority": {"type": "integer", "required": True}
    },
    rank="R3", scope="LOCAL", category="process", tags=["process", "priority", "nice"]
)
def nice_renice(pid: int, priority: int) -> ToolResult:
    try:
        import psutil
        p = psutil.Process(pid)
        old = p.nice()
        p.nice(priority)
        return ToolResult.ok(
            output=f"Nice of PID {pid} changed from {old} to {priority}.",
            data={"old": old, "new": priority},
            tool_name="nice_renice"
        )
    except psutil.NoSuchProcess:
        return ToolResult.fail(error=f"Process {pid} does not exist.", tool_name="nice_renice")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="nice_renice")


@jarvis_tool(
    name="pm2_status",
    description="PM2 process list status",
    rank="R0", scope="READ", category="process", tags=["pm2", "process", "monitoring"]
)
def pm2_status() -> ToolResult:
    try:
        result = subprocess.run(["pm2", "status"], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr or "pm2 status failed", tool_name="pm2_status")
        return ToolResult.ok(output=result.stdout, tool_name="pm2_status")
    except FileNotFoundError:
        return ToolResult.fail(error="pm2 not found", tool_name="pm2_status")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="pm2_status")


@jarvis_tool(
    name="pm2_restart",
    description="Restart a PM2 process",
    params={"name": {"type": "string", "required": True}},
    rank="R3", scope="LOCAL", category="process", tags=["pm2", "process", "restart"]
)
def pm2_restart(name: str) -> ToolResult:
    try:
        result = subprocess.run(["pm2", "restart", name], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr or "pm2 restart failed", tool_name="pm2_restart")
        return ToolResult.ok(output=result.stdout, tool_name="pm2_restart")
    except FileNotFoundError:
        return ToolResult.fail(error="pm2 not found", tool_name="pm2_restart")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="pm2_restart")


@jarvis_tool(
    name="systemctl_list",
    description="List systemd services",
    params={"state": {"type": "string", "required": False, "default": ""}},
    rank="R0", scope="READ", category="process", tags=["systemd", "service", "list"]
)
def systemctl_list(state: str = "") -> ToolResult:
    try:
        cmd = ["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"]
        if state:
            cmd.append(f"--state={state}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return ToolResult.ok(output=result.stdout, tool_name="systemctl_list")
    except FileNotFoundError:
        return ToolResult.fail(error="systemctl not found", tool_name="systemctl_list")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="systemctl_list")


@jarvis_tool(
    name="service_start",
    description="Start a systemd service",
    params={"service": {"type": "string", "required": True}},
    rank="R3", scope="LOCAL", category="process", tags=["systemd", "service", "start"]
)
def service_start(service: str) -> ToolResult:
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "start", service],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr or "start failed", tool_name="service_start")
        return ToolResult.ok(output=f"Service {service} started.", tool_name="service_start")
    except FileNotFoundError:
        return ToolResult.fail(error="systemctl not found", tool_name="service_start")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="service_start")


@jarvis_tool(
    name="service_stop",
    description="Stop a systemd service",
    params={"service": {"type": "string", "required": True}},
    rank="R3", scope="LOCAL", category="process", tags=["systemd", "service", "stop"]
)
def service_stop(service: str) -> ToolResult:
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "stop", service],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr or "stop failed", tool_name="service_stop")
        return ToolResult.ok(output=f"Service {service} stopped.", tool_name="service_stop")
    except FileNotFoundError:
        return ToolResult.fail(error="systemctl not found", tool_name="service_stop")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="service_stop")


@jarvis_tool(
    name="service_restart",
    description="Restart a systemd service",
    params={"service": {"type": "string", "required": True}},
    rank="R3", scope="LOCAL", category="process", tags=["systemd", "service", "restart"]
)
def service_restart(service: str) -> ToolResult:
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", service],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return ToolResult.fail(error=result.stderr or "restart failed", tool_name="service_restart")
        return ToolResult.ok(output=f"Service {service} restarted.", tool_name="service_restart")
    except FileNotFoundError:
        return ToolResult.fail(error="systemctl not found", tool_name="service_restart")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="service_restart")


@jarvis_tool(
    name="cron_jobs",
    description="List current user's cron jobs",
    rank="R0", scope="READ", category="process", tags=["cron", "schedule", "jobs"]
)
def cron_jobs() -> ToolResult:
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult.ok(output="No crontab for user.", tool_name="cron_jobs")
        return ToolResult.ok(output=result.stdout, tool_name="cron_jobs")
    except FileNotFoundError:
        return ToolResult.fail(error="crontab not found", tool_name="cron_jobs")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="cron_jobs")


@jarvis_tool(
    name="systemd_timer",
    description="List systemd timers",
    rank="R0", scope="READ", category="process", tags=["systemd", "timer", "schedule"]
)
def systemd_timer() -> ToolResult:
    try:
        result = subprocess.run(
            ["systemctl", "list-timers", "--no-pager", "--no-legend"],
            capture_output=True, text=True, timeout=10
        )
        return ToolResult.ok(output=result.stdout, tool_name="systemd_timer")
    except FileNotFoundError:
        return ToolResult.fail(error="systemctl not found", tool_name="systemd_timer")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="systemd_timer")
