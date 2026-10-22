#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-9999}"
API_RELOAD="${API_RELOAD:-0}"
CONDA_ENV=GBclaw

# The server runs on an unprivileged port. Put a reverse proxy in front of it
# when it has to be reachable on 80 or 443.
if ! [[ "$API_PORT" =~ ^[0-9]+$ ]] || (( 10#$API_PORT < 1024 || 10#$API_PORT > 65535 )); then
  echo "Error: API_PORT must be a port number from 1024 to 65535 (got '${API_PORT}')." >&2
  exit 1
fi

activate_conda() {
  if ! command -v conda >/dev/null 2>&1; then
    echo "Error: conda not found. Activate the ${CONDA_ENV} environment manually." >&2
    exit 1
  fi
  eval "$(conda shell.bash hook)"
  conda activate "$CONDA_ENV"
}

activate_conda
PYTHON_BIN="$(command -v python)"

echo "GBclaw API server"
echo "Listening on http://${API_HOST}:${API_PORT}/"
echo "Press Ctrl+C to stop."
UVICORN_ARGS=(interfaces.api.app:app --host "$API_HOST" --port "$API_PORT"
  --ws websockets --ws-ping-interval 20 --ws-ping-timeout 20)
if [[ "$API_RELOAD" == "1" ]]; then
  UVICORN_ARGS+=(--reload --reload-dir src)
fi
# Keep a caller's PYTHONPATH (for example out-of-tree extensions) after src.
exec env PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -m uvicorn "${UVICORN_ARGS[@]}"
