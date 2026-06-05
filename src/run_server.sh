#!/usr/bin/env bash
# Starts the Entangled servers.
#
# Two modes:
#   Default (no nginx): serves static files on port 80 AND the trainer on 8765.
#   With nginx in front: set USE_NGINX=1 so nginx serves files + proxies /wss,
#       and this script runs ONLY the trainer, bound to localhost.
#
# Examples:
#   bash run_server.sh              # standalone (static + trainer)
#   USE_NGINX=1 bash run_server.sh  # trainer only, behind nginx
#
# Run from the project folder. Stop with Ctrl-C.
set -euo pipefail

PY="./.venv/bin/python"
STATIC_PORT="${STATIC_PORT:-80}"
TRAINER_PORT="${TRAINER_PORT:-8765}"
USE_NGINX="${USE_NGINX:-0}"

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

if [ "$USE_NGINX" = "1" ]; then
  # nginx serves the static files and proxies /wss to the trainer, so the
  # trainer only needs to listen on localhost.
  echo "Starting trainer (localhost:${TRAINER_PORT}) behind nginx..."
  "$PY" server/train_server.py --host 127.0.0.1 --port "$TRAINER_PORT" &
  TRAINER_PID=$!
  echo
  echo "Trainer running behind nginx."
  echo "  Website:  https://www.euwedewil.de/sandbox.html"
  echo "  Trainer:  proxied at wss://www.euwedewil.de/wss"
else
  echo "Starting static website on port ${STATIC_PORT}..."
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
fi

echo
echo "Press Ctrl-C to stop."
wait