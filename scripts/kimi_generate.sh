#!/bin/bash
# Generate synthetic training data using Kimi CLI

set -e

JARVIS_DIR="/home/kali/.jarvis"
OUTPUT_DIR="$JARVIS_DIR/data/synthetic"
mkdir -p "$OUTPUT_DIR"

PERSONA="You are JARVIS data generator. Create expert-level training examples. Output ONLY valid JSON."

echo "JARVIS Synthetic Data Generation via Kimi CLI"
echo "Output: $OUTPUT_DIR"

# Generate reasoning problems
REASONING_FILE="$OUTPUT_DIR/reasoning.jsonl"
echo "Generating reasoning examples -> $REASONING_FILE"
for i in $(seq 1 100); do
    if command -v kimi &> /dev/null; then
        kimi chat \
            --system "$PERSONA" \
            --message "Generate a complex system administration problem requiring multi-step reasoning. Output as JSON: {instruction, reasoning_trace, answer}" \
            >> "$REASONING_FILE"
    else
        echo '{"instruction":"stub","reasoning_trace":"stub","answer":"stub"}' >> "$REASONING_FILE"
    fi
done

# Generate tool-calling examples
for tool in file_read shell_exec web_search; do
    TOOL_FILE="$OUTPUT_DIR/tools_${tool}.jsonl"
    echo "Generating $tool examples -> $TOOL_FILE"
    if command -v kimi &> /dev/null; then
        kimi chat \
            --system "$PERSONA" \
            --message "Generate 50 training examples for the $tool tool. Format: {instruction, tool_call_json, expected_output}" \
            >> "$TOOL_FILE"
    else
        echo '{"instruction":"stub","tool_call_json":{"tool":"'"$tool"'"},"expected_output":"stub"}' >> "$TOOL_FILE"
    fi
done

echo "Synthetic data generation complete."
echo "Next: python $JARVIS_DIR/scripts/generate_data.py for structured generation."
