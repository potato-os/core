"""API tests for OTA update endpoints and /status integration."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.__version__ import __version__
from app.main import create_app, get_runtime
from app.update_state import write_execution_state


def test_update_check_returns_409_when_orchestrator_disabled(client):
    response = client.post("/internal/update/check")
    assert response.status_code == 409
    body = response.json()
    assert body["checked"] is False
    assert body["reason"] == "orchestrator_disabled"


def test_update_check_returns_200_on_success(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _mock_check(_rt):
        from app.runtime_state import _atomic_write_json

        state = {
            "available": True,
            "current_version": __version__,
            "latest_version": "0.5.0",
            "release_notes": "notes",
            "release_url": "https://github.com/slomin/potato-os/releases/tag/v0.5.0",
            "tarball_url": None,
            "checked_at_unix": 1711000000,
            "error": None,
        }
        _atomic_write_json(_rt.update_state_path, state)
        return state

    monkeypatch.setattr("app.routes.update.check_for_update", _mock_check)

    with TestClient(app) as c:
        response = c.post("/internal/update/check")

    assert response.status_code == 200
    body = response.json()
    assert body["checked"] is True
    assert body["available"] is True
    assert body["latest_version"] == "0.5.0"


def test_status_includes_update_key_with_default_shape(client, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()

    assert "update" in body
    update = body["update"]
    assert update["available"] is False
    assert update["current_version"] == __version__
    assert update["latest_version"] is None
    assert update["state"] == "idle"
    assert update["deferred"] is False
    assert update["defer_reason"] is None
    assert update["progress"] == {"phase": None, "percent": 0, "error": None}


def test_status_update_populated_after_check(runtime, monkeypatch):
    state = {
        "available": True,
        "current_version": __version__,
        "latest_version": "0.5.0",
        "release_notes": "notes",
        "release_url": "https://github.com/slomin/potato-os/releases/tag/v0.5.0",
        "tarball_url": None,
        "checked_at_unix": 1711000000,
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")

    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)

    with TestClient(app) as c:
        response = c.get("/status")

    assert response.status_code == 200
    update = response.json()["update"]
    assert update["available"] is True
    assert update["latest_version"] == "0.5.0"
    assert update["checked_at_unix"] == 1711000000


def test_status_update_deferred_when_download_active(runtime, monkeypatch):
    runtime.download_state_path.write_text(
        json.dumps({"bytes_total": 1000, "bytes_downloaded": 500, "percent": 50}),
        encoding="utf-8",
    )

    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)

    with TestClient(app) as c:
        response = c.get("/status")

    assert response.status_code == 200
    update = response.json()["update"]
    assert update["deferred"] is True
    assert update["defer_reason"] == "download_active"


# ---------------------------------------------------------------------------
# POST /internal/update/start
# ---------------------------------------------------------------------------


def test_update_start_returns_409_when_orchestrator_disabled(client):
    response = client.post("/internal/update/start")
    assert response.status_code == 409
    body = response.json()
    assert body["started"] is False
    assert body["reason"] == "orchestrator_disabled"


def test_update_start_returns_409_when_no_update_available(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as c:
        response = c.post("/internal/update/start")

    assert response.status_code == 409
    assert response.json()["reason"] == "no_update_available"


def _patch_version(monkeypatch, version: str = "0.4.0") -> None:
    monkeypatch.setattr("app.update_state.__version__", version)
    monkeypatch.setattr("app.routes.update.__version__", version)


def test_update_start_returns_409_when_no_tarball_url(runtime, monkeypatch):
    state = {
        "available": True,
        "current_version": "0.4.0",
        "latest_version": "0.5.0",
        "tarball_url": None,
        "checked_at_unix": 1711000000,
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    _patch_version(monkeypatch)

    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as c:
        response = c.post("/internal/update/start")

    assert response.status_code == 409
    assert response.json()["reason"] == "no_tarball_url"


def test_update_start_returns_409_when_download_active(runtime, monkeypatch):
    state = {
        "available": True,
        "current_version": "0.4.0",
        "latest_version": "0.5.0",
        "tarball_url": "https://example.com/tarball.tar.gz",
        "checked_at_unix": 1711000000,
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    runtime.download_state_path.write_text(
        json.dumps({"bytes_total": 1000, "bytes_downloaded": 500, "percent": 50}),
        encoding="utf-8",
    )
    _patch_version(monkeypatch)

    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as c:
        response = c.post("/internal/update/start")

    assert response.status_code == 409
    assert response.json()["reason"] == "download_active"


def test_update_start_returns_409_when_update_already_running(runtime, monkeypatch):
    state = {
        "available": True,
        "current_version": "0.4.0",
        "latest_version": "0.5.0",
        "tarball_url": "https://example.com/tarball.tar.gz",
        "checked_at_unix": 1711000000,
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    write_execution_state(runtime, execution_state="downloading", target_version="0.5.0")
    _patch_version(monkeypatch)

    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as c:
        response = c.post("/internal/update/start")

    assert response.status_code == 409
    assert response.json()["reason"] == "update_in_progress"


def test_update_start_rejects_stale_available_after_upgrade(runtime, monkeypatch):
    """P3: stale available=true in state should not allow redundant update."""
    state = {
        "available": True,
        "current_version": "0.4.0",
        "latest_version": "0.5.0",
        "tarball_url": "https://example.com/tarball.tar.gz",
        "checked_at_unix": 1711000000,
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    _patch_version(monkeypatch, "0.5.0")

    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as c:
        response = c.post("/internal/update/start")

    assert response.status_code == 409
    assert response.json()["reason"] == "no_update_available"


def test_update_start_returns_200_and_starts(runtime, monkeypatch):
    state = {
        "available": True,
        "current_version": "0.4.0",
        "latest_version": "0.5.0",
        "tarball_url": "https://example.com/tarball.tar.gz",
        "checked_at_unix": 1711000000,
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    _patch_version(monkeypatch)

    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _mock_run_update(_app, _runtime, _url, _version):
        pass

    monkeypatch.setattr("app.routes.update.run_update", _mock_run_update)

    with TestClient(app) as c:
        response = c.post("/internal/update/start")

    assert response.status_code == 200
    body = response.json()
    assert body["started"] is True


def test_status_update_reflects_downloading(runtime, monkeypatch):
    write_execution_state(
        runtime,
        execution_state="downloading",
        phase="downloading",
        percent=42,
        target_version="0.5.0",
    )

    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)

    with TestClient(app) as c:
        response = c.get("/status")

    assert response.status_code == 200
    update = response.json()["update"]
    assert update["state"] == "downloading"
    assert update["progress"]["phase"] == "downloading"
    assert update["progress"]["percent"] == 42


def test_status_update_reflects_failed(runtime, monkeypatch):
    write_execution_state(
        runtime,
        execution_state="failed",
        error="network_timeout",
        target_version="0.5.0",
    )

    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)

    with TestClient(app) as c:
        response = c.get("/status")

    assert response.status_code == 200
    update = response.json()["update"]
    assert update["state"] == "failed"
    assert update["progress"]["error"] == "network_timeout"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _healthy_false(_runtime, busy_is_healthy=True):
    return False
