#!/bin/bash
set -e

cat > /etc/systemd/system/jarvis.service << 'EOF'
[Unit]
Description=Jarvis Personal AI API
After=network.target ollama.service

[Service]
Type=simple
User=kali
WorkingDirectory=/home/kali/.jarvis
ExecStart=/usr/bin/python3 /home/kali/.jarvis/api.py
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/jarvis-telegram.service << 'EOF'
[Unit]
Description=Jarvis Telegram Bot
After=network.target jarvis.service
Wants=jarvis.service

[Service]
Type=simple
User=kali
WorkingDirectory=/home/kali/.jarvis
ExecStart=/usr/bin/python3 /home/kali/.jarvis/telegram_bot.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/jarvis-monitor.service << 'EOF'
[Unit]
Description=Jarvis Monitor (VPS + Kali health)
After=network.target jarvis.service

[Service]
Type=simple
User=kali
WorkingDirectory=/home/kali/.jarvis
ExecStart=/usr/bin/python3 /home/kali/.jarvis/monitor.py
Restart=always
RestartSec=300
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable jarvis jarvis-telegram jarvis-monitor
systemctl reset-failed jarvis jarvis-telegram jarvis-monitor 2>/dev/null || true
systemctl restart jarvis jarvis-telegram jarvis-monitor
sleep 5
systemctl status jarvis jarvis-telegram jarvis-monitor --no-pager | grep -E "Active|Main PID|service"
