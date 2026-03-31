"""Tests for the Permitato chat SSE stream and action execution."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response


def _make_sse(*chunks: str, done: bool = True) -> str:
    """Build a fake SSE body from content chunks."""
    lines = []
    for chunk in chunks:
        payload = {"choices": [{"delta": {"content": chunk}}]}
        lines.append(f"data: {json.dumps(payload)}\n\n")
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines)


def _build_app(state):
    """Create a minimal FastAPI app with the permitato router wired up."""
    from fastapi import FastAPI

    from apps.permitato.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.permit_state = state
    return app


def _make_state(tmp_path):
    """Build a PermitState suitable for chat action tests."""
    from unittest.mock import AsyncMock

    from apps.permitato.exceptions import ExceptionStore
    from apps.permitato.state import PermitState

    exc_store = ExceptionStore(data_dir=tmp_path)
    state = PermitState(
        data_dir=tmp_path,
        adapter=AsyncMock(),
        exception_store=exc_store,
        mode="work",
        pihole_available=True,
        exception_group_id=3,
    )
    return state


@pytest.mark.anyio
async def test_chat_stream_strips_markers_and_executes_action(tmp_path):
    """Chat stream must strip markers from SSE output and execute the action."""
    state = _make_state(tmp_path)
    app = _build_app(state)

    sse_body = _make_sse(
        "Sure, I'll unblock that for you. ",
        "[ACTION:request_unblock:test-domain.com:work research]",
    )

    with respx.mock() as router:
        router.post("http://127.0.0.1:1983/v1/chat/completions").mock(
            return_value=Response(
                200,
                text=sse_body,
                headers={"content-type": "text/event-stream"},
            )
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/chat",
                json={"message": "please unblock test-domain.com"},
            )

    assert resp.status_code == 200
    body = resp.text

    # Markers must NOT appear in the stream
    assert "[ACTION:" not in body

    # Action result must be present
    assert "permitato_action" in body
    assert "exception_granted" in body
    assert "test-domain.com" in body

    # Exception must actually exist in the store
    active = state.exception_store.list_active()
    assert len(active) == 1
    assert active[0]["domain"] == "test-domain.com"


@pytest.mark.anyio
async def test_chat_stream_last_marker_wins(tmp_path):
    """When LLM emits multiple markers, the last one must be executed."""
    state = _make_state(tmp_path)
    app = _build_app(state)

    sse_body = _make_sse(
        "Hmm, I shouldn't do that. ",
        "[ACTION:deny_unblock:youtube.com:distraction] ",
        "Actually, you need it for a demo. ",
        "[ACTION:request_unblock:youtube.com:work demo]",
    )

    with respx.mock() as router:
        router.post("http://127.0.0.1:1983/v1/chat/completions").mock(
            return_value=Response(
                200,
                text=sse_body,
                headers={"content-type": "text/event-stream"},
            )
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/chat",
                json={"message": "unblock youtube.com please"},
            )

    assert resp.status_code == 200
    body = resp.text

    # Last marker wins — should be exception_granted, not denied
    assert "exception_granted" in body
    assert "exception_denied" not in body

    # Exception must exist
    active = state.exception_store.list_active()
    assert len(active) == 1
    assert active[0]["domain"] == "youtube.com"
