"""Unit tests for Docker/Colima disk-space preflight in image/build_all.py."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from image.build_all import (
    DOCKER_MIN_SPACE_GB,
    DOCKER_WARN_SPACE_GB,
    _parse_df_available_bytes,
    check_docker_disk_space,
)

# ---------------------------------------------------------------------------
# _parse_df_available_bytes
# ---------------------------------------------------------------------------

ALPINE_DF_OUTPUT = """\
Filesystem           1K-blocks      Used Available Use% Mounted on
overlay               61255492  12345678  48909814  20% /
"""

ALPINE_DF_SMALL = """\
Filesystem           1K-blocks      Used Available Use% Mounted on
overlay               61255492  57255492   4000000   8% /
"""


def test_parse_df_available_bytes_from_alpine_output():
    result = _parse_df_available_bytes(ALPINE_DF_OUTPUT)
    assert result == 48909814 * 1024


def test_parse_df_available_bytes_small_space():
    result = _parse_df_available_bytes(ALPINE_DF_SMALL)
    assert result == 4000000 * 1024


def test_parse_df_returns_none_on_garbage():
    assert _parse_df_available_bytes("not a df output at all") is None
    assert _parse_df_available_bytes("") is None
    assert _parse_df_available_bytes("Filesystem\n") is None


def test_parse_df_handles_extra_whitespace():
    messy = (
        "Filesystem           1K-blocks      Used Available Use% Mounted on\n"
        "overlay               10000000   5000000   5000000  50% /\n"
        "\n"
    )
    assert _parse_df_available_bytes(messy) == 5000000 * 1024


# ---------------------------------------------------------------------------
# check_docker_disk_space
# ---------------------------------------------------------------------------


def _mock_df_result(available_1k_blocks: int) -> subprocess.CompletedProcess:
    stdout = (
        "Filesystem           1K-blocks      Used Available Use% Mounted on\n"
        f"overlay               99999999  {99999999 - available_1k_blocks}  {available_1k_blocks}  50% /\n"
    )
    return subprocess.CompletedProcess([], returncode=0, stdout=stdout, stderr="")


def _gb_to_1k_blocks(gb: float) -> int:
    return int(gb * 1024 * 1024)


def test_preflight_fails_below_minimum(monkeypatch):
    monkeypatch.delenv("POTATO_SKIP_SPACE_PREFLIGHT", raising=False)
    monkeypatch.delenv("POTATO_DOCKER_MIN_SPACE_GB", raising=False)
    blocks = _gb_to_1k_blocks(4.0)
    with patch("image.build_all.run_capture", return_value=_mock_df_result(blocks)):
        with pytest.raises(RuntimeError, match=rf"{DOCKER_MIN_SPACE_GB} GB"):
            check_docker_disk_space()


def test_preflight_warns_below_warning_threshold(monkeypatch, capsys):
    monkeypatch.delenv("POTATO_SKIP_SPACE_PREFLIGHT", raising=False)
    monkeypatch.delenv("POTATO_DOCKER_MIN_SPACE_GB", raising=False)
    monkeypatch.delenv("POTATO_DOCKER_WARN_SPACE_GB", raising=False)
    blocks = _gb_to_1k_blocks(10.0)
    with patch("image.build_all.run_capture", return_value=_mock_df_result(blocks)):
        check_docker_disk_space()  # should NOT raise
    captured = capsys.readouterr()
    assert "Warning" in captured.out
    assert f"{DOCKER_MIN_SPACE_GB} GB" in captured.out


def test_preflight_passes_with_sufficient_space(monkeypatch, capsys):
    monkeypatch.delenv("POTATO_SKIP_SPACE_PREFLIGHT", raising=False)
    monkeypatch.delenv("POTATO_DOCKER_MIN_SPACE_GB", raising=False)
    monkeypatch.delenv("POTATO_DOCKER_WARN_SPACE_GB", raising=False)
    blocks = _gb_to_1k_blocks(20.0)
    with patch("image.build_all.run_capture", return_value=_mock_df_result(blocks)):
        check_docker_disk_space()  # should NOT raise
    captured = capsys.readouterr()
    assert "Warning" not in captured.out


def test_preflight_skips_when_docker_run_fails(monkeypatch, capsys):
    monkeypatch.delenv("POTATO_SKIP_SPACE_PREFLIGHT", raising=False)
    failed = subprocess.CompletedProcess([], returncode=1, stdout="", stderr="error")
    with patch("image.build_all.run_capture", return_value=failed):
        check_docker_disk_space()  # should NOT raise
    captured = capsys.readouterr()
    assert "could not measure" in captured.out.lower() or "warning" in captured.out.lower()


def test_preflight_respects_skip_env_var(monkeypatch, capsys):
    monkeypatch.setenv("POTATO_SKIP_SPACE_PREFLIGHT", "1")
    with patch("image.build_all.run_capture") as mock_run:
        check_docker_disk_space()
        mock_run.assert_not_called()
    captured = capsys.readouterr()
    assert "skipped" in captured.out.lower()


def test_preflight_threshold_env_override(monkeypatch):
    monkeypatch.delenv("POTATO_SKIP_SPACE_PREFLIGHT", raising=False)
    monkeypatch.setenv("POTATO_DOCKER_MIN_SPACE_GB", "4")
    monkeypatch.setenv("POTATO_DOCKER_WARN_SPACE_GB", "6")
    blocks = _gb_to_1k_blocks(5.0)
    with patch("image.build_all.run_capture", return_value=_mock_df_result(blocks)):
        check_docker_disk_space()  # 5GB > 4GB custom minimum → should pass
