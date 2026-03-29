"""Tests for LLM response intent parsing and action marker extraction."""

from __future__ import annotations


def test_extract_switch_mode_work():
    from apps.permitato.intent import parse_llm_response

    intent = parse_llm_response(
        "Switching to work mode now. [ACTION:switch_mode:work]"
    )
    assert intent.action == "switch_mode"
    assert intent.params["mode"] == "work"


def test_extract_switch_mode_normal():
    from apps.permitato.intent import parse_llm_response

    intent = parse_llm_response("Back to normal. [ACTION:switch_mode:normal]")
    assert intent.action == "switch_mode"
    assert intent.params["mode"] == "normal"


def test_extract_request_unblock_with_reason():
    from apps.permitato.intent import parse_llm_response

    intent = parse_llm_response(
        "Sure, I'll unblock that. [ACTION:request_unblock:twitter.com:need to check work DMs]"
    )
    assert intent.action == "request_unblock"
    assert intent.params["domain"] == "twitter.com"
    assert intent.params["reason"] == "need to check work DMs"


def test_extract_deny_unblock():
    from apps.permitato.intent import parse_llm_response

    intent = parse_llm_response(
        "I don't think that's a good idea right now. [ACTION:deny_unblock:reddit.com:not work-related]"
    )
    assert intent.action == "deny_unblock"
    assert intent.params["domain"] == "reddit.com"


def test_parse_returns_none_for_plain_chat():
    from apps.permitato.intent import parse_llm_response

    intent = parse_llm_response("Just chatting about the weather!")
    assert intent.action == "none"
    assert intent.params == {}


def test_strip_markers_removes_action_tags():
    from apps.permitato.intent import strip_action_markers

    text = "Here you go! [ACTION:switch_mode:work] Enjoy."
    assert strip_action_markers(text) == "Here you go!  Enjoy."


def test_strip_markers_preserves_rest_of_text():
    from apps.permitato.intent import strip_action_markers

    text = "No actions here, just talking."
    assert strip_action_markers(text) == text


def test_fallback_detects_mode_switch_keywords():
    from apps.permitato.intent import parse_llm_response

    intent = parse_llm_response("Okay, I'm switching you to work mode now.")
    assert intent.action == "switch_mode"
    assert intent.params["mode"] == "work"


def test_fallback_detects_unblock_grant():
    from apps.permitato.intent import parse_llm_response

    intent = parse_llm_response(
        "That sounds reasonable. I'll unblock twitter.com for the next hour."
    )
    assert intent.action == "request_unblock"
    assert intent.params["domain"] == "twitter.com"


def test_marker_takes_priority_over_fallback():
    from apps.permitato.intent import parse_llm_response

    intent = parse_llm_response(
        "Switching to sfw mode. [ACTION:switch_mode:sfw]"
    )
    assert intent.action == "switch_mode"
    assert intent.params["mode"] == "sfw"
