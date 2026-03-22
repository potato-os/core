from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import _runtime_env, create_app, ensure_models_state, get_runtime, save_models_state


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


def test_status_auto_discovers_local_gguf_files_not_in_registry(runtime):
    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime

    (runtime.base_dir / "models" / "custom-local-a.gguf").write_bytes(b"gguf-a")
    (runtime.base_dir / "models" / "custom-local-b.gguf").write_bytes(b"gguf-b")

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    names = {item["filename"] for item in body["models"]}
    assert "custom-local-a.gguf" in names
    assert "custom-local-b.gguf" in names

    discovered = {
        item["filename"]: item
        for item in body["models"]
        if item["filename"] in {"custom-local-a.gguf", "custom-local-b.gguf"}
    }
    assert discovered["custom-local-a.gguf"]["source_type"] == "local_file"
    assert discovered["custom-local-a.gguf"]["status"] == "ready"
    assert discovered["custom-local-b.gguf"]["source_type"] == "local_file"
    assert discovered["custom-local-b.gguf"]["status"] == "ready"


def test_status_ignores_mmproj_files_from_local_model_discovery(runtime):
    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime

    (runtime.base_dir / "models" / "mmproj-test.gguf").write_bytes(b"mmproj")

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    names = {item["filename"] for item in body["models"]}
    assert "mmproj-test.gguf" not in names

def test_purge_models_clears_files_and_model_metadata(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        runtime.model_path.write_bytes(b"default-model")
        runtime.models_state_path.write_text(
            '{"version":1,"countdown_enabled":true,"default_model_downloaded_once":true,'
            '"active_model_id":"default","default_model_id":"default","current_download_model_id":null,'
            '"models":[{"id":"default","filename":"Qwen3.5-2B-Q4_K_M.gguf","source_url":"https://example.com/default.gguf","source_type":"url","status":"ready","error":null},'
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


def test_purge_models_sets_pi4_model_path_on_pi4(runtime, monkeypatch):
    """P1: After purge on Pi 4, runtime.model_path must point at the 0.8B default, not 2B."""
    from app.model_state import MODEL_FILENAME_PI4
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("app.model_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")

    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        runtime.model_path.write_bytes(b"old-model")
        response = client.post("/internal/models/purge", json={"reset_bootstrap_flag": True})

    assert response.status_code == 200
    assert runtime.model_path.name == MODEL_FILENAME_PI4


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

