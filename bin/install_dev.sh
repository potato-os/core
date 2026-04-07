#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_ROOT="${POTATO_TARGET_ROOT:-/opt/potato}"
SERVICE_DIR="/etc/systemd/system"
POTATO_USER="${POTATO_USER:-potato}"
POTATO_GROUP="${POTATO_GROUP:-potato}"
POTATO_HOSTNAME="${POTATO_HOSTNAME:-potato}"
POTATO_ENFORCE_HOSTNAME="${POTATO_ENFORCE_HOSTNAME:-1}"
LLAMA_RUNTIME_DIR="${POTATO_LLAMA_RUNTIME_DIR:-${TARGET_ROOT}/llama}"
LLAMA_BUNDLE_ROOT="${POTATO_LLAMA_BUNDLE_ROOT:-${REPO_ROOT}/references/old_reference_design/llama_cpp_binary}"
LLAMA_BUNDLE_SRC="${POTATO_LLAMA_BUNDLE_SRC:-}"
# Auto-detect runtime family from hardware if not explicitly set.
# Pi 4 cannot run ik_llama (requires ARMv8.2-A dot product instructions).
# When POTATO_LLAMA_BUNDLE_SRC is set, read the family from the bundle's
# runtime.json so the slot dir matches the actual bundle contents.
if [ -n "${POTATO_LLAMA_RUNTIME_FAMILY:-}" ]; then
  LLAMA_RUNTIME_FAMILY="${POTATO_LLAMA_RUNTIME_FAMILY}"
elif [ -n "${POTATO_LLAMA_BUNDLE_SRC:-}" ] && [ -f "${POTATO_LLAMA_BUNDLE_SRC}/runtime.json" ]; then
  # Read family from the bundle metadata (grep+sed; no deps beyond coreutils).
  LLAMA_RUNTIME_FAMILY="$(grep -o '"family"[[:space:]]*:[[:space:]]*"[^"]*"' "${POTATO_LLAMA_BUNDLE_SRC}/runtime.json" 2>/dev/null | sed 's/.*"family"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/' || echo "ik_llama")"
  [ -z "${LLAMA_RUNTIME_FAMILY}" ] && LLAMA_RUNTIME_FAMILY="ik_llama"
else
  _pi_model="$(tr -d '\000' < /proc/device-tree/model 2>/dev/null || true)"
  if [[ "${_pi_model}" == *"Raspberry Pi 4"* ]]; then
    LLAMA_RUNTIME_FAMILY="llama_cpp"
  else
    LLAMA_RUNTIME_FAMILY="ik_llama"
  fi
fi
REQUIRE_LLAMA_BUNDLE="${POTATO_REQUIRE_LLAMA_BUNDLE:-1}"

# Source shared release download helpers
if [ -f "${REPO_ROOT}/bin/lib/runtime_release.sh" ]; then
  # shellcheck source=lib/runtime_release.sh
  source "${REPO_ROOT}/bin/lib/runtime_release.sh"
fi

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

normalize_runtime_dir_permissions() {
  # Ensure the systemd service user can traverse the parent path (notably /opt on some images).
  local target_parent
  target_parent="$(dirname "${TARGET_ROOT}")"
  if [ "${target_parent}" = "/opt" ] && [ -d /opt ]; then
    run_sudo chmod 0755 /opt
  fi

  if [ -d "${TARGET_ROOT}" ]; then
    run_sudo chmod 0755 "${TARGET_ROOT}" || true
  fi
}

resolve_llama_bundle_src() {
  if [ -n "${LLAMA_BUNDLE_SRC}" ]; then
    printf '%s\n' "${LLAMA_BUNDLE_SRC}"
    return
  fi
  # Look for the runtime slot matching the selected family
  local slot_dir="${LLAMA_BUNDLE_ROOT}/runtimes/${LLAMA_RUNTIME_FAMILY}"
  if [ -d "${slot_dir}" ] && [ -x "${slot_dir}/bin/llama-server" ]; then
    printf '%s\n' "${slot_dir}"
    return
  fi
  # GitHub Release download fallback
  if type try_resolve_runtime_from_release >/dev/null 2>&1; then
    local release_result
    release_result="$(try_resolve_runtime_from_release "${LLAMA_RUNTIME_FAMILY}" "${LLAMA_BUNDLE_ROOT}/runtimes/${LLAMA_RUNTIME_FAMILY}" || true)"
    if [ -n "${release_result}" ] && [ -x "${release_result}/bin/llama-server" ]; then
      printf '%s\n' "${release_result}"
      return
    fi
  fi
  # Legacy fallback: filter bundles by requested family
  if [ -d "${LLAMA_BUNDLE_ROOT}" ]; then
    local -a matched=()
    local d name_lower
    while IFS= read -r d; do
      [ -n "${d}" ] || continue
      name_lower="$(basename "${d}" | tr '[:upper:]' '[:lower:]')"
      case "${LLAMA_RUNTIME_FAMILY}" in
        ik_llama)
          [[ "${name_lower}" == *ik* ]] && matched+=("${d}") ;;
        llama_cpp)
          [[ "${name_lower}" != *ik* ]] && matched+=("${d}") ;;
      esac
    done < <(find "${LLAMA_BUNDLE_ROOT}" -mindepth 1 -maxdepth 1 -type d -name 'llama_server_bundle_*' 2>/dev/null)
    if [ "${#matched[@]}" -ge 1 ]; then
      printf '%s\n' "${matched[@]}" | sort | tail -n 1
    else
      printf 'WARNING: no legacy bundle matching family=%s found.\n' "${LLAMA_RUNTIME_FAMILY}" >&2
    fi
  fi
}

run_sudo apt-get update
run_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  avahi-daemon \
  nginx \
  python3 \
  python3-venv \
  git \
  wget \
  curl \
  jq \
  rsync

if [ "${POTATO_ENFORCE_HOSTNAME}" = "1" ]; then
  current_hostname="$(hostnamectl --static 2>/dev/null || hostname)"
  if [ "${current_hostname}" != "${POTATO_HOSTNAME}" ]; then
    run_sudo hostnamectl set-hostname "${POTATO_HOSTNAME}"
  fi

  hosts_tmp="$(mktemp)"
  run_sudo cat /etc/hosts | awk -v hostname="${POTATO_HOSTNAME}" '
    BEGIN { printed = 0 }
    /^127\.0\.1\.1[[:space:]]/ {
      if (!printed) {
        print "127.0.1.1 " hostname ".local " hostname
        printed = 1
      }
      next
    }
    { print }
    END {
      if (!printed) {
        print "127.0.1.1 " hostname ".local " hostname
      }
    }
  ' > "${hosts_tmp}"
  run_sudo install -m 0644 "${hosts_tmp}" /etc/hosts
  rm -f "${hosts_tmp}"

  if [ -f /etc/avahi/avahi-daemon.conf ]; then
    if grep -q '^[#[:space:]]*host-name=' /etc/avahi/avahi-daemon.conf; then
      run_sudo sed -i "s/^[#[:space:]]*host-name=.*/host-name=${POTATO_HOSTNAME}/" /etc/avahi/avahi-daemon.conf
    else
      run_sudo sh -c "printf '\nhost-name=${POTATO_HOSTNAME}\n' >> /etc/avahi/avahi-daemon.conf"
    fi
  fi
fi

if ! getent group "${POTATO_GROUP}" >/dev/null; then
  run_sudo groupadd --system "${POTATO_GROUP}"
fi

if ! id -u "${POTATO_USER}" >/dev/null 2>&1; then
  run_sudo useradd --system --home "${TARGET_ROOT}" --shell /usr/sbin/nologin --gid "${POTATO_GROUP}" "${POTATO_USER}"
fi
if getent group video >/dev/null 2>&1; then
  run_sudo usermod -a -G video "${POTATO_USER}"
fi

run_sudo mkdir -p "${TARGET_ROOT}"/{bin,core,apps,models,state,config,llama,data}
run_sudo mkdir -p "${TARGET_ROOT}/nginx"
normalize_runtime_dir_permissions

run_sudo rsync -a "${REPO_ROOT}/core/" "${TARGET_ROOT}/core/"
run_sudo rsync -a "${REPO_ROOT}/bin/" "${TARGET_ROOT}/bin/"
# Deploy selected apps (default: chat only). Chat is always included —
# /v1/chat/completions is a platform endpoint other apps depend on.
POTATO_IMAGE_APPS="${POTATO_IMAGE_APPS:-chat}"
IFS=',' read -ra _selected_apps <<< "${POTATO_IMAGE_APPS}"
_has_chat=false
for _a in "${_selected_apps[@]}"; do [ "$_a" = "chat" ] && _has_chat=true; done
if [ "$_has_chat" = "false" ]; then
  _selected_apps+=("chat")
fi
# Remove apps from a previous install that are no longer selected
if [ -d "${TARGET_ROOT}/apps" ]; then
  for _existing in "${TARGET_ROOT}/apps"/*/; do
    [ -d "${_existing}" ] || continue
    _ename="$(basename "${_existing}")"
    _keep=false
    for _a in "${_selected_apps[@]}"; do [ "$_a" = "$_ename" ] && _keep=true; done
    [ "$_ename" = "skeleton" ] && _keep=true
    if [ "$_keep" = "false" ]; then
      run_sudo rm -rf "${_existing}"
      printf 'Removed previously installed app: %s\n' "${_ename}"
    fi
  done
fi
for _app_name in "${_selected_apps[@]}"; do
  _app_src="${REPO_ROOT}/apps/${_app_name}"
  if [ -d "${_app_src}" ]; then
    run_sudo mkdir -p "${TARGET_ROOT}/apps/${_app_name}"
    run_sudo rsync -a "${_app_src}/" "${TARGET_ROOT}/apps/${_app_name}/"
  else
    printf 'WARNING: app directory not found: %s\n' "${_app_src}" >&2
  fi
done
if [ -d "${REPO_ROOT}/nginx" ]; then
  run_sudo rsync -a "${REPO_ROOT}/nginx/" "${TARGET_ROOT}/nginx/"
fi
run_sudo install -m 0644 "${REPO_ROOT}/requirements.txt" "${TARGET_ROOT}/core/requirements.txt"

run_sudo chmod +x "${TARGET_ROOT}"/bin/*.sh
run_sudo install -m 0644 "${REPO_ROOT}/systemd/potato.service" "${SERVICE_DIR}/potato.service"
run_sudo install -m 0644 "${REPO_ROOT}/systemd/potato-firstboot.service" "${SERVICE_DIR}/potato-firstboot.service"
run_sudo install -m 0644 "${REPO_ROOT}/systemd/potato-runtime-reset.service" "${SERVICE_DIR}/potato-runtime-reset.service"

bundle_src="$(resolve_llama_bundle_src || true)"
if [ -n "${bundle_src}" ] && [ -x "${bundle_src}/bin/llama-server" ] && [ -d "${bundle_src}/lib" ]; then
  run_sudo mkdir -p "${LLAMA_RUNTIME_DIR}"
  run_sudo rsync -a --delete "${bundle_src}/" "${LLAMA_RUNTIME_DIR}/"
  run_sudo chmod +x "${LLAMA_RUNTIME_DIR}/bin/llama-server"
  if [ -f "${LLAMA_RUNTIME_DIR}/run-llama-server.sh" ]; then
    run_sudo chmod +x "${LLAMA_RUNTIME_DIR}/run-llama-server.sh"
  fi
  # Also populate the runtime slot so discover_runtime_slots() finds it
  slot_dir="${TARGET_ROOT}/runtimes/${LLAMA_RUNTIME_FAMILY}"
  run_sudo mkdir -p "${slot_dir}"
  run_sudo rsync -a --delete "${bundle_src}/" "${slot_dir}/"
  run_sudo chmod +x "${slot_dir}/bin/llama-server"
  printf 'Installed llama runtime: %s -> %s (family: %s, slot: %s)\n' "${bundle_src}" "${LLAMA_RUNTIME_DIR}" "${LLAMA_RUNTIME_FAMILY}" "${slot_dir}"
else
  msg="llama runtime not found. Expected at ${LLAMA_BUNDLE_ROOT}/runtimes/${LLAMA_RUNTIME_FAMILY}/ or set POTATO_LLAMA_BUNDLE_SRC."
  if [ "${REQUIRE_LLAMA_BUNDLE}" = "1" ]; then
    printf 'ERROR: %s\n' "${msg}" >&2
    exit 1
  fi
  printf 'WARNING: %s\n' "${msg}" >&2
fi

if [ ! -x "${TARGET_ROOT}/venv/bin/python" ]; then
  run_sudo python3 -m venv "${TARGET_ROOT}/venv"
fi

run_sudo "${TARGET_ROOT}/venv/bin/pip" install --upgrade pip
run_sudo "${TARGET_ROOT}/venv/bin/pip" install -r "${TARGET_ROOT}/core/requirements.txt"

# --- LiteRT runtime slot provisioning ---
# LiteRT uses a Python adapter (no binary), so we just create the slot metadata.
# litert-lm-api is aarch64-only — install only on ARM.
_arch="$(uname -m 2>/dev/null || true)"
if [ "${_arch}" = "aarch64" ] || [ "${_arch}" = "arm64" ]; then
  _litert_version="$(run_sudo "${TARGET_ROOT}/venv/bin/pip" show litert-lm-api 2>/dev/null | grep '^Version:' | awk '{print $2}' || true)"
  if [ -z "${_litert_version}" ]; then
    printf 'Installing litert-lm-api...\n'
    run_sudo "${TARGET_ROOT}/venv/bin/pip" install litert-lm-api || printf 'WARNING: litert-lm-api install failed (non-fatal)\n' >&2
    _litert_version="$(run_sudo "${TARGET_ROOT}/venv/bin/pip" show litert-lm-api 2>/dev/null | grep '^Version:' | awk '{print $2}' || true)"
  fi
  # Only provision the slot if litert-lm-api is actually installed.
  if [ -n "${_litert_version}" ]; then
    _litert_slot="${TARGET_ROOT}/runtimes/litert"
    run_sudo mkdir -p "${_litert_slot}"
    printf '{"family":"litert","runtime_type":"litert_adapter","version":"%s"}\n' "${_litert_version}" | \
      run_sudo tee "${_litert_slot}/runtime.json" > /dev/null
    printf 'LiteRT runtime slot provisioned at %s (version: %s)\n' "${_litert_slot}" "${_litert_version}"
  else
    printf 'WARNING: litert-lm-api not available — skipping LiteRT slot provisioning\n' >&2
  fi
fi

# --- App-specific install hooks ---
# Each app can provide install.sh for infrastructure setup (e.g., Pi-hole for Permitato).
for _app_name in "${_selected_apps[@]}"; do
  _app_installer="${TARGET_ROOT}/apps/${_app_name}/install.sh"
  if [ -f "${_app_installer}" ]; then
    printf 'Running install hook for app: %s\n' "${_app_name}"
    POTATO_TARGET_ROOT="${TARGET_ROOT}" POTATO_USER="${POTATO_USER}" POTATO_GROUP="${POTATO_GROUP}" \
      run_sudo bash "${_app_installer}"
  fi
done

run_sudo chown -R "${POTATO_USER}:${POTATO_GROUP}" "${TARGET_ROOT}"
normalize_runtime_dir_permissions

if [ -f "${TARGET_ROOT}/nginx/potato.conf" ]; then
  run_sudo install -m 0644 "${TARGET_ROOT}/nginx/potato.conf" /etc/nginx/sites-available/potato
  run_sudo ln -sf /etc/nginx/sites-available/potato /etc/nginx/sites-enabled/potato
  run_sudo rm -f /etc/nginx/sites-enabled/default
  run_sudo nginx -t
fi

sudoers_tmp="$(mktemp)"
cat > "${sudoers_tmp}" <<'SUDOERS'
potato ALL=(root) NOPASSWD: /bin/systemctl start --no-block potato-runtime-reset.service
potato ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block potato-runtime-reset.service
SUDOERS
run_sudo install -m 0440 "${sudoers_tmp}" /etc/sudoers.d/potato-runtime-reset
rm -f "${sudoers_tmp}"

sudoers_terminal_tmp="$(mktemp)"
cat > "${sudoers_terminal_tmp}" <<'SUDOERS'
potato ALL=(pi) NOPASSWD: ALL
SUDOERS
run_sudo install -m 0440 "${sudoers_terminal_tmp}" /etc/sudoers.d/potato-terminal
rm -f "${sudoers_terminal_tmp}"

sudoers_ota_tmp="$(mktemp)"
cat > "${sudoers_ota_tmp}" <<'SUDOERS'
potato ALL=(root) NOPASSWD: /bin/chown -R potato\:potato /opt/potato/app
potato ALL=(root) NOPASSWD: /usr/bin/chown -R potato\:potato /opt/potato/app
potato ALL=(root) NOPASSWD: /bin/chown -R potato\:potato /opt/potato/bin
potato ALL=(root) NOPASSWD: /usr/bin/chown -R potato\:potato /opt/potato/bin
SUDOERS
run_sudo install -m 0440 "${sudoers_ota_tmp}" /etc/sudoers.d/potato-ota-repair
rm -f "${sudoers_ota_tmp}"

run_sudo systemctl daemon-reload
run_sudo systemctl enable avahi-daemon nginx potato-firstboot.service potato.service
run_sudo systemctl restart avahi-daemon nginx potato-firstboot.service potato.service

printf 'Install complete.\n'
printf 'Status: systemctl status potato --no-pager\n'
printf 'Logs: journalctl -u potato -e\n'
