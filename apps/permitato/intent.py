"""LLM response intent parsing — extract action markers from model output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ACTION_RE = re.compile(r"\[ACTION:(\w+)(?::([^\]]*))?\]")

_VALID_MODES = {"normal", "work", "sfw"}

# Fallback patterns for small models that may not produce clean markers
_FALLBACK_MODE_RE = re.compile(
    r"(?:switch(?:ing)?|chang(?:e|ing)|mov(?:e|ing)|set(?:ting)?)\s+"
    r"(?:you\s+)?(?:to\s+|into\s+)?"
    r"(normal|work|sfw)\s+mode",
    re.IGNORECASE,
)
_FALLBACK_UNBLOCK_RE = re.compile(
    r"(?:I'?ll|I\s+will|going\s+to)\s+unblock\s+([\w.-]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedIntent:
    action: str = "none"
    params: dict = field(default_factory=dict)


def extract_action_markers(text: str) -> ParsedIntent | None:
    """Extract the first [ACTION:...] marker from text."""
    match = _ACTION_RE.search(text)
    if not match:
        return None

    action = match.group(1)
    raw_params = match.group(2) or ""

    if action == "switch_mode":
        mode = raw_params.strip().lower()
        if mode in _VALID_MODES:
            return ParsedIntent(action="switch_mode", params={"mode": mode})

    if action == "request_unblock":
        parts = raw_params.split(":", 1)
        domain = parts[0].strip()
        reason = parts[1].strip() if len(parts) > 1 else ""
        return ParsedIntent(action="request_unblock", params={"domain": domain, "reason": reason})

    if action == "deny_unblock":
        parts = raw_params.split(":", 1)
        domain = parts[0].strip()
        reason = parts[1].strip() if len(parts) > 1 else ""
        return ParsedIntent(action="deny_unblock", params={"domain": domain, "reason": reason})

    return ParsedIntent(action=action, params={"raw": raw_params})


def extract_intent_fallback(text: str) -> ParsedIntent | None:
    """Keyword-based fallback for models that don't produce clean markers."""
    mode_match = _FALLBACK_MODE_RE.search(text)
    if mode_match:
        mode = mode_match.group(1).lower()
        if mode in _VALID_MODES:
            return ParsedIntent(action="switch_mode", params={"mode": mode})

    unblock_match = _FALLBACK_UNBLOCK_RE.search(text)
    if unblock_match:
        domain = unblock_match.group(1).lower()
        return ParsedIntent(action="request_unblock", params={"domain": domain, "reason": ""})

    return None


def parse_llm_response(text: str) -> ParsedIntent:
    """Parse LLM response — try markers first, then keyword fallback."""
    result = extract_action_markers(text)
    if result is not None:
        return result
    result = extract_intent_fallback(text)
    if result is not None:
        return result
    return ParsedIntent()


def strip_action_markers(text: str) -> str:
    """Remove [ACTION:...] markers from text before displaying to user."""
    return _ACTION_RE.sub("", text)
