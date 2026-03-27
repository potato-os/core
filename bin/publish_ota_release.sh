#!/usr/bin/env bash
set -euo pipefail
export COPYFILE_DISABLE=1  # Prevent macOS tar from embedding ._ resource forks

# Publish an OTA-consumable app tarball to GitHub Releases.
#
# Packages core/ and bin/ into a tarball the on-device updater can
# download, extract, and apply. Attaches to the v<version> release
# (creating it if needed).
#
# Usage:
#   ./bin/publish_ota_release.sh --version v0.5.0
#   ./bin/publish_ota_release.sh --version v0.5.0 --dry-run

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/build_helpers.sh
source "${REPO_ROOT}/bin/lib/build_helpers.sh"
GITHUB_REPO="${POTATO_GITHUB_REPO:-slomin/potato-os}"

VERSION=""
DRY_RUN=0

usage() {
  cat <<'EOF'
Publish an OTA app tarball to GitHub Releases.

Usage:
  ./bin/publish_ota_release.sh --version <tag> [options]

Options:
  --version <tag>   Release tag (e.g. v0.5.0). Required.
  --dry-run         Build the tarball locally but don't publish.
  -h, --help        Show this help.

Example:
  ./bin/publish_ota_release.sh --version v0.5.0
  ./bin/publish_ota_release.sh --version v0.5.0 --dry-run
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
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[ -n "${VERSION}" ] || die "--version is required (e.g. v0.5.0)"
[[ "${VERSION}" =~ ^v[0-9] ]] || die "Version must start with 'v' followed by a number (e.g. v0.5.0)"

# Strip the v prefix for artifact naming.
VERSION_NUM="${VERSION#v}"

# ── Read version from canonical source ─────────────────────────────────
APP_VERSION="$(python3 -c "
import sys; sys.path.insert(0, '${REPO_ROOT}')
from core.__version__ import __version__; print(__version__)
")"

printf 'Tag version:  %s\n' "${VERSION_NUM}"
printf 'App version:  %s\n' "${APP_VERSION}"

if [ "${VERSION_NUM}" != "${APP_VERSION}" ]; then
  die "Tag version \"${VERSION_NUM}\" does not match core/__version__.py \"${APP_VERSION}\". Update __version__ first."
fi

# ── Validate source directories ────────────────────────────────────────
[ -d "${REPO_ROOT}/core" ] || die "core/ directory not found at ${REPO_ROOT}/core"
[ -d "${REPO_ROOT}/bin" ] || die "bin/ directory not found at ${REPO_ROOT}/bin"

# ── Create staging directory ───────────────────────────────────────────
STAGING="$(mktemp -d)"
trap 'rm -rf "${STAGING}"' EXIT

ARCHIVE_NAME="potato-os-${VERSION_NUM}"
TARBALL_NAME="${ARCHIVE_NAME}.tar.gz"
CHECKSUM_NAME="${TARBALL_NAME}.sha256"
PREFIX="${STAGING}/${ARCHIVE_NAME}"

mkdir -p "${PREFIX}"

# ── Copy core/, apps/, and bin/ into staging ───────────────────────────
cp -a "${REPO_ROOT}/core" "${PREFIX}/core"
cp -a "${REPO_ROOT}/bin" "${PREFIX}/bin"
if [ -d "${REPO_ROOT}/apps" ]; then
  cp -a "${REPO_ROOT}/apps" "${PREFIX}/apps"
fi

if [ -f "${REPO_ROOT}/requirements.txt" ]; then
  cp "${REPO_ROOT}/requirements.txt" "${PREFIX}/requirements.txt"
fi

if [ -f "${REPO_ROOT}/LICENSE" ]; then
  cp "${REPO_ROOT}/LICENSE" "${PREFIX}/LICENSE"
fi

if [ -f "${REPO_ROOT}/THIRD_PARTY_NOTICES.md" ]; then
  cp "${REPO_ROOT}/THIRD_PARTY_NOTICES.md" "${PREFIX}/THIRD_PARTY_NOTICES.md"
fi

# ── Create tarball ─────────────────────────────────────────────────────
TARBALL_PATH="${STAGING}/${TARBALL_NAME}"
tar -C "${STAGING}" -czf "${TARBALL_PATH}" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='._*' \
  "${ARCHIVE_NAME}"

TARBALL_SIZE="$(wc -c < "${TARBALL_PATH}" | tr -d ' ')"

# ── Generate checksum ──────────────────────────────────────────────────
CHECKSUM_PATH="${STAGING}/${CHECKSUM_NAME}"
(cd "${STAGING}" && potato_sha256 "${TARBALL_NAME}" > "${CHECKSUM_PATH}")

CHECKSUM_HASH="$(cut -d' ' -f1 < "${CHECKSUM_PATH}")"

printf '\n'
printf '┌─────────────────────────────────────────────────┐\n'
printf '│ Potato OS OTA Release                           │\n'
printf '├─────────────────────────────────────────────────┤\n'
printf '│ Version:    %-36s│\n' "${VERSION}"
printf '│ App:        %-36s│\n' "${APP_VERSION}"
printf '│ Tarball:    %-36s│\n' "${TARBALL_NAME}"
printf '│ Size:       %-36s│\n' "$(python3 -c "print(f'${TARBALL_SIZE} bytes ({${TARBALL_SIZE}/1048576:.1f} MB)')")"
printf '│ SHA256:     %-36s│\n' "${CHECKSUM_HASH:0:16}..."
printf '│ Repo:       %-36s│\n' "${GITHUB_REPO}"
printf '└─────────────────────────────────────────────────┘\n'
printf '\n'
printf 'Assets:\n'
printf '  1. %s\n' "${TARBALL_NAME}"
printf '  2. %s\n' "${CHECKSUM_NAME}"
printf '\n'

if [ "${DRY_RUN}" = "1" ]; then
  cp "${TARBALL_PATH}" "./${TARBALL_NAME}"
  cp "${CHECKSUM_PATH}" "./${CHECKSUM_NAME}"
  printf 'Dry run complete. Files saved to:\n'
  printf '  ./%s\n' "${TARBALL_NAME}"
  printf '  ./%s\n' "${CHECKSUM_NAME}"
  exit 0
fi

# ── Check dependencies (publish-only) ─────────────────────────────────
command -v gh >/dev/null 2>&1 || die "gh CLI is required. Install from https://cli.github.com"

# ── Publish to GitHub Releases ─────────────────────────────────────────
if gh release view "${VERSION}" --repo "${GITHUB_REPO}" >/dev/null 2>&1; then
  # Release already exists — upload/replace OTA assets.
  printf 'Release %s exists — uploading OTA assets...\n' "${VERSION}"
  gh release upload "${VERSION}" \
    "${TARBALL_PATH}" \
    "${CHECKSUM_PATH}" \
    --repo "${GITHUB_REPO}" \
    --clobber
else
  # Release does not exist — create it with OTA assets.
  printf 'Creating new release: %s\n' "${VERSION}"

  PUSH_REMOTE="$(find_github_remote "${GITHUB_REPO}")"

  git tag "${VERSION}" 2>/dev/null || true
  if [ -n "${PUSH_REMOTE}" ]; then
    printf 'Pushing tag %s to %s\n' "${VERSION}" "${PUSH_REMOTE}"
    # Tolerate already-pushed tag so a retry after transient gh failure works.
    git push "${PUSH_REMOTE}" "${VERSION}" 2>/dev/null || true
  fi

  RELEASE_NOTES="$(cat <<NOTES
## Potato OS ${VERSION} — OTA Update

| Field | Value |
|-------|-------|
| Version | \`${APP_VERSION}\` |
| Tarball | \`${TARBALL_NAME}\` |
| SHA256 | \`${CHECKSUM_HASH}\` |

### OTA update

Devices running Potato OS can check for and apply this update via the built-in updater.

### Verify

\`\`\`bash
sha256sum -c ${CHECKSUM_NAME}
\`\`\`
NOTES
  )"

  gh release create "${VERSION}" \
    "${TARBALL_PATH}" \
    "${CHECKSUM_PATH}" \
    --repo "${GITHUB_REPO}" \
    --title "Potato OS ${VERSION}" \
    --notes "${RELEASE_NOTES}"
fi

printf '\nPublished: https://github.com/%s/releases/tag/%s\n' "${GITHUB_REPO}" "${VERSION}"
