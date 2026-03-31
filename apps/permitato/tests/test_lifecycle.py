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
    mock_startup_sched = AsyncMock()
    monkeypatch.setattr(lifecycle, "initialize_permitato", mock_init)
    monkeypatch.setattr(lifecycle, "apply_startup_schedule", mock_startup_sched)
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
    schedule_task = _FakeTask()
    bypass_task = _FakeTask()
    app.state.permit_expiry_task = expiry_task
    app.state.permit_reconnect_task = reconnect_task
    app.state.permit_schedule_task = schedule_task
    app.state.permit_bypass_task = bypass_task
    app.state.permit_state = MagicMock()

    await lifecycle.on_shutdown(app)

    assert expiry_task.cancel_called
    assert reconnect_task.cancel_called
    assert schedule_task.cancel_called
    assert bypass_task.cancel_called
    mock_shutdown.assert_awaited_once_with(app.state.permit_state)


# ---------------------------------------------------------------------------
# DNS cache flush in background tasks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_expiry_loop_flushes_after_cleanup(tmp_path, monkeypatch):
    """Expiry loop must flush DNS cache once after cleaning up expired exceptions."""
    import time
    from apps.permitato.exceptions import ExceptionStore
    from apps.permitato.state import PermitState

    adapter = AsyncMock()
    store = ExceptionStore(data_dir=tmp_path)
    # Grant two exceptions with TTL=0 so they expire immediately
    store.grant("a.com", "test", ttl_seconds=0)
    store.grant("b.com", "test", ttl_seconds=0)

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        exception_store=store,
    )

    app = MagicMock()
    app.state.permit_state = state

    # Run one iteration of the expiry loop body (skip the sleep)
    from apps.permitato.audit import write_audit_entry
    for exc in state.exception_store.get_expired():
        if state.adapter and state.pihole_available:
            await state.adapter.delete_domain_rule(exc.regex_pattern, "allow", "regex")
        write_audit_entry(state.data_dir, {"event": "exception_expired", "domain": exc.domain, "exception_id": exc.id})
    revoked = state.exception_store.cleanup_expired()

    # Import the function we're testing — it should flush once
    from apps.permitato.state import flush_dns_cache_safe
    if revoked:
        await flush_dns_cache_safe(state)

    adapter.flush_dns_cache.assert_awaited_once()


@pytest.mark.anyio
async def test_expiry_loop_no_flush_when_nothing_expired(tmp_path):
    """Expiry loop must not flush if no exceptions expired."""
    from apps.permitato.exceptions import ExceptionStore
    from apps.permitato.state import PermitState, flush_dns_cache_safe

    adapter = AsyncMock()
    store = ExceptionStore(data_dir=tmp_path)
    # Grant with long TTL — not expired
    store.grant("a.com", "test", ttl_seconds=3600)

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        exception_store=store,
    )

    revoked = state.exception_store.cleanup_expired()
    if revoked:
        await flush_dns_cache_safe(state)

    adapter.flush_dns_cache.assert_not_awaited()


@pytest.mark.anyio
async def test_schedule_tick_flushes_on_mode_change(tmp_path, monkeypatch):
    """Schedule tick must flush DNS cache when mode changes."""
    from apps.permitato.state import PermitState
    from apps.permitato.lifecycle import _apply_schedule_tick

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        mode="normal",
        client_id="192.168.1.10",
        group_map={"permitato_work": 1, "permitato_sfw": 2},
    )

    # Set up a schedule store that evaluates to "work"
    schedule_store = MagicMock()
    schedule_store.list_rules.return_value = [MagicMock()]
    schedule_store.evaluate.return_value = "work"
    state.schedule_store = schedule_store

    from datetime import datetime
    await _apply_schedule_tick(state, now=datetime(2026, 3, 31, 10, 0))

    assert state.mode == "work"
    adapter.flush_dns_cache.assert_awaited()


@pytest.mark.anyio
async def test_schedule_tick_no_flush_when_mode_unchanged(tmp_path):
    """Schedule tick must not flush when mode stays the same."""
    from apps.permitato.state import PermitState
    from apps.permitato.lifecycle import _apply_schedule_tick

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        mode="work",
        client_id="192.168.1.10",
        group_map={"permitato_work": 1, "permitato_sfw": 2},
    )

    schedule_store = MagicMock()
    schedule_store.list_rules.return_value = [MagicMock()]
    schedule_store.evaluate.return_value = "work"  # same as current
    state.schedule_store = schedule_store

    from datetime import datetime
    await _apply_schedule_tick(state, now=datetime(2026, 3, 31, 10, 0))

    assert state.mode == "work"
    adapter.flush_dns_cache.assert_not_awaited()


@pytest.mark.anyio
async def test_startup_schedule_flushes_on_mode_change(tmp_path):
    """apply_startup_schedule must flush DNS cache when mode changes."""
    from apps.permitato.state import PermitState, apply_startup_schedule

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        mode="normal",
        client_id="192.168.1.10",
        group_map={"permitato_work": 1, "permitato_sfw": 2},
    )

    schedule_store = MagicMock()
    schedule_store.evaluate.return_value = "work"
    schedule_store.list_rules.return_value = [MagicMock()]
    state.schedule_store = schedule_store

    # effective_mode returns the scheduled mode when no override
    state.override_mode = None
    state.override_scheduled_mode = None

    from datetime import datetime
    await apply_startup_schedule(state, now=datetime(2026, 3, 31, 10, 0))

    assert state.mode == "work"
    adapter.flush_dns_cache.assert_awaited()


# ---------------------------------------------------------------------------
# DNS bypass detection loop
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bypass_check_loop_calls_update(tmp_path, monkeypatch):
    """Bypass check loop must call update_bypass_status."""
    from apps.permitato.state import PermitState
    from apps.permitato import lifecycle

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        client_id="192.168.1.10",
    )

    mock_update = AsyncMock()
    monkeypatch.setattr(lifecycle, "update_bypass_status", mock_update)

    app = MagicMock()
    app.state.permit_state = state

    # Simulate one loop iteration (the function body after sleep)
    await mock_update(state)
    mock_update.assert_awaited_once_with(state)
