from __future__ import annotations

import json

import respx

from core.main import save_models_state


def test_chat_returns_503_when_not_ready(client, monkeypatch):
    monkeypatch.setattr("core.main.check_llama_health", _healthy_false)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "placeholder", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["state"] in {"BOOTING", "DOWNLOADING"}


def test_chat_proxies_non_stream_when_ready(client, runtime, monkeypatch):
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
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


def test_chat_defaults_qwen35_to_non_thinking_when_not_explicit(client, runtime, monkeypatch):
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
    runtime.model_path = runtime.model_path.parent / "Qwen_Qwen3.5-35B-A3B-Q2_K_L.gguf"
    runtime.model_path.write_bytes(b"gguf")
    _set_active_model_ready(runtime, "qwen35-a3b", runtime.model_path.name)

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://llama.test:8080/v1/chat/completions").mock(
            return_value=_json_response(
                200,
                {
                    "id": "chatcmpl-qwen35",
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
    forwarded = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert forwarded["chat_template_kwargs"]["enable_thinking"] is False


def test_chat_preserves_explicit_qwen35_thinking_override(client, runtime, monkeypatch):
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
    runtime.model_path = runtime.model_path.parent / "Qwen_Qwen3.5-35B-A3B-Q2_K_L.gguf"
    runtime.model_path.write_bytes(b"gguf")
    _set_active_model_ready(runtime, "qwen35-a3b", runtime.model_path.name)

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://llama.test:8080/v1/chat/completions").mock(
            return_value=_json_response(
                200,
                {
                    "id": "chatcmpl-qwen35-override",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
                },
            )
        )

        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen",
                "messages": [{"role": "user", "content": "hello"}],
                "chat_template_kwargs": {"enable_thinking": True},
            },
        )

    assert route.called
    assert response.status_code == 200
    forwarded = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert forwarded["chat_template_kwargs"]["enable_thinking"] is True


def test_chat_proxies_stream_when_ready(client, runtime, monkeypatch):
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
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
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
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
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
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
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
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


def test_chat_applies_persisted_active_model_settings_when_request_omits_them(client, runtime, monkeypatch):
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
    runtime.model_path.write_bytes(b"gguf")
    save_models_state(
        runtime,
        {
            "active_model_id": "default",
            "default_model_id": "default",
            "default_model_downloaded_once": True,
            "models": [
                {
                    "id": "default",
                    "filename": runtime.model_path.name,
                    "source_type": "url",
                    "status": "ready",
                    "error": None,
                    "settings": {
                        "chat": {
                            "temperature": 0.15,
                            "top_p": 0.95,
                            "top_k": 33,
                            "repetition_penalty": 1.1,
                            "presence_penalty": 0.25,
                            "max_tokens": 2048,
                            "stream": False,
                            "generation_mode": "deterministic",
                            "seed": 7,
                            "system_prompt": "Keep it short.",
                        }
                    },
                }
            ],
        },
    )

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://llama.test:8080/v1/chat/completions").mock(
            return_value=_json_response(
                200,
                {
                    "id": "chatcmpl-model-defaults",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                },
            )
        )

        response = client.post(
            "/v1/chat/completions",
            json={"model": "qwen", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert route.called
    assert response.status_code == 200
    forwarded = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert forwarded["temperature"] == 0.15
    assert forwarded["top_p"] == 0.95
    assert forwarded["top_k"] == 33
    assert forwarded["repetition_penalty"] == 1.1
    assert forwarded["presence_penalty"] == 0.25
    assert forwarded["max_tokens"] == 2048
    assert forwarded["stream"] is False
    assert forwarded["generation_mode"] == "deterministic"
    assert forwarded["seed"] == 7
    assert forwarded["messages"][0] == {"role": "system", "content": "Keep it short."}
    assert forwarded["messages"][1] == {"role": "user", "content": "hello"}


def test_chat_remains_available_when_active_model_is_healthy_but_download_error_exists(
    client,
    runtime,
    monkeypatch,
):
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
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


def test_chat_sets_cache_prompt_true_by_default(client, runtime, monkeypatch):
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
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
                    "id": "chatcmpl-cp1",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                },
            )
        )

        response = client.post("/v1/chat/completions", json=request_payload)

    assert route.called
    assert response.status_code == 200
    forwarded = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert forwarded["cache_prompt"] is True


def test_chat_does_not_forward_system_prompt_field(client, runtime, monkeypatch):
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
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
                    "id": "chatcmpl-sp1",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                },
            )
        )

        response = client.post("/v1/chat/completions", json=request_payload)

    assert route.called
    assert response.status_code == 200
    forwarded = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert "system_prompt" not in forwarded


def test_chat_forwards_nonempty_system_prompt(client, runtime, monkeypatch):
    monkeypatch.setattr("core.main.check_llama_health", _healthy_true)
    runtime.model_path.write_bytes(b"gguf")

    request_payload = {
        "model": "qwen",
        "messages": [{"role": "user", "content": "hello"}],
        "system_prompt": "You are a pirate.",
    }

    with respx.mock(assert_all_called=True) as router:
        route = router.post("http://llama.test:8080/v1/chat/completions").mock(
            return_value=_json_response(
                200,
                {
                    "id": "chatcmpl-sp2",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "arrr"}}],
                },
            )
        )

        response = client.post("/v1/chat/completions", json=request_payload)

    assert route.called
    assert response.status_code == 200
    forwarded = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert forwarded["system_prompt"] == "You are a pirate."


def test_cache_prompt_persists_in_model_settings():
    from core.model_state import normalize_model_settings

    settings = normalize_model_settings(
        {"chat": {"cache_prompt": False}},
        filename="test.gguf",
    )
    assert settings["chat"]["cache_prompt"] is False

    settings_default = normalize_model_settings(
        {"chat": {}},
        filename="test.gguf",
    )
    assert settings_default["chat"]["cache_prompt"] is True


def _set_active_model_ready(runtime, model_id: str, filename: str) -> None:
    save_models_state(
        runtime,
        {
            "active_model_id": model_id,
            "default_model_id": "default",
            "default_model_downloaded_once": True,
            "models": [
                {
                    "id": model_id,
                    "filename": filename,
                    "source_type": "upload",
                    "status": "ready",
                    "error": None,
                }
            ],
        },
    )
