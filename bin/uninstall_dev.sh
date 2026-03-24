#!/usr/bin/env bash
set -euo pipefail

TARGET_ROOT="${POTATO_TARGET_ROOT:-/opt/potato}"
POTATO_USER="${POTATO_USER:-potato}"
POTATO_GROUP="${POTATO_GROUP:-potato}"
REMOVE_PACKAGES="${REMOVE_PACKAGES:-0}"

run_sudo() {
  if [ "${EUID}" -eq 0 ]; then
    "$@"
    return
  fi
  if [ -n "${PI_PASSWORD:-}" ]; then
    printf '%s\n' "${PI_PASSWORD}" | sudo -S -p '' "$@"
    return
  fi
  sudo "$@"
}

# Clean up OpenClaw addon if installed — target the real user (pi), not root.
OPENCLAW_USER="${SUDO_USER:-$(logname 2>/dev/null || whoami)}"
OPENCLAW_HOME="$(eval echo "~${OPENCLAW_USER}")"
if command -v openclaw &>/dev/null; then
  OC_UID="$(id -u "${OPENCLAW_USER}" 2>/dev/null || echo 1000)"
  sudo -u "${OPENCLAW_USER}" XDG_RUNTIME_DIR="/run/user/${OC_UID}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${OC_UID}/bus" \
    systemctl --user disable --now openclaw-gateway 2>/dev/null || true
  rm -f "${OPENCLAW_HOME}/.config/systemd/user/openclaw-gateway.service"
  sudo -u "${OPENCLAW_USER}" XDG_RUNTIME_DIR="/run/user/${OC_UID}" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${OC_UID}/bus" \
    systemctl --user daemon-reload 2>/dev/null || true
  rm -rf "${OPENCLAW_HOME}/.openclaw"
fi

run_sudo systemctl disable --now potato.service potato-firstboot.service potato-runtime-reset.service || true
run_sudo rm -f /etc/systemd/system/potato.service /etc/systemd/system/potato-firstboot.service /etc/systemd/system/potato-runtime-reset.service
run_sudo rm -f /etc/sudoers.d/potato-runtime-reset
run_sudo rm -f /etc/nginx/sites-enabled/potato /etc/nginx/sites-available/potato
run_sudo systemctl disable --now nginx || true
run_sudo systemctl daemon-reload

run_sudo rm -rf "${TARGET_ROOT}" /tmp/potato-os

if id -u "${POTATO_USER}" >/dev/null 2>&1; then
  run_sudo userdel "${POTATO_USER}" || true
fi

if getent group "${POTATO_GROUP}" >/dev/null 2>&1; then
  run_sudo groupdel "${POTATO_GROUP}" || true
fi

if [ "${REMOVE_PACKAGES}" = "1" ]; then
  run_sudo apt-get remove --purge -y avahi-daemon nginx jq || true
  run_sudo apt-get autoremove -y || true
fi

printf 'Pi rollback complete. Local Mac workspace was not modified.\n'
