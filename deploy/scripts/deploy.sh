#!/bin/bash
# deploy.sh — Deploy T.A.R.S. services via Docker Compose
# Usage: ./deploy.sh [node-directory]
# Example: ./deploy.sh /opt/tars/deploy/node1
set -euo pipefail

NODE_DIR="${1:-.}"

echo "[T.A.R.S.] Deploying from $NODE_DIR..."
cd "$NODE_DIR"

docker compose pull
docker compose up -d --remove-orphans

echo "[T.A.R.S.] Waiting for health checks..."
sleep 10

docker compose ps

echo "[T.A.R.S.] Deploy complete."
