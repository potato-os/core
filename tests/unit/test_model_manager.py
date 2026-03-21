from __future__ import annotations

import asyncio

import pytest

from app.main import compute_auto_download_remaining_seconds, create_app, start_model_download
from app.model_state import (
    describe_model_storage,
    ensure_models_state,
    resolve_model_runtime_path,
    validate_model_url,
)
from app.runtime_state import RuntimeConfig, get_free_storage_bytes, is_likely_too_large_for_storage, read_download_progress


def test_validate_model_url_accepts_https_gguf():
    ok, reason, filename = validate_model_url(
        "https://huggingface.co/org/model/resolve/main/cool-model.Q4_K_M.gguf"
    )
    assert ok is True
    assert reason == ""
    assert filename == "cool-model.Q4_K_M.gguf"


def test_validate_model_url_rejects_non_https_and_non_gguf():
    ok_http, reason_http, _ = validate_model_url("http://example.com/model.gguf")
    ok_ext, reason_ext, _ = validate_model_url("https://example.com/model.bin")

    assert ok_http is False
    assert reason_http == "https_required"
    assert ok_ext is False
    assert reason_ext == "gguf_required"


def test_ensure_models_state_has_default_model(runtime: RuntimeConfig):
    state = ensure_models_state(runtime)

    assert state["countdown_enabled"] is True
    assert state["default_model_downloaded_once"] is False
    assert isinstance(state["models"], list)
    assert state["active_model_id"]
    assert any(model["source_url"] for model in state["models"])


def test_ensure_models_state_populates_default_model_settings(runtime: RuntimeConfig):
    state = ensure_models_state(runtime)

    default_model = next(model for model in state["models"] if model["id"] == "default")
    assert default_model["settings"]["chat"]["temperature"] == 0.7
    assert default_model["settings"]["chat"]["generation_mode"] == "random"
    assert default_model["settings"]["chat"]["system_prompt"] == ""
    assert default_model["settings"]["vision"]["enabled"] is True
    assert default_model["settings"]["vision"]["projector_mode"] == "default"
    assert default_model["settings"]["vision"]["projector_filename"] is None


def test_auto_download_remaining_zero_when_countdown_disabled(runtime: RuntimeConfig):
    runtime.enable_orchestrator = True
    runtime.auto_download_idle_seconds = 300

    remaining = compute_auto_download_remaining_seconds(
        runtime,
        model_present=False,
        download_active=False,
        startup_monotonic=100.0,
        now_monotonic=150.0,
        countdown_enabled=False,
    )

    assert remaining == 0


def test_ensure_models_state_marks_default_downloaded_once_when_file_exists(runtime: RuntimeConfig):
    runtime.model_path.write_bytes(b"gguf")

    state = ensure_models_state(runtime)

    assert state["default_model_downloaded_once"] is True


@pytest.mark.anyio
async def test_start_model_download_predicts_insufficient_storage(runtime: RuntimeConfig, monkeypatch: pytest.MonkeyPatch):
    runtime.enable_orchestrator = True
    runtime.ensure_model_script.parent.mkdir(parents=True, exist_ok=True)
    runtime.ensure_model_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    runtime.ensure_model_script.chmod(0o755)

    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.state.download_lock = asyncio.Lock()
    app.state.model_download_task = None
    app.state.model_download_process = None

    async def _fake_length(_url: str) -> int:
        return 2_000

    monkeypatch.setattr("app.main.fetch_remote_content_length_bytes", _fake_length)
    monkeypatch.setattr("app.main.get_free_storage_bytes", lambda _runtime: 500)

    started, reason = await start_model_download(app, runtime, trigger="manual")

    assert started is False
    assert reason == "insufficient_storage"
    state = ensure_models_state(runtime)
    default = next(model for model in state["models"] if model["id"] == "default")
    assert default["status"] == "failed"
    assert default["error"] == "insufficient_storage"
    progress = read_download_progress(runtime)
    assert progress["error"] == "insufficient_storage"


def test_get_free_storage_bytes_returns_unknown_when_psutil_missing(runtime: RuntimeConfig, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.runtime_state.psutil", None)

    free_bytes = get_free_storage_bytes(runtime)

    assert free_bytes is None


def test_storage_precheck_skips_when_free_space_unknown():
    assert is_likely_too_large_for_storage(total_bytes=2_000, free_bytes=None, partial_bytes=0) is False


@pytest.mark.anyio
async def test_start_model_download_does_not_fail_precheck_when_free_space_unknown(
    runtime: RuntimeConfig,
    monkeypatch: pytest.MonkeyPatch,
):
    runtime.enable_orchestrator = True
    runtime.ensure_model_script.parent.mkdir(parents=True, exist_ok=True)
    runtime.ensure_model_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    runtime.ensure_model_script.chmod(0o755)

    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.state.download_lock = asyncio.Lock()
    app.state.model_download_task = None
    app.state.model_download_process = None

    async def _fake_length(_url: str) -> int:
        return 2_000

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = None
            self.returncode = None

        async def wait(self) -> int:
            self.returncode = 1
            return 1

    async def _fake_spawn(*_args, **_kwargs):
        return _FakeProc()

    monkeypatch.setattr("app.main.fetch_remote_content_length_bytes", _fake_length)
    monkeypatch.setattr("app.main.get_free_storage_bytes", lambda _runtime: None)
    monkeypatch.setattr("app.main.asyncio.create_subprocess_exec", _fake_spawn)

    started, reason = await start_model_download(app, runtime, trigger="manual")

    assert started is True
    assert reason == "started"
    task = app.state.model_download_task
    assert task is not None
    await task


def test_resolve_model_runtime_path_follows_symlinks(runtime: RuntimeConfig):
    """Legacy SSD-backed models are symlinked — path resolution must follow them."""
    external_dir = runtime.base_dir / "external-drive" / "potato-models"
    external_dir.mkdir(parents=True, exist_ok=True)
    real_file = external_dir / runtime.model_path.name
    real_file.write_bytes(b"gguf-on-ssd")

    models_dir = runtime.base_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    symlink = models_dir / runtime.model_path.name
    symlink.symlink_to(real_file)

    resolved = resolve_model_runtime_path(runtime, runtime.model_path.name)
    assert resolved == real_file.resolve()
    assert not resolved.is_symlink()


def test_resolve_model_runtime_path_returns_plain_path_when_no_symlink(runtime: RuntimeConfig):
    runtime.model_path.parent.mkdir(parents=True, exist_ok=True)
    runtime.model_path.write_bytes(b"gguf-local")

    resolved = resolve_model_runtime_path(runtime, runtime.model_path.name)
    assert resolved == runtime.model_path


def test_describe_model_storage_reports_local_for_all_models(runtime: RuntimeConfig):
    runtime.model_path.parent.mkdir(parents=True, exist_ok=True)
    runtime.model_path.write_bytes(b"gguf-data")

    storage = describe_model_storage(runtime, runtime.model_path.name)
    assert storage["location"] == "local"
    assert storage["exists"] is True
    assert storage["size_bytes"] > 0

