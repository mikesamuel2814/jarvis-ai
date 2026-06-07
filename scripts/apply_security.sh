#!/bin/bash
# JARVIS Security Hardening Script
# Run with sudo: sudo ./scripts/apply_security.sh

set -e

JARVIS_DIR="/home/kali/.jarvis"

echo "============================================================"
echo "JARVIS Security Hardening"
echo "============================================================"

# 1. ufw firewall
echo "[1/5] Configuring ufw..."
if command -v ufw &> /dev/null; then
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow from 127.0.0.1 to any port 8000 comment "JARVIS API"
    ufw allow from 127.0.0.1 to any port 11434 comment "Ollama"
    ufw allow from 127.0.0.1 to any port 8181 comment "JARVIS v3 API"
    ufw allow from 127.0.0.1 to any port 22 comment "SSH"
    ufw --force enable
    echo "[PASS] ufw configured"
else
    echo "[SKIP] ufw not installed. Install with: sudo apt install ufw"
fi

# 2. nvidia-persistenced
echo "[2/5] Enabling nvidia-persistenced..."
if systemctl list-unit-files | grep -q nvidia-persistenced; then
    systemctl enable nvidia-persistenced
    systemctl start nvidia-persistenced
    echo "[PASS] nvidia-persistenced enabled"
else
    echo "[SKIP] nvidia-persistenced service not found"
fi

# 3. Disable unnecessary services
echo "[3/5] Disabling unnecessary services..."
for svc in bluetooth avahi-daemon; do
    if systemctl list-unit-files | grep -q "^$svc"; then
        systemctl disable $svc 2>/dev/null || true
        systemctl stop $svc 2>/dev/null || true
        echo "[PASS] Disabled $svc"
    fi
done

# 4. Log directory
echo "[4/5] Setting up log directory..."
mkdir -p /var/log/jarvis
chown kali:kali /var/log/jarvis 2>/dev/null || true
echo "[PASS] /var/log/jarvis ready"

# 5. AppArmor (optional)
echo "[5/5] AppArmor check..."
if command -v aa-enforce &> /dev/null; then
    if [ -f "$JARVIS_DIR/config/apparmor.jarvis" ]; then
        cp "$JARVIS_DIR/config/apparmor.jarvis" /etc/apparmor.d/usr.local.bin.jarvis
        aa-enforce /etc/apparmor.d/usr.local.bin.jarvis 2>/dev/null || true
        echo "[PASS] AppArmor profile loaded"
    else
        echo "[INFO] No AppArmor profile found at $JARVIS_DIR/config/apparmor.jarvis"
    fi
else
    echo "[SKIP] AppArmor not installed"
fi

echo "============================================================"
echo "Security hardening complete."
echo "Note: systemd service install requires:"
echo "  sudo cp $JARVIS_DIR/systemd/jarvis-serve.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable jarvis-serve"
echo "============================================================"
