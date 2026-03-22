#!/usr/bin/env bash
set -euo pipefail

MODEL_FILENAME="Qwen3.5-2B-Q4_K_M.gguf"
MODEL_URL_DEFAULT="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"
MMPROJ_URL_F16_DEFAULT="https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/mmproj-F16.gguf"
# Output artifacts are named as potato-lite-<timestamp> and potato-full-<timestamp>.

info() {
  printf '[potato-image] %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}"
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${path}"
    return
  fi
  die "Missing sha256 utility (sha256sum or shasum)"
}

resolve_repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "${script_dir}/../.." && pwd
}

# Source shared release download helpers
_common_repo_root="$(resolve_repo_root)"
if [ -f "${_common_repo_root}/bin/lib/runtime_release.sh" ]; then
  # shellcheck source=../../bin/lib/runtime_release.sh
  source "${_common_repo_root}/bin/lib/runtime_release.sh"
fi

resolve_llama_bundle_src() {
  local repo_root="$1"
  local bundle_src="${POTATO_LLAMA_BUNDLE_SRC:-}"
  local bundle_root="${POTATO_LLAMA_BUNDLE_ROOT:-${repo_root}/references/old_reference_design/llama_cpp_binary}"
  local family="${POTATO_LLAMA_RUNTIME_FAMILY:-ik_llama}"

  if [ -n "${bundle_src}" ]; then
    printf '%s\n' "${bundle_src}"
    return
  fi
  # Explicit runtime slot
  local slot_dir="${bundle_root}/runtimes/${family}"
  if [ -d "${slot_dir}" ] && [ -x "${slot_dir}/bin/llama-server" ]; then
    printf '%s\n' "${slot_dir}"
    return
  fi
  # GitHub Release download fallback
  if type try_resolve_runtime_from_release >/dev/null 2>&1; then
    local release_result
    release_result="$(try_resolve_runtime_from_release "${family}" "${bundle_root}/runtimes/${family}" || true)"
    if [ -n "${release_result}" ] && [ -x "${release_result}/bin/llama-server" ]; then
      printf '%s\n' "${release_result}"
      return
    fi
  fi
  # Legacy fallback
  if [ -d "${bundle_root}" ]; then
    find "${bundle_root}" -mindepth 1 -maxdepth 1 -type d -name 'llama_server_bundle_*' 2>/dev/null | sort | tail -n 1
  fi
}

download_to_cache() {
  local url="$1"
  local target="$2"
  mkdir -p "$(dirname "${target}")"
  if [ -f "${target}" ] && [ -s "${target}" ]; then
    return
  fi
  info "Downloading ${url} -> ${target}"
  curl -L -C - --fail --output "${target}" "${url}"
}

resolve_mmproj_file_for_full() {
  local cache_dir="$1"
  local repo_root="$2"
  local mmproj_path="${POTATO_FULL_MMPROJ_PATH:-}"
  local url_f16="${POTATO_MMPROJ_URL_F16:-${MMPROJ_URL_F16_DEFAULT}}"

  if [ -n "${mmproj_path}" ]; then
    [ -f "${mmproj_path}" ] || die "POTATO_FULL_MMPROJ_PATH file not found: ${mmproj_path}"
    printf '%s\n' "${mmproj_path}"
    return
  fi

  local local_f16="${repo_root}/models/mmproj-F16.gguf"
  if [ -f "${local_f16}" ]; then
    printf '%s\n' "${local_f16}"
    return
  fi

  local f16_target="${cache_dir}/$(basename "${url_f16%%\?*}")"
  download_to_cache "${url_f16}" "${f16_target}"
  printf '%s\n' "${f16_target}"
}

write_manifest() {
  local manifest_path="$1"
  local variant="$2"
  local image_name="$3"
  local hostname="$4"
  local ssh_user="$5"
  local includes_model="$6"

  cat > "${manifest_path}" <<JSON
{
  "variant": "${variant}",
  "image_name": "${image_name}",
  "hostname": "${hostname}",
  "ssh_user": "${ssh_user}",
  "includes_model": ${includes_model},
  "git_sha": "$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
}
JSON
}

write_pigen_config() {
  local config_path="$1"
  local stage_path="$2"
  local image_name="$3"
  local hostname="$4"
  local ssh_user="$5"
  local ssh_password="$6"

  cat > "${config_path}" <<CFG
IMG_NAME=${image_name}
ENABLE_SSH=1
DISABLE_FIRST_BOOT_USER_RENAME=1
FIRST_USER_NAME=${ssh_user}
FIRST_USER_PASS=${ssh_password}
TARGET_HOSTNAME=${hostname}
DEPLOY_COMPRESSION=xz
DEPLOY_ZIP=0
STAGE_LIST="stage0 stage1 stage2 ${stage_path}"
CFG
}

build_stage_payload() {
  local repo_root="$1"
  local stage_path="$2"
  local variant="$3"
  local cache_dir="$4"

  local files_root="${stage_path}/00-potato/files"
  local potato_root="${files_root}/opt/potato"
  local bundle_root="${POTATO_LLAMA_BUNDLE_ROOT:-${repo_root}/references/old_reference_design/llama_cpp_binary}"
  mkdir -p "${potato_root}/app" "${potato_root}/bin" "${potato_root}/systemd" "${potato_root}/nginx" "${potato_root}/models" "${potato_root}/state" "${potato_root}/config" "${potato_root}/llama" "${potato_root}/runtimes"
  # Git does not preserve directory modes, and local umask can make files/opt too restrictive.
  # Normalize stage payload directories so the flashed image keeps /opt traversable by service users.
  chmod 0755 "${files_root}/opt" "${potato_root}" "${potato_root}/app" "${potato_root}/bin" "${potato_root}/systemd" "${potato_root}/nginx" "${potato_root}/models" "${potato_root}/state" "${potato_root}/config" "${potato_root}/llama" "${potato_root}/runtimes"

  rsync -a "${repo_root}/app/" "${potato_root}/app/"
  rsync -a "${repo_root}/bin/" "${potato_root}/bin/"
  rsync -a "${repo_root}/systemd/" "${potato_root}/systemd/"
  rsync -a "${repo_root}/nginx/" "${potato_root}/nginx/"
  install -m 0644 "${repo_root}/requirements.txt" "${potato_root}/app/requirements.txt"

  local bundle_src
  bundle_src="$(resolve_llama_bundle_src "${repo_root}")"
  if [ -z "${bundle_src}" ] || [ ! -x "${bundle_src}/bin/llama-server" ] || [ ! -d "${bundle_src}/lib" ]; then
    die "llama runtime bundle missing. Set POTATO_LLAMA_BUNDLE_SRC or place llama_server_bundle_* under references/old_reference_design/llama_cpp_binary"
  fi
  rsync -a --delete "${bundle_src}/" "${potato_root}/llama/"
  chmod +x "${potato_root}/llama/bin/llama-server"

  # Populate runtime slots from explicit runtimes/ dirs first, then legacy bundles
  local runtimes_src="${bundle_root}/runtimes"
  local slot_name slot_src
  for slot_name in ik_llama llama_cpp; do
    slot_src="${runtimes_src}/${slot_name}"
    if [ -d "${slot_src}" ] && [ -x "${slot_src}/bin/llama-server" ]; then
      rsync -a --delete "${slot_src}/" "${potato_root}/runtimes/${slot_name}/"
      chmod +x "${potato_root}/runtimes/${slot_name}/bin/llama-server"
    fi
  done

  # Fill any empty slots from legacy llama_server_bundle_* directories
  if [ -d "${bundle_root}" ]; then
    local legacy_dir legacy_lower
    while IFS= read -r legacy_dir; do
      [ -n "${legacy_dir}" ] || continue
      [ -x "${legacy_dir}/bin/llama-server" ] || continue
      [ -d "${legacy_dir}/lib" ] || continue
      legacy_lower="$(basename "${legacy_dir}" | tr '[:upper:]' '[:lower:]')"
      if [[ "${legacy_lower}" == *ik* ]]; then
        slot_name="ik_llama"
      else
        slot_name="llama_cpp"
      fi
      if [ ! -d "${potato_root}/runtimes/${slot_name}/bin" ]; then
        mkdir -p "${potato_root}/runtimes/${slot_name}"
        rsync -a --delete "${legacy_dir}/" "${potato_root}/runtimes/${slot_name}/"
        chmod +x "${potato_root}/runtimes/${slot_name}/bin/llama-server"
      fi
    done < <(find "${bundle_root}" -mindepth 1 -maxdepth 1 -type d -name 'llama_server_bundle_*' 2>/dev/null | sort)
  fi

  if [ "${variant}" = "full" ]; then
    local model_path="${POTATO_FULL_MODEL_PATH:-}"
    local model_url="${POTATO_MODEL_URL:-${MODEL_URL_DEFAULT}}"
    if [ -z "${model_path}" ]; then
      local local_model_path="${repo_root}/models/${MODEL_FILENAME}"
      if [ -f "${local_model_path}" ] && [ -s "${local_model_path}" ]; then
        model_path="${local_model_path}"
      else
        model_path="${cache_dir}/${MODEL_FILENAME}"
        download_to_cache "${model_url}" "${model_path}"
      fi
    fi
    [ -f "${model_path}" ] || die "Full image model not found: ${model_path}"
    cp -f "${model_path}" "${potato_root}/models/${MODEL_FILENAME}"

    local mmproj_path
    mmproj_path="$(resolve_mmproj_file_for_full "${cache_dir}" "${repo_root}")"
    [ -f "${mmproj_path}" ] || die "Full image mmproj not found: ${mmproj_path}"
    cp -f "${mmproj_path}" "${potato_root}/models/$(basename "${mmproj_path}")"
  fi
}

copy_stage_template() {
  local repo_root="$1"
  local work_dir="$2"
  local stage_path="${work_dir}/stage-potato"
  rsync -a --delete "${repo_root}/image/stage-potato/" "${stage_path}/"
  printf '%s\n' "${stage_path}"
}

run_build() {
  local variant="$1"
  shift || true

  [ "${variant}" = "lite" ] || [ "${variant}" = "full" ] || die "Unknown variant: ${variant}"
  [ "$#" -eq 0 ] || die "This script does not accept positional arguments. Configure via POTATO_* env vars."

  require_cmd bash
  require_cmd rsync
  require_cmd tar
  require_cmd curl
  require_cmd python3

  local repo_root
  repo_root="$(resolve_repo_root)"

  local pigen_dir="${POTATO_PI_GEN_DIR:-}"
  if [ -z "${pigen_dir}" ]; then
    die "POTATO_PI_GEN_DIR must point to a preinstalled pi-gen checkout"
  fi
  [ -d "${pigen_dir}" ] || die "POTATO_PI_GEN_DIR does not exist: ${pigen_dir}"
  [ -x "${pigen_dir}/build.sh" ] || die "pi-gen build.sh missing/executable bit not set in: ${pigen_dir}"

  local output_dir="${POTATO_IMAGE_OUTPUT_DIR:-${repo_root}/output/images}"
  local build_root="${POTATO_IMAGE_BUILD_ROOT:-${repo_root}/.cache/potato-image-build}"
  local cache_dir="${POTATO_IMAGE_CACHE_DIR:-${repo_root}/.cache/potato-image-cache}"
  local dry_run="${POTATO_IMAGE_DRY_RUN:-0}"
  local use_docker="${POTATO_PI_GEN_USE_DOCKER:-0}"
  local hostname="${POTATO_HOSTNAME:-potato}"
  local ssh_user="${POTATO_SSH_USER:-pi}"
  local ssh_password="${POTATO_SSH_PASSWORD:-raspberry}"

  mkdir -p "${output_dir}" "${build_root}" "${cache_dir}"

  local timestamp image_name includes_model
  timestamp="$(date +%Y%m%d-%H%M%S)"
  image_name="potato-${variant}-${timestamp}"
  includes_model=false
  if [ "${variant}" = "full" ]; then
    includes_model=true
  fi

  local work_dir
  work_dir="$(mktemp -d "${build_root}/${variant}-XXXXXX")"
  trap 'rm -rf "${work_dir:-}"' EXIT

  local stage_name stage_path
  stage_name="stage-potato-${variant}-${timestamp}"
  stage_path="${pigen_dir}/${stage_name}"
  rm -rf "${stage_path}"
  mkdir -p "${stage_path}"
  rsync -a --delete "${repo_root}/image/stage-potato/" "${stage_path}/"
  build_stage_payload "${repo_root}" "${stage_path}" "${variant}" "${cache_dir}"

  local config_path="${work_dir}/pigen.config"
  write_pigen_config "${config_path}" "${stage_name}" "${image_name}" "${hostname}" "${ssh_user}" "${ssh_password}"

  write_manifest "${output_dir}/potato-${variant}-build-info.json" "${variant}" "${image_name}" "${hostname}" "${ssh_user}" "${includes_model}"
  printf '%s\n' "${stage_path}" > "${output_dir}/potato-${variant}-stage-path.txt"
  cp -f "${config_path}" "${output_dir}/potato-${variant}-config.txt"

  if [ "${dry_run}" = "1" ]; then
    info "Dry run complete for ${variant} (stage prepared, no pi-gen build started)"
    return
  fi

  local before_list="${work_dir}/deploy-before.txt"
  local after_list="${work_dir}/deploy-after.txt"
  find "${pigen_dir}/deploy" -maxdepth 1 -type f \( -name '*.img' -o -name '*.img.xz' -o -name '*.zip' \) 2>/dev/null | sort > "${before_list}" || true

  info "Starting pi-gen build for ${variant} using ${pigen_dir}"
  if [ "${use_docker}" = "1" ]; then
    [ -x "${pigen_dir}/build-docker.sh" ] || die "pi-gen build-docker.sh missing in ${pigen_dir}"
    local config_backup=""
    local container_name="potato-pigen-${variant}"
    if [ -f "${pigen_dir}/config" ]; then
      config_backup="${work_dir}/config.backup"
      cp -f "${pigen_dir}/config" "${config_backup}"
    fi
    cp -f "${config_path}" "${pigen_dir}/config"
    docker rm -f "${container_name}" >/dev/null 2>&1 || true
    (
      cd "${pigen_dir}"
      CONTAINER_NAME="${container_name}" DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 ./build-docker.sh
    )
    if [ -n "${config_backup}" ]; then
      cp -f "${config_backup}" "${pigen_dir}/config"
    else
      rm -f "${pigen_dir}/config"
    fi
  elif [ "${EUID}" -eq 0 ]; then
    (cd "${pigen_dir}" && ./build.sh -c "${config_path}")
  else
    case "$(uname -s)" in
      Linux)
        sudo env "PATH=${PATH}" "${pigen_dir}/build.sh" -c "${config_path}"
        ;;
      *)
        die "Non-Linux host detected. Re-run with POTATO_PI_GEN_USE_DOCKER=1 (or use image/build-all.sh)."
        ;;
    esac
  fi

  find "${pigen_dir}/deploy" -maxdepth 1 -type f \( -name '*.img' -o -name '*.img.xz' -o -name '*.zip' \) | sort > "${after_list}"
  local built_artifact
  built_artifact="$(comm -13 "${before_list}" "${after_list}" | tail -n 1 || true)"
  if [ -z "${built_artifact}" ]; then
    built_artifact="$(find "${pigen_dir}/deploy" -maxdepth 1 -type f -name "${image_name}*.img.xz" | sort | tail -n 1 || true)"
  fi
  [ -n "${built_artifact}" ] || die "Unable to locate built image artifact in ${pigen_dir}/deploy"

  local ext out_image
  ext="${built_artifact##*.}"
  if [ "${ext}" = "xz" ]; then
    out_image="${output_dir}/potato-${variant}-${timestamp}.img.xz"
  elif [ "${ext}" = "img" ]; then
    out_image="${output_dir}/potato-${variant}-${timestamp}.img"
  else
    out_image="${output_dir}/potato-${variant}-${timestamp}.${ext}"
  fi

  cp -f "${built_artifact}" "${out_image}"
  sha256_file "${out_image}" > "${output_dir}/SHA256SUMS"
  case "${out_image}" in
    *.img|*.img.xz)
      info "Generating Raspberry Pi Imager manifest (this may take a minute for xz decompression)..."
      rm -f "${output_dir}/potato-${variant}.rpi-imager-manifest"
      cp -f "${repo_root}/bin/assets/potato-imager-icon.svg" "${output_dir}/potato-imager-icon.svg"
      python3 "${repo_root}/bin/generate_imager_manifest.py" \
        --image "${out_image}" \
        --output "${output_dir}/potato-${variant}.rpi-imager-manifest" \
        --name "Potato OS — Local AI, No Cloud" \
        --icon "${output_dir}/potato-imager-icon.svg" \
        || die "Manifest generation failed for ${out_image}"
      info "Manifest generated: ${output_dir}/potato-${variant}.rpi-imager-manifest"
      ;;
    *)
      info "Skipping Raspberry Pi Imager manifest generation for unsupported artifact type: ${out_image}"
      ;;
  esac
  info "Built image: ${out_image}"
}
