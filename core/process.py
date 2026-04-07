"""Llama process lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any

try:
    from core.runtime_state import RuntimeConfig
except ModuleNotFoundError:
    from runtime_state import RuntimeConfig  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

LLAMA_SHUTDOWN_TIMEOUT_SECONDS = 5.0


async def terminate_process(proc: Any, *, timeout: float | None = None) -> None:
    """Send SIGTERM, wait, then SIGKILL if needed."""
    if timeout is None:
        timeout = LLAMA_SHUTDOWN_TIMEOUT_SECONDS
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("pid=%s did not exit after SIGTERM, sending SIGKILL", getattr(proc, "pid", "?"))
        proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=3.0)


async def list_llama_server_pids(runtime: RuntimeConfig) -> list[int]:
    """Find running llama-server processes by binary path."""
    llama_server_bin = str(runtime.base_dir / "llama" / "bin" / "llama-server")
    try:
        proc = await asyncio.create_subprocess_exec(
            "pgrep",
            "-f",
            llama_server_bin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return []
    except OSError:
        logger.warning("Could not inspect running llama-server processes", exc_info=True)
        return []

    stdout, _stderr = await proc.communicate()
    if proc.returncode not in {0, 1}:
        return []

    pids: list[int] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        value = line.strip()
        if not value:
            continue
        try:
            pids.append(int(value))
        except ValueError:
            continue
    return pids


async def list_litert_adapter_pids(runtime: RuntimeConfig) -> list[int]:
    """Find running litert adapter processes by command pattern."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pgrep",
            "-f",
            "litert_adapter",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError):
        return []

    stdout, _stderr = await proc.communicate()
    if proc.returncode not in {0, 1}:
        return []

    pids: list[int] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        value = line.strip()
        if not value:
            continue
        try:
            pids.append(int(value))
        except ValueError:
            continue
    return pids


async def terminate_stray_litert_processes(runtime: RuntimeConfig, *, exclude_pids: set[int] | None = None) -> int:
    """Kill any litert adapter processes not in the exclude set."""
    excluded = {int(pid) for pid in (exclude_pids or set())}
    terminated = 0

    async def _kill_matching(sig: signal.Signals) -> int:
        count = 0
        for pid in await list_litert_adapter_pids(runtime):
            if pid in excluded:
                continue
            try:
                os.kill(pid, sig)
                count += 1
            except ProcessLookupError:
                continue
            except PermissionError:
                logger.warning("Permission denied terminating stray litert adapter pid=%s", pid)
            except OSError:
                logger.warning("Could not terminate stray litert adapter pid=%s", pid, exc_info=True)
        return count

    terminated += await _kill_matching(signal.SIGTERM)
    if terminated:
        await asyncio.sleep(0.2)

    remaining = [pid for pid in await list_litert_adapter_pids(runtime) if pid not in excluded]
    if remaining:
        terminated += await _kill_matching(signal.SIGKILL)

    return terminated


async def terminate_stray_llama_processes(runtime: RuntimeConfig, *, exclude_pids: set[int] | None = None) -> int:
    """Kill any llama-server processes not in the exclude set."""
    excluded = {int(pid) for pid in (exclude_pids or set())}
    terminated = 0

    async def _kill_matching(sig: signal.Signals) -> int:
        count = 0
        for pid in await list_llama_server_pids(runtime):
            if pid in excluded:
                continue
            try:
                os.kill(pid, sig)
                count += 1
            except ProcessLookupError:
                continue
            except PermissionError:
                logger.warning("Permission denied terminating stray llama-server pid=%s", pid)
            except OSError:
                logger.warning("Could not terminate stray llama-server pid=%s", pid, exc_info=True)
        return count

    terminated += await _kill_matching(signal.SIGTERM)
    if terminated:
        await asyncio.sleep(0.2)

    remaining = [pid for pid in await list_llama_server_pids(runtime) if pid not in excluded]
    if remaining:
        terminated += await _kill_matching(signal.SIGKILL)

    return terminated
