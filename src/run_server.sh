#!/usr/bin/env bash
# Starts both servers for public hosting:
#   - static files (the website) on port 80
#   - the PPO trainer WebSocket on port 8765, listening on all interfaces
#
# Run from the project folder:   bash run_server.sh
# Stop everything with Ctrl-C.
set -euo pipefail

PY="./.venv/bin/python"
STATIC_PORT="${STATIC_PORT:-80}"
TRAINER_PORT="${TRAINER_PORT:-8765}"

if [ ! -x "$PY" ]; then
  echo "No virtualenv found. Run 'bash deploy_setup.sh' first."
  exit 1
fi

cleanup() {
  echo
  echo "Stopping servers..."
  kill "${STATIC_PID:-}" "${TRAINER_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting static website on port ${STATIC_PORT}..."
# Port 80 needs root; use sudo only if not already root.
# serve_static.py only serves web assets (never .venv, server/, .git, dotfiles).
if [ "$(id -u)" -ne 0 ] && [ "$STATIC_PORT" -lt 1024 ]; then
  sudo "$PY" serve_static.py "$STATIC_PORT" &
else
  "$PY" serve_static.py "$STATIC_PORT" &
fi
STATIC_PID=$!

echo "Starting trainer WebSocket on port ${TRAINER_PORT} (all interfaces)..."
"$PY" server/train_server.py --host 0.0.0.0 --port "$TRAINER_PORT" &
TRAINER_PID=$!

echo
echo "Both running."
echo "  Website:  http://<your-server-ip>/sandbox.html"
echo "  Trainer:  ws://<your-server-ip>:${TRAINER_PORT}"
echo
echo "Press Ctrl-C to stop."
wait
