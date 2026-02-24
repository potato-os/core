from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_shell_scripts_have_valid_bash_syntax():
    scripts = [
        REPO_ROOT / "bin" / "run.sh",
        REPO_ROOT / "bin" / "prepare_imager_bundle.sh",
        REPO_ROOT / "bin" / "ensure_model.sh",
        REPO_ROOT / "bin" / "start_llama.sh",
        REPO_ROOT / "bin" / "reset_runtime.sh",
        REPO_ROOT / "bin" / "firstboot.sh",
        REPO_ROOT / "bin" / "install_dev.sh",
        REPO_ROOT / "bin" / "uninstall_dev.sh",
        REPO_ROOT / "image" / "build-lite.sh",
        REPO_ROOT / "image" / "build-full.sh",
        REPO_ROOT / "image" / "build-all.sh",
        REPO_ROOT / "image" / "lib" / "common.sh",
        REPO_ROOT / "image" / "stage-potato" / "prerun.sh",
        REPO_ROOT / "image" / "stage-potato" / "00-potato" / "00-run.sh",
        REPO_ROOT / "tests" / "e2e" / "smoke_pi.sh",
        REPO_ROOT / "tests" / "e2e" / "stream_chat_pi.sh",
        REPO_ROOT / "tests" / "e2e" / "vision_multi_image_pi.sh",
        REPO_ROOT / "tests" / "e2e" / "uninstall_pi.sh",
    ]

    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True, cwd=REPO_ROOT)


def test_image_stage_nginx_symlink_points_to_runtime_path():
    stage_script = (REPO_ROOT / "image" / "stage-potato" / "00-potato" / "00-run.sh").read_text(encoding="utf-8")

    assert 'ln -sf /etc/nginx/sites-available/potato "${ROOTFS_DIR}/etc/nginx/sites-enabled/potato"' in stage_script
    assert 'ln -sf "${ROOTFS_DIR}/etc/nginx/sites-available/potato" "${ROOTFS_DIR}/etc/nginx/sites-enabled/potato"' not in stage_script


def test_uninstall_script_executes_pi_cleanup_without_package_removal(tmp_path: Path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    calls = tmp_path / "calls.log"

    _write_stub(
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
    _write_stub(
        fakebin / "systemctl",
        """#!/usr/bin/env bash
set -euo pipefail
echo "systemctl $*" >> "$CALLS_FILE"
""",
    )
    _write_stub(
        fakebin / "rm",
        """#!/usr/bin/env bash
set -euo pipefail
echo "rm $*" >> "$CALLS_FILE"
""",
    )
    _write_stub(
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
    _write_stub(
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
    _write_stub(
        fakebin / "userdel",
        """#!/usr/bin/env bash
set -euo pipefail
echo "userdel $*" >> "$CALLS_FILE"
""",
    )
    _write_stub(
        fakebin / "groupdel",
        """#!/usr/bin/env bash
set -euo pipefail
echo "groupdel $*" >> "$CALLS_FILE"
""",
    )
    _write_stub(
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

    _write_stub(
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
        _write_stub(
            fakebin / cmd,
            f"""#!/usr/bin/env bash
set -euo pipefail
echo "{cmd} $*" >> "$CALLS_FILE"
""",
        )

    _write_stub(
        fakebin / "id",
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )
    _write_stub(
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


def test_start_llama_script_builds_expected_command(tmp_path: Path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    args_out = tmp_path / "args.txt"
    model_path = tmp_path / "model.gguf"
    mmproj_path = tmp_path / "mmproj-Qwen3-VL-4B-Instruct-Q8_0.gguf"
    model_path.write_bytes(b"gguf")
    mmproj_path.write_bytes(b"mmproj")

    _write_stub(
        fakebin / "fake-llama-server",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "$ARGS_OUT"
""",
    )

    env = os.environ.copy()
    env["LLAMA_SERVER_BIN"] = str(fakebin / "fake-llama-server")
    env["POTATO_MODEL_PATH"] = str(model_path)
    env["POTATO_LLAMA_HOST"] = "0.0.0.0"
    env["POTATO_LLAMA_PORT"] = "8080"
    env["POTATO_CTX_SIZE"] = "16384"
    env["POTATO_LLAMA_KV_FLAGS"] = "--cache-type-k q8_0 --cache-type-v q8_0"
    env["POTATO_MMPROJ_PATH"] = str(mmproj_path)
    env["POTATO_AUTO_DOWNLOAD_MMPROJ"] = "0"
    env["POTATO_SLOT_SAVE_PATH"] = str(tmp_path / "llama-slots")
    env["ARGS_OUT"] = str(args_out)

    subprocess.run([str(REPO_ROOT / "bin" / "start_llama.sh")], check=True, cwd=REPO_ROOT, env=env)

    args = args_out.read_text(encoding="utf-8")
    assert "--model" in args
    assert str(model_path) in args
    assert "--mmproj" in args
    assert str(mmproj_path) in args
    assert "--ctx-size" in args
    assert "16384" in args
    assert "--slot-save-path" in args
    assert "--jinja" in args
    assert "--flash-attn" in args
    assert "on" in args


def test_start_llama_script_uses_bundle_runtime_and_prefers_q8_mmproj(tmp_path: Path):
    runtime_dir = tmp_path / "llama"
    runtime_bin = runtime_dir / "bin"
    runtime_lib = runtime_dir / "lib"
    runtime_bin.mkdir(parents=True)
    runtime_lib.mkdir(parents=True)

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model_path = model_dir / "Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
    model_path.write_bytes(b"gguf")
    q8_mmproj = model_dir / "mmproj-Qwen3-VL-4B-Instruct-Q8_0.gguf"
    f16_mmproj = model_dir / "mmproj-Qwen3-VL-4B-Instruct-F16.gguf"
    q8_mmproj.write_bytes(b"q8")
    f16_mmproj.write_bytes(b"f16")

    args_out = tmp_path / "args.txt"
    ld_out = tmp_path / "ld.txt"
    _write_stub(
        runtime_bin / "llama-server",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" > "$ARGS_OUT"
printf '%s\\n' "${LD_LIBRARY_PATH:-}" > "$LD_OUT"
""",
    )

    env = os.environ.copy()
    env["POTATO_BASE_DIR"] = str(tmp_path)
    env["POTATO_LLAMA_RUNTIME_DIR"] = str(runtime_dir)
    env["POTATO_MODEL_PATH"] = str(model_path)
    env["POTATO_AUTO_DOWNLOAD_MMPROJ"] = "0"
    env["ARGS_OUT"] = str(args_out)
    env["LD_OUT"] = str(ld_out)

    subprocess.run([str(REPO_ROOT / "bin" / "start_llama.sh")], check=True, cwd=REPO_ROOT, env=env)

    args = args_out.read_text(encoding="utf-8")
    ld_library_path = ld_out.read_text(encoding="utf-8").strip()

    assert "--mmproj" in args
    assert str(q8_mmproj) in args
    assert str(f16_mmproj) not in args
    assert ld_library_path.startswith(str(runtime_lib))


def test_generate_imager_manifest_script_outputs_pi5_manifest(tmp_path: Path):
    image_path = tmp_path / "potato-lite-test.img"
    image_path.write_bytes(b"potato-os")
    manifest_path = tmp_path / "potato-lite.rpi-imager-manifest"

    subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "bin" / "generate_imager_manifest.py"),
            "--image",
            str(image_path),
            "--output",
            str(manifest_path),
            "--name",
            "Potato OS Test",
        ],
        check=True,
        cwd=REPO_ROOT,
    )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "imager" in payload
    assert "os_list" in payload
    assert payload["os_list"][0]["name"] == "Potato OS Test"
    assert payload["os_list"][0]["devices"] == ["pi5-64bit"]
    assert payload["os_list"][0]["init_format"] == "systemd"
    assert payload["os_list"][0]["architecture"] == "armv8"
    assert payload["os_list"][0]["extract_size"] == image_path.stat().st_size
    assert payload["os_list"][0]["url"].startswith("file://")


def test_vision_e2e_script_exercises_multimodal_requests():
    script = (REPO_ROOT / "tests" / "e2e" / "vision_multi_image_pi.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "accept: application/json" in script
    assert "type: \"image_url\"" in script
    assert "data:${mime};base64," in script
    assert "cat|https://upload.wikimedia.org/" in script
    assert "dog|https://upload.wikimedia.org/" in script
    assert "elephant|https://upload.wikimedia.org/" in script
    assert "Vision E2E passed" in script


def test_image_build_lite_dry_run_writes_manifest_and_stage(tmp_path: Path):
    pigen_dir = tmp_path / "pi-gen"
    pigen_dir.mkdir()
    _write_stub(
        pigen_dir / "build.sh",
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )

    llama_bundle = tmp_path / "llama_bundle"
    (llama_bundle / "bin").mkdir(parents=True)
    (llama_bundle / "lib").mkdir(parents=True)
    _write_stub(llama_bundle / "bin" / "llama-server", "#!/usr/bin/env bash\nexit 0\n")

    output_dir = tmp_path / "out"
    env = os.environ.copy()
    env["POTATO_PI_GEN_DIR"] = str(pigen_dir)
    env["POTATO_IMAGE_OUTPUT_DIR"] = str(output_dir)
    env["POTATO_IMAGE_BUILD_ROOT"] = str(tmp_path / "build-root")
    env["POTATO_IMAGE_DRY_RUN"] = "1"
    env["POTATO_LLAMA_BUNDLE_SRC"] = str(llama_bundle)
    env["POTATO_SSH_USER"] = "pi"
    env["POTATO_SSH_PASSWORD"] = "raspberry"
    env["POTATO_HOSTNAME"] = "potato"

    subprocess.run([str(REPO_ROOT / "image" / "build-lite.sh")], check=True, cwd=REPO_ROOT, env=env)

    manifest = output_dir / "potato-lite-build-info.json"
    stage_marker = output_dir / "potato-lite-stage-path.txt"
    config_copy = output_dir / "potato-lite-config.txt"

    assert manifest.exists()
    assert stage_marker.exists()
    assert config_copy.exists()

    manifest_text = manifest.read_text(encoding="utf-8")
    assert '"variant": "lite"' in manifest_text
    assert '"ssh_user": "pi"' in manifest_text
    assert '"hostname": "potato"' in manifest_text
    assert '"includes_model": false' in manifest_text

    config_text = config_copy.read_text(encoding="utf-8")
    assert "FIRST_USER_NAME=pi" in config_text
    assert "FIRST_USER_PASS=raspberry" in config_text
    assert "TARGET_HOSTNAME=potato" in config_text
