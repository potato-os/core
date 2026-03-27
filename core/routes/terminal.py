"""Web terminal — WebSocket endpoint backed by a real PTY session.

Security model: the terminal shares Potato OS's existing unauthenticated LAN
trust model.  There is no user login or session auth — the same as chat,
settings, model management, and SSH (pi:raspberry).  The per-boot token and
Origin check guard against cross-site WebSocket hijacking and act as a light
"page-loaded" speed bump, but they do not constitute real authorization against
a determined LAN client (the token is published in the HTML served by GET /).
App-wide authentication is tracked separately.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import select
import signal
import struct
import termios
import time
import secrets
import uuid

from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_TERMINAL_SESSIONS = 3
IDLE_TIMEOUT_SECONDS = 900
PTY_READ_CHUNK = 4096


def register_terminal_helpers(**_kwargs: object) -> None:
    """Placeholder for consistency with the other route modules."""


def _is_origin_allowed(origin: str | None, request_host: str) -> bool:
    """Check that the Origin header (if present) matches the Host the request arrived on.

    Browsers always send an Origin on cross-origin WebSocket upgrades, so this
    blocks CSWSH from malicious pages.  The comparison is against the *request*
    Host header, so it works for any hostname/IP the user accesses the Pi through
    (potato.local, 192.168.x.x, a custom DNS name, etc.).
    """
    if not origin:
        return False  # require Origin — non-browser clients must use the token
    try:
        origin_host = urlparse(origin).hostname or ""
    except Exception:
        return False
    # request_host may include a port (e.g. "potato.local:1983")
    host_only = request_host.split(":")[0] if request_host else ""
    return origin_host == host_only


def _cleanup_session(session_id: str, sessions: dict) -> None:
    """Kill the PTY child and close the master fd.  Properly reaps the child."""
    session = sessions.pop(session_id, None)
    if session is None:
        return
    pid = session.get("pid")
    master_fd = session.get("master_fd")
    # Close the fd first — this delivers SIGHUP to the shell in most cases.
    if master_fd is not None:
        try:
            os.close(master_fd)
        except OSError:
            pass
    if pid:
        # SIGTERM → brief blocking wait → SIGKILL → final reap
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(10):
            try:
                rpid, _ = os.waitpid(pid, os.WNOHANG)
                if rpid != 0:
                    return  # reaped
            except ChildProcessError:
                return  # already gone
            time.sleep(0.05)
        # Still alive after 500ms — force kill
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)  # blocking — guaranteed to reap after SIGKILL
        except ChildProcessError:
            pass


def _blocking_pty_read(master_fd: int, stop_event: asyncio.Event) -> bytes | None:
    """Blocking read with select-based timeout so the thread can exit."""
    while not stop_event.is_set():
        ready, _, _ = select.select([master_fd], [], [], 0.2)
        if ready:
            try:
                return os.read(master_fd, PTY_READ_CHUNK)
            except OSError:
                return None
    return None


async def _pty_reader(
    ws: WebSocket,
    master_fd: int,
    session_id: str,
    sessions: dict,
    stop_event: asyncio.Event,
) -> None:
    """Read output from the PTY and forward to the WebSocket client.

    When the shell exits (EOF on the master fd), sends an exit message and
    closes the WebSocket so the main receive loop unblocks immediately.
    """
    loop = asyncio.get_running_loop()
    try:
        while session_id in sessions and not stop_event.is_set():
            data = await loop.run_in_executor(
                None, _blocking_pty_read, master_fd, stop_event
            )
            if data is None or len(data) == 0:
                break
            text = data.decode("utf-8", errors="replace")
            try:
                await ws.send_text(json.dumps({"type": "output", "data": text}))
            except (WebSocketDisconnect, RuntimeError):
                break
            if session_id in sessions:
                sessions[session_id]["last_activity"] = time.monotonic()
    except asyncio.CancelledError:
        return

    # Shell exited — notify client and close the WebSocket so the main
    # receive loop (receive_text) unblocks instead of hanging forever.
    stop_event.set()
    try:
        await ws.send_text(json.dumps({"type": "exit", "code": 0}))
        await ws.close(code=1000)
    except Exception:
        pass


@router.websocket("/ws/terminal")
async def terminal_websocket(websocket: WebSocket) -> None:
    sessions: dict = websocket.app.state.terminal_sessions
    expected_token: str = websocket.app.state.terminal_token

    # Auth gate: require a valid per-boot token AND matching Origin.
    # The token is embedded in the HTML page — you must load the UI first.
    # This blocks raw LAN clients (wscat, scripts) that don't have the token,
    # AND cross-site WebSocket hijacking from malicious pages (Origin mismatch).
    client_token = websocket.query_params.get("token", "")
    origin = websocket.headers.get("origin")
    request_host = websocket.headers.get("host", "")

    if not secrets.compare_digest(client_token, expected_token):
        await websocket.close(code=4003)
        return
    if not _is_origin_allowed(origin, request_host):
        await websocket.close(code=4003)
        return

    if len(sessions) >= MAX_TERMINAL_SESSIONS:
        await websocket.accept()
        await websocket.send_text(
            json.dumps({"type": "error", "message": "Session limit reached"})
        )
        await websocket.close(code=4001)
        return

    await websocket.accept()
    session_id = f"term_{uuid.uuid4().hex[:12]}"

    # Spawn a login shell as the configured user (default: pi for sudo access).
    # Uses `sudo -u <user> -i` with a NOPASSWD sudoers rule installed by
    # install_dev.sh / the SD card image build.  Falls back to the service
    # user's own shell when sudo isn't available (dev machines, tests).
    terminal_user = os.environ.get("POTATO_TERMINAL_USER", "pi")
    pid, master_fd = pty.fork()

    if pid == 0:
        # Child process — try sudo -n (non-interactive, never prompts for password)
        # to get a login shell as the target user.  Pre-probe with `true` to avoid
        # a dead PTY if the sudoers rule isn't installed (dev machines, CI).
        import subprocess

        if terminal_user != os.environ.get("USER", ""):
            probe = subprocess.call(
                ["sudo", "-n", "-u", terminal_user, "true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if probe == 0:
                os.execvp("sudo", ["sudo", "-n", "-u", terminal_user, "-i"])
        # Fallback — run own shell (dev machines, or sudo not configured)
        shell = os.environ.get("SHELL", "/bin/bash")
        os.execvp(shell, [shell, "-l"])
        os._exit(1)

    # Parent process — set non-blocking on the master fd
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

    stop_event = asyncio.Event()

    sessions[session_id] = {
        "pid": pid,
        "master_fd": master_fd,
        "created_at": time.monotonic(),
        "last_activity": time.monotonic(),
    }

    reader_task = asyncio.create_task(
        _pty_reader(ws=websocket, master_fd=master_fd, session_id=session_id, sessions=sessions, stop_event=stop_event)
    )

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            msg_type = msg.get("type")
            if msg_type == "input":
                data = msg.get("data", "")
                if data:
                    try:
                        os.write(master_fd, data.encode("utf-8"))
                    except OSError:
                        break
            elif msg_type == "resize":
                cols = int(msg.get("cols", 80))
                rows = int(msg.get("rows", 24))
                try:
                    fcntl.ioctl(
                        master_fd,
                        termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0),
                    )
                except OSError:
                    pass
            # Unknown types are silently ignored

            if session_id in sessions:
                sessions[session_id]["last_activity"] = time.monotonic()
    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        reader_task.cancel()
        try:
            await asyncio.wait_for(reader_task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        _cleanup_session(session_id, sessions)
