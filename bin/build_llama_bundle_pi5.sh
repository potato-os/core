#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${POTATO_LLAMA_CPP_SOURCE:-${REPO_ROOT}/references/llama.cpp}"
OUTPUT_ROOT="${POTATO_LLAMA_BUNDLE_ROOT:-${REPO_ROOT}/references/old_reference_design/llama_cpp_binary}"
PROFILE="${POTATO_LLAMA_BUILD_PROFILE:-pi5-opt}"
JOBS="${POTATO_LLAMA_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"
CLEAN_BUILD="${POTATO_LLAMA_BUILD_CLEAN:-0}"

usage() {
  cat <<'EOF'
Usage:
  ./bin/build_llama_bundle_pi5.sh [--profile baseline|pi5-opt] [--jobs N] [--clean]

Builds a portable llama-server bundle on a Raspberry Pi 5 (aarch64) from references/llama.cpp.

Profiles:
  baseline  Portable-ish CPU build with OpenBLAS, no Pi5-specific optimizations
  pi5-opt   Pi5-targeted build (GGML_NATIVE + KleidiAI + LTO + OpenBLAS)

Environment overrides:
  POTATO_LLAMA_CPP_SOURCE
  POTATO_LLAMA_BUNDLE_ROOT
  POTATO_LLAMA_BUILD_PROFILE
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
      cp -L "${dep}" "${bundle_lib}/"
    done < <(ldd "${f}" 2>/dev/null | awk '
      $2 == "=>" && $3 ~ /^\// { print $3 }
      $1 ~ /^\// { print $1 }
    ' | sort -u)
  done
}

write_launchers() {
  local bundle_dir="$1"
  cat > "${bundle_dir}/run-llama-server.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$DIR/lib:${LD_LIBRARY_PATH:-}"
exec "$DIR/bin/llama-server" "$@"
EOF
  chmod +x "${bundle_dir}/run-llama-server.sh"

  cat > "${bundle_dir}/run-llama-bench.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$DIR/lib:${LD_LIBRARY_PATH:-}"
exec "$DIR/bin/llama-bench" "$@"
EOF
  chmod +x "${bundle_dir}/run-llama-bench.sh"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      PROFILE="${2:-}"
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
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

case "${PROFILE}" in
  baseline|pi5-opt) ;;
  *) die "Invalid --profile. Use baseline or pi5-opt." ;;
esac

require_cmd cmake
require_cmd git
require_cmd ldd
require_cmd awk
require_cmd cp
require_cmd date

[ -d "${SOURCE_DIR}" ] || die "llama.cpp source directory not found: ${SOURCE_DIR}"
[ -f "${SOURCE_DIR}/CMakeLists.txt" ] || die "Not a llama.cpp source tree: ${SOURCE_DIR}"

arch="$(uname -m)"
case "${arch}" in
  aarch64|arm64) ;;
  *)
    die "This script is intended for aarch64/Pi builds. Current arch: ${arch}"
    ;;
esac

pi_model="$(tr -d '\000' < /proc/device-tree/model 2>/dev/null || true)"
if [ -n "${pi_model}" ] && [[ "${pi_model}" != *"Raspberry Pi 5"* ]]; then
  printf 'WARNING: Non-Pi5 model detected: %s\n' "${pi_model}" >&2
fi

build_dir="/tmp/potato-llama-build-${PROFILE}"
if [ "${CLEAN_BUILD}" = "1" ]; then
  rm -rf "${build_dir}"
fi
mkdir -p "${build_dir}"
mkdir -p "${OUTPUT_ROOT}"

common_flags=(
  -DCMAKE_BUILD_TYPE=Release
  -DGGML_BLAS=ON
  -DGGML_BLAS_VENDOR=OpenBLAS
  -DGGML_OPENMP=ON
  -DLLAMA_BUILD_SERVER=ON
  -DLLAMA_BUILD_TOOLS=ON
  -DLLAMA_BUILD_EXAMPLES=ON
)

profile_flags=()
if [ "${PROFILE}" = "baseline" ]; then
  profile_flags+=(
    -DGGML_NATIVE=OFF
    -DGGML_CPU_KLEIDIAI=OFF
    -DGGML_LTO=OFF
  )
else
  profile_flags+=(
    -DGGML_NATIVE=ON
    -DGGML_CPU_KLEIDIAI=ON
    -DGGML_LTO=ON
  )
fi

cmake -S "${SOURCE_DIR}" -B "${build_dir}" "${common_flags[@]}" "${profile_flags[@]}"
cmake --build "${build_dir}" --config Release -j "${JOBS}"

timestamp="$(date +%Y%m%d_%H%M%S)"
bundle_dir="${OUTPUT_ROOT}/llama_server_bundle_${timestamp}_${PROFILE}"
mkdir -p "${bundle_dir}/bin" "${bundle_dir}/lib"

cp -f "${build_dir}/bin/llama-server" "${bundle_dir}/bin/"
if [ -x "${build_dir}/bin/llama-bench" ]; then
  cp -f "${build_dir}/bin/llama-bench" "${bundle_dir}/bin/"
fi

shopt -s nullglob
for so in "${build_dir}/bin/"*.so* "${build_dir}/lib/"*.so*; do
  cp -P "${so}" "${bundle_dir}/lib/"
done
shopt -u nullglob

copy_runtime_deps "${bundle_dir}/lib" "${bundle_dir}/bin/llama-server" "${bundle_dir}/bin/llama-bench" "${bundle_dir}/lib/"*.so*
write_launchers "${bundle_dir}"

llama_commit="$(git -C "${SOURCE_DIR}" rev-parse --short HEAD 2>/dev/null || true)"
llama_version="$("${bundle_dir}/run-llama-server.sh" --version 2>&1 | head -n 3 || true)"
cat > "${bundle_dir}/README.txt" <<EOF
Portable llama-server bundle (Raspberry Pi / aarch64)

Profile: ${PROFILE}
Build host model: ${pi_model:-unknown}
Build host arch: ${arch}
Built at: $(date -Iseconds)
llama.cpp commit: ${llama_commit:-unknown}

CMake flags:
${common_flags[*]} ${profile_flags[*]}

Version:
${llama_version}

Contents:
- bin/llama-server
- bin/llama-bench (if built)
- lib/*.so runtime dependencies
- run-llama-server.sh launcher (sets LD_LIBRARY_PATH to bundled libs)
- run-llama-bench.sh launcher (sets LD_LIBRARY_PATH to bundled libs)
EOF

printf 'Built bundle: %s\n' "${bundle_dir}"
printf 'Profile: %s\n' "${PROFILE}"
printf 'Version:\n%s\n' "${llama_version}"
