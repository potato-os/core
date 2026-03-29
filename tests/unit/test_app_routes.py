"""Tests for the app-provided routes framework extension."""

from pathlib import Path

import pytest

from tests.unit.conftest import REPO_ROOT


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def test_manifest_with_routes_field_parses():
    from core.app_manifest import AppManifest

    manifest = AppManifest(
        id="test", name="Test", entry="main.py", routes="routes.py",
    )
    assert manifest.routes == "routes.py"


def test_manifest_without_routes_defaults_to_empty():
    from core.app_manifest import AppManifest

    manifest = AppManifest(id="test", name="Test", entry="main.py")
    assert manifest.routes == ""


def test_manifest_from_file_parses_routes(tmp_path):
    import json
    from core.app_manifest import AppManifest

    manifest_path = tmp_path / "app.json"
    manifest_path.write_text(json.dumps({
        "id": "myapp",
        "name": "My App",
        "entry": "main.py",
        "routes": "routes.py",
    }))
    manifest = AppManifest.from_file(manifest_path)
    assert manifest.routes == "routes.py"


def test_manifest_from_file_without_routes_field(tmp_path):
    import json
    from core.app_manifest import AppManifest

    manifest_path = tmp_path / "app.json"
    manifest_path.write_text(json.dumps({
        "id": "myapp",
        "name": "My App",
        "entry": "main.py",
    }))
    manifest = AppManifest.from_file(manifest_path)
    assert manifest.routes == ""


# ---------------------------------------------------------------------------
# Existing apps unaffected
# ---------------------------------------------------------------------------


def test_skeleton_manifest_still_valid():
    """Existing skeleton app must still parse and validate with the new field."""
    from core.app_manifest import AppManifest

    manifest_path = REPO_ROOT / "apps" / "skeleton" / "app.json"
    manifest = AppManifest.from_file(manifest_path)
    errors = manifest.validate()
    assert errors == []
    assert manifest.routes == ""


def test_chat_manifest_still_valid():
    """Existing chat app must still parse and validate with the new field."""
    from core.app_manifest import AppManifest

    manifest_path = REPO_ROOT / "apps" / "chat" / "app.json"
    manifest = AppManifest.from_file(manifest_path)
    errors = manifest.validate()
    assert errors == []


# ---------------------------------------------------------------------------
# Router loading
# ---------------------------------------------------------------------------


def test_app_router_mounted_at_correct_prefix(tmp_path):
    """An app with routes.py gets its router mounted at /app/{id}/api/."""
    import json
    from core.app_manifest import AppManifest

    # Create a minimal app with a routes module
    app_dir = tmp_path / "apps" / "testapp"
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(json.dumps({
        "id": "testapp",
        "name": "Test App",
        "entry": "main.py",
        "routes": "routes.py",
    }))
    (app_dir / "routes.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/ping')\n"
        "async def ping():\n"
        "    return {'pong': True}\n"
    )

    from core.app_manifest import discover_apps
    from core.app_routes import load_app_router

    manifests = discover_apps(tmp_path / "apps")
    assert len(manifests) == 1

    router, prefix = load_app_router(manifests[0], app_dir)
    assert router is not None
    assert prefix == "/app/testapp/api"


def test_load_app_router_returns_none_when_no_routes(tmp_path):
    """An app without routes field returns None."""
    import json
    from core.app_manifest import AppManifest

    app_dir = tmp_path / "apps" / "noroutes"
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(json.dumps({
        "id": "noroutes",
        "name": "No Routes",
        "entry": "main.py",
    }))

    from core.app_routes import load_app_router
    from core.app_manifest import AppManifest

    manifest = AppManifest.from_file(app_dir / "app.json")
    result = load_app_router(manifest, app_dir)
    assert result is None


def test_load_app_router_returns_none_when_file_missing(tmp_path):
    """If routes file doesn't exist, return None and don't crash."""
    from core.app_manifest import AppManifest
    from core.app_routes import load_app_router

    app_dir = tmp_path / "apps" / "broken"
    app_dir.mkdir(parents=True)

    manifest = AppManifest(
        id="broken", name="Broken", entry="main.py", routes="routes.py",
    )
    result = load_app_router(manifest, app_dir)
    assert result is None
