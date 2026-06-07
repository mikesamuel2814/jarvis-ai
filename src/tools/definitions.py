"""
JARVIS Tool Definitions
JSON schemas and metadata for all tools.
Integrates with the existing v3 tools/ ecosystem.
"""
from typing import Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum


class ToolScope(str, Enum):
    READ = "READ"
    LOCAL = "LOCAL"
    NETWORK = "NETWORK"
    PRIVILEGED = "PRIVILEGED"


class ToolRank(str, Enum):
    R0 = "R0"  # Read-Only, auto-run
    R1 = "R1"  # User-Space, auto-run
    R2 = "R2"  # File Modifier, ask first time
    R3 = "R3"  # Service Controller, ask first time
    R4 = "R4"  # System Modifier, always ask
    R5 = "R5"  # Security-Critical, always ask
    R6 = "R6"  # Destructive, typed confirm + countdown


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    rank: ToolRank = ToolRank.R0
    scope: ToolScope = ToolScope.READ
    category: str = "system"
    tags: List[str] = field(default_factory=list)
    return_schema: Dict[str, Any] = field(default_factory=dict)


# Core tool definitions aligned with v3 tools/core/ categories
TOOL_DEFINITIONS: Dict[str, ToolDefinition] = {
    "cpu_info": ToolDefinition(
        name="cpu_info",
        description="Get CPU information and usage statistics.",
        parameters={
            "type": "object",
            "properties": {},
            "required": []
        },
        rank=ToolRank.R0,
        scope=ToolScope.READ,
        category="system",
        tags=["system", "info", "hardware"]
    ),
    "ram_usage": ToolDefinition(
        name="ram_usage",
        description="Get RAM usage statistics.",
        parameters={
            "type": "object",
            "properties": {},
            "required": []
        },
        rank=ToolRank.R0,
        scope=ToolScope.READ,
        category="system",
        tags=["system", "info", "hardware"]
    ),
    "file_read": ToolDefinition(
        name="file_read",
        description="Read contents of a file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "limit": {"type": "integer", "description": "Max lines to read", "default": 100}
            },
            "required": ["path"]
        },
        rank=ToolRank.R0,
        scope=ToolScope.READ,
        category="file",
        tags=["file", "read"]
    ),
    "file_write": ToolDefinition(
        name="file_write",
        description="Write content to a file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
                "append": {"type": "boolean", "default": False}
            },
            "required": ["path", "content"]
        },
        rank=ToolRank.R2,
        scope=ToolScope.LOCAL,
        category="file",
        tags=["file", "write"]
    ),
    "shell_exec": ToolDefinition(
        name="shell_exec",
        description="Execute a shell command with safety constraints.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "integer", "default": 30, "description": "Timeout in seconds"},
                "cwd": {"type": "string", "description": "Working directory"}
            },
            "required": ["command"]
        },
        rank=ToolRank.R2,
        scope=ToolScope.LOCAL,
        category="automation",
        tags=["shell", "execute", "automation"]
    ),
    "service_restart": ToolDefinition(
        name="service_restart",
        description="Restart a system service.",
        parameters={
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name"},
                "sudo": {"type": "boolean", "default": True}
            },
            "required": ["service"]
        },
        rank=ToolRank.R3,
        scope=ToolScope.PRIVILEGED,
        category="process",
        tags=["service", "restart", "systemd"]
    ),
    "web_search": ToolDefinition(
        name="web_search",
        description="Search the web for information.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "num_results": {"type": "integer", "default": 5}
            },
            "required": ["query"]
        },
        rank=ToolRank.R0,
        scope=ToolScope.NETWORK,
        category="web",
        tags=["web", "search", "network"]
    ),
}


def get_tool_definition(name: str) -> ToolDefinition:
    if name not in TOOL_DEFINITIONS:
        raise KeyError(f"Tool '{name}' not defined.")
    return TOOL_DEFINITIONS[name]


def list_tools_by_category(category: str) -> List[ToolDefinition]:
    return [t for t in TOOL_DEFINITIONS.values() if t.category == category]


def list_tools_by_scope(scope: ToolScope) -> List[ToolDefinition]:
    return [t for t in TOOL_DEFINITIONS.values() if t.scope == scope]
