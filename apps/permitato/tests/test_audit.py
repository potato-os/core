"""Tests for Permitato audit trail — JSONL logging."""

from __future__ import annotations

import json


def test_write_creates_file(tmp_path):
    from apps.permitato.audit import write_audit_entry

    write_audit_entry(tmp_path, {"event": "mode_switch", "to_mode": "work"})
    log_path = tmp_path / "audit.jsonl"
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "mode_switch"
    assert "ts" in entry


def test_write_appends(tmp_path):
    from apps.permitato.audit import write_audit_entry

    write_audit_entry(tmp_path, {"event": "mode_switch", "to_mode": "work"})
    write_audit_entry(tmp_path, {"event": "exception_granted", "domain": "twitter.com"})
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2


def test_read_returns_entries(tmp_path):
    from apps.permitato.audit import write_audit_entry, read_audit_log

    for i in range(5):
        write_audit_entry(tmp_path, {"event": f"event_{i}"})

    entries = read_audit_log(tmp_path, limit=3)
    assert len(entries) == 3
    assert entries[-1]["event"] == "event_4"


def test_read_empty_returns_empty(tmp_path):
    from apps.permitato.audit import read_audit_log

    entries = read_audit_log(tmp_path)
    assert entries == []
