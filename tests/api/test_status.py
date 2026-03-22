from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import build_status, create_app, get_runtime, refresh_llama_readiness


def test_status_booting_when_model_missing(client, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()

    assert body["state"] == "BOOTING"
    assert body["model_present"] is False
    assert body["model"]["filename"] == "Qwen3.5-2B-Q4_K_M.gguf"
    assert body["download"]["bytes_downloaded"] == 0
    assert body["download"]["default_model_filename"] == "Qwen3.5-2B-Q4_K_M.gguf"


def test_status_downloading_when_progress_exists(client, runtime, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)
    runtime.download_state_path.write_text(
        json.dumps(
            {
                "bytes_total": 100,
                "bytes_downloaded": 40,
                "percent": 40,
                "speed_bps": 12,
                "eta_seconds": 5,
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()

    assert body["state"] == "DOWNLOADING"
    assert body["download"]["percent"] == 40


def test_status_ready_when_model_exists_and_llama_healthy(client, runtime, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)
    runtime.model_path.write_bytes(b"gguf")

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()

    assert body["state"] == "READY"
    assert body["model_present"] is True
    assert body["llama_server"]["healthy"] is True


def test_status_uses_cached_llama_readiness_when_orchestrated(runtime):
    runtime.enable_orchestrator = True
    runtime.model_path.write_bytes(b"gguf")
    app = create_app(runtime=runtime)
    app.state.llama_readiness_state.update(
        {
            "model_path": str(runtime.model_path),
            "status": "warming",
            "transport_healthy": True,
            "ready": False,
            "healthy_polls": 1,
        }
    )
    body = asyncio.run(build_status(runtime, app=app))
    assert body["state"] == "BOOTING"
    assert body["llama_server"]["transport_healthy"] is True
    assert body["llama_server"]["healthy"] is False
    assert body["llama_server"]["ready"] is False


def test_refresh_llama_readiness_marks_ready_after_strict_health_stabilizes(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    runtime.model_path.write_bytes(b"gguf")
    app = create_app(runtime=runtime)

    class _DummyProc:
        returncode = None

    app.state.llama_process = _DummyProc()
    health_calls: list[bool] = []

    async def _strict_true(_runtime, busy_is_healthy: bool = True):
        health_calls.append(busy_is_healthy)
        return True

    monkeypatch.setattr("app.main.check_llama_health", _strict_true)

    first = asyncio.run(refresh_llama_readiness(app, runtime, active_model_path=runtime.model_path))
    second = asyncio.run(refresh_llama_readiness(app, runtime, active_model_path=runtime.model_path))

    assert first["status"] == "warming"
    assert first["ready"] is False
    assert second["status"] == "ready"
    assert second["ready"] is True
    assert second["transport_healthy"] is True
    assert health_calls == [False, False]
    assert app.state.llama_readiness_state["last_ready_at_unix"] is not None


def test_refresh_llama_readiness_stays_ready_when_busy_after_prior_readiness(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    runtime.model_path.write_bytes(b"gguf")
    app = create_app(runtime=runtime)

    class _DummyProc:
        returncode = None

    app.state.llama_process = _DummyProc()
    app.state.llama_readiness_state.update(
        {
            "model_path": str(runtime.model_path),
            "status": "ready",
            "transport_healthy": True,
            "ready": True,
            "healthy_polls": 2,
            "last_ready_at_unix": 123.0,
        }
    )
    health_calls: list[bool] = []

    async def _busy_after_ready(_runtime, busy_is_healthy: bool = True):
        health_calls.append(busy_is_healthy)
        return busy_is_healthy

    monkeypatch.setattr("app.main.check_llama_health", _busy_after_ready)

    result = asyncio.run(refresh_llama_readiness(app, runtime, active_model_path=runtime.model_path))

    assert result["status"] == "ready"
    assert result["ready"] is True
    assert result["transport_healthy"] is True
    assert health_calls == [True]


def test_status_includes_large_model_warning_for_unsupported_pi(client, runtime, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.5")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    with runtime.model_path.open("wb") as handle:
        handle.seek((6 * 1024 * 1024 * 1024) - 1)
        handle.write(b"x")

    response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert "compatibility" in body
    assert body["compatibility"]["device_class"] == "pi4-8gb"
    assert body["compatibility"]["warnings"]
    assert body["compatibility"]["warnings"][0]["code"] == "large_model_unsupported_pi_warning"


def test_status_includes_llama_runtime_payload(client, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)
    monkeypatch.setattr(
        "app.main.build_llama_runtime_status",
        lambda _runtime, app=None: {
            "current": {"install_dir": "/opt/potato/llama", "family": "ik_llama"},
            "available_runtimes": [{"family": "ik_llama", "path": "/opt/potato/runtimes/ik_llama"}],
            "switch": {"active": False, "error": None},
        },
    )

    response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert "llama_runtime" in body
    assert body["llama_runtime"]["current"]["install_dir"] == "/opt/potato/llama"
    assert len(body["llama_runtime"]["available_runtimes"]) == 1


def test_status_includes_llama_memory_loading_setting(client):
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert "llama_runtime" in body
    assert "memory_loading" in body["llama_runtime"]
    assert body["llama_runtime"]["memory_loading"]["mode"] in {"auto", "full_ram", "mmap"}


def test_status_includes_large_model_override_setting(client):
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert "llama_runtime" in body
    assert "large_model_override" in body["llama_runtime"]
    assert body["llama_runtime"]["large_model_override"]["enabled"] in {True, False}
    assert "override_enabled" in body["compatibility"]


def test_status_includes_active_model_storage_details(client, runtime):
    runtime.model_path.write_bytes(b"gguf")

    response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["model"]["storage"]["location"] == "local"


def test_status_reconciles_active_model_from_runtime_path_when_state_is_missing(client, runtime, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)

    default_path = runtime.model_path
    default_path.write_bytes(b"default")
    custom_path = runtime.base_dir / "models" / "custom-ready.gguf"
    custom_path.write_bytes(b"custom")
    runtime.model_path = custom_path
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": None,
                "default_model_id": "default",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "default",
                        "filename": default_path.name,
                        "source_url": "https://example.com/default.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                    },
                    {
                        "id": "custom-ready",
                        "filename": custom_path.name,
                        "source_url": "https://example.com/custom.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["model"]["filename"] == "custom-ready.gguf"
    assert body["model"]["active_model_id"] == "custom-ready"
    assert next(model for model in body["models"] if model["id"] == "custom-ready")["is_active"] is True
    assert next(model for model in body["models"] if model["id"] == "default")["is_active"] is False

    saved_state = json.loads(runtime.models_state_path.read_text(encoding="utf-8"))
    assert saved_state["active_model_id"] == "custom-ready"


def test_status_includes_canonical_version(client):
    from app.__version__ import __version__

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert "version" in body
    assert body["version"] == __version__


def test_status_includes_platform_version_and_power_fields_under_system(client):
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()

    system = body["system"]
    assert "pi_model_name" in system
    assert "os_pretty_name" in system
    assert "kernel_release" in system
    assert "kernel_version" in system
    assert "bootloader_version" in system
    assert "firmware_version" in system
    assert "power_estimate" in system
    assert "swap_label" in system

    power = system["power_estimate"]
    assert power["method"] == "pmic_read_adc"
    assert "PMIC" in power["label"]
    assert "estimate" in power["label"].lower()
    assert "disclaimer" in power
    assert "raw_total_watts" in power
    assert "adjusted_total_watts" in power
    assert "calibration" in power
    assert power["calibration"]["mode"] in {"default", "custom"}
    assert system["swap_label"] in {"swap", "zram"}


def test_status_stays_ready_when_active_model_healthy_and_download_error_is_from_side_model(
    client,
    runtime,
    monkeypatch,
):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)
    runtime.model_path.write_bytes(b"gguf")
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "default",
                "default_model_id": "default",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "default",
                        "filename": runtime.model_path.name,
                        "source_url": "https://example.com/default.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                    },
                    {
                        "id": "side-model",
                        "filename": "side-model.gguf",
                        "source_url": "https://example.com/side-model.gguf",
                        "source_type": "url",
                        "status": "failed",
                        "error": "insufficient_storage",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    runtime.download_state_path.write_text(
        json.dumps(
            {
                "bytes_total": 1024,
                "bytes_downloaded": 0,
                "percent": 0,
                "speed_bps": 0,
                "eta_seconds": 0,
                "error": "insufficient_storage",
            }
        ),
        encoding="utf-8",
    )

    response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "READY"
    assert body["download"]["error"] == "insufficient_storage"


async def _healthy_true(_runtime):
    return True


async def _healthy_false(_runtime):
    return False


def test_llama_healthz_reports_strict_probe_result(client, monkeypatch):
    async def _strict_false(_runtime, busy_is_healthy: bool = True):
        assert busy_is_healthy is False
        return False

    async def _probe_true(_runtime):
        return True

    monkeypatch.setattr("app.main.check_llama_health", _strict_false)
    monkeypatch.setattr("app.main.probe_llama_inference_slot", _probe_true)

    response = client.get("/internal/llama-healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["healthy"] is False
    assert body["transport_healthy"] is False
    assert body["inference_healthy"] is False


def test_restart_llama_returns_conflict_when_orchestrator_disabled(client):
    response = client.post("/internal/restart-llama")
    assert response.status_code == 409
    body = response.json()
    assert body["restarted"] is False
    assert body["reason"] == "orchestrator_disabled"


def test_cancel_llama_returns_conflict_when_orchestrator_disabled(client):
    response = client.post("/internal/cancel-llama")
    assert response.status_code == 409
    body = response.json()
    assert body["cancelled"] is False
    assert body["reason"] == "orchestrator_disabled"


def test_start_model_download_returns_conflict_when_orchestrator_disabled(client):
    response = client.post("/internal/start-model-download")
    assert response.status_code == 409
    body = response.json()
    assert body["started"] is False
    assert body["reason"] == "orchestrator_disabled"


def test_reset_runtime_returns_conflict_when_orchestrator_disabled(client):
    response = client.post("/internal/reset-runtime")
    assert response.status_code == 409
    body = response.json()
    assert body["started"] is False
    assert body["reason"] == "orchestrator_disabled"


def test_start_model_download_starts_when_enabled(runtime, monkeypatch):
    app = create_app(runtime=runtime, enable_orchestrator=False)
    runtime.enable_orchestrator = True
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _start(_app, _runtime, trigger: str):
        assert trigger == "manual"
        return True, "started"

    monkeypatch.setattr("app.main.start_model_download", _start)

    with TestClient(app) as test_client:
        response = test_client.post("/internal/start-model-download")

    assert response.status_code == 202
    body = response.json()
    assert body["started"] is True
    assert body["reason"] == "started"


def test_start_model_download_insufficient_storage_includes_size_fields(runtime, monkeypatch):
    app = create_app(runtime=runtime, enable_orchestrator=False)
    runtime.enable_orchestrator = True
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _start(_app, _runtime, trigger: str):
        return False, "insufficient_storage"

    monkeypatch.setattr("app.main.start_model_download", _start)
    runtime.download_state_path.write_text(
        json.dumps({
            "bytes_total": 5000000000,
            "bytes_downloaded": 0,
            "percent": 0,
            "error": "insufficient_storage",
            "free_bytes": 2000000000,
            "required_bytes": 5000000000,
        }),
        encoding="utf-8",
    )

    with TestClient(app) as test_client:
        response = test_client.post("/internal/start-model-download")

    assert response.status_code == 200
    body = response.json()
    assert body["started"] is False
    assert body["reason"] == "insufficient_storage"
    assert body["free_bytes"] == 2000000000
    assert body["required_bytes"] == 5000000000


def test_reset_runtime_starts_when_enabled(runtime, monkeypatch):
    app = create_app(runtime=runtime, enable_orchestrator=False)
    runtime.enable_orchestrator = True
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _start(_runtime):
        return True, "scheduled"

    monkeypatch.setattr("app.main.start_runtime_reset", _start)

    with TestClient(app) as test_client:
        response = test_client.post("/internal/reset-runtime")

    assert response.status_code == 202
    body = response.json()
    assert body["started"] is True
    assert body["reason"] == "scheduled"


def test_reset_runtime_returns_not_started_reason(runtime, monkeypatch):
    app = create_app(runtime=runtime, enable_orchestrator=False)
    runtime.enable_orchestrator = True
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _start(_runtime):
        return False, "script_missing"

    monkeypatch.setattr("app.main.start_runtime_reset", _start)

    with TestClient(app) as test_client:
        response = test_client.post("/internal/reset-runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["started"] is False
    assert body["reason"] == "script_missing"


def test_status_includes_auto_start_countdown(runtime, monkeypatch):
    app = create_app(runtime=runtime, enable_orchestrator=False)
    runtime.enable_orchestrator = True
    runtime.auto_download_idle_seconds = 300
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main.get_monotonic_time", lambda: 240.0)
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)

    with TestClient(app) as test_client:
        app.state.startup_monotonic = 100.0
        response = test_client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["download"]["active"] is False
    assert body["download"]["auto_start_seconds"] == 300
    assert body["download"]["auto_start_remaining_seconds"] == 160
    assert body["download"]["auto_download_completed_once"] is False
    assert body["download"]["countdown_enabled"] is True
    assert body["download"]["auto_download_paused"] is False


def test_status_disables_auto_start_when_default_model_was_downloaded_once(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    runtime.auto_download_idle_seconds = 300
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "default",
                "default_model_id": "default",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "default",
                        "filename": "Qwen3.5-2B-Q4_K_M.gguf",
                        "source_url": "https://example.com/Qwen3.5-2B-Q4_K_M.gguf",
                        "source_type": "url",
                        "status": "not_downloaded",
                        "error": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main.get_monotonic_time", lambda: 2000.0)
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)

    with TestClient(app) as test_client:
        app.state.startup_monotonic = 100.0
        response = test_client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["download"]["auto_download_completed_once"] is True
    assert body["download"]["auto_start_remaining_seconds"] == 0
    assert body["download"]["countdown_enabled"] is False
    assert body["download"]["auto_download_paused"] is False


def test_status_falls_back_download_target_model_when_missing(runtime, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)
    runtime.download_state_path.write_text(
        json.dumps(
            {
                "bytes_total": 100,
                "bytes_downloaded": 50,
                "percent": 50,
                "speed_bps": 10,
                "eta_seconds": 5,
            }
        ),
        encoding="utf-8",
    )

    body = asyncio.run(
        build_status(
            runtime,
            app=None,
            download_active=True,
            auto_start_remaining_seconds=0,
            system_snapshot=None,
        )
    )

    assert body["download"]["current_model_id"] == "default"
    default_model = next(item for item in body["models"] if item["id"] == "default")
    assert default_model["status"] == "downloading"
    assert default_model["percent"] == 50


def test_status_includes_model_settings_and_projector_metadata(client):
    response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    default_model = next(item for item in body["models"] if item["id"] == "default")
    assert "settings" in default_model
    assert default_model["settings"]["chat"]["temperature"] == 0.7
    assert "capabilities" in default_model
    assert default_model["capabilities"]["vision"] is True
    assert "projector" in default_model


def test_status_includes_system_runtime_payload(runtime, monkeypatch):
    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)

    sample_system = {
        "available": True,
        "updated_at_unix": 1771778048,
        "cpu_percent": 21.4,
        "cpu_cores_percent": [18.0, 24.0, 19.0, 22.0],
        "cpu_clock_arm_hz": 2400023808,
        "memory_total_bytes": 7900000000,
        "memory_used_bytes": 4800000000,
        "memory_percent": 61.0,
        "swap_total_bytes": 2000000000,
        "swap_used_bytes": 7000000,
        "swap_percent": 0.35,
        "storage_total_bytes": 64000000000,
        "storage_used_bytes": 22000000000,
        "storage_free_bytes": 42000000000,
        "storage_percent": 34.37,
        "temperature_c": 67.5,
        "gpu_clock_core_hz": 910007424,
        "gpu_clock_v3d_hz": 960012800,
        "throttling": {
            "raw": "0x80000",
            "any_current": False,
            "any_history": True,
            "current_flags": [],
            "history_flags": ["Soft temp limit occurred"],
        },
    }

    with TestClient(app) as test_client:
        app.state.system_metrics_snapshot = sample_system
        response = test_client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["system"]["available"] is True
    assert body["system"]["cpu_cores_percent"] == [18.0, 24.0, 19.0, 22.0]
    assert body["system"]["cpu_clock_arm_hz"] == 2400023808
    assert body["system"]["storage_free_bytes"] == 42000000000
    assert body["system"]["throttling"]["raw"] == "0x80000"


def test_cancel_llama_uses_slot_action_when_available(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _slot_cancel(_runtime):
        return True, "interrupt"

    async def _restart(_app):
        raise AssertionError("restart should not be called when slot cancel succeeds")

    monkeypatch.setattr("app.main.request_llama_slot_cancel", _slot_cancel)
    monkeypatch.setattr("app.main.restart_managed_llama_process", _restart)

    with TestClient(app) as test_client:
        response = test_client.post("/internal/cancel-llama")

    assert response.status_code == 200
    body = response.json()
    assert body["cancelled"] is True
    assert body["method"] == "slot:interrupt"
    assert body["restarted"] is False


def test_cancel_llama_returns_no_restart_when_slot_action_unavailable(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _slot_cancel(_runtime):
        return False, "none"

    async def _restart(_app):
        raise AssertionError("restart should not be called by cancel endpoint")

    monkeypatch.setattr("app.main.request_llama_slot_cancel", _slot_cancel)
    monkeypatch.setattr("app.main.restart_managed_llama_process", _restart)

    with TestClient(app) as test_client:
        response = test_client.post("/internal/cancel-llama")

    assert response.status_code == 200
    body = response.json()
    assert body["cancelled"] is False
    assert body["method"] == "none"
    assert body["restarted"] is False
    assert body["reason"] == "slot_action_unavailable"


def test_restart_llama_terminates_running_process_when_enabled(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    class _DummyProc:
        def __init__(self) -> None:
            self.returncode = None
            self.terminated = False
            self.waited = False

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = 0

        async def wait(self) -> int:
            self.waited = True
            return 0

    dummy = _DummyProc()
    app.state.llama_process = dummy

    async def _no_stale(_runtime):
        return 0

    monkeypatch.setattr("app.main.terminate_stray_llama_processes", _no_stale)

    with TestClient(app) as test_client:
        response = test_client.post("/internal/restart-llama")

    assert response.status_code == 200
    body = response.json()
    assert body["restarted"] is True
    assert dummy.terminated is True
    assert dummy.waited is True


def test_restart_llama_terminates_stale_processes_when_no_managed_process(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    app.state.llama_process = None

    async def _terminate_stale(_runtime):
        return 1

    monkeypatch.setattr("app.main.terminate_stray_llama_processes", _terminate_stale)

    with TestClient(app) as test_client:
        response = test_client.post("/internal/restart-llama")

    assert response.status_code == 200
    body = response.json()
    assert body["restarted"] is True
    assert body["reason"] == "terminated_stale_processes"
