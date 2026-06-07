"""
Jarvis v3 — Unified Notification Service

Single, clean path for Jarvis to push instant Telegram notifications about its
own decisions and activities. Replaces the scattered per-module senders.

Policy (per Sir): notify on MEDIUM level and above — operational events that
matter (success, failure, new bot request, autonomous decisions, blocked
actions, remediation). GENERAL / NORMAL chatter is suppressed.

  Level      Send?  Examples
  ─────────  ─────  ───────────────────────────────────────────────
  GENERAL    no     routine reads, heartbeat, debug
  NORMAL     no     ordinary command completed, status checks
  MEDIUM     yes    tool/build success, new bot request, auto-action
  HIGH       yes    failure, blocked privileged action, remediation
  CRITICAL   yes    production down, security event, destructive halt

Config source: config/secrets.env → TELEGRAM_BOT_TOKEN + TELEGRAM_USER_ID.
Sends are non-blocking (daemon thread) and never raise into the caller.
"""

import logging
import os
import threading
import time
from enum import IntEnum
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.notifier")

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", "/home/kali/.jarvis"))
SECRETS_PATH = JARVIS_HOME / "config" / "secrets.env"


class Level(IntEnum):
    GENERAL = 0
    NORMAL = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


# Only MEDIUM and above are delivered.
SEND_THRESHOLD = Level.MEDIUM

_EMOJI = {
    Level.MEDIUM: "🔔",
    Level.HIGH: "⚠️",
    Level.CRITICAL: "🚨",
}
# Event-kind glyphs layered on top of level.
_KIND_EMOJI = {
    "success": "✅", "failure": "❌", "bot_request": "🤖", "build": "🛠️",
    "decision": "🧠", "blocked": "🛡️", "remediation": "🔧", "deploy": "🚀",
    "security": "🔒",
}


def _get_secret(key: str) -> Optional[str]:
    if SECRETS_PATH.exists():
        try:
            for line in SECRETS_PATH.read_text().splitlines():
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:  # noqa: BLE001
            pass
    return os.environ.get(key)


class Notifier:
    """Severity-filtered Telegram notifier for Jarvis's own activity."""

    def __init__(self, threshold: Level = SEND_THRESHOLD,
                 dedup_window_sec: float = 30.0):
        self.threshold = threshold
        self._dedup_window = dedup_window_sec
        self._recent: dict = {}        # message-hash -> last sent ts
        self._lock = threading.Lock()
        self._sent_count = 0
        self._suppressed_count = 0

    # ── Public API ───────────────────────────────────────────────────────────
    def notify(self, message: str, level: Level = Level.MEDIUM,
               kind: str = "", source: str = "jarvis",
               dry_run: bool = False) -> bool:
        """Queue a notification. Returns True if it will be sent (meets the
        threshold and isn't a dedup), False if suppressed."""
        # Global kill-switch (tests, maintenance): JARVIS_NOTIFY_DISABLED=1.
        if os.environ.get("JARVIS_NOTIFY_DISABLED") == "1":
            self._suppressed_count += 1
            return False

        if level < self.threshold:
            self._suppressed_count += 1
            log.debug("Suppressed (%s < %s): %s", level.name, self.threshold.name, message[:60])
            return False

        text = self._format(message, level, kind, source)
        if self._is_dup(text):
            self._suppressed_count += 1
            return False

        if dry_run:
            return True

        threading.Thread(target=self._send, args=(text,), daemon=True).start()
        self._sent_count += 1
        return True

    # ── Convenience helpers (event kinds) ────────────────────────────────────
    def success(self, what: str, detail: str = "", source: str = "jarvis"):
        return self.notify(f"*{what}* succeeded" + (f"\n{detail}" if detail else ""),
                           Level.MEDIUM, "success", source)

    def failure(self, what: str, detail: str = "", source: str = "jarvis"):
        return self.notify(f"*{what}* failed" + (f"\n{detail}" if detail else ""),
                           Level.HIGH, "failure", source)

    def bot_request(self, name: str, detail: str = "", source: str = "tool_builder"):
        return self.notify(f"New bot/tool request: *{name}*" + (f"\n{detail}" if detail else ""),
                           Level.MEDIUM, "bot_request", source)

    def decision(self, what: str, detail: str = "", level: Level = Level.MEDIUM,
                 source: str = "jarvis"):
        return self.notify(f"Decision: *{what}*" + (f"\n{detail}" if detail else ""),
                           level, "decision", source)

    def blocked(self, what: str, reason: str = "", source: str = "guard"):
        return self.notify(f"Blocked *{what}*" + (f"\n{reason}" if reason else ""),
                           Level.HIGH, "blocked", source)

    def remediation(self, what: str, ok: bool, detail: str = "", source: str = "guardian"):
        lvl = Level.HIGH if not ok else Level.MEDIUM
        verb = "applied" if ok else "attempted"
        return self.notify(f"Auto-remediation {verb}: *{what}*" + (f"\n{detail}" if detail else ""),
                           lvl, "remediation", source)

    def critical(self, message: str, kind: str = "security", source: str = "jarvis"):
        return self.notify(message, Level.CRITICAL, kind, source)

    # ── Internals ────────────────────────────────────────────────────────────
    def _format(self, message: str, level: Level, kind: str, source: str) -> str:
        glyph = _KIND_EMOJI.get(kind, "") or _EMOJI.get(level, "🔔")
        tag = _EMOJI.get(level, "")
        head = f"{glyph} {tag}".strip()
        return f"{head} *Jarvis* · _{source}_\n{message}"

    def _is_dup(self, text: str) -> bool:
        now = time.time()
        h = hash(text)
        with self._lock:
            last = self._recent.get(h, 0)
            self._recent = {k: v for k, v in self._recent.items()
                            if now - v < self._dedup_window}
            if now - last < self._dedup_window:
                return True
            self._recent[h] = now
        return False

    def _send(self, text: str):
        token = _get_secret("TELEGRAM_BOT_TOKEN")
        chat_id = _get_secret("TELEGRAM_USER_ID")
        if not token or not chat_id:
            log.warning("Notifier: missing TELEGRAM_BOT_TOKEN/TELEGRAM_USER_ID")
            return
        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Notifier send failed: %s", exc)

    @property
    def stats(self) -> dict:
        return {"sent": self._sent_count, "suppressed": self._suppressed_count,
                "threshold": self.threshold.name}


# Module-level singleton + thin wrappers for easy import.
_notifier: Optional[Notifier] = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier


def notify(message: str, level: Level = Level.MEDIUM, kind: str = "",
           source: str = "jarvis") -> bool:
    return get_notifier().notify(message, level, kind, source)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    n = Notifier()
    print("general suppressed:", n.notify("routine read", Level.GENERAL, dry_run=True))
    print("normal  suppressed:", n.notify("status checked", Level.NORMAL, dry_run=True))
    print("medium  sent      :", n.notify("build ok", Level.MEDIUM, "success", dry_run=True))
    print("high    sent      :", n.notify("build failed", Level.HIGH, "failure", dry_run=True))
    print("dup     suppressed:", n.notify("build ok", Level.MEDIUM, "success", dry_run=True))
    print("preview:\n" + n._format("disk_free_gb built & deployed", Level.MEDIUM, "bot_request", "tool_builder"))
