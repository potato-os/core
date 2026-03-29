"""Permitato audit trail — JSONL logging for mode switches and exception decisions."""

from __future__ import annotations

import json
import time
from pathlib import Path


def write_audit_entry(data_dir: Path, entry: dict) -> None:
    """Append a timestamped JSON-line to the audit log."""
    data_dir.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.time(), **entry}
    log_path = data_dir / "audit.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_audit_log(data_dir: Path, limit: int = 100) -> list[dict]:
    """Read the last N entries from the audit log."""
    log_path = data_dir / "audit.jsonl"
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
