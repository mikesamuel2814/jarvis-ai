"""
Jarvis v3 Intent Classifier
Categorizes user requests into: category, urgency, scope, complexity.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class IntentCategory(Enum):
    SYSTEM = "system"
    SECURITY = "security"
    CODING = "coding"
    PROJECT = "project"
    INFO = "info"
    COMMUNICATION = "communication"


class Urgency(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Intent:
    category: IntentCategory
    urgency: Urgency
    scope: str  # READ, LOCAL, NETWORK, PRIVILEGED
    complexity: int  # 1-10
    keywords: list


class IntentClassifier:
    """
    Rule-based intent classifier with regex patterns.
    Fast (no LLM call needed) — runs in <1ms.
    """

    CATEGORY_PATTERNS = {
        IntentCategory.SYSTEM: [
            r"\b(cpu|ram|memory|disk|gpu|uptime|process|service|system|kernel|boot|shutdown)\b",
            r"\b(restart|stop|start|status|logs)\b.*\b(jarvis|nginx|ollama|service)\b",
        ],
        IntentCategory.SECURITY: [
            r"\b(security|audit|scan|vulnerability|pentest|nmap|nikto|wpscan|sqlmap|hashcat)\b",
            r"\b(rootkit|backdoor|intrusion|firewall|iptables|sudoers|permission)\b",
            r"\b(secret|credential|token|key|password)\b.*\b(scan|audit|check)\b",
        ],
        IntentCategory.CODING: [
            r"\b(code|bug|fix|refactor|debug|review|lint|test|build|deploy|git|docker)\b",
            r"\b(function|class|module|component|api|endpoint|route|controller)\b",
            r"\b(javascript|typescript|python|node|react|fastapi|express|sql)\b",
        ],
        IntentCategory.PROJECT: [
            r"\b(AsthaCash|Starline|conztru|Payment-Gateway|gateway|real estate)\b",
            r"\b(project|repo|repository|pipeline|ci/cd|jenkins|github|gitlab)\b",
        ],
        IntentCategory.COMMUNICATION: [
            r"\b(send|notify|alert|message|email|telegram|tts|speak|notify)\b",
        ],
    }

    URGENCY_PATTERNS = {
        Urgency.CRITICAL: [
            r"\b(down|broken|failed|crash|urgent|immediately|asap|emergency)\b",
            r"\b(production|money-critical|revenue)\b.*\b(down|stopped)\b",
        ],
        Urgency.HIGH: [
            r"\b(important|priority|soon|quickly|hurry)\b",
            r"\b(security|breach|attack|unauthorized)\b",
        ],
    }

    SCOPE_PATTERNS = {
        "PRIVILEGED": [
            r"\b(reboot|shutdown|format|partition|root|sudo)\b",
            r"\b(iptables|ufw|firewall|ssl cert)\b.*\b(replace|flush|update)\b",
        ],
        "NETWORK": [
            r"\b(ssh|scp|rsync|nmap|ping|curl|wget|dns|domain|ip)\b",
            r"\b(remote|vps|server|cloud|deploy)\b",
        ],
    }

    def classify(self, query: str) -> Intent:
        ql = query.lower()

        # Category
        category = IntentCategory.INFO
        best_score = 0
        for cat, patterns in self.CATEGORY_PATTERNS.items():
            score = sum(1 for p in patterns if re.search(p, ql))
            if score > best_score:
                best_score = score
                category = cat

        # Urgency
        urgency = Urgency.LOW
        if any(re.search(p, ql) for p in self.URGENCY_PATTERNS.get(Urgency.CRITICAL, [])):
            urgency = Urgency.CRITICAL
        elif any(re.search(p, ql) for p in self.URGENCY_PATTERNS.get(Urgency.HIGH, [])):
            urgency = Urgency.HIGH
        elif best_score > 0:
            urgency = Urgency.NORMAL

        # Scope
        scope = "READ"
        if any(re.search(p, ql) for p in self.SCOPE_PATTERNS.get("PRIVILEGED", [])):
            scope = "PRIVILEGED"
        elif any(re.search(p, ql) for p in self.SCOPE_PATTERNS.get("NETWORK", [])):
            scope = "NETWORK"
        elif best_score > 0 and category != IntentCategory.INFO:
            scope = "LOCAL"

        # Complexity (heuristic: word count + technical terms)
        complexity = min(10, max(1, len(query.split()) // 5 + best_score))

        # Keywords
        keywords = list(set(re.findall(r"[a-z][a-z0-9_]*", ql)))

        return Intent(category, urgency, scope, complexity, keywords)
