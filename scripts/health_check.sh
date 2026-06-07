#!/bin/bash

JARVIS_DIR="/home/kali/.jarvis"
VENV_DIR="$JARVIS_DIR/venv-jarvis"
LOG_FILE="$JARVIS_DIR/logs/health_check_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$JARVIS_DIR/logs"

echo "JARVIS v3 Health Check — $(date -Iseconds)" | tee "$LOG_FILE"
echo "============================================================" | tee -a "$LOG_FILE"

# 1. Python environment
if [ -f "$VENV_DIR/bin/python" ]; then
    PYTHON_VER=$($VENV_DIR/bin/python --version 2>&1)
    echo "[PASS] venv-jarvis: $PYTHON_VER" | tee -a "$LOG_FILE"
else
    echo "[FAIL] venv-jarvis missing" | tee -a "$LOG_FILE"
fi

# 2. CUDA
if command -v nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)
    GPU_MEM=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -n1)
    echo "[PASS] GPU: $GPU_NAME | Free VRAM: ${GPU_MEM}MB" | tee -a "$LOG_FILE"
else
    echo "[WARN] nvidia-smi not found" | tee -a "$LOG_FILE"
fi

# 3. Ollama models
echo "[INFO] Ollama models:" | tee -a "$LOG_FILE"
if command -v ollama &> /dev/null; then
    ollama list 2>/dev/null | while read line; do
        echo "       $line" | tee -a "$LOG_FILE"
    done
else
    echo "[WARN] Ollama not found" | tee -a "$LOG_FILE"
fi

# 4. HF base model (optional, for training)
if [ -d "$JARVIS_DIR/models/base/deepseek-r1-qwen-7b" ]; then
    echo "[PASS] HF base model (deepseek-r1-qwen-7b) OK" | tee -a "$LOG_FILE"
else
    echo "[INFO] HF base model not downloaded (optional — Ollama models available for inference)" | tee -a "$LOG_FILE"
fi

# 5. Final model
if [ -f "$JARVIS_DIR/models/final/jarvis-q4_k_m.gguf" ]; then
    echo "[PASS] Final GGUF model OK" | tee -a "$LOG_FILE"
else
    echo "[INFO] Final model not built yet" | tee -a "$LOG_FILE"
fi

# 6. Core v3 files
check_file() {
    if [ -f "$1" ]; then
        echo "[PASS] $(basename $1)" | tee -a "$LOG_FILE"
    else
        echo "[FAIL] $(basename $1) MISSING" | tee -a "$LOG_FILE"
    fi
}

check_file "$JARVIS_DIR/api_v3.py"
check_file "$JARVIS_DIR/orchestrator.py"
check_file "$JARVIS_DIR/brain_router.py"
check_file "$JARVIS_DIR/autonomy_v3.py"

# 7. Security modules
for f in scope_enforcer.py sudo_auditor.py ids_hook.py permission_engine.py key_rotator.py port_monitor.py remote_access_audit.py; do
    check_file "$JARVIS_DIR/security/$f"
done

# 8. Tool ecosystem
check_file "$JARVIS_DIR/tools/decorator.py"
check_file "$JARVIS_DIR/tools/registry.py"
check_file "$JARVIS_DIR/tools/result.py"

# 9. Nano swarm
for f in task_queue.py blackboard.py gossip.py worker_pool.py; do
    check_file "$JARVIS_DIR/nano_swarm/$f"
done

# 10. Telegram
check_file "$JARVIS_DIR/telegram_bot.py"
check_file "$JARVIS_DIR/telegram_ui/callbacks.py"

# 11. Config files
check_file "$JARVIS_DIR/config/jarvis_v3.yaml"
check_file "$JARVIS_DIR/config/mike_profile.yaml"
check_file "$JARVIS_DIR/config/secrets.env"
check_file "$JARVIS_DIR/config/safety_rules.json"

# 12. Disk space
DF=$(df -h "$JARVIS_DIR" | tail -1 | awk '{print $4}')
echo "[INFO] Free space in $JARVIS_DIR: $DF" | tee -a "$LOG_FILE"

# 13. Memory
FREE=$(free -h | grep Mem | awk '{print $7}')
echo "[INFO] Free RAM: $FREE" | tee -a "$LOG_FILE"

# 14. VRAM (again for summary)
if command -v nvidia-smi &> /dev/null; then
    VRAM=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -n1)
    echo "[INFO] Free VRAM: ${VRAM}MB" | tee -a "$LOG_FILE"
fi

# 15. Services
for svc in jarvis jarvis-telegram jarvis-v3; do
    if systemctl is-active --quiet $svc 2>/dev/null; then
        echo "[PASS] Service $svc is running" | tee -a "$LOG_FILE"
    else
        echo "[INFO] Service $svc not running (may be expected)" | tee -a "$LOG_FILE"
    fi
done

# 16. JARVIS API health
curl -s http://127.0.0.1:8181/health > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "[PASS] JARVIS API (port 8181) responding" | tee -a "$LOG_FILE"
else
    echo "[INFO] JARVIS API (port 8181) not responding (service may be stopped)" | tee -a "$LOG_FILE"
fi

echo "============================================================" | tee -a "$LOG_FILE"
echo "Health check complete. Log: $LOG_FILE" | tee -a "$LOG_FILE"
