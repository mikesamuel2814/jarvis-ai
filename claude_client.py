"""
Jarvis 2.0 — Claude CLI cloud client.

Uses the installed Claude Code CLI (claude -p) — no API key required.
Auth comes from the existing Claude Code session/keychain.

Swap to kimi/client.py when MOONSHOT_API_KEY is available.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

# Prevent segfault when Claude CLI subprocess runs alongside Chrome/Selenium
_SAFE_ENV = {**os.environ, "MALLOC_ARENA_MAX": "2"}

import yaml

log = logging.getLogger("jarvis.claude_client")

JARVIS_HOME  = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
CONFIG_FILE  = JARVIS_HOME / "config" / "jarvis_v2.yaml"
CLAUDE_LOG   = JARVIS_HOME / "logs" / "claude_api.log"

DEFAULT_MODEL = "claude-sonnet-4-6"
CLI_TIMEOUT   = 120  # seconds

# Privacy patterns — actual secret material, NOT project/word names.
# Each entry is a compiled regex; a match means the text must not go to cloud.
#
# What IS blocked:  real secret values — API keys, tokens, private key PEM blocks,
#                   password/secret assignments, AWS key IDs, shell env exports with
#                   a secret-looking value.
# What is NOT blocked: project names (AsthaCash, Payment-Gateway, Starline, etc.),
#                      file-path fragments, or any ordinary words.
_SECRET_PATTERNS: list[re.Pattern] = [
    # OpenAI / Anthropic / generic sk- keys
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    # AWS Access Key IDs
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # AWS Secret Access Keys (40-char base64ish after assignment)
    re.compile(r'(?:aws_secret_access_key|AWS_SECRET)["\s]*[=:]["\s]*[A-Za-z0-9/+]{40}\b', re.IGNORECASE),
    # Generic secret/password/token variable assignments with a non-trivial value
    re.compile(
        r'(?:password|passwd|secret|token|api[_\-]?key|auth[_\-]?key|private[_\-]?key)'
        r'\s*[=:]\s*["\']?[A-Za-z0-9!@#$%^&*()\-_+={}\[\]|\\:;<>,.?/`~]{8,}',
        re.IGNORECASE,
    ),
    # PEM private key blocks
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    # Shell export with a secret-looking assignment
    re.compile(
        r'\bexport\s+[A-Z_]{3,}(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)[A-Z_]*\s*=\s*\S{8,}',
        re.IGNORECASE,
    ),
    # GitHub / GitLab personal access tokens
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|glpat)_[A-Za-z0-9_]{20,}\b"),
    # Generic long hex strings that look like secrets (32+ hex chars not in URLs)
    re.compile(r'(?<![./\w])[0-9a-f]{32,}(?![./\w])', re.IGNORECASE),
]


def _load_extra_patterns() -> list[re.Pattern]:
    """
    Load additional regex patterns from jarvis_v2.yaml under
    claude.privacy_patterns (list of regex strings).  Never raises.
    """
    try:
        cfg = yaml.safe_load(CONFIG_FILE.read_text()) or {}
        raw = cfg.get("claude", {}).get("privacy_patterns", [])
        return [re.compile(p) for p in raw if isinstance(p, str)]
    except Exception:
        return []


def _check_privacy(text: str) -> None:
    """
    Raise ValueError if text appears to contain real secret material.

    Uses regex patterns matching actual secret formats (API keys, PEM blocks,
    password assignments, etc.).  Plain project names, file paths, and ordinary
    words are never blocked.
    """
    all_patterns = _SECRET_PATTERNS + _load_extra_patterns()
    for pat in all_patterns:
        m = pat.search(text)
        if m:
            # Surface the matched fragment (truncated) so the caller can diagnose
            snippet = m.group(0)[:40].replace("\n", " ")
            raise ValueError(
                f"Privacy boundary: text contains a secret-like pattern "
                f"(matched: '{snippet}...'). Cannot send to Claude cloud."
            )


def _find_claude() -> str:
    path = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not os.path.exists(path):
        raise EnvironmentError("claude CLI not found. Install via: npm install -g @anthropic-ai/claude-code")
    return path


def _format_history(history: list[dict]) -> str:
    """Format conversation history as a text block to prepend to the query."""
    if not history:
        return ""
    lines = ["[Conversation context]"]
    for msg in history[-10:]:  # cap at 10 turns
        role = msg.get("role", "user").capitalize()
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
            )
        lines.append(f"{role}: {content[:400]}")
    lines.append("")
    return "\n".join(lines)


class ClaudeClient:
    """Runs cloud queries via the Claude Code CLI (claude -p)."""

    def __init__(self) -> None:
        self._bin = _find_claude()
        cfg = {}
        try:
            raw = yaml.safe_load(CONFIG_FILE.read_text())
            cfg = (raw or {}).get("claude", {})
        except Exception:
            pass
        self._model = cfg.get("model", DEFAULT_MODEL)
        CLAUDE_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._usage: dict[str, int] = {"calls": 0, "chars_out": 0}

    # ── Core call ─────────────────────────────────────────────────────

    def complete(
        self,
        user_message: str,
        system: str = "",
        *,
        timeout: int = CLI_TIMEOUT,
    ) -> tuple[str, None]:
        """
        Call Claude CLI non-interactively.
        Returns (content, None) — None keeps parity with KimiClient interface.
        """
        cmd = [
            self._bin,
            "--print",
            "--model", self._model,
            "--no-session-persistence",
        ]
        if system:
            cmd += ["--system-prompt", system]

        t0 = time.time()
        try:
            result = subprocess.run(
                cmd,
                input=user_message,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_SAFE_ENV,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Claude CLI timed out after {timeout}s")

        elapsed = time.time() - t0

        if result.returncode != 0:
            err = result.stderr.strip()[:300]
            raise RuntimeError(f"Claude CLI error (exit {result.returncode}): {err}")

        content = result.stdout.strip()
        self._usage["calls"] += 1
        self._usage["chars_out"] += len(content)
        self._log_call(len(user_message), len(content), elapsed)
        return content, None

    def query(
        self,
        system: str,
        user: str,
        *,
        thinking: bool = True,   # kept for interface compatibility
        history: list[dict] | None = None,
    ) -> tuple[str, None]:
        """Convenience wrapper matching KimiClient.query() signature."""
        _check_privacy(user)
        _check_privacy(system)

        history_block = _format_history(history or [])
        full_message = history_block + user if history_block else user

        return self.complete(full_message, system=system)

    def extract_json(self, prompt: str, system: str = "") -> dict:
        """Ask Claude to return JSON; strips markdown fences."""
        content, _ = self.query(
            system=system or "Return valid JSON only. No markdown fences, no explanation.",
            user=prompt,
        )
        cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
        return json.loads(cleaned)

    # ── Usage helpers ─────────────────────────────────────────────────

    def session_cost(self) -> dict:
        return {
            "calls":     self._usage["calls"],
            "chars_out": self._usage["chars_out"],
            "cost_usd":  "N/A (CLI session — billed to Claude plan)",
        }

    def _log_call(self, chars_in: int, chars_out: int, elapsed: float) -> None:
        entry = {
            "ts":        int(time.time()),
            "model":     self._model,
            "chars_in":  chars_in,
            "chars_out": chars_out,
            "elapsed_s": round(elapsed, 2),
        }
        with open(CLAUDE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")


# Module-level singleton
_instance: ClaudeClient | None = None


def get_client() -> ClaudeClient:
    global _instance
    if _instance is None:
        _instance = ClaudeClient()
    return _instance
