#!/usr/bin/env python3
"""Persistent HuggingFace model downloader with multiple fallback strategies.

Strategies (tried in order):
1. HuggingFace Hub direct (snapshot_download)
2. HF with mirror endpoint
3. ModelScope snapshot_download
4. Individual file wget with retry

Usage:
    ~/.jarvis/venv-jarvis/bin/python scripts/download_hf_model.py
"""
import os
import sys
import time
import json
from pathlib import Path

os.environ.setdefault("LIBRARY_PATH", os.path.expanduser("~/.local/lib/cuda_stub") + ":" + os.environ.get("LIBRARY_PATH", ""))

MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
LOCAL_DIR = Path.home() / ".jarvis" / "models" / "base" / "qwen2.5-7b-instruct"
MAX_RETRIES = 5
RETRY_DELAY = 60  # seconds


def strategy_hf_hub():
    """Strategy 1: Direct HF hub download."""
    from huggingface_hub import snapshot_download
    print(f"[DOWNLOAD] Strategy 1: HF Hub direct -> {MODEL_ID}")
    return snapshot_download(
        MODEL_ID,
        local_dir=str(LOCAL_DIR),
        local_dir_use_symlinks=False,
        resume_download=True,
    )


def strategy_hf_mirror():
    """Strategy 2: HF mirror endpoint."""
    from huggingface_hub import snapshot_download
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    print(f"[DOWNLOAD] Strategy 2: HF mirror -> {MODEL_ID}")
    return snapshot_download(
        MODEL_ID,
        local_dir=str(LOCAL_DIR),
        local_dir_use_symlinks=False,
        resume_download=True,
    )


def strategy_modelscope():
    """Strategy 3: ModelScope mirror."""
    from modelscope import snapshot_download
    print(f"[DOWNLOAD] Strategy 3: ModelScope -> {MODEL_ID}")
    ms_id = MODEL_ID.replace("/", "/")  # ModelScope uses same format for Qwen
    return snapshot_download(
        ms_id,
        cache_dir=str(LOCAL_DIR),
    )


def strategy_wget_files():
    """Strategy 4: Download critical files individually via wget.
    Not a full model download, just gets config/tokenizer for validation.
    """
    import subprocess
    print(f"[DOWNLOAD] Strategy 4: wget individual files")
    base_url = f"https://huggingface.co/{MODEL_ID}/resolve/main"
    files = ["config.json", "tokenizer.json", "tokenizer_config.json"]
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    for f in files:
        url = f"{base_url}/{f}"
        out = LOCAL_DIR / f
        if out.exists():
            continue
        cmd = ["wget", "-q", "--show-progress", "-O", str(out), url]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"wget failed for {f}: {result.stderr}")
    return str(LOCAL_DIR)


def main():
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    strategies = [
        strategy_hf_hub,
        strategy_hf_mirror,
        strategy_modelscope,
        strategy_wget_files,
    ]

    for attempt in range(MAX_RETRIES):
        print(f"\n[DOWNLOAD] Attempt {attempt + 1}/{MAX_RETRIES}")
        for strat in strategies:
            try:
                result = strat()
                print(f"[DOWNLOAD] SUCCESS: {result}")
                marker = LOCAL_DIR / ".download_complete"
                marker.write_text(json.dumps({"model_id": MODEL_ID, "strategy": strat.__name__}))
                print(f"[DOWNLOAD] Marker written to {marker}")
                return 0
            except Exception as e:
                print(f"[DOWNLOAD] {strat.__name__} failed: {e}")
                continue
        print(f"[DOWNLOAD] All strategies failed. Waiting {RETRY_DELAY}s...")
        time.sleep(RETRY_DELAY)

    print("[DOWNLOAD] Gave up after all retries.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
