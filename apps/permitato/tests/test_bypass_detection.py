"""Tests for DNS bypass detection — pure logic, state integration, and status API."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Pure detection logic: check_dns_bypass()
# ---------------------------------------------------------------------------


def _make_device(client_ip: str, last_query: float, last_seen: float, extra_ips=None):
    """Build a fake Pi-hole network device entry."""
    ips = [{"ip": client_ip, "lastSeen": last_seen, "name": None}]
    if extra_ips:
        ips.extend(extra_ips)
    return {
        "id": 1,
        "hwaddr": "aa:bb:cc:dd:ee:ff",
        "lastQuery": last_query,
        "numQueries": 100,
        "ips": ips,
    }


def test_bypass_detected_when_seen_but_no_queries():
    from apps.permitato.state import check_dns_bypass

    now = 1_700_000_000
    devices = [_make_device("192.168.1.10", last_query=now - 1388, last_seen=now - 60)]
    assert check_dns_bypass(devices, "192.168.1.10", now=now) is True


def test_no_bypass_when_queries_fresh():
    from apps.permitato.state import check_dns_bypass

    now = 1_700_000_000
    devices = [_make_device("192.168.1.10", last_query=now - 30, last_seen=now - 20)]
    assert check_dns_bypass(devices, "192.168.1.10", now=now) is False


def test_no_bypass_when_device_idle():
    from apps.permitato.state import check_dns_bypass

    now = 1_700_000_000
    # Both stale — device is genuinely offline/idle
    devices = [_make_device("192.168.1.10", last_query=now - 600, last_seen=now - 600)]
    assert check_dns_bypass(devices, "192.168.1.10", now=now) is False


def test_no_bypass_when_client_not_in_devices():
    from apps.permitato.state import check_dns_bypass

    now = 1_700_000_000
    devices = [_make_device("192.168.1.99", last_query=now - 10, last_seen=now - 10)]
    assert check_dns_bypass(devices, "192.168.1.10", now=now) is None


def test_no_bypass_when_devices_empty():
    from apps.permitato.state import check_dns_bypass

    assert check_dns_bypass([], "192.168.1.10", now=1_700_000_000) is None


def test_bypass_checks_correct_ip_in_ips_array():
    from apps.permitato.state import check_dns_bypass

    now = 1_700_000_000
    # Device has multiple IPs — target IP is fresh, other is stale
    device = _make_device(
        "192.168.1.10", last_query=now - 600, last_seen=now - 30,
        extra_ips=[{"ip": "10.0.0.5", "lastSeen": now - 900, "name": None}],
    )
    assert check_dns_bypass([device], "192.168.1.10", now=now) is True


def test_bypass_with_zero_last_query():
    from apps.permitato.state import check_dns_bypass

    now = 1_700_000_000
    # Device never queried Pi-hole but is on the network
    devices = [_make_device("192.168.1.10", last_query=0, last_seen=now - 30)]
    assert check_dns_bypass(devices, "192.168.1.10", now=now) is True


def test_bypass_matches_mac_based_client_id():
    from apps.permitato.state import check_dns_bypass

    now = 1_700_000_000
    device = _make_device("192.168.1.10", last_query=now - 600, last_seen=now - 30)
    device["hwaddr"] = "aa:bb:cc:dd:ee:ff"
    assert check_dns_bypass([device], "AA:BB:CC:DD:EE:FF", now=now) is True


def test_no_bypass_mac_client_with_fresh_queries():
    from apps.permitato.state import check_dns_bypass

    now = 1_700_000_000
    device = _make_device("192.168.1.10", last_query=now - 30, last_seen=now - 20)
    device["hwaddr"] = "aa:bb:cc:dd:ee:ff"
    assert check_dns_bypass([device], "aa:bb:cc:dd:ee:ff", now=now) is False


def test_mac_match_uses_freshest_ip_last_seen():
    from apps.permitato.state import check_dns_bypass

    now = 1_700_000_000
    device = {
        "id": 1,
        "hwaddr": "aa:bb:cc:dd:ee:ff",
        "lastQuery": now - 600,
        "ips": [
            {"ip": "192.168.1.10", "lastSeen": now - 900, "name": None},
            {"ip": "192.168.1.11", "lastSeen": now - 30, "name": None},
        ],
    }
    # Freshest lastSeen (30s ago) is fresh, lastQuery is stale → bypass
    assert check_dns_bypass([device], "aa:bb:cc:dd:ee:ff", now=now) is True


# ---------------------------------------------------------------------------
# State integration: update_bypass_status()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_bypass_sets_flag_on_detection(tmp_path):
    from apps.permitato.state import PermitState, update_bypass_status

    now = time.time()
    adapter = AsyncMock()
    adapter.get_network_devices = AsyncMock(return_value=[
        _make_device("192.168.1.10", last_query=now - 600, last_seen=now - 30),
    ])
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        client_id="192.168.1.10",
    )

    await update_bypass_status(state)

    assert state.blocking_bypassed is True
    assert state.blocking_last_checked is not None


@pytest.mark.anyio
async def test_update_bypass_clears_flag_when_resolved(tmp_path):
    from apps.permitato.state import PermitState, update_bypass_status

    now = time.time()
    adapter = AsyncMock()
    adapter.get_network_devices = AsyncMock(return_value=[
        _make_device("192.168.1.10", last_query=now - 30, last_seen=now - 20),
    ])
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        client_id="192.168.1.10",
        blocking_bypassed=True,
    )

    await update_bypass_status(state)

    assert state.blocking_bypassed is False


@pytest.mark.anyio
async def test_update_bypass_clears_flag_when_client_not_found(tmp_path):
    from apps.permitato.state import PermitState, update_bypass_status

    adapter = AsyncMock()
    adapter.get_network_devices = AsyncMock(return_value=[
        _make_device("192.168.1.99", last_query=100, last_seen=100),
    ])
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        client_id="192.168.1.10",
        blocking_bypassed=True,  # was bypassed before
    )

    await update_bypass_status(state)

    assert state.blocking_bypassed is False


@pytest.mark.anyio
async def test_update_bypass_skips_when_pihole_unavailable(tmp_path):
    from apps.permitato.state import PermitState, update_bypass_status

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=False,
        client_id="192.168.1.10",
    )

    await update_bypass_status(state)

    adapter.get_network_devices.assert_not_awaited()


@pytest.mark.anyio
async def test_update_bypass_skips_when_no_client_id(tmp_path):
    from apps.permitato.state import PermitState, update_bypass_status

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        client_id="",
    )

    await update_bypass_status(state)

    adapter.get_network_devices.assert_not_awaited()


@pytest.mark.anyio
async def test_update_bypass_swallows_adapter_errors(tmp_path):
    from apps.permitato.pihole_adapter import PiholeUnavailableError
    from apps.permitato.state import PermitState, update_bypass_status

    adapter = AsyncMock()
    adapter.get_network_devices = AsyncMock(side_effect=PiholeUnavailableError("down"))
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        client_id="192.168.1.10",
        blocking_bypassed=False,
    )

    await update_bypass_status(state)  # must not raise

    assert state.blocking_bypassed is False


# ---------------------------------------------------------------------------
# Status API: blocking_bypassed in response
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_status_includes_blocking_bypassed_false(tmp_path):
    from apps.permitato.state import PermitState
    from apps.permitato.exceptions import ExceptionStore

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        client_id="192.168.1.10",
        mode="normal",
        blocking_bypassed=False,
        exception_store=ExceptionStore(data_dir=tmp_path),
    )

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from apps.permitato.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/status")

    assert resp.status_code == 200
    assert resp.json()["blocking_bypassed"] is False


@pytest.mark.anyio
async def test_status_includes_blocking_bypassed_true(tmp_path):
    from apps.permitato.state import PermitState
    from apps.permitato.exceptions import ExceptionStore

    adapter = AsyncMock()
    state = PermitState(
        data_dir=tmp_path,
        adapter=adapter,
        pihole_available=True,
        client_id="192.168.1.10",
        mode="normal",
        blocking_bypassed=True,
        exception_store=ExceptionStore(data_dir=tmp_path),
    )

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from apps.permitato.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/status")

    assert resp.status_code == 200
    assert resp.json()["blocking_bypassed"] is True
