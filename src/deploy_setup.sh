#!/usr/bin/env bash
# One-time setup for hosting Entangled on an Ubuntu 22.04/24.04 server.
# Run this ONCE after copying the project folder to the server:
#     bash deploy_setup.sh
set -euo pipefail

echo "=== Entangled server setup ==="

# 1. System packages: Python, venv, and Node.js (the trainer spawns node).
echo "[1/3] Installing system packages (sudo password may be required)..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip nodejs npm

# 2. Python virtualenv + dependencies (torch is large; this can take minutes).
echo "[2/3] Creating Python virtualenv and installing requirements..."
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r server/requirements.txt

# 3. Done.
echo "[3/3] Setup complete."
echo
echo "Start the servers with:   bash run_server.sh"
