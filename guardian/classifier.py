"""
Jarvis v3 Guardian — Severity Classifier
Maps events to 7 severity levels.
"""

from enum import Enum
from typing import Dict


class Severity(Enum):
    P0_CRITICAL = "P0"      # Red — Immediate + auto-restart
    P1_HIGH = "P1"          # Orange — 5 min + inline buttons
    P2_MEDIUM = "P2"        # Yellow — 15 min notification
    P3_LOW = "P3"           # Blue — 1 hour
    P4_INFO = "P4"          # White — Daily
    P5_SECURITY = "P5"      # Purple — Immediate + block IP
    P6_PERFORMANCE = "P6"   # Green — 30 min


class SeverityClassifier:
    """
    Classifies monitoring events into severity levels.
    """

    THRESHOLDS = {
        "cpu_percent": [(95, Severity.P0_CRITICAL), (90, Severity.P1_HIGH), (80, Severity.P2_MEDIUM)],
        "ram_percent": [(95, Severity.P0_CRITICAL), (85, Severity.P1_HIGH), (75, Severity.P2_MEDIUM)],
        "disk_percent": [(95, Severity.P0_CRITICAL), (90, Severity.P1_HIGH), (80, Severity.P2_MEDIUM)],
        "gpu_vram_percent": [(98, Severity.P0_CRITICAL), (95, Severity.P1_HIGH)],
        "nginx_error_rate": [(10, Severity.P1_HIGH), (5, Severity.P2_MEDIUM)],
        "ssh_failures_per_hour": [(50, Severity.P5_SECURITY), (20, Severity.P1_HIGH), (10, Severity.P2_MEDIUM)],
        "uncommitted_hours": [(72, Severity.P2_MEDIUM), (24, Severity.P3_LOW)],
    }

    def classify(self, dimension: str, value: float, context: Dict = None) -> Severity:
        """Classify a single metric value."""
        thresholds = self.THRESHOLDS.get(dimension, [])
        for threshold, severity in thresholds:
            if value >= threshold:
                return severity
        return Severity.P4_INFO

    def classify_event(self, event_type: str, details: str) -> Severity:
        """Classify an event by type and description."""
        d = details.lower()
        if event_type in ("service_down", "production_down"):
            return Severity.P0_CRITICAL
        if event_type == "port_anomaly":
            return Severity.P5_SECURITY
        if event_type == "sudoers_changed":
            return Severity.P5_SECURITY
        if event_type == "ssh_key_modified":
            return Severity.P5_SECURITY
        if event_type == "secret_expired":
            return Severity.P1_HIGH
        if event_type == "build_failure":
            return Severity.P2_MEDIUM
        if event_type == "uncommitted":
            return Severity.P3_LOW
        return Severity.P4_INFO
