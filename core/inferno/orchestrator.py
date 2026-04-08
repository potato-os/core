"""Inferno orchestrator — inference process lifecycle, health, and readiness.

This module owns the runtime-facing orchestration logic that was previously
embedded in core.main:  health probing, readiness state machine, process
restart coordination, and the per-tick inference loop decision logic.

Functions accept primitives, dicts, and callbacks — never FastAPI app.state.
The Potato layer (core.main) maps between app.state and these interfaces.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, NamedTuple

import httpx

from .model_families import (
    build_model_projector_status,
    is_gemma4_filename,
    recommended_runtime_for_model,
)
from .model_registry import (
    is_qwen35_a3b_filename,
    model_supports_vision_filename,
    normalize_model_settings,
    resolve_model_runtime_path,
)
from .runtime_manager import (
    LLAMA_SERVER_RUNTIME_FAMILIES,
    check_runtime_device_compatibility,
    discover_runtime_slots,
)

logger = logging.getLogger("potato")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

READY_HEALTH_POLLS_REQUIRED: int = 2
"""Consecutive healthy probes before marking the inference server ready."""

MAX_CONSECUTIVE_FAILURES: int = 5
"""Failure ceiling — stop restarting the process after this many crashes."""

# ---------------------------------------------------------------------------
# Tick result
# ---------------------------------------------------------------------------


class InferenceTickResult(NamedTuple):
    process: Any
    consecutive_failures: int
    failure_model_key: str | None
    failure_runtime_key: str | None
    readiness: dict[str, Any]


# ---------------------------------------------------------------------------
# State factories
# ---------------------------------------------------------------------------


def empty_readiness_state() -> dict[str, Any]:
    return {
        "generation": 0,
        "model_path": None,
        "status": "idle",
        "transport_healthy": False,
        "ready": False,
        "healthy_polls": 0,
        "last_error": None,
        "last_ready_at_unix": None,
    }


def empty_runtime_switch_state() -> dict[str, Any]:
    return {
        "active": False,
        "target_bundle_path": None,
        "started_at_unix": None,
        "completed_at_unix": None,
        "error": None,
        "last_bundle_path": None,
    }


# ---------------------------------------------------------------------------
# Readiness state transitions (pure)
# ---------------------------------------------------------------------------


def reset_readiness(
    previous: dict[str, Any] | None,
    *,
    model_path: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create a fresh readiness state, incrementing the generation counter."""
    generation = 1
    if isinstance(previous, dict):
        generation = max(0, int(previous.get("generation") or 0)) + 1
    state = empty_readiness_state()
    state["generation"] = generation
    state["model_path"] = str(model_path) if model_path else None
    state["status"] = "loading" if model_path else "idle"
    state["last_error"] = reason
    return state


def resolve_readiness(
    current: dict[str, Any] | None,
    *,
    active_model_path: str | None = None,
) -> dict[str, Any]:
    """Return existing readiness state, auto-resetting on model change."""
    if not isinstance(current, dict):
        current = empty_readiness_state()
    target_path = str(active_model_path) if active_model_path is not None else None
    if target_path and current.get("model_path") != target_path:
        return reset_readiness(current, model_path=active_model_path, reason="model_changed")
    if target_path is None and current.get("model_path") is not None:
        return reset_readiness(current, reason="no_model")
    return dict(current)


# ---------------------------------------------------------------------------
# Health probing (async, httpx)
# ---------------------------------------------------------------------------


async def check_health(base_url: str, *, busy_is_healthy: bool = True) -> bool:
    """Probe the inference server's health endpoints."""
    timeout = httpx.Timeout(2.0, connect=1.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for path in ("/health", "/v1/models"):
            try:
                response = await client.get(f"{base_url}{path}")
            except httpx.ReadTimeout:
                if busy_is_healthy:
                    return True
                continue
            except httpx.HTTPError:
                continue
            if response.status_code < 500:
                return True
    return False


async def probe_inference_slot(base_url: str) -> bool:
    """Send a minimal inference request to verify the slot is functional."""
    timeout = httpx.Timeout(4.0, connect=1.0)
    payload = {
        "model": "qwen-local",
        "stream": False,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/v1/chat/completions",
                json=payload,
            )
        return response.status_code < 500
    except httpx.HTTPError:
        return False


# ---------------------------------------------------------------------------
# Readiness refresh (async)
# ---------------------------------------------------------------------------


async def refresh_readiness(
    readiness: dict[str, Any],
    *,
    base_url: str,
    process_alive: bool,
) -> dict[str, Any]:
    """Advance the readiness state machine by one health-check cycle.

    Returns a new state dict (the input is not mutated).
    """
    state = dict(readiness)
    target_path = state.get("model_path")

    if target_path is None:
        return state

    if not process_alive:
        state.update(
            {
                "status": "loading",
                "transport_healthy": False,
                "ready": False,
                "healthy_polls": 0,
            }
        )
        return state

    busy_is_healthy = bool(state.get("ready"))
    transport_healthy = await check_health(base_url, busy_is_healthy=busy_is_healthy)
    state["transport_healthy"] = transport_healthy
    if not transport_healthy:
        state.update(
            {
                "status": "loading",
                "ready": False,
                "healthy_polls": 0,
            }
        )
        return state

    state["healthy_polls"] = min(
        READY_HEALTH_POLLS_REQUIRED,
        max(0, int(state.get("healthy_polls") or 0)) + 1,
    )
    if int(state["healthy_polls"]) >= READY_HEALTH_POLLS_REQUIRED:
        if not state.get("ready"):
            state["last_ready_at_unix"] = time.time()
        state["ready"] = True
        state["status"] = "ready"
        state["last_error"] = None
    else:
        state["ready"] = False
        state["status"] = "warming"
    return state


# ---------------------------------------------------------------------------
# mmproj resolution
# ---------------------------------------------------------------------------


def resolve_mmproj_for_launch(
    models_dir: Path,
    resolved_model_dir: Path,
    active_model: dict[str, Any],
    installed_family: str,
) -> str | None:
    """Resolve the mmproj path for a vision-enabled model.

    Returns the projector path, or ``None`` if vision is not enabled.
    Raises ``RuntimeError`` if vision is enabled but no projector is available.
    """
    active_filename = str(active_model.get("filename") or "")
    active_settings = normalize_model_settings(active_model.get("settings"), filename=active_filename)
    vision_settings = active_settings.get("vision", {})

    if not (model_supports_vision_filename(active_filename) and bool(vision_settings.get("enabled", False))):
        return None

    # Suppress Gemma4 vision on ik_llama (clip_init failure).
    if is_gemma4_filename(active_filename) and installed_family == "ik_llama":
        return None

    projector_mode = str(vision_settings.get("projector_mode") or "default").strip().lower()
    projector_filename = str(vision_settings.get("projector_filename") or "").strip()

    if projector_mode == "custom" and projector_filename:
        custom_path = models_dir / projector_filename
        if custom_path.exists():
            return str(custom_path)
        raise RuntimeError(f"Custom projector not found: {custom_path}")

    projector_status = build_model_projector_status(models_dir, active_model)
    if projector_status.get("present") and projector_status.get("path"):
        return str(projector_status["path"])

    if resolved_model_dir != models_dir:
        for candidate in projector_status.get("default_candidates") or []:
            candidate_path = resolved_model_dir / candidate
            if candidate_path.exists():
                return str(candidate_path)

    raise RuntimeError(f"Vision enabled but no projector found for {active_filename}")


async def ensure_mmproj_for_launch(
    models_dir: Path,
    active_model: dict[str, Any],
    installed_family: str,
    *,
    download_fn: Callable[[str], Awaitable[tuple[bool, str, str | None]]] | None = None,
) -> str | None:
    """Resolve or download the mmproj for launch.

    *download_fn* is an async callback ``(model_id) -> (ok, reason, filename)``
    injected by the Potato layer.  Returns the path or ``None``.
    """
    resolved_dir = models_dir
    try:
        active_filename = str(active_model.get("filename") or "")
        resolved_dir = resolve_model_runtime_path(models_dir, active_filename).parent
    except Exception:
        pass

    try:
        return resolve_mmproj_for_launch(models_dir, resolved_dir, active_model, installed_family)
    except RuntimeError:
        pass

    if download_fn is None:
        logger.warning("Vision projector unavailable and no download function — skipping launch")
        return None

    active_model_id = str(active_model.get("id") or "")
    if not active_model_id:
        logger.warning("Vision enabled but model has no id — skipping projector download")
        return None
    try:
        ok, _reason, downloaded_name = await download_fn(active_model_id)
    except Exception:
        logger.warning("Projector download failed — skipping vision launch", exc_info=True)
        return None
    if ok and downloaded_name:
        return str(models_dir / downloaded_name)

    logger.warning("Vision projector unavailable — skipping launch (will retry)")
    return None


# ---------------------------------------------------------------------------
# no-mmap resolution
# ---------------------------------------------------------------------------


def resolve_no_mmap(
    memory_loading_status: dict[str, Any],
    active_filename: str,
    installed_family: str,
    *,
    device_class: str,
    bundle_marker: dict[str, Any] | None,
) -> bool:
    """Resolve the ``--no-mmap`` flag, including the 'auto' heuristic."""
    no_mmap_env = str(memory_loading_status.get("no_mmap_env") or "auto")
    if no_mmap_env.lower() in ("true", "1"):
        return True
    if no_mmap_env.lower() in ("false", "0"):
        return False

    # Auto mode — replicate the old shell heuristic.
    if not is_qwen35_a3b_filename(active_filename):
        return False
    if device_class != "pi5-16gb":
        return False
    runtime_profile = str((bundle_marker or {}).get("profile") or "")
    return runtime_profile == "pi5-opt" and installed_family == "ik_llama"


# ---------------------------------------------------------------------------
# Process restart
# ---------------------------------------------------------------------------


async def restart_inference_process(
    readiness: dict[str, Any],
    process: Any,
    *,
    model_path: str | None = None,
    terminate_fn: Callable[..., Awaitable[None]],
    stray_kill_fn: Callable[[], Awaitable[int]],
) -> tuple[dict[str, Any], bool, str]:
    """Restart the inference process.

    Returns ``(new_readiness, terminated_any, reason)``.
    """
    new_readiness = reset_readiness(readiness, model_path=model_path, reason="restart_requested")
    terminated_running = False
    terminated_stale = False

    if process is not None and process.returncode is None:
        await terminate_fn(process, timeout=3.0)
        terminated_running = True
    terminated_stale = bool(await stray_kill_fn())

    if terminated_running and terminated_stale:
        return new_readiness, True, "terminated_running_and_stale_processes"
    if terminated_running:
        return new_readiness, True, "terminated_running_process"
    if terminated_stale:
        return new_readiness, True, "terminated_stale_processes"
    return new_readiness, False, "no_running_process"


# ---------------------------------------------------------------------------
# Inference tick
# ---------------------------------------------------------------------------


async def run_inference_tick(
    process: Any,
    consecutive_failures: int,
    failure_model_key: str | None,
    failure_runtime_key: str | None,
    readiness: dict[str, Any],
    *,
    model_path: Path,
    base_url: str,
    installed_family: str,
    launch_llama_fn: Callable[[], Awaitable[Any | None]],
    launch_litert_fn: Callable[[], Awaitable[Any]] | None,
    switch_in_progress: bool = False,
) -> InferenceTickResult:
    """Run one iteration of the inference process management loop.

    The tick owns the decision logic (should we spawn? count failures?
    check readiness?) while the actual process spawning is delegated to
    the caller-provided launch callbacks.
    """
    active_model_is_present = False
    try:
        active_model_is_present = model_path.exists() and model_path.stat().st_size > 0
    except OSError:
        active_model_is_present = False

    if not active_model_is_present:
        new_readiness = reset_readiness(readiness, reason="model_missing")
        return InferenceTickResult(
            process=process,
            consecutive_failures=0,
            failure_model_key=failure_model_key,
            failure_runtime_key=failure_runtime_key,
            readiness=new_readiness,
        )

    # Reset failure counter when active model or runtime changes.
    current_model_key = str(model_path)
    current_runtime_key = installed_family
    if failure_model_key != current_model_key or failure_runtime_key != current_runtime_key:
        consecutive_failures = 0
        failure_model_key = current_model_key
        failure_runtime_key = current_runtime_key

    llama_process = process
    if llama_process is None or llama_process.returncode is not None:
        # Count the previous process's failure BEFORE starting a new one.
        if llama_process is not None and llama_process.returncode is not None and llama_process.returncode != 0:
            consecutive_failures += 1
            llama_process = None
            if consecutive_failures == MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "llama-server failed %d times in a row — stopping restart attempts (model may be corrupt)",
                    consecutive_failures,
                )

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES or switch_in_progress:
            pass  # Limit reached or switch in progress — don't restart.
        elif installed_family == "litert" and launch_litert_fn is not None:
            llama_process = await launch_litert_fn()
            if llama_process is not None:
                logger.info("Started litert adapter process")
        else:
            llama_process = await launch_llama_fn()
            if llama_process is not None:
                logger.info("Started llama-server process")

    process_alive = llama_process is not None and llama_process.returncode is None
    new_readiness = await refresh_readiness(
        resolve_readiness(readiness, active_model_path=str(model_path)),
        base_url=base_url,
        process_alive=process_alive,
    )
    if new_readiness.get("ready"):
        consecutive_failures = 0

    return InferenceTickResult(
        process=llama_process,
        consecutive_failures=consecutive_failures,
        failure_model_key=failure_model_key,
        failure_runtime_key=failure_runtime_key,
        readiness=new_readiness,
    )


# ---------------------------------------------------------------------------
# Activation runtime prep
# ---------------------------------------------------------------------------


def prepare_activation_runtime(
    model_filename: str,
    model_format: str,
    current_family: str,
    device_class: str,
    runtimes_dir: Path,
) -> tuple[bool, str, str | None]:
    """Decide if a runtime switch is needed for model activation.

    Returns ``(should_switch, reason, target_family)``.
    """
    preferred = recommended_runtime_for_model(model_filename)
    # GGUF models can't run on litert — fall back to llama_cpp.
    if not preferred and current_family == "litert" and model_format == "gguf":
        preferred = "llama_cpp"

    if not preferred:
        return False, "no_switch_needed", None

    compat = check_runtime_device_compatibility(device_class, preferred)
    if not compat.get("compatible", True):
        fmt = model_format
        if (fmt == "litertlm" and preferred == "litert") or (fmt == "gguf" and preferred in LLAMA_SERVER_RUNTIME_FAMILIES):
            return False, "incompatible_runtime", None
        # Preferred runtime is incompatible but format doesn't strictly require it.
        return False, "no_switch_needed", None

    if current_family == preferred:
        return False, "already_on_preferred", None

    # Check if we have a slot for the preferred family.
    slots = discover_runtime_slots(runtimes_dir)
    for slot in slots:
        if slot.get("family") == preferred:
            return True, "switch_required", preferred

    return False, "no_slot_available", preferred
