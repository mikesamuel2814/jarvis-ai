"""
Jarvis v3 Guardian — Auto-Remediation Engine

Maps guardian alerts to remediation actions and executes only the SAFE ones
automatically. Every remediation is filtered through the existing autonomy
safety model (`autonomy.should_auto_execute`) so destructive or `always_ask`
actions are never auto-run — they are surfaced as recommendations requiring
Telegram approval (spec Part 9: P0 → auto-restart, everything else gated).
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .classifier import Severity

log = logging.getLogger("jarvis.guardian.auto_remedy")


@dataclass
class Remediation:
    alert_title: str
    action: str                    # executor action / tool name
    arg: str = ""
    severity: str = "P2"
    rationale: str = ""
    auto: bool = False             # was it auto-executed?
    executed: bool = False
    success: Optional[bool] = None
    detail: str = ""


# Map a service name -> its safe restart action in the v2 executor.
SERVICE_REMEDY = {
    "jarvis": "restart_jarvis",
    "jarvis-telegram": "restart_jarvis_telegram",
    "nginx": "nginx_status",       # diagnostic only; restart needs approval
    "ollama": "restart_ollama",
    "openclaw": "restart_openclaw",
}


class AutoRemediator:
    """
    Decides and (optionally) applies remediations for guardian alerts.

    Safety: only alerts at P0/P1 with a known safe action are eligible for
    auto-execution, and even then the autonomy gate makes the final call.
    """

    def __init__(self, executor: Optional[Callable] = None,
                 autonomy_gate: Optional[Callable] = None,
                 alerter=None, enable_auto: bool = True):
        # executor: callable(action:str, arg:str) -> dict|ToolResult
        # autonomy_gate: callable(action:str) -> (bool, reason)
        self._executor = executor
        self._autonomy_gate = autonomy_gate
        self.alerter = alerter
        self.enable_auto = enable_auto
        self.history: List[Remediation] = []

    def _gate(self, action: str):
        if self._autonomy_gate is not None:
            try:
                return self._autonomy_gate(action)
            except Exception as exc:  # noqa: BLE001
                return False, f"gate error: {exc}"
        # Default conservative gate if autonomy unavailable.
        from autonomy import should_auto_execute
        return should_auto_execute(action)

    def _exec(self, action: str, arg: str) -> tuple:
        if self._executor is None:
            from executor import run_action
            self._executor = run_action
        res = self._executor(action, arg)
        if hasattr(res, "success"):
            return bool(res.success), getattr(res, "output", "") or getattr(res, "error", "")
        if isinstance(res, dict):
            return bool(res.get("success", True)), str(res.get("output", res))
        return True, str(res)

    def plan(self, alert: dict) -> Optional[Remediation]:
        """Produce a Remediation proposal for an alert, or None if not actionable."""
        dim = alert.get("dimension")
        sev = alert.get("severity", "P4")
        title = alert.get("title", "")

        if dim == "service_health":
            # Title format: "Service Down: <svc>"
            svc = title.split(":")[-1].strip()
            action = SERVICE_REMEDY.get(svc)
            if action:
                return Remediation(alert_title=title, action=action, severity=sev,
                                   rationale=f"{svc} inactive — restart")
            return Remediation(alert_title=title, action=f"systemctl restart {svc}",
                               severity=sev, rationale=f"{svc} inactive (manual)")

        if dim == "system_resources" and "disk" in title.lower():
            return Remediation(alert_title=title, action="clear_logs", severity=sev,
                               rationale="High disk — rotate/clear old logs")

        if dim in ("port_anomalies", "sudo_integrity", "security_posture"):
            # Security events are never auto-remediated — investigate only.
            return Remediation(alert_title=title, action="investigate", severity=sev,
                               rationale="Security event — requires human review")

        return None

    def handle(self, alert: dict) -> Optional[Remediation]:
        """Plan and, if safe, execute a remediation for one alert."""
        rem = self.plan(alert)
        if rem is None:
            return None

        eligible = (self.enable_auto and rem.severity in ("P0", "P1")
                    and rem.action not in ("investigate",))
        if eligible:
            auto, reason = self._gate(rem.action)
            if auto:
                ok, detail = self._exec(rem.action, rem.arg)
                rem.auto = True
                rem.executed = True
                rem.success = ok
                rem.detail = detail[:300]
                log.warning("Auto-remediated %s via %s -> %s",
                            rem.alert_title, rem.action, "OK" if ok else "FAIL")
            else:
                rem.detail = f"gated: {reason}"
        else:
            rem.detail = "recommendation only (needs approval)"

        self.history.append(rem)
        if self.alerter is not None and not rem.executed:
            self.alerter.send(
                Severity.P2_MEDIUM, f"Remediation suggested: {rem.alert_title}",
                f"{rem.rationale}\nProposed: `{rem.action} {rem.arg}`".strip(),
            )
        return rem

    def handle_all(self, alerts: List[dict]) -> List[Remediation]:
        return [r for r in (self.handle(a) for a in alerts) if r is not None]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ar = AutoRemediator(
        executor=lambda a, arg: {"success": True, "output": f"ran {a}"},
        autonomy_gate=lambda a: (a in ("restart_jarvis", "restart_ollama"), "test gate"),
        enable_auto=True,
    )
    tests = [
        {"dimension": "service_health", "severity": "P1", "title": "Service Down: ollama"},
        {"dimension": "service_health", "severity": "P1", "title": "Service Down: nginx"},
        {"dimension": "port_anomalies", "severity": "P5", "title": "Port Anomaly"},
        {"dimension": "system_resources", "severity": "P1", "title": "High Disk Percent"},
    ]
    for r in ar.handle_all(tests):
        print(f"{r.alert_title:30} action={r.action:20} auto={r.auto} "
              f"exec={r.executed} ok={r.success} :: {r.detail}")
