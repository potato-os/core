#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_PASSWORD="${PI_PASSWORD:-raspberry}"
PI_HOST_PRIMARY="${PI_HOST_PRIMARY:-192.168.1.132}"
PI_HOST_FALLBACK="${PI_HOST_FALLBACK:-192.168.1.131}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXPECT_BACKEND="${EXPECT_BACKEND:-llama}"
LLAMA_BUNDLE_ROOT="${LLAMA_BUNDLE_ROOT:-${PROJECT_ROOT}/references/old_reference_design/llama_cpp_binary}"
LLAMA_BUNDLE_SRC="${LLAMA_BUNDLE_SRC:-}"
WAIT_ATTEMPTS="${WAIT_ATTEMPTS:-120}"
WAIT_SECONDS="${WAIT_SECONDS:-5}"
WAIT_FOR_LLAMA_HEALTH="${WAIT_FOR_LLAMA_HEALTH:-1}"
PI_SCHEME="${PI_SCHEME:-http}"
PI_PORT="${PI_PORT:-80}"
PI_SSH_OPTIONS="${PI_SSH_OPTIONS:--o StrictHostKeyChecking=accept-new}"
RSYNC_PROGRESS="${RSYNC_PROGRESS:-1}"
STATUS_POLL_TIMEOUT_SECONDS="${STATUS_POLL_TIMEOUT_SECONDS:-5}"
SHOW_REMOTE_DIAGNOSTICS="${SHOW_REMOTE_DIAGNOSTICS:-1}"
RUN_CHAT_SMOKE="${RUN_CHAT_SMOKE:-1}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1" >&2
    exit 1
  fi
}

require_cmd sshpass
require_cmd rsync
require_cmd curl
require_cmd jq

now_epoch() {
  date +%s
}

log_stage() {
  echo "[smoke] $*"
}

report_stage_time() {
  local label="$1"
  local started_at="$2"
  local finished_at
  finished_at="$(now_epoch)"
  log_stage "${label} completed in $((finished_at - started_at))s"
}

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

resolve_bundle_src() {
  if [ -n "${LLAMA_BUNDLE_SRC}" ]; then
    echo "${LLAMA_BUNDLE_SRC}"
    return
  fi
  if [ ! -d "${LLAMA_BUNDLE_ROOT}" ]; then
    echo ""
    return
  fi
  find "${LLAMA_BUNDLE_ROOT}" -mindepth 1 -maxdepth 1 -type d -name 'llama_server_bundle_*' | sort | tail -n 1
}

PI_HOST="$(pick_host)"
if [ -z "${PI_HOST}" ]; then
  echo "No reachable Pi host found (${PI_HOST_PRIMARY}, ${PI_HOST_FALLBACK})." >&2
  exit 1
fi

TOTAL_STARTED_AT="$(now_epoch)"
log_stage "Using Pi host: ${PI_HOST}"
BASE_URL="${PI_SCHEME}://${PI_HOST}"
if [ -n "${PI_PORT}" ]; then
  BASE_URL="${BASE_URL}:${PI_PORT}"
fi

bundle_src="$(resolve_bundle_src)"
if [ -z "${bundle_src}" ] || [ ! -x "${bundle_src}/bin/llama-server" ] || [ ! -d "${bundle_src}/lib" ]; then
  echo "Missing llama bundle source. Set LLAMA_BUNDLE_SRC or ensure ${LLAMA_BUNDLE_ROOT}/llama_server_bundle_* exists." >&2
  exit 1
fi

rsync_flags=(-az --delete)
rsync_progress_flags=()
if [ "${RSYNC_PROGRESS}" = "1" ]; then
  if rsync --help 2>/dev/null | grep -q -- '--info='; then
    rsync_progress_flags+=(--info=progress2)
  else
    rsync_progress_flags+=(--progress)
  fi
fi

stage_started_at="$(now_epoch)"
log_stage "Syncing repository to Pi (excluding local heavy artifacts)..."
SSHPASS="${PI_PASSWORD}" sshpass -e rsync "${rsync_flags[@]}" "${rsync_progress_flags[@]}" \
  -e "ssh ${PI_SSH_OPTIONS}" \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude 'references/' \
  --exclude 'models/' --exclude 'node_modules/' --exclude 'output/' --exclude '.cache/' \
  --exclude 'test-results/' --exclude 'playwright-report/' --exclude '.pytest_cache/' \
  "${PROJECT_ROOT}/" "${PI_USER}@${PI_HOST}:/tmp/potato-os/"
report_stage_time "Repository sync" "${stage_started_at}"

stage_started_at="$(now_epoch)"
log_stage "Syncing llama server bundle to Pi..."
SSHPASS="${PI_PASSWORD}" sshpass -e rsync "${rsync_flags[@]}" "${rsync_progress_flags[@]}" \
  -e "ssh ${PI_SSH_OPTIONS}" \
  "${bundle_src}/" "${PI_USER}@${PI_HOST}:/tmp/potato-os/.llama_bundle/"
report_stage_time "Llama bundle sync" "${stage_started_at}"

read -r -a SSH_OPTION_ARGS <<< "${PI_SSH_OPTIONS}"
stage_started_at="$(now_epoch)"
log_stage "Running Pi install/update script..."
SSHPASS="${PI_PASSWORD}" sshpass -e ssh "${SSH_OPTION_ARGS[@]}" "${PI_USER}@${PI_HOST}" \
  "cd /tmp/potato-os && PI_PASSWORD='${PI_PASSWORD}' POTATO_LLAMA_BUNDLE_SRC='/tmp/potato-os/.llama_bundle' ./bin/install_dev.sh"
report_stage_time "Install/update" "${stage_started_at}"

status_json=""
wait_started_at="$(now_epoch)"
log_stage "Waiting for API readiness at ${BASE_URL}/status..."
for attempt in $(seq 1 "${WAIT_ATTEMPTS}"); do
  elapsed="$(( $(now_epoch) - wait_started_at ))"
  wait_pct=$(( attempt * 100 / WAIT_ATTEMPTS ))
  status_json="$(curl -sS --max-time "${STATUS_POLL_TIMEOUT_SECONDS}" "${BASE_URL}/status" 2>/dev/null || true)"
  if [ -z "${status_json}" ]; then
    log_stage "[wait ${wait_pct}%] attempt ${attempt}/${WAIT_ATTEMPTS}, elapsed ${elapsed}s: no response yet"
    sleep "${WAIT_SECONDS}"
    continue
  fi
  if ! printf '%s' "${status_json}" | jq -e . >/dev/null 2>&1; then
    log_stage "[wait ${wait_pct}%] attempt ${attempt}/${WAIT_ATTEMPTS}, elapsed ${elapsed}s: non-JSON response"
    sleep "${WAIT_SECONDS}"
    continue
  fi
  active_backend="$(printf '%s' "${status_json}" | jq -r '.backend.active // empty')"
  llama_healthy="$(printf '%s' "${status_json}" | jq -r '.llama_server.healthy // false')"
  log_stage "[wait ${wait_pct}%] attempt ${attempt}/${WAIT_ATTEMPTS}, elapsed ${elapsed}s: backend=${active_backend:-n/a}, llama_healthy=${llama_healthy}"
  if [ -n "${EXPECT_BACKEND}" ] && [ "${active_backend}" = "${EXPECT_BACKEND}" ]; then
    if [ "${EXPECT_BACKEND}" != "llama" ] || [ "${WAIT_FOR_LLAMA_HEALTH}" = "0" ] || [ "${llama_healthy}" = "true" ]; then
      break
    fi
  fi
  sleep "${WAIT_SECONDS}"
done

if [ -z "${status_json}" ]; then
  echo "Unable to reach status endpoint on ${PI_HOST}" >&2
  if [ "${SHOW_REMOTE_DIAGNOSTICS}" = "1" ]; then
    log_stage "Collecting remote diagnostics..."
    SSHPASS="${PI_PASSWORD}" sshpass -e ssh "${SSH_OPTION_ARGS[@]}" "${PI_USER}@${PI_HOST}" \
      "set +e; systemctl --no-pager --full status potato; echo '---'; journalctl -u potato -n 60 --no-pager" || true
  fi
  exit 1
fi

echo "${status_json}"

active_backend="$(printf '%s' "${status_json}" | jq -r '.backend.active // empty')"
if [ -n "${EXPECT_BACKEND}" ] && [ "${active_backend}" != "${EXPECT_BACKEND}" ]; then
  echo "Expected backend '${EXPECT_BACKEND}', got '${active_backend}'" >&2
  exit 1
fi

if [ "${EXPECT_BACKEND}" = "llama" ]; then
  llama_healthy="$(printf '%s' "${status_json}" | jq -r '.llama_server.healthy // false')"
  if [ "${WAIT_FOR_LLAMA_HEALTH}" = "1" ] && [ "${llama_healthy}" != "true" ]; then
    echo "llama backend expected but not healthy" >&2
    exit 1
  fi
fi

if [ "${RUN_CHAT_SMOKE}" = "1" ]; then
  curl --fail --retry 20 --retry-delay 2 --retry-connrefused --retry-all-errors -X POST "${BASE_URL}/v1/chat/completions" \
    -H 'content-type: application/json' \
    -d '{"model":"qwen-local","stream":false,"max_tokens":32,"messages":[{"role":"user","content":"ping"}]}'
else
  log_stage "RUN_CHAT_SMOKE=0, skipping /v1/chat/completions smoke call."
fi

report_stage_time "Readiness wait + smoke check" "${wait_started_at}"
log_stage "Smoke checks completed for ${PI_HOST} in $(( $(now_epoch) - TOTAL_STARTED_AT ))s total"
