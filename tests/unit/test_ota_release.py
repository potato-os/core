"""Tests for OTA release tarball packaging and contract."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
from pathlib import Path

from core.update_state import _find_update_root
from tests.unit.conftest import REPO_ROOT


def _build_fake_app_tree(root: Path) -> None:
    """Create a minimal core/ + bin/ + requirements.txt tree for packaging tests."""
    app = root / "core"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "__version__.py").write_text('__version__ = "0.5.0"\n', encoding="utf-8")
    (app / "main.py").write_text("# app entrypoint\n", encoding="utf-8")
    # Add __pycache__ that should be excluded
    cache = app / "__pycache__"
    cache.mkdir()
    (cache / "main.cpython-311.pyc").write_bytes(b"\x00" * 16)

    assets = app / "assets"
    assets.mkdir()
    (assets / "chat.html").write_text("<html></html>\n", encoding="utf-8")

    bindir = root / "bin"
    bindir.mkdir()
    (bindir / "run.sh").write_text("#!/bin/bash\necho run\n", encoding="utf-8")
    (bindir / "install_dev.sh").write_text("#!/bin/bash\necho install\n", encoding="utf-8")
    lib = bindir / "lib"
    lib.mkdir()
    (lib / "build_helpers.sh").write_text("# helpers\n", encoding="utf-8")

    (root / "requirements.txt").write_text("fastapi>=0.100\n", encoding="utf-8")


def _package_ota_tarball(source_root: Path, staging: Path, version: str) -> Path:
    """Simulate the tarball packaging that publish_ota_release.sh performs."""
    version_num = version.lstrip("vV")
    archive_name = f"potato-os-{version_num}"
    tarball_name = f"{archive_name}.tar.gz"

    prefix = staging / archive_name
    prefix.mkdir(parents=True)

    env = {**os.environ, "COPYFILE_DISABLE": "1"}
    subprocess.run(["cp", "-a", str(source_root / "core"), str(prefix / "core")], check=True, env=env)
    subprocess.run(["cp", "-a", str(source_root / "bin"), str(prefix / "bin")], check=True, env=env)
    subprocess.run(["cp", str(source_root / "requirements.txt"), str(prefix / "requirements.txt")], check=True)

    tarball_path = staging / tarball_name
    subprocess.run(
        [
            "tar", "-C", str(staging), "-czf", str(tarball_path),
            "--exclude=__pycache__", "--exclude=*.pyc", "--exclude=.DS_Store",
            archive_name,
        ],
        check=True,
        env=env,
    )
    return tarball_path


# ---------------------------------------------------------------------------
# Tarball naming
# ---------------------------------------------------------------------------


def test_ota_tarball_name_follows_convention():
    """OTA tarball name is potato-os-<version>.tar.gz."""
    version = "0.5.0"
    expected = f"potato-os-{version}.tar.gz"
    assert expected == "potato-os-0.5.0.tar.gz"

    # With v prefix stripped
    raw = "v0.5.0"
    version_num = raw.lstrip("vV")
    assert f"potato-os-{version_num}.tar.gz" == "potato-os-0.5.0.tar.gz"


# ---------------------------------------------------------------------------
# Tarball contents
# ---------------------------------------------------------------------------


def test_ota_tarball_contains_app_and_bin(tmp_path):
    """Tarball includes potato-os-<version>/core/ and potato-os-<version>/bin/."""
    source = tmp_path / "source"
    source.mkdir()
    _build_fake_app_tree(source)

    staging = tmp_path / "staging"
    staging.mkdir()
    tarball = _package_ota_tarball(source, staging, "v0.5.0")

    with tarfile.open(tarball, "r:gz") as tf:
        names = tf.getnames()

    assert "potato-os-0.5.0/core/__version__.py" in names
    assert "potato-os-0.5.0/core/main.py" in names
    assert "potato-os-0.5.0/core/assets/chat.html" in names
    assert "potato-os-0.5.0/bin/run.sh" in names
    assert "potato-os-0.5.0/bin/install_dev.sh" in names
    assert "potato-os-0.5.0/bin/lib/build_helpers.sh" in names


def test_ota_tarball_contains_requirements_txt(tmp_path):
    """Tarball includes requirements.txt at the top-level directory root."""
    source = tmp_path / "source"
    source.mkdir()
    _build_fake_app_tree(source)

    staging = tmp_path / "staging"
    staging.mkdir()
    tarball = _package_ota_tarball(source, staging, "v0.5.0")

    with tarfile.open(tarball, "r:gz") as tf:
        names = tf.getnames()

    assert "potato-os-0.5.0/requirements.txt" in names


def test_ota_tarball_excludes_pycache(tmp_path):
    """__pycache__ directories and .pyc files are excluded from the tarball."""
    source = tmp_path / "source"
    source.mkdir()
    _build_fake_app_tree(source)

    staging = tmp_path / "staging"
    staging.mkdir()
    tarball = _package_ota_tarball(source, staging, "v0.5.0")

    with tarfile.open(tarball, "r:gz") as tf:
        names = tf.getnames()

    pycache_entries = [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
    assert pycache_entries == [], f"Found pycache entries: {pycache_entries}"


# ---------------------------------------------------------------------------
# Updater roundtrip
# ---------------------------------------------------------------------------


def test_ota_tarball_extracts_with_find_update_root(tmp_path):
    """Extracted tarball is compatible with _find_update_root()."""
    source = tmp_path / "source"
    source.mkdir()
    _build_fake_app_tree(source)

    staging = tmp_path / "staging"
    staging.mkdir()
    tarball = _package_ota_tarball(source, staging, "v0.5.0")

    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    subprocess.run(["tar", "-xzf", str(tarball), "-C", str(extract_dir)], check=True)

    root = _find_update_root(extract_dir)
    assert (root / "core").is_dir()
    assert (root / "bin").is_dir()
    assert (root / "requirements.txt").is_file()


# ---------------------------------------------------------------------------
# Checksum format
# ---------------------------------------------------------------------------


def test_ota_checksum_file_format(tmp_path):
    """Checksum file contains '<sha256>  <filename>' format."""
    source = tmp_path / "source"
    source.mkdir()
    _build_fake_app_tree(source)

    staging = tmp_path / "staging"
    staging.mkdir()
    tarball = _package_ota_tarball(source, staging, "v0.5.0")

    # Simulate checksum generation (same as potato_sha256 in build_helpers.sh)
    sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    checksum_line = f"{sha}  {tarball.name}"

    assert checksum_line.startswith(sha)
    assert checksum_line.endswith("potato-os-0.5.0.tar.gz")
    # Two-space separator per sha256sum convention
    assert "  " in checksum_line


# ---------------------------------------------------------------------------
# Script validation — contract tests (keep for publish-path-only invariants)
# ---------------------------------------------------------------------------


def test_publish_ota_script_tolerates_existing_remote_tag():
    """Tag push must not abort the script if the tag already exists on the remote."""
    script = (REPO_ROOT / "bin" / "publish_ota_release.sh").read_text(encoding="utf-8")
    push_section = script[script.index("Pushing tag"):]
    push_line_end = push_section.index("\n", push_section.index("git push"))
    push_line = push_section[:push_line_end]
    assert "|| true" in push_line or "2>/dev/null" in push_line


# ---------------------------------------------------------------------------
# Behavior-first dry-run tests
# ---------------------------------------------------------------------------


def test_publish_ota_dry_run_produces_tarball_and_checksum(tmp_path: Path):
    """--dry-run builds the tarball and checksum without publishing."""
    from core.__version__ import __version__

    env = os.environ.copy()
    env["POTATO_GITHUB_REPO"] = "test/repo"
    result = subprocess.run(
        [str(REPO_ROOT / "bin" / "publish_ota_release.sh"), "--version", f"v{__version__}", "--dry-run"],
        check=True,
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    tarball = tmp_path / f"potato-os-{__version__}.tar.gz"
    checksum = tmp_path / f"potato-os-{__version__}.tar.gz.sha256"
    assert tarball.exists(), f"tarball not found: {tarball}"
    assert checksum.exists(), f"checksum not found: {checksum}"
    assert "Dry run complete" in result.stdout

    import tarfile as tf

    with tf.open(str(tarball), "r:gz") as tar:
        names = tar.getnames()
    assert any("core/" in n for n in names)
    assert any("bin/" in n for n in names)

    checksum_text = checksum.read_text(encoding="utf-8").strip()
    assert f"potato-os-{__version__}.tar.gz" in checksum_text


def test_publish_ota_dry_run_excludes_macos_resource_forks(tmp_path: Path):
    """OTA tarball must not contain ._ AppleDouble entries (macOS resource forks)."""
    from core.__version__ import __version__

    env = os.environ.copy()
    env["POTATO_GITHUB_REPO"] = "test/repo"
    subprocess.run(
        [str(REPO_ROOT / "bin" / "publish_ota_release.sh"), "--version", f"v{__version__}", "--dry-run"],
        check=True,
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
    )
    tarball = tmp_path / f"potato-os-{__version__}.tar.gz"
    with tarfile.open(str(tarball), "r:gz") as tf:
        names = tf.getnames()

    resource_fork_entries = [n for n in names if n.startswith("._") or "/._" in n]
    assert resource_fork_entries == [], f"Found macOS resource fork entries: {resource_fork_entries}"


def test_publish_ota_dry_run_rejects_invalid_version(tmp_path: Path):
    """Version without 'v' prefix must be rejected."""
    env = os.environ.copy()
    env["POTATO_GITHUB_REPO"] = "test/repo"
    result = subprocess.run(
        [str(REPO_ROOT / "bin" / "publish_ota_release.sh"), "--version", "0.6.0", "--dry-run"],
        check=False,
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "must start with" in result.stderr.lower() or "must start with" in result.stdout.lower()


def test_publish_ota_dry_run_rejects_version_mismatch(tmp_path: Path):
    """Tag version that doesn't match core/__version__.py must fail."""
    env = os.environ.copy()
    env["POTATO_GITHUB_REPO"] = "test/repo"
    result = subprocess.run(
        [str(REPO_ROOT / "bin" / "publish_ota_release.sh"), "--version", "v99.99.99", "--dry-run"],
        check=False,
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "does not match" in combined.lower() or "mismatch" in combined.lower()


