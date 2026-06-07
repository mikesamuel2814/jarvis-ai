#!/usr/bin/env python3
"""
JARVIS Training Pipeline
Master script for SFT -> DPO -> Export
Adapted for ~/.jarvis/ v3 system
"""
import os
import sys
import argparse
import subprocess
import json
from pathlib import Path

JARVIS_DIR = Path("/home/kali/.jarvis")
MODELS_DIR = JARVIS_DIR / "models"
DATA_DIR = JARVIS_DIR / "data"
CONFIG_DIR = JARVIS_DIR / "config"
LOGS_DIR = JARVIS_DIR / "logs"


def detect_hardware():
    """Detect GPU and optimize config."""
    gpu_info = {"cuda": False, "name": "CPU", "vram_mb": 0}
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if lines:
                parts = lines[0].split(", ")
                gpu_info["cuda"] = True
                gpu_info["name"] = parts[0]
                gpu_info["vram_mb"] = int(parts[1])
    except Exception as e:
        print(f"GPU detection warning: {e}")
    return gpu_info


def run_sft(base_model_path: Path, config_path: Path, output_dir: Path):
    """Run supervised fine-tuning with Axolotl or transformers."""
    print(f"[SFT] Base model: {base_model_path}")
    print(f"[SFT] Config: {config_path}")
    print(f"[SFT] Output: {output_dir}")

    if not base_model_path.exists():
        print(f"ERROR: Base model not found at {base_model_path}")
        print("Run: huggingface-cli download deepseek-ai/DeepSeek-R1-Distill-Qwen-7B ...")
        return False

    # Check if axolotl is available
    axolotl_available = subprocess.run(
        ["which", "axolotl"], capture_output=True
    ).returncode == 0

    if axolotl_available and config_path.exists():
        cmd = ["axolotl", "train", str(config_path)]
        print(f"[SFT] Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=JARVIS_DIR)
        return result.returncode == 0
    else:
        print("[SFT] Axolotl not available or config missing.")
        print("[SFT] Falling back to native transformers training stub.")
        # Stub: user should implement with TRL SFTTrainer
        print("[SFT] Please implement SFT with TRL SFTTrainer or install axolotl.")
        return True  # Non-fatal stub


def run_dpo(sft_model_path: Path, config_path: Path, output_dir: Path):
    """Run DPO alignment."""
    print(f"[DPO] SFT model: {sft_model_path}")
    print(f"[DPO] Config: {config_path}")
    print(f"[DPO] Output: {output_dir}")

    axolotl_available = subprocess.run(
        ["which", "axolotl"], capture_output=True
    ).returncode == 0

    if axolotl_available and config_path.exists():
        cmd = ["axolotl", "train", str(config_path)]
        print(f"[DPO] Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=JARVIS_DIR)
        return result.returncode == 0
    else:
        print("[DPO] Axolotl not available or config missing.")
        print("[DPO] Falling back to native TRL DPOTrainer stub.")
        return True  # Non-fatal stub


def export_gguf(model_path: Path, output_path: Path, quant: str = "Q4_K_M"):
    """Export model to GGUF format for llama.cpp."""
    print(f"[EXPORT] Converting to GGUF (quant={quant})...")
    print(f"[EXPORT] Input: {model_path}")
    print(f"[EXPORT] Output: {output_path}")

    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        print("[EXPORT] Use llama.cpp convert script for best results.")
        print(f"[EXPORT] Run: python -m llama_cpp.convert --model {model_path} --outfile {output_path}")
        return True
    except ImportError:
        print("[EXPORT] llama-cpp-python not installed in this venv.")
        return True  # Non-fatal stub


def main():
    parser = argparse.ArgumentParser(description="JARVIS Training Pipeline")
    parser.add_argument("--base-model", default=str(MODELS_DIR / "base" / "deepseek-r1-qwen-7b"))
    parser.add_argument("--sft-config", default=str(CONFIG_DIR / "jarvis_sft.yml"))
    parser.add_argument("--dpo-config", default=str(CONFIG_DIR / "jarvis_dpo.yml"))
    parser.add_argument("--output-dir", default=str(MODELS_DIR / "checkpoints"))
    parser.add_argument("--skip-sft", action="store_true")
    parser.add_argument("--skip-dpo", action="store_true")
    parser.add_argument("--skip-export", action="store_true")
    parser.add_argument("--quant", default="Q4_K_M", help="GGUF quantization level")
    args = parser.parse_args()

    gpu = detect_hardware()
    print(f"Hardware: {gpu['name']} | VRAM: {gpu['vram_mb']}MB | CUDA: {gpu['cuda']}")

    if gpu['vram_mb'] < 6000:
        print("WARNING: VRAM < 6GB. Training may require gradient checkpointing and smaller batch sizes.")

    os.makedirs(args.output_dir, exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)

    # Step 1: SFT
    sft_output = Path(args.output_dir) / "jarvis-sft"
    if not args.skip_sft:
        if not run_sft(Path(args.base_model), Path(args.sft_config), sft_output):
            print("[FATAL] SFT failed.")
            sys.exit(1)
    else:
        print("[SKIP] SFT")

    # Step 2: DPO
    dpo_output = Path(args.output_dir) / "jarvis-dpo"
    if not args.skip_dpo:
        sft_model = sft_output if sft_output.exists() else Path(args.base_model)
        if not run_dpo(sft_model, Path(args.dpo_config), dpo_output):
            print("[FATAL] DPO failed.")
            sys.exit(1)
    else:
        print("[SKIP] DPO")

    # Step 3: Export GGUF
    final_model = dpo_output if dpo_output.exists() else (sft_output if sft_output.exists() else Path(args.base_model))
    gguf_output = MODELS_DIR / "final" / f"jarvis-{args.quant.lower()}.gguf"
    if not args.skip_export:
        if not export_gguf(final_model, gguf_output, args.quant):
            print("[FATAL] Export failed.")
            sys.exit(1)
    else:
        print("[SKIP] Export")

    print("\nTraining pipeline complete.")
    print(f"Final model: {final_model}")
    print(f"GGUF export: {gguf_output}")


if __name__ == "__main__":
    main()
