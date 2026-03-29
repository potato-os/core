"""Tests for Permitato exception lifecycle — domain-family regex, TTL, persistence."""

from __future__ import annotations

import time
from pathlib import Path

import pytest


def test_build_domain_regex_basic():
    from apps.permitato.exceptions import build_domain_regex

    assert build_domain_regex("twitter.com") == r"(^|\.)twitter\.com$"


def test_build_domain_regex_escapes_dots():
    from apps.permitato.exceptions import build_domain_regex

    regex = build_domain_regex("sub.example.co.uk")
    assert r"sub\.example\.co\.uk" in regex


def test_build_domain_regex_rejects_empty():
    from apps.permitato.exceptions import build_domain_regex

    with pytest.raises(ValueError):
        build_domain_regex("")


def test_build_domain_regex_rejects_bare_tld():
    from apps.permitato.exceptions import build_domain_regex

    with pytest.raises(ValueError):
        build_domain_regex("com")


def test_exception_store_grant_and_list(tmp_path):
    from apps.permitato.exceptions import ExceptionStore, build_domain_regex

    store = ExceptionStore(data_dir=tmp_path)
    exc = store.grant("twitter.com", "need to check DMs", ttl_seconds=3600)

    assert exc.domain == "twitter.com"
    assert exc.regex_pattern == build_domain_regex("twitter.com")
    assert exc.ttl_seconds == 3600
    assert exc.expires_at > exc.granted_at
    assert store.active_count() == 1

    active = store.list_active()
    assert len(active) == 1
    assert active[0]["domain"] == "twitter.com"


def test_exception_store_revoke(tmp_path):
    from apps.permitato.exceptions import ExceptionStore

    store = ExceptionStore(data_dir=tmp_path)
    exc = store.grant("twitter.com", "reason", ttl_seconds=3600)
    store.revoke(exc.id)
    assert store.active_count() == 0


def test_exception_store_revoke_unknown_id(tmp_path):
    from apps.permitato.exceptions import ExceptionStore

    store = ExceptionStore(data_dir=tmp_path)
    with pytest.raises(KeyError):
        store.revoke("nonexistent-id")


def test_cleanup_expired_removes_old(tmp_path):
    from apps.permitato.exceptions import ExceptionStore

    store = ExceptionStore(data_dir=tmp_path)
    exc = store.grant("twitter.com", "reason", ttl_seconds=1)
    # Force expiry
    store._exceptions[exc.id].expires_at = time.time() - 10

    revoked = store.cleanup_expired()
    assert exc.id in revoked
    assert store.active_count() == 0


def test_cleanup_expired_keeps_active(tmp_path):
    from apps.permitato.exceptions import ExceptionStore

    store = ExceptionStore(data_dir=tmp_path)
    store.grant("twitter.com", "reason", ttl_seconds=3600)
    revoked = store.cleanup_expired()
    assert len(revoked) == 0
    assert store.active_count() == 1


def test_persist_and_load(tmp_path):
    from apps.permitato.exceptions import ExceptionStore

    store = ExceptionStore(data_dir=tmp_path)
    store.grant("twitter.com", "DMs", ttl_seconds=3600)
    store.grant("reddit.com", "research", ttl_seconds=1800)
    store.persist()

    store2 = ExceptionStore(data_dir=tmp_path)
    store2.load()
    assert store2.active_count() == 2


def test_load_handles_missing_file(tmp_path):
    from apps.permitato.exceptions import ExceptionStore

    store = ExceptionStore(data_dir=tmp_path)
    store.load()  # should not raise
    assert store.active_count() == 0


def test_load_handles_corrupt_json(tmp_path):
    from apps.permitato.exceptions import ExceptionStore

    (tmp_path / "exceptions.json").write_text("not json!!!")
    store = ExceptionStore(data_dir=tmp_path)
    store.load()  # should not raise
    assert store.active_count() == 0
