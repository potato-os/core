"""Tests for Permitato mode definitions and group mapping."""

from __future__ import annotations


def test_get_mode_returns_definition():
    from apps.permitato.modes import get_mode

    mode = get_mode("work")
    assert mode.name == "work"
    assert mode.display_name == "Work"
    assert mode.group_name == "permitato_work"


def test_get_mode_all_three():
    from apps.permitato.modes import get_mode

    for name in ("normal", "work", "sfw"):
        mode = get_mode(name)
        assert mode.name == name


def test_get_mode_raises_for_unknown():
    from apps.permitato.modes import get_mode
    import pytest

    with pytest.raises(ValueError):
        get_mode("invalid")


def test_modes_are_mutually_exclusive():
    from apps.permitato.modes import MODES

    group_names = [m.group_name for m in MODES.values() if m.group_name]
    assert len(group_names) == len(set(group_names)), "Group names must be unique"


def test_normal_mode_has_no_group():
    from apps.permitato.modes import get_mode

    mode = get_mode("normal")
    assert mode.group_name == ""


def test_work_deny_domains_not_empty():
    from apps.permitato.modes import WORK_DENY_DOMAINS

    assert len(WORK_DENY_DOMAINS) > 0
    assert any("facebook" in d for d in WORK_DENY_DOMAINS)


def test_sfw_deny_domains_not_empty():
    from apps.permitato.modes import SFW_DENY_DOMAINS

    assert len(SFW_DENY_DOMAINS) > 0


def test_domain_regex_format():
    """All deny domain patterns should be valid Pi-hole regex format."""
    from apps.permitato.modes import WORK_DENY_DOMAINS, SFW_DENY_DOMAINS
    import re

    for domain in (*WORK_DENY_DOMAINS, *SFW_DENY_DOMAINS):
        re.compile(domain)  # should not raise
