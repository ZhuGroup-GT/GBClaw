#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

CONDA_ENV=GBclaw

activate_conda() {
  if ! command -v conda >/dev/null 2>&1; then
    echo "Error: conda not found. Activate the ${CONDA_ENV} environment manually." >&2
    exit 1
  fi
  eval "$(conda shell.bash hook)"
  conda activate "$CONDA_ENV"
}

# Fail fast on the two things the CLI cannot do without, before importing the stack.
if [[ ! -f "$ROOT/.key" ]]; then
  echo "Error: .key not found at $ROOT/.key (copy .key.example and fill in a model endpoint)." >&2
  exit 2
fi

if [[ ! -t 0 || ! -t 1 ]]; then
  echo "GBclaw CLI requires an interactive terminal." >&2
  exit 3
fi

activate_conda

echo "GBclaw terminal chat"
echo "Type /help for commands. Ctrl+C stops a running turn; /quit, Ctrl+D or Ctrl+C twice exits."
# Keep a caller's PYTHONPATH (for example out-of-tree extensions) after src.
exec env PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" python -m interfaces.cli
