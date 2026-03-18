#!/usr/bin/env bash
set -euo pipefail

PI_HOST_PRIMARY="${PI_HOST_PRIMARY:-potato.local}"
PI_HOST_FALLBACK="${PI_HOST_FALLBACK:-potato.local}"
PI_SCHEME="${PI_SCHEME:-http}"
PI_PORT="${PI_PORT:-80}"
EXPECT_BACKEND="${EXPECT_BACKEND:-llama}"
VISION_TIMEOUT_SECONDS="${VISION_TIMEOUT_SECONDS:-240}"
MAX_TOKENS="${MAX_TOKENS:-96}"
VISION_PROMPT="${VISION_PROMPT:-Identify the main animal in this image and describe it in one short sentence.}"
SHOW_RESPONSES="${SHOW_RESPONSES:-1}"
HTTP_USER_AGENT="${HTTP_USER_AGENT:-potato-os-vision-e2e/1.0 (+https://localhost)}"

# label|url|expected_keywords_csv
VISION_CASES=(
  "cat|https://upload.wikimedia.org/wikipedia/commons/thumb/b/b6/Felis_catus-cat_on_snow.jpg/512px-Felis_catus-cat_on_snow.jpg|cat,feline,kitten"
  "dog|https://upload.wikimedia.org/wikipedia/commons/thumb/6/6e/Golde33443.jpg/512px-Golde33443.jpg|dog,canine,retriever,puppy"
  "elephant|https://upload.wikimedia.org/wikipedia/commons/thumb/3/37/African_Bush_Elephant.jpg/512px-African_Bush_Elephant.jpg|elephant,tusk,trunk"
)

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd jq
require_cmd base64
require_cmd tr
require_cmd grep

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

mime_type_for_file() {
  local file_path="$1"
  local mime=""

  if command -v file >/dev/null 2>&1; then
    mime="$(file --brief --mime-type "${file_path}" 2>/dev/null || true)"
  fi

  if [ -n "${mime}" ] && [ "${mime#image/}" != "${mime}" ]; then
    printf '%s' "${mime}"
    return
  fi

  case "${file_path}" in
    *.png) printf '%s' "image/png" ;;
    *.webp) printf '%s' "image/webp" ;;
    *.gif) printf '%s' "image/gif" ;;
    *) printf '%s' "image/jpeg" ;;
  esac
}

check_backend_ready() {
  local base_url="$1"
  local status_json
  local active_backend
  local llama_healthy

  status_json="$(curl -sS --max-time 5 "${base_url}/status")"
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
check_backend_ready "${BASE_URL}"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

success_count=0

echo "Running vision E2E on ${PI_HOST} (${#VISION_CASES[@]} images)..."

for case_entry in "${VISION_CASES[@]}"; do
  IFS='|' read -r label image_url expected_keywords <<< "${case_entry}"
  image_path="${tmp_dir}/${label}.img"

  curl -fsSL -A "${HTTP_USER_AGENT}" --retry 4 --retry-delay 1 --retry-all-errors \
    "${image_url}" -o "${image_path}"

  if [ ! -s "${image_path}" ]; then
    echo "Downloaded image is empty for ${label}: ${image_url}" >&2
    exit 1
  fi

  mime="$(mime_type_for_file "${image_path}")"
  if [ "${mime#image/}" = "${mime}" ]; then
    echo "Downloaded file is not an image for ${label} (mime=${mime})" >&2
    exit 1
  fi

  image_b64="$(base64 < "${image_path}" | tr -d '\n')"
  data_url="data:${mime};base64,${image_b64}"

  payload="$(
    jq -nc \
      --arg model "qwen-local" \
      --arg prompt "${VISION_PROMPT}" \
      --arg image_url "${data_url}" \
      --argjson max_tokens "${MAX_TOKENS}" \
      '{
        model: $model,
        stream: false,
        max_tokens: $max_tokens,
        messages: [
          {
            role: "user",
            content: [
              {type: "text", text: $prompt},
              {type: "image_url", image_url: {url: $image_url}}
            ]
          }
        ]
      }'
  )"

  response="$(curl -sS --max-time "${VISION_TIMEOUT_SECONDS}" \
    -X POST "${BASE_URL}/v1/chat/completions" \
    -H 'content-type: application/json' \
    -H 'accept: application/json' \
    -d "${payload}")"

  if ! printf '%s' "${response}" | jq -e . >/dev/null 2>&1; then
    echo "Invalid JSON response for ${label}" >&2
    echo "${response}" >&2
    exit 1
  fi

  assistant_text="$(printf '%s' "${response}" | jq -r '.choices[0].message.content // empty')"
  if [ -z "${assistant_text}" ]; then
    echo "Empty assistant response for ${label}" >&2
    echo "${response}" >&2
    exit 1
  fi

  keyword_match=0
  IFS=',' read -r -a keywords <<< "${expected_keywords}"
  for keyword in "${keywords[@]}"; do
    if printf '%s' "${assistant_text}" | grep -Fqi "${keyword}"; then
      keyword_match=1
      break
    fi
  done

  if [ "${keyword_match}" -ne 1 ]; then
    echo "Response for ${label} did not include expected keywords (${expected_keywords})." >&2
    echo "Response: ${assistant_text}" >&2
    exit 1
  fi

  success_count=$((success_count + 1))
  if [ "${SHOW_RESPONSES}" = "1" ]; then
    echo "${label}: ${assistant_text}"
  else
    echo "${label}: ok"
  fi
done

echo "Vision E2E passed on ${PI_HOST} (${success_count}/${#VISION_CASES[@]} images)."
