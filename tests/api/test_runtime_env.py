from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from core.main import _runtime_env, build_status, create_app, ensure_models_state, get_runtime, save_models_state
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


def test_runtime_env_resolves_9b_non_byteshape_to_unsloth_repo(runtime):
    """Non-ByteShape Qwen3.5-9B must resolve to unsloth/Qwen3.5-9B-GGUF."""
    model_filename = "Qwen3.5-9B-Q4_K_M.gguf"
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
                        "source_url": "https://example.com/qwen35-9b.gguf",
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
    assert env["POTATO_HF_MMPROJ_REPO"] == "unsloth/Qwen3.5-9B-GGUF"


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


# ── Gemma 4 runtime env ────────────────────────────────────────────


def test_runtime_env_sets_gemma4_vision_flag_when_active(runtime):
    model_filename = "gemma-4-E2B-it-Q4_K_M.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    mmproj = runtime.base_dir / "models" / "mmproj-gemma-4-E2B-it-f16.gguf"
    mmproj.write_bytes(b"projector")
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "gemma4-model",
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
                        "id": "gemma4-model",
                        "filename": model_filename,
                        "source_url": "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf",
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
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    assert env["POTATO_VISION_MODEL_NAME_PATTERN_GEMMA4"] == "1"
    assert env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] == "0"
    assert env["POTATO_AUTO_DOWNLOAD_MMPROJ"] == "1"
    assert env["POTATO_HF_MMPROJ_REPO"] == "unsloth/gemma-4-E2B-it-GGUF"
    assert "POTATO_MMPROJ_PATH" in env
    assert "gemma-4-E2B-it" in env["POTATO_MMPROJ_PATH"]


def test_runtime_env_suppresses_gemma4_vision_on_ik_llama(runtime):
    """When ik_llama is the active runtime, Gemma 4 vision must be suppressed
    because the fork doesn't support gemma4v projectors.  KV cache must NOT
    be overridden — q8_0 default is correct with the upstream D=512 kernel."""
    model_filename = "gemma-4-26B-A4B-it-UD-IQ4_NL.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    mmproj = runtime.base_dir / "models" / "mmproj-gemma-4-26B-A4B-it-f16.gguf"
    mmproj.write_bytes(b"projector")
    # Simulate ik_llama as the installed runtime
    llama_dir = runtime.base_dir / "llama"
    llama_dir.mkdir(parents=True, exist_ok=True)
    (llama_dir / "runtime.json").write_text(
        '{"family": "ik_llama", "commit": "584df79e"}',
        encoding="utf-8",
    )
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "gemma4-model",
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
                        "id": "gemma4-model",
                        "filename": model_filename,
                        "source_url": "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/" + model_filename,
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
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    # Vision must be suppressed — ik_llama doesn't support gemma4v projectors
    assert env["POTATO_VISION_MODEL_NAME_PATTERN_GEMMA4"] == "0"
    assert "POTATO_MMPROJ_PATH" not in env
    # KV cache must NOT be overridden — q8_0 default works with D=512 kernel
    assert "POTATO_CACHE_TYPE_K" not in env
    assert "POTATO_CACHE_TYPE_V" not in env


def test_status_reports_gemma4_vision_false_on_ik_llama(runtime):
    """When ik_llama is installed, /status must report capabilities.vision=false
    for the active Gemma 4 model so the UI hides image upload (#270 P2)."""
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    model_filename = "gemma-4-E2B-it-Q4_K_M.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    # Simulate ik_llama as the installed runtime
    llama_dir = runtime.base_dir / "llama"
    llama_dir.mkdir(parents=True, exist_ok=True)
    (llama_dir / "runtime.json").write_text(
        '{"family": "ik_llama", "commit": "584df79e"}',
        encoding="utf-8",
    )
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "gemma4-model",
                "default_model_id": "gemma4-model",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "gemma4-model",
                        "filename": model_filename,
                        "source_url": "https://example.com/gemma4.gguf",
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
            }
        ),
        encoding="utf-8",
    )

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    # Active model capabilities must report vision=false on ik_llama
    assert body["model"]["capabilities"]["vision"] is False
    # Model list entry should also reflect no vision
    gemma4_entry = next(m for m in body["models"] if m["id"] == "gemma4-model")
    assert gemma4_entry["capabilities"]["vision"] is False


def test_runtime_env_disables_gemma4_flag_when_vision_off(runtime):
    model_filename = "gemma-4-E4B-it-Q4_0.gguf"
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"gguf")
    runtime.model_path = model_path
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "gemma4-model",
                "default_model_id": "gemma4-model",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "gemma4-model",
                        "filename": model_filename,
                        "source_url": "https://example.com/gemma4.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                        "settings": {
                            "vision": {
                                "enabled": False,
                                "projector_mode": "default",
                                "projector_filename": None,
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    env = _runtime_env(runtime)

    assert env["POTATO_VISION_MODEL_NAME_PATTERN_GEMMA4"] == "0"
    assert env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] == "0"
    assert env["POTATO_AUTO_DOWNLOAD_MMPROJ"] == "0"
    assert "POTATO_MMPROJ_PATH" not in env


# -- LiteRT Gemma 4 vision gating in /status ------------------------------------


def _make_litert_gemma4_runtime(runtime, model_filename="gemma-4-E2B-it.litertlm"):
    """Set up a runtime with a LiteRT Gemma 4 model and litert installed."""
    runtime.enable_orchestrator = True
    model_path = runtime.base_dir / "models" / model_filename
    model_path.write_bytes(b"litert-model")
    runtime.model_path = model_path
    # Install litert runtime marker
    llama_dir = runtime.base_dir / "llama"
    llama_dir.mkdir(parents=True, exist_ok=True)
    (llama_dir / "runtime.json").write_text(
        '{"family": "litert", "commit": "abc123"}',
        encoding="utf-8",
    )
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "gemma4-litert",
                "default_model_id": "gemma4-litert",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "gemma4-litert",
                        "filename": model_filename,
                        "source_url": "https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm",
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
            }
        ),
        encoding="utf-8",
    )
    return runtime


def test_status_litert_gemma4_vision_true_when_adapter_confirms(runtime, monkeypatch):
    """When LiteRT adapter reports vision=true, /status capabilities.vision=True."""
    _make_litert_gemma4_runtime(runtime)
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _adapter_health_vision_true(_runtime):
        return True

    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
    monkeypatch.setattr("core.main._probe_litert_adapter_vision", _adapter_health_vision_true)

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["model"]["capabilities"]["vision"] is True
    gemma4_entry = next(m for m in body["models"] if m["id"] == "gemma4-litert")
    assert gemma4_entry["capabilities"]["vision"] is True
    # LiteRT bundles vision — projector.present should be True, no download needed
    assert body["model"]["projector"]["present"] is True
    assert body["model"]["projector"]["default_candidates"] == []


def test_status_litert_gemma4_vision_false_when_adapter_denies(runtime, monkeypatch):
    """When LiteRT adapter reports vision=false, /status capabilities.vision=False."""
    _make_litert_gemma4_runtime(runtime)
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _adapter_health_vision_false(_runtime):
        return False

    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
    monkeypatch.setattr("core.main._probe_litert_adapter_vision", _adapter_health_vision_false)

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["model"]["capabilities"]["vision"] is False
    gemma4_entry = next(m for m in body["models"] if m["id"] == "gemma4-litert")
    assert gemma4_entry["capabilities"]["vision"] is False


def test_status_litert_preserves_vision_when_adapter_unreachable(runtime, monkeypatch):
    """When adapter is unreachable (probe returns None), preserve filename-derived capabilities."""
    _make_litert_gemma4_runtime(runtime)
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _adapter_unreachable(_runtime):
        return None

    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
    monkeypatch.setattr("core.main._probe_litert_adapter_vision", _adapter_unreachable)

    with TestClient(app) as client:
        response = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    # Gemma 4 is vision-capable by filename — should NOT be downgraded to false
    assert body["model"]["capabilities"]["vision"] is True


