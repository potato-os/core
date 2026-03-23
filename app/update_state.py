"""OTA update state — version check, state persistence, execution, status payload."""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import tarfile
import time
from pathlib import Path
from typing import Any, Callable

import httpx

try:
    from app.__version__ import __version__
    from app.runtime_state import RuntimeConfig, _atomic_write_json, read_download_progress
except ModuleNotFoundError:
    from __version__ import __version__  # type: ignore[no-redef]
    from runtime_state import RuntimeConfig, _atomic_write_json, read_download_progress  # type: ignore[no-redef]

logger = logging.getLogger("potato")

GITHUB_RELEASES_LATEST_URL = "https://api.github.com/repos/slomin/potato-os/releases/latest"
GITHUB_CHECK_TIMEOUT_SECONDS = 10


def parse_version(version_str: str) -> tuple[tuple[int, ...], str]:
    """Parse a version string into (numeric_tuple, pre_release_suffix).

    Examples:
        "0.4.0"           -> ((0, 4, 0), "")
        "v0.3.6-pre-alpha" -> ((0, 3, 6), "pre-alpha")
        "1.0.0-rc1"       -> ((1, 0, 0), "rc1")
        "bad"             -> ((0,), "")
    """
    s = version_str.strip().lstrip("vV")
    if not s:
        return ((0,), "")

    parts = s.split("-", 1)
    base = parts[0]
    suffix = parts[1] if len(parts) > 1 else ""

    nums: list[int] = []
    for segment in base.split("."):
        try:
            nums.append(int(segment))
        except ValueError:
            nums.append(0)

    if not nums:
        return ((0,), suffix)

    return (tuple(nums), suffix)


def _pad_tuple(t: tuple[int, ...], length: int) -> tuple[int, ...]:
    return t + (0,) * (length - len(t))


def is_newer(latest: str, current: str) -> bool:
    """Return True if *latest* is strictly newer than *current*."""
    latest_nums, latest_suffix = parse_version(latest)
    current_nums, current_suffix = parse_version(current)

    # Normalize length so (0,3) and (0,3,0) compare equal.
    max_len = max(len(latest_nums), len(current_nums))
    latest_nums = _pad_tuple(latest_nums, max_len)
    current_nums = _pad_tuple(current_nums, max_len)

    if latest_nums != current_nums:
        return latest_nums > current_nums

    # Same numeric base: release (no suffix) beats pre-release (has suffix).
    if current_suffix and not latest_suffix:
        return True
    if latest_suffix and not current_suffix:
        return False

    # Both have or lack suffixes with same base — not newer.
    return False


def read_update_state(runtime: RuntimeConfig) -> dict[str, Any] | None:
    """Read persisted update state. Returns None if missing or corrupt."""
    if not runtime.update_state_path.exists():
        return None
    try:
        data = json.loads(runtime.update_state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _is_download_active(runtime: RuntimeConfig) -> bool:
    """Return True if a model download is in progress (not errored, not complete)."""
    progress = read_download_progress(runtime)
    if progress.get("error"):
        return False
    downloaded = progress.get("bytes_downloaded", 0)
    total = progress.get("bytes_total", 0)
    if downloaded > 0 and total > 0 and downloaded < total:
        return True
    percent = progress.get("percent", 0)
    if 0 < percent < 100:
        return True
    return False


def build_update_status(runtime: RuntimeConfig) -> dict[str, Any]:
    """Build the ``update`` sub-payload for ``/status``."""
    state = read_update_state(runtime)
    deferred = _is_download_active(runtime)

    exec_state = "idle"
    exec_phase: str | None = None
    exec_percent = 0
    exec_error: str | None = None
    check_error: str | None = None

    if state is not None:
        exec_state = str(state.get("execution_state", "idle") or "idle")
        exec_phase = state.get("execution_phase")
        exec_percent = int(state.get("execution_percent", 0) or 0)
        exec_error = state.get("execution_error")
        check_error = state.get("error")

    progress_error = exec_error or check_error

    if state is None:
        return {
            "available": False,
            "current_version": __version__,
            "latest_version": None,
            "release_notes": None,
            "checked_at_unix": None,
            "state": exec_state,
            "deferred": deferred,
            "defer_reason": "download_active" if deferred else None,
            "progress": {"phase": exec_phase, "percent": exec_percent, "error": progress_error},
        }

    latest_version = state.get("latest_version")
    if not isinstance(latest_version, str):
        latest_version = None
    available = is_newer(latest_version, __version__) if latest_version else False

    return {
        "available": available,
        "current_version": __version__,
        "latest_version": latest_version,
        "release_notes": state.get("release_notes"),
        "checked_at_unix": state.get("checked_at_unix"),
        "state": exec_state,
        "deferred": deferred,
        "defer_reason": "download_active" if deferred else None,
        "progress": {"phase": exec_phase, "percent": exec_percent, "error": progress_error},
    }


def is_update_safe(runtime: RuntimeConfig) -> tuple[bool, str | None]:
    """Check whether it is safe to apply an update right now."""
    if _is_download_active(runtime):
        return (False, "download_active")
    exec_state = read_execution_state(runtime)
    if exec_state in EXECUTION_ACTIVE_STATES:
        return (False, "update_in_progress")
    return (True, None)


async def check_for_update(runtime: RuntimeConfig) -> dict[str, Any]:
    """Hit GitHub Releases API, compare versions, persist result."""
    result: dict[str, Any] = {
        "available": False,
        "current_version": __version__,
        "latest_version": None,
        "release_notes": None,
        "release_url": None,
        "tarball_url": None,
        "checked_at_unix": int(time.time()),
        "error": None,
    }

    try:
        async with httpx.AsyncClient(
            timeout=GITHUB_CHECK_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            resp = await client.get(
                GITHUB_RELEASES_LATEST_URL,
                headers={"Accept": "application/vnd.github+json"},
            )

        if resp.status_code == 403:
            result["error"] = "rate_limited"
        elif resp.status_code != 200:
            result["error"] = f"http_{resp.status_code}"
        else:
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                result["error"] = "parse_error"
                data = None

            if data is not None:
                tag = str(data.get("tag_name") or "")
                latest_version = tag.lstrip("vV") if tag else None

                if latest_version:
                    result["available"] = is_newer(latest_version, __version__)
                    result["latest_version"] = latest_version
                result["release_notes"] = data.get("body") or None
                result["release_url"] = data.get("html_url") or None

                # Find tarball asset.
                for asset in data.get("assets") or []:
                    name = str(asset.get("name") or "")
                    if name.endswith(".tar.gz"):
                        result["tarball_url"] = asset.get("browser_download_url")
                        break

    except httpx.HTTPError:
        result["error"] = "network_error"
    except Exception:
        logger.warning("Unexpected error during update check", exc_info=True)
        result["error"] = "unknown_error"

    _atomic_write_json(runtime.update_state_path, result)
    return result


# ---------------------------------------------------------------------------
# Phase B — execution state machine
# ---------------------------------------------------------------------------

UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 600
UPDATE_STAGING_DIR_NAME = ".update_staging"
UPDATE_APPLY_DIRS = ("app", "bin")

EXECUTION_ACTIVE_STATES = frozenset({"downloading", "staging", "applying", "restart_pending"})


def staging_dir(runtime: RuntimeConfig) -> Path:
    """Return the staging directory path."""
    return runtime.base_dir / UPDATE_STAGING_DIR_NAME


def cleanup_staging(runtime: RuntimeConfig) -> None:
    """Remove the staging directory if it exists."""
    stage = staging_dir(runtime)
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)


def read_execution_state(runtime: RuntimeConfig) -> str:
    """Read execution_state from update.json. Returns 'idle' if missing."""
    state = read_update_state(runtime)
    if state is None:
        return "idle"
    return str(state.get("execution_state", "idle") or "idle")


def write_execution_state(
    runtime: RuntimeConfig,
    *,
    execution_state: str,
    phase: str | None = None,
    percent: int = 0,
    error: str | None = None,
    target_version: str | None = None,
    started_at_unix: int | None = None,
) -> None:
    """Merge execution fields into update.json atomically."""
    existing = read_update_state(runtime) or {}
    existing["execution_state"] = execution_state
    existing["execution_phase"] = phase
    existing["execution_percent"] = percent
    existing["execution_error"] = error
    if target_version is not None:
        existing["execution_target_version"] = target_version
    if started_at_unix is not None:
        existing["execution_started_at_unix"] = started_at_unix
    _atomic_write_json(runtime.update_state_path, existing)


def detect_post_update_state(runtime: RuntimeConfig) -> bool:
    """Called at startup. Detect if an update was just applied after restart."""
    state = read_update_state(runtime)
    if state is None:
        return False
    exec_state = state.get("execution_state")
    if exec_state != "restart_pending":
        return False
    target = state.get("execution_target_version")
    if target and is_newer(target, __version__):
        # Target is still newer — version didn't change, update failed
        state["execution_state"] = "failed"
        state["execution_error"] = "version_unchanged_after_restart"
        _atomic_write_json(runtime.update_state_path, state)
        return False
    # Version matches or exceeds target — update succeeded
    state["execution_state"] = "idle"
    state["execution_phase"] = None
    state["execution_percent"] = 0
    state["execution_error"] = None
    state["execution_target_version"] = None
    state["execution_started_at_unix"] = None
    _atomic_write_json(runtime.update_state_path, state)
    return True


async def download_release_tarball(
    runtime: RuntimeConfig,
    url: str,
    dest: Path,
    *,
    on_progress: Callable[[int], None] | None = None,
) -> Path:
    """Stream-download a release tarball to dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(
        timeout=UPDATE_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and total > 0:
                        on_progress(min(100, int(downloaded * 100 / total)))
    return dest


async def extract_tarball(tarball_path: Path, dest_dir: Path) -> None:
    """Extract tarball to dest_dir in a thread."""
    import asyncio

    def _extract() -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball_path, "r:gz") as tf:
            tf.extractall(dest_dir, filter="data")

    await asyncio.to_thread(_extract)


def _find_update_root(extracted_dir: Path) -> Path:
    """Find the root of extracted update content.

    Handles both flat layout (app/ directly in extracted_dir) and
    single-subdir layout (potato-os-0.5.0/app/ inside extracted_dir).
    """
    if (extracted_dir / "app").is_dir():
        return extracted_dir
    children = [c for c in extracted_dir.iterdir() if c.is_dir()]
    if len(children) == 1 and (children[0] / "app").is_dir():
        return children[0]
    raise FileNotFoundError(
        f"Cannot find app/ directory in extracted tarball at {extracted_dir}"
    )


async def apply_staged_update(runtime: RuntimeConfig, staged_dir: Path) -> None:
    """Copy staged files over the running installation."""
    import asyncio

    def _apply() -> None:
        root = _find_update_root(staged_dir)
        for dirname in UPDATE_APPLY_DIRS:
            src = root / dirname
            if not src.is_dir():
                continue
            dst = runtime.base_dir / dirname
            shutil.copytree(src, dst, dirs_exist_ok=True)
        # Set executable bits on shell scripts
        bin_dir = runtime.base_dir / "bin"
        if bin_dir.is_dir():
            for sh_file in bin_dir.glob("*.sh"):
                sh_file.chmod(sh_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    await asyncio.to_thread(_apply)


async def signal_service_restart() -> None:
    """Ask systemd to restart the potato service."""
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", "systemctl", "restart", "--no-block", "potato.service",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
