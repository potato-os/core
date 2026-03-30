"""Tests for Permitato attention stats and streaks."""

from __future__ import annotations

import pytest

NOW = 1_700_000_000.0
DAY = 86400


def _entry(event: str, ago: int, **extra) -> dict:
    """Build an audit entry *ago* seconds before NOW."""
    return {"ts": NOW - ago, "event": event, **extra}


# ---------------------------------------------------------------------------
# Focus streak
# ---------------------------------------------------------------------------


def test_streak_empty_returns_zero():
    from apps.permitato.stats import compute_focus_streak

    assert compute_focus_streak([], now=NOW) == 0


def test_streak_grant_today_returns_zero():
    from apps.permitato.stats import compute_focus_streak

    entries = [
        _entry("exception_granted", 3600, domain="twitter.com"),
    ]
    assert compute_focus_streak(entries, now=NOW) == 0


def test_streak_no_grants_today():
    """Grant 2 days ago, denial today → streak = 2 (today + yesterday)."""
    from apps.permitato.stats import compute_focus_streak

    entries = [
        _entry("exception_granted", 2 * DAY, domain="twitter.com"),
        _entry("exception_denied", 3600, domain="reddit.com"),
    ]
    # Grant breaks the streak on day -2; today and yesterday are clean
    assert compute_focus_streak(entries, now=NOW) == 2


def test_streak_gap_days_count():
    """Grant 4 days ago, nothing since → streak covers 4 clean days."""
    from apps.permitato.stats import compute_focus_streak

    entries = [
        _entry("exception_granted", 4 * DAY, domain="twitter.com"),
    ]
    # Grant on day -4; days -3, -2, -1, today are all clean
    assert compute_focus_streak(entries, now=NOW) == 4


def test_streak_only_denials_count_as_focus():
    """Denials today + yesterday, grant 2 days ago → streak = 2."""
    from apps.permitato.stats import compute_focus_streak

    entries = [
        _entry("exception_granted", 2 * DAY, domain="twitter.com"),
        _entry("exception_denied", DAY + 3600, domain="reddit.com"),
        _entry("exception_denied", 3600, domain="reddit.com"),
    ]
    # Grant on day -2; yesterday (denial) and today (denial) are clean
    assert compute_focus_streak(entries, now=NOW) == 2


# ---------------------------------------------------------------------------
# Requests today
# ---------------------------------------------------------------------------


def test_today_mixed():
    from apps.permitato.stats import compute_requests_today

    entries = [
        _entry("exception_granted", 5 * DAY, domain="old.com"),
        _entry("exception_granted", 3600, domain="a.com"),
        _entry("exception_granted", 1800, domain="b.com"),
        _entry("exception_denied", 900, domain="c.com"),
    ]
    result = compute_requests_today(entries, now=NOW)
    assert result == {"granted": 2, "denied": 1}


def test_today_empty():
    from apps.permitato.stats import compute_requests_today

    assert compute_requests_today([], now=NOW) == {"granted": 0, "denied": 0}


def test_today_ignores_other_events():
    from apps.permitato.stats import compute_requests_today

    entries = [
        _entry("mode_switch", 3600, from_mode="normal", to_mode="work"),
        _entry("exception_expired", 1800, domain="x.com"),
    ]
    assert compute_requests_today(entries, now=NOW) == {"granted": 0, "denied": 0}


# ---------------------------------------------------------------------------
# Top domains
# ---------------------------------------------------------------------------


def test_top_domains_ranked():
    from apps.permitato.stats import compute_top_domains

    entries = [
        *[_entry("exception_granted", i * 60, domain="twitter.com") for i in range(5)],
        *[_entry("exception_denied", i * 60, domain="reddit.com") for i in range(3)],
        _entry("exception_granted", 10, domain="youtube.com"),
    ]
    result = compute_top_domains(entries)
    assert len(result) == 3
    assert result[0]["domain"] == "twitter.com"
    assert result[0]["count"] == 5
    assert result[1]["domain"] == "reddit.com"
    assert result[1]["count"] == 3
    assert result[2]["domain"] == "youtube.com"


def test_top_domains_max_three():
    from apps.permitato.stats import compute_top_domains

    entries = [
        _entry("exception_granted", i * 60, domain=f"d{i}.com") for i in range(5)
    ]
    assert len(compute_top_domains(entries)) == 3


def test_top_domains_empty():
    from apps.permitato.stats import compute_top_domains

    assert compute_top_domains([]) == []


def test_top_domains_counts_grants_and_denials():
    from apps.permitato.stats import compute_top_domains

    entries = [
        _entry("exception_granted", 300, domain="x.com"),
        _entry("exception_granted", 200, domain="x.com"),
        _entry("exception_denied", 100, domain="x.com"),
        _entry("exception_denied", 50, domain="x.com"),
        _entry("exception_denied", 30, domain="x.com"),
    ]
    result = compute_top_domains(entries)
    assert result[0] == {"domain": "x.com", "count": 5}


# ---------------------------------------------------------------------------
# Mode duration
# ---------------------------------------------------------------------------


def test_duration_from_recent_switch():
    from apps.permitato.stats import compute_mode_duration

    entries = [
        _entry("mode_switch", 3600, from_mode="normal", to_mode="work"),
    ]
    assert compute_mode_duration(entries, "work", now=NOW) == pytest.approx(3600.0)


def test_duration_none_when_no_switch():
    from apps.permitato.stats import compute_mode_duration

    assert compute_mode_duration([], "normal", now=NOW) is None


def test_duration_uses_most_recent():
    from apps.permitato.stats import compute_mode_duration

    entries = [
        _entry("mode_switch", 7200, from_mode="normal", to_mode="sfw"),
        _entry("mode_switch", 3600, from_mode="sfw", to_mode="work"),
    ]
    assert compute_mode_duration(entries, "work", now=NOW) == pytest.approx(3600.0)


def test_duration_handles_scheduled():
    from apps.permitato.stats import compute_mode_duration

    entries = [
        _entry("scheduled_mode_switch", 1800, from_mode="normal", to_mode="work"),
    ]
    assert compute_mode_duration(entries, "work", now=NOW) == pytest.approx(1800.0)


def test_duration_anchors_to_matching_mode():
    """Duration anchors to the most recent switch into current_mode."""
    from apps.permitato.stats import compute_mode_duration

    entries = [
        _entry("mode_switch", 7200, from_mode="normal", to_mode="work"),
        _entry("mode_switch", 3600, from_mode="work", to_mode="sfw"),
    ]
    # Current mode is "sfw" — anchors to the sfw switch at 3600s ago
    assert compute_mode_duration(entries, "sfw", now=NOW) == pytest.approx(3600.0)
    # Current mode is "work" — anchors to the work switch at 7200s ago
    assert compute_mode_duration(entries, "work", now=NOW) == pytest.approx(7200.0)


# ---------------------------------------------------------------------------
# Deny rate
# ---------------------------------------------------------------------------


def test_deny_rate_sufficient():
    from apps.permitato.stats import compute_deny_rate

    entries = [
        *[_entry("exception_denied", i * 60, domain="x.com") for i in range(6)],
        *[_entry("exception_granted", i * 60, domain="y.com") for i in range(4)],
    ]
    result = compute_deny_rate(entries)
    assert result["rate"] == pytest.approx(0.6)
    assert result["total"] == 10
    assert result["denied"] == 6


def test_deny_rate_insufficient():
    from apps.permitato.stats import compute_deny_rate

    entries = [
        _entry("exception_denied", 300, domain="x.com"),
        _entry("exception_granted", 200, domain="y.com"),
        _entry("exception_denied", 100, domain="z.com"),
    ]
    result = compute_deny_rate(entries)
    assert result["rate"] is None
    assert result["total"] == 3


def test_deny_rate_empty():
    from apps.permitato.stats import compute_deny_rate

    assert compute_deny_rate([]) == {"rate": None, "total": 0, "denied": 0}


# ---------------------------------------------------------------------------
# Data span
# ---------------------------------------------------------------------------


def test_span_multiple_days():
    from apps.permitato.stats import compute_data_span_days

    entries = [
        _entry("mode_switch", 20 * DAY, from_mode="normal", to_mode="work"),
        _entry("mode_switch", 0, from_mode="work", to_mode="normal"),
    ]
    assert compute_data_span_days(entries) == 20


def test_span_empty():
    from apps.permitato.stats import compute_data_span_days

    assert compute_data_span_days([]) == 0


# ---------------------------------------------------------------------------
# compute_stats integration
# ---------------------------------------------------------------------------


def test_compute_stats_all_fields():
    from apps.permitato.stats import compute_stats

    entries = [
        _entry("mode_switch", 2 * DAY, from_mode="normal", to_mode="work"),
        _entry("exception_denied", DAY + 3600, domain="twitter.com"),
        _entry("exception_granted", DAY + 1800, domain="reddit.com"),
        _entry("mode_switch", 3600, from_mode="work", to_mode="normal"),
        _entry("exception_denied", 1800, domain="twitter.com"),
    ]
    result = compute_stats(entries, current_mode="normal", now=NOW)
    assert "focus_streak_days" in result
    assert "requests_today" in result
    assert "top_domains" in result
    assert "mode_duration_seconds" in result
    assert "deny_rate" in result
    assert "data_span_days" in result
    assert isinstance(result["requests_today"], dict)
    assert isinstance(result["top_domains"], list)


def test_compute_stats_empty():
    from apps.permitato.stats import compute_stats

    result = compute_stats([], current_mode="normal", now=NOW)
    assert result["focus_streak_days"] == 0
    assert result["requests_today"] == {"granted": 0, "denied": 0}
    assert result["top_domains"] == []
    assert result["mode_duration_seconds"] is None
    assert result["deny_rate"] == {"rate": None, "total": 0, "denied": 0}
    assert result["data_span_days"] == 0


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stats_endpoint_returns_payload(tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from apps.permitato.audit import write_audit_entry
    from apps.permitato.routes import router
    from apps.permitato.state import PermitState

    write_audit_entry(tmp_path, {"event": "exception_denied", "domain": "twitter.com", "reason": "no"})
    write_audit_entry(tmp_path, {"event": "exception_granted", "domain": "reddit.com", "reason": "ok", "ttl_seconds": 3600, "exception_id": "e1"})

    state = PermitState(data_dir=tmp_path, mode="work")
    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert "focus_streak_days" in data
    assert "requests_today" in data
    assert "top_domains" in data
    assert "deny_rate" in data
    assert data["data_span_days"] >= 0


@pytest.mark.anyio
async def test_stats_endpoint_empty(tmp_path):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from apps.permitato.routes import router
    from apps.permitato.state import PermitState

    state = PermitState(data_dir=tmp_path, mode="normal")
    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert data["focus_streak_days"] == 0
    assert data["top_domains"] == []


@pytest.mark.anyio
async def test_stats_endpoint_503_when_not_initialized():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from apps.permitato.routes import router

    app = FastAPI()
    app.include_router(router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/stats")

    assert resp.status_code == 503
