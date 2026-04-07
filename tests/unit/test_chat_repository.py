from __future__ import annotations

from typing import Any

import httpx
import pytest

from core.inferno import backend
from core.inferno.backend import BackendProxyError


class _FakeUpstream:
    def __init__(self) -> None:
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}
        self.closed = False

    async def aiter_raw(self):
        yield b"data: hello\n\n"

    async def aclose(self) -> None:
        self.closed = True


class _FakeAsyncClient:
    def __init__(self, upstream: _FakeUpstream) -> None:
        self._upstream = upstream
        self.closed = False

    def build_request(self, method: str, url: str, json: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        return {"method": method, "url": url, "json": json, "headers": headers}

    async def send(self, request: dict[str, Any], stream: bool = False) -> _FakeUpstream:
        _ = request
        assert stream is True
        return self._upstream

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_llama_stream_closes_upstream_and_client_when_consumer_closes(monkeypatch: pytest.MonkeyPatch):
    upstream = _FakeUpstream()
    client = _FakeAsyncClient(upstream)

    def _client_factory(*args: Any, **kwargs: Any) -> _FakeAsyncClient:
        _ = args, kwargs
        return client

    monkeypatch.setattr(backend.httpx, "AsyncClient", _client_factory)

    repo = backend.LlamaCppRepository("http://llama.local")
    response = await repo.create_chat_completion(
        payload={"stream": True, "messages": [{"role": "user", "content": "ping"}]},
        forward_headers={},
    )

    assert response.stream is not None
    stream = response.stream

    first = await anext(stream)
    assert first == b"data: hello\n\n"

    await stream.aclose()

    assert upstream.closed is True
    assert client.closed is True


@pytest.mark.anyio
async def test_fake_stream_uses_test_mode_prefill_and_chunk_delay(monkeypatch: pytest.MonkeyPatch):
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setenv("POTATO_TEST_MODE", "1")
    monkeypatch.setenv("POTATO_FAKE_PREFILL_DELAY_MS", "250")
    monkeypatch.setenv("POTATO_FAKE_STREAM_CHUNK_DELAY_MS", "40")
    monkeypatch.setattr(backend.asyncio, "sleep", _fake_sleep)

    repo = backend.FakeLlamaRepository()
    response = await repo.create_chat_completion(
        payload={"stream": True, "messages": [{"role": "user", "content": "hello"}]},
        forward_headers={},
    )

    assert response.stream is not None
    chunks = []
    async for chunk in response.stream:
        chunks.append(chunk.decode("utf-8"))
        if "[DONE]" in chunks[-1]:
            break

    assert any('"delta":{"role":"assistant"}' in chunk for chunk in chunks)
    assert 0.25 in sleep_calls
    assert 0.04 in sleep_calls


@pytest.mark.anyio
async def test_fake_stream_honors_prefill_delay_override_without_test_mode(monkeypatch: pytest.MonkeyPatch):
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.delenv("POTATO_TEST_MODE", raising=False)
    monkeypatch.setenv("POTATO_FAKE_PREFILL_DELAY_MS", "250")
    monkeypatch.setattr(backend.asyncio, "sleep", _fake_sleep)

    repo = backend.FakeLlamaRepository()
    response = await repo.create_chat_completion(
        payload={"stream": True, "messages": [{"role": "user", "content": "hello"}]},
        forward_headers={},
    )

    assert response.stream is not None
    async for chunk in response.stream:
        if b"[DONE]" in chunk:
            break

    assert 0.25 in sleep_calls


def test_fake_content_has_fake_marker_and_last_user_message():
    payload = {
        "messages": [
            {"role": "system", "content": "be precise"},
            {"role": "user", "content": "what is next for CS?"},
        ]
    }

    content = backend._fake_content(payload)

    assert "[fake-llama.cpp]" in content
    assert "Potato OS" in content
    assert "what is next for CS?" in content


def test_fake_reply_pool_has_ten_entries():
    assert len(backend.FAKE_PARODY_REPLIES) == 10


def test_fake_content_uses_random_choice_for_reply(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(backend.random, "choice", lambda _items: "RANDOM_POTATO_REPLY")
    payload = {"messages": [{"role": "user", "content": "same prompt every time"}]}
    content = backend._fake_content(payload)
    assert "RANDOM_POTATO_REPLY" in content


def test_fake_content_is_deterministic_when_seed_is_provided(monkeypatch: pytest.MonkeyPatch):
    def _random_choice_should_not_run(_items):
        raise AssertionError("global random.choice should not be used for seeded fake replies")

    monkeypatch.setattr(backend.random, "choice", _random_choice_should_not_run)
    payload = {
        "seed": 42,
        "messages": [{"role": "user", "content": "same prompt every time"}],
    }

    first = backend._fake_content(payload)
    second = backend._fake_content(payload)

    assert first == second


def test_fake_default_timing_targets_about_five_tokens_per_second(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("POTATO_TEST_MODE", raising=False)
    monkeypatch.delenv("POTATO_FAKE_PREFILL_DELAY_MS", raising=False)
    monkeypatch.delenv("POTATO_FAKE_STREAM_CHUNK_DELAY_MS", raising=False)

    prefill_s, chunk_s = backend._read_fake_timing_config()
    assert prefill_s == 0.0
    assert 0.19 <= chunk_s <= 0.23


class _TimeoutCapturingClient:
    """Fake httpx.AsyncClient that captures the timeout and raises ReadTimeout."""

    captured_timeouts: list[httpx.Timeout] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        timeout = kwargs.get("timeout")
        if isinstance(timeout, httpx.Timeout):
            _TimeoutCapturingClient.captured_timeouts.append(timeout)

    def build_request(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs

    async def send(self, request: Any, **kwargs: Any) -> None:
        raise httpx.ReadTimeout("read timed out")

    async def post(self, url: str, **kwargs: Any) -> None:
        raise httpx.ReadTimeout("read timed out")

    async def aclose(self) -> None:
        pass

    async def __aenter__(self) -> "_TimeoutCapturingClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


@pytest.mark.anyio
async def test_llama_stream_uses_unbounded_read_timeout(monkeypatch: pytest.MonkeyPatch):
    _TimeoutCapturingClient.captured_timeouts.clear()
    monkeypatch.setattr(backend.httpx, "AsyncClient", _TimeoutCapturingClient)
    repo = backend.LlamaCppRepository("http://llama.local")
    with pytest.raises(BackendProxyError):
        await repo.create_chat_completion(
            payload={"stream": True, "messages": [{"role": "user", "content": "hi"}]},
            forward_headers={},
        )
    assert len(_TimeoutCapturingClient.captured_timeouts) == 1
    assert _TimeoutCapturingClient.captured_timeouts[0].read is None


@pytest.mark.anyio
async def test_llama_non_stream_uses_unbounded_read_timeout(monkeypatch: pytest.MonkeyPatch):
    _TimeoutCapturingClient.captured_timeouts.clear()
    monkeypatch.setattr(backend.httpx, "AsyncClient", _TimeoutCapturingClient)
    repo = backend.LlamaCppRepository("http://llama.local")
    with pytest.raises(BackendProxyError):
        await repo.create_chat_completion(
            payload={"stream": False, "messages": [{"role": "user", "content": "hi"}]},
            forward_headers={},
        )
    assert len(_TimeoutCapturingClient.captured_timeouts) == 1
    assert _TimeoutCapturingClient.captured_timeouts[0].read is None


@pytest.mark.anyio
async def test_llama_both_paths_have_bounded_connect_timeout(monkeypatch: pytest.MonkeyPatch):
    _TimeoutCapturingClient.captured_timeouts.clear()
    monkeypatch.setattr(backend.httpx, "AsyncClient", _TimeoutCapturingClient)
    repo = backend.LlamaCppRepository("http://llama.local")
    for stream in (True, False):
        with pytest.raises(BackendProxyError):
            await repo.create_chat_completion(
                payload={"stream": stream, "messages": [{"role": "user", "content": "hi"}]},
                forward_headers={},
            )
    assert len(_TimeoutCapturingClient.captured_timeouts) == 2
    for t in _TimeoutCapturingClient.captured_timeouts:
        assert t.connect == 5.0
