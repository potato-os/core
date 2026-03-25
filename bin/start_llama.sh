#!/usr/bin/env bash
set -euo pipefail

POTATO_BASE_DIR="${POTATO_BASE_DIR:-/opt/potato}"
MODEL_PATH="${POTATO_MODEL_PATH:-${POTATO_BASE_DIR}/models/Qwen3.5-2B-Q4_K_M.gguf}"
LLAMA_RUNTIME_DIR="${POTATO_LLAMA_RUNTIME_DIR:-${POTATO_BASE_DIR}/llama}"
LLAMA_SERVER_BIN="${LLAMA_SERVER_BIN:-${LLAMA_RUNTIME_DIR}/bin/llama-server}"
LLAMA_HOST="${POTATO_LLAMA_HOST:-0.0.0.0}"
LLAMA_PORT="${POTATO_LLAMA_PORT:-8080}"
CTX_SIZE_DEFAULT="16384"
CTX_SIZE="${POTATO_CTX_SIZE:-${CTX_SIZE_DEFAULT}}"
LLAMA_PARALLEL="${POTATO_LLAMA_PARALLEL:-1}"
SLOT_SAVE_PATH="${POTATO_SLOT_SAVE_PATH:-${POTATO_BASE_DIR}/state/llama-slots}"
CACHE_RAM_MIB="${POTATO_LLAMA_CACHE_RAM_MIB:-1024}"

MMPROJ_PATH="${POTATO_MMPROJ_PATH:-}"
AUTO_DOWNLOAD_MMPROJ="${POTATO_AUTO_DOWNLOAD_MMPROJ:-1}"
HF_MMPROJ_REPO="${POTATO_HF_MMPROJ_REPO:-}"
# Plain Qwen3.5 2B/4B filenames are ambiguous across text-only and multimodal GGUFs.
# Keep this opt-in so text models are not forced into mmproj by default.
VISION_MODEL_NAME_PATTERN_QWEN35="${POTATO_VISION_MODEL_NAME_PATTERN_QWEN35:-0}"

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

resolve_path_portably() {
  local candidate_path="${1:-}"
  if [ -z "${candidate_path}" ]; then
    return 1
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=False))' "${candidate_path}" 2>/dev/null && return 0
  fi
  printf '%s\n' "${candidate_path}"
}

managed_model_path() {
  printf '%s/models/%s\n' "${POTATO_BASE_DIR}" "$(basename "${MODEL_PATH}")"
}

mmproj_search_dirs() {
  local real_model_path=''
  local managed_path=''
  local real_dir=''
  local managed_dir=''

  real_model_path="$(resolve_path_portably "${MODEL_PATH}" || printf '%s\n' "${MODEL_PATH}")"
  managed_path="$(managed_model_path)"
  real_dir="$(dirname "${real_model_path}")"
  managed_dir="$(dirname "${managed_path}")"

  if [ -d "${real_dir}" ]; then
    printf '%s\n' "${real_dir}"
  fi
  if [ -d "${managed_dir}" ] && [ "${managed_dir}" != "${real_dir}" ]; then
    printf '%s\n' "${managed_dir}"
  fi
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

model_is_qwen35_vision() {
  local model_name
  model_name="$(model_filename_lower)"
  [[ "${model_name}" == *qwen*3.5* ]]
}

model_requires_mmproj() {
  if [ "${VISION_MODEL_NAME_PATTERN_QWEN35}" = "1" ] && model_is_qwen35_vision; then
    return 0
  fi
  return 1
}

resolve_mmproj_repo() {
  local model_name
  model_name="$(model_filename_lower)"
  if [ -n "${HF_MMPROJ_REPO}" ]; then
    printf '%s' "${HF_MMPROJ_REPO}"
    return 0
  fi

  if [[ "${model_name}" == *qwen*3.5*2b* ]]; then
    printf 'unsloth/Qwen3.5-2B-GGUF'
    return 0
  fi
  if [[ "${model_name}" == *qwen*3.5*4b* ]]; then
    printf 'unsloth/Qwen3.5-4B-GGUF'
    return 0
  fi

  # Default fallback for unrecognized vision models
  printf 'unsloth/Qwen3.5-2B-GGUF'
}

qwen35_mmproj_name_candidates() {
  local model_stem trimmed_stem next_stem
  model_stem="$(basename "${MODEL_PATH}")"
  model_stem="${model_stem%.gguf}"
  trimmed_stem="${model_stem}"

  printf 'mmproj-%s-f16.gguf\n' "${model_stem}"
  while true; do
    next_stem="$(printf '%s\n' "${trimmed_stem}" | sed -E 's/-(I?Q[0-9]+(_[A-Za-z0-9]+)*|[0-9]+(\.[0-9]+)?bpw)$//I')"
    if [ -z "${next_stem}" ] || [ "${next_stem}" = "${trimmed_stem}" ]; then
      break
    fi
    trimmed_stem="${next_stem}"
    printf 'mmproj-%s-f16.gguf\n' "${trimmed_stem}"
  done

  printf '%s\n' "mmproj-F16.gguf"
}

mmproj_filename_candidates() {
  if model_is_qwen35_vision; then
    printf '%s\n' "mmproj-F16.gguf"
  fi
}

download_mmproj() {
  local model_dir
  local repo
  local url
  local remote_name
  local local_name
  local preferred_local
  local target
  local tmp
  local candidate
  model_dir="$(dirname "${MODEL_PATH}")"
  repo="$(resolve_mmproj_repo)"

  if ! command -v curl >/dev/null 2>&1; then
    return 1
  fi

  # Determine preferred model-specific local name (last entry before generic).
  preferred_local=""
  while read -r candidate; do
    [ -n "${candidate}" ] || continue
    [ "${candidate}" = "mmproj-F16.gguf" ] && break
    preferred_local="${candidate}"
  done < <(qwen35_mmproj_name_candidates)

  while read -r candidate; do
    [ -n "${candidate}" ] || continue
    url="https://huggingface.co/${repo}/resolve/main/${candidate}?download=true"
    remote_name="$(basename "${url%%\?*}")"
    # When downloading the generic file, save with model-specific name
    # to prevent stale reuse across model switches (#136).
    if [ "${remote_name}" = "mmproj-F16.gguf" ] && [ -n "${preferred_local}" ]; then
      local_name="${preferred_local}"
    else
      local_name="${remote_name}"
    fi
    target="${model_dir}/${local_name}"
    tmp="${target}.part"
    rm -f "${tmp}"
    if ionice -c3 nice -n 19 curl --fail --location --continue-at - --output "${tmp}" "${url}"; then
      mv -f "${tmp}" "${target}"
      # Clean up stale generic after saving model-specific file.
      if [ "${local_name}" != "mmproj-F16.gguf" ]; then
        rm -f "${model_dir}/mmproj-F16.gguf"
      fi
      MMPROJ_PATH="${target}"
      return 0
    fi
    rm -f "${tmp}"
  done < <(mmproj_filename_candidates)

  return 1
}

pick_mmproj() {
  local model_dir
  local candidate_base=''
  local f16_candidate=''
  local search_dir=''
  local candidate=''
  local -a mmproj_candidates=()
  local -a compatible_candidates=()
  model_dir="$(dirname "${MODEL_PATH}")"

  if [ -n "${MMPROJ_PATH}" ]; then
    [ -f "${MMPROJ_PATH}" ] || die "mmproj file not found: ${MMPROJ_PATH}"
    # When Python passed the generic file and auto-download is available,
    # try to replace it with a model-specific version (#136).
    if [ "$(basename "${MMPROJ_PATH}")" = "mmproj-F16.gguf" ] \
        && [ "${AUTO_DOWNLOAD_MMPROJ}" = "1" ] \
        && model_is_qwen35_vision; then
      if download_mmproj; then
        return 0
      fi
      # Download failed (offline?) — keep using the generic as-is.
    fi
    return 0
  fi

  shopt -s nullglob
  while read -r search_dir; do
    [ -n "${search_dir}" ] || continue
    for candidate in "${search_dir}"/mmproj*.gguf; do
      [ -e "${candidate}" ] || continue
      mmproj_candidates+=("${candidate}")
    done
  done < <(mmproj_search_dirs)
  shopt -u nullglob

  if [ "${#mmproj_candidates[@]}" -eq 0 ]; then
    if [ "${AUTO_DOWNLOAD_MMPROJ}" = "1" ] && download_mmproj; then
      return 0
    fi
    die "No mmproj file found in ${model_dir}. Set POTATO_MMPROJ_PATH or place mmproj*.gguf there."
  fi

  if model_is_qwen35_vision; then
    # 1. Try exact model-specific match (skip generic — handled below).
    while read -r candidate_base; do
      [ -n "${candidate_base}" ] || continue
      [ "${candidate_base}" = "mmproj-F16.gguf" ] && continue
      for candidate in "${mmproj_candidates[@]}"; do
        if [ "$(basename "${candidate}")" = "${candidate_base}" ]; then
          MMPROJ_PATH="${candidate}"
          return 0
        fi
      done
    done < <(qwen35_mmproj_name_candidates | awk '!seen[$0]++')

    # 2. No model-specific match — try auto-download before falling back.
    #    download_mmproj saves with a model-specific local name (#136).
    if [ "${AUTO_DOWNLOAD_MMPROJ}" = "1" ] && download_mmproj; then
      return 0
    fi

    # 3. Offline fallback: accept only generic mmproj-F16.gguf.
    #    Do NOT accept other models' specific projectors — they have
    #    different embedding dimensions and would crash llama-server (#136).
    for candidate in "${mmproj_candidates[@]}"; do
      candidate_base="$(basename "${candidate}" | tr '[:upper:]' '[:lower:]')"
      if [[ "${candidate_base}" == mmproj-f16.gguf ]]; then
        compatible_candidates+=("${candidate}")
      fi
    done
    if [ "${#compatible_candidates[@]}" -gt 0 ]; then
      mmproj_candidates=("${compatible_candidates[@]}")
      compatible_candidates=()
    else
      die "No compatible mmproj found for Qwen3.5 vision model (model: $(basename "${MODEL_PATH}"))."
    fi
  fi

  if [ "${#mmproj_candidates[@]}" -eq 1 ]; then
    MMPROJ_PATH="${mmproj_candidates[0]}"
    return 0
  fi

  # Qwen3.5 models: prefer F16 projectors
  f16_candidate="$(printf '%s\n' "${mmproj_candidates[@]}" | grep -i 'F16' | head -n 1 || true)"
  if [ -n "${f16_candidate}" ]; then
    MMPROJ_PATH="${f16_candidate}"
    return 0
  fi

  MMPROJ_PATH="${mmproj_candidates[0]}"
}

[ -f "${MODEL_PATH}" ] || die "Model file not found: ${MODEL_PATH}"
[ -x "${LLAMA_SERVER_BIN}" ] || die "llama-server binary not found or not executable: ${LLAMA_SERVER_BIN}"

# Qwen3.5-35B-A3B: use the default 16k context. The previous 4096 override
# caused context-shift crashes (GGML_ASSERT in rope) and is no longer needed
# with the ik_llama 433531dd build on Pi5 16GB with q8_0 KV cache.

if model_requires_mmproj; then
  pick_mmproj
fi

if [ -d "${LLAMA_RUNTIME_DIR}/lib" ]; then
  export LD_LIBRARY_PATH="${LLAMA_RUNTIME_DIR}/lib:${LD_LIBRARY_PATH:-}"
  export GGML_BACKEND_DIR="${LLAMA_RUNTIME_DIR}/lib"
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
REASONING_FORMAT="${POTATO_REASONING_FORMAT:-none}"
extra_args+=(--reasoning-format "${REASONING_FORMAT}")
CHAT_TEMPLATE_KWARGS="${POTATO_CHAT_TEMPLATE_KWARGS:-{\"enable_thinking\": false}}"
extra_args+=(--chat-template-kwargs "${CHAT_TEMPLATE_KWARGS}")
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
