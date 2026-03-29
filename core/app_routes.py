"""Dynamic router loading for apps that provide their own API routes."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

try:
    from core.app_manifest import AppManifest
except ModuleNotFoundError:
    from app_manifest import AppManifest  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


def load_app_router(
    manifest: AppManifest,
    app_dir: Path,
) -> tuple | None:
    """Load an app's router module and return (router, prefix), or None."""
    if not manifest.routes:
        return None

    routes_path = app_dir / manifest.routes
    if not routes_path.is_file():
        logger.warning("App %s declares routes=%s but file not found: %s", manifest.id, manifest.routes, routes_path)
        return None

    try:
        # Ensure the apps parent directory is importable so app modules can
        # use absolute imports like `from apps.permitato.modes import ...`
        apps_parent = str(app_dir.parent.parent)
        if apps_parent not in sys.path:
            sys.path.insert(0, apps_parent)

        spec = importlib.util.spec_from_file_location(f"app_{manifest.id}_routes", routes_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        logger.exception("Failed to load routes for app %s from %s", manifest.id, routes_path)
        return None

    router = getattr(mod, "router", None)
    if router is None:
        logger.warning("App %s routes module has no 'router' attribute: %s", manifest.id, routes_path)
        return None

    prefix = f"/app/{manifest.id}/api"
    return router, prefix
