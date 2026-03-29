"""Tests for the Permitato app lifecycle hooks."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.anyio
async def test_on_startup_skips_when_no_password_file(tmp_path):
    from apps.permitato import lifecycle

    app = MagicMock()
    app.state.runtime.base_dir = tmp_path  # no config/permitato_pihole_password
    await lifecycle.on_startup(app, tmp_path / "apps" / "permitato", tmp_path / "data")
    assert app.state.permit_state is None


@pytest.mark.anyio
async def test_on_startup_initializes_state(tmp_path, monkeypatch):
    from apps.permitato import lifecycle

    pw_dir = tmp_path / "config"
    pw_dir.mkdir()
    (pw_dir / "permitato_pihole_password").write_text("secret")

    fake_state = MagicMock()
    mock_init = AsyncMock(return_value=fake_state)
    monkeypatch.setattr(lifecycle, "initialize_permitato", mock_init)
    monkeypatch.setattr(asyncio, "create_task", lambda coro, **kw: (coro.close(), MagicMock())[1])

    app = MagicMock()
    app.state.runtime.base_dir = tmp_path

    await lifecycle.on_startup(app, tmp_path / "apps" / "permitato", tmp_path / "data")

    mock_init.assert_awaited_once()
    assert app.state.permit_state == fake_state


@pytest.mark.anyio
async def test_on_shutdown_cleans_up(monkeypatch):
    from apps.permitato import lifecycle

    mock_shutdown = AsyncMock()
    monkeypatch.setattr(lifecycle, "shutdown_permitato", mock_shutdown)

    app = MagicMock()

    class _FakeTask:
        def __init__(self):
            self.cancel_called = False
        def cancel(self):
            self.cancel_called = True
        def __await__(self):
            return asyncio.sleep(0).__await__()

    expiry_task = _FakeTask()
    reconnect_task = _FakeTask()
    app.state.permit_expiry_task = expiry_task
    app.state.permit_reconnect_task = reconnect_task
    app.state.permit_state = MagicMock()

    await lifecycle.on_shutdown(app)

    assert expiry_task.cancel_called
    assert reconnect_task.cancel_called
    mock_shutdown.assert_awaited_once_with(app.state.permit_state)
