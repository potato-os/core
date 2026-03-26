#!/usr/bin/env bash
set -euo pipefail
#
# Build MNN with LLM support on Raspberry Pi 5.
#
# Prerequisites: cmake, g++, make (sudo apt install -y cmake g++ make)
# Usage: rsync references/mnn/ to /tmp/mnn on Pi, then run this script ON the Pi.
#
# Environment variables:
#   MNN_SRC   — path to MNN source (default: /tmp/mnn)
#   BUILD_DIR — build output directory (default: /tmp/mnn-build)
#   JOBS      — parallel build jobs (default: 4)
#
# Refs #24

MNN_SRC="${MNN_SRC:-/tmp/mnn}"
BUILD_DIR="${BUILD_DIR:-/tmp/mnn-build}"
JOBS="${JOBS:-4}"

echo "=== MNN LLM Build for Pi 5 ==="
echo "  Source:  ${MNN_SRC}"
echo "  Build:   ${BUILD_DIR}"
echo "  Jobs:    ${JOBS}"

# Validate architecture
arch="$(uname -m)"
case "${arch}" in
  aarch64|arm64) ;;
  *)
    echo "ERROR: This script targets aarch64 (Pi 5). Current arch: ${arch}" >&2
    exit 1
    ;;
esac

# Validate source exists
if [ ! -f "${MNN_SRC}/CMakeLists.txt" ]; then
  echo "ERROR: MNN source not found at ${MNN_SRC}/CMakeLists.txt" >&2
  echo "Rsync the MNN repo first:" >&2
  echo "  sshpass -e rsync -az --delete --exclude .git references/mnn/ pi@potato.local:/tmp/mnn/" >&2
  exit 1
fi

# Check prerequisites
for cmd in cmake g++ make; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: ${cmd} not found. Install with: sudo apt install -y cmake g++ make" >&2
    exit 1
  fi
done

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

echo ""
echo "=== Running cmake ==="
cmake -S "${MNN_SRC}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DMNN_BUILD_LLM=ON \
  -DMNN_LOW_MEMORY=ON \
  -DMNN_CPU_WEIGHT_DEQUANT_GEMM=ON \
  -DMNN_SUPPORT_TRANSFORMER_FUSE=ON \
  -DMNN_ARM82=ON \
  -DMNN_USE_THREAD_POOL=ON

echo ""
echo "=== Building (this takes ~15 minutes on Pi 5) ==="
time cmake --build "${BUILD_DIR}" --config Release -j "${JOBS}"

echo ""
echo "=== Build complete ==="
if [ -f "${BUILD_DIR}/llm_demo" ]; then
  echo "Binary: ${BUILD_DIR}/llm_demo"
  ls -lh "${BUILD_DIR}/llm_demo"
else
  echo "WARNING: llm_demo not found at ${BUILD_DIR}/llm_demo" >&2
  echo "Searching for it..." >&2
  find "${BUILD_DIR}" -name "llm_demo" -type f 2>/dev/null
  exit 1
fi
