"""App Supervisor — process-level lifecycle management for Potato OS apps."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from core.app_manifest import AppManifest, discover_apps
    from core.process import terminate_process
    from core.runtime_state import RuntimeConfig
except ModuleNotFoundError:
    from app_manifest import AppManifest, discover_apps  # type: ignore[no-redef]
    from process import terminate_process  # type: ignore[no-redef]
    from runtime_state import RuntimeConfig  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

APP_SUPERVISOR_POLL_INTERVAL_S = 5
APP_SHUTDOWN_TIMEOUT_S = 10.0
CRASH_LOOP_WINDOW_S = 120
CRASH_LOOP_THRESHOLD = 5
BACKOFF_MAX_S = 60.0
HEALTH_CHECK_TIMEOUT_S = 2.0


@dataclass
class AppInstance:
    manifest: AppManifest
    process: Any | None = None
    status: str = "discovered"
    consecutive_failures: int = 0
    last_started_at: float | None = None
    last_crashed_at: float | None = None
    next_restart_at: float | None = None
    crash_times: list[float] = field(default_factory=list)


def compute_restart_backoff(failure_count: int) -> float:
    return min(2 ** failure_count, BACKOFF_MAX_S)


def is_crash_loop(
    crash_times: list[float],
    *,
    window_s: float = CRASH_LOOP_WINDOW_S,
    threshold: int = CRASH_LOOP_THRESHOLD,
) -> bool:
    if len(crash_times) < threshold:
        return False
    now = time.monotonic()
    recent = [t for t in crash_times if now - t <= window_s]
    return len(recent) >= threshold


def build_app_env(
    manifest: AppManifest,
    *,
    inferno_url: str,
    socket_dir: Path,
    data_dir: Path,
    apps_dir: Path,
) -> dict[str, str]:
    env: dict[str, str] = {
        "POTATO_APP_ID": manifest.id,
        "POTATO_SOCKET_PATH": str(socket_dir / manifest.socket),
        "POTATO_DATA_DIR": str(data_dir),
        "POTATO_ASSETS_DIR": str(apps_dir / manifest.ui_path) if manifest.has_ui else "",
        "POTATO_LOG_LEVEL": os.environ.get("POTATO_LOG_LEVEL", "info"),
    }
    if manifest.inferno:
        env["POTATO_INFERNO_URL"] = inferno_url
    return env


async def start_app(instance: AppInstance, runtime: RuntimeConfig) -> None:
    app_dir = runtime.base_dir / "apps" / instance.manifest.id
    entry = app_dir / instance.manifest.entry
    if not entry.exists():
        logger.error("App entry not found: %s", entry)
        instance.status = "error"
        return

    socket_dir = Path(os.environ.get("POTATO_SOCKET_DIR", "/run/potato"))
    # On real installs, systemd creates /run/potato via RuntimeDirectory=potato.
    # In dev/test, POTATO_SOCKET_DIR points to a writable tmp dir.
    if not socket_dir.is_dir():
        socket_dir.mkdir(parents=True, exist_ok=True)
    # Remove stale socket from a previous unclean exit (SIGKILL, crash)
    stale_socket = socket_dir / instance.manifest.socket
    if stale_socket.exists():
        stale_socket.unlink()
    data_dir = runtime.base_dir / "data" / instance.manifest.id
    data_dir.mkdir(parents=True, exist_ok=True)

    env = {**os.environ, **build_app_env(
        instance.manifest,
        inferno_url=runtime.llama_base_url,
        socket_dir=socket_dir,
        data_dir=data_dir,
        apps_dir=app_dir,
    )}

    instance.process = await asyncio.create_subprocess_exec(
        "python3", str(entry),
        cwd=str(app_dir),
        env=env,
    )
    instance.status = "running"
    instance.last_started_at = time.monotonic()
    logger.info("Started app %s (pid=%s)", instance.manifest.id, instance.process.pid)


async def stop_app(instance: AppInstance) -> None:
    if instance.process is None or instance.process.returncode is not None:
        instance.status = "stopped"
        return
    try:
        await terminate_process(instance.process, timeout=APP_SHUTDOWN_TIMEOUT_S)
    except (asyncio.TimeoutError, OSError):
        logger.critical(
            "App %s pid=%s did not exit after SIGKILL",
            instance.manifest.id,
            getattr(instance.process, "pid", "?"),
        )
    instance.status = "stopped"
    logger.info("Stopped app %s", instance.manifest.id)


async def check_app_health(instance: AppInstance, socket_dir: Path) -> bool:
    socket_path = socket_dir / instance.manifest.socket
    if not socket_path.exists():
        return False
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(json.dumps({"type": "health_check"}).encode() + b"\n")
        await writer.drain()
        response_line = await asyncio.wait_for(reader.readline(), timeout=HEALTH_CHECK_TIMEOUT_S)
        writer.close()
        await writer.wait_closed()
        data = json.loads(response_line)
        return data.get("status") == "ok"
    except (OSError, asyncio.TimeoutError, json.JSONDecodeError, ConnectionRefusedError):
        return False


async def app_supervisor_loop(app: Any, runtime: RuntimeConfig) -> None:
    apps_dir = runtime.base_dir / "apps"
    socket_dir = Path(os.environ.get("POTATO_SOCKET_DIR", "/run/potato"))

    while True:
        try:
            manifests = discover_apps(apps_dir)
            instances: dict[str, AppInstance] = app.state.app_instances

            for manifest in manifests:
                if manifest.id not in instances:
                    instances[manifest.id] = AppInstance(manifest=manifest)

            for app_id, instance in instances.items():
                if instance.status == "crash_loop":
                    continue

                # Process exited — record the crash once, then clear the process reference
                if instance.process is not None and instance.process.returncode is not None:
                    exit_code = instance.process.returncode
                    instance.process = None  # clear so we don't re-count this exit
                    instance.consecutive_failures += 1
                    instance.last_crashed_at = time.monotonic()
                    instance.crash_times.append(time.monotonic())
                    logger.warning(
                        "App %s exited with code %s (failure #%d)",
                        app_id, exit_code, instance.consecutive_failures,
                    )

                    if is_crash_loop(instance.crash_times):
                        instance.status = "crash_loop"
                        logger.critical("App %s is in crash loop, stopping restarts", app_id)
                        continue

                    if not instance.manifest.critical:
                        instance.status = "crashed"
                        continue

                    backoff = compute_restart_backoff(instance.consecutive_failures - 1)
                    instance.next_restart_at = time.monotonic() + backoff
                    instance.status = "waiting_restart"

                # Skip apps that are stopped or crashed (non-critical)
                if instance.status in ("crashed", "stopped"):
                    continue

                # Not running — either first start or waiting for restart
                if instance.process is None:
                    if instance.status == "waiting_restart":
                        if instance.next_restart_at and time.monotonic() < instance.next_restart_at:
                            continue

                    await start_app(instance, runtime)

            await asyncio.sleep(APP_SUPERVISOR_POLL_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("App supervisor loop error")
            await asyncio.sleep(APP_SUPERVISOR_POLL_INTERVAL_S)
