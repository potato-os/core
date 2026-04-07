from __future__ import annotations

import asyncio

import pytest

from core.main import compute_auto_download_remaining_seconds, create_app, start_model_download
from core.model_state import (
    MODEL_FILENAME,
    MODEL_URL,
    MODEL_FILENAME_PI4,
    MODEL_URL_PI4,
    default_model_for_device,
    describe_model_storage,
    ensure_models_state,
    resolve_model_runtime_path,
    validate_model_url,
)
from core.runtime_state import RuntimeConfig, get_free_storage_bytes, is_likely_too_large_for_storage, read_download_progress


def test_validate_model_url_accepts_https_gguf():
    ok, reason, filename = validate_model_url(
        "https://huggingface.co/org/model/resolve/main/cool-model.Q4_K_M.gguf"
    )
    assert ok is True
    assert reason == ""
    assert filename == "cool-model.Q4_K_M.gguf"


def test_validate_model_url_rejects_non_https_and_unsupported_format():
    ok_http, reason_http, _ = validate_model_url("http://example.com/model.gguf")
    ok_ext, reason_ext, _ = validate_model_url("https://example.com/model.bin")

    assert ok_http is False
    assert reason_http == "https_required"
    assert ok_ext is False
    assert reason_ext == "unsupported_model_format"


def test_validate_model_url_accepts_litertlm():
    ok, reason, filename = validate_model_url(
        "https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it.litertlm"
    )
    assert ok is True
    assert reason == ""
    assert filename == "gemma-4-E2B-it.litertlm"


def test_validate_model_url_rejects_unknown_extension():
    ok, reason, _ = validate_model_url("https://example.com/model.safetensors")
    assert ok is False
    assert reason == "unsupported_model_format"


def test_discoverable_local_model_includes_litertlm():
    from core.model_state import _is_discoverable_local_model_filename
    assert _is_discoverable_local_model_filename("gemma-4-E2B-it.litertlm") is True
    assert _is_discoverable_local_model_filename("model.gguf") is True
    assert _is_discoverable_local_model_filename("model.bin") is False


def test_normalize_preserves_litertlm_suffix(runtime: RuntimeConfig):
    """_normalize_models_state must not force .gguf on .litertlm files."""
    import json
    runtime.models_state_path.write_text(json.dumps({
        "version": 1,
        "countdown_enabled": True,
        "default_model_downloaded_once": False,
        "active_model_id": "litert-model",
        "default_model_id": "default",
        "models": [
            {
                "id": "litert-model",
                "filename": "gemma-4-E2B-it.litertlm",
                "source_url": "https://example.com/gemma-4-E2B-it.litertlm",
                "source_type": "url",
                "status": "ready",
                "error": None,
            }
        ],
    }), encoding="utf-8")
    state = ensure_models_state(runtime)
    model = next(m for m in state["models"] if m["id"] == "litert-model")
    assert model["filename"].endswith(".litertlm")


def test_model_format_for_filename():
    from core.model_state import model_format_for_filename
    assert model_format_for_filename("model.gguf") == "gguf"
    assert model_format_for_filename("gemma-4-E2B-it.litertlm") == "litertlm"
    assert model_format_for_filename("Model.GGUF") == "gguf"
    assert model_format_for_filename("model.LITERTLM") == "litertlm"


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

    monkeypatch.setattr("core.main.fetch_remote_content_length_bytes", _fake_length)
    monkeypatch.setattr("core.main.get_free_storage_bytes", lambda _runtime: 500)

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
    monkeypatch.setattr("core.runtime_state.psutil", None)

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

    monkeypatch.setattr("core.main.fetch_remote_content_length_bytes", _fake_length)
    monkeypatch.setattr("core.main.get_free_storage_bytes", lambda _runtime: None)
    monkeypatch.setattr("core.main.asyncio.create_subprocess_exec", _fake_spawn)

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


def test_default_model_for_pi4_returns_0_8b():
    filename, url = default_model_for_device("pi4-8gb")
    assert filename == MODEL_FILENAME_PI4
    assert "0.8B" in filename
    assert "IQ4_NL" in filename
    assert url == MODEL_URL_PI4


def test_default_model_for_pi4_4gb_returns_0_8b():
    filename, url = default_model_for_device("pi4-4gb")
    assert filename == MODEL_FILENAME_PI4


def test_default_model_for_pi5_returns_2b():
    filename, url = default_model_for_device("pi5-8gb")
    assert filename == MODEL_FILENAME
    assert url == MODEL_URL


def test_default_model_for_unknown_returns_2b():
    filename, url = default_model_for_device("unknown")
    assert filename == MODEL_FILENAME
    assert url == MODEL_URL

