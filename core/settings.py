"""Settings document helpers for the Potato OS YAML settings flow."""

from __future__ import annotations

import json
from typing import Any

import yaml

try:
    from core.model_state import (
        DEFAULT_MODEL_CHAT_SETTINGS,
        ModelSettingsValidationError,
        ensure_models_state,
        get_model_by_id,
        normalize_model_settings,
        save_models_state,
    )
    from core.runtime_state import (
        RuntimeConfig,
        normalize_allow_unsupported_large_models,
        normalize_llama_memory_loading_mode,
        read_llama_runtime_settings,
        write_llama_runtime_settings,
    )
except ModuleNotFoundError:
    from model_state import (  # type: ignore[no-redef]
        DEFAULT_MODEL_CHAT_SETTINGS,
        ModelSettingsValidationError,
        ensure_models_state,
        get_model_by_id,
        normalize_model_settings,
        save_models_state,
    )
    from runtime_state import (  # type: ignore[no-redef]
        RuntimeConfig,
        normalize_allow_unsupported_large_models,
        normalize_llama_memory_loading_mode,
        read_llama_runtime_settings,
        write_llama_runtime_settings,
    )


DEFAULT_CHAT_SETTINGS = DEFAULT_MODEL_CHAT_SETTINGS


def merge_chat_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge default chat settings into a request payload."""
    merged = dict(payload)
    for key, value in DEFAULT_CHAT_SETTINGS.items():
        if key == "seed" and "seed" not in merged:
            continue
        merged.setdefault(key, value)
    merged.setdefault("cache_prompt", True)
    return merged


def merge_active_model_chat_defaults(payload: dict[str, Any], *, runtime: RuntimeConfig) -> dict[str, Any]:
    """Merge the active model's persisted chat settings into a request payload."""
    merged = dict(payload)
    chat_settings = get_active_model_settings(runtime).get("chat", {})
    if not isinstance(chat_settings, dict):
        chat_settings = {}

    for key in (
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "presence_penalty",
        "max_tokens",
        "stream",
        "generation_mode",
        "cache_prompt",
    ):
        if key not in merged and key in chat_settings:
            merged[key] = chat_settings[key]

    if "seed" not in merged and str(chat_settings.get("generation_mode") or "").strip().lower() == "deterministic":
        merged["seed"] = chat_settings.get("seed")

    system_prompt = str(chat_settings.get("system_prompt") or "").strip()
    messages = merged.get("messages")
    if system_prompt and isinstance(messages, list):
        has_system_message = any(
            isinstance(message, dict) and str(message.get("role") or "").strip().lower() == "system"
            for message in messages
        )
        if not has_system_message:
            merged["messages"] = [{"role": "system", "content": system_prompt}, *messages]

    return merged


def get_active_model_settings(runtime: RuntimeConfig) -> dict[str, Any]:
    """Return the normalized settings dict for the currently active model."""
    state = ensure_models_state(runtime)
    active_model = get_model_by_id(state, str(state.get("active_model_id") or ""))
    if not isinstance(active_model, dict):
        active_model = state["models"][0]
    filename = str(active_model.get("filename") or "")
    return normalize_model_settings(active_model.get("settings"), filename=filename)


def build_settings_document_payload(runtime: RuntimeConfig) -> dict[str, Any]:
    """Build the full settings document payload for YAML export."""
    models_state = ensure_models_state(runtime)
    runtime_settings = read_llama_runtime_settings(runtime)
    models_payload: list[dict[str, Any]] = []
    for item in models_state.get("models", []):
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "")
        models_payload.append(
            {
                "id": str(item.get("id") or ""),
                "settings": normalize_model_settings(item.get("settings"), filename=filename),
            }
        )
    return {
        "version": 1,
        "active_model_id": str(models_state.get("active_model_id") or ""),
        "runtime": {
            "memory_loading_mode": str(runtime_settings.get("memory_loading_mode") or "auto"),
            "allow_unsupported_large_models": bool(runtime_settings.get("allow_unsupported_large_models", False)),
        },
        "models": models_payload,
    }


def export_settings_document_yaml(runtime: RuntimeConfig) -> str:
    """Export settings as YAML string."""
    return yaml.safe_dump(build_settings_document_payload(runtime), sort_keys=False, allow_unicode=True)


def apply_settings_document_yaml(runtime: RuntimeConfig, document: str) -> tuple[bool, str, dict[str, Any]]:
    """Apply a YAML settings document atomically."""
    try:
        payload = yaml.safe_load(document) or {}
    except yaml.YAMLError:
        return False, "invalid_yaml", {}
    if not isinstance(payload, dict):
        return False, "invalid_document", {}

    current_models_state = ensure_models_state(runtime)
    next_models_state = json.loads(json.dumps(current_models_state))
    next_runtime_settings = read_llama_runtime_settings(runtime)

    active_model_id = str(payload.get("active_model_id") or next_models_state.get("active_model_id") or "").strip()
    model_entries = payload.get("models")
    if model_entries is not None and not isinstance(model_entries, list):
        return False, "invalid_models", {}

    if isinstance(model_entries, list):
        for item in model_entries:
            if not isinstance(item, dict):
                return False, "invalid_models", {}
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                return False, "model_id_required", {}
            model = get_model_by_id(next_models_state, model_id)
            if model is None:
                return False, "model_not_found", {"model_id": model_id}
            filename = str(model.get("filename") or "")
            try:
                model["settings"] = normalize_model_settings(item.get("settings"), filename=filename)
            except ModelSettingsValidationError as exc:
                return False, "invalid_settings", {"field": exc.field, "model_id": model_id}

    if active_model_id:
        if get_model_by_id(next_models_state, active_model_id) is None:
            return False, "active_model_not_found", {"active_model_id": active_model_id}
        next_models_state["active_model_id"] = active_model_id

    runtime_payload = payload.get("runtime")
    if runtime_payload is not None:
        if not isinstance(runtime_payload, dict):
            return False, "invalid_runtime", {}
        if "memory_loading_mode" in runtime_payload:
            next_runtime_settings["memory_loading_mode"] = normalize_llama_memory_loading_mode(
                runtime_payload.get("memory_loading_mode")
            )
        if "allow_unsupported_large_models" in runtime_payload:
            next_runtime_settings["allow_unsupported_large_models"] = normalize_allow_unsupported_large_models(
                runtime_payload.get("allow_unsupported_large_models")
            )

    save_models_state(runtime, next_models_state)
    write_llama_runtime_settings(
        runtime,
        memory_loading_mode=str(next_runtime_settings.get("memory_loading_mode") or "auto"),
        allow_unsupported_large_models=bool(next_runtime_settings.get("allow_unsupported_large_models", False)),
        power_calibration=next_runtime_settings.get("power_calibration"),
    )
    return True, "updated", build_settings_document_payload(runtime)
