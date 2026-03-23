#!/usr/bin/env bash
set -euo pipefail

# End-to-end OTA update test on a real Pi.
#
# Exercises the full download → stage → apply → restart cycle using
# a local HTTP server to serve a test tarball. No GitHub publish needed.
#
# Usage:
#   ./tests/e2e/ota_update_pi.sh                # happy-path update
#   ./tests/e2e/ota_update_pi.sh --test-failure  # pip-failure rollback test

PI_USER="${PI_USER:-pi}"
PI_PASSWORD="${PI_PASSWORD:-raspberry}"
PI_HOST_PRIMARY="${PI_HOST_PRIMARY:-potato.local}"
PI_HOST_FALLBACK="${PI_HOST_FALLBACK:-potato.local}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PI_SSH_OPTIONS="${PI_SSH_OPTIONS:--o StrictHostKeyChecking=accept-new}"
HTTP_SERVER_PORT="${OTA_HTTP_PORT:-9876}"
WAIT_ATTEMPTS="${OTA_WAIT_ATTEMPTS:-90}"
WAIT_SECONDS="${OTA_WAIT_SECONDS:-2}"
STATUS_POLL_TIMEOUT_SECONDS="${STATUS_POLL_TIMEOUT_SECONDS:-5}"
TEST_VERSION="${OTA_TEST_VERSION:-0.4.1-ota-test}"
TEST_FAILURE_MODE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --test-failure) TEST_FAILURE_MODE=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

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
require_cmd python3

now_epoch() { date +%s; }

log_stage() { echo "[ota-e2e] $*"; }

report_stage_time() {
  local label="$1" started_at="$2"
  log_stage "${label} completed in $(( $(now_epoch) - started_at ))s"
}

pick_host() {
  if ping -c 1 -W 1 "${PI_HOST_PRIMARY}" >/dev/null 2>&1; then
    echo "${PI_HOST_PRIMARY}"; return
  fi
  if ping -c 1 -W 1 "${PI_HOST_FALLBACK}" >/dev/null 2>&1; then
    echo "${PI_HOST_FALLBACK}"; return
  fi
  echo ""
}

get_local_ip() {
  # macOS
  if command -v ipconfig >/dev/null 2>&1; then
    ipconfig getifaddr en0 2>/dev/null && return
  fi
  # Linux
  if command -v hostname >/dev/null 2>&1; then
    hostname -I 2>/dev/null | awk '{print $1}' && return
  fi
  echo ""
}

ssh_pi() {
  read -r -a SSH_OPTS <<< "${PI_SSH_OPTIONS}"
  SSHPASS="${PI_PASSWORD}" sshpass -e ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "$@"
}

rsync_to_pi() {
  read -r -a SSH_OPTS <<< "${PI_SSH_OPTIONS}"
  SSHPASS="${PI_PASSWORD}" sshpass -e rsync -az --delete \
    -e "ssh ${PI_SSH_OPTIONS}" "$@"
}

pi_status() {
  curl -sS --max-time "${STATUS_POLL_TIMEOUT_SECONDS}" "http://${PI_HOST}/status" 2>/dev/null || true
}

pi_status_field() {
  local json="$1" field="$2"
  printf '%s' "${json}" | jq -r "${field}" 2>/dev/null || echo ""
}

# ── Cleanup trap ──────────────────────────────────────────────────────

STAGING_DIR=""
HTTP_SERVER_PID=""

PI_STATE_DIRTY=0

cleanup() {
  local exit_code=$?
  log_stage "Cleaning up..."

  # Kill local HTTP server
  if [ -n "${HTTP_SERVER_PID}" ]; then
    kill "${HTTP_SERVER_PID}" 2>/dev/null || true
    wait "${HTTP_SERVER_PID}" 2>/dev/null || true
  fi

  # Always restore Pi to pre-test state if we touched it
  if [ "${PI_STATE_DIRTY}" = "1" ] && [ -n "${PI_HOST}" ]; then
    log_stage "Restoring Pi to pre-test state (cleanup trap)..."
    _restore_pi || log_stage "WARNING: Pi restore failed during cleanup"
  fi

  # Remove local staging
  if [ -n "${STAGING_DIR}" ] && [ -d "${STAGING_DIR}" ]; then
    rm -rf "${STAGING_DIR}"
  fi

  exit "${exit_code}"
}
trap cleanup EXIT

_sudo_pi() {
  # Run a command on the Pi with sudo, using PI_PASSWORD
  ssh_pi "echo '${PI_PASSWORD}' | sudo -S $*"
}

_restore_pi() {
  # Restore app/ and bin/ from the local checkout and reset update.json.
  # This is called from both Phase 8 and the cleanup trap.

  # Fix ownership so rsync can overwrite
  _sudo_pi "chown -R ${PI_USER}:${PI_USER} /opt/potato/app /opt/potato/bin 2>/dev/null" || true
  _sudo_pi "find /opt/potato/app /opt/potato/bin -name '__pycache__' -exec rm -rf {} + 2>/dev/null" || true

  # Restore both app/ AND bin/
  rsync_to_pi "${PROJECT_ROOT}/app/" "${PI_USER}@${PI_HOST}:/opt/potato/app/"
  rsync_to_pi "${PROJECT_ROOT}/bin/" "${PI_USER}@${PI_HOST}:/opt/potato/bin/"

  # Reset update.json to clean idle state (remove test seed and any stale execution state)
  ssh_pi "cat > /tmp/ota_clean_state.json <<'CLEANEOF'
{\"available\":false,\"current_version\":\"${ORIGINAL_VERSION}\",\"latest_version\":null,\"release_notes\":null,\"release_url\":null,\"tarball_url\":null,\"checked_at_unix\":null,\"error\":null}
CLEANEOF
"
  _sudo_pi "cp /tmp/ota_clean_state.json /opt/potato/state/update.json"
  _sudo_pi "chown potato:potato /opt/potato/state/update.json"
  ssh_pi "rm -f /tmp/ota_clean_state.json"

  # Restart service
  _sudo_pi "systemctl restart potato"

  # Wait for service with the original version
  log_stage "  Waiting for service to come back with version ${ORIGINAL_VERSION}..."
  local restored_ver=""
  for attempt in $(seq 1 30); do
    local sj
    sj="$(pi_status)"
    if [ -n "${sj}" ]; then
      restored_ver="$(pi_status_field "${sj}" '.version // "unknown"')"
      if [ "${restored_ver}" = "${ORIGINAL_VERSION}" ]; then
        log_stage "  Restored to version ${restored_ver}"
        PI_STATE_DIRTY=0
        return 0
      fi
    fi
    sleep 2
  done

  log_stage "  FATAL: Could not restore Pi to version ${ORIGINAL_VERSION} (got: ${restored_ver:-no response})"
  return 1
}

# ── Resolve Pi host ───────────────────────────────────────────────────

PI_HOST="$(pick_host)"
if [ -z "${PI_HOST}" ]; then
  echo "No reachable Pi host found." >&2
  exit 1
fi
log_stage "Pi host: ${PI_HOST}"

LOCAL_IP="$(get_local_ip)"
if [ -z "${LOCAL_IP}" ]; then
  echo "Could not determine local IP address." >&2
  exit 1
fi
log_stage "Local IP: ${LOCAL_IP}"

TOTAL_STARTED_AT="$(now_epoch)"

# ── Phase 1: Record current state ────────────────────────────────────

stage_started_at="$(now_epoch)"
log_stage "Phase 1: Recording current Pi state..."

status_json="$(pi_status)"
if [ -z "${status_json}" ] || ! printf '%s' "${status_json}" | jq -e . >/dev/null 2>&1; then
  echo "Pi not responding at http://${PI_HOST}/status" >&2
  exit 1
fi

ORIGINAL_VERSION="$(pi_status_field "${status_json}" '.version // empty')"
log_stage "Current version: ${ORIGINAL_VERSION}"
report_stage_time "Phase 1 (record state)" "${stage_started_at}"

# ── Phase 2: Create test tarball ──────────────────────────────────────

stage_started_at="$(now_epoch)"
log_stage "Phase 2: Creating test tarball (version ${TEST_VERSION})..."

STAGING_DIR="$(mktemp -d)"
ARCHIVE_NAME="potato-os-${TEST_VERSION}"
PREFIX="${STAGING_DIR}/${ARCHIVE_NAME}"
mkdir -p "${PREFIX}"

cp -a "${PROJECT_ROOT}/app" "${PREFIX}/app"
cp -a "${PROJECT_ROOT}/bin" "${PREFIX}/bin"
if [ -f "${PROJECT_ROOT}/requirements.txt" ]; then
  cp "${PROJECT_ROOT}/requirements.txt" "${PREFIX}/requirements.txt"
fi

# Bump version in the tarball
cat > "${PREFIX}/app/__version__.py" <<PYEOF
"""Canonical version for Potato OS — the single source of truth."""

__version__ = "${TEST_VERSION}"
PYEOF

# Inject pip failure if testing rollback
if [ "${TEST_FAILURE_MODE}" = "1" ]; then
  log_stage "  (injecting pip failure: bad requirements.txt)"
  printf 'nonexistent-ota-test-package==99.99.99\n' >> "${PREFIX}/requirements.txt"
fi

TARBALL_NAME="${ARCHIVE_NAME}.tar.gz"
TARBALL_PATH="${STAGING_DIR}/${TARBALL_NAME}"
tar -C "${STAGING_DIR}" -czf "${TARBALL_PATH}" \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' --exclude='._*' \
  "${ARCHIVE_NAME}"

TARBALL_SIZE="$(wc -c < "${TARBALL_PATH}" | tr -d ' ')"
log_stage "  Tarball: ${TARBALL_NAME} (${TARBALL_SIZE} bytes)"
report_stage_time "Phase 2 (create tarball)" "${stage_started_at}"

# ── Phase 3: Start HTTP server ────────────────────────────────────────

stage_started_at="$(now_epoch)"
log_stage "Phase 3: Starting HTTP server on ${LOCAL_IP}:${HTTP_SERVER_PORT}..."

python3 -m http.server "${HTTP_SERVER_PORT}" --directory "${STAGING_DIR}" --bind 0.0.0.0 >/dev/null 2>&1 &
HTTP_SERVER_PID=$!
sleep 1

if ! kill -0 "${HTTP_SERVER_PID}" 2>/dev/null; then
  echo "HTTP server failed to start." >&2
  exit 1
fi

TARBALL_URL="http://${LOCAL_IP}:${HTTP_SERVER_PORT}/${TARBALL_NAME}"
log_stage "  Serving at: ${TARBALL_URL}"
report_stage_time "Phase 3 (start server)" "${stage_started_at}"

# ── Phase 4: Seed update state on Pi ──────────────────────────────────

# Ensure the potato user owns its directories (may be root-owned after dev rsync)
log_stage "Fixing /opt/potato ownership for potato user..."
_sudo_pi "chown -R potato:potato /opt/potato/app /opt/potato/bin /opt/potato/state 2>/dev/null" || true

stage_started_at="$(now_epoch)"
log_stage "Phase 4: Seeding update.json on Pi..."

UPDATE_JSON="$(cat <<JEOF
{
  "available": true,
  "current_version": "${ORIGINAL_VERSION}",
  "latest_version": "${TEST_VERSION}",
  "release_notes": "OTA e2e test tarball",
  "release_url": null,
  "tarball_url": "${TARBALL_URL}",
  "checked_at_unix": $(date +%s),
  "error": null
}
JEOF
)"

ssh_pi "cat > /tmp/ota_test_update.json <<'SEEDEOF'
${UPDATE_JSON}
SEEDEOF
"
_sudo_pi "cp /tmp/ota_test_update.json /opt/potato/state/update.json"
_sudo_pi "chown potato:potato /opt/potato/state/update.json"
ssh_pi "rm -f /tmp/ota_test_update.json"
PI_STATE_DIRTY=1
log_stage "  Seeded tarball_url=${TARBALL_URL}"
report_stage_time "Phase 4 (seed state)" "${stage_started_at}"

# ── Phase 5: Trigger update ───────────────────────────────────────────

stage_started_at="$(now_epoch)"
log_stage "Phase 5: Triggering OTA update..."

start_response="$(curl -sS -X POST "http://${PI_HOST}/internal/update/start" \
  -H 'content-type: application/json' 2>/dev/null || true)"

started="$(printf '%s' "${start_response}" | jq -r '.started // false')"
if [ "${started}" != "true" ]; then
  reason="$(printf '%s' "${start_response}" | jq -r '.reason // "unknown"')"
  echo "Update start failed: ${reason}" >&2
  echo "Response: ${start_response}" >&2
  exit 1
fi

log_stage "  Update started successfully"
report_stage_time "Phase 5 (trigger)" "${stage_started_at}"

# ── Phase 6: Poll for completion ──────────────────────────────────────

stage_started_at="$(now_epoch)"
log_stage "Phase 6: Polling for update completion..."

final_state=""
for attempt in $(seq 1 "${WAIT_ATTEMPTS}"); do
  status_json="$(pi_status)"
  if [ -z "${status_json}" ]; then
    log_stage "  [${attempt}/${WAIT_ATTEMPTS}] No response (service restarting?)"
    sleep "${WAIT_SECONDS}"
    continue
  fi

  update_state="$(pi_status_field "${status_json}" '.update.state // "unknown"')"
  update_percent="$(pi_status_field "${status_json}" '.update.progress.percent // 0')"
  update_phase="$(pi_status_field "${status_json}" '.update.progress.phase // "none"')"
  update_error="$(pi_status_field "${status_json}" '.update.progress.error // "none"')"
  current_ver="$(pi_status_field "${status_json}" '.version // "unknown"')"

  log_stage "  [${attempt}/${WAIT_ATTEMPTS}] state=${update_state} phase=${update_phase} percent=${update_percent}% version=${current_ver}"

  if [ "${update_state}" = "idle" ] && [ "${attempt}" -gt 3 ]; then
    final_state="idle"
    break
  fi
  if [ "${update_state}" = "failed" ]; then
    final_state="failed"
    log_stage "  Error: ${update_error}"
    break
  fi

  sleep "${WAIT_SECONDS}"
done

if [ -z "${final_state}" ]; then
  echo "Timed out waiting for update to complete." >&2
  exit 1
fi

report_stage_time "Phase 6 (poll completion)" "${stage_started_at}"

# ── Phase 7: Verify result ────────────────────────────────────────────

stage_started_at="$(now_epoch)"
log_stage "Phase 7: Verifying result..."

status_json="$(pi_status)"
final_version="$(pi_status_field "${status_json}" '.version // "unknown"')"

if [ "${TEST_FAILURE_MODE}" = "1" ]; then
  # Failure test: expect failed state and original version preserved
  if [ "${final_state}" != "failed" ]; then
    echo "FAIL: Expected state=failed but got state=${final_state}" >&2
    exit 1
  fi
  if [ "${final_version}" != "${ORIGINAL_VERSION}" ]; then
    echo "FAIL: Expected version=${ORIGINAL_VERSION} after rollback but got version=${final_version}" >&2
    exit 1
  fi
  log_stage "  PASS: Update failed as expected, version preserved at ${final_version}"
else
  # Happy path: expect idle state and new version
  if [ "${final_state}" != "idle" ]; then
    echo "FAIL: Expected state=idle but got state=${final_state}" >&2
    exit 1
  fi
  if [ "${final_version}" != "${TEST_VERSION}" ]; then
    echo "FAIL: Expected version=${TEST_VERSION} but got version=${final_version}" >&2
    exit 1
  fi
  log_stage "  PASS: Update succeeded, version is now ${final_version}"
fi

report_stage_time "Phase 7 (verify)" "${stage_started_at}"

# ── Phase 8: Restore Pi to pre-test state ─────────────────────────────

stage_started_at="$(now_epoch)"
log_stage "Phase 8: Restoring Pi to pre-test state..."

if ! _restore_pi; then
  echo "FATAL: Could not restore Pi to original state. Manual intervention required." >&2
  echo "  Expected version: ${ORIGINAL_VERSION}" >&2
  echo "  Run: sshpass -e rsync -az --delete -e 'ssh ${PI_SSH_OPTIONS}' app/ ${PI_USER}@${PI_HOST}:/opt/potato/app/" >&2
  echo "  Run: sshpass -e rsync -az --delete -e 'ssh ${PI_SSH_OPTIONS}' bin/ ${PI_USER}@${PI_HOST}:/opt/potato/bin/" >&2
  exit 1
fi
report_stage_time "Phase 8 (restore)" "${stage_started_at}"

# ── Done ──────────────────────────────────────────────────────────────

MODE_LABEL="happy path"
if [ "${TEST_FAILURE_MODE}" = "1" ]; then
  MODE_LABEL="failure/rollback"
fi

log_stage "OTA e2e test (${MODE_LABEL}) PASSED in $(( $(now_epoch) - TOTAL_STARTED_AT ))s total"
