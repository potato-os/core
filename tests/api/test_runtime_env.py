from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import _runtime_env, create_app, ensure_models_state, get_runtime, save_models_state


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
                                "projector_filename": "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf",
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    assert env["POTATO_VISION_MODEL_NAME_PATTERN_VL"] == "0"
    assert env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] == "0"
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
    assert env["POTATO_MMPROJ_PATH"] == str(mmproj_path)



