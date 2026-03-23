#!/usr/bin/env bash
# Self-contained OpenClaw installer for Potato OS.
# Designed to be run as: curl -fsSL <raw-url> | sudo bash
#
# All config is embedded — no external files needed.
# Context budget is tuned for 16k local models, overridable via env vars.
set -euo pipefail

# ── Constants ──────────────────────────────────────────────────────────────────
OPENCLAW_VERSION="${POTATO_OPENCLAW_VERSION:-2026.3.22}"
OPENCLAW_PORT=18789
REQUIRED_NODE_MAJOR=22
TARGET_NODE_MAJOR=24
TARGET_ROOT="${POTATO_TARGET_ROOT:-/opt/potato}"

# ── Configurable context budget ────────────────────────────────────────────────
# Tuned for 16k context windows. Override via env vars for larger models.
CONTEXT_WINDOW="${POTATO_CONTEXT_WINDOW:-16384}"
MAX_TOKENS="${POTATO_MAX_TOKENS:-4096}"
BOOTSTRAP_MAX="${POTATO_BOOTSTRAP_MAX:-400}"
BOOTSTRAP_TOTAL="${POTATO_BOOTSTRAP_TOTAL:-1200}"
COMPACTION_RESERVE="${POTATO_COMPACTION_RESERVE:-8000}"
COMPACTION_KEEP_RECENT="${POTATO_COMPACTION_KEEP_RECENT:-4000}"
SKILLS_PROMPT_CHARS="${POTATO_SKILLS_PROMPT:-0}"

# ── User detection ─────────────────────────────────────────────────────────────
# When piped through `sudo bash`, SUDO_USER gives us the real caller.
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || whoami)}"
REAL_HOME="$(eval echo "~${REAL_USER}")"

# ── Preflight checks ──────────────────────────────────────────────────────────

if [ "$(id -u)" -ne 0 ]; then
  printf 'Error: this script must be run as root (use sudo).\n' >&2
  exit 1
fi

if [ "$(uname -s)" != "Linux" ]; then
  printf 'Error: this installer is for Linux (Raspberry Pi) only.\n' >&2
  exit 1
fi

if [ ! -d "${TARGET_ROOT}" ]; then
  printf 'Error: Potato OS not found at %s. Install Potato OS first.\n' "${TARGET_ROOT}" >&2
  exit 1
fi

printf '=== OpenClaw installer for Potato OS ===\n'
printf 'User: %s  Home: %s  Version: %s\n\n' "${REAL_USER}" "${REAL_HOME}" "${OPENCLAW_VERSION}"

# ── Phase 1: Node.js ──────────────────────────────────────────────────────────

NEED_NODEJS=0
if ! command -v node >/dev/null 2>&1; then
  NEED_NODEJS=1
else
  NODE_MAJOR="$(node --version | sed 's/v//' | cut -d. -f1)"
  if [ "${NODE_MAJOR}" -lt "${REQUIRED_NODE_MAJOR}" ]; then
    NEED_NODEJS=1
  fi
fi

if [ "${NEED_NODEJS}" = "1" ]; then
  printf '[1/7] Installing Node.js %s...\n' "${TARGET_NODE_MAJOR}"
  curl -fsSL "https://deb.nodesource.com/setup_${TARGET_NODE_MAJOR}.x" | bash -
  apt-get install -y nodejs
else
  printf '[1/7] Node.js %s found, skipping.\n' "$(node --version)"
fi

# lsof is required by OpenClaw gateway for stale-pid detection
if ! command -v lsof >/dev/null 2>&1; then
  apt-get install -y lsof
fi

# ── Phase 2: OpenClaw ────────────────────────────────────────────────────────

if command -v openclaw >/dev/null 2>&1; then
  INSTALLED="$(openclaw --version 2>/dev/null | head -1 || echo unknown)"
  printf '[2/7] OpenClaw %s found, upgrading to %s...\n' "${INSTALLED}" "${OPENCLAW_VERSION}"
fi
printf '[2/7] Installing OpenClaw %s...\n' "${OPENCLAW_VERSION}"
npm install -g "openclaw@${OPENCLAW_VERSION}"

# ── Phase 3: Deploy config ────────────────────────────────────────────────────

OPENCLAW_DIR="${REAL_HOME}/.openclaw"
WORKSPACE_DIR="${OPENCLAW_DIR}/workspace"
mkdir -p "${WORKSPACE_DIR}"

# Preserve existing config on upgrades — only deploy on fresh install.
if [ -f "${OPENCLAW_DIR}/openclaw.json" ]; then
  printf '[3/7] Existing OpenClaw config found, preserving.\n'
  # Re-read the existing token so we can print the dashboard URL at the end.
  GATEWAY_TOKEN="$(grep -oP '"token"\s*:\s*"\K[^"]+' "${OPENCLAW_DIR}/openclaw.json" 2>/dev/null || echo unknown)"
else
  printf '[3/7] Deploying Potato OS config (fresh install)...\n'

  # Build dynamic allowedOrigins from actual hostname + IPs + mDNS
  ORIGINS="\"http://localhost:${OPENCLAW_PORT}\", \"http://127.0.0.1:${OPENCLAW_PORT}\""
  PI_HOSTNAME="$(hostname 2>/dev/null || true)"
  if [ -n "${PI_HOSTNAME}" ]; then
    ORIGINS="${ORIGINS}, \"http://${PI_HOSTNAME}:${OPENCLAW_PORT}\""
    # Add .local mDNS variant (standard remote access path)
    ORIGINS="${ORIGINS}, \"http://${PI_HOSTNAME}.local:${OPENCLAW_PORT}\""
  fi
  for ip in $(hostname -I 2>/dev/null || true); do
    ip="$(echo "${ip}" | tr -d '[:space:]')"
    [ -n "${ip}" ] && ORIGINS="${ORIGINS}, \"http://${ip}:${OPENCLAW_PORT}\""
  done

  # Generate a fresh gateway token
  GATEWAY_TOKEN="$(openssl rand -hex 24)"

  cat > "${OPENCLAW_DIR}/openclaw.json" <<OCEOF
{
  "models": {
    "mode": "merge",
    "providers": {
      "potato": {
        "baseUrl": "http://127.0.0.1:1983/v1",
        "apiKey": "not-needed",
        "api": "openai-completions",
        "models": [
          {
            "id": "local",
            "name": "Potato OS Local Model",
            "reasoning": false,
            "input": ["text", "image"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": ${CONTEXT_WINDOW},
            "maxTokens": ${MAX_TOKENS}
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "potato/local" },
      "skipBootstrap": true,
      "bootstrapMaxChars": ${BOOTSTRAP_MAX},
      "bootstrapTotalMaxChars": ${BOOTSTRAP_TOTAL},
      "bootstrapPromptTruncationWarning": "off",
      "memorySearch": { "enabled": false },
      "compaction": {
        "mode": "safeguard",
        "reserveTokens": ${COMPACTION_RESERVE},
        "keepRecentTokens": ${COMPACTION_KEEP_RECENT}
      }
    }
  },
  "tools": {
    "profile": "minimal"
  },
  "gateway": {
    "port": ${OPENCLAW_PORT},
    "mode": "local",
    "bind": "lan",
    "controlUi": {
      "allowedOrigins": [${ORIGINS}],
      "allowInsecureAuth": true,
      "dangerouslyDisableDeviceAuth": true
    },
    "auth": {
      "mode": "token",
      "token": "${GATEWAY_TOKEN}"
    },
    "http": {
      "endpoints": {
        "chatCompletions": { "enabled": true }
      }
    }
  },
  "skills": {
    "allowBundled": [],
    "limits": { "maxSkillsPromptChars": ${SKILLS_PROMPT_CHARS} }
  }
}
OCEOF

  # Workspace SOUL — keep agent replies short to save tokens
  cat > "${WORKSPACE_DIR}/SOUL.md" <<'SOULEOF'
Calm, terse, practical.
SOULEOF

  # Create empty bootstrap files to prevent OpenClaw from generating defaults
  for f in AGENTS.md TOOLS.md IDENTITY.md USER.md HEARTBEAT.md BOOTSTRAP.md MEMORY.md; do
    : > "${WORKSPACE_DIR}/${f}"
  done

  chown -R "${REAL_USER}:${REAL_USER}" "${OPENCLAW_DIR}"
fi

# ── Phase 4: Disable ALL bundled skills ───────────────────────────────────────

printf '[4/7] Disabling all bundled skills...\n'
SKILLS_DIR="$(npm root -g)/openclaw/skills"
DISABLED_COUNT=0
if [ -d "${SKILLS_DIR}" ]; then
  while IFS= read -r skill_file; do
    mv "${skill_file}" "${skill_file}.disabled"
    DISABLED_COUNT=$((DISABLED_COUNT + 1))
  done < <(find "${SKILLS_DIR}" -name "SKILL.md" -not -name "*.disabled" 2>/dev/null)
fi
printf '  disabled %d skills\n' "${DISABLED_COUNT}"

# ── Phase 5: Performance tuning ───────────────────────────────────────────────

printf '[5/7] Configuring performance optimizations...\n'
mkdir -p /var/tmp/openclaw-compile-cache
chown "${REAL_USER}:${REAL_USER}" /var/tmp/openclaw-compile-cache

BASHRC="${REAL_HOME}/.bashrc"
if ! grep -q 'NODE_COMPILE_CACHE' "${BASHRC}" 2>/dev/null; then
  cat >> "${BASHRC}" <<'PERFEOF'

# OpenClaw performance (added by install_openclaw.sh)
export NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache
export OPENCLAW_NO_RESPAWN=1
PERFEOF
  chown "${REAL_USER}:${REAL_USER}" "${BASHRC}"
fi

# ── Phase 6: Systemd gateway service ─────────────────────────────────────────

printf '[6/7] Setting up systemd gateway service...\n'
loginctl enable-linger "${REAL_USER}"

# Let OpenClaw create its own service unit (run as the real user)
su - "${REAL_USER}" -c "openclaw gateway install --port ${OPENCLAW_PORT} --token '${GATEWAY_TOKEN}' 2>/dev/null" || true

SERVICE_DIR="${REAL_HOME}/.config/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/openclaw-gateway.service"
if [ -f "${SERVICE_FILE}" ]; then
  # Add performance env vars if not present
  if ! grep -q 'NODE_COMPILE_CACHE' "${SERVICE_FILE}"; then
    sed -i "/\[Service\]/a Environment=NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache\nEnvironment=OPENCLAW_NO_RESPAWN=1" "${SERVICE_FILE}"
  fi
  su - "${REAL_USER}" -c "systemctl --user daemon-reload && systemctl --user enable --now openclaw-gateway"
fi

# ── Phase 7: Verify ──────────────────────────────────────────────────────────

printf '[7/7] Waiting for gateway to start (this takes ~55s on Pi)...\n'
READY=0
for i in $(seq 1 70); do
  if su - "${REAL_USER}" -c "curl -sf http://127.0.0.1:${OPENCLAW_PORT}/ >/dev/null 2>&1"; then
    READY=1
    break
  fi
  sleep 1
  [ $((i % 10)) -eq 0 ] && printf '  still waiting (%ds)...\n' "${i}"
done

printf '\n'
if [ "${READY}" = "1" ]; then
  printf '✓ OpenClaw is running!\n\n'
else
  printf '⏳ Gateway may still be starting. Check: systemctl --user status openclaw-gateway\n\n'
fi

printf 'Dashboard:  http://%s:%s/#token=%s\n' "${PI_HOSTNAME:-potato.local}" "${OPENCLAW_PORT}" "${GATEWAY_TOKEN}"
printf 'Test:       su - %s -c "openclaw agent --local --agent main --message hi"\n' "${REAL_USER}"
printf '\nContext budget: %s window / %s max tokens (override with POTATO_CONTEXT_WINDOW / POTATO_MAX_TOKENS)\n' "${CONTEXT_WINDOW}" "${MAX_TOKENS}"
