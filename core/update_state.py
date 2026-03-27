"""OTA update state — version check, state persistence, execution, status payload."""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any, Callable

import httpx

try:
    from core.__version__ import __version__
    from core.runtime_state import RuntimeConfig, _atomic_write_json, read_download_progress
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


def read_first_boot_update_done(runtime: RuntimeConfig) -> bool:
    """Return True if the one-time first-boot update check has completed."""
    state = read_update_state(runtime)
    if state is None:
        return False
    return bool(state.get("first_boot_update_done", False))


def mark_first_boot_update_done(runtime: RuntimeConfig) -> None:
    """Set the first-boot update sentinel so it never runs again."""
    existing = read_update_state(runtime) or {}
    existing["first_boot_update_done"] = True
    _atomic_write_json(runtime.update_state_path, existing)


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
                    if name.startswith("potato-os-") and name.endswith(".tar.gz"):
                        result["tarball_url"] = asset.get("browser_download_url")
                        break

    except httpx.HTTPError:
        result["error"] = "network_error"
    except Exception:
        logger.warning("Unexpected error during update check", exc_info=True)
        result["error"] = "unknown_error"

    # Merge check-phase fields into existing state so execution_* fields
    # written by run_update() are preserved.  Without this, a check during
    # an active download/stage/apply would overwrite the execution state.
    existing = read_update_state(runtime) or {}
    existing.update(result)
    _atomic_write_json(runtime.update_state_path, existing)
    return result


# ---------------------------------------------------------------------------
# Phase B — execution state machine
# ---------------------------------------------------------------------------

UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 600
UPDATE_STAGING_DIR_NAME = ".update_staging"
UPDATE_APPLY_DIRS = ("core", "bin", "apps")

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
    if (extracted_dir / "core").is_dir():
        return extracted_dir
    children = [c for c in extracted_dir.iterdir() if c.is_dir()]
    if len(children) == 1 and (children[0] / "core").is_dir():
        return children[0]
    raise FileNotFoundError(
        f"Cannot find core/ directory in extracted tarball at {extracted_dir}"
    )


def _find_unwritable(base_dir: Path) -> list[Path]:
    """Return paths under app/ and bin/ that the current process cannot write."""
    bad: list[Path] = []
    for dirname in UPDATE_APPLY_DIRS:
        target = base_dir / dirname
        if not target.exists():
            continue
        if not os.access(target, os.W_OK):
            bad.append(target)
        for child in target.rglob("*"):
            if not os.access(child, os.W_OK):
                bad.append(child)
    return bad


def _repair_ownership(base_dir: Path) -> bool:
    """Attempt to fix ownership via sudo chown. Returns True on success."""
    for dirname in UPDATE_APPLY_DIRS:
        target = base_dir / dirname
        if not target.exists():
            continue
        result = subprocess.run(
            ["sudo", "-n", "chown", "-R", "potato:potato", str(target)],
            capture_output=True,
        )
        if result.returncode != 0:
            logger.warning(
                "Ownership repair failed for %s: %s",
                target,
                result.stderr.decode(errors="replace").strip(),
            )
            return False
    logger.info("Ownership repair succeeded for %s", base_dir)
    return True


def _ensure_target_writable(runtime: RuntimeConfig) -> None:
    """Check target dirs are writable; attempt repair if not."""
    bad = _find_unwritable(runtime.base_dir)
    if not bad:
        return
    logger.warning("Found %d non-writable paths, attempting ownership repair", len(bad))
    if _repair_ownership(runtime.base_dir):
        bad = _find_unwritable(runtime.base_dir)
    if bad:
        raise PermissionError(
            f"Ownership drift prevents OTA apply ({len(bad)} non-writable paths "
            f"under {runtime.base_dir}). "
            f"Fix with: sudo chown -R potato:potato {runtime.base_dir}"
        )


def _backup_live_dirs(runtime: RuntimeConfig, backup_dir: Path) -> None:
    """Snapshot current app/ and bin/ so they can be restored on failure."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    for dirname in UPDATE_APPLY_DIRS:
        src = runtime.base_dir / dirname
        if src.is_dir():
            shutil.copytree(src, backup_dir / dirname)
    req = runtime.base_dir / "core" / "requirements.txt"
    if req.is_file():
        shutil.copy2(req, backup_dir / "requirements.txt")


def _restore_from_backup(runtime: RuntimeConfig, backup_dir: Path) -> None:
    """Overwrite live dirs with the pre-update backup."""
    for dirname in UPDATE_APPLY_DIRS:
        bak = backup_dir / dirname
        if not bak.is_dir():
            continue
        dst = runtime.base_dir / dirname
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(bak, dst)
    req_bak = backup_dir / "requirements.txt"
    if req_bak.is_file():
        shutil.copy2(req_bak, runtime.base_dir / "core" / "requirements.txt")


async def apply_staged_update(runtime: RuntimeConfig, staged_dir: Path) -> None:
    """Copy staged files over the running installation.

    Backs up live app/ and bin/ first. If the copy or pip install fails
    the backup is restored so the device boots the old code on restart.
    """
    import asyncio

    backup_dir = staging_dir(runtime) / "_backup"

    def _apply() -> None:
        _ensure_target_writable(runtime)
        _backup_live_dirs(runtime, backup_dir)
        root = _find_update_root(staged_dir)
        for dirname in UPDATE_APPLY_DIRS:
            src = root / dirname
            if not src.is_dir():
                continue
            dst = runtime.base_dir / dirname
            shutil.copytree(src, dst, dirs_exist_ok=True)
        # Copy requirements.txt to app/ (install_dev.sh places it there)
        req_src = root / "requirements.txt"
        if req_src.is_file():
            shutil.copy2(req_src, runtime.base_dir / "core" / "requirements.txt")
        # Set executable bits on shell scripts
        bin_dir = runtime.base_dir / "bin"
        if bin_dir.is_dir():
            for sh_file in bin_dir.glob("*.sh"):
                sh_file.chmod(sh_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    try:
        await asyncio.to_thread(_apply)
        await install_requirements(runtime)
    except Exception:
        logger.warning("Apply failed, restoring backup", exc_info=True)
        try:
            await asyncio.to_thread(_restore_from_backup, runtime, backup_dir)
        except Exception:
            logger.critical("Backup restore also failed", exc_info=True)
        raise


async def install_requirements(runtime: RuntimeConfig) -> None:
    """Run pip install for updated dependencies after apply."""
    import asyncio

    req_path = runtime.base_dir / "core" / "requirements.txt"
    venv_pip = runtime.base_dir / "venv" / "bin" / "pip"
    if not req_path.exists() or not venv_pip.exists():
        return
    proc = await asyncio.create_subprocess_exec(
        str(venv_pip), "install", "-r", str(req_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"pip install failed (exit {proc.returncode}): {stderr.decode(errors='replace').strip()}"
        )


async def signal_service_restart(runtime: RuntimeConfig) -> None:
    """Restart the potato service via the configured reset service.

    Uses the same sudoers-allowed command as start_runtime_reset():
    sudo -n systemctl start --no-block <reset-service>
    """
    import asyncio

    service_name = runtime.runtime_reset_service.strip()
    if not service_name:
        raise RuntimeError("runtime_reset_service not configured")
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", "systemctl", "start", "--no-block", service_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"systemctl start {service_name} failed (exit {proc.returncode}): "
            f"{stderr.decode(errors='replace').strip()}"
        )
