from __future__ import annotations

import json

import respx


def test_chat_returns_503_when_not_ready(client, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_false)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "placeholder", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["state"] in {"BOOTING", "DOWNLOADING"}


def test_chat_proxies_non_stream_when_ready(client, runtime, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)
    runtime.model_path.write_bytes(b"gguf")

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://llama.test:8080/v1/chat/completions").mock(
            return_value=_json_response(
                200,
                {
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                },
            )
        )

        response = client.post(
            "/v1/chat/completions",
            json={"model": "qwen", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert route.called
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "hello"


def test_chat_proxies_stream_when_ready(client, runtime, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)
    runtime.model_path.write_bytes(b"gguf")

    stream_body = b"data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\n"

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://llama.test:8080/v1/chat/completions").mock(
            return_value=_stream_response(200, stream_body)
        )

        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "qwen",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        ) as response:
            chunks = b"".join(response.iter_bytes())

    assert route.called
    assert response.status_code == 200
    assert b"data:" in chunks


def test_chat_proxies_multimodal_payload_when_ready(client, runtime, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)
    runtime.model_path.write_bytes(b"gguf")

    request_payload = {
        "model": "qwen",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this photo?"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
                ],
            }
        ],
    }

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://llama.test:8080/v1/chat/completions").mock(
            return_value=_json_response(
                200,
                {
                    "id": "chatcmpl-mm-1",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "cat"}}],
                },
            )
        )

        response = client.post("/v1/chat/completions", json=request_payload)

    assert route.called
    assert response.status_code == 200
    forwarded = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert forwarded["messages"][0]["content"][1]["type"] == "image_url"
    assert (
        forwarded["messages"][0]["content"][1]["image_url"]["url"]
        == request_payload["messages"][0]["content"][1]["image_url"]["url"]
    )


def test_chat_proxies_seed_when_present(client, runtime, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)
    runtime.model_path.write_bytes(b"gguf")

    request_payload = {
        "model": "qwen",
        "messages": [{"role": "user", "content": "hello"}],
        "seed": 42,
    }

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://llama.test:8080/v1/chat/completions").mock(
            return_value=_json_response(
                200,
                {
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                },
            )
        )

        response = client.post("/v1/chat/completions", json=request_payload)

    assert route.called
    assert response.status_code == 200
    forwarded = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert forwarded["seed"] == 42


def test_chat_does_not_force_seed_when_absent(client, runtime, monkeypatch):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)
    runtime.model_path.write_bytes(b"gguf")

    request_payload = {
        "model": "qwen",
        "messages": [{"role": "user", "content": "hello"}],
    }

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://llama.test:8080/v1/chat/completions").mock(
            return_value=_json_response(
                200,
                {
                    "id": "chatcmpl-2",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                },
            )
        )

        response = client.post("/v1/chat/completions", json=request_payload)

    assert route.called
    assert response.status_code == 200
    forwarded = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert "seed" not in forwarded


def test_chat_remains_available_when_active_model_is_healthy_but_download_error_exists(
    client,
    runtime,
    monkeypatch,
):
    monkeypatch.setattr("app.main.check_llama_health", _healthy_true)
    runtime.model_path.write_bytes(b"gguf")
    runtime.models_state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "countdown_enabled": True,
                "default_model_downloaded_once": True,
                "active_model_id": "default",
                "default_model_id": "default",
                "current_download_model_id": None,
                "models": [
                    {
                        "id": "default",
                        "filename": runtime.model_path.name,
                        "source_url": "https://example.com/default.gguf",
                        "source_type": "url",
                        "status": "ready",
                        "error": None,
                    },
                    {
                        "id": "side-model",
                        "filename": "side-model.gguf",
                        "source_url": "https://example.com/side-model.gguf",
                        "source_type": "url",
                        "status": "failed",
                        "error": "insufficient_storage",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    runtime.download_state_path.write_text(
        json.dumps(
            {
                "bytes_total": 1000,
                "bytes_downloaded": 0,
                "percent": 0,
                "speed_bps": 0,
                "eta_seconds": 0,
                "error": "insufficient_storage",
            }
        ),
        encoding="utf-8",
    )

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://llama.test:8080/v1/chat/completions").mock(
            return_value=_json_response(
                200,
                {
                    "id": "chatcmpl-side-error",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "still ready"}}],
                },
            )
        )
        response = client.post(
            "/v1/chat/completions",
            json={"model": "qwen", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert route.called
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "still ready"


async def _healthy_true(_runtime):
    return True


async def _healthy_false(_runtime):
    return False


def _json_response(status_code: int, payload: dict):
    import httpx

    return httpx.Response(
        status_code,
        headers={"content-type": "application/json"},
        text=json.dumps(payload),
    )


def _stream_response(status_code: int, payload: bytes):
    import httpx

    stream = httpx.ByteStream(payload)
    return httpx.Response(
        status_code,
        headers={"content-type": "text/event-stream"},
        stream=stream,
    )
