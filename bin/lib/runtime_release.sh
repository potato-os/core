#!/usr/bin/env bash
# Shared helpers for downloading runtime binaries from GitHub Releases.
# Sourced by install_dev.sh, prepare_imager_bundle.sh, and image/lib/common.sh.

POTATO_GITHUB_REPO="${POTATO_GITHUB_REPO:-slomin/potato-os}"

resolve_latest_runtime_release_url() {
  local family="$1"
  local repo="${POTATO_GITHUB_REPO}"

  # Prefer gh CLI if available (handles auth, private repos)
  if command -v gh >/dev/null 2>&1; then
    local tag
    tag="$(gh release list --repo "${repo}" --limit 20 \
      --json tagName --jq "[.[] | select(.tagName | startswith(\"runtime/${family}-\"))] | .[0].tagName" 2>/dev/null || true)"
    if [ -n "${tag}" ]; then
      gh release view "${tag}" --repo "${repo}" \
        --json assets --jq '.assets[0].url' 2>/dev/null || true
      return
    fi
  fi

  # Fallback: curl-only path using GitHub REST API (no gh needed)
  if command -v curl >/dev/null 2>&1; then
    local api_url="https://api.github.com/repos/${repo}/releases"
    local releases_json
    releases_json="$(curl -sL --fail "${api_url}?per_page=20" 2>/dev/null || true)"
    if [ -z "${releases_json}" ]; then
      return
    fi
    # Find first release whose tag starts with runtime/<family>- and extract asset URL
    if command -v jq >/dev/null 2>&1; then
      local asset_url
      asset_url="$(printf '%s' "${releases_json}" | jq -r \
        "[.[] | select(.tag_name | startswith(\"runtime/${family}-\"))] | .[0].assets[0].browser_download_url // empty" 2>/dev/null || true)"
      if [ -n "${asset_url}" ]; then
        printf '%s' "${asset_url}"
        return
      fi
    fi
  fi
}

download_and_extract_runtime() {
  local url="$1"
  local target_dir="$2"
  local tmp_tarball
  tmp_tarball="$(mktemp /tmp/potato-runtime-XXXXXX.tar.gz)"

  printf 'Downloading runtime from %s\n' "${url}"
  if ! curl -L --fail --silent --show-error --output "${tmp_tarball}" "${url}"; then
    printf 'WARNING: Failed to download runtime from %s\n' "${url}" >&2
    rm -f "${tmp_tarball}"
    return 1
  fi

  rm -rf "${target_dir}"
  mkdir -p "${target_dir}"
  tar -xzf "${tmp_tarball}" -C "${target_dir}" --strip-components=1
  rm -f "${tmp_tarball}"

  if [ ! -x "${target_dir}/bin/llama-server" ]; then
    printf 'WARNING: Downloaded runtime is missing bin/llama-server\n' >&2
    rm -rf "${target_dir}"
    return 1
  fi
  printf 'Runtime downloaded and extracted to %s\n' "${target_dir}"
}

try_resolve_runtime_from_release() {
  local family="$1"
  local target_dir="$2"

  local release_url="${POTATO_LLAMA_RELEASE_URL:-}"
  if [ -z "${release_url}" ] && [ "${POTATO_LLAMA_RELEASE_AUTO:-0}" = "1" ]; then
    release_url="$(resolve_latest_runtime_release_url "${family}" || true)"
  fi

  if [ -n "${release_url}" ]; then
    if download_and_extract_runtime "${release_url}" "${target_dir}"; then
      printf '%s\n' "${target_dir}"
      return 0
    fi
  fi
  return 1
}
