"""LiteRT adapter — OpenAI-compatible HTTP wrapper around litert-lm-api.

Runs as a standalone FastAPI process on the same port as llama-server (8080),
exposing /health and /v1/chat/completions so Potato's existing proxy works
unchanged.

Conversation persistence: a single Conversation is kept alive across requests
so that the KV cache is reused for multi-turn chat (matching the Gallery
pattern). The conversation is reset only when the incoming message history
diverges from what we've already processed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("litert_adapter")

try:
    import litert_lm  # type: ignore[import-untyped]
except ImportError:
    litert_lm = None  # type: ignore[assignment]

_engine: Any = None
_conversation: Any = None
_vision_enabled: bool = False
# Tracks the messages we've already sent to the persistent conversation
# so we can detect continuations vs new sessions.
_conversation_history: list[dict[str, Any]] = []
_lock = asyncio.Lock()


def _probe_vision_support(model_path: str) -> tuple[Any, bool]:
    """Try to create an Engine with vision support.

    Returns (engine, vision_enabled).  Falls back to text-only if the
    current litert-lm build does not support the vision_backend kwarg
    or if the runtime vision calculator is missing.
    """
    try:
        engine = litert_lm.Engine(
            model_path,
            backend=litert_lm.Backend.CPU,
            vision_backend=litert_lm.Backend.CPU,
        )
        logger.info("LiteRT vision probe succeeded — multimodal enabled")
        return engine, True
    except (TypeError, RuntimeError, Exception) as exc:
        logger.info("LiteRT vision probe failed (%s) — text-only mode", exc)
        engine = litert_lm.Engine(model_path, backend=litert_lm.Backend.CPU)
        return engine, False


def _reset_conversation() -> None:
    """Close the current conversation and create a fresh one."""
    global _conversation, _conversation_history
    if _conversation is not None:
        try:
            _conversation.__exit__(None, None, None)
        except Exception:
            pass
    _conversation = _engine.create_conversation()
    _conversation.__enter__()
    _conversation_history = []
    logger.info("Conversation reset")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _engine, _vision_enabled
    model_path = os.environ.get("POTATO_MODEL_PATH", "")
    if not model_path:
        logger.error("POTATO_MODEL_PATH not set")
    elif litert_lm is None:
        logger.error("litert_lm package not installed — pip install litert-lm-api")
    else:
        logger.info("Loading LiteRT engine from %s", model_path)
        try:
            _engine, _vision_enabled = _probe_vision_support(model_path)
            _reset_conversation()
            logger.info("LiteRT engine loaded successfully (vision=%s)", _vision_enabled)
        except Exception:
            logger.exception("Failed to load LiteRT engine")
            _engine = None
    yield
    global _conversation
    if _conversation is not None:
        try:
            _conversation.__exit__(None, None, None)
        except Exception:
            pass
        _conversation = None
    if _engine is not None:
        try:
            _engine.__exit__(None, None, None)
        except Exception:
            logger.exception("Error cleaning up LiteRT engine")
        _engine = None


app = FastAPI(lifespan=_lifespan)


@app.get("/health")
async def health():
    if _engine is None:
        return JSONResponse(status_code=503, content={"status": "error", "reason": "engine_not_loaded"})
    return {"status": "ok", "vision": _vision_enabled}


def _build_openai_response(text: str, model: str, prompt_tokens: int = 0) -> dict[str, Any]:
    completion_tokens = max(1, len(text) // 4)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _extract_text(response: Any) -> str:
    if isinstance(response, dict):
        content_list = response.get("content", [])
        if content_list and isinstance(content_list[0], dict):
            return content_list[0].get("text", "")
    return ""


def _convert_openai_to_litert_content(content: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Convert OpenAI-format multimodal content to litert-lm format.

    OpenAI: [{"type": "text", ...}, {"type": "image_url", "image_url": {"url": "data:...;base64,AAAA"}}]
    LiteRT: [{"type": "text", ...}, {"type": "image", "blob": "AAAA"}]

    Only base64 data URLs are supported.  Remote URLs (https://...) are
    rejected — LiteRT expects raw image bytes, not a URL fetch.
    """
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for part in content:
        if part.get("type") == "image_url":
            data_url = part.get("image_url", {}).get("url", "")
            if not data_url.startswith("data:"):
                raise ValueError(f"Only base64 data URLs are supported for LiteRT vision, got: {data_url[:60]}")
            blob = data_url.split(",", 1)[1] if "," in data_url else ""
            parts.append({"type": "image", "blob": blob})
        else:
            parts.append(part)
    return parts


def _content_equal(a: str | list | None, b: str | list | None) -> bool:
    """Compare message content that may be a string or a list of parts."""
    return a == b


def _has_image_content(messages: list[dict[str, Any]]) -> bool:
    """Return True if any message contains an image_url content part."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "image_url":
                    return True
    return False


def _estimate_prompt_chars(messages: list[dict[str, Any]]) -> int:
    """Estimate total character count across all messages, handling multimodal."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    total += len(part.get("text", ""))
                else:
                    total += 256  # approximate image token cost in chars
    return total


def _messages_match(incoming: list[dict[str, Any]], history: list[dict[str, Any]]) -> bool:
    """Check if incoming message history is a continuation of our tracked history.

    Returns True only when history is non-empty AND every tracked message
    matches the corresponding incoming message (i.e. incoming starts with
    the full tracked history).
    """
    if not history:
        return False
    if len(history) > len(incoming):
        return False
    for prev, inc in zip(history, incoming):
        if prev.get("role") != inc.get("role") or not _content_equal(prev.get("content"), inc.get("content")):
            return False
    return True


def _prepare_conversation_sync(messages: list[dict[str, str]]) -> None:
    """Ensure the persistent conversation is ready for the final user message.

    If the incoming history matches what we've already sent, this is a no-op
    (KV cache hit). Otherwise, reset and replay user/system messages only —
    assistant turns are skipped since send_message would generate fresh
    (different) output. The model responds implicitly to each replayed user
    turn, rebuilding the KV cache with approximate context.
    """
    global _conversation_history

    history_messages = messages[:-1]  # everything except the new user message

    if _messages_match(history_messages, _conversation_history):
        # Continuation — nothing to do, KV cache covers it.
        return

    # History diverged (new session, page reload, session switch).
    logger.info("Conversation history diverged, resetting and replaying user turns")
    _reset_conversation()

    for msg in history_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "assistant":
            # Skip — the model already generated an implicit response to
            # the preceding user turn via send_message above.
            continue
        if role == "system":
            _conversation.send_message(f"[System instruction] {content}" if isinstance(content, str) else content)
        elif isinstance(content, list):
            # Multimodal: send_message expects a dict with role/content
            converted = _convert_openai_to_litert_content(content)
            _conversation.send_message({"role": "user", "content": converted})
        else:
            _conversation.send_message(content)

    _conversation_history = list(history_messages)


def _run_inference_sync(messages: list[dict[str, Any]], stream: bool) -> Any:
    """Run inference synchronously — must be called via asyncio.to_thread."""
    _prepare_conversation_sync(messages)

    raw_content = messages[-1].get("content", "") if messages else ""
    if isinstance(raw_content, list):
        # Multimodal: send_message expects {"role": "user", "content": [...]}
        final_content = {"role": "user", "content": _convert_openai_to_litert_content(raw_content)}
    else:
        final_content = raw_content

    if stream:
        return _conversation.send_message_async(final_content)
    else:
        response = _conversation.send_message(final_content)
        text = _extract_text(response)
        # Track the full exchange in history (store original content for matching)
        _conversation_history.append(messages[-1])
        _conversation_history.append({"role": "assistant", "content": text})
        return text


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    if _engine is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "LiteRT engine not loaded", "type": "server_error"}},
        )

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Invalid JSON", "type": "invalid_request_error"}},
        )

    messages = payload.get("messages", [])
    if not messages:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "messages required", "type": "invalid_request_error"}},
        )

    # Reject multimodal input when vision is not available
    if not _vision_enabled and _has_image_content(messages):
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "Vision input is not supported by this model/runtime configuration", "type": "invalid_request_error"}},
        )

    stream = payload.get("stream", False)
    model_name = payload.get("model", os.environ.get("POTATO_MODEL_PATH", "litert"))

    async with _lock:
        try:
            if stream:
                iterator = await asyncio.to_thread(
                    _run_inference_sync, messages, True,
                )

                # Bridge sync iterator → async generator via a queue so
                # tokens stream to the client as they arrive.
                queue: asyncio.Queue[str | None] = asyncio.Queue()
                collected_text: list[str] = []
                generation_start = time.monotonic()
                first_token_time: float | None = None

                async def _producer():
                    nonlocal first_token_time
                    def _iterate():
                        nonlocal first_token_time
                        for chunk in iterator:
                            text = _extract_text(chunk)
                            if text:
                                if first_token_time is None:
                                    first_token_time = time.monotonic()
                                collected_text.append(text)
                                queue.put_nowait(text)
                        queue.put_nowait(None)  # sentinel
                    try:
                        await asyncio.to_thread(_iterate)
                    except Exception:
                        queue.put_nowait(None)
                        raise

                producer_task = asyncio.create_task(_producer())

                async def _stream_chunks():
                    response_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
                    try:
                        while True:
                            text = await queue.get()
                            if text is None:
                                break
                            chunk_data = {
                                "id": response_id,
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": model_name,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": text},
                                        "finish_reason": None,
                                    }
                                ],
                            }
                            yield f"data: {_json_dumps(chunk_data)}\n\n"

                        # Build timings matching llama-server's format so the
                        # chat UI stats display works identically.
                        now = time.monotonic()
                        full_text = "".join(collected_text)
                        # Estimate token count from text (~4 chars per token)
                        predicted_n = max(1, len(full_text) // 4)
                        prompt_ms = ((first_token_time - generation_start) * 1000) if first_token_time else 0
                        # Decode time = total minus prompt/prefill time
                        decode_start = first_token_time or generation_start
                        predicted_ms = (now - decode_start) * 1000
                        per_token_ms = (predicted_ms / predicted_n) if predicted_n > 0 else 0
                        per_second = (predicted_n / (predicted_ms / 1000)) if predicted_ms > 0 else 0
                        prompt_n = _estimate_prompt_chars(messages) // 4

                        stop_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model_name,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "stop",
                                }
                            ],
                            "timings": {
                                "prompt_n": prompt_n,
                                "prompt_ms": prompt_ms,
                                "prompt_per_second": (prompt_n / (prompt_ms / 1000)) if prompt_ms > 0 else 0,
                                "predicted_n": predicted_n,
                                "predicted_ms": predicted_ms,
                                "predicted_per_token_ms": per_token_ms,
                                "predicted_per_second": per_second,
                            },
                        }
                        yield f"data: {_json_dumps(stop_chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                    finally:
                        await producer_task
                        # Track the streamed exchange in conversation history
                        full_response = "".join(collected_text)
                        _conversation_history.append(messages[-1])
                        _conversation_history.append({"role": "assistant", "content": full_response})

                return StreamingResponse(
                    _stream_chunks(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            else:
                text = await asyncio.to_thread(_run_inference_sync, messages, False)
                prompt_tokens = _estimate_prompt_chars(messages) // 4
                return JSONResponse(content=_build_openai_response(str(text), model_name, prompt_tokens))
        except ValueError as ve:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": str(ve), "type": "invalid_request_error"}},
            )
        except Exception:
            logger.exception("Inference error")
            return JSONResponse(
                status_code=500,
                content={"error": {"message": "Inference failed", "type": "server_error"}},
            )


def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, separators=(",", ":"))
