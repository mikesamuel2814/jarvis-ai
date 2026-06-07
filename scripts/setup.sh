#!/bin/bash
set -e

JARVIS_DIR="/home/kali/.jarvis"
VENV_DIR="$JARVIS_DIR/venv-jarvis"

echo "╔══════════════════════════════════════════╗"
echo "║      JARVIS AI Setup Script              ║"
echo "║      Kali Linux + v3 Compatible          ║"
echo "╚══════════════════════════════════════════╝"

# Detect environment
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
else
    OS=$(uname -s)
fi

if command -v nvidia-smi &> /dev/null; then
    GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)
    echo "OS: $OS | GPU: $GPU"
else
    echo "OS: $OS | GPU: None detected"
fi

# 1. Python check
PYTHON_CMD=""
for cmd in python3.11 python3.13 python3; do
    if command -v $cmd &> /dev/null; then
        PYTHON_CMD=$cmd
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "ERROR: No suitable Python found. Install Python 3.11+ first."
    exit 1
fi

echo "Using Python: $($PYTHON_CMD --version)"

# 2. Virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR..."
    $PYTHON_CMD -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# 3. Dependencies
echo "Installing dependencies..."
if [[ "$GPU" == *"NVIDIA"* ]]; then
    pip install --upgrade pip setuptools wheel
    # Install CPU-safe packages first
    pip install transformers datasets accelerate peft bitsandbytes trl \
        pydantic aiosqlite aiohttp fastapi uvicorn python-multipart \
        psutil cryptography python-jose jinja2 pyyaml jsonlines \
        huggingface-hub safetensors sentencepiece protobuf
    # Try CUDA torch (may need index URL adjustment for Kali's CUDA version)
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 || \
        pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 || \
        pip install torch torchvision torchaudio
else
    pip install -r "$JARVIS_DIR/requirements-cloud.txt"
fi

# 4. Model download (Ollama)
echo "Pulling Ollama models..."
for model in qwen2.5:7b mistral:7b qwen2.5-coder:7b deepseek-r1:7b; do
    if ! ollama list | grep -q "^$model\\b"; then
        echo "Pulling $model..."
        ollama pull $model
    else
        echo "$model already present"
    fi
done

# Optional: HuggingFace base model for training
# echo "Downloading HF base model for training..."
# if [ ! -d "$JARVIS_DIR/models/base/deepseek-r1-qwen-7b" ]; then
#     huggingface-cli download deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
#         --local-dir "$JARVIS_DIR/models/base/deepseek-r1-qwen-7b" \
#         --local-dir-use-symlinks False
# fi

# 5. Data directories
mkdir -p "$JARVIS_DIR/data"/{raw,synthetic,processed,configs}
mkdir -p "$JARVIS_DIR/models"/{checkpoints,final}
mkdir -p "$JARVIS_DIR/logs"

# 6. Git hooks (optional)
if [ -d "$JARVIS_DIR/.git" ]; then
    cat > "$JARVIS_DIR/.git/hooks/pre-commit" << 'HOOK'
#!/bin/bash
# Prevent committing large models
if git diff --cached --name-only | grep -E '\.(bin|gguf|safetensors)$'; then
    echo "Blocked: Do not commit model files. Use Git LFS or HuggingFace."
    exit 1
fi
HOOK
    chmod +x "$JARVIS_DIR/.git/hooks/pre-commit"
fi

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Generate data: ./scripts/kimi_generate.sh"
echo "  2. Train model:   python scripts/train.py"
echo "  3. Serve JARVIS:  python scripts/serve.py --interactive"
echo "  4. Health check:  ./scripts/health_check.sh"
