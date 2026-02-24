#!/usr/bin/env bash
set -euo pipefail

POTATO_SERVICE="${POTATO_SERVICE:-potato.service}"
RESET_DELAY_SECONDS="${POTATO_RESET_DELAY_SECONDS:-1}"
RECLAIM_SWAP="${POTATO_RESET_RECLAIM_SWAP:-1}"
DROP_CACHES="${POTATO_RESET_DROP_CACHES:-1}"

if [ "${EUID}" -ne 0 ]; then
  echo "ERROR: bin/reset_runtime.sh must run as root" >&2
  exit 1
fi

echo "[potato-reset] stopping ${POTATO_SERVICE}"
systemctl stop "${POTATO_SERVICE}" || true

if [ "${RESET_DELAY_SECONDS}" -gt 0 ] 2>/dev/null; then
  sleep "${RESET_DELAY_SECONDS}"
fi

if [ "${DROP_CACHES}" = "1" ]; then
  sync || true
  if [ -w /proc/sys/vm/drop_caches ]; then
    echo 3 > /proc/sys/vm/drop_caches || true
  fi
fi

if [ "${RECLAIM_SWAP}" = "1" ]; then
  swapoff -a || true
  if [ -x /sbin/swapon ]; then
    /sbin/swapon -a || true
  elif command -v swapon >/dev/null 2>&1; then
    swapon -a || true
  fi
fi

systemctl restart systemd-zram-setup@zram0.service || true

echo "[potato-reset] starting ${POTATO_SERVICE}"
systemctl start "${POTATO_SERVICE}"
echo "[potato-reset] done"
