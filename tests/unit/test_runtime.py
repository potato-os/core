from __future__ import annotations

import json
import httpx
import pytest
import app.runtime_state as runtime_state

from app.main import compute_auto_download_remaining_seconds, should_auto_start_download
from app.runtime_state import (
    LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT,
    RuntimeConfig,
    build_power_estimate_status,
    build_large_model_compatibility,
    check_llama_health,
    check_runtime_device_compatibility,
    collect_system_metrics_snapshot,
    compute_required_download_bytes,
    classify_runtime_device,
    decode_throttled_bits,
    fetch_remote_content_length_bytes,
    get_large_model_warn_threshold_bytes,
    is_likely_too_large_for_storage,
    normalize_power_calibration_settings,
    _apply_power_calibration,
    _fit_linear_power_calibration,
    _estimate_power_from_cpu_load,
    ensure_compatible_runtime,
    _parse_vcgencmd_bootloader_version,
    _parse_vcgencmd_firmware_version,
    _parse_vcgencmd_pmic_read_adc,
    _read_swap_label,
    probe_llama_inference_slot,
    read_download_progress,
    request_llama_slot_cancel,
)


def test_read_download_progress_defaults(runtime):
    progress = read_download_progress(runtime)

    assert progress["bytes_total"] == 0
    assert progress["bytes_downloaded"] == 0
    assert progress["percent"] == 0
    assert progress["error"] is None


def test_read_download_progress_handles_invalid_json(runtime):
    runtime.download_state_path.write_text("not-json", encoding="utf-8")

    progress = read_download_progress(runtime)

    assert progress["bytes_total"] == 0
    assert progress["percent"] == 0


def test_read_download_progress_calculates_percent(runtime):
    runtime.download_state_path.write_text(
        json.dumps(
            {
                "bytes_total": 200,
                "bytes_downloaded": 100,
                "percent": 0,
                "speed_bps": 50,
                "eta_seconds": 2,
            }
        ),
        encoding="utf-8",
    )

    progress = read_download_progress(runtime)

    assert progress["percent"] == 50
    assert progress["speed_bps"] == 50


def test_read_download_progress_preserves_specific_error(runtime):
    runtime.download_state_path.write_text(
        json.dumps(
            {
                "bytes_total": 1000,
                "bytes_downloaded": 700,
                "percent": 70,
                "speed_bps": 0,
                "eta_seconds": 0,
                "error": "insufficient_storage",
            }
        ),
        encoding="utf-8",
    )

    progress = read_download_progress(runtime)

    assert progress["error"] == "insufficient_storage"
    assert progress["percent"] == 70


@pytest.mark.anyio
async def test_check_llama_health_treats_read_timeout_as_busy(runtime, monkeypatch):
    class _BusyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, _url):
            raise httpx.ReadTimeout("busy")

    monkeypatch.setattr("app.runtime_state.httpx.AsyncClient", lambda timeout: _BusyClient())

    healthy = await check_llama_health(runtime)

    assert healthy is True


@pytest.mark.anyio
async def test_check_llama_health_strict_mode_treats_read_timeout_as_unhealthy(runtime, monkeypatch):
    class _BusyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, _url):
            raise httpx.ReadTimeout("busy")

    monkeypatch.setattr("app.runtime_state.httpx.AsyncClient", lambda timeout: _BusyClient())

    healthy = await check_llama_health(runtime, busy_is_healthy=False)

    assert healthy is False


@pytest.mark.anyio
async def test_check_llama_health_returns_false_on_connect_error(runtime, monkeypatch):
    class _DownClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, _url):
            raise httpx.ConnectError("down")

    monkeypatch.setattr("app.runtime_state.httpx.AsyncClient", lambda timeout: _DownClient())

    healthy = await check_llama_health(runtime)

    assert healthy is False


@pytest.mark.anyio
async def test_probe_llama_inference_slot_returns_false_on_timeout(runtime, monkeypatch):
    class _StuckClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, _url, json):
            assert json["max_tokens"] == 1
            raise httpx.ReadTimeout("stuck")

    monkeypatch.setattr("app.runtime_state.httpx.AsyncClient", lambda timeout: _StuckClient())

    healthy = await probe_llama_inference_slot(runtime)

    assert healthy is False


@pytest.mark.anyio
async def test_request_llama_slot_cancel_returns_action_on_success(runtime, monkeypatch):
    class _Response:
        status_code = 200

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url):
            assert "/slots/0?action=erase" in url
            return _Response()

    monkeypatch.setattr("app.runtime_state.httpx.AsyncClient", lambda timeout: _Client())

    cancelled, action = await request_llama_slot_cancel(runtime)

    assert cancelled is True
    assert action == "erase"


@pytest.mark.anyio
async def test_request_llama_slot_cancel_returns_false_when_all_actions_fail(runtime, monkeypatch):
    class _Response:
        status_code = 501

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url):
            assert "/slots/0?action=" in url
            return _Response()

    monkeypatch.setattr("app.runtime_state.httpx.AsyncClient", lambda timeout: _Client())

    cancelled, action = await request_llama_slot_cancel(runtime)

    assert cancelled is False
    assert action == "none"


@pytest.mark.anyio
async def test_fetch_remote_content_length_uses_streaming_range_fallback_without_body_download(monkeypatch):
    class _HeadResponse:
        headers: dict[str, str] = {}

    class _RangeResponse:
        status_code = 200
        headers = {"content-length": "1234567890"}

    class _StreamCtx:
        def __init__(self, response):
            self._response = response

        async def __aenter__(self):
            return self._response

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def head(self, _url):
            return _HeadResponse()

        def get(self, *_args, **_kwargs):  # pragma: no cover - test should fail before this
            raise AssertionError("fallback must not use body-reading client.get")

        def stream(self, method, url, headers=None):
            assert method == "GET"
            assert url == "https://example.com/model.gguf"
            assert headers == {"range": "bytes=0-0"}
            return _StreamCtx(_RangeResponse())

    monkeypatch.setattr("app.runtime_state.httpx.AsyncClient", lambda timeout, follow_redirects: _Client())

    size_bytes = await fetch_remote_content_length_bytes("https://example.com/model.gguf")

    assert size_bytes == 1234567890


def test_runtime_from_env_defaults_to_llama_and_disables_fake_fallback(monkeypatch):
    monkeypatch.delenv("POTATO_CHAT_BACKEND", raising=False)
    monkeypatch.delenv("POTATO_ALLOW_FAKE_FALLBACK", raising=False)
    monkeypatch.setenv("POTATO_BASE_DIR", "/tmp/potato-test")

    runtime = RuntimeConfig.from_env()

    assert runtime.chat_backend_mode == "llama"
    assert runtime.allow_fake_fallback is False


def test_compute_auto_download_remaining_seconds_counts_down(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    runtime.auto_download_idle_seconds = 300
    monkeypatch.setattr("app.main.AUTO_DOWNLOAD_BOOTSTRAP_ENABLED", True)

    remaining = compute_auto_download_remaining_seconds(
        runtime,
        model_present=False,
        download_active=False,
        startup_monotonic=100.0,
        now_monotonic=222.4,
    )

    assert remaining == 178


def test_compute_auto_download_remaining_seconds_zero_when_not_applicable(runtime):
    runtime.enable_orchestrator = False
    runtime.auto_download_idle_seconds = 300

    remaining = compute_auto_download_remaining_seconds(
        runtime,
        model_present=False,
        download_active=False,
        startup_monotonic=0.0,
        now_monotonic=999.0,
    )

    assert remaining == 0


def test_compute_auto_download_remaining_seconds_zero_after_first_default_download(runtime):
    runtime.enable_orchestrator = True
    runtime.auto_download_idle_seconds = 300

    remaining = compute_auto_download_remaining_seconds(
        runtime,
        model_present=False,
        download_active=False,
        startup_monotonic=0.0,
        now_monotonic=10.0,
        default_model_downloaded_once=True,
    )

    assert remaining == 0


def test_should_auto_start_download_only_after_timeout(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    runtime.auto_download_idle_seconds = 300
    monkeypatch.setattr("app.main.AUTO_DOWNLOAD_BOOTSTRAP_ENABLED", True)

    before_timeout = should_auto_start_download(
        runtime,
        model_present=False,
        download_active=False,
        startup_monotonic=50.0,
        now_monotonic=349.9,
    )
    after_timeout = should_auto_start_download(
        runtime,
        model_present=False,
        download_active=False,
        startup_monotonic=50.0,
        now_monotonic=350.0,
    )

    assert before_timeout is False
    assert after_timeout is True


def test_should_auto_start_download_stays_false_after_first_default_download(runtime):
    runtime.enable_orchestrator = True
    runtime.auto_download_idle_seconds = 300

    should_start = should_auto_start_download(
        runtime,
        model_present=False,
        download_active=False,
        startup_monotonic=0.0,
        now_monotonic=999.0,
        default_model_downloaded_once=True,
    )

    assert should_start is False


def test_should_auto_start_download_false_when_countdown_disabled(runtime):
    runtime.enable_orchestrator = True
    runtime.auto_download_idle_seconds = 300

    should_start = should_auto_start_download(
        runtime,
        model_present=False,
        download_active=False,
        startup_monotonic=0.0,
        now_monotonic=999.0,
        countdown_enabled=False,
    )

    assert should_start is False


def test_compute_required_download_bytes_accounts_for_partial_file():
    assert compute_required_download_bytes(1_000, 200) == 800
    assert compute_required_download_bytes(1_000, 1_200) == 0


def test_is_likely_too_large_for_storage_uses_required_bytes():
    assert is_likely_too_large_for_storage(total_bytes=1_000, free_bytes=700, partial_bytes=200) is True
    assert is_likely_too_large_for_storage(total_bytes=1_000, free_bytes=800, partial_bytes=200) is False


def test_decode_throttled_bits_reports_current_and_history_flags():
    decoded = decode_throttled_bits(0x5)

    assert decoded["raw"] == "0x5"
    assert decoded["any_current"] is True
    assert "Undervoltage" in decoded["current_flags"]
    assert "Throttled" in decoded["current_flags"]
    assert decoded["any_history"] is False


def test_decode_throttled_bits_reports_soft_temp_history():
    decoded = decode_throttled_bits(0x80000)

    assert decoded["raw"] == "0x80000"
    assert decoded["any_current"] is False
    assert "Soft temp limit occurred" in decoded["history_flags"]


def test_parse_vcgencmd_bootloader_version_parses_structured_fields():
    raw = "\n".join(
        [
            "2024/02/16 15:33:41",
            "version 1234567890abcdef1234567890abcdef12345678 (release)",
            "timestamp 1708097621",
            "update-time 1708097621",
            "capabilities 0x0000007f",
        ]
    )

    payload = _parse_vcgencmd_bootloader_version(raw)

    assert payload["available"] is True
    assert payload["date"] == "2024/02/16 15:33:41"
    assert payload["version"].startswith("1234567890abcdef")
    assert payload["timestamp"] == 1708097621
    assert payload["update_time"] == 1708097621
    assert payload["capabilities"] == "0x0000007f"
    assert payload["raw"] == raw


def test_parse_vcgencmd_firmware_version_parses_structured_fields():
    raw = "\n".join(
        [
            "Nov 19 2025 12:34:56",
            "Copyright (c) 2012 Broadcom",
            "version abcdef1234567890 (release) (start)",
        ]
    )

    payload = _parse_vcgencmd_firmware_version(raw)

    assert payload["available"] is True
    assert payload["date"] == "Nov 19 2025 12:34:56"
    assert payload["build_info"] == "Copyright (c) 2012 Broadcom"
    assert payload["version"] == "abcdef1234567890 (release) (start)"
    assert payload["raw"] == raw


def test_parse_vcgencmd_pmic_read_adc_sums_paired_rails_and_ignores_unmatched():
    raw = "\n".join(
        [
            "VDD_CORE_V volt(2)=0.900000V",
            "VDD_CORE_A current(2)=1.500000A",
            "VDD_SOC_V volt(3)=0.850000V",
            "VDD_SOC_A current(3)=0.700000A",
            "EXT5V_V volt(4)=5.020000V",
        ]
    )

    payload = _parse_vcgencmd_pmic_read_adc(raw)

    assert payload["available"] is True
    assert payload["method"] == "pmic_read_adc"
    assert payload["rails_paired_count"] == 2
    assert payload["total_watts"] == pytest.approx((0.9 * 1.5) + (0.85 * 0.7), rel=0, abs=1e-6)
    assert "excludes main 5V input current" in payload["disclaimer"]
    assert payload["error"] is None


def test_read_swap_label_prefers_zram_when_active(monkeypatch):
    class _SwapFile:
        def read_text(self, encoding="utf-8"):
            return (
                "Filename\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
                "/dev/zram0                              partition\t2097148\t114688\t100\n"
            )

    monkeypatch.setattr("app.runtime_state.Path.exists", lambda self: str(self) == "/proc/swaps")
    monkeypatch.setattr("app.runtime_state.Path.read_text", lambda self, encoding="utf-8": _SwapFile().read_text(encoding))

    assert _read_swap_label() == "zram"


def test_collect_system_metrics_snapshot_includes_platform_and_power_fields(monkeypatch):
    monkeypatch.setitem(runtime_state._SYSTEM_STATIC_INFO_CACHE, "expires_at_unix", 0)
    monkeypatch.setitem(runtime_state._SYSTEM_STATIC_INFO_CACHE, "value", None)
    monkeypatch.setattr("app.runtime_state.psutil", None)
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.1")
    monkeypatch.setattr("app.runtime_state._read_os_release_pretty_name", lambda: "Debian GNU/Linux 12 (bookworm)")
    monkeypatch.setattr(
        "app.runtime_state._read_kernel_version_info",
        lambda: {
            "kernel_release": "6.12.62+rpt-rpi-2712",
            "kernel_version": "#1 SMP PREEMPT Debian 6.12.62-1+rpt1",
        },
    )

    def _fake_vcgencmd(*args):
        if args == ("pmic_read_adc",):
            return "\n".join(
                [
                    "VDD_CORE_V volt(2)=0.900000V",
                    "VDD_CORE_A current(2)=1.500000A",
                ]
            )
        if args == ("bootloader_version",):
            return "2024/02/16 15:33:41\nversion deadbeef\ntimestamp 1708097621"
        if args == ("version",):
            return "Nov 19 2025 12:34:56\nCopyright (c) 2012 Broadcom\nversion abc123"
        return None

    monkeypatch.setattr("app.runtime_state._run_vcgencmd", _fake_vcgencmd)
    monkeypatch.setattr("app.runtime_state._read_sysfs_temp", lambda: None)
    monkeypatch.setattr("app.runtime_state._read_swap_label", lambda: "zram")

    snapshot = collect_system_metrics_snapshot()

    assert "pi_model_name" in snapshot
    assert snapshot["pi_model_name"] == "Raspberry Pi 5 Model B Rev 1.1"
    assert snapshot["os_pretty_name"] == "Debian GNU/Linux 12 (bookworm)"
    assert snapshot["kernel_release"] == "6.12.62+rpt-rpi-2712"
    assert snapshot["kernel_version"].startswith("#1 SMP")
    assert snapshot["bootloader_version"]["available"] is True
    assert snapshot["firmware_version"]["available"] is True
    assert snapshot["power_estimate"]["available"] is True
    assert snapshot["power_estimate"]["total_watts"] == pytest.approx(1.35, rel=0, abs=1e-6)
    assert snapshot["swap_label"] == "zram"


def test_fit_linear_power_calibration_from_two_samples():
    fit = _fit_linear_power_calibration(
        [
            {"raw_pmic_watts": 5.0, "wall_watts": 6.5},
            {"raw_pmic_watts": 8.0, "wall_watts": 9.8},
        ]
    )

    assert fit is not None
    assert fit["sample_count"] == 2
    assert fit["a"] > 0
    assert isinstance(fit["b"], float)


def test_fit_linear_power_calibration_rejects_degenerate_samples():
    fit = _fit_linear_power_calibration(
        [
            {"raw_pmic_watts": 5.0, "wall_watts": 6.0},
            {"raw_pmic_watts": 5.0, "wall_watts": 7.0},
        ]
    )
    assert fit is None


def test_apply_power_calibration_uses_linear_model():
    adjusted = _apply_power_calibration(5.0, a=1.2, b=0.5)
    assert adjusted == pytest.approx(6.5, rel=0, abs=1e-6)


def test_normalize_power_calibration_settings_defaults_and_preserves_samples():
    payload = normalize_power_calibration_settings(
        {
            "mode": "custom",
            "a": 1.23,
            "b": 0.45,
            "fitted_at_unix": 123,
            "samples": [{"raw_pmic_watts": 4.2, "wall_watts": 5.9, "captured_at_unix": 111}],
        }
    )
    assert payload["mode"] == "custom"
    assert payload["a"] == pytest.approx(1.23)
    assert payload["b"] == pytest.approx(0.45)
    assert payload["sample_count"] == 1
    assert len(payload["samples"]) == 1


def test_build_power_estimate_status_adds_adjusted_power_and_calibration(runtime):
    status = build_power_estimate_status(
        runtime,
        {
            "available": True,
            "total_watts": 5.0,
            "label": "PMIC rails estimate",
            "method": "pmic_read_adc",
        },
    )
    assert status["raw_total_watts"] == 5.0
    assert status["adjusted_total_watts"] is not None
    assert status["calibration"]["mode"] in {"default", "custom"}
    assert "estimated_total_disclaimer" in status


def test_classify_runtime_device_identifies_pi5_16gb():
    device = classify_runtime_device(
        total_memory_bytes=16 * 1024 * 1024 * 1024,
        pi_model_name="Raspberry Pi 5 Model B Rev 1.1",
    )
    assert device == "pi5-16gb"


def test_classify_runtime_device_identifies_pi5_8gb():
    device = classify_runtime_device(
        total_memory_bytes=8 * 1024 * 1024 * 1024,
        pi_model_name="Raspberry Pi 5 Model B Rev 1.0",
    )
    assert device == "pi5-8gb"


def test_classify_runtime_device_identifies_pi4_8gb():
    device = classify_runtime_device(
        total_memory_bytes=8 * 1024 * 1024 * 1024,
        pi_model_name="Raspberry Pi 4 Model B Rev 1.5",
    )
    assert device == "pi4-8gb"


def test_classify_runtime_device_identifies_pi4_4gb():
    device = classify_runtime_device(
        total_memory_bytes=4 * 1024 * 1024 * 1024,
        pi_model_name="Raspberry Pi 4 Model B Rev 1.4",
    )
    assert device == "pi4-4gb"


def test_classify_runtime_device_identifies_other_pi():
    device = classify_runtime_device(
        total_memory_bytes=1 * 1024 * 1024 * 1024,
        pi_model_name="Raspberry Pi 3 Model B Plus Rev 1.3",
    )
    assert device == "other-pi"


def test_large_model_warn_threshold_defaults_to_5gib(monkeypatch):
    monkeypatch.delenv("POTATO_UNSUPPORTED_PI_LARGE_MODEL_WARN_BYTES", raising=False)
    assert get_large_model_warn_threshold_bytes() == LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT


def test_large_model_warn_threshold_honors_env_override(monkeypatch):
    monkeypatch.setenv("POTATO_UNSUPPORTED_PI_LARGE_MODEL_WARN_BYTES", "12345")
    assert get_large_model_warn_threshold_bytes() == 12345


def test_build_large_model_compatibility_warns_on_unsupported_large_model(monkeypatch, runtime):
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.0")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)

    payload = build_large_model_compatibility(
        runtime,
        model_filename="Qwen_Qwen3.5-35B-A3B-Q2_K_L.gguf",
        model_size_bytes=6 * 1024 * 1024 * 1024,
    )

    assert payload["device_class"] == "pi5-8gb"
    assert payload["large_model_warn_threshold_bytes"] == LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT
    assert payload["warnings"]
    assert "Pi 5 16GB" in payload["warnings"][0]["message"]


def test_build_large_model_compatibility_no_warning_on_pi5_16gb(monkeypatch, runtime):
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.1")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 16 * 1024 * 1024 * 1024)

    payload = build_large_model_compatibility(
        runtime,
        model_filename="Qwen_Qwen3.5-35B-A3B-Q2_K_L.gguf",
        model_size_bytes=6 * 1024 * 1024 * 1024,
    )

    assert payload["device_class"] == "pi5-16gb"
    assert payload["warnings"] == []


def test_runtime_device_compatibility_pi4_ik_llama_incompatible():
    result = check_runtime_device_compatibility("pi4-8gb", "ik_llama")
    assert result["compatible"] is False
    assert result["recommended_family"] == "llama_cpp"
    assert "dot product" in result["reason"]


def test_runtime_device_compatibility_pi4_llama_cpp_ok():
    result = check_runtime_device_compatibility("pi4-8gb", "llama_cpp")
    assert result["compatible"] is True
    assert result["reason"] is None


def test_runtime_device_compatibility_pi5_ik_llama_ok():
    result = check_runtime_device_compatibility("pi5-8gb", "ik_llama")
    assert result["compatible"] is True


def test_runtime_device_compatibility_pi4_4gb_ik_llama_incompatible():
    result = check_runtime_device_compatibility("pi4-4gb", "ik_llama")
    assert result["compatible"] is False
    assert result["recommended_family"] == "llama_cpp"


@pytest.mark.anyio
async def test_ensure_compatible_runtime_switches_on_pi4(monkeypatch, runtime, tmp_path):
    # Set up a fake llama_cpp slot
    slot_dir = runtime.base_dir / "runtimes" / "llama_cpp"
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / "bin").mkdir(parents=True, exist_ok=True)
    (slot_dir / "bin" / "llama-server").write_bytes(b"fake")
    (slot_dir / "bin" / "llama-server").chmod(0o755)
    (slot_dir / "lib").mkdir(exist_ok=True)
    import json
    (slot_dir / "runtime.json").write_text(json.dumps({
        "family": "llama_cpp", "commit": "abc", "profile": "pi4-opt",
    }))
    # Simulate ik_llama as current runtime
    marker_path = runtime.base_dir / "llama" / ".potato-llama-runtime-bundle.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps({"family": "ik_llama", "profile": "pi5-opt"}))

    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024**3)

    switched, reason = await ensure_compatible_runtime(runtime)
    assert switched is True
    assert reason == "pi4_incompatible_runtime"


@pytest.mark.anyio
async def test_ensure_compatible_runtime_noop_on_pi5(monkeypatch, runtime):
    import json
    marker_path = runtime.base_dir / "llama" / ".potato-llama-runtime-bundle.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps({"family": "ik_llama", "profile": "pi5-opt"}))

    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.0")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024**3)

    switched, reason = await ensure_compatible_runtime(runtime)
    assert switched is False
    assert reason == "compatible"


@pytest.mark.anyio
async def test_ensure_compatible_runtime_detects_family_from_runtime_json_when_no_marker(monkeypatch, runtime):
    """P4: Fresh install has no marker file. Should read runtime.json from install dir."""
    import json
    # No marker, but runtime.json exists in the llama install dir
    llama_dir = runtime.base_dir / "llama"
    llama_dir.mkdir(parents=True, exist_ok=True)
    (llama_dir / "runtime.json").write_text(json.dumps({"family": "ik_llama", "profile": "pi5-opt"}))
    (llama_dir / "bin").mkdir(parents=True, exist_ok=True)
    (llama_dir / "bin" / "llama-server").write_bytes(b"fake")
    # Set up llama_cpp slot
    slot_dir = runtime.base_dir / "runtimes" / "llama_cpp"
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / "bin").mkdir(parents=True, exist_ok=True)
    (slot_dir / "bin" / "llama-server").write_bytes(b"fake")
    (slot_dir / "bin" / "llama-server").chmod(0o755)
    (slot_dir / "lib").mkdir(exist_ok=True)
    (slot_dir / "runtime.json").write_text(json.dumps({"family": "llama_cpp", "commit": "abc", "profile": "pi4-opt"}))

    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024**3)

    switched, reason = await ensure_compatible_runtime(runtime)
    assert switched is True
    assert reason == "pi4_incompatible_runtime"


@pytest.mark.anyio
async def test_ensure_compatible_runtime_noop_when_already_llama_cpp(monkeypatch, runtime):
    import json
    marker_path = runtime.base_dir / "llama" / ".potato-llama-runtime-bundle.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps({"family": "llama_cpp", "profile": "pi4-opt"}))

    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024**3)

    switched, reason = await ensure_compatible_runtime(runtime)
    assert switched is False
    assert reason == "compatible"


@pytest.mark.anyio
async def test_ensure_compatible_runtime_returns_false_when_install_fails(monkeypatch, runtime):
    """Auto-switch must report failure when install_llama_runtime_bundle returns ok=False."""
    import json
    # ik_llama marker
    llama_dir = runtime.base_dir / "llama"
    llama_dir.mkdir(parents=True, exist_ok=True)
    (llama_dir / "runtime.json").write_text(json.dumps({"family": "ik_llama"}))
    # llama_cpp slot exists
    slot_dir = runtime.base_dir / "runtimes" / "llama_cpp"
    slot_dir.mkdir(parents=True, exist_ok=True)
    (slot_dir / "bin").mkdir(parents=True, exist_ok=True)
    (slot_dir / "bin" / "llama-server").write_bytes(b"fake")
    (slot_dir / "bin" / "llama-server").chmod(0o755)
    (slot_dir / "lib").mkdir(exist_ok=True)
    (slot_dir / "runtime.json").write_text(json.dumps({"family": "llama_cpp"}))

    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024**3)

    async def _fake_install_fail(_runtime, _path):
        return {"ok": False, "reason": "rsync_not_available"}

    monkeypatch.setattr("app.runtime_state.install_llama_runtime_bundle", _fake_install_fail)

    switched, reason = await ensure_compatible_runtime(runtime)
    assert switched is False
    assert reason == "install_failed"


def test_compatibility_detects_runtime_from_runtime_json_when_no_marker(monkeypatch, runtime):
    """P3: build_large_model_compatibility should detect ik_llama from runtime.json on fresh installs."""
    import json
    llama_dir = runtime.base_dir / "llama"
    llama_dir.mkdir(parents=True, exist_ok=True)
    (llama_dir / "runtime.json").write_text(json.dumps({"family": "ik_llama"}))

    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024**3)

    payload = build_large_model_compatibility(runtime)
    assert payload["runtime_compatibility"]["compatible"] is False
    assert payload["runtime_compatibility"]["recommended_family"] == "llama_cpp"


def test_runtime_status_detects_family_from_runtime_json_when_no_marker(runtime):
    """P3: build_llama_runtime_status should show family from runtime.json on fresh installs."""
    import json
    from app.runtime_state import build_llama_runtime_status
    llama_dir = runtime.base_dir / "llama"
    llama_dir.mkdir(parents=True, exist_ok=True)
    (llama_dir / "bin").mkdir(parents=True, exist_ok=True)
    (llama_dir / "bin" / "llama-server").write_bytes(b"fake")
    (llama_dir / "runtime.json").write_text(json.dumps({"family": "llama_cpp", "profile": "pi4-opt"}))

    status = build_llama_runtime_status(runtime)
    assert status["current"]["family"] == "llama_cpp"
    assert status["current"]["profile"] == "pi4-opt"


def test_runtime_config_model_path_uses_device_default_on_pi4(monkeypatch):
    """P1: model_path should align with the Pi 4 default model, not hardcode 2B."""
    from app.model_state import MODEL_FILENAME_PI4
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.delenv("POTATO_MODEL_PATH", raising=False)
    config = RuntimeConfig.from_env()
    assert config.model_path.name == MODEL_FILENAME_PI4


def test_runtime_config_model_path_uses_2b_on_pi5(monkeypatch):
    """P1: Pi 5 should still default to 2B."""
    from app.model_state import MODEL_FILENAME
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.0")
    monkeypatch.delenv("POTATO_MODEL_PATH", raising=False)
    config = RuntimeConfig.from_env()
    assert config.model_path.name == MODEL_FILENAME


def test_cpu_load_power_estimate_idle():
    result = _estimate_power_from_cpu_load(cpu_percent=0.0, device_class="pi4-8gb")
    assert result["available"] is True
    assert result["method"] == "cpu_load_estimate"
    assert result["total_watts"] == pytest.approx(3.0, abs=0.5)


def test_cpu_load_power_estimate_full_load():
    result = _estimate_power_from_cpu_load(cpu_percent=100.0, device_class="pi4-8gb")
    assert result["available"] is True
    assert result["total_watts"] == pytest.approx(6.0, abs=0.5)


def test_cpu_load_power_estimate_half_load():
    result = _estimate_power_from_cpu_load(cpu_percent=50.0, device_class="pi4-8gb")
    assert result["available"] is True
    assert 4.0 <= result["total_watts"] <= 5.0


def test_cpu_load_power_estimate_pi5_returns_unavailable():
    result = _estimate_power_from_cpu_load(cpu_percent=50.0, device_class="pi5-8gb")
    assert result["available"] is False
    assert result["method"] == "cpu_load_estimate"


def test_cpu_load_power_estimate_unknown_device_returns_unavailable():
    result = _estimate_power_from_cpu_load(cpu_percent=50.0, device_class="unknown")
    assert result["available"] is False


def test_pi4_power_calibration_defaults_differ_from_pi5(monkeypatch):
    from app.runtime_state import (
        POWER_CALIBRATION_DEFAULT_A,
        POWER_CALIBRATION_DEFAULT_B,
        POWER_CALIBRATION_DEFAULT_A_PI4,
        POWER_CALIBRATION_DEFAULT_B_PI4,
        _get_power_calibration_default_coefficients,
    )
    monkeypatch.delenv("POTATO_POWER_ESTIMATE_ADJUST_A", raising=False)
    monkeypatch.delenv("POTATO_POWER_ESTIMATE_ADJUST_B", raising=False)

    # Pi 4: uses Pi 4 coefficients
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    a, b = _get_power_calibration_default_coefficients()
    assert a == pytest.approx(POWER_CALIBRATION_DEFAULT_A_PI4)
    assert b == pytest.approx(POWER_CALIBRATION_DEFAULT_B_PI4)

    # Pi 5: uses Pi 5 coefficients
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.0")
    a, b = _get_power_calibration_default_coefficients()
    assert a == pytest.approx(POWER_CALIBRATION_DEFAULT_A)
    assert b == pytest.approx(POWER_CALIBRATION_DEFAULT_B)
