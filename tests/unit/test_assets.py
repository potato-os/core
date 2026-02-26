from __future__ import annotations

from pathlib import Path

from app.main import CHAT_HTML


def test_start_llama_contains_required_flags():
    script = Path("bin/start_llama.sh").read_text(encoding="utf-8")

    assert "--ctx-size" in script
    assert 'CTX_SIZE_DEFAULT="16384"' in script
    assert 'CTX_SIZE="${POTATO_CTX_SIZE:-${CTX_SIZE_DEFAULT}}"' in script
    assert "Applying Qwen3.5-35B-A3B runtime profile" in script
    assert 'CACHE_RAM_MIB="${POTATO_LLAMA_CACHE_RAM_MIB:-0}"' in script
    assert "--cache-ram" in script
    assert "--jinja" in script
    assert "--no-warmup" in script
    assert 'DISABLE_WARMUP="${POTATO_LLAMA_NO_WARMUP:-1}"' in script
    assert "--slot-save-path" in script


def test_run_script_defaults_to_llama_backend_without_fake_fallback():
    script = Path("bin/run.sh").read_text(encoding="utf-8")

    assert 'POTATO_CHAT_BACKEND="${POTATO_CHAT_BACKEND:-llama}"' in script
    assert 'POTATO_ALLOW_FAKE_FALLBACK="${POTATO_ALLOW_FAKE_FALLBACK:-0}"' in script
    assert 'POTATO_CHAT_BACKEND="${POTATO_CHAT_BACKEND:-auto}"' not in script


def test_potato_service_points_to_run_script():
    unit_file = Path("systemd/potato.service").read_text(encoding="utf-8")

    assert "User=potato" in unit_file
    assert "ExecStart=/opt/potato/bin/run.sh" in unit_file
    assert "Restart=always" in unit_file


def test_firstboot_service_avoids_repeating_setup():
    unit_file = Path("systemd/potato-firstboot.service").read_text(encoding="utf-8")

    assert "ConditionPathExists=!/opt/potato/state/firstboot.done" in unit_file
    assert "ExecStart=/opt/potato/bin/firstboot.sh" in unit_file


def test_uninstall_script_targets_pi_runtime_only():
    script = Path("bin/uninstall_dev.sh").read_text(encoding="utf-8")

    assert "systemctl disable --now potato.service potato-firstboot.service potato-runtime-reset.service" in script
    assert "rm -f /etc/systemd/system/potato.service /etc/systemd/system/potato-firstboot.service /etc/systemd/system/potato-runtime-reset.service" in script
    assert "rm -f /etc/sudoers.d/potato-runtime-reset" in script
    assert "rm -rf \"${TARGET_ROOT}\" /tmp/potato-os" in script
    assert "userdel \"${POTATO_USER}\"" in script
    assert "groupdel \"${POTATO_GROUP}\"" in script


def test_smoke_script_retries_connection_refused():
    script = Path("tests/e2e/smoke_pi.sh").read_text(encoding="utf-8")

    assert "--retry-connrefused" in script
    assert "--retry-all-errors" in script
    assert "Syncing repository to Pi (excluding local heavy artifacts)..." in script
    assert "--exclude 'models/'" in script
    assert "--exclude 'node_modules/'" in script
    assert "--exclude 'output/'" in script
    assert 'PI_SSH_OPTIONS="${PI_SSH_OPTIONS:--o StrictHostKeyChecking=accept-new}"' in script
    assert 'RSYNC_PROGRESS="${RSYNC_PROGRESS:-1}"' in script
    assert "if rsync --help 2>/dev/null | grep -q -- '--info='" in script
    assert 'rsync_progress_flags+=(--info=progress2)' in script
    assert 'rsync_progress_flags+=(--progress)' in script
    assert 'log_stage "[wait ${wait_pct}%] attempt ${attempt}/${WAIT_ATTEMPTS}, elapsed ${elapsed}s:' in script
    assert 'SHOW_REMOTE_DIAGNOSTICS="${SHOW_REMOTE_DIAGNOSTICS:-1}"' in script
    assert "Collecting remote diagnostics..." in script
    assert "Smoke checks completed for" in script
    assert '-e "ssh ${PI_SSH_OPTIONS}"' in script
    assert 'read -r -a SSH_OPTION_ARGS <<< "${PI_SSH_OPTIONS}"' in script
    assert 'ssh "${SSH_OPTION_ARGS[@]}"' in script


def test_stream_chat_script_validates_sse_done_and_chunk_object():
    script = Path("tests/e2e/stream_chat_pi.sh").read_text(encoding="utf-8")

    assert "[DONE]" in script
    assert "chat.completion.chunk" in script
    assert "delta.role == \"assistant\"" in script
    assert "STREAM_PROMPT" in script
    assert 'if [ "$#" -gt 0 ]; then' in script
    assert "Throughput:" in script
    assert "timings.predicted_per_second" in script


def test_seed_mode_pi_script_validates_deterministic_seed_behavior():
    script = Path("tests/e2e/seed_mode_pi.sh").read_text(encoding="utf-8")

    assert "Seed deterministic check passed on" in script
    assert "/v1/chat/completions" in script
    assert "seed: ($seed | tonumber)" in script
    assert "Deterministic outputs diverged for seed" in script
    assert "random output:" in script
    assert "PI_HOST_MDNS" in script
    assert "potato.local" in script


def test_install_script_uses_reference_llama_bundle_sync():
    script = Path("bin/install_dev.sh").read_text(encoding="utf-8")

    assert "references/old_reference_design/llama_cpp_binary" in script
    assert "POTATO_LLAMA_BUNDLE_SRC" in script
    assert "POTATO_LLAMA_BUNDLE_SELECT" in script
    assert 'LLAMA_BUNDLE_SELECT="${POTATO_LLAMA_BUNDLE_SELECT:-}"' in script
    assert 'llama_server_bundle_*${LLAMA_BUNDLE_SELECT}*' in script
    assert "llama_server_bundle_" in script
    assert "TARGET_ROOT}/llama" in script
    assert "apt-get install -y \\" in script
    assert "nginx \\" in script
    assert "/etc/nginx/sites-available/potato" in script
    assert "systemctl enable avahi-daemon nginx" in script
    assert "usermod -a -G video" in script
    assert 'POTATO_HOSTNAME="${POTATO_HOSTNAME:-potato}"' in script
    assert 'POTATO_ENFORCE_HOSTNAME="${POTATO_ENFORCE_HOSTNAME:-1}"' in script
    assert "hostnamectl set-hostname" in script
    assert '"127.0.1.1 " hostname ".local " hostname' in script
    assert "avahi-daemon.conf" in script
    assert "host-name=${POTATO_HOSTNAME}" in script
    assert "potato-runtime-reset.service" in script
    assert "/etc/sudoers.d/potato-runtime-reset" in script
    assert "systemctl start --no-block potato-runtime-reset.service" in script
    assert "normalize_runtime_dir_permissions" in script
    assert 'if [ "${target_parent}" = "/opt" ]' in script
    assert "chmod 0755 /opt" in script


def test_nginx_config_allows_large_streaming_uploads():
    conf = Path("nginx/potato.conf").read_text(encoding="utf-8")

    assert "client_max_body_size 0;" in conf
    assert "client_body_timeout 3600;" in conf
    assert "proxy_request_buffering off;" in conf
    assert "proxy_buffering off;" in conf


def test_prepare_imager_bundle_script_wires_first_boot_installer():
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
    assert "generate_imager_manifest.py" in script
    assert ".rpi-imager-manifest" in script
    assert "Raspberry Pi 5" in script
    assert "python3" in script
    assert "--clean-artifacts <mode>" in script
    assert "--clean-artifacts-yes" in script
    assert "--clean-artifacts-no" in script
    assert "Previous artifacts found" in script
    assert "Remove them before build? [y/N]:" in script
    assert "CLEAN_ARTIFACTS_MODE" in script


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


def test_imager_manifest_generator_is_pi5_only():
    script = Path("bin/generate_imager_manifest.py").read_text(encoding="utf-8")

    assert "pi5-64bit" in script
    assert "Raspberry Pi 5" in script
    assert "rpi-imager-manifest" in script
    assert "extract_sha256" in script
    assert "image_download_sha256" in script


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
    assert "generate_imager_manifest.py" in common
    assert "potato-${variant}.rpi-imager-manifest" in common
    assert "Potato OS (${variant}, Raspberry Pi 5)" in common
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
    assert "${potato_root}/llama-bundles" in common
    assert "llama-bundles/${bundle_name}/" in common
    assert "find \"${bundle_root}\" -mindepth 1 -maxdepth 1 -type d -name 'llama_server_bundle_*' | sort" in common
    assert "docker context use default" in uv_script
    assert "colima start" in uv_script
    assert "except RuntimeError as exc" in uv_script
    assert "except subprocess.CalledProcessError as exc" in uv_script
    assert "--setup-docker" in uv_script
    assert 'run(["brew", "install", "docker", "colima"])' in uv_script


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


def test_gitignore_excludes_large_artifacts_and_model_downloads():
    ignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "models/" in ignore
    assert "output/" in ignore
    assert ".cache/potato-image-build/" in ignore
    assert ".cache/potato-image-cache/" in ignore
    assert ".cache/pi-gen-arm64/" in ignore


def test_chat_ui_streaming_parses_sse_and_ignores_done_marker():
    assert "function consumeSseDeltas" in CHAT_HTML
    assert 'dataPayload === "[DONE]"' in CHAT_HTML
    assert "event?.choices?.[0]?.delta?.content" in CHAT_HTML
    assert "updateMessage(assistantDiv, assistantText)" in CHAT_HTML
    assert 'renderMessage("assistant", output.trim())' not in CHAT_HTML


def test_chat_ui_supports_theme_system_prompt_setting_and_enter_to_send():
    assert 'class="theme-toggle"' in CHAT_HTML
    assert 'id="themeToggle"' in CHAT_HTML
    assert "theme-icon--moon" in CHAT_HTML
    assert "theme-icon--sun" in CHAT_HTML
    assert "Switch to light theme" in CHAT_HTML
    assert 'theme: "light"' in CHAT_HTML
    assert "function detectSystemTheme(" in CHAT_HTML
    assert 'window.matchMedia("(prefers-color-scheme: dark)")' in CHAT_HTML
    assert 'return "dark";' in CHAT_HTML
    assert 'return "light";' in CHAT_HTML
    assert "theme: detectSystemTheme()" in CHAT_HTML
    assert 'id="theme"' not in CHAT_HTML
    assert "applyTheme(" in CHAT_HTML
    assert 'id="systemPrompt"' in CHAT_HTML
    assert "System Prompt (optional)" in CHAT_HTML
    assert 'userPrompt.addEventListener("keydown"' in CHAT_HTML
    assert 'event.key === "Enter"' in CHAT_HTML
    assert "!event.shiftKey" in CHAT_HTML


def test_chat_ui_seed_mode_settings_contract():
    assert 'id="generationMode"' in CHAT_HTML
    assert '<option value="random">Random</option>' in CHAT_HTML
    assert '<option value="deterministic">Deterministic</option>' in CHAT_HTML
    assert 'id="seed"' in CHAT_HTML
    assert "generation_mode: \"random\"" in CHAT_HTML
    assert "seed: 42" in CHAT_HTML
    assert "function normalizeGenerationMode(" in CHAT_HTML
    assert "function normalizeSeedValue(" in CHAT_HTML
    assert "function updateSeedFieldState(" in CHAT_HTML
    assert "function resolveSeedForRequest(" in CHAT_HTML
    assert "seedField.disabled = generationMode !== \"deterministic\";" in CHAT_HTML
    assert "#seed:disabled" in CHAT_HTML
    assert "cursor: not-allowed;" in CHAT_HTML
    assert "reqBody.seed = resolvedSeed;" in CHAT_HTML


def test_chat_ui_keeps_theme_toggle_clear_of_status_badge():
    assert ".chat-header {" in CHAT_HTML
    assert "padding: 2px 6px;" in CHAT_HTML
    assert ".header-actions {" in CHAT_HTML
    assert ".theme-toggle {" in CHAT_HTML
    assert "position: static;" in CHAT_HTML


def test_chat_ui_copy_and_stats_footnote_contract():
    assert "Local-first chat frontend on your Pi." in CHAT_HTML
    assert "Local-first chat front end on your Pi." not in CHAT_HTML
    assert "Press Enter to send. Shift+Enter adds a new line." not in CHAT_HTML
    assert 'meta.className = "message-meta"' in CHAT_HTML
    assert "function formatStopReason(" in CHAT_HTML
    assert "function formatAssistantStats(" in CHAT_HTML
    assert "tok/sec" in CHAT_HTML
    assert "Stop reason:" in CHAT_HTML
    assert "EOS Token found" in CHAT_HTML


def test_chat_ui_runtime_details_hide_compact_and_apply_metric_threshold_classes():
    assert 'id="compatibilityWarnings"' in CHAT_HTML
    assert 'id="compatibilityWarningsText"' in CHAT_HTML
    assert 'id="compatibilityOverrideBtn"' in CHAT_HTML
    assert "function renderCompatibilityWarnings(" in CHAT_HTML
    assert "statusPayload?.compatibility?.warnings" in CHAT_HTML
    assert "statusPayload?.compatibility?.override_enabled" in CHAT_HTML
    assert 'id="runtimeCompact"' in CHAT_HTML
    assert "compact.hidden = runtimeDetailsExpanded;" in CHAT_HTML
    assert 'toggle.textContent = runtimeDetailsExpanded ? "Show compact" : "Show details";' in CHAT_HTML
    assert "function runtimeMetricSeverityClass(" in CHAT_HTML
    assert "runtime-metric-normal" in CHAT_HTML
    assert "runtime-metric-warn" in CHAT_HTML
    assert "runtime-metric-high" in CHAT_HTML
    assert "runtime-metric-critical" in CHAT_HTML
    assert "CPU_CLOCK_MAX_HZ_PI5" in CHAT_HTML
    assert "GPU_CLOCK_MAX_HZ_PI5" in CHAT_HTML
    assert "applyRuntimeMetricSeverity(memoryDetail, systemPayload?.memory_percent);" in CHAT_HTML
    assert "applyRuntimeMetricSeverity(swapDetail, systemPayload?.swap_percent);" in CHAT_HTML
    assert "applyRuntimeMetricSeverity(tempDetail, tempValue);" in CHAT_HTML
    assert 'case "tool_calls"' in CHAT_HTML
    assert "content_filter" not in CHAT_HTML
    assert "function_call" not in CHAT_HTML


def test_chat_ui_exposes_llama_runtime_bundle_switch_controls():
    assert "Llama Runtime Bundle" in CHAT_HTML
    assert 'id="llamaRuntimeBundleSelect"' in CHAT_HTML
    assert 'id="switchLlamaRuntimeBtn"' in CHAT_HTML
    assert 'id="llamaRuntimeCurrent"' in CHAT_HTML
    assert 'id="llamaRuntimeSwitchStatus"' in CHAT_HTML
    assert "function renderLlamaRuntimeStatus(" in CHAT_HTML
    assert "function switchLlamaRuntimeBundle(" in CHAT_HTML
    assert "/internal/llama-runtime/switch" in CHAT_HTML
    assert "statusPayload?.llama_runtime" in CHAT_HTML


def test_chat_ui_exposes_llama_memory_loading_controls():
    assert "Model Memory Loading" in CHAT_HTML
    assert 'id="llamaMemoryLoadingMode"' in CHAT_HTML
    assert 'id="applyLlamaMemoryLoadingBtn"' in CHAT_HTML
    assert 'id="llamaMemoryLoadingStatus"' in CHAT_HTML
    assert "function applyLlamaMemoryLoadingMode(" in CHAT_HTML


def test_chat_ui_exposes_large_model_compatibility_override_controls():
    assert "Allow unsupported large models (try anyway)" in CHAT_HTML
    assert 'id="largeModelOverrideEnabled"' in CHAT_HTML
    assert 'id="applyLargeModelOverrideBtn"' in CHAT_HTML
    assert 'id="largeModelOverrideStatus"' in CHAT_HTML
    assert "function applyLargeModelCompatibilityOverride(" in CHAT_HTML
    assert "/internal/compatibility/large-model-override" in CHAT_HTML
    assert "/internal/llama-runtime/memory-loading" in CHAT_HTML
    assert "memory_loading" in CHAT_HTML


def test_chat_ui_has_potato_chat_brand_and_thinking_toggle():
    assert "🥔 Potato Chat" in CHAT_HTML
    assert "Smart Search" not in CHAT_HTML
    assert 'id="thinkingToggleBtn"' in CHAT_HTML
    assert "Deep thinking" in CHAT_HTML
    assert "thinking_enabled: false" in CHAT_HTML
    assert "function normalizeThinkingEnabled(" in CHAT_HTML
    assert "function setThinkingToggleState(" in CHAT_HTML
    assert "function toggleThinkingMode(" in CHAT_HTML
    assert "chat_template_kwargs" in CHAT_HTML
    assert "enable_thinking" in CHAT_HTML


def test_chat_ui_supports_stop_generation_button_and_abort_controller():
    assert "let activeRequest = null;" in CHAT_HTML
    assert "function stopGeneration()" in CHAT_HTML
    assert "function requestLlamaCancelRecovery(" in CHAT_HTML
    assert "function requestLlamaRestart(" in CHAT_HTML
    assert "function scheduleImageCancelRestartFallback(" in CHAT_HTML
    assert "IMAGE_CANCEL_RESTART_DELAY_MS" in CHAT_HTML
    assert "function queueImageCancelRecovery(" in CHAT_HTML
    assert 'sendBtn.textContent = "Stop"' in CHAT_HTML
    assert 'sendBtn.classList.add("stop-mode")' in CHAT_HTML
    assert "controller: new AbortController()" in CHAT_HTML
    assert "signal: requestCtx.controller.signal" in CHAT_HTML
    assert 'if (requestInFlight) {' in CHAT_HTML
    assert "stopGeneration();" in CHAT_HTML
    assert "queueImageCancelRecovery(current);" in CHAT_HTML
    assert "if (!requestCtx?.hasImageRequest)" in CHAT_HTML
    assert "/internal/llama-healthz" in CHAT_HTML
    assert "/internal/cancel-llama" in CHAT_HTML
    assert "/internal/restart-llama" in CHAT_HTML
    assert 'case "cancelled"' in CHAT_HTML


def test_chat_ui_shows_llama_connection_indicator():
    assert 'id="llamaIndicator"' not in CHAT_HTML
    assert 'id="llamaIndicatorLabel"' not in CHAT_HTML
    assert 'id="statusDot"' in CHAT_HTML
    assert 'id="statusLabel"' in CHAT_HTML
    assert "indicator-dot" in CHAT_HTML
    assert "function updateLlamaIndicator(" in CHAT_HTML
    assert "statusPayload?.llama_server?.healthy" in CHAT_HTML
    assert "const modelSuffix = modelFilename ? `:${modelFilename}` : \"\";" in CHAT_HTML
    assert "label.textContent = `CONNECTED:llama.cpp${modelSuffix}`" in CHAT_HTML
    assert "label.textContent = `LOADING:llama.cpp${modelSuffix}`" in CHAT_HTML
    assert "label.textContent = `FAILED:llama.cpp${modelSuffix}`" in CHAT_HTML
    assert 'label.textContent = "DISCONNECTED:llama.cpp"' in CHAT_HTML
    assert 'label.textContent = "CONNECTED:Fake Backend"' in CHAT_HTML
    assert 'dot.classList.add("online")' in CHAT_HTML
    assert 'dot.classList.add("loading")' in CHAT_HTML
    assert 'dot.classList.add("failed")' in CHAT_HTML
    assert 'dot.classList.add("offline")' in CHAT_HTML
    assert '.indicator-dot.loading {' in CHAT_HTML
    assert '.indicator-dot.failed {' in CHAT_HTML
    assert '.badge.loading {' in CHAT_HTML
    assert '.badge.failed {' in CHAT_HTML
    assert "statusPayload?.backend?.active" in CHAT_HTML
    assert "backendMode === \"fake\"" in CHAT_HTML
    assert "Llama server: connected" not in CHAT_HTML


def test_chat_ui_mobile_layout_prioritizes_chat_area_before_sidebar():
    assert "@media (max-width: 900px)" in CHAT_HTML
    assert ".app-shell {" in CHAT_HTML
    assert 'id="sidebarPanel"' in CHAT_HTML
    assert 'id="sidebarToggle"' in CHAT_HTML
    assert 'id="sidebarCloseBtn"' in CHAT_HTML
    assert 'id="sidebarBackdrop"' in CHAT_HTML
    assert ".sidebar-backdrop {" in CHAT_HTML
    assert "body.sidebar-open .sidebar {" in CHAT_HTML
    assert "transform: translateX(-100%);" in CHAT_HTML
    assert "body.sidebar-open {" in CHAT_HTML
    assert "overflow: hidden;" in CHAT_HTML
    assert "function setSidebarOpen(" in CHAT_HTML
    assert "function bindMobileSidebar(" in CHAT_HTML
    assert 'document.getElementById("sidebarToggle").addEventListener("click"' in CHAT_HTML
    assert 'document.getElementById("sidebarCloseBtn").addEventListener("click"' in CHAT_HTML
    assert 'document.getElementById("sidebarBackdrop").addEventListener("click"' in CHAT_HTML
    assert '<details class="settings" open>' not in CHAT_HTML
    assert '<details class="settings">' in CHAT_HTML


def test_chat_ui_mobile_composer_keeps_actions_together():
    assert ".composer-bottom {" in CHAT_HTML
    assert "display: grid;" in CHAT_HTML
    assert "grid-template-columns: 1fr auto;" in CHAT_HTML
    assert ".composer-right {" in CHAT_HTML
    assert "justify-content: flex-end;" in CHAT_HTML
    assert ".composer-left {" in CHAT_HTML
    assert "@media (max-width: 900px)" in CHAT_HTML
    assert ".composer-bottom { grid-template-columns: 1fr; }" in CHAT_HTML
    assert ".composer-right {" in CHAT_HTML
    assert "width: 100%;" in CHAT_HTML
    assert "justify-content: flex-end;" in CHAT_HTML


def test_chat_ui_uses_continuous_chat_history_in_openai_messages_format():
    assert "const chatHistory = [];" in CHAT_HTML
    assert "reqBody.messages = reqBody.messages.concat(chatHistory);" in CHAT_HTML
    assert "const userMessage = { role: \"user\", content: buildUserMessageContent(content) };" in CHAT_HTML
    assert "chatHistory.push(userMessage);" in CHAT_HTML
    assert CHAT_HTML.index("reqBody.messages.push(userMessage);") < CHAT_HTML.index("chatHistory.push(userMessage);")
    assert "chatHistory.push({ role: \"assistant\", content: assistantText.trim() || \"(empty response)\" });" in CHAT_HTML
    assert "chatHistory.push({ role: \"assistant\", content: msg });" in CHAT_HTML


def test_chat_ui_formats_download_sizes_and_shows_model_filename_in_settings():
    assert 'id="modelName"' in CHAT_HTML
    assert "Loaded Model" in CHAT_HTML
    assert "readonly" in CHAT_HTML
    assert "function formatBytes(" in CHAT_HTML
    assert "units = [\"B\", \"KB\", \"MB\", \"GB\", \"TB\"]" in CHAT_HTML
    assert "formatBytes(statusPayload.download.bytes_downloaded)" in CHAT_HTML
    assert "formatBytes(statusPayload.download.bytes_total)" in CHAT_HTML
    assert "statusPayload?.model?.filename" in CHAT_HTML
    assert "modelNameField.value" in CHAT_HTML


def test_chat_ui_supports_manual_or_idle_model_download_prompt():
    assert 'id="downloadPrompt"' in CHAT_HTML
    assert 'id="startDownloadBtn"' in CHAT_HTML
    assert 'id="downloadPromptHint"' in CHAT_HTML
    assert "function startModelDownload(" in CHAT_HTML
    assert "function renderDownloadPrompt(" in CHAT_HTML
    assert 'fetch("/internal/start-model-download"' in CHAT_HTML
    assert "Auto-download starts in" in CHAT_HTML
    assert "statusPayload.download.auto_start_remaining_seconds" in CHAT_HTML
    assert "Not enough free storage for this model." in CHAT_HTML
    assert "Model likely too large for free storage. Delete files and retry." in CHAT_HTML
    assert "freeBytes < 512 * 1024 * 1024" in CHAT_HTML


def test_chat_ui_supports_heavy_runtime_reset_action_with_confirmation():
    assert 'id="resetRuntimeBtn"' in CHAT_HTML
    assert "Unload model + clean memory + restart" in CHAT_HTML
    assert "function resetRuntimeHeavy(" in CHAT_HTML
    assert "window.confirm(" in CHAT_HTML
    assert 'fetch("/internal/reset-runtime"' in CHAT_HTML
    assert 'document.getElementById("resetRuntimeBtn").addEventListener("click", resetRuntimeHeavy);' in CHAT_HTML


def test_chat_ui_runtime_reset_has_active_reconnect_polling():
    assert "function startRuntimeReconnectWatch(" in CHAT_HTML
    assert "function stopRuntimeReconnectWatch(" in CHAT_HTML
    assert "RUNTIME_RECONNECT_MAX_ATTEMPTS" in CHAT_HTML
    assert "Runtime reset in progress. Reconnecting..." in CHAT_HTML
    assert "Runtime reconnected." in CHAT_HTML
    assert "Model files on disk are unchanged." in CHAT_HTML
    assert "const controller = new AbortController();" in CHAT_HTML
    assert "cache: \"no-store\"" in CHAT_HTML
    assert "controller.abort();" in CHAT_HTML


def test_chat_ui_shows_pi_runtime_compact_with_details_toggle_above_settings():
    assert 'id="systemRuntimeCard"' in CHAT_HTML
    assert 'id="runtimeCompact"' in CHAT_HTML
    assert 'id="runtimeDetails"' in CHAT_HTML
    assert 'id="runtimeViewToggle"' in CHAT_HTML
    assert 'id="runtimeDetailCpuClock"' in CHAT_HTML
    assert "Show details" in CHAT_HTML
    assert "function setRuntimeDetailsExpanded(" in CHAT_HTML
    assert "function renderSystemRuntime(" in CHAT_HTML
    assert "CPU clock:" in CHAT_HTML
    assert "Storage free:" in CHAT_HTML
    assert 'id="runtimeDetailStorage"' in CHAT_HTML
    assert "renderSystemRuntime(statusPayload?.system)" in CHAT_HTML
    assert CHAT_HTML.index('id="systemRuntimeCard"') < CHAT_HTML.index('<details class="settings"')


def test_chat_ui_supports_image_upload_for_vision_messages():
    assert 'id="imageInput"' in CHAT_HTML
    assert 'accept="image/*"' in CHAT_HTML
    assert 'id="attachImageBtn"' in CHAT_HTML
    assert 'for="imageInput"' not in CHAT_HTML
    assert 'id="attachImageBtn" class="attach-btn" type="button">Attach image</button>' in CHAT_HTML
    assert "function handleImageSelected(" in CHAT_HTML
    assert "FileReader()" in CHAT_HTML
    assert "reader.readAsDataURL(file);" in CHAT_HTML
    assert "pendingImage = {" in CHAT_HTML
    assert "type: \"image_url\"" in CHAT_HTML
    assert "image_url: { url: pendingImage.dataUrl }" in CHAT_HTML
    assert "function openImagePicker(" in CHAT_HTML
    assert "input.showPicker()" not in CHAT_HTML
    assert "input.click();" in CHAT_HTML
    assert 'document.getElementById("attachImageBtn").addEventListener("click", openImagePicker);' in CHAT_HTML
    assert 'document.getElementById("attachImageBtn").addEventListener("click", (event) => {' not in CHAT_HTML
    assert 'document.getElementById("attachImageBtn").addEventListener("keydown"' not in CHAT_HTML


def test_chat_ui_renders_image_thumbnail_in_user_bubble():
    assert "function buildUserBubblePayload(" in CHAT_HTML
    assert "imageDataUrl: pendingImage.dataUrl" in CHAT_HTML
    assert 'thumbnail.className = "message-image-thumb"' in CHAT_HTML
    assert 'thumbnail.src = imageDataUrl;' in CHAT_HTML
    assert "bubble.replaceChildren();" in CHAT_HTML
    assert 'caption.className = "message-text"' in CHAT_HTML


def test_chat_ui_compresses_large_images_before_send():
    assert "const IMAGE_SAFE_MAX_BYTES = 140 * 1024;" in CHAT_HTML
    assert "const IMAGE_MAX_DIMENSION = 896;" in CHAT_HTML
    assert "const IMAGE_MAX_PIXEL_COUNT = IMAGE_MAX_DIMENSION * IMAGE_MAX_DIMENSION;" in CHAT_HTML
    assert "function estimateDataUrlBytes(" in CHAT_HTML
    assert "function inspectImageDataUrl(" in CHAT_HTML
    assert "function compressImageDataUrl(" in CHAT_HTML
    assert "function maybeCompressImage(" in CHAT_HTML
    assert "const needsResize =" in CHAT_HTML
    assert "metadata.maxDim > IMAGE_MAX_DIMENSION" in CHAT_HTML
    assert "metadata.pixelCount > IMAGE_MAX_PIXEL_COUNT" in CHAT_HTML
    assert "setComposerActivity(\"Optimizing image...\")" in CHAT_HTML
    assert "await maybeCompressImage(result, file);" in CHAT_HTML
    assert "optimized from" in CHAT_HTML


def test_chat_ui_model_manager_supports_model_delete_action():
    assert "async function deleteSelectedModel(" in CHAT_HTML
    assert "async function cancelActiveModelDownload(modelId = null)" in CHAT_HTML
    assert "/internal/models/delete" in CHAT_HTML
    assert 'deleteBtn.dataset.action = "delete"' in CHAT_HTML
    assert "Delete model" in CHAT_HTML
    assert "Cancel + delete" in CHAT_HTML
    assert "Stop download" in CHAT_HTML
    assert "formatModelStatusLabel" in CHAT_HTML
    assert "function startModelDownloadForModel(" in CHAT_HTML
    assert "/internal/models/download" in CHAT_HTML
    assert "insufficient_storage" in CHAT_HTML
    assert 'id="purgeModelsBtn"' in CHAT_HTML
    assert "async function purgeAllModels(" in CHAT_HTML
    assert "/internal/models/purge" in CHAT_HTML
    assert "reset_bootstrap_flag: false" in CHAT_HTML


def test_chat_ui_shows_processing_indicator_while_generating():
    assert 'id="composerActivity"' in CHAT_HTML
    assert 'id="composerStatusChip"' in CHAT_HTML
    assert 'id="composerStatusText"' in CHAT_HTML
    assert 'class="composer-status-chip"' in CHAT_HTML
    assert 'id="cancelBtn"' in CHAT_HTML
    assert "function setComposerActivity(" in CHAT_HTML
    assert "function setComposerStatusChip(" in CHAT_HTML
    assert "function hideComposerStatusChip(" in CHAT_HTML
    assert "function setCancelEnabled(" in CHAT_HTML
    assert "function cancelCurrentWork(" in CHAT_HTML
    assert "const PREFILL_PROGRESS_CAP = 95;" in CHAT_HTML
    assert "function estimatePrefillEtaMs(" in CHAT_HTML
    assert "function beginPrefillProgress(" in CHAT_HTML
    assert "function markPrefillGenerationStarted(" in CHAT_HTML
    assert "function stopPrefillProgress(" in CHAT_HTML
    assert "potato_prefill_metrics_v1" in CHAT_HTML
    assert "Preparing prompt..." in CHAT_HTML
    assert "Preparing prompt • " in CHAT_HTML
    assert "Preparing prompt: " not in CHAT_HTML
    assert "Math.min(PREFILL_PROGRESS_CAP" in CHAT_HTML
    assert 'setComposerStatusChip("Generating..."' in CHAT_HTML
    assert 'setComposerActivity("Reading image...")' in CHAT_HTML
    assert "reader.onprogress" in CHAT_HTML
    assert "pendingImageReader.abort();" in CHAT_HTML
    assert 'document.getElementById("cancelBtn").addEventListener("click", cancelCurrentWork);' in CHAT_HTML
    assert "setComposerActivity(\"\")" in CHAT_HTML
