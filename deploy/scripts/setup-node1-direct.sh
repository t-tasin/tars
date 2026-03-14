#!/usr/bin/env bash
set -euo pipefail

echo "=== T.A.R.S. Node 1 Direct Deployment Setup ==="

# 1. Install system dependencies
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    build-essential libpq-dev portaudio19-dev ffmpeg \
    libportaudio2 libpq5 \
    curl git

# 2. Install pyenv + Python 3.12
if ! command -v pyenv &>/dev/null; then
    curl -fsSL https://pyenv.run | bash
    echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
    echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc
    echo 'eval "$(pyenv init -)"' >> ~/.bashrc
    export PYENV_ROOT="$HOME/.pyenv"
    export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)"
fi
pyenv install -s 3.12
pyenv global 3.12

# 3. Install Node.js 22 LTS (for MCP servers)
if ! command -v node &>/dev/null || [[ "$(node -v)" != v22* ]]; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

# 4. Install Claude Code CLI
if ! command -v claude &>/dev/null; then
    sudo npm install -g @anthropic-ai/claude-code
    echo ">>> Run 'claude login' to authenticate with your Max plan <<<"
fi

# 5. Set up backend virtualenv
cd /opt/tars/backend
python -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install --extra-index-url https://download.pytorch.org/whl/cpu -e .

# 6. Create data directories
sudo mkdir -p /data/repos /data/outputs /data/logs
sudo chown -R tasin:tasin /data

# 7. Install systemd service
sudo cp /opt/tars/deploy/node1/tars-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tars-backend

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. Run 'claude login' to authenticate Claude Code CLI"
echo "  2. Update .env: DATABASE_URL=postgresql+asyncpg://tars:PASSWORD@localhost:5432/tars"
echo "  3. Run: docker compose down tars-backend"
echo "  4. Run: docker compose up -d"
echo "  5. Run: cd /opt/tars/backend && .venv/bin/python -m alembic upgrade head"
echo "  6. Run: sudo systemctl start tars-backend"
echo "  7. Check: sudo journalctl -u tars-backend -f"
