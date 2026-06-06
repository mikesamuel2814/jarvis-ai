#!/bin/bash
# Jarvis 2.0 — Complete Setup Script
# Run ONCE on Kali Linux after pulling the repo.
# Idempotent — safe to re-run.
#
# Prerequisites: Ollama running, Python 3.11+, Node 24
# Usage: bash ~/.jarvis/setup_v2.sh

set -euo pipefail

JARVIS_HOME="${JARVIS_HOME:-$HOME/.jarvis}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$JARVIS_HOME/venv"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓ $*${NC}"; }
warn() { echo -e "${YELLOW}⚠ $*${NC}"; }
err()  { echo -e "${RED}✗ $*${NC}"; exit 1; }
step() { echo -e "\n${YELLOW}━━━ $* ━━━${NC}"; }

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   JARVIS 2.0 — HYBRID CORTEX SETUP  ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Step 1: Verify prerequisites ─────────────────────────────────
step "1/10 Checking prerequisites"

command -v python3 >/dev/null || err "Python 3 not found"
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  Python: $PYVER"

command -v node >/dev/null || warn "Node.js not found — install with: nvm install 24"
NODE_VER=$(node --version 2>/dev/null || echo "not installed")
echo "  Node: $NODE_VER"

command -v npm >/dev/null || warn "npm not found"

if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    ok "Ollama is running"
else
    warn "Ollama not running. Start with: systemctl start ollama"
fi

# ── Step 2: Create directory structure ────────────────────────────
step "2/10 Creating directory structure"

mkdir -p "$JARVIS_HOME"/{config,data,logs,memory,openclaw_workspace}
mkdir -p "$HOME/.openclaw/workspace/skills/jarvis-system"
mkdir -p "$HOME/.openclaw/workspace/memory"

# Copy repo files to JARVIS_HOME (if running from repo)
if [ "$REPO_DIR" != "$JARVIS_HOME" ]; then
    echo "  Syncing repo to $JARVIS_HOME ..."
    rsync -av --exclude='.git' --exclude='__pycache__' \
        "$REPO_DIR/" "$JARVIS_HOME/" --delete
    ok "Repo synced to $JARVIS_HOME"
else
    ok "Running from JARVIS_HOME directly"
fi

# ── Step 3: Python virtualenv + dependencies ─────────────────────
step "3/10 Setting up Python virtualenv"

if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
    ok "Created virtualenv at $VENV"
else
    ok "Virtualenv already exists"
fi

"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$JARVIS_HOME/requirements_v2.txt"
ok "Python dependencies installed"

# ── Step 4: Configure credentials ────────────────────────────────
step "4/10 Configuring credentials"

SECRETS_FILE="$JARVIS_HOME/config/secrets.env"
if [ ! -f "$SECRETS_FILE" ]; then
    cat > "$SECRETS_FILE" << 'SECRETS'
# Jarvis 2.0 Secrets — NEVER commit this file
# Fill in your values below

# Kimi K2.6 — get from: https://platform.kimi.ai
MOONSHOT_API_KEY=REPLACE_ME

# Telegram Bot — get from: @BotFather
TELEGRAM_BOT_TOKEN=REPLACE_ME

# Your Telegram user ID — get from: @userinfobot
TELEGRAM_USER_ID=REPLACE_ME

# Jarvis API key (generate a random string)
# Run: openssl rand -hex 32
JARVIS_API_KEY=REPLACE_ME

# Shared secret for OpenClaw webhook HMAC
# Run: openssl rand -hex 32
JARVIS_WEBHOOK_SECRET=REPLACE_ME

# VPS host (optional)
JARVIS_VPS_HOST=38.47.35.16
SECRETS
    chmod 600 "$SECRETS_FILE"
    warn "Created $SECRETS_FILE — FILL IN YOUR KEYS before continuing!"
    warn "Run: nano $SECRETS_FILE"
    echo ""
    echo "Required keys:"
    echo "  MOONSHOT_API_KEY   → https://platform.kimi.ai"
    echo "  TELEGRAM_BOT_TOKEN → @BotFather on Telegram"
    echo "  TELEGRAM_USER_ID   → @userinfobot on Telegram"
    echo "  JARVIS_API_KEY     → openssl rand -hex 32"
    echo "  JARVIS_WEBHOOK_SECRET → openssl rand -hex 32"
    echo ""
    read -rp "Press Enter after filling in secrets.env to continue..."
else
    ok "Secrets file already exists"
fi

# Source secrets to validate
set -a
source "$SECRETS_FILE"
set +a

[ "${MOONSHOT_API_KEY:-REPLACE_ME}" = "REPLACE_ME" ] && warn "MOONSHOT_API_KEY not set in secrets.env"
[ "${TELEGRAM_BOT_TOKEN:-REPLACE_ME}" = "REPLACE_ME" ] && warn "TELEGRAM_BOT_TOKEN not set in secrets.env"
[ "${JARVIS_API_KEY:-REPLACE_ME}" = "REPLACE_ME" ] && warn "JARVIS_API_KEY not set in secrets.env"

# ── Step 5: Install OpenClaw ──────────────────────────────────────
step "5/10 Installing OpenClaw"

if command -v openclaw >/dev/null 2>&1; then
    ok "OpenClaw already installed ($(openclaw --version 2>/dev/null || echo 'version unknown'))"
else
    echo "  Installing OpenClaw via npm..."
    npm install -g openclaw@latest
    ok "OpenClaw installed"
fi

# ── Step 6: Configure OpenClaw ────────────────────────────────────
step "6/10 Configuring OpenClaw"

OC_CONFIG="$HOME/.openclaw/openclaw.json"
if [ ! -f "$OC_CONFIG" ]; then
    # Generate from template, substituting secrets
    TGID="${TELEGRAM_USER_ID:-REPLACE_WITH_YOUR_TELEGRAM_USER_ID}"
    sed \
        -e "s|REPLACE_ME.*MOONSHOT.*|${MOONSHOT_API_KEY}|g" \
        -e "s|REPLACE_ME.*JARVIS.*API.*KEY.*|${JARVIS_API_KEY}|g" \
        -e "s|REPLACE_WITH_YOUR_TELEGRAM_USER_ID|${TGID}|g" \
        "$JARVIS_HOME/openclaw_config/openclaw.json.template" \
        > "$OC_CONFIG"
    chmod 600 "$OC_CONFIG"
    ok "Created $OC_CONFIG"
    warn "Review and adjust $OC_CONFIG — especially dmPolicy and allowFrom"
else
    ok "OpenClaw config already exists at $OC_CONFIG"
fi

# Copy AGENTS.md, SOUL.md to OpenClaw workspace
cp "$JARVIS_HOME/openclaw_config/AGENTS.md" "$HOME/.openclaw/workspace/AGENTS.md"
cp "$JARVIS_HOME/openclaw_config/SOUL.md"   "$HOME/.openclaw/workspace/SOUL.md"
ok "Copied AGENTS.md and SOUL.md to OpenClaw workspace"

# Install jarvis-system skill
mkdir -p "$HOME/.openclaw/workspace/skills/jarvis-system"
cp "$JARVIS_HOME/skills/jarvis-system/SKILL.md" \
   "$HOME/.openclaw/workspace/skills/jarvis-system/SKILL.md"
ok "Installed jarvis-system skill"

# Generate initial skill from OpenClaw skill_generator
"$VENV/bin/python3" -c "
import sys
sys.path.insert(0, '$JARVIS_HOME')
from openclaw.skill_generator import write_jarvis_system_skill
write_jarvis_system_skill()
print('SKILL.md written to OpenClaw workspace')
" && ok "jarvis-system SKILL.md generated"

# ── Step 7: Install systemd services ─────────────────────────────
step "7/10 Installing systemd services"

SYSTEMD_DIR="/etc/systemd/system"

# Copy openclaw and cursor services
for svc in openclaw jarvis-cursor; do
    if [ -f "$JARVIS_HOME/systemd/${svc}.service" ]; then
        sudo cp "$JARVIS_HOME/systemd/${svc}.service" "$SYSTEMD_DIR/"
        ok "Installed ${svc}.service"
    fi
done

sudo systemctl daemon-reload

# Enable new services
sudo systemctl enable openclaw jarvis-cursor 2>/dev/null || warn "Could not enable services (check sudo)"
ok "Services enabled"

# ── Step 8: Update crontab ────────────────────────────────────────
step "8/10 Updating crontab"

CRON_MARKER="# JARVIS 2.0"
if ! crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    (crontab -l 2>/dev/null; cat << CRON

$CRON_MARKER — Complete Automation Schedule
PYTHONUNBUFFERED=1
JARVIS_HOME=$JARVIS_HOME

# Every 5 minutes — Local system monitoring
*/5  * * * *   $VENV/bin/python3 $JARVIS_HOME/monitor.py >> $JARVIS_HOME/logs/monitor.log 2>&1

# Every 10 minutes — Self-healing
*/10 * * * *   $VENV/bin/python3 $JARVIS_HOME/healer.py >> $JARVIS_HOME/logs/healer.log 2>&1

# Every 6 hours — Training pipeline
0    */6 * * * $JARVIS_HOME/train_v2.sh >> $JARVIS_HOME/logs/training.log 2>&1

# 9 AM daily — Briefing
0    9   * * * $VENV/bin/python3 $JARVIS_HOME/analyze.py --daily >> $JARVIS_HOME/logs/analyze.log 2>&1

# 9 AM Sunday — Weekly summary
0    9   * * 0 $VENV/bin/python3 $JARVIS_HOME/analyze.py --weekly >> $JARVIS_HOME/logs/analyze.log 2>&1

# Sunday 2 AM — Full evolution
0    2   * * 0 $VENV/bin/python3 $JARVIS_HOME/selftrain_v2.py --full >> $JARVIS_HOME/logs/selftrain.log 2>&1

# Monthly — Cost audit
0    0   1 * * $VENV/bin/python3 $JARVIS_HOME/kimi/cost_tracker.py --audit >> $JARVIS_HOME/logs/kimi_api.log 2>&1
CRON
    ) | crontab -
    ok "Crontab updated"
else
    ok "Jarvis 2.0 cron entries already present"
fi

# ── Step 9: Test Kimi connection ──────────────────────────────────
step "9/10 Testing Kimi K2.6 connection"

if [ "${MOONSHOT_API_KEY:-REPLACE_ME}" != "REPLACE_ME" ]; then
    "$VENV/bin/python3" -c "
import sys
sys.path.insert(0, '$JARVIS_HOME')
from kimi.client import KimiClient
client = KimiClient()
content, reasoning = client.query(
    system='You are a test assistant.',
    user='Say: KIMI_OK',
    thinking=False,
)
assert 'KIMI_OK' in content or len(content) > 0
print(f'✓ Kimi K2.6 connected. Response: {content[:80]}')
" && ok "Kimi K2.6 connection verified" || warn "Kimi K2.6 test failed — check MOONSHOT_API_KEY"
else
    warn "Skipping Kimi test — MOONSHOT_API_KEY not set"
fi

# ── Step 10: Start services ───────────────────────────────────────
step "10/10 Starting services"

sudo systemctl restart jarvis        2>/dev/null && ok "jarvis started" || warn "jarvis not started"
sudo systemctl restart jarvis-telegram 2>/dev/null && ok "jarvis-telegram started" || warn "jarvis-telegram not started"
sudo systemctl start openclaw        2>/dev/null && ok "openclaw started" || warn "openclaw not started (start manually: openclaw gateway)"
sudo systemctl start jarvis-cursor   2>/dev/null && ok "jarvis-cursor started" || warn "jarvis-cursor not started"

# ── Summary ───────────────────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║         JARVIS 2.0 SETUP COMPLETE              ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""
echo "  API:         http://127.0.0.1:8181"
echo "  OpenClaw UI: http://127.0.0.1:18789"
echo "  Health:      curl http://127.0.0.1:8181/health"
echo "  Metrics:     curl -H 'X-API-Key: \$JARVIS_API_KEY' http://127.0.0.1:8181/metrics"
echo ""
echo "  Verify: bash $JARVIS_HOME/verify.sh"
echo "  Guide:  $JARVIS_HOME/SETUP_GUIDE.md"
echo ""
