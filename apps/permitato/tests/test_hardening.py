"""Tests for Permitato hardening — atomic writes, reconnection, compensation, rotation."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
from httpx import Response


# ---------------------------------------------------------------------------
# Atomic persistence — corruption resistance via write-tmp + os.replace
# ---------------------------------------------------------------------------


def test_state_persist_does_not_corrupt_on_replace_failure(tmp_path):
    from apps.permitato.state import PermitState

    # Write valid state first
    state = PermitState(data_dir=tmp_path, mode="work", client_id="10.0.0.1")
    state.persist()

    # Now try to persist with os.replace failing
    state.mode = "sfw"
    with patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            state.persist()

    # Original file should still be intact
    path = tmp_path / "state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mode"] == "work"


def test_exception_persist_does_not_corrupt_on_replace_failure(tmp_path):
    from apps.permitato.exceptions import ExceptionStore

    store = ExceptionStore(data_dir=tmp_path)
    store.grant("twitter.com", "DMs", ttl_seconds=3600)
    store.persist()

    # Grant another and fail the write
    store.grant("reddit.com", "research", ttl_seconds=1800)
    with patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            store.persist()

    # Original file should have only 1 exception
    store2 = ExceptionStore(data_dir=tmp_path)
    store2.load()
    assert store2.active_count() == 1


# ---------------------------------------------------------------------------
# Pi-hole reconnection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reconnect_recovers_from_degraded(tmp_path):
    from apps.permitato.state import PermitState, reconnect_pihole

    adapter = AsyncMock()
    adapter.connect = AsyncMock()
    adapter.get_groups = AsyncMock(return_value=[
        {"name": "permitato_work", "id": 1},
        {"name": "permitato_sfw", "id": 2},
        {"name": "permitato_exceptions", "id": 3},
    ])
    adapter.add_domain_rule = AsyncMock()
    adapter.get_domain_rules = AsyncMock(return_value=[])
    adapter.update_client = AsyncMock()

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=False,
        degraded_since=time.time() - 60,
    )
    state.exception_store = MagicMock()
    state.exception_store.list_active.return_value = []

    await reconnect_pihole(state)

    assert state.pihole_available is True
    assert state.degraded_since is None
    adapter.connect.assert_awaited_once()


@pytest.mark.anyio
async def test_reconnect_noop_when_already_connected(tmp_path):
    from apps.permitato.state import PermitState, reconnect_pihole

    adapter = AsyncMock()
    adapter.is_healthy = AsyncMock(return_value=True)

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
    )

    await reconnect_pihole(state)

    adapter.connect.assert_not_awaited()


@pytest.mark.anyio
async def test_reconnect_stays_degraded_on_failure(tmp_path):
    from apps.permitato.pihole_adapter import PiholeUnavailableError
    from apps.permitato.state import PermitState, reconnect_pihole

    adapter = AsyncMock()
    adapter.connect = AsyncMock(side_effect=PiholeUnavailableError("still down"))

    degraded_ts = time.time() - 120
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=False,
        degraded_since=degraded_ts,
    )

    await reconnect_pihole(state)

    assert state.pihole_available is False
    assert state.degraded_since == degraded_ts


@pytest.mark.anyio
async def test_reconnect_reseeds_groups(tmp_path):
    from apps.permitato.state import PermitState, reconnect_pihole

    adapter = AsyncMock()
    adapter.connect = AsyncMock()
    adapter.get_groups = AsyncMock(return_value=[
        {"name": "permitato_work", "id": 1},
        {"name": "permitato_sfw", "id": 2},
        {"name": "permitato_exceptions", "id": 3},
    ])
    adapter.add_domain_rule = AsyncMock()
    adapter.get_domain_rules = AsyncMock(return_value=[])
    adapter.update_client = AsyncMock()

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=False,
        degraded_since=time.time() - 60,
    )
    state.exception_store = MagicMock()
    state.exception_store.list_active.return_value = []

    await reconnect_pihole(state)

    assert state.group_map["permitato_work"] == 1
    assert state.exception_group_id == 3
    adapter.add_domain_rule.assert_called()  # deny-lists reseeded


@pytest.mark.anyio
async def test_reconnect_stays_degraded_on_mid_recovery_failure(tmp_path):
    from apps.permitato.state import PermitState, reconnect_pihole

    adapter = AsyncMock()
    adapter.connect = AsyncMock()
    # connect succeeds, but get_groups blows up
    adapter.get_groups = AsyncMock(side_effect=Exception("transient failure"))

    degraded_ts = time.time() - 60
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=False,
        degraded_since=degraded_ts,
    )

    await reconnect_pihole(state)

    assert state.pihole_available is False
    assert state.degraded_since == degraded_ts


@pytest.mark.anyio
async def test_reconnect_reapplies_mode_to_client(tmp_path):
    from apps.permitato.state import PermitState, reconnect_pihole

    adapter = AsyncMock()
    adapter.connect = AsyncMock()
    adapter.get_groups = AsyncMock(return_value=[
        {"name": "permitato_work", "id": 1},
        {"name": "permitato_sfw", "id": 2},
        {"name": "permitato_exceptions", "id": 3},
    ])
    adapter.add_domain_rule = AsyncMock()
    adapter.get_domain_rules = AsyncMock(return_value=[])
    adapter.update_client = AsyncMock()

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=False,
        degraded_since=time.time() - 60,
        mode="work",
        client_id="192.168.1.10",
    )
    state.exception_store = MagicMock()
    state.exception_store.list_active.return_value = []

    await reconnect_pihole(state)

    # Mode should have been reapplied to the client
    adapter.update_client.assert_called_once()
    call_args = adapter.update_client.call_args
    assert call_args.args[0] == "192.168.1.10"
    groups = call_args.args[1]
    assert 1 in groups  # permitato_work group


# ---------------------------------------------------------------------------
# Exception compensation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_compensation_readds_missing_pihole_rules(tmp_path):
    from apps.permitato.state import PermitState, compensate_exceptions
    from apps.permitato.exceptions import ExceptionStore

    adapter = AsyncMock()
    # Pi-hole has no allow rules
    adapter.get_domain_rules = AsyncMock(return_value=[])
    adapter.add_domain_rule = AsyncMock()

    store = ExceptionStore(data_dir=tmp_path)
    exc = store.grant("twitter.com", "DMs", ttl_seconds=3600)

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        exception_store=store,
        exception_group_id=3,
    )

    await compensate_exceptions(state)

    # Should have re-added the missing allow rule
    adapter.add_domain_rule.assert_called_once()
    call_kw = adapter.add_domain_rule.call_args.kwargs
    assert call_kw["rule_type"] == "allow"
    assert call_kw["domain"] == exc.regex_pattern


@pytest.mark.anyio
async def test_compensation_removes_orphaned_permitato_rules(tmp_path):
    from apps.permitato.state import PermitState, compensate_exceptions
    from apps.permitato.exceptions import ExceptionStore

    adapter = AsyncMock()
    # Pi-hole has a Permitato-owned orphaned rule
    adapter.get_domain_rules = AsyncMock(return_value=[
        {"domain": r"(^|\.)old\.com$", "groups": [3], "comment": "Permitato: old"},
    ])
    adapter.delete_domain_rule = AsyncMock()

    store = ExceptionStore(data_dir=tmp_path)
    # Store is empty — no active exceptions

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        exception_store=store,
        exception_group_id=3,
    )

    await compensate_exceptions(state)

    adapter.delete_domain_rule.assert_called_once()


@pytest.mark.anyio
async def test_compensation_skips_non_permitato_rules(tmp_path):
    from apps.permitato.state import PermitState, compensate_exceptions
    from apps.permitato.exceptions import ExceptionStore

    adapter = AsyncMock()
    # Pi-hole has rules not owned by Permitato — different group or no comment
    adapter.get_domain_rules = AsyncMock(return_value=[
        {"domain": r"(^|\.)manual\.com$", "groups": [99], "comment": "user rule"},
        {"domain": r"(^|\.)other\.com$", "groups": [3], "comment": "not Permitato"},
        {"domain": r"(^|\.)bare\.com$", "groups": [3]},
    ])
    adapter.delete_domain_rule = AsyncMock()
    adapter.add_domain_rule = AsyncMock()

    store = ExceptionStore(data_dir=tmp_path)

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        exception_store=store,
        exception_group_id=3,
    )

    await compensate_exceptions(state)

    # None should be deleted — they're not Permitato-owned
    adapter.delete_domain_rule.assert_not_called()


@pytest.mark.anyio
async def test_compensation_noop_when_in_sync(tmp_path):
    from apps.permitato.state import PermitState, compensate_exceptions
    from apps.permitato.exceptions import ExceptionStore

    adapter = AsyncMock()

    store = ExceptionStore(data_dir=tmp_path)
    exc = store.grant("twitter.com", "DMs", ttl_seconds=3600)

    # Pi-hole has the matching Permitato-owned rule
    adapter.get_domain_rules = AsyncMock(return_value=[
        {"domain": exc.regex_pattern, "groups": [3], "comment": "Permitato: DMs"},
    ])
    adapter.add_domain_rule = AsyncMock()
    adapter.delete_domain_rule = AsyncMock()

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        exception_store=store,
        exception_group_id=3,
    )

    await compensate_exceptions(state)

    adapter.add_domain_rule.assert_not_called()
    adapter.delete_domain_rule.assert_not_called()


@pytest.mark.anyio
async def test_compensation_noop_without_exception_group(tmp_path):
    from apps.permitato.state import PermitState, compensate_exceptions
    from apps.permitato.exceptions import ExceptionStore

    adapter = AsyncMock()
    store = ExceptionStore(data_dir=tmp_path)

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        exception_store=store,
        exception_group_id=0,  # no exception group
    )

    await compensate_exceptions(state)

    adapter.get_domain_rules.assert_not_called()


# ---------------------------------------------------------------------------
# Custom list compensation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_compensate_custom_lists_readds_missing(tmp_path):
    from apps.permitato.state import PermitState, compensate_custom_lists
    from apps.permitato.custom_lists import CustomListStore

    adapter = AsyncMock()
    adapter.get_domain_rules = AsyncMock(return_value=[])
    adapter.add_domain_rule = AsyncMock()

    store = CustomListStore(data_dir=tmp_path)
    store.add("work", "facebook.com")

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        custom_list_store=store,
        group_map={"permitato_work": 1, "permitato_sfw": 2},
    )

    await compensate_custom_lists(state)

    adapter.add_domain_rule.assert_called_once()
    call_kw = adapter.add_domain_rule.call_args.kwargs
    assert call_kw["rule_type"] == "deny"
    assert call_kw["comment"].startswith("Permitato-custom:")


@pytest.mark.anyio
async def test_compensate_custom_lists_removes_orphaned(tmp_path):
    from apps.permitato.state import PermitState, compensate_custom_lists
    from apps.permitato.custom_lists import CustomListStore

    adapter = AsyncMock()
    adapter.get_domain_rules = AsyncMock(return_value=[
        {"domain": r"(^|\.)old\.com$", "groups": [1], "comment": "Permitato-custom: old.com"},
    ])
    adapter.delete_domain_rule = AsyncMock()

    store = CustomListStore(data_dir=tmp_path)

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        custom_list_store=store,
        group_map={"permitato_work": 1, "permitato_sfw": 2},
    )

    await compensate_custom_lists(state)

    adapter.delete_domain_rule.assert_called_once()


@pytest.mark.anyio
async def test_compensate_custom_lists_skips_builtin_rules(tmp_path):
    from apps.permitato.state import PermitState, compensate_custom_lists
    from apps.permitato.custom_lists import CustomListStore

    adapter = AsyncMock()
    adapter.get_domain_rules = AsyncMock(return_value=[
        {"domain": r"(^|\.)facebook\.com$", "groups": [1], "comment": "Permitato: work mode"},
    ])
    adapter.delete_domain_rule = AsyncMock()
    adapter.add_domain_rule = AsyncMock()

    store = CustomListStore(data_dir=tmp_path)

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        custom_list_store=store,
        group_map={"permitato_work": 1, "permitato_sfw": 2},
    )

    await compensate_custom_lists(state)

    adapter.delete_domain_rule.assert_not_called()
    adapter.add_domain_rule.assert_not_called()


@pytest.mark.anyio
async def test_compensate_custom_lists_noop_when_in_sync(tmp_path):
    from apps.permitato.state import PermitState, compensate_custom_lists
    from apps.permitato.custom_lists import CustomListStore
    from apps.permitato.exceptions import build_domain_regex

    adapter = AsyncMock()
    regex = build_domain_regex("facebook.com")
    adapter.get_domain_rules = AsyncMock(return_value=[
        {"domain": regex, "groups": [1], "comment": "Permitato-custom: facebook.com"},
    ])
    adapter.delete_domain_rule = AsyncMock()
    adapter.add_domain_rule = AsyncMock()

    store = CustomListStore(data_dir=tmp_path)
    store.add("work", "facebook.com")

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        custom_list_store=store,
        group_map={"permitato_work": 1, "permitato_sfw": 2},
    )

    await compensate_custom_lists(state)

    adapter.delete_domain_rule.assert_not_called()
    adapter.add_domain_rule.assert_not_called()


# ---------------------------------------------------------------------------
# Audit rotation
# ---------------------------------------------------------------------------


def test_audit_rotation_keeps_last_n(tmp_path):
    from apps.permitato.audit import write_audit_entry, rotate_audit_log

    for i in range(100):
        write_audit_entry(tmp_path, {"event": f"event_{i}"})

    removed = rotate_audit_log(tmp_path, max_lines=30)
    assert removed == 70

    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 30
    # Most recent entry should be last
    assert json.loads(lines[-1])["event"] == "event_99"
    # Oldest kept should be event_70
    assert json.loads(lines[0])["event"] == "event_70"


def test_audit_rotation_noop_under_threshold(tmp_path):
    from apps.permitato.audit import write_audit_entry, rotate_audit_log

    for i in range(10):
        write_audit_entry(tmp_path, {"event": f"event_{i}"})

    removed = rotate_audit_log(tmp_path, max_lines=100)
    assert removed == 0

    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 10


def test_audit_rotation_leaves_no_tmp_files(tmp_path):
    from apps.permitato.audit import write_audit_entry, rotate_audit_log

    for i in range(50):
        write_audit_entry(tmp_path, {"event": f"event_{i}"})

    rotate_audit_log(tmp_path, max_lines=10)
    assert list(tmp_path.glob("*.tmp")) == []


def test_audit_rotation_handles_empty_log(tmp_path):
    from apps.permitato.audit import rotate_audit_log

    removed = rotate_audit_log(tmp_path, max_lines=100)
    assert removed == 0


# ---------------------------------------------------------------------------
# Domain validation hardening
# ---------------------------------------------------------------------------


def test_domain_rejects_whitespace():
    from apps.permitato.exceptions import build_domain_regex

    with pytest.raises(ValueError):
        build_domain_regex("twitter .com")

    with pytest.raises(ValueError):
        build_domain_regex("twitter.com\n")


# ---------------------------------------------------------------------------
# Degraded status tracking
# ---------------------------------------------------------------------------


def test_state_has_degraded_since_field():
    from apps.permitato.state import PermitState

    state = PermitState()
    assert state.degraded_since is None


def test_state_degraded_since_tracks_timestamp():
    from apps.permitato.state import PermitState

    now = time.time()
    state = PermitState(degraded_since=now)
    assert state.degraded_since == now


# ---------------------------------------------------------------------------
# Pi-hole adapter: get_domain_rules
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_adapter_get_domain_rules():
    from apps.permitato.pihole_adapter import PiholeAdapter

    adapter = PiholeAdapter(base_url="http://pihole.test:8081/api", password="testpw")
    adapter._sid = "sid1"

    with respx.mock() as router:
        router.get("http://pihole.test:8081/api/domains/allow/regex").mock(
            return_value=Response(200, json={
                "domains": [
                    {"domain": r"(^|\.)twitter\.com$", "groups": [3]},
                    {"domain": r"(^|\.)reddit\.com$", "groups": [3]},
                ],
            })
        )
        result = await adapter.get_domain_rules("allow", "regex")

    assert len(result) == 2
    assert result[0]["domain"] == r"(^|\.)twitter\.com$"
    await adapter.disconnect()


# ---------------------------------------------------------------------------
# DNS cache flush safety
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flush_dns_cache_safe_calls_adapter(tmp_path):
    from apps.permitato.state import PermitState, flush_dns_cache_safe

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
    )

    await flush_dns_cache_safe(state)

    adapter.flush_dns_cache.assert_awaited_once()


@pytest.mark.anyio
async def test_flush_dns_cache_safe_skips_when_unavailable(tmp_path):
    from apps.permitato.state import PermitState, flush_dns_cache_safe

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=False,
    )

    await flush_dns_cache_safe(state)

    adapter.flush_dns_cache.assert_not_awaited()


@pytest.mark.anyio
async def test_flush_dns_cache_safe_skips_when_no_adapter(tmp_path):
    from apps.permitato.state import PermitState, flush_dns_cache_safe

    state = PermitState(data_dir=tmp_path, adapter=None, pihole_available=True)

    await flush_dns_cache_safe(state)  # no error


@pytest.mark.anyio
async def test_flush_dns_cache_safe_swallows_pihole_error(tmp_path):
    from apps.permitato.pihole_adapter import PiholeUnavailableError
    from apps.permitato.state import PermitState, flush_dns_cache_safe

    adapter = AsyncMock()
    adapter.flush_dns_cache = AsyncMock(side_effect=PiholeUnavailableError("fail"))
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
    )

    await flush_dns_cache_safe(state)  # must not raise


@pytest.mark.anyio
async def test_flush_dns_cache_safe_swallows_unexpected_error(tmp_path):
    from apps.permitato.state import PermitState, flush_dns_cache_safe

    adapter = AsyncMock()
    adapter.flush_dns_cache = AsyncMock(side_effect=RuntimeError("boom"))
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
    )

    await flush_dns_cache_safe(state)  # must not raise


# ---------------------------------------------------------------------------
# DNS cache flush — route integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_grant_exception_flushes_dns_cache(tmp_path):
    from apps.permitato.exceptions import ExceptionStore
    from apps.permitato.state import PermitState

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        exception_store=ExceptionStore(data_dir=tmp_path),
        exception_group_id=3,
        mode="normal",
    )

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from apps.permitato.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/exceptions", json={"domain": "twitter.com", "reason": "DMs"})

    assert resp.status_code == 200
    adapter.flush_dns_cache.assert_awaited_once()


@pytest.mark.anyio
async def test_revoke_exception_flushes_dns_cache(tmp_path):
    from apps.permitato.exceptions import ExceptionStore
    from apps.permitato.state import PermitState

    adapter = AsyncMock()
    store = ExceptionStore(data_dir=tmp_path)
    exc = store.grant("twitter.com", "DMs", ttl_seconds=3600)

    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        exception_store=store,
        exception_group_id=3,
        mode="normal",
    )

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from apps.permitato.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/exceptions/{exc.id}")

    assert resp.status_code == 200
    adapter.flush_dns_cache.assert_awaited_once()


@pytest.mark.anyio
async def test_mode_switch_flushes_dns_cache(tmp_path):
    from apps.permitato.state import PermitState

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        mode="normal",
        client_id="192.168.1.10",
        group_map={"permitato_work": 1, "permitato_sfw": 2},
    )

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from apps.permitato.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/mode", json={"mode": "work"})

    assert resp.status_code == 200
    adapter.flush_dns_cache.assert_awaited()


@pytest.mark.anyio
async def test_flush_failure_does_not_fail_grant(tmp_path):
    from apps.permitato.exceptions import ExceptionStore
    from apps.permitato.pihole_adapter import PiholeUnavailableError
    from apps.permitato.state import PermitState

    adapter = AsyncMock()
    adapter.flush_dns_cache = AsyncMock(side_effect=PiholeUnavailableError("fail"))
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        exception_store=ExceptionStore(data_dir=tmp_path),
        exception_group_id=3,
        mode="normal",
    )

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from apps.permitato.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/exceptions", json={"domain": "twitter.com", "reason": "DMs"})

    assert resp.status_code == 200
    assert "exception" in resp.json()


@pytest.mark.anyio
async def test_set_client_flushes_dns_cache(tmp_path):
    from apps.permitato.state import PermitState

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        mode="work",
        group_map={"permitato_work": 1, "permitato_sfw": 2},
    )

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from apps.permitato.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/client", json={"client_id": "192.168.1.50"})

    assert resp.status_code == 200
    adapter.flush_dns_cache.assert_awaited()


@pytest.mark.anyio
async def test_set_client_resets_bypass_and_rechecks(tmp_path):
    from apps.permitato.state import PermitState

    adapter = AsyncMock()
    adapter.get_network_devices = AsyncMock(return_value=[])
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        mode="work",
        client_id="192.168.1.10",
        blocking_bypassed=True,  # stale flag from previous client
        group_map={"permitato_work": 1, "permitato_sfw": 2},
    )

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from apps.permitato.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/client", json={"client_id": "192.168.1.50"})

    assert resp.status_code == 200
    assert state.blocking_bypassed is False
    adapter.get_network_devices.assert_awaited()
