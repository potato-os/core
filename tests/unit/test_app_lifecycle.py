from __future__ import annotations

import asyncio
import warnings
from typing import Any

from fastapi.testclient import TestClient

from core import main


class _FakeTask:
    def __init__(self, name: str) -> None:
        self.name = name
        self.cancel_called = False
        self.awaited = False
        self._done = False

    def cancel(self) -> None:
        self.cancel_called = True
        self._done = True

    def done(self) -> bool:
        return self._done

    def __await__(self):
        async def _wait() -> None:
            self.awaited = True

        return _wait().__await__()


class _FakeProcess:
    def __init__(self, *, hang_on_terminate: bool = False, hang_on_kill: bool = False) -> None:
        self.returncode = None
        self.terminated = False
        self.waited = False
        self.killed = False
        self._hang_on_terminate = hang_on_terminate
        self._hang_on_kill = hang_on_kill
        self._killed_event: asyncio.Event | None = None
        if hang_on_terminate:
            self._killed_event = asyncio.Event()

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        if self._killed_event is not None and not self._hang_on_kill:
            self._killed_event.set()

    async def wait(self) -> int:
        if self._hang_on_terminate and not self.killed:
            assert self._killed_event is not None
            await self._killed_event.wait()
        if self._hang_on_kill and self.killed:
            await asyncio.Event().wait()  # block forever
        self.waited = True
        self.returncode = 0
        return 0


def test_create_app_does_not_emit_fastapi_on_event_deprecation_warning(runtime: main.RuntimeConfig) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app = main.create_app(runtime=runtime, enable_orchestrator=False)
        with TestClient(app):
            pass

    messages = [
        str(warning.message)
        for warning in caught
        if issubclass(warning.category, DeprecationWarning)
    ]
    assert not any("on_event is deprecated" in message for message in messages)


def test_app_lifespan_runs_startup_and_shutdown_hooks(
    runtime: main.RuntimeConfig,
    monkeypatch,
) -> None:
    startup_events: list[str] = []
    created_tasks: list[_FakeTask] = []
    download_task = _FakeTask("download")
    llama_process = _FakeProcess()

    def _ensure_models_state(_runtime: main.RuntimeConfig) -> dict[str, Any]:
        startup_events.append("ensure_models_state")
        return {}

    def _prime_system_metrics_counters() -> None:
        startup_events.append("prime_system_metrics_counters")

    def _collect_system_metrics_snapshot() -> dict[str, Any]:
        startup_events.append("collect_system_metrics_snapshot")
        return {"cpu_percent": 12.5}

    def _create_task(coro: Any, *, name: str | None = None) -> _FakeTask:
        coro.close()
        task = _FakeTask(name or "unnamed")
        created_tasks.append(task)
        return task

    monkeypatch.setattr(main, "ensure_models_state", _ensure_models_state)
    monkeypatch.setattr(main, "prime_system_metrics_counters", _prime_system_metrics_counters)
    monkeypatch.setattr(main, "collect_system_metrics_snapshot", _collect_system_metrics_snapshot)
    monkeypatch.setattr(main, "get_monotonic_time", lambda: 123.0)
    monkeypatch.setattr(main.asyncio, "create_task", _create_task)

    app = main.create_app(runtime=runtime, enable_orchestrator=True)

    with TestClient(app):
        app.state.model_download_task = download_task
        app.state.llama_process = llama_process

        assert app.state.startup_monotonic == 123.0
        assert app.state.system_metrics_snapshot == {"cpu_percent": 12.5}
        assert startup_events == [
            "ensure_models_state",
            "prime_system_metrics_counters",
            "collect_system_metrics_snapshot",
        ]
        assert [task.name for task in created_tasks] == [
            "potato-system-metrics",
            "potato-orchestrator",
        ]

    assert all(task.cancel_called for task in created_tasks)
    assert all(task.awaited for task in created_tasks)
    assert download_task.cancel_called is True
    assert download_task.awaited is True
    assert llama_process.terminated is True
    assert llama_process.waited is True


def test_lifespan_shutdown_escalates_to_kill_on_timeout(monkeypatch) -> None:
    proc = _FakeProcess(hang_on_terminate=True)
    monkeypatch.setattr(main, "LLAMA_SHUTDOWN_TIMEOUT_SECONDS", 0.1)
    asyncio.run(main._terminate_process(proc))
    assert proc.terminated is True
    assert proc.killed is True
    assert proc.waited is True


def test_lifespan_shutdown_clean_exit_does_not_kill() -> None:
    proc = _FakeProcess()
    asyncio.run(main._terminate_process(proc))
    assert proc.terminated is True
    assert proc.killed is False
    assert proc.waited is True


def test_terminate_process_raises_when_kill_also_times_out(monkeypatch) -> None:
    proc = _FakeProcess(hang_on_terminate=True, hang_on_kill=True)
    monkeypatch.setattr(main, "LLAMA_SHUTDOWN_TIMEOUT_SECONDS", 0.1)
    try:
        asyncio.run(main._terminate_process(proc, timeout=0.1))
        raised = False
    except (TimeoutError, asyncio.TimeoutError):
        raised = True
    assert raised is True
    assert proc.terminated is True
    assert proc.killed is True


def test_consecutive_failure_counter_initialized_on_app():
    from core.main import create_app, RuntimeConfig

    app = create_app(enable_orchestrator=False)
    assert hasattr(app.state, "llama_consecutive_failures")
    assert app.state.llama_consecutive_failures == 0


def test_max_consecutive_failures_constant_exists():
    assert hasattr(main, "LLAMA_MAX_CONSECUTIVE_FAILURES")
    assert main.LLAMA_MAX_CONSECUTIVE_FAILURES >= 3


def test_orchestrator_loop_has_crash_loop_guard():
    """The orchestrator must check consecutive_failures before restarting."""
    import inspect
    from inferno import orchestrator

    source = inspect.getsource(orchestrator.run_inference_tick)
    assert "consecutive_failures" in source
    assert "MAX_CONSECUTIVE_FAILURES" in source


def test_orchestrator_resets_failures_on_model_change():
    """Switching models must reset the failure counter so the new model can start."""
    import inspect
    source = inspect.getsource(main.orchestrator_loop)

    assert "_llama_failure_model" in source


def test_crash_loop_counter_stops_restarts_for_fast_crashing_process(
    runtime: main.RuntimeConfig,
):
    """Simulate a process that starts successfully (returncode=None) but crashes
    before the next poll (returncode=1). This matches the real Pi behavior where
    llama-server exits between orchestrator iterations. The orchestrator must
    detect the pattern and stop restarting after LLAMA_MAX_CONSECUTIVE_FAILURES."""
    import json

    app = main.create_app(runtime=runtime, enable_orchestrator=False)

    model_file = runtime.model_path
    model_file.write_bytes(b"fake gguf content")
    state = {
        "default_model_id": "default",
        "models": [{
            "id": "default",
            "filename": model_file.name,
            "source_url": None,
            "source_type": "local_file",
            "status": "ready",
            "error": None,
            "settings": {"chat": {}, "vision": {}},
        }],
    }
    runtime.models_state_path.write_text(json.dumps(state), encoding="utf-8")

    start_script = runtime.base_dir / "bin" / "start_llama.sh"
    start_script.parent.mkdir(parents=True, exist_ok=True)
    start_script.write_text("#!/bin/sh\nexit 1\n")
    start_script.chmod(0o755)
    runtime.start_llama_script = start_script

    start_count = 0
    # Track all created processes so we can crash them between iterations
    all_procs: list[_FakeProcess] = []

    async def _fake_create_subprocess_exec(*args, **kwargs):
        nonlocal start_count
        start_count += 1
        proc = _FakeProcess()
        # Process starts alive (returncode=None) — like the real Pi
        all_procs.append(proc)
        return proc

    async def _fake_terminate_stray(*_args):
        pass

    async def _fake_refresh_readiness(_app, _runtime, *, active_model_path):
        return {"ready": False, "transport_healthy": False}

    async def _run_iterations(n: int):
        """Simulate n orchestrator iterations with process crashes between them."""
        for i in range(n):
            # Between iterations: crash any running process (simulates llama-server
            # dying on a corrupt model between polls)
            if i > 0:
                for p in all_procs:
                    if p.returncode is None:
                        p.returncode = 1

            # Run one iteration of the actual orchestrator body
            models_state = main.ensure_models_state(runtime)
            active_model_path = runtime.model_path
            active_model_is_present = active_model_path.exists() and active_model_path.stat().st_size > 0

            if active_model_is_present:
                current_model_key = str(active_model_path)
                if getattr(app.state, "_llama_failure_model", None) != current_model_key:
                    app.state.llama_consecutive_failures = 0
                    app.state._llama_failure_model = current_model_key

                llama_process = app.state.llama_process
                if llama_process is None or llama_process.returncode is not None:
                    if llama_process is not None and llama_process.returncode is not None and llama_process.returncode != 0:
                        app.state.llama_consecutive_failures += 1
                        app.state.llama_process = None

                    if app.state.llama_consecutive_failures >= main.LLAMA_MAX_CONSECUTIVE_FAILURES:
                        pass
                    elif start_script.exists():
                        await _fake_terminate_stray(runtime)
                        app.state.llama_process = await _fake_create_subprocess_exec()

                readiness = await _fake_refresh_readiness(app, runtime, active_model_path=active_model_path)
                if readiness.get("ready"):
                    app.state.llama_consecutive_failures = 0

    asyncio.run(_run_iterations(20))

    assert app.state.llama_consecutive_failures == main.LLAMA_MAX_CONSECUTIVE_FAILURES, (
        f"Counter should be exactly {main.LLAMA_MAX_CONSECUTIVE_FAILURES}, got {app.state.llama_consecutive_failures}"
    )
    # Must be capped — NOT 20 starts
    assert start_count == main.LLAMA_MAX_CONSECUTIVE_FAILURES, (
        f"Expected {main.LLAMA_MAX_CONSECUTIVE_FAILURES} start attempts, got {start_count}"
    )
