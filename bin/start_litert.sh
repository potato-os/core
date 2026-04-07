#!/usr/bin/env bash
set -euo pipefail

# ── LiteRT adapter launcher ──────────────────────────────────────────
# Starts the LiteRT inference adapter on the configured port.
# Reads the same env vars as start_llama.sh:
#   POTATO_BASE_DIR     — base Potato directory (/opt/potato)
#   POTATO_MODEL_PATH   — path to the active .litertlm model
#   POTATO_LLAMA_PORT   — port to serve on (default 8080)

POTATO_BASE_DIR="${POTATO_BASE_DIR:-/opt/potato}"
POTATO_MODEL_PATH="${POTATO_MODEL_PATH:-}"
POTATO_LLAMA_PORT="${POTATO_LLAMA_PORT:-8080}"
POTATO_VENV_DIR="${POTATO_VENV_DIR:-${POTATO_BASE_DIR}/venv}"

if [ -z "${POTATO_MODEL_PATH}" ]; then
  echo "ERROR: POTATO_MODEL_PATH not set" >&2
  exit 1
fi

if [ ! -f "${POTATO_MODEL_PATH}" ]; then
  echo "ERROR: Model file not found: ${POTATO_MODEL_PATH}" >&2
  exit 1
fi

export POTATO_MODEL_PATH
export POTATO_BASE_DIR

cd "${POTATO_BASE_DIR}"

exec "${POTATO_VENV_DIR}/bin/uvicorn" \
  core.inferno.litert_adapter:app \
  --host 0.0.0.0 \
  --port "${POTATO_LLAMA_PORT}" \
  --workers 1
