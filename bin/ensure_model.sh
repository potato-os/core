#!/usr/bin/env bash
set -euo pipefail

POTATO_BASE_DIR="${POTATO_BASE_DIR:-/opt/potato}"
MODEL_PATH="${POTATO_MODEL_PATH:-${POTATO_BASE_DIR}/models/Qwen3-VL-4B-Instruct-Q4_K_M.gguf}"
STATE_PATH="${POTATO_DOWNLOAD_STATE_PATH:-${POTATO_BASE_DIR}/state/download.json}"
MODEL_URL="${POTATO_MODEL_URL:-https://huggingface.co/unsloth/Qwen3-VL-4B-Instruct-GGUF/resolve/main/Qwen3-VL-4B-Instruct-Q4_K_M.gguf}"

mkdir -p "$(dirname "${MODEL_PATH}")" "$(dirname "${STATE_PATH}")"
TMP_PATH="${MODEL_PATH}.part"
CURL_ERR_PATH="${STATE_PATH}.curl.err"

filesize() {
  local path="$1"
  if [ -f "$path" ]; then
    stat -c%s "$path"
  else
    echo 0
  fi
}

free_space_bytes() {
  df -B1 "$(dirname "${MODEL_PATH}")" | awk 'NR==2 {print $4+0}'
}

write_state() {
  local bytes_total="$1"
  local bytes_downloaded="$2"
  local percent="$3"
  local speed_bps="$4"
  local eta_seconds="$5"
  local error_msg="${6:-}"

  python3 - "$STATE_PATH" "$bytes_total" "$bytes_downloaded" "$percent" "$speed_bps" "$eta_seconds" "$error_msg" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
obj = {
    "bytes_total": int(sys.argv[2]),
    "bytes_downloaded": int(sys.argv[3]),
    "percent": int(sys.argv[4]),
    "speed_bps": int(sys.argv[5]),
    "eta_seconds": int(sys.argv[6]),
    "error": sys.argv[7] or None,
}

tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(obj), encoding="utf-8")
tmp.replace(path)
PY
}

if [ -f "${MODEL_PATH}" ] && [ "$(filesize "${MODEL_PATH}")" -gt 0 ]; then
  total_now="$(filesize "${MODEL_PATH}")"
  write_state "${total_now}" "${total_now}" 100 0 0 ""
  exit 0
fi

total_bytes="$(curl -fsSLI "${MODEL_URL}" | tr -d '\r' | awk -F': ' 'tolower($1)=="content-length"{print $2}' | tail -n1)"
if [ -z "${total_bytes}" ]; then
  total_bytes=0
fi

if [ "${total_bytes}" -gt 0 ]; then
  already_downloaded="$(filesize "${TMP_PATH}")"
  if [ "${already_downloaded}" -lt 0 ]; then
    already_downloaded=0
  fi
  bytes_needed=$((total_bytes - already_downloaded))
  if [ "${bytes_needed}" -lt 0 ]; then
    bytes_needed=0
  fi
  free_now="$(free_space_bytes)"
  if [ "${free_now}" -lt "${bytes_needed}" ]; then
    percent=0
    if [ "${total_bytes}" -gt 0 ]; then
      percent=$((already_downloaded * 100 / total_bytes))
    fi
    write_state "${total_bytes}" "${already_downloaded}" "${percent}" 0 0 "insufficient_storage"
    exit 1
  fi
fi

start_ts="$(date +%s)"
rm -f "${CURL_ERR_PATH}"
curl -L -C - --fail --output "${TMP_PATH}" "${MODEL_URL}" 2>"${CURL_ERR_PATH}" &
download_pid=$!

while kill -0 "${download_pid}" 2>/dev/null; do
  downloaded="$(filesize "${TMP_PATH}")"
  now_ts="$(date +%s)"
  elapsed=$((now_ts - start_ts))
  if [ "${elapsed}" -le 0 ]; then
    elapsed=1
  fi
  speed=$((downloaded / elapsed))

  percent=0
  eta=0
  if [ "${total_bytes}" -gt 0 ]; then
    percent=$((downloaded * 100 / total_bytes))
    if [ "${speed}" -gt 0 ]; then
      remaining=$((total_bytes - downloaded))
      if [ "${remaining}" -gt 0 ]; then
        eta=$((remaining / speed))
      fi
    fi
  fi

  write_state "${total_bytes}" "${downloaded}" "${percent}" "${speed}" "${eta}" ""
  sleep 2
done

if ! wait "${download_pid}"; then
  err_msg="download_failed"
  downloaded_now="$(filesize "${TMP_PATH}")"
  percent_now=0
  if [ "${total_bytes}" -gt 0 ]; then
    percent_now=$((downloaded_now * 100 / total_bytes))
  fi
  if [ "$(free_space_bytes)" -le 0 ]; then
    err_msg="insufficient_storage"
  elif [ -f "${CURL_ERR_PATH}" ] && grep -qi "No space left on device" "${CURL_ERR_PATH}"; then
    err_msg="insufficient_storage"
  fi
  write_state "${total_bytes}" "${downloaded_now}" "${percent_now}" 0 0 "${err_msg}"
  exit 1
fi

mv -f "${TMP_PATH}" "${MODEL_PATH}"
final_size="$(filesize "${MODEL_PATH}")"
write_state "${final_size}" "${final_size}" 100 0 0 ""
rm -f "${CURL_ERR_PATH}"
