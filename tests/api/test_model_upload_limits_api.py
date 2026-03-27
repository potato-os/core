from __future__ import annotations

from fastapi.testclient import TestClient

from core.main import create_app, ensure_models_state, get_runtime


def test_upload_rejects_body_over_detected_limit(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("core.main.get_model_upload_max_bytes", lambda _r: 3)

    with TestClient(app) as client:
        response = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "too-large.gguf"},
            content=b"gguf",
        )

    assert response.status_code == 413
    assert response.json()["reason"] == "upload_too_large"


def test_upload_rejection_includes_max_upload_bytes(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("core.main.get_model_upload_max_bytes", lambda _r: 3)

    with TestClient(app) as client:
        response = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "too-large.gguf"},
            content=b"gguf",
        )

    assert response.status_code == 413
    assert response.json()["max_upload_bytes"] == 3


def test_upload_allows_body_when_limit_disabled(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("core.main.get_model_upload_max_bytes", lambda _r: None)

    with TestClient(app) as client:
        response = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "large-ok.gguf"},
            content=b"gguf",
        )

    assert response.status_code == 200
    assert response.json()["uploaded"] is True


def test_upload_returns_500_when_models_state_corrupted(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("core.main.get_model_upload_max_bytes", lambda _r: None)

    original = ensure_models_state(runtime)

    def _corrupted_state(_runtime):
        state = original.copy()
        state["models"] = "not-a-list"
        return state

    monkeypatch.setattr("core.main.ensure_models_state", _corrupted_state)

    with TestClient(app) as client:
        response = client.post(
            "/internal/models/upload",
            headers={"x-potato-filename": "corrupt-test.gguf"},
            content=b"gguf",
        )

        assert response.status_code == 500
        assert response.json()["reason"] == "models_state_invalid"
        assert app.state.model_upload_state["error"] == "models_state_invalid"
        assert app.state.model_upload_state["active"] is False
