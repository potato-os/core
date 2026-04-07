from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from core.main import _runtime_env, create_app, ensure_models_state, get_runtime, save_models_state


async def _healthy_true(_runtime):
    return True


def test_register_model_url_rejects_invalid(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        response = client.post("/internal/models/register", json={"source_url": "http://example.com/model.bin"})

    assert response.status_code == 400
    assert response.json()["reason"] in {"https_required", "unsupported_model_format"}


def test_activate_model_blocks_non_ready(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        reg = client.post(
            "/internal/models/register",
            json={"source_url": "https://example.com/fancy-model.gguf"},
        )
        model_id = reg.json()["model"]["id"]
        activate = client.post("/internal/models/activate", json={"model_id": model_id})

    assert reg.status_code == 200
    assert activate.status_code == 409
    assert activate.json()["reason"] == "model_not_ready"


def test_register_model_url_returns_warning_for_large_model_on_unsupported_pi(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _fake_size(_url: str) -> int:
        return 6 * 1024 * 1024 * 1024

    monkeypatch.setattr("core.main.fetch_remote_content_length_bytes", _fake_size)
    monkeypatch.setattr("core.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.5")
    monkeypatch.setattr("core.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("core.runtime_state.get_large_model_warn_threshold_bytes", lambda: 1)

    with TestClient(app) as client:
        response = client.post(
            "/internal/models/register",
            json={"source_url": "https://example.com/Qwen_Qwen3.5-35B-A3B-Q2_K_L.gguf"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["warnings"]
    assert body["warnings"][0]["code"] == "large_model_unsupported_pi_warning"


def test_upload_rejects_non_gguf(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        response = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "bad.txt"},
            content=b"not a model",
        )

    assert response.status_code == 400
    assert response.json()["reason"] == "unsupported_model_format"


def test_upload_returns_warning_for_large_model_on_unsupported_pi(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    monkeypatch.setattr("core.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.5")
    monkeypatch.setattr("core.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("core.runtime_state.get_large_model_warn_threshold_bytes", lambda: 1)

    with TestClient(app) as client:
        response = client.post(
            "/internal/models/upload",
            headers={
                "x-potato-filename": "Qwen_Qwen3.5-35B-A3B-Q2_K_L.gguf",
            },
            content=b"gguf",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["uploaded"] is True
    assert body["warnings"]
    assert body["warnings"][0]["code"] == "large_model_unsupported_pi_warning"


def test_upload_sets_uploaded_model_active(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        response = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "new-upload.gguf"},
            content=b"gguf",
        )
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["uploaded"] is True
    assert body["switched"] is True
    assert body["model"]["filename"] == "new-upload.gguf"

    status_body = status.json()
    assert status_body["model"]["filename"] == "new-upload.gguf"
    assert any(m["filename"] == "new-upload.gguf" and m["is_active"] for m in status_body["models"])


def test_delete_model_removes_file_and_registry(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        reg = client.post(
            "/internal/models/register",
            json={"source_url": "https://example.com/deletable-model.gguf"},
        )
        model = reg.json()["model"]
        model_id = model["id"]
        model_path = runtime.model_path.parent / model["filename"]
        model_path.write_bytes(b"gguf")

        delete = client.post("/internal/models/delete", json={"model_id": model_id})
        status = client.get("/status")

    assert reg.status_code == 200
    assert delete.status_code == 200
    body = delete.json()
    assert body["deleted"] is True
    assert body["model_id"] == model_id
    assert body["deleted_file"] is True
    assert not model_path.exists()
    assert all(m["id"] != model_id for m in status.json()["models"])


def test_delete_model_allows_default_model(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        runtime.model_path.write_bytes(b"default-model")
        response = client.post("/internal/models/delete", json={"model_id": "default"})
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["reason"] == "deleted"
    assert body["deleted_file"] is True
    assert not runtime.model_path.exists()
    # The default model registration is retained, but its file is removed.
    assert any(model["id"] == "default" for model in status.json()["models"])


def test_delete_model_allows_active_model(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        upload = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "active-upload.gguf"},
            content=b"gguf",
        )
        assert upload.status_code == 200
        active_model_id = upload.json()["model"]["id"]
        active_path = runtime.model_path
        assert active_path.name == "active-upload.gguf"
        assert active_path.exists()

        delete = client.post("/internal/models/delete", json={"model_id": active_model_id})
        status = client.get("/status")

    assert delete.status_code == 200
    body = delete.json()
    assert body["deleted"] is True
    assert body["reason"] == "deleted"
    assert body["deleted_file"] is True
    assert not active_path.exists()
    assert all(model["id"] != active_model_id for model in status.json()["models"])


def test_delete_model_removes_partial_download_file(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        reg = client.post(
            "/internal/models/register",
            json={"source_url": "https://example.com/partial-only.gguf"},
        )
        model = reg.json()["model"]
        model_id = model["id"]
        partial_path = runtime.model_path.parent / f"{model['filename']}.part"
        partial_path.write_bytes(b"partial-data")

        delete = client.post("/internal/models/delete", json={"model_id": model_id})

    assert reg.status_code == 200
    assert delete.status_code == 200
    body = delete.json()
    assert body["deleted"] is True
    assert body["deleted_file"] is True
    assert body["freed_bytes"] >= len(b"partial-data")
    assert not partial_path.exists()


def test_delete_model_cancels_active_download_for_same_model(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    task_sentinel = object()

    async def _fake_cancel(_app, _runtime, **_kwargs):
        state = ensure_models_state(runtime)
        state["current_download_model_id"] = None
        for item in state.get("models", []):
            if isinstance(item, dict) and item.get("status") == "downloading":
                item["status"] = "not_downloaded"
                item["error"] = None
        save_models_state(runtime, state)
        app.state.model_download_task = None
        return True, "cancelled"

    monkeypatch.setattr("core.main.is_download_task_active", lambda task: task is task_sentinel)
    monkeypatch.setattr("core.main._cancel_model_download_locked", _fake_cancel)

    with TestClient(app) as client:
        reg = client.post(
            "/internal/models/register",
            json={"source_url": "https://example.com/downloading.gguf"},
        )
        model = reg.json()["model"]
        model_id = model["id"]
        partial_path = runtime.model_path.parent / f"{model['filename']}.part"
        partial_path.write_bytes(b"partial")

        state = ensure_models_state(runtime)
        state["current_download_model_id"] = model_id
        for item in state["models"]:
            if isinstance(item, dict) and item.get("id") == model_id:
                item["status"] = "downloading"
                item["error"] = None
        save_models_state(runtime, state)
        app.state.model_download_task = task_sentinel

        response = client.post("/internal/models/delete", json={"model_id": model_id})
        status = client.get("/status")
        app.state.model_download_task = None

    assert reg.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["cancelled_download"] is True
    assert body["reason"] == "deleted"
    assert not partial_path.exists()
    assert all(model["id"] != model_id for model in status.json()["models"])


def test_delete_model_returns_conflict_when_cancel_active_download_times_out(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    task_sentinel = object()

    async def _fake_cancel(_app, _runtime, **_kwargs):
        return False, "cancel_timeout"

    monkeypatch.setattr("core.main.is_download_task_active", lambda task: task is task_sentinel)
    monkeypatch.setattr("core.main._cancel_model_download_locked", _fake_cancel)

    with TestClient(app) as client:
        reg = client.post(
            "/internal/models/register",
            json={"source_url": "https://example.com/downloading-timeout.gguf"},
        )
        model = reg.json()["model"]
        model_id = model["id"]
        partial_path = runtime.model_path.parent / f"{model['filename']}.part"
        partial_path.write_bytes(b"partial")

        state = ensure_models_state(runtime)
        state["current_download_model_id"] = model_id
        for item in state["models"]:
            if isinstance(item, dict) and item.get("id") == model_id:
                item["status"] = "downloading"
                item["error"] = None
        save_models_state(runtime, state)
        app.state.model_download_task = task_sentinel

        response = client.post("/internal/models/delete", json={"model_id": model_id})
        status = client.get("/status")
        app.state.model_download_task = None

    assert reg.status_code == 200
    assert response.status_code == 409
    body = response.json()
    assert body["deleted"] is False
    assert body["reason"] == "delete_cancel_timeout"
    assert partial_path.exists()
    assert any(model["id"] == model_id for model in status.json()["models"])



