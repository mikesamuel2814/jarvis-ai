"""
Jarvis v3 System Info Tools (15 tools)
Category: system | Rank: R0 | Scope: READ
"""

import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..decorator import jarvis_tool
from ..result import ToolResult

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))


@jarvis_tool(
    name="cpu_info",
    description="Get CPU model, cores, threads, and current usage percentage",
    rank="R0", scope="READ", category="system", tags=["hardware", "monitoring", "cpu"]
)
def cpu_info() -> ToolResult:
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        count = psutil.cpu_count(logical=True)
        physical = psutil.cpu_count(logical=False)
        freq = psutil.cpu_freq()
        model = "unknown"
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        model = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
        data = {
            "model": model,
            "physical_cores": physical,
            "logical_cores": count,
            "usage_percent": cpu_percent,
            "freq_mhz": freq.current if freq else None,
        }
        output = f"CPU: {model}\nCores: {physical} physical / {count} logical\nUsage: {cpu_percent}%"
        return ToolResult.ok(output=output, data=data, tool_name="cpu_info")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="cpu_info")


@jarvis_tool(
    name="ram_usage",
    description="Get RAM total, used, free, and usage percentage",
    rank="R0", scope="READ", category="system", tags=["hardware", "monitoring", "memory"]
)
def ram_usage() -> ToolResult:
    try:
        import psutil
        mem = psutil.virtual_memory()
        data = {
            "total_gb": round(mem.total / (1024**3), 2),
            "used_gb": round(mem.used / (1024**3), 2),
            "free_gb": round(mem.available / (1024**3), 2),
            "percent": mem.percent,
        }
        output = f"RAM: {data['used_gb']}GB / {data['total_gb']}GB ({data['percent']}% used)"
        return ToolResult.ok(output=output, data=data, tool_name="ram_usage")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="ram_usage")


@jarvis_tool(
    name="gpu_status",
    description="Get NVIDIA GPU status including temperature, usage, and VRAM",
    rank="R0", scope="READ", category="system", tags=["hardware", "monitoring", "gpu"]
)
def gpu_status() -> ToolResult:
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return ToolResult.fail(error="nvidia-smi failed: " + result.stderr, tool_name="gpu_status")
        parts = [p.strip() for p in result.stdout.strip().split(",")]
        data = {
            "name": parts[0] if len(parts) > 0 else "unknown",
            "temp_c": parts[1] if len(parts) > 1 else None,
            "util_percent": parts[2] if len(parts) > 2 else None,
            "vram_used": parts[3] if len(parts) > 3 else None,
            "vram_total": parts[4] if len(parts) > 4 else None,
        }
        return ToolResult.ok(output=result.stdout.strip(), data=data, tool_name="gpu_status")
    except FileNotFoundError:
        return ToolResult.fail(error="nvidia-smi not found. No NVIDIA GPU detected.", tool_name="gpu_status")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="gpu_status")


@jarvis_tool(
    name="disk_space",
    description="Get disk usage for all mounted filesystems",
    rank="R0", scope="READ", category="system", tags=["hardware", "monitoring", "disk"]
)
def disk_space() -> ToolResult:
    try:
        result = subprocess.run(["df", "-h"], capture_output=True, text=True, timeout=10)
        return ToolResult.ok(output=result.stdout, tool_name="disk_space")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="disk_space")


@jarvis_tool(
    name="uptime",
    description="Get system uptime and load averages",
    rank="R0", scope="READ", category="system", tags=["monitoring", "system"]
)
def uptime() -> ToolResult:
    try:
        result = subprocess.run(["uptime"], capture_output=True, text=True, timeout=5)
        return ToolResult.ok(output=result.stdout.strip(), tool_name="uptime")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="uptime")


@jarvis_tool(
    name="os_info",
    description="Get operating system name, version, and architecture",
    rank="R0", scope="READ", category="system", tags=["system", "info"]
)
def os_info() -> ToolResult:
    data = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "node": platform.node(),
    }
    output = f"{data['system']} {data['release']} ({data['machine']})"
    return ToolResult.ok(output=output, data=data, tool_name="os_info")


@jarvis_tool(
    name="kernel_version",
    description="Get Linux kernel version",
    rank="R0", scope="READ", category="system", tags=["system", "kernel"]
)
def kernel_version() -> ToolResult:
    try:
        result = subprocess.run(["uname", "-r"], capture_output=True, text=True, timeout=5)
        kv = result.stdout.strip()
        return ToolResult.ok(output=f"Kernel: {kv}", data={"version": kv}, tool_name="kernel_version")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="kernel_version")


@jarvis_tool(
    name="load_average",
    description="Get 1, 5, and 15-minute load averages",
    rank="R0", scope="READ", category="system", tags=["monitoring", "system"]
)
def load_average() -> ToolResult:
    try:
        import psutil
        load1, load5, load15 = psutil.getloadavg()
        data = {"load_1min": load1, "load_5min": load5, "load_15min": load15}
        output = f"Load average: {load1:.2f} (1m) | {load5:.2f} (5m) | {load15:.2f} (15m)"
        return ToolResult.ok(output=output, data=data, tool_name="load_average")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="load_average")


@jarvis_tool(
    name="temp_sensors",
    description="Get temperature readings from all available sensors",
    rank="R0", scope="READ", category="system", tags=["hardware", "temperature", "monitoring"]
)
def temp_sensors() -> ToolResult:
    try:
        import psutil
        temps = psutil.sensors_temperatures()
        if not temps:
            return ToolResult.ok(output="No temperature sensors available.", tool_name="temp_sensors")
        lines = []
        data = {}
        for name, entries in temps.items():
            for entry in entries:
                lines.append(f"{name} ({entry.label or 'n/a'}): {entry.current}°C")
                data[f"{name}_{entry.label or 'n/a'}"] = entry.current
        return ToolResult.ok(output="\n".join(lines), data=data, tool_name="temp_sensors")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="temp_sensors")


@jarvis_tool(
    name="battery_status",
    description="Get battery charge level and power status",
    rank="R0", scope="READ", category="system", tags=["hardware", "power", "laptop"]
)
def battery_status() -> ToolResult:
    try:
        import psutil
        batt = psutil.sensors_battery()
        if batt is None:
            return ToolResult.ok(output="No battery detected (desktop system).", tool_name="battery_status")
        data = {
            "percent": batt.percent,
            "power_plugged": batt.power_plugged,
            "secs_left": batt.secs_left if batt.secs_left != psutil.POWER_TIME_UNLIMITED else None,
        }
        status = "plugged in" if batt.power_plugged else "on battery"
        output = f"Battery: {batt.percent}% — {status}"
        return ToolResult.ok(output=output, data=data, tool_name="battery_status")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="battery_status")


@jarvis_tool(
    name="usb_devices",
    description="List connected USB devices",
    rank="R0", scope="READ", category="system", tags=["hardware", "usb"]
)
def usb_devices() -> ToolResult:
    try:
        result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=10)
        return ToolResult.ok(output=result.stdout, tool_name="usb_devices")
    except FileNotFoundError:
        return ToolResult.fail(error="lsusb not found", tool_name="usb_devices")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="usb_devices")


@jarvis_tool(
    name="pci_devices",
    description="List PCI devices",
    rank="R0", scope="READ", category="system", tags=["hardware", "pci"]
)
def pci_devices() -> ToolResult:
    try:
        result = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
        return ToolResult.ok(output=result.stdout, tool_name="pci_devices")
    except FileNotFoundError:
        return ToolResult.fail(error="lspci not found", tool_name="pci_devices")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="pci_devices")


@jarvis_tool(
    name="block_devices",
    description="List block devices (disks, partitions)",
    rank="R0", scope="READ", category="system", tags=["hardware", "disk", "storage"]
)
def block_devices() -> ToolResult:
    try:
        result = subprocess.run(["lsblk", "-o", "NAME,SIZE,TYPE,MOUNTPOINT"],
                                capture_output=True, text=True, timeout=10)
        return ToolResult.ok(output=result.stdout, tool_name="block_devices")
    except FileNotFoundError:
        return ToolResult.fail(error="lsblk not found", tool_name="block_devices")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="block_devices")


@jarvis_tool(
    name="memory_map",
    description="Show detailed memory map and hugepages info",
    rank="R0", scope="READ", category="system", tags=["hardware", "memory", "advanced"]
)
def memory_map() -> ToolResult:
    try:
        result = subprocess.run(["cat", "/proc/meminfo"], capture_output=True, text=True, timeout=5)
        return ToolResult.ok(output=result.stdout, tool_name="memory_map")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="memory_map")


@jarvis_tool(
    name="sysctl_params",
    description="Show kernel sysctl parameters (optionally filter by prefix)",
    params={"prefix": {"type": "string", "required": False, "default": ""}},
    rank="R0", scope="READ", category="system", tags=["kernel", "tuning", "advanced"]
)
def sysctl_params(prefix: str = "") -> ToolResult:
    try:
        cmd = ["sysctl", "-a"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        lines = result.stdout.splitlines()
        if prefix:
            lines = [l for l in lines if prefix in l]
        output = "\n".join(lines[:100])  # cap output
        return ToolResult.ok(output=output, tool_name="sysctl_params")
    except FileNotFoundError:
        return ToolResult.fail(error="sysctl not found", tool_name="sysctl_params")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="sysctl_params")
