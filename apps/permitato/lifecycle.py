"""Permitato app lifecycle hooks — called by platform during startup/shutdown."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from datetime import datetime

from apps.permitato.state import (
    PermitState, apply_mode_to_client, apply_startup_schedule,
    initialize_permitato, shutdown_permitato, reconnect_pihole,
)

logger = logging.getLogger(__name__)


async def on_startup(app, app_dir: Path, data_dir: Path) -> None:
    """Initialize Permitato: connect to Pi-hole, start expiry loop."""
    app.state.permit_state = None
    app.state.permit_expiry_task = None

    pw_path = app.state.runtime.base_dir / "config" / "permitato_pihole_password"
    if not pw_path.exists():
        logger.info("Pi-hole password not found at %s — skipping Permitato init", pw_path)
        return

    pihole_pw = pw_path.read_text(encoding="utf-8").strip()
    permit_data_dir = data_dir / "permitato"
    app.state.permit_state = await initialize_permitato(
        data_dir=permit_data_dir,
        pihole_password=pihole_pw,
    )

    # Initialize schedule store and apply schedule on startup
    from apps.permitato.schedule import ScheduleStore

    state = app.state.permit_state
    state.schedule_store = ScheduleStore(data_dir=permit_data_dir)
    state.schedule_store.load()
    await apply_startup_schedule(state)

    app.state.permit_expiry_task = asyncio.create_task(
        _exception_expiry_loop(app),
        name="permitato-expiry",
    )
    app.state.permit_reconnect_task = asyncio.create_task(
        _pihole_reconnection_loop(app),
        name="permitato-reconnect",
    )
    app.state.permit_schedule_task = asyncio.create_task(
        _schedule_check_loop(app),
        name="permitato-schedule",
    )


async def on_shutdown(app) -> None:
    """Shutdown Permitato: cancel background tasks, disconnect adapter."""
    for attr in ("permit_expiry_task", "permit_reconnect_task", "permit_schedule_task"):
        task = getattr(app.state, attr, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    permit_state = getattr(app.state, "permit_state", None)
    if permit_state is not None:
        await shutdown_permitato(permit_state)


async def _exception_expiry_loop(app) -> None:
    """Background task: revoke expired exceptions every 30s."""
    from apps.permitato.audit import write_audit_entry

    while True:
        await asyncio.sleep(30)
        state = getattr(app.state, "permit_state", None)
        if state and state.exception_store:
            for exc in state.exception_store.get_expired():
                if state.adapter and state.pihole_available:
                    try:
                        await state.adapter.delete_domain_rule(
                            exc.regex_pattern, "allow", "regex",
                        )
                    except Exception:
                        pass
                write_audit_entry(state.data_dir, {
                    "event": "exception_expired",
                    "domain": exc.domain,
                    "exception_id": exc.id,
                })
            revoked = state.exception_store.cleanup_expired()
            if revoked:
                state.exception_store.persist()


async def _pihole_reconnection_loop(app) -> None:
    """Background task: attempt Pi-hole reconnection every 60s when degraded."""
    from apps.permitato.audit import write_audit_entry

    while True:
        await asyncio.sleep(60)
        state = getattr(app.state, "permit_state", None)
        if state and not state.pihole_available:
            was_degraded = not state.pihole_available
            await reconnect_pihole(state)
            if was_degraded and state.pihole_available:
                write_audit_entry(state.data_dir, {"event": "pihole_recovered"})


async def _schedule_check_loop(app) -> None:
    """Background task: evaluate schedule and apply mode transitions every 60s."""
    while True:
        await asyncio.sleep(60)
        state = getattr(app.state, "permit_state", None)
        if state:
            await _apply_schedule_tick(state)


async def _apply_schedule_tick(
    state: PermitState, now: datetime | None = None,
) -> None:
    """Single schedule evaluation tick — used by the loop and testable directly."""
    from apps.permitato.audit import write_audit_entry

    if not state.schedule_store or not state.schedule_store.list_rules():
        return

    scheduled_mode = state.schedule_store.evaluate(now)

    # Override clearing: if the schedule has moved to a different window, clear
    if state.override_mode is not None:
        if scheduled_mode != state.override_scheduled_mode:
            old_override = state.override_mode
            state.override_mode = None
            state.override_scheduled_mode = None
            state.persist()
            write_audit_entry(state.data_dir, {
                "event": "override_cleared",
                "old_override_mode": old_override,
                "new_scheduled_mode": scheduled_mode,
            })
        else:
            return  # override still valid, skip

    effective = scheduled_mode or "normal"
    if effective != state.mode:
        old_mode = state.mode
        state.mode = effective
        state.persist()
        await apply_mode_to_client(state)
        write_audit_entry(state.data_dir, {
            "event": "scheduled_mode_switch",
            "from_mode": old_mode,
            "to_mode": effective,
        })
