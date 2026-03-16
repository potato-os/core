from __future__ import annotations

import json
import logging
import shutil
import time
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:
    from app.runtime_state import RuntimeConfig, _atomic_write_json
except ModuleNotFoundError:
    from runtime_state import RuntimeConfig, _atomic_write_json  # type: ignore[no-redef]

logger = logging.getLogger("potato")

MODEL_FILENAME = "Qwen3.5-2B-Q4_K_M.gguf"
MODEL_URL = (
    "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/"
    "Qwen3.5-2B-Q4_K_M.gguf"
)
MODELS_STATE_VERSION = 1

DEFAULT_MODEL_CHAT_SETTINGS = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "repetition_penalty": 1.0,
    "presence_penalty": 1.5,
    "max_tokens": 16384,
    "stream": True,
    "generation_mode": "random",
    "seed": 42,
    "system_prompt": "",
    "cache_prompt": True,
}

DEFAULT_MODEL_VISION_SETTINGS = {
    "enabled": False,
    "projector_mode": "default",
    "projector_filename": None,
}


class ModelSettingsValidationError(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


def _model_file_path(runtime: RuntimeConfig, filename: str) -> Path:
    return runtime.base_dir / "models" / filename


def _sanitize_filename(filename: str) -> str:
    candidate = Path(filename).name.strip()
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate)
    candidate = candidate.lstrip(".")
    return candidate or "model.gguf"


def _slugify_id(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug or "model"


def is_qwen35_a3b_filename(filename: str | None) -> bool:
    value = str(filename or "").strip().lower()
    return bool(value) and "qwen" in value and "3.5" in value and "35b" in value and "a3b" in value


def model_supports_vision_filename(filename: str | None) -> bool:
    value = str(filename or "").strip().lower()
    if not value:
        return False
    if "qwen3" in value and "vl" in value:
        return True
    if "qwen" in value and "3.5" in value:
        return True
    return False


def _coerce_float_setting(raw_value: Any, *, field: str, default: float) -> float:
    value = default if raw_value is None else raw_value
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ModelSettingsValidationError(field) from exc


def _coerce_int_setting(raw_value: Any, *, field: str, default: int) -> int:
    value = default if raw_value is None else raw_value
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ModelSettingsValidationError(field) from exc


def _normalize_chat_settings(raw_value: Any) -> dict[str, Any]:
    raw = raw_value if isinstance(raw_value, dict) else {}
    return {
        "temperature": _coerce_float_setting(
            raw.get("temperature"),
            field="chat.temperature",
            default=DEFAULT_MODEL_CHAT_SETTINGS["temperature"],
        ),
        "top_p": _coerce_float_setting(
            raw.get("top_p"),
            field="chat.top_p",
            default=DEFAULT_MODEL_CHAT_SETTINGS["top_p"],
        ),
        "top_k": _coerce_int_setting(
            raw.get("top_k"),
            field="chat.top_k",
            default=DEFAULT_MODEL_CHAT_SETTINGS["top_k"],
        ),
        "repetition_penalty": _coerce_float_setting(
            raw.get("repetition_penalty"),
            field="chat.repetition_penalty",
            default=DEFAULT_MODEL_CHAT_SETTINGS["repetition_penalty"],
        ),
        "presence_penalty": _coerce_float_setting(
            raw.get("presence_penalty"),
            field="chat.presence_penalty",
            default=DEFAULT_MODEL_CHAT_SETTINGS["presence_penalty"],
        ),
        "max_tokens": _coerce_int_setting(
            raw.get("max_tokens"),
            field="chat.max_tokens",
            default=DEFAULT_MODEL_CHAT_SETTINGS["max_tokens"],
        ),
        "stream": bool(raw.get("stream", DEFAULT_MODEL_CHAT_SETTINGS["stream"])),
        "generation_mode": (
            "deterministic"
            if str(raw.get("generation_mode", DEFAULT_MODEL_CHAT_SETTINGS["generation_mode"])).strip().lower() == "deterministic"
            else "random"
        ),
        "seed": _coerce_int_setting(
            raw.get("seed"),
            field="chat.seed",
            default=DEFAULT_MODEL_CHAT_SETTINGS["seed"],
        ),
        "system_prompt": str(raw.get("system_prompt", DEFAULT_MODEL_CHAT_SETTINGS["system_prompt"]) or ""),
        "cache_prompt": bool(raw.get("cache_prompt", DEFAULT_MODEL_CHAT_SETTINGS["cache_prompt"])),
    }


def _normalize_vision_settings(raw_value: Any, *, filename: str) -> dict[str, Any]:
    raw = raw_value if isinstance(raw_value, dict) else {}
    default_enabled = model_supports_vision_filename(filename)
    projector_filename_raw = raw.get("projector_filename")
    projector_filename = None
    if projector_filename_raw is not None:
        value = str(projector_filename_raw).strip()
        projector_filename = value or None
    projector_mode = str(raw.get("projector_mode", DEFAULT_MODEL_VISION_SETTINGS["projector_mode"]) or "default").strip().lower()
    if projector_mode not in {"default", "custom"}:
        projector_mode = "default"
    return {
        "enabled": bool(raw.get("enabled", default_enabled)),
        "projector_mode": projector_mode,
        "projector_filename": projector_filename,
    }


def normalize_model_settings(raw_value: Any, *, filename: str) -> dict[str, Any]:
    raw = raw_value if isinstance(raw_value, dict) else {}
    return {
        "chat": _normalize_chat_settings(raw.get("chat")),
        "vision": _normalize_vision_settings(raw.get("vision"), filename=filename),
    }


def build_model_capabilities(filename: str | None) -> dict[str, Any]:
    return {
        "vision": model_supports_vision_filename(filename),
    }


def apply_model_chat_defaults(payload: dict[str, Any], *, active_model_filename: str | None) -> dict[str, Any]:
    if not is_qwen35_a3b_filename(active_model_filename):
        return payload

    chat_template_kwargs = payload.get("chat_template_kwargs")
    if isinstance(chat_template_kwargs, dict) and "enable_thinking" in chat_template_kwargs:
        return payload

    updated = dict(payload)
    if isinstance(chat_template_kwargs, dict):
        merged = dict(chat_template_kwargs)
    else:
        merged = {}
    merged["enable_thinking"] = False
    updated["chat_template_kwargs"] = merged
    return updated


def _unique_model_id(base_id: str, existing_ids: set[str]) -> str:
    candidate = base_id
    idx = 2
    while candidate in existing_ids:
        candidate = f"{base_id}-{idx}"
        idx += 1
    return candidate


def _unique_filename(base_name: str, existing_names: set[str]) -> str:
    stem = Path(base_name).stem
    suffix = Path(base_name).suffix or ".gguf"
    candidate = f"{stem}{suffix}"
    idx = 2
    while candidate in existing_names:
        candidate = f"{stem}-{idx}{suffix}"
        idx += 1
    return candidate


def validate_model_url(source_url: str) -> tuple[bool, str, str]:
    parsed = urlparse(source_url.strip())
    if parsed.scheme != "https":
        return False, "https_required", ""
    basename = unquote(Path(parsed.path).name)
    if not basename:
        return False, "filename_missing", ""
    if not basename.lower().endswith(".gguf"):
        return False, "gguf_required", ""
    safe_name = _sanitize_filename(basename)
    if not safe_name.lower().endswith(".gguf"):
        safe_name = f"{Path(safe_name).stem}.gguf"
    return True, "", safe_name


def _default_model_record(_runtime: RuntimeConfig) -> dict[str, Any]:
    return {
        "id": "default",
        "filename": MODEL_FILENAME,
        "source_url": MODEL_URL,
        "source_type": "url",
        "status": "not_downloaded",
        "error": None,
        "settings": normalize_model_settings(None, filename=MODEL_FILENAME),
    }


def _is_discoverable_local_model_filename(filename: str) -> bool:
    name = _sanitize_filename(filename)
    if not name.lower().endswith(".gguf"):
        return False
    stem = Path(name).stem.lower()
    if stem.startswith("mmproj") or "mmproj" in stem:
        return False
    return True


def _discover_local_model_filenames(runtime: RuntimeConfig) -> list[str]:
    model_dir = runtime.base_dir / "models"
    try:
        children = list(model_dir.iterdir())
    except OSError:
        return []
    names: list[str] = []
    for child in children:
        if not child.is_file():
            continue
        filename = _sanitize_filename(child.name)
        if not _is_discoverable_local_model_filename(filename):
            continue
        names.append(filename)
    return sorted(set(names))


def model_file_present(runtime: RuntimeConfig, filename: str) -> bool:
    path = _model_file_path(runtime, filename)
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


def any_model_ready(runtime: RuntimeConfig) -> bool:
    """Return True if any model in the models state has a file on disk."""
    state = ensure_models_state(runtime)
    models = state.get("models") or []
    for model in models:
        filename = str(model.get("filename") or "").strip()
        if filename and model_file_present(runtime, filename):
            return True
    return False


def resolve_model_runtime_path(runtime: RuntimeConfig, filename: str) -> Path:
    path = _model_file_path(runtime, filename)
    try:
        if path.is_symlink():
            return path.resolve(strict=False)
    except OSError:
        return path
    return path


def describe_model_storage(runtime: RuntimeConfig, filename: str, *, ssd_dir: Path | None = None) -> dict[str, Any]:
    path = _model_file_path(runtime, filename)
    managed_models_dir = runtime.base_dir / "models"
    actual_path = path
    is_symlink = False
    size_bytes = 0
    exists = False

    try:
        is_symlink = path.is_symlink()
    except OSError:
        is_symlink = False

    try:
        exists = path.exists()
    except OSError:
        exists = False

    if is_symlink:
        try:
            actual_path = path.resolve(strict=False)
        except OSError:
            actual_path = path
    if exists:
        try:
            size_bytes = max(0, int(actual_path.stat().st_size))
        except OSError:
            size_bytes = 0

    location = "local"
    if ssd_dir is not None:
        try:
            if actual_path.is_relative_to(ssd_dir):
                location = "ssd"
        except (OSError, ValueError):
            location = "local"
    elif is_symlink:
        try:
            if not actual_path.is_relative_to(managed_models_dir):
                location = "ssd"
        except (OSError, ValueError):
            location = "ssd"

    return {
        "location": location,
        "is_symlink": is_symlink,
        "actual_path": str(actual_path),
        "size_bytes": size_bytes,
        "exists": exists,
    }


def _normalize_models_state(runtime: RuntimeConfig, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = raw or {}
    models_raw = payload.get("models")
    models: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_filenames: set[str] = set()

    if isinstance(models_raw, list):
        for item in models_raw:
            if not isinstance(item, dict):
                continue
            source_url = str(item.get("source_url") or "")
            filename = _sanitize_filename(str(item.get("filename") or ""))
            if not filename.lower().endswith(".gguf"):
                filename = f"{Path(filename).stem}.gguf"
            item_id_raw = str(item.get("id") or _slugify_id(Path(filename).stem))
            item_id = _unique_model_id(_slugify_id(item_id_raw), seen_ids)
            filename = _unique_filename(filename, seen_filenames)
            seen_ids.add(item_id)
            seen_filenames.add(filename)
            source_type_raw = str(item.get("source_type") or "").strip().lower()
            if source_url:
                source_type = source_type_raw or "url"
            elif source_type_raw in {"upload", "local_file"}:
                source_type = source_type_raw
            else:
                source_type = "upload"
            models.append(
                {
                    "id": item_id,
                    "filename": filename,
                    "source_url": source_url or None,
                    "source_type": source_type,
                    "status": str(item.get("status") or "not_downloaded"),
                    "error": item.get("error"),
                    "settings": normalize_model_settings(item.get("settings"), filename=filename),
                }
            )

    if not models:
        default_model = _default_model_record(runtime)
        models.append(default_model)
        seen_ids.add(default_model["id"])
        seen_filenames.add(default_model["filename"])
    elif "default" not in seen_ids:
        default_model = _default_model_record(runtime)
        default_model["id"] = _unique_model_id("default", seen_ids)
        default_model["filename"] = _unique_filename(default_model["filename"], seen_filenames)
        models.insert(0, default_model)
        seen_ids.add(default_model["id"])
        seen_filenames.add(default_model["filename"])

    for local_filename in _discover_local_model_filenames(runtime):
        if local_filename in seen_filenames:
            continue
        local_id = _unique_model_id(_slugify_id(Path(local_filename).stem), seen_ids)
        models.append(
            {
                "id": local_id,
                "filename": local_filename,
                "source_url": None,
                "source_type": "local_file",
                "status": "ready",
                "error": None,
                "settings": normalize_model_settings(None, filename=local_filename),
            }
        )
        seen_ids.add(local_id)
        seen_filenames.add(local_filename)

    runtime_model_name = _sanitize_filename(getattr(runtime.model_path, "name", ""))
    active_model_id = str(payload.get("active_model_id") or "").strip()
    if active_model_id not in seen_ids:
        runtime_match = next(
            (
                str(item.get("id") or "")
                for item in models
                if isinstance(item, dict) and _sanitize_filename(str(item.get("filename") or "")) == runtime_model_name
            ),
            "",
        )
        active_model_id = runtime_match or models[0]["id"]

    default_model_id = str(payload.get("default_model_id") or "default")
    if default_model_id not in seen_ids:
        default_model_id = "default" if "default" in seen_ids else models[0]["id"]

    current_download_model_id = payload.get("current_download_model_id")
    if current_download_model_id not in seen_ids:
        current_download_model_id = None

    return {
        "version": MODELS_STATE_VERSION,
        "countdown_enabled": bool(payload.get("countdown_enabled", True)),
        "default_model_downloaded_once": bool(payload.get("default_model_downloaded_once", False)),
        "active_model_id": active_model_id,
        "default_model_id": default_model_id,
        "current_download_model_id": current_download_model_id,
        "models": models,
    }


def ensure_models_state(runtime: RuntimeConfig) -> dict[str, Any]:
    raw: dict[str, Any] | None = None
    if runtime.models_state_path.exists():
        try:
            loaded = json.loads(runtime.models_state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError):
            raw = None

    normalized = _normalize_models_state(runtime, raw)
    default_model_id = str(normalized.get("default_model_id") or "default")
    default_model = get_model_by_id(normalized, default_model_id)
    if isinstance(default_model, dict):
        default_filename = str(default_model.get("filename") or "")
        if default_filename == MODEL_FILENAME and model_file_present(runtime, default_filename):
            normalized["default_model_downloaded_once"] = True
    _atomic_write_json(runtime.models_state_path, normalized)
    return normalized


def save_models_state(runtime: RuntimeConfig, state: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_models_state(runtime, state)
    _atomic_write_json(runtime.models_state_path, normalized)
    return normalized


def get_model_by_id(state: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    for item in state.get("models", []):
        if isinstance(item, dict) and item.get("id") == model_id:
            return item
    return None


def update_model_settings(
    runtime: RuntimeConfig,
    *,
    model_id: str,
    settings: dict[str, Any],
) -> tuple[bool, str, dict[str, Any] | None]:
    state = ensure_models_state(runtime)
    model = get_model_by_id(state, model_id)
    if model is None:
        return False, "model_not_found", None
    filename = str(model.get("filename") or "")
    try:
        model["settings"] = normalize_model_settings(settings, filename=filename)
    except ModelSettingsValidationError:
        return False, "invalid_settings", None
    saved = save_models_state(runtime, state)
    updated = get_model_by_id(saved, model_id)
    return True, "updated", updated


def resolve_active_model(state: dict[str, Any], runtime: RuntimeConfig) -> tuple[dict[str, Any], Path]:
    active_id = str(state.get("active_model_id") or "")
    model = get_model_by_id(state, active_id)
    if model is None:
        model = state["models"][0]
        state["active_model_id"] = model["id"]
    path = resolve_model_runtime_path(runtime, str(model["filename"]))
    runtime.model_path = path
    return model, path


def model_present(runtime: RuntimeConfig) -> bool:
    state = ensure_models_state(runtime)
    _, active_model_path = resolve_active_model(state, runtime)
    try:
        return active_model_path.exists() and active_model_path.stat().st_size > 0
    except OSError:
        return False


def set_download_countdown_enabled(runtime: RuntimeConfig, enabled: bool) -> dict[str, Any]:
    state = ensure_models_state(runtime)
    state["countdown_enabled"] = bool(enabled)
    return save_models_state(runtime, state)


def register_model_url(runtime: RuntimeConfig, source_url: str, alias: str | None = None) -> tuple[bool, str, dict[str, Any] | None]:
    ok, reason, filename = validate_model_url(source_url)
    if not ok:
        return False, reason, None

    state = ensure_models_state(runtime)
    models = state.get("models", [])
    assert isinstance(models, list)
    existing_ids = {str(item.get("id")) for item in models if isinstance(item, dict)}
    existing_names = {str(item.get("filename")) for item in models if isinstance(item, dict)}

    for item in models:
        if isinstance(item, dict) and str(item.get("source_url") or "") == source_url:
            saved = save_models_state(runtime, state)
            model = get_model_by_id(saved, str(item.get("id") or ""))
            return True, "already_exists", model

    preferred_name = filename
    if alias:
        alias_safe = _sanitize_filename(alias)
        if not alias_safe.lower().endswith(".gguf"):
            alias_safe = f"{Path(alias_safe).stem}.gguf"
        if alias_safe:
            preferred_name = alias_safe

    final_name = _unique_filename(preferred_name, existing_names)
    model_id = _unique_model_id(_slugify_id(Path(final_name).stem), existing_ids)
    model_record = {
        "id": model_id,
        "filename": final_name,
        "source_url": source_url,
        "source_type": "url",
        "status": "ready" if model_file_present(runtime, final_name) else "not_downloaded",
        "error": None,
    }
    models.append(model_record)
    saved = save_models_state(runtime, state)
    created = get_model_by_id(saved, model_id)
    return True, "registered", created


def move_model_to_ssd(runtime: RuntimeConfig, *, model_id: str, ssd_dir: Path) -> tuple[bool, str, dict[str, Any]]:
    state = ensure_models_state(runtime)
    model = get_model_by_id(state, model_id)
    if model is None:
        return False, "model_not_found", {"location": "unknown", "is_symlink": False, "actual_path": "", "size_bytes": 0, "exists": False}

    filename = str(model.get("filename") or "")
    source_path = _model_file_path(runtime, filename)
    storage = describe_model_storage(runtime, filename, ssd_dir=ssd_dir)
    if storage["location"] == "ssd":
        return False, "already_on_ssd", storage
    if not model_file_present(runtime, filename):
        return False, "model_not_ready", storage

    try:
        ssd_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False, "no_ssd_available", storage

    target_path = ssd_dir / filename
    if target_path.exists():
        return False, "target_exists", storage

    temp_target = ssd_dir / f".{filename}.copying-{int(time.time() * 1000)}"
    temp_link = source_path.with_name(f".{source_path.name}.ssd-link")
    backup_source = source_path.with_name(f".{source_path.name}.local-backup")

    try:
        shutil.copy2(source_path, temp_target)
        temp_target.replace(target_path)
        temp_link.unlink(missing_ok=True)
        temp_link.symlink_to(target_path)
        backup_source.unlink(missing_ok=True)
        source_path.replace(backup_source)
        try:
            temp_link.replace(source_path)
        except OSError:
            if backup_source.exists():
                backup_source.replace(source_path)
            raise
        backup_source.unlink(missing_ok=True)
    except OSError:
        temp_link.unlink(missing_ok=True)
        temp_target.unlink(missing_ok=True)
        logger.warning("Could not move model to SSD: %s", source_path, exc_info=True)
        return False, "move_failed", describe_model_storage(runtime, filename, ssd_dir=ssd_dir)

    return True, "moved", describe_model_storage(runtime, filename, ssd_dir=ssd_dir)


def delete_model(runtime: RuntimeConfig, *, model_id: str) -> tuple[bool, str, bool, int, bool]:
    state = ensure_models_state(runtime)
    active_model_id = str(state.get("active_model_id") or "")
    target = get_model_by_id(state, model_id)
    if target is None:
        return False, "model_not_found", False, 0, False
    was_active = model_id == active_model_id

    filename = str(target.get("filename") or "")
    models = state.get("models", [])
    assert isinstance(models, list)

    same_filename_elsewhere = any(
        isinstance(item, dict)
        and str(item.get("id") or "") != model_id
        and str(item.get("filename") or "") == filename
        for item in models
    )

    deleted_file = False
    freed_bytes = 0
    if filename and not same_filename_elsewhere:
        candidate_paths = (
            _model_file_path(runtime, filename),
            _model_file_path(runtime, filename + ".part"),
        )
        for candidate_path in candidate_paths:
            candidate_is_symlink = False
            try:
                candidate_is_symlink = candidate_path.is_symlink()
            except OSError:
                candidate_is_symlink = False
            if not candidate_path.exists() and not candidate_is_symlink:
                continue

            target_path: Path | None = None
            file_size = 0
            if candidate_is_symlink:
                try:
                    target_path = candidate_path.resolve(strict=False)
                    if target_path.exists():
                        file_size = max(0, target_path.stat().st_size)
                except OSError:
                    target_path = None
                    file_size = 0
            else:
                try:
                    file_size = max(0, candidate_path.stat().st_size)
                except OSError:
                    file_size = 0
            try:
                candidate_path.unlink(missing_ok=True)
                if target_path is not None and target_path.exists():
                    target_path.unlink(missing_ok=True)
                deleted_file = True
                freed_bytes += file_size
            except OSError:
                logger.warning("Could not delete model file: %s", candidate_path, exc_info=True)
                return False, "delete_failed", False, 0, was_active

    remaining_models = [
        item
        for item in models
        if not (isinstance(item, dict) and str(item.get("id") or "") == model_id)
    ]
    state["models"] = remaining_models
    if str(state.get("current_download_model_id") or "") == model_id:
        state["current_download_model_id"] = None
    if was_active:
        next_active_id: str | None = None
        for item in remaining_models:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("id") or "")
            candidate_name = str(item.get("filename") or "")
            if candidate_id and model_file_present(runtime, candidate_name):
                next_active_id = candidate_id
                break
        if next_active_id is None:
            for item in remaining_models:
                if isinstance(item, dict) and item.get("id"):
                    next_active_id = str(item["id"])
                    break
        if next_active_id is None:
            next_active_id = str(state.get("default_model_id") or "default")
        state["active_model_id"] = next_active_id
    save_models_state(runtime, state)
    return True, "deleted", deleted_file, freed_bytes, was_active
