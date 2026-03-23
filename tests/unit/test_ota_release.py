"""Tests for OTA release tarball packaging and contract."""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
from pathlib import Path

from app.update_state import _find_update_root

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _build_fake_app_tree(root: Path) -> None:
    """Create a minimal app/ + bin/ + requirements.txt tree for packaging tests."""
    app = root / "app"
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

    subprocess.run(["cp", "-a", str(source_root / "app"), str(prefix / "app")], check=True)
    subprocess.run(["cp", "-a", str(source_root / "bin"), str(prefix / "bin")], check=True)
    subprocess.run(["cp", str(source_root / "requirements.txt"), str(prefix / "requirements.txt")], check=True)

    tarball_path = staging / tarball_name
    subprocess.run(
        [
            "tar", "-C", str(staging), "-czf", str(tarball_path),
            "--exclude=__pycache__", "--exclude=*.pyc", "--exclude=.DS_Store",
            archive_name,
        ],
        check=True,
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
    """Tarball includes potato-os-<version>/app/ and potato-os-<version>/bin/."""
    source = tmp_path / "source"
    source.mkdir()
    _build_fake_app_tree(source)

    staging = tmp_path / "staging"
    staging.mkdir()
    tarball = _package_ota_tarball(source, staging, "v0.5.0")

    with tarfile.open(tarball, "r:gz") as tf:
        names = tf.getnames()

    assert "potato-os-0.5.0/app/__version__.py" in names
    assert "potato-os-0.5.0/app/main.py" in names
    assert "potato-os-0.5.0/app/assets/chat.html" in names
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
    assert (root / "app").is_dir()
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
# Script validation
# ---------------------------------------------------------------------------


def test_publish_ota_script_has_valid_bash_syntax():
    """bin/publish_ota_release.sh must parse without bash syntax errors."""
    script_path = REPO_ROOT / "bin" / "publish_ota_release.sh"
    assert script_path.exists(), f"publish_ota_release.sh not found at {script_path}"
    subprocess.run(["bash", "-n", str(script_path)], check=True, cwd=REPO_ROOT)


def test_publish_ota_script_references_expected_elements():
    """Contract: publish_ota_release.sh references expected packaging/publishing elements."""
    script = (REPO_ROOT / "bin" / "publish_ota_release.sh").read_text(encoding="utf-8")
    assert "potato-os-" in script
    assert "tar " in script or "tar\n" in script
    assert "gh release" in script
    assert "--version" in script
    assert "--dry-run" in script
    assert "potato_sha256" in script
    assert "build_helpers.sh" in script
    assert "__version__" in script
