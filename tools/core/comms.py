"""
Jarvis v3 Communication Tools (5 tools)
Category: comms | Rank: R0-R1 | Scope: READ, LOCAL
"""

import os
import smtplib
import subprocess
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from ..decorator import jarvis_tool
from ..result import ToolResult

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
SECRETS_PATH = JARVIS_HOME / "config" / "secrets.env"


def _get_secret(key: str) -> Optional[str]:
    if SECRETS_PATH.exists():
        with open(SECRETS_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(key)


@jarvis_tool(
    name="telegram_msg",
    description="Send Telegram message (uses bot token from vault)",
    params={
        "chat_id": {"type": "string", "required": True},
        "message": {"type": "string", "required": True},
    },
    rank="R0", scope="LOCAL", category="comms", tags=["comms", "telegram", "message"]
)
def telegram_msg(chat_id: str, message: str) -> ToolResult:
    try:
        import requests
        token = _get_secret("TELEGRAM_BOT_TOKEN")
        if not token:
            return ToolResult.fail(error="TELEGRAM_BOT_TOKEN not found in secrets.env or environment", tool_name="telegram_msg")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        output = f"Message sent. Message ID: {data.get('result', {}).get('message_id', 'N/A')}"
        return ToolResult.ok(output=output, data=data, tool_name="telegram_msg")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="telegram_msg")


@jarvis_tool(
    name="telegram_alert",
    description="Send Telegram alert with emoji prefix",
    params={
        "chat_id": {"type": "string", "required": True},
        "message": {"type": "string", "required": True},
        "level": {"type": "string", "required": False, "default": "info"},
    },
    rank="R0", scope="LOCAL", category="comms", tags=["comms", "telegram", "alert"]
)
def telegram_alert(chat_id: str, message: str, level: str = "info") -> ToolResult:
    try:
        import requests
        token = _get_secret("TELEGRAM_BOT_TOKEN")
        if not token:
            return ToolResult.fail(error="TELEGRAM_BOT_TOKEN not found in secrets.env or environment", tool_name="telegram_alert")
        emojis = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨",
            "success": "✅",
        }
        emoji = emojis.get(level.lower(), "ℹ️")
        text = f"{emoji} <b>[{level.upper()}]</b>\n{message}"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        output = f"Alert sent. Message ID: {data.get('result', {}).get('message_id', 'N/A')}"
        return ToolResult.ok(output=output, data=data, tool_name="telegram_alert")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="telegram_alert")


@jarvis_tool(
    name="email_send",
    description="Send email",
    params={
        "to": {"type": "string", "required": True},
        "subject": {"type": "string", "required": True},
        "body": {"type": "string", "required": True},
        "smtp_host": {"type": "string", "required": False, "default": "localhost"},
        "smtp_port": {"type": "integer", "required": False, "default": 25},
    },
    rank="R1", scope="LOCAL", category="comms", tags=["comms", "email", "smtp"]
)
def email_send(to: str, subject: str, body: str, smtp_host: str = "localhost", smtp_port: int = 25) -> ToolResult:
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = "jarvis@localhost"
        msg["To"] = to
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.send_message(msg)
        output = f"Email sent to {to} via {smtp_host}:{smtp_port}"
        return ToolResult.ok(output=output, tool_name="email_send")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="email_send")


@jarvis_tool(
    name="tts_speak",
    description="Text-to-speech via edge-tts or espeak fallback",
    params={
        "text": {"type": "string", "required": True},
        "output_file": {"type": "string", "required": False, "default": "/tmp/jarvis_tts.mp3"},
    },
    rank="R0", scope="LOCAL", category="comms", tags=["comms", "tts", "voice", "audio"]
)
def tts_speak(text: str, output_file: str = "/tmp/jarvis_tts.mp3") -> ToolResult:
    try:
        try:
            import edge_tts
            import asyncio

            async def _speak():
                communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
                await communicate.save(output_file)

            asyncio.run(_speak())
            output = f"TTS audio saved to {output_file} (edge-tts)"
            return ToolResult.ok(output=output, data={"path": output_file, "engine": "edge-tts"}, tool_name="tts_speak")
        except ImportError:
            result = subprocess.run(["espeak", text, "-w", output_file.replace(".mp3", ".wav")], capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or "espeak failed")
            output = f"TTS audio saved to {output_file.replace('.mp3', '.wav')} (espeak fallback)"
            return ToolResult.ok(output=output, data={"path": output_file.replace(".mp3", ".wav"), "engine": "espeak"}, tool_name="tts_speak")
    except FileNotFoundError:
        return ToolResult.fail(error="No TTS engine available (edge-tts or espeak)", tool_name="tts_speak")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="tts_speak")


@jarvis_tool(
    name="notify_desktop",
    description="Send desktop notification",
    params={
        "title": {"type": "string", "required": False, "default": "Jarvis"},
        "message": {"type": "string", "required": True},
        "urgency": {"type": "string", "required": False, "default": "normal"},
    },
    rank="R0", scope="LOCAL", category="comms", tags=["comms", "desktop", "notification"]
)
def notify_desktop(title: str = "Jarvis", message: str = "", urgency: str = "normal") -> ToolResult:
    try:
        result = subprocess.run(
            ["notify-send", "-u", urgency, title, message],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or "notify-send failed")
        output = f"Desktop notification sent: {title}"
        return ToolResult.ok(output=output, tool_name="notify_desktop")
    except FileNotFoundError:
        return ToolResult.fail(error="notify-send not found", tool_name="notify_desktop")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="notify_desktop")
