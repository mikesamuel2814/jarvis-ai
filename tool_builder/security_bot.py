"""
Tool Builder — Security Review Agent

Static analysis of generated tool code. Blocks deployment on hardcoded secrets,
command/code injection, and path traversal — the recurring classes from the
Kimi CLI live-system audit (SEC-01..SEC-07).
"""

import ast
import re
from dataclasses import dataclass, field
from typing import List

# Hardcoded secret heuristics.
SECRET_PATTERNS = [
    (re.compile(r'(?i)(api[_-]?key|secret|token|password|passwd)\s*=\s*["\'][^"\']{8,}["\']'),
     "hardcoded credential"),
    (re.compile(r'(?i)(AKIA[0-9A-Z]{16})'), "AWS access key"),
    (re.compile(r'(?i)bearer\s+[a-z0-9._\-]{20,}'), "hardcoded bearer token"),
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'), "embedded private key"),
]

# Dangerous call patterns (in addition to AST checks).
DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}
DANGEROUS_OS = {"system", "popen"}


@dataclass
class SecurityFinding:
    severity: str          # CRITICAL | HIGH | MEDIUM
    category: str
    detail: str
    line: int = 0


@dataclass
class SecurityReport:
    passed: bool
    findings: List[SecurityFinding] = field(default_factory=list)

    def to_dict(self):
        return {
            "passed": self.passed,
            "findings": [vars(f) for f in self.findings],
        }


class SecurityBot:
    """Audits generated code; deployment is blocked on any CRITICAL/HIGH finding."""

    def review(self, code: str) -> SecurityReport:
        findings: List[SecurityFinding] = []

        # 1. Secret scan (regex, line-attributed).
        for i, line in enumerate(code.splitlines(), 1):
            for pat, label in SECRET_PATTERNS:
                if pat.search(line):
                    findings.append(SecurityFinding(
                        "CRITICAL", "hardcoded_secret", label, i))

        # 2. AST-based dangerous construct + injection detection.
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            findings.append(SecurityFinding(
                "HIGH", "syntax", f"unparseable: {exc.msg}", exc.lineno or 0))
            return SecurityReport(passed=False, findings=findings)

        findings.extend(self._walk(tree))

        worst = {f.severity for f in findings}
        passed = not ("CRITICAL" in worst or "HIGH" in worst)
        return SecurityReport(passed=passed, findings=findings)

    def _walk(self, tree) -> List[SecurityFinding]:
        out: List[SecurityFinding] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = self._call_name(node.func)
                # eval/exec/compile/__import__
                if name in DANGEROUS_CALLS:
                    out.append(SecurityFinding(
                        "HIGH", "code_injection", f"use of {name}()", node.lineno))
                # os.system / os.popen
                if isinstance(node.func, ast.Attribute) and \
                        node.func.attr in DANGEROUS_OS:
                    out.append(SecurityFinding(
                        "HIGH", "command_injection",
                        f"os.{node.func.attr}()", node.lineno))
                # subprocess with shell=True
                if name and name.startswith("subprocess"):
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) \
                                and kw.value.value is True:
                            out.append(SecurityFinding(
                                "HIGH", "command_injection",
                                "subprocess shell=True", node.lineno))
                # open() with string concatenation -> potential path traversal
                if name == "open" and node.args and \
                        isinstance(node.args[0], ast.BinOp):
                    out.append(SecurityFinding(
                        "MEDIUM", "path_traversal",
                        "open() with concatenated path", node.lineno))
        return out

    @staticmethod
    def _call_name(func) -> str:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            parts = []
            cur = func
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            return ".".join(reversed(parts))
        return ""
