from __future__ import annotations

import types

from app.main import (
    MODEL_UPLOAD_LIMIT_8GB_BYTES,
    MODEL_UPLOAD_LIMIT_16GB_BYTES,
    get_model_upload_max_bytes,
)


def test_upload_limit_detects_8gb_pi_from_memory(monkeypatch):
    fake_psutil = types.SimpleNamespace(
        virtual_memory=lambda: types.SimpleNamespace(total=8 * 1024 * 1024 * 1024)
    )
    monkeypatch.setattr("app.main.psutil", fake_psutil)
    monkeypatch.delenv("POTATO_MODEL_UPLOAD_MAX_BYTES", raising=False)

    assert get_model_upload_max_bytes() == MODEL_UPLOAD_LIMIT_8GB_BYTES


def test_upload_limit_detects_16gb_pi_from_memory(monkeypatch):
    fake_psutil = types.SimpleNamespace(
        virtual_memory=lambda: types.SimpleNamespace(total=16 * 1024 * 1024 * 1024)
    )
    monkeypatch.setattr("app.main.psutil", fake_psutil)
    monkeypatch.delenv("POTATO_MODEL_UPLOAD_MAX_BYTES", raising=False)

    assert get_model_upload_max_bytes() == MODEL_UPLOAD_LIMIT_16GB_BYTES


def test_upload_limit_defaults_to_16gb_when_memory_detection_unavailable(monkeypatch):
    monkeypatch.setattr("app.main.psutil", None)
    monkeypatch.delenv("POTATO_MODEL_UPLOAD_MAX_BYTES", raising=False)

    assert get_model_upload_max_bytes() == MODEL_UPLOAD_LIMIT_16GB_BYTES


def test_upload_limit_env_override_can_disable_limit(monkeypatch):
    monkeypatch.setenv("POTATO_MODEL_UPLOAD_MAX_BYTES", "0")

    assert get_model_upload_max_bytes() is None


def test_upload_limit_env_override_can_set_custom_bytes(monkeypatch):
    monkeypatch.setenv("POTATO_MODEL_UPLOAD_MAX_BYTES", "12345")

    assert get_model_upload_max_bytes() == 12345
