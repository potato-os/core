#!/usr/bin/env bash
set -euo pipefail

REMOVE_NODEJS="${REMOVE_NODEJS:-0}"
OPENCLAW_SKILLS_TO_DISABLE="healthcheck node-connect skill-creator weather"

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

printf '=== OpenClaw uninstaller for Potato OS ===\n\n'

# ---------------------------------------------------------------------------
# Phase 1: Stop and remove systemd service
# ---------------------------------------------------------------------------

printf '[1/5] Stopping OpenClaw gateway...\n'
systemctl --user disable --now openclaw-gateway 2>/dev/null || true
rm -f "${HOME}/.config/systemd/user/openclaw-gateway.service"
systemctl --user daemon-reload 2>/dev/null || true

# ---------------------------------------------------------------------------
# Phase 2: Restore disabled skills
# ---------------------------------------------------------------------------

printf '[2/5] Restoring disabled skills...\n'
OPENCLAW_SKILLS_DIR="$(npm root -g 2>/dev/null)/openclaw/skills"
if [ -d "${OPENCLAW_SKILLS_DIR}" ]; then
  for skill in ${OPENCLAW_SKILLS_TO_DISABLE}; do
    if [ -f "${OPENCLAW_SKILLS_DIR}/${skill}/SKILL.md.disabled" ]; then
      run_sudo mv "${OPENCLAW_SKILLS_DIR}/${skill}/SKILL.md.disabled" \
                  "${OPENCLAW_SKILLS_DIR}/${skill}/SKILL.md"
      printf '  restored: %s\n' "${skill}"
    fi
  done
fi

# ---------------------------------------------------------------------------
# Phase 3: Remove OpenClaw
# ---------------------------------------------------------------------------

printf '[3/5] Removing OpenClaw...\n'
if command -v openclaw >/dev/null 2>&1; then
  run_sudo npm uninstall -g openclaw || true
fi

# ---------------------------------------------------------------------------
# Phase 4: Remove config and state
# ---------------------------------------------------------------------------

printf '[4/5] Removing OpenClaw config and state...\n'
rm -rf "${HOME}/.openclaw"

# Remove performance vars from .bashrc
if [ -f "${HOME}/.bashrc" ]; then
  sed -i '/# OpenClaw performance (added by install_openclaw.sh)/d' "${HOME}/.bashrc"
  sed -i '/NODE_COMPILE_CACHE=\/var\/tmp\/openclaw-compile-cache/d' "${HOME}/.bashrc"
  sed -i '/OPENCLAW_NO_RESPAWN=1/d' "${HOME}/.bashrc"
fi

# ---------------------------------------------------------------------------
# Phase 5: Optionally remove Node.js
# ---------------------------------------------------------------------------

if [ "${REMOVE_NODEJS}" = "1" ]; then
  printf '[5/5] Removing Node.js...\n'
  run_sudo apt-get remove --purge -y nodejs || true
  run_sudo apt-get autoremove -y || true
else
  printf '[5/5] Keeping Node.js (set REMOVE_NODEJS=1 to remove).\n'
fi

printf '\nOpenClaw removed.\n'
