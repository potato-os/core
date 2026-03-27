"""API tests for the web terminal WebSocket endpoint."""

from __future__ import annotations

import json
import sys
import time
import warnings

import pytest

from core.main import create_app, get_runtime

# pty.fork() is Unix-only — skip the entire module on Windows.
pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning:pty"),
]


@pytest.fixture
def terminal_client(runtime):
    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime
    from starlette.testclient import TestClient

    with TestClient(app) as c:
        yield c


def _ws_url(client):
    """Build the authenticated WS URL with the per-boot token."""
    token = client.app.state.terminal_token
    return f"/ws/terminal?token={token}"


def _recv_until(ws, predicate, *, max_messages=50, timeout=5):
    """Read messages from the WebSocket until predicate(parsed_msg) is True."""
    collected = []
    deadline = time.monotonic() + timeout
    for _ in range(max_messages):
        if time.monotonic() > deadline:
            break
        raw = ws.receive_text()
        msg = json.loads(raw)
        collected.append(msg)
        if predicate(msg):
            return collected
    return collected


def test_terminal_websocket_accepts_connection(terminal_client):
    with terminal_client.websocket_connect(_ws_url(terminal_client), headers={"origin": "http://testserver"}) as ws:
        # Should receive at least one output message (shell prompt)
        msgs = _recv_until(ws, lambda m: m["type"] == "output")
        assert any(m["type"] == "output" for m in msgs)


def test_terminal_sends_input_receives_output(terminal_client):
    with terminal_client.websocket_connect(_ws_url(terminal_client), headers={"origin": "http://testserver"}) as ws:
        # Drain initial prompt output
        _recv_until(ws, lambda m: m["type"] == "output")

        # Send a command
        ws.send_text(json.dumps({"type": "input", "data": "echo potato-terminal-test\r"}))

        # Wait for the echoed output
        msgs = _recv_until(
            ws,
            lambda m: m["type"] == "output" and "potato-terminal-test" in m.get("data", ""),
        )
        combined = "".join(m.get("data", "") for m in msgs if m["type"] == "output")
        assert "potato-terminal-test" in combined


def test_terminal_resize(terminal_client):
    with terminal_client.websocket_connect(_ws_url(terminal_client), headers={"origin": "http://testserver"}) as ws:
        # Drain initial output
        _recv_until(ws, lambda m: m["type"] == "output")

        # Send resize — should not crash or produce an error
        ws.send_text(json.dumps({"type": "resize", "cols": 120, "rows": 40}))

        # Verify we can still send input after resize
        ws.send_text(json.dumps({"type": "input", "data": "echo resize-ok\r"}))
        msgs = _recv_until(
            ws,
            lambda m: m["type"] == "output" and "resize-ok" in m.get("data", ""),
        )
        combined = "".join(m.get("data", "") for m in msgs if m["type"] == "output")
        assert "resize-ok" in combined


def test_terminal_session_limit(terminal_client):
    from core.routes.terminal import MAX_TERMINAL_SESSIONS

    # Open MAX sessions
    open_ws = []
    for _ in range(MAX_TERMINAL_SESSIONS):
        ws = terminal_client.websocket_connect(_ws_url(terminal_client), headers={"origin": "http://testserver"})
        ws.__enter__()
        # Drain initial output so the PTY is fully set up
        _recv_until(ws, lambda m: m["type"] == "output")
        open_ws.append(ws)

    try:
        # The next connection should be rejected
        with terminal_client.websocket_connect(_ws_url(terminal_client), headers={"origin": "http://testserver"}) as overflow_ws:
            msgs = _recv_until(overflow_ws, lambda m: m["type"] == "error")
            assert any(
                m["type"] == "error" and "limit" in m.get("message", "").lower()
                for m in msgs
            )
    finally:
        for ws in open_ws:
            ws.__exit__(None, None, None)


def test_terminal_session_cleanup_on_disconnect(terminal_client):
    with terminal_client.websocket_connect(_ws_url(terminal_client), headers={"origin": "http://testserver"}) as ws:
        _recv_until(ws, lambda m: m["type"] == "output")
        sessions_before = len(terminal_client.app.state.terminal_sessions)
        assert sessions_before >= 1

    # After context manager exits (disconnect), session should be cleaned up
    assert len(terminal_client.app.state.terminal_sessions) == 0


def test_terminal_invalid_json_ignored(terminal_client):
    with terminal_client.websocket_connect(_ws_url(terminal_client), headers={"origin": "http://testserver"}) as ws:
        _recv_until(ws, lambda m: m["type"] == "output")

        # Send garbage — should not crash the connection
        ws.send_text("this is not json at all")

        # Verify the connection still works
        ws.send_text(json.dumps({"type": "input", "data": "echo still-alive\r"}))
        msgs = _recv_until(
            ws,
            lambda m: m["type"] == "output" and "still-alive" in m.get("data", ""),
        )
        combined = "".join(m.get("data", "") for m in msgs if m["type"] == "output")
        assert "still-alive" in combined


def test_terminal_unknown_message_type_ignored(terminal_client):
    with terminal_client.websocket_connect(_ws_url(terminal_client), headers={"origin": "http://testserver"}) as ws:
        _recv_until(ws, lambda m: m["type"] == "output")

        # Send unknown type — should not crash
        ws.send_text(json.dumps({"type": "nonexistent", "data": "whatever"}))

        # Verify the connection still works
        ws.send_text(json.dumps({"type": "input", "data": "echo type-ok\r"}))
        msgs = _recv_until(
            ws,
            lambda m: m["type"] == "output" and "type-ok" in m.get("data", ""),
        )
        combined = "".join(m.get("data", "") for m in msgs if m["type"] == "output")
        assert "type-ok" in combined


def test_terminal_rejects_bad_origin(terminal_client):
    """Cross-origin WebSocket connections must be rejected."""
    from starlette.websockets import WebSocketDisconnect as _WSD

    with pytest.raises(_WSD):
        with terminal_client.websocket_connect(
            _ws_url(terminal_client),
            headers={"origin": "https://evil.example.com"},
        ) as ws:
            ws.receive_text()


def test_terminal_rejects_missing_token(terminal_client):
    """Connections without a valid token must be rejected."""
    from starlette.websockets import WebSocketDisconnect as _WSD

    with pytest.raises(_WSD):
        with terminal_client.websocket_connect(
            "/ws/terminal",
            headers={"origin": "http://testserver"},
        ) as ws:
            ws.receive_text()


def test_terminal_rejects_wrong_token(terminal_client):
    """Connections with an invalid token must be rejected."""
    from starlette.websockets import WebSocketDisconnect as _WSD

    with pytest.raises(_WSD):
        with terminal_client.websocket_connect(
            "/ws/terminal?token=wrong-token-value",
            headers={"origin": "http://testserver"},
        ) as ws:
            ws.receive_text()


def test_terminal_shell_exit_closes_websocket(terminal_client):
    """P3: When the user types 'exit', the WS should close and session should be freed."""
    with terminal_client.websocket_connect(_ws_url(terminal_client), headers={"origin": "http://testserver"}) as ws:
        _recv_until(ws, lambda m: m["type"] == "output")

        # Tell the shell to exit
        ws.send_text(json.dumps({"type": "input", "data": "exit\r"}))

        # Should receive an exit message before the connection closes
        msgs = _recv_until(ws, lambda m: m["type"] == "exit", timeout=5)
        assert any(m["type"] == "exit" for m in msgs)

    # Session should be cleaned up with no zombies
    assert len(terminal_client.app.state.terminal_sessions) == 0
