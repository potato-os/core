from __future__ import annotations

import json
import httpx
import pytest

from app.main import (
    LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT,
    RuntimeConfig,
    build_large_model_compatibility,
    check_llama_health,
    compute_required_download_bytes,
    compute_auto_download_remaining_seconds,
    classify_runtime_device,
    decode_throttled_bits,
    fetch_remote_content_length_bytes,
    get_large_model_warn_threshold_bytes,
    is_likely_too_large_for_storage,
    probe_llama_inference_slot,
    read_download_progress,
    request_llama_slot_cancel,
    should_auto_start_download,
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

    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda timeout: _BusyClient())

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

    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda timeout: _BusyClient())

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

    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda timeout: _DownClient())

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

    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda timeout: _StuckClient())

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

    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda timeout: _Client())

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

    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda timeout: _Client())

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

    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda timeout, follow_redirects: _Client())

    size_bytes = await fetch_remote_content_length_bytes("https://example.com/model.gguf")

    assert size_bytes == 1234567890


def test_runtime_from_env_defaults_to_llama_and_disables_fake_fallback(monkeypatch):
    monkeypatch.delenv("POTATO_CHAT_BACKEND", raising=False)
    monkeypatch.delenv("POTATO_ALLOW_FAKE_FALLBACK", raising=False)
    monkeypatch.setenv("POTATO_BASE_DIR", "/tmp/potato-test")

    runtime = RuntimeConfig.from_env()

    assert runtime.chat_backend_mode == "llama"
    assert runtime.allow_fake_fallback is False


def test_compute_auto_download_remaining_seconds_counts_down(runtime):
    runtime.enable_orchestrator = True
    runtime.auto_download_idle_seconds = 300

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


def test_should_auto_start_download_only_after_timeout(runtime):
    runtime.enable_orchestrator = True
    runtime.auto_download_idle_seconds = 300

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


def test_classify_runtime_device_identifies_other_pi():
    device = classify_runtime_device(
        total_memory_bytes=8 * 1024 * 1024 * 1024,
        pi_model_name="Raspberry Pi 4 Model B Rev 1.5",
    )
    assert device == "other-pi"


def test_large_model_warn_threshold_defaults_to_5gib(monkeypatch):
    monkeypatch.delenv("POTATO_UNSUPPORTED_PI_LARGE_MODEL_WARN_BYTES", raising=False)
    assert get_large_model_warn_threshold_bytes() == LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT


def test_large_model_warn_threshold_honors_env_override(monkeypatch):
    monkeypatch.setenv("POTATO_UNSUPPORTED_PI_LARGE_MODEL_WARN_BYTES", "12345")
    assert get_large_model_warn_threshold_bytes() == 12345


def test_build_large_model_compatibility_warns_on_unsupported_large_model(monkeypatch, runtime):
    monkeypatch.setattr("app.main._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.0")
    monkeypatch.setattr("app.main._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)

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
    monkeypatch.setattr("app.main._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.1")
    monkeypatch.setattr("app.main._detect_total_memory_bytes", lambda: 16 * 1024 * 1024 * 1024)

    payload = build_large_model_compatibility(
        runtime,
        model_filename="Qwen_Qwen3.5-35B-A3B-Q2_K_L.gguf",
        model_size_bytes=6 * 1024 * 1024 * 1024,
    )

    assert payload["device_class"] == "pi5-16gb"
    assert payload["warnings"] == []
