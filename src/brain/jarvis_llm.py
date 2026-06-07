#!/usr/bin/env python3
"""JARVIS Main LLM Brain

Unified interface for JARVIS language model operations.
- Inference: Routes to Ollama (qwen2.5:7b) for production use
- Training: Interfaces with HF TRL pipeline for fine-tuning
- Future: Will load merged local HF model when training completes
"""
import os
import json
import yaml
import asyncio
from pathlib import Path
from typing import List, Dict, Optional, Any, AsyncGenerator

# Ollama REST API endpoint
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")


def load_config() -> dict:
    cfg_path = Path.home() / ".jarvis" / "config" / "training_config.yml"
    with open(cfg_path, "r") as f:
        return yaml.safe_load(f)


class JarvisLLM:
    """Main LLM interface for JARVIS v3."""

    def __init__(self, backend: str = "auto"):
        self.cfg = load_config()
        self.ollama_model = self.cfg["ollama_model"]
        self.hf_model_path = self.cfg["merged_output"]
        self.system_prompt = self.cfg["system_prompt"].strip()

        if backend == "auto":
            self.backend = self._detect_backend()
        else:
            self.backend = backend

        self._hf_model = None
        self._hf_tokenizer = None

        print(f"[JARVIS-LLM] Backend: {self.backend}")

    def _detect_backend(self) -> str:
        """Use HF merged model if available, else Ollama."""
        merged_path = Path(self.hf_model_path)
        if merged_path.exists() and (merged_path / "config.json").exists():
            try:
                import torch
                if torch.cuda.is_available():
                    return "hf"
            except ImportError:
                pass
        return "ollama"

    def _load_hf(self):
        """Lazy-load HF model for local inference."""
        if self._hf_model is not None:
            return
        from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
        import torch

        print(f"[JARVIS-LLM] Loading HF model from {self.hf_model_path}")
        tokenizer = AutoTokenizer.from_pretrained(self.hf_model_path, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            self.hf_model_path,
            torch_dtype=torch.float16,
            device_map="auto",
            trust_remote_code=True,
        )
        self._hf_tokenizer = tokenizer
        self._hf_model = model

    # ------------------------------------------------------------------
    # Ollama sync interface
    # ------------------------------------------------------------------

    def chat_sync(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 512,
        tools: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Synchronous chat via Ollama REST API."""
        import requests

        url = f"{OLLAMA_URL}/api/chat"
        payload = {
            "model": self.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if tools:
            payload["tools"] = tools

        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError:
            return {"error": "Ollama not running. Start with: ollama serve"}
        except Exception as e:
            return {"error": str(e)}

    def generate_sync(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        """Simple text generation via Ollama."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        result = self.chat_sync(messages, temperature=temperature, max_tokens=max_tokens)
        if "error" in result:
            return f"[ERROR] {result['error']}"
        return result.get("message", {}).get("content", "")

    # ------------------------------------------------------------------
    # HF local interface
    # ------------------------------------------------------------------

    def generate_hf(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        """Generate using local HF model."""
        self._load_hf()
        import torch

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt},
        ]
        text = self._hf_tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._hf_tokenizer(text, return_tensors="pt").to(self._hf_model.device)
        with torch.no_grad():
            outputs = self._hf_model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=True,
                temperature=temperature,
                top_p=0.9,
                pad_token_id=self._hf_tokenizer.eos_token_id,
            )
        response = self._hf_tokenizer.decode(
            outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
        )
        return response.strip()

    # ------------------------------------------------------------------
    # Unified interface
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        """Unified generate - uses active backend."""
        if self.backend == "hf":
            return self.generate_hf(prompt, temperature, max_tokens)
        return self.generate_sync(prompt, temperature, max_tokens)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 512,
        tools: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Unified chat - uses active backend."""
        if self.backend == "hf":
            # HF path via simple generation for now
            prompt_msgs = messages[:-1] if messages else []
            user_msg = messages[-1]["content"] if messages else ""
            full_prompt = "\n".join([f"{m['role']}: {m['content']}" for m in prompt_msgs])
            full_prompt += f"\nuser: {user_msg}\nassistant:"
            text = self.generate_hf(full_prompt, temperature, max_tokens)
            return {"message": {"content": text}}
        return self.chat_sync(messages, temperature, max_tokens, tools)


# ----------------------------------------------------------------------
# CLI test
# ----------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="JARVIS LLM interface")
    parser.add_argument("--backend", choices=["ollama", "hf", "auto"], default="auto")
    parser.add_argument("--prompt", type=str, default="Hello, who are you?")
    parser.add_argument("--temp", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()

    jarvis = JarvisLLM(backend=args.backend)
    print(f"\nUser: {args.prompt}")
    response = jarvis.generate(args.prompt, temperature=args.temp, max_tokens=args.max_tokens)
    print(f"JARVIS: {response}\n")


if __name__ == "__main__":
    main()
