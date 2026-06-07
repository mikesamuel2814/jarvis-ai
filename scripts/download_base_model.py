#!/usr/bin/env python3
"""Download JARVIS base model from ModelScope (HF mirror)."""
import os
import sys
from modelscope import snapshot_download

# Primary: Qwen2.5-7B-Instruct (best 7B for tool use + instruction following)
# Fallbacks considered: Qwen2.5-3B-Instruct, Phi-4-mini-instruct
MODEL_ID = "qwen/Qwen2.5-7B-Instruct"
LOCAL_DIR = os.path.expanduser("~/.jarvis/models/base/qwen2.5-7b-instruct")

def main():
    os.makedirs(LOCAL_DIR, exist_ok=True)
    print(f"[DOWNLOAD] Starting {MODEL_ID} -> {LOCAL_DIR}")
    try:
        model_dir = snapshot_download(
            MODEL_ID,
            cache_dir=LOCAL_DIR,
            local_files_only=False,
        )
        print(f"[DONE] Model cached at: {model_dir}")
        # Write a marker file for the training scripts
        with open(os.path.join(LOCAL_DIR, ".model_id"), "w") as f:
            f.write(MODEL_ID)
        print("[DONE] Marker file written.")
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
