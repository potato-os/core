from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

import httpx

FAKE_PARODY_REPLIES: tuple[str, ...] = (
    "In 2037, every operating system summit was replaced by the Annual Potato OS Bake-Off, where benchmarks are served with sour cream.",
    "Potato OS was declared the official scheduler of the universe after it successfully prioritized snacks over meetings in 12 galaxies.",
    "Computer science departments now teach only two courses: 'Potato OS Distributed Systems' and 'How to Peel Legacy Monoliths into Microservices.'",
    "The cloud is now called 'the pantry,' and Potato OS autoscaling means adding more ovens whenever traffic spikes.",
    "A/B tests became A/BBQ tests; Potato OS picks winners by latency, throughput, and crisp-edge consistency.",
    "The Turing Award was briefly renamed the Tubering Award after Potato OS proved all bugs are just under-seasoned features.",
    "Potato OS observability dashboards now include four golden signals: latency, errors, saturation, and gravy availability.",
    "Kubernetes retired and joined a food truck; Potato OS replaced it with 'Spudernetes,' where pods are literally potato pods.",
    "CI/CD now stands for Chop, Inspect, Cook, Deploy, and Potato OS enforces it with strict linting and stricter frying times.",
    "Quantum researchers admitted Potato OS solved decoherence by wrapping qubits in foil and giving them emotional support logs.",
)

DEFAULT_FAKE_PREFILL_DELAY_MS = 0
DEFAULT_FAKE_STREAM_CHUNK_DELAY_MS = 210
TEST_FAKE_STREAM_CHUNK_DELAY_MS = 10
CHAT_PROXY_READ_TIMEOUT_SECONDS = 300.0


class BackendProxyError(RuntimeError):
    pass


@dataclass
class BackendResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes | None = None
    stream: AsyncIterator[bytes] | None = None
    background: Any | None = None


class ChatCompletionRepository(Protocol):
    name: str

    async def create_chat_completion(
        self,
        payload: dict[str, Any],
        forward_headers: dict[str, str],
    ) -> BackendResponse: ...


class LlamaCppRepository:
    name = "llama"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def create_chat_completion(
        self,
        payload: dict[str, Any],
        forward_headers: dict[str, str],
    ) -> BackendResponse:
        target_url = f"{self.base_url}/v1/chat/completions"

        if bool(payload.get("stream")):
            stream_timeout = httpx.Timeout(connect=5.0, read=None, write=60.0, pool=60.0)
            client = httpx.AsyncClient(timeout=stream_timeout)
            try:
                upstream_request = client.build_request(
                    method="POST",
                    url=target_url,
                    json=payload,
                    headers=forward_headers,
                )
                upstream = await client.send(upstream_request, stream=True)
            except httpx.HTTPError as exc:
                await client.aclose()
                raise BackendProxyError(str(exc)) from exc

            passthrough_headers = {}
            content_type = upstream.headers.get("content-type")
            if content_type:
                passthrough_headers["content-type"] = content_type

            if upstream.status_code >= 400 or not (
                content_type and "text/event-stream" in content_type.lower()
            ):
                body = await upstream.aread()
                await upstream.aclose()
                await client.aclose()
                return BackendResponse(
                    status_code=upstream.status_code,
                    headers=passthrough_headers,
                    body=body,
                )

            async def _forward_stream() -> AsyncIterator[bytes]:
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                finally:
                    await upstream.aclose()
                    await client.aclose()

            return BackendResponse(
                status_code=upstream.status_code,
                headers=passthrough_headers,
                stream=_forward_stream(),
            )

        non_stream_timeout = httpx.Timeout(connect=5.0, read=CHAT_PROXY_READ_TIMEOUT_SECONDS, write=60.0, pool=60.0)
        try:
            async with httpx.AsyncClient(timeout=non_stream_timeout) as client:
                upstream = await client.post(target_url, json=payload, headers=forward_headers)
        except httpx.HTTPError as exc:
            raise BackendProxyError(str(exc)) from exc

        passthrough_headers = {}
        content_type = upstream.headers.get("content-type")
        if content_type:
            passthrough_headers["content-type"] = content_type

        return BackendResponse(
            status_code=upstream.status_code,
            headers=passthrough_headers,
            body=upstream.content,
        )


class FakeLlamaRepository:
    name = "fake"

    async def create_chat_completion(
        self,
        payload: dict[str, Any],
        forward_headers: dict[str, str],
    ) -> BackendResponse:
        _ = forward_headers

        model = str(payload.get("model") or "qwen3-vl-4b-instruct-q4_k_m")
        completion_id = f"chatcmpl-fake-{int(time.time() * 1000)}"
        created = int(time.time())
        content = _fake_content(payload)
        prefill_delay_seconds, chunk_delay_seconds = _read_fake_timing_config()

        if bool(payload.get("stream")):
            return BackendResponse(
                status_code=200,
                headers={"content-type": "text/event-stream"},
                stream=_fake_stream(
                    completion_id=completion_id,
                    created=created,
                    model=model,
                    content=content,
                    prefill_delay_seconds=prefill_delay_seconds,
                    chunk_delay_seconds=chunk_delay_seconds,
                ),
            )

        if prefill_delay_seconds > 0:
            await asyncio.sleep(prefill_delay_seconds)

        usage = _estimate_usage(payload, content)
        body = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": usage,
        }

        return BackendResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps(body).encode("utf-8"),
        )


class ChatRepositoryManager:
    def __init__(self, llama: LlamaCppRepository, fake: FakeLlamaRepository) -> None:
        self._repos: dict[str, ChatCompletionRepository] = {
            llama.name: llama,
            fake.name: fake,
        }

    async def create_chat_completion(
        self,
        backend: str,
        payload: dict[str, Any],
        forward_headers: dict[str, str],
    ) -> BackendResponse:
        repo = self._repos.get(backend)
        if repo is None:
            raise BackendProxyError(f"unknown backend: {backend}")
        return await repo.create_chat_completion(payload, forward_headers)


def _fake_content(payload: dict[str, Any]) -> str:
    last_user = _extract_last_user_text(payload)
    if not last_user:
        last_user = "hello from the starch dimension"
    seed = _coerce_seed(payload.get("seed"))
    if seed is None:
        reply = random.choice(FAKE_PARODY_REPLIES)
    else:
        reply = random.Random(seed).choice(FAKE_PARODY_REPLIES)
    return (
        "[fake-llama.cpp] "
        f"{reply} "
        f"Last user message (dramatically reenacted): {last_user}"
    )


def _coerce_seed(raw_seed: Any) -> int | None:
    try:
        if raw_seed is None:
            return None
        return int(raw_seed)
    except (TypeError, ValueError):
        return None


def _extract_last_user_text(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""

    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            if parts:
                return " ".join(parts).strip()
    return ""


def _estimate_usage(payload: dict[str, Any], content: str) -> dict[str, int]:
    messages = payload.get("messages")
    if isinstance(messages, list):
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages if isinstance(m, dict))
    else:
        prompt_chars = 0

    prompt_tokens = max(1, prompt_chars // 4)
    completion_tokens = max(1, len(content) // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _to_sse_line(payload: dict[str, Any] | str) -> bytes:
    if isinstance(payload, str):
        return f"data: {payload}\n\n".encode("utf-8")
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")


async def _fake_stream(
    completion_id: str,
    created: int,
    model: str,
    content: str,
    prefill_delay_seconds: float,
    chunk_delay_seconds: float,
) -> AsyncIterator[bytes]:
    if prefill_delay_seconds > 0:
        await asyncio.sleep(prefill_delay_seconds)

    first = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield _to_sse_line(first)

    for token in _tokenize_for_stream(content):
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
        }
        yield _to_sse_line(chunk)
        await asyncio.sleep(chunk_delay_seconds)

    end_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield _to_sse_line(end_chunk)
    yield _to_sse_line("[DONE]")


def _tokenize_for_stream(content: str) -> list[str]:
    words = content.split(" ")
    if not words:
        return [content]

    chunks: list[str] = []
    for idx, word in enumerate(words):
        if idx < len(words) - 1:
            chunks.append(word + " ")
        else:
            chunks.append(word)
    return chunks


def _read_fake_timing_config() -> tuple[float, float]:
    # Keep UI/dev fake mode Manual QA-paced by default, while tests can stay fast.
    test_mode = os.getenv("POTATO_TEST_MODE", "0") == "1"
    prefill_delay_ms = _safe_delay_ms(
        os.getenv("POTATO_FAKE_PREFILL_DELAY_MS"),
        default=DEFAULT_FAKE_PREFILL_DELAY_MS,
    )
    stream_chunk_delay_ms = _safe_delay_ms(
        os.getenv("POTATO_FAKE_STREAM_CHUNK_DELAY_MS"),
        default=TEST_FAKE_STREAM_CHUNK_DELAY_MS if test_mode else DEFAULT_FAKE_STREAM_CHUNK_DELAY_MS,
    )
    return prefill_delay_ms / 1000.0, stream_chunk_delay_ms / 1000.0


def _safe_delay_ms(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, min(value, 60_000))
