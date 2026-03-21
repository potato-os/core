"""OTA update check routes."""

from __future__ import annotations

import types

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

try:
    from app.deps import get_runtime
    from app.runtime_state import RuntimeConfig
    from app.update_state import build_update_status, check_for_update
except ModuleNotFoundError:
    from deps import get_runtime  # type: ignore[no-redef]
    from runtime_state import RuntimeConfig  # type: ignore[no-redef]
    from update_state import build_update_status, check_for_update  # type: ignore[no-redef]

router = APIRouter()

_main: types.ModuleType | None = None


def register_update_helpers(*, main_module: types.ModuleType) -> None:
    global _main
    _main = main_module


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
