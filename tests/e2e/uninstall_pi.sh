#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_PASSWORD="${PI_PASSWORD:-raspberry}"
PI_HOST_PRIMARY="${PI_HOST_PRIMARY:-potato.local}"
PI_HOST_FALLBACK="${PI_HOST_FALLBACK:-potato.local}"
REMOVE_PACKAGES="${REMOVE_PACKAGES:-0}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing command: $1" >&2
    exit 1
  fi
}

require_cmd sshpass
require_cmd scp

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

echo "Using Pi host: ${PI_HOST}"

SSHPASS="${PI_PASSWORD}" sshpass -e scp -o StrictHostKeyChecking=accept-new \
  "${PROJECT_ROOT}/bin/uninstall_dev.sh" "${PI_USER}@${PI_HOST}:/tmp/uninstall_dev.sh"

SSHPASS="${PI_PASSWORD}" sshpass -e ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" \
  "chmod +x /tmp/uninstall_dev.sh && PI_PASSWORD='${PI_PASSWORD}' REMOVE_PACKAGES='${REMOVE_PACKAGES}' /tmp/uninstall_dev.sh"

echo "Pi rollback completed on ${PI_HOST}"
