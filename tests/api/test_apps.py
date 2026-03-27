from __future__ import annotations


def test_internal_apps_returns_empty_list_when_no_apps(client):
    response = client.get("/internal/apps")
    assert response.status_code == 200
    body = response.json()
    assert "apps" in body
    assert isinstance(body["apps"], list)


def test_internal_apps_returns_discovered_app(client, runtime, monkeypatch):
    import json
    from core.app_manifest import AppManifest
    from core.app_supervisor import AppInstance

    app_dir = runtime.base_dir / "apps" / "testapp"
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(json.dumps({
        "id": "testapp",
        "name": "Test App",
        "version": "0.1.0",
        "entry": "main.py",
        "critical": False,
        "has_ui": False,
        "ui_path": "",
        "socket": "testapp.sock",
        "inferno": False,
        "description": "For testing",
    }))

    manifest = AppManifest.from_file(app_dir / "app.json")
    instance = AppInstance(manifest=manifest, status="running")
    client.app.state.app_instances = {"testapp": instance}

    response = client.get("/internal/apps")
    assert response.status_code == 200
    body = response.json()
    assert len(body["apps"]) == 1
    assert body["apps"][0]["id"] == "testapp"
    assert body["apps"][0]["name"] == "Test App"
    assert body["apps"][0]["status"] == "running"
