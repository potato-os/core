from __future__ import annotations

import pytest

from core.constants.model_families import is_gemma4_filename, projector_repo_for_model, recommended_runtime_for_model


def test_projector_repo_for_qwen35_9b():
    """Qwen3.5-9B generic model resolves to unsloth 9B projector repo."""
    assert projector_repo_for_model("Qwen3.5-9B-Q4_K_S-3.92bpw.gguf") == "unsloth/Qwen3.5-9B-GGUF"


def test_projector_repo_for_qwen35_9b_byteshape_by_filename():
    """ByteShape in filename resolves to ByteShape repo."""
    assert projector_repo_for_model("byteshape-Qwen3.5-9B-Q4_K_S.gguf") == "byteshape/Qwen3.5-9B-GGUF"


def test_projector_repo_for_qwen35_9b_byteshape_by_source_url():
    """ByteShape in source_url resolves to ByteShape repo even when filename
    has no publisher prefix — this is the real HuggingFace scenario."""
    assert projector_repo_for_model(
        "Qwen3.5-9B-Q4_K_S-3.92bpw.gguf",
        source_url="https://huggingface.co/byteshape/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_S-3.92bpw.gguf",
    ) == "byteshape/Qwen3.5-9B-GGUF"


def test_projector_repo_for_qwen35_9b_without_source_url():
    """Generic 9B without source_url falls back to unsloth."""
    assert projector_repo_for_model("Qwen3.5-9B-Q4_K_S-3.92bpw.gguf") == "unsloth/Qwen3.5-9B-GGUF"


def test_projector_repo_returns_none_for_unknown_qwen35_size():
    """Unrecognized Qwen3.5 sizes must return None, not silently fall back."""
    assert projector_repo_for_model("Qwen3.5-7B-Q4_K_M.gguf") is None


def test_projector_repo_no_substring_collision_19b():
    """19B must NOT match the 9B rule — '9b' is a substring of '19b'."""
    assert projector_repo_for_model("Qwen3.5-Creative-19B-A3B-REAP.Q4_K_S.gguf") is None


def test_projector_repo_no_substring_collision_bpw():
    """3.92bpw must NOT match the 2B rule — '2b' is a substring of '3.92bpw'."""
    result = projector_repo_for_model("Qwen3.5-9B-Q4_K_S-3.92bpw.gguf")
    assert result == "unsloth/Qwen3.5-9B-GGUF", f"got {result!r} (2B collision via '3.92bpw')"


def test_projector_repo_dot_delimited_9b():
    """Dot-delimited filenames like Qwen3.5-9B.gguf must still match."""
    assert projector_repo_for_model("Qwen3.5-9B.gguf") == "unsloth/Qwen3.5-9B-GGUF"
    assert projector_repo_for_model("Qwen3.5-9B.Q4_K_S.gguf") == "unsloth/Qwen3.5-9B-GGUF"


def test_projector_repo_dot_delimited_4b():
    """Dot-delimited filenames like Qwen3.5-4B.Q4_K_M.gguf must still match."""
    assert projector_repo_for_model("Qwen3.5-4B.Q4_K_M.gguf") == "unsloth/Qwen3.5-4B-GGUF"


def test_default_candidates_include_model_specific_bf16_but_not_generic():
    """default_projector_candidates_for_model must include model-specific bf16
    names but NOT generic mmproj-bf16.gguf (unsafe cross-model reuse)."""
    from core.model_state import default_projector_candidates_for_model

    candidates = default_projector_candidates_for_model("Qwen3.5-9B-Q4_K_S.gguf")
    assert "mmproj-F16.gguf" in candidates
    assert "mmproj-Qwen3.5-9B-f16.gguf" in candidates
    assert "mmproj-Qwen3.5-9B-bf16.gguf" in candidates
    # Generic bf16 must NOT be in the list — it's unsafe for cross-model reuse
    assert "mmproj-bf16.gguf" not in candidates


def test_projector_status_finds_model_specific_bf16_on_disk(runtime):
    """build_model_projector_status must detect a model-specific bf16 projector."""
    from core.model_state import build_model_projector_status

    models_dir = runtime.base_dir / "models"
    (models_dir / "mmproj-Qwen3.5-9B-bf16.gguf").write_bytes(b"bf16-projector")

    model = {
        "filename": "Qwen3.5-9B-Q4_K_S.gguf",
        "settings": {
            "vision": {"enabled": True, "projector_mode": "default", "projector_filename": None},
        },
    }
    status = build_model_projector_status(runtime, model)
    assert status["present"] is True
    assert "bf16" in status["filename"]


def test_projector_status_ignores_stale_generic_bf16_for_wrong_model(runtime):
    """A generic mmproj-bf16.gguf left over from a 9B download must NOT be
    reported as present for a 4B model — wrong dimensions would crash. #136."""
    from core.model_state import build_model_projector_status

    models_dir = runtime.base_dir / "models"
    (models_dir / "mmproj-bf16.gguf").write_bytes(b"stale-9b-bf16")

    model = {
        "filename": "Qwen3.5-4B-Q4_K_M.gguf",
        "settings": {
            "vision": {"enabled": True, "projector_mode": "default", "projector_filename": None},
        },
    }
    status = build_model_projector_status(runtime, model)
    assert status["present"] is False, (
        "Generic mmproj-bf16.gguf must not be accepted for a different model size"
    )


@pytest.mark.parametrize(
    "filename, expected_repo",
    [
        ("Qwen3.5-2B-Q4_K_M.gguf", "unsloth/Qwen3.5-2B-GGUF"),
        ("Qwen3.5-4B-Q4_K_M.gguf", "unsloth/Qwen3.5-4B-GGUF"),
        ("Qwen3.5-0.8B-Q4_K_M.gguf", "unsloth/Qwen3.5-0.8B-GGUF"),
        ("Qwen3.5-35B-A3B-Q2_K_L.gguf", "AesSedai/Qwen3.5-35B-A3B-GGUF"),
    ],
)
def test_projector_repo_existing_sizes_unchanged(filename: str, expected_repo: str):
    """Existing size mappings must not regress."""
    assert projector_repo_for_model(filename) == expected_repo


# ── Gemma 4 detection ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename",
    [
        "gemma-4-E2B-it-Q4_K_M.gguf",
        "gemma-4-E2B-it-UD-Q4_K_XL.gguf",
        "gemma-4-E4B-it-Q4_0.gguf",
        "gemma-4-E4B-it-Q8_0.gguf",
        "gemma-4-26B-A4B-it-UD-IQ2_M.gguf",
        "gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf",
    ],
)
def test_is_gemma4_filename_positive(filename: str):
    """All Gemma 4 variant filenames are detected."""
    assert is_gemma4_filename(filename) is True


@pytest.mark.parametrize(
    "filename",
    [
        "gemma-3-9b-it-Q4_K_M.gguf",
        "Qwen3.5-4B-Q4_K_M.gguf",
        "llama-4-scout-Q4_K_M.gguf",
        None,
        "",
    ],
)
def test_is_gemma4_filename_negative(filename):
    """Non-Gemma-4 filenames must not match."""
    assert is_gemma4_filename(filename) is False


# ── Gemma 4 projector repo resolution ──────────────────────────────


@pytest.mark.parametrize(
    "filename, expected_repo",
    [
        ("gemma-4-E2B-it-Q4_K_M.gguf", "unsloth/gemma-4-E2B-it-GGUF"),
        ("gemma-4-E2B-it-UD-Q4_K_XL.gguf", "unsloth/gemma-4-E2B-it-GGUF"),
        ("gemma-4-E2B-it-Q8_0.gguf", "unsloth/gemma-4-E2B-it-GGUF"),
        ("gemma-4-E2B-it-IQ4_NL.gguf", "unsloth/gemma-4-E2B-it-GGUF"),
        ("gemma-4-E4B-it-Q4_0.gguf", "unsloth/gemma-4-E4B-it-GGUF"),
        ("gemma-4-E4B-it-Q8_0.gguf", "unsloth/gemma-4-E4B-it-GGUF"),
        ("gemma-4-26B-A4B-it-UD-IQ2_M.gguf", "unsloth/gemma-4-26B-A4B-it-GGUF"),
        ("gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf", "unsloth/gemma-4-26B-A4B-it-GGUF"),
    ],
)
def test_projector_repo_for_gemma4(filename: str, expected_repo: str):
    """Each Gemma 4 variant resolves to its Unsloth projector repo."""
    assert projector_repo_for_model(filename) == expected_repo


def test_projector_repo_returns_none_for_unknown_gemma4_variant():
    """Unrecognized Gemma 4 variants must return None."""
    assert projector_repo_for_model("gemma-4-99B-it-Q4_K_M.gguf") is None


def test_projector_repo_gemma4_no_collision_with_quant_4():
    """'4' in Q4_K_M must not trigger Gemma 4 detection on non-Gemma models."""
    assert projector_repo_for_model("some-model-Q4_K_M.gguf") is None


def test_projector_repo_gemma4_does_not_break_qwen35():
    """Qwen3.5 repos must still resolve correctly with Gemma 4 code present."""
    assert projector_repo_for_model("Qwen3.5-2B-Q4_K_M.gguf") == "unsloth/Qwen3.5-2B-GGUF"
    assert projector_repo_for_model("Qwen3.5-9B-Q4_K_S.gguf") == "unsloth/Qwen3.5-9B-GGUF"


# ── Gemma 4 projector candidates ───────────────────────────────────


def test_default_candidates_gemma4_e2b():
    """Gemma 4 E2B produces model-specific f16/bf16 then generic F16."""
    from core.model_state import default_projector_candidates_for_model

    candidates = default_projector_candidates_for_model("gemma-4-E2B-it-Q4_K_M.gguf")
    assert len(candidates) > 0
    assert "mmproj-gemma-4-E2B-it-f16.gguf" in candidates
    assert "mmproj-gemma-4-E2B-it-bf16.gguf" in candidates
    assert "mmproj-F16.gguf" in candidates
    # Generic bf16 must NOT be in the list
    assert "mmproj-bf16.gguf" not in candidates
    # f16 model-specific should come before generic
    assert candidates.index("mmproj-gemma-4-E2B-it-f16.gguf") < candidates.index("mmproj-F16.gguf")


def test_default_candidates_gemma4_26b_a4b():
    """Gemma 4 26B-A4B stem-trimming strips the UD-IQ2_M quant suffix."""
    from core.model_state import default_projector_candidates_for_model

    candidates = default_projector_candidates_for_model("gemma-4-26B-A4B-it-UD-IQ2_M.gguf")
    assert "mmproj-gemma-4-26B-A4B-it-f16.gguf" in candidates
    assert "mmproj-F16.gguf" in candidates


def test_projector_status_gemma4_finds_model_specific_on_disk(runtime):
    """build_model_projector_status detects a Gemma 4 model-specific projector."""
    from core.model_state import build_model_projector_status

    models_dir = runtime.base_dir / "models"
    (models_dir / "mmproj-gemma-4-E2B-it-f16.gguf").write_bytes(b"g4-projector")

    model = {
        "filename": "gemma-4-E2B-it-Q4_K_M.gguf",
        "settings": {
            "vision": {"enabled": True, "projector_mode": "default", "projector_filename": None},
        },
    }
    status = build_model_projector_status(runtime, model)
    assert status["present"] is True
    assert "gemma-4-E2B-it" in status["filename"]


# ── Recommended runtime ────────────────────────────────────────────


def test_recommended_runtime_gemma4_is_ik_llama():
    """Gemma 4 models should prefer the ik_llama runtime."""
    assert recommended_runtime_for_model("gemma-4-E2B-it-Q4_K_M.gguf") == "ik_llama"
    assert recommended_runtime_for_model("gemma-4-E4B-it-Q4_0.gguf") == "ik_llama"
    assert recommended_runtime_for_model("gemma-4-26B-A4B-it-UD-IQ2_M.gguf") == "ik_llama"


def test_recommended_runtime_qwen35_has_no_preference():
    """Qwen3.5 models should have no runtime preference (None)."""
    assert recommended_runtime_for_model("Qwen3.5-2B-Q4_K_M.gguf") is None


def test_recommended_runtime_unknown_model_has_no_preference():
    """Unknown models should have no runtime preference."""
    assert recommended_runtime_for_model("some-random-model.gguf") is None
