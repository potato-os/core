"""Structural contract tests — verify key files exist and critical wiring is intact.

These do NOT test behavior (Playwright handles that). They catch structural
regressions: missing files, broken imports, removed DOM IDs that other code
depends on.
"""
from __future__ import annotations

from pathlib import Path

from core.main import CHAT_HTML, WEB_ASSETS_DIR

_REPO_ROOT = Path(__file__).parent.parent.parent
_CHAT_APP_ASSETS = _REPO_ROOT / "apps" / "chat" / "assets"


def _read(directory, name):
    p = directory / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


# Aggregate: platform + chat app sources for cross-cutting assertions
_PLATFORM_JS = " ".join(
    _read(WEB_ASSETS_DIR, f) for f in (
        "shell.js", "state.js", "utils.js", "status.js", "runtime-ui.js",
        "settings-ui.js", "platform-controls.js", "platform-api.js",
        "model-api.js", "platform-notify.js", "model-switcher.js",
        "update-ui.js",
    )
)
_CHAT_JS = " ".join(
    _read(_CHAT_APP_ASSETS, f) for f in (
        "chat.js", "chat-engine.js", "messages.js",
        "session-manager.js", "image-handler.js",
    )
)
_ALL_JS = _PLATFORM_JS + _CHAT_JS
_ALL_UI = CHAT_HTML + _read(_CHAT_APP_ASSETS, "chat.html") + _ALL_JS


def test_root_endpoint_serves_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text.lower()
    assert "Potato" in response.text


def test_platform_assets_exist():
    for name in ("shell.js", "state.js", "utils.js", "status.js",
                 "platform-controls.js", "settings-ui.js"):
        assert (WEB_ASSETS_DIR / name).exists(), f"Missing platform asset: {name}"


def test_chat_app_assets_exist():
    for name in ("app.js", "chat.js", "chat-engine.js", "messages.js",
                 "session-manager.js", "image-handler.js", "chat.html"):
        assert (_CHAT_APP_ASSETS / name).exists(), f"Missing chat app asset: {name}"


def test_chat_app_manifest_exists():
    manifest = _REPO_ROOT / "apps" / "chat" / "app.json"
    assert manifest.exists(), "apps/chat/app.json must exist"
    import json
    data = json.loads(manifest.read_text())
    assert data["id"] == "chat"
    assert data["has_ui"] is True


def test_shell_has_app_container():
    assert 'id="appContainer"' in CHAT_HTML


def test_shell_loads_app_dynamically():
    shell = _read(WEB_ASSETS_DIR, "shell.js")
    assert "import(" in shell
    assert "appContainer" in shell


def test_chat_html_fragment_has_required_elements():
    fragment = _read(_CHAT_APP_ASSETS, "chat.html")
    assert 'id="messages"' in fragment
    assert 'id="composerForm"' in fragment
    assert 'id="userPrompt"' in fragment
    assert 'id="sendBtn"' in fragment


def test_chat_exports_init():
    app_js = _read(_CHAT_APP_ASSETS, "app.js")
    assert "export async function init(" in app_js
    assert "export function destroy(" in app_js


def test_streaming_and_sse_handling():
    assert "function consumeSseDeltas" in _CHAT_JS
    assert '[DONE]' in _CHAT_JS


def test_key_platform_functions_exist():
    assert "function pollStatus(" in _PLATFORM_JS
    assert "function setSidebarOpen(" in _PLATFORM_JS
    assert "function setStatus(" in _PLATFORM_JS
    assert "function showPlatformNotice(" in _PLATFORM_JS


def test_platform_css_renamed_to_shell():
    assert (WEB_ASSETS_DIR / "shell.css").exists(), "core/assets/shell.css must exist"
    assert not (WEB_ASSETS_DIR / "chat.css").exists(), "core/assets/chat.css should not exist"


def test_shell_html_references_shell_css():
    assert 'href="/assets/shell.css"' in CHAT_HTML
    assert 'href="/assets/chat.css"' not in CHAT_HTML


def test_chat_app_css_has_chat_classes():
    css = _read(_CHAT_APP_ASSETS, "chat.css")
    assert ".messages" in css
    assert ".composer" in css
    assert ".edit-modal" in css
    assert ".send-btn" in css
