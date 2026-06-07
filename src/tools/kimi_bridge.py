"""
JARVIS Kimi CLI Bridge
Integrates Kimi CLI for tasks requiring frontier model capability.
Compatible with v3 brain_router.py KIMI tier.
"""
import os
import json
import subprocess
from typing import Optional, Dict, Any
from pathlib import Path

JARVIS_DIR = Path("/home/kali/.jarvis")


class KimiBridge:
    """Bridge to Kimi CLI for tasks requiring frontier model capability."""

    def __init__(self, model: str = "kimi-k2.6"):
        self.model = model
        self._check_cli()

    def _check_cli(self):
        """Verify kimi CLI is available."""
        result = subprocess.run(
            ["which", "kimi"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError("kimi CLI not found in PATH. Install with: pip install kimi-cli")

    def generate(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        timeout: int = 120
    ) -> str:
        """Call Kimi CLI and return response text."""
        cmd = [
            "kimi", "chat",
            "--model", self.model,
            "--message", prompt
        ]
        if system:
            cmd.extend(["--system", system])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:
            return f"Kimi CLI Error: {result.stderr}"

        return result.stdout.strip()

    def generate_structured(
        self,
        prompt: str,
        schema: Dict[str, Any],
        system: str = "",
        max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """Generate structured output via Kimi CLI with JSON schema enforcement."""
        structured_prompt = f"""{prompt}

You must respond with valid JSON matching this schema:
{json.dumps(schema, indent=2)}

Respond ONLY with JSON. No markdown, no explanations."""

        response = self.generate(structured_prompt, system=system, max_tokens=max_tokens)

        # Extract JSON block
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start != -1 and end > start:
                return json.loads(response[start:end])

            # Try array
            start = response.find('[')
            end = response.rfind(']') + 1
            if start != -1 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

        return {"error": "Failed to parse JSON", "raw": response}

    def distill_instruction(
        self,
        instruction: str,
        teacher_system: str = "You are an expert AI assistant. Think step by step. Provide detailed, accurate answers."
    ) -> str:
        """Generate a teacher (soft label) response for knowledge distillation."""
        return self.generate(instruction, system=teacher_system)

    def analyze_code(
        self,
        code: str,
        language: str = "python"
    ) -> Dict[str, Any]:
        """Analyze code for issues, optimizations, and security."""
        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                            "line": {"type": "integer"},
                            "message": {"type": "string"},
                            "fix": {"type": "string"}
                        }
                    }
                },
                "optimizations": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "security_concerns": {
                    "type": "array",
                    "items": {"type": "string"}
                }
            },
            "required": ["summary", "issues", "optimizations", "security_concerns"]
        }
        prompt = f"Analyze this {language} code:\n\n```{language}\n{code}\n```\n\nProvide a structured analysis."
        return self.generate_structured(prompt, schema, system="You are a senior code reviewer and security auditor.")

    def build_tool(self, requirement: str) -> Dict[str, Any]:
        """Generate a new tool implementation via Kimi."""
        schema = {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "description": {"type": "string"},
                "parameters": {"type": "object"},
                "implementation": {"type": "string"},
                "rank": {"type": "string", "enum": ["R0", "R1", "R2", "R3", "R4", "R5", "R6"]},
                "scope": {"type": "string", "enum": ["READ", "LOCAL", "NETWORK", "PRIVILEGED"]},
                "tests": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["tool_name", "description", "parameters", "implementation", "rank", "scope"]
        }
        prompt = f"Build a JARVIS tool for this requirement: {requirement}\n\nThe tool must be a Python function with @jarvis_tool decorator."
        return self.generate_structured(prompt, schema, system="You are JARVIS ToolBuilder. Generate production-ready Python tools.")


def main():
    """Quick test of KimiBridge."""
    bridge = KimiBridge()
    response = bridge.generate("Hello, this is a connectivity test.", system="Be concise.")
    print("Kimi response:", response[:200])


if __name__ == "__main__":
    main()
