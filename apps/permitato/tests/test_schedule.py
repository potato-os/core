"""Tests for Permitato schedule — day/time rules, evaluation, override, persistence."""

from __future__ import annotations

from datetime import datetime

import pytest


# ---------------------------------------------------------------------------
# ScheduleRule serialization
# ---------------------------------------------------------------------------


def test_schedule_rule_roundtrip():
    from apps.permitato.schedule import ScheduleRule

    rule = ScheduleRule(
        id="r1", mode="work", days=[0, 1, 2, 3, 4],
        start_time="09:00", end_time="17:00",
    )
    restored = ScheduleRule.from_dict(rule.to_dict())
    assert restored.id == rule.id
    assert restored.mode == rule.mode
    assert restored.days == rule.days
    assert restored.start_time == rule.start_time
    assert restored.end_time == rule.end_time
    assert restored.enabled is True


# ---------------------------------------------------------------------------
# add_rule validation
# ---------------------------------------------------------------------------


def test_add_rule_creates_with_uuid(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    rule = store.add_rule("work", [0, 1, 2, 3, 4], "09:00", "17:00")
    assert rule.id
    assert rule.mode == "work"
    assert len(store.list_rules()) == 1


def test_add_rule_rejects_invalid_mode(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    with pytest.raises(ValueError, match="mode"):
        store.add_rule("invalid", [0], "09:00", "17:00")


def test_add_rule_rejects_empty_days(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    with pytest.raises(ValueError, match="days"):
        store.add_rule("work", [], "09:00", "17:00")


def test_add_rule_rejects_invalid_day(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    with pytest.raises(ValueError, match="day"):
        store.add_rule("work", [7], "09:00", "17:00")
    with pytest.raises(ValueError, match="day"):
        store.add_rule("work", [-1], "09:00", "17:00")


def test_add_rule_rejects_bad_time_format(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    with pytest.raises(ValueError, match="time"):
        store.add_rule("work", [0], "9:00", "17:00")
    with pytest.raises(ValueError, match="time"):
        store.add_rule("work", [0], "09:00", "25:00")


def test_add_rule_rejects_end_before_start(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    with pytest.raises(ValueError, match="end_time.*start_time"):
        store.add_rule("work", [0], "17:00", "09:00")


def test_add_rule_rejects_equal_start_end(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    with pytest.raises(ValueError, match="end_time.*start_time"):
        store.add_rule("work", [0], "09:00", "09:00")


# ---------------------------------------------------------------------------
# remove_rule / update_rule
# ---------------------------------------------------------------------------


def test_remove_rule(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    rule = store.add_rule("work", [0], "09:00", "17:00")
    removed = store.remove_rule(rule.id)
    assert removed.id == rule.id
    assert len(store.list_rules()) == 0


def test_remove_rule_unknown_id(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    with pytest.raises(KeyError):
        store.remove_rule("nonexistent")


def test_update_rule(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    rule = store.add_rule("work", [0], "09:00", "17:00")
    updated = store.update_rule(rule.id, mode="sfw", start_time="10:00")
    assert updated.mode == "sfw"
    assert updated.start_time == "10:00"
    assert updated.end_time == "17:00"  # unchanged


def test_update_rule_unknown_id(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    with pytest.raises(KeyError):
        store.update_rule("nonexistent", mode="sfw")


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


def test_evaluate_inside_window():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0, 1, 2, 3, 4], "09:00", "17:00")
    # Monday 10:00
    now = datetime(2026, 3, 30, 10, 0)  # 2026-03-30 is a Monday
    assert store.evaluate(now) == "work"


def test_evaluate_outside_window():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0, 1, 2, 3, 4], "09:00", "17:00")
    # Monday 18:00
    now = datetime(2026, 3, 30, 18, 0)
    assert store.evaluate(now) is None


def test_evaluate_wrong_day():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")  # Monday only
    # Tuesday 10:00
    now = datetime(2026, 3, 31, 10, 0)
    assert store.evaluate(now) is None


def test_evaluate_at_start_boundary():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")
    # Exactly 09:00
    now = datetime(2026, 3, 30, 9, 0)
    assert store.evaluate(now) == "work"


def test_evaluate_at_end_boundary():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")
    # Exactly 17:00 — outside the window (end is exclusive)
    now = datetime(2026, 3, 30, 17, 0)
    assert store.evaluate(now) is None


def test_evaluate_disabled_rule_ignored():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    rule = store.add_rule("work", [0], "09:00", "17:00")
    store.update_rule(rule.id, enabled=False)
    now = datetime(2026, 3, 30, 10, 0)
    assert store.evaluate(now) is None


def test_evaluate_empty_store():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    assert store.evaluate(datetime(2026, 3, 30, 10, 0)) is None


def test_evaluate_overlapping_rules_last_wins():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")
    store.add_rule("sfw", [0], "08:00", "18:00")
    now = datetime(2026, 3, 30, 10, 0)
    assert store.evaluate(now) == "sfw"


def test_evaluate_multiple_non_overlapping():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0, 1, 2, 3, 4], "09:00", "17:00")
    store.add_rule("sfw", [0, 1, 2, 3, 4, 5, 6], "22:00", "23:59")
    # Monday 10:00 → work
    assert store.evaluate(datetime(2026, 3, 30, 10, 0)) == "work"
    # Monday 22:30 → sfw
    assert store.evaluate(datetime(2026, 3, 30, 22, 30)) == "sfw"
    # Monday 20:00 → none
    assert store.evaluate(datetime(2026, 3, 30, 20, 0)) is None


# ---------------------------------------------------------------------------
# next_transition()
# ---------------------------------------------------------------------------


def test_next_transition_upcoming():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")
    # Monday 07:00 → next transition is "work" at 09:00
    now = datetime(2026, 3, 30, 7, 0)
    nxt = store.next_transition(now)
    assert nxt is not None
    assert nxt["mode"] == "work"
    assert nxt["time"] == "09:00"


def test_next_transition_is_end_of_window():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")
    # Monday 10:00 (inside window) → next transition is end at 17:00
    now = datetime(2026, 3, 30, 10, 0)
    nxt = store.next_transition(now)
    assert nxt is not None
    assert nxt["time"] == "17:00"
    assert nxt["mode"] == "normal"  # what it transitions TO


def test_next_transition_empty_schedule():
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=None)
    assert store.next_transition(datetime(2026, 3, 30, 10, 0)) is None


# ---------------------------------------------------------------------------
# persist / load
# ---------------------------------------------------------------------------


def test_persist_and_load_roundtrip(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    store.add_rule("work", [0, 1, 2, 3, 4], "09:00", "17:00")
    store.add_rule("sfw", [5, 6], "22:00", "23:00")
    store.persist()

    store2 = ScheduleStore(data_dir=tmp_path)
    store2.load()
    assert len(store2.list_rules()) == 2


def test_load_missing_file(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    store = ScheduleStore(data_dir=tmp_path)
    store.load()
    assert len(store.list_rules()) == 0


def test_load_corrupt_json(tmp_path):
    from apps.permitato.schedule import ScheduleStore

    (tmp_path / "schedule.json").write_text("not json!!!")
    store = ScheduleStore(data_dir=tmp_path)
    store.load()
    assert len(store.list_rules()) == 0


# ---------------------------------------------------------------------------
# PermitState override fields and effective_mode
# ---------------------------------------------------------------------------


def test_state_persist_loads_override_fields(tmp_path):
    from unittest.mock import AsyncMock
    from apps.permitato.state import PermitState

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.mode = "normal"
    state.override_mode = "normal"
    state.override_scheduled_mode = "work"
    state.persist()

    state2 = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state2.load()
    assert state2.override_mode == "normal"
    assert state2.override_scheduled_mode == "work"


def test_state_load_v1_no_override(tmp_path):
    """Loading a version-1 state.json (pre-schedule) sets override fields to None."""
    import json
    (tmp_path / "state.json").write_text(json.dumps({
        "version": 1, "mode": "work", "client_id": "192.168.1.5",
    }))

    from apps.permitato.state import PermitState
    state = PermitState(data_dir=tmp_path)
    state.load()
    assert state.mode == "work"
    assert state.override_mode is None
    assert state.override_scheduled_mode is None


def test_effective_mode_override_wins(tmp_path):
    from unittest.mock import MagicMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0, 1, 2, 3, 4], "09:00", "17:00")

    state = PermitState(data_dir=tmp_path)
    state.schedule_store = store
    state.override_mode = "normal"
    # Even though schedule says "work", override takes precedence
    now = datetime(2026, 3, 30, 10, 0)
    assert state.effective_mode(now) == "normal"


def test_effective_mode_schedule_when_no_override(tmp_path):
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0, 1, 2, 3, 4], "09:00", "17:00")

    state = PermitState(data_dir=tmp_path)
    state.schedule_store = store
    now = datetime(2026, 3, 30, 10, 0)
    assert state.effective_mode(now) == "work"


def test_effective_mode_normal_when_nothing_matches(tmp_path):
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    store = ScheduleStore(data_dir=None)
    state = PermitState(data_dir=tmp_path)
    state.schedule_store = store
    assert state.effective_mode(datetime(2026, 3, 30, 10, 0)) == "normal"


def test_effective_mode_normal_when_no_store(tmp_path):
    from apps.permitato.state import PermitState

    state = PermitState(data_dir=tmp_path)
    assert state.effective_mode() == "normal"


# ---------------------------------------------------------------------------
# Schedule check loop behavior
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_schedule_loop_applies_scheduled_mode(tmp_path):
    """When schedule says 'work' and current mode is 'normal', loop switches."""
    from unittest.mock import AsyncMock, patch
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = store
    state.mode = "normal"
    state.pihole_available = True

    from apps.permitato.lifecycle import _apply_schedule_tick
    now = datetime(2026, 3, 30, 10, 0)
    await _apply_schedule_tick(state, now)

    assert state.mode == "work"


@pytest.mark.anyio
async def test_schedule_loop_skips_when_override_in_same_window(tmp_path):
    """Override stays active while we're still in the same scheduled window."""
    from unittest.mock import AsyncMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = store
    state.mode = "normal"
    state.override_mode = "normal"
    state.override_scheduled_mode = "work"

    from apps.permitato.lifecycle import _apply_schedule_tick
    now = datetime(2026, 3, 30, 10, 0)  # still inside work window
    await _apply_schedule_tick(state, now)

    assert state.mode == "normal"  # override held
    assert state.override_mode == "normal"


@pytest.mark.anyio
async def test_schedule_loop_clears_override_on_transition(tmp_path):
    """Override clears when schedule transitions to a different window."""
    from unittest.mock import AsyncMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = store
    state.mode = "normal"
    state.override_mode = "normal"
    state.override_scheduled_mode = "work"
    state.pihole_available = True

    from apps.permitato.lifecycle import _apply_schedule_tick
    now = datetime(2026, 3, 30, 18, 0)  # outside work window
    await _apply_schedule_tick(state, now)

    assert state.override_mode is None
    assert state.override_scheduled_mode is None
    # Effective mode is "normal" (no schedule match → fallback)
    assert state.mode == "normal"


@pytest.mark.anyio
async def test_schedule_loop_noop_when_mode_matches(tmp_path):
    """No Pi-hole call when scheduled mode already matches current mode."""
    from unittest.mock import AsyncMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")

    adapter = AsyncMock()
    state = PermitState(data_dir=tmp_path, adapter=adapter)
    state.schedule_store = store
    state.mode = "work"
    state.pihole_available = True

    from apps.permitato.lifecycle import _apply_schedule_tick
    now = datetime(2026, 3, 30, 10, 0)
    await _apply_schedule_tick(state, now)

    assert state.mode == "work"
    adapter.update_client.assert_not_called()


@pytest.mark.anyio
async def test_schedule_loop_noop_without_store(tmp_path):
    from apps.permitato.state import PermitState

    state = PermitState(data_dir=tmp_path)
    from apps.permitato.lifecycle import _apply_schedule_tick
    await _apply_schedule_tick(state, datetime(2026, 3, 30, 10, 0))
    assert state.mode == "normal"


# ---------------------------------------------------------------------------
# Startup schedule evaluation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_startup_clears_stale_override(tmp_path):
    """On startup, override is cleared if schedule has moved past its window."""
    import json

    # Simulate persisted state with override for a 'work' window
    (tmp_path / "state.json").write_text(json.dumps({
        "version": 2, "mode": "normal", "client_id": "",
        "override_mode": "normal", "override_scheduled_mode": "work",
    }))
    # Schedule: work Mon 09:00-17:00
    (tmp_path / "schedule.json").write_text(json.dumps({
        "version": 1,
        "rules": {"r1": {
            "id": "r1", "mode": "work", "days": [0],
            "start_time": "09:00", "end_time": "17:00", "enabled": True,
        }},
    }))

    from apps.permitato.state import PermitState
    from apps.permitato.schedule import ScheduleStore

    state = PermitState(data_dir=tmp_path)
    state.load()
    state.schedule_store = ScheduleStore(data_dir=tmp_path)
    state.schedule_store.load()

    # Simulate boot at 18:00 Mon — outside the work window
    from apps.permitato.state import apply_startup_schedule
    await apply_startup_schedule(state, now=datetime(2026, 3, 30, 18, 0))

    assert state.override_mode is None
    assert state.override_scheduled_mode is None

    # Verify the clear was persisted to disk (not just in memory)
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["override_mode"] is None
    assert saved["override_scheduled_mode"] is None


@pytest.mark.anyio
async def test_startup_preserves_valid_override(tmp_path):
    """On startup, override stays if still within the same scheduled window."""
    import json

    (tmp_path / "state.json").write_text(json.dumps({
        "version": 2, "mode": "normal", "client_id": "",
        "override_mode": "normal", "override_scheduled_mode": "work",
    }))
    (tmp_path / "schedule.json").write_text(json.dumps({
        "version": 1,
        "rules": {"r1": {
            "id": "r1", "mode": "work", "days": [0],
            "start_time": "09:00", "end_time": "17:00", "enabled": True,
        }},
    }))

    from apps.permitato.state import PermitState
    from apps.permitato.schedule import ScheduleStore

    state = PermitState(data_dir=tmp_path)
    state.load()
    state.schedule_store = ScheduleStore(data_dir=tmp_path)
    state.schedule_store.load()

    # Boot at 10:00 Mon — still inside work window
    from apps.permitato.state import apply_startup_schedule
    await apply_startup_schedule(state, now=datetime(2026, 3, 30, 10, 0))

    assert state.override_mode == "normal"
    assert state.mode == "normal"  # override held


# ---------------------------------------------------------------------------
# P1: Startup must apply mode to Pi-hole client
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_startup_schedule_applies_to_pihole(tmp_path):
    """apply_startup_schedule must call apply_mode_to_client when mode changes."""
    import json
    from unittest.mock import AsyncMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState, apply_startup_schedule

    # Persisted state says "normal", but schedule says "work" right now
    (tmp_path / "state.json").write_text(json.dumps({
        "version": 2, "mode": "normal", "client_id": "192.168.1.5",
        "override_mode": None, "override_scheduled_mode": None,
    }))
    (tmp_path / "schedule.json").write_text(json.dumps({
        "version": 1,
        "rules": {"r1": {
            "id": "r1", "mode": "work", "days": [0],
            "start_time": "09:00", "end_time": "17:00", "enabled": True,
        }},
    }))

    adapter = AsyncMock()
    state = PermitState(data_dir=tmp_path, adapter=adapter)
    state.load()
    state.pihole_available = True
    state.schedule_store = ScheduleStore(data_dir=tmp_path)
    state.schedule_store.load()

    now = datetime(2026, 3, 30, 10, 0)  # Monday 10:00, inside work window
    await apply_startup_schedule(state, now=now)

    assert state.mode == "work"
    adapter.update_client.assert_called_once()

    # Startup mode correction must write an audit entry so stats can anchor to it
    from apps.permitato.audit import read_audit_log
    entries = read_audit_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["event"] == "scheduled_mode_switch"
    assert entries[0]["from_mode"] == "normal"
    assert entries[0]["to_mode"] == "work"


# ---------------------------------------------------------------------------
# P1: Override clear must persist even when effective mode stays same
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_schedule_tick_persists_override_clear_same_mode(tmp_path):
    """When override clears but effective mode matches state.mode, still persist."""
    from unittest.mock import AsyncMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = store
    # User overrode to "normal" during work window, then work window ended
    state.mode = "normal"
    state.override_mode = "normal"
    state.override_scheduled_mode = "work"
    state.pihole_available = True
    state.persist()

    from apps.permitato.lifecycle import _apply_schedule_tick
    now = datetime(2026, 3, 30, 18, 0)  # outside work window — override should clear
    await _apply_schedule_tick(state, now)

    assert state.override_mode is None

    # Verify the clear was persisted (reload from disk)
    import json
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["override_mode"] is None
    assert saved["override_scheduled_mode"] is None


# ---------------------------------------------------------------------------
# P2: Schedule edits re-evaluate immediately
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_post_schedule_applies_immediately(tmp_path):
    """Creating a rule whose window includes now must apply the mode right away."""
    from unittest.mock import AsyncMock, patch
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    adapter = AsyncMock()
    state = PermitState(data_dir=tmp_path, adapter=adapter)
    state.schedule_store = ScheduleStore(data_dir=tmp_path)
    state.mode = "normal"
    state.client_id = "192.168.1.5"
    state.pihole_available = True

    from apps.permitato import routes
    now = datetime(2026, 3, 30, 10, 0)  # Monday 10:00
    with patch.object(routes, "_schedule_now", return_value=now):
        result = await _call_post_schedule(state, {
            "mode": "work", "days": [0, 1, 2, 3, 4],
            "start_time": "09:00", "end_time": "17:00",
        })

    assert result["rule"]["mode"] == "work"
    assert state.mode == "work"
    adapter.update_client.assert_called_once()


@pytest.mark.anyio
async def test_delete_schedule_applies_immediately(tmp_path):
    """Deleting the active rule must revert mode right away."""
    from unittest.mock import AsyncMock, patch
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    adapter = AsyncMock()
    state = PermitState(data_dir=tmp_path, adapter=adapter)
    state.schedule_store = ScheduleStore(data_dir=tmp_path)
    rule = state.schedule_store.add_rule("work", [0], "09:00", "17:00")
    state.mode = "work"
    state.pihole_available = True

    from apps.permitato import routes
    now = datetime(2026, 3, 30, 10, 0)
    with patch.object(routes, "_schedule_now", return_value=now):
        result = await _call_delete_schedule(state, rule.id)

    assert result["deleted"] is True
    assert state.mode == "normal"  # reverted because no rules match


# ---------------------------------------------------------------------------
# Override recording on POST /mode
# ---------------------------------------------------------------------------


def test_record_override_sets_when_deviating(tmp_path):
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState
    from apps.permitato.routes import _record_override

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")

    state = PermitState(data_dir=tmp_path)
    state.schedule_store = store

    _record_override(state, "normal", now=datetime(2026, 3, 30, 10, 0))
    assert state.override_mode == "normal"
    assert state.override_scheduled_mode == "work"


def test_record_override_clears_when_matching(tmp_path):
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState
    from apps.permitato.routes import _record_override

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")

    state = PermitState(data_dir=tmp_path)
    state.schedule_store = store
    state.override_mode = "normal"
    state.override_scheduled_mode = "work"

    # User switches back to "work" — matches schedule, clear override
    _record_override(state, "work", now=datetime(2026, 3, 30, 10, 0))
    assert state.override_mode is None
    assert state.override_scheduled_mode is None


def test_record_override_noop_when_no_schedule(tmp_path):
    from apps.permitato.state import PermitState
    from apps.permitato.routes import _record_override

    state = PermitState(data_dir=tmp_path)
    _record_override(state, "work")
    assert state.override_mode is None


# ---------------------------------------------------------------------------
# Schedule CRUD API routes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_schedule_empty(tmp_path):
    from unittest.mock import AsyncMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState
    from apps.permitato.routes import get_schedule
    from starlette.testclient import TestClient

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = ScheduleStore(data_dir=tmp_path)

    result = await _call_get_schedule(state)
    assert result["rules"] == []
    assert result["scheduled_mode"] is None


@pytest.mark.anyio
async def test_post_schedule_creates_rule(tmp_path):
    from unittest.mock import AsyncMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = ScheduleStore(data_dir=tmp_path)

    result = await _call_post_schedule(state, {
        "mode": "work", "days": [0, 1, 2, 3, 4],
        "start_time": "09:00", "end_time": "17:00",
    })
    assert "rule" in result
    assert result["rule"]["mode"] == "work"
    assert len(state.schedule_store.list_rules()) == 1


@pytest.mark.anyio
async def test_post_schedule_rejects_invalid(tmp_path):
    from unittest.mock import AsyncMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = ScheduleStore(data_dir=tmp_path)

    result, status = await _call_post_schedule(
        state, {"mode": "invalid", "days": [0], "start_time": "09:00", "end_time": "17:00"},
        return_status=True,
    )
    assert status == 400


@pytest.mark.anyio
async def test_delete_schedule_removes_rule(tmp_path):
    from unittest.mock import AsyncMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = ScheduleStore(data_dir=tmp_path)
    rule = state.schedule_store.add_rule("work", [0], "09:00", "17:00")

    result = await _call_delete_schedule(state, rule.id)
    assert result["deleted"] is True
    assert len(state.schedule_store.list_rules()) == 0


@pytest.mark.anyio
async def test_delete_schedule_unknown_id(tmp_path):
    from unittest.mock import AsyncMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = ScheduleStore(data_dir=tmp_path)

    result, status = await _call_delete_schedule(state, "nonexistent", return_status=True)
    assert status == 404


@pytest.mark.anyio
async def test_get_status_includes_schedule_fields(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = store
    state.pihole_available = True
    state.exception_store = MagicMock()
    state.exception_store.active_count.return_value = 0
    state.exception_store.list_active.return_value = []

    result = await _call_get_status(state, now=datetime(2026, 3, 30, 10, 0))
    assert result["schedule_active"] is True
    assert result["scheduled_mode"] == "work"
    assert result["override_active"] is False
    assert result["override_mode"] is None


@pytest.mark.anyio
async def test_get_status_override_fields(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from apps.permitato.schedule import ScheduleStore
    from apps.permitato.state import PermitState

    store = ScheduleStore(data_dir=None)
    store.add_rule("work", [0], "09:00", "17:00")

    state = PermitState(data_dir=tmp_path, adapter=AsyncMock())
    state.schedule_store = store
    state.override_mode = "normal"
    state.override_scheduled_mode = "work"
    state.pihole_available = True
    state.exception_store = MagicMock()
    state.exception_store.active_count.return_value = 0
    state.exception_store.list_active.return_value = []

    result = await _call_get_status(state, now=datetime(2026, 3, 30, 10, 0))
    assert result["override_active"] is True
    assert result["override_mode"] == "normal"


# ---------------------------------------------------------------------------
# Test helpers — direct function calls with mock state
# ---------------------------------------------------------------------------


async def _call_get_schedule(state):
    """Call the get_schedule route handler directly."""
    from unittest.mock import MagicMock
    request = MagicMock()
    request.app.state.permit_state = state

    from apps.permitato.routes import get_schedule
    return await get_schedule(request)


async def _call_post_schedule(state, body, return_status=False):
    from unittest.mock import AsyncMock, MagicMock
    request = MagicMock()
    request.app.state.permit_state = state
    request.json = AsyncMock(return_value=body)

    from apps.permitato.routes import create_schedule_rule
    result = await create_schedule_rule(request)
    if return_status:
        if hasattr(result, "status_code"):
            import json
            return json.loads(result.body.decode()), result.status_code
        return result, 200
    return result


async def _call_delete_schedule(state, rule_id, return_status=False):
    from unittest.mock import MagicMock
    request = MagicMock()
    request.app.state.permit_state = state

    from apps.permitato.routes import delete_schedule_rule
    result = await delete_schedule_rule(request, rule_id)
    if return_status:
        if hasattr(result, "status_code"):
            import json
            return json.loads(result.body.decode()), result.status_code
        return result, 200
    return result


async def _call_get_status(state, now=None):
    from unittest.mock import AsyncMock, MagicMock, patch
    request = MagicMock()
    request.app.state.permit_state = state

    from apps.permitato import routes
    if now:
        with patch.object(routes, "_schedule_now", return_value=now):
            return await routes.permitato_status(request)
    return await routes.permitato_status(request)
