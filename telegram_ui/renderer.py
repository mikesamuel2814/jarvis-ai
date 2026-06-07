"""
Jarvis v3 Telegram Dynamic UI Renderer
Classifies message type and renders appropriate template.
"""

from typing import Dict, List, Optional

from . import templates


class MessageType:
    STATUS = "status"
    PROGRESS = "progress"
    CODE = "code"
    TABLE = "table"
    ALERT = "alert"
    PERMISSION = "permission"
    EXPANDABLE = "expandable"
    PLAIN = "plain"


class TelegramRenderer:
    """Renders Jarvis responses into Telegram-friendly formatted messages."""

    def render(self, data: dict) -> dict:
        """
        Input: dict with keys like type, content, metadata
        Output: dict with 'text' and optional 'reply_markup'
        """
        msg_type = data.get("type", MessageType.PLAIN)

        if msg_type == MessageType.STATUS:
            return {"text": templates.status_card(**data.get("metrics", {}))}

        if msg_type == MessageType.PROGRESS:
            return {"text": templates.progress_bar(data.get("percent", 0))}

        if msg_type == MessageType.CODE:
            return {"text": templates.code_block(data.get("code", ""), data.get("language", "bash"))}

        if msg_type == MessageType.TABLE:
            return {"text": templates.data_table(data.get("headers", []), data.get("rows", []))}

        if msg_type == MessageType.ALERT:
            return {"text": templates.alert_banner(
                data.get("severity", "P3"),
                data.get("title", "Alert"),
                data.get("message", ""),
                data.get("actions"),
            )}

        if msg_type == MessageType.PERMISSION:
            out = {"text": templates.permission_request(
                data.get("command", ""),
                data.get("rank", "R2"),
                data.get("downtime_sec"),
                data.get("rollback"),
                data.get("system_context"),
                data.get("project_impact"),
            )}
            # Inline [Allow] [Allow & Save] [Deny] keyboard if provided by the
            # PermissionCallbackHandler (rows of {text, callback_data}).
            if data.get("keyboard"):
                out["reply_markup"] = data["keyboard"]
            return out

        if msg_type == MessageType.EXPANDABLE:
            return {"text": templates.expandable_section(data.get("title", ""), data.get("content", ""))}

        # Plain text fallback
        return {"text": str(data.get("content", ""))[:4000]}

    def classify(self, text: str) -> str:
        """Heuristic classifier for plain text → message type."""
        t = text.lower()
        if "permission" in t or "needs your approval" in t:
            return MessageType.PERMISSION
        if any(x in t for x in ["cpu:", "ram:", "disk:", "gpu:", "system status"]):
            return MessageType.STATUS
        if "alert" in t or "critical" in t or "warning" in t:
            return MessageType.ALERT
        if "```" in text:
            return MessageType.CODE
        return MessageType.PLAIN
