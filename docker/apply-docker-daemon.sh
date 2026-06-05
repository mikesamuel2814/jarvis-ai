#!/bin/bash
# Configure Docker daemon for Kali (disables seccomp to fix apt/pip segfaults in containers).
set -euo pipefail

DAEMON_JSON=/etc/docker/daemon.json

if [[ -f "$DAEMON_JSON" ]]; then
  echo "Existing daemon.json:"
  cat "$DAEMON_JSON"
  echo ""
  echo "Merge manually if needed, then: sudo systemctl restart docker"
  exit 0
fi

sudo tee "$DAEMON_JSON" > /dev/null <<'EOF'
{
  "seccomp-profile": "unconfined"
}
EOF

echo "daemon.json written. Restarting Docker..."
sudo systemctl restart docker
echo "Done. Retry: sudo bash ~/.jarvis/docker/up.sh"
