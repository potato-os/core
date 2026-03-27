from __future__ import annotations

from typing import Final

QWEN35_PROJECTOR_REPO_RULES: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("35b", "a3b", "hauhaucs"), "HauhauCS/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive"),
    (("35b", "a3b"), "AesSedai/Qwen3.5-35B-A3B-GGUF"),
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


def _qwen35_projector_repo(filename: str | None) -> str | None:
    model_name = _normalized_model_name(filename)
    if not is_qwen35_filename(model_name):
        return None

    for required_tokens, repo in QWEN35_PROJECTOR_REPO_RULES:
        if all(token in model_name for token in required_tokens):
            return repo
    return None


def projector_repo_for_model(filename: str | None) -> str | None:
    return _qwen35_projector_repo(filename)
