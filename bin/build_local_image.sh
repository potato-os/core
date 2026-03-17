#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${POTATO_IMAGE_OUTPUT_DIR:-${REPO_ROOT}/output/images}"
VARIANT="lite"
NO_UPDATE_PI_GEN=1
SETUP_DOCKER=0
CLEAN_PIGEN_WORK=1
CLEAN_ARTIFACTS_MODE="${POTATO_CLEAN_ARTIFACTS:-ask}"

usage() {
  cat <<'EOF'
Build a local Potato image and package artifacts for manual flashing.

Usage:
  ./bin/build_local_image.sh [options]

Options:
  --variant <lite|full|both>  Image variant (default: lite).
  --output-dir <path>         Artifact output dir (default: output/images).
  --update-pi-gen             Allow pi-gen fetch/pull before build.
  --setup-docker              Pass through --setup-docker to image/build-all.sh.
  --skip-clean-pigen-work     Do not remove stale pigen_work container.
  --clean-artifacts <mode>    Handle old output artifacts: ask|yes|no (default: ask).
  --clean-artifacts-yes       Remove old output artifacts without prompting.
  --clean-artifacts-no        Keep old output artifacts without prompting.
  -h, --help                  Show this help.

Example:
  ./bin/build_local_image.sh --variant lite
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

output_has_artifacts() {
  [ -d "${OUTPUT_DIR}" ] || return 1
  find "${OUTPUT_DIR}" -mindepth 1 -maxdepth 1 | read -r _
}

purge_output_artifacts() {
  [ -d "${OUTPUT_DIR}" ] || return 0
  find "${OUTPUT_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
}

maybe_clean_output_artifacts() {
  output_has_artifacts || return 0

  case "${CLEAN_ARTIFACTS_MODE}" in
    yes)
      echo "[potato-local-build] Removing previous artifacts in ${OUTPUT_DIR}"
      purge_output_artifacts
      ;;
    no)
      echo "[potato-local-build] Keeping existing artifacts in ${OUTPUT_DIR}"
      ;;
    ask)
      if [ -t 0 ]; then
        printf 'Previous artifacts found in %s. Remove them before build? [y/N]: ' "${OUTPUT_DIR}"
        read -r reply
        case "${reply}" in
          y|Y|yes|YES)
            purge_output_artifacts
            echo "[potato-local-build] Removed previous artifacts."
            ;;
          *)
            echo "[potato-local-build] Keeping existing artifacts."
            ;;
        esac
      else
        echo "[potato-local-build] Non-interactive shell; keeping existing artifacts in ${OUTPUT_DIR}."
        echo "[potato-local-build] Use --clean-artifacts yes to purge automatically."
      fi
      ;;
    *)
      die "Invalid clean-artifacts mode: ${CLEAN_ARTIFACTS_MODE} (expected ask|yes|no)"
      ;;
  esac
}

sha256_line() {
  local file_path="$1"
  if has_cmd sha256sum; then
    sha256sum "${file_path}"
    return
  fi
  if has_cmd shasum; then
    shasum -a 256 "${file_path}"
    return
  fi
  die "Missing sha256 utility (sha256sum or shasum)"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --variant)
      VARIANT="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --update-pi-gen)
      NO_UPDATE_PI_GEN=0
      shift
      ;;
    --setup-docker)
      SETUP_DOCKER=1
      shift
      ;;
    --skip-clean-pigen-work)
      CLEAN_PIGEN_WORK=0
      shift
      ;;
    --clean-artifacts)
      CLEAN_ARTIFACTS_MODE="${2:-}"
      shift 2
      ;;
    --clean-artifacts-yes)
      CLEAN_ARTIFACTS_MODE="yes"
      shift
      ;;
    --clean-artifacts-no)
      CLEAN_ARTIFACTS_MODE="no"
      shift
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

case "${VARIANT}" in
  lite|full|both) ;;
  *) die "Invalid --variant: ${VARIANT} (expected lite|full|both)" ;;
esac

require_cmd tee
require_cmd date
require_cmd mkdir
require_cmd ls
require_cmd awk
require_cmd python3
require_cmd find
require_cmd rm

mkdir -p "${OUTPUT_DIR}"
maybe_clean_output_artifacts
timestamp="$(date +%Y%m%d-%H%M%S)"
build_log="${OUTPUT_DIR}/build-${VARIANT}-${timestamp}.log"

if has_cmd docker; then
  if [ "${CLEAN_PIGEN_WORK}" = "1" ]; then
    if docker container inspect pigen_work >/dev/null 2>&1; then
      docker rm -v pigen_work >/dev/null 2>&1 || true
    fi
  fi
fi

build_cmd=("${REPO_ROOT}/image/build-all.sh" "--variant" "${VARIANT}")
if [ "${NO_UPDATE_PI_GEN}" = "1" ]; then
  build_cmd+=("--no-update-pi-gen")
fi
if [ "${SETUP_DOCKER}" = "1" ]; then
  build_cmd+=("--setup-docker")
fi

echo "[potato-local-build] Running: ${build_cmd[*]}"
/usr/bin/time -p "${build_cmd[@]}" 2>&1 | tee "${build_log}"

copy_if_exists() {
  local src="$1"
  local dst_dir="$2"
  [ -f "${src}" ] && cp -f "${src}" "${dst_dir}/"
}

find_latest_image() {
  local target_variant="$1"
  ls -t "${OUTPUT_DIR}/potato-${target_variant}-"*.img.xz "${OUTPUT_DIR}/potato-${target_variant}-"*.img 2>/dev/null | head -n 1 || true
}

write_bundle_metadata() {
  local bundle_dir="$1"
  local target_variant="$2"
  local image_name="$3"
  local image_sha="$4"
  local image_size_bytes="$5"
  local build_log_name="$6"

  cat > "${bundle_dir}/METADATA.json" <<EOF
{
  "bundle_type": "potato-local-test",
  "variant": "${target_variant}",
  "generated_at": "${timestamp}",
  "image_file": "${image_name}",
  "image_sha256": "${image_sha}",
  "image_size_bytes": ${image_size_bytes},
  "build_log": "${build_log_name}",
  "build_command": "${build_cmd[*]}"
}
EOF
}

collect_variant_bundle() {
  local target_variant="$1"
  local image_path
  image_path="$(find_latest_image "${target_variant}")"
  [ -n "${image_path}" ] || die "No built image found for variant ${target_variant} in ${OUTPUT_DIR}"

  local image_name
  image_name="$(basename "${image_path}")"
  local image_stem
  image_stem="${image_name%.img.xz}"
  image_stem="${image_stem%.img}"
  local bundle_dir="${OUTPUT_DIR}/local-test-${target_variant}-${image_stem}"
  mkdir -p "${bundle_dir}"
  cp -f "${image_path}" "${bundle_dir}/"
  copy_if_exists "${OUTPUT_DIR}/SHA256SUMS" "${bundle_dir}"
  if [ -f "${bundle_dir}/SHA256SUMS" ]; then
    mv -f "${bundle_dir}/SHA256SUMS" "${bundle_dir}/SHA256SUMS.source"
  fi
  copy_if_exists "${OUTPUT_DIR}/potato-${target_variant}-build-info.json" "${bundle_dir}"
  copy_if_exists "${OUTPUT_DIR}/potato-${target_variant}-config.txt" "${bundle_dir}"
  copy_if_exists "${OUTPUT_DIR}/potato-${target_variant}-stage-path.txt" "${bundle_dir}"
  copy_if_exists "${build_log}" "${bundle_dir}"

  local actual_sha
  actual_sha="$(sha256_line "${image_path}" | awk '{print $1}')"
  printf '%s  %s\n' "${actual_sha}" "${image_name}" > "${bundle_dir}/SHA256SUMS"

  local imager_manifest="${bundle_dir}/potato-${target_variant}.rpi-imager-manifest"
  echo "[potato-local-build] Generating Raspberry Pi Imager manifest (this may take a minute)..."
  if ! python3 "${REPO_ROOT}/bin/generate_imager_manifest.py" \
    --image "${bundle_dir}/${image_name}" \
    --output "${imager_manifest}" \
    --name "Potato OS (${target_variant}, Raspberry Pi 5)"; then
    echo "[potato-local-build] WARNING: Manifest generation failed. Regenerate manually:"
    echo "  python3 bin/generate_imager_manifest.py --image ${bundle_dir}/${image_name} --output ${imager_manifest}"
  fi

  local image_size_bytes
  image_size_bytes="$(wc -c < "${image_path}" | tr -d ' ')"
  write_bundle_metadata "${bundle_dir}" "${target_variant}" "${image_name}" "${actual_sha}" "${image_size_bytes}" "$(basename "${build_log}")"

  cat > "${bundle_dir}/README-local-test.txt" <<EOF
Potato local test bundle (${target_variant}) generated at ${timestamp}.

Image:
  ${image_name}

Checksum (local):
  ${actual_sha}  ${image_name}

Supporting metadata copied (if present):
  - potato-${target_variant}.rpi-imager-manifest  (use this in Raspberry Pi Imager Content Repository)
  - SHA256SUMS
  - SHA256SUMS.source
  - METADATA.json
  - potato-${target_variant}-build-info.json
  - potato-${target_variant}-config.txt
  - potato-${target_variant}-stage-path.txt
  - $(basename "${build_log}")
EOF

  cat > "${bundle_dir}/README.md" <<EOF
# Potato OS Pi Image Bundle (${target_variant})

Generated at: ${timestamp}

## Use In Raspberry Pi Imager
1. Open Raspberry Pi Imager.
2. Go to \`App Options -> Content Repository\`.
3. Choose \`Use custom file\`.
4. Select: \`potato-${target_variant}.rpi-imager-manifest\`.
5. Click \`Apply & Restart\`.
6. Select \`Potato OS (${target_variant}, Raspberry Pi 5)\` and flash.

## Important
- This bundle is Pi 5-only (`pi5-64bit`).
- Do not use \`METADATA.json\` or \`potato-${target_variant}-build-info.json\` in Imager.

## Files
- Image: \`${image_name}\`
- Manifest: \`potato-${target_variant}.rpi-imager-manifest\`
- Checksum: \`SHA256SUMS\`
EOF

  echo "[potato-local-build] Bundle ready: ${bundle_dir}"
}

if [ "${VARIANT}" = "both" ]; then
  collect_variant_bundle "lite"
  collect_variant_bundle "full"
else
  collect_variant_bundle "${VARIANT}"
fi

echo "[potato-local-build] Done."
