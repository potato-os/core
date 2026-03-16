from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import _runtime_env, create_app, ensure_models_state, get_runtime, save_models_state


async def _healthy_true(_runtime):
    return True


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



