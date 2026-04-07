"""Runtime control routes — restart, switch, memory loading, power calibration."""

from __future__ import annotations

import time
import types
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

try:
    from core.deps import get_runtime
    from core.model_state import model_format_for_filename, ensure_models_state, get_model_by_id
    from core.runtime_state import (
        LLAMA_SERVER_RUNTIME_FAMILIES,
        RuntimeConfig,
        build_large_model_compatibility,
        build_llama_memory_loading_status,
        build_power_calibration_status,
        build_power_estimate_status,
        find_runtime_slot_by_family,
        check_runtime_device_compatibility,
        classify_runtime_device,
        _read_pi_device_model_name,
        _detect_total_memory_bytes,
        normalize_allow_unsupported_large_models,
        normalize_llama_memory_loading_mode,
        write_llama_runtime_bundle_marker,
        write_llama_runtime_settings,
        _append_power_calibration_sample,
        _fit_and_persist_power_calibration,
        _reset_power_calibration,
        _safe_positive_float,
    )
except ModuleNotFoundError:
    from deps import get_runtime  # type: ignore[no-redef]
    from model_state import model_format_for_filename, ensure_models_state, get_model_by_id  # type: ignore[no-redef]
    from runtime_state import (  # type: ignore[no-redef]
        LLAMA_SERVER_RUNTIME_FAMILIES,
        RuntimeConfig,
        build_large_model_compatibility,
        build_llama_memory_loading_status,
        build_power_calibration_status,
        build_power_estimate_status,
        find_runtime_slot_by_family,
        check_runtime_device_compatibility,
        classify_runtime_device,
        _read_pi_device_model_name,
        _detect_total_memory_bytes,
        normalize_allow_unsupported_large_models,
        normalize_llama_memory_loading_mode,
        write_llama_runtime_bundle_marker,
        write_llama_runtime_settings,
        _append_power_calibration_sample,
        _fit_and_persist_power_calibration,
        _reset_power_calibration,
        _safe_positive_float,
    )

router = APIRouter()

# Late-bound references — resolved via the main module so monkeypatching works.
_main: types.ModuleType | None = None


def register_runtime_helpers(*, main_module: types.ModuleType):
    global _main
    _main = main_module


@router.post("/internal/restart-llama")
async def restart_llama(request: Request, runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"restarted": False, "reason": "orchestrator_disabled"},
        )
    restarted, reason = await _main.restart_managed_llama_process(request.app)
    if restarted:
        return JSONResponse(status_code=200, content={"restarted": True, "reason": reason})
    return JSONResponse(
        status_code=200,
        content={"restarted": False, "reason": reason},
    )


@router.post("/internal/llama-runtime/switch")
async def switch_llama_runtime(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"switched": False, "reason": "orchestrator_disabled"},
        )

    payload = await request.json()
    family = str(payload.get("family") or "").strip()
    if not family:
        return JSONResponse(status_code=400, content={"switched": False, "reason": "family_required"})

    slot = find_runtime_slot_by_family(runtime_cfg, family)
    if slot is None:
        return JSONResponse(status_code=404, content={"switched": False, "reason": "runtime_not_found"})

    device_class = classify_runtime_device(
        pi_model_name=_read_pi_device_model_name(),
        total_memory_bytes=_detect_total_memory_bytes(),
    )
    compat = check_runtime_device_compatibility(device_class, family)
    if not compat["compatible"]:
        return JSONResponse(status_code=409, content={"switched": False, "reason": "incompatible_runtime"})

    # Reject switching to a runtime that can't serve the active model format.
    try:
        models_state = ensure_models_state(runtime_cfg)
        active_model = get_model_by_id(models_state, str(models_state.get("active_model_id") or ""))
        active_filename = str(active_model.get("filename") or "") if isinstance(active_model, dict) else ""
    except Exception:
        active_filename = ""
    if active_filename:
        fmt = model_format_for_filename(active_filename)
        if family == "litert" and fmt != "litertlm":
            return JSONResponse(status_code=409, content={"switched": False, "reason": "model_format_incompatible"})
        if family in LLAMA_SERVER_RUNTIME_FAMILIES and fmt == "litertlm":
            return JSONResponse(status_code=409, content={"switched": False, "reason": "model_format_incompatible"})

    async with request.app.state.llama_runtime_switch_lock:
        switch_state = request.app.state.llama_runtime_switch_state
        if switch_state.get("active"):
            return JSONResponse(status_code=409, content={"switched": False, "reason": "switch_already_running"})

        switch_state.update(
            {
                "active": True,
                "target_family": family,
                "started_at_unix": int(time.time()),
                "completed_at_unix": None,
                "error": None,
            }
        )

        try:
            restarted, restart_reason = await _main.restart_managed_llama_process(request.app)
            install_result = await _main.install_llama_runtime_bundle(runtime_cfg, Path(str(slot["path"])))
            if not install_result.get("ok"):
                reason = str(install_result.get("reason") or "install_failed")
                switch_state.update(
                    {
                        "active": False,
                        "completed_at_unix": int(time.time()),
                        "error": reason,
                    }
                )
                return JSONResponse(
                    status_code=500,
                    content={
                        "switched": False,
                        "reason": reason,
                        "family": family,
                        "restarted": restarted,
                        "restart_reason": restart_reason,
                    },
                )

            marker = write_llama_runtime_bundle_marker(runtime_cfg, slot)
            switch_state.update(
                {
                    "active": False,
                    "target_family": None,
                    "completed_at_unix": int(time.time()),
                    "error": None,
                }
            )
            return JSONResponse(
                status_code=200,
                content={
                    "switched": True,
                    "reason": "runtime_switched",
                    "family": family,
                    "slot": slot,
                    "install": install_result,
                    "restarted": restarted,
                    "restart_reason": restart_reason,
                    "marker": marker,
                },
            )
        except Exception:
            switch_state.update(
                {
                    "active": False,
                    "completed_at_unix": int(time.time()),
                    "error": "switch_failed",
                }
            )
            raise


@router.post("/internal/llama-runtime/memory-loading")
async def set_llama_memory_loading_mode(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"updated": False, "reason": "orchestrator_disabled"},
        )

    payload = await request.json()
    requested_mode = payload.get("mode", payload.get("memory_loading_mode"))
    mode = normalize_llama_memory_loading_mode(requested_mode)
    saved = write_llama_runtime_settings(runtime_cfg, memory_loading_mode=mode)
    restarted, restart_reason = await _main.restart_managed_llama_process(request.app)
    memory_loading = build_llama_memory_loading_status(runtime_cfg)
    return JSONResponse(
        status_code=200,
        content={
            "updated": True,
            "reason": "memory_loading_updated",
            "memory_loading": memory_loading,
            "saved": saved,
            "restarted": restarted,
            "restart_reason": restart_reason,
        },
    )


@router.post("/internal/compatibility/large-model-override")
async def set_large_model_override(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"updated": False, "reason": "orchestrator_disabled"},
        )

    payload = await request.json()
    enabled = normalize_allow_unsupported_large_models(
        payload.get("enabled", payload.get("allow_unsupported_large_models"))
    )
    saved = write_llama_runtime_settings(
        runtime_cfg,
        allow_unsupported_large_models=enabled,
    )
    compatibility = build_large_model_compatibility(
        runtime_cfg,
        model_filename=runtime_cfg.model_path.name,
        model_size_bytes=(runtime_cfg.model_path.stat().st_size if runtime_cfg.model_path.exists() else None),
        allow_override=enabled,
    )
    return JSONResponse(
        status_code=200,
        content={
            "updated": True,
            "reason": "large_model_override_updated",
            "override": {
                "enabled": enabled,
                "label": (
                    "Try unsupported large model anyway"
                    if enabled
                    else "Use compatibility warnings (default)"
                ),
            },
            "compatibility": compatibility,
            "saved": saved,
        },
    )


@router.post("/internal/power-calibration/sample")
async def capture_power_calibration_sample(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    payload = await request.json()
    wall_watts = _safe_positive_float(payload.get("wall_watts"))
    if wall_watts is None:
        return JSONResponse(
            status_code=400,
            content={"captured": False, "reason": "invalid_wall_watts"},
        )

    current_power = _main._build_power_estimate_snapshot()
    raw_watts = _safe_positive_float(current_power.get("total_watts"))
    if raw_watts is None:
        return JSONResponse(
            status_code=409,
            content={
                "captured": False,
                "reason": "power_unavailable",
                "power_estimate": build_power_estimate_status(runtime_cfg, current_power),
            },
        )

    calibration = _append_power_calibration_sample(
        runtime_cfg,
        raw_pmic_watts=raw_watts,
        wall_watts=wall_watts,
    )
    power_status = build_power_estimate_status(runtime_cfg, current_power)
    return JSONResponse(
        status_code=200,
        content={
            "captured": True,
            "reason": "power_calibration_sample_captured",
            "sample": calibration.get("samples", [])[-1] if calibration.get("samples") else None,
            "calibration": build_power_calibration_status(runtime_cfg),
            "power_estimate": power_status,
        },
    )


@router.post("/internal/power-calibration/fit")
async def fit_power_calibration(
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    ok, reason, calibration = _fit_and_persist_power_calibration(runtime_cfg)
    status_code = 200 if ok else 400
    return JSONResponse(
        status_code=status_code,
        content={
            "updated": ok,
            "reason": reason,
            "calibration": build_power_calibration_status(runtime_cfg) if ok else calibration,
        },
    )


@router.post("/internal/power-calibration/reset")
async def reset_power_calibration(
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    calibration = _reset_power_calibration(runtime_cfg)
    return JSONResponse(
        status_code=200,
        content={
            "updated": True,
            "reason": "power_calibration_reset",
            "calibration": build_power_calibration_status(runtime_cfg),
            "saved": calibration,
        },
    )


@router.post("/internal/reset-runtime")
async def reset_runtime_now(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"started": False, "reason": "orchestrator_disabled"},
        )

    started, reason = await _main.start_runtime_reset(runtime_cfg)
    status_code = 202 if started else 200
    return JSONResponse(status_code=status_code, content={"started": started, "reason": reason})


@router.post("/internal/cancel-llama")
async def cancel_llama(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"cancelled": False, "restarted": False, "reason": "orchestrator_disabled"},
        )

    cancelled, action = await _main.request_llama_slot_cancel(runtime_cfg)
    if cancelled:
        return JSONResponse(
            status_code=200,
            content={"cancelled": True, "restarted": False, "method": f"slot:{action}"},
        )

    return JSONResponse(
        status_code=200,
        content={"cancelled": False, "restarted": False, "method": "none", "reason": "slot_action_unavailable"},
    )
