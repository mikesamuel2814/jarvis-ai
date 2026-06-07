"""
JARVIS Tool Executors
Safe execution layer for all tools.
Integrates with v3 security/scope_enforcer.py and safety_rules.json.
"""
import os
import re
import json
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple
from .definitions import ToolDefinition, ToolScope, ToolRank, get_tool_definition

JARVIS_DIR = Path("/home/kali/.jarvis")
SAFETY_RULES_PATH = JARVIS_DIR / "config" / "safety_rules.json"


def load_safety_rules() -> Dict[str, Any]:
    if SAFETY_RULES_PATH.exists():
        with open(SAFETY_RULES_PATH) as f:
            return json.load(f)
    return {"absolute_blocks": [], "resource_limits": {}, "privacy_rules": {}}


def check_safety(command: str) -> Tuple[bool, str]:
    """Check command against safety rules. Returns (allowed, reason)."""
    rules = load_safety_rules()
    for block in rules.get("absolute_blocks", []):
        pattern = block.get("pattern", "")
        if re.search(pattern, command):
            if block.get("action") == "block":
                return False, f"SAFETY BLOCK: {block.get('reason', pattern)}"
            elif block.get("action") == "confirm":
                return True, f"SAFETY CONFIRM REQUIRED: {block.get('reason', pattern)}"
    return True, "OK"


def check_privacy(data: str) -> Tuple[bool, str]:
    """Ensure no secrets are leaked externally."""
    rules = load_safety_rules()
    forbidden = rules.get("privacy_rules", {}).get("never_externally_send", [])
    for term in forbidden:
        if term.lower() in data.lower():
            return False, f"PRIVACY VIOLATION: '{term}' detected in output"
    return True, "OK"


class ToolExecutor:
    """Execute tools with safety, scope, and privacy checks."""

    def __init__(self, session_scope: ToolScope = ToolScope.READ):
        self.session_scope = session_scope

    def execute(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool by name with parameters."""
        try:
            tool_def = get_tool_definition(tool_name)
        except KeyError as e:
            return {"status": "error", "output": str(e)}

        # Scope check
        if tool_def.scope.value > self.session_scope.value:
            return {
                "status": "denied",
                "output": f"Scope denied: {tool_def.name} requires {tool_def.scope.value}, session is {self.session_scope.value}"
            }

        # Execute by name
        method = getattr(self, f"exec_{tool_name}", None)
        if method is None:
            return {"status": "error", "output": f"No executor implemented for '{tool_name}'"}

        try:
            result = method(parameters)
            return {"status": "success", "output": result}
        except Exception as e:
            return {"status": "error", "output": str(e)}

    def exec_cpu_info(self, params: Dict[str, Any]) -> str:
        result = subprocess.run(
            ["lscpu"], capture_output=True, text=True, timeout=10
        )
        return result.stdout[:2000]

    def exec_ram_usage(self, params: Dict[str, Any]) -> str:
        result = subprocess.run(
            ["free", "-h"], capture_output=True, text=True, timeout=10
        )
        return result.stdout

    def exec_file_read(self, params: Dict[str, Any]) -> str:
        path = Path(params["path"]).expanduser().resolve()
        limit = params.get("limit", 100)

        # Block dangerous paths
        blocked = ["/etc/shadow", "/etc/sudoers", "/root/.ssh", "/boot"]
        for b in blocked:
            if str(path).startswith(b):
                raise PermissionError(f"Access to {path} is blocked by safety policy.")

        with open(path) as f:
            lines = f.readlines()[:limit]
        return "".join(lines)

    def exec_file_write(self, params: Dict[str, Any]) -> str:
        path = Path(params["path"]).expanduser().resolve()
        content = params["content"]
        append = params.get("append", False)
        mode = "a" if append else "w"
        with open(path, mode) as f:
            f.write(content)
        return f"Wrote {len(content)} chars to {path}"

    def exec_shell_exec(self, params: Dict[str, Any]) -> str:
        command = params["command"]
        timeout = params.get("timeout", 30)
        cwd = params.get("cwd")

        allowed, reason = check_safety(command)
        if not allowed:
            raise PermissionError(reason)

        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd
        )
        output = result.stdout + result.stderr
        return output[:4000]

    def exec_service_restart(self, params: Dict[str, Any]) -> str:
        service = params["service"]
        allowed, reason = check_safety(f"systemctl restart {service}")
        if not allowed:
            raise PermissionError(reason)

        result = subprocess.run(
            ["sudo", "systemctl", "restart", service],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout + result.stderr

    def exec_web_search(self, params: Dict[str, Any]) -> str:
        query = params["query"]
        allowed, reason = check_privacy(query)
        if not allowed:
            raise PermissionError(reason)
        return f"[stub] Web search results for: {query}"
