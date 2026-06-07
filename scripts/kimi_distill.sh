#!/bin/bash
# Knowledge distillation using Kimi CLI as teacher

set -e

JARVIS_DIR="/home/kali/.jarvis"
INPUT_DATA="$JARVIS_DIR/data/processed/train.jsonl"
OUTPUT_DATA="$JARVIS_DIR/data/processed/train_distilled.jsonl"

if [ ! -f "$INPUT_DATA" ]; then
    echo "WARNING: $INPUT_DATA not found. Creating empty file."
    mkdir -p "$(dirname "$INPUT_DATA")"
    touch "$INPUT_DATA"
fi

mkdir -p "$(dirname "$OUTPUT_DATA")"

TEACHER_SYSTEM="You are an expert AI assistant. Think step by step. Provide detailed, accurate answers."

echo "JARVIS Knowledge Distillation via Kimi CLI"
echo "Input:  $INPUT_DATA"
echo "Output: $OUTPUT_DATA"

line_count=0
while IFS= read -r line; do
    instruction=$(echo "$line" | python3 -c "import sys,json; print(json.load(sys.stdin).get('instruction',''))" 2>/dev/null || echo "")
    if [ -z "$instruction" ]; then
        continue
    fi

    if command -v kimi &> /dev/null; then
        teacher_output=$(kimi chat \
            --system "$TEACHER_SYSTEM" \
            --message "$instruction" 2>/dev/null || echo "")
    else
        teacher_output="[Kimi CLI not available]"
    fi

    # Append teacher output to the record
    echo "$line" | python3 -c "
import sys, json
data = json.load(sys.stdin)
data['teacher_output'] = sys.argv[1]
print(json.dumps(data, ensure_ascii=False))
" "$teacher_output" >> "$OUTPUT_DATA"

    line_count=$((line_count + 1))
    if [ $((line_count % 10)) -eq 0 ]; then
        echo "Processed $line_count lines..."
    fi

    sleep 1  # Rate limit
done < "$INPUT_DATA"

echo "Distillation complete. $line_count records written to $OUTPUT_DATA"
