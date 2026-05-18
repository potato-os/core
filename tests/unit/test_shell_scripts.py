from __future__ import annotations

import json
import os
import subprocess

import pytest
from pathlib import Path

from tests.unit.conftest import REPO_ROOT, write_stub


def test_install_dev_reads_family_from_bundle_runtime_json_when_bundle_src_set():
    """When POTATO_LLAMA_BUNDLE_SRC is set, install_dev.sh must derive the family from the bundle's runtime.json."""
    script = (REPO_ROOT / "bin" / "install_dev.sh").read_text(encoding="utf-8")

    import re
    auto_detect_block = re.search(
        r'if\s.*POTATO_LLAMA_RUNTIME_FAMILY.*?fi',
        script,
        re.DOTALL,
    )
    assert auto_detect_block, "auto-detect block not found in install_dev.sh"
    block_text = auto_detect_block.group(0)
    assert "POTATO_LLAMA_BUNDLE_SRC" in block_text, (
        "install_dev.sh auto-detect block must check POTATO_LLAMA_BUNDLE_SRC"
    )
    # Must read family from the bundle, not hardcode it
    assert "runtime.json" in block_text, (
        "When POTATO_LLAMA_BUNDLE_SRC is set, install_dev.sh must read the family "
        "from the bundle's runtime.json, not hardcode a default"
    )
    # Must not depend on jq — it's installed later in the script via apt-get
    assert "jq " not in block_text, (
        "Bundle family detection must not use jq — it runs before apt-get install"
    )


def test_shell_scripts_do_not_use_local_outside_functions():
    """Trixie bash is strict about 'local' outside functions — catch this at test time."""
    scripts = [
        REPO_ROOT / "bin" / "install_dev.sh",
        REPO_ROOT / "bin" / "uninstall_dev.sh",
    ]
    import re
    for script_path in scripts:
        lines = script_path.read_text(encoding="utf-8").splitlines()
        in_function = 0
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if re.match(r"^[\w_]+\s*\(\)\s*\{", stripped) or stripped.endswith("() {"):
                in_function += 1
            if stripped == "}":
                in_function = max(0, in_function - 1)
            if stripped.startswith("local ") and in_function == 0:
                raise AssertionError(
                    f"{script_path.name}:{i}: 'local' used outside a function: {stripped!r}"
                )


def test_image_stage_nginx_symlink_points_to_runtime_path():
    stage_script = (REPO_ROOT / "image" / "stage-potato" / "00-potato" / "00-run.sh").read_text(encoding="utf-8")

    assert 'ln -sf /etc/nginx/sites-available/potato "${ROOTFS_DIR}/etc/nginx/sites-enabled/potato"' in stage_script
    assert 'ln -sf "${ROOTFS_DIR}/etc/nginx/sites-available/potato" "${ROOTFS_DIR}/etc/nginx/sites-enabled/potato"' not in stage_script


def test_ensure_model_script_reports_insufficient_storage_errors():
    script = (REPO_ROOT / "bin" / "ensure_model.sh").read_text(encoding="utf-8")
    assert "insufficient_storage" in script
    assert "No space left on device" in script


def test_ensure_model_script_keeps_shell_functions_outside_python_heredoc():
    script = (REPO_ROOT / "bin" / "ensure_model.sh").read_text(encoding="utf-8")
    start = script.find("<<'PY'")
    assert start != -1
    end = script.find("\nPY\n", start)
    assert end != -1
    python_block = script[start:end]
    assert "free_space_bytes()" not in python_block


def test_build_llama_runtime_collects_nested_shared_libraries():
    """Upstream may place required .so files outside top-level build lib/bin dirs."""
    script = (REPO_ROOT / "bin" / "build_llama_runtime.sh").read_text(encoding="utf-8")

    assert "find \"${build_dir}\"" in script
    assert "\\( -type f -o -type l \\)" in script
    assert "cp -L \"${so}\" \"${slot_dir}/lib/${base}\"" in script
    assert "libmtmd*.so*" in script
    assert "libllama*.so*" in script
    assert "libggml*.so*" in script


def test_remote_runtime_build_syncs_install_requirements():
    """The remote build checkout must be complete enough to run install_dev.sh."""
    script = (REPO_ROOT / "bin" / "build_and_publish_remote.sh").read_text(encoding="utf-8")

    assert "--include 'requirements.txt'" in script


def test_install_dev_installs_litert_native_runtime_dependencies():
    """LiteRT-LM 0.12 needs libvulkan.so.1 available even for CPU startup."""
    script = (REPO_ROOT / "bin" / "install_dev.sh").read_text(encoding="utf-8")

    assert "libvulkan1" in script


def test_uninstall_script_executes_pi_cleanup_without_package_removal(tmp_path: Path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    calls = tmp_path / "calls.log"

    write_stub(
        fakebin / "sudo",
        """#!/usr/bin/env bash
set -euo pipefail
echo "sudo $*" >> "$CALLS_FILE"
args=()
skip_next=0
for a in "$@"; do
  if [ "$skip_next" -eq 1 ]; then
    skip_next=0
    continue
  fi
  case "$a" in
    -S) ;;
    -p) skip_next=1 ;;
    *) args+=("$a") ;;
  esac
done
"${args[@]}"
""",
    )
    write_stub(
        fakebin / "systemctl",
        """#!/usr/bin/env bash
set -euo pipefail
echo "systemctl $*" >> "$CALLS_FILE"
""",
    )
    write_stub(
        fakebin / "rm",
        """#!/usr/bin/env bash
set -euo pipefail
echo "rm $*" >> "$CALLS_FILE"
""",
    )
    write_stub(
        fakebin / "id",
        """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "-u" ] && [ "${2:-}" = "potato" ]; then
  echo 1001
  exit 0
fi
exit 1
""",
    )
    write_stub(
        fakebin / "getent",
        """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "group" ] && [ "${2:-}" = "potato" ]; then
  echo "potato:x:999:"
  exit 0
fi
exit 2
""",
    )
    write_stub(
        fakebin / "userdel",
        """#!/usr/bin/env bash
set -euo pipefail
echo "userdel $*" >> "$CALLS_FILE"
""",
    )
    write_stub(
        fakebin / "groupdel",
        """#!/usr/bin/env bash
set -euo pipefail
echo "groupdel $*" >> "$CALLS_FILE"
""",
    )
    write_stub(
        fakebin / "apt-get",
        """#!/usr/bin/env bash
set -euo pipefail
echo "apt-get $*" >> "$CALLS_FILE"
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fakebin}:{env.get('PATH', '')}"
    env["CALLS_FILE"] = str(calls)
    env["PI_PASSWORD"] = "raspberry"
    env["POTATO_TARGET_ROOT"] = "/opt/potato-test"

    subprocess.run([str(REPO_ROOT / "bin" / "uninstall_dev.sh")], check=True, cwd=REPO_ROOT, env=env)

    log = calls.read_text(encoding="utf-8")
    assert "systemctl disable --now potato.service potato-firstboot.service potato-runtime-reset.service" in log
    assert "systemctl daemon-reload" in log
    assert "rm -f /etc/sudoers.d/potato-runtime-reset" in log
    assert "rm -rf /opt/potato-test /tmp/potato-os" in log
    assert "userdel potato" in log
    assert "groupdel potato" in log
    assert "apt-get" not in log


def test_uninstall_script_can_optionally_remove_packages(tmp_path: Path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    calls = tmp_path / "calls.log"

    write_stub(
        fakebin / "sudo",
        """#!/usr/bin/env bash
set -euo pipefail
echo "sudo $*" >> "$CALLS_FILE"
args=()
skip_next=0
for a in "$@"; do
  if [ "$skip_next" -eq 1 ]; then
    skip_next=0
    continue
  fi
  case "$a" in
    -S) ;;
    -p) skip_next=1 ;;
    *) args+=("$a") ;;
  esac
done
"${args[@]}"
""",
    )
    for cmd in ("systemctl", "rm", "userdel", "groupdel", "apt-get"):
        write_stub(
            fakebin / cmd,
            f"""#!/usr/bin/env bash
set -euo pipefail
echo "{cmd} $*" >> "$CALLS_FILE"
""",
        )

    write_stub(
        fakebin / "id",
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )
    write_stub(
        fakebin / "getent",
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fakebin}:{env.get('PATH', '')}"
    env["CALLS_FILE"] = str(calls)
    env["PI_PASSWORD"] = "raspberry"
    env["REMOVE_PACKAGES"] = "1"

    subprocess.run([str(REPO_ROOT / "bin" / "uninstall_dev.sh")], check=True, cwd=REPO_ROOT, env=env)

    log = calls.read_text(encoding="utf-8")
    assert "apt-get remove --purge -y avahi-daemon nginx jq" in log
    assert "apt-get autoremove -y" in log


# ---------------------------------------------------------------------------
# start_llama.sh thin wrapper tests
#
# Business logic (arg construction) is tested in test_launch_config.py.
# These tests only verify the shell wrapper forwards args and sets up the
# dynamic library path correctly.
# ---------------------------------------------------------------------------


def test_start_llama_wrapper_forwards_args_to_exec(tmp_path: Path):
    """The thin wrapper must exec the command it receives as $@."""
    args_out = tmp_path / "args.txt"

    write_stub(
        tmp_path / "fake-llama-server",
        """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$ARGS_OUT"
""",
    )

    env = os.environ.copy()
    env["POTATO_SLOT_SAVE_PATH"] = str(tmp_path / "llama-slots")
    env["ARGS_OUT"] = str(args_out)

    subprocess.run(
        [
            str(REPO_ROOT / "bin" / "start_llama.sh"),
            str(tmp_path / "fake-llama-server"),
            "--model", "/model.gguf",
            "--host", "0.0.0.0",
        ],
        check=True, cwd=REPO_ROOT, env=env,
    )

    args = args_out.read_text(encoding="utf-8").splitlines()
    assert args[0] == "--model"
    assert args[1] == "/model.gguf"
    assert args[2] == "--host"
    assert args[3] == "0.0.0.0"


def test_start_llama_wrapper_sets_ld_library_path(tmp_path: Path):
    """The wrapper must export LD_LIBRARY_PATH when runtime lib/ exists."""
    lib_dir = tmp_path / "llama" / "lib"
    lib_dir.mkdir(parents=True)
    env_out = tmp_path / "env.txt"

    write_stub(
        tmp_path / "fake-llama-server",
        """#!/usr/bin/env bash
echo "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}" > "$ENV_OUT"
echo "GGML_BACKEND_DIR=${GGML_BACKEND_DIR:-}" >> "$ENV_OUT"
""",
    )

    env = os.environ.copy()
    env["POTATO_LLAMA_RUNTIME_DIR"] = str(tmp_path / "llama")
    env["POTATO_SLOT_SAVE_PATH"] = str(tmp_path / "llama-slots")
    env["ENV_OUT"] = str(env_out)

    subprocess.run(
        [
            str(REPO_ROOT / "bin" / "start_llama.sh"),
            str(tmp_path / "fake-llama-server"),
        ],
        check=True, cwd=REPO_ROOT, env=env,
    )

    env_text = env_out.read_text(encoding="utf-8")
    assert str(lib_dir) in env_text
    assert f"GGML_BACKEND_DIR={lib_dir}" in env_text


def test_start_llama_wrapper_fails_without_args(tmp_path: Path):
    """The wrapper must exit non-zero when called with no arguments."""
    env = os.environ.copy()
    env["POTATO_SLOT_SAVE_PATH"] = str(tmp_path / "llama-slots")
    result = subprocess.run(
        [str(REPO_ROOT / "bin" / "start_llama.sh")],
        cwd=REPO_ROOT, env=env, capture_output=True,
    )
    assert result.returncode != 0
    assert b"no arguments" in result.stderr


# Old test_start_llama_* shell contract tests removed — business logic now
# tested in tests/unit/test_launch_config.py (pure Python, no subprocess).


# ---------------------------------------------------------------------------
# Pi-hole installation section in install_dev.sh (#212)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Selective app deployment
# ---------------------------------------------------------------------------

def test_install_dev_uses_selective_app_deployment():
    """install_dev.sh must deploy apps selectively via POTATO_IMAGE_APPS."""
    script = (REPO_ROOT / "bin" / "install_dev.sh").read_text(encoding="utf-8")
    assert "POTATO_IMAGE_APPS" in script
    assert "_selected_apps" in script


def test_install_dev_runs_app_install_hooks():
    """install_dev.sh must run apps/{app}/install.sh for each selected app."""
    script = (REPO_ROOT / "bin" / "install_dev.sh").read_text(encoding="utf-8")
    assert "install.sh" in script
    assert "_app_installer" in script or "install.sh" in script


def test_install_dev_supports_external_apps_repo():
    """install_dev.sh must support POTATO_APPS_REPO for non-core apps."""
    script = (REPO_ROOT / "bin" / "install_dev.sh").read_text(encoding="utf-8")
    assert "POTATO_APPS_REPO" in script
    assert "APPS_REPO" in script


def test_install_dev_external_repo_takes_precedence():
    """External apps repo must be checked before REPO_ROOT/apps/."""
    script = (REPO_ROOT / "bin" / "install_dev.sh").read_text(encoding="utf-8")
    ext_idx = script.index("APPS_REPO")
    repo_root_idx = script.index('REPO_ROOT}/apps/${_app_name}')
    assert ext_idx < repo_root_idx, "APPS_REPO must be checked before REPO_ROOT"


def test_prepare_imager_supports_external_apps_repo():
    """prepare_imager_bundle.sh must support POTATO_APPS_REPO for non-core apps."""
    script = (REPO_ROOT / "bin" / "prepare_imager_bundle.sh").read_text(encoding="utf-8")
    assert "POTATO_APPS_REPO" in script
    assert "APPS_REPO" in script


def test_install_dev_aborts_on_missing_app():
    """install_dev.sh must exit 1 when a selected app directory is not found."""
    script = (REPO_ROOT / "bin" / "install_dev.sh").read_text(encoding="utf-8")
    assert "exit 1" in script
    # The error path must print ERROR, not just a warning
    assert "ERROR: app directory not found" in script


def test_prepare_imager_aborts_on_missing_app():
    """prepare_imager_bundle.sh must exit 1 when a selected app is missing from payload."""
    script = (REPO_ROOT / "bin" / "prepare_imager_bundle.sh").read_text(encoding="utf-8")
    assert "ERROR: selected app missing from payload" in script
    assert "exit 1" in script
