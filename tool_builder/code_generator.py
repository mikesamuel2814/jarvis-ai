"""
Tool Builder — Provider-Agnostic Code Generator

Jarvis builds tools using whatever intelligence is available, in order:

  1. KIMI       — Kimi K2.6 (if MOONSHOT_API_KEY set and reachable).
  2. LOCAL_CODE — Ollama qwen2.5-coder:7b → :3b (code-specialized, on-GPU).
  3. LOCAL_REASON — Ollama deepseek-r1:7b (reasoning fallback).
  4. SYNTH      — Jarvis's own template + shell synthesis. For system/command
                  tools it picks a shell command, RUNS it (granted rooted
                  ability, silently) to confirm it works, then emits a tool
                  that wraps the verified command. No external LLM required.

This makes tool-building independent of any single provider or API key.
Respects the RTX 3050 limit: one GPU model at a time, num_ctx ≤ 2048.
"""

import logging
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

log = logging.getLogger("jarvis.tool_builder.codegen")

OLLAMA_HOST = "http://localhost:11434"
CODE_MODELS = ["qwen2.5-coder:7b", "qwen2.5-coder:3b"]
REASON_MODELS = ["deepseek-r1:7b"]
NUM_CTX = 2048  # VRAM safety (6GB RTX 3050)


class CodeGenerator:
    """Tries each provider until one yields usable Python code."""

    def __init__(self, prefer_local: bool = False):
        # prefer_local: try Ollama before Kimi (cost/offline preference).
        self.prefer_local = prefer_local
        self._kimi = None
        self._kimi_tried = False

    # ── Public API ───────────────────────────────────────────────────────────
    def generate(self, prompt: str, system: str = "") -> Tuple[str, str]:
        """Return (code, provider_used). Raises RuntimeError only if every
        provider — including local synthesis — fails."""
        system = system or (
            "You are a senior Python engineer. Output ONLY valid, self-contained "
            "Python code. No markdown fences, no prose."
        )
        chain = self._provider_chain()
        last_err = None
        for name, fn in chain:
            try:
                code = fn(prompt, system)
                if code and self._looks_like_code(code):
                    log.info("Tool code generated via %s (%d chars)", name, len(code))
                    return self._clean(code), name
                last_err = f"{name}: empty/invalid output"
            except Exception as exc:  # noqa: BLE001
                last_err = f"{name}: {exc}"
                log.debug("Generator %s failed: %s", name, exc)
        raise RuntimeError(f"all code generators failed (last: {last_err})")

    def _provider_chain(self) -> List[Tuple[str, callable]]:
        llm = [("kimi", self._gen_kimi),
               ("local_code", self._gen_local_code),
               ("local_reason", self._gen_local_reason)]
        if self.prefer_local:
            llm = llm[1:] + llm[:1]
        # Synthesis is always the last-resort, never-fails-to-try option.
        return llm + [("synth", self._gen_synth)]

    # ── Provider 1: Kimi ─────────────────────────────────────────────────────
    def _gen_kimi(self, prompt: str, system: str) -> str:
        if not self._kimi and not self._kimi_tried:
            self._kimi_tried = True
            try:
                from kimi.client import get_client
                self._kimi = get_client()
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"kimi unavailable: {exc}")
        if not self._kimi:
            raise RuntimeError("kimi unavailable")
        content, _ = self._kimi.query(system, prompt, thinking=False)
        return content

    # ── Providers 2 & 3: Ollama ──────────────────────────────────────────────
    def _gen_local_code(self, prompt: str, system: str) -> str:
        return self._ollama(CODE_MODELS, prompt, system)

    def _gen_local_reason(self, prompt: str, system: str) -> str:
        code = self._ollama(REASON_MODELS, prompt, system)
        # deepseek-r1 emits <think>...</think>; strip it.
        return re.sub(r"<think>.*?</think>", "", code, flags=re.DOTALL)

    def _ollama(self, models: List[str], prompt: str, system: str) -> str:
        import ollama
        client = ollama.Client(host=OLLAMA_HOST)
        available = {m.get("model") or m.get("name") for m in
                     client.list().get("models", [])}
        for model in models:
            if model not in available:
                continue
            resp = client.chat(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                options={"num_ctx": NUM_CTX, "temperature": 0.2},
            )
            return resp["message"]["content"]
        raise RuntimeError(f"none of {models} installed")

    # ── Provider 4: Jarvis's own synthesis (no LLM) ──────────────────────────
    def _gen_synth(self, prompt: str, system: str) -> str:
        """Build a tool from Jarvis's own knowledge. For command-style tools it
        verifies the underlying shell command actually runs before emitting."""
        name = self._infer_name(prompt)
        cmd = self._infer_command(prompt)
        if cmd:
            verified = self._verify_command(cmd)
            note = "verified" if verified else "unverified"
            return self._shell_tool_template(name, prompt, cmd, note)
        return self._stub_template(name, prompt)

    # ── Synthesis helpers ────────────────────────────────────────────────────
    # Heuristic mapping from intent keywords to a shell command.
    _CMD_MAP = [
        (r"\b(disk|storage|free space)\b", "df -h /"),
        (r"\b(memory|ram)\b", "free -h"),
        (r"\b(cpu|load)\b", "uptime"),
        (r"\b(process|running)\b", "ps aux --sort=-%cpu | head -15"),
        (r"\b(uptime|boot)\b", "uptime -p"),
        (r"\b(kernel|os version)\b", "uname -a"),
        (r"\b(network|ip address|interface)\b", "ip -br addr"),
        (r"\b(listen|port|socket)\b", "ss -tlnp"),
        (r"\b(gpu|nvidia|vram)\b", "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader"),
        (r"\b(temperature|temp|sensor)\b", "sensors 2>/dev/null || echo 'lm-sensors not installed'"),
        (r"\b(service|systemd).*status\b", "systemctl --failed --no-legend"),
        (r"\b(docker|container)\b", "docker ps --format '{{.Names}}: {{.Status}}'"),
    ]

    def _infer_command(self, prompt: str) -> Optional[str]:
        low = prompt.lower()
        for pat, cmd in self._CMD_MAP:
            if re.search(pat, low):
                # Only return if the base binary exists.
                binary = cmd.split()[0]
                if shutil.which(binary) or binary in ("echo",):
                    return cmd
        return None

    def _verify_command(self, cmd: str) -> bool:
        """Run the command silently (granted rooted ability) to confirm it works."""
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True,
                               text=True, timeout=15)
            return r.returncode == 0 and bool(r.stdout.strip())
        except Exception:  # noqa: BLE001
            return False

    def _infer_name(self, prompt: str) -> str:
        words = re.findall(r"[a-z]+", prompt.lower())
        stop = {"a", "the", "tool", "that", "to", "for", "of", "get", "show", "me"}
        words = [w for w in words if w not in stop][:3]
        return "_".join(words) or "synth_tool"

    def _shell_tool_template(self, name, desc, cmd, note) -> str:
        esc = desc.replace('"', "'")
        return f'''from tools.decorator import jarvis_tool
from tools.result import ToolResult
import subprocess


@jarvis_tool(name="{name}", description="{esc}", params={{}},
             rank="R0", scope="READ", category="dynamic")
def {name}():
    """Auto-synthesized by Jarvis ({note} command: {cmd})."""
    try:
        r = subprocess.run({cmd!r}, shell=True, capture_output=True,
                           text=True, timeout=15)
        if r.returncode != 0:
            return ToolResult.fail(error=r.stderr.strip() or "command failed")
        return ToolResult.ok(output=r.stdout.strip())
    except Exception as exc:
        return ToolResult.fail(error=str(exc))
'''

    def _stub_template(self, name, desc) -> str:
        esc = desc.replace('"', "'")
        return f'''from tools.decorator import jarvis_tool
from tools.result import ToolResult


@jarvis_tool(name="{name}", description="{esc}", params={{}},
             rank="R0", scope="READ", category="dynamic")
def {name}():
    """Auto-synthesized stub for: {esc}. Refine when an LLM provider is available."""
    return ToolResult.ok(output="{esc} — stub; no command mapping found")
'''

    # ── Validation ───────────────────────────────────────────────────────────
    @staticmethod
    def _looks_like_code(text: str) -> bool:
        t = text.strip()
        return ("def " in t or "import " in t or "@jarvis_tool" in t)

    @staticmethod
    def _clean(code: str) -> str:
        code = code.strip()
        if code.startswith("```"):
            code = re.sub(r"^```[a-zA-Z]*\n", "", code)
            code = re.sub(r"\n```\s*$", "", code)
        return code.strip()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gen = CodeGenerator(prefer_local=True)
    # Force synthesis path to show offline capability:
    code = gen._gen_synth("a tool to check disk free space", "")
    print(code)
    print("--- verify import ---")
    import ast
    ast.parse(code)
    print("OK")
