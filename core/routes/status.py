"""Status, health-check, and log-stream routes."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

try:
    from core.deps import get_runtime
    from core.runtime_state import RuntimeConfig
except ModuleNotFoundError:
    from deps import get_runtime  # type: ignore[no-redef]
    from runtime_state import RuntimeConfig  # type: ignore[no-redef]

router = APIRouter()

# Late-bound references — resolved via the main module so monkeypatching works.
_main: types.ModuleType | None = None
_chat_html_path: Path | None = None


def register_status_helpers(*, main_module: types.ModuleType, chat_html_path: Path):
    global _main, _chat_html_path
    _main = main_module
    _chat_html_path = chat_html_path


@router.get("/", response_class=HTMLResponse)
async def root(request: Request) -> HTMLResponse:
    html = _chat_html_path.read_text(encoding="utf-8")
    # Inject the per-boot terminal token so the frontend can authenticate WS connections.
    terminal_token = getattr(request.app.state, "terminal_token", "")
    html = html.replace("</head>", f'<meta name="terminal-token" content="{terminal_token}">\n</head>', 1)
    return HTMLResponse(html)


@router.get("/status")
async def status(request: Request, runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
    download_active, auto_start_remaining = await _main.get_status_download_context(request.app, runtime_cfg)
    return JSONResponse(
        await _main.build_status(
            runtime_cfg,
            app=request.app,
            download_active=download_active,
            auto_start_remaining_seconds=auto_start_remaining,
            system_snapshot=request.app.state.system_metrics_snapshot,
        )
    )


@router.get("/internal/llama-healthz")
async def llama_healthz(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
    transport_healthy = await _main.check_llama_health(runtime_cfg, busy_is_healthy=False)
    inference_healthy = False
    if transport_healthy:
        inference_healthy = await _main.probe_llama_inference_slot(runtime_cfg)
    return JSONResponse(
        {
            "healthy": transport_healthy and inference_healthy,
            "transport_healthy": transport_healthy,
            "inference_healthy": inference_healthy,
        }
    )


@router.get("/logs")
async def logs() -> StreamingResponse:
    return StreamingResponse(
        _main.log_stream(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )
