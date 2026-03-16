"""Settings document routes."""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

try:
    from app.deps import get_runtime
    from app.runtime_state import RuntimeConfig
    from app.settings import apply_settings_document_yaml, export_settings_document_yaml
except ModuleNotFoundError:
    from deps import get_runtime  # type: ignore[no-redef]
    from runtime_state import RuntimeConfig  # type: ignore[no-redef]
    from settings import apply_settings_document_yaml, export_settings_document_yaml  # type: ignore[no-redef]

router = APIRouter()

_restart_managed_llama_process = None


def register_settings_helpers(*, restart_managed_llama_process):
    global _restart_managed_llama_process
    _restart_managed_llama_process = restart_managed_llama_process


@router.get("/internal/settings-document")
async def get_settings_document(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "format": "yaml",
            "document": export_settings_document_yaml(runtime_cfg),
        },
    )


@router.post("/internal/settings-document")
async def apply_settings_document_endpoint(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
) -> JSONResponse:
    payload = await request.json()
    document = str(payload.get("document") or "")
    if not document.strip():
        return JSONResponse(status_code=400, content={"updated": False, "reason": "document_required"})
    updated, reason, settings_document = apply_settings_document_yaml(runtime_cfg, document)
    if not updated:
        return JSONResponse(status_code=400, content={"updated": False, "reason": reason, **settings_document})
    restarted, restart_reason = await _restart_managed_llama_process(request.app)
    return JSONResponse(
        status_code=200,
        content={
            "updated": True,
            "reason": reason,
            "active_model_id": settings_document.get("active_model_id"),
            "document": yaml.safe_dump(settings_document, sort_keys=False, allow_unicode=True),
            "restarted": restarted,
            "restart_reason": restart_reason,
        },
    )
