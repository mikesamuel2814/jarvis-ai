#!/bin/bash
# Install Docker on Kali — works around cnf-update-db / NVIDIA apt issues.
set -euo pipefail

APT_SAFE=(
  -o APT::Update::Post-Invoke-Success=
  -o APT::Update::Post-Invoke=
)

if command -v docker >/dev/null 2>&1; then
  docker --version
  docker compose version 2>/dev/null || true
  exit 0
fi

echo "=== Jarvis Docker install ==="

# Optional: skip broken NVIDIA CUDA apt repo (GPG key missing on some Kali setups)
CUDA_LIST=$(ls /etc/apt/sources.list.d/*cuda* /etc/apt/sources.list.d/*nvidia* 2>/dev/null | head -1 || true)
if [[ -n "${CUDA_LIST}" ]] && [[ ! -f "${CUDA_LIST}.jarvis-disabled" ]]; then
  echo "Note: NVIDIA CUDA apt repo can break 'apt update'. To disable temporarily:"
  echo "  sudo mv ${CUDA_LIST} ${CUDA_LIST}.jarvis-disabled"
fi

echo "Updating package lists (cnf hook disabled)..."
if ! sudo apt-get "${APT_SAFE[@]}" update; then
  echo "apt update had errors — trying install without refresh..."
fi

echo "Installing docker.io..."
if ! sudo apt-get "${APT_SAFE[@]}" install -y docker.io; then
  echo "docker.io install failed — check apt sources."
  exit 1
fi

# docker-compose-plugin is not in Kali repos; install standalone docker-compose instead
if ! command -v docker-compose >/dev/null 2>&1 && ! docker compose version >/dev/null 2>&1; then
  echo "Installing docker-compose (standalone)..."
  sudo apt-get "${APT_SAFE[@]}" install -y docker-compose 2>/dev/null || \
    sudo curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
      -o /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose
fi

sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" 2>/dev/null || true

echo ""
docker --version
docker compose version 2>/dev/null || docker-compose --version 2>/dev/null || true
echo ""
echo "Done. Log out and back in (or: newgrp docker), then:"
echo "  bash ~/.jarvis/docker/up.sh"
