"""
Jarvis v3 Tool Framework
Public API for tools.
"""

import importlib
import logging

from .result import ToolResult
from .decorator import jarvis_tool, get_tool_metadata, list_registered_tools, get_tool_func
from .registry import ToolRegistry

_log = logging.getLogger("jarvis.tools")

# All 13 core categories. Importing each module triggers @jarvis_tool
# registration as a side effect.
CORE_MODULES = [
    "system", "process", "file", "network", "security", "security_audit",
    "dev", "database", "docker", "web", "backup", "automation", "comms",
]

_loaded = False


def _load_dynamic_tools() -> None:
    """Import the latest version of each deployed dynamic tool (tools/dynamic/
    <name>/vN/tool.py) so auto-built tools self-register."""
    import importlib.util
    from pathlib import Path

    dyn = Path(__file__).parent / "dynamic"
    if not dyn.is_dir():
        return
    for tool_dir in dyn.iterdir():
        if not tool_dir.is_dir():
            continue
        versions = sorted(
            (p for p in tool_dir.glob("v*") if p.name[1:].isdigit()),
            key=lambda p: int(p.name[1:]),
        )
        if not versions:
            continue
        tool_py = versions[-1] / "tool.py"
        if not tool_py.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"tools.dynamic.{tool_dir.name}", tool_py)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to load dynamic tool %s: %s", tool_dir.name, exc)


def load_all_tools() -> int:
    """Import every core tool module (and deployed dynamic tools) so all tools
    self-register. Idempotent. Returns the number of registered tools."""
    global _loaded
    if not _loaded:
        for mod in CORE_MODULES:
            try:
                importlib.import_module(f"tools.core.{mod}")
            except Exception as exc:  # noqa: BLE001
                _log.warning("Failed to load tool module %s: %s", mod, exc)
        _load_dynamic_tools()
        _loaded = True
    return len(list_registered_tools())


__all__ = [
    "ToolResult",
    "jarvis_tool",
    "get_tool_metadata",
    "list_registered_tools",
    "get_tool_func",
    "ToolRegistry",
    "load_all_tools",
]
