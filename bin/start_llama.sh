#!/usr/bin/env bash
# Thin wrapper around llama-server — all business logic lives in Python
# (inferno.launch_config.build_llama_server_args).
#
# This script only handles:
#   1. Dynamic library path for GGML backends
#   2. Slot save directory creation
#   3. exec into the command supplied by Python as "$@"
set -euo pipefail

LLAMA_RUNTIME_DIR="${POTATO_LLAMA_RUNTIME_DIR:-/opt/potato/llama}"
SLOT_SAVE_PATH="${POTATO_SLOT_SAVE_PATH:-/opt/potato/state/llama-slots}"

if [ $# -eq 0 ]; then
  echo "ERROR: no arguments provided — Python owns command construction" >&2
  exit 1
fi

# Set up dynamic library path so llama-server can find GGML backends.
if [ -d "${LLAMA_RUNTIME_DIR}/lib" ]; then
  export LD_LIBRARY_PATH="${LLAMA_RUNTIME_DIR}/lib:${LD_LIBRARY_PATH:-}"
  export GGML_BACKEND_DIR="${LLAMA_RUNTIME_DIR}/lib"
fi

mkdir -p "${SLOT_SAVE_PATH}"

exec "$@"
