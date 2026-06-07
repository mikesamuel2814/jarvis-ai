#!/usr/bin/env python3
"""
JARVIS Model Serving Script
Supports: vLLM, llama-cpp-python, or fallback to Ollama REST
"""
import os
import sys
import argparse
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

JARVIS_DIR = Path("/home/kali/.jarvis")
MODELS_DIR = JARVIS_DIR / "models"

app = FastAPI(title="JARVIS Model Server", version="1.0.0")

# Global model reference
model_engine = None


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = "default"
    max_tokens: int = 512
    temperature: float = 0.7


class ChatResponse(BaseModel):
    response: str
    model: str
    session_id: str


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": model_engine is not None}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if model_engine is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    # Stub: implement with actual inference
    return ChatResponse(
        response=f"[stub] Echo: {req.message}",
        model="jarvis-dpo",
        session_id=req.session_id
    )


def load_vllm(model_path: Path):
    """Load model with vLLM."""
    try:
        from vllm import LLM, SamplingParams
        print(f"[vLLM] Loading {model_path}...")
        llm = LLM(model=str(model_path), gpu_memory_utilization=0.85)
        return llm
    except ImportError:
        print("[vLLM] not installed")
        return None


def load_llama_cpp(model_path: Path):
    """Load GGUF model with llama-cpp-python."""
    try:
        from llama_cpp import Llama
        print(f"[llama.cpp] Loading {model_path}...")
        llm = Llama(
            model_path=str(model_path),
            n_ctx=4096,
            n_gpu_layers=-1,
            verbose=False
        )
        return llm
    except ImportError:
        print("[llama.cpp] not installed")
        return None


def main():
    parser = argparse.ArgumentParser(description="JARVIS Model Server")
    parser.add_argument("--model", default=str(MODELS_DIR / "final" / "jarvis-dpo"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--interactive", action="store_true", help="Interactive terminal mode")
    parser.add_argument("--backend", choices=["vllm", "llama", "auto"], default="auto")
    args = parser.parse_args()

    model_path = Path(args.model)

    if args.interactive:
        print("╔══════════════════════════════════════════╗")
        print("║      JARVIS Interactive Terminal         ║")
        print("╚══════════════════════════════════════════╝")
        print(f"Model: {model_path}")
        print("Type 'exit' to quit.\n")

        while True:
            try:
                user_input = input("You: ")
                if user_input.lower() in ("exit", "quit"):
                    break
                print(f"JARVIS: [stub] Echo: {user_input}\n")
            except (EOFError, KeyboardInterrupt):
                break
        return

    # API mode
    global model_engine
    if args.backend == "auto":
        if model_path.suffix == ".gguf":
            model_engine = load_llama_cpp(model_path)
        else:
            model_engine = load_vllm(model_path)
    elif args.backend == "vllm":
        model_engine = load_vllm(model_path)
    elif args.backend == "llama":
        model_engine = load_llama_cpp(model_path)

    if model_engine is None:
        print("WARNING: No backend loaded. Running in stub mode.")

    print(f"Starting server on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
