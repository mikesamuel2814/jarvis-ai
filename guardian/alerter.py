"""
Jarvis v3 Guardian — Alerter
Sends Telegram alerts with rate limiting and deduplication.
"""

import hashlib
import logging
import time
from typing import Dict, Optional

from .classifier import Severity, SeverityClassifier

log = logging.getLogger("jarvis.guardian.alerter")


class Alerter:
    """
    Sends alerts via Telegram with deduplication and escalation.
    """

    def __init__(self, cooldown_sec: float = 300.0):
        self.cooldown_sec = cooldown_sec
        self._last_alert: Dict[str, float] = {}  # key -> timestamp
        self._classifier = SeverityClassifier()

    def _make_key(self, severity: Severity, title: str) -> str:
        return hashlib.sha256(f"{severity.value}:{title}".encode()).hexdigest()[:16]

    def should_alert(self, severity: Severity, title: str) -> bool:
        """Check if enough time has passed since the last alert of this type."""
        key = self._make_key(severity, title)
        last = self._last_alert.get(key, 0)
        # Critical alerts bypass cooldown
        if severity in (Severity.P0_CRITICAL, Severity.P5_SECURITY):
            return True
        return (time.time() - last) > self.cooldown_sec

    def send(self, severity: Severity, title: str, message: str, actions: Optional[list] = None):
        """Send an alert. Returns True if sent, False if deduplicated."""
        if not self.should_alert(severity, title):
            log.debug("Alert deduplicated: %s", title)
            return False

        key = self._make_key(severity, title)
        self._last_alert[key] = time.time()

        emoji = {
            Severity.P0_CRITICAL: "🚨",
            Severity.P1_HIGH: "⚠️",
            Severity.P2_MEDIUM: "🔶",
            Severity.P3_LOW: "🔹",
            Severity.P4_INFO: "ℹ️",
            Severity.P5_SECURITY: "🛡️",
            Severity.P6_PERFORMANCE: "📈",
        }.get(severity, "⚪")

        text = f"{emoji} *{title}*\nSeverity: `{severity.value}`\n\n{message}"
        if actions:
            text += f"\n\nActions: {', '.join(actions)}"

        # TODO: Integrate with Telegram bot sender
        log.warning("ALERT [%s] %s: %s", severity.value, title, message[:200])
        return True

    def clear_cache(self):
        self._last_alert.clear()
