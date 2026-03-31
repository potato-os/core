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


# ---------------------------------------------------------------------------
# Multi-marker: last marker wins
# ---------------------------------------------------------------------------


def test_extract_last_marker_when_multiple_present():
    from apps.permitato.intent import parse_llm_response

    intent = parse_llm_response(
        "Let me switch. [ACTION:switch_mode:work] Actually, sfw is better. [ACTION:switch_mode:sfw]"
    )
    assert intent.action == "switch_mode"
    assert intent.params["mode"] == "sfw"


def test_extract_last_marker_different_actions():
    from apps.permitato.intent import parse_llm_response

    intent = parse_llm_response(
        "I shouldn't unblock that. [ACTION:deny_unblock:youtube.com:distraction] "
        "On second thought, you need it for work. [ACTION:request_unblock:youtube.com:work demo]"
    )
    assert intent.action == "request_unblock"
    assert intent.params["domain"] == "youtube.com"
    assert intent.params["reason"] == "work demo"


# ---------------------------------------------------------------------------
# clean_for_stream: strip complete + partial markers for SSE output
# ---------------------------------------------------------------------------


def test_clean_for_stream_strips_complete_marker():
    from apps.permitato.intent import clean_for_stream

    assert clean_for_stream("Sure! [ACTION:switch_mode:work] Done.") == "Sure!  Done."


def test_clean_for_stream_trims_partial_marker_at_end():
    from apps.permitato.intent import clean_for_stream

    assert clean_for_stream("Sure! [ACTION:request_unbl") == "Sure! "


def test_clean_for_stream_trims_trailing_lone_bracket():
    from apps.permitato.intent import clean_for_stream

    # Trailing [ is trimmed — it could be the start of a marker during streaming.
    # If the next chunk proves otherwise, the bracket reappears in the delta.
    assert clean_for_stream("See [this] and also [") == "See [this] and also "


def test_clean_for_stream_preserves_mid_text_bracket():
    from apps.permitato.intent import clean_for_stream

    assert clean_for_stream("See [this] and also [ more") == "See [this] and also [ more"


def test_clean_for_stream_trims_bracket_a():
    from apps.permitato.intent import clean_for_stream

    assert clean_for_stream("Hello [A") == "Hello "


def test_clean_for_stream_strips_multiple_markers():
    from apps.permitato.intent import clean_for_stream

    text = "First [ACTION:deny_unblock:x.com:no] then [ACTION:request_unblock:x.com:yes] done."
    assert clean_for_stream(text) == "First  then  done."
