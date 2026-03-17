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




