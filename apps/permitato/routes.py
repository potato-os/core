"""Permitato API routes — mounted at /app/permitato/api/ by the platform."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from apps.permitato.audit import build_recent_context, read_audit_log, write_audit_entry
from apps.permitato.custom_lists import CustomListStore
from apps.permitato.exceptions import ExceptionStore, build_domain_regex
from apps.permitato.intent import parse_llm_response, strip_action_markers
from apps.permitato.stats import compute_stats
from apps.permitato.modes import get_mode, MODES
from apps.permitato.pihole_adapter import PiholeUnavailableError
from apps.permitato.net_resolve import resolve_requester_ipv4
from apps.permitato.lifecycle import _apply_schedule_tick
from apps.permitato.state import PermitState, apply_mode_to_client, validate_client
from apps.permitato.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)

router = APIRouter()


async def _parse_json(request: Request) -> dict | JSONResponse:
    """Parse JSON body, returning a 400 JSONResponse on malformed input."""
    try:
        return await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})


def _get_state(request: Request):
    return getattr(request.app.state, "permit_state", None)


def _schedule_now() -> datetime:
    """Return current local time. Extracted for test patching."""
    return datetime.now()


def _record_override(
    state: PermitState, new_mode: str, now: datetime | None = None,
) -> None:
    """Set or clear override state based on whether new_mode deviates from schedule."""
    scheduled_mode = None
    if state.schedule_store:
        scheduled_mode = state.schedule_store.evaluate(now)
    if scheduled_mode is not None and new_mode != scheduled_mode:
        state.override_mode = new_mode
        state.override_scheduled_mode = scheduled_mode
    else:
        state.override_mode = None
        state.override_scheduled_mode = None


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get("/status")
async def permitato_status(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})

    mode_def = get_mode(state.mode)
    await validate_client(state)

    now = _schedule_now()
    scheduled_mode = state.schedule_store.evaluate(now) if state.schedule_store else None

    return {
        "mode": state.mode,
        "mode_display": mode_def.display_name,
        "mode_description": mode_def.description,
        "active_exceptions": state.exception_store.active_count() if state.exception_store else 0,
        "exceptions": state.exception_store.list_active() if state.exception_store else [],
        "pihole_available": state.pihole_available,
        "degraded_since": state.degraded_since,
        "client_id": state.client_id,
        "client_valid": state.client_valid,
        "schedule_active": scheduled_mode is not None,
        "scheduled_mode": scheduled_mode,
        "override_active": state.override_mode is not None,
        "override_mode": state.override_mode,
        "custom_domain_count": len(state.custom_list_store.list_entries()) if state.custom_list_store else 0,
    }


# ---------------------------------------------------------------------------
# GET /stats
# ---------------------------------------------------------------------------


@router.get("/stats")
async def permitato_stats(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})
    entries = read_audit_log(state.data_dir, limit=5000)
    return compute_stats(entries, current_mode=state.mode)


# ---------------------------------------------------------------------------
# POST /mode
# ---------------------------------------------------------------------------


@router.post("/mode")
async def switch_mode(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})
    if not state.pihole_available:
        return JSONResponse(status_code=503, content={"error": "Pi-hole is unreachable"})

    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    mode_name = body.get("mode", "").lower()
    try:
        mode_def = get_mode(mode_name)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": f"Invalid mode: {mode_name}", "valid": list(MODES)})

    old_mode = state.mode
    state.mode = mode_name
    _record_override(state, mode_name)
    state.persist()
    await apply_mode_to_client(state)

    write_audit_entry(state.data_dir, {
        "event": "mode_switch",
        "from_mode": old_mode,
        "to_mode": mode_name,
        "override": state.override_mode is not None,
    })

    return {"mode": mode_name, "mode_display": mode_def.display_name}


# ---------------------------------------------------------------------------
# POST /client
# ---------------------------------------------------------------------------


@router.post("/client")
async def set_client(request: Request):
    """Set the controlled client IP/MAC for mode enforcement."""
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})

    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    client_id = body.get("client_id", "").strip()
    if not client_id:
        return JSONResponse(status_code=400, content={"error": "client_id is required"})

    state.client_id = client_id
    state.persist()
    await apply_mode_to_client(state)
    await validate_client(state, force_refresh=True)

    warning = None
    if state.client_valid is False:
        warning = "Client not found in Pi-hole's known clients"

    return {
        "client_id": client_id,
        "mode": state.mode,
        "client_valid": state.client_valid,
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# GET /clients
# ---------------------------------------------------------------------------


@router.get("/clients")
async def list_clients(request: Request):
    """Discover Pi-hole clients for onboarding."""
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})
    if not state.pihole_available or not state.adapter:
        return {"clients": [], "pihole_available": False}

    try:
        raw = await state.adapter.get_clients()
    except PiholeUnavailableError:
        return {"clients": [], "pihole_available": False}

    requester_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else None)
    client_ips = {c.get("client", "") for c in raw}
    resolved_ip = resolve_requester_ipv4(requester_ip, client_ips)

    clients = [
        {
            "client": c.get("client", ""),
            "name": c.get("name", ""),
            "id": c.get("id"),
            "selected": c.get("client", "") == state.client_id,
            "is_requester": c.get("client", "") == resolved_ip,
        }
        for c in raw
    ]
    return {"clients": clients, "pihole_available": True}


# ---------------------------------------------------------------------------
# GET /exceptions
# ---------------------------------------------------------------------------


@router.get("/exceptions")
async def list_exceptions(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})
    return {"exceptions": state.exception_store.list_active() if state.exception_store else []}


# ---------------------------------------------------------------------------
# POST /exceptions
# ---------------------------------------------------------------------------


@router.post("/exceptions")
async def grant_exception(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})
    if not state.pihole_available:
        return JSONResponse(status_code=503, content={"error": "Pi-hole is unreachable"})

    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    domain = body.get("domain", "").strip()
    reason = body.get("reason", "")
    ttl_seconds = int(body.get("ttl_seconds", 3600))

    try:
        build_domain_regex(domain)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    exc = state.exception_store.grant(domain, reason, ttl_seconds=ttl_seconds)

    # Add allow rule to Pi-hole
    try:
        await state.adapter.add_domain_rule(
            domain=exc.regex_pattern,
            rule_type="allow",
            kind="regex",
            groups=[state.exception_group_id] if state.exception_group_id else [0],
            comment=f"Permitato: {reason}",
        )
    except PiholeUnavailableError:
        state.pihole_available = False
        logger.warning("Failed to add Pi-hole allow rule for %s", domain)

    state.exception_store.persist()
    write_audit_entry(state.data_dir, {
        "event": "exception_granted",
        "domain": domain,
        "reason": reason,
        "ttl_seconds": ttl_seconds,
        "exception_id": exc.id,
    })

    return {"exception": exc.to_dict()}


# ---------------------------------------------------------------------------
# DELETE /exceptions/{exception_id}
# ---------------------------------------------------------------------------


@router.delete("/exceptions/{exception_id}")
async def revoke_exception(request: Request, exception_id: str):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})

    try:
        exc = state.exception_store.revoke(exception_id)
    except KeyError:
        return JSONResponse(status_code=404, content={"error": f"No exception with id: {exception_id}"})

    # Remove allow rule from Pi-hole
    if state.pihole_available and state.adapter:
        try:
            await state.adapter.delete_domain_rule(exc.regex_pattern, "allow", "regex")
        except (PiholeUnavailableError, Exception):
            logger.warning("Failed to remove Pi-hole allow rule for %s", exc.domain)

    state.exception_store.persist()
    write_audit_entry(state.data_dir, {
        "event": "exception_revoked",
        "domain": exc.domain,
        "exception_id": exception_id,
    })

    return {"revoked": True}


# ---------------------------------------------------------------------------
# GET /schedule
# ---------------------------------------------------------------------------


@router.get("/schedule")
async def get_schedule(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})

    store = state.schedule_store
    now = _schedule_now()
    scheduled_mode = store.evaluate(now) if store else None
    next_trans = store.next_transition(now) if store else None

    return {
        "rules": store.list_rules() if store else [],
        "scheduled_mode": scheduled_mode,
        "next_transition": next_trans,
    }


# ---------------------------------------------------------------------------
# POST /schedule
# ---------------------------------------------------------------------------


@router.post("/schedule")
async def create_schedule_rule(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})
    if not state.schedule_store:
        return JSONResponse(status_code=503, content={"error": "Schedule not available"})

    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    try:
        rule = state.schedule_store.add_rule(
            mode=body.get("mode", ""),
            days=body.get("days", []),
            start_time=body.get("start_time", ""),
            end_time=body.get("end_time", ""),
        )
    except (ValueError, TypeError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    state.schedule_store.persist()
    write_audit_entry(state.data_dir, {
        "event": "schedule_rule_created",
        "rule_id": rule.id,
        "mode": rule.mode,
        "days": rule.days,
        "start_time": rule.start_time,
        "end_time": rule.end_time,
    })

    await _apply_schedule_tick(state, _schedule_now())
    return {"rule": rule.to_dict()}


# ---------------------------------------------------------------------------
# PUT /schedule/{rule_id}
# ---------------------------------------------------------------------------


@router.put("/schedule/{rule_id}")
async def update_schedule_rule(request: Request, rule_id: str):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})
    if not state.schedule_store:
        return JSONResponse(status_code=503, content={"error": "Schedule not available"})

    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    try:
        rule = state.schedule_store.update_rule(rule_id, **body)
    except KeyError:
        return JSONResponse(status_code=404, content={"error": f"No rule with id: {rule_id}"})
    except (ValueError, TypeError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    state.schedule_store.persist()
    write_audit_entry(state.data_dir, {
        "event": "schedule_rule_updated",
        "rule_id": rule.id,
    })

    await _apply_schedule_tick(state, _schedule_now())
    return {"rule": rule.to_dict()}


# ---------------------------------------------------------------------------
# DELETE /schedule/{rule_id}
# ---------------------------------------------------------------------------


@router.delete("/schedule/{rule_id}")
async def delete_schedule_rule(request: Request, rule_id: str):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})
    if not state.schedule_store:
        return JSONResponse(status_code=503, content={"error": "Schedule not available"})

    try:
        rule = state.schedule_store.remove_rule(rule_id)
    except KeyError:
        return JSONResponse(status_code=404, content={"error": f"No rule with id: {rule_id}"})

    state.schedule_store.persist()
    write_audit_entry(state.data_dir, {
        "event": "schedule_rule_deleted",
        "rule_id": rule.id,
        "mode": rule.mode,
    })

    if state.schedule_store.list_rules():
        await _apply_schedule_tick(state, _schedule_now())
    else:
        state.override_mode = None
        state.override_scheduled_mode = None
        if state.mode != "normal":
            old_mode = state.mode
            state.mode = "normal"
            state.persist()
            await apply_mode_to_client(state)
            write_audit_entry(state.data_dir, {
                "event": "scheduled_mode_switch",
                "from_mode": old_mode,
                "to_mode": "normal",
            })
        else:
            state.persist()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# GET /custom-domains
# ---------------------------------------------------------------------------


@router.get("/custom-domains")
async def get_custom_domains(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})

    mode = request.query_params.get("mode")
    store = state.custom_list_store
    entries = store.list_entries(mode=mode) if store else []
    return {"entries": entries}


# ---------------------------------------------------------------------------
# POST /custom-domains
# ---------------------------------------------------------------------------


@router.post("/custom-domains")
async def add_custom_domain(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})
    if not state.custom_list_store:
        return JSONResponse(status_code=503, content={"error": "Custom lists not available"})

    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    mode = body.get("mode", "").strip().lower()
    domain = body.get("domain", "").strip()

    try:
        entry = state.custom_list_store.add(mode, domain)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    # Push deny rule to Pi-hole
    if state.pihole_available and state.adapter:
        gid = state.group_map.get(f"permitato_{mode}")
        if gid is not None:
            try:
                await state.adapter.add_domain_rule(
                    domain=entry.regex_pattern,
                    rule_type="deny",
                    kind="regex",
                    groups=[gid],
                    comment=f"Permitato-custom: {entry.domain}",
                )
            except PiholeUnavailableError:
                state.pihole_available = False
                logger.warning("Failed to add Pi-hole deny rule for %s", domain)

    state.custom_list_store.persist()
    write_audit_entry(state.data_dir, {
        "event": "custom_domain_added",
        "domain": entry.domain,
        "mode": mode,
        "entry_id": entry.id,
    })

    return {"entry": entry.to_dict()}


# ---------------------------------------------------------------------------
# DELETE /custom-domains/{entry_id}
# ---------------------------------------------------------------------------


@router.delete("/custom-domains/{entry_id}")
async def delete_custom_domain(request: Request, entry_id: str):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})
    if not state.custom_list_store:
        return JSONResponse(status_code=503, content={"error": "Custom lists not available"})

    store = state.custom_list_store
    if entry_id not in {e["id"] for e in store.list_entries()}:
        return JSONResponse(status_code=404, content={"error": f"No custom domain with id: {entry_id}"})

    # Try Pi-hole delete first — only remove locally if it succeeds or Pi-hole
    # is already known to be unavailable (compensation handles cleanup on reconnect).
    if state.pihole_available and state.adapter:
        entry_data = next(e for e in store.list_entries() if e["id"] == entry_id)
        try:
            await state.adapter.delete_domain_rule(entry_data["regex_pattern"], "deny", "regex")
        except PiholeUnavailableError:
            state.pihole_available = False
            state.degraded_since = state.degraded_since or __import__("time").time()
            logger.warning("Pi-hole became unreachable removing deny rule for %s", entry_data["domain"])
            return JSONResponse(status_code=503, content={"error": "Pi-hole became unreachable — retry later"})
        except Exception:
            logger.warning("Failed to remove Pi-hole deny rule for %s", entry_data["domain"])
            return JSONResponse(status_code=502, content={"error": "Failed to remove Pi-hole rule — retry later"})

    entry = store.remove(entry_id)
    store.persist()
    write_audit_entry(state.data_dir, {
        "event": "custom_domain_removed",
        "domain": entry.domain,
        "mode": entry.mode,
        "entry_id": entry_id,
    })

    return {"deleted": True}


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------


@router.post("/chat")
async def permitato_chat(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})

    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    user_message = body.get("message", "")
    history = body.get("history", [])

    # Build system prompt with current state
    mode_def = get_mode(state.mode)

    schedule_status = "No schedule configured"
    if state.schedule_store:
        scheduled = state.schedule_store.evaluate()
        if scheduled is not None and state.override_mode is not None:
            schedule_status = f"Manually overridden from scheduled {scheduled} mode"
        elif scheduled is not None:
            schedule_status = f"Scheduled ({scheduled} mode active)"

    recent_entries = read_audit_log(state.data_dir, limit=50)
    recent_context = build_recent_context(recent_entries)

    system_prompt = build_system_prompt(
        current_mode=mode_def.display_name,
        mode_description=mode_def.description,
        exception_count=state.exception_store.active_count() if state.exception_store else 0,
        active_exceptions=state.exception_store.list_active() if state.exception_store else [],
        schedule_status=schedule_status,
        recent_context=recent_context,
    )

    # Build messages array for LLM
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": "default",
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
        "max_tokens": 512,
    }

    # Proxy to the platform's LLM endpoint
    platform_url = "http://127.0.0.1:1983/v1/chat/completions"
    accumulated_text = ""

    async def _stream_and_capture():
        nonlocal accumulated_text
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", platform_url, json=payload) as resp:
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    # Normalize all non-200 bodies into {"error": {...}} envelope
                    try:
                        parsed = json.loads(error_body)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, dict) and "error" in parsed:
                        error_envelope = parsed
                    elif isinstance(parsed, dict) and "state" in parsed:
                        # 503 status payload (model not ready)
                        error_envelope = {"error": {"message": f"Model not ready (state: {parsed['state']})", "code": resp.status_code}}
                    else:
                        error_envelope = {"error": {"message": f"LLM returned {resp.status_code}", "code": resp.status_code}}
                    yield f"data: {json.dumps(error_envelope)}\n\ndata: [DONE]\n\n"
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        yield line + "\n"
                        continue

                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        # Parse intent from accumulated text before sending DONE
                        intent = parse_llm_response(accumulated_text)
                        if intent.action != "none":
                            action_result = await _execute_action(state, intent, user_message)
                            yield f"data: {json.dumps({'permitato_action': action_result})}\n\n"
                        yield line + "\n"
                        continue

                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            accumulated_text += content
                    except (json.JSONDecodeError, IndexError, KeyError):
                        pass

                    yield line + "\n"

    return StreamingResponse(
        _stream_and_capture(),
        media_type="text/event-stream",
    )


async def _execute_action(state, intent, user_message: str) -> dict:
    """Execute a parsed intent and return the action result."""
    if intent.action == "switch_mode":
        mode_name = intent.params.get("mode", "normal")
        try:
            mode_def = get_mode(mode_name)
        except ValueError:
            return {"type": "error", "message": f"Invalid mode: {mode_name}"}

        old_mode = state.mode
        state.mode = mode_name
        _record_override(state, mode_name)
        state.persist()
        await apply_mode_to_client(state)
        write_audit_entry(state.data_dir, {
            "event": "mode_switch",
            "from_mode": old_mode,
            "to_mode": mode_name,
            "override": state.override_mode is not None,
            "user_message": user_message,
        })
        return {"type": "mode_switched", "mode": mode_name, "display": mode_def.display_name}

    if intent.action == "request_unblock":
        domain = intent.params.get("domain", "")
        reason = intent.params.get("reason", "")
        if not domain:
            return {"type": "error", "message": "No domain specified"}

        try:
            build_domain_regex(domain)
        except ValueError:
            return {"type": "error", "message": f"Invalid domain: {domain}"}

        exc = state.exception_store.grant(domain, reason, ttl_seconds=3600)

        if state.pihole_available and state.adapter:
            try:
                await state.adapter.add_domain_rule(
                    domain=exc.regex_pattern,
                    rule_type="allow",
                    kind="regex",
                    groups=[state.exception_group_id] if state.exception_group_id else [0],
                    comment=f"Permitato: {reason}",
                )
            except PiholeUnavailableError:
                state.pihole_available = False

        state.exception_store.persist()
        write_audit_entry(state.data_dir, {
            "event": "exception_granted",
            "domain": domain,
            "reason": reason,
            "ttl_seconds": 3600,
            "exception_id": exc.id,
            "user_message": user_message,
        })
        return {"type": "exception_granted", "domain": domain, "expires_in_minutes": 60}

    if intent.action == "deny_unblock":
        domain = intent.params.get("domain", "")
        reason = intent.params.get("reason", "")
        write_audit_entry(state.data_dir, {
            "event": "exception_denied",
            "domain": domain,
            "reason": reason,
            "user_message": user_message,
        })
        return {"type": "exception_denied", "domain": domain}

    return {"type": "none"}
