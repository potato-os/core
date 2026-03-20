#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${POTATO_IMAGE_OUTPUT_DIR:-${REPO_ROOT}/output/images}"
BUILD_ROOT="${POTATO_IMAGE_BUILD_ROOT:-${REPO_ROOT}/.cache/potato-image-build}"
DOWNLOAD_CACHE_DIR="${POTATO_IMAGE_CACHE_DIR:-${REPO_ROOT}/.cache/potato-image-cache}"
PIGEN_CHECKOUT_DIR="${POTATO_PI_GEN_DIR:-${REPO_ROOT}/.cache/pi-gen-arm64}"

INCLUDE_DOWNLOAD_CACHE=0
INCLUDE_PIGEN_CHECKOUT=0
DOCKER_PRUNE=0
ASSUME_YES=0

usage() {
  cat <<'EOF'
Clean local Potato Raspberry Pi OS image build artifacts before a new build.

Usage:
  ./bin/clean_image_build_artifacts.sh [options]

Options:
  --output-dir <path>            Override output/images path.
  --build-root <path>            Override .cache/potato-image-build path.
  --download-cache-dir <path>    Override .cache/potato-image-cache path.
  --pi-gen-checkout-dir <path>   Override .cache/pi-gen-arm64 path.
  --include-download-cache       Also clear cached downloads (slower next build).
  --include-pi-gen-checkout      Also delete local pi-gen checkout (re-cloned next build).
  --deep                         Equivalent to both cache options above.
  --docker-prune                 Prune unused Docker images, build cache, and volumes (>24h old).
  --yes                          Skip confirmation prompt.
  -h, --help                     Show help.

Default cleanup:
  - output image artifacts under output/images
  - temporary image build work under .cache/potato-image-build
  - stale pi-gen Docker containers (if docker is installed)
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --build-root)
      BUILD_ROOT="${2:-}"
      shift 2
      ;;
    --download-cache-dir)
      DOWNLOAD_CACHE_DIR="${2:-}"
      shift 2
      ;;
    --pi-gen-checkout-dir)
      PIGEN_CHECKOUT_DIR="${2:-}"
      shift 2
      ;;
    --include-download-cache)
      INCLUDE_DOWNLOAD_CACHE=1
      shift
      ;;
    --include-pi-gen-checkout)
      INCLUDE_PIGEN_CHECKOUT=1
      shift
      ;;
    --deep)
      INCLUDE_DOWNLOAD_CACHE=1
      INCLUDE_PIGEN_CHECKOUT=1
      shift
      ;;
    --docker-prune)
      DOCKER_PRUNE=1
      shift
      ;;
    --yes)
      ASSUME_YES=1
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

cleanup_dir_contents() {
  local target="$1"
  if [ ! -d "${target}" ]; then
    printf '[potato-image-clean] Skip missing dir: %s\n' "${target}"
    return
  fi
  printf '[potato-image-clean] Cleaning contents: %s\n' "${target}"
  find "${target}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
}

remove_dir_tree() {
  local target="$1"
  if [ ! -e "${target}" ]; then
    printf '[potato-image-clean] Skip missing path: %s\n' "${target}"
    return
  fi
  printf '[potato-image-clean] Removing path: %s\n' "${target}"
  rm -rf "${target}"
}

cleanup_docker_artifacts() {
  if ! has_cmd docker; then
    printf '[potato-image-clean] Docker not installed; skipping artifact prune.\n'
    return
  fi

  printf '[potato-image-clean] Pruning unused Docker images, build cache, and volumes (older than 24h)...\n'
  docker image prune --force --filter "until=24h" 2>/dev/null || true
  docker builder prune --force --filter "until=24h" 2>/dev/null || true
  docker volume prune --force --filter "until=24h" 2>/dev/null || true
  printf '[potato-image-clean] Docker artifact prune complete.\n'
}

cleanup_docker_containers() {
  if ! has_cmd docker; then
    printf '[potato-image-clean] Docker not installed; skipping container cleanup.\n'
    return
  fi

  local name
  for name in pigen_work potato-pigen-lite potato-pigen-full; do
    if docker container inspect "${name}" >/dev/null 2>&1; then
      printf '[potato-image-clean] Removing Docker container: %s\n' "${name}"
      docker rm -f "${name}" >/dev/null 2>&1 || true
    fi
  done
}

if [ "${ASSUME_YES}" != "1" ] && [ -t 0 ]; then
  printf 'This will clean image build artifacts in:\n'
  printf '  - %s (contents)\n' "${OUTPUT_DIR}"
  printf '  - %s (contents)\n' "${BUILD_ROOT}"
  if [ "${INCLUDE_DOWNLOAD_CACHE}" = "1" ]; then
    printf '  - %s (contents)\n' "${DOWNLOAD_CACHE_DIR}"
  fi
  if [ "${INCLUDE_PIGEN_CHECKOUT}" = "1" ]; then
    printf '  - %s (entire directory)\n' "${PIGEN_CHECKOUT_DIR}"
  fi
  printf '  - stale Docker pi-gen containers (if present)\n'
  printf 'Continue? [y/N]: '
  read -r reply
  case "${reply}" in
    y|Y|yes|YES) ;;
    *)
      printf '[potato-image-clean] Aborted.\n'
      exit 0
      ;;
  esac
fi

mkdir -p "$(dirname "${OUTPUT_DIR}")" "$(dirname "${BUILD_ROOT}")"
cleanup_dir_contents "${OUTPUT_DIR}"
cleanup_dir_contents "${BUILD_ROOT}"

if [ "${INCLUDE_DOWNLOAD_CACHE}" = "1" ]; then
  cleanup_dir_contents "${DOWNLOAD_CACHE_DIR}"
fi

if [ "${INCLUDE_PIGEN_CHECKOUT}" = "1" ]; then
  remove_dir_tree "${PIGEN_CHECKOUT_DIR}"
fi

cleanup_docker_containers

if [ "${DOCKER_PRUNE}" = "1" ]; then
  cleanup_docker_artifacts
fi

printf '[potato-image-clean] Done.\n'
