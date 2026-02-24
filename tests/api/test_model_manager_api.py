from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.main import create_app, get_runtime


async def _healthy_true(_runtime):
    return True


def test_status_includes_models_payload(runtime, monkeypatch):
    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert "models" in body
    assert isinstance(body["models"], list)
    assert "countdown_enabled" in body["download"]


def test_toggle_download_countdown_endpoint(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        off = client.post("/internal/download-countdown", json={"enabled": False})
        on = client.post("/internal/download-countdown", json={"enabled": True})

    assert off.status_code == 200
    assert off.json()["countdown_enabled"] is False
    assert on.status_code == 200
    assert on.json()["countdown_enabled"] is True


def test_register_model_url_rejects_invalid(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        response = client.post("/internal/models/register", json={"source_url": "http://example.com/model.bin"})

    assert response.status_code == 400
    assert response.json()["reason"] in {"https_required", "gguf_required"}


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
    assert response.json()["reason"] == "gguf_required"


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


def test_purge_models_clears_files_and_model_metadata(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        runtime.model_path.write_bytes(b"default-model")
        runtime.models_state_path.write_text(
            '{"version":1,"countdown_enabled":true,"default_model_downloaded_once":true,'
            '"active_model_id":"default","default_model_id":"default","current_download_model_id":null,'
            '"models":[{"id":"default","filename":"Qwen3-VL-4B-Instruct-Q4_K_M.gguf","source_url":"https://example.com/default.gguf","source_type":"url","status":"ready","error":null},'
            '{"id":"custom","filename":"custom.gguf","source_url":"https://example.com/custom.gguf","source_type":"url","status":"failed","error":"download_failed"}]}',
            encoding="utf-8",
        )
        (runtime.model_path.parent / "custom.gguf").write_bytes(b"custom")
        runtime.download_state_path.write_text(
            '{"bytes_total":1000,"bytes_downloaded":500,"percent":50,"speed_bps":0,"eta_seconds":0,"error":"download_failed"}',
            encoding="utf-8",
        )

        response = client.post("/internal/models/purge", json={"reset_bootstrap_flag": True})
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["purged"] is True
    assert body["deleted_files"] >= 2
    assert body["freed_bytes"] >= len(b"default-model") + len(b"custom")

    status_body = status.json()
    assert status_body["model"]["active_model_id"] == "default"
    assert len(status_body["models"]) == 1
    assert status_body["models"][0]["id"] == "default"
    assert status_body["models"][0]["status"] == "not_downloaded"
    assert status_body["download"]["error"] is None
    assert status_body["download"]["bytes_total"] == 0
    assert status_body["download"]["bytes_downloaded"] == 0


def test_upload_write_failure_clears_active_state_and_allows_retry(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    original_open = type(runtime.model_path).open
    fail_once = {"enabled": True}

    class _WriteFailingHandle:
        def __init__(self, wrapped) -> None:
            self._wrapped = wrapped
            self._failed = False

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self._wrapped.__exit__(exc_type, exc, tb)

        def write(self, _chunk):
            if not self._failed:
                self._failed = True
                raise OSError("No space left on device")
            return self._wrapped.write(_chunk)

        def __getattr__(self, name: str):
            return getattr(self._wrapped, name)

    def _patched_open(path_obj, *args, **kwargs):
        handle = original_open(path_obj, *args, **kwargs)
        mode = str(args[0] if args else kwargs.get("mode", "r"))
        if fail_once["enabled"] and path_obj.name.endswith(".gguf.part") and "w" in mode:
            fail_once["enabled"] = False
            return _WriteFailingHandle(handle)
        return handle

    monkeypatch.setattr(type(runtime.model_path), "open", _patched_open)

    with TestClient(app) as client:
        failed = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "broken-upload.gguf"},
            content=b"gguf",
        )
        retried = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "retry-upload.gguf"},
            content=b"gguf",
        )

    assert failed.status_code == 500
    assert failed.json()["reason"] == "upload_write_failed"
    assert app.state.model_upload_state["active"] is False
    assert not (runtime.model_path.parent / "broken-upload.gguf.part").exists()
    assert retried.status_code == 200
    assert retried.json()["uploaded"] is True


def test_purge_models_returns_timeout_when_upload_cancel_does_not_finish(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    class _StuckUploadLock:
        def __init__(self) -> None:
            self._is_locked = True

        def locked(self) -> bool:
            return self._is_locked

        async def acquire(self) -> bool:
            await asyncio.sleep(60)
            self._is_locked = True
            return True

        def release(self) -> None:
            self._is_locked = False

    async def _restart_should_not_run(_app):
        raise AssertionError("purge should not restart llama when upload cancel times out")

    monkeypatch.setattr("app.main.MODEL_UPLOAD_PURGE_WAIT_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr("app.main.restart_managed_llama_process", _restart_should_not_run)

    with TestClient(app) as client:
        app.state.model_upload_state.update({"active": True})
        app.state.model_upload_lock = _StuckUploadLock()
        response = client.post("/internal/models/purge", json={"reset_bootstrap_flag": True})

    assert response.status_code == 200
    body = response.json()
    assert body["purged"] is False
    assert body["reason"] == "upload_cancel_timeout"
    assert body["cancelled_upload"] is True
    assert app.state.model_upload_cancel_requested is True
