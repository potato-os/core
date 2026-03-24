#!/usr/bin/env bash
# Self-contained OpenClaw uninstaller for Potato OS.
# Designed to be run as: curl -fsSL <raw-url> | sudo bash
set -euo pipefail

REMOVE_NODEJS="${REMOVE_NODEJS:-0}"

# ── User detection ─────────────────────────────────────────────────────────────
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || whoami)}"
REAL_HOME="$(eval echo "~${REAL_USER}")"

if [ "$(id -u)" -ne 0 ]; then
  printf 'Error: this script must be run as root (use sudo).\n' >&2
  exit 1
fi

printf '=== OpenClaw uninstaller for Potato OS ===\n'
printf 'User: %s  Home: %s\n\n' "${REAL_USER}" "${REAL_HOME}"

# ── Phase 1: Stop and remove systemd service ──────────────────────────────────

printf '[1/5] Stopping OpenClaw gateway...\n'
REAL_UID="$(id -u "${REAL_USER}")"
_run_as_user() {
  sudo -u "${REAL_USER}" \
    XDG_RUNTIME_DIR="/run/user/${REAL_UID}" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${REAL_UID}/bus" \
    "$@"
}
_run_as_user systemctl --user disable --now openclaw-gateway 2>/dev/null || true
rm -f "${REAL_HOME}/.config/systemd/user/openclaw-gateway.service"
_run_as_user systemctl --user daemon-reload 2>/dev/null || true

# ── Phase 2: Restore disabled skills ──────────────────────────────────────────

printf '[2/5] Restoring disabled skills...\n'
SKILLS_DIR="$(npm root -g 2>/dev/null)/openclaw/skills"
RESTORED=0
if [ -d "${SKILLS_DIR}" ]; then
  while IFS= read -r disabled_file; do
    mv "${disabled_file}" "${disabled_file%.disabled}"
    RESTORED=$((RESTORED + 1))
  done < <(find "${SKILLS_DIR}" -name "SKILL.md.disabled" 2>/dev/null)
fi
printf '  restored %d skills\n' "${RESTORED}"

# ── Phase 3: Remove OpenClaw ──────────────────────────────────────────────────

printf '[3/5] Removing OpenClaw...\n'
if command -v openclaw >/dev/null 2>&1; then
  npm uninstall -g openclaw || true
fi

# ── Phase 4: Remove config and state ──────────────────────────────────────────

printf '[4/5] Removing OpenClaw config and state...\n'
rm -rf "${REAL_HOME}/.openclaw"

# Remove performance vars from .bashrc
BASHRC="${REAL_HOME}/.bashrc"
if [ -f "${BASHRC}" ]; then
  sed -i '/# OpenClaw performance (added by install_openclaw.sh)/d' "${BASHRC}"
  sed -i '/NODE_COMPILE_CACHE=\/var\/tmp\/openclaw-compile-cache/d' "${BASHRC}"
  sed -i '/OPENCLAW_NO_RESPAWN=1/d' "${BASHRC}"
fi

rm -rf /var/tmp/openclaw-compile-cache

# ── Phase 5: Optionally remove Node.js ────────────────────────────────────────

if [ "${REMOVE_NODEJS}" = "1" ]; then
  printf '[5/5] Removing Node.js...\n'
  apt-get remove --purge -y nodejs || true
  apt-get autoremove -y || true
else
  printf '[5/5] Keeping Node.js (set REMOVE_NODEJS=1 to remove).\n'
fi

printf '\nOpenClaw removed.\n'
