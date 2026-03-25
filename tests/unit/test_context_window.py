"""Unit tests for context_window_runner pure-logic functions.

Tests the conversation builder, request builder, system metrics parsers,
and failure detectors used by the context window characterization runner.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The runner lives in tests/e2e/ — add it to the import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))

from context_window_runner import (
    ConversationBuilder,
    build_chat_request,
    detect_context_shift,
    detect_severe_ttft_degradation,
    detect_swap_thrashing,
    detect_thermal_throttle,
    parse_available_memory,
    parse_cpu_temp,
    parse_rss_from_ps,
    parse_swap_from_free,
    parse_zram_mm_stat,
    should_abort,
)


# ── ConversationBuilder ──────────────────────────────────────────────────


class TestConversationBuilder:
    def test_initial_state_has_system_prompt_only(self):
        b = ConversationBuilder()
        msgs = b.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"

    def test_add_user_turn_returns_messages_with_user(self):
        b = ConversationBuilder()
        msgs = b.add_user_turn(1)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_second_turn_includes_assistant_response(self):
        b = ConversationBuilder()
        b.add_user_turn(1)
        b.add_assistant_response("The lighthouse keeper gazed out to sea.")
        msgs = b.add_user_turn(2)
        assert len(msgs) == 4
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"
        assert msgs[2]["content"] == "The lighthouse keeper gazed out to sea."
        assert msgs[3]["role"] == "user"

    def test_prompts_cycle_through_list(self):
        b = ConversationBuilder()
        msg1 = b.add_user_turn(1)
        content_turn1 = msg1[-1]["content"]

        # Reset and try turn 11 — should cycle back to same prompt
        b2 = ConversationBuilder()
        msg11 = b2.add_user_turn(11)
        content_turn11 = msg11[-1]["content"]

        assert content_turn1 == content_turn11

    def test_different_turns_get_different_prompts(self):
        b = ConversationBuilder()
        msg1 = b.add_user_turn(1)
        b.add_assistant_response("Response one.")
        msg2 = b.add_user_turn(2)
        assert msg1[-1]["content"] != msg2[-1]["content"]

    def test_estimated_tokens_grows_after_turns(self):
        b = ConversationBuilder()
        est0 = b.estimated_tokens()
        b.add_user_turn(1)
        b.add_assistant_response("A" * 4000)  # ~1000 tokens
        est1 = b.estimated_tokens()
        assert est1 > est0

    def test_total_messages_count(self):
        b = ConversationBuilder()
        assert b.total_messages() == 1  # system only
        b.add_user_turn(1)
        assert b.total_messages() == 2
        b.add_assistant_response("Response.")
        assert b.total_messages() == 3
        b.add_user_turn(2)
        assert b.total_messages() == 4


# ── build_chat_request ───────────────────────────────────────────────────


class TestBuildChatRequest:
    def test_has_required_fields(self):
        msgs = [{"role": "system", "content": "Hi"}]
        req = build_chat_request(msgs)
        assert req["model"] == "qwen-local"
        assert req["stream"] is True
        assert req["temperature"] == 0
        assert req["seed"] == 42
        assert req["max_tokens"] == 1024
        assert req["messages"] is msgs

    def test_cache_prompt_enabled(self):
        req = build_chat_request([{"role": "system", "content": "Hi"}])
        assert req["cache_prompt"] is True

    def test_thinking_disabled(self):
        req = build_chat_request([{"role": "system", "content": "Hi"}])
        assert req["chat_template_kwargs"]["enable_thinking"] is False

    def test_deterministic_settings(self):
        req = build_chat_request([{"role": "system", "content": "Hi"}])
        assert req["temperature"] == 0
        assert req["top_p"] == 1
        assert req["seed"] == 42
        assert req["presence_penalty"] == 0
        assert req["frequency_penalty"] == 0


# ── System metrics parsers ───────────────────────────────────────────────


class TestParseRssFromPs:
    def test_valid_output(self):
        # ps -o rss= returns kilobytes
        assert parse_rss_from_ps("  2148388\n") == 2098

    def test_small_value(self):
        assert parse_rss_from_ps("1024\n") == 1

    def test_empty_returns_none(self):
        assert parse_rss_from_ps("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_rss_from_ps("   \n") is None

    def test_non_numeric_returns_none(self):
        assert parse_rss_from_ps("error: no process\n") is None


class TestParseSwapFromFree:
    def test_valid_free_output(self):
        output = (
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:           7812        3456        1234         128        3122        4100\n"
            "Swap:          2047         512        1535\n"
        )
        result = parse_swap_from_free(output)
        assert result == (512, 2047)

    def test_no_swap_line(self):
        output = (
            "              total        used        free\n"
            "Mem:           7812        3456        1234\n"
        )
        assert parse_swap_from_free(output) is None

    def test_empty_returns_none(self):
        assert parse_swap_from_free("") is None

    def test_zero_swap(self):
        output = (
            "              total        used        free\n"
            "Mem:           7812        3456        1234\n"
            "Swap:          2047           0        2047\n"
        )
        result = parse_swap_from_free(output)
        assert result == (0, 2047)


class TestParseCpuTemp:
    def test_standard_format(self):
        assert parse_cpu_temp("temp=62.5'C\n") == 62.5

    def test_high_temp(self):
        assert parse_cpu_temp("temp=85.0'C\n") == 85.0

    def test_empty_returns_none(self):
        assert parse_cpu_temp("") is None

    def test_malformed_returns_none(self):
        assert parse_cpu_temp("not a temperature\n") is None


class TestParseZramMmStat:
    def test_valid_output(self):
        # Real mm_stat: orig=500MB compr=200MB mem_used=210MB
        stdout = "  524288000  209715200  220200960  0  220200960  0  0  0  0\n"
        result = parse_zram_mm_stat(stdout)
        assert result is not None
        assert result["zram_orig_mb"] == 500
        assert result["zram_compr_mb"] == 200
        assert result["zram_mem_used_mb"] == 210
        assert result["zram_ratio"] == 2.5

    def test_idle_zram(self):
        # Nearly idle: 16KB original, 69 bytes compressed
        stdout = "   16384       69    49152        0    49152        0        0        0        0\n"
        result = parse_zram_mm_stat(stdout)
        assert result is not None
        assert result["zram_orig_mb"] == 0
        assert result["zram_compr_mb"] == 0

    def test_empty_returns_none(self):
        assert parse_zram_mm_stat("") is None

    def test_garbage_returns_none(self):
        assert parse_zram_mm_stat("not numbers\n") is None


class TestParseAvailableMemory:
    def test_valid_free_output(self):
        output = (
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:           7812        3456        1234         128        3122        4100\n"
            "Swap:          2047         512        1535\n"
        )
        assert parse_available_memory(output) == 4100

    def test_empty_returns_none(self):
        assert parse_available_memory("") is None


# ── Failure detectors ────────────────────────────────────────────────────


class TestDetectContextShift:
    def test_detects_context_shift(self):
        lines = [
            "INFO [some_function] normal log",
            "INFO [context_shift] context shift detected",
            "INFO [release_slots] slot released",
        ]
        assert detect_context_shift(lines) is True

    def test_no_context_shift(self):
        lines = [
            "INFO [batch_pending_prompt] kv cache rm [p0, end)",
            "INFO [release_slots] slot released",
        ]
        assert detect_context_shift(lines) is False

    def test_empty_lines(self):
        assert detect_context_shift([]) is False


class TestDetectSevereTtftDegradation:
    def test_above_threshold(self):
        assert detect_severe_ttft_degradation(10.1, baseline=1.0, threshold=10.0) is True

    def test_below_threshold(self):
        assert detect_severe_ttft_degradation(9.9, baseline=1.0, threshold=10.0) is False

    def test_exactly_at_threshold(self):
        assert detect_severe_ttft_degradation(10.0, baseline=1.0, threshold=10.0) is False

    def test_zero_baseline_does_not_crash(self):
        # Should not divide by zero
        result = detect_severe_ttft_degradation(5.0, baseline=0.0, threshold=10.0)
        assert isinstance(result, bool)


class TestDetectSwapThrashing:
    def test_increasing_swap(self):
        assert detect_swap_thrashing([100, 200, 300], window=3) is True

    def test_stable_swap(self):
        assert detect_swap_thrashing([100, 100, 100], window=3) is False

    def test_decreasing_swap(self):
        assert detect_swap_thrashing([300, 200, 100], window=3) is False

    def test_too_few_samples(self):
        assert detect_swap_thrashing([100, 200], window=3) is False

    def test_mixed_trend(self):
        assert detect_swap_thrashing([100, 200, 150], window=3) is False


class TestDetectThermalThrottle:
    def test_above_threshold(self):
        assert detect_thermal_throttle(81.0, threshold=80.0) is True

    def test_below_threshold(self):
        assert detect_thermal_throttle(79.0, threshold=80.0) is False

    def test_at_threshold(self):
        assert detect_thermal_throttle(80.0, threshold=80.0) is False


class TestShouldAbort:
    def test_oom_triggers_abort(self):
        failures = {
            "oom_crash": True,
            "context_shift": False,
            "severe_ttft": False,
            "swap_thrashing": False,
            "thermal_throttle": False,
        }
        abort, reason = should_abort(failures)
        assert abort is True
        assert "oom" in reason.lower()

    def test_thermal_triggers_abort(self):
        failures = {
            "oom_crash": False,
            "context_shift": False,
            "severe_ttft": False,
            "swap_thrashing": False,
            "thermal_throttle": True,
        }
        abort, reason = should_abort(failures)
        assert abort is True
        assert "thermal" in reason.lower()

    def test_ttft_triggers_abort(self):
        failures = {
            "oom_crash": False,
            "context_shift": False,
            "severe_ttft": True,
            "swap_thrashing": False,
            "thermal_throttle": False,
        }
        abort, reason = should_abort(failures)
        assert abort is True

    def test_context_shift_alone_does_not_abort(self):
        failures = {
            "oom_crash": False,
            "context_shift": True,
            "severe_ttft": False,
            "swap_thrashing": False,
            "thermal_throttle": False,
        }
        abort, _ = should_abort(failures)
        assert abort is False

    def test_swap_thrashing_alone_does_not_abort(self):
        failures = {
            "oom_crash": False,
            "context_shift": False,
            "severe_ttft": False,
            "swap_thrashing": True,
            "thermal_throttle": False,
        }
        abort, _ = should_abort(failures)
        assert abort is False

    def test_all_clear(self):
        failures = {
            "oom_crash": False,
            "context_shift": False,
            "severe_ttft": False,
            "swap_thrashing": False,
            "thermal_throttle": False,
        }
        abort, reason = should_abort(failures)
        assert abort is False
        assert reason == ""
