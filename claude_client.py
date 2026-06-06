"""
Jarvis 2.0 — Claude (Anthropic) cloud client.

Drop-in replacement for kimi/client.py while MOONSHOT_API_KEY is unavailable.
Switch back by changing brain.py's cloud imports to kimi.client.

Model: claude-sonnet-4-6  (configurable via jarvis_v2.yaml → claude.model)
Pricing (Jun 2026): $3/M input · $15/M output (Sonnet 4.6)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("jarvis.claude_client")

JARVIS_HOME  = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
SECRETS_FILE = JARVIS_HOME / "config" / "secrets.env"
CONFIG_FILE  = JARVIS_HOME / "config" / "jarvis_v2.yaml"
CLAUDE_LOG   = JARVIS_HOME / "logs" / "claude_api.log"

DEFAULT_MODEL   = "claude-sonnet-4-6"
MAX_TOKENS      = 8192

# Privacy blocklist — never sent to cloud
_PRIVACY_BLOCKLIST = [
    "Payment-Gateway", "AsthaCash", ".ssh", "credentials",
    ".env", "secrets", "private_key", "id_rsa", "id_ed25519",
]


def _load_secret(key: str, default: str = "") -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return default


def _load_privacy_blocklist() -> list[str]:
    try:
        cfg = yaml.safe_load(CONFIG_FILE.read_text())
        return cfg.get("claude", {}).get("privacy_blocklist", _PRIVACY_BLOCKLIST)
    except Exception:
        return _PRIVACY_BLOCKLIST


def _check_privacy(text: str) -> None:
    """Raise ValueError if text contains any blocked pattern."""
    for pattern in _load_privacy_blocklist():
        if pattern.lower() in text.lower():
            raise ValueError(
                f"Privacy boundary: text contains '{pattern}'. "
                "Cannot send to Claude cloud."
            )


class ClaudeClient:
    """Thread-safe Anthropic Claude API wrapper matching KimiClient interface."""

    def __init__(self, api_key: str | None = None) -> None:
        import anthropic  # lazy — avoids import cost when edge-only
        key = api_key or _load_secret("ANTHROPIC_API_KEY")
        if not key or key == "REPLACE_ME":
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. "
                "Add it to ~/.jarvis/config/secrets.env"
            )
        cfg = {}
        try:
            raw = yaml.safe_load(CONFIG_FILE.read_text())
            cfg = raw.get("claude", {}) if raw else {}
        except Exception:
            pass

        self._model = cfg.get("model", DEFAULT_MODEL)
        self._client = anthropic.Anthropic(api_key=key)
        CLAUDE_LOG.parent.mkdir(parents=True, exist_ok=True)
        self._usage: dict[str, int] = {"input": 0, "output": 0}

    # ── Core completion ───────────────────────────────────────────────

    def complete(
        self,
        messages: list[dict],
        *,
        system: str = "",
        max_tokens: int = MAX_TOKENS,
    ) -> tuple[str, None]:
        """
        Call Claude. Returns (content, None) — None for reasoning slot
        so callers handle it the same way they handle Kimi's reasoning_content.
        """
        t0 = time.time()
        kwargs: dict[str, Any] = dict(
            model=self._model,
            max_tokens=max_tokens,
            messages=messages,
        )
        if system:
            kwargs["system"] = system

        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as exc:
            log.error("Claude API error: %s", exc)
            raise

        elapsed = time.time() - t0
        content = resp.content[0].text if resp.content else ""

        if resp.usage:
            inp = resp.usage.input_tokens
            out = resp.usage.output_tokens
            self._usage["input"]  += inp
            self._usage["output"] += out
            self._log_call(inp, out, elapsed)

        return content, None

    def query(
        self,
        system: str,
        user: str,
        *,
        thinking: bool = True,  # kept for interface compatibility; ignored
        history: list[dict] | None = None,
    ) -> tuple[str, None]:
        """Convenience wrapper — returns (answer, None)."""
        _check_privacy(user)
        _check_privacy(system)

        messages: list[dict] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user})

        return self.complete(messages, system=system)

    def extract_json(self, prompt: str, system: str = "") -> dict:
        """Ask Claude to return JSON; tolerates markdown fences."""
        import re
        content, _ = self.query(
            system=system or "Return valid JSON only. No markdown fences, no explanation.",
            user=prompt,
        )
        cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
        return json.loads(cleaned)

    # ── Usage / cost helpers ──────────────────────────────────────────

    def session_cost(self) -> dict:
        inp = self._usage["input"]
        out = self._usage["output"]
        # Sonnet 4.6 pricing: $3/M input, $15/M output
        return {
            "input_tokens":  inp,
            "output_tokens": out,
            "cost_usd": round(inp / 1_000_000 * 3.00 + out / 1_000_000 * 15.00, 4),
        }

    def _log_call(self, inp: int, out: int, elapsed: float) -> None:
        entry = {
            "ts":            int(time.time()),
            "model":         self._model,
            "input_tokens":  inp,
            "output_tokens": out,
            "cost_usd":      round(inp / 1_000_000 * 3.00 + out / 1_000_000 * 15.00, 6),
            "elapsed_s":     round(elapsed, 2),
        }
        with open(CLAUDE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")


# Module-level singleton (lazy init)
_instance: ClaudeClient | None = None


def get_client() -> ClaudeClient:
    global _instance
    if _instance is None:
        _instance = ClaudeClient()
    return _instance
