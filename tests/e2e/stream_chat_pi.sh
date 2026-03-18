#!/usr/bin/env bash
set -euo pipefail

PI_HOST_PRIMARY="${PI_HOST_PRIMARY:-potato.local}"
PI_HOST_FALLBACK="${PI_HOST_FALLBACK:-potato.local}"
PI_SCHEME="${PI_SCHEME:-http}"
PI_PORT="${PI_PORT:-80}"
EXPECT_BACKEND="${EXPECT_BACKEND:-llama}"
STREAM_TIMEOUT_SECONDS="${STREAM_TIMEOUT_SECONDS:-120}"
MAX_TOKENS="${MAX_TOKENS:-128}"
PROMPT="${STREAM_PROMPT:-Return a short stream test.}"
SYSTEM_PROMPT="${STREAM_SYSTEM_PROMPT:-You are Potato OS, a quirky potato-powered AI box. Explain what Potato OS is in a funny, light, and playful way while still being technically correct.}"
SHOW_STREAM_TEXT="${SHOW_STREAM_TEXT:-1}"

if [ "$#" -gt 0 ]; then
  PROMPT="$*"
fi

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd jq

pick_host() {
  if ping -c 1 -W 1 "${PI_HOST_PRIMARY}" >/dev/null 2>&1; then
    echo "${PI_HOST_PRIMARY}"
    return
  fi
  if ping -c 1 -W 1 "${PI_HOST_FALLBACK}" >/dev/null 2>&1; then
    echo "${PI_HOST_FALLBACK}"
    return
  fi
  echo ""
}

PI_HOST="$(pick_host)"
if [ -z "${PI_HOST}" ]; then
  echo "No reachable Pi host found (${PI_HOST_PRIMARY}, ${PI_HOST_FALLBACK})." >&2
  exit 1
fi

BASE_URL="${PI_SCHEME}://${PI_HOST}"
if [ -n "${PI_PORT}" ]; then
  BASE_URL="${BASE_URL}:${PI_PORT}"
fi
status_json="$(curl -sS --max-time 5 "${BASE_URL}/status")"

active_backend="$(printf '%s' "${status_json}" | jq -r '.backend.active // empty')"
llama_healthy="$(printf '%s' "${status_json}" | jq -r '.llama_server.healthy // false')"

if [ -n "${EXPECT_BACKEND}" ] && [ "${active_backend}" != "${EXPECT_BACKEND}" ]; then
  echo "Expected backend '${EXPECT_BACKEND}', got '${active_backend}'" >&2
  exit 1
fi

if [ "${EXPECT_BACKEND}" = "llama" ] && [ "${llama_healthy}" != "true" ]; then
  echo "Llama backend not healthy on ${PI_HOST}" >&2
  exit 1
fi

stream_file="$(mktemp)"
trap 'rm -f "${stream_file}"' EXIT

if [ -n "${SYSTEM_PROMPT}" ]; then
  payload="$(
    jq -nc \
      --arg model "qwen-local" \
      --arg prompt "${PROMPT}" \
      --arg system_prompt "${SYSTEM_PROMPT}" \
      --arg max_tokens "${MAX_TOKENS}" \
      '{
        model: $model,
        stream: true,
        max_tokens: ($max_tokens | tonumber),
        messages: [
          {role: "system", content: $system_prompt},
          {role: "user", content: $prompt}
        ]
      }'
  )"
else
  payload="$(
    jq -nc \
      --arg model "qwen-local" \
      --arg prompt "${PROMPT}" \
      --arg max_tokens "${MAX_TOKENS}" \
      '{
        model: $model,
        stream: true,
        max_tokens: ($max_tokens | tonumber),
        messages: [
          {role: "user", content: $prompt}
        ]
      }'
  )"
fi

start_ts="$(date +%s)"
if [ "${SHOW_STREAM_TEXT}" = "1" ]; then
  curl -sS -N --no-buffer --max-time "${STREAM_TIMEOUT_SECONDS}" \
    -X POST "${BASE_URL}/v1/chat/completions" \
    -H 'content-type: application/json' \
    -d "${payload}" | tee "${stream_file}" | while IFS= read -r line; do
      case "${line}" in
        "data: "*) ;;
        *) continue ;;
      esac

      payload="${line#data: }"
      if [ "${payload}" = "[DONE]" ]; then
        continue
      fi

      token="$(printf '%s\n' "${payload}" | jq -r '.choices[0].delta.content // empty' 2>/dev/null || true)"
      if [ -n "${token}" ]; then
        printf '%s' "${token}"
      fi
    done
  printf '\n'
else
  curl -sS -N --no-buffer --max-time "${STREAM_TIMEOUT_SECONDS}" \
    -X POST "${BASE_URL}/v1/chat/completions" \
    -H 'content-type: application/json' \
    -d "${payload}" > "${stream_file}"
fi
end_ts="$(date +%s)"

if ! grep -q '^data: ' "${stream_file}"; then
  echo "No SSE data lines returned" >&2
  exit 1
fi

if ! grep -q '^data: \[DONE\]$' "${stream_file}"; then
  echo "Missing terminal [DONE] event" >&2
  exit 1
fi

chunk_count=0
role_seen=0
content_seen=0
finish_seen=0
content_chunk_count=0
predicted_tps=""
predicted_tokens=""

while IFS= read -r line; do
  case "${line}" in
    "data: "*) ;;
    *) continue ;;
  esac

  payload="${line#data: }"
  if [ "${payload}" = "[DONE]" ]; then
    continue
  fi

  if ! printf '%s\n' "${payload}" | jq -e . >/dev/null 2>&1; then
    echo "Invalid JSON stream payload: ${payload}" >&2
    exit 1
  fi

  object_type="$(printf '%s\n' "${payload}" | jq -r '.object // empty')"
  if [ "${object_type}" != "chat.completion.chunk" ]; then
    echo "Unexpected chunk object type: ${object_type}" >&2
    exit 1
  fi

  chunk_count=$((chunk_count + 1))

  if printf '%s\n' "${payload}" | jq -e '.choices[0].delta.role == "assistant"' >/dev/null 2>&1; then
    role_seen=1
  fi

  if printf '%s\n' "${payload}" | jq -e '.choices[0].delta.content? | strings | length > 0' >/dev/null 2>&1; then
    content_seen=1
    content_chunk_count=$((content_chunk_count + 1))
  fi

  if printf '%s\n' "${payload}" | jq -e '.choices[0].finish_reason != null' >/dev/null 2>&1; then
    finish_seen=1
  fi

  tps_val="$(printf '%s\n' "${payload}" | jq -r '.timings.predicted_per_second // empty')"
  pred_n_val="$(printf '%s\n' "${payload}" | jq -r '.timings.predicted_n // empty')"
  if [ -n "${tps_val}" ]; then
    predicted_tps="${tps_val}"
  fi
  if [ -n "${pred_n_val}" ]; then
    predicted_tokens="${pred_n_val}"
  fi
done < "${stream_file}"

if [ "${chunk_count}" -lt 2 ]; then
  echo "Expected multiple stream chunks, got ${chunk_count}" >&2
  exit 1
fi

if [ "${role_seen}" -ne 1 ]; then
  echo "Missing assistant role delta chunk" >&2
  exit 1
fi

if [ "${content_seen}" -ne 1 ]; then
  echo "Missing content delta chunk" >&2
  exit 1
fi

if [ "${finish_seen}" -ne 1 ]; then
  echo "Missing finish_reason chunk" >&2
  exit 1
fi

elapsed_seconds=$((end_ts - start_ts))
if [ "${elapsed_seconds}" -le 0 ]; then
  elapsed_seconds=1
fi

if [ -n "${predicted_tps}" ]; then
  if [ -n "${predicted_tokens}" ]; then
    printf 'Throughput: %s t/s (%s tokens, llama timings)\n' "${predicted_tps}" "${predicted_tokens}"
  else
    printf 'Throughput: %s t/s (llama timings)\n' "${predicted_tps}"
  fi
else
  approx_tps="$(awk -v n="${content_chunk_count}" -v s="${elapsed_seconds}" 'BEGIN { if (s <= 0) s = 1; printf "%.3f", n / s }')"
  printf 'Throughput: %s t/s (estimated from content chunks)\n' "${approx_tps}"
fi

echo "Streaming validation passed on ${PI_HOST} (${chunk_count} chunks)."
