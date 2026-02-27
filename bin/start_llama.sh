#!/usr/bin/env bash
set -euo pipefail

POTATO_BASE_DIR="${POTATO_BASE_DIR:-/opt/potato}"
MODEL_PATH="${POTATO_MODEL_PATH:-${POTATO_BASE_DIR}/models/Qwen3-VL-4B-Instruct-Q4_K_M.gguf}"
LLAMA_RUNTIME_DIR="${POTATO_LLAMA_RUNTIME_DIR:-${POTATO_BASE_DIR}/llama}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-${LLAMA_RUNTIME_DIR}/bin/llama-server}"
LLAMA_HOST="${POTATO_LLAMA_HOST:-0.0.0.0}"
LLAMA_PORT="${POTATO_LLAMA_PORT:-8080}"
CTX_SIZE_DEFAULT="16384"
CTX_SIZE="${POTATO_CTX_SIZE:-${CTX_SIZE_DEFAULT}}"
LLAMA_PARALLEL="${POTATO_LLAMA_PARALLEL:-1}"
SLOT_SAVE_PATH="${POTATO_SLOT_SAVE_PATH:-${POTATO_BASE_DIR}/state/llama-slots}"
CACHE_RAM_MIB="${POTATO_LLAMA_CACHE_RAM_MIB:-0}"

MMPROJ_PATH="${POTATO_MMPROJ_PATH:-}"
AUTO_DOWNLOAD_MMPROJ="${POTATO_AUTO_DOWNLOAD_MMPROJ:-1}"
HF_MMPROJ_REPO="${POTATO_HF_MMPROJ_REPO:-}"
VISION_MODEL_NAME_PATTERN_VL="${POTATO_VISION_MODEL_NAME_PATTERN_VL:-1}"

CACHE_TYPE_K="${POTATO_CACHE_TYPE_K:-q8_0}"
CACHE_TYPE_V="${POTATO_CACHE_TYPE_V:-q8_0}"
KV_FLAGS="${POTATO_LLAMA_KV_FLAGS:-}"
ENABLE_FLASH_ATTN="${POTATO_LLAMA_FLASH_ATTN:-1}"
USE_JINJA="${POTATO_LLAMA_JINJA:-1}"
DISABLE_WARMUP="${POTATO_LLAMA_NO_WARMUP:-1}"
LLAMA_NO_MMAP="${POTATO_LLAMA_NO_MMAP:-auto}"
EXTRA_FLAGS="${POTATO_LLAMA_EXTRA_FLAGS:-}"
PI_16GB_MEMORY_THRESHOLD_BYTES="$((12 * 1024 * 1024 * 1024))"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

bool_env_true() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

bool_env_false() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off) return 0 ;;
    *) return 1 ;;
  esac
}

detect_pi_model_name() {
  if [ -n "${POTATO_PI_MODEL_OVERRIDE:-}" ]; then
    printf '%s' "${POTATO_PI_MODEL_OVERRIDE}"
    return 0
  fi
  if [ -r /proc/device-tree/model ]; then
    tr -d '\000' < /proc/device-tree/model 2>/dev/null || true
    return 0
  fi
  return 1
}

detect_total_memory_bytes() {
  if [ -n "${POTATO_TOTAL_MEMORY_BYTES_OVERRIDE:-}" ]; then
    printf '%s' "${POTATO_TOTAL_MEMORY_BYTES_OVERRIDE}"
    return 0
  fi
  if [ -r /proc/meminfo ]; then
    awk '/^MemTotal:/ { print $2 * 1024; exit }' /proc/meminfo 2>/dev/null || true
    return 0
  fi
  return 1
}

is_pi5_16gb() {
  local model_name total_memory
  model_name="$(detect_pi_model_name || true)"
  total_memory="$(detect_total_memory_bytes || true)"
  model_name="$(printf '%s' "${model_name}" | tr '[:upper:]' '[:lower:]')"
  if [[ "${model_name}" != *"raspberry pi 5"* ]]; then
    return 1
  fi
  if ! [[ "${total_memory}" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  [ "${total_memory}" -ge "${PI_16GB_MEMORY_THRESHOLD_BYTES}" ]
}

should_disable_mmap() {
  local runtime_profile=''
  if bool_env_true "${LLAMA_NO_MMAP}"; then
    return 0
  fi
  if bool_env_false "${LLAMA_NO_MMAP}"; then
    return 1
  fi
  if [ "${LLAMA_NO_MMAP}" != "auto" ] && [ -n "${LLAMA_NO_MMAP}" ]; then
    printf 'WARNING: invalid POTATO_LLAMA_NO_MMAP=%s; using auto\n' "${LLAMA_NO_MMAP}" >&2
  fi
  runtime_profile=''
  if [ -r "${LLAMA_RUNTIME_DIR}/.potato-llama-runtime-bundle.json" ]; then
    runtime_profile="$(
      sed -n 's/.*"profile"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "${LLAMA_RUNTIME_DIR}/.potato-llama-runtime-bundle.json" 2>/dev/null | head -n1
    )"
  fi
  if model_is_qwen35_a3b && is_pi5_16gb && [ "${runtime_profile}" = "pi5-opt" ]; then
    return 0
  fi
  return 1
}

model_filename_lower() {
  basename "${MODEL_PATH}" | tr '[:upper:]' '[:lower:]'
}

model_is_qwen3vl() {
  local model_name
  model_name="$(model_filename_lower)"
  [[ "${model_name}" == *qwen3*vl* ]]
}

model_is_qwen35_a3b() {
  local model_name
  model_name="$(model_filename_lower)"
  [[ "${model_name}" == *qwen*3.5*35b*a3b* ]]
}

model_has_vl_name() {
  local model_name
  model_name="$(model_filename_lower)"
  [[ "${model_name}" == *vl* ]]
}

model_requires_mmproj() {
  if [ "${VISION_MODEL_NAME_PATTERN_VL}" != "1" ]; then
    return 1
  fi
  model_has_vl_name
}

qwen3vl_size_tag() {
  local model_name
  model_name="$(model_filename_lower)"
  if [[ "${model_name}" =~ qwen3[-_]*vl[-_]*([0-9]+b) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

qwen3vl_variant() {
  local model_name
  model_name="$(model_filename_lower)"
  if [[ "${model_name}" == *thinking* ]]; then
    printf 'Thinking'
  else
    printf 'Instruct'
  fi
}

resolve_mmproj_repo() {
  local model_name
  model_name="$(model_filename_lower)"
  if [ -n "${HF_MMPROJ_REPO}" ]; then
    printf '%s' "${HF_MMPROJ_REPO}"
    return 0
  fi

  if [[ "${model_name}" == *qwen3*vl*2b*thinking* ]]; then
    printf 'Qwen/Qwen3-VL-2B-Thinking-GGUF'
    return 0
  fi
  if [[ "${model_name}" == *qwen3*vl*2b* ]]; then
    printf 'Qwen/Qwen3-VL-2B-Instruct-GGUF'
    return 0
  fi
  if [[ "${model_name}" == *qwen3*vl*4b*thinking* ]]; then
    printf 'Qwen/Qwen3-VL-4B-Thinking-GGUF'
    return 0
  fi
  if [[ "${model_name}" == *qwen3*vl*4b* ]]; then
    printf 'Qwen/Qwen3-VL-4B-Instruct-GGUF'
    return 0
  fi

  printf 'Qwen/Qwen3-VL-4B-Instruct-GGUF'
}

mmproj_filename_candidates() {
  local model_name
  local repo
  local size_tag=''
  local size_upper=''
  local variant='Instruct'
  model_name="$(model_filename_lower)"
  repo="$(resolve_mmproj_repo)"

  if model_is_qwen3vl; then
    size_tag="$(qwen3vl_size_tag || true)"
    if [ -n "${size_tag}" ]; then
      size_upper="$(printf '%s' "${size_tag}" | tr '[:lower:]' '[:upper:]')"
      variant="$(qwen3vl_variant)"
      printf '%s\n' \
        "mmproj-Qwen3VL-${size_upper}-${variant}-Q8_0.gguf" \
        "mmproj-Qwen3VL-${size_upper}-${variant}-F16.gguf"
    fi
  fi

  case "${repo}" in
    *Qwen3-VL-2B-*)
      printf '%s\n' \
        "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf" \
        "mmproj-Qwen3VL-2B-Instruct-F16.gguf"
      ;;
    *Qwen3-VL-4B-*)
      printf '%s\n' \
        "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf" \
        "mmproj-Qwen3-VL-4B-Instruct-Q8_0.gguf" \
        "mmproj-Qwen3VL-4B-Instruct-F16.gguf" \
        "mmproj-Qwen3-VL-4B-Instruct-F16.gguf"
      ;;
  esac
}

download_mmproj() {
  local model_dir
  local repo
  local url
  local remote_name
  local target
  local tmp
  local candidate
  model_dir="$(dirname "${MODEL_PATH}")"
  repo="$(resolve_mmproj_repo)"

  if ! command -v curl >/dev/null 2>&1; then
    return 1
  fi

  while read -r candidate; do
    [ -n "${candidate}" ] || continue
    url="https://huggingface.co/${repo}/resolve/main/${candidate}?download=true"
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
  done < <(mmproj_filename_candidates)

  return 1
}

pick_mmproj() {
  local model_dir
  local model_size_tag=''
  local candidate_base=''
  local q8_candidate=''
  local f16_candidate=''
  local -a mmproj_candidates=()
  local -a compatible_candidates=()
  model_dir="$(dirname "${MODEL_PATH}")"

  if [ -n "${MMPROJ_PATH}" ]; then
    [ -f "${MMPROJ_PATH}" ] || die "mmproj file not found: ${MMPROJ_PATH}"
    return 0
  fi

  shopt -s nullglob
  mmproj_candidates=("${model_dir}"/mmproj*.gguf)
  shopt -u nullglob

  if model_is_qwen3vl; then
    model_size_tag="$(qwen3vl_size_tag || true)"
  fi

  if [ "${#mmproj_candidates[@]}" -eq 0 ]; then
    if [ "${AUTO_DOWNLOAD_MMPROJ}" = "1" ] && download_mmproj; then
      return 0
    fi
    die "No mmproj file found in ${model_dir}. Set POTATO_MMPROJ_PATH or place mmproj*.gguf there."
  fi

  if [ -n "${model_size_tag}" ]; then
    for candidate in "${mmproj_candidates[@]}"; do
      candidate_base="$(basename "${candidate}" | tr '[:upper:]' '[:lower:]')"
      if [[ "${candidate_base}" == *"${model_size_tag}"* ]]; then
        compatible_candidates+=("${candidate}")
      fi
    done
  fi

  if [ "${#compatible_candidates[@]}" -gt 0 ]; then
    mmproj_candidates=("${compatible_candidates[@]}")
  elif [ -n "${model_size_tag}" ]; then
    if [ "${AUTO_DOWNLOAD_MMPROJ}" = "1" ] && download_mmproj; then
      return 0
    fi
    die "No compatible mmproj found for model size ${model_size_tag} (model: $(basename "${MODEL_PATH}"))."
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

# Qwen3.5-35B-A3B on Pi5 16GB is memory-sensitive; use a smaller default context
# unless the operator explicitly set POTATO_CTX_SIZE.
if model_is_qwen35_a3b && [ -z "${POTATO_CTX_SIZE+x}" ]; then
  CTX_SIZE="4096"
  printf 'Applying Qwen3.5-35B-A3B runtime profile: ctx-size=%s\n' "${CTX_SIZE}" >&2
fi

if model_requires_mmproj; then
  pick_mmproj
fi

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
if should_disable_mmap; then
  extra_args+=(--no-mmap)
  printf 'Applying no-mmap runtime profile for local weights (disable GGUF mmap streaming)\n' >&2
fi
if [ -n "${EXTRA_FLAGS}" ]; then
  # shellcheck disable=SC2206
  split_extra=(${EXTRA_FLAGS})
  extra_args+=("${split_extra[@]}")
fi

cmd=(
  "${LLAMA_SERVER_BIN}"
  --model "${MODEL_PATH}"
  --host "${LLAMA_HOST}"
  --port "${LLAMA_PORT}"
  --ctx-size "${CTX_SIZE}"
  --cache-ram "${CACHE_RAM_MIB}"
  --parallel "${LLAMA_PARALLEL}"
  --slot-save-path "${SLOT_SAVE_PATH}"
)

if model_requires_mmproj; then
  cmd+=(--mmproj "${MMPROJ_PATH}")
fi

cmd+=("${kv_args[@]}")
cmd+=("${extra_args[@]}")

exec "${cmd[@]}"
