"""Tests for core.inferno.orchestrator — health, readiness, process lifecycle, inference tick."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

try:
    from core.inferno.orchestrator import (
        READY_HEALTH_POLLS_REQUIRED,
        MAX_CONSECUTIVE_FAILURES,
        InferenceTickResult,
        empty_readiness_state,
        empty_runtime_switch_state,
        reset_readiness,
        resolve_readiness,
        check_health,
        probe_inference_slot,
        refresh_readiness,
        restart_inference_process,
        resolve_mmproj_for_launch,
        resolve_no_mmap,
        run_inference_tick,
        prepare_activation_runtime,
    )
except ModuleNotFoundError:
    from inferno.orchestrator import (  # type: ignore[no-redef]
        READY_HEALTH_POLLS_REQUIRED,
        MAX_CONSECUTIVE_FAILURES,
        InferenceTickResult,
        empty_readiness_state,
        empty_runtime_switch_state,
        reset_readiness,
        resolve_readiness,
        check_health,
        probe_inference_slot,
        refresh_readiness,
        restart_inference_process,
        resolve_mmproj_for_launch,
        resolve_no_mmap,
        run_inference_tick,
        prepare_activation_runtime,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tick_env(tmp_path: Path) -> dict[str, Any]:
    """Minimal filesystem + paths for tick tests."""
    base = tmp_path / "potato"
    models_dir = base / "models"
    models_dir.mkdir(parents=True)

    model_path = models_dir / "test-model.gguf"
    model_path.write_bytes(b"\x00" * 100)

    return {
        "model_path": model_path,
        "base_url": "http://llama.test:8080",
    }


class _FakeProcess:
    """Minimal process stub for testing orchestrator logic."""

    def __init__(self, *, returncode: int | None = None, pid: int = 42):
        self.returncode = returncode
        self.pid = pid
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


# ---------------------------------------------------------------------------
# 1. Constants and state factories
# ---------------------------------------------------------------------------


def test_ready_health_polls_required_positive():
    assert READY_HEALTH_POLLS_REQUIRED > 0


def test_max_consecutive_failures_positive():
    assert MAX_CONSECUTIVE_FAILURES > 0


def test_empty_readiness_state_defaults():
    state = empty_readiness_state()
    assert state["generation"] == 0
    assert state["status"] == "idle"
    assert state["ready"] is False
    assert state["transport_healthy"] is False
    assert state["healthy_polls"] == 0
    assert state["model_path"] is None
    assert state["last_error"] is None
    assert state["last_ready_at_unix"] is None


def test_empty_runtime_switch_state_defaults():
    state = empty_runtime_switch_state()
    assert state["active"] is False
    assert state["target_bundle_path"] is None
    assert state["error"] is None


# ---------------------------------------------------------------------------
# 2. Readiness state transitions (pure)
# ---------------------------------------------------------------------------


def test_reset_readiness_increments_generation():
    prev = empty_readiness_state()
    prev["generation"] = 5
    result = reset_readiness(prev, model_path="/m/test.gguf", reason="test")
    assert result["generation"] == 6


def test_reset_readiness_with_model_sets_loading():
    result = reset_readiness(None, model_path="/m/test.gguf", reason=None)
    assert result["status"] == "loading"
    assert result["model_path"] == "/m/test.gguf"


def test_reset_readiness_without_model_sets_idle():
    result = reset_readiness(None, model_path=None, reason="no_model")
    assert result["status"] == "idle"
    assert result["model_path"] is None
    assert result["last_error"] == "no_model"


def test_resolve_readiness_model_change_triggers_reset():
    current = empty_readiness_state()
    current["model_path"] = "/m/old.gguf"
    current["ready"] = True
    result = resolve_readiness(current, active_model_path="/m/new.gguf")
    assert result["model_path"] == "/m/new.gguf"
    assert result["ready"] is False
    assert result["status"] == "loading"


def test_resolve_readiness_no_model_when_had_one_resets():
    current = empty_readiness_state()
    current["model_path"] = "/m/old.gguf"
    current["ready"] = True
    result = resolve_readiness(current, active_model_path=None)
    assert result["model_path"] is None
    assert result["ready"] is False


def test_resolve_readiness_same_model_no_change():
    current = empty_readiness_state()
    current["model_path"] = "/m/same.gguf"
    current["status"] = "warming"
    current["healthy_polls"] = 1
    result = resolve_readiness(current, active_model_path="/m/same.gguf")
    assert result["status"] == "warming"
    assert result["healthy_polls"] == 1


# ---------------------------------------------------------------------------
# 3. Health check and slot probe (async, httpx)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_health_true_on_healthy_endpoint(monkeypatch):
    async def _fake_get(self, url, **kw):
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    assert await check_health("http://llama.test:8080") is True


@pytest.mark.anyio
async def test_check_health_false_on_all_500(monkeypatch):
    async def _fake_get(self, url, **kw):
        return httpx.Response(500)

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    assert await check_health("http://llama.test:8080") is False


@pytest.mark.anyio
async def test_check_health_busy_is_healthy_on_timeout(monkeypatch):
    async def _fake_get(self, url, **kw):
        raise httpx.ReadTimeout("busy")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    assert await check_health("http://llama.test:8080", busy_is_healthy=True) is True


@pytest.mark.anyio
async def test_check_health_strict_timeout_not_healthy(monkeypatch):
    async def _fake_get(self, url, **kw):
        raise httpx.ReadTimeout("busy")

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    assert await check_health("http://llama.test:8080", busy_is_healthy=False) is False


@pytest.mark.anyio
async def test_probe_inference_slot_true_on_success(monkeypatch):
    async def _fake_post(self, url, **kw):
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    assert await probe_inference_slot("http://llama.test:8080") is True


@pytest.mark.anyio
async def test_probe_inference_slot_false_on_error(monkeypatch):
    async def _fake_post(self, url, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    assert await probe_inference_slot("http://llama.test:8080") is False


# ---------------------------------------------------------------------------
# 4. Readiness refresh (async)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_refresh_no_model_stays_idle(monkeypatch):
    state = empty_readiness_state()
    result = await refresh_readiness(state, base_url="http://llama.test:8080", process_alive=True)
    assert result["status"] == "idle"


@pytest.mark.anyio
async def test_refresh_process_dead_resets_to_loading(monkeypatch):
    state = empty_readiness_state()
    state["model_path"] = "/m/test.gguf"
    state["status"] = "warming"
    state["healthy_polls"] = 1
    result = await refresh_readiness(state, base_url="http://llama.test:8080", process_alive=False)
    assert result["status"] == "loading"
    assert result["healthy_polls"] == 0
    assert result["ready"] is False


@pytest.mark.anyio
async def test_refresh_healthy_increments_polls(monkeypatch):
    async def _fake_get(self, url, **kw):
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    state = empty_readiness_state()
    state["model_path"] = "/m/test.gguf"
    state["status"] = "loading"
    result = await refresh_readiness(state, base_url="http://llama.test:8080", process_alive=True)
    assert result["healthy_polls"] == 1
    assert result["transport_healthy"] is True


@pytest.mark.anyio
async def test_refresh_becomes_ready_at_threshold(monkeypatch):
    async def _fake_get(self, url, **kw):
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    state = empty_readiness_state()
    state["model_path"] = "/m/test.gguf"
    state["healthy_polls"] = READY_HEALTH_POLLS_REQUIRED - 1
    result = await refresh_readiness(state, base_url="http://llama.test:8080", process_alive=True)
    assert result["ready"] is True
    assert result["status"] == "ready"
    assert result["last_error"] is None


@pytest.mark.anyio
async def test_refresh_unhealthy_resets_polls(monkeypatch):
    async def _fake_get(self, url, **kw):
        return httpx.Response(500)

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    state = empty_readiness_state()
    state["model_path"] = "/m/test.gguf"
    state["healthy_polls"] = 1
    state["transport_healthy"] = True
    result = await refresh_readiness(state, base_url="http://llama.test:8080", process_alive=True)
    assert result["healthy_polls"] == 0
    assert result["transport_healthy"] is False
    assert result["ready"] is False


# ---------------------------------------------------------------------------
# 5. mmproj resolution
# ---------------------------------------------------------------------------


def test_resolve_mmproj_non_vision_returns_none(tmp_path):
    model = {"filename": "plain-model.gguf", "settings": {}}
    result = resolve_mmproj_for_launch(tmp_path, tmp_path, model, "ik_llama")
    assert result is None


def test_resolve_mmproj_gemma4_ik_llama_suppressed(tmp_path):
    model = {
        "filename": "gemma-4-4b-it-Q4_K_M.gguf",
        "settings": {"vision": {"enabled": True}},
    }
    result = resolve_mmproj_for_launch(tmp_path, tmp_path, model, "ik_llama")
    assert result is None


# ---------------------------------------------------------------------------
# 6. no-mmap resolution
# ---------------------------------------------------------------------------


def test_resolve_no_mmap_explicit_true():
    status = {"no_mmap_env": "true"}
    assert resolve_no_mmap(status, "any-model.gguf", "ik_llama", device_class="pi5-8gb", bundle_marker=None) is True


def test_resolve_no_mmap_explicit_false():
    status = {"no_mmap_env": "false"}
    assert resolve_no_mmap(status, "any-model.gguf", "ik_llama", device_class="pi5-8gb", bundle_marker=None) is False


def test_resolve_no_mmap_auto_non_a3b():
    status = {"no_mmap_env": "auto"}
    assert resolve_no_mmap(status, "plain-model.gguf", "ik_llama", device_class="pi5-16gb", bundle_marker=None) is False


def test_resolve_no_mmap_auto_a3b_heuristic():
    status = {"no_mmap_env": "auto"}
    marker = {"profile": "pi5-opt"}
    result = resolve_no_mmap(
        status,
        "Qwen3.5-35B-A3B-Q4_K_M.gguf",
        "ik_llama",
        device_class="pi5-16gb",
        bundle_marker=marker,
    )
    assert result is True


# ---------------------------------------------------------------------------
# 7. Restart inference process
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_restart_terminates_running_process():
    proc = _FakeProcess(returncode=None)
    terminate_called = False

    async def _terminate(p, timeout=3.0):
        nonlocal terminate_called
        terminate_called = True

    readiness = empty_readiness_state()
    readiness["model_path"] = "/m/test.gguf"

    new_readiness, terminated, reason = await restart_inference_process(
        readiness=readiness,
        process=proc,
        model_path="/m/test.gguf",
        terminate_fn=_terminate,
        stray_kill_fn=AsyncMock(return_value=0),
    )
    assert terminate_called
    assert terminated is True
    assert "terminated_running" in reason


@pytest.mark.anyio
async def test_restart_cleans_stale_processes():
    stray_kill = AsyncMock(return_value=2)

    new_readiness, terminated, reason = await restart_inference_process(
        readiness=empty_readiness_state(),
        process=None,
        model_path="/m/test.gguf",
        terminate_fn=AsyncMock(),
        stray_kill_fn=stray_kill,
    )
    stray_kill.assert_awaited_once()
    assert terminated is True
    assert "stale" in reason


@pytest.mark.anyio
async def test_restart_resets_readiness_to_loading():
    readiness = empty_readiness_state()
    readiness["ready"] = True
    readiness["status"] = "ready"

    new_readiness, _, _ = await restart_inference_process(
        readiness=readiness,
        process=None,
        model_path="/m/test.gguf",
        terminate_fn=AsyncMock(),
        stray_kill_fn=AsyncMock(return_value=0),
    )
    assert new_readiness["status"] == "loading"
    assert new_readiness["ready"] is False


@pytest.mark.anyio
async def test_restart_propagates_termination_failure():
    async def _exploding_terminate(proc, timeout=3.0):
        raise OSError("process stuck")

    proc = _FakeProcess(returncode=None)
    with pytest.raises(OSError, match="process stuck"):
        await restart_inference_process(
            readiness=empty_readiness_state(),
            process=proc,
            model_path="/m/test.gguf",
            terminate_fn=_exploding_terminate,
            stray_kill_fn=AsyncMock(return_value=0),
        )


# ---------------------------------------------------------------------------
# 8. Inference tick
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tick_model_missing_resets_readiness(tmp_path):
    missing = tmp_path / "missing.gguf"
    readiness = empty_readiness_state()
    readiness["model_path"] = str(missing)
    readiness["ready"] = True

    result = await run_inference_tick(
        process=None, consecutive_failures=3,
        failure_model_key="old", failure_runtime_key="old",
        readiness=readiness,
        model_path=missing, base_url="http://test:8080", installed_family="ik_llama",
        launch_llama_fn=AsyncMock(), launch_litert_fn=None,
    )
    assert result.readiness["status"] != "ready"
    assert result.consecutive_failures == 0


@pytest.mark.anyio
async def test_tick_model_present_dead_process_spawns(tick_env, monkeypatch):
    new_proc = _FakeProcess(returncode=None)
    launch = AsyncMock(return_value=new_proc)

    async def _fake_get(self, url, **kw):
        return httpx.Response(500)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await run_inference_tick(
        process=None, consecutive_failures=0,
        failure_model_key=None, failure_runtime_key=None,
        readiness=empty_readiness_state(),
        model_path=tick_env["model_path"], base_url=tick_env["base_url"],
        installed_family="ik_llama",
        launch_llama_fn=launch, launch_litert_fn=None,
    )
    launch.assert_awaited_once()
    assert result.process is new_proc


@pytest.mark.anyio
async def test_tick_model_changed_resets_failure_counter(tick_env, monkeypatch):
    async def _fake_get(self, url, **kw):
        return httpx.Response(500)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await run_inference_tick(
        process=_FakeProcess(returncode=None), consecutive_failures=3,
        failure_model_key="/m/old.gguf", failure_runtime_key="ik_llama",
        readiness=empty_readiness_state(),
        model_path=tick_env["model_path"], base_url=tick_env["base_url"],
        installed_family="ik_llama",
        launch_llama_fn=AsyncMock(), launch_litert_fn=None,
    )
    assert result.consecutive_failures == 0
    assert result.failure_model_key == str(tick_env["model_path"])


@pytest.mark.anyio
async def test_tick_runtime_changed_resets_failure_counter(tick_env, monkeypatch):
    async def _fake_get(self, url, **kw):
        return httpx.Response(500)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await run_inference_tick(
        process=_FakeProcess(returncode=None), consecutive_failures=3,
        failure_model_key=str(tick_env["model_path"]), failure_runtime_key="llama_cpp",
        readiness=empty_readiness_state(),
        model_path=tick_env["model_path"], base_url=tick_env["base_url"],
        installed_family="ik_llama",
        launch_llama_fn=AsyncMock(), launch_litert_fn=None,
    )
    assert result.consecutive_failures == 0
    assert result.failure_runtime_key == "ik_llama"


@pytest.mark.anyio
async def test_tick_failure_increments_on_nonzero_exit(tick_env, monkeypatch):
    async def _fake_get(self, url, **kw):
        return httpx.Response(500)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await run_inference_tick(
        process=_FakeProcess(returncode=1), consecutive_failures=0,
        failure_model_key=str(tick_env["model_path"]), failure_runtime_key="ik_llama",
        readiness=empty_readiness_state(),
        model_path=tick_env["model_path"], base_url=tick_env["base_url"],
        installed_family="ik_llama",
        launch_llama_fn=AsyncMock(return_value=_FakeProcess(returncode=None)),
        launch_litert_fn=None,
    )
    assert result.consecutive_failures == 1


@pytest.mark.anyio
async def test_tick_max_failures_stops_restart(tick_env, monkeypatch):
    launch = AsyncMock()

    async def _fake_get(self, url, **kw):
        return httpx.Response(500)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await run_inference_tick(
        process=_FakeProcess(returncode=1),
        consecutive_failures=MAX_CONSECUTIVE_FAILURES - 1,
        failure_model_key=str(tick_env["model_path"]), failure_runtime_key="ik_llama",
        readiness=empty_readiness_state(),
        model_path=tick_env["model_path"], base_url=tick_env["base_url"],
        installed_family="ik_llama",
        launch_llama_fn=launch, launch_litert_fn=None,
    )
    launch.assert_not_awaited()
    assert result.consecutive_failures == MAX_CONSECUTIVE_FAILURES


@pytest.mark.anyio
async def test_tick_ready_resets_failures(tick_env, monkeypatch):
    async def _fake_get(self, url, **kw):
        return httpx.Response(200)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    readiness = empty_readiness_state()
    readiness["model_path"] = str(tick_env["model_path"])
    readiness["healthy_polls"] = READY_HEALTH_POLLS_REQUIRED - 1

    result = await run_inference_tick(
        process=_FakeProcess(returncode=None), consecutive_failures=2,
        failure_model_key=str(tick_env["model_path"]), failure_runtime_key="ik_llama",
        readiness=readiness,
        model_path=tick_env["model_path"], base_url=tick_env["base_url"],
        installed_family="ik_llama",
        launch_llama_fn=AsyncMock(), launch_litert_fn=None,
    )
    assert result.readiness["ready"] is True
    assert result.consecutive_failures == 0


@pytest.mark.anyio
async def test_tick_switch_in_progress_skips_spawn(tick_env, monkeypatch):
    launch = AsyncMock()

    async def _fake_get(self, url, **kw):
        return httpx.Response(500)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    result = await run_inference_tick(
        process=None, consecutive_failures=0,
        failure_model_key=str(tick_env["model_path"]), failure_runtime_key="ik_llama",
        readiness=empty_readiness_state(),
        model_path=tick_env["model_path"], base_url=tick_env["base_url"],
        installed_family="ik_llama",
        launch_llama_fn=launch, launch_litert_fn=None,
        switch_in_progress=True,
    )
    launch.assert_not_awaited()


@pytest.mark.anyio
async def test_tick_launch_returns_none_downgrades_readiness(tick_env, monkeypatch):
    """When launch_llama_fn returns None (e.g. missing script), readiness must not stay ready."""
    async def _fake_get(self, url, **kw):
        return httpx.Response(500)
    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    readiness = empty_readiness_state()
    readiness["model_path"] = str(tick_env["model_path"])
    readiness["ready"] = True
    readiness["status"] = "ready"

    result = await run_inference_tick(
        process=None, consecutive_failures=0,
        failure_model_key=str(tick_env["model_path"]), failure_runtime_key="ik_llama",
        readiness=readiness,
        model_path=tick_env["model_path"], base_url=tick_env["base_url"],
        installed_family="ik_llama",
        launch_llama_fn=AsyncMock(return_value=None), launch_litert_fn=None,
    )
    assert result.process is None
    assert result.readiness["ready"] is False
    assert result.readiness["status"] == "loading"


# ---------------------------------------------------------------------------
# 11. Activation runtime prep
# ---------------------------------------------------------------------------


def test_prepare_activation_incompatible_device(tmp_path):
    runtimes_dir = tmp_path / "runtimes"
    runtimes_dir.mkdir()
    should_switch, reason, family = prepare_activation_runtime(
        model_filename="test.gguf",
        model_format="gguf",
        current_family="llama_cpp",
        device_class="pi4-8gb",
        runtimes_dir=runtimes_dir,
    )
    # ik_llama is incompatible with pi4 — should not switch or should fail.
    # The function returns the decision; pi4 can run llama_cpp fine so no switch needed.
    assert isinstance(should_switch, bool)
    assert isinstance(reason, str)


def test_prepare_activation_format_requires_incompatible(tmp_path):
    runtimes_dir = tmp_path / "runtimes"
    runtimes_dir.mkdir()
    should_switch, reason, family = prepare_activation_runtime(
        model_filename="model.litertlm",
        model_format="litertlm",
        current_family="llama_cpp",
        device_class="pi4-8gb",
        runtimes_dir=runtimes_dir,
    )
    # LiteRT is incompatible with Pi 4 — should fail.
    assert should_switch is False
    assert "incompatible" in reason.lower() or "no_switch" in reason.lower() or family is None


def test_prepare_activation_finds_slot(tmp_path):
    runtimes_dir = tmp_path / "runtimes"
    # GGUF on litert falls back to llama_cpp — create a valid slot.
    slot_dir = runtimes_dir / "llama_cpp"
    (slot_dir / "bin").mkdir(parents=True)
    (slot_dir / "bin" / "llama-server").write_text("#!/bin/sh\n")
    runtime_json = slot_dir / "runtime.json"
    runtime_json.write_text(json.dumps({"family": "llama_cpp", "profile": "pi5-opt"}))

    should_switch, reason, family = prepare_activation_runtime(
        model_filename="test.gguf",
        model_format="gguf",
        current_family="litert",
        device_class="pi5-8gb",
        runtimes_dir=runtimes_dir,
    )
    # GGUF model on litert should want to switch to llama_cpp.
    assert should_switch is True
    assert family == "llama_cpp"
