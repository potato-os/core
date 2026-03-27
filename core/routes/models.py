"""Model management routes — download, register, upload, activate, delete, purge."""

from __future__ import annotations

import asyncio
import logging
import types
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

try:
    from core.deps import get_runtime
    from core.model_state import (
        get_model_by_id,
        normalize_model_settings,
        save_models_state,
        set_download_countdown_enabled,
        update_model_settings,
        register_model_url,
        _model_file_path,
        _slugify_id,
        _unique_filename,
        _unique_model_id,
    )
    from core.runtime_state import (
        RuntimeConfig,
        build_large_model_compatibility,
        _safe_int,
    )
except ModuleNotFoundError:
    from deps import get_runtime  # type: ignore[no-redef]
    from model_state import (  # type: ignore[no-redef]
        get_model_by_id,
        normalize_model_settings,
        save_models_state,
        set_download_countdown_enabled,
        update_model_settings,
        register_model_url,
        _model_file_path,
        _slugify_id,
        _unique_filename,
        _unique_model_id,
    )
    from runtime_state import (  # type: ignore[no-redef]
        RuntimeConfig,
        build_large_model_compatibility,
        _safe_int,
    )

logger = logging.getLogger(__name__)

router = APIRouter()

# Late-bound references — resolved via the main module so monkeypatching works.
_main: types.ModuleType | None = None


def register_models_helpers(*, main_module: types.ModuleType):
    global _main
    _main = main_module


@router.post("/internal/start-model-download")
async def start_model_download_now(request: Request, runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"started": False, "reason": "orchestrator_disabled"},
        )

    started, reason = await _main.start_model_download(request.app, runtime_cfg, trigger="manual")
    status_code = 202 if started else 200
    content: dict[str, Any] = {"started": started, "reason": reason}
    if reason == "insufficient_storage":
        dl = _main.read_download_progress(runtime_cfg)
        content["free_bytes"] = dl.get("free_bytes")
        content["required_bytes"] = dl.get("required_bytes")
    return JSONResponse(status_code=status_code, content=content)


@router.post("/internal/download-countdown")
async def set_download_countdown(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"updated": False, "reason": "orchestrator_disabled"},
        )
    payload = await request.json()
    enabled = bool(payload.get("enabled", True))
    updated_state = set_download_countdown_enabled(runtime_cfg, enabled)
    return JSONResponse(
        status_code=200,
        content={
            "updated": True,
            "reason": "countdown_updated",
            "countdown_enabled": bool(updated_state.get("countdown_enabled", enabled)),
        },
    )


@router.post("/internal/models/register")
async def register_model_endpoint(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    payload = await request.json()
    source_url = str(payload.get("source_url") or "").strip()
    alias_raw = payload.get("alias")
    alias = str(alias_raw).strip() if isinstance(alias_raw, str) and alias_raw.strip() else None
    ok, reason, model = register_model_url(runtime_cfg, source_url=source_url, alias=alias)
    if not ok:
        return JSONResponse(status_code=400, content={"ok": False, "reason": reason})
    response_payload: dict[str, Any] = {"ok": True, "reason": reason, "model": model}
    if isinstance(model, dict):
        model_filename = str(model.get("filename") or "")
        model_source_url = str(model.get("source_url") or "")
        size_bytes = 0
        if model_source_url:
            size_bytes = await _main.fetch_remote_content_length_bytes(model_source_url)
        compatibility = build_large_model_compatibility(
            runtime_cfg,
            model_filename=model_filename,
            model_size_bytes=size_bytes or None,
        )
        warnings = compatibility.get("warnings")
        if isinstance(warnings, list) and warnings:
            response_payload["warnings"] = warnings
    return JSONResponse(status_code=200, content=response_payload)


@router.post("/internal/models/download")
async def start_selected_model_download(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"started": False, "reason": "orchestrator_disabled"},
        )
    payload = await request.json()
    model_id = str(payload.get("model_id") or "").strip()
    if not model_id:
        return JSONResponse(status_code=400, content={"started": False, "reason": "model_id_required"})
    started, reason = await _main.start_model_download(
        request.app,
        runtime_cfg,
        trigger="manual-model",
        model_id=model_id,
    )
    status_code = 202 if started else 200
    content: dict[str, Any] = {"started": started, "reason": reason, "model_id": model_id}
    if reason == "insufficient_storage":
        dl = _main.read_download_progress(runtime_cfg)
        content["free_bytes"] = dl.get("free_bytes")
        content["required_bytes"] = dl.get("required_bytes")
    return JSONResponse(status_code=status_code, content=content)


@router.post("/internal/models/settings")
async def update_model_settings_endpoint(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    payload = await request.json()
    model_id = str(payload.get("model_id") or "").strip()
    settings = payload.get("settings")
    if not model_id:
        return JSONResponse(status_code=400, content={"updated": False, "reason": "model_id_required"})
    if not isinstance(settings, dict):
        return JSONResponse(status_code=400, content={"updated": False, "reason": "settings_required"})
    updated, reason, model = update_model_settings(runtime_cfg, model_id=model_id, settings=settings)
    if not updated:
        status_code = 404 if reason == "model_not_found" else 400
        return JSONResponse(status_code=status_code, content={"updated": False, "reason": reason, "model_id": model_id})
    restarted = False
    restart_reason = "not_required"
    state = _main.ensure_models_state(runtime_cfg)
    if model_id == str(state.get("active_model_id") or ""):
        restarted, restart_reason = await _main.restart_managed_llama_process(request.app)
    return JSONResponse(
        status_code=200,
        content={
            "updated": True,
            "reason": reason,
            "model_id": model_id,
            "model": model,
            "restarted": restarted,
            "restart_reason": restart_reason,
        },
    )


@router.post("/internal/models/download-projector")
async def download_projector_for_model_endpoint(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    payload = await request.json()
    model_id = str(payload.get("model_id") or "").strip()
    if not model_id:
        return JSONResponse(status_code=400, content={"downloaded": False, "reason": "model_id_required"})
    downloaded, reason, projector_filename = await asyncio.to_thread(
        _main.download_default_projector_for_model,
        runtime=runtime_cfg,
        model_id=model_id,
    )
    if not downloaded:
        return JSONResponse(
            status_code=400 if reason != "model_not_found" else 404,
            content={"downloaded": False, "reason": reason, "model_id": model_id},
        )
    state = _main.ensure_models_state(runtime_cfg)
    model = get_model_by_id(state, model_id)
    if isinstance(model, dict):
        settings = normalize_model_settings(model.get("settings"), filename=str(model.get("filename") or ""))
        settings["vision"]["projector_filename"] = projector_filename
        model["settings"] = settings
        save_models_state(runtime_cfg, state)
    restarted = False
    restart_reason = "not_required"
    if str(state.get("active_model_id") or "") == model_id:
        restarted, restart_reason = await _main.restart_managed_llama_process(request.app)
    return JSONResponse(
        status_code=200,
        content={
            "downloaded": True,
            "reason": reason,
            "model_id": model_id,
            "projector_filename": projector_filename,
            "restarted": restarted,
            "restart_reason": restart_reason,
        },
    )


@router.post("/internal/models/cancel-download")
async def cancel_selected_model_download(request: Request, runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"cancelled": False, "reason": "orchestrator_disabled"},
        )
    cancelled, reason = await _main.cancel_model_download(request.app, runtime_cfg)
    return JSONResponse(status_code=200, content={"cancelled": cancelled, "reason": reason})


@router.post("/internal/models/activate")
async def activate_model_endpoint(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"switched": False, "reason": "orchestrator_disabled"},
        )
    payload = await request.json()
    model_id = str(payload.get("model_id") or "").strip()
    if not model_id:
        return JSONResponse(status_code=400, content={"switched": False, "reason": "model_id_required"})
    switched, reason, restarted = await _main.activate_model(request.app, runtime_cfg, model_id=model_id)
    status_code = 200 if switched else (409 if reason in {"model_not_ready", "model_not_found"} else 400)
    return JSONResponse(
        status_code=status_code,
        content={"switched": switched, "reason": reason, "restarted": restarted, "model_id": model_id},
    )


@router.post("/internal/models/delete")
async def delete_model_endpoint(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"deleted": False, "reason": "orchestrator_disabled"},
        )
    payload = await request.json()
    model_id = str(payload.get("model_id") or "").strip()
    if not model_id:
        return JSONResponse(status_code=400, content={"deleted": False, "reason": "model_id_required"})
    cancelled_download = False

    try:
        from core.model_state import delete_model, resolve_active_model
    except ModuleNotFoundError:
        from model_state import delete_model, resolve_active_model  # type: ignore[no-redef]

    async with request.app.state.download_lock:
        models_state = _main.ensure_models_state(runtime_cfg)
        current_download_model_id = str(models_state.get("current_download_model_id") or "").strip()
        if current_download_model_id == model_id and _main.is_download_task_active(request.app.state.model_download_task):
            cancelled_download, cancel_reason = await _main._cancel_model_download_locked(
                request.app,
                runtime_cfg,
                expected_model_id=model_id,
                timeout_seconds=_main.MODEL_DOWNLOAD_CANCEL_WAIT_TIMEOUT_SECONDS,
            )
            if not cancelled_download:
                reason = "delete_cancel_timeout" if cancel_reason == "cancel_timeout" else "delete_cancel_failed"
                status_code = 409 if reason == "delete_cancel_timeout" else 400
                return JSONResponse(
                    status_code=status_code,
                    content={
                        "deleted": False,
                        "reason": reason,
                        "model_id": model_id,
                        "deleted_file": False,
                        "freed_bytes": 0,
                        "cancelled_download": False,
                    },
                )
        deleted, reason, deleted_file, freed_bytes, deleted_active = delete_model(runtime_cfg, model_id=model_id)
    restarted = False
    restart_reason = "not_required"
    if deleted and deleted_active:
        models_state = _main.ensure_models_state(runtime_cfg)
        resolve_active_model(models_state, runtime_cfg)
        restarted, restart_reason = await _main.restart_managed_llama_process(request.app)
    if deleted:
        return JSONResponse(
            status_code=200,
            content={
                "deleted": True,
                "reason": reason,
                "model_id": model_id,
                "deleted_file": deleted_file,
                "freed_bytes": int(max(0, freed_bytes)),
                "deleted_active": deleted_active,
                "cancelled_download": cancelled_download,
                "restarted": restarted,
                "restart_reason": restart_reason,
            },
        )
    status_code = 404 if reason == "model_not_found" else 400
    return JSONResponse(
        status_code=status_code,
        content={
            "deleted": False,
            "reason": reason,
            "model_id": model_id,
            "deleted_file": False,
            "freed_bytes": 0,
            "cancelled_download": False,
        },
    )


@router.post("/internal/models/purge")
async def purge_models_endpoint(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"purged": False, "reason": "orchestrator_disabled"},
        )
    payload = await request.json()
    reset_bootstrap_flag = bool(payload.get("reset_bootstrap_flag", True))
    result = await _main.purge_all_models(request.app, runtime_cfg, reset_bootstrap_flag=reset_bootstrap_flag)
    return JSONResponse(status_code=200, content=result)


@router.post("/internal/models/upload")
async def upload_model_endpoint(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"uploaded": False, "reason": "orchestrator_disabled"},
        )

    raw_filename = request.headers.get("x-potato-filename", "")
    try:
        filename = _main._safe_upload_filename(raw_filename)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"uploaded": False, "reason": str(exc)})

    async with request.app.state.model_upload_lock:
        if request.app.state.model_upload_state.get("active"):
            return JSONResponse(status_code=409, content={"uploaded": False, "reason": "upload_already_running"})

        declared_total = max(0, _safe_int(request.headers.get("content-length"), 0))
        max_upload_bytes = _main.get_model_upload_max_bytes(runtime_cfg)
        request.app.state.model_upload_cancel_requested = False
        request.app.state.model_upload_state = {
            "active": True,
            "model_id": None,
            "bytes_total": declared_total,
            "bytes_received": 0,
            "percent": 0,
            "error": None,
        }
        if max_upload_bytes is not None and declared_total > 0 and declared_total > max_upload_bytes:
            request.app.state.model_upload_state.update({"active": False, "error": "upload_too_large"})
            return JSONResponse(status_code=413, content={"uploaded": False, "reason": "upload_too_large", "max_upload_bytes": max_upload_bytes})

        state = _main.ensure_models_state(runtime_cfg)
        existing_names = {
            str(item.get("filename"))
            for item in state.get("models", [])
            if isinstance(item, dict)
        }
        final_filename = _unique_filename(filename, existing_names)
        tmp_path = _model_file_path(runtime_cfg, final_filename + ".part")
        final_path = _model_file_path(runtime_cfg, final_filename)
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        total_received = 0
        error_reason: str | None = None
        upload_completed = False

        def _cleanup_partial_upload() -> None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not delete partial upload file: %s", tmp_path, exc_info=True)

        try:
            with tmp_path.open("wb") as handle:
                async for chunk in request.stream():
                    if request.app.state.model_upload_cancel_requested:
                        error_reason = "upload_cancelled"
                        break
                    if not chunk:
                        continue
                    total_received += len(chunk)
                    if max_upload_bytes is not None and total_received > max_upload_bytes:
                        error_reason = "upload_too_large"
                        break
                    handle.write(chunk)
                    state_total = request.app.state.model_upload_state.get("bytes_total") or 0
                    percent = int((total_received * 100 / state_total)) if state_total else 0
                    request.app.state.model_upload_state.update(
                        {
                            "bytes_received": total_received,
                            "percent": max(0, min(100, percent)),
                        }
                    )

            if error_reason == "upload_too_large":
                _cleanup_partial_upload()
                return JSONResponse(status_code=413, content={"uploaded": False, "reason": error_reason, "max_upload_bytes": max_upload_bytes})
            if error_reason == "upload_cancelled":
                _cleanup_partial_upload()
                return JSONResponse(status_code=200, content={"uploaded": False, "reason": error_reason})
            if total_received <= 0:
                error_reason = "upload_empty"
                _cleanup_partial_upload()
                return JSONResponse(status_code=400, content={"uploaded": False, "reason": error_reason})

            tmp_path.replace(final_path)
            models = state.get("models", [])
            if not isinstance(models, list):
                error_reason = "models_state_invalid"
                _cleanup_partial_upload()
                return JSONResponse(status_code=500, content={"uploaded": False, "reason": error_reason})
            existing_ids = {
                str(item.get("id"))
                for item in models
                if isinstance(item, dict)
            }
            model_id = _unique_model_id(_slugify_id(Path(final_filename).stem), existing_ids)
            record = {
                "id": model_id,
                "filename": final_filename,
                "source_url": None,
                "source_type": "upload",
                "status": "ready",
                "error": None,
            }
            models.append(record)
            state["active_model_id"] = model_id
            saved = save_models_state(runtime_cfg, state)
            runtime_cfg.model_path = final_path
            restarted, _reason = await _main.restart_managed_llama_process(request.app)
            request.app.state.model_upload_state.update(
                {
                    "active": False,
                    "error": None,
                    "model_id": model_id,
                    "bytes_total": total_received,
                    "bytes_received": total_received,
                    "percent": 100,
                }
            )
            upload_completed = True
            model = get_model_by_id(saved, model_id)
            compatibility = build_large_model_compatibility(
                runtime_cfg,
                model_filename=final_filename,
                model_size_bytes=max(total_received, declared_total or 0) or None,
            )
            response_payload: dict[str, Any] = {
                "uploaded": True,
                "model": model,
                "switched": True,
                "restarted": restarted,
            }
            warnings = compatibility.get("warnings")
            if isinstance(warnings, list) and warnings:
                response_payload["warnings"] = warnings
            return JSONResponse(
                status_code=200,
                content=response_payload,
            )
        except OSError:
            if error_reason is None:
                error_reason = "upload_write_failed"
            _cleanup_partial_upload()
            logger.warning("Model upload failed during file write: %s", final_filename, exc_info=True)
            return JSONResponse(status_code=500, content={"uploaded": False, "reason": error_reason})
        except Exception:
            _cleanup_partial_upload()
            raise
        finally:
            if not upload_completed:
                request.app.state.model_upload_state.update(
                    {
                        "active": False,
                        "model_id": None,
                        "error": error_reason,
                    }
                )


@router.post("/internal/models/cancel-upload")
async def cancel_model_upload(request: Request, runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"cancelled": False, "reason": "orchestrator_disabled"},
        )
    if not request.app.state.model_upload_state.get("active"):
        return JSONResponse(status_code=200, content={"cancelled": False, "reason": "not_running"})
    request.app.state.model_upload_cancel_requested = True
    return JSONResponse(status_code=200, content={"cancelled": True, "reason": "cancel_requested"})
