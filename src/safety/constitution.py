"""
JARVIS Safety Constitution
Runtime safety enforcement.
Integrates with config/safety_rules.json and v3 security/ modules.
"""
import json
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass

JARVIS_DIR = Path("/home/kali/.jarvis")
SAFETY_RULES_PATH = JARVIS_DIR / "config" / "safety_rules.json"


@dataclass
class SafetyVerdict:
    allowed: bool
    action: str  # block | confirm | allow
    severity: str  # critical | high | medium | low
    reason: str


class Constitution:
    """JARVIS safety constitution — runtime rule engine."""

    def __init__(self, rules_path: Path = SAFETY_RULES_PATH):
        self.rules_path = rules_path
        self.rules = self._load_rules()

    def _load_rules(self) -> Dict[str, Any]:
        if self.rules_path.exists():
            with open(self.rules_path) as f:
                return json.load(f)
        return {"absolute_blocks": [], "resource_limits": {}, "privacy_rules": {}}

    def reload(self):
        self.rules = self._load_rules()

    def check_command(self, command: str) -> SafetyVerdict:
        """Check a shell command against absolute block patterns."""
        for block in self.rules.get("absolute_blocks", []):
            pattern = block.get("pattern", "")
            if re.search(pattern, command, re.IGNORECASE):
                return SafetyVerdict(
                    allowed=block.get("action") != "block",
                    action=block.get("action", "block"),
                    severity=block.get("severity", "high"),
                    reason=block.get("reason", f"Matched pattern: {pattern}")
                )
        return SafetyVerdict(allowed=True, action="allow", severity="low", reason="No rules matched")

    def check_path(self, path: str) -> SafetyVerdict:
        """Check if a file path is blocked."""
        limits = self.rules.get("resource_limits", {})
        blocked = limits.get("blocked_paths", [])
        for bp in blocked:
            if path.startswith(bp) or bp in path:
                return SafetyVerdict(
                    allowed=False,
                    action="block",
                    severity="critical",
                    reason=f"Access to blocked path: {bp}"
                )
        return SafetyVerdict(allowed=True, action="allow", severity="low", reason="Path allowed")

    def check_privacy(self, data: str) -> SafetyVerdict:
        """Check if data contains forbidden terms that must not leave the system."""
        privacy = self.rules.get("privacy_rules", {})
        forbidden = privacy.get("never_externally_send", [])
        for term in forbidden:
            if term.lower() in data.lower():
                return SafetyVerdict(
                    allowed=False,
                    action="block",
                    severity="critical",
                    reason=f"Privacy violation: forbidden term '{term}' detected"
                )
        # Check redact patterns
        for pattern in privacy.get("redact_patterns", []):
            if re.search(pattern, data):
                return SafetyVerdict(
                    allowed=True,
                    action="confirm",
                    severity="high",
                    reason="Potential secret detected — review before sending externally"
                )
        return SafetyVerdict(allowed=True, action="allow", severity="low", reason="No privacy issues")

    def check_resource_limits(self, file_size_mb: float = 0, command_timeout: int = 0) -> SafetyVerdict:
        """Check if operation exceeds resource limits."""
        limits = self.rules.get("resource_limits", {})
        max_file = limits.get("max_file_size_mb", 100)
        max_timeout = limits.get("max_command_timeout", 300)

        if file_size_mb > max_file:
            return SafetyVerdict(
                allowed=False,
                action="block",
                severity="high",
                reason=f"File size {file_size_mb}MB exceeds limit {max_file}MB"
            )
        if command_timeout > max_timeout:
            return SafetyVerdict(
                allowed=False,
                action="block",
                severity="high",
                reason=f"Command timeout {command_timeout}s exceeds limit {max_timeout}s"
            )
        return SafetyVerdict(allowed=True, action="allow", severity="low", reason="Within resource limits")

    def full_audit(self, command: str = "", path: str = "", data: str = "") -> List[SafetyVerdict]:
        """Run all safety checks and return verdicts."""
        verdicts = []
        if command:
            verdicts.append(self.check_command(command))
        if path:
            verdicts.append(self.check_path(path))
        if data:
            verdicts.append(self.check_privacy(data))
        return verdicts

    def is_safe(self, command: str = "", path: str = "", data: str = "") -> Tuple[bool, str]:
        """Quick boolean check with first blocking reason."""
        verdicts = self.full_audit(command, path, data)
        for v in verdicts:
            if not v.allowed:
                return False, v.reason
        return True, "All checks passed"
