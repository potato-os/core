"""Shared helpers and fixtures for unit tests that exercise shell scripts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def write_stub(path: Path, content: str) -> None:
    """Create an executable stub script at *path*."""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def fakebin(tmp_path: Path) -> Path:
    """Return a temporary directory for fake binaries, already created."""
    fb = tmp_path / "fakebin"
    fb.mkdir()
    return fb


@pytest.fixture
def shell_env(fakebin: Path) -> dict[str, str]:
    """Return an env dict with *fakebin* prepended to PATH."""
    env = os.environ.copy()
    env["PATH"] = f"{fakebin}:{env.get('PATH', '')}"
    return env


def run_script(
    script: str | Path,
    *args: str,
    env: dict[str, str],
    check: bool = True,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a shell script as a subprocess and return the result."""
    cmd = [str(script), *args]
    return subprocess.run(
        cmd,
        check=check,
        cwd=cwd or REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
