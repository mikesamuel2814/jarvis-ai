#!/bin/bash
set -e
echo "[1/5] Copying service files..."
sudo cp /home/kali/.jarvis/systemd/openclaw.service /etc/systemd/system/
sudo cp /home/kali/.jarvis/systemd/jarvis-cursor.service /etc/systemd/system/
sudo cp /home/kali/.jarvis/systemd/jarvis.service /etc/systemd/system/

echo "[2/5] Reloading systemd..."
sudo systemctl daemon-reload

echo "[3/5] Enabling + starting openclaw and jarvis-cursor..."
sudo systemctl enable --now openclaw
sudo systemctl enable --now jarvis-cursor

echo "[4/5] Restarting jarvis (api_v2.py)..."
sudo -n systemctl restart jarvis
sleep 4

echo "[5/5] Health check..."
curl -s http://localhost:8181/health
echo ""
echo "DONE"
