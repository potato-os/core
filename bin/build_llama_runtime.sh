#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAMILY="${POTATO_LLAMA_RUNTIME_FAMILY:-ik_llama}"
OUTPUT_ROOT="${POTATO_LLAMA_RUNTIME_OUTPUT:-${REPO_ROOT}/references/old_reference_design/llama_cpp_binary/runtimes}"
JOBS="${POTATO_LLAMA_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"
CLEAN_BUILD="${POTATO_LLAMA_BUILD_CLEAN:-0}"
FETCH_SOURCE="${POTATO_LLAMA_BUILD_FETCH:-0}"

usage() {
  cat <<'EOF'
Usage:
  ./bin/build_llama_runtime.sh --family ik_llama|llama_cpp|both [--jobs N] [--clean] [--fetch]

Builds a portable llama-server runtime on a Raspberry Pi 5 (aarch64).

Families:
  ik_llama   ik_llama.cpp with IQK optimizations (default)
  llama_cpp  Upstream llama.cpp
  both       Build both families in sequence

Options:
  --fetch    Clone source repos if missing, pull latest main if present

Source is expected in references/ik_llama.cpp or references/llama.cpp respectively.
Use --fetch to auto-clone/pull from GitHub.

Environment overrides:
  POTATO_LLAMA_RUNTIME_FAMILY
  POTATO_LLAMA_RUNTIME_OUTPUT
  POTATO_LLAMA_BUILD_JOBS
  POTATO_LLAMA_BUILD_CLEAN=1
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

copy_runtime_deps() {
  local bundle_lib="$1"
  shift
  local f dep base
  mkdir -p "${bundle_lib}"

  for f in "$@"; do
    [ -e "${f}" ] || continue
    while read -r dep; do
      [ -n "${dep}" ] || continue
      base="$(basename "${dep}")"
      case "${base}" in
        ld-linux-*|libc.so.*|libm.so.*|libpthread.so.*|librt.so.*|libdl.so.*|libresolv.so.*)
          continue
          ;;
      esac
      [ -e "${bundle_lib}/${base}" ] && continue
      cp -L "${dep}" "${bundle_lib}/"
    done < <(ldd "${f}" 2>/dev/null | awk '
      $2 == "=>" && $3 ~ /^\// { print $3 }
      $1 ~ /^\// { print $1 }
    ' | sort -u)
  done
}

write_launchers() {
  local bundle_dir="$1"
  cat > "${bundle_dir}/run-llama-server.sh" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$DIR/lib:${LD_LIBRARY_PATH:-}"
export GGML_BACKEND_DIR="$DIR/lib"
exec "$DIR/bin/llama-server" "$@"
LAUNCHER
  chmod +x "${bundle_dir}/run-llama-server.sh"

  cat > "${bundle_dir}/run-llama-bench.sh" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$DIR/lib:${LD_LIBRARY_PATH:-}"
export GGML_BACKEND_DIR="$DIR/lib"
exec "$DIR/bin/llama-bench" "$@"
LAUNCHER
  chmod +x "${bundle_dir}/run-llama-bench.sh"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --family)
      FAMILY="${2:-}"
      shift 2
      ;;
    --jobs)
      JOBS="${2:-}"
      shift 2
      ;;
    --clean)
      CLEAN_BUILD="1"
      shift
      ;;
    --fetch)
      FETCH_SOURCE="1"
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

case "${FAMILY}" in
  ik_llama|llama_cpp|both) ;;
  *) die "Invalid --family. Use ik_llama, llama_cpp, or both." ;;
esac

# Handle --family both by re-invoking this script for each family
if [ "${FAMILY}" = "both" ]; then
  printf '=== Building both runtime families ===\n\n'
  build_args=()
  [ "${CLEAN_BUILD}" = "1" ] && build_args+=(--clean)
  [ "${FETCH_SOURCE}" = "1" ] && build_args+=(--fetch)
  [ -n "${JOBS}" ] && build_args+=(--jobs "${JOBS}")
  # Don't propagate POTATO_LLAMA_CPP_SOURCE into child invocations —
  # it's a single-family override that would wrongly apply to both.
  # Each child resolves its own default source dir.
  POTATO_LLAMA_CPP_SOURCE='' "${BASH_SOURCE[0]}" --family ik_llama "${build_args[@]}"
  printf '\n'
  POTATO_LLAMA_CPP_SOURCE='' "${BASH_SOURCE[0]}" --family llama_cpp "${build_args[@]}"
  exit $?
fi

require_cmd cmake
require_cmd git
require_cmd ldd
require_cmd awk

# Resolve source directory and repo URL based on family
if [ "${FAMILY}" = "ik_llama" ]; then
  SOURCE_DIR="${POTATO_LLAMA_CPP_SOURCE:-${REPO_ROOT}/references/ik_llama.cpp}"
  REPO_URL="https://github.com/ikawrakow/ik_llama.cpp"
  REPO_BRANCH="main"
else
  SOURCE_DIR="${POTATO_LLAMA_CPP_SOURCE:-${REPO_ROOT}/references/llama.cpp}"
  REPO_URL="https://github.com/ggerganov/llama.cpp"
  REPO_BRANCH="master"
fi

# Fetch source: clone if missing/broken, pull latest if present
if [ "${FETCH_SOURCE}" = "1" ]; then
  if git -C "${SOURCE_DIR}" rev-parse --git-dir >/dev/null 2>&1; then
    printf 'Pulling latest %s in %s\n' "${REPO_BRANCH}" "${SOURCE_DIR}"
    git -C "${SOURCE_DIR}" fetch origin "${REPO_BRANCH}" --depth 1
    git -C "${SOURCE_DIR}" checkout FETCH_HEAD
  else
    printf 'Cloning %s into %s\n' "${REPO_URL}" "${SOURCE_DIR}"
    rm -rf "${SOURCE_DIR}"
    mkdir -p "$(dirname "${SOURCE_DIR}")"
    git clone --depth 1 --branch "${REPO_BRANCH}" "${REPO_URL}" "${SOURCE_DIR}"
  fi
fi

[ -d "${SOURCE_DIR}" ] || die "Source directory not found: ${SOURCE_DIR}. Use --fetch to clone it."
[ -f "${SOURCE_DIR}/CMakeLists.txt" ] || die "Not a llama.cpp source tree: ${SOURCE_DIR}"

arch="$(uname -m)"
case "${arch}" in
  aarch64|arm64) ;;
  *) die "This script is intended for aarch64/Pi builds. Current arch: ${arch}" ;;
esac

pi_model="$(tr -d '\000' < /proc/device-tree/model 2>/dev/null || true)"
printf 'Detected hardware: %s\n' "${pi_model:-unknown}"

# Auto-detect build profile from hardware, allow env override.
# For llama_cpp, default to "universal" (GGML_CPU_ALL_VARIANTS) which produces
# per-ISA backend .so files and works on both Pi 4 (armv8.0) and Pi 5 (armv8.2+dotprod).
if [ -n "${POTATO_LLAMA_BUILD_PROFILE:-}" ]; then
  BUILD_PROFILE="${POTATO_LLAMA_BUILD_PROFILE}"
elif [ "${FAMILY}" = "llama_cpp" ]; then
  BUILD_PROFILE="universal"
elif [ -n "${pi_model}" ] && [[ "${pi_model}" == *"Raspberry Pi 4"* ]]; then
  BUILD_PROFILE="pi4-opt"
else
  BUILD_PROFILE="pi5-opt"
fi
printf 'Build profile: %s\n' "${BUILD_PROFILE}"

build_dir="/tmp/potato-llama-build-${FAMILY}"
if [ "${CLEAN_BUILD}" = "1" ]; then
  rm -rf "${build_dir}"
fi
mkdir -p "${build_dir}"

# Build flags depend on the profile.
# "universal" uses GGML_CPU_ALL_VARIANTS which produces per-ISA .so backends
# and requires GGML_BACKEND_DL + BUILD_SHARED_LIBS (GGML_NATIVE must be OFF).
if [ "${BUILD_PROFILE}" = "universal" ]; then
  common_flags=(
    -DCMAKE_BUILD_TYPE=Release
    -DGGML_CPU_ALL_VARIANTS=ON
    -DGGML_BACKEND_DL=ON
    -DBUILD_SHARED_LIBS=ON
    -DGGML_NATIVE=OFF
    -DGGML_BLAS=ON
    -DGGML_BLAS_VENDOR=OpenBLAS
    -DGGML_OPENMP=ON
    -DLLAMA_BUILD_SERVER=ON
    -DLLAMA_BUILD_TOOLS=ON
    -DGGML_VULKAN=OFF
  )
else
  common_flags=(
    -DCMAKE_BUILD_TYPE=Release
    -DGGML_BLAS=ON
    -DGGML_BLAS_VENDOR=OpenBLAS
    -DGGML_OPENMP=ON
    -DLLAMA_BUILD_SERVER=ON
    -DLLAMA_BUILD_TOOLS=ON
    -DGGML_VULKAN=OFF
    -DGGML_NATIVE=ON
    -DGGML_LTO=ON
    "-DCMAKE_C_FLAGS=-mcpu=native"
    "-DCMAKE_CXX_FLAGS=-mcpu=native"
  )
fi

# Family-specific flags
family_flags=()
if [ "${FAMILY}" = "ik_llama" ]; then
  family_flags+=(
    -DGGML_CPU_KLEIDIAI=OFF
    -DGGML_IQK_FA_ALL_QUANTS=ON
    "-DCMAKE_C_FLAGS=-fno-strict-aliasing -mcpu=native"
    "-DCMAKE_CXX_FLAGS=-fno-strict-aliasing -mcpu=native"
  )
fi

printf 'Building %s from %s\n' "${FAMILY}" "${SOURCE_DIR}"
cmake -S "${SOURCE_DIR}" -B "${build_dir}" "${common_flags[@]}" "${family_flags[@]}"
cmake --build "${build_dir}" --config Release -j "${JOBS}"

# Package into runtime slot
slot_dir="${OUTPUT_ROOT}/${FAMILY}"
rm -rf "${slot_dir}"
mkdir -p "${slot_dir}/bin" "${slot_dir}/lib"

cp -f "${build_dir}/bin/llama-server" "${slot_dir}/bin/"
if [ -x "${build_dir}/bin/llama-bench" ]; then
  cp -f "${build_dir}/bin/llama-bench" "${slot_dir}/bin/"
fi

# Fix RPATH: cmake embeds the build directory as RUNPATH. Replace with $ORIGIN
# so the binary finds shared libs relative to itself (bin/../lib/).
if command -v patchelf >/dev/null 2>&1; then
  patchelf --set-rpath '$ORIGIN/../lib' "${slot_dir}/bin/llama-server"
  [ -x "${slot_dir}/bin/llama-bench" ] && patchelf --set-rpath '$ORIGIN/../lib' "${slot_dir}/bin/llama-bench"
fi

shopt -s nullglob
# Copy lib/ first (real files), then bin/ (may contain symlinks).
# Use cp -L to dereference symlinks so we always get the real file.
for so in "${build_dir}/lib/"*.so* "${build_dir}/bin/"*.so*; do
  base="$(basename "${so}")"
  [ -e "${slot_dir}/lib/${base}" ] && continue
  cp -L "${so}" "${slot_dir}/lib/"
done
shopt -u nullglob

# Universal profile: cmake MODULE libraries (ggml-cpu-armv8*.so) may land in
# subdirectories of the build tree instead of bin/ or lib/. Copy any we missed.
if [ "${BUILD_PROFILE}" = "universal" ]; then
  while IFS= read -r so; do
    base="$(basename "${so}")"
    [ ! -e "${slot_dir}/lib/${base}" ] && cp -P "${so}" "${slot_dir}/lib/"
  done < <(find "${build_dir}" -name 'libggml*.so*' -type f 2>/dev/null)
fi

copy_runtime_deps "${slot_dir}/lib" "${slot_dir}/bin/llama-server" "${slot_dir}/bin/llama-bench" "${slot_dir}/lib/"*.so*

# Universal profile: llama.cpp discovers BACKEND_DL backends by scanning the
# executable's directory (GGML_BACKEND_DIR env var is ignored in current versions).
# Symlink backend .so files into bin/ so llama-server finds them at startup.
if [ "${BUILD_PROFILE}" = "universal" ]; then
  for so in "${slot_dir}/lib/libggml-cpu-"*.so "${slot_dir}/lib/libggml-blas.so"; do
    [ -f "${so}" ] && ln -sf "../lib/$(basename "${so}")" "${slot_dir}/bin/$(basename "${so}")"
  done
fi
write_launchers "${slot_dir}"

# Generate runtime.json metadata
commit="$(git -C "${SOURCE_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
version_output="$("${slot_dir}/run-llama-server.sh" --version 2>&1 | head -n 1 || true)"
build_flags="${common_flags[*]} ${family_flags[*]}"

cat > "${slot_dir}/runtime.json" <<EOF
{
  "family": "${FAMILY}",
  "repo": "${REPO_URL}",
  "commit": "${commit}",
  "profile": "${BUILD_PROFILE}",
  "build_timestamp": "$(date -Iseconds)",
  "build_host": "${pi_model:-unknown}",
  "build_arch": "${arch}",
  "build_flags": "${build_flags}",
  "version": "${version_output}"
}
EOF

# Include upstream LICENSE in the runtime slot for redistribution compliance.
if [ -f "${SOURCE_DIR}/LICENSE" ]; then
  cp "${SOURCE_DIR}/LICENSE" "${slot_dir}/LICENSE"
fi

printf '\n=== Built runtime: %s ===\n' "${FAMILY}"
printf 'Slot: %s\n' "${slot_dir}"
printf 'Commit: %s\n' "${commit}"
printf 'Version: %s\n' "${version_output}"
