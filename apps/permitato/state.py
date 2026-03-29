"""Permitato state management — init, shutdown, and runtime state."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from apps.permitato.exceptions import ExceptionStore
from apps.permitato.modes import MODES, get_mode, WORK_DENY_DOMAINS, SFW_DENY_DOMAINS
from apps.permitato.pihole_adapter import PiholeAdapter, PiholeUnavailableError

logger = logging.getLogger(__name__)


@dataclass
class PermitState:
    mode: str = "normal"
    adapter: PiholeAdapter | None = None
    exception_store: ExceptionStore | None = None
    client_id: str = ""
    group_map: dict = field(default_factory=dict)
    exception_group_id: int = 0
    pihole_available: bool = False
    data_dir: Path = field(default_factory=lambda: Path("/opt/potato/data/permitato"))

    def persist(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "state.json"
        path.write_text(json.dumps({
            "version": 1,
            "mode": self.mode,
            "client_id": self.client_id,
        }, indent=2), encoding="utf-8")

    def load(self) -> None:
        path = self.data_dir / "state.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.mode = data.get("mode", "normal")
            self.client_id = data.get("client_id", "")
        except (json.JSONDecodeError, KeyError):
            logger.warning("Failed to load state from %s", path)


async def initialize_permitato(
    data_dir: Path,
    pihole_password: str,
    pihole_base_url: str = "http://127.0.0.1:8081/api",
) -> PermitState:
    """Bootstrap Permitato: connect to Pi-hole, load persisted state, cleanup."""
    state = PermitState(data_dir=data_dir)
    state.load()

    state.exception_store = ExceptionStore(data_dir=data_dir)
    state.exception_store.load()

    state.adapter = PiholeAdapter(base_url=pihole_base_url, password=pihole_password)
    try:
        await state.adapter.connect()
        state.pihole_available = True
        logger.info("Connected to Pi-hole at %s", pihole_base_url)

        # Ensure Permitato groups exist in Pi-hole
        state.group_map = await _ensure_groups(state.adapter)
        state.exception_group_id = state.group_map.get("permitato_exceptions", 0)
        logger.info("Pi-hole groups: %s", state.group_map)

        # Seed deny-list domains (idempotent — duplicates are ignored by Pi-hole)
        await _seed_domain_lists(state.adapter, state.group_map)

        # Revoke Pi-hole rules for exceptions that expired while we were down
        for exc in state.exception_store.get_expired():
            try:
                await state.adapter.delete_domain_rule(exc.regex_pattern, "allow", "regex")
            except Exception:
                pass
        revoked = state.exception_store.cleanup_expired()
        if revoked:
            logger.info("Cleaned up %d expired exceptions on startup", len(revoked))
            state.exception_store.persist()

    except PiholeUnavailableError:
        state.pihole_available = False
        logger.warning("Pi-hole unavailable at %s — running in degraded mode", pihole_base_url)

    return state


async def shutdown_permitato(state: PermitState) -> None:
    """Clean shutdown: persist state and disconnect."""
    if state.exception_store:
        state.exception_store.persist()
    state.persist()
    if state.adapter:
        await state.adapter.disconnect()
    logger.info("Permitato shutdown complete")


async def _ensure_groups(adapter: PiholeAdapter) -> dict[str, int]:
    """Create Permitato groups if they don't exist. Return name→id mapping."""
    existing = await adapter.get_groups()
    name_to_id = {g["name"]: g["id"] for g in existing}

    needed = ["permitato_work", "permitato_sfw", "permitato_exceptions"]
    for name in needed:
        if name not in name_to_id:
            try:
                result = await adapter.create_group(name)
                groups = result.get("groups", [])
                for g in groups:
                    if g["name"] == name:
                        name_to_id[name] = g["id"]
                        break
                logger.info("Created Pi-hole group: %s", name)
            except Exception:
                logger.warning("Failed to create Pi-hole group: %s", name, exc_info=True)

    return name_to_id


async def _seed_domain_lists(adapter: PiholeAdapter, group_map: dict[str, int]) -> None:
    """Seed deny-list regex domains into their Pi-hole groups."""
    work_gid = group_map.get("permitato_work")
    sfw_gid = group_map.get("permitato_sfw")

    if work_gid is not None:
        for domain in WORK_DENY_DOMAINS:
            try:
                await adapter.add_domain_rule(
                    domain=domain, rule_type="deny", kind="regex",
                    groups=[work_gid], comment="Permitato: work mode",
                )
            except Exception:
                pass  # duplicate or transient — skip silently

    if sfw_gid is not None:
        for domain in SFW_DENY_DOMAINS:
            try:
                await adapter.add_domain_rule(
                    domain=domain, rule_type="deny", kind="regex",
                    groups=[sfw_gid], comment="Permitato: sfw mode",
                )
            except Exception:
                pass


async def apply_mode_to_client(state: PermitState) -> None:
    """Apply the current mode to the controlled client in Pi-hole."""
    if not state.pihole_available or not state.adapter or not state.client_id:
        return

    mode_def = get_mode(state.mode)
    # Build group list: always include Default (0) + exceptions group
    groups = [0]
    if state.exception_group_id:
        groups.append(state.exception_group_id)
    # Add the mode-specific deny group
    if mode_def.group_name and mode_def.group_name in state.group_map:
        groups.append(state.group_map[mode_def.group_name])

    try:
        # Try update first, create if client doesn't exist yet
        await state.adapter.update_client(state.client_id, groups)
    except Exception:
        try:
            await state.adapter.add_client(state.client_id, groups)
        except Exception:
            logger.warning("Failed to set client groups for %s", state.client_id, exc_info=True)
