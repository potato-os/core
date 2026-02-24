#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${PI_HOST:-}"
PI_HOST_PRIMARY="${PI_HOST_PRIMARY:-192.168.1.132}"
PI_HOST_FALLBACK="${PI_HOST_FALLBACK:-192.168.1.131}"
PI_HOST_MDNS="${PI_HOST_MDNS:-potato.local}"
PI_SCHEME="${PI_SCHEME:-http}"
PI_PORT="${PI_PORT:-80}"
EXPECT_BACKEND="${EXPECT_BACKEND:-llama}"
REQUEST_TIMEOUT_SECONDS="${REQUEST_TIMEOUT_SECONDS:-120}"
MAX_TOKENS="${MAX_TOKENS:-48}"
SYSTEM_PROMPT="${SEED_SYSTEM_PROMPT:-You are Potato OS. Reply in one short sentence.}"
PROMPT="${SEED_PROMPT:-Describe Potato OS in a playful sentence with a tiny bit of randomness.}"
SEED_A="${SEED_A:-42}"
SEED_B="${SEED_B:-43}"
TEMPERATURE="${SEED_TEST_TEMPERATURE:-1.0}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd jq

pick_host() {
  if [ -n "${PI_HOST}" ]; then
    echo "${PI_HOST}"
    return
  fi
  if ping -c 1 -W 1 "${PI_HOST_MDNS}" >/dev/null 2>&1; then
    echo "${PI_HOST_MDNS}"
    return
  fi
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

TARGET_HOST="$(pick_host)"
if [ -z "${TARGET_HOST}" ]; then
  echo "No reachable Pi host found (${PI_HOST_MDNS}, ${PI_HOST_PRIMARY}, ${PI_HOST_FALLBACK})." >&2
  exit 1
fi

BASE_URL="${PI_SCHEME}://${TARGET_HOST}"
if [ -n "${PI_PORT}" ]; then
  BASE_URL="${BASE_URL}:${PI_PORT}"
fi

status_json="$(curl -sS --max-time 8 "${BASE_URL}/status")"
if ! printf '%s\n' "${status_json}" | jq -e . >/dev/null 2>&1; then
  echo "Invalid /status payload from ${BASE_URL}" >&2
  exit 1
fi

active_backend="$(printf '%s\n' "${status_json}" | jq -r '.backend.active // empty')"
llama_healthy="$(printf '%s\n' "${status_json}" | jq -r '.llama_server.healthy // false')"

if [ -n "${EXPECT_BACKEND}" ] && [ "${active_backend}" != "${EXPECT_BACKEND}" ]; then
  echo "Expected backend '${EXPECT_BACKEND}', got '${active_backend}'" >&2
  exit 1
fi

if [ "${EXPECT_BACKEND}" = "llama" ] && [ "${llama_healthy}" != "true" ]; then
  echo "Llama backend not healthy on ${TARGET_HOST}" >&2
  exit 1
fi

run_completion() {
  local include_seed="$1"
  local seed_value="$2"
  local payload=""
  local response=""
  local content=""

  if [ "${include_seed}" = "1" ]; then
    payload="$(
      jq -nc \
        --arg model "qwen-local" \
        --arg prompt "${PROMPT}" \
        --arg system_prompt "${SYSTEM_PROMPT}" \
        --arg seed "${seed_value}" \
        --arg max_tokens "${MAX_TOKENS}" \
        --arg temperature "${TEMPERATURE}" \
        '{
          model: $model,
          stream: false,
          temperature: ($temperature | tonumber),
          max_tokens: ($max_tokens | tonumber),
          seed: ($seed | tonumber),
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
        --arg system_prompt "${SYSTEM_PROMPT}" \
        --arg max_tokens "${MAX_TOKENS}" \
        --arg temperature "${TEMPERATURE}" \
        '{
          model: $model,
          stream: false,
          temperature: ($temperature | tonumber),
          max_tokens: ($max_tokens | tonumber),
          messages: [
            {role: "system", content: $system_prompt},
            {role: "user", content: $prompt}
          ]
        }'
    )"
  fi

  response="$(curl -sS --max-time "${REQUEST_TIMEOUT_SECONDS}" \
    -X POST "${BASE_URL}/v1/chat/completions" \
    -H 'content-type: application/json' \
    -d "${payload}")"
  if ! printf '%s\n' "${response}" | jq -e . >/dev/null 2>&1; then
    echo "Invalid completion response payload." >&2
    echo "${response}" >&2
    exit 1
  fi

  content="$(printf '%s\n' "${response}" | jq -r '.choices[0].message.content // empty')"
  if [ -z "${content}" ]; then
    echo "Empty completion content." >&2
    echo "${response}" >&2
    exit 1
  fi
  printf '%s' "${content}"
}

det_a="$(run_completion 1 "${SEED_A}")"
det_b="$(run_completion 1 "${SEED_A}")"
det_c="$(run_completion 1 "${SEED_B}")"
random_out="$(run_completion 0 0)"

if [ "${det_a}" != "${det_b}" ]; then
  echo "Deterministic outputs diverged for seed ${SEED_A}." >&2
  echo "A: ${det_a}" >&2
  echo "B: ${det_b}" >&2
  exit 1
fi

if [ "${det_a}" = "${det_c}" ]; then
  echo "Note: seed ${SEED_A} and seed ${SEED_B} produced the same sentence (allowed but uncommon)."
fi

printf 'Seed deterministic check passed on %s\n' "${TARGET_HOST}"
printf 'seed=%s output: %s\n' "${SEED_A}" "${det_a}"
printf 'seed=%s output: %s\n' "${SEED_B}" "${det_c}"
printf 'random output: %s\n' "${random_out}"
