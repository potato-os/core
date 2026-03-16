#!/usr/bin/env bash
set -euo pipefail

# Build llama runtimes on a remote Pi and publish to GitHub Releases.
#
# Usage (from Mac):
#   ./bin/build_and_publish_remote.sh
#   ./bin/build_and_publish_remote.sh --family ik_llama
#   ./bin/build_and_publish_remote.sh --family both --publish
#   ./bin/build_and_publish_remote.sh --build-only
#
# Requires: sshpass, rsync, ssh, gh (for publish)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PI_HOST="${POTATO_PI_HOST:-potato.local}"
PI_USER="${POTATO_PI_USER:-pi}"
PI_PASSWORD="${POTATO_PI_PASSWORD:-raspberry}"
FAMILY="${POTATO_LLAMA_RUNTIME_FAMILY:-both}"
DO_PUBLISH="${POTATO_PUBLISH:-0}"
BUILD_ONLY=0
REMOTE_REPO_DIR="/tmp/potato-os"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

usage() {
  cat <<'EOF'
Usage:
  ./bin/build_and_publish_remote.sh [--family ik_llama|llama_cpp|both] [--publish] [--build-only]

Syncs the repo to a Pi, builds runtime(s) from latest source, syncs
the built slots back to your Mac, and optionally publishes to GitHub Releases.

Options:
  --family <name>   Runtime family to build (default: both)
  --publish         Also publish to GitHub Releases after building
  --build-only      Build on Pi but don't sync back or publish
  --host <host>     Pi hostname (default: potato.local)
  -h, --help        Show this help

Environment:
  POTATO_PI_HOST       Pi hostname (default: potato.local)
  POTATO_PI_USER       SSH user (default: pi)
  POTATO_PI_PASSWORD   SSH password (default: raspberry)
EOF
}

die() { printf '\n  ERROR: %s\n\n' "$*" >&2; exit 1; }

BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[32m'
YELLOW='\033[33m'
CYAN='\033[36m'
RESET='\033[0m'

log_step() {
  printf '\n%b━━━ %s ━━━%b\n\n' "${BOLD}${CYAN}" "$*" "${RESET}"
}

log_info() {
  printf '  %b▸%b %s\n' "${GREEN}" "${RESET}" "$*"
}

log_detail() {
  printf '  %b%s%b\n' "${DIM}" "$*" "${RESET}"
}

log_warn() {
  printf '  %b⚠ %s%b\n' "${YELLOW}" "$*" "${RESET}"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --family) FAMILY="$2"; shift 2 ;;
    --publish) DO_PUBLISH=1; shift ;;
    --build-only) BUILD_ONLY=1; shift ;;
    --host) PI_HOST="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

command -v sshpass >/dev/null 2>&1 || die "sshpass is required. Install with: brew install hudochenkov/sshpass/sshpass"
export SSHPASS="${PI_PASSWORD}"

_ssh() {
  sshpass -e ssh ${SSH_OPTS} "${PI_USER}@${PI_HOST}" "$@"
}

_rsync() {
  sshpass -e rsync -az --progress -e "ssh ${SSH_OPTS}" "$@"
}

printf '\n'
printf '%b╔══════════════════════════════════════════╗%b\n' "${BOLD}${CYAN}" "${RESET}"
printf '%b║   Potato Runtime Remote Build            ║%b\n' "${BOLD}${CYAN}" "${RESET}"
printf '%b╚══════════════════════════════════════════╝%b\n' "${BOLD}${CYAN}" "${RESET}"
printf '\n'
log_info "Host:    ${PI_USER}@${PI_HOST}"
log_info "Family:  ${FAMILY}"
log_info "Publish: $([ "${DO_PUBLISH}" = "1" ] && echo "yes" || echo "no (use --publish)")"
printf '\n'

# ── Step 1: Check Pi is reachable ─────────────────────────────────────
log_step "[1/5] Checking Pi is reachable"
if _ssh "echo ok" >/dev/null 2>&1; then
  pi_model="$(_ssh "tr -d '\000' < /proc/device-tree/model 2>/dev/null || echo unknown")"
  pi_mem="$(_ssh "awk '/MemTotal/{printf \"%.0f GB\", \$2/1024/1024}' /proc/meminfo 2>/dev/null || echo unknown")"
  pi_free="$(_ssh "df -h / | awk 'NR==2{print \$4}'" 2>/dev/null || echo unknown)"
  log_info "Connected to: ${pi_model}"
  log_info "Memory: ${pi_mem} | Free disk: ${pi_free}"
else
  die "Cannot reach ${PI_HOST}. Is the Pi on and connected?"
fi

# ── Step 2: Sync repo to Pi ───────────────────────────────────────────
log_step "[2/5] Syncing repo to Pi"
log_detail "Source:  ${REPO_ROOT}"
log_detail "Target:  ${PI_USER}@${PI_HOST}:${REMOTE_REPO_DIR}"

_rsync --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude '.venv' \
  --exclude 'models/' \
  --exclude 'output/' \
  --exclude 'references/old_reference_design/llama_cpp_binary/llama_server_bundle_*' \
  --exclude 'references/old_reference_design/llama_cpp_binary/runtimes/' \
  "${REPO_ROOT}/" "${PI_USER}@${PI_HOST}:${REMOTE_REPO_DIR}/"

log_info "Repo synced"

# ── Step 3: Build on Pi ───────────────────────────────────────────────
log_step "[3/5] Building runtime(s) on Pi"
if [ "${FAMILY}" = "both" ]; then
  log_info "Building ik_llama + llama_cpp (expect ~15 min each)"
else
  log_info "Building ${FAMILY} (expect ~15 min)"
fi
log_detail "Fetching latest source from GitHub..."
log_detail "Build output streams below:"
printf '\n%b--- Pi build output ---%b\n\n' "${DIM}" "${RESET}"

_ssh "cd ${REMOTE_REPO_DIR} && bash bin/build_llama_runtime.sh --family ${FAMILY} --fetch --clean"

printf '\n%b--- End Pi build output ---%b\n' "${DIM}" "${RESET}"
log_info "Build complete"

if [ "${BUILD_ONLY}" = "1" ]; then
  log_warn "Build-only mode. Slots are on Pi at ${REMOTE_REPO_DIR}/references/old_reference_design/llama_cpp_binary/runtimes/"
  exit 0
fi

# ── Step 4: Sync built slots back to Mac ──────────────────────────────
log_step "[4/5] Syncing built slots back to Mac"
SLOTS_DIR="${REPO_ROOT}/references/old_reference_design/llama_cpp_binary/runtimes"
mkdir -p "${SLOTS_DIR}"

sync_slot() {
  local fam="$1"
  log_info "Syncing ${fam}..."
  _rsync --delete \
    "${PI_USER}@${PI_HOST}:${REMOTE_REPO_DIR}/references/old_reference_design/llama_cpp_binary/runtimes/${fam}/" \
    "${SLOTS_DIR}/${fam}/"
  if [ -f "${SLOTS_DIR}/${fam}/runtime.json" ]; then
    local commit version
    commit="$(jq -r '.commit // "unknown"' "${SLOTS_DIR}/${fam}/runtime.json")"
    version="$(jq -r '.version // "unknown"' "${SLOTS_DIR}/${fam}/runtime.json")"
    log_detail "  Commit:  ${commit}"
    log_detail "  Version: ${version}"
    log_detail "  Size:    $(du -sh "${SLOTS_DIR}/${fam}" | cut -f1)"
  fi
}

if [ "${FAMILY}" = "both" ]; then
  sync_slot ik_llama
  sync_slot llama_cpp
else
  sync_slot "${FAMILY}"
fi

# ── Step 5: Publish (optional) ────────────────────────────────────────
if [ "${DO_PUBLISH}" = "1" ]; then
  log_step "[5/5] Publishing to GitHub Releases"
  if [ "${FAMILY}" = "both" ]; then
    log_info "Publishing ik_llama..."
    "${REPO_ROOT}/bin/publish_runtime.sh" --family ik_llama
    printf '\n'
    log_info "Publishing llama_cpp..."
    "${REPO_ROOT}/bin/publish_runtime.sh" --family llama_cpp
  else
    log_info "Publishing ${FAMILY}..."
    "${REPO_ROOT}/bin/publish_runtime.sh" --family "${FAMILY}"
  fi
else
  log_step "[5/5] Publish"
  log_warn "Skipped (use --publish to upload to GitHub Releases)"
  log_detail "You can publish later with:"
  if [ "${FAMILY}" = "both" ]; then
    log_detail "  ./bin/publish_runtime.sh --family ik_llama"
    log_detail "  ./bin/publish_runtime.sh --family llama_cpp"
  else
    log_detail "  ./bin/publish_runtime.sh --family ${FAMILY}"
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────
printf '\n'
printf '%b╔══════════════════════════════════════════╗%b\n' "${BOLD}${GREEN}" "${RESET}"
printf '%b║   Build complete                         ║%b\n' "${BOLD}${GREEN}" "${RESET}"
printf '%b╚══════════════════════════════════════════╝%b\n' "${BOLD}${GREEN}" "${RESET}"
printf '\n'

show_summary() {
  local fam="$1"
  if [ -f "${SLOTS_DIR}/${fam}/runtime.json" ]; then
    local commit profile version
    commit="$(jq -r '.commit // "?"' "${SLOTS_DIR}/${fam}/runtime.json")"
    profile="$(jq -r '.profile // "?"' "${SLOTS_DIR}/${fam}/runtime.json")"
    version="$(jq -r '.version // "?"' "${SLOTS_DIR}/${fam}/runtime.json")"
    log_info "${fam}"
    log_detail "  Commit:  ${commit}"
    log_detail "  Profile: ${profile}"
    log_detail "  Version: ${version}"
    log_detail "  Path:    ${SLOTS_DIR}/${fam}"
  fi
}

if [ "${FAMILY}" = "both" ]; then
  show_summary ik_llama
  show_summary llama_cpp
else
  show_summary "${FAMILY}"
fi
printf '\n'
