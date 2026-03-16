from __future__ import annotations

from pathlib import Path


def test_start_llama_contains_required_flags():
    script = Path("bin/start_llama.sh").read_text(encoding="utf-8")

    assert "--ctx-size" in script
    assert 'CTX_SIZE_DEFAULT="16384"' in script
    assert 'CTX_SIZE="${POTATO_CTX_SIZE:-${CTX_SIZE_DEFAULT}}"' in script
    assert 'CACHE_RAM_MIB="${POTATO_LLAMA_CACHE_RAM_MIB:-1024}"' in script
    assert "--cache-ram" in script
    assert "--jinja" in script
    assert "--no-warmup" in script
    assert 'DISABLE_WARMUP="${POTATO_LLAMA_NO_WARMUP:-1}"' in script
    assert "--slot-save-path" in script


def test_start_llama_uses_q8_kv_cache_by_default():
    script = Path("bin/start_llama.sh").read_text(encoding="utf-8")

    assert 'CACHE_TYPE_K="${POTATO_CACHE_TYPE_K:-q8_0}"' in script
    assert 'CACHE_TYPE_V="${POTATO_CACHE_TYPE_V:-q8_0}"' in script


def test_start_llama_supports_q4_v_cache_override():
    script = Path("bin/start_llama.sh").read_text(encoding="utf-8")

    assert 'CACHE_TYPE_V="${POTATO_CACHE_TYPE_V:-q8_0}"' in script
    assert "--cache-type-v" in script


def test_start_llama_does_not_override_ctx_size_for_a3b():
    script = Path("bin/start_llama.sh").read_text(encoding="utf-8")

    assert 'CTX_SIZE="4096"' not in script


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
    assert "POTATO_LLAMA_RUNTIME_FAMILY" in script
    assert "runtimes/${LLAMA_RUNTIME_FAMILY}" in script
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



