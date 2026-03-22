# Shared build/release helper functions for Potato OS.
# Source this file from build, image, and publish scripts.

# Resolve the repository root from this file's location.
_build_helpers_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_build_helpers_repo_root="$(cd "${_build_helpers_lib_dir}/../.." && pwd)"

# Source branding constants if not already loaded.
if [ -z "${POTATO_IMAGER_TAGLINE:-}" ] && [ -f "${_build_helpers_lib_dir}/branding.sh" ]; then
  # shellcheck source=branding.sh
  source "${_build_helpers_lib_dir}/branding.sh"
fi

# generate_potato_manifest <image> <output> <icon> <variant> [version] [download_url]
#
# Generates a Raspberry Pi Imager manifest JSON with consistent naming.
# Uses POTATO_IMAGER_TAGLINE from branding.sh for the display name.
generate_potato_manifest() {
  local image_file="$1"
  local output_path="$2"
  local icon_path="$3"
  local variant="${4:-}"
  local version="${5:-}"
  local download_url="${6:-}"

  local name_parts="${POTATO_PROJECT_NAME:-Potato OS}"
  [ -n "${version}" ] && name_parts="${name_parts} ${version}"
  [ -n "${variant}" ] && name_parts="${name_parts} (${variant})"
  name_parts="${name_parts} — ${POTATO_IMAGER_TAGLINE:-Local AI, No Cloud}"

  local args=(
    python3 "${_build_helpers_repo_root}/bin/generate_imager_manifest.py"
    --image "${image_file}"
    --output "${output_path}"
    --name "${name_parts}"
    --icon "${icon_path}"
  )
  [ -n "${download_url}" ] && args+=(--download-url "${download_url}")
  "${args[@]}"
}

# potato_sha256 <file>
#
# Cross-platform SHA256 checksum (sha256sum on Linux, shasum on macOS).
# Outputs: "<hash>  <filename>"
potato_sha256() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}"
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${path}"
    return
  fi
  printf 'ERROR: Missing sha256 utility (sha256sum or shasum)\n' >&2
  return 1
}

# find_github_remote [repo_slug]
#
# Finds the git remote whose URL matches the given GitHub repo slug.
# Prints the remote name, or empty string if none found.
find_github_remote() {
  local repo="${1:-${GITHUB_REPO:-slomin/potato-os}}"
  local _remote _remote_url
  for _remote in $(git remote 2>/dev/null); do
    _remote_url="$(git remote get-url "${_remote}" 2>/dev/null || true)"
    if printf '%s' "${_remote_url}" | grep -q "${repo}"; then
      printf '%s' "${_remote}"
      return
    fi
  done
}
