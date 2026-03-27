from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from core.main import _runtime_env, create_app, ensure_models_state, get_runtime, save_models_state
from core.model_state import build_model_projector_status


async def _healthy_true(_runtime):
    return True


def test_status_prefers_model_specific_qwen35_projector_over_stale_generic_default(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    model_filename = "Qwen_Qwen3.5-2B-IQ4_NL.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    (runtime.base_dir / "models" / "mmproj-F16.gguf").write_bytes(b"generic")
    (runtime.base_dir / "models" / "mmproj-Qwen_Qwen3.5-2B-f16.gguf").write_bytes(b"specific")
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "vision-model",
                "default_model_id": "default",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "default",
                        "filename": runtime.model_path.name,
                        "source_url": "https://example.com/default.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                    },
                    {
                        "id": "vision-model",
                        "filename": model_filename,
                        "source_url": "https://example.com/qwen35.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                        "settings": {
                            "vision": {
                                "enabled": True,
                                "projector_mode": "default",
                                "projector_filename": "mmproj-F16.gguf",
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["model"]["projector"]["filename"] == "mmproj-Qwen_Qwen3.5-2B-f16.gguf"
    assert body["model"]["projector"]["present"] is True
    assert body["model"]["projector"]["default_candidates"][0] == "mmproj-Qwen_Qwen3.5-2B-IQ4_NL-f16.gguf"
    assert "mmproj-Qwen_Qwen3.5-2B-f16.gguf" in body["model"]["projector"]["default_candidates"]


def test_runtime_env_uses_resolved_qwen35_default_projector(runtime):
    model_filename = "Qwen_Qwen3.5-2B-IQ4_NL.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    generic_mmproj = runtime.base_dir / "models" / "mmproj-F16.gguf"
    specific_mmproj = runtime.base_dir / "models" / "mmproj-Qwen_Qwen3.5-2B-f16.gguf"
    generic_mmproj.write_bytes(b"generic")
    specific_mmproj.write_bytes(b"specific")
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "vision-model",
                "default_model_id": "default",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "default",
                        "filename": runtime.model_path.name,
                        "source_url": "https://example.com/default.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                    },
                    {
                        "id": "vision-model",
                        "filename": model_filename,
                        "source_url": "https://example.com/qwen35.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                        "settings": {
                            "vision": {
                                "enabled": True,
                                "projector_mode": "default",
                                "projector_filename": "mmproj-F16.gguf",
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    assert env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] == "1"
    assert env["POTATO_MMPROJ_PATH"] == str(specific_mmproj)


def test_runtime_env_disables_vl_projector_heuristic_when_vision_is_off(runtime):
    model_filename = "Qwen3.5-2B-Q4_K_M.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    runtime.model_path = model_path
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "default",
                "default_model_id": "default",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "default",
                        "filename": model_filename,
                        "source_url": "https://example.com/qwen3-vl.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                        "settings": {
                            "vision": {
                                "enabled": False,
                                "projector_mode": "default",
                                "projector_filename": "mmproj-test-Q8_0.gguf",
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    assert "POTATO_VISION_MODEL_NAME_PATTERN_VL" not in env
    assert env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] == "0"
    assert env["POTATO_AUTO_DOWNLOAD_MMPROJ"] == "0"
    assert "POTATO_MMPROJ_PATH" not in env


def test_runtime_env_enables_vl_projector_heuristic_when_vision_is_on(runtime):
    model_filename = "Qwen3.5-2B-Q4_K_M.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    mmproj_path = runtime.base_dir / "models" / "mmproj-F16.gguf"
    model_path.write_bytes(b"gguf")
    mmproj_path.write_bytes(b"mmproj")
    runtime.model_path = model_path
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "default",
                "default_model_id": "default",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "default",
                        "filename": model_filename,
                        "source_url": "https://example.com/qwen3-vl.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                        "settings": {
                            "vision": {
                                "enabled": True,
                                "projector_mode": "default",
                                "projector_filename": None,
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    assert env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] == "1"
    assert env["POTATO_AUTO_DOWNLOAD_MMPROJ"] == "1"
    assert env["POTATO_MMPROJ_PATH"] == str(mmproj_path)


def test_runtime_env_enables_mmproj_auto_download_when_vision_on_and_no_mmproj(runtime):
    """When vision is enabled but no mmproj is present, AUTO_DOWNLOAD_MMPROJ must be 1
    so start_llama.sh can download it instead of crash-looping."""
    model_filename = "Qwen3.5-2B-Q4_K_M.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    runtime.model_path = model_path
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "default",
                "default_model_id": "default",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "default",
                        "filename": model_filename,
                        "source_url": "https://example.com/qwen35.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                        "settings": {
                            "vision": {
                                "enabled": True,
                                "projector_mode": "default",
                                "projector_filename": None,
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    assert env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] == "1"
    assert env["POTATO_AUTO_DOWNLOAD_MMPROJ"] == "1"
    assert "POTATO_MMPROJ_PATH" not in env
    # Must pass the curated HF repo so start_llama.sh downloads the right projector
    assert "POTATO_HF_MMPROJ_REPO" in env
    assert "unsloth" in env["POTATO_HF_MMPROJ_REPO"] or "huggingface" in env["POTATO_HF_MMPROJ_REPO"].lower()


def test_projector_status_reports_generic_truthfully_for_offline_compat(runtime):
    """build_model_projector_status must report mmproj-F16.gguf as present even
    when model-specific candidates exist — preserves offline upgrades where the
    generic is the only file available. Shell handles upgrade at runtime (#136)."""
    models_dir = runtime.base_dir / "models"
    (models_dir / "mmproj-F16.gguf").write_bytes(b"generic-projector")

    model_4b = {
        "filename": "Qwen3.5-4B-Q4_K_M.gguf",
        "settings": {
            "vision": {"enabled": True, "projector_mode": "default", "projector_filename": None},
        },
    }
    status = build_model_projector_status(runtime, model_4b)

    assert status["present"] is True, (
        "Generic mmproj-F16.gguf must be reported as present for offline compat"
    )
    assert status["filename"] == "mmproj-F16.gguf"
    # Model-specific candidates must still be listed so the shell can try them
    assert any(c != "mmproj-F16.gguf" for c in status["default_candidates"])


def test_model_switch_passes_generic_with_auto_download_for_shell_upgrade(runtime):
    """When only mmproj-F16.gguf exists after a model switch, _runtime_env must
    still pass it as POTATO_MMPROJ_PATH (offline fallback) AND enable auto-download
    so the shell can upgrade it to a model-specific file (#136)."""
    models_dir = runtime.base_dir / "models"
    model_2b = "Qwen3.5-2B-Q4_K_M.gguf"
    model_4b = "Qwen3.5-4B-Q4_K_M.gguf"
    (models_dir / model_2b).write_bytes(b"gguf")
    (models_dir / model_4b).write_bytes(b"gguf")
    (models_dir / "mmproj-F16.gguf").write_bytes(b"generic-projector")

    runtime.model_path = models_dir / model_4b
    runtime.models_state_path.write_text(
        json.dumps({
            "version": 1,
            "countdown_enabled": True,
            "default_model_downloaded_once": True,
            "active_model_id": "model-4b",
            "default_model_id": "default",
            "current_download_model_id": None,
            "models": [
                {
                    "id": "default",
                    "filename": model_2b,
                    "source_url": "https://example.com/2b.gguf",
                    "source_type": "url",
                    "status": "ready",
                    "error": None,
                },
                {
                    "id": "model-4b",
                    "filename": model_4b,
                    "source_url": "https://example.com/4b.gguf",
                    "source_type": "url",
                    "status": "ready",
                    "error": None,
                    "settings": {
                        "vision": {
                            "enabled": True,
                            "projector_mode": "default",
                            "projector_filename": None,
                        }
                    },
                },
            ],
        }),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    # Generic is passed as fallback — shell will try to upgrade it
    assert "POTATO_MMPROJ_PATH" in env
    assert env["POTATO_MMPROJ_PATH"].endswith("mmproj-F16.gguf")
    # Auto-download must be enabled so the shell can upgrade
    assert env["POTATO_AUTO_DOWNLOAD_MMPROJ"] == "1"
    assert "POTATO_HF_MMPROJ_REPO" in env


