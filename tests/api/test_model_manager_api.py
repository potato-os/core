from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import create_app, ensure_models_state, get_runtime, save_models_state


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


def test_register_model_url_returns_warning_for_large_model_on_unsupported_pi(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _fake_size(_url: str) -> int:
        return 6 * 1024 * 1024 * 1024

    monkeypatch.setattr("app.main.fetch_remote_content_length_bytes", _fake_size)
    monkeypatch.setattr("app.main._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.5")
    monkeypatch.setattr("app.main._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("app.main.get_large_model_warn_threshold_bytes", lambda: 1)

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
    assert response.json()["reason"] == "gguf_required"


def test_upload_returns_warning_for_large_model_on_unsupported_pi(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    monkeypatch.setattr("app.main._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.5")
    monkeypatch.setattr("app.main._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("app.main.get_large_model_warn_threshold_bytes", lambda: 1)

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


def test_switch_llama_runtime_bundle_copies_selected_bundle_and_reports_status(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    bundle = runtime.base_dir / "bundle-root" / "llama_server_bundle_test_pi5-opt"
    (bundle / "bin").mkdir(parents=True)
    (bundle / "lib").mkdir(parents=True)
    (bundle / "bin" / "llama-server").write_text("binary", encoding="utf-8")
    (bundle / "README.txt").write_text("Profile: pi5-opt\n", encoding="utf-8")

    install_calls: list[str] = []

    async def _fake_install(_runtime, bundle_dir):
        install_calls.append(str(bundle_dir))
        install_dir = _runtime.base_dir / "llama"
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / "bin").mkdir(exist_ok=True)
        (install_dir / "lib").mkdir(exist_ok=True)
        (install_dir / "bin" / "llama-server").write_text("installed", encoding="utf-8")
        return {"ok": True, "install_dir": str(install_dir)}

    async def _fake_restart(_app):
        return False, "no_running_process"

    monkeypatch.setattr("app.main.install_llama_runtime_bundle", _fake_install)
    monkeypatch.setattr("app.main.restart_managed_llama_process", _fake_restart)
    monkeypatch.setattr("app.main._default_llama_runtime_bundle_roots", lambda _runtime: [bundle.parent])

    with TestClient(app) as client:
        response = client.post("/internal/llama-runtime/switch", json={"bundle_path": str(bundle)})
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["switched"] is True
    assert body["bundle"]["path"] == str(bundle)
    assert install_calls == [str(bundle)]

    status_body = status.json()
    assert "llama_runtime" in status_body
    assert status_body["llama_runtime"]["current"]["source_bundle_path"] == str(bundle)
    marker_path = runtime.base_dir / "llama" / ".potato-llama-runtime-bundle.json"
    assert marker_path.exists()
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["source_bundle_path"] == str(bundle)


def test_set_llama_memory_loading_mode_persists_and_restarts(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _fake_restart(_app):
        return True, "terminated_running_process"

    monkeypatch.setattr("app.main.restart_managed_llama_process", _fake_restart)

    with TestClient(app) as client:
        response = client.post(
            "/internal/llama-runtime/memory-loading",
            json={"mode": "full_ram"},
        )
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["updated"] is True
    assert body["memory_loading"]["mode"] == "full_ram"
    assert body["memory_loading"]["no_mmap_env"] == "1"
    assert body["restarted"] is True

    status_body = status.json()
    assert status_body["llama_runtime"]["memory_loading"]["mode"] == "full_ram"
    assert status_body["llama_runtime"]["memory_loading"]["no_mmap_env"] == "1"


def test_set_large_model_override_persists_without_restart(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.5")
    monkeypatch.setattr("app.main._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("app.main.get_large_model_warn_threshold_bytes", lambda: 1)
    with runtime.model_path.open("wb") as handle:
        handle.seek((6 * 1024 * 1024 * 1024) - 1)
        handle.write(b"x")

    with TestClient(app) as client:
        before = client.get("/status")
        response = client.post(
            "/internal/compatibility/large-model-override",
            json={"enabled": True},
        )
        after = client.get("/status")

    assert before.status_code == 200
    assert before.json()["compatibility"]["warnings"]
    assert response.status_code == 200
    body = response.json()
    assert body["updated"] is True
    assert body["override"]["enabled"] is True
    assert body["compatibility"]["override_enabled"] is True
    assert body["compatibility"]["warnings"] == []
    assert after.status_code == 200
    after_body = after.json()
    assert after_body["compatibility"]["override_enabled"] is True
    assert after_body["compatibility"]["warnings"] == []
    assert after_body["llama_runtime"]["large_model_override"]["enabled"] is True


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

    monkeypatch.setattr("app.main.is_download_task_active", lambda task: task is task_sentinel)
    monkeypatch.setattr("app.main._cancel_model_download_locked", _fake_cancel)

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

    monkeypatch.setattr("app.main.is_download_task_active", lambda task: task is task_sentinel)
    monkeypatch.setattr("app.main._cancel_model_download_locked", _fake_cancel)

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
