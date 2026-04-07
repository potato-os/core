"""Tests for the LiteRT adapter with mocked litert_lm engine."""

from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient


class _FakeConversation:
    """Mock LiteRT-LM Conversation with send_message / send_message_async."""

    def __init__(self):
        self._history: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def send_message(self, msg: str) -> dict:
        self._history.append(msg)
        return {"content": [{"type": "text", "text": f"Reply to: {msg[:20]}"}]}

    def send_message_async(self, msg: str):
        self._history.append(msg)
        for word in f"Reply to {msg[:10]}".split():
            yield {"content": [{"type": "text", "text": word + " "}]}


class _FakeEngine:
    """Mock LiteRT-LM Engine."""

    def __init__(self, model_path: str = "", backend=None):
        self.model_path = model_path

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def create_conversation(self, **kwargs):
        return _FakeConversation()


class _FakeBackend:
    CPU = "cpu"


@pytest.fixture(autouse=True)
def _mock_litert_lm(monkeypatch):
    """Inject a fake litert_lm module before importing the adapter."""
    fake_module = types.ModuleType("litert_lm")
    fake_module.Engine = _FakeEngine  # type: ignore[attr-defined]
    fake_module.Backend = _FakeBackend  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litert_lm", fake_module)

    # Force re-import of the adapter so it picks up the mock
    for key in list(sys.modules):
        if "litert_adapter" in key:
            del sys.modules[key]

    import core.litert_adapter as adapter
    adapter.litert_lm = fake_module  # type: ignore[attr-defined]
    adapter._engine = _FakeEngine("test.litertlm", backend=_FakeBackend.CPU)
    adapter._conversation = _FakeConversation()
    adapter._conversation.__enter__()
    adapter._conversation_history = []
    yield adapter
    adapter._engine = None
    adapter._conversation = None
    adapter._conversation_history = []


@pytest.fixture
def client(_mock_litert_lm):
    with TestClient(_mock_litert_lm.app) as c:
        yield c


def test_health_ok_when_engine_loaded(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_503_when_no_engine(_mock_litert_lm, client):
    _mock_litert_lm._engine = None
    response = client.get("/health")
    assert response.status_code == 503


def test_chat_completion_non_streaming_openai_format(client):
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Hi"}], "stream": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert len(body["choices"]) == 1
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"]  # non-empty
    assert body["choices"][0]["finish_reason"] == "stop"
    assert "usage" in body


def test_chat_completion_streaming_sse_chunks(client):
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Hi"}], "stream": True},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    text = response.text
    assert "data:" in text
    assert "[DONE]" in text


def test_chat_completion_replays_user_turns_on_divergence(_mock_litert_lm, client):
    """On history divergence, user turns are replayed (assistant turns skipped)."""
    conv = _mock_litert_lm._conversation
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "user", "content": "First"},
                {"role": "assistant", "content": "Response 1"},
                {"role": "user", "content": "Second"},
            ],
            "stream": False,
        },
    )
    assert response.status_code == 200
    # Conversation should have received "First" (replay) + "Second" (final).
    # "Response 1" (assistant) should NOT have been sent through send_message.
    history = _mock_litert_lm._conversation._history
    assert "First" in history
    assert "Second" in history
    # No assistant content should appear in conversation history
    assert not any("Response 1" in h for h in history)


def test_chat_completion_continuation_reuses_conversation(_mock_litert_lm, client):
    """Second turn with matching history should NOT reset the conversation."""
    # First turn
    client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Hello"}], "stream": False},
    )
    # After first turn, history should have the exchange
    assert len(_mock_litert_lm._conversation_history) == 2  # user + assistant

    # Capture the conversation object
    conv_before = _mock_litert_lm._conversation

    # Second turn — includes first turn in history (continuation)
    resp = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": _mock_litert_lm._conversation_history[1]["content"]},
                {"role": "user", "content": "Follow up"},
            ],
            "stream": False,
        },
    )
    assert resp.status_code == 200
    # Same conversation object — no reset
    assert _mock_litert_lm._conversation is conv_before


def test_chat_completion_new_session_resets_conversation(_mock_litert_lm, client):
    """A completely different message history should reset the conversation."""
    # First turn
    client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Hello"}], "stream": False},
    )
    conv_before = _mock_litert_lm._conversation

    # New session — different first message
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Totally different"}], "stream": False},
    )
    assert resp.status_code == 200
    # Conversation was reset — new object
    assert _mock_litert_lm._conversation is not conv_before


def test_chat_completion_handles_system_prompt(client):
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hi"},
            ],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"]


def test_chat_completion_returns_500_on_engine_error(_mock_litert_lm, client):
    """If inference fails, adapter returns 500."""
    class _BrokenConversation:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def send_message(self, msg):
            raise RuntimeError("Engine crashed")

    class _BrokenEngine:
        def create_conversation(self, **kwargs):
            return _BrokenConversation()
        def __exit__(self, *_args):
            pass

    _mock_litert_lm._engine = _BrokenEngine()
    _mock_litert_lm._conversation = _BrokenConversation()
    _mock_litert_lm._conversation_history = []
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Hi"}], "stream": False},
    )
    assert response.status_code == 500
    assert "error" in response.json()


def test_chat_completion_returns_503_when_no_engine(_mock_litert_lm, client):
    _mock_litert_lm._engine = None
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 503


def test_chat_completion_rejects_empty_messages(client):
    response = client.post(
        "/v1/chat/completions",
        json={"messages": []},
    )
    assert response.status_code == 400
