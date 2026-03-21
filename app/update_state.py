"""OTA update state — version check, state persistence, status payload."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

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

    if state is None:
        return {
            "available": False,
            "current_version": __version__,
            "latest_version": None,
            "release_notes": None,
            "checked_at_unix": None,
            "state": "idle",
            "deferred": deferred,
            "defer_reason": "download_active" if deferred else None,
            "progress": {"phase": None, "percent": 0, "error": None},
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
        "state": "idle",
        "deferred": deferred,
        "defer_reason": "download_active" if deferred else None,
        "progress": {"phase": None, "percent": 0, "error": state.get("error")},
    }


def is_update_safe(runtime: RuntimeConfig) -> tuple[bool, str | None]:
    """Check whether it is safe to apply an update right now."""
    if _is_download_active(runtime):
        return (False, "download_active")
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
