#!/usr/bin/env bash
set -euo pipefail

POTATO_BASE_DIR="${POTATO_BASE_DIR:-/opt/potato}"
MODEL_PATH="${POTATO_MODEL_PATH:-${POTATO_BASE_DIR}/models/Qwen3-VL-4B-Instruct-Q4_K_M.gguf}"
LLAMA_RUNTIME_DIR="${POTATO_LLAMA_RUNTIME_DIR:-${POTATO_BASE_DIR}/llama}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-${LLAMA_RUNTIME_DIR}/bin/llama-server}"
LLAMA_HOST="${POTATO_LLAMA_HOST:-0.0.0.0}"
LLAMA_PORT="${POTATO_LLAMA_PORT:-8080}"
CTX_SIZE="${POTATO_CTX_SIZE:-4096}"
LLAMA_PARALLEL="${POTATO_LLAMA_PARALLEL:-1}"
SLOT_SAVE_PATH="${POTATO_SLOT_SAVE_PATH:-${POTATO_BASE_DIR}/state/llama-slots}"
CACHE_RAM_MIB="${POTATO_LLAMA_CACHE_RAM_MIB:-0}"

MMPROJ_PATH="${POTATO_MMPROJ_PATH:-}"
AUTO_DOWNLOAD_MMPROJ="${POTATO_AUTO_DOWNLOAD_MMPROJ:-1}"
HF_MMPROJ_REPO="${POTATO_HF_MMPROJ_REPO:-Qwen/Qwen3-VL-4B-Instruct-GGUF}"

CACHE_TYPE_K="${POTATO_CACHE_TYPE_K:-q8_0}"
CACHE_TYPE_V="${POTATO_CACHE_TYPE_V:-q8_0}"
KV_FLAGS="${POTATO_LLAMA_KV_FLAGS:-}"
ENABLE_FLASH_ATTN="${POTATO_LLAMA_FLASH_ATTN:-1}"
USE_JINJA="${POTATO_LLAMA_JINJA:-1}"
DISABLE_WARMUP="${POTATO_LLAMA_NO_WARMUP:-1}"
EXTRA_FLAGS="${POTATO_LLAMA_EXTRA_FLAGS:-}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

download_mmproj() {
  local model_dir
  local url
  local remote_name
  local target
  local tmp
  model_dir="$(dirname "${MODEL_PATH}")"

  if ! command -v curl >/dev/null 2>&1; then
    return 1
  fi

  for url in \
    "https://huggingface.co/${HF_MMPROJ_REPO}/resolve/main/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf?download=true" \
    "https://huggingface.co/${HF_MMPROJ_REPO}/resolve/main/mmproj-Qwen3-VL-4B-Instruct-Q8_0.gguf?download=true" \
    "https://huggingface.co/${HF_MMPROJ_REPO}/resolve/main/mmproj-Qwen3VL-4B-Instruct-F16.gguf?download=true" \
    "https://huggingface.co/${HF_MMPROJ_REPO}/resolve/main/mmproj-Qwen3-VL-4B-Instruct-F16.gguf?download=true"; do
    remote_name="$(basename "${url%%\?*}")"
    target="${model_dir}/${remote_name}"
    tmp="${target}.part"
    rm -f "${tmp}"
    if curl --fail --location --continue-at - --output "${tmp}" "${url}"; then
      mv -f "${tmp}" "${target}"
      MMPROJ_PATH="${target}"
      return 0
    fi
    rm -f "${tmp}"
  done

  return 1
}

pick_mmproj() {
  local model_dir
  local q8_candidate
  local f16_candidate
  local -a mmproj_candidates=()
  model_dir="$(dirname "${MODEL_PATH}")"

  if [ -n "${MMPROJ_PATH}" ]; then
    [ -f "${MMPROJ_PATH}" ] || die "mmproj file not found: ${MMPROJ_PATH}"
    return 0
  fi

  shopt -s nullglob
  mmproj_candidates=("${model_dir}"/mmproj*.gguf)
  shopt -u nullglob

  if [ "${#mmproj_candidates[@]}" -eq 0 ]; then
    if [ "${AUTO_DOWNLOAD_MMPROJ}" = "1" ] && download_mmproj; then
      return 0
    fi
    die "No mmproj file found in ${model_dir}. Set POTATO_MMPROJ_PATH or place mmproj*.gguf there."
  fi

  if [ "${#mmproj_candidates[@]}" -eq 1 ]; then
    MMPROJ_PATH="${mmproj_candidates[0]}"
    return 0
  fi

  q8_candidate="$(printf '%s\n' "${mmproj_candidates[@]}" | grep -i 'Q8_0' | head -n 1 || true)"
  if [ -n "${q8_candidate}" ]; then
    MMPROJ_PATH="${q8_candidate}"
    return 0
  fi

  f16_candidate="$(printf '%s\n' "${mmproj_candidates[@]}" | grep -i 'F16' | head -n 1 || true)"
  if [ -n "${f16_candidate}" ]; then
    MMPROJ_PATH="${f16_candidate}"
    return 0
  fi

  MMPROJ_PATH="${mmproj_candidates[0]}"
}

[ -f "${MODEL_PATH}" ] || die "Model file not found: ${MODEL_PATH}"
[ -x "${LLAMA_SERVER_BIN}" ] || die "llama-server binary not found or not executable: ${LLAMA_SERVER_BIN}"

pick_mmproj

if [ -d "${LLAMA_RUNTIME_DIR}/lib" ]; then
  export LD_LIBRARY_PATH="${LLAMA_RUNTIME_DIR}/lib:${LD_LIBRARY_PATH:-}"
fi

mkdir -p "${SLOT_SAVE_PATH}"

if [ -n "${KV_FLAGS}" ]; then
  # shellcheck disable=SC2206
  kv_args=(${KV_FLAGS})
else
  kv_args=(--cache-type-k "${CACHE_TYPE_K}" --cache-type-v "${CACHE_TYPE_V}")
fi

extra_args=()
if [ "${USE_JINJA}" = "1" ]; then
  extra_args+=(--jinja)
fi
if [ "${ENABLE_FLASH_ATTN}" = "1" ]; then
  extra_args+=(--flash-attn on)
fi
if [ "${DISABLE_WARMUP}" = "1" ]; then
  extra_args+=(--no-warmup)
fi
if [ -n "${EXTRA_FLAGS}" ]; then
  # shellcheck disable=SC2206
  split_extra=(${EXTRA_FLAGS})
  extra_args+=("${split_extra[@]}")
fi

exec "${LLAMA_SERVER_BIN}" \
  --model "${MODEL_PATH}" \
  --mmproj "${MMPROJ_PATH}" \
  --host "${LLAMA_HOST}" \
  --port "${LLAMA_PORT}" \
  --ctx-size "${CTX_SIZE}" \
  --cache-ram "${CACHE_RAM_MIB}" \
  --parallel "${LLAMA_PARALLEL}" \
  --slot-save-path "${SLOT_SAVE_PATH}" \
  "${kv_args[@]}" \
  "${extra_args[@]}"
