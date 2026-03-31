"""Tests for the Pi-hole v6 REST API adapter."""

from __future__ import annotations

import pytest
import respx
from httpx import Response


def _adapter(**kwargs):
    from apps.permitato.pihole_adapter import PiholeAdapter

    defaults = {"base_url": "http://pihole.test:8081/api", "password": "testpw"}
    defaults.update(kwargs)
    return PiholeAdapter(**defaults)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_connect_obtains_sid():
    from apps.permitato.pihole_adapter import PiholeAdapter

    adapter = _adapter()
    with respx.mock(assert_all_called=True) as router:
        router.post("http://pihole.test:8081/api/auth").mock(
            return_value=Response(200, json={
                "session": {"valid": True, "sid": "abc123", "validity": 1800},
            })
        )
        await adapter.connect()
    assert adapter._sid == "abc123"
    await adapter.disconnect()


@pytest.mark.anyio
async def test_disconnect_cleans_session():
    from apps.permitato.pihole_adapter import PiholeAdapter

    adapter = _adapter()
    with respx.mock() as router:
        router.post("http://pihole.test:8081/api/auth").mock(
            return_value=Response(200, json={
                "session": {"valid": True, "sid": "abc123", "validity": 1800},
            })
        )
        router.delete("http://pihole.test:8081/api/auth").mock(
            return_value=Response(204)
        )
        await adapter.connect()
        await adapter.disconnect()
    assert adapter._sid is None


@pytest.mark.anyio
async def test_request_attaches_sid_header():
    adapter = _adapter()
    with respx.mock() as router:
        router.post("http://pihole.test:8081/api/auth").mock(
            return_value=Response(200, json={
                "session": {"valid": True, "sid": "mysid", "validity": 1800},
            })
        )
        route = router.get("http://pihole.test:8081/api/groups").mock(
            return_value=Response(200, json={"groups": []})
        )
        await adapter.connect()
        await adapter.get_groups()

    assert route.called
    assert route.calls[0].request.headers["X-FTL-SID"] == "mysid"
    await adapter.disconnect()


@pytest.mark.anyio
async def test_reconnects_on_401():
    adapter = _adapter()
    call_count = 0

    with respx.mock() as router:
        def auth_response(request):
            return Response(200, json={
                "session": {"valid": True, "sid": f"sid-{call_count}", "validity": 1800},
            })

        router.post("http://pihole.test:8081/api/auth").mock(side_effect=auth_response)

        def groups_response(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Response(401, json={"error": "unauthorized"})
            return Response(200, json={"groups": []})

        router.get("http://pihole.test:8081/api/groups").mock(side_effect=groups_response)

        await adapter.connect()
        result = await adapter.get_groups()

    assert result == []
    await adapter.disconnect()


# ---------------------------------------------------------------------------
# Unavailable
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_raises_unavailable_on_connection_error():
    import httpx
    from apps.permitato.pihole_adapter import PiholeUnavailableError

    adapter = _adapter()
    with respx.mock() as router:
        router.post("http://pihole.test:8081/api/auth").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        with pytest.raises(PiholeUnavailableError):
            await adapter.connect()


@pytest.mark.anyio
async def test_is_healthy_true():
    adapter = _adapter()
    with respx.mock() as router:
        router.post("http://pihole.test:8081/api/auth").mock(
            return_value=Response(200, json={
                "session": {"valid": True, "sid": "sid1", "validity": 1800},
            })
        )
        router.get("http://pihole.test:8081/api/dns/blocking").mock(
            return_value=Response(200, json={"blocking": "enabled"})
        )
        await adapter.connect()
        assert await adapter.is_healthy() is True
    await adapter.disconnect()


@pytest.mark.anyio
async def test_is_healthy_false_on_error():
    adapter = _adapter()
    adapter._sid = "stale"
    with respx.mock() as router:
        router.get("http://pihole.test:8081/api/dns/blocking").mock(
            side_effect=Exception("down")
        )
        assert await adapter.is_healthy() is False


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_group_sends_correct_payload():
    adapter = _adapter()
    adapter._sid = "sid1"
    with respx.mock() as router:
        route = router.post("http://pihole.test:8081/api/groups").mock(
            return_value=Response(201, json={"groups": [{"name": "test_group", "id": 5}]})
        )
        result = await adapter.create_group("test_group")

    assert route.called
    import json
    body = json.loads(route.calls[0].request.content.decode())
    assert body["name"] == "test_group"
    await adapter.disconnect()


# ---------------------------------------------------------------------------
# Domain rules
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_add_domain_rule_with_groups():
    adapter = _adapter()
    adapter._sid = "sid1"
    with respx.mock() as router:
        route = router.post("http://pihole.test:8081/api/domains/deny/regex").mock(
            return_value=Response(201, json={"domains": []})
        )
        await adapter.add_domain_rule(
            domain=r"(^|\.)facebook\.com$",
            rule_type="deny",
            kind="regex",
            groups=[3],
            comment="work mode",
        )

    assert route.called
    import json
    body = json.loads(route.calls[0].request.content.decode())
    assert body["domain"] == r"(^|\.)facebook\.com$"
    assert body["groups"] == [3]
    await adapter.disconnect()


# ---------------------------------------------------------------------------
# DNS cache flush
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flush_dns_cache_calls_restartdns():
    adapter = _adapter()
    adapter._sid = "sid1"
    with respx.mock() as router:
        route = router.post("http://pihole.test:8081/api/action/restartdns").mock(
            return_value=Response(200, json={"status": "restarting"})
        )
        await adapter.flush_dns_cache()

    assert route.called
    assert route.calls[0].request.headers["X-FTL-SID"] == "sid1"
    await adapter.disconnect()


@pytest.mark.anyio
async def test_flush_dns_cache_raises_on_http_error():
    from apps.permitato.pihole_adapter import PiholeUnavailableError

    adapter = _adapter()
    adapter._sid = "sid1"
    with respx.mock() as router:
        router.post("http://pihole.test:8081/api/action/restartdns").mock(
            return_value=Response(500, json={"error": "internal"})
        )
        with pytest.raises(PiholeUnavailableError):
            await adapter.flush_dns_cache()
    await adapter.disconnect()


@pytest.mark.anyio
async def test_flush_dns_cache_raises_on_network_error():
    import httpx
    from apps.permitato.pihole_adapter import PiholeUnavailableError

    adapter = _adapter()
    adapter._sid = "sid1"
    with respx.mock() as router:
        router.post("http://pihole.test:8081/api/action/restartdns").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        with pytest.raises(PiholeUnavailableError):
            await adapter.flush_dns_cache()
    await adapter.disconnect()


# ---------------------------------------------------------------------------
# Network devices
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_network_devices_returns_device_list():
    adapter = _adapter()
    adapter._sid = "sid1"
    fake_devices = [
        {"id": 1, "hwaddr": "aa:bb:cc:dd:ee:ff", "lastQuery": 1700000000, "ips": [{"ip": "192.168.1.10", "lastSeen": 1700000100}]},
        {"id": 2, "hwaddr": "11:22:33:44:55:66", "lastQuery": 1700000050, "ips": [{"ip": "192.168.1.20", "lastSeen": 1700000090}]},
    ]
    with respx.mock() as router:
        router.get("http://pihole.test:8081/api/network/devices").mock(
            return_value=Response(200, json={"devices": fake_devices})
        )
        result = await adapter.get_network_devices()

    assert len(result) == 2
    assert result[0]["hwaddr"] == "aa:bb:cc:dd:ee:ff"
    await adapter.disconnect()


@pytest.mark.anyio
async def test_get_network_devices_returns_empty_on_no_devices():
    adapter = _adapter()
    adapter._sid = "sid1"
    with respx.mock() as router:
        router.get("http://pihole.test:8081/api/network/devices").mock(
            return_value=Response(200, json={"devices": []})
        )
        result = await adapter.get_network_devices()

    assert result == []
    await adapter.disconnect()
