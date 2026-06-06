"""
Jarvis v3 Permission Engine
Rich Telegram permission requests with context (system stats, rollback, downtime).
"""

from typing import Optional, Dict, Any
from datetime import datetime, timezone


class PermissionEngine:
    """
    Formats permission requests for Telegram with rich context.
    Supports R0-R6 ranks with appropriate UI patterns.
    """

    RANK_EMOJI = {
        "R0": "🔵", "R1": "🟢", "R2": "🟡", "R3": "🟠",
        "R4": "🔴", "R5": "🟣", "R6": "⚫",
    }

    def __init__(self):
        pass

    def build_request(
        self,
        tool_name: str,
        command_display: str,
        rank: str,
        downtime_sec: Optional[int] = None,
        rollback_command: Optional[str] = None,
        system_context: Optional[dict] = None,
        project_impact: Optional[dict] = None,
    ) -> dict:
        """Build a structured permission request for Telegram renderer."""
        emoji = self.RANK_EMOJI.get(rank, "⚪")
        lines = [
            f"Jarvis needs permission:",
            "",
            f"`{command_display}`",
            "",
            f"Rank: {rank} [{self._rank_name(rank)}]",
        ]
        if downtime_sec is not None:
            lines.append(f"Downtime: ~{downtime_sec}s")
        if rollback_command:
            lines.append(f"Rollback: `{rollback_command}`")
        lines.append("")

        if system_context:
            lines.append(
                f"System: CPU {system_context.get('cpu', '?')}% | "
                f"RAM {system_context.get('ram', '?')}% | "
                f"Disk {system_context.get('disk', '?')}%"
            )

        if project_impact:
            for proj, status in project_impact.items():
                lines.append(f"{proj}: {status}")

        text = "\n".join(lines)

        return {
            "text": text,
            "rank": rank,
            "buttons": self._buttons_for_rank(rank),
            "destructive": rank == "R6",
        }

    def _rank_name(self, rank: str) -> str:
        names = {
            "R0": "Read-Only", "R1": "User-Space", "R2": "File Modifier",
            "R3": "Service Controller", "R4": "System Modifier",
            "R5": "Security-Critical", "R6": "Destructive",
        }
        return names.get(rank, "Unknown")

    def _buttons_for_rank(self, rank: str) -> list:
        if rank == "R6":
            return []  # R6 requires typed confirmation, no buttons
        return [
            {"text": "Allow", "callback_data": f"perm:allow:{rank}"},
            {"text": "Allow & Save", "callback_data": f"perm:allow_save:{rank}"},
            {"text": "Deny", "callback_data": f"perm:deny:{rank}"},
        ]

    def validate_r6_confirmation(self, user_input: str, expected: str) -> bool:
        """R6 destructive actions require exact typed confirmation."""
        return user_input.strip().upper() == expected.upper()
