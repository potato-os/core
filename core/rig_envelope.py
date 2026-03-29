"""RIG step envelope validation — contract checks for the MS/TS envelope format."""

from __future__ import annotations

_REQUIRED_FIELDS = ("step_id", "type", "result", "next")
_VALID_TYPES = {"ms", "ts"}
_VALID_NEXT_MODES = {"direct", "model"}


def validate_envelope(data: dict) -> list[str]:
    """Validate a RIG step envelope dict. Returns error strings (empty = valid)."""
    errors: list[str] = []

    for field in _REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"missing required field: {field}")

    if errors:
        return errors

    step_type = data["type"]
    if not isinstance(step_type, str) or step_type not in _VALID_TYPES:
        errors.append(f"type must be one of {_VALID_TYPES}, got: {step_type!r}")

    if not isinstance(data["result"], dict):
        errors.append("result must be a dict")

    nxt = data["next"]
    if nxt is not None:
        if not isinstance(nxt, dict):
            errors.append("next must be a dict or null")
        else:
            mode = nxt.get("mode")
            if not isinstance(mode, str) or mode not in _VALID_NEXT_MODES:
                errors.append(f"next.mode must be one of {_VALID_NEXT_MODES}, got: {mode!r}")
            elif mode == "direct" and "step_id" not in nxt:
                errors.append("next.step_id is required when mode is 'direct'")
            elif mode == "model" and "prompt_id" not in nxt:
                errors.append("next.prompt_id is required when mode is 'model'")

    return errors
