#!/bin/bash
# setup-server.sh — Initial server bootstrap for T.A.R.S. nodes
# Usage: sudo ./setup-server.sh
# Run once on each HP Z2 Mini G3 workstation
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[T.A.R.S.] Error: This script must be run as root (sudo)."
    exit 1
fi

echo "[T.A.R.S.] Starting server setup..."

# --- Install Docker via official apt repo ---
echo "[T.A.R.S.] Installing Docker..."
apt-get update
apt-get install -y ca-certificates curl gnupg

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "[T.A.R.S.] Docker installed: $(docker --version)"
echo "[T.A.R.S.] Docker Compose installed: $(docker compose version)"

# --- Install Tailscale ---
echo "[T.A.R.S.] Installing Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh

echo "[T.A.R.S.] Tailscale installed: $(tailscale version)"

# --- Create tars system user ---
echo "[T.A.R.S.] Setting up tars user..."
if ! id -u tars &>/dev/null; then
    useradd --system --create-home --shell /bin/bash tars
    echo "[T.A.R.S.] Created system user: tars"
else
    echo "[T.A.R.S.] User tars already exists."
fi

# Add tars user to docker group (no sudo required for docker)
usermod -aG docker tars
echo "[T.A.R.S.] Added tars to docker group."

# --- Create directory structure ---
echo "[T.A.R.S.] Creating directory structure..."
mkdir -p /opt/tars/deploy/node1
mkdir -p /opt/tars/deploy/node2
mkdir -p /opt/tars/deploy/scripts
mkdir -p /data/backups
mkdir -p /data/repos
mkdir -p /data/outputs
mkdir -p /data/wardrobe
mkdir -p /data/logs

chown -R tars:tars /opt/tars
chown -R tars:tars /data

echo "[T.A.R.S.] Directory structure created."

# --- Enable and start Docker ---
systemctl enable docker
systemctl start docker

echo ""
echo "============================================"
echo "  T.A.R.S. Server Setup Complete"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Run 'sudo tailscale up' to join the Tailnet"
echo "  2. Copy docker-compose.yml to /opt/tars/deploy/node{1,2}/"
echo "  3. Copy .env.production to /opt/tars/deploy/node{1,2}/.env"
echo "  4. Run: cd /opt/tars/deploy/node{1,2} && docker compose up -d"
echo "  5. Verify: docker compose ps"
echo ""
