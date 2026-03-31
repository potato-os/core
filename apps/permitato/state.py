"""Permitato state management — init, shutdown, and runtime state."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from datetime import datetime

from apps.permitato.audit import write_audit_entry
from apps.permitato.custom_lists import CustomListStore
from apps.permitato.exceptions import ExceptionStore
from apps.permitato.modes import MODES, get_mode, WORK_DENY_DOMAINS, SFW_DENY_DOMAINS
from apps.permitato.pihole_adapter import PiholeAdapter, PiholeUnavailableError
from apps.permitato.schedule import ScheduleStore

logger = logging.getLogger(__name__)

BYPASS_THRESHOLD_SECONDS = 300  # 5 minutes


def atomic_write(path: Path, data: str) -> None:
    """Write data to path atomically via tmp + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(str(tmp), str(path))


@dataclass
class PermitState:
    mode: str = "normal"
    adapter: PiholeAdapter | None = None
    exception_store: ExceptionStore | None = None
    custom_list_store: CustomListStore | None = None
    schedule_store: ScheduleStore | None = None
    override_mode: str | None = None
    override_scheduled_mode: str | None = None
    client_id: str = ""
    group_map: dict = field(default_factory=dict)
    exception_group_id: int = 0
    pihole_available: bool = False
    degraded_since: float | None = None
    client_valid: bool | None = None
    blocking_bypassed: bool = False
    blocking_last_checked: float | None = None
    _client_cache_ts: float = 0
    _cached_clients: list = field(default_factory=list)
    data_dir: Path = field(default_factory=lambda: Path("/opt/potato/data/permitato"))

    def persist(self) -> None:
        path = self.data_dir / "state.json"
        atomic_write(path, json.dumps({
            "version": 2,
            "mode": self.mode,
            "client_id": self.client_id,
            "override_mode": self.override_mode,
            "override_scheduled_mode": self.override_scheduled_mode,
        }, indent=2))

    def load(self) -> None:
        path = self.data_dir / "state.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.mode = data.get("mode", "normal")
            self.client_id = data.get("client_id", "")
            self.override_mode = data.get("override_mode")
            self.override_scheduled_mode = data.get("override_scheduled_mode")
        except (json.JSONDecodeError, KeyError):
            logger.warning("Failed to load state from %s", path)

    def effective_mode(self, now: datetime | None = None) -> str:
        """Return the mode that should actually be applied."""
        if self.override_mode is not None:
            return self.override_mode
        if self.schedule_store:
            scheduled = self.schedule_store.evaluate(now)
            if scheduled is not None:
                return scheduled
        return "normal"


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

    state.custom_list_store = CustomListStore(data_dir=data_dir)
    state.custom_list_store.load()

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
        await _seed_custom_lists(state.adapter, state.group_map, state.custom_list_store)

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

        await validate_client(state)

    except PiholeUnavailableError:
        state.pihole_available = False
        state.degraded_since = time.time()
        logger.warning("Pi-hole unavailable at %s — running in degraded mode", pihole_base_url)

    return state


async def validate_client(state: PermitState, force_refresh: bool = False) -> bool | None:
    """Check if the saved client_id exists in Pi-hole. Uses a 30s cache."""
    if not state.client_id:
        state.client_valid = None
        return None
    if not state.pihole_available or not state.adapter:
        state.client_valid = None
        return None

    now = time.time()
    cache_expired = now - state._client_cache_ts > 30
    if cache_expired or force_refresh:
        try:
            state._cached_clients = await state.adapter.get_clients()
            state._client_cache_ts = now
        except PiholeUnavailableError:
            # Pi-hole went down — enter degraded mode so reconnect loop picks it up
            state._client_cache_ts = now
            state.pihole_available = False
            if state.degraded_since is None:
                state.degraded_since = now
            state.client_valid = None
            logger.warning("Pi-hole became unreachable during client validation")
            return None

    known_ids = {c.get("client", "") for c in state._cached_clients}
    state.client_valid = state.client_id in known_ids
    return state.client_valid


async def shutdown_permitato(state: PermitState) -> None:
    """Clean shutdown: persist state and disconnect."""
    if state.exception_store:
        state.exception_store.persist()
    if state.custom_list_store:
        state.custom_list_store.persist()
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


async def _seed_custom_lists(
    adapter: PiholeAdapter, group_map: dict[str, int], store: CustomListStore | None,
) -> None:
    """Seed user-defined custom deny-list entries into Pi-hole (idempotent)."""
    if not store:
        return
    for mode_name in ("work", "sfw"):
        gid = group_map.get(f"permitato_{mode_name}")
        if gid is None:
            continue
        for entry in store.entries_for_mode(mode_name):
            try:
                await adapter.add_domain_rule(
                    domain=entry.regex_pattern,
                    rule_type="deny",
                    kind="regex",
                    groups=[gid],
                    comment=f"Permitato-custom: {entry.domain}",
                )
            except Exception:
                pass  # duplicate or transient — skip silently


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


async def flush_dns_cache_safe(state: PermitState) -> None:
    """Flush Pi-hole DNS cache, swallowing errors."""
    if not state.pihole_available or not state.adapter:
        return
    try:
        await state.adapter.flush_dns_cache()
    except Exception:
        logger.warning("DNS cache flush failed", exc_info=True)


def check_dns_bypass(
    devices: list[dict],
    client_id: str,
    now: float | None = None,
) -> bool | None:
    """Check if the client's DNS is bypassing Pi-hole.

    client_id may be an IP address or a MAC address.
    Returns True (bypass detected), False (no bypass), or None (client not found).
    """
    if not client_id or not devices:
        return None

    if now is None:
        now = time.time()

    for device in devices:
        # Match by IP
        for ip_entry in device.get("ips", []):
            if ip_entry.get("ip") == client_id:
                return _evaluate_bypass(device, ip_entry.get("lastSeen", 0), now)

        # Match by MAC (hwaddr)
        if device.get("hwaddr", "").lower() == client_id.lower():
            # Use freshest lastSeen across all IPs
            best_seen = max(
                (ip.get("lastSeen", 0) for ip in device.get("ips", [])),
                default=0,
            )
            return _evaluate_bypass(device, best_seen, now)

    return None


def _evaluate_bypass(device: dict, last_seen: float, now: float) -> bool:
    last_query = device.get("lastQuery", 0)
    seen_fresh = (now - last_seen) < BYPASS_THRESHOLD_SECONDS
    query_stale = (now - last_query) >= BYPASS_THRESHOLD_SECONDS
    return seen_fresh and query_stale


async def update_bypass_status(state: PermitState) -> None:
    """Query Pi-hole network devices and update bypass detection fields."""
    if not state.pihole_available or not state.adapter or not state.client_id:
        return

    try:
        devices = await state.adapter.get_network_devices()
    except PiholeUnavailableError:
        logger.warning("Could not fetch network devices for bypass check")
        return
    except Exception:
        logger.warning("Unexpected error during bypass check", exc_info=True)
        return

    result = check_dns_bypass(devices, state.client_id)
    state.blocking_last_checked = time.time()

    if result is True:
        if not state.blocking_bypassed:
            logger.warning(
                "DNS bypass detected: client %s is on network but not querying Pi-hole",
                state.client_id,
            )
        state.blocking_bypassed = True
    else:
        # False (no bypass) or None (client not found) — clear the flag
        if state.blocking_bypassed:
            logger.info("DNS bypass resolved for client %s", state.client_id)
        state.blocking_bypassed = False


async def reconnect_pihole(state: PermitState) -> None:
    """Attempt to reconnect to Pi-hole if currently degraded."""
    if state.pihole_available or not state.adapter:
        return

    try:
        await state.adapter.connect()
    except PiholeUnavailableError:
        return

    # Connected — but don't mark healthy until full recovery completes
    try:
        state.group_map = await _ensure_groups(state.adapter)
        state.exception_group_id = state.group_map.get("permitato_exceptions", 0)
        await _seed_domain_lists(state.adapter, state.group_map)
        await _seed_custom_lists(state.adapter, state.group_map, state.custom_list_store)
        await compensate_exceptions(state)
        await compensate_custom_lists(state)
    except Exception:
        logger.warning("Pi-hole connected but recovery failed — staying degraded", exc_info=True)
        return

    state.pihole_available = True
    state.degraded_since = None
    logger.info("Pi-hole connection recovered")

    # Reapply mode to client now that we're healthy
    await apply_mode_to_client(state)


async def compensate_exceptions(state: PermitState) -> None:
    """Reconcile local exception store with Pi-hole allow rules."""
    if not state.adapter or not state.exception_store:
        return

    exc_gid = state.exception_group_id
    if not exc_gid:
        return

    # What Pi-hole currently has — filtered to Permitato-owned rules only
    all_rules = await state.adapter.get_domain_rules("allow", "regex")
    pihole_rules = [
        r for r in all_rules
        if exc_gid in r.get("groups", [])
        and r.get("comment", "").startswith("Permitato:")
    ]
    pihole_domains = {r["domain"] for r in pihole_rules}

    # What we expect to be there
    local_exceptions = state.exception_store.list_active()
    local_patterns = {exc["regex_pattern"] for exc in local_exceptions}

    # Re-add missing rules
    for exc in local_exceptions:
        if exc["regex_pattern"] not in pihole_domains:
            try:
                await state.adapter.add_domain_rule(
                    domain=exc["regex_pattern"],
                    rule_type="allow",
                    kind="regex",
                    groups=[exc_gid],
                    comment=f"Permitato: {exc.get('reason', '')}",
                )
                logger.info("Compensation: re-added missing allow rule for %s", exc["domain"])
            except Exception:
                logger.warning("Compensation: failed to re-add rule for %s", exc["domain"])

    # Remove orphaned Permitato rules
    for rule in pihole_rules:
        if rule["domain"] not in local_patterns:
            try:
                await state.adapter.delete_domain_rule(rule["domain"], "allow", "regex")
                logger.info("Compensation: removed orphaned allow rule %s", rule["domain"])
            except Exception:
                logger.warning("Compensation: failed to remove orphaned rule %s", rule["domain"])


async def compensate_custom_lists(state: PermitState) -> None:
    """Reconcile local custom list store with Pi-hole deny rules."""
    if not state.adapter or not state.custom_list_store:
        return

    # Fetch all deny regex rules from Pi-hole
    all_rules = await state.adapter.get_domain_rules("deny", "regex")

    # Filter to Permitato-custom-owned rules only
    pihole_custom = [
        r for r in all_rules
        if r.get("comment", "").startswith("Permitato-custom:")
    ]
    pihole_domains = {r["domain"] for r in pihole_custom}

    # What we expect
    local_entries = state.custom_list_store.list_entries()
    local_patterns = {e["regex_pattern"] for e in local_entries}

    # Re-add missing
    for entry in local_entries:
        if entry["regex_pattern"] not in pihole_domains:
            mode_name = entry["mode"]
            gid = state.group_map.get(f"permitato_{mode_name}")
            if gid is None:
                continue
            try:
                await state.adapter.add_domain_rule(
                    domain=entry["regex_pattern"],
                    rule_type="deny",
                    kind="regex",
                    groups=[gid],
                    comment=f"Permitato-custom: {entry['domain']}",
                )
                logger.info("Compensation: re-added missing custom rule for %s", entry["domain"])
            except Exception:
                logger.warning("Compensation: failed to re-add custom rule for %s", entry["domain"])

    # Remove orphaned custom rules
    for rule in pihole_custom:
        if rule["domain"] not in local_patterns:
            try:
                await state.adapter.delete_domain_rule(rule["domain"], "deny", "regex")
                logger.info("Compensation: removed orphaned custom rule %s", rule["domain"])
            except Exception:
                logger.warning("Compensation: failed to remove orphaned custom rule %s", rule["domain"])


async def apply_startup_schedule(state: PermitState, now: datetime | None = None) -> None:
    """Evaluate schedule on startup, clear stale overrides, and sync Pi-hole."""
    if not state.schedule_store:
        return

    scheduled_mode = state.schedule_store.evaluate(now)

    # Clear override if the schedule has moved past the overridden window
    if state.override_mode is not None:
        if scheduled_mode != state.override_scheduled_mode:
            logger.info(
                "Clearing stale override (was %s for %s, schedule now %s)",
                state.override_mode, state.override_scheduled_mode, scheduled_mode,
            )
            state.override_mode = None
            state.override_scheduled_mode = None
            state.persist()

    effective = state.effective_mode(now)
    if effective != state.mode:
        old_mode = state.mode
        logger.info("Startup schedule: switching %s → %s", old_mode, effective)
        state.mode = effective
        state.persist()
        write_audit_entry(state.data_dir, {
            "event": "scheduled_mode_switch",
            "from_mode": old_mode,
            "to_mode": effective,
        })
        await apply_mode_to_client(state)
        await flush_dns_cache_safe(state)
