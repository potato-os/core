from __future__ import annotations


def test_fake_mode_reports_ready_without_model(client, runtime):
    runtime.chat_backend_mode = "fake"
    runtime.allow_fake_fallback = True

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()

    assert body["state"] == "READY"
    assert body["backend"]["active"] == "fake"


def test_auto_mode_falls_back_to_fake_when_model_exists_but_llama_unhealthy(client, runtime, monkeypatch):
    runtime.chat_backend_mode = "auto"
    runtime.allow_fake_fallback = True
    runtime.model_path.write_bytes(b"gguf")
    monkeypatch.setattr("core.main.check_llama_health", _healthy_false)

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()

    assert body["state"] == "READY"
    assert body["backend"]["active"] == "fake"
    assert body["backend"]["fallback_active"] is True


def test_auto_mode_does_not_fallback_to_fake_when_disabled(client, runtime, monkeypatch):
    runtime.chat_backend_mode = "auto"
    runtime.allow_fake_fallback = False
    runtime.model_path.write_bytes(b"gguf")
    monkeypatch.setattr("core.main.check_llama_health", _healthy_false)

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()

    assert body["backend"]["active"] == "llama"
    assert body["backend"]["fallback_active"] is False
    assert body["state"] == "BOOTING"


def test_fake_mode_is_rejected_when_fake_backend_disabled(client, runtime):
    runtime.chat_backend_mode = "fake"
    runtime.allow_fake_fallback = False

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()

    assert body["backend"]["active"] == "llama"
    assert body["backend"]["mode"] == "llama"
    assert body["state"] == "BOOTING"


def test_fake_chat_non_stream_matches_openai_shape(client, runtime, monkeypatch):
    runtime.chat_backend_mode = "fake"
    runtime.allow_fake_fallback = True
    monkeypatch.setenv("POTATO_TEST_MODE", "1")
    monkeypatch.setenv("POTATO_FAKE_STREAM_CHUNK_DELAY_MS", "1")

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3-vl-4b",
            "messages": [{"role": "user", "content": "hello from potato"}],
            "stream": False,
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    content = body["choices"][0]["message"]["content"]
    assert "[fake-llama.cpp]" in content
    assert "Potato OS" in content
    assert "Last user message" in content
    assert body["choices"][0]["finish_reason"] == "stop"


def test_fake_chat_stream_matches_openai_chunk_shape(client, runtime, monkeypatch):
    runtime.chat_backend_mode = "fake"
    runtime.allow_fake_fallback = True
    monkeypatch.setenv("POTATO_TEST_MODE", "1")
    monkeypatch.setenv("POTATO_FAKE_STREAM_CHUNK_DELAY_MS", "1")

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "qwen3-vl-4b",
            "messages": [{"role": "user", "content": "stream test"}],
            "stream": True,
        },
    ) as response:
        chunks = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"object":"chat.completion.chunk"' in chunks
    assert '"delta":{"role":"assistant"}' in chunks
    assert "fake-llama.cpp" in chunks
    assert "Potato" in chunks
    assert '"finish_reason":"stop"' in chunks
    assert "data: [DONE]" in chunks


async def _healthy_false(_runtime):
    return False
