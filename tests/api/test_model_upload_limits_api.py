from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app, get_runtime


def test_upload_rejects_body_over_detected_limit(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main.get_model_upload_max_bytes", lambda: 3)

    with TestClient(app) as client:
        response = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "too-large.gguf"},
            content=b"gguf",
        )

    assert response.status_code == 413
    assert response.json()["reason"] == "upload_too_large"


def test_upload_allows_body_when_limit_disabled(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main.get_model_upload_max_bytes", lambda: None)

    with TestClient(app) as client:
        response = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "large-ok.gguf"},
            content=b"gguf",
        )

    assert response.status_code == 200
    assert response.json()["uploaded"] is True
