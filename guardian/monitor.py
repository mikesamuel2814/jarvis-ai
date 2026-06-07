"""
Jarvis v3 Guardian — Monitor
8-dimension monitoring with configurable intervals and thresholds.
"""

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import psutil

from .classifier import SeverityClassifier, Severity
from .alerter import Alerter

JARVIS_HOME = Path("/home/kali/.jarvis")
log = logging.getLogger("jarvis.guardian.monitor")


class GuardianMonitor:
    """
    Monitors 8 dimensions of system health and security.
    """

    DIMENSIONS = [
        "system_resources",
        "service_health",
        "security_posture",
        "port_anomalies",
        "secret_health",
        "sudo_integrity",
        "project_health",
        "remote_access",
    ]

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or (JARVIS_HOME / "config" / "jarvis_v3.yaml")
        self.classifier = SeverityClassifier()
        self.alerter = Alerter()
        self._load_config()

    def _load_config(self):
        import yaml
        self.config = {}
        if self.config_path.exists():
            try:
                self.config = yaml.safe_load(self.config_path.read_text()) or {}
            except Exception as exc:
                log.warning("Could not load guardian config: %s", exc)
        self.thresholds = self.config.get("guardian", {}).get("thresholds", {
            "cpu_percent": 90,
            "ram_percent": 85,
            "disk_percent": 90,
            "gpu_vram_percent": 95,
            "nginx_error_rate": 5,
            "ssh_failures_per_hour": 10,
            "uncommitted_hours": 24,
        })

    def check_all(self) -> List[dict]:
        """Run all monitoring checks and return alerts."""
        alerts = []
        alerts.extend(self.check_system_resources())
        alerts.extend(self.check_service_health())
        alerts.extend(self.check_security_posture())
        alerts.extend(self.check_port_anomalies())
        alerts.extend(self.check_sudo_integrity())
        alerts.extend(self.check_project_health())
        alerts.extend(self.check_remote_access())
        return alerts

    def check_system_resources(self) -> List[dict]:
        alerts = []
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent

        for metric, value in [("cpu_percent", cpu), ("ram_percent", ram), ("disk_percent", disk)]:
            sev = self.classifier.classify(metric, value)
            if sev != Severity.P4_INFO:
                alerts.append({
                    "dimension": "system_resources",
                    "severity": sev.value,
                    "title": f"High {metric.replace('_', ' ').title()}",
                    "message": f"{metric}: {value:.1f}%",
                })
                self.alerter.send(sev, f"High {metric}", f"{metric}: {value:.1f}%")
        return alerts

    def check_service_health(self) -> List[dict]:
        alerts = []
        services = ["jarvis", "jarvis-telegram", "ollama", "nginx", "openclaw"]
        for svc in services:
            try:
                result = subprocess.run(["systemctl", "is-active", svc],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode != 0:
                    alerts.append({
                        "dimension": "service_health",
                        "severity": "P1",
                        "title": f"Service Down: {svc}",
                        "message": f"{svc} is not active",
                    })
                    self.alerter.send(Severity.P1_HIGH, f"Service Down: {svc}", f"{svc} is not active")
            except Exception as exc:
                log.debug("Service check failed for %s: %s", svc, exc)
        return alerts

    def check_security_posture(self) -> List[dict]:
        # Count failed SSH logins in the LAST HOUR (not all-time, which would
        # always trip the per-hour threshold on a long-lived auth.log).
        alerts = []
        try:
            result = subprocess.run(["grep", "Failed password", "/var/log/auth.log"],
                                    capture_output=True, text=True, timeout=5)
            count = self._count_recent_failures(result.stdout)
            if count > self.thresholds.get("ssh_failures_per_hour", 10):
                alerts.append({
                    "dimension": "security_posture",
                    "severity": "P5",
                    "title": "SSH Brute Force Detected",
                    "message": f"{count} failed SSH attempts",
                })
                self.alerter.send(Severity.P5_SECURITY, "SSH Brute Force", f"{count} failed attempts")
        except Exception:
            pass
        return alerts

    def _count_recent_failures(self, grep_output: str, window_min: int = 60) -> int:
        """Count auth.log 'Failed password' lines within the last window_min
        minutes. Handles ISO and traditional syslog timestamps; if a line's
        time can't be parsed it is conservatively counted."""
        now = datetime.now()
        cutoff = now.timestamp() - window_min * 60
        count = 0
        for line in grep_output.strip().splitlines():
            ts = self._parse_log_time(line, now)
            if ts is None or ts >= cutoff:
                count += 1
        return count

    @staticmethod
    def _parse_log_time(line: str, now: datetime) -> Optional[float]:
        import re
        # ISO: 2026-06-07T07:33:03 or 2026-06-07 07:33:03
        m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", line)
        if m:
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)}",
                                         "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                return None
        # Traditional syslog: "Jun  7 07:33:03" (no year → assume current).
        m = re.match(r"([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})", line)
        if m:
            try:
                dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)} {now.year}",
                                       "%b %d %H:%M:%S %Y")
                # Handle year wrap (Dec log read in Jan).
                if dt.timestamp() > now.timestamp() + 86400:
                    dt = dt.replace(year=now.year - 1)
                return dt.timestamp()
            except ValueError:
                return None
        return None

    def check_port_anomalies(self) -> List[dict]:
        # Delegate to PortMonitor from security module
        alerts = []
        try:
            from security.port_monitor import PortMonitor
            pm = PortMonitor()
            anomalies = pm.check()
            for a in anomalies:
                alerts.append({
                    "dimension": "port_anomalies",
                    "severity": "P5",
                    "title": "Port Anomaly",
                    "message": f"New listener: {a['protocol']}/{a['port']} on {a['bind_addr']}",
                })
                self.alerter.send(Severity.P5_SECURITY, "Port Anomaly",
                                  f"New listener: {a['protocol']}/{a['port']}")
        except Exception as exc:
            log.debug("Port anomaly check failed: %s", exc)
        return alerts

    def check_sudo_integrity(self) -> List[dict]:
        alerts = []
        try:
            from security.sudo_auditor import SudoAuditor
            auditor = SudoAuditor()
            vios = auditor.audit()
            for v in vios:
                if v["severity"] == "HIGH":
                    alerts.append({
                        "dimension": "sudo_integrity",
                        "severity": "P5",
                        "title": "sudo Policy Violation",
                        "message": v["raw"],
                    })
                    self.alerter.send(Severity.P5_SECURITY, "sudo Violation", v["raw"])
        except Exception as exc:
            log.debug("sudo check failed: %s", exc)
        return alerts

    def check_project_health(self) -> List[dict]:
        alerts = []
        repos = [
            Path("/home/kali/Projects/kalimike/Payment-Gateway"),
            Path("/home/kali/Projects/kalimike/Starline-Final-web"),
        ]
        for repo in repos:
            if not repo.exists():
                continue
            try:
                result = subprocess.run(["git", "-C", str(repo), "status", "--short"],
                                        capture_output=True, text=True, timeout=10)
                if result.stdout.strip():
                    alerts.append({
                        "dimension": "project_health",
                        "severity": "P3",
                        "title": f"Uncommitted Changes: {repo.name}",
                        "message": f"{len(result.stdout.strip().splitlines())} modified files",
                    })
            except Exception:
                pass
        return alerts

    def check_remote_access(self) -> List[dict]:
        alerts = []
        try:
            result = subprocess.run(["systemctl", "is-active", "anydesk"],
                                    capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                alerts.append({
                    "dimension": "remote_access",
                    "severity": "P4",
                    "title": "AnyDesk Active",
                    "message": "AnyDesk service is running. Review unattended access settings.",
                })
        except Exception:
            pass
        return alerts
