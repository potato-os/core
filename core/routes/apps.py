"""App listing and management routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/internal/apps")
async def list_apps(request: Request):
    instances = getattr(request.app.state, "app_instances", {})
    return {
        "apps": [
            {
                "id": inst.manifest.id,
                "name": inst.manifest.name,
                "version": inst.manifest.version,
                "status": inst.status,
                "critical": inst.manifest.critical,
                "has_ui": inst.manifest.has_ui,
                "pid": inst.process.pid if inst.process and inst.process.returncode is None else None,
                "consecutive_failures": inst.consecutive_failures,
            }
            for inst in instances.values()
        ],
    }
