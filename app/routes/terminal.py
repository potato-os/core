"""Web terminal — WebSocket endpoint backed by a real PTY session."""

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
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_TERMINAL_SESSIONS = 3
IDLE_TIMEOUT_SECONDS = 900
PTY_READ_CHUNK = 4096


def register_terminal_helpers(**_kwargs: object) -> None:
    """Placeholder for consistency with the other route modules."""


def _cleanup_session(session_id: str, sessions: dict) -> None:
    """Kill the PTY child and close the master fd."""
    session = sessions.pop(session_id, None)
    if session is None:
        return
    pid = session.get("pid")
    master_fd = session.get("master_fd")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
    if master_fd is not None:
        try:
            os.close(master_fd)
        except OSError:
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
    """Read output from the PTY and forward to the WebSocket client."""
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
        pass


@router.websocket("/ws/terminal")
async def terminal_websocket(websocket: WebSocket) -> None:
    sessions: dict = websocket.app.state.terminal_sessions

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
