"""Permitato attention stats — lightweight focus metrics from audit history."""

from __future__ import annotations

import time
from collections import Counter
from datetime import date, datetime


_DECISION_EVENTS = frozenset({"exception_granted", "exception_denied"})
_MODE_EVENTS = frozenset({"mode_switch", "scheduled_mode_switch"})


def _calendar_date(ts: float) -> date:
    """Convert a unix timestamp to a local calendar date."""
    return datetime.fromtimestamp(ts).date()


def compute_focus_streak(entries: list[dict], now: float) -> int:
    """Consecutive calendar days ending today with no exception_granted events.

    Days with only denials or no activity count as focused.
    Returns 0 when there are no entries (no data = no meaningful streak).
    """
    if not entries:
        return 0

    grant_dates: set[date] = set()
    earliest = _calendar_date(entries[0]["ts"])
    for e in entries:
        if e.get("event") == "exception_granted":
            grant_dates.add(_calendar_date(e["ts"]))

    today = _calendar_date(now)
    streak = 0
    d = today
    while d not in grant_dates and d >= earliest:
        streak += 1
        d = date.fromordinal(d.toordinal() - 1)
    return streak


def compute_requests_today(entries: list[dict], now: float) -> dict:
    """Count granted and denied exceptions for today's calendar date."""
    today = _calendar_date(now)
    granted = 0
    denied = 0
    for e in entries:
        if _calendar_date(e["ts"]) != today:
            continue
        event = e.get("event")
        if event == "exception_granted":
            granted += 1
        elif event == "exception_denied":
            denied += 1
    return {"granted": granted, "denied": denied}


def compute_top_domains(
    entries: list[dict], max_domains: int = 3
) -> list[dict]:
    """Top N domains by combined grant+deny count."""
    counts: Counter[str] = Counter()
    for e in entries:
        if e.get("event") in _DECISION_EVENTS:
            domain = e.get("domain")
            if domain:
                counts[domain] += 1
    return [
        {"domain": domain, "count": count}
        for domain, count in counts.most_common(max_domains)
    ]


def compute_mode_duration(
    entries: list[dict], current_mode: str, now: float
) -> float | None:
    """Seconds since the most recent switch into *current_mode*. None if not found."""
    for e in reversed(entries):
        if e.get("event") in _MODE_EVENTS and e.get("to_mode") == current_mode:
            return now - e["ts"]
    return None


def compute_deny_rate(entries: list[dict]) -> dict:
    """Deny rate across all decisions. Rate is None when total < 5."""
    denied = 0
    total = 0
    for e in entries:
        if e.get("event") in _DECISION_EVENTS:
            total += 1
            if e["event"] == "exception_denied":
                denied += 1
    rate = denied / total if total >= 5 else None
    return {"rate": rate, "total": total, "denied": denied}


def compute_data_span_days(entries: list[dict]) -> int:
    """Calendar days between oldest and newest entry."""
    if len(entries) < 2:
        return 0
    oldest = _calendar_date(entries[0]["ts"])
    newest = _calendar_date(entries[-1]["ts"])
    return (newest - oldest).days


def compute_stats(
    entries: list[dict],
    current_mode: str,
    now: float | None = None,
) -> dict:
    """Aggregate all stats into a single dict for the API response."""
    if now is None:
        now = time.time()
    return {
        "focus_streak_days": compute_focus_streak(entries, now),
        "requests_today": compute_requests_today(entries, now),
        "top_domains": compute_top_domains(entries),
        "mode_duration_seconds": compute_mode_duration(entries, current_mode, now),
        "deny_rate": compute_deny_rate(entries),
        "data_span_days": compute_data_span_days(entries),
    }
