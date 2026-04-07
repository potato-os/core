from __future__ import annotations

import re
from typing import Final

QWEN35_PROJECTOR_REPO_RULES: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("35b", "a3b", "hauhaucs"), "HauhauCS/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive"),
    (("35b", "a3b"), "AesSedai/Qwen3.5-35B-A3B-GGUF"),
    (("9b", "byteshape"), "byteshape/Qwen3.5-9B-GGUF"),
    (("9b",), "unsloth/Qwen3.5-9B-GGUF"),
    (("4b", "hauhaucs"), "HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive"),
    (("4b",), "unsloth/Qwen3.5-4B-GGUF"),
    (("2b",), "unsloth/Qwen3.5-2B-GGUF"),
    (("0.8b",), "unsloth/Qwen3.5-0.8B-GGUF"),
)


def _normalized_model_name(filename: str | None) -> str:
    return str(filename or "").strip().lower()


def is_qwen35_filename(filename: str | None) -> bool:
    model_name = _normalized_model_name(filename)
    return bool(model_name) and "qwen" in model_name and "3.5" in model_name


def _token_at_boundary(token: str, text: str) -> bool:
    """Match token only when surrounded by non-alphanumeric boundaries.

    Prevents substring collisions like '9b' matching inside '19b'
    or '2b' matching inside '3.92bpw'.
    """
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", text))


def _qwen35_projector_repo(filename: str | None, source_url: str | None = None) -> str | None:
    model_name = _normalized_model_name(filename)
    if not is_qwen35_filename(model_name):
        return None

    # Match tokens against filename + source URL so publisher-specific rules
    # fire even when the filename itself doesn't contain the publisher name.
    match_text = model_name
    if source_url:
        match_text = _normalized_model_name(source_url) + " " + match_text

    for required_tokens, repo in QWEN35_PROJECTOR_REPO_RULES:
        if all(_token_at_boundary(token, match_text) for token in required_tokens):
            return repo
    return None


GEMMA4_PROJECTOR_REPO_RULES: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("e2b",), "unsloth/gemma-4-E2B-it-GGUF"),
    (("e4b",), "unsloth/gemma-4-E4B-it-GGUF"),
    (("26b", "a4b"), "unsloth/gemma-4-26B-A4B-it-GGUF"),
)


def is_gemma4_filename(filename: str | None) -> bool:
    model_name = _normalized_model_name(filename)
    return bool(model_name) and "gemma" in model_name and _token_at_boundary("4", model_name)


def _gemma4_projector_repo(filename: str | None, source_url: str | None = None) -> str | None:
    model_name = _normalized_model_name(filename)
    if not is_gemma4_filename(model_name):
        return None

    match_text = model_name
    if source_url:
        match_text = _normalized_model_name(source_url) + " " + match_text

    for required_tokens, repo in GEMMA4_PROJECTOR_REPO_RULES:
        if all(_token_at_boundary(token, match_text) for token in required_tokens):
            return repo
    return None


def _is_gemma4_26b_a4b(filename: str | None) -> bool:
    model_name = _normalized_model_name(filename)
    return is_gemma4_filename(model_name) and "26b" in model_name and "a4b" in model_name


def recommended_runtime_for_model(filename: str | None) -> str | None:
    """Return the preferred runtime family for a model, or None for no preference."""
    if filename and _normalized_model_name(filename).endswith(".litertlm"):
        return "litert"
    if _is_gemma4_26b_a4b(filename):
        return "ik_llama"
    return None


def projector_repo_for_model(filename: str | None, source_url: str | None = None) -> str | None:
    return (
        _qwen35_projector_repo(filename, source_url=source_url)
        or _gemma4_projector_repo(filename, source_url=source_url)
    )
