"""Unit tests for OTA update state module."""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import tarfile
import time
from pathlib import Path

import httpx
import pytest
import respx

from core.__version__ import __version__ as APP_VERSION
from core.update_state import (
    EXECUTION_ACTIVE_STATES,
    GITHUB_RELEASES_LATEST_URL,
    UPDATE_APPLY_DIRS,
    apply_staged_update,
    build_update_status,
    check_for_update,
    cleanup_staging,
    detect_post_update_state,
    download_release_tarball,
    extract_tarball,
    install_requirements,
    is_newer,
    is_update_safe,
    parse_version,
    read_execution_state,
    read_update_state,
    signal_service_restart,
    staging_dir,
    write_execution_state,
)

# Test version constants — derived from the real app version so tests
# don't break on release bumps.
_major, _minor, _patch = (int(x) for x in APP_VERSION.split("-")[0].split("."))
TEST_CURRENT_VERSION = APP_VERSION
TEST_NEWER_VERSION = f"{_major}.{_minor + 1}.0"
TEST_OLDER_VERSION = f"{_major}.{_minor}.{max(_patch - 1, 0)}-pre-alpha"


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


def test_parse_version_simple():
    assert parse_version("0.4.0") == ((0, 4, 0), "")


def test_parse_version_with_pre_release():
    assert parse_version("0.3.6-pre-alpha") == ((0, 3, 6), "pre-alpha")


def test_parse_version_strips_v_prefix():
    assert parse_version("v1.2.3") == ((1, 2, 3), "")


def test_parse_version_rc_suffix():
    assert parse_version("1.0.0-rc1") == ((1, 0, 0), "rc1")


def test_parse_version_malformed():
    nums, suffix = parse_version("not-a-version")
    assert nums == (0,)


def test_parse_version_empty():
    nums, suffix = parse_version("")
    assert nums == (0,)


# ---------------------------------------------------------------------------
# is_newer
# ---------------------------------------------------------------------------


def test_newer_release_beats_pre_alpha():
    assert is_newer("0.4.0", "0.3.6-pre-alpha") is True


def test_same_version_is_not_newer():
    assert is_newer("0.4.0", "0.4.0") is False


def test_higher_minor_is_newer():
    assert is_newer("0.5.0", "0.4.0") is True


def test_release_beats_rc_of_same_base():
    assert is_newer("1.0.0", "1.0.0-rc1") is True


def test_rc_is_not_newer_than_release():
    assert is_newer("1.0.0-rc1", "1.0.0") is False


def test_lower_version_is_not_newer():
    assert is_newer("0.2.0", "0.3.6-pre-alpha") is False


def test_v_prefix_stripped_in_comparison():
    assert is_newer("v0.5.0", "0.4.0") is True


def test_malformed_tag_not_newer():
    assert is_newer("not-a-version", "0.4.0") is False


def test_empty_string_not_newer():
    assert is_newer("", "0.4.0") is False


def test_pre_alpha_not_newer_than_same_base_release():
    assert is_newer("0.4.0-pre-alpha", "0.4.0") is False


def test_higher_major_is_newer():
    assert is_newer("2.0.0", "1.99.99") is True


def test_higher_patch_is_newer():
    assert is_newer("0.4.1", "0.4.0") is True


def test_short_tag_release_beats_pre_alpha_of_same_base():
    # v0.3 (two-part tag) should beat 0.3.0-pre-alpha (three-part pre-release)
    assert is_newer("0.3", "0.3.0-pre-alpha") is True


def test_short_tag_equal_to_padded_release():
    # v0.3 and 0.3.0 are the same version — not newer
    assert is_newer("0.3", "0.3.0") is False


# ---------------------------------------------------------------------------
# read_update_state
# ---------------------------------------------------------------------------


def test_read_update_state_none_when_missing(runtime):
    assert read_update_state(runtime) is None


def test_read_update_state_returns_dict_on_valid(runtime):
    state = {
        "available": True,
        "current_version": "0.3.6-pre-alpha",
        "latest_version": "0.5.0",
        "checked_at_unix": int(time.time()),
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    result = read_update_state(runtime)
    assert isinstance(result, dict)
    assert result["available"] is True
    assert result["latest_version"] == "0.5.0"


def test_read_update_state_none_on_corrupt_json(runtime):
    runtime.update_state_path.write_text("not json{{{", encoding="utf-8")
    assert read_update_state(runtime) is None


def test_read_update_state_none_on_non_dict_json(runtime):
    runtime.update_state_path.write_text("[]", encoding="utf-8")
    assert read_update_state(runtime) is None


# ---------------------------------------------------------------------------
# build_update_status
# ---------------------------------------------------------------------------


def test_build_update_status_default_shape_no_state(runtime):
    result = build_update_status(runtime)
    assert result["available"] is False
    assert isinstance(result["current_version"], str)
    assert result["latest_version"] is None
    assert result["release_notes"] is None
    assert result["checked_at_unix"] is None
    assert result["state"] == "idle"
    assert result["deferred"] is False
    assert result["defer_reason"] is None
    assert result["progress"] == {"phase": None, "percent": 0, "error": None}


def test_build_update_status_survives_non_string_latest_version(runtime):
    state = {"latest_version": ["0.4.0"], "checked_at_unix": 1711000000, "error": None}
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    result = build_update_status(runtime)
    assert result["available"] is False
    assert result["latest_version"] is None


def test_build_update_status_populated_from_state(runtime):
    state = {
        "available": True,
        "current_version": TEST_OLDER_VERSION,
        "latest_version": TEST_NEWER_VERSION,
        "release_notes": "Bug fixes",
        "release_url": f"https://github.com/slomin/potato-os/releases/tag/v{TEST_NEWER_VERSION}",
        "tarball_url": None,
        "checked_at_unix": 1711000000,
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    result = build_update_status(runtime)
    assert result["available"] is True
    assert result["latest_version"] == TEST_NEWER_VERSION
    assert result["release_notes"] == "Bug fixes"
    assert result["checked_at_unix"] == 1711000000


def test_build_update_status_with_error_in_state(runtime):
    state = {
        "available": False,
        "current_version": "0.3.6-pre-alpha",
        "latest_version": None,
        "release_notes": None,
        "release_url": None,
        "tarball_url": None,
        "checked_at_unix": 1711000000,
        "error": "rate_limited",
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    result = build_update_status(runtime)
    assert result["available"] is False
    assert result["progress"]["error"] == "rate_limited"


def test_build_update_status_recomputes_availability_from_live_version(runtime, monkeypatch):
    """After upgrading, stale state should not report a phantom update."""
    state = {
        "available": True,
        "current_version": "0.3.6-pre-alpha",
        "latest_version": "0.4.0",
        "release_notes": "notes",
        "release_url": None,
        "tarball_url": None,
        "checked_at_unix": 1711000000,
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    # Simulate the app now running 0.4.0 (upgraded)
    monkeypatch.setattr("core.update_state.__version__", "0.4.0")
    result = build_update_status(runtime)
    assert result["available"] is False
    assert result["current_version"] == "0.4.0"


def test_build_update_status_uses_live_version_not_cached(runtime, monkeypatch):
    state = {
        "available": False,
        "current_version": TEST_OLDER_VERSION,
        "latest_version": TEST_NEWER_VERSION,
        "checked_at_unix": 1711000000,
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    # State was written by old build that thought newer wasn't newer (bug).
    # Live version is still old — should recompute as available.
    monkeypatch.setattr("core.update_state.__version__", "0.3.6-pre-alpha")
    result = build_update_status(runtime)
    assert result["available"] is True
    assert result["current_version"] == "0.3.6-pre-alpha"


def test_build_update_status_deferred_when_download_active(runtime):
    runtime.download_state_path.write_text(
        json.dumps({"bytes_total": 1000, "bytes_downloaded": 500, "percent": 50}),
        encoding="utf-8",
    )
    result = build_update_status(runtime)
    assert result["deferred"] is True
    assert result["defer_reason"] == "download_active"


def test_build_update_status_not_deferred_when_download_complete(runtime):
    runtime.download_state_path.write_text(
        json.dumps({"bytes_total": 1000, "bytes_downloaded": 1000, "percent": 100}),
        encoding="utf-8",
    )
    result = build_update_status(runtime)
    assert result["deferred"] is False


def test_build_update_status_not_deferred_when_download_errored(runtime):
    runtime.download_state_path.write_text(
        json.dumps({"bytes_total": 1000, "bytes_downloaded": 500, "percent": 50, "error": "network"}),
        encoding="utf-8",
    )
    result = build_update_status(runtime)
    assert result["deferred"] is False


# ---------------------------------------------------------------------------
# check_for_update (async — mocked via respx)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_for_update_writes_state_on_success(runtime):
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "tag_name": f"v{TEST_NEWER_VERSION}",
                    "body": "Release notes here",
                    "html_url": f"https://github.com/slomin/potato-os/releases/tag/v{TEST_NEWER_VERSION}",
                    "assets": [
                        {
                            "name": f"potato-os-{TEST_NEWER_VERSION}.tar.gz",
                            "browser_download_url": f"https://github.com/slomin/potato-os/releases/download/v{TEST_NEWER_VERSION}/potato-os-{TEST_NEWER_VERSION}.tar.gz",
                        }
                    ],
                },
            )
        )
        result = await check_for_update(runtime)

    assert result["available"] is True
    assert result["latest_version"] == TEST_NEWER_VERSION
    assert result["release_notes"] == "Release notes here"
    assert result["error"] is None
    assert result["checked_at_unix"] > 0
    assert runtime.update_state_path.exists()


@pytest.mark.anyio
async def test_check_for_update_not_available_when_same_version(runtime, monkeypatch):
    monkeypatch.setattr("core.update_state.__version__", TEST_NEWER_VERSION)
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "tag_name": f"v{TEST_NEWER_VERSION}",
                    "body": "",
                    "html_url": f"https://github.com/slomin/potato-os/releases/tag/v{TEST_NEWER_VERSION}",
                    "assets": [],
                },
            )
        )
        result = await check_for_update(runtime)

    assert result["available"] is False


@pytest.mark.anyio
async def test_check_for_update_handles_rate_limit(runtime):
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(
            return_value=httpx.Response(403, json={"message": "rate limit exceeded"})
        )
        result = await check_for_update(runtime)

    assert result["error"] == "rate_limited"
    assert result["available"] is False


@pytest.mark.anyio
async def test_check_for_update_handles_non_200(runtime):
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        result = await check_for_update(runtime)

    assert result["error"] == "http_500"
    assert result["available"] is False


@pytest.mark.anyio
async def test_check_for_update_handles_network_error(runtime):
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(side_effect=httpx.ConnectError("DNS failure"))
        result = await check_for_update(runtime)

    assert result["error"] == "network_error"
    assert result["available"] is False
    assert runtime.update_state_path.exists()


@pytest.mark.anyio
async def test_check_for_update_handles_malformed_json(runtime):
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(
            return_value=httpx.Response(200, text="<html>not json</html>")
        )
        result = await check_for_update(runtime)

    assert result["error"] == "parse_error"
    assert result["available"] is False


@pytest.mark.anyio
async def test_check_for_update_handles_missing_tag_name(runtime):
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(
            return_value=httpx.Response(200, json={"body": "notes", "html_url": "url", "assets": []})
        )
        result = await check_for_update(runtime)

    assert result["available"] is False
    assert result["latest_version"] is None
    assert result["error"] is None


@pytest.mark.anyio
async def test_check_for_update_extracts_tarball_url(runtime):
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "tag_name": "v0.5.0",
                    "body": "",
                    "html_url": "https://github.com/slomin/potato-os/releases/tag/v0.5.0",
                    "assets": [
                        {
                            "name": "potato-os-0.5.0.tar.gz",
                            "browser_download_url": "https://example.com/tarball.tar.gz",
                        }
                    ],
                },
            )
        )
        result = await check_for_update(runtime)

    assert result["tarball_url"] == "https://example.com/tarball.tar.gz"


@pytest.mark.anyio
async def test_check_for_update_ignores_non_ota_tarball(runtime):
    """Runtime tarballs (ik_llama-*.tar.gz) must not be picked up as OTA assets."""
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "tag_name": "v0.5.0",
                    "body": "",
                    "html_url": "https://github.com/slomin/potato-os/releases/tag/v0.5.0",
                    "assets": [
                        {
                            "name": "ik_llama-abc12345-pi5-opt.tar.gz",
                            "browser_download_url": "https://example.com/runtime.tar.gz",
                        }
                    ],
                },
            )
        )
        result = await check_for_update(runtime)

    assert result["tarball_url"] is None


@pytest.mark.anyio
async def test_check_for_update_picks_ota_over_runtime_tarball(runtime):
    """When both OTA and runtime tarballs are present, OTA tarball is selected."""
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "tag_name": "v0.5.0",
                    "body": "",
                    "html_url": "https://github.com/slomin/potato-os/releases/tag/v0.5.0",
                    "assets": [
                        {
                            "name": "ik_llama-abc12345-pi5-opt.tar.gz",
                            "browser_download_url": "https://example.com/runtime.tar.gz",
                        },
                        {
                            "name": "potato-os-0.5.0.tar.gz",
                            "browser_download_url": "https://example.com/ota.tar.gz",
                        },
                    ],
                },
            )
        )
        result = await check_for_update(runtime)

    assert result["tarball_url"] == "https://example.com/ota.tar.gz"


@pytest.mark.anyio
async def test_check_for_update_preserves_execution_state(runtime):
    """check_for_update() must not overwrite execution_* fields from an active update."""
    # Simulate an in-progress update
    write_execution_state(
        runtime,
        execution_state="downloading",
        phase="downloading",
        percent=42,
        target_version="0.5.0",
        started_at_unix=1711000100,
    )
    with respx.mock(assert_all_called=True) as router:
        router.get(GITHUB_RELEASES_LATEST_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "tag_name": "v0.6.0",
                    "body": "new notes",
                    "html_url": "https://github.com/slomin/potato-os/releases/tag/v0.6.0",
                    "assets": [
                        {
                            "name": "potato-os-0.6.0.tar.gz",
                            "browser_download_url": "https://example.com/ota.tar.gz",
                        }
                    ],
                },
            )
        )
        await check_for_update(runtime)

    # Execution fields must survive the check
    state = read_update_state(runtime)
    assert state["execution_state"] == "downloading"
    assert state["execution_percent"] == 42
    assert state["execution_target_version"] == "0.5.0"
    assert state["execution_started_at_unix"] == 1711000100
    # Check fields should also be updated
    assert state["latest_version"] == "0.6.0"
    assert state["checked_at_unix"] > 0


# ---------------------------------------------------------------------------
# is_update_safe
# ---------------------------------------------------------------------------


def test_is_update_safe_blocked_by_active_download(runtime):
    runtime.download_state_path.write_text(
        json.dumps({"bytes_total": 1000, "bytes_downloaded": 500, "percent": 50}),
        encoding="utf-8",
    )
    safe, reason = is_update_safe(runtime)
    assert safe is False
    assert reason == "download_active"


def test_is_update_safe_clear_when_no_download(runtime):
    safe, reason = is_update_safe(runtime)
    assert safe is True
    assert reason is None


def test_is_update_safe_clear_when_download_complete(runtime):
    runtime.download_state_path.write_text(
        json.dumps({"bytes_total": 1000, "bytes_downloaded": 1000, "percent": 100}),
        encoding="utf-8",
    )
    safe, reason = is_update_safe(runtime)
    assert safe is True
    assert reason is None


def test_is_update_safe_clear_when_download_errored(runtime):
    runtime.download_state_path.write_text(
        json.dumps({"bytes_total": 1000, "bytes_downloaded": 500, "percent": 50, "error": "timeout"}),
        encoding="utf-8",
    )
    safe, reason = is_update_safe(runtime)
    assert safe is True
    assert reason is None


def test_is_update_safe_blocked_by_active_update(runtime):
    write_execution_state(runtime, execution_state="downloading", target_version="0.5.0")
    safe, reason = is_update_safe(runtime)
    assert safe is False
    assert reason == "update_in_progress"


def test_is_update_safe_blocked_by_all_active_states(runtime):
    for active_state in EXECUTION_ACTIVE_STATES:
        write_execution_state(runtime, execution_state=active_state, target_version="0.5.0")
        safe, reason = is_update_safe(runtime)
        assert safe is False, f"Expected blocked for {active_state}"
        assert reason == "update_in_progress"


def test_is_update_safe_clear_after_failed_update(runtime):
    write_execution_state(runtime, execution_state="failed", error="download_failed")
    safe, reason = is_update_safe(runtime)
    assert safe is True
    assert reason is None


# ---------------------------------------------------------------------------
# Phase B — write/read execution state
# ---------------------------------------------------------------------------


def test_write_execution_state_creates_fields(runtime):
    write_execution_state(
        runtime,
        execution_state="downloading",
        phase="downloading",
        percent=42,
        target_version="0.5.0",
        started_at_unix=1711000100,
    )
    state = read_update_state(runtime)
    assert state["execution_state"] == "downloading"
    assert state["execution_phase"] == "downloading"
    assert state["execution_percent"] == 42
    assert state["execution_error"] is None
    assert state["execution_target_version"] == "0.5.0"
    assert state["execution_started_at_unix"] == 1711000100


def test_write_execution_state_preserves_check_fields(runtime):
    check_state = {
        "available": True,
        "current_version": "0.4.0",
        "latest_version": "0.5.0",
        "release_notes": "Bug fixes",
        "tarball_url": "https://example.com/tarball.tar.gz",
        "checked_at_unix": 1711000000,
        "error": None,
    }
    runtime.update_state_path.write_text(json.dumps(check_state), encoding="utf-8")

    write_execution_state(runtime, execution_state="downloading", target_version="0.5.0")

    state = read_update_state(runtime)
    assert state["available"] is True
    assert state["latest_version"] == "0.5.0"
    assert state["tarball_url"] == "https://example.com/tarball.tar.gz"
    assert state["execution_state"] == "downloading"


def test_write_execution_state_error(runtime):
    write_execution_state(
        runtime,
        execution_state="failed",
        error="network_timeout",
        target_version="0.5.0",
    )
    state = read_update_state(runtime)
    assert state["execution_state"] == "failed"
    assert state["execution_error"] == "network_timeout"


def test_read_execution_state_returns_idle_when_missing(runtime):
    assert read_execution_state(runtime) == "idle"


def test_read_execution_state_returns_idle_on_corrupt(runtime):
    runtime.update_state_path.write_text("not json{{{", encoding="utf-8")
    assert read_execution_state(runtime) == "idle"


def test_read_execution_state_returns_value(runtime):
    write_execution_state(runtime, execution_state="staging", target_version="0.5.0")
    assert read_execution_state(runtime) == "staging"


# ---------------------------------------------------------------------------
# Phase B — build_update_status with execution state
# ---------------------------------------------------------------------------


def test_build_update_status_reflects_downloading_state(runtime):
    write_execution_state(
        runtime,
        execution_state="downloading",
        phase="downloading",
        percent=55,
        target_version="0.5.0",
    )
    result = build_update_status(runtime)
    assert result["state"] == "downloading"
    assert result["progress"]["phase"] == "downloading"
    assert result["progress"]["percent"] == 55


def test_build_update_status_reflects_failed_state(runtime):
    write_execution_state(
        runtime,
        execution_state="failed",
        error="extract_failed",
        target_version="0.5.0",
    )
    result = build_update_status(runtime)
    assert result["state"] == "failed"
    assert result["progress"]["error"] == "extract_failed"


def test_build_update_status_execution_error_overrides_check_error(runtime):
    state = {
        "available": True,
        "latest_version": "0.5.0",
        "checked_at_unix": 1711000000,
        "error": "rate_limited",
        "execution_state": "failed",
        "execution_error": "apply_failed",
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    result = build_update_status(runtime)
    assert result["progress"]["error"] == "apply_failed"


def test_build_update_status_falls_back_to_check_error(runtime):
    state = {
        "available": False,
        "latest_version": None,
        "checked_at_unix": 1711000000,
        "error": "rate_limited",
        "execution_state": "idle",
        "execution_error": None,
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")
    result = build_update_status(runtime)
    assert result["progress"]["error"] == "rate_limited"


# ---------------------------------------------------------------------------
# Phase B — detect_post_update_state
# ---------------------------------------------------------------------------


def test_detect_post_update_state_noop_when_idle(runtime):
    assert detect_post_update_state(runtime) is False


def test_detect_post_update_state_noop_when_no_state(runtime):
    assert detect_post_update_state(runtime) is False


def test_detect_post_update_state_clears_restart_pending_on_success(runtime, monkeypatch):
    monkeypatch.setattr("core.update_state.__version__", "0.5.0")
    state = {
        "available": True,
        "latest_version": "0.5.0",
        "execution_state": "restart_pending",
        "execution_target_version": "0.5.0",
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")

    assert detect_post_update_state(runtime) is True

    after = read_update_state(runtime)
    assert after["execution_state"] == "idle"
    assert after["execution_error"] is None
    assert after["execution_target_version"] is None


def test_detect_post_update_state_fails_on_version_mismatch(runtime, monkeypatch):
    monkeypatch.setattr("core.update_state.__version__", "0.4.0")
    state = {
        "available": True,
        "latest_version": "0.5.0",
        "execution_state": "restart_pending",
        "execution_target_version": "0.5.0",
    }
    runtime.update_state_path.write_text(json.dumps(state), encoding="utf-8")

    assert detect_post_update_state(runtime) is False

    after = read_update_state(runtime)
    assert after["execution_state"] == "failed"
    assert after["execution_error"] == "version_unchanged_after_restart"


# ---------------------------------------------------------------------------
# Phase B — staging_dir / cleanup_staging
# ---------------------------------------------------------------------------


def test_staging_dir_path(runtime):
    result = staging_dir(runtime)
    assert result == runtime.base_dir / ".update_staging"


def test_cleanup_staging_removes_dir(runtime):
    stage = staging_dir(runtime)
    stage.mkdir(parents=True)
    (stage / "file.txt").write_text("data")
    cleanup_staging(runtime)
    assert not stage.exists()


def test_cleanup_staging_noop_when_missing(runtime):
    cleanup_staging(runtime)  # should not raise


# ---------------------------------------------------------------------------
# Phase B — download_release_tarball
# ---------------------------------------------------------------------------


def _make_tarball_bytes(contents: dict[str, str]) -> bytes:
    """Create an in-memory tar.gz with the given filename->content map."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in contents.items():
            info = tarfile.TarInfo(name=name)
            encoded = data.encode("utf-8")
            info.size = len(encoded)
            tf.addfile(info, io.BytesIO(encoded))
    return buf.getvalue()


@pytest.mark.anyio
async def test_download_release_tarball_writes_file(runtime):
    tarball_data = _make_tarball_bytes({"core/main.py": "print('hello')"})
    dest = staging_dir(runtime) / "update.tar.gz"

    with respx.mock(assert_all_called=True) as router:
        router.get("https://example.com/update.tar.gz").mock(
            return_value=httpx.Response(
                200,
                content=tarball_data,
                headers={"content-length": str(len(tarball_data))},
            )
        )
        result = await download_release_tarball(runtime, "https://example.com/update.tar.gz", dest)

    assert result == dest
    assert dest.exists()
    assert dest.stat().st_size == len(tarball_data)


@pytest.mark.anyio
async def test_download_release_tarball_reports_progress(runtime):
    tarball_data = _make_tarball_bytes({"core/main.py": "x" * 1000})
    dest = staging_dir(runtime) / "update.tar.gz"
    progress_values: list[int] = []

    with respx.mock(assert_all_called=True) as router:
        router.get("https://example.com/update.tar.gz").mock(
            return_value=httpx.Response(
                200,
                content=tarball_data,
                headers={"content-length": str(len(tarball_data))},
            )
        )
        await download_release_tarball(
            runtime,
            "https://example.com/update.tar.gz",
            dest,
            on_progress=progress_values.append,
        )

    assert len(progress_values) > 0
    assert progress_values[-1] == 100


@pytest.mark.anyio
async def test_download_release_tarball_raises_on_http_error(runtime):
    dest = staging_dir(runtime) / "update.tar.gz"

    with respx.mock(assert_all_called=True) as router:
        router.get("https://example.com/update.tar.gz").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        with pytest.raises(httpx.HTTPStatusError):
            await download_release_tarball(runtime, "https://example.com/update.tar.gz", dest)


# ---------------------------------------------------------------------------
# Phase B — extract_tarball
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extract_tarball_creates_expected_dirs(tmp_path):
    tarball_data = _make_tarball_bytes({
        "potato-os-0.5.0/core/main.py": "print('hello')",
        "potato-os-0.5.0/bin/run.sh": "#!/bin/bash",
    })
    tarball_path = tmp_path / "update.tar.gz"
    tarball_path.write_bytes(tarball_data)

    dest = tmp_path / "extracted"
    await extract_tarball(tarball_path, dest)

    assert (dest / "potato-os-0.5.0" / "core" / "main.py").exists()
    assert (dest / "potato-os-0.5.0" / "bin" / "run.sh").exists()


@pytest.mark.anyio
async def test_extract_tarball_raises_on_corrupt(tmp_path):
    tarball_path = tmp_path / "bad.tar.gz"
    tarball_path.write_text("not a tarball")

    with pytest.raises((tarfile.TarError, EOFError)):
        await extract_tarball(tarball_path, tmp_path / "extracted")


# ---------------------------------------------------------------------------
# Phase B — apply_staged_update
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_apply_staged_update_copies_app_and_bin(runtime):
    staged = staging_dir(runtime) / "extracted" / "potato-os-0.5.0"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "main.py").write_text("# new version")
    (staged / "bin").mkdir(parents=True)
    (staged / "bin" / "run.sh").write_text("#!/bin/bash\necho new")

    # Pre-create target dirs with old content
    (runtime.base_dir / "core").mkdir(parents=True, exist_ok=True)
    (runtime.base_dir / "core" / "main.py").write_text("# old version")

    await apply_staged_update(runtime, staged)

    assert (runtime.base_dir / "core" / "main.py").read_text() == "# new version"
    assert (runtime.base_dir / "bin" / "run.sh").read_text() == "#!/bin/bash\necho new"


@pytest.mark.anyio
async def test_apply_staged_update_sets_executable_bits(runtime):
    staged = staging_dir(runtime) / "extracted" / "potato-os-0.5.0"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "main.py").write_text("# app")
    (staged / "bin").mkdir(parents=True)
    (staged / "bin" / "start.sh").write_text("#!/bin/bash")

    await apply_staged_update(runtime, staged)

    sh_file = runtime.base_dir / "bin" / "start.sh"
    assert sh_file.stat().st_mode & stat.S_IXUSR


@pytest.mark.anyio
async def test_apply_staged_update_skips_state_dir(runtime):
    staged = staging_dir(runtime) / "extracted" / "potato-os-0.5.0"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "main.py").write_text("# new")

    # Pre-create a state file that should not be touched
    state_dir = runtime.base_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "models.json").write_text('{"keep": true}')

    await apply_staged_update(runtime, staged)

    assert (state_dir / "models.json").read_text() == '{"keep": true}'


@pytest.mark.anyio
async def test_apply_staged_update_handles_single_subdir_layout(runtime):
    staged = staging_dir(runtime) / "extracted"
    inner = staged / "potato-os-0.5.0"
    (inner / "core").mkdir(parents=True)
    (inner / "core" / "main.py").write_text("# new")

    await apply_staged_update(runtime, staged)

    assert (runtime.base_dir / "core" / "main.py").read_text() == "# new"


@pytest.mark.anyio
async def test_apply_staged_update_raises_on_missing_app_dir(runtime):
    staged = staging_dir(runtime) / "extracted"
    staged.mkdir(parents=True)
    (staged / "random.txt").write_text("no app dir here")

    with pytest.raises(FileNotFoundError, match="core/"):
        await apply_staged_update(runtime, staged)


@pytest.mark.anyio
async def test_apply_staged_update_copies_requirements_txt(runtime):
    staged = staging_dir(runtime) / "extracted" / "potato-os-0.5.0"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "main.py").write_text("# new")
    (staged / "requirements.txt").write_text("httpx>=0.27\nfastapi>=0.111\n")

    await apply_staged_update(runtime, staged)

    req_dst = runtime.base_dir / "core" / "requirements.txt"
    assert req_dst.exists()
    assert "httpx>=0.27" in req_dst.read_text()


@pytest.mark.anyio
async def test_apply_staged_update_restores_on_pip_failure(runtime, monkeypatch):
    """If pip install fails after file copy, the old tree is restored."""
    # Set up old content
    (runtime.base_dir / "core").mkdir(parents=True, exist_ok=True)
    (runtime.base_dir / "core" / "main.py").write_text("# old version")
    (runtime.base_dir / "core" / "requirements.txt").write_text("httpx>=0.25\n")
    (runtime.base_dir / "bin").mkdir(parents=True, exist_ok=True)
    (runtime.base_dir / "bin" / "run.sh").write_text("#!/bin/bash\necho old")

    # Set up staged new content
    staged = staging_dir(runtime) / "extracted" / "potato-os-0.5.0"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "main.py").write_text("# new version")
    (staged / "bin").mkdir(parents=True)
    (staged / "bin" / "run.sh").write_text("#!/bin/bash\necho new")
    (staged / "requirements.txt").write_text("httpx>=0.27\nnew-dep>=1.0\n")

    # Create fake venv/bin/pip that fails
    venv_bin = runtime.base_dir / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    pip_script = venv_bin / "pip"
    pip_script.write_text("#!/bin/bash\nexit 1")
    pip_script.chmod(pip_script.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(RuntimeError, match="pip install failed"):
        await apply_staged_update(runtime, staged)

    # Old content should be restored
    assert (runtime.base_dir / "core" / "main.py").read_text() == "# old version"
    assert (runtime.base_dir / "bin" / "run.sh").read_text() == "#!/bin/bash\necho old"
    assert "httpx>=0.25" in (runtime.base_dir / "core" / "requirements.txt").read_text()


@pytest.mark.anyio
async def test_apply_staged_update_restores_on_copy_failure(runtime, monkeypatch):
    """If file copy itself fails, the old tree is restored."""
    (runtime.base_dir / "core").mkdir(parents=True, exist_ok=True)
    (runtime.base_dir / "core" / "main.py").write_text("# old version")

    staged = staging_dir(runtime) / "extracted" / "potato-os-0.5.0"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "main.py").write_text("# new version")

    # Make copytree fail on the live overwrite (dirs_exist_ok=True),
    # but allow backup copies (no dirs_exist_ok) to succeed.
    original_copytree = shutil.copytree

    def _failing_copytree(src, dst, **kwargs):
        if kwargs.get("dirs_exist_ok"):
            raise OSError("Disk full")
        return original_copytree(src, dst, **kwargs)

    monkeypatch.setattr("core.update_state.shutil.copytree", _failing_copytree)

    with pytest.raises(OSError, match="Disk full"):
        await apply_staged_update(runtime, staged)

    assert (runtime.base_dir / "core" / "main.py").read_text() == "# old version"


# ---------------------------------------------------------------------------
# Phase B — ownership drift
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_apply_staged_update_repairs_ownership_drift(runtime, monkeypatch):
    """Apply auto-repairs non-writable target dirs via sudo chown."""
    import subprocess as subprocess_mod

    # Set up staged content
    staged = staging_dir(runtime) / "extracted" / "potato-os-0.5.0"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "main.py").write_text("# new version")
    (staged / "bin").mkdir(parents=True)
    (staged / "bin" / "run.sh").write_text("#!/bin/bash\necho new")

    # Pre-create target dirs with non-writable file (simulates root ownership)
    app_dir = runtime.base_dir / "core"
    app_dir.mkdir(parents=True, exist_ok=True)
    old_file = app_dir / "main.py"
    old_file.write_text("# old version")
    old_file.chmod(0o444)

    chown_calls: list[list[str]] = []

    def _mock_chown_run(args, **kwargs):
        chown_calls.append(list(args))
        # Simulate successful chown by making files writable again
        for f in app_dir.rglob("*"):
            if f.is_file():
                f.chmod(0o644)
        app_dir.chmod(0o755)

        class _Result:
            returncode = 0
        return _Result()

    monkeypatch.setattr(subprocess_mod, "run", _mock_chown_run)

    await apply_staged_update(runtime, staged)

    assert (runtime.base_dir / "core" / "main.py").read_text() == "# new version"
    assert len(chown_calls) > 0
    assert "chown" in chown_calls[0]


@pytest.mark.anyio
async def test_apply_staged_update_fails_early_on_unwritable_target(runtime, monkeypatch):
    """Apply fails early with actionable message when repair is unavailable."""
    import subprocess as subprocess_mod

    staged = staging_dir(runtime) / "extracted" / "potato-os-0.5.0"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "main.py").write_text("# new version")

    # Pre-create target with non-writable file
    app_dir = runtime.base_dir / "core"
    app_dir.mkdir(parents=True, exist_ok=True)
    old_file = app_dir / "main.py"
    old_file.write_text("# old version")
    old_file.chmod(0o444)

    def _mock_chown_fail(args, **kwargs):
        class _Result:
            returncode = 1
            stderr = b"sudo: a password is required"
        return _Result()

    monkeypatch.setattr(subprocess_mod, "run", _mock_chown_fail)

    with pytest.raises(PermissionError, match="chown"):
        await apply_staged_update(runtime, staged)

    # Backup should NOT have been created (early exit before backup)
    backup_dir = staging_dir(runtime) / "_backup"
    assert not backup_dir.exists()

    # Original content should be untouched
    old_file.chmod(0o644)  # restore for cleanup
    assert old_file.read_text() == "# old version"


@pytest.mark.anyio
async def test_apply_staged_update_skips_repair_when_writable(runtime, monkeypatch):
    """Apply does not attempt sudo chown when target is already writable."""
    import subprocess as subprocess_mod

    staged = staging_dir(runtime) / "extracted" / "potato-os-0.5.0"
    (staged / "core").mkdir(parents=True)
    (staged / "core" / "main.py").write_text("# new version")

    # Pre-create writable target (normal case)
    (runtime.base_dir / "core").mkdir(parents=True, exist_ok=True)
    (runtime.base_dir / "core" / "main.py").write_text("# old version")

    chown_calls: list = []
    original_run = subprocess_mod.run

    def _tracking_run(args, **kwargs):
        if "chown" in str(args):
            chown_calls.append(list(args))
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess_mod, "run", _tracking_run)

    await apply_staged_update(runtime, staged)

    assert chown_calls == [], "sudo chown should not be called when target is writable"
    assert (runtime.base_dir / "core" / "main.py").read_text() == "# new version"


# ---------------------------------------------------------------------------
# Phase B — signal_service_restart
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_signal_service_restart_uses_reset_service(runtime, monkeypatch):
    calls: list[tuple] = []

    async def _mock_subprocess_exec(*args, **kwargs):
        calls.append(args)

        class _MockProc:
            returncode = 0
            async def communicate(self):
                return b"", b""
        return _MockProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _mock_subprocess_exec)
    await signal_service_restart(runtime)

    assert len(calls) == 1
    assert "potato-runtime-reset.service" in calls[0]
    assert "start" in calls[0]
    assert "restart" not in calls[0]


@pytest.mark.anyio
async def test_signal_service_restart_raises_on_failure(runtime, monkeypatch):
    async def _mock_subprocess_exec(*args, **kwargs):
        class _MockProc:
            returncode = 1
            async def communicate(self):
                return b"", b"permission denied"
        return _MockProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _mock_subprocess_exec)
    with pytest.raises(RuntimeError, match="failed"):
        await signal_service_restart(runtime)


# ---------------------------------------------------------------------------
# First-boot auto-update sentinel
# ---------------------------------------------------------------------------


def test_read_first_boot_update_done_false_when_no_state(runtime):
    from core.update_state import read_first_boot_update_done

    assert read_first_boot_update_done(runtime) is False


def test_read_first_boot_update_done_false_when_missing_field(runtime):
    from core.update_state import read_first_boot_update_done

    runtime.update_state_path.write_text(json.dumps({"available": False}), encoding="utf-8")
    assert read_first_boot_update_done(runtime) is False


def test_read_first_boot_update_done_true_when_set(runtime):
    from core.update_state import read_first_boot_update_done

    runtime.update_state_path.write_text(
        json.dumps({"first_boot_update_done": True}), encoding="utf-8"
    )
    assert read_first_boot_update_done(runtime) is True


def test_mark_first_boot_update_done_creates_state(runtime):
    from core.update_state import mark_first_boot_update_done, read_first_boot_update_done

    mark_first_boot_update_done(runtime)
    assert read_first_boot_update_done(runtime) is True


def test_mark_first_boot_update_done_preserves_existing_state(runtime):
    from core.update_state import mark_first_boot_update_done

    runtime.update_state_path.write_text(
        json.dumps({"available": True, "latest_version": "0.6.0"}), encoding="utf-8"
    )
    mark_first_boot_update_done(runtime)

    state = read_update_state(runtime)
    assert state["first_boot_update_done"] is True
    assert state["available"] is True
    assert state["latest_version"] == "0.6.0"
