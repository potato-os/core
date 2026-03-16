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

    (runtime.base_dir / "models" / "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf").write_bytes(b"mmproj")

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    names = {item["filename"] for item in body["models"]}
    assert "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf" not in names


def test_toggle_download_countdown_endpoint(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        off = client.post("/internal/download-countdown", json={"enabled": False})
        on = client.post("/internal/download-countdown", json={"enabled": True})

    assert off.status_code == 200
    assert off.json()["countdown_enabled"] is False
    assert off.json()["updated"] is True
    assert off.json()["reason"] == "countdown_updated"
    assert on.status_code == 200
    assert on.json()["countdown_enabled"] is True
    assert on.json()["updated"] is True
    assert on.json()["reason"] == "countdown_updated"


def test_update_model_settings_persists_per_model_chat_and_vision(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        upload = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"},
            content=b"gguf",
        )
        assert upload.status_code == 200
        model_id = upload.json()["model"]["id"]

        response = client.post(
            "/internal/models/settings",
            json={
                "model_id": model_id,
                "settings": {
                    "chat": {
                        "temperature": 0.2,
                        "top_p": 0.9,
                        "top_k": 32,
                        "repetition_penalty": 1.1,
                        "presence_penalty": 0.4,
                        "max_tokens": 2048,
                        "stream": False,
                        "generation_mode": "deterministic",
                        "seed": 123,
                        "system_prompt": "Speak plainly.",
                    },
                    "vision": {
                        "enabled": True,
                        "projector_mode": "default",
                        "projector_filename": "mmproj-F16.gguf",
                    },
                },
            },
        )
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["updated"] is True
    assert body["model"]["settings"]["chat"]["temperature"] == 0.2
    assert body["model"]["settings"]["chat"]["generation_mode"] == "deterministic"
    assert body["model"]["settings"]["vision"]["enabled"] is True
    assert body["model"]["settings"]["vision"]["projector_filename"] == "mmproj-F16.gguf"

    status_model = next(item for item in status.json()["models"] if item["id"] == model_id)
    assert status_model["settings"]["chat"]["system_prompt"] == "Speak plainly."
    assert status_model["settings"]["vision"]["enabled"] is True


def test_status_prefers_model_specific_qwen35_projector_over_stale_generic_default(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    model_filename = "Qwen_Qwen3.5-2B-IQ4_NL.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    (runtime.base_dir / "models" / "mmproj-F16.gguf").write_bytes(b"generic")
    (runtime.base_dir / "models" / "mmproj-Qwen_Qwen3.5-2B-f16.gguf").write_bytes(b"specific")
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "vision-model",
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
                        "id": "vision-model",
                        "filename": model_filename,
                        "source_url": "https://example.com/qwen35.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                        "settings": {
                            "vision": {
                                "enabled": True,
                                "projector_mode": "default",
                                "projector_filename": "mmproj-F16.gguf",
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["model"]["projector"]["filename"] == "mmproj-Qwen_Qwen3.5-2B-f16.gguf"
    assert body["model"]["projector"]["present"] is True
    assert body["model"]["projector"]["default_candidates"][0] == "mmproj-Qwen_Qwen3.5-2B-IQ4_NL-f16.gguf"
    assert "mmproj-Qwen_Qwen3.5-2B-f16.gguf" in body["model"]["projector"]["default_candidates"]


def test_runtime_env_uses_resolved_qwen35_default_projector(runtime):
    model_filename = "Qwen_Qwen3.5-2B-IQ4_NL.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    generic_mmproj = runtime.base_dir / "models" / "mmproj-F16.gguf"
    specific_mmproj = runtime.base_dir / "models" / "mmproj-Qwen_Qwen3.5-2B-f16.gguf"
    generic_mmproj.write_bytes(b"generic")
    specific_mmproj.write_bytes(b"specific")
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "vision-model",
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
                        "id": "vision-model",
                        "filename": model_filename,
                        "source_url": "https://example.com/qwen35.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                        "settings": {
                            "vision": {
                                "enabled": True,
                                "projector_mode": "default",
                                "projector_filename": "mmproj-F16.gguf",
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    assert env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] == "1"
    assert env["POTATO_MMPROJ_PATH"] == str(specific_mmproj)


def test_runtime_env_disables_vl_projector_heuristic_when_vision_is_off(runtime):
    model_filename = "Qwen3.5-2B-Q4_K_M.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    runtime.model_path = model_path
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
                        "filename": model_filename,
                        "source_url": "https://example.com/qwen3-vl.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                        "settings": {
                            "vision": {
                                "enabled": False,
                                "projector_mode": "default",
                                "projector_filename": "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf",
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    assert env["POTATO_VISION_MODEL_NAME_PATTERN_VL"] == "0"
    assert env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] == "0"
    assert "POTATO_MMPROJ_PATH" not in env


def test_runtime_env_enables_vl_projector_heuristic_when_vision_is_on(runtime):
    model_filename = "Qwen3.5-2B-Q4_K_M.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    mmproj_path = runtime.base_dir / "models" / "mmproj-F16.gguf"
    model_path.write_bytes(b"gguf")
    mmproj_path.write_bytes(b"mmproj")
    runtime.model_path = model_path
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
                        "filename": model_filename,
                        "source_url": "https://example.com/qwen3-vl.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                        "settings": {
                            "vision": {
                                "enabled": True,
                                "projector_mode": "default",
                                "projector_filename": None,
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    assert env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] == "1"
    assert env["POTATO_MMPROJ_PATH"] == str(mmproj_path)


def test_settings_document_yaml_round_trip_updates_active_model_and_model_settings(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        upload = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"},
            content=b"gguf",
        )
        assert upload.status_code == 200
        model_id = upload.json()["model"]["id"]

        exported = client.get("/internal/settings-document")
        assert exported.status_code == 200
        exported_body = exported.json()
        assert exported_body["format"] == "yaml"
        assert "active_model_id:" in exported_body["document"]
        assert "stream: true" in exported_body["document"]

        document = f"""
version: 1
active_model_id: {model_id}
runtime:
  memory_loading_mode: auto
  allow_unsupported_large_models: false
models:
  - id: default
    settings:
      chat:
        temperature: 0.7
        top_p: 0.8
        top_k: 20
        repetition_penalty: 1.0
        presence_penalty: 1.5
        max_tokens: 16384
        stream: true
        generation_mode: random
        seed: 42
        system_prompt: ""
      vision:
        enabled: true
        projector_mode: default
        projector_filename:
  - id: {model_id}
    settings:
      chat:
        temperature: 0.15
        top_p: 0.95
        top_k: 40
        repetition_penalty: 1.0
        presence_penalty: 0.0
        max_tokens: 1024
        stream: false
        generation_mode: deterministic
        seed: 9
        system_prompt: Keep it short.
      vision:
        enabled: true
        projector_mode: default
        projector_filename: mmproj-F16.gguf
""".strip()

        applied = client.post("/internal/settings-document", json={"document": document})
        status = client.get("/status")

    assert applied.status_code == 200
    applied_body = applied.json()
    assert applied_body["updated"] is True
    assert applied_body["active_model_id"] == model_id

    status_body = status.json()
    assert status_body["model"]["active_model_id"] == model_id
    updated_model = next(item for item in status_body["models"] if item["id"] == model_id)
    assert updated_model["settings"]["chat"]["temperature"] == 0.15
    assert updated_model["settings"]["chat"]["system_prompt"] == "Keep it short."
    assert updated_model["settings"]["chat"]["stream"] is False
    assert updated_model["settings"]["vision"]["projector_filename"] == "mmproj-F16.gguf"


def test_update_model_settings_rejects_invalid_numeric_value(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        upload = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"},
            content=b"gguf",
        )
        assert upload.status_code == 200
        model_id = upload.json()["model"]["id"]

        response = client.post(
            "/internal/models/settings",
            json={
                "model_id": model_id,
                "settings": {
                    "chat": {
                        "temperature": "",
                    }
                },
            },
        )
        status = client.get("/status")

    assert response.status_code == 400
    assert response.json()["updated"] is False
    assert response.json()["reason"] == "invalid_settings"
    saved_model = next(item for item in status.json()["models"] if item["id"] == model_id)
    assert saved_model["settings"]["chat"]["temperature"] == 0.7


def test_settings_document_rejects_invalid_numeric_value(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        exported = client.get("/internal/settings-document")
        assert exported.status_code == 200

        response = client.post(
            "/internal/settings-document",
            json={
                "document": """
version: 1
active_model_id: default
models:
  - id: default
    settings:
      chat:
        temperature: ""
""".strip()
            },
        )
        status = client.get("/status")

    assert response.status_code == 400
    assert response.json()["updated"] is False
    assert response.json()["reason"] == "invalid_settings"
    default_model = next(item for item in status.json()["models"] if item["id"] == "default")
    assert default_model["settings"]["chat"]["temperature"] == 0.7


def test_download_default_projector_for_model_uses_curated_helper(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    calls: list[str] = []

    def _fake_download(*, runtime, model_id: str):
        calls.append(model_id)
        return True, "downloaded", "mmproj-F16.gguf"

    monkeypatch.setattr("app.main.download_default_projector_for_model", _fake_download)

    with TestClient(app) as client:
        upload = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"},
            content=b"gguf",
        )
        assert upload.status_code == 200
        model_id = upload.json()["model"]["id"]

        response = client.post("/internal/models/download-projector", json={"model_id": model_id})

    assert response.status_code == 200
    body = response.json()
    assert body["downloaded"] is True
    assert body["reason"] == "downloaded"
    assert body["projector_filename"] == "mmproj-F16.gguf"
    assert calls == [model_id]


def test_download_default_projector_for_builtin_qwen3_vl_model(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    requested_urls: list[str] = []

    class _FakeStreamResponse:
        def __init__(self, url: str):
            self._url = url

        def __enter__(self):
            requested_urls.append(self._url)
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"mmproj"

    class _FakeHttpClient:
        def __init__(self, *args, **kwargs):
            return None

        def stream(self, method: str, url: str):
            assert method == "GET"
            return _FakeStreamResponse(url)

        def close(self):
            return None

    async def _fake_restart(_app):
        return False, "not_required"

    monkeypatch.setattr("app.main.httpx.Client", _FakeHttpClient)
    monkeypatch.setattr("app.main.restart_managed_llama_process", _fake_restart)

    with TestClient(app) as client:
        response = client.post("/internal/models/download-projector", json={"model_id": "default"})
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["downloaded"] is True
    assert body["projector_filename"] == "mmproj-Qwen3.5-2B-Q4_K_M-f16.gguf"
    assert requested_urls == [
        "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/mmproj-Qwen3.5-2B-Q4_K_M-f16.gguf"
    ]
    assert (runtime.base_dir / "models" / "mmproj-Qwen3.5-2B-Q4_K_M-f16.gguf").exists()
    default_model = next(item for item in status.json()["models"] if item["id"] == "default")
    assert default_model["settings"]["vision"]["projector_filename"] == "mmproj-Qwen3.5-2B-Q4_K_M-f16.gguf"


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
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.5")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("app.runtime_state.get_large_model_warn_threshold_bytes", lambda: 1)

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

    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.5")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("app.runtime_state.get_large_model_warn_threshold_bytes", lambda: 1)

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


def test_switch_llama_runtime_by_family_installs_slot_and_reports_status(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    # Create a runtime slot at runtimes/ik_llama/
    slot = runtime.base_dir / "runtimes" / "ik_llama"
    (slot / "bin").mkdir(parents=True)
    (slot / "lib").mkdir(parents=True)
    (slot / "bin" / "llama-server").write_text("binary", encoding="utf-8")
    (slot / "runtime.json").write_text(
        json.dumps({"family": "ik_llama", "commit": "abc123", "profile": "pi5-opt"}),
        encoding="utf-8",
    )

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

    with TestClient(app) as client:
        response = client.post("/internal/llama-runtime/switch", json={"family": "ik_llama"})
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["switched"] is True
    assert body["family"] == "ik_llama"
    assert install_calls == [str(slot)]

    status_body = status.json()
    assert "llama_runtime" in status_body
    marker_path = runtime.base_dir / "llama" / ".potato-llama-runtime-bundle.json"
    assert marker_path.exists()
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["family"] == "ik_llama"


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
    monkeypatch.setattr("app.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.5")
    monkeypatch.setattr("app.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("app.runtime_state.get_large_model_warn_threshold_bytes", lambda: 1)
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


def test_power_calibration_sample_fit_and_reset_persist_in_status(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    power_state = {"value": 4.0}

    def _fake_power_snapshot(*, now_unix=None):
        power_state["value"] += 1.5
        watts = power_state["value"]
        return {
            "available": True,
            "updated_at_unix": now_unix,
            "total_watts": watts,
            "rails_paired_count": 2,
            "method": "pmic_read_adc",
            "label": "PMIC rails estimate",
            "disclaimer": "test",
            "error": None,
        }

    monkeypatch.setattr("app.main._build_power_estimate_snapshot", _fake_power_snapshot)

    with TestClient(app) as client:
        s1 = client.post("/internal/power-calibration/sample", json={"wall_watts": 6.5})
        s2 = client.post("/internal/power-calibration/sample", json={"wall_watts": 10.1})
        fit = client.post("/internal/power-calibration/fit")
        status = client.get("/status")
        reset = client.post("/internal/power-calibration/reset")
        status_after_reset = client.get("/status")

    assert s1.status_code == 200
    assert s1.json()["captured"] is True
    assert s2.status_code == 200
    assert s2.json()["captured"] is True
    assert fit.status_code == 200
    assert fit.json()["updated"] is True
    assert fit.json()["calibration"]["mode"] == "custom"

    power = status.json()["system"]["power_estimate"]
    assert "raw_total_watts" in power
    assert "adjusted_total_watts" in power
    assert power["calibration"]["mode"] == "custom"
    assert power["calibration"]["sample_count"] >= 2

    assert reset.status_code == 200
    assert reset.json()["updated"] is True
    assert reset.json()["calibration"]["mode"] == "default"
    assert status_after_reset.json()["system"]["power_estimate"]["calibration"]["mode"] == "default"


def test_power_calibration_fit_requires_two_samples(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        response = client.post("/internal/power-calibration/fit")

    assert response.status_code == 400
    assert response.json()["reason"] == "insufficient_samples"


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


def test_purge_models_removes_ssd_offloaded_targets(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    ssd_dir = runtime.base_dir / "media" / "ssd" / "potato-models"
    ssd_dir.mkdir(parents=True)
    ssd_target = ssd_dir / runtime.model_path.name

    with TestClient(app) as client:
        ssd_target.write_bytes(b"default-model")
        if runtime.model_path.exists():
            runtime.model_path.unlink()
        runtime.model_path.symlink_to(ssd_target)

        response = client.post("/internal/models/purge", json={"reset_bootstrap_flag": True})

    assert response.status_code == 200
    body = response.json()
    assert body["purged"] is True
    assert body["deleted_files"] >= 2
    assert body["freed_bytes"] >= len(b"default-model")
    assert not runtime.model_path.exists()
    assert not ssd_target.exists()


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
