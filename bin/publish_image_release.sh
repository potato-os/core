#!/usr/bin/env bash
set -euo pipefail

# Publish a built Potato OS image to GitHub Releases.
#
# The generated manifest uses HTTPS URLs pointing at the release assets,
# so users can paste the manifest URL into Raspberry Pi Imager's
# "Content Repository → Use custom URL" field and flash directly.
#
# Usage:
#   ./bin/publish_image_release.sh --version v0.3
#   ./bin/publish_image_release.sh --version v0.3 --bundle-dir output/images/local-test-lite-*/
#   ./bin/publish_image_release.sh --version v0.3 --dry-run

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GITHUB_REPO="${POTATO_GITHUB_REPO:-slomin/potato-os}"

VERSION=""
BUNDLE_DIR=""
DRY_RUN=0
VARIANT="lite"

usage() {
  cat <<'EOF'
Publish a Potato OS image to GitHub Releases.

Usage:
  ./bin/publish_image_release.sh --version <tag> [options]

Options:
  --version <tag>        Release tag (e.g. v0.3). Required.
  --bundle-dir <path>    Path to local-test bundle dir. Auto-detected if omitted.
  --variant <lite|full>  Image variant (default: lite).
  --dry-run              Validate and show what would be published, but don't create the release.
  -h, --help             Show this help.

Example:
  ./bin/publish_image_release.sh --version v0.3 --variant lite
EOF
  exit 1
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --bundle-dir) BUNDLE_DIR="$2"; shift 2 ;;
    --variant) VARIANT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[ -n "${VERSION}" ] || die "--version is required (e.g. v0.3)"
[[ "${VERSION}" =~ ^v[0-9] ]] || die "Version must start with 'v' followed by a number (e.g. v0.3)"
[ "${VARIANT}" = "lite" ] || [ "${VARIANT}" = "full" ] || die "--variant must be lite or full"

# ── Check dependencies ─────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || die "python3 is required"

# ── Locate bundle ──────────────────────────────────────────────────────
if [ -z "${BUNDLE_DIR}" ]; then
  OUTPUT_DIR="${POTATO_IMAGE_OUTPUT_DIR:-${REPO_ROOT}/output/images}"
  BUNDLE_DIR="$(find "${OUTPUT_DIR}" -mindepth 1 -maxdepth 1 -type d -name "local-test-${VARIANT}-*" 2>/dev/null | sort | tail -n 1 || true)"
  [ -n "${BUNDLE_DIR}" ] || die "No local-test-${VARIANT}-* bundle found in ${OUTPUT_DIR}. Build an image first."
fi
[ -d "${BUNDLE_DIR}" ] || die "Bundle directory does not exist: ${BUNDLE_DIR}"

# ── Validate bundle contents ───────────────────────────────────────────
IMAGE_FILE="$(find "${BUNDLE_DIR}" -maxdepth 1 -name 'potato-*.img.xz' -o -name 'potato-*.img' | sort | tail -n 1 || true)"
[ -n "${IMAGE_FILE}" ] || die "No potato-*.img.xz or *.img found in ${BUNDLE_DIR}"

ICON_FILE="${BUNDLE_DIR}/potato-imager-icon.svg"
[ -f "${ICON_FILE}" ] || die "Missing icon: ${ICON_FILE}"

CHECKSUMS_FILE="${BUNDLE_DIR}/SHA256SUMS"
[ -f "${CHECKSUMS_FILE}" ] || die "Missing checksums: ${CHECKSUMS_FILE}"

IMAGE_NAME="$(basename "${IMAGE_FILE}")"
IMAGE_SIZE="$(wc -c < "${IMAGE_FILE}" | tr -d ' ')"

# ── Read version from canonical source ─────────────────────────────────
APP_VERSION="$(python3 -c "
import sys; sys.path.insert(0, '${REPO_ROOT}')
from app.__version__ import __version__; print(__version__)
")"

# ── Build release asset URLs ───────────────────────────────────────────
DOWNLOAD_BASE="https://github.com/${GITHUB_REPO}/releases/download/${VERSION}"
IMAGE_URL="${DOWNLOAD_BASE}/${IMAGE_NAME}"
ICON_URL="${DOWNLOAD_BASE}/potato-imager-icon.svg"
MANIFEST_NAME="potato-${VARIANT}.rpi-imager-manifest"

# ── Regenerate manifest with release URLs ──────────────────────────────
STAGING="$(mktemp -d)"
trap 'rm -rf "${STAGING}"' EXIT

MANIFEST_PATH="${STAGING}/${MANIFEST_NAME}"
python3 "${REPO_ROOT}/bin/generate_imager_manifest.py" \
  --image "${IMAGE_FILE}" \
  --output "${MANIFEST_PATH}" \
  --name "Potato OS ${VERSION} (${VARIANT}, Raspberry Pi 4 / 5)" \
  --icon "${ICON_URL}" \
  --download-url "${IMAGE_URL}" \
  || die "Manifest generation failed"

printf '\n'
printf '┌─────────────────────────────────────────────────┐\n'
printf '│ Potato OS Image Release                         │\n'
printf '├─────────────────────────────────────────────────┤\n'
printf '│ Version:    %-36s│\n' "${VERSION}"
printf '│ App:        %-36s│\n' "${APP_VERSION}"
printf '│ Variant:    %-36s│\n' "${VARIANT}"
printf '│ Image:      %-36s│\n' "${IMAGE_NAME}"
printf '│ Size:       %-36s│\n' "$(python3 -c "print(f'{${IMAGE_SIZE}/1048576:.0f} MB')")"
printf '│ Tag:        %-36s│\n' "${VERSION}"
printf '│ Repo:       %-36s│\n' "${GITHUB_REPO}"
printf '└─────────────────────────────────────────────────┘\n'
printf '\n'
printf 'Assets to upload:\n'
printf '  1. %s\n' "${IMAGE_NAME}"
printf '  2. %s\n' "${MANIFEST_NAME}"
printf '  3. potato-imager-icon.svg\n'
printf '  4. SHA256SUMS\n'
printf '\n'
printf 'Raspberry Pi Imager URL:\n'
printf '  %s/%s\n' "${DOWNLOAD_BASE}" "${MANIFEST_NAME}"
printf '\n'

if [ "${DRY_RUN}" = "1" ]; then
  printf 'Dry run — manifest written to: %s\n' "${MANIFEST_PATH}"
  cat "${MANIFEST_PATH}"
  printf '\nNo release created.\n'
  exit 0
fi

# ── Check dependencies (publish-only) ─────────────────────────────────
command -v gh >/dev/null 2>&1 || die "gh CLI is required. Install from https://cli.github.com"

# ── Check tag does not already exist ───────────────────────────────────
if git tag -l "${VERSION}" | grep -q "${VERSION}"; then
  die "Tag ${VERSION} already exists. Delete it first or use a different version."
fi
if gh release view "${VERSION}" --repo "${GITHUB_REPO}" >/dev/null 2>&1; then
  die "Release ${VERSION} already exists on GitHub."
fi

# ── Resolve push remote ────────────────────────────────────────────────
PUSH_REMOTE=""
for _remote in $(git remote 2>/dev/null); do
  _remote_url="$(git remote get-url "${_remote}" 2>/dev/null || true)"
  if printf '%s' "${_remote_url}" | grep -q "${GITHUB_REPO}"; then
    PUSH_REMOTE="${_remote}"
    break
  fi
done

RELEASE_NOTES="$(cat <<NOTES
## Potato OS ${VERSION}

Local AI chat on Raspberry Pi 4 / 5 — zero cloud dependency.

| Field | Value |
|-------|-------|
| Version | \`${APP_VERSION}\` |
| Variant | ${VARIANT} |
| Image | \`${IMAGE_NAME}\` |
| Device | Raspberry Pi 4 (8 GB) / Raspberry Pi 5 (8 GB / 16 GB) |

### Flash with Raspberry Pi Imager

1. Open [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Go to **OS** → **Content Repository**
3. Select **Use custom URL** and paste:
   \`\`\`
   ${DOWNLOAD_BASE}/${MANIFEST_NAME}
   \`\`\`
4. Click **Apply & Restart**
5. Select **Potato OS** and flash

### Direct download

\`\`\`bash
curl -LO ${IMAGE_URL}
\`\`\`

Verify checksum:
\`\`\`bash
sha256sum -c SHA256SUMS
\`\`\`

### Pi 4 support

Same image boots on both Pi 4 and Pi 5. On Pi 4:
- Runtime: llama.cpp universal (auto-selects armv8.0 backend)
- Default model: Qwen3.5-0.8B (~5 tok/sec)
- Tested on Pi 4 Model B Rev 1.4 (8 GB), SD card only
- Thermal throttling observed without active cooling
NOTES
)"

# ── Create release (tag created locally, pushed only after success) ────
git tag "${VERSION}"

printf 'Creating GitHub release: %s\n' "${VERSION}"
if ! gh release create "${VERSION}" \
  "${IMAGE_FILE}" \
  "${MANIFEST_PATH}" \
  "${ICON_FILE}" \
  "${CHECKSUMS_FILE}" \
  --repo "${GITHUB_REPO}" \
  --title "Potato OS ${VERSION}" \
  --notes "${RELEASE_NOTES}"; then
  printf 'Release creation failed — cleaning up local tag %s\n' "${VERSION}" >&2
  git tag -d "${VERSION}" 2>/dev/null || true
  die "gh release create failed. No tag was pushed — safe to retry."
fi

if [ -n "${PUSH_REMOTE}" ]; then
  printf 'Pushing tag %s to %s\n' "${VERSION}" "${PUSH_REMOTE}"
  git push "${PUSH_REMOTE}" "${VERSION}"
fi

printf '\nPublished: https://github.com/%s/releases/tag/%s\n' "${GITHUB_REPO}" "${VERSION}"
printf '\nRaspberry Pi Imager URL:\n  %s/%s\n' "${DOWNLOAD_BASE}" "${MANIFEST_NAME}"

# ── Update stable pointer release ─────────────────────────────────────
# Maintains a "stable" release with manifests for each variant so docs
# can link to a version-independent URL:
#   https://github.com/<repo>/releases/download/stable/potato-lite.rpi-imager-manifest
# Assets are uploaded/replaced individually — the release is never
# deleted and recreated, so a transient failure cannot break the
# existing public URL.
STABLE_TAG="stable"
STABLE_BASE="https://github.com/${GITHUB_REPO}/releases/download/${STABLE_TAG}"

# Regenerate manifest with download URLs pointing at the versioned release assets
STABLE_MANIFEST="${STAGING}/${MANIFEST_NAME}"
python3 "${REPO_ROOT}/bin/generate_imager_manifest.py" \
  --image "${IMAGE_FILE}" \
  --output "${STABLE_MANIFEST}" \
  --name "Potato OS ${VERSION} (${VARIANT}, Raspberry Pi 4 / 5)" \
  --icon "${STABLE_BASE}/potato-imager-icon.svg" \
  --download-url "${IMAGE_URL}" \
  || die "Stable manifest generation failed"

printf '\nUpdating stable pointer release...\n'

# Create the stable release if it does not exist yet
if ! gh release view "${STABLE_TAG}" --repo "${GITHUB_REPO}" >/dev/null 2>&1; then
  git tag -f "${STABLE_TAG}" >/dev/null 2>&1
  if [ -n "${PUSH_REMOTE}" ]; then
    git push "${PUSH_REMOTE}" "${STABLE_TAG}" --force 2>/dev/null || true
  fi
  gh release create "${STABLE_TAG}" \
    --repo "${GITHUB_REPO}" \
    --title "Potato OS (latest stable)" \
    --notes "Pointer release — always tracks the latest stable image." \
    --prerelease \
    || { printf 'WARNING: stable release creation failed (non-fatal)\n' >&2; true; }
fi

# Upload/replace only this variant's manifest, icon, and checksums.
# Other variant manifests already on the release are left untouched.
# Note: --clobber deletes then re-uploads per asset, so a transient
# failure could briefly remove an asset. Re-run the script to fix.
gh release upload "${STABLE_TAG}" \
  "${STABLE_MANIFEST}" \
  "${ICON_FILE}" \
  "${CHECKSUMS_FILE}" \
  --repo "${GITHUB_REPO}" \
  --clobber \
  || printf 'WARNING: stable asset upload failed (non-fatal)\n' >&2

# Update release notes to reflect the current version
gh release edit "${STABLE_TAG}" \
  --repo "${GITHUB_REPO}" \
  --notes "Pointer release — always tracks the latest stable image. Current: ${VERSION} (${VARIANT}). Use this URL in Raspberry Pi Imager: \`${STABLE_BASE}/potato-${VARIANT}.rpi-imager-manifest\`" \
  2>/dev/null || true

printf '\nStable Imager URL:\n  %s/%s\n' "${STABLE_BASE}" "${MANIFEST_NAME}"
