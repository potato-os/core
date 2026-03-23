"""OTA update check and execution routes."""

from __future__ import annotations

import asyncio
import types
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

try:
    from app.deps import get_runtime
    from app.runtime_state import RuntimeConfig
    from app.update_state import (
        build_update_status,
        check_for_update,
        is_update_safe,
        read_update_state,
    )
except ModuleNotFoundError:
    from deps import get_runtime  # type: ignore[no-redef]
    from runtime_state import RuntimeConfig  # type: ignore[no-redef]
    from update_state import build_update_status, check_for_update, is_update_safe, read_update_state  # type: ignore[no-redef]

router = APIRouter()

_main: types.ModuleType | None = None

# Late-bound reference to run_update — resolved via main module or monkeypatched in tests.
run_update: Any = None


def register_update_helpers(*, main_module: types.ModuleType) -> None:
    global _main, run_update
    _main = main_module
    run_update = getattr(main_module, "run_update", None)


@router.post("/internal/update/check")
async def update_check(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"checked": False, "reason": "orchestrator_disabled"},
        )

    await check_for_update(runtime_cfg)
    return JSONResponse(
        status_code=200,
        content={
            "checked": True,
            **build_update_status(runtime_cfg),
        },
    )


@router.post("/internal/update/start")
async def update_start(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    if not runtime_cfg.enable_orchestrator:
        return JSONResponse(
            status_code=409,
            content={"started": False, "reason": "orchestrator_disabled"},
        )

    state = read_update_state(runtime_cfg)
    if state is None or not state.get("available"):
        return JSONResponse(
            status_code=409,
            content={"started": False, "reason": "no_update_available"},
        )

    tarball_url = state.get("tarball_url")
    if not tarball_url:
        return JSONResponse(
            status_code=409,
            content={"started": False, "reason": "no_tarball_url"},
        )

    safe, reason = is_update_safe(runtime_cfg)
    if not safe:
        return JSONResponse(
            status_code=409,
            content={"started": False, "reason": reason},
        )

    lock = request.app.state.update_lock
    async with lock:
        task = request.app.state.update_task
        if task is not None and not task.done():
            return JSONResponse(
                status_code=409,
                content={"started": False, "reason": "update_in_progress"},
            )

        target_version = state.get("latest_version", "unknown")
        new_task = asyncio.create_task(
            run_update(request.app, runtime_cfg, tarball_url, target_version),
            name="potato-update",
        )

        def _clear_task(finished: asyncio.Task) -> None:  # type: ignore[type-arg]
            if request.app.state.update_task is finished:
                request.app.state.update_task = None

        new_task.add_done_callback(_clear_task)
        request.app.state.update_task = new_task

    return JSONResponse(
        status_code=200,
        content={
            "started": True,
            **build_update_status(runtime_cfg),
        },
    )
