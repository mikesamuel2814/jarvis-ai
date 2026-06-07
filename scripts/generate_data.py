#!/usr/bin/env python3
"""
JARVIS Synthetic Data Generator
Generates training examples for reasoning, tool-calling, and safety.
Integrates with Kimi CLI via src/tools/kimi_bridge.py
"""
import os
import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict

JARVIS_DIR = Path("/home/kali/.jarvis")
DATA_DIR = JARVIS_DIR / "data"
SYNTHETIC_DIR = DATA_DIR / "synthetic"
LOGS_DIR = JARVIS_DIR / "logs"


REASONING_TEMPLATE = """You are JARVIS data generator. Create expert-level training examples.

Generate a complex system administration problem requiring multi-step reasoning.
Output as JSON:
{
  "instruction": "the problem statement",
  "reasoning_trace": "step-by-step thought process",
  "answer": "final concise answer"
}"""

TOOL_TEMPLATE = """You are JARVIS data generator. Create expert-level training examples.

Generate {count} training examples for the {tool_name} tool.
Each example must be valid JSON with:
{
  "instruction": "user request",
  "tool_call_json": {"tool": "{tool_name}", "parameters": {...}},
  "expected_output": "what the tool should return"
}"""


def generate_reasoning(num_examples: int, output_file: Path) -> int:
    """Generate reasoning problems using KimiBridge or local fallback."""
    count = 0
    try:
        sys.path.insert(0, str(JARVIS_DIR / "src"))
        from tools.kimi_bridge import KimiBridge
        bridge = KimiBridge()
        use_kimi = True
    except Exception as e:
        print(f"KimiBridge not available ({e}), using local fallback.")
        use_kimi = False

    with open(output_file, "w") as f:
        for i in range(num_examples):
            if use_kimi:
                try:
                    response = bridge.generate(
                        REASONING_TEMPLATE,
                        system="You are JARVIS data generator. Create expert-level training examples. Output ONLY valid JSON."
                    )
                    # Extract JSON
                    start = response.find('{')
                    end = response.rfind('}') + 1
                    if start != -1 and end > start:
                        obj = json.loads(response[start:end])
                        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                        count += 1
                except Exception as e:
                    print(f"Kimi generation failed for example {i}: {e}")
            else:
                # Local fallback stub
                stub = {
                    "instruction": f"Stub reasoning problem #{i+1}: Analyze system load and optimize services.",
                    "reasoning_trace": "1) Check CPU/RAM usage. 2) Identify top processes. 3) Optimize or restart heavy services.",
                    "answer": "Services optimized."
                }
                f.write(json.dumps(stub, ensure_ascii=False) + "\n")
                count += 1
    return count


def generate_tool_examples(tool_name: str, count: int, output_file: Path) -> int:
    """Generate tool-calling training examples."""
    written = 0
    try:
        sys.path.insert(0, str(JARVIS_DIR / "src"))
        from tools.kimi_bridge import KimiBridge
        bridge = KimiBridge()
        use_kimi = True
    except Exception:
        use_kimi = False

    prompt = TOOL_TEMPLATE.format(tool_name=tool_name, count=count)

    with open(output_file, "w") as f:
        if use_kimi:
            try:
                response = bridge.generate(
                    prompt,
                    system="You are JARVIS data generator. Create expert-level training examples. Output a JSON array of examples."
                )
                start = response.find('[')
                end = response.rfind(']') + 1
                if start != -1 and end > start:
                    arr = json.loads(response[start:end])
                    for obj in arr:
                        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                        written += 1
            except Exception as e:
                print(f"Kimi generation failed for {tool_name}: {e}")

        # Fallback stubs for any missing examples
        while written < count:
            stub = {
                "instruction": f"Stub: Use {tool_name} example #{written+1}",
                "tool_call_json": {"tool": tool_name, "parameters": {}},
                "expected_output": "Stub output"
            }
            f.write(json.dumps(stub, ensure_ascii=False) + "\n")
            written += 1
    return written


def main():
    parser = argparse.ArgumentParser(description="JARVIS Synthetic Data Generator")
    parser.add_argument("--reasoning", type=int, default=100, help="Number of reasoning examples")
    parser.add_argument("--tools", nargs="+", default=["file_read", "shell_exec", "web_search"])
    parser.add_argument("--tool-count", type=int, default=50, help="Examples per tool")
    parser.add_argument("--output-dir", default=str(SYNTHETIC_DIR))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)

    print(f"Output directory: {output_dir}")

    # Reasoning
    reasoning_file = output_dir / "reasoning.jsonl"
    count = generate_reasoning(args.reasoning, reasoning_file)
    print(f"Generated {count} reasoning examples -> {reasoning_file}")

    # Tool examples
    for tool in args.tools:
        tool_file = output_dir / f"tools_{tool}.jsonl"
        count = generate_tool_examples(tool, args.tool_count, tool_file)
        print(f"Generated {count} {tool} examples -> {tool_file}")

    print("\nSynthetic data generation complete.")


if __name__ == "__main__":
    main()
