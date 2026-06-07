"""
Jarvis v3 Task Decomposer
Breaks a user request into sub-tasks with dependency graphs.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .intent_classifier import Intent, IntentCategory


@dataclass
class SubTask:
    id: str
    description: str
    tool_name: Optional[str] = None
    params: dict = field(default_factory=dict)
    deps: Set[str] = field(default_factory=set)
    scope: str = "READ"


class TaskDecomposer:
    """
    Decomposes a high-level request into executable sub-tasks.
    Uses rule-based decomposition; can be enhanced with LLM calls.
    """

    def decompose(self, query: str, intent: Intent) -> List[SubTask]:
        ql = query.lower()
        tasks: List[SubTask] = []

        # Casual / greeting / chat — no tools needed
        if intent.category == IntentCategory.CASUAL:
            return tasks

        # System info requests → single task
        if intent.category == IntentCategory.SYSTEM:
            # Service control (restart/stop/start <svc>) → the real R3 tool, so it
            # routes through the trust/permission gate instead of becoming advice.
            svc = self._extract_service(ql)
            if svc:
                tasks.append(SubTask(
                    id="t1", description=f"Restart service {svc}",
                    tool_name="service_restart", params={"service": svc},
                    scope="LOCAL"))
                return tasks
            if any(k in ql for k in ["cpu", "processor"]):
                tasks.append(SubTask(id="t1", description="Get CPU info", tool_name="cpu_info"))
            if any(k in ql for k in ["ram", "memory"]):
                tasks.append(SubTask(id="t2", description="Get RAM usage", tool_name="ram_usage"))
            if any(k in ql for k in ["disk", "storage", "space"]):
                tasks.append(SubTask(id="t3", description="Get disk usage", tool_name="disk_space"))
            if any(k in ql for k in ["gpu", "nvidia", "vram"]):
                tasks.append(SubTask(id="t4", description="Get GPU status", tool_name="gpu_status"))
            if not tasks:
                tasks.append(SubTask(id="t1", description="Get system overview", tool_name="os_info"))

        # Security audit → multi-step pipeline
        elif intent.category == IntentCategory.SECURITY:
            tasks = [
                SubTask(id="t1", description="Scan listening ports", tool_name="port_listener_scan"),
                SubTask(id="t2", description="Audit sudoers", tool_name="sudoers_audit"),
                SubTask(id="t3", description="Check SSH config", tool_name="ssh_config_audit", deps={"t1", "t2"}),
                SubTask(id="t4", description="Scan for secrets", tool_name="secrets_scan"),
                SubTask(id="t5", description="Check file permissions", tool_name="file_permissions_audit"),
                SubTask(id="t6", description="Audit remote access", tool_name="remote_access_audit", deps={"t3", "t4", "t5"}),
            ]

        # Coding / project → git + build tasks
        elif intent.category in (IntentCategory.CODING, IntentCategory.PROJECT):
            if "git" in ql or "pull" in ql:
                tasks.append(SubTask(id="t1", description="Git pull", tool_name="git_pull", params={"path": "."}))
            if "build" in ql or "compile" in ql:
                tasks.append(SubTask(id="t2", description="Build project", tool_name="pnpm_build", deps={"t1"} if tasks else set()))
            if "test" in ql:
                tasks.append(SubTask(id="t3", description="Run tests", tool_name="pytest_run", deps={"t2"} if len(tasks) > 1 else set()))
            if not tasks:
                tasks.append(SubTask(id="t1", description="Check git status", tool_name="git_status", params={"path": "."}))

        # Default: single direct task
        if not tasks:
            tasks.append(SubTask(id="t1", description=query, tool_name=None))

        return tasks

    # Known service units Jarvis manages, matched by name anywhere in the query.
    _KNOWN_SERVICES = (
        "jarvis-telegram", "jarvis", "nginx", "ollama", "postgresql",
        "postgres", "docker", "ssh", "sshd", "pm2", "anydesk", "tailscaled",
    )
    _SERVICE_VERB = re.compile(r"\b(restart|reboot|stop|start|reload)\b")

    def _extract_service(self, ql: str) -> Optional[str]:
        """Parse a service name from a control request, else None.
        Requires both a control verb and a recognisable service target so plain
        info queries ('start menu', 'cpu load') don't trigger it."""
        if not self._SERVICE_VERB.search(ql):
            return None
        # 'reboot' alone (no service) is a host reboot — leave it to other paths.
        for svc in self._KNOWN_SERVICES:
            if re.search(rf"\b{re.escape(svc)}\b", ql):
                return svc
        # Fallback: "<verb> the <name> service" / "<verb> <name> service"
        m = re.search(r"\b(?:restart|stop|start|reload)\b\s+(?:the\s+)?"
                      r"([a-z0-9_.-]+)\s+service\b", ql)
        if m:
            return m.group(1)
        return None

    def assign_tools(self, tasks: List[SubTask], available_tools: List[str]) -> List[SubTask]:
        """Assign the best matching tool to each sub-task.
        Uses whole-word boundaries to prevent substring false-positives
        (e.g. 'hi' must not match 'tar_archive')."""
        for task in tasks:
            if task.tool_name:
                continue
            best = None
            best_score = 0
            desc = task.description.lower()
            for tool in available_tools:
                score = 0
                # Whole-word match: tool name appears as a complete word/token
                if re.search(r'\b' + re.escape(tool) + r'\b', desc):
                    score += 10
                # Each description word must match as a whole word in the tool name
                for part in desc.split():
                    if len(part) >= 2 and re.search(r'\b' + re.escape(part) + r'\b', tool):
                        score += 3
                if score > best_score:
                    best_score = score
                    best = tool
            task.tool_name = best
        return tasks
