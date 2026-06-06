"""
Jarvis v3 Tool Result
Standardized return type for all tool executions.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ToolResult:
    """Result of a tool execution."""

    success: bool
    output: str = ""                    # Human-readable output
    data: Dict[str, Any] = field(default_factory=dict)  # Structured data
    error: Optional[str] = None         # Error message if failed
    duration_ms: float = 0.0            # Execution time
    tool_name: str = ""                 # Name of the tool that ran

    def __bool__(self):
        return self.success

    @classmethod
    def ok(cls, output: str = "", data: Optional[Dict[str, Any]] = None, duration_ms: float = 0.0, tool_name: str = "") -> "ToolResult":
        return cls(success=True, output=output, data=data or {}, duration_ms=duration_ms, tool_name=tool_name)

    @classmethod
    def fail(cls, error: str, output: str = "", duration_ms: float = 0.0, tool_name: str = "") -> "ToolResult":
        return cls(success=False, output=output, error=error, duration_ms=duration_ms, tool_name=tool_name)
