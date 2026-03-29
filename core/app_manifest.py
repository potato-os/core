"""App manifest parsing, validation, and discovery."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = ("id", "name", "entry")


@dataclass
class AppManifest:
    id: str = ""
    name: str = ""
    version: str = ""
    entry: str = ""
    critical: bool = False
    has_ui: bool = False
    ui_path: str = ""
    socket: str = ""
    inferno: bool = False
    description: str = ""
    routes: str = ""

    @classmethod
    def from_file(cls, path: Path) -> AppManifest:
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {path}")
        raw = path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Manifest contains invalid JSON: {path}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Manifest is not a JSON object: {path}")
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            version=str(data.get("version", "")),
            entry=str(data.get("entry", "")),
            critical=bool(data.get("critical", False)),
            has_ui=bool(data.get("has_ui", False)),
            ui_path=str(data.get("ui_path", "")),
            socket=str(data.get("socket", "")),
            inferno=bool(data.get("inferno", False)),
            description=str(data.get("description", "")),
            routes=str(data.get("routes", "")),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        for f in _REQUIRED_FIELDS:
            if not getattr(self, f, ""):
                errors.append(f"missing required field: {f}")
        return errors


def discover_apps(apps_dir: Path) -> list[AppManifest]:
    """Scan apps_dir/*/app.json and return manifests that parse and validate."""
    if not apps_dir.is_dir():
        return []
    manifests: list[AppManifest] = []
    for child in sorted(apps_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "app.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = AppManifest.from_file(manifest_path)
        except (ValueError, FileNotFoundError):
            logger.warning("Skipping invalid manifest: %s", manifest_path)
            continue
        errors = manifest.validate()
        if errors:
            logger.warning("Skipping manifest with validation errors: %s — %s", manifest_path, errors)
            continue
        manifests.append(manifest)
    return manifests
