#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_ROOT="${POTATO_TARGET_ROOT:-/opt/potato}"
OPENCLAW_PORT="${OPENCLAW_PORT:-3080}"
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

# ---------------------------------------------------------------------------
# Phase 1: Prerequisites
# ---------------------------------------------------------------------------

if [ ! -d "${TARGET_ROOT}" ]; then
  printf 'Error: Potato OS not found at %s. Install Potato OS first.\n' "${TARGET_ROOT}" >&2
  exit 1
fi

if ! systemctl list-unit-files potato.service >/dev/null 2>&1; then
  printf 'Error: potato.service not found. Install Potato OS first.\n' >&2
  exit 1
fi

if [ "$(uname -m)" != "aarch64" ]; then
  printf 'Warning: expected aarch64, got %s. OpenClaw Pi support is ARM64 only.\n' "$(uname -m)" >&2
fi

printf '=== OpenClaw installer for Potato OS ===\n\n'

# ---------------------------------------------------------------------------
# Phase 2: Node.js 24
# ---------------------------------------------------------------------------

NEED_NODEJS=0
if ! command -v node >/dev/null 2>&1; then
  NEED_NODEJS=1
elif [ "$(node --version | sed 's/v//' | cut -d. -f1)" -lt 24 ]; then
  NEED_NODEJS=1
fi

if [ "${NEED_NODEJS}" = "1" ]; then
  printf '[1/8] Installing Node.js 24...\n'
  NODESOURCE_SCRIPT="$(mktemp)"
  curl -fsSL https://deb.nodesource.com/setup_24.x -o "${NODESOURCE_SCRIPT}"
  run_sudo bash "${NODESOURCE_SCRIPT}"
  rm -f "${NODESOURCE_SCRIPT}"
  run_sudo apt-get install -y nodejs
else
  printf '[1/8] Node.js %s already installed, skipping.\n' "$(node --version)"
fi

# lsof is required by OpenClaw gateway for stale-pid detection
if ! command -v lsof >/dev/null 2>&1; then
  run_sudo apt-get install -y lsof
fi

# ---------------------------------------------------------------------------
# Phase 3: OpenClaw
# ---------------------------------------------------------------------------

if ! command -v openclaw >/dev/null 2>&1; then
  printf '[2/8] Installing OpenClaw...\n'
  run_sudo npm install -g openclaw@latest
else
  printf '[2/8] OpenClaw %s already installed, skipping.\n' "$(openclaw --version 2>/dev/null | head -1)"
fi

# ---------------------------------------------------------------------------
# Phase 4: Deploy config
# ---------------------------------------------------------------------------

printf '[3/8] Deploying OpenClaw config...\n'
mkdir -p "${HOME}/.openclaw/workspace"

cp "${REPO_ROOT}/openclaw/openclaw.json" "${HOME}/.openclaw/openclaw.json"
cp "${REPO_ROOT}/openclaw/workspace/AGENTS.md" "${HOME}/.openclaw/workspace/AGENTS.md"
cp "${REPO_ROOT}/openclaw/workspace/SOUL.md" "${HOME}/.openclaw/workspace/SOUL.md"

# Create empty bootstrap files to prevent OpenClaw from generating defaults
for f in TOOLS.md IDENTITY.md USER.md HEARTBEAT.md BOOTSTRAP.md MEMORY.md; do
  : > "${HOME}/.openclaw/workspace/${f}"
done

# Generate a fresh gateway token
GATEWAY_TOKEN="$(openssl rand -hex 24)"
openclaw config set gateway.auth.token "${GATEWAY_TOKEN}" 2>/dev/null
openclaw config set gateway.mode local 2>/dev/null

# ---------------------------------------------------------------------------
# Phase 5: Disable bundled skills on disk
# ---------------------------------------------------------------------------

printf '[4/8] Disabling bundled skills...\n'
OPENCLAW_SKILLS_DIR="$(npm root -g)/openclaw/skills"
for skill in ${OPENCLAW_SKILLS_TO_DISABLE}; do
  if [ -f "${OPENCLAW_SKILLS_DIR}/${skill}/SKILL.md" ]; then
    run_sudo mv "${OPENCLAW_SKILLS_DIR}/${skill}/SKILL.md" \
                "${OPENCLAW_SKILLS_DIR}/${skill}/SKILL.md.disabled"
    printf '  disabled: %s\n' "${skill}"
  fi
done

# ---------------------------------------------------------------------------
# Phase 6: Performance tuning
# ---------------------------------------------------------------------------

printf '[5/8] Configuring performance optimizations...\n'
mkdir -p /var/tmp/openclaw-compile-cache

if ! grep -q 'NODE_COMPILE_CACHE' "${HOME}/.bashrc" 2>/dev/null; then
  cat >> "${HOME}/.bashrc" <<'PERF'
# OpenClaw performance (added by install_openclaw.sh)
export NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache
export OPENCLAW_NO_RESPAWN=1
PERF
fi

# ---------------------------------------------------------------------------
# Phase 7: Systemd setup
# ---------------------------------------------------------------------------

printf '[6/8] Setting up systemd gateway service...\n'
run_sudo loginctl enable-linger "$(whoami)"

# Let OpenClaw create the service unit
openclaw gateway install 2>/dev/null || true

SERVICE_FILE="${HOME}/.config/systemd/user/openclaw-gateway.service"
if [ -f "${SERVICE_FILE}" ]; then
  # Patch port from default 18789 to configured port
  sed -i "s/--port 18789/--port ${OPENCLAW_PORT}/g" "${SERVICE_FILE}"
  sed -i "s/OPENCLAW_GATEWAY_PORT=18789/OPENCLAW_GATEWAY_PORT=${OPENCLAW_PORT}/g" "${SERVICE_FILE}"

  # Add performance env vars if not already present
  if ! grep -q 'NODE_COMPILE_CACHE' "${SERVICE_FILE}"; then
    sed -i "/\[Service\]/a Environment=NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache\nEnvironment=OPENCLAW_NO_RESPAWN=1" "${SERVICE_FILE}"
  fi

  systemctl --user daemon-reload
  systemctl --user enable openclaw-gateway
fi

# ---------------------------------------------------------------------------
# Phase 8: Start and verify
# ---------------------------------------------------------------------------

printf '[7/8] Starting OpenClaw gateway (this takes ~55s on Pi)...\n'
systemctl --user restart openclaw-gateway
sleep 55

if ss -tlnp 2>/dev/null | grep -q ":${OPENCLAW_PORT}"; then
  printf '[8/8] OpenClaw is running!\n\n'
else
  printf '[8/8] Gateway may still be starting. Check with: ss -tlnp | grep %s\n\n' "${OPENCLAW_PORT}"
fi

DASHBOARD_URL="$(openclaw dashboard --no-open 2>/dev/null | grep -oE 'http://[^ ]+' | head -1 || true)"
if [ -n "${DASHBOARD_URL}" ]; then
  # Replace localhost with potato.local for LAN access
  LAN_URL="$(printf '%s' "${DASHBOARD_URL}" | sed "s|127.0.0.1|potato.local|g")"
  printf 'Dashboard: %s\n' "${LAN_URL}"
else
  printf 'Dashboard: http://potato.local:%s/#token=%s\n' "${OPENCLAW_PORT}" "${GATEWAY_TOKEN}"
fi

printf 'Test:      openclaw agent --local --agent main --message "hi"\n'
printf '\nNote: gateway takes ~55s to start on Pi after reboot.\n'
