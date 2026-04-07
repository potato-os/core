"""Tests for the LiteRT adapter with mocked litert_lm engine."""

from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient


class _FakeConversation:
    """Mock LiteRT-LM Conversation with send_message / send_message_async."""

    def __init__(self):
        self._history: list = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def send_message(self, msg) -> dict:
        self._history.append(msg)
        label = str(msg)[:20] if isinstance(msg, str) else "multimodal"
        return {"content": [{"type": "text", "text": f"Reply to: {label}"}]}

    def send_message_async(self, msg):
        self._history.append(msg)
        label = str(msg)[:10] if isinstance(msg, str) else "image"
        for word in f"Reply to {label}".split():
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


# -- LiteRT vision support tests ------------------------------------------------


def test_health_reports_vision_false_by_default(client):
    """When engine loaded without vision, /health reports vision=false."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["vision"] is False


def test_health_reports_vision_true_when_probe_succeeds(_mock_litert_lm, client):
    """When vision probe succeeded, /health reports vision=true."""
    _mock_litert_lm._vision_enabled = True
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["vision"] is True


def test_vision_probe_falls_back_to_text_only_engine(_mock_litert_lm):
    """_probe_vision_support catches failures and returns (engine, False)."""
    # Make Engine raise TypeError when vision_backend is passed (simulates
    # litert_lm version that doesn't support the kwarg).
    original_engine_cls = _mock_litert_lm.litert_lm.Engine

    class _VisionUnsupportedEngine:
        def __init__(self, model_path="", backend=None, **kwargs):
            if "vision_backend" in kwargs:
                raise TypeError("unexpected keyword argument 'vision_backend'")
            self.model_path = model_path

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def create_conversation(self, **kwargs):
            return _FakeConversation()

    _mock_litert_lm.litert_lm.Engine = _VisionUnsupportedEngine
    try:
        engine, vision = _mock_litert_lm._probe_vision_support("test.litertlm")
        assert engine is not None
        assert vision is False
    finally:
        _mock_litert_lm.litert_lm.Engine = original_engine_cls


def test_vision_probe_succeeds_when_engine_accepts_vision_backend(_mock_litert_lm):
    """_probe_vision_support returns (engine, True) when Engine accepts vision_backend."""

    class _VisionEngine:
        def __init__(self, model_path="", backend=None, vision_backend=None):
            self.model_path = model_path
            self.vision_backend = vision_backend

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def create_conversation(self, **kwargs):
            return _FakeConversation()

    _mock_litert_lm.litert_lm.Engine = _VisionEngine
    try:
        engine, vision = _mock_litert_lm._probe_vision_support("test.litertlm")
        assert engine is not None
        assert vision is True
    finally:
        _mock_litert_lm.litert_lm.Engine = _FakeEngine


def test_multimodal_message_converted_to_litert_format(_mock_litert_lm):
    """OpenAI image_url content parts are converted to litert-lm blob format."""
    openai_content = [
        {"type": "text", "text": "Describe this image:"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
    ]
    result = _mock_litert_lm._convert_openai_to_litert_content(openai_content)
    assert isinstance(result, list)
    assert result[0] == {"type": "text", "text": "Describe this image:"}
    assert result[1] == {"type": "image", "blob": "AAAA"}


def test_multimodal_inference_sends_dict_message_to_engine(_mock_litert_lm, client):
    """Multimodal content is wrapped as {role, content} dict for send_message."""
    _mock_litert_lm._vision_enabled = True
    client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
                    ],
                }
            ],
            "stream": False,
        },
    )
    # The last send_message call should receive a dict with role/content
    last_sent = _mock_litert_lm._conversation._history[-1]
    assert isinstance(last_sent, dict)
    assert last_sent["role"] == "user"
    assert isinstance(last_sent["content"], list)
    assert last_sent["content"][1] == {"type": "image", "blob": "AAAA"}


def test_base64_extracted_from_data_url(_mock_litert_lm):
    """data:image/png;base64,<data> is correctly split to extract the blob."""
    content = [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
    ]
    result = _mock_litert_lm._convert_openai_to_litert_content(content)
    assert result[0]["blob"] == "iVBORw0KGgo="


def test_remote_image_url_rejected(_mock_litert_lm):
    """Remote https:// image URLs are rejected with ValueError."""
    content = [
        {"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}},
    ]
    with pytest.raises(ValueError, match="Only base64 data URLs"):
        _mock_litert_lm._convert_openai_to_litert_content(content)


def test_structured_text_only_content_accepted_without_vision(_mock_litert_lm, client):
    """Text-only structured content [{"type":"text","text":"hi"}] must not be rejected."""
    _mock_litert_lm._vision_enabled = False
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Hello"}],
                }
            ],
            "stream": False,
        },
    )
    assert response.status_code == 200


def test_text_only_content_passes_through_unchanged(_mock_litert_lm):
    """Plain string content is returned as-is."""
    result = _mock_litert_lm._convert_openai_to_litert_content("Hello, world!")
    assert result == "Hello, world!"


def test_multimodal_message_rejected_when_vision_disabled(_mock_litert_lm, client):
    """When vision is not available, multimodal messages return 400."""
    _mock_litert_lm._vision_enabled = False
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
                    ],
                }
            ],
            "stream": False,
        },
    )
    assert response.status_code == 400
    assert "vision" in response.json()["error"]["message"].lower()


def test_multimodal_message_accepted_when_vision_enabled(_mock_litert_lm, client):
    """When vision is enabled, multimodal messages are accepted."""
    _mock_litert_lm._vision_enabled = True
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
                    ],
                }
            ],
            "stream": False,
        },
    )
    assert response.status_code == 200


def test_messages_match_handles_multimodal_content(_mock_litert_lm):
    """_messages_match works correctly with content-as-list."""
    multimodal_msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
        ],
    }
    history = [multimodal_msg, {"role": "assistant", "content": "It's a photo."}]
    incoming = [
        multimodal_msg,
        {"role": "assistant", "content": "It's a photo."},
        {"role": "user", "content": "Tell me more."},
    ]
    assert _mock_litert_lm._messages_match(incoming, history) is True


def test_conversation_history_tracks_multimodal_messages(_mock_litert_lm, client):
    """After a multimodal turn, history stores content list correctly."""
    _mock_litert_lm._vision_enabled = True
    multimodal_msg = [
        {"type": "text", "text": "What is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
    ]
    client.post(
        "/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": multimodal_msg}],
            "stream": False,
        },
    )
    assert len(_mock_litert_lm._conversation_history) == 2
    assert isinstance(_mock_litert_lm._conversation_history[0]["content"], list)


def test_prompt_token_estimate_handles_multimodal_content(_mock_litert_lm, client):
    """Token estimation doesn't crash when content is a list of parts."""
    _mock_litert_lm._vision_enabled = True
    response = client.post(
        "/v1/chat/completions",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this."},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
                    ],
                }
            ],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["usage"]["prompt_tokens"] >= 0
