from __future__ import annotations

from pathlib import Path

from core.main import CHAT_HTML, WEB_ASSETS_DIR

CHAT_CSS = (WEB_ASSETS_DIR / "chat.css").read_text(encoding="utf-8")
CHAT_JS = (WEB_ASSETS_DIR / "chat.js").read_text(encoding="utf-8")
SHELL_JS = (WEB_ASSETS_DIR / "shell.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "shell.js").exists() else ""
CHAT_STATE_JS = (WEB_ASSETS_DIR / "state.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "state.js").exists() else ""
CHAT_UTILS_JS = (WEB_ASSETS_DIR / "utils.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "utils.js").exists() else ""
CHAT_SESSION_JS = (WEB_ASSETS_DIR / "session-manager.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "session-manager.js").exists() else ""
CHAT_STATUS_JS = (WEB_ASSETS_DIR / "status.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "status.js").exists() else ""
CHAT_RUNTIME_UI_JS = (WEB_ASSETS_DIR / "runtime-ui.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "runtime-ui.js").exists() else ""
CHAT_MESSAGES_JS = (WEB_ASSETS_DIR / "messages.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "messages.js").exists() else ""
CHAT_IMAGE_HANDLER_JS = (WEB_ASSETS_DIR / "image-handler.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "image-handler.js").exists() else ""
CHAT_SETTINGS_UI_JS = (WEB_ASSETS_DIR / "settings-ui.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "settings-ui.js").exists() else ""
CHAT_ENGINE_JS = (WEB_ASSETS_DIR / "chat-engine.js").read_text(encoding="utf-8") if (WEB_ASSETS_DIR / "chat-engine.js").exists() else ""
CHAT_UI = CHAT_HTML + CHAT_CSS + CHAT_JS + SHELL_JS + CHAT_STATE_JS + CHAT_UTILS_JS + CHAT_SESSION_JS + CHAT_STATUS_JS + CHAT_RUNTIME_UI_JS + CHAT_MESSAGES_JS + CHAT_IMAGE_HANDLER_JS + CHAT_SETTINGS_UI_JS + CHAT_ENGINE_JS

def test_nginx_config_allows_large_streaming_uploads():
    conf = Path("nginx/potato.conf").read_text(encoding="utf-8")

    assert "client_max_body_size 0;" in conf
    assert "client_body_timeout 3600;" in conf
    assert "proxy_request_buffering off;" in conf
    assert "proxy_buffering off;" in conf


def test_prepare_imager_bundle_script_wires_first_boot_installer():
    """Contract: prepare_imager_bundle.sh wires the first-boot installer flow.

    Kept as text-inspection — the script rsyncs the full repo (including
    models/ and projectors/ which are multi-GB locally), making subprocess
    testing too slow without script-level changes to add test exclusions.
    """
    script = Path("bin/prepare_imager_bundle.sh").read_text(encoding="utf-8")

    assert "--boot-path" in script
    assert "--output-dir" in script
    assert "firstrun.sh" in script
    assert "POTATO_BUNDLE_HOOK_START" in script
    assert "install_potato_from_bundle.sh" in script
    assert "potato_firstrun_hook.sh" in script
    assert "POTATO_LLAMA_BUNDLE_SRC" in script
    assert "bundle_install.done" in script


def test_build_llama_bundle_pi5_script_supports_baseline_and_pi5_opt_profiles():
    script = Path("bin/build_llama_bundle_pi5.sh").read_text(encoding="utf-8")

    assert "--profile baseline|pi5-opt" in script
    assert 'PROFILE="${POTATO_LLAMA_BUILD_PROFILE:-pi5-opt}"' in script
    assert "GGML_CPU_KLEIDIAI=ON" in script
    assert "GGML_NATIVE=ON" in script
    assert "GGML_LTO=ON" in script
    assert "GGML_CPU_KLEIDIAI=OFF" in script
    assert "GGML_BLAS_VENDOR=OpenBLAS" in script
    assert "Raspberry Pi 5" in script


def test_local_image_build_script_collects_artifacts_for_flash_test():
    script = Path("bin/build_local_image.sh").read_text(encoding="utf-8")

    assert "/usr/bin/time -p" in script
    assert "image/build-all.sh" in script
    assert "--variant" in script
    assert "--no-update-pi-gen" in script
    assert "pigen_work" in script
    assert "local-test-" in script
    assert "SHA256SUMS.source" in script
    assert "METADATA.json" in script
    assert "build_command" in script
    assert "README-local-test.txt" in script
    assert 'cat > "${bundle_dir}/README.md"' in script
    assert "Use In Raspberry Pi Imager" in script
    assert "Content Repository" in script
    assert "generate_potato_manifest" in script
    assert ".rpi-imager-manifest" in script
    assert "POTATO_ICON_FILENAME" in script
    assert "--clean-artifacts <mode>" in script
    assert "--clean-artifacts-yes" in script
    assert "--clean-artifacts-no" in script
    assert "Previous artifacts found" in script
    assert "Remove them before build? [y/N]:" in script
    assert "CLEAN_ARTIFACTS_MODE" in script

    # Post-cleanup support (#112) — runs Docker prune directly, not via cleanup script
    assert "--post-cleanup" in script
    assert "docker image prune" in script
    assert "docker builder prune" in script
    assert "docker volume prune" in script


def test_publish_image_release_dry_run_generates_manifest(tmp_path: Path):
    """--dry-run validates the bundle and generates a manifest without publishing."""
    import os
    import subprocess

    import lzma

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    img = bundle / "potato-lite-test.img.xz"
    img.write_bytes(lzma.compress(b"\x00" * 1024))
    icon_src = Path("bin/assets/potato-imager-icon.svg")
    if icon_src.exists():
        (bundle / "potato-imager-icon.svg").write_text(icon_src.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        (bundle / "potato-imager-icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Potato OS</text></svg>')
    (bundle / "SHA256SUMS").write_text("abcdef1234567890  potato-lite-test.img.xz\n")

    env = os.environ.copy()
    env["POTATO_GITHUB_REPO"] = "test/repo"
    result = subprocess.run(
        [
            str(Path("bin/publish_image_release.sh").resolve()),
            "--version", "v0.6.0",
            "--bundle-dir", str(bundle),
            "--variant", "lite",
            "--dry-run",
        ],
        check=True,
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    assert "No release created" in result.stdout
    assert "manifest" in result.stdout.lower() or "Manifest" in result.stdout


def test_publish_image_release_dry_run_rejects_missing_image(tmp_path: Path):
    """Empty bundle dir with no image must fail."""
    import os
    import subprocess

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    env = os.environ.copy()
    env["POTATO_GITHUB_REPO"] = "test/repo"
    result = subprocess.run(
        [
            str(Path("bin/publish_image_release.sh").resolve()),
            "--version", "v0.6.0",
            "--bundle-dir", str(bundle),
            "--dry-run",
        ],
        check=False,
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_clean_image_build_artifacts_script_cleans_outputs_and_optional_caches():
    script = Path("bin/clean_image_build_artifacts.sh").read_text(encoding="utf-8")

    assert "output/images" in script
    assert ".cache/potato-image-build" in script
    assert ".cache/potato-image-cache" in script
    assert ".cache/pi-gen-arm64" in script
    assert "--deep" in script
    assert "--include-download-cache" in script
    assert "--include-pi-gen-checkout" in script
    assert "docker container inspect" in script
    assert "pigen_work potato-pigen-lite potato-pigen-full" in script
    assert "find \"${target}\" -mindepth 1 -maxdepth 1 -exec rm -rf {} +" in script

    # Docker prune support (#112)
    assert "--docker-prune" in script
    assert "docker image prune" in script
    assert "docker builder prune" in script
    assert "docker volume prune" in script
    assert "--filter" in script


def test_chat_html_loads_local_markdown_assets_and_renders_assistant_markdown():
    assert '<script src="/assets/vendor/marked.umd.js"></script>' in CHAT_HTML
    assert '<script src="/assets/vendor/purify.min.js"></script>' in CHAT_HTML
    assert "function renderAssistantMarkdownToHtml(text)" in CHAT_UI
    assert "window.marked?.parse" in CHAT_UI
    assert "window.DOMPurify?.sanitize" in CHAT_UI
    assert "ALLOWED_TAGS" in CHAT_UI
    assert "ALLOWED_ATTR" in CHAT_UI
    assert '"img",' not in CHAT_MESSAGES_JS
    assert "'img'," not in CHAT_MESSAGES_JS
    assert "USE_PROFILES: { html: true }" not in CHAT_MESSAGES_JS
    assert "bubble.innerHTML = sanitizedHtml;" in CHAT_UI
    assert "renderBubbleContent(bubble, content, { ...options, role });" in CHAT_UI


def test_imager_manifest_generator_targets_pi5_and_pi4():
    script = Path("bin/generate_imager_manifest.py").read_text(encoding="utf-8")

    assert "pi5-64bit" in script
    assert "pi4-64bit" in script
    assert "Raspberry Pi 5" in script
    assert "Raspberry Pi 4" in script
    assert "rpi-imager-manifest" in script
    assert "extract_sha256" in script
    assert "image_download_sha256" in script
    assert "potato-imager-icon.svg" in script


def test_potato_imager_icon_exists_and_is_valid_svg():
    icon_path = Path("bin/assets/potato-imager-icon.svg")

    assert icon_path.is_file(), "potato imager icon SVG must exist in bin/assets/"
    content = icon_path.read_text(encoding="utf-8")
    assert content.strip().startswith("<svg")
    assert "Potato OS" in content


def test_image_build_scripts_exist_for_lite_and_full_variants():
    lite = Path("image/build-lite.sh").read_text(encoding="utf-8")
    full = Path("image/build-full.sh").read_text(encoding="utf-8")
    all_in_one = Path("image/build-all.sh").read_text(encoding="utf-8")
    uv_script = Path("image/build_all.py").read_text(encoding="utf-8")
    common = Path("image/lib/common.sh").read_text(encoding="utf-8")

    assert "run_build lite" in lite
    assert "run_build full" in full
    assert "POTATO_PI_GEN_DIR" in common
    assert "POTATO_SSH_USER" in common
    assert "POTATO_SSH_PASSWORD" in common
    assert "pi" in common
    assert "raspberry" in common
    assert "POTATO_IMAGE_OUTPUT_DIR" in common
    assert "potato-lite" in common
    assert "potato-full" in common
    assert "generate_potato_manifest" in common
    assert "POTATO_ICON_FILENAME" in common
    assert "potato-${variant}.rpi-imager-manifest" in common
    assert "branding.sh" in common
    assert "uv run --script" in all_in_one
    assert "--variant" in uv_script
    assert "https://github.com/RPi-Distro/pi-gen.git" in uv_script
    assert "POTATO_PI_GEN_DIR" in uv_script
    assert "POTATO_PI_GEN_USE_DOCKER" in uv_script
    assert "build-docker.sh" in common
    assert "DOCKER_BUILDKIT=0" in common
    assert "container_name=\"potato-pigen-${variant}\"" in common
    assert "docker rm -f" in common
    assert "Git does not preserve directory modes" in common
    assert "chmod 0755 \"${files_root}/opt\"" in common
    assert "${potato_root}/runtimes" in common
    assert "runtimes/${slot_name}" in common
    assert "docker context use default" in uv_script
    assert "colima start" in uv_script
    assert "except RuntimeError as exc" in uv_script
    assert "except subprocess.CalledProcessError as exc" in uv_script
    assert "--setup-docker" in uv_script
    assert 'run(["brew", "install", "docker", "colima"])' in uv_script

    # Docker space preflight (#112)
    assert "check_docker_disk_space" in uv_script
    assert "POTATO_SKIP_SPACE_PREFLIGHT" in uv_script
    assert "POTATO_DOCKER_MIN_SPACE_GB" in uv_script
    assert "POTATO_DOCKER_WARN_SPACE_GB" in uv_script
    assert "docker system prune" in uv_script
    assert "colima stop" in uv_script


def test_manual_qa_scripts_exist_for_fake_and_real_flows():
    fake_script = Path("fake_manual_qa").read_text(encoding="utf-8")
    real_script = Path("real_manual_qa").read_text(encoding="utf-8")
    lite_script = Path("lite_real_manual_qa").read_text(encoding="utf-8")

    assert "POTATO_CHAT_BACKEND=fake" in fake_script
    assert "POTATO_ALLOW_FAKE_FALLBACK=1" in fake_script
    assert "uvicorn app.main:app" in fake_script
    assert 'HOST="${POTATO_QA_HOST:-127.0.0.1}"' in fake_script
    assert 'URL="http://${HOST}:${PORT}"' in fake_script
    assert "Stopping existing process(es) on port" in fake_script
    assert "Press Ctrl+C to stop the server." in fake_script
    assert "open_url" in fake_script

    assert "tests/e2e/smoke_pi.sh" in real_script
    assert "potato.local" in real_script
    assert 'REAL_QA_MODE="${POTATO_REAL_QA_MODE:-full}"' in real_script
    assert 'PI_USER="${PI_USER:-pi}"' in real_script
    assert 'PI_PASSWORD="${PI_PASSWORD:-raspberry}"' in real_script
    assert 'MEMORY_PREFLIGHT="${POTATO_QA_MEMORY_PREFLIGHT:-1}"' in real_script
    assert 'SWAP_RECLAIM="${POTATO_QA_SWAP_RECLAIM:-0}"' in real_script
    assert "run_memory_preflight" in real_script
    assert "drop caches + safe swap reset" in real_script
    assert "/sbin/swapon -a || swapon -a || true" in real_script
    assert "systemd-zram-setup@zram0.service" in real_script
    assert "POTATO_QA_RESET_SSH_HOST_KEYS" in real_script
    assert "Resetting stale SSH host keys for QA targets..." in real_script
    assert "ssh-keygen -R" in real_script
    assert 'PI_SSH_OPTIONS="${PI_SSH_OPTIONS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o GlobalKnownHostsFile=/dev/null -o LogLevel=ERROR}"' in real_script
    assert 'PI_SSH_OPTIONS="${PI_SSH_OPTIONS}"' in real_script
    assert 'QA_HOST="${PI_HOST_PRIMARY}"' in real_script
    assert 'PI_HOST_PRIMARY="${QA_HOST}"' in real_script
    assert 'TARGET_URL="${PI_SCHEME}://${QA_HOST}"' in real_script
    assert "open_url" in real_script

    assert "Fast real-Pi QA (no apt/install/model sync)" in lite_script
    assert 'PI_QA_PORT="${PI_QA_PORT:-1984}"' in lite_script
    assert 'MEMORY_PREFLIGHT="${POTATO_QA_MEMORY_PREFLIGHT:-1}"' in lite_script
    assert 'SWAP_RECLAIM="${POTATO_QA_SWAP_RECLAIM:-0}"' in lite_script
    assert "run_memory_preflight" in lite_script
    assert "drop caches + safe swap reset" in lite_script
    assert "/sbin/swapon -a || swapon -a || true" in lite_script
    assert "systemd-zram-setup@zram0.service" in lite_script
    assert "POTATO_ENABLE_ORCHESTRATOR=0" in lite_script
    assert "POTATO_LLAMA_BASE_URL=http://127.0.0.1:8080" in lite_script
    assert "/opt/potato/venv/bin/uvicorn app.main:app" in lite_script
    assert "open_when_ready" in lite_script


def test_image_stage_assets_define_systemd_firstboot_image_flow():
    packages = Path("image/stage-potato/00-potato/00-packages").read_text(encoding="utf-8")
    run_script = Path("image/stage-potato/00-potato/00-run.sh").read_text(encoding="utf-8")
    prerun_script = Path("image/stage-potato/prerun.sh").read_text(encoding="utf-8")
    export_marker = Path("image/stage-potato/EXPORT_IMAGE").read_text(encoding="utf-8")

    assert "avahi-daemon" in packages
    assert "nginx" in packages
    assert "python3-venv" in packages
    assert 'rsync -a files/ "${ROOTFS_DIR}/"' in run_script
    assert '"${ROOTFS_DIR}/opt/potato"' in run_script
    assert 'chmod 0755 "${ROOTFS_DIR}/opt" "${ROOTFS_DIR}/opt/potato"' in run_script
    assert "on_chroot <<'EOF'" in run_script
    assert "systemctl enable potato-firstboot.service potato.service nginx avahi-daemon" in run_script
    assert "potato-firstboot.service" in run_script
    assert "potato.service" in run_script
    assert "potato-runtime-reset.service" in run_script
    assert "/etc/sudoers.d/potato-runtime-reset" in run_script
    assert "systemctl start --no-block potato-runtime-reset.service" in run_script
    assert "potato.local" in run_script
    assert "usermod -a -G video potato" in run_script
    assert "chmod 0755 /opt /opt/potato" in run_script
    assert 'printf \'potato\\n\' > "${ROOTFS_DIR}/etc/hostname"' in run_script
    assert "127.0.1.1 potato.local potato" in run_script
    assert "host-name=potato" in run_script
    assert "copy_previous" in prerun_script
    assert export_marker.strip() == ""


def test_firstboot_script_enforces_potato_hostname_and_avahi_refresh():
    firstboot = Path("bin/firstboot.sh").read_text(encoding="utf-8")

    assert 'POTATO_HOSTNAME="${POTATO_HOSTNAME:-potato}"' in firstboot
    assert 'POTATO_ENFORCE_HOSTNAME="${POTATO_ENFORCE_HOSTNAME:-1}"' in firstboot
    assert "hostnamectl set-hostname" in firstboot
    assert '"127.0.1.1 " hostname ".local " hostname' in firstboot
    assert "avahi-daemon.conf" in firstboot
    assert "host-name=${POTATO_HOSTNAME}" in firstboot
    assert "systemctl restart avahi-daemon" in firstboot
    assert "chmod 0755 /opt" in firstboot
    assert 'chmod 0755 "${POTATO_BASE_DIR}"' in firstboot


def test_image_build_guide_exists_with_required_sections():
    guide = Path("docs/building-images.md").read_text(encoding="utf-8")

    # Key sections exist
    assert "# Building Potato OS Images" in guide
    assert "Prerequisites" in guide
    assert "Build a Potato OS image" in guide
    assert "Flash the image" in guide

    # Primary entry point is build_local_image.sh
    assert "build_local_image.sh" in guide
    assert "--setup-docker" in guide

    # Flashing methods documented
    assert "dd " in guide or "Raspberry Pi Imager" in guide
    assert "rpi-imager-manifest" in guide

    # Default image is "Potato OS", not leading with "lite"
    assert guide.index("Potato OS") < guide.index("lite")

    # Variants explained
    assert "full" in guide
    assert "--variant full" in guide

    # Cleanup documented
    assert "clean_image_build_artifacts" in guide

    # Troubleshooting section
    assert "Troubleshoot" in guide or "troubleshoot" in guide

    # Disk space recovery guidance (#112)
    assert "Disk space" in guide or "disk space" in guide
    assert "docker system prune" in guide
    assert "colima stop" in guide
    assert "POTATO_SKIP_SPACE_PREFLIGHT" in guide
    assert "--docker-prune" in guide
    assert "--post-cleanup" in guide


def test_readme_links_to_image_build_guide():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "docs/building-images.md" in readme
    assert "build_local_image.sh" in readme


def test_build_local_image_defaults_to_lite_variant():
    script = Path("bin/build_local_image.sh").read_text(encoding="utf-8")

    assert 'VARIANT="lite"' in script


def test_gitignore_excludes_large_artifacts_and_model_downloads():
    ignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "models/" in ignore
    assert "output/" in ignore
    assert ".cache/potato-image-build/" in ignore
    assert ".cache/potato-image-cache/" in ignore
    assert ".cache/pi-gen-arm64/" in ignore


def test_web_favicon_exists_and_html_wires_it():
    favicon_path = WEB_ASSETS_DIR / "favicon.svg"
    assert favicon_path.exists(), "core/assets/favicon.svg must exist"

    assert '<link rel="icon"' in CHAT_HTML
    assert 'href="/assets/favicon.svg"' in CHAT_HTML

    imager_icon = Path("bin/assets/potato-imager-icon.svg").read_text(encoding="utf-8")
    assert favicon_path.read_text(encoding="utf-8") == imager_icon, (
        "web favicon should match bin/assets/potato-imager-icon.svg"
    )

