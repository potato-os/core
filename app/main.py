from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

try:
    from app.repositories import (
        BackendProxyError,
        ChatRepositoryManager,
        FakeLlamaRepository,
        LlamaCppRepository,
    )
except ModuleNotFoundError:
    from repositories import (  # type: ignore[no-redef]
        BackendProxyError,
        ChatRepositoryManager,
        FakeLlamaRepository,
        LlamaCppRepository,
    )

try:
    from app.constants import (
        is_qwen35_filename,
        is_qwen3_vl_filename,
        projector_repo_for_model,
    )
    from app.model_state import (
        DEFAULT_MODEL_CHAT_SETTINGS,
        MODEL_FILENAME,
        MODELS_STATE_VERSION,
        MODEL_URL,
        ModelSettingsValidationError,
        _default_model_record,
        apply_model_chat_defaults,
        build_model_capabilities,
        delete_model,
        describe_model_storage,
        ensure_models_state,
        any_model_ready,
        get_model_by_id,
        is_qwen35_a3b_filename,
        model_file_present,
        model_present,
        model_supports_vision_filename,
        move_model_to_ssd,
        normalize_model_settings,
        register_model_url,
        resolve_active_model,
        resolve_model_runtime_path,
        save_models_state,
        set_download_countdown_enabled,
        update_model_settings,
        validate_model_url,
        _model_file_path,
        _sanitize_filename,
        _slugify_id,
        _unique_filename,
        _unique_model_id,
    )
    from app.runtime_state import (
        LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT,
        LLAMA_RUNTIME_BUNDLE_MARKER_FILENAME,
        MODEL_UPLOAD_LIMIT_16GB_BYTES,
        MODEL_UPLOAD_LIMIT_8GB_BYTES,
        MODEL_UPLOAD_PI_16GB_MEMORY_THRESHOLD_BYTES,
        POWER_CALIBRATION_DEFAULT_A,
        POWER_CALIBRATION_DEFAULT_B,
        RuntimeConfig,
        build_model_storage_target_status,
        build_large_model_compatibility,
        build_llama_large_model_override_status,
        build_llama_memory_loading_status,
        build_llama_runtime_status,
        build_power_calibration_status,
        build_power_estimate_status,
        check_llama_health,
        classify_runtime_device,
        collect_system_metrics_snapshot,
        compute_required_download_bytes,
        decode_throttled_bits,
        default_system_metrics_snapshot,
        discover_llama_runtime_bundles,
        fetch_remote_content_length_bytes,
        find_runtime_slot_by_family,
        get_free_storage_bytes,
        get_large_model_warn_threshold_bytes,
        get_model_upload_max_bytes,
        get_monotonic_time,
        get_preferred_model_offload_dir,
        install_llama_runtime_bundle,
        is_likely_too_large_for_storage,
        llama_memory_loading_no_mmap_env,
        normalize_allow_unsupported_large_models,
        normalize_llama_memory_loading_mode,
        normalize_power_calibration_settings,
        prime_system_metrics_counters,
        probe_llama_inference_slot,
        read_download_progress,
        read_llama_runtime_settings,
        request_llama_slot_cancel,
        system_metrics_loop,
        write_llama_runtime_bundle_marker,
        write_llama_runtime_settings,
        _append_power_calibration_sample,
        _apply_power_calibration,
        _atomic_write_json,
        _build_power_estimate_snapshot,
        _default_llama_runtime_bundle_roots,
        _detect_total_memory_bytes,
        _fit_and_persist_power_calibration,
        _fit_linear_power_calibration,
        _parse_vcgencmd_bootloader_version,
        _parse_vcgencmd_firmware_version,
        _parse_vcgencmd_pmic_read_adc,
        _read_kernel_version_info,
        _read_os_release_pretty_name,
        _read_pi_device_model_name,
        _safe_float,
        _safe_int,
        _safe_positive_float,
        _read_swap_label,
        _read_sysfs_temp,
        _reset_power_calibration,
        _run_vcgencmd,
    )
except ModuleNotFoundError:
    from constants import (  # type: ignore[no-redef]
        is_qwen35_filename,
        is_qwen3_vl_filename,
        projector_repo_for_model,
    )
    from model_state import (  # type: ignore[no-redef]
        DEFAULT_MODEL_CHAT_SETTINGS,
        MODEL_FILENAME,
        MODELS_STATE_VERSION,
        MODEL_URL,
        ModelSettingsValidationError,
        _default_model_record,
        apply_model_chat_defaults,
        build_model_capabilities,
        delete_model,
        describe_model_storage,
        ensure_models_state,
        any_model_ready,
        get_model_by_id,
        is_qwen35_a3b_filename,
        model_file_present,
        model_present,
        model_supports_vision_filename,
        move_model_to_ssd,
        normalize_model_settings,
        register_model_url,
        resolve_active_model,
        resolve_model_runtime_path,
        save_models_state,
        set_download_countdown_enabled,
        update_model_settings,
        validate_model_url,
        _model_file_path,
        _sanitize_filename,
        _slugify_id,
        _unique_filename,
        _unique_model_id,
    )
    from runtime_state import (  # type: ignore[no-redef]
        LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT,
        LLAMA_RUNTIME_BUNDLE_MARKER_FILENAME,
        MODEL_UPLOAD_LIMIT_16GB_BYTES,
        MODEL_UPLOAD_LIMIT_8GB_BYTES,
        MODEL_UPLOAD_PI_16GB_MEMORY_THRESHOLD_BYTES,
        POWER_CALIBRATION_DEFAULT_A,
        POWER_CALIBRATION_DEFAULT_B,
        RuntimeConfig,
        build_model_storage_target_status,
        build_large_model_compatibility,
        build_llama_large_model_override_status,
        build_llama_memory_loading_status,
        build_llama_runtime_status,
        build_power_calibration_status,
        build_power_estimate_status,
        check_llama_health,
        classify_runtime_device,
        collect_system_metrics_snapshot,
        compute_required_download_bytes,
        decode_throttled_bits,
        default_system_metrics_snapshot,
        discover_llama_runtime_bundles,
        fetch_remote_content_length_bytes,
        find_runtime_slot_by_family,
        get_free_storage_bytes,
        get_large_model_warn_threshold_bytes,
        get_model_upload_max_bytes,
        get_monotonic_time,
        get_preferred_model_offload_dir,
        install_llama_runtime_bundle,
        is_likely_too_large_for_storage,
        llama_memory_loading_no_mmap_env,
        normalize_allow_unsupported_large_models,
        normalize_llama_memory_loading_mode,
        normalize_power_calibration_settings,
        prime_system_metrics_counters,
        probe_llama_inference_slot,
        read_download_progress,
        read_llama_runtime_settings,
        request_llama_slot_cancel,
        system_metrics_loop,
        write_llama_runtime_bundle_marker,
        write_llama_runtime_settings,
        _append_power_calibration_sample,
        _apply_power_calibration,
        _atomic_write_json,
        _build_power_estimate_snapshot,
        _default_llama_runtime_bundle_roots,
        _detect_total_memory_bytes,
        _fit_and_persist_power_calibration,
        _fit_linear_power_calibration,
        _parse_vcgencmd_bootloader_version,
        _parse_vcgencmd_firmware_version,
        _parse_vcgencmd_pmic_read_adc,
        _read_kernel_version_info,
        _read_os_release_pretty_name,
        _read_pi_device_model_name,
        _safe_float,
        _safe_int,
        _safe_positive_float,
        _read_swap_label,
        _read_sysfs_temp,
        _reset_power_calibration,
        _run_vcgencmd,
    )

logger = logging.getLogger("potato")
logging.basicConfig(level=logging.INFO)

MAX_MODEL_UPLOAD_BYTES = MODEL_UPLOAD_LIMIT_16GB_BYTES
MODEL_UPLOAD_PURGE_WAIT_TIMEOUT_SECONDS = 20.0
MODEL_DOWNLOAD_CANCEL_WAIT_TIMEOUT_SECONDS = 20.0
# One-off auto-download: on first start with no model, downloads the default
# starter model (Qwen3.5-2B-Q4_K_M) after a 5-minute idle countdown.
AUTO_DOWNLOAD_BOOTSTRAP_ENABLED = True
LLAMA_READY_HEALTH_POLLS_REQUIRED = 2
LLAMA_SHUTDOWN_TIMEOUT_SECONDS = 5.0

DEFAULT_CHAT_SETTINGS = {
    **DEFAULT_MODEL_CHAT_SETTINGS,
}

WEB_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _empty_model_upload_state() -> dict[str, Any]:
    return {
        "active": False,
        "model_id": None,
        "bytes_total": 0,
        "bytes_received": 0,
        "percent": 0,
        "error": None,
    }


def _empty_llama_runtime_switch_state() -> dict[str, Any]:
    return {
        "active": False,
        "target_bundle_path": None,
        "started_at_unix": None,
        "completed_at_unix": None,
        "error": None,
        "last_bundle_path": None,
    }


def _empty_llama_readiness_state() -> dict[str, Any]:
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


def reset_llama_readiness_state(
    app: FastAPI,
    *,
    model_path: Path | str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    previous = getattr(app.state, "llama_readiness_state", None)
    generation = 1
    if isinstance(previous, dict):
        generation = max(0, int(previous.get("generation") or 0)) + 1
    next_state = _empty_llama_readiness_state()
    next_state["generation"] = generation
    next_state["model_path"] = str(model_path) if model_path else None
    next_state["status"] = "loading" if model_path else "idle"
    next_state["last_error"] = reason
    app.state.llama_readiness_state = next_state
    return dict(next_state)


def get_llama_readiness_state(app: FastAPI, *, active_model_path: Path | None = None) -> dict[str, Any]:
    state = getattr(app.state, "llama_readiness_state", None)
    if not isinstance(state, dict):
        state = _empty_llama_readiness_state()
        app.state.llama_readiness_state = state
    target_path = str(active_model_path) if active_model_path is not None else None
    if target_path and state.get("model_path") != target_path:
        return reset_llama_readiness_state(app, model_path=active_model_path, reason="model_changed")
    if target_path is None and state.get("model_path") is not None:
        return reset_llama_readiness_state(app, reason="no_model")
    return dict(state)


async def refresh_llama_readiness(
    app: FastAPI,
    runtime: RuntimeConfig,
    *,
    active_model_path: Path | None,
) -> dict[str, Any]:
    state = get_llama_readiness_state(app, active_model_path=active_model_path)
    target_path = str(active_model_path) if active_model_path is not None else None
    next_state = app.state.llama_readiness_state

    if target_path is None:
        return dict(next_state)

    proc = getattr(app.state, "llama_process", None)
    running = proc is not None and proc.returncode is None
    if not running:
        next_state.update(
            {
                "status": "loading",
                "transport_healthy": False,
                "ready": False,
                "healthy_polls": 0,
            }
        )
        return dict(next_state)

    busy_is_healthy = bool(next_state.get("ready"))
    transport_healthy = await check_llama_health(runtime, busy_is_healthy=busy_is_healthy)
    next_state["transport_healthy"] = transport_healthy
    if not transport_healthy:
        next_state.update(
            {
                "status": "loading",
                "ready": False,
                "healthy_polls": 0,
            }
        )
        return dict(next_state)

    next_state["healthy_polls"] = min(
        LLAMA_READY_HEALTH_POLLS_REQUIRED,
        max(0, int(next_state.get("healthy_polls") or 0)) + 1,
    )
    if int(next_state["healthy_polls"]) >= LLAMA_READY_HEALTH_POLLS_REQUIRED:
        if not next_state.get("ready"):
            next_state["last_ready_at_unix"] = time.time()
        next_state["ready"] = True
        next_state["status"] = "ready"
        next_state["last_error"] = None
    else:
        next_state["ready"] = False
        next_state["status"] = "warming"
    return dict(next_state)


def get_runtime(request: Request) -> RuntimeConfig:
    return request.app.state.runtime


def get_chat_repository(request: Request) -> ChatRepositoryManager:
    return request.app.state.chat_repository


async def _terminate_process(proc, *, timeout=None):
    if timeout is None:
        timeout = LLAMA_SHUTDOWN_TIMEOUT_SECONDS
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("pid=%s did not exit after SIGTERM, sending SIGKILL", getattr(proc, "pid", "?"))
        proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=3.0)


async def restart_managed_llama_process(app: FastAPI) -> tuple[bool, str]:
    try:
        current_model_path = getattr(app.state.runtime, "model_path", None)
    except Exception:
        current_model_path = None
    reset_llama_readiness_state(app, model_path=current_model_path, reason="restart_requested")
    proc = app.state.llama_process
    terminated_running = False

    if proc is not None and proc.returncode is None:
        await _terminate_process(proc, timeout=3.0)
        terminated_running = True

    terminated_stale = await terminate_stray_llama_processes(app.state.runtime)

    app.state.llama_process = None
    if terminated_running and terminated_stale:
        return True, "terminated_running_and_stale_processes"
    if terminated_running:
        return True, "terminated_running_process"
    if terminated_stale:
        return True, "terminated_stale_processes"
    return False, "no_running_process"


async def _list_llama_server_pids(runtime: RuntimeConfig) -> list[int]:
    llama_server_bin = str(runtime.base_dir / "llama" / "bin" / "llama-server")
    try:
        proc = await asyncio.create_subprocess_exec(
            "pgrep",
            "-f",
            llama_server_bin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return []
    except OSError:
        logger.warning("Could not inspect running llama-server processes", exc_info=True)
        return []

    stdout, _stderr = await proc.communicate()
    if proc.returncode not in {0, 1}:
        return []

    pids: list[int] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        value = line.strip()
        if not value:
            continue
        try:
            pids.append(int(value))
        except ValueError:
            continue
    return pids


async def terminate_stray_llama_processes(runtime: RuntimeConfig, *, exclude_pids: set[int] | None = None) -> int:
    excluded = {int(pid) for pid in (exclude_pids or set())}
    terminated = 0

    async def _kill_matching(sig: signal.Signals) -> int:
        count = 0
        for pid in await _list_llama_server_pids(runtime):
            if pid in excluded:
                continue
            try:
                os.kill(pid, sig)
                count += 1
            except ProcessLookupError:
                continue
            except PermissionError:
                logger.warning("Permission denied terminating stray llama-server pid=%s", pid)
            except OSError:
                logger.warning("Could not terminate stray llama-server pid=%s", pid, exc_info=True)
        return count

    terminated += await _kill_matching(signal.SIGTERM)
    if terminated:
        await asyncio.sleep(0.2)

    remaining = [pid for pid in await _list_llama_server_pids(runtime) if pid not in excluded]
    if remaining:
        terminated += await _kill_matching(signal.SIGKILL)

    return terminated


def _resolve_backend_active(
    runtime: RuntimeConfig,
    model_present: bool,
    llama_healthy: bool,
) -> tuple[str, bool]:
    mode = runtime.chat_backend_mode
    if mode not in {"auto", "llama", "fake"}:
        mode = "llama"

    # Fake backend is test/dev only and must be explicitly enabled.
    if mode == "fake" and not runtime.allow_fake_fallback:
        mode = "llama"

    if mode == "fake":
        return "fake", False
    if mode == "llama":
        return "llama", False
    if runtime.allow_fake_fallback and model_present and not llama_healthy:
        return "fake", True
    return "llama", False


def compute_auto_download_remaining_seconds(
    runtime: RuntimeConfig,
    *,
    model_present: bool,
    download_active: bool,
    startup_monotonic: float | None,
    now_monotonic: float,
    countdown_enabled: bool = True,
    default_model_downloaded_once: bool = False,
) -> int:
    if not AUTO_DOWNLOAD_BOOTSTRAP_ENABLED:
        return 0
    if not runtime.enable_orchestrator:
        return 0
    if not countdown_enabled:
        return 0
    if default_model_downloaded_once:
        return 0

    delay_seconds = max(0, int(runtime.auto_download_idle_seconds))
    if delay_seconds <= 0:
        return 0
    if model_present or download_active:
        return 0
    if startup_monotonic is None:
        return delay_seconds

    elapsed_seconds = max(0.0, float(now_monotonic) - float(startup_monotonic))
    remaining = delay_seconds - int(elapsed_seconds)
    return max(0, remaining)


def should_auto_start_download(
    runtime: RuntimeConfig,
    *,
    model_present: bool,
    download_active: bool,
    startup_monotonic: float | None,
    now_monotonic: float,
    countdown_enabled: bool = True,
    default_model_downloaded_once: bool = False,
) -> bool:
    if not AUTO_DOWNLOAD_BOOTSTRAP_ENABLED:
        return False
    if model_present or download_active:
        return False
    if not runtime.enable_orchestrator:
        return False
    if not countdown_enabled:
        return False
    if default_model_downloaded_once:
        return False
    return compute_auto_download_remaining_seconds(
        runtime,
        model_present=model_present,
        download_active=download_active,
        startup_monotonic=startup_monotonic,
        now_monotonic=now_monotonic,
        countdown_enabled=countdown_enabled,
        default_model_downloaded_once=default_model_downloaded_once,
    ) == 0


def is_download_task_active(task: asyncio.Task[Any] | None) -> bool:
    return task is not None and not task.done()


def default_projector_candidates_for_model(filename: str | None) -> list[str]:
    model_name = str(filename or "").strip().lower()
    if not model_name:
        return []
    if "qwen3" in model_name and "vl" in model_name:
        if "2b" in model_name:
            return [
                "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf",
                "mmproj-Qwen3VL-2B-Instruct-F16.gguf",
            ]
        if "4b" in model_name:
            return [
                "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf",
                "mmproj-Qwen3-VL-4B-Instruct-Q8_0.gguf",
                "mmproj-Qwen3VL-4B-Instruct-F16.gguf",
                "mmproj-Qwen3-VL-4B-Instruct-F16.gguf",
            ]
    if "qwen" in model_name and "3.5" in model_name:
        stem = Path(str(filename or "")).stem
        stem_candidates = [stem]
        trimmed_stem = stem
        while True:
            next_stem = re.sub(
                r"-(?:\d+(?:\.\d+)?bpw|I?Q\d+(?:_[A-Za-z0-9]+)*)$",
                "",
                trimmed_stem,
                flags=re.IGNORECASE,
            )
            if next_stem == trimmed_stem or not next_stem:
                break
            trimmed_stem = next_stem
            if trimmed_stem not in stem_candidates:
                stem_candidates.append(trimmed_stem)

        candidates: list[str] = []
        for candidate_stem in stem_candidates:
            candidate_name = f"mmproj-{candidate_stem}-f16.gguf"
            if candidate_name not in candidates:
                candidates.append(candidate_name)
        for fallback in ("mmproj-F16.gguf", "mmproj-BF16.gguf", "mmproj-F32.gguf"):
            if fallback not in candidates:
                candidates.append(fallback)
        return candidates
    return []


def build_model_projector_status(runtime: RuntimeConfig, model: dict[str, Any]) -> dict[str, Any]:
    filename = str(model.get("filename") or "")
    settings = normalize_model_settings(model.get("settings"), filename=filename)
    vision = settings.get("vision", {})
    projector_mode = str(vision.get("projector_mode") or "default").strip().lower()
    configured_filename = str(vision.get("projector_filename") or "").strip() or None
    default_candidates = default_projector_candidates_for_model(filename)
    search_names: list[str] = []
    if projector_mode == "custom":
        if configured_filename:
            search_names.append(configured_filename)
    else:
        for candidate in default_candidates:
            if candidate not in search_names:
                search_names.append(candidate)
        if configured_filename and configured_filename not in search_names:
            search_names.append(configured_filename)

    resolved_name = configured_filename
    present = False
    resolved_path = None
    for candidate in search_names:
        candidate_path = runtime.base_dir / "models" / candidate
        if candidate_path.exists():
            present = True
            resolved_name = candidate
            resolved_path = candidate_path
            break

    return {
        "configured_filename": configured_filename,
        "filename": resolved_name,
        "present": present,
        "path": str(resolved_path) if resolved_path is not None else None,
        "default_candidates": default_candidates,
    }


async def build_status(
    runtime: RuntimeConfig,
    *,
    app: FastAPI | None = None,
    download_active: bool = False,
    auto_start_remaining_seconds: int = 0,
    system_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    models_state = ensure_models_state(runtime)
    active_model, active_model_path = resolve_active_model(models_state, runtime)
    has_model = model_file_present(runtime, str(active_model["filename"]))
    download = read_download_progress(runtime)
    llama_running = False
    llama_transport_healthy = False
    llama_ready = False
    storage_targets = build_model_storage_target_status(runtime)
    ssd_models_dir_raw = storage_targets.get("ssd", {}).get("models_dir")
    ssd_models_dir = Path(str(ssd_models_dir_raw)) if ssd_models_dir_raw else None

    if has_model:
        readiness_state: dict[str, Any] | None = None
        if app is not None and runtime.enable_orchestrator:
            readiness_state = get_llama_readiness_state(app, active_model_path=active_model_path)
            proc = getattr(app.state, "llama_process", None)
            llama_running = proc is not None and proc.returncode is None
        if readiness_state is not None:
            llama_transport_healthy = bool(readiness_state.get("transport_healthy", False))
            llama_ready = bool(readiness_state.get("ready", False))
        else:
            llama_ready = await check_llama_health(runtime)
            llama_transport_healthy = llama_ready
            llama_running = llama_ready

    active_backend, fallback_active = _resolve_backend_active(runtime, has_model, llama_ready)
    effective_mode = runtime.chat_backend_mode
    if effective_mode not in {"auto", "llama", "fake"}:
        effective_mode = "llama"
    if effective_mode == "fake" and not runtime.allow_fake_fallback:
        effective_mode = "llama"

    if active_backend == "fake":
        state = "READY"
    elif has_model and llama_ready:
        state = "READY"
    elif download.get("error"):
        state = "ERROR"
    elif download_active or (
        not has_model and (
            download["bytes_downloaded"] > 0 or download["percent"] > 0 or download["bytes_total"] > 0
        )
    ):
        state = "DOWNLOADING"
    else:
        state = "BOOTING"

    current_download_model_id = models_state.get("current_download_model_id")
    if download_active and (not isinstance(current_download_model_id, str) or not current_download_model_id):
        current_download_model_id = str(models_state.get("default_model_id") or "default")
    models_payload: list[dict[str, Any]] = []
    for item in models_state.get("models", []):
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "")
        model_id = str(item.get("id") or "")
        present = model_file_present(runtime, filename)
        status_label = str(item.get("status") or "not_downloaded")
        model_progress = {
            "bytes_total": 0,
            "bytes_downloaded": 0,
            "percent": 0,
        }
        if download_active and model_id == current_download_model_id:
            status_label = "downloading"
            model_progress = {
                "bytes_total": download["bytes_total"],
                "bytes_downloaded": download["bytes_downloaded"],
                "percent": download["percent"],
            }
        elif present:
            status_label = "ready"
        elif status_label not in {"failed", "not_downloaded"}:
            status_label = "not_downloaded"

        models_payload.append(
            {
                "id": model_id,
                "filename": filename,
                "source_url": item.get("source_url"),
                "source_type": item.get("source_type") or "url",
                "status": status_label,
                "error": item.get("error"),
                "is_active": model_id == models_state.get("active_model_id"),
                "settings": normalize_model_settings(item.get("settings"), filename=filename),
                "capabilities": build_model_capabilities(filename),
                "projector": build_model_projector_status(runtime, item),
                "storage": describe_model_storage(runtime, filename, ssd_dir=ssd_models_dir),
                **model_progress,
            }
        )

    download_payload = dict(download)
    download_payload["active"] = bool(download_active)
    download_payload["auto_start_seconds"] = int(max(0, runtime.auto_download_idle_seconds))
    download_payload["auto_start_remaining_seconds"] = int(max(0, auto_start_remaining_seconds))
    download_payload["countdown_enabled"] = (
        bool(models_state.get("countdown_enabled", True))
        and AUTO_DOWNLOAD_BOOTSTRAP_ENABLED
        and not bool(models_state.get("default_model_downloaded_once", False))
    )
    download_payload["auto_download_completed_once"] = bool(models_state.get("default_model_downloaded_once", False))
    download_payload["current_model_id"] = current_download_model_id
    download_payload["auto_download_paused"] = not AUTO_DOWNLOAD_BOOTSTRAP_ENABLED

    upload_snapshot = {
        "active": False,
        "model_id": None,
        "bytes_total": 0,
        "bytes_received": 0,
        "percent": 0,
        "error": None,
    }
    if app is not None:
        state_snapshot = getattr(app.state, "model_upload_state", None)
        if isinstance(state_snapshot, dict):
            upload_snapshot.update(
                {
                    "active": bool(state_snapshot.get("active", False)),
                    "model_id": state_snapshot.get("model_id"),
                    "bytes_total": _safe_int(state_snapshot.get("bytes_total"), 0),
                    "bytes_received": _safe_int(state_snapshot.get("bytes_received"), 0),
                    "percent": _safe_int(state_snapshot.get("percent"), 0),
                    "error": state_snapshot.get("error"),
                }
            )

    active_model_size_bytes = 0
    if has_model:
        try:
            active_model_size_bytes = max(0, int(active_model_path.stat().st_size))
        except OSError:
            active_model_size_bytes = 0
    compatibility = build_large_model_compatibility(
        runtime,
        model_filename=active_model_path.name,
        model_size_bytes=active_model_size_bytes or None,
    )

    raw_system_snapshot = system_snapshot if isinstance(system_snapshot, dict) else default_system_metrics_snapshot()
    system_payload = dict(raw_system_snapshot)
    system_payload["power_estimate"] = build_power_estimate_status(
        runtime,
        raw_system_snapshot.get("power_estimate") if isinstance(raw_system_snapshot, dict) else None,
    )

    return {
        "state": state,
        "model_present": has_model,
        "model": {
            "filename": active_model_path.name,
            "active_model_id": models_state.get("active_model_id"),
            "storage": describe_model_storage(runtime, active_model_path.name, ssd_dir=ssd_models_dir),
            "settings": normalize_model_settings(active_model.get("settings"), filename=active_model_path.name),
            "capabilities": build_model_capabilities(active_model_path.name),
            "projector": build_model_projector_status(runtime, active_model),
        },
        "models": models_payload,
        "storage_targets": storage_targets,
        "download": download_payload,
        "upload": upload_snapshot,
        "llama_server": {
            "running": llama_running or llama_transport_healthy,
            "healthy": llama_ready,
            "ready": llama_ready,
            "transport_healthy": llama_transport_healthy,
            "url": runtime.llama_base_url,
        },
        "backend": {
            "mode": effective_mode,
            "active": active_backend,
            "fallback_active": fallback_active,
        },
        "compatibility": compatibility,
        "llama_runtime": build_llama_runtime_status(runtime, app=app),
        "system": system_payload,
    }


async def _run_script(path: Path, runtime: RuntimeConfig) -> int:
    env = _runtime_env(runtime)
    proc = await asyncio.create_subprocess_exec(
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )

    if proc.stdout is not None:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            logger.info("%s", line.decode("utf-8", errors="replace").rstrip())

    return await proc.wait()


def _upsert_model_status(
    runtime: RuntimeConfig,
    *,
    model_id: str,
    status: str,
    error: str | None = None,
    current_download_model_id: str | None = None,
) -> dict[str, Any]:
    state = ensure_models_state(runtime)
    model = get_model_by_id(state, model_id)
    if model is not None:
        model["status"] = status
        model["error"] = error
    state["current_download_model_id"] = current_download_model_id
    return save_models_state(runtime, state)


def _runtime_env(runtime: RuntimeConfig) -> dict[str, str]:
    env = os.environ.copy()
    env["POTATO_BASE_DIR"] = str(runtime.base_dir)
    env["POTATO_MODEL_PATH"] = str(runtime.model_path)
    env["POTATO_DOWNLOAD_STATE_PATH"] = str(runtime.download_state_path)
    env["POTATO_MODELS_STATE_PATH"] = str(runtime.models_state_path)
    env["POTATO_LLAMA_BASE_URL"] = runtime.llama_base_url
    env["POTATO_CHAT_BACKEND"] = runtime.chat_backend_mode
    env["POTATO_ALLOW_FAKE_FALLBACK"] = "1" if runtime.allow_fake_fallback else "0"
    env["POTATO_LLAMA_PORT"] = str(runtime.llama_port)
    env["POTATO_LLAMA_NO_MMAP"] = str(build_llama_memory_loading_status(runtime).get("no_mmap_env") or "auto")
    env["POTATO_AUTO_DOWNLOAD_MMPROJ"] = "0"
    env["POTATO_VISION_MODEL_NAME_PATTERN_VL"] = "0"
    env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] = "0"
    env.pop("POTATO_MMPROJ_PATH", None)
    try:
        state = ensure_models_state(runtime)
        active_model = get_model_by_id(state, str(state.get("active_model_id") or ""))
    except Exception:
        active_model = None
    if isinstance(active_model, dict):
        active_filename = str(active_model.get("filename") or "")
        active_settings = normalize_model_settings(active_model.get("settings"), filename=active_filename)
        vision_settings = active_settings.get("vision", {})
        if model_supports_vision_filename(active_filename) and bool(vision_settings.get("enabled", False)):
            if is_qwen3_vl_filename(active_filename):
                env["POTATO_VISION_MODEL_NAME_PATTERN_VL"] = "1"
            if is_qwen35_filename(active_filename):
                env["POTATO_VISION_MODEL_NAME_PATTERN_QWEN35"] = "1"
            projector_status = build_model_projector_status(runtime, active_model)
            if projector_status.get("present") and projector_status.get("path"):
                env["POTATO_MMPROJ_PATH"] = str(projector_status["path"])
            else:
                projector_mode = str(vision_settings.get("projector_mode") or "default").strip().lower()
                projector_filename = str(vision_settings.get("projector_filename") or "").strip()
                if projector_mode == "custom" and projector_filename:
                    env["POTATO_MMPROJ_PATH"] = str(runtime.base_dir / "models" / projector_filename)
    env.setdefault("POTATO_MODEL_URL", MODEL_URL)
    return env


async def start_model_download(
    app: FastAPI,
    runtime: RuntimeConfig,
    trigger: str,
    *,
    model_id: str | None = None,
) -> tuple[bool, str]:
    lock = app.state.download_lock

    async with lock:
        task = app.state.model_download_task
        if is_download_task_active(task):
            return False, "already_running"

        if not runtime.ensure_model_script or not runtime.ensure_model_script.exists():
            logger.warning("ensure_model script missing: %s", runtime.ensure_model_script)
            return False, "script_missing"

        state = ensure_models_state(runtime)
        selected_model_id = model_id or str(state.get("default_model_id") or "default")
        default_model_id = str(state.get("default_model_id") or "default")
        model = get_model_by_id(state, selected_model_id)
        if not isinstance(model, dict):
            return False, "model_not_found"

        target_filename = str(model.get("filename") or "")
        target_path = _model_file_path(runtime, target_filename)
        if model_file_present(runtime, target_filename):
            if model.get("status") != "ready":
                _upsert_model_status(runtime, model_id=selected_model_id, status="ready")
            if (
                selected_model_id == default_model_id
                and target_filename == MODEL_FILENAME
                and not bool(state.get("default_model_downloaded_once", False))
            ):
                state["default_model_downloaded_once"] = True
                save_models_state(runtime, state)
            return False, "model_present"

        source_url = str(model.get("source_url") or "")
        if not source_url:
            return False, "source_url_missing"

        expected_total_bytes = await fetch_remote_content_length_bytes(source_url)
        partial_path = _model_file_path(runtime, target_filename + ".part")
        partial_bytes = 0
        try:
            if partial_path.exists():
                partial_bytes = max(0, int(partial_path.stat().st_size))
        except OSError:
            partial_bytes = 0
        free_bytes = get_free_storage_bytes(runtime)
        if is_likely_too_large_for_storage(
            total_bytes=expected_total_bytes,
            free_bytes=free_bytes,
            partial_bytes=partial_bytes,
        ):
            required_bytes = compute_required_download_bytes(expected_total_bytes, partial_bytes)
            percent = int(partial_bytes * 100 / expected_total_bytes) if expected_total_bytes > 0 else 0
            _atomic_write_json(
                runtime.download_state_path,
                {
                    "bytes_total": int(max(0, expected_total_bytes)),
                    "bytes_downloaded": int(max(0, partial_bytes)),
                    "percent": int(max(0, percent)),
                    "speed_bps": 0,
                    "eta_seconds": 0,
                    "error": "insufficient_storage",
                    "required_bytes": int(max(0, required_bytes)),
                    "free_bytes": int(max(0, free_bytes)),
                },
            )
            _upsert_model_status(
                runtime,
                model_id=selected_model_id,
                status="failed",
                error="insufficient_storage",
                current_download_model_id=None,
            )
            return False, "insufficient_storage"

        _upsert_model_status(
            runtime,
            model_id=selected_model_id,
            status="downloading",
            error=None,
            current_download_model_id=selected_model_id,
        )

        env = _runtime_env(runtime)
        env["POTATO_MODEL_PATH"] = str(target_path)
        env["POTATO_MODEL_URL"] = source_url

        async def _worker() -> int:
            logger.info("Starting model download (%s, model=%s)", trigger, selected_model_id)
            proc = await asyncio.create_subprocess_exec(
                str(runtime.ensure_model_script),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            app.state.model_download_process = proc
            try:
                if proc.stdout is not None:
                    while True:
                        line = await proc.stdout.readline()
                        if not line:
                            break
                        logger.info("%s", line.decode("utf-8", errors="replace").rstrip())
                result = await proc.wait()
            finally:
                if app.state.model_download_process is proc:
                    app.state.model_download_process = None

            if result == 0 and model_file_present(runtime, target_filename):
                _upsert_model_status(
                    runtime,
                    model_id=selected_model_id,
                    status="ready",
                    error=None,
                    current_download_model_id=None,
                )
                updated_state = ensure_models_state(runtime)
                if (
                    selected_model_id == default_model_id
                    and target_filename == MODEL_FILENAME
                    and not bool(updated_state.get("default_model_downloaded_once", False))
                ):
                    updated_state["default_model_downloaded_once"] = True
                    save_models_state(runtime, updated_state)
                # Auto-download projector for vision-capable bootstrap model
                if model_supports_vision_filename(target_filename):
                    try:
                        downloaded, reason, proj_name = download_default_projector_for_model(
                            runtime=runtime, model_id=selected_model_id,
                        )
                        if downloaded:
                            logger.info("Auto-downloaded projector %s for bootstrap model", proj_name)
                    except Exception:
                        logger.warning("Failed to auto-download projector for bootstrap model", exc_info=True)
            else:
                failure_state = read_download_progress(runtime)
                failure_reason = str(failure_state.get("error") or "download_failed")
                _upsert_model_status(
                    runtime,
                    model_id=selected_model_id,
                    status="failed",
                    error=failure_reason,
                    current_download_model_id=None,
                )
                logger.warning("Model download script exited with %s (%s)", result, failure_reason)
            return result

        task = asyncio.create_task(_worker(), name=f"potato-download-{trigger}")

        def _clear_task(finished: asyncio.Task[Any]) -> None:
            if app.state.model_download_task is finished:
                app.state.model_download_task = None

        task.add_done_callback(_clear_task)
        app.state.model_download_task = task
        return True, "started"


async def _cancel_model_download_locked(
    app: FastAPI,
    runtime: RuntimeConfig,
    *,
    expected_model_id: str | None = None,
    timeout_seconds: float = MODEL_DOWNLOAD_CANCEL_WAIT_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    task = app.state.model_download_task
    if not is_download_task_active(task):
        return False, "not_running"

    state = ensure_models_state(runtime)
    current_model_id = state.get("current_download_model_id")
    current_model_id_str = (
        str(current_model_id).strip()
        if isinstance(current_model_id, str) and str(current_model_id).strip()
        else None
    )
    if expected_model_id is not None and current_model_id_str != expected_model_id:
        return False, "not_target_download"

    proc = app.state.model_download_process
    if proc is not None and proc.returncode is None:
        proc.terminate()
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=max(0.1, float(timeout_seconds)))
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError:
        return False, "cancel_timeout"

    if app.state.model_download_task is task:
        app.state.model_download_task = None
    if proc is not None and app.state.model_download_process is proc:
        app.state.model_download_process = None

    if current_model_id_str is not None:
        _upsert_model_status(
            runtime,
            model_id=current_model_id_str,
            status="not_downloaded",
            error=None,
            current_download_model_id=None,
        )
    else:
        state["current_download_model_id"] = None
        save_models_state(runtime, state)
    # Mark bootstrap as consumed only when cancelling the default starter model
    if current_model_id_str is not None:
        updated = ensure_models_state(runtime)
        default_id = str(updated.get("default_model_id") or "default")
        if current_model_id_str == default_id and not updated.get("default_model_downloaded_once"):
            updated["default_model_downloaded_once"] = True
            save_models_state(runtime, updated)
    return True, "cancelled"


async def cancel_model_download(
    app: FastAPI,
    runtime: RuntimeConfig,
    *,
    expected_model_id: str | None = None,
    timeout_seconds: float = MODEL_DOWNLOAD_CANCEL_WAIT_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    async with app.state.download_lock:
        return await _cancel_model_download_locked(
            app,
            runtime,
            expected_model_id=expected_model_id,
            timeout_seconds=timeout_seconds,
        )


async def start_runtime_reset(runtime: RuntimeConfig) -> tuple[bool, str]:
    service_name = runtime.runtime_reset_service.strip()
    if not service_name:
        return False, "service_not_configured"

    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo",
            "-n",
            "systemctl",
            "start",
            "--no-block",
            service_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except FileNotFoundError:
        return False, "sudo_missing"
    except OSError:
        logger.exception("Failed to start runtime reset service")
        return False, "spawn_failed"

    if proc.returncode == 0:
        return True, "scheduled"

    details = ((stderr or b"") + b"\n" + (stdout or b"")).decode("utf-8", errors="replace").lower()
    if "not found" in details and service_name.lower() in details:
        return False, "service_missing"
    if "password" in details or "sudoers" in details:
        return False, "permission_denied"
    return False, "start_failed"


def get_status_download_context(app: FastAPI, runtime: RuntimeConfig) -> tuple[bool, int]:
    models_state = ensure_models_state(runtime)
    resolve_active_model(models_state, runtime)
    default_model_id = str(models_state.get("default_model_id") or "default")
    default_model = get_model_by_id(models_state, default_model_id)
    default_model_present = False
    default_model_is_bootstrap_target = False
    if isinstance(default_model, dict):
        default_filename = str(default_model.get("filename") or "")
        default_model_present = model_file_present(runtime, default_filename)
        default_model_is_bootstrap_target = default_filename == MODEL_FILENAME
    task = app.state.model_download_task
    download_active = is_download_task_active(task)
    remaining = compute_auto_download_remaining_seconds(
        runtime,
        model_present=default_model_present,
        download_active=download_active,
        startup_monotonic=app.state.startup_monotonic,
        now_monotonic=get_monotonic_time(),
        countdown_enabled=bool(models_state.get("countdown_enabled", True)),
        default_model_downloaded_once=bool(models_state.get("default_model_downloaded_once", False))
        or not default_model_is_bootstrap_target,
    )
    return download_active, remaining


async def activate_model(
    app: FastAPI,
    runtime: RuntimeConfig,
    *,
    model_id: str,
) -> tuple[bool, str, bool]:
    state = ensure_models_state(runtime)
    target = get_model_by_id(state, model_id)
    if target is None:
        return False, "model_not_found", False
    filename = str(target.get("filename") or "")
    if not model_file_present(runtime, filename):
        return False, "model_not_ready", False
    state["active_model_id"] = model_id
    target["status"] = "ready"
    save_models_state(runtime, state)
    runtime.model_path = resolve_model_runtime_path(runtime, filename)
    restarted, _reason = await restart_managed_llama_process(app)
    return True, "activated", restarted


async def purge_all_models(
    app: FastAPI,
    runtime: RuntimeConfig,
    *,
    reset_bootstrap_flag: bool = False,
) -> dict[str, Any]:
    cancelled_download = False
    cancelled_upload = False
    restarted = False
    restart_reason = "not_required"
    deleted_files = 0
    freed_bytes = 0

    async with app.state.download_lock:
        task = app.state.model_download_task
        if is_download_task_active(task):
            proc = app.state.model_download_process
            if proc is not None and proc.returncode is None:
                proc.terminate()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            cancelled_download = True
        app.state.model_download_task = None
        app.state.model_download_process = None

    upload_lock = app.state.model_upload_lock
    upload_wait_required = bool(app.state.model_upload_state.get("active")) or bool(upload_lock.locked())
    upload_lock_acquired = False
    if upload_wait_required:
        app.state.model_upload_cancel_requested = True
        cancelled_upload = True
    try:
        await asyncio.wait_for(upload_lock.acquire(), timeout=MODEL_UPLOAD_PURGE_WAIT_TIMEOUT_SECONDS)
        upload_lock_acquired = True
    except asyncio.TimeoutError:
        return {
            "purged": False,
            "reason": "upload_cancel_timeout",
            "deleted_files": int(max(0, deleted_files)),
            "freed_bytes": int(max(0, freed_bytes)),
            "cancelled_download": cancelled_download,
            "cancelled_upload": cancelled_upload,
            "restarted": restarted,
            "restart_reason": restart_reason,
            "reset_bootstrap_flag": bool(reset_bootstrap_flag),
        }

    try:
        app.state.model_upload_state = _empty_model_upload_state()
        app.state.model_upload_cancel_requested = False

        restarted, restart_reason = await restart_managed_llama_process(app)

        models_dir = runtime.base_dir / "models"
        deleted_target_paths: set[Path] = set()
        if models_dir.exists():
            for path in models_dir.iterdir():
                try:
                    path_is_symlink = path.is_symlink()
                except OSError:
                    path_is_symlink = False
                if not path.is_file() and not path_is_symlink:
                    continue
                target_path: Path | None = None
                try:
                    if path_is_symlink:
                        target_path = path.resolve(strict=False)
                        file_size = max(0, int(target_path.stat().st_size)) if target_path.exists() else 0
                    else:
                        file_size = max(0, int(path.stat().st_size))
                except OSError:
                    file_size = 0
                try:
                    path.unlink(missing_ok=True)
                    deleted_files += 1
                    freed_bytes += file_size
                    if (
                        target_path is not None
                        and target_path not in deleted_target_paths
                        and target_path.exists()
                    ):
                        target_path.unlink(missing_ok=True)
                        deleted_target_paths.add(target_path)
                        deleted_files += 1
                except OSError:
                    logger.warning("Could not delete model file during purge: %s", path, exc_info=True)

        for state_path in (
            runtime.download_state_path,
            runtime.download_state_path.with_suffix(runtime.download_state_path.suffix + ".curl.err"),
        ):
            try:
                state_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not delete state file during purge: %s", state_path, exc_info=True)

        previous = ensure_models_state(runtime)
        countdown_enabled = bool(previous.get("countdown_enabled", True))
        downloaded_once = bool(previous.get("default_model_downloaded_once", False))
        if reset_bootstrap_flag:
            downloaded_once = False

        reset_state = {
            "version": MODELS_STATE_VERSION,
            "countdown_enabled": countdown_enabled,
            "default_model_downloaded_once": downloaded_once,
            "active_model_id": "default",
            "default_model_id": "default",
            "current_download_model_id": None,
            "models": [_default_model_record(runtime)],
        }
        save_models_state(runtime, reset_state)
        runtime.model_path = _model_file_path(runtime, MODEL_FILENAME)

        return {
            "purged": True,
            "reason": "purged",
            "deleted_files": int(max(0, deleted_files)),
            "freed_bytes": int(max(0, freed_bytes)),
            "cancelled_download": cancelled_download,
            "cancelled_upload": cancelled_upload,
            "restarted": restarted,
            "restart_reason": restart_reason,
            "reset_bootstrap_flag": bool(reset_bootstrap_flag),
        }
    finally:
        if upload_lock_acquired:
            upload_lock.release()


def _safe_upload_filename(name: str) -> str:
    cleaned = _sanitize_filename(name)
    if not cleaned.lower().endswith(".gguf"):
        raise ValueError("gguf_required")
    return cleaned


async def orchestrator_loop(app: FastAPI, runtime: RuntimeConfig) -> None:
    while True:
        try:
            models_state = ensure_models_state(runtime)
            active_model, active_model_path = resolve_active_model(models_state, runtime)
            download_active = is_download_task_active(app.state.model_download_task)
            default_model = get_model_by_id(
                models_state,
                str(models_state.get("default_model_id") or "default"),
            )
            default_model_present = False
            default_model_is_bootstrap_target = False
            if isinstance(default_model, dict):
                default_filename = str(default_model.get("filename") or "")
                default_model_present = model_file_present(runtime, default_filename)
                default_model_is_bootstrap_target = default_filename == MODEL_FILENAME

            any_ready = any_model_ready(runtime)
            if should_auto_start_download(
                runtime,
                model_present=default_model_present or any_ready,
                download_active=download_active,
                startup_monotonic=app.state.startup_monotonic,
                now_monotonic=get_monotonic_time(),
                countdown_enabled=bool(models_state.get("countdown_enabled", True)),
                default_model_downloaded_once=bool(models_state.get("default_model_downloaded_once", False))
                or not default_model_is_bootstrap_target,
            ):
                await start_model_download(
                    app,
                    runtime,
                    trigger="idle",
                    model_id=str(models_state.get("default_model_id") or "default"),
                )

            active_model_is_present = False
            try:
                active_model_is_present = active_model_path.exists() and active_model_path.stat().st_size > 0
            except OSError:
                active_model_is_present = False

            if active_model_is_present:
                llama_process = app.state.llama_process
                if llama_process is None or llama_process.returncode is not None:
                    if runtime.start_llama_script.exists():
                        await terminate_stray_llama_processes(runtime)
                        app.state.llama_process = await asyncio.create_subprocess_exec(
                            str(runtime.start_llama_script),
                            env=_runtime_env(runtime),
                        )
                        logger.info("Started llama-server process")
                    else:
                        logger.warning("start_llama script missing: %s", runtime.start_llama_script)

                await refresh_llama_readiness(app, runtime, active_model_path=active_model_path)
            else:
                reset_llama_readiness_state(app, reason="model_missing")

            await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("orchestrator loop error")
            await asyncio.sleep(2)


async def log_stream() -> Any:
    if not shutil_which("journalctl"):
        yield "data: journalctl not available\n\n"
        return

    proc = await asyncio.create_subprocess_exec(
        "journalctl",
        "-u",
        "potato",
        "-f",
        "-n",
        "200",
        "--no-pager",
        "-o",
        "cat",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    try:
        while True:
            assert proc.stdout is not None
            line = await proc.stdout.readline()
            if not line:
                if proc.returncode is not None:
                    break
                await asyncio.sleep(0.1)
                continue
            text = line.decode("utf-8", errors="replace").rstrip()
            yield f"data: {text}\n\n"
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()


def shutil_which(cmd: str) -> str | None:
    for path in os.getenv("PATH", "").split(os.pathsep):
        candidate = Path(path) / cmd
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _merge_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload)
    for key, value in DEFAULT_CHAT_SETTINGS.items():
        if key == "seed" and "seed" not in merged:
            continue
        merged.setdefault(key, value)
    merged.setdefault("cache_prompt", True)
    return merged


def _merge_active_model_chat_defaults(payload: dict[str, Any], *, runtime: RuntimeConfig) -> dict[str, Any]:
    merged = dict(payload)
    chat_settings = get_active_model_settings(runtime).get("chat", {})
    if not isinstance(chat_settings, dict):
        chat_settings = {}

    for key in (
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "presence_penalty",
        "max_tokens",
        "stream",
        "generation_mode",
        "cache_prompt",
    ):
        if key not in merged and key in chat_settings:
            merged[key] = chat_settings[key]

    if "seed" not in merged and str(chat_settings.get("generation_mode") or "").strip().lower() == "deterministic":
        merged["seed"] = chat_settings.get("seed")

    system_prompt = str(chat_settings.get("system_prompt") or "").strip()
    messages = merged.get("messages")
    if system_prompt and isinstance(messages, list):
        has_system_message = any(
            isinstance(message, dict) and str(message.get("role") or "").strip().lower() == "system"
            for message in messages
        )
        if not has_system_message:
            merged["messages"] = [{"role": "system", "content": system_prompt}, *messages]

    return merged


def get_active_model_settings(runtime: RuntimeConfig) -> dict[str, Any]:
    state = ensure_models_state(runtime)
    active_model = get_model_by_id(state, str(state.get("active_model_id") or ""))
    if not isinstance(active_model, dict):
        active_model = state["models"][0]
    filename = str(active_model.get("filename") or "")
    return normalize_model_settings(active_model.get("settings"), filename=filename)


def build_settings_document_payload(runtime: RuntimeConfig) -> dict[str, Any]:
    models_state = ensure_models_state(runtime)
    runtime_settings = read_llama_runtime_settings(runtime)
    models_payload: list[dict[str, Any]] = []
    for item in models_state.get("models", []):
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "")
        models_payload.append(
            {
                "id": str(item.get("id") or ""),
                "settings": normalize_model_settings(item.get("settings"), filename=filename),
            }
        )
    return {
        "version": 1,
        "active_model_id": str(models_state.get("active_model_id") or ""),
        "runtime": {
            "memory_loading_mode": str(runtime_settings.get("memory_loading_mode") or "auto"),
            "allow_unsupported_large_models": bool(runtime_settings.get("allow_unsupported_large_models", False)),
        },
        "models": models_payload,
    }


def export_settings_document_yaml(runtime: RuntimeConfig) -> str:
    return yaml.safe_dump(build_settings_document_payload(runtime), sort_keys=False, allow_unicode=True)


def apply_settings_document_yaml(runtime: RuntimeConfig, document: str) -> tuple[bool, str, dict[str, Any]]:
    try:
        payload = yaml.safe_load(document) or {}
    except yaml.YAMLError:
        return False, "invalid_yaml", {}
    if not isinstance(payload, dict):
        return False, "invalid_document", {}

    current_models_state = ensure_models_state(runtime)
    next_models_state = json.loads(json.dumps(current_models_state))
    next_runtime_settings = read_llama_runtime_settings(runtime)

    active_model_id = str(payload.get("active_model_id") or next_models_state.get("active_model_id") or "").strip()
    model_entries = payload.get("models")
    if model_entries is not None and not isinstance(model_entries, list):
        return False, "invalid_models", {}

    if isinstance(model_entries, list):
        for item in model_entries:
            if not isinstance(item, dict):
                return False, "invalid_models", {}
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                return False, "model_id_required", {}
            model = get_model_by_id(next_models_state, model_id)
            if model is None:
                return False, "model_not_found", {"model_id": model_id}
            filename = str(model.get("filename") or "")
            try:
                model["settings"] = normalize_model_settings(item.get("settings"), filename=filename)
            except ModelSettingsValidationError as exc:
                return False, "invalid_settings", {"field": exc.field, "model_id": model_id}

    if active_model_id:
        if get_model_by_id(next_models_state, active_model_id) is None:
            return False, "active_model_not_found", {"active_model_id": active_model_id}
        next_models_state["active_model_id"] = active_model_id

    runtime_payload = payload.get("runtime")
    if runtime_payload is not None:
        if not isinstance(runtime_payload, dict):
            return False, "invalid_runtime", {}
        if "memory_loading_mode" in runtime_payload:
            next_runtime_settings["memory_loading_mode"] = normalize_llama_memory_loading_mode(
                runtime_payload.get("memory_loading_mode")
            )
        if "allow_unsupported_large_models" in runtime_payload:
            next_runtime_settings["allow_unsupported_large_models"] = normalize_allow_unsupported_large_models(
                runtime_payload.get("allow_unsupported_large_models")
            )

    save_models_state(runtime, next_models_state)
    write_llama_runtime_settings(
        runtime,
        memory_loading_mode=str(next_runtime_settings.get("memory_loading_mode") or "auto"),
        allow_unsupported_large_models=bool(next_runtime_settings.get("allow_unsupported_large_models", False)),
        power_calibration=next_runtime_settings.get("power_calibration"),
    )
    return True, "updated", build_settings_document_payload(runtime)


def curated_projector_repo_for_model(filename: str) -> str | None:
    return projector_repo_for_model(filename)


def download_default_projector_for_model(*, runtime: RuntimeConfig, model_id: str) -> tuple[bool, str, str | None]:
    state = ensure_models_state(runtime)
    model = get_model_by_id(state, model_id)
    if model is None:
        return False, "model_not_found", None
    filename = str(model.get("filename") or "")
    if not model_supports_vision_filename(filename):
        return False, "vision_not_supported", None
    repo = curated_projector_repo_for_model(filename)
    candidates = default_projector_candidates_for_model(filename)
    if not repo or not candidates:
        return False, "projector_repo_unknown", None

    models_dir = runtime.base_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(follow_redirects=True, timeout=120.0)
    try:
        for candidate in candidates:
            target_path = models_dir / candidate
            if target_path.exists():
                return True, "downloaded", candidate
            url = f"https://huggingface.co/{repo}/resolve/main/{candidate}"
            part_path = target_path.with_suffix(target_path.suffix + ".part")
            try:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with part_path.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            if chunk:
                                handle.write(chunk)
                part_path.replace(target_path)
                return True, "downloaded", candidate
            except Exception:
                part_path.unlink(missing_ok=True)
                continue
    finally:
        client.close()
    return False, "download_failed", None


def _forward_headers(request: Request) -> dict[str, str]:
    forward = {}
    if "authorization" in request.headers:
        forward["authorization"] = request.headers["authorization"]
    if "openai-organization" in request.headers:
        forward["openai-organization"] = request.headers["openai-organization"]
    return forward


CHAT_HTML = (Path(__file__).resolve().parent / "assets" / "chat.html").read_text(encoding="utf-8")


def create_app(runtime: RuntimeConfig | None = None, enable_orchestrator: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.startup_monotonic = get_monotonic_time()
        ensure_models_state(app.state.runtime)
        prime_system_metrics_counters()
        app.state.system_metrics_snapshot = collect_system_metrics_snapshot()
        app.state.system_metrics_task = asyncio.create_task(
            system_metrics_loop(app),
            name="potato-system-metrics",
        )
        if app.state.runtime.enable_orchestrator:
            app.state.orchestrator_task = asyncio.create_task(
                orchestrator_loop(app, app.state.runtime),
                name="potato-orchestrator",
            )
        try:
            yield
        finally:
            task = app.state.orchestrator_task
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            proc = app.state.llama_process
            if proc is not None and proc.returncode is None:
                try:
                    await _terminate_process(proc)
                except (asyncio.TimeoutError, OSError):
                    logger.critical("pid=%s did not exit after SIGKILL during shutdown, giving up", getattr(proc, "pid", "?"))

            download_task = app.state.model_download_task
            if is_download_task_active(download_task):
                download_task.cancel()
                try:
                    await download_task
                except asyncio.CancelledError:
                    pass

            system_task = app.state.system_metrics_task
            if system_task is not None:
                system_task.cancel()
                try:
                    await system_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="Potato Web", version="0.3-pre-alpha", lifespan=_lifespan)
    app.mount("/assets", StaticFiles(directory=str(WEB_ASSETS_DIR)), name="assets")
    app.state.runtime = runtime or RuntimeConfig.from_env()
    app.state.llama_process = None
    app.state.model_download_task = None
    app.state.model_download_process = None
    app.state.system_metrics_task = None
    app.state.system_metrics_snapshot = default_system_metrics_snapshot()
    app.state.download_lock = asyncio.Lock()
    app.state.model_upload_lock = asyncio.Lock()
    app.state.model_upload_cancel_requested = False
    app.state.model_upload_state = _empty_model_upload_state()
    app.state.llama_runtime_switch_lock = asyncio.Lock()
    app.state.llama_runtime_switch_state = _empty_llama_runtime_switch_state()
    app.state.llama_readiness_state = _empty_llama_readiness_state()
    app.state.startup_monotonic = None
    app.state.orchestrator_task = None
    app.state.chat_repository = ChatRepositoryManager(
        llama=LlamaCppRepository(app.state.runtime.llama_base_url),
        fake=FakeLlamaRepository(),
    )

    if enable_orchestrator is not None:
        app.state.runtime.enable_orchestrator = enable_orchestrator

    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        return HTMLResponse(CHAT_HTML)

    @app.get("/status")
    async def status(request: Request, runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
        download_active, auto_start_remaining = get_status_download_context(request.app, runtime_cfg)
        return JSONResponse(
            await build_status(
                runtime_cfg,
                app=request.app,
                download_active=download_active,
                auto_start_remaining_seconds=auto_start_remaining,
                system_snapshot=request.app.state.system_metrics_snapshot,
            )
        )

    @app.get("/internal/llama-healthz")
    async def llama_healthz(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
        transport_healthy = await check_llama_health(runtime_cfg, busy_is_healthy=False)
        inference_healthy = False
        if transport_healthy:
            inference_healthy = await probe_llama_inference_slot(runtime_cfg)
        return JSONResponse(
            {
                "healthy": transport_healthy and inference_healthy,
                "transport_healthy": transport_healthy,
                "inference_healthy": inference_healthy,
            }
        )

    @app.get("/logs")
    async def logs() -> StreamingResponse:
        return StreamingResponse(
            log_stream(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    @app.post("/internal/restart-llama")
    async def restart_llama(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"restarted": False, "reason": "orchestrator_disabled"},
            )
        restarted, reason = await restart_managed_llama_process(app)
        if restarted:
            return JSONResponse(status_code=200, content={"restarted": True, "reason": reason})
        return JSONResponse(
            status_code=200,
            content={"restarted": False, "reason": reason},
        )

    @app.post("/internal/llama-runtime/switch")
    async def switch_llama_runtime(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"switched": False, "reason": "orchestrator_disabled"},
            )

        payload = await request.json()
        family = str(payload.get("family") or "").strip()
        if not family:
            return JSONResponse(status_code=400, content={"switched": False, "reason": "family_required"})

        slot = find_runtime_slot_by_family(runtime_cfg, family)
        if slot is None:
            return JSONResponse(status_code=404, content={"switched": False, "reason": "runtime_not_found"})

        async with app.state.llama_runtime_switch_lock:
            switch_state = app.state.llama_runtime_switch_state
            if switch_state.get("active"):
                return JSONResponse(status_code=409, content={"switched": False, "reason": "switch_already_running"})

            switch_state.update(
                {
                    "active": True,
                    "target_family": family,
                    "started_at_unix": int(time.time()),
                    "completed_at_unix": None,
                    "error": None,
                }
            )

            try:
                restarted, restart_reason = await restart_managed_llama_process(app)
                install_result = await install_llama_runtime_bundle(runtime_cfg, Path(str(slot["path"])))
                if not install_result.get("ok"):
                    reason = str(install_result.get("reason") or "install_failed")
                    switch_state.update(
                        {
                            "active": False,
                            "completed_at_unix": int(time.time()),
                            "error": reason,
                        }
                    )
                    return JSONResponse(
                        status_code=500,
                        content={
                            "switched": False,
                            "reason": reason,
                            "family": family,
                            "restarted": restarted,
                            "restart_reason": restart_reason,
                        },
                    )

                marker = write_llama_runtime_bundle_marker(runtime_cfg, slot)
                switch_state.update(
                    {
                        "active": False,
                        "target_family": None,
                        "completed_at_unix": int(time.time()),
                        "error": None,
                    }
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "switched": True,
                        "reason": "runtime_switched",
                        "family": family,
                        "slot": slot,
                        "install": install_result,
                        "restarted": restarted,
                        "restart_reason": restart_reason,
                        "marker": marker,
                    },
                )
            except Exception:
                switch_state.update(
                    {
                        "active": False,
                        "completed_at_unix": int(time.time()),
                        "error": "switch_failed",
                    }
                )
                raise

    @app.post("/internal/llama-runtime/memory-loading")
    async def set_llama_memory_loading_mode(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"updated": False, "reason": "orchestrator_disabled"},
            )

        payload = await request.json()
        requested_mode = payload.get("mode", payload.get("memory_loading_mode"))
        mode = normalize_llama_memory_loading_mode(requested_mode)
        saved = write_llama_runtime_settings(runtime_cfg, memory_loading_mode=mode)
        restarted, restart_reason = await restart_managed_llama_process(app)
        memory_loading = build_llama_memory_loading_status(runtime_cfg)
        return JSONResponse(
            status_code=200,
            content={
                "updated": True,
                "reason": "memory_loading_updated",
                "memory_loading": memory_loading,
                "saved": saved,
                "restarted": restarted,
                "restart_reason": restart_reason,
            },
        )

    @app.post("/internal/compatibility/large-model-override")
    async def set_large_model_override(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"updated": False, "reason": "orchestrator_disabled"},
            )

        payload = await request.json()
        enabled = normalize_allow_unsupported_large_models(
            payload.get("enabled", payload.get("allow_unsupported_large_models"))
        )
        saved = write_llama_runtime_settings(
            runtime_cfg,
            allow_unsupported_large_models=enabled,
        )
        compatibility = build_large_model_compatibility(
            runtime_cfg,
            model_filename=runtime_cfg.model_path.name,
            model_size_bytes=(runtime_cfg.model_path.stat().st_size if runtime_cfg.model_path.exists() else None),
            allow_override=enabled,
        )
        return JSONResponse(
            status_code=200,
            content={
                "updated": True,
                "reason": "large_model_override_updated",
                "override": {
                    "enabled": enabled,
                    "label": (
                        "Try unsupported large model anyway"
                        if enabled
                        else "Use compatibility warnings (default)"
                    ),
                },
                "compatibility": compatibility,
                "saved": saved,
            },
        )

    @app.post("/internal/power-calibration/sample")
    async def capture_power_calibration_sample(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        payload = await request.json()
        wall_watts = _safe_positive_float(payload.get("wall_watts"))
        if wall_watts is None:
            return JSONResponse(
                status_code=400,
                content={"captured": False, "reason": "invalid_wall_watts"},
            )

        current_power = _build_power_estimate_snapshot()
        raw_watts = _safe_positive_float(current_power.get("total_watts"))
        if raw_watts is None:
            return JSONResponse(
                status_code=409,
                content={
                    "captured": False,
                    "reason": "power_unavailable",
                    "power_estimate": build_power_estimate_status(runtime_cfg, current_power),
                },
            )

        calibration = _append_power_calibration_sample(
            runtime_cfg,
            raw_pmic_watts=raw_watts,
            wall_watts=wall_watts,
        )
        power_status = build_power_estimate_status(runtime_cfg, current_power)
        return JSONResponse(
            status_code=200,
            content={
                "captured": True,
                "reason": "power_calibration_sample_captured",
                "sample": calibration.get("samples", [])[-1] if calibration.get("samples") else None,
                "calibration": build_power_calibration_status(runtime_cfg),
                "power_estimate": power_status,
            },
        )

    @app.post("/internal/power-calibration/fit")
    async def fit_power_calibration(
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        ok, reason, calibration = _fit_and_persist_power_calibration(runtime_cfg)
        status_code = 200 if ok else 400
        return JSONResponse(
            status_code=status_code,
            content={
                "updated": ok,
                "reason": reason,
                "calibration": build_power_calibration_status(runtime_cfg) if ok else calibration,
            },
        )

    @app.post("/internal/power-calibration/reset")
    async def reset_power_calibration(
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        calibration = _reset_power_calibration(runtime_cfg)
        return JSONResponse(
            status_code=200,
            content={
                "updated": True,
                "reason": "power_calibration_reset",
                "calibration": build_power_calibration_status(runtime_cfg),
                "saved": calibration,
            },
        )

    @app.post("/internal/start-model-download")
    async def start_model_download_now(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"started": False, "reason": "orchestrator_disabled"},
            )

        started, reason = await start_model_download(app, runtime_cfg, trigger="manual")
        status_code = 202 if started else 200
        return JSONResponse(status_code=status_code, content={"started": started, "reason": reason})

    @app.post("/internal/download-countdown")
    async def set_download_countdown(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"updated": False, "reason": "orchestrator_disabled"},
            )
        payload = await request.json()
        enabled = bool(payload.get("enabled", True))
        updated_state = set_download_countdown_enabled(runtime_cfg, enabled)
        return JSONResponse(
            status_code=200,
            content={
                "updated": True,
                "reason": "countdown_updated",
                "countdown_enabled": bool(updated_state.get("countdown_enabled", enabled)),
            },
        )

    @app.post("/internal/models/register")
    async def register_model_endpoint(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        payload = await request.json()
        source_url = str(payload.get("source_url") or "").strip()
        alias_raw = payload.get("alias")
        alias = str(alias_raw).strip() if isinstance(alias_raw, str) and alias_raw.strip() else None
        ok, reason, model = register_model_url(runtime_cfg, source_url=source_url, alias=alias)
        if not ok:
            return JSONResponse(status_code=400, content={"ok": False, "reason": reason})
        response_payload: dict[str, Any] = {"ok": True, "reason": reason, "model": model}
        if isinstance(model, dict):
            model_filename = str(model.get("filename") or "")
            model_source_url = str(model.get("source_url") or "")
            size_bytes = 0
            if model_source_url:
                size_bytes = await fetch_remote_content_length_bytes(model_source_url)
            compatibility = build_large_model_compatibility(
                runtime_cfg,
                model_filename=model_filename,
                model_size_bytes=size_bytes or None,
            )
            warnings = compatibility.get("warnings")
            if isinstance(warnings, list) and warnings:
                response_payload["warnings"] = warnings
        return JSONResponse(status_code=200, content=response_payload)

    @app.post("/internal/models/download")
    async def start_selected_model_download(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"started": False, "reason": "orchestrator_disabled"},
            )
        payload = await request.json()
        model_id = str(payload.get("model_id") or "").strip()
        if not model_id:
            return JSONResponse(status_code=400, content={"started": False, "reason": "model_id_required"})
        started, reason = await start_model_download(
            app,
            runtime_cfg,
            trigger="manual-model",
            model_id=model_id,
        )
        status_code = 202 if started else 200
        return JSONResponse(status_code=status_code, content={"started": started, "reason": reason, "model_id": model_id})

    @app.post("/internal/models/settings")
    async def update_model_settings_endpoint(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        payload = await request.json()
        model_id = str(payload.get("model_id") or "").strip()
        settings = payload.get("settings")
        if not model_id:
            return JSONResponse(status_code=400, content={"updated": False, "reason": "model_id_required"})
        if not isinstance(settings, dict):
            return JSONResponse(status_code=400, content={"updated": False, "reason": "settings_required"})
        updated, reason, model = update_model_settings(runtime_cfg, model_id=model_id, settings=settings)
        if not updated:
            status_code = 404 if reason == "model_not_found" else 400
            return JSONResponse(status_code=status_code, content={"updated": False, "reason": reason, "model_id": model_id})
        restarted = False
        restart_reason = "not_required"
        state = ensure_models_state(runtime_cfg)
        if model_id == str(state.get("active_model_id") or ""):
            restarted, restart_reason = await restart_managed_llama_process(app)
        return JSONResponse(
            status_code=200,
            content={
                "updated": True,
                "reason": reason,
                "model_id": model_id,
                "model": model,
                "restarted": restarted,
                "restart_reason": restart_reason,
            },
        )

    @app.get("/internal/settings-document")
    async def get_settings_document(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={
                "format": "yaml",
                "document": export_settings_document_yaml(runtime_cfg),
            },
        )

    @app.post("/internal/settings-document")
    async def apply_settings_document_endpoint(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        payload = await request.json()
        document = str(payload.get("document") or "")
        if not document.strip():
            return JSONResponse(status_code=400, content={"updated": False, "reason": "document_required"})
        updated, reason, settings_document = apply_settings_document_yaml(runtime_cfg, document)
        if not updated:
            return JSONResponse(status_code=400, content={"updated": False, "reason": reason, **settings_document})
        restarted, restart_reason = await restart_managed_llama_process(app)
        return JSONResponse(
            status_code=200,
            content={
                "updated": True,
                "reason": reason,
                "active_model_id": settings_document.get("active_model_id"),
                "document": yaml.safe_dump(settings_document, sort_keys=False, allow_unicode=True),
                "restarted": restarted,
                "restart_reason": restart_reason,
            },
        )

    @app.post("/internal/models/download-projector")
    async def download_projector_for_model_endpoint(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        payload = await request.json()
        model_id = str(payload.get("model_id") or "").strip()
        if not model_id:
            return JSONResponse(status_code=400, content={"downloaded": False, "reason": "model_id_required"})
        downloaded, reason, projector_filename = await asyncio.to_thread(
            download_default_projector_for_model,
            runtime=runtime_cfg,
            model_id=model_id,
        )
        if not downloaded:
            return JSONResponse(
                status_code=400 if reason != "model_not_found" else 404,
                content={"downloaded": False, "reason": reason, "model_id": model_id},
            )
        state = ensure_models_state(runtime_cfg)
        model = get_model_by_id(state, model_id)
        if isinstance(model, dict):
            settings = normalize_model_settings(model.get("settings"), filename=str(model.get("filename") or ""))
            settings["vision"]["projector_filename"] = projector_filename
            model["settings"] = settings
            save_models_state(runtime_cfg, state)
        restarted = False
        restart_reason = "not_required"
        if str(state.get("active_model_id") or "") == model_id:
            restarted, restart_reason = await restart_managed_llama_process(app)
        return JSONResponse(
            status_code=200,
            content={
                "downloaded": True,
                "reason": reason,
                "model_id": model_id,
                "projector_filename": projector_filename,
                "restarted": restarted,
                "restart_reason": restart_reason,
            },
        )

    @app.post("/internal/models/cancel-download")
    async def cancel_selected_model_download(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"cancelled": False, "reason": "orchestrator_disabled"},
            )
        cancelled, reason = await cancel_model_download(app, runtime_cfg)
        return JSONResponse(status_code=200, content={"cancelled": cancelled, "reason": reason})

    @app.post("/internal/models/activate")
    async def activate_model_endpoint(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"switched": False, "reason": "orchestrator_disabled"},
            )
        payload = await request.json()
        model_id = str(payload.get("model_id") or "").strip()
        if not model_id:
            return JSONResponse(status_code=400, content={"switched": False, "reason": "model_id_required"})
        switched, reason, restarted = await activate_model(app, runtime_cfg, model_id=model_id)
        status_code = 200 if switched else (409 if reason in {"model_not_ready", "model_not_found"} else 400)
        return JSONResponse(
            status_code=status_code,
            content={"switched": switched, "reason": reason, "restarted": restarted, "model_id": model_id},
        )

    @app.post("/internal/models/move-to-ssd")
    async def move_model_to_ssd_endpoint(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"moved": False, "reason": "orchestrator_disabled"},
            )
        payload = await request.json()
        model_id = str(payload.get("model_id") or "").strip()
        if not model_id:
            return JSONResponse(status_code=400, content={"moved": False, "reason": "model_id_required"})
        ssd_dir = get_preferred_model_offload_dir(runtime_cfg)
        if ssd_dir is None:
            return JSONResponse(
                status_code=409,
                content={"moved": False, "reason": "no_ssd_available", "model_id": model_id},
            )
        moved, reason, storage = await asyncio.to_thread(
            move_model_to_ssd,
            runtime_cfg,
            model_id=model_id,
            ssd_dir=ssd_dir,
        )
        if moved:
            restarted = False
            restart_reason = "not_required"
            state = ensure_models_state(runtime_cfg)
            if model_id == str(state.get("active_model_id") or ""):
                model = get_model_by_id(state, model_id)
                if isinstance(model, dict):
                    runtime_cfg.model_path = resolve_model_runtime_path(runtime_cfg, str(model.get("filename") or ""))
                restarted, restart_reason = await restart_managed_llama_process(app)
            return JSONResponse(
                status_code=200,
                content={
                    "moved": True,
                    "reason": reason,
                    "model_id": model_id,
                    "storage": storage,
                    "restarted": restarted,
                    "restart_reason": restart_reason,
                },
            )
        status_code = 404 if reason == "model_not_found" else 500 if reason == "move_failed" else 409
        return JSONResponse(
            status_code=status_code,
            content={
                "moved": False,
                "reason": reason,
                "model_id": model_id,
                "storage": storage,
                "restarted": False,
                "restart_reason": "not_required",
            },
        )

    @app.post("/internal/models/delete")
    async def delete_model_endpoint(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"deleted": False, "reason": "orchestrator_disabled"},
            )
        payload = await request.json()
        model_id = str(payload.get("model_id") or "").strip()
        if not model_id:
            return JSONResponse(status_code=400, content={"deleted": False, "reason": "model_id_required"})
        cancelled_download = False
        async with app.state.download_lock:
            models_state = ensure_models_state(runtime_cfg)
            current_download_model_id = str(models_state.get("current_download_model_id") or "").strip()
            if current_download_model_id == model_id and is_download_task_active(app.state.model_download_task):
                cancelled_download, cancel_reason = await _cancel_model_download_locked(
                    app,
                    runtime_cfg,
                    expected_model_id=model_id,
                    timeout_seconds=MODEL_DOWNLOAD_CANCEL_WAIT_TIMEOUT_SECONDS,
                )
                if not cancelled_download:
                    reason = "delete_cancel_timeout" if cancel_reason == "cancel_timeout" else "delete_cancel_failed"
                    status_code = 409 if reason == "delete_cancel_timeout" else 400
                    return JSONResponse(
                        status_code=status_code,
                        content={
                            "deleted": False,
                            "reason": reason,
                            "model_id": model_id,
                            "deleted_file": False,
                            "freed_bytes": 0,
                            "cancelled_download": False,
                        },
                    )
            deleted, reason, deleted_file, freed_bytes, deleted_active = delete_model(runtime_cfg, model_id=model_id)
        restarted = False
        restart_reason = "not_required"
        if deleted and deleted_active:
            models_state = ensure_models_state(runtime_cfg)
            resolve_active_model(models_state, runtime_cfg)
            restarted, restart_reason = await restart_managed_llama_process(app)
        if deleted:
            return JSONResponse(
                status_code=200,
                content={
                    "deleted": True,
                    "reason": reason,
                    "model_id": model_id,
                    "deleted_file": deleted_file,
                    "freed_bytes": int(max(0, freed_bytes)),
                    "deleted_active": deleted_active,
                    "cancelled_download": cancelled_download,
                    "restarted": restarted,
                    "restart_reason": restart_reason,
                },
            )
        status_code = 404 if reason == "model_not_found" else 400
        return JSONResponse(
            status_code=status_code,
            content={
                "deleted": False,
                "reason": reason,
                "model_id": model_id,
                "deleted_file": False,
                "freed_bytes": 0,
                "cancelled_download": False,
            },
        )

    @app.post("/internal/models/purge")
    async def purge_models_endpoint(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"purged": False, "reason": "orchestrator_disabled"},
            )
        payload = await request.json()
        reset_bootstrap_flag = bool(payload.get("reset_bootstrap_flag", True))
        result = await purge_all_models(app, runtime_cfg, reset_bootstrap_flag=reset_bootstrap_flag)
        return JSONResponse(status_code=200, content=result)

    @app.post("/internal/models/upload")
    async def upload_model_endpoint(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"uploaded": False, "reason": "orchestrator_disabled"},
            )

        raw_filename = request.headers.get("x-potato-filename", "")
        try:
            filename = _safe_upload_filename(raw_filename)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"uploaded": False, "reason": str(exc)})

        async with app.state.model_upload_lock:
            if app.state.model_upload_state.get("active"):
                return JSONResponse(status_code=409, content={"uploaded": False, "reason": "upload_already_running"})

            declared_total = max(0, _safe_int(request.headers.get("content-length"), 0))
            max_upload_bytes = get_model_upload_max_bytes()
            app.state.model_upload_cancel_requested = False
            app.state.model_upload_state = {
                "active": True,
                "model_id": None,
                "bytes_total": declared_total,
                "bytes_received": 0,
                "percent": 0,
                "error": None,
            }
            if max_upload_bytes is not None and declared_total > 0 and declared_total > max_upload_bytes:
                app.state.model_upload_state.update({"active": False, "error": "upload_too_large"})
                return JSONResponse(status_code=413, content={"uploaded": False, "reason": "upload_too_large"})

            state = ensure_models_state(runtime_cfg)
            existing_names = {
                str(item.get("filename"))
                for item in state.get("models", [])
                if isinstance(item, dict)
            }
            final_filename = _unique_filename(filename, existing_names)
            tmp_path = _model_file_path(runtime_cfg, final_filename + ".part")
            final_path = _model_file_path(runtime_cfg, final_filename)
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            total_received = 0
            error_reason: str | None = None
            upload_completed = False

            def _cleanup_partial_upload() -> None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not delete partial upload file: %s", tmp_path, exc_info=True)

            try:
                with tmp_path.open("wb") as handle:
                    async for chunk in request.stream():
                        if app.state.model_upload_cancel_requested:
                            error_reason = "upload_cancelled"
                            break
                        if not chunk:
                            continue
                        total_received += len(chunk)
                        if max_upload_bytes is not None and total_received > max_upload_bytes:
                            error_reason = "upload_too_large"
                            break
                        handle.write(chunk)
                        state_total = app.state.model_upload_state.get("bytes_total") or 0
                        percent = int((total_received * 100 / state_total)) if state_total else 0
                        app.state.model_upload_state.update(
                            {
                                "bytes_received": total_received,
                                "percent": max(0, min(100, percent)),
                            }
                        )

                if error_reason == "upload_too_large":
                    _cleanup_partial_upload()
                    return JSONResponse(status_code=413, content={"uploaded": False, "reason": error_reason})
                if error_reason == "upload_cancelled":
                    _cleanup_partial_upload()
                    return JSONResponse(status_code=200, content={"uploaded": False, "reason": error_reason})
                if total_received <= 0:
                    error_reason = "upload_empty"
                    _cleanup_partial_upload()
                    return JSONResponse(status_code=400, content={"uploaded": False, "reason": error_reason})

                tmp_path.replace(final_path)
                models = state.get("models", [])
                if not isinstance(models, list):
                    error_reason = "models_state_invalid"
                    _cleanup_partial_upload()
                    return JSONResponse(status_code=500, content={"uploaded": False, "reason": error_reason})
                existing_ids = {
                    str(item.get("id"))
                    for item in models
                    if isinstance(item, dict)
                }
                model_id = _unique_model_id(_slugify_id(Path(final_filename).stem), existing_ids)
                record = {
                    "id": model_id,
                    "filename": final_filename,
                    "source_url": None,
                    "source_type": "upload",
                    "status": "ready",
                    "error": None,
                }
                models.append(record)
                state["active_model_id"] = model_id
                saved = save_models_state(runtime_cfg, state)
                runtime_cfg.model_path = final_path
                restarted, _reason = await restart_managed_llama_process(app)
                app.state.model_upload_state.update(
                    {
                        "active": False,
                        "error": None,
                        "model_id": model_id,
                        "bytes_total": total_received,
                        "bytes_received": total_received,
                        "percent": 100,
                    }
                )
                upload_completed = True
                model = get_model_by_id(saved, model_id)
                compatibility = build_large_model_compatibility(
                    runtime_cfg,
                    model_filename=final_filename,
                    model_size_bytes=max(total_received, declared_total or 0) or None,
                )
                response_payload: dict[str, Any] = {
                    "uploaded": True,
                    "model": model,
                    "switched": True,
                    "restarted": restarted,
                }
                warnings = compatibility.get("warnings")
                if isinstance(warnings, list) and warnings:
                    response_payload["warnings"] = warnings
                return JSONResponse(
                    status_code=200,
                    content=response_payload,
                )
            except OSError:
                if error_reason is None:
                    error_reason = "upload_write_failed"
                _cleanup_partial_upload()
                logger.warning("Model upload failed during file write: %s", final_filename, exc_info=True)
                return JSONResponse(status_code=500, content={"uploaded": False, "reason": error_reason})
            except Exception:
                _cleanup_partial_upload()
                raise
            finally:
                if not upload_completed:
                    app.state.model_upload_state.update(
                        {
                            "active": False,
                            "model_id": None,
                            "error": error_reason,
                        }
                    )

    @app.post("/internal/models/cancel-upload")
    async def cancel_model_upload(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"cancelled": False, "reason": "orchestrator_disabled"},
            )
        if not app.state.model_upload_state.get("active"):
            return JSONResponse(status_code=200, content={"cancelled": False, "reason": "not_running"})
        app.state.model_upload_cancel_requested = True
        return JSONResponse(status_code=200, content={"cancelled": True, "reason": "cancel_requested"})

    @app.post("/internal/reset-runtime")
    async def reset_runtime_now(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"started": False, "reason": "orchestrator_disabled"},
            )

        started, reason = await start_runtime_reset(runtime_cfg)
        status_code = 202 if started else 200
        return JSONResponse(status_code=status_code, content={"started": started, "reason": reason})

    @app.post("/internal/cancel-llama")
    async def cancel_llama(runtime_cfg: RuntimeConfig = Depends(get_runtime)) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"cancelled": False, "restarted": False, "reason": "orchestrator_disabled"},
            )

        cancelled, action = await request_llama_slot_cancel(runtime_cfg)
        if cancelled:
            return JSONResponse(
                status_code=200,
                content={"cancelled": True, "restarted": False, "method": f"slot:{action}"},
            )

        return JSONResponse(
            status_code=200,
            content={"cancelled": False, "restarted": False, "method": "none", "reason": "slot_action_unavailable"},
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
        chat_repository: ChatRepositoryManager = Depends(get_chat_repository),
    ) -> Response:
        download_active, auto_start_remaining = get_status_download_context(request.app, runtime_cfg)
        status_payload = await build_status(
            runtime_cfg,
            app=request.app,
            download_active=download_active,
            auto_start_remaining_seconds=auto_start_remaining,
            system_snapshot=request.app.state.system_metrics_snapshot,
        )
        if status_payload["state"] != "READY":
            return JSONResponse(status_code=503, content=status_payload)

        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

        payload = _merge_active_model_chat_defaults(payload, runtime=runtime_cfg)
        payload = _merge_defaults(payload)
        payload = apply_model_chat_defaults(
            payload,
            active_model_filename=str(status_payload.get("model", {}).get("filename") or ""),
        )
        headers = _forward_headers(request)
        active_backend = status_payload["backend"]["active"]

        try:
            backend_response = await chat_repository.create_chat_completion(
                backend=active_backend,
                payload=payload,
                forward_headers=headers,
            )
        except BackendProxyError as exc:
            if runtime_cfg.chat_backend_mode == "auto" and active_backend == "llama":
                backend_response = await chat_repository.create_chat_completion(
                    backend="fake",
                    payload=payload,
                    forward_headers=headers,
                )
            else:
                logger.exception("Backend proxy error")
                raise HTTPException(status_code=502, detail=f"backend unavailable: {exc}") from exc

        if backend_response.stream is not None:
            return StreamingResponse(
                backend_response.stream,
                status_code=backend_response.status_code,
                headers=backend_response.headers,
                background=backend_response.background,
            )

        return Response(
            content=backend_response.body or b"",
            status_code=backend_response.status_code,
            headers=backend_response.headers,
        )

    return app


app = create_app()
