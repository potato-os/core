"""Permitato API routes — mounted at /app/permitato/api/ by the platform."""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from apps.permitato.audit import write_audit_entry
from apps.permitato.exceptions import ExceptionStore, build_domain_regex
from apps.permitato.intent import parse_llm_response, strip_action_markers
from apps.permitato.modes import get_mode, MODES
from apps.permitato.pihole_adapter import PiholeUnavailableError
from apps.permitato.state import apply_mode_to_client
from apps.permitato.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_state(request: Request):
    return getattr(request.app.state, "permit_state", None)


# ---------------------------------------------------------------------------
# GET /status
# ---------------------------------------------------------------------------


@router.get("/status")
async def permitato_status(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})

    mode_def = get_mode(state.mode)
    return {
        "mode": state.mode,
        "mode_display": mode_def.display_name,
        "mode_description": mode_def.description,
        "active_exceptions": state.exception_store.active_count() if state.exception_store else 0,
        "exceptions": state.exception_store.list_active() if state.exception_store else [],
        "pihole_available": state.pihole_available,
        "degraded_since": state.degraded_since,
        "client_id": state.client_id,
    }


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

    body = await request.json()
    mode_name = body.get("mode", "").lower()
    try:
        mode_def = get_mode(mode_name)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": f"Invalid mode: {mode_name}", "valid": list(MODES)})

    old_mode = state.mode
    state.mode = mode_name
    state.persist()
    await apply_mode_to_client(state)

    write_audit_entry(state.data_dir, {
        "event": "mode_switch",
        "from_mode": old_mode,
        "to_mode": mode_name,
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

    body = await request.json()
    client_id = body.get("client_id", "").strip()
    if not client_id:
        return JSONResponse(status_code=400, content={"error": "client_id is required"})

    state.client_id = client_id
    state.persist()
    await apply_mode_to_client(state)

    return {"client_id": client_id, "mode": state.mode}


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

    body = await request.json()
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
# POST /chat
# ---------------------------------------------------------------------------


@router.post("/chat")
async def permitato_chat(request: Request):
    state = _get_state(request)
    if state is None:
        return JSONResponse(status_code=503, content={"error": "Permitato not initialized"})

    body = await request.json()
    user_message = body.get("message", "")
    history = body.get("history", [])

    # Build system prompt with current state
    mode_def = get_mode(state.mode)
    system_prompt = build_system_prompt(
        current_mode=mode_def.display_name,
        mode_description=mode_def.description,
        exception_count=state.exception_store.active_count() if state.exception_store else 0,
        active_exceptions=state.exception_store.list_active() if state.exception_store else [],
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
        state.persist()
        await apply_mode_to_client(state)
        write_audit_entry(state.data_dir, {
            "event": "mode_switch",
            "from_mode": old_mode,
            "to_mode": mode_name,
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
