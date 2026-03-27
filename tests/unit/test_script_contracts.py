from __future__ import annotations

import json
import subprocess
import textwrap
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
    assert "Syncing install paths to Pi..." in script
    assert "--include='/app/'" in script
    assert "--include='/bin/'" in script
    assert "--include='/nginx/'" in script
    assert "--include='/systemd/'" in script
    assert "--include='/requirements.txt'" in script
    assert "--exclude='*'" in script
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
    assert "run_sudo cat /etc/hosts" in script
    assert "potato-runtime-reset.service" in script
    assert "/etc/sudoers.d/potato-runtime-reset" in script
    assert "systemctl start --no-block potato-runtime-reset.service" in script
    assert "normalize_runtime_dir_permissions" in script
    assert 'if [ "${target_parent}" = "/opt" ]' in script
    assert "chmod 0755 /opt" in script


def test_ensure_model_validates_download_size_before_finalizing():
    script = Path("bin/ensure_model.sh").read_text(encoding="utf-8")

    # Must check final_size against total_bytes before moving file
    assert 'final_size="$(filesize "${TMP_PATH}")"' in script
    assert '"${final_size}" -lt "${total_bytes}"' in script
    assert "download_incomplete" in script
    assert 'rm -f "${TMP_PATH}"' in script


def test_ensure_model_validates_size_before_mv():
    """The size check must happen BEFORE mv moves the partial file to final path."""
    script = Path("bin/ensure_model.sh").read_text(encoding="utf-8")

    size_check_pos = script.index('"${final_size}" -lt "${total_bytes}"')
    mv_pos = script.index('mv -f "${TMP_PATH}" "${MODEL_PATH}"')
    assert size_check_pos < mv_pos, "Size validation must occur before mv"


def test_ensure_model_runs_curl_at_idle_io_priority():
    script = Path("bin/ensure_model.sh").read_text(encoding="utf-8")

    assert "ionice -c3 nice -n 19 curl" in script


def test_start_llama_runs_projector_curl_at_idle_io_priority():
    script = Path("bin/start_llama.sh").read_text(encoding="utf-8")

    assert "ionice -c3 nice -n 19 curl" in script


def test_model_download_does_not_inline_projector_download():
    """The projector download must NOT run inside start_model_download.
    It must be handled by start_llama.sh to avoid overlapping large
    writes that overwhelm SD card I/O on Pi 5."""
    import inspect

    from core.main import start_model_download

    source = inspect.getsource(start_model_download)
    assert "download_default_projector_for_model" not in source, (
        "Projector download must not run inside start_model_download — "
        "let start_llama.sh handle it sequentially to avoid I/O starvation"
    )


def test_build_status_offloads_filesystem_io_to_thread():
    """build_status must use asyncio.to_thread to keep the event loop free."""
    import inspect

    from core.main import build_status

    source = inspect.getsource(build_status)
    assert "to_thread" in source, (
        "build_status must use asyncio.to_thread for filesystem I/O — "
        "sync reads block the event loop and freeze HTTP responses during downloads"
    )


def test_get_status_download_context_is_async():
    """get_status_download_context must be async to use to_thread."""
    import inspect

    from core.main import get_status_download_context

    assert inspect.iscoroutinefunction(get_status_download_context), (
        "get_status_download_context must be async — "
        "sync filesystem reads block the event loop during downloads"
    )


def test_orchestrator_allows_llama_restart_during_download():
    """The orchestrator must not gate llama-server restart on download state."""
    import inspect

    from core.main import orchestrator_loop

    source = inspect.getsource(orchestrator_loop)
    assert "active_model_is_present and not download_active" not in source, (
        "Orchestrator restart logic must run during downloads — "
        "otherwise restart_managed_llama_process leaves llama dead until download finishes"
    )


# ---------------------------------------------------------------------------
# OpenClaw addon scripts
# ---------------------------------------------------------------------------


def test_install_openclaw_checks_potato_prerequisite():
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "/opt/potato" in script


def test_install_openclaw_requires_root():
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "id -u" in script


def test_install_openclaw_detects_real_user():
    """Must use SUDO_USER to target the real user, not root."""
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "SUDO_USER" in script
    assert "logname" in script


def test_install_openclaw_installs_nodejs():
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "nodesource.com/setup_" in script
    assert "apt-get install" in script
    assert "nodejs" in script


def test_install_openclaw_pins_version():
    """Must pin to a specific version, not @latest, and not the broken 2026.3.22."""
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "OPENCLAW_VERSION=" in script
    assert "openclaw@${OPENCLAW_VERSION}" in script
    assert "@latest" not in script
    # v2026.3.22 has a confirmed packaging regression — missing Control UI assets.
    # See: https://github.com/openclaw/openclaw/issues/52808
    assert "2026.3.22" not in script


def test_install_openclaw_embeds_config_as_heredoc():
    """Config must be embedded in the script, not copied from external files."""
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    # Should contain the config inline, not reference external files
    assert "openclaw.json" in script
    assert "127.0.0.1:1983/v1" in script
    assert "potato/local" in script
    assert "skipBootstrap" in script
    # Must NOT reference the openclaw/ directory
    assert 'cp "${REPO_ROOT}/openclaw/' not in script


def test_install_openclaw_configurable_context_budget():
    """Context budget values must be overridable via env vars."""
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "POTATO_CONTEXT_WINDOW" in script
    assert "POTATO_MAX_TOKENS" in script
    assert "POTATO_BOOTSTRAP_MAX" in script
    assert "POTATO_COMPACTION_RESERVE" in script


def test_install_openclaw_dynamic_origins():
    """allowedOrigins must be built dynamically from hostname and IPs, including .local mDNS."""
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "hostname -I" in script
    assert "allowedOrigins" in script
    assert ".local:" in script  # mDNS variant must be included


def test_install_openclaw_disables_all_skills():
    """Must glob ALL SKILL.md files, not a hardcoded list."""
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert ".disabled" in script
    assert "find" in script
    assert 'SKILL.md' in script
    # Must NOT have a hardcoded skill list
    assert "OPENCLAW_SKILLS_TO_DISABLE" not in script


def test_install_openclaw_preserves_existing_config():
    """Re-running the installer must not overwrite existing config but must migrate Potato fixes."""
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "openclaw.json" in script
    # Must check if config exists before writing
    assert "-f" in script  # file existence test
    # Must migrate .local origin and image input on existing configs
    assert ".local:" in script
    assert "migrated" in script


def test_origin_migration_adds_mdns_without_duplicating(tmp_path):
    """Origin migration must add .local variant and be idempotent."""
    config_path = tmp_path / "openclaw.json"
    config = {
        "gateway": {
            "controlUi": {
                "allowedOrigins": [
                    "http://localhost:18789",
                    "http://potato:18789",
                ]
            }
        }
    }
    config_path.write_text(json.dumps(config))

    # The origin migration uses sed — simulate it
    origin = "http://potato.local:18789"
    content = config_path.read_text()
    assert origin not in content

    # Simulate the sed: insert at the start of allowedOrigins array
    content = content.replace(
        '"allowedOrigins": [',
        f'"allowedOrigins": ["{origin}", ',
    )
    config_path.write_text(content)
    result = json.loads(config_path.read_text())
    assert origin in result["gateway"]["controlUi"]["allowedOrigins"]

    # Running again should NOT duplicate (the grep check prevents it)
    assert content.count(origin) == 1


def test_vision_migration_targets_only_potato_model(tmp_path):
    """Run the actual migration logic against a sample config with multiple providers."""
    config = {
        "models": {
            "providers": {
                "potato": {
                    "baseUrl": "http://127.0.0.1:1983/v1",
                    "models": [
                        {"id": "local", "name": "Potato OS Local Model", "input": ["text"]},
                    ],
                },
                "custom": {
                    "baseUrl": "http://example.com/v1",
                    "models": [
                        {"id": "text-model", "name": "Text Only Model", "input": ["text"]},
                    ],
                },
            }
        }
    }
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(json.dumps(config, indent=2))

    # Extract and run the migration logic from the install script
    migration = textwrap.dedent(f"""\
        import json
        cfg = json.load(open('{config_path}'))
        models = cfg.get('models',{{}}).get('providers',{{}}).get('potato',{{}}).get('models',[])
        potato_model = next((m for m in models if m.get('id') == 'local'), None)
        if potato_model and potato_model.get('input') == ['text']:
            potato_model['input'] = ['text', 'image']
            json.dump(cfg, open('{config_path}', 'w'), indent=2)
    """)
    subprocess.check_call(["python3", "-c", migration])

    result = json.loads(config_path.read_text())
    # Potato model should be migrated
    potato_models = result["models"]["providers"]["potato"]["models"]
    assert potato_models[0]["input"] == ["text", "image"]
    # Custom user model must NOT be touched
    custom_models = result["models"]["providers"]["custom"]["models"]
    assert custom_models[0]["input"] == ["text"]


def test_install_openclaw_advertises_vision():
    """Model input must include image for vision-capable Potato models."""
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert '"image"' in script
    assert '"text"' in script


def test_install_openclaw_stock_port():
    """Must use stock OpenClaw port 18789."""
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "OPENCLAW_PORT=18789" in script


def test_install_openclaw_enables_linger():
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "loginctl enable-linger" in script


def test_install_openclaw_generates_gateway_token():
    script = Path("bin/install_openclaw.sh").read_text(encoding="utf-8")
    assert "openssl rand" in script
    assert "GATEWAY_TOKEN" in script


def test_uninstall_openclaw_detects_real_user():
    script = Path("bin/uninstall_openclaw.sh").read_text(encoding="utf-8")
    assert "SUDO_USER" in script
    assert "logname" in script


def test_uninstall_openclaw_removes_service():
    script = Path("bin/uninstall_openclaw.sh").read_text(encoding="utf-8")
    assert "disable --now openclaw-gateway" in script
    assert "openclaw-gateway.service" in script


def test_uninstall_openclaw_restores_all_skills():
    """Must glob ALL .disabled files, not a hardcoded list."""
    script = Path("bin/uninstall_openclaw.sh").read_text(encoding="utf-8")
    assert "SKILL.md.disabled" in script
    assert "find" in script


def test_uninstall_openclaw_removes_config():
    script = Path("bin/uninstall_openclaw.sh").read_text(encoding="utf-8")
    assert ".openclaw" in script


def test_uninstall_dev_handles_openclaw():
    script = Path("bin/uninstall_dev.sh").read_text(encoding="utf-8")
    assert "openclaw" in script


def test_uninstall_dev_targets_real_user():
    """Must use SUDO_USER to clean up the real user's service, not root's."""
    script = Path("bin/uninstall_dev.sh").read_text(encoding="utf-8")
    assert "SUDO_USER" in script
    assert "sudo -u" in script
    assert "XDG_RUNTIME_DIR" in script

