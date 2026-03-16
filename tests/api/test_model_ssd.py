from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import _runtime_env, create_app, ensure_models_state, get_runtime, save_models_state


async def _healthy_true(_runtime):
    return True


def test_move_model_to_ssd_moves_ready_model_and_reports_storage(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    runtime.model_path.write_bytes(b"default-model")
    ssd_dir = runtime.base_dir / "media" / "ssd" / "potato-models"
    ssd_dir.mkdir(parents=True)
    monkeypatch.setattr("app.main.get_preferred_model_offload_dir", lambda _runtime: ssd_dir)

    with TestClient(app) as client:
        response = client.post("/internal/models/move-to-ssd", json={"model_id": "default"})
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["moved"] is True
    assert body["model_id"] == "default"
    assert body["storage"]["location"] == "ssd"
    assert runtime.model_path == ssd_dir / "Qwen3.5-2B-Q4_K_M.gguf"
    managed_path = runtime.base_dir / "models" / "Qwen3.5-2B-Q4_K_M.gguf"
    assert managed_path.is_symlink()
    assert managed_path.resolve() == runtime.model_path

    status_body = status.json()
    default_model = next(model for model in status_body["models"] if model["id"] == "default")
    assert default_model["storage"]["location"] == "ssd"


def test_move_model_to_ssd_restarts_when_moving_active_model(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    runtime.model_path.write_bytes(b"default-model")
    ssd_dir = runtime.base_dir / "media" / "ssd" / "potato-models"
    ssd_dir.mkdir(parents=True)
    monkeypatch.setattr("app.main.get_preferred_model_offload_dir", lambda _runtime: ssd_dir)

    restart_calls: list[bool] = []

    async def _fake_restart(_app):
        restart_calls.append(True)
        return True, "restarted"

    monkeypatch.setattr("app.main.restart_managed_llama_process", _fake_restart)

    with TestClient(app) as client:
        response = client.post("/internal/models/move-to-ssd", json={"model_id": "default"})

    assert response.status_code == 200
    body = response.json()
    assert body["moved"] is True
    assert body["restarted"] is True
    assert body["restart_reason"] == "restarted"
    assert restart_calls == [True]
    assert runtime.model_path == ssd_dir / "Qwen3.5-2B-Q4_K_M.gguf"


def test_move_model_to_ssd_rejects_when_no_ssd_target(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    runtime.model_path.write_bytes(b"default-model")
    monkeypatch.setattr("app.main.get_preferred_model_offload_dir", lambda _runtime: None)

    with TestClient(app) as client:
        response = client.post("/internal/models/move-to-ssd", json={"model_id": "default"})

    assert response.status_code == 409
    assert response.json()["reason"] == "no_ssd_available"


def test_move_model_to_ssd_uses_worker_thread(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    runtime.model_path.write_bytes(b"default-model")
    ssd_dir = runtime.base_dir / "media" / "ssd" / "potato-models"
    ssd_dir.mkdir(parents=True)
    monkeypatch.setattr("app.main.get_preferred_model_offload_dir", lambda _runtime: ssd_dir)

    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def _fake_move(_runtime, *, model_id: str, ssd_dir):
        return True, "moved", {"location": "ssd", "actual_path": str(ssd_dir / "Qwen3.5-2B-Q4_K_M.gguf")}

    async def _fake_to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    async def _fake_restart(_app):
        return False, "not_required"

    monkeypatch.setattr("app.main.move_model_to_ssd", _fake_move)
    monkeypatch.setattr("app.main.asyncio.to_thread", _fake_to_thread)
    monkeypatch.setattr("app.main.restart_managed_llama_process", _fake_restart)

    with TestClient(app) as client:
        response = client.post("/internal/models/move-to-ssd", json={"model_id": "default"})

    assert response.status_code == 200
    assert calls == [(_fake_move, (runtime,), {"model_id": "default", "ssd_dir": ssd_dir})]



