# Jarvis 2.0 — Manual Service Installation Commands

**Date:** 2026-06-06
**Purpose:** Paste these commands into a terminal with sudo access to complete the Jarvis 2.0 service setup.

> All commands below require your sudo password. Run them in order.

---

## Pre-flight Checks (already verified by Claude)

| Check | Status |
|---|---|
| `/home/kali/.jarvis/venv/bin/python3` | EXISTS |
| `/home/kali/.jarvis/cursor/watcher.py` | EXISTS |
| `/usr/bin/node` | EXISTS |
| `/home/kali/.npm-global/lib/node_modules/openclaw/openclaw.mjs` | EXISTS |
| `secrets.env` loaded by both new service files | CONFIRMED |
| `jarvis-sudoers` has openclaw + daemon-reload entries | CONFIRMED |

---

## STEP 1 — Copy service files to systemd

```bash
sudo cp /home/kali/.jarvis/systemd/openclaw.service /etc/systemd/system/openclaw.service
sudo cp /home/kali/.jarvis/systemd/jarvis-cursor.service /etc/systemd/system/jarvis-cursor.service
```

> NOTE: Use the files from `/home/kali/.jarvis/systemd/` — NOT `/tmp/openclaw.service`.
> The /tmp copy has the Telegram token hardcoded in plaintext; the systemd/ version loads it cleanly from secrets.env.

---

## STEP 2 — Reload systemd daemon

```bash
sudo systemctl daemon-reload
```

---

## STEP 3 — Enable and start openclaw

```bash
sudo systemctl enable openclaw
sudo systemctl start openclaw
sudo systemctl status openclaw
```

---

## STEP 4 — Enable and start jarvis-cursor

```bash
sudo systemctl enable jarvis-cursor
sudo systemctl start jarvis-cursor
sudo systemctl status jarvis-cursor
```

---

## STEP 5 — Update /etc/sudoers.d/jarvis

This adds NOPASSWD rules for: `openclaw`, `jarvis-cursor`, `daemon-reload`, and `cp *.service`.

```bash
sudo cp /home/kali/.jarvis/config/jarvis-sudoers /tmp/jarvis-sudoers-new
sudo visudo -c -f /tmp/jarvis-sudoers-new && sudo cp /tmp/jarvis-sudoers-new /etc/sudoers.d/jarvis && sudo chmod 440 /etc/sudoers.d/jarvis
```

> The `visudo -c` check runs first — if the file has a syntax error the copy is aborted. Safe.

---

## STEP 6 — Update jarvis.service to load secrets.env

```bash
sudo bash -c 'cat > /etc/systemd/system/jarvis.service << '"'"'EOF'"'"'
[Unit]
Description=Jarvis Personal AI API
After=network.target ollama.service

[Service]
Type=simple
User=kali
WorkingDirectory=/home/kali/.jarvis
EnvironmentFile=-/home/kali/.jarvis/config/secrets.env
ExecStart=/home/kali/.jarvis/venv/bin/python3 /home/kali/.jarvis/api.py
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF'
```

---

## STEP 7 — Update jarvis-telegram.service to load secrets.env

```bash
sudo bash -c 'cat > /etc/systemd/system/jarvis-telegram.service << '"'"'EOF'"'"'
[Unit]
Description=Jarvis Telegram Bot
After=network.target jarvis.service
Wants=jarvis.service

[Service]
Type=simple
User=kali
WorkingDirectory=/home/kali/.jarvis
EnvironmentFile=-/home/kali/.jarvis/config/secrets.env
ExecStart=/home/kali/.jarvis/venv/bin/python3 /home/kali/.jarvis/telegram_bot.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF'
```

---

## STEP 8 — Reload daemon again and restart core services

```bash
sudo systemctl daemon-reload
sudo systemctl restart jarvis
sudo systemctl restart jarvis-telegram
sudo systemctl status jarvis jarvis-telegram openclaw jarvis-cursor
```

---

## STEP 9 — Set your Moonshot API key (Kimi K2.6)

Get your key from: https://platform.kimi.ai

```bash
# Edit the secrets file and replace REPLACE_ME with your actual key:
nano /home/kali/.jarvis/config/secrets.env
# Change: MOONSHOT_API_KEY=REPLACE_ME
# To:     MOONSHOT_API_KEY=sk-...your-key-here...

# Then restart all services to pick it up:
sudo systemctl restart jarvis jarvis-telegram openclaw
```

---

## Final Verification

```bash
sudo systemctl is-active jarvis jarvis-telegram openclaw jarvis-cursor
curl -s http://localhost:8181/health
```

Both should show active / `{"status":"ok"}`.

---

## Summary of What Was Changed

| File | Change |
|---|---|
| `/etc/systemd/system/openclaw.service` | NEW — OpenClaw AI Gateway, loads secrets.env |
| `/etc/systemd/system/jarvis-cursor.service` | NEW — Cursor IDE bridge via watcher.py |
| `/etc/systemd/system/jarvis.service` | UPDATED — added `EnvironmentFile` for secrets.env |
| `/etc/systemd/system/jarvis-telegram.service` | UPDATED — added `EnvironmentFile` for secrets.env |
| `/etc/sudoers.d/jarvis` | UPDATED — openclaw, jarvis-cursor, daemon-reload, cp *.service |
