from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from core.main import _runtime_env, create_app, ensure_models_state, get_runtime, save_models_state


async def _healthy_true(_runtime):
    return True


def test_update_model_settings_persists_per_model_chat_and_vision(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        upload = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "Qwen3.5-4B-Q4_K_M.gguf"},
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


def test_settings_document_yaml_round_trip_updates_active_model_and_model_settings(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        upload = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "Qwen3.5-4B-Q4_K_M.gguf"},
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
            headers={"x-potato-filename": "Qwen3.5-4B-Q4_K_M.gguf"},
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



