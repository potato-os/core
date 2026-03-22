#!/usr/bin/env bash
set -euo pipefail

# Publish a Pi-built runtime slot to GitHub Releases.
#
# Usage:
#   ./bin/publish_runtime.sh --family ik_llama
#   ./bin/publish_runtime.sh --family llama_cpp --slot-dir /path/to/slot
#   ./bin/publish_runtime.sh --family ik_llama --dry-run

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/build_helpers.sh
source "${REPO_ROOT}/bin/lib/build_helpers.sh"
GITHUB_REPO="${POTATO_GITHUB_REPO:-slomin/potato-os}"
DEFAULT_SLOT_ROOT="${REPO_ROOT}/references/old_reference_design/llama_cpp_binary/runtimes"

FAMILY=""
SLOT_DIR=""
DRY_RUN=0

usage() {
  printf 'Usage: %s --family <ik_llama|llama_cpp> [--slot-dir <path>] [--dry-run]\n' "$(basename "$0")"
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --family) FAMILY="$2"; shift 2 ;;
    --slot-dir) SLOT_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) usage ;;
  esac
done

if [ -z "${FAMILY}" ]; then
  FAMILY="${POTATO_LLAMA_RUNTIME_FAMILY:-}"
fi
if [ -z "${FAMILY}" ]; then
  printf 'ERROR: --family is required (ik_llama or llama_cpp)\n' >&2
  usage
fi
if [ "${FAMILY}" != "ik_llama" ] && [ "${FAMILY}" != "llama_cpp" ]; then
  printf 'ERROR: --family must be ik_llama or llama_cpp, got: %s\n' "${FAMILY}" >&2
  exit 1
fi

if [ -z "${SLOT_DIR}" ]; then
  SLOT_DIR="${DEFAULT_SLOT_ROOT}/${FAMILY}"
fi

# ── Check dependencies ─────────────────────────────────────────────────
if ! command -v jq >/dev/null 2>&1; then
  printf 'ERROR: jq is required to read runtime.json. Install with: brew install jq\n' >&2
  exit 1
fi

# ── Validate slot ──────────────────────────────────────────────────────
if [ ! -d "${SLOT_DIR}" ]; then
  printf 'ERROR: Slot directory does not exist: %s\n' "${SLOT_DIR}" >&2
  exit 1
fi
if [ ! -x "${SLOT_DIR}/bin/llama-server" ]; then
  printf 'ERROR: Missing bin/llama-server in slot: %s\n' "${SLOT_DIR}" >&2
  exit 1
fi
if [ ! -f "${SLOT_DIR}/runtime.json" ]; then
  printf 'ERROR: Missing runtime.json in slot: %s\n' "${SLOT_DIR}" >&2
  exit 1
fi

# ── Read metadata ──────────────────────────────────────────────────────
COMMIT="$(jq -r '.commit // empty' "${SLOT_DIR}/runtime.json")"
PROFILE="$(jq -r '.profile // "pi5-opt"' "${SLOT_DIR}/runtime.json")"
JSON_FAMILY="$(jq -r '.family // empty' "${SLOT_DIR}/runtime.json")"

if [ -z "${COMMIT}" ]; then
  printf 'ERROR: runtime.json is missing "commit" field\n' >&2
  exit 1
fi
if [ -n "${JSON_FAMILY}" ] && [ "${JSON_FAMILY}" != "${FAMILY}" ]; then
  printf 'WARNING: runtime.json family "%s" differs from --family "%s", using --family\n' "${JSON_FAMILY}" "${FAMILY}" >&2
fi

ARCHIVE_NAME="${FAMILY}-${COMMIT}-${PROFILE}"
TARBALL_NAME="${ARCHIVE_NAME}.tar.gz"
TAG_NAME="runtime/${FAMILY}-${COMMIT}"

printf 'Family:   %s\n' "${FAMILY}"
printf 'Commit:   %s\n' "${COMMIT}"
printf 'Profile:  %s\n' "${PROFILE}"
printf 'Tarball:  %s\n' "${TARBALL_NAME}"
printf 'Tag:      %s\n' "${TAG_NAME}"
printf 'Slot:     %s\n' "${SLOT_DIR}"

# ── Create tarball ─────────────────────────────────────────────────────
STAGING="$(mktemp -d)"
trap 'rm -rf "${STAGING}"' EXIT

cp -a "${SLOT_DIR}" "${STAGING}/${ARCHIVE_NAME}"
TARBALL_PATH="${STAGING}/${TARBALL_NAME}"
tar -C "${STAGING}" -czf "${TARBALL_PATH}" "${ARCHIVE_NAME}"

TARBALL_SIZE="$(wc -c < "${TARBALL_PATH}" | tr -d ' ')"
printf 'Tarball created: %s (%s bytes)\n' "${TARBALL_PATH}" "${TARBALL_SIZE}"

if [ "${DRY_RUN}" = "1" ]; then
  # Copy tarball to working directory for inspection
  cp "${TARBALL_PATH}" "./${TARBALL_NAME}"
  printf 'Dry run complete. Tarball saved to ./%s\n' "${TARBALL_NAME}"
  exit 0
fi

# ── Create tag + release ───────────────────────────────────────────────
if ! command -v gh >/dev/null 2>&1; then
  printf 'ERROR: gh CLI is required for publishing. Install from https://cli.github.com\n' >&2
  exit 1
fi

if git tag -l "${TAG_NAME}" | grep -q "${TAG_NAME}"; then
  printf 'ERROR: Tag %s already exists. Delete it first or use a different commit.\n' "${TAG_NAME}" >&2
  exit 1
fi

# Resolve the git remote that matches the target GitHub repo.
# If no remote matches, skip git push — gh release create will
# create the tag on the target repo automatically.
PUSH_REMOTE="$(find_github_remote "${GITHUB_REPO}")"

git tag "${TAG_NAME}" 2>/dev/null || true
if [ -n "${PUSH_REMOTE}" ]; then
  printf 'Pushing tag %s to remote %s\n' "${TAG_NAME}" "${PUSH_REMOTE}"
  git push "${PUSH_REMOTE}" "${TAG_NAME}"
else
  printf 'No git remote matches %s — skipping tag push (gh will create it on the target repo)\n' "${GITHUB_REPO}"
fi

RELEASE_NOTES="$(cat <<NOTES
## ${FAMILY} runtime (${COMMIT})

| Field | Value |
|-------|-------|
| Family | ${FAMILY} |
| Commit | \`${COMMIT}\` |
| Profile | ${PROFILE} |
| Build host | $(jq -r '.build_host // "unknown"' "${SLOT_DIR}/runtime.json") |
| Build arch | $(jq -r '.build_arch // "unknown"' "${SLOT_DIR}/runtime.json") |
| Version | $(jq -r '.version // "unknown"' "${SLOT_DIR}/runtime.json") |

### Install

\`\`\`bash
POTATO_LLAMA_RELEASE_URL=<asset-url> ./bin/install_dev.sh
\`\`\`
NOTES
)"

printf 'Creating GitHub release: %s\n' "${TAG_NAME}"
gh release create "${TAG_NAME}" "${TARBALL_PATH}" \
  --repo "${GITHUB_REPO}" \
  --title "${FAMILY} runtime (${COMMIT})" \
  --notes "${RELEASE_NOTES}"

printf 'Published: https://github.com/%s/releases/tag/%s\n' "${GITHUB_REPO}" "${TAG_NAME}"
