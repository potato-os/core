"""Verify the canonical version source is importable and well-formed."""

from __future__ import annotations

import re


def test_version_module_exports_semver_like_string():
    from core.__version__ import __version__

    assert isinstance(__version__, str)
    assert len(__version__) > 0
    # Must look like semver (with optional pre-release tag)
    assert re.match(r"^\d+\.\d+\.\d+", __version__), f"Version {__version__!r} is not semver-like"


def test_fastapi_app_uses_canonical_version():
    from core.__version__ import __version__
    from core.main import create_app

    app = create_app()
    assert app.version == __version__
