#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOT_PATH="${BOOT_PATH:-}"
OUTPUT_DIR="${OUTPUT_DIR:-}"
PAYLOAD_NAME="${POTATO_BUNDLE_NAME:-potato_bundle.tar.gz}"
LLAMA_BUNDLE_ROOT="${POTATO_LLAMA_BUNDLE_ROOT:-${REPO_ROOT}/references/old_reference_design/llama_cpp_binary}"
LLAMA_BUNDLE_SRC="${POTATO_LLAMA_BUNDLE_SRC:-}"

# Source shared release download helpers
if [ -f "${REPO_ROOT}/bin/lib/runtime_release.sh" ]; then
  # shellcheck source=lib/runtime_release.sh
  source "${REPO_ROOT}/bin/lib/runtime_release.sh"
fi

usage() {
  cat <<'EOF'
Usage:
  ./bin/prepare_imager_bundle.sh --boot-path /Volumes/bootfs
  ./bin/prepare_imager_bundle.sh --output-dir ./output/potato-bundle

Options:
  --boot-path <path>   Mounted Raspberry Pi bootfs path. If set, script will also patch firstrun.sh.
  --output-dir <path>  Output folder for bundle artifacts only (no mounted card required).
  --bundle-name <name> Payload archive name under potato/ (default: potato_bundle.tar.gz).
  --llama-bundle <dir> Path to compiled llama-server bundle (default: latest under references/old_reference_design/llama_cpp_binary).
  -h, --help           Show this help.

What this does:
  1) Builds a Potato payload tarball (core/bin/systemd/nginx/etc + llama runtime bundle).
  2) Writes potato/install_potato_from_bundle.sh.
  3) If bootfs is provided, injects a hook into firstrun.sh so first boot installs Potato automatically.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    die "Missing command: $1"
  fi
}

resolve_boot_path() {
  if [ -n "${BOOT_PATH}" ]; then
    printf '%s\n' "${BOOT_PATH}"
    return
  fi

  for candidate in \
    "/Volumes/bootfs" \
    "/Volumes/boot" \
    "/media/${USER}/bootfs" \
    "/media/${USER}/boot"; do
    if [ -d "${candidate}" ]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done
}

resolve_llama_bundle_src() {
  if [ -n "${LLAMA_BUNDLE_SRC}" ]; then
    printf '%s\n' "${LLAMA_BUNDLE_SRC}"
    return
  fi
  local family="${POTATO_LLAMA_RUNTIME_FAMILY:-ik_llama}"
  local slot_dir="${LLAMA_BUNDLE_ROOT}/runtimes/${family}"
  if [ -d "${slot_dir}" ] && [ -x "${slot_dir}/bin/llama-server" ]; then
    printf '%s\n' "${slot_dir}"
    return
  fi
  # GitHub Release download fallback
  if type try_resolve_runtime_from_release >/dev/null 2>&1; then
    local release_result
    release_result="$(try_resolve_runtime_from_release "${family}" "${LLAMA_BUNDLE_ROOT}/runtimes/${family}" || true)"
    if [ -n "${release_result}" ] && [ -x "${release_result}/bin/llama-server" ]; then
      printf '%s\n' "${release_result}"
      return
    fi
  fi
  # Legacy fallback
  if [ -d "${LLAMA_BUNDLE_ROOT}" ]; then
    find "${LLAMA_BUNDLE_ROOT}" -mindepth 1 -maxdepth 1 -type d -name 'llama_server_bundle_*' 2>/dev/null | sort | tail -n 1
  fi
}

insert_hook_before_exit() {
  local firstrun_path="$1"
  local hook_path="$2"
  local tmp_path
  tmp_path="$(mktemp)"

  awk '
    FNR == NR {
      hook = hook $0 ORS
      next
    }
    /^exit 0[[:space:]]*$/ && inserted == 0 {
      printf "%s", hook
      inserted = 1
    }
    { print }
    END {
      if (inserted == 0) {
        printf "%s", hook
      }
    }
  ' "${hook_path}" "${firstrun_path}" > "${tmp_path}"

  mv "${tmp_path}" "${firstrun_path}"
}

write_installer_script() {
  local target_path="$1"
  cat > "${target_path}" <<EOF
#!/usr/bin/env bash
set -euo pipefail

BOOT_DIR="/boot/firmware"
if [ ! -d "\${BOOT_DIR}" ]; then
  BOOT_DIR="/boot"
fi

PAYLOAD="\${BOOT_DIR}/potato/${PAYLOAD_NAME}"
STATE_DIR="/opt/potato/state"
DONE_MARKER="\${STATE_DIR}/bundle_install.done"
LOG_FILE="/var/log/potato-bundle-install.log"

mkdir -p "\${STATE_DIR}"
exec >> "\${LOG_FILE}" 2>&1

echo "[potato-bundle] starting: \$(date -Iseconds)"
if [ -f "\${DONE_MARKER}" ]; then
  echo "[potato-bundle] already complete"
  exit 0
fi
if [ ! -f "\${PAYLOAD}" ]; then
  echo "[potato-bundle] payload missing: \${PAYLOAD}"
  exit 1
fi

tmpdir="\$(mktemp -d /tmp/potato-bundle-XXXXXX)"
trap 'rm -rf "\${tmpdir}"' EXIT
tar -xzf "\${PAYLOAD}" -C "\${tmpdir}"

cd "\${tmpdir}/payload/potato-os"
PI_PASSWORD="\${PI_PASSWORD:-raspberry}" POTATO_LLAMA_BUNDLE_SRC="\${tmpdir}/payload/llama_bundle" ./bin/install_dev.sh

touch "\${DONE_MARKER}"
echo "[potato-bundle] complete: \$(date -Iseconds)"
EOF
  chmod +x "${target_path}" || true
}

write_hook_script() {
  local target_path="$1"
  cat > "${target_path}" <<'EOF'
# POTATO_BUNDLE_HOOK_START
BOOT_DIR="/boot/firmware"
if [ ! -d "${BOOT_DIR}" ]; then
  BOOT_DIR="/boot"
fi
if [ -f "${BOOT_DIR}/potato/install_potato_from_bundle.sh" ]; then
  bash "${BOOT_DIR}/potato/install_potato_from_bundle.sh" || true
fi
# POTATO_BUNDLE_HOOK_END
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --boot-path)
      BOOT_PATH="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --bundle-name)
      PAYLOAD_NAME="${2:-}"
      shift 2
      ;;
    --llama-bundle)
      LLAMA_BUNDLE_SRC="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

require_cmd rsync
require_cmd tar
require_cmd awk

if [ -z "${OUTPUT_DIR}" ]; then
  BOOT_PATH="$(resolve_boot_path || true)"
  if [ -z "${BOOT_PATH}" ]; then
    die "Boot path not found. Pass --boot-path <mounted bootfs> or use --output-dir."
  fi
  if [ ! -d "${BOOT_PATH}" ]; then
    die "Boot path does not exist: ${BOOT_PATH}"
  fi
  FIRSTRUN_PATH="${BOOT_PATH}/firstrun.sh"
  if [ ! -f "${FIRSTRUN_PATH}" ]; then
    die "Missing ${FIRSTRUN_PATH}. In Raspberry Pi Imager, enable OS customisation at least once so firstrun.sh is generated."
  fi
else
  mkdir -p "${OUTPUT_DIR}"
fi

bundle_src="$(resolve_llama_bundle_src || true)"
if [ -z "${bundle_src}" ] || [ ! -x "${bundle_src}/bin/llama-server" ] || [ ! -d "${bundle_src}/lib" ]; then
  die "llama runtime bundle missing. Set --llama-bundle or ensure ${LLAMA_BUNDLE_ROOT}/llama_server_bundle_* exists."
fi

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

payload_root="${workdir}/payload"
payload_repo="${payload_root}/potato-os"
payload_llama="${payload_root}/llama_bundle"
mkdir -p "${payload_repo}" "${payload_llama}"

rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude 'node_modules' \
  --exclude 'output' \
  --exclude 'test-results' \
  --exclude 'raspberry_os_clean_image' \
  --exclude 'references' \
  "${REPO_ROOT}/" "${payload_repo}/"

rsync -a --delete "${bundle_src}/" "${payload_llama}/"

target_root="${BOOT_PATH:-${OUTPUT_DIR}}"
mkdir -p "${target_root}/potato"
tar -C "${workdir}" -czf "${target_root}/potato/${PAYLOAD_NAME}" payload

install_script_path="${target_root}/potato/install_potato_from_bundle.sh"
write_installer_script "${install_script_path}"

hook_path="${target_root}/potato/potato_firstrun_hook.sh"
write_hook_script "${hook_path}"

if [ -n "${BOOT_PATH}" ] && ! grep -q "POTATO_BUNDLE_HOOK_START" "${FIRSTRUN_PATH}"; then
  insert_hook_before_exit "${FIRSTRUN_PATH}" "${hook_path}"
fi

printf 'Bundle prepared successfully.\n'
if [ -n "${BOOT_PATH}" ]; then
  printf 'Boot path: %s\n' "${BOOT_PATH}"
  printf 'firstrun hook injected: %s\n' "${FIRSTRUN_PATH}"
else
  printf 'Output dir: %s\n' "${OUTPUT_DIR}"
  printf 'Note: apply %s to bootfs/firstrun.sh after flashing.\n' "${hook_path}"
fi
printf 'Payload: %s\n' "${target_root}/potato/${PAYLOAD_NAME}"
printf 'First-boot installer: %s\n' "${install_script_path}"
