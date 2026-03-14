from __future__ import annotations

import asyncio
import warnings
from typing import Any

from fastapi.testclient import TestClient

from app import main


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
    def __init__(self, *, hang_on_terminate: bool = False) -> None:
        self.returncode = None
        self.terminated = False
        self.waited = False
        self.killed = False
        self._hang_on_terminate = hang_on_terminate
        self._killed_event: asyncio.Event | None = None
        if hang_on_terminate:
            self._killed_event = asyncio.Event()

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        if self._killed_event is not None:
            self._killed_event.set()

    async def wait(self) -> int:
        if self._hang_on_terminate and not self.killed:
            assert self._killed_event is not None
            await self._killed_event.wait()
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
