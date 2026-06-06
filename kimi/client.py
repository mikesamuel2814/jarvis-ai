"""
Kimi K2.6 client — Moonshot AI OpenAI-compatible API.

Model: kimi-k2.6
Base:  https://api.moonshot.ai/v1
Docs:  https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart

Key facts (verified June 2026):
  - 262,144 token context window
  - Thinking mode ON by default; access via response.choices[0].message.reasoning_content
  - Disable thinking: extra_body={"thinking": {"type": "disabled"}}
  - Temperature: 1.0 with thinking, 0.6 without
  - Pricing: $0.95/M input, $4.00/M output
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI

log = logging.getLogger("jarvis.kimi")

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
KIMI_CONFIG  = JARVIS_HOME / "config" / "kimi.yaml"
KIMI_LOG     = JARVIS_HOME / "logs" / "kimi_api.log"

MODEL_ID     = "kimi-k2.6"
BASE_URL     = "https://api.moonshot.ai/v1"
CONTEXT_WIN  = 262144
MAX_OUTPUT   = 8192

# Privacy blocklist — content matching these strings is NEVER sent to Kimi
_PRIVACY_BLOCKLIST = [
    "Payment-Gateway", "AsthaCash", ".ssh", "credentials",
    ".env", "secrets", "private_key", "id_rsa", "id_ed25519",
]


def _load_privacy_blocklist() -> list[str]:
    try:
        cfg = yaml.safe_load(KIMI_CONFIG.read_text())
        return cfg.get("kimi", {}).get("privacy_blocklist", _PRIVACY_BLOCKLIST)
    except Exception:
        return _PRIVACY_BLOCKLIST


def _check_privacy(text: str) -> None:
    """Raise ValueError if text contains any blocked pattern."""
    blocklist = _load_privacy_blocklist()
    for pattern in blocklist:
        if pattern.lower() in text.lower():
            raise ValueError(
                f"Privacy boundary: text contains blocked pattern '{pattern}'. "
                "This content cannot be sent to Kimi cloud."
            )


class KimiClient:
    """Thread-safe wrapper around the Kimi K2.6 API."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("MOONSHOT_API_KEY")
        if not key:
            raise EnvironmentError(
                "MOONSHOT_API_KEY not set. "
                "Get yours at https://platform.kimi.ai"
            )
        self._client = OpenAI(api_key=key, base_url=BASE_URL)
        self._log_file = KIMI_LOG
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        self._usage_today: dict[str, int] = {"input": 0, "output": 0}

    # ── Core completion ───────────────────────────────────────────

    def complete(
        self,
        messages: list[dict],
        *,
        thinking: bool = True,
        max_tokens: int = MAX_OUTPUT,
        stream: bool = False,
        tools: list[dict] | None = None,
    ) -> tuple[str, str | None]:
        """
        Call Kimi K2.6.

        Returns (content, reasoning_content).
        reasoning_content is None when thinking=False.
        """
        temperature = 1.0 if thinking else 0.6
        extra: dict[str, Any] = {}
        if not thinking:
            extra["thinking"] = {"type": "disabled"}

        kwargs: dict[str, Any] = dict(
            model=MODEL_ID,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if extra:
            kwargs["extra_body"] = extra
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        t0 = time.time()
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            log.error("Kimi API error: %s", exc)
            raise

        elapsed = time.time() - t0
        msg = resp.choices[0].message
        content   = msg.content or ""
        reasoning = getattr(msg, "reasoning_content", None)

        # Track usage
        if resp.usage:
            self._usage_today["input"]  += resp.usage.prompt_tokens
            self._usage_today["output"] += resp.usage.completion_tokens
            self._log_call(resp.usage.prompt_tokens, resp.usage.completion_tokens, elapsed)

        return content, reasoning

    def query(
        self,
        system: str,
        user: str,
        *,
        thinking: bool = True,
        history: list[dict] | None = None,
    ) -> tuple[str, str | None]:
        """Convenience wrapper — returns (answer, reasoning)."""
        _check_privacy(user)
        _check_privacy(system)

        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user})

        return self.complete(messages, thinking=thinking)

    def extract_json(self, prompt: str, system: str = "") -> dict:
        """Ask Kimi to return JSON; tolerates markdown fences."""
        content, _ = self.query(
            system=system or "Return valid JSON only. No markdown fences, no explanation.",
            user=prompt,
            thinking=False,
        )
        # Strip markdown fences if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
        return json.loads(cleaned)

    # ── File upload (256K context) ─────────────────────────────────

    def upload_file(self, path: Path, purpose: str = "assistants") -> str:
        """Upload a file to Kimi. Returns file_id for use in messages."""
        _check_privacy(str(path))
        with open(path, "rb") as f:
            resp = self._client.files.create(file=f, purpose=purpose)
        log.info("Uploaded %s → file_id=%s", path.name, resp.id)
        return resp.id

    def delete_file(self, file_id: str) -> None:
        self._client.files.delete(file_id)

    # ── Usage / cost helpers ───────────────────────────────────────

    def session_cost(self) -> dict:
        inp = self._usage_today["input"]
        out = self._usage_today["output"]
        return {
            "input_tokens": inp,
            "output_tokens": out,
            "cost_usd": round(inp / 1_000_000 * 0.95 + out / 1_000_000 * 4.00, 4),
        }

    def _log_call(self, inp: int, out: int, elapsed: float) -> None:
        entry = {
            "ts": int(time.time()),
            "model": MODEL_ID,
            "input_tokens": inp,
            "output_tokens": out,
            "cost_usd": round(inp / 1_000_000 * 0.95 + out / 1_000_000 * 4.00, 6),
            "elapsed_s": round(elapsed, 2),
        }
        with open(self._log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")


# Module-level singleton (lazy init)
_instance: KimiClient | None = None


def get_client() -> KimiClient:
    global _instance
    if _instance is None:
        _instance = KimiClient()
    return _instance
