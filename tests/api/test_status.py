from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import create_app, get_runtime


def test_status_booting_when_model_missing(client, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()

    assert body["state"] == "BOOTING"
    assert body["model_present"] is False
    assert body["model"]["filename"] == "Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
    assert body["download"]["bytes_downloaded"] == 0


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


def test_restart_llama_terminates_running_process_when_enabled(runtime):
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

    with TestClient(app) as test_client:
        response = test_client.post("/internal/restart-llama")

    assert response.status_code == 200
    body = response.json()
    assert body["restarted"] is True
    assert dummy.terminated is True
    assert dummy.waited is True
