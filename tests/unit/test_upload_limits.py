from __future__ import annotations

from core.runtime_state import (
    MODEL_UPLOAD_STORAGE_SAFETY_FRACTION,
    get_model_upload_max_bytes,
)


def test_upload_limit_uses_safety_fraction_of_free_storage(monkeypatch, runtime):
    free_bytes = 32 * 1024 * 1024 * 1024
    monkeypatch.setattr("core.runtime_state.get_model_volume_free_bytes", lambda _r: free_bytes)
    monkeypatch.delenv("POTATO_MODEL_UPLOAD_MAX_BYTES", raising=False)

    assert get_model_upload_max_bytes(runtime) == int(free_bytes * MODEL_UPLOAD_STORAGE_SAFETY_FRACTION)


def test_upload_limit_returns_none_when_storage_unavailable(monkeypatch, runtime):
    monkeypatch.setattr("core.runtime_state.get_model_volume_free_bytes", lambda _r: None)
    monkeypatch.delenv("POTATO_MODEL_UPLOAD_MAX_BYTES", raising=False)

    assert get_model_upload_max_bytes(runtime) is None


def test_upload_limit_env_override_takes_priority_over_storage(monkeypatch, runtime):
    monkeypatch.setattr("core.runtime_state.get_model_volume_free_bytes", lambda _r: 32 * 1024 * 1024 * 1024)
    monkeypatch.setenv("POTATO_MODEL_UPLOAD_MAX_BYTES", "99999")

    assert get_model_upload_max_bytes(runtime) == 99999


def test_upload_limit_env_override_can_disable_limit(monkeypatch, runtime):
    monkeypatch.setenv("POTATO_MODEL_UPLOAD_MAX_BYTES", "0")

    assert get_model_upload_max_bytes(runtime) is None


def test_upload_limit_env_override_can_set_custom_bytes(monkeypatch, runtime):
    monkeypatch.setenv("POTATO_MODEL_UPLOAD_MAX_BYTES", "12345")

    assert get_model_upload_max_bytes(runtime) == 12345
