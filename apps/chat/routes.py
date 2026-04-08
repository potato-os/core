"""Chat completions proxy route."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from inferno import BackendProxyError, ChatRepositoryManager

try:
    from core.deps import get_runtime, get_chat_repository
    from core.model_state import apply_model_chat_defaults
    from core.runtime_state import RuntimeConfig
    from core.settings import merge_active_model_chat_defaults, merge_chat_defaults
except ModuleNotFoundError:
    from deps import get_runtime, get_chat_repository  # type: ignore[no-redef]
    from model_state import apply_model_chat_defaults  # type: ignore[no-redef]
    from runtime_state import RuntimeConfig  # type: ignore[no-redef]
    from settings import merge_active_model_chat_defaults, merge_chat_defaults  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    runtime_cfg: RuntimeConfig = Depends(get_runtime),
    chat_repository: ChatRepositoryManager = Depends(get_chat_repository),
) -> Response:
    lock = request.app.state.inference_lock
    if lock.locked():
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "message": "A completion is already in progress. Try again shortly.",
                    "type": "concurrent_request",
                    "code": 429,
                }
            },
        )

    # Acquire immediately after locked() check — no awaits in between so no
    # scheduling window for a second request to slip past.  asyncio.Lock.acquire()
    # is synchronous (no yield) when the lock is free.
    await lock.acquire()
    released = False
    try:
        _get_status_download_context = request.app.state.get_status_download_context
        _build_status = request.app.state.build_status

        download_active, auto_start_remaining = await _get_status_download_context(request.app, runtime_cfg)
        status_payload = await _build_status(
            runtime_cfg,
            app=request.app,
            download_active=download_active,
            auto_start_remaining_seconds=auto_start_remaining,
            system_snapshot=request.app.state.system_metrics_snapshot,
        )
        if status_payload["state"] != "READY":
            return JSONResponse(status_code=503, content=status_payload)

        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        except Exception as exc:
            if type(exc).__name__ == "ClientDisconnect":
                return Response(status_code=499)
            raise

        payload = merge_active_model_chat_defaults(payload, runtime=runtime_cfg)
        payload = merge_chat_defaults(payload)
        payload = apply_model_chat_defaults(
            payload,
            active_model_filename=str(status_payload.get("model", {}).get("filename") or ""),
        )
        headers = request.app.state.forward_headers(request)
        active_backend = status_payload["backend"]["active"]

        try:
            backend_response = await chat_repository.create_chat_completion(
                backend=active_backend,
                payload=payload,
                forward_headers=headers,
            )
        except BackendProxyError as exc:
            if runtime_cfg.chat_backend_mode == "auto" and active_backend == "llama":
                backend_response = await chat_repository.create_chat_completion(
                    backend="fake",
                    payload=payload,
                    forward_headers=headers,
                )
            else:
                logger.exception("Backend proxy error")
                raise HTTPException(status_code=502, detail=f"backend unavailable: {exc}") from exc

        if backend_response.stream is not None:
            original = backend_response.stream

            async def _guarded_stream():
                try:
                    async for chunk in original:
                        yield chunk
                finally:
                    lock.release()

            released = True
            return StreamingResponse(
                _guarded_stream(),
                status_code=backend_response.status_code,
                headers=backend_response.headers,
                background=backend_response.background,
            )

        return Response(
            content=backend_response.body or b"",
            status_code=backend_response.status_code,
            headers=backend_response.headers,
        )
    finally:
        if not released:
            lock.release()
