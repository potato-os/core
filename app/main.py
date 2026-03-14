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
        find_llama_runtime_bundle_by_path,
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
        find_llama_runtime_bundle_by_path,
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
# Temporarily pause implicit bootstrap model downloads until the new model-first
# settings flow is in place. Manual downloads remain supported.
AUTO_DOWNLOAD_BOOTSTRAP_ENABLED = False
LLAMA_READY_HEALTH_POLLS_REQUIRED = 2

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


async def restart_managed_llama_process(app: FastAPI) -> tuple[bool, str]:
    try:
        current_model_path = getattr(app.state.runtime, "model_path", None)
    except Exception:
        current_model_path = None
    reset_llama_readiness_state(app, model_path=current_model_path, reason="restart_requested")
    proc = app.state.llama_process
    terminated_running = False

    if proc is not None and proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=3.0)
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
    download_payload["countdown_enabled"] = bool(models_state.get("countdown_enabled", True)) and AUTO_DOWNLOAD_BOOTSTRAP_ENABLED
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

            if should_auto_start_download(
                runtime,
                model_present=default_model_present,
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


CHAT_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Potato OS Chat</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --bg-grad-a: rgba(43, 136, 255, 0.18);
      --bg-grad-b: rgba(18, 206, 193, 0.15);
      --panel: #ffffff;
      --panel-muted: #f1f4f9;
      --text: #142033;
      --text-muted: #5f6f86;
      --border: #d7dfeb;
      --user-bg: linear-gradient(135deg, #1e67de, #2a82f1);
      --user-text: #ffffff;
      --assistant-bg: #ffffff;
      --assistant-text: #142033;
      --composer-bg: #ffffff;
      --status-bg: #edf4ff;
      --status-text: #215b9a;
      --shadow: 0 18px 45px rgba(19, 37, 74, 0.11);
      --shadow-soft: 0 8px 26px rgba(19, 37, 74, 0.08);
      --focus: #2f7cf6;
      --metric-normal: #142033;
      --metric-warn: #b57d00;
      --metric-high: #d95e00;
      --metric-critical: #c81e1e;
    }

    :root[data-theme="dark"] {
      --bg: #081425;
      --bg-grad-a: rgba(41, 147, 251, 0.2);
      --bg-grad-b: rgba(16, 185, 129, 0.16);
      --panel: #0e1f35;
      --panel-muted: #10243c;
      --text: #e7eefb;
      --text-muted: #a6b6d0;
      --border: #233a59;
      --user-bg: linear-gradient(135deg, #2f7cf6, #17a5f2);
      --user-text: #f6fbff;
      --assistant-bg: #112944;
      --assistant-text: #e7eefb;
      --composer-bg: #0f223a;
      --status-bg: #132e4b;
      --status-text: #d1e6ff;
      --shadow: 0 20px 48px rgba(0, 0, 0, 0.38);
      --shadow-soft: 0 10px 28px rgba(0, 0, 0, 0.24);
      --focus: #69adff;
      --metric-normal: #f8fbff;
      --metric-warn: #ffd86b;
      --metric-high: #ffb067;
      --metric-critical: #ff7f7f;
    }

    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      font-family: "Manrope", "Avenir Next", "SF Pro Display", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 16% -5%, var(--bg-grad-a), transparent 42%),
        radial-gradient(circle at 88% -10%, var(--bg-grad-b), transparent 36%),
        var(--bg);
      color: var(--text);
    }

    .app-shell {
      min-height: 100%;
      display: grid;
      grid-template-columns: 340px minmax(0, 1fr);
    }

    .sidebar-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(5, 12, 22, 0.52);
      opacity: 0;
      pointer-events: none;
      transition: opacity 180ms ease;
      z-index: 32;
    }

    body.sidebar-open {
      overflow: hidden;
    }

    body.sidebar-open .sidebar-backdrop {
      opacity: 1;
      pointer-events: auto;
    }

    .sidebar {
      border-right: 1px solid var(--border);
      padding: 20px 16px;
      background: color-mix(in srgb, var(--panel-muted) 72%, transparent);
      display: flex;
      flex-direction: column;
      gap: 14px;
      max-height: 100vh;
      overflow: auto;
    }

    .brand {
      font-size: 20px;
      font-weight: 780;
      letter-spacing: 0.4px;
      margin: 0;
    }

    .sidebar-mobile-actions {
      display: none;
    }

    .sidebar-close {
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--panel);
      color: var(--text);
      font-size: 13px;
      font-weight: 600;
      padding: 6px 12px;
      cursor: pointer;
    }

    .sidebar-note {
      color: var(--text-muted);
      font-size: 14px;
      line-height: 1.4;
      margin: 0;
    }

    .sidebar-section {
      border: 1px solid var(--border);
      border-radius: 16px;
      background: var(--panel);
      padding: 14px;
      box-shadow: var(--shadow-soft);
    }

    .sidebar-section h3 {
      margin: 0 0 8px;
      font-size: 13px;
      letter-spacing: 0.3px;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
    }

    .status-card {
      border: 1px solid var(--border);
      background: var(--status-bg);
      color: var(--status-text);
      border-radius: 12px;
      padding: 12px;
      font-size: 13px;
      line-height: 1.4;
    }

    .status-summary {
      display: grid;
      gap: 8px;
    }

    .status-actions {
      display: flex;
      justify-content: flex-end;
    }

    .status-actions[hidden] {
      display: none;
    }

    .runtime-card {
      margin-top: 10px;
      background: color-mix(in srgb, var(--panel) 90%, var(--status-bg));
      color: var(--text);
    }

    .runtime-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
      font-size: 13px;
      font-weight: 680;
    }

    .runtime-toggle {
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--panel);
      color: var(--text-muted);
      font-size: 11px;
      font-weight: 600;
      padding: 4px 8px;
      cursor: pointer;
    }

    .runtime-toggle:hover {
      color: var(--text);
      border-color: color-mix(in srgb, var(--accent) 45%, var(--border));
    }

    .runtime-compact {
      font-size: 11.5px;
      line-height: 1.45;
      color: var(--text-muted);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .runtime-details {
      margin-top: 8px;
      display: grid;
      gap: 10px;
    }

    .runtime-details[hidden] {
      display: none;
    }

    .runtime-detail-group {
      display: grid;
      gap: 6px;
      border: 1px solid color-mix(in srgb, var(--border) 88%, transparent);
      background: color-mix(in srgb, var(--panel-muted) 72%, transparent);
      border-radius: 12px;
      padding: 10px 11px;
    }

    .runtime-detail-group--power {
      gap: 4px;
      background: color-mix(in srgb, var(--status-bg) 56%, var(--panel));
      border-color: color-mix(in srgb, var(--focus) 24%, var(--border));
    }

    .runtime-detail-group-title {
      font-size: 11px;
      line-height: 1.2;
      letter-spacing: 0.04em;
      color: var(--text-muted);
      font-weight: 700;
    }

    .runtime-detail-prominent {
      font-size: 15px;
      line-height: 1.35;
      font-weight: 760;
      color: var(--text);
      margin-bottom: 1px;
    }

    .runtime-detail-secondary {
      font-size: 12px;
      line-height: 1.4;
      color: var(--text-muted);
    }

    .runtime-detail-row {
      display: grid;
      grid-template-columns: minmax(92px, 132px) 1fr;
      gap: 10px;
      align-items: baseline;
      font-size: 12.5px;
      line-height: 1.4;
    }

    .runtime-detail-label {
      color: var(--text-muted);
      font-weight: 600;
    }

    .runtime-detail-value {
      color: var(--text);
      min-width: 0;
      overflow-wrap: anywhere;
    }

    .runtime-metric-normal {
      color: var(--metric-normal);
    }

    .runtime-metric-warn {
      color: var(--metric-warn);
    }

    .runtime-metric-high {
      color: var(--metric-high);
    }

    .runtime-metric-critical {
      color: var(--metric-critical);
      font-weight: 680;
    }

    .download-prompt {
      margin-top: 10px;
      border: 1px solid color-mix(in srgb, var(--accent) 42%, var(--border));
      background: color-mix(in srgb, var(--panel) 92%, var(--status-bg));
      border-radius: 12px;
      padding: 10px;
      display: grid;
      gap: 8px;
    }

    .download-prompt[hidden] {
      display: none;
    }

    .download-prompt-title {
      margin: 0;
      font-size: 13px;
      font-weight: 680;
      color: var(--text);
    }

    .download-prompt-hint {
      margin: 0;
      font-size: 12px;
      color: var(--text-muted);
      line-height: 1.35;
    }

    .download-prompt-actions {
      display: inline-flex;
      justify-content: flex-start;
    }

    .indicator-dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      box-shadow: 0 0 0 2px rgba(0, 0, 0, 0.12) inset;
      background: #ef4444;
      flex: 0 0 auto;
    }

    .indicator-dot.online {
      background: #22c55e;
    }

    .indicator-dot.loading {
      background: #f59e0b;
    }

    .indicator-dot.failed {
      background: #dc2626;
    }

    .indicator-dot.offline {
      background: #ef4444;
    }

    .theme-toggle {
      position: static;
      width: 44px;
      height: 44px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--text);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      box-shadow: var(--shadow);
      transition: transform 160ms ease, background-color 160ms ease;
    }

    .theme-toggle:hover {
      transform: translateY(-1px);
    }

    .theme-toggle .theme-icon {
      width: 20px;
      height: 20px;
      stroke: currentColor;
      fill: none;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .theme-toggle .theme-icon--sun { display: none; }
    :root[data-theme="light"] .theme-toggle .theme-icon--sun { display: block; }
    :root[data-theme="light"] .theme-toggle .theme-icon--moon { display: none; }
    :root[data-theme="dark"] .theme-toggle .theme-icon--moon { display: block; }
    :root[data-theme="dark"] .theme-toggle .theme-icon--sun { display: none; }

    .theme-toggle::after {
      content: "";
      position: absolute;
      inset: 5px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.06);
      pointer-events: none;
    }

    .chat-shell {
      max-width: 1100px;
      width: 100%;
      margin: 0 auto;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 14px;
      padding: 24px 22px;
      min-height: 100%;
    }

    .chat-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 2px 6px;
    }

    .chat-header h1 {
      margin: 0;
      font-size: 24px;
      font-weight: 760;
      letter-spacing: 0.2px;
    }

    .chat-brand-mark {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 34px;
      height: 34px;
      border-radius: 999px;
      background: color-mix(in srgb, #2563eb 14%, var(--panel));
      border: 1px solid color-mix(in srgb, #2563eb 26%, var(--border));
      font-size: 18px;
      line-height: 1;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.25);
    }

    .header-primary {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }

    .sidebar-toggle {
      display: none;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--text);
      border-radius: 10px;
      width: 38px;
      height: 38px;
      cursor: pointer;
      padding: 0;
      line-height: 1;
      font-size: 18px;
      box-shadow: var(--shadow-soft);
    }

    .sidebar-toggle .bars {
      font-weight: 700;
      transform: translateY(-1px);
    }

    .header-actions {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      margin-left: auto;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      font-size: 12px;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 5px 10px;
      color: var(--text-muted);
      background: var(--panel);
      font-weight: 600;
      letter-spacing: 0.2px;
    }

    .badge.online {
      color: #0f766e;
      border-color: rgba(16, 185, 129, 0.45);
      background: rgba(16, 185, 129, 0.12);
    }

    .badge.loading {
      color: #92400e;
      border-color: rgba(245, 158, 11, 0.45);
      background: rgba(245, 158, 11, 0.12);
    }

    .badge.failed {
      color: #7f1d1d;
      border-color: rgba(220, 38, 38, 0.5);
      background: rgba(220, 38, 38, 0.14);
    }

    .badge.offline {
      color: #991b1b;
      border-color: rgba(239, 68, 68, 0.45);
      background: rgba(239, 68, 68, 0.12);
    }

    .messages {
      background: color-mix(in srgb, var(--panel) 96%, transparent);
      border-radius: 20px;
      border: 1px solid var(--border);
      padding: 16px 14px;
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
      min-height: 320px;
      max-height: min(62vh, 680px);
      box-shadow: var(--shadow-soft);
    }

    .message-row {
      display: flex;
      width: 100%;
    }

    .message-row.user { justify-content: flex-end; }
    .message-row.assistant { justify-content: flex-start; }

    .message-stack {
      max-width: min(82ch, 86%);
      display: flex;
      flex-direction: column;
      gap: 6px;
      position: relative;
    }

    .message-bubble {
      border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
      border-radius: 18px;
      padding: 13px 15px;
      white-space: pre-wrap;
      line-height: 1.5;
      font-size: 15px;
      box-shadow: 0 3px 14px rgba(16, 23, 42, 0.06);
      cursor: text;
      user-select: text;
      -webkit-user-select: text;
    }

    .message-actions {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      align-self: flex-end;
      margin-top: -2px;
      opacity: 0;
      transform: translateY(-2px);
      pointer-events: none;
      transition: opacity 120ms ease, transform 120ms ease;
    }

    .message-row.message-row-actions-hidden .message-actions {
      display: none !important;
    }

    .message-stack:hover .message-actions,
    .message-stack:focus-within .message-actions,
    .message-actions[data-visible="true"] {
      opacity: 1;
      transform: translateY(0);
      pointer-events: auto;
    }

    .message-action-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      border-radius: 10px;
      border: 1px solid color-mix(in srgb, var(--border) 75%, transparent);
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      color: var(--text-muted);
      cursor: pointer;
      box-shadow: 0 4px 10px rgba(15, 23, 42, 0.08);
      transition: transform 120ms ease, color 120ms ease, border-color 120ms ease, background-color 120ms ease;
    }

    .message-action-btn:hover,
    .message-action-btn:focus-visible {
      color: var(--text);
      border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
      background: color-mix(in srgb, var(--panel-muted) 82%, transparent);
      transform: translateY(-1px);
      outline: none;
    }

    .message-action-btn[data-copied="true"] {
      color: #0f766e;
      border-color: rgba(16, 163, 127, 0.38);
      background: rgba(16, 163, 127, 0.12);
    }

    .message-action-btn svg {
      width: 16px;
      height: 16px;
      stroke: currentColor;
      fill: none;
      stroke-width: 1.9;
      stroke-linecap: round;
      stroke-linejoin: round;
      pointer-events: none;
    }

    .message-bubble.processing {
      background: color-mix(in srgb, var(--assistant-bg) 90%, var(--panel-muted));
      border-color: color-mix(in srgb, #60a5fa 20%, var(--border));
      box-shadow: 0 8px 20px rgba(37, 99, 235, 0.06);
      padding: 10px 12px;
    }

    .message-bubble.processing[data-phase="generating"] {
      border-color: color-mix(in srgb, #10a37f 28%, var(--border));
      box-shadow: 0 8px 20px rgba(16, 163, 127, 0.08);
    }

    .message-bubble.with-image {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .message-bubble.markdown-rendered {
      white-space: normal;
    }

    .message-bubble.markdown-rendered > *:first-child {
      margin-top: 0;
    }

    .message-bubble.markdown-rendered > *:last-child {
      margin-bottom: 0;
    }

    .message-bubble.markdown-rendered p,
    .message-bubble.markdown-rendered ul,
    .message-bubble.markdown-rendered ol,
    .message-bubble.markdown-rendered pre,
    .message-bubble.markdown-rendered blockquote,
    .message-bubble.markdown-rendered h1,
    .message-bubble.markdown-rendered h2,
    .message-bubble.markdown-rendered h3,
    .message-bubble.markdown-rendered h4 {
      margin: 0 0 0.8em;
    }

    .message-bubble.markdown-rendered h1,
    .message-bubble.markdown-rendered h2,
    .message-bubble.markdown-rendered h3,
    .message-bubble.markdown-rendered h4 {
      line-height: 1.2;
      letter-spacing: -0.02em;
    }

    .message-bubble.markdown-rendered h1 {
      font-size: 1.22em;
    }

    .message-bubble.markdown-rendered h2 {
      font-size: 1.12em;
    }

    .message-bubble.markdown-rendered ul,
    .message-bubble.markdown-rendered ol {
      padding-left: 1.2em;
    }

    .message-bubble.markdown-rendered li + li {
      margin-top: 0.28em;
    }

    .message-bubble.markdown-rendered code {
      font-family: "JetBrains Mono", "SFMono-Regular", ui-monospace, monospace;
      font-size: 0.92em;
      background: color-mix(in srgb, var(--panel-muted) 82%, transparent);
      border-radius: 7px;
      padding: 0.12em 0.38em;
    }

    .message-bubble.markdown-rendered pre {
      overflow: auto;
      padding: 0.8em 0.9em;
      border-radius: 12px;
      background: color-mix(in srgb, var(--panel-muted) 90%, transparent);
      border: 1px solid color-mix(in srgb, var(--border) 75%, transparent);
    }

    .message-bubble.markdown-rendered pre code {
      padding: 0;
      border-radius: 0;
      background: transparent;
    }

    .message-bubble.markdown-rendered blockquote {
      border-left: 3px solid color-mix(in srgb, var(--brand) 42%, var(--border));
      padding-left: 0.85em;
      color: var(--text-muted);
    }

    .message-bubble.markdown-rendered a {
      color: inherit;
      text-decoration-thickness: 0.08em;
      text-underline-offset: 0.14em;
    }

    .message-image-thumb {
      display: block;
      width: min(100%, 320px);
      max-height: 220px;
      object-fit: cover;
      border-radius: 10px;
      border: 1px solid rgba(15, 23, 42, 0.12);
      background: rgba(15, 23, 42, 0.06);
    }

    .message-text {
      white-space: pre-wrap;
      line-height: inherit;
    }

    .message-processing-shell {
      display: grid;
      gap: 7px;
    }

    .message-processing-label {
      font-size: 13.5px;
      line-height: 1.25;
      font-weight: 700;
      color: var(--text);
    }

    .message-processing-meter {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
    }

    .message-processing-bar {
      position: relative;
      height: 8px;
      overflow: hidden;
      border-radius: 999px;
      background: color-mix(in srgb, var(--border) 60%, transparent);
    }

    .message-processing-bar-fill {
      position: absolute;
      inset: 0 auto 0 0;
      width: 0%;
      border-radius: inherit;
      background: linear-gradient(90deg, #60a5fa 0%, #10a37f 100%);
      transition: width 180ms ease;
    }

    .message-bubble.processing[data-phase="generating"] .message-processing-bar-fill {
      width: 38%;
      animation: message-processing-indeterminate 1.2s ease-in-out infinite;
    }

    .message-processing-percent {
      font-size: 11.5px;
      line-height: 1;
      font-weight: 700;
      color: var(--text);
      letter-spacing: 0.02em;
    }

    .message-meta {
      color: var(--text-muted);
      font-size: 12.5px;
      line-height: 1.35;
      padding: 0 4px;
      user-select: text;
      -webkit-user-select: text;
    }

    .message-bubble *,
    .message-text {
      user-select: text;
      -webkit-user-select: text;
    }

    .message-row.user .message-meta {
      text-align: right;
    }

    .message-row.user .message-bubble {
      background: var(--user-bg);
      color: var(--user-text);
      border-color: transparent;
      border-bottom-right-radius: 5px;
    }

    .message-row.assistant .message-bubble {
      background: var(--assistant-bg);
      color: var(--assistant-text);
      border-bottom-left-radius: 5px;
    }

    .composer {
      border: 1px solid var(--border);
      border-radius: 18px;
      background: var(--composer-bg);
      padding: 14px;
      box-shadow: var(--shadow-soft);
    }

    .composer textarea {
      width: 100%;
      border: none;
      resize: vertical;
      min-height: 68px;
      max-height: 240px;
      background: transparent;
      color: var(--text);
      font: inherit;
      outline: none;
      font-size: 15px;
    }

    .composer-bottom {
      margin-top: 12px;
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: end;
      gap: 12px;
    }

    .composer-left {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      min-width: 0;
      overflow: hidden;
    }

    .composer-right {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      justify-content: flex-end;
    }

    .visually-hidden-file {
      display: none;
    }

    .attach-btn,
    .ghost-btn {
      border: 1px solid var(--border);
      background: var(--panel-muted);
      color: var(--text);
      border-radius: 999px;
      padding: 7px 13px;
      font-size: 13px;
      cursor: pointer;
      transition: transform 120ms ease, border-color 120ms ease, background-color 120ms ease, opacity 120ms ease;
    }

    .attach-btn {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      font-weight: 600;
      user-select: none;
    }

    .attach-btn::before {
      content: "+";
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 16px;
      height: 16px;
      border-radius: 999px;
      background: rgba(16, 163, 127, 0.16);
      color: #0f766e;
      font-size: 12px;
      line-height: 1;
      font-weight: 700;
    }

    .attach-btn.selected {
      border-color: rgba(16, 163, 127, 0.45);
      background: rgba(16, 163, 127, 0.14);
    }

    .attach-btn:hover,
    .ghost-btn:hover {
      transform: translateY(-1px);
    }

    .attach-btn:disabled,
    .ghost-btn:disabled {
      opacity: 0.55;
      cursor: not-allowed;
      transform: none;
    }

    .image-meta {
      font-size: 12px;
      color: var(--text-muted);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 5px 10px;
      background: var(--panel-muted);
    }

    .composer-vision-notice {
      margin-top: 8px;
      font-size: 12px;
      line-height: 1.45;
      color: var(--text-muted);
    }

    .composer-vision-notice[hidden] {
      display: none !important;
    }

    .image-preview-wrap {
      margin-top: 10px;
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      max-width: 220px;
      background: var(--panel-muted);
    }

    .image-preview-wrap img {
      display: block;
      width: 100%;
      height: auto;
      max-height: 160px;
      object-fit: cover;
    }

    .composer-status-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      border: 1px solid color-mix(in srgb, #10a37f 35%, var(--border));
      background: color-mix(in srgb, #10a37f 14%, var(--panel-muted));
      color: color-mix(in srgb, var(--text) 78%, #0f766e 22%);
      font-size: 13px;
      line-height: 1;
      font-weight: 650;
      padding: 7px 10px;
      min-height: 34px;
      max-width: min(48vw, 340px);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .composer-status-chip[hidden] {
      display: none !important;
    }

    .composer-status-chip[data-phase="generating"] {
      border-color: color-mix(in srgb, #60a5fa 44%, var(--border));
      background: color-mix(in srgb, #60a5fa 13%, var(--panel-muted));
      color: color-mix(in srgb, var(--text) 82%, #1d4ed8 18%);
    }

    .composer-status-chip[data-phase="cancel"] {
      border-color: color-mix(in srgb, #f97316 46%, var(--border));
      background: color-mix(in srgb, #f97316 16%, var(--panel-muted));
      color: color-mix(in srgb, var(--text) 84%, #c2410c 16%);
    }

    .chip-spinner {
      width: 14px;
      height: 14px;
      border-radius: 999px;
      border: 2px solid color-mix(in srgb, currentColor 20%, transparent);
      border-top-color: currentColor;
      animation: chip-spin 0.9s linear infinite;
      flex: 0 0 auto;
    }

    .chip-cancel-btn {
      border: 1px solid color-mix(in srgb, currentColor 32%, transparent);
      background: transparent;
      color: inherit;
      width: 22px;
      height: 22px;
      border-radius: 999px;
      cursor: pointer;
      font-size: 15px;
      line-height: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      flex: 0 0 auto;
    }

    .chip-cancel-btn:hover {
      background: color-mix(in srgb, currentColor 12%, transparent);
    }

    .assistive-live {
      position: absolute;
      width: 1px;
      height: 1px;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      border: 0;
      white-space: nowrap;
    }

    .send-btn {
      border: none;
      border-radius: 999px;
      background: #10a37f;
      color: #ffffff;
      font-weight: 600;
      cursor: pointer;
      padding: 10px 18px;
      min-width: 96px;
    }

    .send-btn.stop-mode {
      background: #dc2626;
    }

    .send-btn:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }

    @keyframes chip-spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }

    @keyframes message-processing-indeterminate {
      0% { transform: translateX(-18%); }
      50% { transform: translateX(112%); }
      100% { transform: translateX(-18%); }
    }

    .settings-grid {
      display: grid;
      gap: 12px;
      grid-template-columns: 1fr;
    }

    .settings-grid label {
      font-size: 12.5px;
      color: var(--text-muted);
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
    }

    .settings-utility-card > label,
    .settings-chat-panel > label.full,
    .settings-field-grid > label {
      display: grid;
      gap: 8px;
      align-content: start;
      min-width: 0;
      padding: 12px 14px;
      border-radius: 18px;
      border: 1px solid color-mix(in srgb, var(--border) 58%, transparent);
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--panel) 90%, white 10%), color-mix(in srgb, var(--panel-muted) 84%, transparent));
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
      font-size: 12.5px;
      font-weight: 700;
      letter-spacing: 0.01em;
      color: var(--text-muted);
    }

    .settings-field-label {
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }

    .settings-field-hint {
      font-size: 11px;
      font-weight: 600;
      color: color-mix(in srgb, var(--text-muted) 90%, transparent);
      letter-spacing: 0;
    }

    .settings-chat-panel > label.full {
      padding: 14px 16px;
    }

    .settings-grid input,
    .settings-grid select,
    .settings-grid textarea {
      border: 1px solid color-mix(in srgb, var(--border) 68%, transparent);
      border-radius: 14px;
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--panel) 92%, white 8%), color-mix(in srgb, var(--panel-muted) 86%, transparent));
      color: var(--text);
      padding: 10px 12px;
      font: inherit;
      width: 100%;
      max-width: 100%;
      min-width: 0;
      box-sizing: border-box;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
      transition: border-color 140ms ease, box-shadow 160ms ease, background 160ms ease, transform 140ms ease;
    }

    .settings-utility-card input:not([type="file"]),
    .settings-utility-card select,
    .settings-utility-card textarea,
    .settings-chat-panel input:not([type="checkbox"]):not([type="file"]),
    .settings-chat-panel select,
    .settings-chat-panel textarea {
      width: 100%;
      min-width: 0;
      border: 1px solid color-mix(in srgb, var(--border) 64%, transparent);
      border-radius: 14px;
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--panel) 94%, white 6%), color-mix(in srgb, var(--panel-muted) 76%, transparent));
      color: var(--text);
      padding: 11px 13px;
      font: inherit;
      font-size: 14px;
      font-weight: 600;
      line-height: 1.4;
      box-sizing: border-box;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
      transition: border-color 140ms ease, box-shadow 160ms ease, background 160ms ease;
    }

    .settings-utility-card input[type="file"] {
      width: 100%;
      min-width: 0;
      font: inherit;
      color: var(--text);
    }

    .settings-chat-panel textarea,
    .settings-utility-card textarea {
      min-height: 110px;
      resize: vertical;
    }

    .settings-chat-panel select,
    .settings-utility-card select {
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      padding-right: 38px;
      background-image:
        linear-gradient(45deg, transparent 50%, color-mix(in srgb, var(--text-muted) 88%, transparent) 50%),
        linear-gradient(135deg, color-mix(in srgb, var(--text-muted) 88%, transparent) 50%, transparent 50%);
      background-position:
        calc(100% - 20px) calc(50% - 2px),
        calc(100% - 14px) calc(50% - 2px);
      background-size: 6px 6px, 6px 6px;
      background-repeat: no-repeat;
    }

    .settings-segmented {
      display: inline-grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 4px;
      padding: 4px;
      border-radius: 999px;
      border: 1px solid color-mix(in srgb, var(--border) 62%, transparent);
      background: color-mix(in srgb, var(--panel-muted) 82%, transparent);
      width: fit-content;
      min-width: 100%;
    }

    .settings-segment-btn {
      appearance: none;
      border: none;
      background: transparent;
      color: var(--text-muted);
      border-radius: 999px;
      padding: 9px 12px;
      font: inherit;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: -0.01em;
      cursor: pointer;
      transition: background 140ms ease, color 140ms ease, box-shadow 160ms ease;
    }

    .settings-segment-btn:hover {
      color: var(--text);
    }

    .settings-segment-btn.active {
      background: color-mix(in srgb, var(--panel) 96%, white 4%);
      color: var(--text);
      box-shadow: 0 4px 10px rgba(15, 23, 42, 0.08);
    }

    .settings-segment-btn:focus-visible {
      outline: none;
      box-shadow:
        0 0 0 3px color-mix(in srgb, #f59e0b 12%, transparent),
        0 4px 10px rgba(15, 23, 42, 0.08);
    }

    .settings-grid textarea {
      min-height: 86px;
      resize: vertical;
    }

    .settings-grid input:focus,
    .settings-grid select:focus,
    .settings-grid textarea:focus,
    .settings-utility-card input:not([type="file"]):focus,
    .settings-utility-card select:focus,
    .settings-utility-card textarea:focus,
    .settings-chat-panel input:not([type="checkbox"]):not([type="file"]):focus,
    .settings-chat-panel select:focus,
    .settings-chat-panel textarea:focus,
    .settings-yaml-input:focus,
    .edit-modal-input:focus {
      outline: none;
      border-color: color-mix(in srgb, #f59e0b 44%, var(--border));
      box-shadow:
        0 0 0 4px color-mix(in srgb, #f59e0b 12%, transparent),
        inset 0 1px 0 rgba(255, 255, 255, 0.1);
      background: color-mix(in srgb, var(--panel) 94%, white 6%);
    }

    #seed:disabled {
      background: var(--panel);
      color: var(--text-muted);
      opacity: 0.75;
      cursor: not-allowed;
    }

    .settings-grid .full {
      grid-column: 1 / -1;
    }

    .settings-section {
      display: grid;
      gap: 12px;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: color-mix(in srgb, var(--panel) 72%, var(--panel-muted));
      padding: 13px;
      min-width: 0;
    }

    .settings-section-title {
      margin: 0;
      font-size: 13px;
      color: var(--text-muted);
      letter-spacing: 0.04em;
      font-weight: 800;
      text-transform: none;
    }

    .settings-section-note {
      font-size: 12.5px;
      color: var(--text-muted);
      line-height: 1.5;
    }

    .settings-modal .ghost-btn {
      border-color: color-mix(in srgb, var(--border) 62%, transparent);
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--panel) 92%, white 8%), color-mix(in srgb, var(--panel-muted) 84%, transparent));
      color: var(--text);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
      font-weight: 700;
      letter-spacing: -0.01em;
    }

    .settings-modal .ghost-btn:hover {
      border-color: color-mix(in srgb, #f59e0b 22%, var(--border));
      background:
        linear-gradient(180deg, color-mix(in srgb, #f59e0b 8%, var(--panel)), color-mix(in srgb, var(--panel-muted) 80%, transparent));
      box-shadow: 0 10px 18px rgba(15, 23, 42, 0.07);
    }

    .settings-modal .ghost-btn:disabled {
      opacity: 0.55;
      cursor: not-allowed;
      transform: none;
      box-shadow: none;
    }

    .settings-subdetails {
      border: 1px dashed color-mix(in srgb, var(--border) 88%, transparent);
      border-radius: 10px;
      padding: 10px;
      background: color-mix(in srgb, var(--panel-muted) 65%, transparent);
    }

    .settings-subdetails summary {
      cursor: pointer;
      list-style: none;
      font-size: 12px;
      font-weight: 700;
      color: var(--text);
    }

    .settings-subdetails summary::-webkit-details-marker {
      display: none;
    }

    .settings-subdetails[open] summary {
      margin-bottom: 10px;
    }

    .settings-action-row {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      align-items: stretch;
      min-width: 0;
    }

    .settings-action-row .ghost-btn {
      width: 100%;
      text-align: center;
      box-sizing: border-box;
      min-width: 0;
    }

    .settings-grid > *,
    .settings-section > *,
    .settings-subdetails > *,
    .model-row,
    #modelsList {
      min-width: 0;
    }

    .settings-subdetails {
      min-width: 0;
    }

    .settings-subdetails .settings-action-row {
      margin-top: 4px;
    }

    .sidebar-actions {
      margin-top: 10px;
    }

    .sidebar-settings-btn {
      width: 100%;
      justify-content: center;
      font-weight: 650;
    }

    body.settings-modal-open,
    body.edit-modal-open {
      overflow: hidden;
    }

    .settings-backdrop {
      position: fixed;
      inset: 0;
      display: none;
      background: rgba(15, 23, 42, 0.48);
      backdrop-filter: blur(4px);
      z-index: 42;
    }

    .settings-modal {
      position: fixed;
      inset: 0;
      display: none;
      place-items: center;
      padding: 24px;
      z-index: 43;
      pointer-events: none;
    }

    .settings-backdrop[hidden],
    .settings-modal[hidden],
    .edit-backdrop[hidden],
    .edit-modal[hidden] {
      display: none !important;
      pointer-events: none !important;
    }

    body.settings-modal-open .settings-backdrop,
    body.edit-modal-open .edit-backdrop {
      display: block;
    }

    body.settings-modal-open .settings-modal,
    body.edit-modal-open .edit-modal {
      display: grid;
    }

    .settings-modal-shell {
      width: min(1180px, calc(100vw - 28px));
      max-height: calc(100vh - 28px);
      overflow: auto;
      border: 1px solid color-mix(in srgb, var(--border) 76%, transparent);
      border-radius: 32px;
      background:
        radial-gradient(circle at top left, color-mix(in srgb, #f59e0b 10%, transparent), transparent 32%),
        linear-gradient(180deg, color-mix(in srgb, var(--panel) 95%, white 5%), color-mix(in srgb, var(--panel-muted) 30%, var(--panel) 70%));
      box-shadow: 0 34px 90px rgba(15, 23, 42, 0.22);
      padding: 26px;
      pointer-events: auto;
    }

    .settings-modal-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 20px;
      margin-bottom: 10px;
    }

    .settings-modal-header-actions {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
    }

    .settings-modal-title {
      margin: 0;
      font-size: 28px;
      line-height: 1;
      letter-spacing: -0.03em;
    }

    .settings-modal-note {
      margin: 10px 0 0;
      font-size: 14px;
      line-height: 1.5;
      color: var(--text-muted);
      max-width: 58ch;
    }

    .settings-close-btn {
      flex-shrink: 0;
    }

    .settings-advanced-btn {
      min-width: 0;
      justify-content: center;
      font-size: 13px;
      font-weight: 700;
      line-height: 1;
      padding-inline: 14px;
    }

    body.legacy-settings-modal-open {
      overflow: hidden;
    }

    body.legacy-settings-modal-open .legacy-settings-backdrop {
      display: block;
    }

    body.legacy-settings-modal-open .legacy-settings-modal {
      display: grid;
    }

    .settings-workspace-shell {
      display: grid;
      gap: 20px;
    }

    .settings-workspace-tabs {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
      padding: 6px;
      border: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
      border-radius: 999px;
      background: color-mix(in srgb, var(--panel-muted) 78%, transparent);
      width: fit-content;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
    }

    .settings-workspace-tab {
      border: 1px solid transparent;
      border-radius: 999px;
      background: transparent;
      color: var(--text-muted);
      font: inherit;
      font-weight: 700;
      padding: 10px 16px;
      cursor: pointer;
    }

    .settings-workspace-tab.active {
      background: color-mix(in srgb, var(--panel) 94%, white 6%);
      color: var(--text);
      border-color: color-mix(in srgb, #f59e0b 26%, var(--border));
      box-shadow: 0 8px 16px rgba(15, 23, 42, 0.08);
    }

    .settings-workspace-panel[hidden] {
      display: none !important;
    }

    .settings-workspace-grid {
      display: grid;
      gap: 22px;
      grid-template-columns: minmax(320px, 0.92fr) minmax(0, 1.15fr);
      align-items: start;
    }

    .settings-section {
      gap: 16px;
      border: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
      border-radius: 22px;
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--panel) 94%, white 6%), color-mix(in srgb, var(--panel-muted) 16%, var(--panel) 84%));
      padding: 18px;
      box-shadow: 0 16px 34px rgba(15, 23, 42, 0.06);
    }

    .settings-panel-heading {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 14px;
    }

    .settings-panel-heading-copy {
      display: grid;
      gap: 4px;
    }

    .settings-utility-card {
      display: grid;
      gap: 12px;
      border: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
      border-radius: 18px;
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--panel-muted) 86%, transparent), color-mix(in srgb, var(--panel) 92%, var(--panel-muted) 8%));
      padding: 14px;
    }

    .settings-model-list-wrap {
      border-top: 1px solid color-mix(in srgb, var(--border) 64%, transparent);
      padding-top: 8px;
    }

    .settings-danger-zone {
      margin-top: 4px;
      border-top: 1px solid color-mix(in srgb, var(--border) 58%, transparent);
      padding-top: 10px;
    }

    .settings-danger-zone summary {
      list-style: none;
      cursor: pointer;
      color: var(--text-muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.01em;
      user-select: none;
    }

    .settings-danger-zone summary::-webkit-details-marker {
      display: none;
    }

    .settings-danger-zone summary::before {
      content: "▸";
      display: inline-block;
      margin-right: 8px;
      transition: transform 140ms ease;
    }

    .settings-danger-zone[open] summary::before {
      transform: rotate(90deg);
    }

    .settings-danger-zone-body {
      display: grid;
      gap: 10px;
      margin-top: 12px;
      padding-top: 4px;
    }

    .settings-danger-note {
      font-size: 12px;
      line-height: 1.45;
      color: var(--text-muted);
      margin: 0;
    }

    .settings-model-list {
      display: grid;
      gap: 12px;
      max-height: 56vh;
      overflow: auto;
      padding-right: 4px;
    }

    .selected-model-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 18px;
      padding: 20px;
      border-radius: 22px;
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--panel) 96%, white 4%), color-mix(in srgb, var(--panel-muted) 32%, var(--panel)));
      border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
    }

    .selected-model-header-copy {
      display: grid;
      gap: 8px;
      min-width: 0;
      flex: 1;
    }

    .selected-model-header-actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 10px;
      align-items: center;
    }

    #modelName {
      margin: 0;
      font-size: clamp(19px, 2vw, 24px);
      font-weight: 760;
      letter-spacing: -0.03em;
      line-height: 1.15;
      color: var(--text);
      overflow-wrap: anywhere;
    }

    .selected-model-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 10px;
      align-items: center;
      color: var(--text-muted);
      font-size: 12px;
      line-height: 1.45;
      min-height: 18px;
    }

    .selected-model-meta-item {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    .selected-model-meta-item::before {
      content: "";
      width: 4px;
      height: 4px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--text-muted) 45%, transparent);
      flex: 0 0 auto;
    }

    .settings-save-btn {
      white-space: nowrap;
      font-weight: 700;
      border-color: color-mix(in srgb, #347ae3 22%, var(--border));
      background: color-mix(in srgb, #347ae3 10%, var(--panel));
    }

    .settings-chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      min-height: 28px;
    }

    .settings-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid color-mix(in srgb, var(--border) 86%, transparent);
      background: color-mix(in srgb, var(--panel-muted) 74%, transparent);
      font-size: 12px;
      font-weight: 700;
      color: var(--text-muted);
    }

    .settings-chip[data-kind="active"] {
      border-color: color-mix(in srgb, #10a37f 40%, var(--border));
      background: color-mix(in srgb, #10a37f 14%, var(--panel));
      color: color-mix(in srgb, var(--text) 82%, #10a37f 18%);
    }

    .settings-chip[data-kind="vision"] {
      border-color: color-mix(in srgb, #347ae3 40%, var(--border));
      background: color-mix(in srgb, #347ae3 12%, var(--panel));
      color: color-mix(in srgb, var(--text) 82%, #347ae3 18%);
    }

    .settings-chip[data-kind="status"] {
      border-color: color-mix(in srgb, #f59e0b 38%, var(--border));
      background: color-mix(in srgb, #f59e0b 12%, var(--panel));
      color: color-mix(in srgb, var(--text) 80%, #b45309 20%);
    }

    .settings-chat-panel {
      gap: 16px;
    }

    .settings-field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }

    .settings-field-grid label:first-child,
    .settings-field-grid label:nth-child(2) {
      grid-column: span 1;
    }

    .settings-subpanel {
      border: 1px solid color-mix(in srgb, var(--border) 62%, transparent);
      border-radius: 18px;
      padding: 16px;
      background: color-mix(in srgb, var(--panel-muted) 58%, transparent);
      display: grid;
      gap: 14px;
    }

    .settings-subpanel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }

    .settings-subpanel-title {
      margin: 0;
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0.01em;
      color: var(--text);
    }

    .settings-inline-toggle {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 12.5px;
      color: var(--text-muted);
    }

    .settings-yaml-input {
      width: 100%;
      min-height: 460px;
      resize: vertical;
      border: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
      border-radius: 18px;
      background: color-mix(in srgb, var(--panel-muted) 76%, transparent);
      color: var(--text);
      font: 500 13px/1.5 "SFMono-Regular", "Menlo", "Monaco", monospace;
      padding: 16px;
      box-sizing: border-box;
    }

    .edit-backdrop {
      position: fixed;
      inset: 0;
      display: none;
      background: rgba(15, 23, 42, 0.54);
      backdrop-filter: blur(4px);
      z-index: 44;
    }

    .edit-modal {
      position: fixed;
      inset: 0;
      display: none;
      place-items: center;
      padding: 24px;
      z-index: 45;
      pointer-events: none;
    }

    .edit-modal-shell {
      width: min(760px, calc(100vw - 32px));
      max-height: calc(100vh - 32px);
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 24px;
      background: var(--panel);
      box-shadow: var(--shadow);
      padding: 18px;
      display: grid;
      gap: 14px;
      pointer-events: auto;
    }

    .edit-modal-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
    }

    .edit-modal-title {
      margin: 0;
      font-size: 20px;
      line-height: 1.1;
    }

    .edit-modal-note {
      margin: 6px 0 0;
      font-size: 13px;
      line-height: 1.45;
      color: var(--text-muted);
    }

    .edit-modal-input {
      width: 100%;
      min-height: 200px;
      max-height: 52vh;
      resize: vertical;
      border: 1px solid color-mix(in srgb, var(--border) 64%, transparent);
      border-radius: 22px;
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--composer-bg) 94%, white 6%), color-mix(in srgb, var(--panel-muted) 62%, var(--composer-bg)));
      color: var(--text);
      font: inherit;
      font-size: 16px;
      line-height: 1.5;
      padding: 18px 20px;
      box-sizing: border-box;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
    }

    .edit-modal-actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }

    .edit-modal-hint {
      font-size: 12.5px;
      line-height: 1.4;
      color: var(--text-muted);
    }

    .edit-modal-action-row {
      display: inline-flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      flex-wrap: wrap;
    }

    .edit-send-btn {
      min-width: 118px;
    }

    .edit-modal .ghost-btn,
    .edit-modal .primary-btn {
      border-radius: 999px;
      padding: 10px 16px;
      font-weight: 700;
      letter-spacing: -0.01em;
    }

    .edit-modal .ghost-btn {
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--panel) 90%, white 10%), color-mix(in srgb, var(--panel-muted) 84%, transparent));
      border-color: color-mix(in srgb, var(--border) 62%, transparent);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
    }

    .edit-modal .primary-btn {
      border: 1px solid color-mix(in srgb, #f59e0b 26%, #10a37f 44%);
      background: linear-gradient(135deg, #f59e0b, #f97316);
      color: #fffaf2;
      box-shadow: 0 12px 24px rgba(249, 115, 22, 0.22);
    }

    .model-row-actions {
      display: grid;
      grid-template-columns: 1fr;
      gap: 6px;
    }

    .model-row-actions .ghost-btn {
      width: 100%;
      min-width: 0;
      justify-content: center;
      box-sizing: border-box;
    }

    .model-row {
      border: 1px solid color-mix(in srgb, var(--border) 68%, transparent);
      border-radius: 18px;
      background:
        linear-gradient(180deg, color-mix(in srgb, var(--panel) 92%, white 8%), color-mix(in srgb, var(--panel-muted) 10%, var(--panel) 90%));
      padding: 14px;
      display: grid;
      gap: 10px;
      cursor: pointer;
      transition: border-color 140ms ease, transform 140ms ease, box-shadow 180ms ease, background 180ms ease;
    }

    .model-row.selected {
      border-color: color-mix(in srgb, #f59e0b 30%, var(--border));
      box-shadow:
        inset 0 0 0 1px color-mix(in srgb, #f59e0b 18%, transparent),
        0 16px 32px rgba(15, 23, 42, 0.10);
      transform: translateY(-1px);
      background:
        linear-gradient(135deg, color-mix(in srgb, #f59e0b 8%, var(--panel)) 0%, color-mix(in srgb, var(--panel-muted) 88%, transparent) 42%, color-mix(in srgb, #2f7cf6 5%, var(--panel)) 100%);
    }

    .model-row:hover {
      border-color: color-mix(in srgb, #f59e0b 18%, var(--border));
      box-shadow: 0 14px 28px rgba(15, 23, 42, 0.08);
      transform: translateY(-1px);
    }

    .model-row-head {
      font-size: 12px;
      color: var(--text);
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
    }

    .model-row-name {
      font-weight: 800;
      letter-spacing: -0.01em;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }

    .model-row-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }

    .model-mini-chip {
      display: inline-flex;
      align-items: center;
      padding: 4px 9px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--panel-muted) 82%, transparent);
      border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
      color: var(--text-muted);
      font-size: 11px;
      font-weight: 700;
    }

    .model-status-pill {
      border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
      border-radius: 999px;
      padding: 5px 10px;
      font-size: 11px;
      color: var(--text-muted);
      background: color-mix(in srgb, var(--panel-muted) 78%, transparent);
      white-space: nowrap;
      font-weight: 700;
    }

    .model-row-actions {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      align-items: center;
      padding-top: 4px;
      border-top: 1px solid color-mix(in srgb, var(--border) 56%, transparent);
    }

    .model-row-actions .ghost-btn {
      width: auto;
      font-size: 12px;
      padding: 7px 10px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--panel-muted) 75%, transparent);
    }

    .danger-btn {
      border-color: color-mix(in srgb, #dc2626 48%, var(--border));
      background: color-mix(in srgb, #dc2626 14%, var(--panel-muted));
      color: color-mix(in srgb, var(--text) 88%, #dc2626 12%);
      font-weight: 600;
    }

    .danger-btn:disabled {
      opacity: 0.55;
      cursor: not-allowed;
      transform: none;
    }

    /* User feedback pass: keep layout cleaner and less flashy. */
    :root {
      --bg: #f3f5f8;
      --bg-grad-a: rgba(52, 122, 227, 0.11);
      --bg-grad-b: rgba(27, 173, 141, 0.08);
      --panel: #ffffff;
      --panel-muted: #f0f3f8;
      --text: #1d2635;
      --text-muted: #5f6b80;
      --border: #d5dce8;
      --user-bg: #2f6fe6;
      --user-text: #ffffff;
      --assistant-bg: #ffffff;
      --assistant-text: #111827;
      --composer-bg: #ffffff;
      --status-bg: #eaf3ff;
      --status-text: #27518a;
      --shadow: 0 12px 28px rgba(15, 23, 42, 0.09);
      --shadow-soft: 0 7px 18px rgba(15, 23, 42, 0.08);
      --focus: #357bff;
    }

    :root[data-theme="dark"] {
      --bg: #0e131b;
      --bg-grad-a: rgba(44, 112, 214, 0.18);
      --bg-grad-b: rgba(30, 154, 126, 0.1);
      --panel: #1f242d;
      --panel-muted: #171c24;
      --text: #e9edf5;
      --text-muted: #a8b1c3;
      --border: #2f3a4d;
      --user-bg: #357bff;
      --user-text: #eff6ff;
      --assistant-bg: #1b212b;
      --assistant-text: #e9edf5;
      --composer-bg: #1f242d;
      --status-bg: #1a2434;
      --status-text: #d5e0f5;
      --shadow: 0 18px 42px rgba(0, 0, 0, 0.35);
      --shadow-soft: 0 10px 22px rgba(0, 0, 0, 0.23);
      --focus: #69a0ff;
    }

    body {
      font-family: "Segoe UI", "SF Pro Text", "Helvetica Neue", sans-serif;
      background: radial-gradient(circle at 20% 0%, var(--bg-grad-a), transparent 40%), var(--bg);
    }

    .app-shell {
      grid-template-columns: 396px minmax(0, 1fr);
    }

    .sidebar {
      padding: 18px 14px;
      background: var(--panel-muted);
      gap: 12px;
      overflow: auto;
      min-width: 0;
    }

    .sidebar-section {
      border: none;
      background: transparent;
      padding: 0;
      box-shadow: none;
    }

    .brand {
      margin: 0;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0.2px;
    }

    .chat-shell {
      max-width: 980px;
      grid-template-rows: auto 1fr auto;
      padding: 22px 20px;
      min-width: 0;
    }

    .messages {
      background: color-mix(in srgb, var(--panel) 96%, transparent);
      border-radius: 18px;
      border: 1px solid var(--border);
      padding: 12px;
      gap: 12px;
      max-height: calc(100vh - 300px);
      box-shadow: var(--shadow-soft);
    }

    .message-stack {
      max-width: min(76ch, 85%);
    }

    .message-bubble {
      border-radius: 16px;
      padding: 12px 14px;
      line-height: 1.45;
      box-shadow: var(--shadow-soft);
    }

    .message-row.user .message-bubble {
      border-bottom-right-radius: 6px;
    }

    .message-row.assistant .message-bubble {
      border-bottom-left-radius: 6px;
    }

    .composer {
      border-radius: 20px;
      padding: 14px;
      box-shadow: var(--shadow-soft);
    }

    .composer textarea {
      min-height: 72px;
      font-size: 16px;
    }

    :focus-visible {
      outline: 2px solid var(--focus);
      outline-offset: 2px;
    }

    @media (max-width: 900px) {
      .app-shell {
        display: block;
      }
      .sidebar {
        position: fixed;
        top: 0;
        left: 0;
        bottom: 0;
        width: min(84vw, 360px);
        max-height: 100vh;
        padding: 12px;
        transform: translateX(-100%);
        transition: transform 220ms ease;
        z-index: 36;
        border-right: 1px solid var(--border);
        border-top: none;
        box-shadow: var(--shadow);
      }
      body.sidebar-open .sidebar {
        transform: translateX(0);
      }
      .settings-modal {
        padding: 12px;
      }
      .settings-modal-shell {
        width: min(100vw - 16px, 100%);
        max-height: calc(100vh - 16px);
        padding: 14px;
        border-radius: 16px;
      }
      .settings-modal-header {
        flex-direction: column;
        align-items: stretch;
      }
      .settings-workspace-grid {
        grid-template-columns: 1fr;
      }
      .selected-model-header {
        flex-direction: column;
        align-items: stretch;
      }
      .model-row-head {
        grid-template-columns: 1fr;
      }
      .settings-field-grid {
        grid-template-columns: 1fr;
      }
      .settings-segmented {
        width: 100%;
      }
      .edit-modal {
        padding: 12px;
      }
      .edit-modal-shell {
        width: min(100vw - 16px, 100%);
        max-height: calc(100vh - 16px);
        padding: 14px;
        border-radius: 18px;
      }
      .edit-modal-header {
        flex-direction: column;
        align-items: stretch;
      }
      .edit-modal-input {
        min-height: 160px;
        border-radius: 18px;
        padding: 15px 16px;
      }
      .edit-modal-actions {
        flex-direction: column;
        align-items: stretch;
      }
      .edit-modal-action-row {
        width: 100%;
        justify-content: stretch;
      }
      .edit-modal-action-row .ghost-btn,
      .edit-modal-action-row .primary-btn {
        flex: 1 1 0;
      }
      .chat-shell {
        padding: 12px;
        gap: 10px;
      }
      .sidebar-mobile-actions {
        display: flex;
        justify-content: flex-end;
        margin-bottom: 10px;
      }
      .sidebar-toggle {
        display: inline-flex;
      }
      .chat-header h1 {
        font-size: 22px;
      }
      .header-actions {
        gap: 8px;
      }
      .theme-toggle {
        width: 40px;
        height: 40px;
      }
      .messages {
        min-height: 290px;
        max-height: min(56vh, 520px);
      }
      .composer-bottom { grid-template-columns: 1fr; }
      .composer-right {
        width: 100%;
        justify-content: flex-end;
      }
      .composer-status-chip {
        max-width: min(100%, 360px);
      }
    }
  </style>
</head>
<body>
  <div id="sidebarBackdrop" class="sidebar-backdrop" hidden></div>
  <div class="app-shell">
    <aside id="sidebarPanel" class="sidebar" aria-hidden="false">
      <div class="sidebar-mobile-actions">
        <button id="sidebarCloseBtn" class="sidebar-close" type="button" hidden>Close</button>
      </div>
      <section class="sidebar-section">
        <h2 class="brand">Potato OS</h2>
        <p id="sidebarNote" class="sidebar-note">V0.3 Pre-Alpha</p>
        <div class="status-summary">
          <div id="statusText" class="status-card">Checking status...</div>
          <div id="statusActions" class="status-actions" hidden>
            <button id="statusResumeDownloadBtn" class="ghost-btn" type="button">Resume</button>
          </div>
        </div>
        <div id="downloadPrompt" class="download-prompt" hidden>
          <p class="download-prompt-title">Model download required</p>
          <p id="downloadPromptHint" class="download-prompt-hint">Auto-download starts in 5:00.</p>
          <div class="download-prompt-actions">
            <button id="startDownloadBtn" class="ghost-btn" type="button">Start download now</button>
          </div>
        </div>
        <div id="systemRuntimeCard" class="status-card runtime-card">
          <div class="runtime-header">
            <span>Pi Runtime</span>
            <button id="runtimeViewToggle" class="runtime-toggle" type="button" aria-expanded="false">Show details</button>
          </div>
          <div id="compatibilityWarnings" class="runtime-compact" hidden>
            <span id="compatibilityWarningsText"></span>
            <button id="compatibilityOverrideBtn" class="ghost-btn" type="button" hidden>Try anyway</button>
          </div>
          <div id="runtimeCompact" class="runtime-compact">CPU -- | Cores -- | GPU -- | Swap -- | Throttle --</div>
          <div id="runtimeDetails" class="runtime-details" hidden>
            <section id="runtimeDetailsPowerGroup" class="runtime-detail-group runtime-detail-group--power" aria-label="Power">
              <div class="runtime-detail-group-title">Power</div>
              <div id="runtimeDetailPower" class="runtime-detail-prominent">Power (estimated total): --</div>
              <div id="runtimeDetailPowerRaw" class="runtime-detail-secondary">Power (PMIC raw): --</div>
            </section>
            <section id="runtimeDetailsPerformanceGroup" class="runtime-detail-group" aria-label="Performance">
              <div class="runtime-detail-group-title">Performance</div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">CPU total</span><span id="runtimeDetailCpuValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">CPU cores</span><span id="runtimeDetailCoresValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">CPU clock</span><span id="runtimeDetailCpuClockValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">GPU clock</span><span id="runtimeDetailGpuValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">Temperature</span><span id="runtimeDetailTempValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">Throttling</span><span id="runtimeDetailThrottleValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">History</span><span id="runtimeDetailThrottleHistoryValue" class="runtime-detail-value">--</span></div>
            </section>
            <section id="runtimeDetailsMemoryGroup" class="runtime-detail-group" aria-label="Memory and storage">
              <div class="runtime-detail-group-title">Memory &amp; storage</div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">Memory</span><span id="runtimeDetailMemoryValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span id="runtimeDetailSwapLabel" class="runtime-detail-label">zram</span><span id="runtimeDetailSwapValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">Storage free</span><span id="runtimeDetailStorageValue" class="runtime-detail-value">--</span></div>
            </section>
            <section id="runtimeDetailsPlatformGroup" class="runtime-detail-group" aria-label="Platform">
              <div class="runtime-detail-group-title">Platform</div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">Pi model</span><span id="runtimeDetailPiModelValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">OS</span><span id="runtimeDetailOsValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">Kernel</span><span id="runtimeDetailKernelValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">Bootloader</span><span id="runtimeDetailBootloaderValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">Firmware</span><span id="runtimeDetailFirmwareValue" class="runtime-detail-value">--</span></div>
              <div class="runtime-detail-row"><span class="runtime-detail-label">Updated</span><span id="runtimeDetailUpdatedValue" class="runtime-detail-value">--</span></div>
            </section>
          </div>
        </div>
      </section>
      <div class="sidebar-actions">
        <button id="settingsOpenBtn" class="ghost-btn sidebar-settings-btn" type="button">Settings</button>
      </div>
    </aside>

    <main class="chat-shell">
      <header class="chat-header">
        <div class="header-primary">
          <button id="sidebarToggle" class="sidebar-toggle" type="button" aria-label="Open sidebar" aria-controls="sidebarPanel" aria-expanded="false" hidden>
            <span class="bars" aria-hidden="true">≡</span>
          </button>
          <span class="chat-brand-mark" aria-hidden="true">🥔</span>
          <h1 aria-label="🥔 Potato Chat">Potato Chat</h1>
        </div>
        <div class="header-actions">
          <span id="statusBadge" class="badge offline">
            <span id="statusDot" class="indicator-dot offline" aria-hidden="true"></span>
            <span id="statusSpinner" class="chip-spinner" aria-hidden="true" hidden></span>
            <span id="statusLabel">DISCONNECTED:llama.cpp</span>
          </span>
          <button id="themeToggle" class="theme-toggle" type="button" aria-label="Switch to light theme" title="Switch theme">
            <svg class="theme-icon theme-icon--moon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z"></path>
            </svg>
            <svg class="theme-icon theme-icon--sun" viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="4.5"></circle>
              <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.2 2.2M16.9 16.9l2.2 2.2M19.1 4.9l-2.2 2.2M7.1 16.9l-2.2 2.2"></path>
            </svg>
          </button>
        </div>
      </header>

      <section id="messages" class="messages"></section>

      <form id="composerForm" class="composer">
        <textarea id="userPrompt" rows="3" placeholder="Message Potato OS..."></textarea>
        <div class="composer-bottom">
          <div class="composer-left">
            <input id="imageInput" class="visually-hidden-file" type="file" accept="image/*">
            <button id="attachImageBtn" class="attach-btn" type="button">Attach image</button>
            <button id="clearImageBtn" class="ghost-btn" type="button" hidden>Remove image</button>
            <span id="imageMeta" class="image-meta" hidden></span>
          </div>
          <div class="composer-right">
            <div id="composerStatusChip" class="composer-status-chip" hidden>
              <span class="chip-spinner" aria-hidden="true"></span>
              <span id="composerStatusText">Preparing prompt • 0%</span>
              <button id="cancelBtn" class="chip-cancel-btn" type="button" hidden disabled aria-label="Cancel current work" title="Cancel">×</button>
            </div>
            <button id="sendBtn" class="send-btn" type="submit">Send</button>
          </div>
        </div>
        <div id="composerVisionNotice" class="composer-vision-notice" hidden></div>
        <div id="imagePreviewWrap" class="image-preview-wrap" hidden>
          <img id="imagePreview" alt="Selected upload preview">
        </div>
        <div id="composerActivity" class="assistive-live" aria-live="polite"></div>
      </form>

    </main>
  </div>
  <div id="settingsBackdrop" class="settings-backdrop" hidden></div>
  <div id="settingsModal" class="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settingsModalTitle" hidden>
    <div class="settings-modal-shell settings-workspace-shell">
      <div class="settings-modal-header">
        <div>
          <h2 id="settingsModalTitle" class="settings-modal-title">Settings</h2>
          <p class="settings-modal-note">Model profiles, downloads, vision encoders, and YAML in one place.</p>
        </div>
        <div class="settings-modal-header-actions">
          <button id="settingsAdvancedBtn" class="ghost-btn settings-advanced-btn" type="button" aria-label="Open advanced settings" title="Advanced settings">Advanced</button>
          <button id="settingsCloseBtn" class="ghost-btn settings-close-btn" type="button" aria-label="Close settings">Close</button>
        </div>
      </div>
      <div class="settings-workspace-tabs" role="tablist" aria-label="Settings views">
        <button id="settingsWorkspaceTabModel" class="settings-workspace-tab active" type="button" role="tab" aria-selected="true">Model</button>
        <button id="settingsWorkspaceTabYaml" class="settings-workspace-tab" type="button" role="tab" aria-selected="false">YAML</button>
      </div>
      <div id="settingsModelWorkspace" class="settings-workspace-panel">
        <div class="settings-workspace-grid">
          <section class="settings-section settings-models-column">
            <div class="settings-panel-heading">
              <div class="settings-panel-heading-copy">
                <h3 class="settings-section-title">Models</h3>
                <div class="settings-section-note">Auto-download bootstrap is paused for now. Use explicit downloads only.</div>
              </div>
            </div>
            <div class="settings-utility-card">
              <label class="full">Add model by URL
                <input id="modelUrlInput" type="url" placeholder="https://.../model.gguf">
              </label>
              <div class="settings-action-row full">
                <button id="registerModelBtn" class="ghost-btn" type="button">Add URL model</button>
              </div>
              <div id="modelUrlStatus" class="runtime-compact full">Add an HTTPS .gguf URL to register another model.</div>
              <label class="full">Upload local GGUF to Pi
                <input id="modelUploadInput" type="file" accept=".gguf,application/octet-stream">
              </label>
              <div class="settings-action-row full">
                <button id="uploadModelBtn" class="ghost-btn" type="button">Upload model</button>
                <button id="cancelUploadBtn" class="ghost-btn" type="button" hidden>Cancel upload</button>
              </div>
              <div id="modelUploadStatus" class="runtime-compact full">No upload in progress.</div>
              <details class="settings-danger-zone full">
                <summary>Danger zone</summary>
                <div class="settings-danger-zone-body">
                  <p class="settings-danger-note">Bulk deletion is tucked away so the main workspace can stay focused on setup and diagnostics.</p>
                  <button id="purgeModelsBtn" class="ghost-btn danger-btn" type="button">Delete all models</button>
                </div>
              </details>
            </div>
            <div class="full settings-model-list-wrap">
              <div id="modelsList" class="runtime-details settings-model-list"></div>
            </div>
          </section>
          <section class="settings-section settings-editor-column">
            <div class="selected-model-header">
              <div class="selected-model-header-copy">
                <h3 class="settings-section-title">Selected model</h3>
                <div id="modelName">Checking...</div>
                <div id="modelIdentityMeta" class="selected-model-meta" aria-live="polite"></div>
                <div id="modelSettingsStatus" class="runtime-compact">Select a model to edit its chat and vision settings.</div>
              </div>
              <div class="selected-model-header-actions">
                <button id="discardModelSettingsBtn" class="ghost-btn" type="button" hidden>Discard changes</button>
                <button id="saveModelSettingsBtn" class="ghost-btn settings-save-btn" type="button">Save model settings</button>
              </div>
            </div>
            <div id="modelCapabilitiesChips" class="settings-chip-row" aria-live="polite"></div>
            <div id="modelCapabilitiesText" class="settings-section-note">Capabilities unknown.</div>
            <section class="settings-subpanel full settings-chat-panel">
              <div class="settings-subpanel-header">
                <h4 class="settings-subpanel-title">Chat profile</h4>
              </div>
              <label class="full">System Prompt (optional)
                <textarea id="systemPrompt" placeholder="Set assistant behavior for this model"></textarea>
              </label>
              <div class="settings-field-grid">
                <label>
                  <span class="settings-field-label">Generation Mode</span>
                  <div class="settings-segmented" data-target="generationMode" role="group" aria-label="Generation Mode">
                    <button type="button" class="settings-segment-btn" data-target="generationMode" data-value="random">Random</button>
                    <button type="button" class="settings-segment-btn" data-target="generationMode" data-value="deterministic">Deterministic</button>
                  </div>
                  <input id="generationMode" type="hidden" value="random">
                </label>
                <label><span class="settings-field-label">Seed <span class="settings-field-hint">Only used in deterministic mode</span></span>
                  <input id="seed" type="number" step="1">
                </label>
                <label><span class="settings-field-label">Temperature</span>
                  <input id="temperature" type="number" step="0.1" min="0" max="2">
                </label>
                <label><span class="settings-field-label">Top P</span>
                  <input id="top_p" type="number" step="0.1" min="0" max="1">
                </label>
                <label><span class="settings-field-label">Top K</span>
                  <input id="top_k" type="number" step="1" min="0">
                </label>
                <label><span class="settings-field-label">Repetition Penalty</span>
                  <input id="repetition_penalty" type="number" step="0.1" min="0">
                </label>
                <label><span class="settings-field-label">Presence Penalty</span>
                  <input id="presence_penalty" type="number" step="0.1">
                </label>
                <label><span class="settings-field-label">Max Tokens</span>
                  <input id="max_tokens" type="number" step="1" min="1">
                </label>
              </div>
            </section>
            <section id="settingsVisionSection" class="settings-subpanel full">
              <div class="settings-subpanel-header">
                <h4 class="settings-subpanel-title">Vision</h4>
                <label class="settings-inline-toggle">
                  <input id="visionEnabled" type="checkbox">
                  <span>Enable vision</span>
                </label>
              </div>
              <div id="projectorStatusText" class="runtime-compact">No vision projector configured.</div>
              <div class="settings-action-row full">
                <button id="downloadProjectorBtn" class="ghost-btn" type="button">Download vision encoder</button>
              </div>
            </section>
          </section>
        </div>
      </div>
      <div id="settingsYamlPanel" class="settings-workspace-panel" hidden>
        <section class="settings-section full">
          <h3 class="settings-section-title">Whole settings document</h3>
          <div class="settings-section-note">Edit and apply the full settings document atomically.</div>
          <textarea id="settingsYamlInput" class="settings-yaml-input" spellcheck="false" placeholder="Loading YAML..."></textarea>
          <div class="settings-action-row full">
            <button id="settingsYamlReloadBtn" class="ghost-btn" type="button">Reload YAML</button>
            <button id="settingsYamlApplyBtn" class="ghost-btn" type="button">Apply YAML</button>
          </div>
          <div id="settingsYamlStatus" class="runtime-compact">No YAML loaded yet.</div>
        </section>
      </div>
    </div>
  </div>
  <div id="legacySettingsBackdrop" class="settings-backdrop legacy-settings-backdrop" hidden></div>
  <div id="legacySettingsModal" class="settings-modal legacy-settings-modal" role="dialog" aria-modal="true" aria-labelledby="legacySettingsModalTitle" hidden>
    <div class="settings-modal-shell">
      <div class="settings-modal-header">
        <div>
          <h2 id="legacySettingsModalTitle" class="settings-modal-title">Advanced settings</h2>
          <p class="settings-modal-note">Legacy runtime and diagnostic controls kept around during the migration.</p>
        </div>
        <button id="legacySettingsCloseBtn" class="ghost-btn settings-close-btn" type="button" aria-label="Close advanced settings">Close</button>
      </div>
      <div class="settings-grid">
        <section id="legacySettingsRuntimeSection" class="settings-section full">
          <h3 class="settings-section-title">Runtime controls</h3>
          <div class="settings-section-note">Automatic default-model bootstrap download is temporarily paused.</div>
          <label class="full" style="display:flex; align-items:center; gap:8px;">
            <input id="largeModelOverrideEnabled" type="checkbox">
            <span>Allow unsupported large models (try anyway)</span>
          </label>
          <div class="settings-action-row full">
            <button id="applyLargeModelOverrideBtn" class="ghost-btn" type="button">Apply compatibility override</button>
          </div>
          <div id="largeModelOverrideStatus" class="runtime-compact">Compatibility override: default warnings</div>
          <label class="full">GGUF loading mode (requires runtime restart)
            <select id="llamaMemoryLoadingMode">
              <option value="auto">Automatic (profile-based)</option>
              <option value="full_ram">Full RAM load (--no-mmap)</option>
              <option value="mmap">Memory-mapped (mmap)</option>
            </select>
          </label>
          <div class="settings-action-row full">
            <button id="applyLlamaMemoryLoadingBtn" class="ghost-btn" type="button">Apply memory loading + restart</button>
          </div>
          <div id="llamaMemoryLoadingStatus" class="runtime-compact">Current memory loading: unknown</div>
          <div class="full">
            <h3 class="settings-section-title">Llama Runtime Bundle</h3>
            <div id="llamaRuntimeCurrent" class="runtime-compact">Current runtime: unknown</div>
            <label class="full">Installed/Test Bundles
              <select id="llamaRuntimeBundleSelect">
                <option value="">No bundles discovered</option>
              </select>
            </label>
            <div class="settings-action-row full">
              <button id="switchLlamaRuntimeBtn" class="ghost-btn" type="button">Switch llama runtime</button>
            </div>
            <div id="llamaRuntimeSwitchStatus" class="runtime-compact">No runtime switch in progress.</div>
          </div>
          <div class="settings-action-row full">
            <button id="resetRuntimeBtn" class="ghost-btn danger-btn" type="button">Unload model + clean memory + restart</button>
          </div>
        </section>
        <section class="settings-section full">
          <h3 class="settings-section-title">Power calibration</h3>
          <details id="settingsPowerCalibration" class="settings-subdetails full" open>
            <summary>PMIC to wall-meter calibration</summary>
            <div class="settings-section-note">Optional wall-meter calibration for Pi 5 power estimates.</div>
            <div id="powerCalibrationLiveStatus" class="runtime-compact">Current PMIC raw power: --</div>
            <label class="full">Wall meter reading (W)
              <input id="powerCalibrationWallWatts" type="number" min="0" step="0.01" placeholder="e.g. 9.4">
            </label>
            <div class="settings-action-row full">
              <button id="capturePowerCalibrationSampleBtn" class="ghost-btn" type="button">Capture calibration sample</button>
              <button id="fitPowerCalibrationBtn" class="ghost-btn" type="button">Compute calibration</button>
              <button id="resetPowerCalibrationBtn" class="ghost-btn danger-btn" type="button">Reset calibration</button>
            </div>
            <div id="powerCalibrationStatus" class="runtime-compact">Power calibration: default correction</div>
          </details>
        </section>
      </div>
    </div>
  </div>
  <div id="editBackdrop" class="edit-backdrop" hidden></div>
  <div id="editModal" class="edit-modal" role="dialog" aria-modal="true" aria-labelledby="editModalTitle" hidden>
    <div class="edit-modal-shell">
      <div class="edit-modal-header">
        <div>
          <h2 id="editModalTitle" class="edit-modal-title">Edit message</h2>
          <p id="editModalNote" class="edit-modal-note">Update the message and resend from this point in the conversation.</p>
        </div>
        <button id="editCloseBtn" class="ghost-btn settings-close-btn" type="button" aria-label="Close edit message dialog">Close</button>
      </div>
      <textarea id="editMessageInput" class="edit-modal-input" placeholder="Update your message..."></textarea>
      <div class="edit-modal-actions">
        <div id="editModalHint" class="edit-modal-hint">Everything after this turn will be replaced by the new run.</div>
        <div class="edit-modal-action-row">
          <button id="editCancelBtn" class="ghost-btn" type="button">Cancel</button>
          <button id="editSendBtn" class="primary-btn edit-send-btn" type="button">Send</button>
        </div>
      </div>
    </div>
  </div>

  <script src="/assets/vendor/marked.umd.js"></script>
  <script src="/assets/vendor/purify.min.js"></script>
  <script>
    const defaultSettings = {
      temperature: 0.7,
      top_p: 0.8,
      top_k: 20,
      repetition_penalty: 1.0,
      presence_penalty: 1.5,
      max_tokens: 16384,
      stream: true,
      generation_mode: "random",
      seed: 42,
      theme: "light",
      system_prompt: "",
    };
    const settingsKey = "potato_settings_v2";
    const PREFILL_METRICS_KEY = "potato_prefill_metrics_v1";
    const PREFILL_PROGRESS_CAP = 99;
    const PREFILL_PROGRESS_TAIL_START = 89;
    const PREFILL_PROGRESS_FLOOR = 6;
    const PREFILL_TICK_MS = 180;
    const PREFILL_FINISH_DURATION_MS = Math.max(
      120,
      Number(window.__POTATO_PREFILL_FINISH_DURATION_MS__ || 1000),
    );
    const PREFILL_FINISH_TICK_MS = 40;
    const PREFILL_FINISH_HOLD_MS = Math.max(
      80,
      Number(window.__POTATO_PREFILL_FINISH_HOLD_MS__ || 220),
    );
    const STATUS_CHIP_MIN_VISIBLE_MS = 260;
    const STATUS_POLL_TIMEOUT_MS = 3500;
    const RUNTIME_RECONNECT_INTERVAL_MS = 1200;
    const RUNTIME_RECONNECT_TIMEOUT_MS = 2500;
    const RUNTIME_RECONNECT_MAX_ATTEMPTS = 75;
    const IMAGE_CANCEL_RECOVERY_DELAY_MS = Math.max(
      200,
      Number(window.__POTATO_CANCEL_RECOVERY_DELAY_MS__ || 8000),
    );
    const IMAGE_CANCEL_RESTART_DELAY_MS = Math.max(
      2000,
      Number(window.__POTATO_CANCEL_RESTART_DELAY_MS__ || 45000),
    );
    let requestInFlight = false;
    let activeRequest = null;
    let activePrefillProgress = null;
    let imageCancelRecoveryTimer = null;
    let imageCancelRestartTimer = null;
    let statusChipVisibleAtMs = 0;
    let statusChipHideTimer = null;
    let latestStatus = null;
    let downloadStartInFlight = false;
    let modelActionInFlight = false;
    let llamaRuntimeSwitchInFlight = false;
    let llamaMemoryLoadingApplyInFlight = false;
    let largeModelOverrideApplyInFlight = false;
    let powerCalibrationActionInFlight = false;
    let uploadRequest = null;
    let runtimeResetInFlight = false;
    let runtimeReconnectWatchActive = false;
    let runtimeReconnectWatchTimer = null;
    let runtimeReconnectAttempts = 0;
    let statusPollSeq = 0;
    let statusPollAppliedSeq = 0;
    let runtimeDetailsExpanded = true;
    let mobileSidebarMql = null;
    let settingsModalOpen = false;
    let legacySettingsModalOpen = false;
    let settingsModalOpenedAtMs = 0;
    let editModalOpen = false;
    let settingsWorkspaceTab = "model";
    let selectedSettingsModelId = "";
    let settingsYamlLoaded = false;
    let settingsYamlRequestInFlight = false;
    let modelSettingsSaveInFlight = false;
    let modelSettingsStatusModelId = "";
    let modelSettingsDraftDirty = false;
    let modelSettingsDraftModelId = "";
    let displayedSettingsModelId = "";
    let projectorDownloadInFlight = false;
    let messagesPinnedToBottom = true;
    let messagePointerSelectionActive = false;
    const chatHistory = [];
    const conversationTurns = [];
    let activeEditState = null;
    let pendingImage = null;
    let pendingImageReader = null;
    let pendingImageToken = 0;
    const IMAGE_SAFE_MAX_BYTES = 140 * 1024;
    const IMAGE_MAX_DIMENSION = 896;
    const IMAGE_MAX_PIXEL_COUNT = IMAGE_MAX_DIMENSION * IMAGE_MAX_DIMENSION;
    const CPU_CLOCK_MAX_HZ_PI5 = 2_400_000_000;
    const GPU_CLOCK_MAX_HZ_PI5 = 1_000_000_000;
    const RUNTIME_METRIC_SEVERITY_CLASSES = [
      "runtime-metric-normal",
      "runtime-metric-warn",
      "runtime-metric-high",
      "runtime-metric-critical",
    ];

    function detectSystemTheme() {
      try {
        if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
          if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
            return "dark";
          }
        }
      } catch (_err) {
        // Fall through to light theme fallback.
      }
      return "light";
    }

    function normalizeTheme(rawTheme, fallback = defaultSettings.theme) {
      if (rawTheme === "dark") return "dark";
      if (rawTheme === "light") return "light";
      return fallback;
    }

    function loadSettings() {
      const raw = localStorage.getItem(settingsKey);
      if (!raw) {
        return { theme: detectSystemTheme() };
      }
      try {
        const parsedRaw = JSON.parse(raw);
        return {
          theme: normalizeTheme(parsedRaw?.theme, detectSystemTheme()),
        };
      } catch (_err) {
        return { theme: detectSystemTheme() };
      }
    }

    function saveSettings(settings) {
      const theme = normalizeTheme(settings?.theme, detectSystemTheme());
      localStorage.setItem(settingsKey, JSON.stringify({ theme }));
    }

    function parseNumber(id, fallback) {
      const parsed = Number(document.getElementById(id).value);
      return Number.isFinite(parsed) ? parsed : fallback;
    }

    function normalizeGenerationMode(rawMode) {
      return rawMode === "deterministic" ? "deterministic" : "random";
    }

    function normalizeSeedValue(rawSeed, fallback = defaultSettings.seed) {
      const parsed = Number(rawSeed);
      if (!Number.isFinite(parsed)) return fallback;
      return Math.trunc(parsed);
    }

    function updateSeedFieldState(generationMode) {
      const seedField = document.getElementById("seed");
      if (!seedField) return;
      seedField.disabled = generationMode !== "deterministic";
      seedField.title = seedField.disabled ? "Seed is only used in deterministic mode" : "";
    }

    function resolveSeedForRequest(settings) {
      const mode = normalizeGenerationMode(settings?.generation_mode);
      if (mode !== "deterministic") {
        return null;
      }
      return normalizeSeedValue(settings?.seed, defaultSettings.seed);
    }

    function formatBytes(rawBytes) {
      const bytes = Number(rawBytes);
      if (!Number.isFinite(bytes) || bytes <= 0) {
        return "0 B";
      }
      const units = ["B", "KB", "MB", "GB", "TB"];
      let value = bytes;
      let unitIndex = 0;
      while (value >= 1000 && unitIndex < units.length - 1) {
        value /= 1000;
        unitIndex += 1;
      }
      const precision = value >= 100 ? 0 : value >= 10 ? 1 : 2;
      return `${value.toFixed(precision)} ${units[unitIndex]}`;
    }

    function formatPercent(rawValue, digits = 0) {
      const value = Number(rawValue);
      if (!Number.isFinite(value)) return "--";
      return `${value.toFixed(digits)}%`;
    }

    function formatClockMHz(rawHz) {
      const hz = Number(rawHz);
      if (!Number.isFinite(hz) || hz <= 0) return "--";
      return `${Math.round(hz / 1_000_000)} MHz`;
    }

    function normalizePercent(rawValue) {
      const value = Number(rawValue);
      if (!Number.isFinite(value)) return Number.NaN;
      return Math.min(100, Math.max(0, value));
    }

    function percentFromRatio(rawCurrent, rawMax) {
      const current = Number(rawCurrent);
      const max = Number(rawMax);
      if (!Number.isFinite(current) || !Number.isFinite(max) || current < 0 || max <= 0) {
        return Number.NaN;
      }
      return normalizePercent((current / max) * 100);
    }

    function runtimeMetricSeverityClass(rawPercent) {
      const percent = normalizePercent(rawPercent);
      if (!Number.isFinite(percent)) return "runtime-metric-normal";
      if (percent >= 90) return "runtime-metric-critical";
      if (percent >= 75) return "runtime-metric-high";
      if (percent >= 60) return "runtime-metric-warn";
      return "runtime-metric-normal";
    }

    function applyRuntimeMetricSeverity(element, rawPercent) {
      if (!element) return;
      element.classList.remove(...RUNTIME_METRIC_SEVERITY_CLASSES);
      element.classList.add(runtimeMetricSeverityClass(rawPercent));
    }

    function formatCountdownSeconds(rawSeconds) {
      const totalSeconds = Math.max(0, Math.floor(Number(rawSeconds) || 0));
      const minutes = Math.floor(totalSeconds / 60);
      const seconds = totalSeconds % 60;
      return `${minutes}:${String(seconds).padStart(2, "0")}`;
    }

    function estimateDataUrlBytes(dataUrl) {
      const marker = "base64,";
      const idx = dataUrl.indexOf(marker);
      if (idx < 0) return 0;
      const base64Payload = dataUrl.slice(idx + marker.length);
      const padding = base64Payload.endsWith("==") ? 2 : base64Payload.endsWith("=") ? 1 : 0;
      return Math.floor((base64Payload.length * 3) / 4) - padding;
    }

    function dataUrlToImage(dataUrl) {
      return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error("image_decode_failed"));
        img.src = dataUrl;
      });
    }

    async function inspectImageDataUrl(dataUrl) {
      const image = await dataUrlToImage(dataUrl);
      const width = Math.max(1, Number(image.naturalWidth) || 1);
      const height = Math.max(1, Number(image.naturalHeight) || 1);
      return {
        width,
        height,
        maxDim: Math.max(width, height),
        pixelCount: width * height,
      };
    }

    function canvasToDataUrl(canvas, mimeType, quality) {
      return new Promise((resolve, reject) => {
        canvas.toBlob(
          (blob) => {
            if (!blob) {
              reject(new Error("canvas_blob_failed"));
              return;
            }
            const fr = new FileReader();
            fr.onload = () => resolve({ dataUrl: String(fr.result || ""), size: blob.size });
            fr.onerror = () => reject(new Error("canvas_read_failed"));
            fr.readAsDataURL(blob);
          },
          mimeType,
          quality
        );
      });
    }

    async function compressImageDataUrl(originalDataUrl) {
      const image = await dataUrlToImage(originalDataUrl);
      const maxDim = Math.max(image.naturalWidth || 1, image.naturalHeight || 1);
      const scale = maxDim > IMAGE_MAX_DIMENSION ? IMAGE_MAX_DIMENSION / maxDim : 1;
      const width = Math.max(1, Math.round((image.naturalWidth || 1) * scale));
      const height = Math.max(1, Math.round((image.naturalHeight || 1) * scale));
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        throw new Error("canvas_context_failed");
      }
      ctx.drawImage(image, 0, 0, width, height);

      const qualities = [0.82, 0.74, 0.66, 0.58, 0.5, 0.42];
      let best = null;
      for (const quality of qualities) {
        const candidate = await canvasToDataUrl(canvas, "image/jpeg", quality);
        if (!best || candidate.size < best.size) {
          best = candidate;
        }
        if (candidate.size <= IMAGE_SAFE_MAX_BYTES) {
          break;
        }
      }

      if (!best) {
        throw new Error("compress_failed");
      }

      return {
        dataUrl: best.dataUrl,
        size: best.size,
        type: "image/jpeg",
      };
    }

    async function maybeCompressImage(dataUrl, file) {
      const inputSize = Number(file?.size) || estimateDataUrlBytes(dataUrl);
      let metadata = null;
      try {
        metadata = await inspectImageDataUrl(dataUrl);
      } catch (_err) {
        metadata = null;
      }
      const needsResize = Boolean(
        metadata && (
          metadata.maxDim > IMAGE_MAX_DIMENSION
          || metadata.pixelCount > IMAGE_MAX_PIXEL_COUNT
        )
      );

      if (inputSize <= IMAGE_SAFE_MAX_BYTES && !needsResize) {
        return {
          dataUrl,
          size: inputSize,
          type: file?.type || "image/*",
          optimized: false,
          originalSize: inputSize,
        };
      }

      setComposerActivity("Optimizing image...");
      setComposerStatusChip("Optimizing image...", { phase: "image" });
      const compressed = await compressImageDataUrl(dataUrl);
      return {
        dataUrl: compressed.dataUrl,
        size: compressed.size,
        type: compressed.type,
        optimized: true,
        originalSize: inputSize,
      };
    }

    function normalizeChatSettings(rawSettings) {
      const chat = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
      const generationMode = normalizeGenerationMode(chat.generation_mode);
      return {
        temperature: Number.isFinite(Number(chat.temperature)) ? Number(chat.temperature) : defaultSettings.temperature,
        top_p: Number.isFinite(Number(chat.top_p)) ? Number(chat.top_p) : defaultSettings.top_p,
        top_k: Number.isFinite(Number(chat.top_k)) ? Number(chat.top_k) : defaultSettings.top_k,
        repetition_penalty: Number.isFinite(Number(chat.repetition_penalty)) ? Number(chat.repetition_penalty) : defaultSettings.repetition_penalty,
        presence_penalty: Number.isFinite(Number(chat.presence_penalty)) ? Number(chat.presence_penalty) : defaultSettings.presence_penalty,
        max_tokens: Number.isFinite(Number(chat.max_tokens)) ? Number(chat.max_tokens) : defaultSettings.max_tokens,
        stream: true,
        generation_mode: generationMode,
        seed: normalizeSeedValue(chat.seed, defaultSettings.seed),
        system_prompt: String(chat.system_prompt || "").trim(),
      };
    }

    function normalizeVisionSettings(rawSettings) {
      const vision = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
      return {
        enabled: Boolean(vision.enabled),
        projector_mode: String(vision.projector_mode || "default"),
        projector_filename: String(vision.projector_filename || ""),
      };
    }

    function normalizeProjectorStatus(rawProjector, visionSettings = {}) {
      const projector = rawProjector && typeof rawProjector === "object" ? rawProjector : {};
      const defaultCandidates = Array.isArray(projector.default_candidates)
        ? projector.default_candidates.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
      const legacyDefaultFilename = String(projector.default_filename || "").trim();
      if (legacyDefaultFilename && !defaultCandidates.includes(legacyDefaultFilename)) {
        defaultCandidates.unshift(legacyDefaultFilename);
      }
      const resolvedFilename = String(
        projector.filename
        || projector.selected_filename
        || visionSettings.projector_filename
        || ""
      ).trim();
      return {
        present: projector.present === true || projector.available === true,
        filename: resolvedFilename,
        defaultFilename: defaultCandidates[0] || "",
        defaultCandidates,
      };
    }

    function resolveActiveRuntimeModel(statusPayload = latestStatus) {
      const topModel = statusPayload?.model && typeof statusPayload.model === "object"
        ? statusPayload.model
        : null;
      if (typeof topModel?.capabilities?.vision === "boolean") {
        return topModel;
      }
      const models = getSettingsModels(statusPayload);
      const activeModelId = String(topModel?.active_model_id || "");
      if (activeModelId) {
        const exact = models.find((item) => String(item?.id || "") === activeModelId);
        if (exact) return exact;
      }
      const activeFilename = String(topModel?.filename || "").trim();
      if (activeFilename) {
        const exactFilename = models.find((item) => String(item?.filename || "").trim() === activeFilename);
        if (exactFilename) return exactFilename;
      }
      const active = models.find((item) => item?.is_active === true);
      return active || topModel || null;
    }

    function activeRuntimeVisionCapability(statusPayload = latestStatus) {
      const activeModel = resolveActiveRuntimeModel(statusPayload);
      const supportsVision = activeModel?.capabilities?.vision;
      return typeof supportsVision === "boolean" ? supportsVision : null;
    }

    function formatTextOnlyImageNotice(statusPayload = latestStatus) {
      const activeModel = resolveActiveRuntimeModel(statusPayload);
      const modelName = String(activeModel?.filename || "The current model").trim();
      return `${modelName} is text-only. Switch to a vision-capable model in Settings to send images.`;
    }

    function formatImageRejectedNotice(statusPayload = latestStatus) {
      if (activeRuntimeVisionCapability(statusPayload) === false) {
        return formatTextOnlyImageNotice(statusPayload);
      }
      return "This model can't process images right now. Switch to a vision-capable model or configure its vision encoder in Settings.";
    }

    function setComposerVisionNotice(message) {
      const notice = document.getElementById("composerVisionNotice");
      if (!notice) return;
      const text = String(message || "").trim();
      notice.textContent = text;
      notice.hidden = text.length === 0;
    }

    function showTextOnlyImageBlockedState(statusPayload = latestStatus) {
      const notice = formatTextOnlyImageNotice(statusPayload);
      setComposerVisionNotice(notice);
      setComposerActivity(notice);
      setComposerStatusChip("Current model is text-only.", { phase: "image" });
      hideComposerStatusChip();
      setCancelEnabled(false);
      focusPromptInput();
    }

    function renderComposerCapabilities(statusPayload = latestStatus) {
      const attachBtn = document.getElementById("attachImageBtn");
      const clearBtn = document.getElementById("clearImageBtn");
      if (!attachBtn) return;
      const visionCapability = activeRuntimeVisionCapability(statusPayload);
      const explicitTextOnly = visionCapability === false;
      const blockedMessage = explicitTextOnly ? formatTextOnlyImageNotice(statusPayload) : "";
      setComposerVisionNotice(blockedMessage);
      attachBtn.disabled = requestInFlight || explicitTextOnly;
      attachBtn.setAttribute("aria-disabled", attachBtn.disabled ? "true" : "false");
      attachBtn.setAttribute("title", explicitTextOnly ? blockedMessage : "Attach image");
      attachBtn.setAttribute("aria-label", explicitTextOnly ? blockedMessage : "Attach image");
      if (clearBtn) {
        clearBtn.disabled = requestInFlight;
      }
      if (explicitTextOnly && pendingImage) {
        clearPendingImage();
        setComposerActivity("Image removed.");
      }
    }

    function getSettingsModels(statusPayload = latestStatus) {
      return Array.isArray(statusPayload?.models) ? statusPayload.models : [];
    }

    function resolveSelectedSettingsModel(statusPayload = latestStatus) {
      const models = getSettingsModels(statusPayload);
      if (models.length === 0) return null;
      if (selectedSettingsModelId) {
        const exact = models.find((item) => String(item?.id || "") === selectedSettingsModelId);
        if (exact) return exact;
      }
      const activeModelId = String(statusPayload?.model?.active_model_id || "");
      const active = models.find((item) => String(item?.id || "") === activeModelId || item?.is_active === true);
      if (active) {
        selectedSettingsModelId = String(active.id || "");
        return active;
      }
      selectedSettingsModelId = String(models[0]?.id || "");
      return models[0];
    }

    function getActiveChatSettings(statusPayload = latestStatus) {
      const activeChat = statusPayload?.model?.settings?.chat;
      return normalizeChatSettings(activeChat);
    }

    function collectSelectedModelSettings() {
      const selectedModel = resolveSelectedSettingsModel(latestStatus);
      const supportsVision = Boolean(selectedModel?.capabilities?.vision);
      const generationMode = normalizeGenerationMode(document.getElementById("generationMode").value);
      const seed = normalizeSeedValue(document.getElementById("seed").value, defaultSettings.seed);
      const persistedStream = selectedModel?.settings?.chat?.stream !== false;
      return {
        chat: {
          temperature: parseNumber("temperature", defaultSettings.temperature),
          top_p: parseNumber("top_p", defaultSettings.top_p),
          top_k: parseNumber("top_k", defaultSettings.top_k),
          repetition_penalty: parseNumber("repetition_penalty", defaultSettings.repetition_penalty),
          presence_penalty: parseNumber("presence_penalty", defaultSettings.presence_penalty),
          max_tokens: parseNumber("max_tokens", defaultSettings.max_tokens),
          stream: persistedStream,
          generation_mode: generationMode,
          seed,
          system_prompt: document.getElementById("systemPrompt").value.trim(),
        },
        vision: {
          enabled: supportsVision && Boolean(document.getElementById("visionEnabled")?.checked),
          projector_mode: "default",
          projector_filename: supportsVision
            ? String(document.getElementById("downloadProjectorBtn")?.dataset?.projectorFilename || "")
            : "",
        },
      };
    }

    function markModelSettingsDraftDirty() {
      const selectedModel = resolveSelectedSettingsModel(latestStatus);
      modelSettingsDraftDirty = true;
      modelSettingsDraftModelId = String(selectedModel?.id || "");
      const statusEl = document.getElementById("modelSettingsStatus");
      const discardBtn = document.getElementById("discardModelSettingsBtn");
      if (statusEl) {
        statusEl.textContent = "Unsaved changes.";
      }
      if (discardBtn) {
        discardBtn.hidden = false;
        discardBtn.disabled = modelSettingsSaveInFlight;
      }
    }

    function clearModelSettingsDraftState() {
      modelSettingsDraftDirty = false;
      modelSettingsDraftModelId = "";
      const discardBtn = document.getElementById("discardModelSettingsBtn");
      if (discardBtn) {
        discardBtn.hidden = true;
        discardBtn.disabled = true;
      }
    }

    function setModelUrlStatus(message) {
      const statusEl = document.getElementById("modelUrlStatus");
      if (statusEl) {
        statusEl.textContent = String(message || "");
      }
    }

    function formatModelUrlStatus(reason, fallbackStatus) {
      const normalized = String(reason || "").trim().toLowerCase();
      if (normalized === "https_required") {
        return "Use an HTTPS model URL that ends with .gguf.";
      }
      if (normalized === "gguf_required") {
        return "Model URL must point to a .gguf file.";
      }
      if (normalized === "filename_missing") {
        return "Model URL must include a model filename.";
      }
      if (normalized === "already_exists") {
        return "That model URL is already registered.";
      }
      return `Could not add model URL (${reason || fallbackStatus}).`;
    }

    function isEditingModelSettingsField() {
      const active = document.activeElement;
      if (!(active instanceof HTMLElement)) return false;
      if (!active.id) return false;
      return [
        "systemPrompt",
        "seed",
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "presence_penalty",
        "max_tokens",
        "visionEnabled",
      ].includes(String(active.id));
    }

    function shouldPauseSelectedModelSettingsRender() {
      return settingsModalOpen
        && settingsWorkspaceTab === "model"
        && (modelSettingsDraftDirty || isEditingModelSettingsField());
    }

    function modelSettingsFormHasUnsavedValues(chat, vision) {
      const systemPromptEl = document.getElementById("systemPrompt");
      const generationModeEl = document.getElementById("generationMode");
      const seedEl = document.getElementById("seed");
      const temperatureEl = document.getElementById("temperature");
      const topPEl = document.getElementById("top_p");
      const topKEl = document.getElementById("top_k");
      const repetitionPenaltyEl = document.getElementById("repetition_penalty");
      const presencePenaltyEl = document.getElementById("presence_penalty");
      const maxTokensEl = document.getElementById("max_tokens");
      const visionEnabledEl = document.getElementById("visionEnabled");
      if (
        !systemPromptEl || !generationModeEl || !seedEl || !temperatureEl
        || !topPEl || !topKEl || !repetitionPenaltyEl || !presencePenaltyEl || !maxTokensEl
      ) {
        return false;
      }
      return (
        String(systemPromptEl.value || "") !== String(chat.system_prompt || "")
        || String(generationModeEl.value || "") !== String(chat.generation_mode)
        || String(seedEl.value || "") !== String(chat.seed)
        || String(temperatureEl.value || "") !== String(chat.temperature)
        || String(topPEl.value || "") !== String(chat.top_p)
        || String(topKEl.value || "") !== String(chat.top_k)
        || String(repetitionPenaltyEl.value || "") !== String(chat.repetition_penalty)
        || String(presencePenaltyEl.value || "") !== String(chat.presence_penalty)
        || String(maxTokensEl.value || "") !== String(chat.max_tokens)
        || (visionEnabledEl ? Boolean(visionEnabledEl.checked) !== Boolean(vision.enabled) : false)
      );
    }

    function selectedModelHasUnsavedChanges(statusPayload = latestStatus) {
      const selectedModel = resolveSelectedSettingsModel(statusPayload);
      const selectedModelId = String(selectedModel?.id || "");
      if (!selectedModelId) return false;
      const chat = normalizeChatSettings(selectedModel?.settings?.chat);
      const vision = normalizeVisionSettings(selectedModel?.settings?.vision);
      return (
        (modelSettingsDraftDirty && modelSettingsDraftModelId === selectedModelId)
        || (
          displayedSettingsModelId === selectedModelId
          && modelSettingsFormHasUnsavedValues(chat, vision)
        )
      );
    }

    function blockModelSelectionChange() {
      const statusEl = document.getElementById("modelSettingsStatus");
      if (statusEl) {
        statusEl.textContent = "Save or discard changes before switching models.";
      }
    }

    function discardSelectedModelSettings() {
      const selectedModel = resolveSelectedSettingsModel(latestStatus);
      if (!selectedModel) return;
      clearModelSettingsDraftState();
      displayedSettingsModelId = "";
      modelSettingsStatusModelId = "";
      renderSelectedModelSettings(latestStatus);
      const statusEl = document.getElementById("modelSettingsStatus");
      if (statusEl) {
        statusEl.textContent = "Changes discarded.";
      }
    }

    function collectSettings() {
      return {
        ...getActiveChatSettings(),
        theme: document.documentElement.getAttribute("data-theme") || defaultSettings.theme,
      };
    }

    function focusPromptInput(options = {}) {
      const prompt = document.getElementById("userPrompt");
      if (!prompt) return;
      const preventScroll = options.preventScroll !== false;
      prompt.focus({ preventScroll });
      if (options.moveCaretToEnd === false) return;
      const cursor = prompt.value.length;
      if (typeof prompt.setSelectionRange === "function") {
        prompt.setSelectionRange(cursor, cursor);
      }
    }

    function cancelPendingImageWork() {
      pendingImageToken += 1;
      if (pendingImageReader) {
        pendingImageReader.abort();
      }
      pendingImageReader = null;
    }

    function clearPendingImage() {
      pendingImage = null;
      const fileInput = document.getElementById("imageInput");
      const attachBtn = document.getElementById("attachImageBtn");
      const preview = document.getElementById("imagePreview");
      const previewWrap = document.getElementById("imagePreviewWrap");
      const imageMeta = document.getElementById("imageMeta");
      const clearBtn = document.getElementById("clearImageBtn");
      if (fileInput) {
        fileInput.value = "";
      }
      if (preview) {
        preview.removeAttribute("src");
      }
      if (previewWrap) {
        previewWrap.hidden = true;
      }
      if (imageMeta) {
        imageMeta.textContent = "";
        imageMeta.hidden = true;
      }
      if (clearBtn) {
        clearBtn.hidden = true;
      }
      if (attachBtn) {
        attachBtn.textContent = "Attach image";
        attachBtn.classList.remove("selected");
      }
    }

    function handleImageSelected(file) {
      const selectionToken = pendingImageToken + 1;
      pendingImageToken = selectionToken;

      if (!file) {
        clearPendingImage();
        setComposerActivity("");
        hideComposerStatusChip();
        setCancelEnabled(false);
        focusPromptInput();
        return;
      }
      if (activeRuntimeVisionCapability(latestStatus) === false) {
        clearPendingImage();
        showTextOnlyImageBlockedState(latestStatus);
        return;
      }
      if (!String(file.type || "").startsWith("image/")) {
        appendMessage("assistant", "Only image files are supported.");
        clearPendingImage();
        setComposerActivity("");
        hideComposerStatusChip();
        setCancelEnabled(false);
        focusPromptInput();
        return;
      }

      if (pendingImageReader) {
        pendingImageReader.abort();
      }
      const reader = new FileReader();
      pendingImageReader = reader;
      setComposerActivity("Reading image...");
      setComposerStatusChip("Reading image • 0%", { phase: "image" });
      setCancelEnabled(true);
      reader.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          const percent = Math.round((event.loaded * 100) / event.total);
          setComposerActivity(`Reading image... ${percent}%`);
          setComposerStatusChip(`Reading image • ${percent}%`, { phase: "image" });
          return;
        }
        setComposerActivity("Reading image...");
        setComposerStatusChip("Reading image...", { phase: "image" });
      };
      reader.onload = async () => {
        if (selectionToken !== pendingImageToken) {
          return;
        }
        const result = typeof reader.result === "string" ? reader.result : "";
        if (!result.startsWith("data:image/")) {
          appendMessage("assistant", "Invalid image encoding.");
          clearPendingImage();
          pendingImageReader = null;
          setComposerActivity("");
          hideComposerStatusChip();
          setCancelEnabled(false);
          focusPromptInput();
          return;
        }

        let processedImage;
        try {
          processedImage = await maybeCompressImage(result, file);
        } catch (_err) {
          appendMessage("assistant", "Could not optimize the selected image.");
          clearPendingImage();
          pendingImageReader = null;
          setComposerActivity("");
          hideComposerStatusChip();
          setCancelEnabled(false);
          focusPromptInput();
          return;
        }

        if (selectionToken !== pendingImageToken) {
          return;
        }

        pendingImage = {
          name: file.name || "image",
          type: processedImage.type || file.type || "image/*",
          size: Number(processedImage.size) || 0,
          originalSize: Number(processedImage.originalSize) || Number(file.size) || 0,
          optimized: Boolean(processedImage.optimized),
          dataUrl: processedImage.dataUrl || result,
        };

        const preview = document.getElementById("imagePreview");
        const previewWrap = document.getElementById("imagePreviewWrap");
        const imageMeta = document.getElementById("imageMeta");
        const clearBtn = document.getElementById("clearImageBtn");
        const attachBtn = document.getElementById("attachImageBtn");
        if (preview) {
          preview.src = pendingImage.dataUrl;
        }
        if (previewWrap) {
          previewWrap.hidden = false;
        }
        if (imageMeta) {
          if (pendingImage.optimized && pendingImage.originalSize > pendingImage.size) {
            imageMeta.textContent = `${pendingImage.name} (${formatBytes(pendingImage.size)}, optimized from ${formatBytes(pendingImage.originalSize)})`;
          } else {
            imageMeta.textContent = `${pendingImage.name} (${formatBytes(pendingImage.size)})`;
          }
          imageMeta.hidden = false;
        }
        if (clearBtn) {
          clearBtn.hidden = false;
        }
        if (attachBtn) {
          attachBtn.textContent = "Change image";
          attachBtn.classList.add("selected");
        }
        pendingImageReader = null;
        setComposerActivity("");
        hideComposerStatusChip();
        setCancelEnabled(false);
        focusPromptInput();
      };
      reader.onerror = () => {
        if (selectionToken !== pendingImageToken) {
          return;
        }
        appendMessage("assistant", "Could not read the selected image.");
        clearPendingImage();
        pendingImageReader = null;
        setComposerActivity("");
        hideComposerStatusChip();
        setCancelEnabled(false);
        focusPromptInput();
      };
      reader.onabort = () => {
        if (selectionToken !== pendingImageToken) {
          return;
        }
        clearPendingImage();
        pendingImageReader = null;
        setComposerActivity("Image load cancelled.");
        hideComposerStatusChip();
        setCancelEnabled(false);
        focusPromptInput();
      };
      reader.readAsDataURL(file);
    }

    function buildUserMessageContent(content) {
      if (!pendingImage) {
        return content;
      }
      const textPart = content || "Describe this image.";
      return [
        { type: "text", text: textPart },
        { type: "image_url", image_url: { url: pendingImage.dataUrl } },
      ];
    }

    function buildUserBubblePayload(content) {
      const text = String(content || "");
      if (!pendingImage) {
        return {
          text,
          imageDataUrl: "",
          imageName: "",
        };
      }
      return {
        text,
        imageDataUrl: pendingImage.dataUrl,
        imageName: pendingImage.name || "image",
      };
    }

    function extractApiErrorMessage(body) {
      if (!body || typeof body !== "object") return "";
      const candidate = body?.error?.message || body?.detail || body?.message || "";
      return typeof candidate === "string" ? candidate.trim() : "";
    }

    function formatChatFailureMessage(statusCode, body, requestCtx = {}) {
      const apiMessage = extractApiErrorMessage(body);
      const normalized = apiMessage.toLowerCase();
      if (
        requestCtx?.hasImageRequest
        && (
          normalized.includes("image input is not supported")
          || normalized.includes("mmproj")
        )
      ) {
        return formatImageRejectedNotice(latestStatus);
      }
      if (apiMessage) {
        return `Request failed (${statusCode}): ${apiMessage}`;
      }
      return `Request failed (${statusCode}).`;
    }

    function openImagePicker() {
      if (requestInFlight) return;
      if (activeRuntimeVisionCapability(latestStatus) === false) {
        clearPendingImage();
        showTextOnlyImageBlockedState(latestStatus);
        return;
      }
      const input = document.getElementById("imageInput");
      if (!input) return;
      input.value = "";
      input.click();
    }

    function isMobileSidebarViewport() {
      if (!mobileSidebarMql) {
        mobileSidebarMql = window.matchMedia("(max-width: 900px)");
      }
      return mobileSidebarMql.matches;
    }

    function setSidebarOpen(open) {
      const sidebar = document.getElementById("sidebarPanel");
      const backdrop = document.getElementById("sidebarBackdrop");
      const toggle = document.getElementById("sidebarToggle");
      const closeBtn = document.getElementById("sidebarCloseBtn");
      const mobile = isMobileSidebarViewport();
      const shouldOpen = Boolean(open) && mobile;

      document.body.classList.toggle("sidebar-open", shouldOpen);

      if (sidebar) {
        sidebar.setAttribute("aria-hidden", mobile ? (shouldOpen ? "false" : "true") : "false");
      }
      if (backdrop) {
        backdrop.hidden = !shouldOpen;
      }
      if (toggle) {
        toggle.hidden = !mobile;
        toggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
      }
      if (closeBtn) {
        closeBtn.hidden = !shouldOpen;
      }
    }

    function setSettingsModalOpen(open) {
      settingsModalOpen = Boolean(open);
      const modal = document.getElementById("settingsModal");
      const backdrop = document.getElementById("settingsBackdrop");
      document.body.classList.toggle("settings-modal-open", settingsModalOpen);
      if (modal) {
        modal.hidden = !settingsModalOpen;
      }
      if (backdrop) {
        backdrop.hidden = !settingsModalOpen;
      }
      if (settingsModalOpen) {
        settingsModalOpenedAtMs = performance.now();
        setSidebarOpen(false);
      } else {
        closeLegacySettingsModal();
      }
    }

    function setLegacySettingsModalOpen(open) {
      legacySettingsModalOpen = Boolean(open);
      const modal = document.getElementById("legacySettingsModal");
      const backdrop = document.getElementById("legacySettingsBackdrop");
      document.body.classList.toggle("legacy-settings-modal-open", legacySettingsModalOpen);
      if (modal) {
        modal.hidden = !legacySettingsModalOpen;
      }
      if (backdrop) {
        backdrop.hidden = !legacySettingsModalOpen;
      }
      if (legacySettingsModalOpen) {
        setSidebarOpen(false);
      }
    }

    function showSettingsWorkspaceTab(tabName) {
      settingsWorkspaceTab = tabName === "yaml" ? "yaml" : "model";
      const modelPanel = document.getElementById("settingsModelWorkspace");
      const yamlPanel = document.getElementById("settingsYamlPanel");
      const modelTabBtn = document.getElementById("settingsWorkspaceTabModel");
      const yamlTabBtn = document.getElementById("settingsWorkspaceTabYaml");
      const isYaml = settingsWorkspaceTab === "yaml";
      if (modelPanel) modelPanel.hidden = isYaml;
      if (yamlPanel) yamlPanel.hidden = !isYaml;
      if (modelTabBtn) {
        modelTabBtn.classList.toggle("active", !isYaml);
        modelTabBtn.setAttribute("aria-selected", !isYaml ? "true" : "false");
      }
      if (yamlTabBtn) {
        yamlTabBtn.classList.toggle("active", isYaml);
        yamlTabBtn.setAttribute("aria-selected", isYaml ? "true" : "false");
      }
      if (isYaml && !settingsYamlLoaded) {
        loadSettingsDocument();
      }
    }

    function openSettingsModal() {
      showSettingsWorkspaceTab(settingsWorkspaceTab);
      renderSettingsWorkspace(latestStatus);
      setSettingsModalOpen(true);
    }

    function closeSettingsModal() {
      setSettingsModalOpen(false);
    }

    function openLegacySettingsModal() {
      setLegacySettingsModalOpen(true);
    }

    function closeLegacySettingsModal() {
      setLegacySettingsModalOpen(false);
    }

    function syncSegmentedControl(targetId) {
      const currentValue = String(document.getElementById(targetId)?.value || "");
      document.querySelectorAll(`.settings-segmented[data-target="${targetId}"] .settings-segment-btn`).forEach((button) => {
        const active = String(button.dataset.value || "") === currentValue;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }

    function setSegmentedControlValue(targetId, value) {
      const input = document.getElementById(targetId);
      if (!input) return;
      input.value = String(value || "");
      syncSegmentedControl(targetId);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function renderSelectedModelSettings(statusPayload) {
      const selectedModel = resolveSelectedSettingsModel(statusPayload);
      const selectedModelId = String(selectedModel?.id || "");
      const modelNameField = document.getElementById("modelName");
      const modelIdentityMeta = document.getElementById("modelIdentityMeta");
      const capabilitiesChips = document.getElementById("modelCapabilitiesChips");
      const capabilitiesText = document.getElementById("modelCapabilitiesText");
      const statusEl = document.getElementById("modelSettingsStatus");
      const saveBtn = document.getElementById("saveModelSettingsBtn");
      const discardBtn = document.getElementById("discardModelSettingsBtn");
      const projectorBtn = document.getElementById("downloadProjectorBtn");
      const projectorStatus = document.getElementById("projectorStatusText");
      const visionSection = document.getElementById("settingsVisionSection");
      const visionEnabled = document.getElementById("visionEnabled");
      if (!selectedModel) {
        clearModelSettingsDraftState();
        displayedSettingsModelId = "";
        if (modelNameField) modelNameField.textContent = "No model selected";
        if (modelIdentityMeta) modelIdentityMeta.replaceChildren();
        if (capabilitiesText) capabilitiesText.textContent = "Register or upload a model to configure it.";
        if (capabilitiesChips) capabilitiesChips.replaceChildren();
        if (statusEl) statusEl.textContent = "No models registered yet.";
        if (saveBtn) saveBtn.disabled = true;
        if (discardBtn) {
          discardBtn.hidden = true;
          discardBtn.disabled = true;
        }
        if (projectorBtn) projectorBtn.disabled = true;
        if (visionSection) visionSection.hidden = true;
        return;
      }

      const chat = normalizeChatSettings(selectedModel?.settings?.chat);
      const vision = normalizeVisionSettings(selectedModel?.settings?.vision);
      const supportsVision = Boolean(selectedModel?.capabilities?.vision);
      const projector = normalizeProjectorStatus(selectedModel?.projector, vision);
      const preserveDraft = (
        (modelSettingsDraftDirty && modelSettingsDraftModelId === selectedModelId)
        || (
          displayedSettingsModelId === selectedModelId
          && modelSettingsFormHasUnsavedValues(chat, vision)
        )
        || isEditingModelSettingsField()
      );

      if (modelNameField) {
        modelNameField.textContent = String(selectedModel?.filename || "");
      }
      if (modelIdentityMeta) {
        modelIdentityMeta.replaceChildren();
        const metaBits = [];
        if (selectedModel?.storage?.location === "ssd") metaBits.push("Stored on SSD");
        else if (selectedModel?.storage?.location) metaBits.push(`Stored on ${String(selectedModel.storage.location).toUpperCase()}`);
        if (selectedModel?.source_type === "url") metaBits.push("Added from URL");
        else if (selectedModel?.source_type === "upload") metaBits.push("Uploaded locally");
        if (selectedModel?.status === "failed" && selectedModel?.error) {
          metaBits.push(String(selectedModel.error));
        }
        for (const bit of metaBits) {
          const item = document.createElement("span");
          item.className = "selected-model-meta-item";
          item.textContent = String(bit);
          modelIdentityMeta.appendChild(item);
        }
      }
      if (capabilitiesText) {
        const bits = [
          selectedModel?.is_active ? "Active" : "Inactive",
          selectedModel?.status ? `Status: ${formatModelStatusLabel(selectedModel.status)}` : "",
          supportsVision ? "Vision capable" : "Text only",
        ].filter(Boolean);
        capabilitiesText.textContent = bits.join(" · ");
      }
      if (capabilitiesChips) {
        const chipSpecs = [
          {
            kind: "active",
            text: selectedModel?.is_active ? "Active" : "Inactive",
          },
          {
            kind: "status",
            text: formatModelStatusLabel(selectedModel?.status),
          },
          {
            kind: "vision",
            text: supportsVision ? "Vision" : "Text only",
          },
        ];
        capabilitiesChips.replaceChildren(...chipSpecs.map((chip) => {
          const node = document.createElement("span");
          node.className = "settings-chip";
          node.dataset.kind = String(chip.kind || "");
          node.textContent = String(chip.text || "");
          return node;
        }));
      }
      if (statusEl) {
        if (preserveDraft) {
          statusEl.textContent = "Unsaved changes.";
        } else {
          const currentText = String(statusEl.textContent || "");
          const keepRecentSuccess = (
            modelSettingsStatusModelId === selectedModelId
            && /updated|saved/i.test(currentText)
          );
          if (!keepRecentSuccess) {
            statusEl.textContent = selectedModel?.status === "failed"
              ? `Model state: ${String(selectedModel?.error || "failed")}`
              : "Update the selected model profile and save to persist it.";
          }
        }
      }

      if (!preserveDraft) {
        document.getElementById("systemPrompt").value = chat.system_prompt;
        document.getElementById("generationMode").value = chat.generation_mode;
        document.getElementById("seed").value = String(chat.seed);
        document.getElementById("temperature").value = String(chat.temperature);
        document.getElementById("top_p").value = String(chat.top_p);
        document.getElementById("top_k").value = String(chat.top_k);
        document.getElementById("repetition_penalty").value = String(chat.repetition_penalty);
        document.getElementById("presence_penalty").value = String(chat.presence_penalty);
        document.getElementById("max_tokens").value = String(chat.max_tokens);
        syncSegmentedControl("generationMode");
        updateSeedFieldState(chat.generation_mode);
        displayedSettingsModelId = selectedModelId;
      }

      if (visionSection) {
        visionSection.hidden = !supportsVision;
      }
      if (visionEnabled) {
        visionEnabled.checked = supportsVision ? vision.enabled : false;
        visionEnabled.disabled = !supportsVision;
      }
      if (projectorStatus) {
        if (!supportsVision) {
          projectorStatus.textContent = "This model does not use a vision encoder.";
        } else if (projector.present) {
          projectorStatus.textContent = `Vision encoder ready: ${projector.filename || projector.defaultFilename || "available"}`;
        } else {
          projectorStatus.textContent = `No encoder installed. Default: ${projector.defaultFilename || "unknown"}`;
        }
      }
      if (projectorBtn) {
        projectorBtn.hidden = !supportsVision;
        projectorBtn.disabled = !supportsVision || projectorDownloadInFlight;
        projectorBtn.dataset.modelId = String(selectedModel?.id || "");
        projectorBtn.dataset.projectorFilename = supportsVision
          ? String(projector.filename || vision.projector_filename || "")
          : "";
        projectorBtn.textContent = projector.present ? "Re-download vision encoder" : "Download vision encoder";
      }
      if (saveBtn) {
        saveBtn.disabled = modelSettingsSaveInFlight;
      }
      if (discardBtn) {
        const hasUnsavedChanges = selectedModelHasUnsavedChanges(statusPayload);
        discardBtn.hidden = !hasUnsavedChanges;
        discardBtn.disabled = modelSettingsSaveInFlight || !hasUnsavedChanges;
      }
    }

    function renderModelsList(statusPayload) {
      const container = document.getElementById("modelsList");
      if (!container) return;
      const models = Array.isArray(statusPayload?.models) ? statusPayload.models : [];
      const ssdAvailable = statusPayload?.storage_targets?.ssd?.available === true;
      const selectedModel = resolveSelectedSettingsModel(statusPayload);
      container.replaceChildren();
      if (models.length === 0) {
        const empty = document.createElement("div");
        empty.className = "runtime-compact";
        empty.textContent = "No models registered yet.";
        container.appendChild(empty);
        return;
      }

      for (const model of models) {
        const row = document.createElement("div");
        row.className = "model-row";
        row.dataset.modelId = String(model?.id || "");
        if (String(model?.id || "") === String(selectedModel?.id || "")) {
          row.classList.add("selected");
        }

        const head = document.createElement("div");
        head.className = "model-row-head";
        const name = document.createElement("span");
        name.className = "model-row-name";
        name.textContent = String(model?.filename || "unknown.gguf");
        const status = document.createElement("span");
        status.className = "model-status-pill";
        status.textContent = formatModelStatusLabel(model?.status);
        head.appendChild(name);
        head.appendChild(status);

        const meta = document.createElement("div");
        meta.className = "model-row-meta";
        const metaBits = [];
        if (model?.is_active === true) metaBits.push("Active");
        if (model?.capabilities?.vision) metaBits.push("Vision");
        if (model?.storage?.location === "ssd") metaBits.push("SSD");
        if (String(model?.source_type || "") === "url") metaBits.push("URL");
        for (const bit of metaBits) {
          const chip = document.createElement("span");
          chip.className = "model-mini-chip";
          chip.textContent = String(bit);
          meta.appendChild(chip);
        }

        const actions = document.createElement("div");
        actions.className = "model-row-actions";
        if (model?.status === "downloading") {
          const cancelBtn = document.createElement("button");
          cancelBtn.type = "button";
          cancelBtn.className = "ghost-btn";
          cancelBtn.dataset.action = "cancel-download";
          cancelBtn.textContent = "Stop download";
          cancelBtn.title = "Stop the active download for this model";
          actions.appendChild(cancelBtn);
        } else if (model?.status !== "ready" && model?.source_type === "url") {
          const downloadBtn = document.createElement("button");
          downloadBtn.type = "button";
          downloadBtn.className = "ghost-btn";
          downloadBtn.dataset.action = "download";
          downloadBtn.textContent = model?.status === "failed" ? "Resume download" : "Download";
          actions.appendChild(downloadBtn);
        }
        if (model?.is_active !== true && model?.status === "ready") {
          const activeBtn = document.createElement("button");
          activeBtn.type = "button";
          activeBtn.className = "ghost-btn";
          activeBtn.dataset.action = "activate";
          activeBtn.textContent = "Set active";
          actions.appendChild(activeBtn);
        }
        if (ssdAvailable && model?.status === "ready" && model?.storage?.location !== "ssd") {
          const ssdBtn = document.createElement("button");
          ssdBtn.type = "button";
          ssdBtn.className = "ghost-btn";
          ssdBtn.dataset.action = "move-to-ssd";
          ssdBtn.textContent = "Move to SSD";
          ssdBtn.title = "Copy this model to the attached SSD and keep using it from there";
          actions.appendChild(ssdBtn);
        }
        if (String(model?.id || "").length > 0) {
          const deleteBtn = document.createElement("button");
          deleteBtn.type = "button";
          deleteBtn.className = "ghost-btn danger-btn";
          deleteBtn.dataset.action = "delete";
          deleteBtn.textContent = model?.status === "downloading" ? "Cancel + delete" : "Delete model";
          actions.appendChild(deleteBtn);
        }
        if (model?.is_active === true) {
          const activeLabel = document.createElement("span");
          activeLabel.className = "runtime-compact";
          activeLabel.textContent = "Active model";
          actions.appendChild(activeLabel);
        }
        if (model?.storage?.location === "ssd") {
          const storageLabel = document.createElement("span");
          storageLabel.className = "runtime-compact";
          storageLabel.textContent = "On SSD";
          actions.appendChild(storageLabel);
        }
        if (model?.status === "downloading") {
          const progress = document.createElement("span");
          progress.className = "runtime-compact";
          progress.textContent = `Downloading ${Number(model?.percent || 0)}% (${formatBytes(model?.bytes_downloaded)} / ${formatBytes(model?.bytes_total)})`;
          actions.appendChild(progress);
        } else if (model?.status === "failed" && Number(model?.bytes_total || 0) > 0) {
          const progress = document.createElement("span");
          progress.className = "runtime-compact";
          progress.textContent = `Failed at ${formatBytes(model?.bytes_downloaded)} / ${formatBytes(model?.bytes_total)}`;
          actions.appendChild(progress);
        }
        row.appendChild(head);
        if (meta.childElementCount > 0) {
          row.appendChild(meta);
        }
        row.appendChild(actions);
        container.appendChild(row);
      }
    }

    function renderSettingsWorkspace(statusPayload) {
      renderModelsList(statusPayload);
      if (!shouldPauseSelectedModelSettingsRender()) {
        renderSelectedModelSettings(statusPayload);
      }
      showSettingsWorkspaceTab(settingsWorkspaceTab);
    }

    async function loadSettingsDocument() {
      if (settingsYamlRequestInFlight) return;
      settingsYamlRequestInFlight = true;
      const statusEl = document.getElementById("settingsYamlStatus");
      if (statusEl) statusEl.textContent = "Loading YAML...";
      try {
        const res = await fetch("/internal/settings-document", { cache: "no-store" });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          if (statusEl) statusEl.textContent = `Could not load YAML (${body?.reason || res.status}).`;
          return;
        }
        document.getElementById("settingsYamlInput").value = String(body?.document || "");
        settingsYamlLoaded = true;
        if (statusEl) statusEl.textContent = "YAML loaded.";
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not load YAML: ${err}`;
      } finally {
        settingsYamlRequestInFlight = false;
      }
    }

    async function applySettingsDocument() {
      if (settingsYamlRequestInFlight) return;
      settingsYamlRequestInFlight = true;
      const statusEl = document.getElementById("settingsYamlStatus");
      if (statusEl) statusEl.textContent = "Applying YAML...";
      try {
        const documentText = String(document.getElementById("settingsYamlInput").value || "");
        const { res, body } = await postJson("/internal/settings-document", { document: documentText });
        if (!res.ok) {
          if (statusEl) statusEl.textContent = `Could not apply YAML (${body?.reason || res.status}).`;
          return;
        }
        clearModelSettingsDraftState();
        displayedSettingsModelId = "";
        modelSettingsStatusModelId = "";
        if (body?.active_model_id) {
          selectedSettingsModelId = String(body.active_model_id);
        }
        if (statusEl) statusEl.textContent = "YAML applied.";
        settingsYamlLoaded = true;
        await pollStatus();
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not apply YAML: ${err}`;
      } finally {
        settingsYamlRequestInFlight = false;
      }
    }

    async function saveSelectedModelSettings() {
      const selectedModel = resolveSelectedSettingsModel(latestStatus);
      if (!selectedModel || modelSettingsSaveInFlight) return;
      modelSettingsSaveInFlight = true;
      const statusEl = document.getElementById("modelSettingsStatus");
      const saveBtn = document.getElementById("saveModelSettingsBtn");
      const discardBtn = document.getElementById("discardModelSettingsBtn");
      if (saveBtn) saveBtn.disabled = true;
      if (discardBtn) discardBtn.disabled = true;
      if (statusEl) statusEl.textContent = "Saving model settings...";
      try {
        const settings = collectSelectedModelSettings();
        const { res, body } = await postJson("/internal/models/settings", {
          model_id: selectedModel.id,
          settings,
        });
        if (!res.ok) {
          if (statusEl) statusEl.textContent = `Could not save model settings (${body?.reason || res.status}).`;
          return;
        }
        clearModelSettingsDraftState();
        displayedSettingsModelId = "";
        modelSettingsStatusModelId = String(selectedModel?.id || "");
        if (statusEl) statusEl.textContent = "Model settings updated.";
        await pollStatus();
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not save model settings: ${err}`;
      } finally {
        modelSettingsSaveInFlight = false;
        if (saveBtn) saveBtn.disabled = false;
        if (discardBtn) discardBtn.disabled = false;
      }
    }

    async function downloadProjectorForSelectedModel() {
      const selectedModel = resolveSelectedSettingsModel(latestStatus);
      if (!selectedModel || projectorDownloadInFlight) return;
      projectorDownloadInFlight = true;
      const statusEl = document.getElementById("projectorStatusText");
      const button = document.getElementById("downloadProjectorBtn");
      if (button) button.disabled = true;
      if (statusEl) statusEl.textContent = "Downloading vision encoder...";
      try {
        const { res, body } = await postJson("/internal/models/download-projector", { model_id: selectedModel.id });
        if (!res.ok) {
          if (statusEl) statusEl.textContent = `Could not download encoder (${body?.reason || res.status}).`;
          return;
        }
        if (button) {
          button.dataset.projectorFilename = String(body?.projector_filename || "");
        }
        if (statusEl) statusEl.textContent = `Vision encoder ready: ${body?.projector_filename || "downloaded"}`;
        await pollStatus();
      } catch (err) {
        if (statusEl) statusEl.textContent = `Could not download encoder: ${err}`;
      } finally {
        projectorDownloadInFlight = false;
        if (button) button.disabled = false;
      }
    }

    function setEditModalOpen(open) {
      editModalOpen = Boolean(open);
      const modal = document.getElementById("editModal");
      const backdrop = document.getElementById("editBackdrop");
      document.body.classList.toggle("edit-modal-open", editModalOpen);
      if (modal) {
        modal.hidden = !editModalOpen;
      }
      if (backdrop) {
        backdrop.hidden = !editModalOpen;
      }
      if (editModalOpen) {
        setSidebarOpen(false);
      }
    }

    function closeEditMessageModal(options = {}) {
      activeEditState = null;
      setEditModalOpen(false);
      if (options.restoreFocus !== false) {
        focusPromptInput();
      }
    }

    function setEditModalBusy(busy) {
      const input = document.getElementById("editMessageInput");
      const sendBtn = document.getElementById("editSendBtn");
      const cancelBtn = document.getElementById("editCancelBtn");
      const closeBtn = document.getElementById("editCloseBtn");
      if (input) input.disabled = Boolean(busy);
      if (sendBtn) sendBtn.disabled = Boolean(busy);
      if (cancelBtn) cancelBtn.disabled = Boolean(busy);
      if (closeBtn) closeBtn.disabled = Boolean(busy);
    }

    function updateEditModalCopy(state) {
      const note = document.getElementById("editModalNote");
      const hint = document.getElementById("editModalHint");
      const sendBtn = document.getElementById("editSendBtn");
      const isGenerating = Boolean(state?.wasGenerating);
      if (note) {
        note.textContent = isGenerating
          ? "Update the message, stop the current reply, and restart from this point."
          : "Update the message and resend from this point in the conversation.";
      }
      if (hint) {
        hint.textContent = isGenerating
          ? "Sending will cancel the in-progress response and replace everything from this turn onward."
          : "Everything after this turn will be replaced by the new run.";
      }
      if (sendBtn) {
        sendBtn.textContent = isGenerating ? "Cancel & send" : "Send";
      }
    }

    function openEditMessageModal(messageView) {
      const turn = messageView?.turnRef;
      if (!turn || messageView?.role !== "user") return;
      const input = document.getElementById("editMessageInput");
      activeEditState = {
        turn,
        wasGenerating: Boolean(requestInFlight),
      };
      updateEditModalCopy(activeEditState);
      if (input) {
        input.value = String(turn.userText || messageView.editText || "");
      }
      setEditModalBusy(false);
      setEditModalOpen(true);
      window.setTimeout(() => {
        if (!input) return;
        input.focus({ preventScroll: true });
        if (typeof input.setSelectionRange === "function") {
          input.setSelectionRange(input.value.length, input.value.length);
        }
      }, 0);
    }

    function getMessagesBox() {
      return document.getElementById("messages");
    }

    function isMessagesPinned(box = getMessagesBox()) {
      if (!box) return true;
      return (box.scrollHeight - box.clientHeight - box.scrollTop) <= 24;
    }

    function setMessagesPinnedState(pinned) {
      messagesPinnedToBottom = Boolean(pinned);
    }

    function createMessageActionIcon(kind) {
      const svgNS = "http://www.w3.org/2000/svg";
      const svg = document.createElementNS(svgNS, "svg");
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("aria-hidden", "true");
      if (kind === "copy") {
        const back = document.createElementNS(svgNS, "rect");
        back.setAttribute("x", "9");
        back.setAttribute("y", "4");
        back.setAttribute("width", "11");
        back.setAttribute("height", "13");
        back.setAttribute("rx", "2");
        const front = document.createElementNS(svgNS, "rect");
        front.setAttribute("x", "4");
        front.setAttribute("y", "9");
        front.setAttribute("width", "11");
        front.setAttribute("height", "11");
        front.setAttribute("rx", "2");
        svg.appendChild(back);
        svg.appendChild(front);
        return svg;
      }
      const path = document.createElementNS(svgNS, "path");
      path.setAttribute("d", "M4 20h4l10-10a2.5 2.5 0 0 0-4-4L4 16v4");
      const tip = document.createElementNS(svgNS, "path");
      tip.setAttribute("d", "M13.5 6.5l4 4");
      svg.appendChild(path);
      svg.appendChild(tip);
      return svg;
    }

    async function copyTextToClipboard(text) {
      const value = String(text || "");
      if (!value) return false;
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(value);
        return true;
      }
      const probe = document.createElement("textarea");
      probe.value = value;
      probe.setAttribute("readonly", "readonly");
      probe.style.position = "fixed";
      probe.style.top = "-9999px";
      probe.style.opacity = "0";
      document.body.appendChild(probe);
      probe.focus();
      probe.select();
      try {
        return document.execCommand("copy");
      } finally {
        document.body.removeChild(probe);
      }
    }

    function flashCopiedState(button) {
      if (!button) return;
      button.dataset.copied = "true";
      button.setAttribute("title", "Copied");
      window.setTimeout(() => {
        if (!button.isConnected) return;
        delete button.dataset.copied;
        button.setAttribute("title", "Copy message");
      }, 1400);
    }

    function populatePromptForEditing(text) {
      const prompt = document.getElementById("userPrompt");
      if (!prompt) return;
      prompt.value = String(text || "");
      focusPromptInput();
    }

    function createMessageActions(messageView, options = {}) {
      const actions = document.createElement("div");
      actions.className = "message-actions";
      actions.dataset.visible = "false";

      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "message-action-btn";
      copyBtn.dataset.action = "copy";
      copyBtn.setAttribute("aria-label", "Copy message");
      copyBtn.setAttribute("title", "Copy message");
      copyBtn.appendChild(createMessageActionIcon("copy"));
      copyBtn.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        try {
          const copied = await copyTextToClipboard(messageView.copyText || messageView.editText || messageView.bubble?.innerText || "");
          if (copied) {
            flashCopiedState(copyBtn);
          }
        } catch (error) {
          console.warn("Clipboard copy failed", error);
        }
      });
      actions.appendChild(copyBtn);

      if (options.editable === true) {
        const editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "message-action-btn";
        editBtn.dataset.action = "edit";
        editBtn.setAttribute("aria-label", "Edit message");
        editBtn.setAttribute("title", "Edit message");
        editBtn.appendChild(createMessageActionIcon("edit"));
        editBtn.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          openEditMessageModal(messageView);
        });
        actions.appendChild(editBtn);
      }

      return actions;
    }

    function setMessageActionsVisible(messageView, visible) {
      if (!messageView?.actions) return;
      if (messageView.row) {
        messageView.row.classList.toggle("message-row-actions-hidden", !visible);
      }
      messageView.actions.hidden = !visible;
      messageView.actions.dataset.visible = visible ? "true" : "false";
    }

    function hasActiveMessageSelection(box = getMessagesBox()) {
      if (!box || typeof window === "undefined" || typeof window.getSelection !== "function") {
        return false;
      }
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || selection.rangeCount < 1) {
        return false;
      }
      const range = selection.getRangeAt(0);
      const container = range.commonAncestorContainer;
      const node = container?.nodeType === Node.TEXT_NODE ? container.parentNode : container;
      return Boolean(node && box.contains(node));
    }

    function handleMessagesChanged(shouldFollow, options = {}) {
      const box = getMessagesBox();
      if (!box) return;
      const forceFollow = options.forceFollow === true;
      if (forceFollow || (shouldFollow && !messagePointerSelectionActive && !hasActiveMessageSelection(box))) {
        box.scrollTop = box.scrollHeight;
        setMessagesPinnedState(true);
      }
    }

    function bindMessagesScroller() {
      const box = getMessagesBox();
      if (!box) return;
      box.addEventListener("pointerdown", (event) => {
        if (event.target instanceof Element && event.target.closest(".message-bubble, .message-meta")) {
          messagePointerSelectionActive = true;
        }
      });
      const clearPointerSelection = () => {
        messagePointerSelectionActive = false;
      };
      box.addEventListener("pointerup", clearPointerSelection);
      box.addEventListener("pointercancel", clearPointerSelection);
      document.addEventListener("pointerup", clearPointerSelection);
      document.addEventListener("selectionchange", () => {
        if (!hasActiveMessageSelection(box) && !document.activeElement?.closest?.(".message-bubble, .message-meta")) {
          messagePointerSelectionActive = false;
        }
      });
      box.addEventListener("scroll", () => {
        setMessagesPinnedState(isMessagesPinned(box));
      });
      setMessagesPinnedState(isMessagesPinned(box));
    }

    function bindMobileSidebar() {
      mobileSidebarMql = window.matchMedia("(max-width: 900px)");
      const sync = () => {
        if (!mobileSidebarMql.matches) {
          setSidebarOpen(false);
        } else {
          setSidebarOpen(document.body.classList.contains("sidebar-open"));
        }
      };

      const onViewportChange = () => {
        sync();
      };
      if (typeof mobileSidebarMql.addEventListener === "function") {
        mobileSidebarMql.addEventListener("change", onViewportChange);
      } else if (typeof mobileSidebarMql.addListener === "function") {
        mobileSidebarMql.addListener(onViewportChange);
      }

      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          if (editModalOpen) {
            closeEditMessageModal();
            return;
          }
          if (legacySettingsModalOpen) {
            closeLegacySettingsModal();
            return;
          }
          if (settingsModalOpen) {
            closeSettingsModal();
            return;
          }
          setSidebarOpen(false);
        }
      });

      sync();
    }

    function bindSettingsModal() {
      document.getElementById("settingsOpenBtn").addEventListener("click", openSettingsModal);
      document.getElementById("settingsCloseBtn").addEventListener("click", closeSettingsModal);
      document.getElementById("settingsBackdrop").addEventListener("click", closeSettingsModal);
      document.getElementById("settingsModal").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) {
          closeSettingsModal();
        }
      });
      document.getElementById("settingsModal").addEventListener("input", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        if (
          target.closest("#systemPrompt, #seed, #temperature, #top_p, #top_k, #repetition_penalty, #presence_penalty, #max_tokens, #visionEnabled")
        ) {
          markModelSettingsDraftDirty();
        }
      });
      document.getElementById("settingsModal").addEventListener("change", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        if (
          target.closest("#systemPrompt, #seed, #temperature, #top_p, #top_k, #repetition_penalty, #presence_penalty, #max_tokens, #visionEnabled")
        ) {
          markModelSettingsDraftDirty();
        }
      });
      document.getElementById("settingsModal").addEventListener("keydown", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        if (
          target.closest("#systemPrompt, #seed, #temperature, #top_p, #top_k, #repetition_penalty, #presence_penalty, #max_tokens")
        ) {
          markModelSettingsDraftDirty();
        }
      });
      document.getElementById("settingsAdvancedBtn").addEventListener("click", openLegacySettingsModal);
      document.getElementById("settingsWorkspaceTabModel").addEventListener("click", () => {
        showSettingsWorkspaceTab("model");
      });
      document.getElementById("settingsWorkspaceTabYaml").addEventListener("click", () => {
        showSettingsWorkspaceTab("yaml");
      });
      document.getElementById("saveModelSettingsBtn").addEventListener("click", saveSelectedModelSettings);
      document.getElementById("discardModelSettingsBtn").addEventListener("click", discardSelectedModelSettings);
      document.getElementById("downloadProjectorBtn").addEventListener("click", downloadProjectorForSelectedModel);
      document.getElementById("settingsYamlReloadBtn").addEventListener("click", loadSettingsDocument);
      document.getElementById("settingsYamlApplyBtn").addEventListener("click", applySettingsDocument);
      document.querySelectorAll(".settings-segment-btn").forEach((button) => {
        button.addEventListener("click", () => {
          markModelSettingsDraftDirty();
          setSegmentedControlValue(String(button.dataset.target || ""), String(button.dataset.value || ""));
        });
      });
      document.getElementById("generationMode").addEventListener("change", (event) => {
        updateSeedFieldState(normalizeGenerationMode(event.target?.value));
      });
      syncSegmentedControl("generationMode");
      document.getElementById("legacySettingsCloseBtn").addEventListener("click", closeLegacySettingsModal);
      document.getElementById("legacySettingsBackdrop").addEventListener("click", closeLegacySettingsModal);
      document.getElementById("legacySettingsModal").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) {
          closeLegacySettingsModal();
        }
      });
    }

    function bindEditModal() {
      document.getElementById("editCloseBtn").addEventListener("click", () => closeEditMessageModal());
      document.getElementById("editCancelBtn").addEventListener("click", () => closeEditMessageModal());
      document.getElementById("editBackdrop").addEventListener("click", () => closeEditMessageModal());
      document.getElementById("editModal").addEventListener("click", (event) => {
        if (event.target === event.currentTarget) {
          closeEditMessageModal();
        }
      });
      document.getElementById("editSendBtn").addEventListener("click", submitEditMessageModal);
      document.getElementById("editMessageInput").addEventListener("keydown", (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
          event.preventDefault();
          submitEditMessageModal();
        }
      });
    }

    function applyTheme(theme) {
      const resolved = theme === "light" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", resolved);
      const toggle = document.getElementById("themeToggle");
      const target = resolved === "dark" ? "light" : "dark";
      toggle.setAttribute("aria-label", `Switch to ${target} theme`);
      toggle.setAttribute("title", `Switch to ${target} theme`);
    }

    function bindSettings() {
      const settings = loadSettings();
      applyTheme(settings.theme);
    }

    function setSendEnabled() {
      const sendBtn = document.getElementById("sendBtn");
      const ready = latestStatus && latestStatus.state === "READY";
      if (requestInFlight) {
        sendBtn.disabled = false;
        sendBtn.textContent = "Stop";
        sendBtn.classList.add("stop-mode");
        renderComposerCapabilities(latestStatus);
        return;
      }
      sendBtn.textContent = "Send";
      sendBtn.classList.remove("stop-mode");
      sendBtn.disabled = !ready;
      renderComposerCapabilities(latestStatus);
    }

    function setComposerActivity(message) {
      const activity = document.getElementById("composerActivity");
      if (!activity) return;
      activity.textContent = String(message || "");
    }

    function setComposerStatusChip(message, options = {}) {
      const chip = document.getElementById("composerStatusChip");
      const text = document.getElementById("composerStatusText");
      if (!chip || !text) return;
      if (statusChipHideTimer) {
        window.clearTimeout(statusChipHideTimer);
        statusChipHideTimer = null;
      }

      const label = String(message || "").trim();
      if (!label) {
        chip.hidden = true;
        text.textContent = "";
        chip.dataset.phase = "idle";
        statusChipVisibleAtMs = 0;
        return;
      }

      if (chip.hidden) {
        statusChipVisibleAtMs = performance.now();
      }
      chip.hidden = false;
      text.textContent = label;
      chip.dataset.phase = String(options.phase || "prefill");
    }

    function hideComposerStatusChip(options = {}) {
      const chip = document.getElementById("composerStatusChip");
      if (!chip) return;
      const immediate = options.immediate === true;
      const elapsedMs = statusChipVisibleAtMs > 0 ? (performance.now() - statusChipVisibleAtMs) : STATUS_CHIP_MIN_VISIBLE_MS;
      const delayMs = immediate ? 0 : Math.max(0, STATUS_CHIP_MIN_VISIBLE_MS - elapsedMs);
      if (statusChipHideTimer) {
        window.clearTimeout(statusChipHideTimer);
      }
      statusChipHideTimer = window.setTimeout(() => {
        statusChipHideTimer = null;
        setComposerStatusChip("");
      }, delayMs);
    }

    function estimateContentChars(content) {
      if (typeof content === "string") {
        return content.length;
      }
      if (!Array.isArray(content)) {
        return 0;
      }
      let chars = 0;
      for (const part of content) {
        if (!part || typeof part !== "object") continue;
        if (part.type === "text" && typeof part.text === "string") {
          chars += part.text.length;
        } else if (part.type === "image_url") {
          chars += 1200;
        }
      }
      return chars;
    }

    function estimatePromptTokens(messages) {
      if (!Array.isArray(messages)) return 0;
      let chars = 0;
      for (const message of messages) {
        if (!message || typeof message !== "object") continue;
        chars += estimateContentChars(message.content);
      }
      return Math.max(1, Math.round(chars / 4));
    }

    function loadPrefillMetrics() {
      const raw = localStorage.getItem(PREFILL_METRICS_KEY);
      if (!raw) return {};
      try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? parsed : {};
      } catch (_err) {
        return {};
      }
    }

    function savePrefillMetrics(metrics) {
      localStorage.setItem(PREFILL_METRICS_KEY, JSON.stringify(metrics));
    }

    function choosePrefillBucket(hasImage, promptTokens, imageBytes) {
      const largeText = promptTokens > 1300;
      const largeImage = imageBytes > 120 * 1024;
      const size = largeText || largeImage ? "large" : "small";
      return `${hasImage ? "vision" : "text"}_${size}`;
    }

    function estimatePrefillEtaMs(hasImage, promptTokens, imageBytes, bucket) {
      const isBigImage = hasImage && imageBytes >= (120 * 1024);
      const baseMs = hasImage ? (isBigImage ? 8500 : 5400) : 1800;
      const promptMs = Math.round(Math.max(0, promptTokens) * 7);
      const imageMs = hasImage
        ? Math.round((Math.max(0, imageBytes) / 1024) * (isBigImage ? 80 : 28))
        : 0;
      let estimateMs = baseMs + promptMs + imageMs;

      const metrics = loadPrefillMetrics();
      const sample = metrics[bucket];
      const learnedMs = Number(sample?.ewma_ms);
      const learnedCount = Number(sample?.count);
      if (Number.isFinite(learnedMs) && learnedMs > 0 && Number.isFinite(learnedCount) && learnedCount >= 1) {
        estimateMs = Math.round((estimateMs * 0.55) + (learnedMs * 0.45));
      }

      const etaMs = Math.max(1500, Math.min(120000, estimateMs));
      return { etaMs };
    }

    function beginPrefillProgress(requestCtx, options) {
      stopPrefillProgress({ resetUi: false });
      const hasImage = Boolean(options?.hasImage);
      const promptTokens = Number(options?.promptTokens) || 0;
      const imageBytes = Number(options?.imageBytes) || 0;
      const bucket = String(options?.bucket || choosePrefillBucket(hasImage, promptTokens, imageBytes));
      const estimate = estimatePrefillEtaMs(hasImage, promptTokens, imageBytes, bucket);
      const initialProgress = hasImage ? 14 : PREFILL_PROGRESS_FLOOR;

      activePrefillProgress = {
        requestCtx,
        bucket,
        startedAtMs: performance.now(),
        etaMs: estimate.etaMs,
        progress: initialProgress,
        timerId: null,
        finishTimerId: null,
        finishPromise: null,
        finishResolve: null,
      };

      setComposerActivity("Preparing prompt...");
      applyPrefillProgressState(requestCtx, initialProgress);

      activePrefillProgress.timerId = window.setInterval(() => {
        const active = activePrefillProgress;
        if (!active || active.requestCtx !== requestCtx) return;
        const elapsedMs = Math.max(0, performance.now() - active.startedAtMs);
        const normalized = Math.max(0, elapsedMs / Math.max(active.etaMs, 1));
        const eased = 1 - Math.exp(-3.2 * Math.min(1.4, normalized));
        let target = PREFILL_PROGRESS_FLOOR + ((95 - PREFILL_PROGRESS_FLOOR) * eased);
        if (normalized > 0.75) {
          target -= Math.min(2.8, (normalized - 0.75) * 7.5);
        }
        if (elapsedMs > active.etaMs) {
          const overtimeSeconds = (elapsedMs - active.etaMs) / 1000;
          const tail = Math.min(
            PREFILL_PROGRESS_CAP - PREFILL_PROGRESS_TAIL_START,
            Math.log1p(overtimeSeconds) * 2.6
          );
          target = Math.max(target, PREFILL_PROGRESS_TAIL_START + tail);
        }
        active.progress = Math.max(active.progress, Math.min(PREFILL_PROGRESS_CAP, target));
        const percent = Math.round(Math.min(PREFILL_PROGRESS_CAP, active.progress));
        applyPrefillProgressState(requestCtx, percent);
      }, PREFILL_TICK_MS);
    }

    function markPrefillGenerationStarted(requestCtx) {
      const active = activePrefillProgress;
      if (!active || active.requestCtx !== requestCtx) return Promise.resolve({ cancelled: false });
      if (active.finishPromise) {
        return active.finishPromise;
      }
      if (active.timerId !== null) {
        window.clearInterval(active.timerId);
      }
      active.timerId = null;
      const startPercent = Math.max(
        PREFILL_PROGRESS_FLOOR,
        Math.min(PREFILL_PROGRESS_CAP, Math.round(Number(active.progress) || PREFILL_PROGRESS_FLOOR)),
      );
      active.finishPromise = new Promise((resolve) => {
        active.finishResolve = resolve;
        const startedAtMs = performance.now();

        const finalize = (cancelled = false) => {
          if (active.finishTimerId !== null) {
            window.clearTimeout(active.finishTimerId);
            active.finishTimerId = null;
          }
          active.finishResolve = null;
          active.finishPromise = null;
          if (activePrefillProgress && activePrefillProgress.requestCtx === requestCtx) {
            activePrefillProgress = null;
          }
          if (cancelled) {
            resolve({ cancelled: true });
            return;
          }
          applyPrefillProgressState(requestCtx, 100);
          active.finishTimerId = window.setTimeout(() => {
            if (active.finishTimerId !== null) {
              window.clearTimeout(active.finishTimerId);
              active.finishTimerId = null;
            }
            hideComposerStatusChip({ immediate: true });
            resolve({ cancelled: false });
          }, PREFILL_FINISH_HOLD_MS);
        };

        const step = () => {
          if (requestCtx?.stoppedByUser === true) {
            finalize(true);
            return;
          }
          const elapsedMs = Math.max(0, performance.now() - startedAtMs);
          const progress = Math.min(1, elapsedMs / PREFILL_FINISH_DURATION_MS);
          const eased = 1 - Math.pow(1 - progress, 2);
          const nextPercent = startPercent + ((100 - startPercent) * eased);
          active.progress = nextPercent;
          applyPrefillProgressState(requestCtx, nextPercent);
          if (progress >= 1) {
            finalize(false);
            return;
          }
          active.finishTimerId = window.setTimeout(step, PREFILL_FINISH_TICK_MS);
        };

        step();
      });
      return active.finishPromise;
    }

    function stopPrefillProgress(options = {}) {
      const active = activePrefillProgress;
      if (active && active.timerId !== null) {
        window.clearInterval(active.timerId);
      }
      if (active && active.finishTimerId !== null) {
        window.clearTimeout(active.finishTimerId);
      }
      if (active && typeof active.finishResolve === "function") {
        active.finishResolve({ cancelled: true });
      }
      activePrefillProgress = null;
      if (options.resetUi !== false) {
        hideComposerStatusChip();
      }
    }

    function resolvePromptPrefillMs(source, fallbackMs = 0) {
      const direct = Number(source?.timings?.prompt_ms);
      if (Number.isFinite(direct) && direct > 0) {
        return direct;
      }
      const fallback = Number(fallbackMs);
      if (Number.isFinite(fallback) && fallback > 0) {
        return fallback;
      }
      return 0;
    }

    function recordPrefillMetric(bucket, promptMs) {
      if (!bucket) return;
      const sampleMs = Number(promptMs);
      if (!Number.isFinite(sampleMs) || sampleMs <= 0) return;
      const metrics = loadPrefillMetrics();
      const current = metrics[bucket] && typeof metrics[bucket] === "object" ? metrics[bucket] : {};
      const priorCount = Number(current.count);
      const priorEwma = Number(current.ewma_ms);
      const hasPrior = Number.isFinite(priorCount) && priorCount > 0 && Number.isFinite(priorEwma) && priorEwma > 0;
      const ewmaMs = hasPrior ? ((priorEwma * 0.65) + (sampleMs * 0.35)) : sampleMs;
      metrics[bucket] = {
        count: Math.max(1, Math.min(64, Math.floor((hasPrior ? priorCount : 0) + 1))),
        ewma_ms: Math.round(ewmaMs),
      };
      savePrefillMetrics(metrics);
    }

    function applyPrefillProgressState(requestCtx, percent) {
      const safePercent = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
      setComposerStatusChip(`Preparing prompt • ${safePercent}%`, { phase: "prefill" });
      setMessageProcessingState(requestCtx?.assistantView, {
        phase: "prefill",
        label: "Prompt processing",
        percent: safePercent,
      });
    }

    function setCancelEnabled(enabled) {
      const cancelBtn = document.getElementById("cancelBtn");
      if (!cancelBtn) return;
      const show = Boolean(enabled);
      cancelBtn.hidden = !show;
      cancelBtn.disabled = !show;
    }

    let markdownRendererConfigured = false;

    function renderAssistantMarkdownToHtml(text) {
      const source = String(text || "");
      if (!window.marked?.parse || !window.DOMPurify?.sanitize) {
        return null;
      }
      if (!markdownRendererConfigured && typeof window.marked.setOptions === "function") {
        window.marked.setOptions({
          gfm: true,
          breaks: true,
        });
        markdownRendererConfigured = true;
      }
      const renderedHtml = window.marked?.parse(source) || "";
      return window.DOMPurify?.sanitize(renderedHtml, {
        ALLOWED_TAGS: [
          "a", "blockquote", "br", "code", "em", "h1", "h2", "h3", "h4",
          "li", "ol", "p", "pre", "strong", "ul",
        ],
        ALLOWED_ATTR: ["href", "title"],
      }) || "";
    }

    function throwIfRequestStoppedAfterPrefill(requestCtx, finishResult) {
      if (finishResult?.cancelled || requestCtx?.stoppedByUser) {
        const error = new Error("Request cancelled");
        error.name = "AbortError";
        throw error;
      }
    }

    function renderBubbleContent(bubble, content, options = {}) {
      if (!bubble) return;
      const text = String(content || "");
      const imageDataUrl = typeof options.imageDataUrl === "string" ? options.imageDataUrl : "";
      const imageName = typeof options.imageName === "string" ? options.imageName : "uploaded image";
      const role = String(options.role || "");

      bubble.classList.remove("markdown-rendered");
      if (!imageDataUrl) {
        bubble.classList.remove("with-image");
        if (role === "assistant") {
          const sanitizedHtml = renderAssistantMarkdownToHtml(text);
          if (sanitizedHtml !== null) {
            bubble.classList.add("markdown-rendered");
            bubble.innerHTML = sanitizedHtml;
            return;
          }
        }
        bubble.textContent = text;
        return;
      }

      bubble.classList.add("with-image");
      bubble.replaceChildren();
      const thumbnail = document.createElement("img");
      thumbnail.className = "message-image-thumb";
      thumbnail.src = imageDataUrl;
      thumbnail.alt = `Uploaded image: ${imageName}`;
      thumbnail.loading = "lazy";
      bubble.appendChild(thumbnail);

      if (text) {
        const caption = document.createElement("div");
        caption.className = "message-text";
        caption.textContent = text;
        bubble.appendChild(caption);
      }
    }

    function appendMessage(role, content = "", options = {}) {
      const box = document.getElementById("messages");
      const forceFollow = options.forceFollow === true;
      const shouldFollow = forceFollow ? true : isMessagesPinned(box);
      const row = document.createElement("div");
      row.className = `message-row ${role}`;

      const stack = document.createElement("div");
      stack.className = "message-stack";

      const bubble = document.createElement("div");
      bubble.className = "message-bubble";
      renderBubbleContent(bubble, content, { ...options, role });

      const messageView = {
        row,
        stack,
        bubble,
        role,
        copyText: String(options.copyText ?? content ?? ""),
        editText: String(options.editText ?? content ?? ""),
      };

      const actions = createMessageActions(messageView, {
        editable: role === "user" && !options.imageDataUrl && !options.imageName,
      });
      messageView.actions = actions;

      const meta = document.createElement("div");
      meta.className = "message-meta";
      meta.hidden = true;
      messageView.meta = meta;

      stack.appendChild(bubble);
      stack.appendChild(actions);
      stack.appendChild(meta);
      row.appendChild(stack);
      box.appendChild(row);
      const actionsVisible = options.actionsHidden === true ? false : Boolean(String(content || "").trim());
      setMessageActionsVisible(messageView, actionsVisible);
      handleMessagesChanged(shouldFollow, { forceFollow });
      return messageView;
    }

    function setMessageProcessingState(messageView, options = {}) {
      const bubble = messageView?.bubble || messageView;
      if (!bubble) return;
      const box = document.getElementById("messages");
      const shouldFollow = isMessagesPinned(box);
      const phase = String(options.phase || "prefill");
      const percentRaw = Number(options.percent);
      const percent = Number.isFinite(percentRaw)
        ? Math.max(0, Math.min(100, Math.round(percentRaw)))
        : null;
      const label = String(options.label || "Prompt processing");

      bubble.classList.remove("with-image");
      bubble.classList.add("processing");
      bubble.dataset.phase = phase;
      bubble.replaceChildren();
      setMessageActionsVisible(messageView, false);

      const shell = document.createElement("div");
      shell.className = "message-processing-shell";

      const labelEl = document.createElement("div");
      labelEl.className = "message-processing-label";
      labelEl.textContent = label;

      const meter = document.createElement("div");
      meter.className = "message-processing-meter";

      const bar = document.createElement("div");
      bar.className = "message-processing-bar";

      const barFill = document.createElement("div");
      barFill.className = "message-processing-bar-fill";
      if (percent !== null && phase !== "generating") {
        barFill.style.width = `${percent}%`;
      }
      bar.appendChild(barFill);

      const percentEl = document.createElement("div");
      percentEl.className = "message-processing-percent";
      percentEl.textContent = phase === "generating"
        ? "Live"
        : `${percent ?? 0}%`;

      meter.appendChild(bar);
      meter.appendChild(percentEl);

      shell.appendChild(labelEl);
      shell.appendChild(meter);
      bubble.appendChild(shell);
      handleMessagesChanged(shouldFollow);
    }

    function updateMessage(messageView, content, options = {}) {
      const bubble = messageView?.bubble || messageView;
      if (!bubble) return;
      const box = document.getElementById("messages");
      const shouldFollow = isMessagesPinned(box);
      bubble.classList.remove("processing");
      delete bubble.dataset.phase;
      renderBubbleContent(bubble, content, { ...options, role: messageView?.role || options.role });
      if (messageView && typeof messageView === "object") {
        messageView.copyText = String(options.copyText ?? content ?? "");
        if (messageView.role === "user") {
          messageView.editText = String(options.editText ?? content ?? "");
        }
        const requestedVisibility = options.showActions;
        const nextVisibility = requestedVisibility === undefined
          ? messageView.role !== "assistant"
          : Boolean(requestedVisibility);
        setMessageActionsVisible(messageView, nextVisibility);
      }
      handleMessagesChanged(shouldFollow);
    }

    function setMessageMeta(messageView, content) {
      const meta = messageView?.meta;
      if (!meta) return;
      const box = getMessagesBox();
      const shouldFollow = isMessagesPinned(box);
      const text = String(content || "").trim();
      meta.hidden = text.length === 0;
      meta.textContent = text;
      handleMessagesChanged(shouldFollow);
    }

    function removeMessage(messageView) {
      const row = messageView?.row;
      if (row && row.parentNode) {
        row.parentNode.removeChild(row);
      }
    }

    function waitForRequestIdle(timeoutMs = 6000) {
      const deadline = performance.now() + Math.max(250, Number(timeoutMs) || 6000);
      return new Promise((resolve) => {
        const tick = () => {
          if (!requestInFlight || performance.now() >= deadline) {
            resolve(!requestInFlight);
            return;
          }
          window.setTimeout(tick, 40);
        };
        tick();
      });
    }

    function rollbackConversationFromTurn(targetTurn) {
      if (!targetTurn) return;
      const startIndex = conversationTurns.indexOf(targetTurn);
      if (startIndex < 0) return;
      chatHistory.length = Math.max(0, Number(targetTurn.baseHistoryLength) || 0);
      for (let index = conversationTurns.length - 1; index >= startIndex; index -= 1) {
        const turn = conversationTurns[index];
        if (turn?.assistantView) {
          removeMessage(turn.assistantView);
        }
        if (turn?.userView) {
          removeMessage(turn.userView);
        }
      }
      conversationTurns.splice(startIndex);
    }

    async function submitEditMessageModal() {
      if (!activeEditState?.turn) {
        closeEditMessageModal();
        return;
      }
      const input = document.getElementById("editMessageInput");
      const nextText = String(input?.value || "").trim();
      if (!nextText) {
        if (input) {
          input.focus({ preventScroll: true });
        }
        return;
      }

      const { turn } = activeEditState;
      setEditModalBusy(true);

      if (requestInFlight && activeRequest) {
        const current = activeRequest;
        current.hideProcessingBubbleOnCancel = true;
        if (current.assistantView) {
          removeMessage(current.assistantView);
        }
        stopGeneration();
        await waitForRequestIdle();
      }

      rollbackConversationFromTurn(turn);
      closeEditMessageModal({ restoreFocus: false });
      clearPendingImage();
      const prompt = document.getElementById("userPrompt");
      if (prompt) {
        prompt.value = nextText;
      }
      focusPromptInput();
      sendChat();
    }

    function isLocalModelConnected(statusPayload) {
      const backendMode = String(
        statusPayload?.backend?.active
        || statusPayload?.backend?.mode
        || ""
      ).toLowerCase();
      const isReady = String(statusPayload?.state || "").toUpperCase() === "READY";
      const llamaHealthy = statusPayload?.llama_server?.healthy === true;
      return backendMode === "llama" && isReady && llamaHealthy;
    }

    function updateLlamaIndicator(statusPayload) {
      const badge = document.getElementById("statusBadge");
      const dot = document.getElementById("statusDot");
      const spinner = document.getElementById("statusSpinner");
      const label = document.getElementById("statusLabel");
      if (!badge || !dot || !label) return;
      const backendMode = String(
        statusPayload?.backend?.active
        || statusPayload?.backend?.mode
        || ""
      ).toLowerCase();
      const modelFilename = String(statusPayload?.model?.filename || "").trim();
      const modelSuffix = modelFilename ? `:${modelFilename}` : "";
      const activeModelStorage = String(statusPayload?.model?.storage?.location || "").toLowerCase();
      const storageSuffix = activeModelStorage === "ssd" ? ":SSD" : "";
      const isReady = String(statusPayload?.state || "").toUpperCase() === "READY";
      const statusState = String(statusPayload?.state || "").toUpperCase();
      const hasModel = statusPayload?.model_present === true;
      const llamaHealthy = statusPayload?.llama_server?.healthy === true;
      const isHealthy = isLocalModelConnected(statusPayload) || (backendMode === "fake" && isReady);
      const isLoading = backendMode === "llama" && hasModel && !llamaHealthy && statusState === "BOOTING";
      const isFailed = backendMode === "llama" && statusState === "ERROR";
      badge.classList.remove("online", "loading", "failed", "offline");
      dot.classList.remove("online", "loading", "failed", "offline");
      dot.hidden = false;
      if (spinner) spinner.hidden = true;
      if (backendMode === "fake" && isReady) {
        badge.classList.add("online");
        dot.classList.add("online");
        label.textContent = "CONNECTED:Fake Backend";
      } else if (isHealthy) {
        badge.classList.add("online");
        dot.classList.add("online");
        label.textContent = `CONNECTED:llama.cpp${modelSuffix}${storageSuffix}`;
      } else if (isLoading) {
        badge.classList.add("loading");
        dot.classList.add("loading");
        dot.hidden = true;
        if (spinner) spinner.hidden = false;
        label.textContent = `LOADING:llama.cpp${modelSuffix}${storageSuffix}`;
      } else if (isFailed) {
        badge.classList.add("failed");
        dot.classList.add("failed");
        label.textContent = `FAILED:llama.cpp${modelSuffix}${storageSuffix}`;
      } else {
        badge.classList.add("offline");
        dot.classList.add("offline");
        label.textContent = "DISCONNECTED:llama.cpp";
      }
    }

    function renderDownloadPrompt(statusPayload) {
      const prompt = document.getElementById("downloadPrompt");
      const hint = document.getElementById("downloadPromptHint");
      const startBtn = document.getElementById("startDownloadBtn");
      if (!prompt || !hint || !startBtn) return;

      const state = String(statusPayload?.state || "");
      const hasModel = statusPayload?.model_present === true;
      const downloadActive = statusPayload?.download?.active === true || state === "DOWNLOADING";
      if (hasModel || downloadActive || state === "READY") {
        prompt.hidden = true;
        startBtn.textContent = "Start download now";
        startBtn.disabled = false;
        return;
      }

      prompt.hidden = false;
      const countdownEnabled = statusPayload?.download?.countdown_enabled !== false;
      const autoStartRemaining = Number(statusPayload.download.auto_start_remaining_seconds);
      const freeBytes = Number(statusPayload?.system?.storage_free_bytes);
      const downloadError = String(statusPayload?.download?.error || "");
      const resumableFailedModel = findResumableFailedModel(statusPayload);
      if (
        downloadError === "insufficient_storage"
        || (Number.isFinite(freeBytes) && freeBytes < 512 * 1024 * 1024)
      ) {
        const free = formatBytes(statusPayload?.system?.storage_free_bytes);
        hint.textContent = `Not enough free storage for this model. Free space: ${free}. Delete model files and retry.`;
      } else if (downloadError === "download_failed" && resumableFailedModel) {
        hint.textContent =
          `Last download failed at ${formatBytes(resumableFailedModel?.bytes_downloaded)} ` +
          `of ${formatBytes(resumableFailedModel?.bytes_total)}. Resume when ready.`;
      } else if (!countdownEnabled) {
        hint.textContent = "Auto-download is paused. Start manually or re-enable it in settings.";
      } else if (Number.isFinite(autoStartRemaining) && autoStartRemaining > 0) {
        hint.textContent = `Auto-download starts in ${formatCountdownSeconds(autoStartRemaining)} if idle.`;
      } else {
        hint.textContent = "Auto-download starts soon if idle.";
      }

      if (downloadStartInFlight) {
        startBtn.textContent = resumableFailedModel ? "Resuming..." : "Starting...";
        startBtn.disabled = true;
      } else {
        startBtn.textContent = resumableFailedModel ? "Resume download" : "Start download now";
        startBtn.disabled = false;
      }
    }

    function renderStatusActions(statusPayload) {
      const actions = document.getElementById("statusActions");
      const resumeBtn = document.getElementById("statusResumeDownloadBtn");
      if (!actions || !resumeBtn) return;
      const hasModel = statusPayload?.model_present === true;
      const state = String(statusPayload?.state || "").toUpperCase();
      const downloadError = String(statusPayload?.download?.error || "");
      const resumableFailedModel = findResumableFailedModel(statusPayload);
      const showResume = hasModel && state === "READY" && downloadError === "download_failed" && !!resumableFailedModel;
      actions.hidden = !showResume;
      resumeBtn.disabled = downloadStartInFlight;
      resumeBtn.textContent = downloadStartInFlight ? "Resuming..." : "Resume";
    }

    function setRuntimeDetailsExpanded(expanded) {
      runtimeDetailsExpanded = Boolean(expanded);
      const details = document.getElementById("runtimeDetails");
      const toggle = document.getElementById("runtimeViewToggle");
      const compact = document.getElementById("runtimeCompact");
      if (details) {
        details.hidden = !runtimeDetailsExpanded;
      }
      if (compact) {
        compact.hidden = runtimeDetailsExpanded;
      }
      if (toggle) {
        toggle.textContent = runtimeDetailsExpanded ? "Hide details" : "Show details";
        toggle.setAttribute("aria-expanded", runtimeDetailsExpanded ? "true" : "false");
      }
    }

    function renderSystemRuntime(systemPayload) {
      const compact = document.getElementById("runtimeCompact");
      if (!compact) return;

      const available = systemPayload?.available === true;
      const cpuDetail = document.getElementById("runtimeDetailCpuValue");
      const coresDetail = document.getElementById("runtimeDetailCoresValue");
      const cpuClockDetail = document.getElementById("runtimeDetailCpuClockValue");
      const memoryDetail = document.getElementById("runtimeDetailMemoryValue");
      const swapLabelDetail = document.getElementById("runtimeDetailSwapLabel");
      const swapDetail = document.getElementById("runtimeDetailSwapValue");
      const storageDetail = document.getElementById("runtimeDetailStorageValue");
      const tempDetail = document.getElementById("runtimeDetailTempValue");
      const piModelDetail = document.getElementById("runtimeDetailPiModelValue");
      const osDetail = document.getElementById("runtimeDetailOsValue");
      const kernelDetail = document.getElementById("runtimeDetailKernelValue");
      const bootloaderDetail = document.getElementById("runtimeDetailBootloaderValue");
      const firmwareDetail = document.getElementById("runtimeDetailFirmwareValue");
      const powerDetail = document.getElementById("runtimeDetailPower");
      const powerRawDetail = document.getElementById("runtimeDetailPowerRaw");
      const gpuDetail = document.getElementById("runtimeDetailGpuValue");
      const throttleDetail = document.getElementById("runtimeDetailThrottleValue");
      const throttleHistoryDetail = document.getElementById("runtimeDetailThrottleHistoryValue");
      const updatedDetail = document.getElementById("runtimeDetailUpdatedValue");

      if (!available) {
        compact.textContent = "CPU -- | Cores -- | GPU -- | Swap -- | Throttle --";
        if (cpuDetail) cpuDetail.textContent = "--";
        if (coresDetail) coresDetail.textContent = "--";
        if (cpuClockDetail) cpuClockDetail.textContent = "--";
        if (memoryDetail) memoryDetail.textContent = "--";
        if (swapLabelDetail) swapLabelDetail.textContent = "zram";
        if (swapDetail) swapDetail.textContent = "--";
        if (storageDetail) storageDetail.textContent = "--";
        if (tempDetail) tempDetail.textContent = "--";
        if (piModelDetail) piModelDetail.textContent = "--";
        if (osDetail) osDetail.textContent = "--";
        if (kernelDetail) kernelDetail.textContent = "--";
        if (bootloaderDetail) bootloaderDetail.textContent = "--";
        if (firmwareDetail) firmwareDetail.textContent = "--";
        if (powerDetail) powerDetail.textContent = "Power (estimated total): --";
        if (powerRawDetail) powerRawDetail.textContent = "Power (PMIC raw): --";
        if (gpuDetail) gpuDetail.textContent = "--";
        if (throttleDetail) throttleDetail.textContent = "--";
        if (throttleHistoryDetail) throttleHistoryDetail.textContent = "--";
        if (updatedDetail) updatedDetail.textContent = "--";
        applyRuntimeMetricSeverity(cpuClockDetail, Number.NaN);
        applyRuntimeMetricSeverity(memoryDetail, Number.NaN);
        applyRuntimeMetricSeverity(swapDetail, Number.NaN);
        applyRuntimeMetricSeverity(storageDetail, Number.NaN);
        applyRuntimeMetricSeverity(tempDetail, Number.NaN);
        applyRuntimeMetricSeverity(gpuDetail, Number.NaN);
        return;
      }

      const cpuTotal = formatPercent(systemPayload?.cpu_percent, 0);
      const coreValues = Array.isArray(systemPayload?.cpu_cores_percent)
        ? systemPayload.cpu_cores_percent.map((value) => Number(value)).filter((value) => Number.isFinite(value))
        : [];
      const coresText = coreValues.length > 0
        ? `[${coreValues.map((value) => Math.round(value)).join(", ")}]`
        : "--";
      const cpuClock = formatClockMHz(systemPayload?.cpu_clock_arm_hz);
      const gpuCore = formatClockMHz(systemPayload?.gpu_clock_core_hz);
      const gpuV3d = formatClockMHz(systemPayload?.gpu_clock_v3d_hz);
      const gpuCompact = (gpuCore !== "--" || gpuV3d !== "--")
        ? `${gpuCore.replace(" MHz", "")}/${gpuV3d.replace(" MHz", "")} MHz`
        : "--";
      const swapLabel = String(systemPayload?.swap_label || "swap").trim() || "swap";
      const swapPercent = formatPercent(systemPayload?.swap_percent, 0);
      const storageFree = formatBytes(systemPayload?.storage_free_bytes);
      const storagePercent = formatPercent(systemPayload?.storage_percent, 0);
      const throttlingNow = systemPayload?.throttling?.any_current === true ? "Yes" : "No";
      compact.textContent = `CPU ${cpuTotal} @ ${cpuClock} | Cores ${coresText} | GPU ${gpuCompact} | ${swapLabel} ${swapPercent} | Free ${storageFree} | Throttle ${throttlingNow}`;

      if (cpuDetail) cpuDetail.textContent = cpuTotal;
      if (coresDetail) coresDetail.textContent = coresText;
      if (cpuClockDetail) cpuClockDetail.textContent = cpuClock;
      applyRuntimeMetricSeverity(cpuClockDetail, percentFromRatio(systemPayload?.cpu_clock_arm_hz, CPU_CLOCK_MAX_HZ_PI5));

      const memUsed = formatBytes(systemPayload?.memory_used_bytes);
      const memTotal = formatBytes(systemPayload?.memory_total_bytes);
      const memPercent = formatPercent(systemPayload?.memory_percent, 0);
      if (memoryDetail) memoryDetail.textContent = `${memUsed} / ${memTotal} (${memPercent})`;
      applyRuntimeMetricSeverity(memoryDetail, systemPayload?.memory_percent);

      const swapUsed = formatBytes(systemPayload?.swap_used_bytes);
      const swapTotal = formatBytes(systemPayload?.swap_total_bytes);
      if (swapLabelDetail) swapLabelDetail.textContent = swapLabel;
      if (swapDetail) swapDetail.textContent = `${swapUsed} / ${swapTotal} (${swapPercent})`;
      applyRuntimeMetricSeverity(swapDetail, systemPayload?.swap_percent);

      const storageUsed = formatBytes(systemPayload?.storage_used_bytes);
      const storageTotal = formatBytes(systemPayload?.storage_total_bytes);
      if (storageDetail) storageDetail.textContent = `${storageFree} (${storageUsed} / ${storageTotal} used, ${storagePercent})`;
      applyRuntimeMetricSeverity(storageDetail, systemPayload?.storage_percent);

      const tempRaw = systemPayload?.temperature_c;
      const tempValue = typeof tempRaw === "number" ? tempRaw : Number.NaN;
      if (tempDetail) {
        tempDetail.textContent = Number.isFinite(tempValue)
          ? `${tempValue.toFixed(1)}°C`
          : "--";
      }
      applyRuntimeMetricSeverity(tempDetail, tempValue);

      const piModelName = String(systemPayload?.pi_model_name || "").trim();
      if (piModelDetail) {
        piModelDetail.textContent = piModelName || "--";
      }

      const osPrettyName = String(systemPayload?.os_pretty_name || "").trim();
      if (osDetail) {
        osDetail.textContent = osPrettyName || "--";
      }

      const kernelRelease = String(systemPayload?.kernel_release || "").trim();
      const kernelVersion = String(systemPayload?.kernel_version || "").trim();
      if (kernelDetail) {
        if (kernelRelease && kernelVersion) {
          kernelDetail.textContent = `${kernelRelease} • ${kernelVersion}`;
        } else if (kernelRelease || kernelVersion) {
          kernelDetail.textContent = kernelRelease || kernelVersion;
        } else {
          kernelDetail.textContent = "--";
        }
      }

      const bootloader = systemPayload?.bootloader_version || {};
      const bootloaderDate = String(bootloader?.date || "").trim();
      const bootloaderVersion = String(bootloader?.version || "").trim();
      if (bootloaderDetail) {
        if (bootloaderDate && bootloaderVersion) {
          bootloaderDetail.textContent = `${bootloaderDate} • ${bootloaderVersion}`;
        } else if (bootloaderDate || bootloaderVersion) {
          bootloaderDetail.textContent = bootloaderDate || bootloaderVersion;
        } else {
          bootloaderDetail.textContent = "--";
        }
      }

      const firmware = systemPayload?.firmware_version || {};
      const firmwareDate = String(firmware?.date || "").trim();
      const firmwareVersion = String(firmware?.version || "").trim();
      if (firmwareDetail) {
        if (firmwareDate && firmwareVersion) {
          firmwareDetail.textContent = `${firmwareDate} • ${firmwareVersion}`;
        } else if (firmwareDate || firmwareVersion) {
          firmwareDetail.textContent = firmwareDate || firmwareVersion;
        } else {
          firmwareDetail.textContent = "--";
        }
      }

      const powerEstimate = systemPayload?.power_estimate || {};
      const rawPowerWatts = Number(powerEstimate?.raw_total_watts ?? powerEstimate?.total_watts);
      const adjustedPowerWatts = Number(powerEstimate?.adjusted_total_watts);
      if (powerDetail) {
        powerDetail.textContent = Number.isFinite(adjustedPowerWatts) && powerEstimate?.available === true
          ? `Power (estimated total): ${adjustedPowerWatts.toFixed(3)} W`
          : "Power (estimated total): --";
      }
      if (powerRawDetail) {
        powerRawDetail.textContent = Number.isFinite(rawPowerWatts) && powerEstimate?.available === true
          ? `Power (PMIC raw): ${rawPowerWatts.toFixed(3)} W`
          : "Power (PMIC raw): --";
      }

      if (gpuDetail) gpuDetail.textContent = `core ${gpuCore}, v3d ${gpuV3d}`;
      const gpuPeakHz = Math.max(
        Number(systemPayload?.gpu_clock_core_hz) || 0,
        Number(systemPayload?.gpu_clock_v3d_hz) || 0,
      );
      applyRuntimeMetricSeverity(gpuDetail, percentFromRatio(gpuPeakHz, GPU_CLOCK_MAX_HZ_PI5));

      const currentFlags = Array.isArray(systemPayload?.throttling?.current_flags)
        ? systemPayload.throttling.current_flags
        : [];
      const historyFlags = Array.isArray(systemPayload?.throttling?.history_flags)
        ? systemPayload.throttling.history_flags
        : [];
      if (throttleDetail) {
        throttleDetail.textContent = currentFlags.length > 0
          ? `Yes (${currentFlags.join(", ")})`
          : "No";
      }
      if (throttleHistoryDetail) {
        throttleHistoryDetail.textContent = historyFlags.length > 0
          ? historyFlags.join(", ")
          : "None";
      }

      const updatedTs = Number(systemPayload?.updated_at_unix);
      if (updatedDetail) {
        updatedDetail.textContent = Number.isFinite(updatedTs) && updatedTs > 0
          ? new Date(updatedTs * 1000).toLocaleTimeString()
          : "--";
      }
    }

    function renderCompatibilityWarnings(statusPayload) {
      const el = document.getElementById("compatibilityWarnings");
      const textEl = document.getElementById("compatibilityWarningsText");
      const overrideBtn = document.getElementById("compatibilityOverrideBtn");
      if (!el) return;
      const warnings = Array.isArray(statusPayload?.compatibility?.warnings)
        ? statusPayload.compatibility.warnings
        : [];
      const overrideEnabled = statusPayload?.compatibility?.override_enabled === true;
      if (!warnings.length) {
        el.hidden = true;
        if (textEl) textEl.textContent = "";
        else el.textContent = "";
        if (overrideBtn) {
          overrideBtn.hidden = true;
          overrideBtn.disabled = false;
          overrideBtn.textContent = "Try anyway";
        }
        return;
      }
      const text = warnings
        .map((item) => String(item?.message || "Compatibility warning"))
        .filter((item) => item.length > 0)
        .join(" | ");
      if (textEl) textEl.textContent = text || "Compatibility warning";
      else el.textContent = text || "Compatibility warning";
      if (overrideBtn) {
        overrideBtn.hidden = overrideEnabled;
      }
      el.hidden = false;
    }

    function setModelUploadStatus(message) {
      const el = document.getElementById("modelUploadStatus");
      if (!el) return;
      el.textContent = String(message || "No upload in progress.");
    }

    function setLlamaRuntimeSwitchStatus(message) {
      const el = document.getElementById("llamaRuntimeSwitchStatus");
      if (!el) return;
      el.textContent = String(message || "No runtime switch in progress.");
    }

    function setLlamaMemoryLoadingStatus(message) {
      const el = document.getElementById("llamaMemoryLoadingStatus");
      if (!el) return;
      el.textContent = String(message || "Current memory loading: unknown");
    }

    function setLargeModelOverrideStatus(message) {
      const el = document.getElementById("largeModelOverrideStatus");
      if (!el) return;
      el.textContent = String(message || "Compatibility override: default warnings");
    }

    function setPowerCalibrationStatus(message) {
      const el = document.getElementById("powerCalibrationStatus");
      if (!el) return;
      el.textContent = String(message || "Power calibration: default correction");
    }

    function setPowerCalibrationLiveStatus(message) {
      const el = document.getElementById("powerCalibrationLiveStatus");
      if (!el) return;
      el.textContent = String(message || "Current PMIC raw power: --");
    }

    function setLlamaRuntimeSwitchButtonState(inFlight) {
      const btn = document.getElementById("switchLlamaRuntimeBtn");
      if (!btn) return;
      btn.disabled = Boolean(inFlight);
      btn.textContent = inFlight ? "Switching..." : "Switch llama runtime";
    }

    function setLlamaMemoryLoadingButtonState(inFlight) {
      const btn = document.getElementById("applyLlamaMemoryLoadingBtn");
      if (!btn) return;
      btn.disabled = Boolean(inFlight);
      btn.textContent = inFlight ? "Applying..." : "Apply memory loading + restart";
    }

    function setLargeModelOverrideButtonState(inFlight) {
      const btn = document.getElementById("applyLargeModelOverrideBtn");
      if (btn) {
        btn.disabled = Boolean(inFlight);
        btn.textContent = inFlight ? "Applying..." : "Apply compatibility override";
      }
      const quickBtn = document.getElementById("compatibilityOverrideBtn");
      if (quickBtn) {
        quickBtn.disabled = Boolean(inFlight);
        quickBtn.textContent = inFlight ? "Applying..." : "Try anyway";
      }
    }

    function setPowerCalibrationButtonsState(inFlight) {
      const captureBtn = document.getElementById("capturePowerCalibrationSampleBtn");
      const fitBtn = document.getElementById("fitPowerCalibrationBtn");
      const resetBtn = document.getElementById("resetPowerCalibrationBtn");
      for (const btn of [captureBtn, fitBtn, resetBtn]) {
        if (!btn) continue;
        btn.disabled = Boolean(inFlight);
      }
      if (captureBtn) {
        captureBtn.textContent = inFlight ? "Capturing..." : "Capture calibration sample";
      }
      if (fitBtn) {
        fitBtn.textContent = inFlight ? "Computing..." : "Compute calibration";
      }
      if (resetBtn) {
        resetBtn.textContent = inFlight ? "Resetting..." : "Reset calibration";
      }
    }

    function renderLlamaRuntimeStatus(statusPayload) {
      const runtimePayload = statusPayload?.llama_runtime || {};
      const currentEl = document.getElementById("llamaRuntimeCurrent");
      const selectEl = document.getElementById("llamaRuntimeBundleSelect");
      if (currentEl) {
        const current = runtimePayload?.current || {};
        const sourceName = String(current?.source_bundle_name || "").trim();
        const profile = String(current?.profile || "").trim();
        const serverPresent = current?.has_server_binary === true;
        const parts = [];
        if (sourceName) parts.push(sourceName);
        if (profile) parts.push(`profile=${profile}`);
        if (!parts.length && serverPresent) {
          parts.push("custom/current install");
        }
        currentEl.textContent = `Current runtime: ${parts.join(" | ") || "unknown"}`;
      }

      if (selectEl) {
        const bundles = Array.isArray(runtimePayload?.available_bundles) ? runtimePayload.available_bundles : [];
        const prevValue = String(selectEl.value || "");
        selectEl.replaceChildren();
        if (!bundles.length) {
          const option = document.createElement("option");
          option.value = "";
          option.textContent = "No bundles discovered";
          selectEl.appendChild(option);
          selectEl.disabled = true;
        } else {
          let selectedApplied = false;
          for (const bundle of bundles) {
            const option = document.createElement("option");
            option.value = String(bundle?.path || "");
            const labelParts = [String(bundle?.name || "bundle")];
            if (bundle?.profile) {
              labelParts.push(`(${bundle.profile})`);
            }
            if (bundle?.is_current === true) {
              labelParts.push("[current]");
            }
            option.textContent = labelParts.join(" ");
            if (option.value && (option.value === prevValue || (!prevValue && bundle?.is_current === true))) {
              option.selected = true;
              selectedApplied = true;
            }
            selectEl.appendChild(option);
          }
          if (!selectedApplied && selectEl.options.length > 0) {
            selectEl.options[0].selected = true;
          }
          selectEl.disabled = false;
        }
      }

      const memoryLoadingSelect = document.getElementById("llamaMemoryLoadingMode");
      const memoryLoading = runtimePayload?.memory_loading || {};
      if (memoryLoadingSelect) {
        const mode = String(memoryLoading?.mode || "auto");
        const normalizedMode = ["auto", "full_ram", "mmap"].includes(mode) ? mode : "auto";
        memoryLoadingSelect.value = normalizedMode;
      }
      if (memoryLoading?.label) {
        const restartNote = memoryLoading?.no_mmap_env === "1"
          ? " (full RAM preload enabled)"
          : memoryLoading?.no_mmap_env === "0"
          ? " (mmap enabled)"
          : " (auto)";
        setLlamaMemoryLoadingStatus(`Current memory loading: ${memoryLoading.label}${restartNote}`);
      } else {
        setLlamaMemoryLoadingStatus("Current memory loading: unknown");
      }

      const largeModelOverrideToggle = document.getElementById("largeModelOverrideEnabled");
      const largeModelOverride = runtimePayload?.large_model_override || {};
      const overrideEnabled = largeModelOverride?.enabled === true || statusPayload?.compatibility?.override_enabled === true;
      if (largeModelOverrideToggle) {
        largeModelOverrideToggle.checked = overrideEnabled;
      }
      if (overrideEnabled) {
        setLargeModelOverrideStatus("Compatibility override: trying unsupported large models is enabled");
      } else {
        setLargeModelOverrideStatus("Compatibility override: default warnings");
      }

      const powerEstimate = statusPayload?.system?.power_estimate || {};
      const calibration = powerEstimate?.calibration || {};
      const rawPower = Number(powerEstimate?.raw_total_watts ?? powerEstimate?.total_watts);
      if (Number.isFinite(rawPower) && powerEstimate?.available === true) {
        setPowerCalibrationLiveStatus(`Current PMIC raw power: ${rawPower.toFixed(3)} W`);
      } else {
        setPowerCalibrationLiveStatus("Current PMIC raw power: --");
      }
      const mode = String(calibration?.mode || "default");
      const sampleCount = Number(calibration?.sample_count || 0);
      const coeffA = Number(calibration?.a);
      const coeffB = Number(calibration?.b);
      if (mode === "custom") {
        setPowerCalibrationStatus(
          `Power calibration: meter-calibrated (${sampleCount} samples, a=${Number.isFinite(coeffA) ? coeffA.toFixed(4) : "--"}, b=${Number.isFinite(coeffB) ? coeffB.toFixed(4) : "--"})`
        );
      } else {
        setPowerCalibrationStatus(
          `Power calibration: default correction (${sampleCount} stored samples${sampleCount >= 2 ? ", ready to fit" : ""})`
        );
      }

      const switchState = runtimePayload?.switch || {};
      if (switchState?.active) {
        const target = String(switchState?.target_bundle_path || "selected bundle");
        setLlamaRuntimeSwitchStatus(`Switching runtime bundle... ${target}`);
      } else if (switchState?.error) {
        setLlamaRuntimeSwitchStatus(`Last runtime switch error: ${switchState.error}`);
      } else if (runtimePayload?.current?.source_bundle_name) {
        setLlamaRuntimeSwitchStatus(`Active runtime bundle: ${runtimePayload.current.source_bundle_name}`);
      } else {
        setLlamaRuntimeSwitchStatus("No runtime switch in progress.");
      }

      setLlamaRuntimeSwitchButtonState(llamaRuntimeSwitchInFlight || switchState?.active === true);
      setLlamaMemoryLoadingButtonState(llamaMemoryLoadingApplyInFlight);
      setLargeModelOverrideButtonState(largeModelOverrideApplyInFlight);
      setPowerCalibrationButtonsState(powerCalibrationActionInFlight);
    }

    function findModelInLatestStatus(modelId) {
      const models = Array.isArray(latestStatus?.models) ? latestStatus.models : [];
      return models.find((item) => String(item?.id || "") === String(modelId || "")) || null;
    }

    function findResumableFailedModel(statusPayload) {
      const models = Array.isArray(statusPayload?.models) ? statusPayload.models : [];
      return models.find((item) => (
        String(item?.source_type || "") === "url"
        && String(item?.status || "").toLowerCase() === "failed"
      )) || null;
    }

    function formatSidebarStatusDetail(statusPayload) {
      const download = statusPayload?.download || {};
      const downloaded = formatBytes(download.bytes_downloaded);
      const total = formatBytes(download.bytes_total);
      const state = String(statusPayload?.state || "").toUpperCase();
      const downloadActive = download.active === true || state === "DOWNLOADING";
      const downloadError = String(download.error || "");
      const resumableFailedModel = findResumableFailedModel(statusPayload);
      if (downloadActive) {
        return `Download: ${download.percent}% (${downloaded} / ${total})`;
      }
      if (downloadError === "download_failed" && resumableFailedModel) {
        return `Download failed (${downloaded} / ${total})`;
      }
      if (download.auto_download_paused === true) {
        return "Auto-download paused";
      }
      return "No active download";
    }

    function formatModelStatusLabel(rawStatus) {
      const normalized = String(rawStatus || "unknown").trim().toLowerCase();
      if (!normalized) return "unknown";
      return normalized
        .replaceAll("_", " ")
        .split(" ")
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
    }

    async function postJson(url, payload) {
      const res = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      const body = await res.json().catch(() => ({}));
      return { res, body };
    }

    async function switchLlamaRuntimeBundle() {
      if (llamaRuntimeSwitchInFlight) return;
      const select = document.getElementById("llamaRuntimeBundleSelect");
      const bundlePath = String(select?.value || "").trim();
      if (!bundlePath) {
        appendMessage("assistant", "No llama runtime bundle selected.");
        return;
      }
      const selectedLabel = select?.selectedOptions?.[0]?.textContent || bundlePath;
      const confirmed = window.confirm(
        `Switch llama runtime to:\n${selectedLabel}\n\nThis will restart the local llama runtime process.`
      );
      if (!confirmed) return;

      llamaRuntimeSwitchInFlight = true;
      setLlamaRuntimeSwitchButtonState(true);
      setLlamaRuntimeSwitchStatus("Switching runtime bundle...");
      setComposerActivity("Switching llama runtime...");
      try {
        const { res, body } = await postJson("/internal/llama-runtime/switch", { bundle_path: bundlePath });
        if (!res.ok || body?.switched !== true) {
          appendMessage("assistant", `Could not switch llama runtime (${body?.reason || res.status}).`);
          return;
        }
        appendMessage("assistant", `Switched llama runtime bundle to ${body?.bundle?.name || "selected bundle"}.`);
        setComposerActivity("Llama runtime switched. Reconnecting...");
      } catch (err) {
        appendMessage("assistant", `Could not switch llama runtime: ${err}`);
      } finally {
        llamaRuntimeSwitchInFlight = false;
        setLlamaRuntimeSwitchButtonState(false);
        await pollStatus();
      }
    }

    async function applyLlamaMemoryLoadingMode() {
      if (llamaMemoryLoadingApplyInFlight) return;
      const select = document.getElementById("llamaMemoryLoadingMode");
      const mode = String(select?.value || "auto").trim() || "auto";
      const label = select?.selectedOptions?.[0]?.textContent || mode;
      const confirmed = window.confirm(
        `Apply "${label}" and restart the llama runtime now? ` +
        "The model will reload and chat will disconnect briefly."
      );
      if (!confirmed) return;

      llamaMemoryLoadingApplyInFlight = true;
      setLlamaMemoryLoadingButtonState(true);
      setLlamaMemoryLoadingStatus(`Applying memory loading mode: ${label}...`);
      try {
        const { res, body } = await postJson("/internal/llama-runtime/memory-loading", { mode });
        if (!res.ok) {
          appendMessage(
            "assistant",
            `Could not update model memory loading (${res.status}): ${body?.reason || "unknown"}.`
          );
          setLlamaMemoryLoadingStatus(`Last memory loading update error: ${body?.reason || res.status}`);
          return;
        }
        appendMessage(
          "assistant",
          `Applied model memory loading: ${body?.memory_loading?.label || mode}. ` +
          `Runtime restart: ${body?.restart_reason || "requested"}.`
        );
        await pollStatus();
      } catch (err) {
        appendMessage("assistant", `Could not update model memory loading: ${err}`);
        setLlamaMemoryLoadingStatus(`Last memory loading update error: ${err}`);
      } finally {
        llamaMemoryLoadingApplyInFlight = false;
        setLlamaMemoryLoadingButtonState(false);
      }
    }

    async function applyLargeModelCompatibilityOverride(enabled) {
      if (largeModelOverrideApplyInFlight) return;
      largeModelOverrideApplyInFlight = true;
      setLargeModelOverrideButtonState(true);
      setLargeModelOverrideStatus(
        enabled
          ? "Applying compatibility override: try unsupported models..."
          : "Applying compatibility override: restore warnings..."
      );
      try {
        const { res, body } = await postJson("/internal/compatibility/large-model-override", { enabled: Boolean(enabled) });
        if (!res.ok || body?.updated !== true) {
          appendMessage("assistant", `Could not update compatibility override (${body?.reason || res.status}).`);
          setLargeModelOverrideStatus(`Last compatibility override error: ${body?.reason || res.status}`);
          return;
        }
        appendMessage(
          "assistant",
          body?.override?.enabled
            ? "Enabled compatibility override. Potato will try unsupported large models."
            : "Disabled compatibility override. Default large-model warnings are active again."
        );
        setLargeModelOverrideStatus(
          body?.override?.enabled
            ? "Compatibility override: trying unsupported large models is enabled"
            : "Compatibility override: default warnings"
        );
      } catch (err) {
        appendMessage("assistant", `Could not update compatibility override: ${err}`);
        setLargeModelOverrideStatus(`Last compatibility override error: ${err}`);
      } finally {
        largeModelOverrideApplyInFlight = false;
        setLargeModelOverrideButtonState(false);
        await pollStatus();
      }
    }

    async function applyLargeModelOverrideFromSettings() {
      const checkbox = document.getElementById("largeModelOverrideEnabled");
      await applyLargeModelCompatibilityOverride(checkbox?.checked === true);
    }

    async function capturePowerCalibrationSample() {
      if (powerCalibrationActionInFlight) return;
      const input = document.getElementById("powerCalibrationWallWatts");
      const wallWatts = Number(input?.value);
      if (!Number.isFinite(wallWatts) || wallWatts <= 0) {
        appendMessage("assistant", "Enter a valid wall meter reading in watts before capturing a sample.");
        setPowerCalibrationStatus("Power calibration error: invalid wall meter reading");
        return;
      }

      powerCalibrationActionInFlight = true;
      setPowerCalibrationButtonsState(true);
      setPowerCalibrationStatus("Capturing power calibration sample...");
      try {
        const { res, body } = await postJson("/internal/power-calibration/sample", { wall_watts: wallWatts });
        if (!res.ok || body?.captured !== true) {
          appendMessage("assistant", `Could not capture power sample (${body?.reason || res.status}).`);
          setPowerCalibrationStatus(`Power calibration error: ${body?.reason || res.status}`);
          return;
        }
        appendMessage(
          "assistant",
          `Captured power calibration sample (wall ${Number(wallWatts).toFixed(2)} W vs raw ${Number(body?.sample?.raw_pmic_watts || 0).toFixed(3)} W).`
        );
      } catch (err) {
        appendMessage("assistant", `Could not capture power calibration sample: ${err}`);
        setPowerCalibrationStatus(`Power calibration error: ${err}`);
      } finally {
        powerCalibrationActionInFlight = false;
        setPowerCalibrationButtonsState(false);
        await pollStatus();
      }
    }

    async function fitPowerCalibrationModel() {
      if (powerCalibrationActionInFlight) return;
      powerCalibrationActionInFlight = true;
      setPowerCalibrationButtonsState(true);
      setPowerCalibrationStatus("Computing power calibration...");
      try {
        const { res, body } = await postJson("/internal/power-calibration/fit", {});
        if (!res.ok || body?.updated !== true) {
          appendMessage("assistant", `Could not compute power calibration (${body?.reason || res.status}).`);
          setPowerCalibrationStatus(`Power calibration error: ${body?.reason || res.status}`);
          return;
        }
        const cal = body?.calibration || {};
        appendMessage(
          "assistant",
          `Power calibration updated (a=${Number(cal?.a || 0).toFixed(4)}, b=${Number(cal?.b || 0).toFixed(4)}, samples=${Number(cal?.sample_count || 0)}).`
        );
      } catch (err) {
        appendMessage("assistant", `Could not compute power calibration: ${err}`);
        setPowerCalibrationStatus(`Power calibration error: ${err}`);
      } finally {
        powerCalibrationActionInFlight = false;
        setPowerCalibrationButtonsState(false);
        await pollStatus();
      }
    }

    async function resetPowerCalibrationModel() {
      if (powerCalibrationActionInFlight) return;
      const confirmed = window.confirm(
        "Reset power calibration to the default correction model? Saved wall-meter samples will be cleared."
      );
      if (!confirmed) return;

      powerCalibrationActionInFlight = true;
      setPowerCalibrationButtonsState(true);
      setPowerCalibrationStatus("Resetting power calibration...");
      try {
        const { res, body } = await postJson("/internal/power-calibration/reset", {});
        if (!res.ok || body?.updated !== true) {
          appendMessage("assistant", `Could not reset power calibration (${body?.reason || res.status}).`);
          setPowerCalibrationStatus(`Power calibration error: ${body?.reason || res.status}`);
          return;
        }
        appendMessage("assistant", "Power calibration reset. Using default correction again.");
      } catch (err) {
        appendMessage("assistant", `Could not reset power calibration: ${err}`);
        setPowerCalibrationStatus(`Power calibration error: ${err}`);
      } finally {
        powerCalibrationActionInFlight = false;
        setPowerCalibrationButtonsState(false);
        await pollStatus();
      }
    }

    async function allowUnsupportedLargeModelFromWarning() {
      const confirmed = window.confirm(
        "Try loading unsupported large models anyway on this device? " +
        "This may fail or be unstable, but Potato will stop warning-blocking this attempt."
      );
      if (!confirmed) return;
      await applyLargeModelCompatibilityOverride(true);
    }

    function renderUploadState(statusPayload) {
      const upload = statusPayload?.upload || {};
      const cancelBtn = document.getElementById("cancelUploadBtn");
      if (upload?.active) {
        if (cancelBtn) cancelBtn.hidden = false;
        const percent = Number(upload.percent || 0);
        setModelUploadStatus(`Uploading model... ${percent}% (${formatBytes(upload.bytes_received)} / ${formatBytes(upload.bytes_total)})`);
        return;
      }
      if (cancelBtn) cancelBtn.hidden = true;
      if (upload?.error) {
        setModelUploadStatus(`Upload state: ${upload.error}`);
      } else {
        setModelUploadStatus("No upload in progress.");
      }
    }

    async function updateCountdownPreference(enabled) {
      const { res, body } = await postJson("/internal/download-countdown", { enabled });
      if (!res.ok) {
        appendMessage("assistant", `Could not update auto-download: ${body?.reason || res.status}`);
      }
      await pollStatus();
    }

    async function registerModelFromUrl() {
      if (modelActionInFlight) return;
      const input = document.getElementById("modelUrlInput");
      const sourceUrl = String(input?.value || "").trim();
      if (!sourceUrl) {
        setModelUrlStatus("Enter an HTTPS model URL ending with .gguf.");
        return;
      }
      modelActionInFlight = true;
      setModelUrlStatus("Adding model URL...");
      try {
        const { res, body } = await postJson("/internal/models/register", { source_url: sourceUrl });
        if (!res.ok) {
          setModelUrlStatus(formatModelUrlStatus(body?.reason, res.status));
          return;
        }
        setModelUrlStatus(
          body?.reason === "already_exists"
            ? "That model URL is already registered."
            : "Model URL added."
        );
        if (input) input.value = "";
      } catch (err) {
        setModelUrlStatus(`Could not add model URL: ${err}`);
      } finally {
        modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function startModelDownloadForModel(modelId) {
      if (!modelId) return;
      if (modelActionInFlight) return;
      modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/download", { model_id: modelId });
        if (!res.ok) {
          appendMessage("assistant", `Could not start model download (${body?.reason || res.status}).`);
          return;
        }
        if (!body?.started && body?.reason === "insufficient_storage") {
          setComposerActivity("Model likely too large for free storage. Delete files and retry.");
        }
      } catch (err) {
        appendMessage("assistant", `Could not start model download: ${err}`);
      } finally {
        modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function cancelActiveModelDownload(modelId = null) {
      if (modelActionInFlight) return;
      const targetModel = findModelInLatestStatus(modelId) || findModelInLatestStatus(latestStatus?.download?.current_model_id);
      const targetName = String(targetModel?.filename || "this model");
      const confirmed = window.confirm(`Stop the current download for ${targetName}?`);
      if (!confirmed) return;
      modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/cancel-download", {});
        if (!res.ok) {
          appendMessage("assistant", `Could not cancel model download (${body?.reason || res.status}).`);
        }
      } catch (err) {
        appendMessage("assistant", `Could not cancel model download: ${err}`);
      } finally {
        modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function activateSelectedModel(modelId) {
      if (!modelId) return;
      if (modelActionInFlight) return;
      modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/activate", { model_id: modelId });
        if (!res.ok) {
          appendMessage("assistant", `Could not activate model (${body?.reason || res.status}).`);
          return;
        }
        setComposerActivity("Switching active model...");
      } catch (err) {
        appendMessage("assistant", `Could not activate model: ${err}`);
      } finally {
        modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function moveModelToSsd(modelId) {
      if (!modelId) return;
      if (modelActionInFlight) return;
      const targetModel = findModelInLatestStatus(modelId);
      const targetName = String(targetModel?.filename || "this model");
      const targetLabel = String(latestStatus?.storage_targets?.ssd?.label || "attached SSD");
      const confirmed = window.confirm(`Move ${targetName} onto ${targetLabel} now?`);
      if (!confirmed) return;
      modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/move-to-ssd", { model_id: modelId });
        if (!res.ok) {
          appendMessage("assistant", `Could not move model to SSD (${body?.reason || res.status}).`);
          return;
        }
        setComposerActivity(`${targetName} moved to SSD.`);
      } catch (err) {
        appendMessage("assistant", `Could not move model to SSD: ${err}`);
      } finally {
        modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function deleteSelectedModel(modelId) {
      if (!modelId) return;
      if (modelActionInFlight) return;
      const targetModel = findModelInLatestStatus(modelId);
      const targetName = String(targetModel?.filename || "this model");
      const isDownloading = targetModel?.status === "downloading";
      const confirmMessage = isDownloading
        ? `Cancel the download for ${targetName} and delete any partially downloaded data?`
        : `Delete ${targetName} and remove it from the model list?`;
      const confirmed = window.confirm(confirmMessage);
      if (!confirmed) return;
      modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/delete", { model_id: modelId });
        if (!res.ok) {
          appendMessage("assistant", `Could not delete model (${body?.reason || res.status}).`);
          return;
        }
      } catch (err) {
        appendMessage("assistant", `Could not delete model: ${err}`);
      } finally {
        modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function purgeAllModels() {
      if (modelActionInFlight) return;
      const confirmed = window.confirm(
        "Delete ALL model files and clear model/download metadata now?"
      );
      if (!confirmed) return;
      modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/purge", { reset_bootstrap_flag: false });
        if (!res.ok || body?.purged !== true) {
          appendMessage("assistant", `Could not purge models (${body?.reason || res.status}).`);
          return;
        }
        setComposerActivity("All models and metadata were cleared.");
      } catch (err) {
        appendMessage("assistant", `Could not purge models: ${err}`);
      } finally {
        modelActionInFlight = false;
        await pollStatus();
      }
    }

    async function uploadLocalModel() {
      if (uploadRequest) return;
      const input = document.getElementById("modelUploadInput");
      const file = input?.files?.[0];
      if (!file) {
        appendMessage("assistant", "Pick a .gguf file to upload.");
        return;
      }
      if (!String(file.name || "").toLowerCase().endsWith(".gguf")) {
        appendMessage("assistant", "Only .gguf model files are supported.");
        return;
      }

      const xhr = new XMLHttpRequest();
      uploadRequest = xhr;
      const cancelBtn = document.getElementById("cancelUploadBtn");
      if (cancelBtn) cancelBtn.hidden = false;
      setModelUploadStatus("Uploading model... 0%");

      xhr.open("POST", "/internal/models/upload");
      xhr.setRequestHeader("x-potato-filename", file.name);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          const percent = Math.round((event.loaded * 100) / event.total);
          setModelUploadStatus(`Uploading model... ${percent}% (${formatBytes(event.loaded)} / ${formatBytes(event.total)})`);
        } else {
          setModelUploadStatus("Uploading model...");
        }
      };
      xhr.onerror = async () => {
        uploadRequest = null;
        if (cancelBtn) cancelBtn.hidden = true;
        setModelUploadStatus("Upload failed.");
        await pollStatus();
      };
      xhr.onabort = async () => {
        uploadRequest = null;
        if (cancelBtn) cancelBtn.hidden = true;
        setModelUploadStatus("Upload cancelled.");
        await postJson("/internal/models/cancel-upload", {});
        await pollStatus();
      };
      xhr.onload = async () => {
        uploadRequest = null;
        if (cancelBtn) cancelBtn.hidden = true;
        const body = (() => {
          try {
            return JSON.parse(xhr.responseText || "{}");
          } catch (_err) {
            return {};
          }
        })();
        if (xhr.status < 200 || xhr.status >= 300) {
          setModelUploadStatus(`Upload failed (${body?.reason || xhr.status}).`);
        } else if (body?.uploaded) {
          if (input) input.value = "";
          setModelUploadStatus("Upload completed.");
        } else {
          setModelUploadStatus(`Upload did not complete (${body?.reason || "unknown"}).`);
        }
        await pollStatus();
      };
      xhr.send(file);
    }

    function cancelLocalModelUpload() {
      if (!uploadRequest) return;
      uploadRequest.abort();
    }

    async function startModelDownload() {
      if (downloadStartInFlight) return;
      downloadStartInFlight = true;
      renderDownloadPrompt(latestStatus || { download: { auto_start_remaining_seconds: 0 } });
      try {
        const resumableFailedModel = findResumableFailedModel(latestStatus);
        const failedDownload = String(latestStatus?.download?.error || "") === "download_failed";
        let res;
        let body;
        if (resumableFailedModel && failedDownload) {
          ({ res, body } = await postJson("/internal/models/download", { model_id: resumableFailedModel.id }));
        } else {
          res = await fetch("/internal/start-model-download", {
            method: "POST",
            headers: { "content-type": "application/json" },
          });
          body = await res.json().catch(() => ({}));
        }
        if (!res.ok) {
          const reason = body?.reason ? ` (${body.reason})` : "";
          appendMessage(
            "assistant",
            `${resumableFailedModel && failedDownload ? "Could not resume model download" : "Could not start model download"}${reason}.`
          );
          return;
        }
        if (!body?.started && body?.reason === "already_running") {
          setComposerActivity("Model download already running.");
        } else if (!body?.started && body?.reason === "model_present") {
          setComposerActivity("Model already present.");
        } else if (!body?.started && body?.reason === "insufficient_storage") {
          setComposerActivity("Model likely too large for free storage. Delete files and retry.");
        } else if (body?.started) {
          setComposerActivity(resumableFailedModel && failedDownload ? "Model download resumed." : "Model download started.");
        }
      } catch (err) {
        appendMessage(
          "assistant",
          `Could not ${String(latestStatus?.download?.error || "") === "download_failed" ? "resume" : "start"} model download: ${err}`
        );
      } finally {
        downloadStartInFlight = false;
        await pollStatus();
      }
    }

    function setRuntimeResetButtonState(inFlight) {
      const btn = document.getElementById("resetRuntimeBtn");
      if (!btn) return;
      btn.disabled = Boolean(inFlight);
      btn.textContent = inFlight
        ? "Restarting runtime..."
        : "Unload model + clean memory + restart";
    }

    function stopRuntimeReconnectWatch() {
      if (runtimeReconnectWatchTimer) {
        window.clearTimeout(runtimeReconnectWatchTimer);
        runtimeReconnectWatchTimer = null;
      }
      runtimeReconnectWatchActive = false;
      runtimeReconnectAttempts = 0;
    }

    async function stepRuntimeReconnectWatch() {
      if (!runtimeReconnectWatchActive) return;
      runtimeReconnectAttempts += 1;
      const statusPayload = await pollStatus({ timeoutMs: RUNTIME_RECONNECT_TIMEOUT_MS });
      if (isLocalModelConnected(statusPayload)) {
        stopRuntimeReconnectWatch();
        setComposerActivity("Runtime reconnected.");
        window.setTimeout(() => {
          if (!runtimeReconnectWatchActive && !requestInFlight) {
            setComposerActivity("");
          }
        }, 1500);
        return;
      }
      if (runtimeReconnectAttempts >= RUNTIME_RECONNECT_MAX_ATTEMPTS) {
        stopRuntimeReconnectWatch();
        setComposerActivity("");
        appendMessage(
          "assistant",
          "Runtime reset is taking longer than expected. It may still be loading the model. " +
          "Check status in a few moments."
        );
        return;
      }
      runtimeReconnectWatchTimer = window.setTimeout(stepRuntimeReconnectWatch, RUNTIME_RECONNECT_INTERVAL_MS);
    }

    function startRuntimeReconnectWatch() {
      stopRuntimeReconnectWatch();
      runtimeReconnectWatchActive = true;
      runtimeReconnectAttempts = 0;
      setComposerActivity("Runtime reset in progress. Reconnecting...");
      stepRuntimeReconnectWatch();
    }

    async function resetRuntimeHeavy() {
      if (runtimeResetInFlight) return;
      const confirmed = window.confirm(
        "Unload the model, reclaim memory/swap, and restart Potato runtime now? " +
        "The chat will disconnect briefly."
      );
      if (!confirmed) return;

      runtimeResetInFlight = true;
      let shouldTrackReconnect = false;
      setRuntimeResetButtonState(true);
      setComposerActivity("Scheduling runtime reset...");
      try {
        const res = await fetch("/internal/reset-runtime", {
          method: "POST",
          headers: { "content-type": "application/json" },
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          const reason = body?.reason ? ` (${body.reason})` : "";
          appendMessage("assistant", `Could not start runtime reset${reason}.`);
          return;
        }
        if (body?.started) {
          shouldTrackReconnect = true;
          appendMessage(
            "assistant",
            "Runtime reset started. Unloading model from memory and reclaiming RAM/swap. " +
            "Model files on disk are unchanged."
          );
        } else {
          appendMessage("assistant", `Runtime reset did not start (${body?.reason || "unknown"}).`);
        }
      } catch (err) {
        appendMessage("assistant", `Could not start runtime reset: ${err}`);
      } finally {
        runtimeResetInFlight = false;
        setRuntimeResetButtonState(false);
        if (shouldTrackReconnect) {
          startRuntimeReconnectWatch();
        } else {
          setComposerActivity("");
          window.setTimeout(() => {
            pollStatus();
          }, 1000);
        }
      }
    }

    function consumeSseDeltas(state, chunkText) {
      if (!chunkText) return { deltas: [], reasoningDeltas: [], events: [] };
      state.buffer += chunkText.replace(/\\r\\n/g, "\\n");
      const deltas = [];
      const reasoningDeltas = [];
      const events = [];

      while (true) {
        const boundary = state.buffer.indexOf("\\n\\n");
        if (boundary === -1) break;

        const eventBlock = state.buffer.slice(0, boundary);
        state.buffer = state.buffer.slice(boundary + 2);

        const dataPayload = eventBlock
          .split("\\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\\n")
          .trim();

        if (!dataPayload || dataPayload === "[DONE]") continue;

        try {
          const event = JSON.parse(dataPayload);
          events.push(event);
          const delta = event?.choices?.[0]?.delta?.content;
          if (typeof delta === "string") {
            deltas.push(delta);
          }
          const reasoningDelta = event?.choices?.[0]?.delta?.reasoning_content;
          if (typeof reasoningDelta === "string") {
            reasoningDeltas.push(reasoningDelta);
          }
        } catch (_err) {
          // Ignore partial/non-JSON events and continue.
        }
      }

      return { deltas, reasoningDeltas, events };
    }

    function formatReasoningOnlyMessage(reasoningText) {
      const text = String(reasoningText || "").trim();
      if (!text) return "(empty response)";
      return `Thinking...\\n\\n${text}`;
    }

    function formatStopReason(reason) {
      switch (reason) {
        case "stop":
          return "EOS Token found";
        case "length":
          return "Max tokens reached";
        case "tool_calls":
          return "Tool calls emitted";
        case "cancelled":
          return "Stopped by user";
        case null:
        case undefined:
        case "":
          return "Unknown";
        default:
          return String(reason);
      }
    }

    function resolveTimeToFirstTokenMs(source, fallbackMs = 0) {
      const direct = Number(
        source?.timings?.ttft_ms
        ?? source?.timings?.first_token_ms
        ?? source?.timings?.prompt_ms
      );
      if (Number.isFinite(direct) && direct > 0) {
        return direct;
      }
      const fallback = Number(fallbackMs);
      if (Number.isFinite(fallback) && fallback > 0) {
        return fallback;
      }
      return 0;
    }

    function formatAssistantStats(source, elapsedSeconds = 0, firstTokenLatencyMs = 0) {
      const timings = source?.timings && typeof source.timings === "object" ? source.timings : {};
      const usage = source?.usage && typeof source.usage === "object" ? source.usage : {};
      const rawTokens = Number(timings.predicted_n ?? usage.completion_tokens ?? 0);
      const tokens = Number.isFinite(rawTokens) && rawTokens > 0 ? Math.round(rawTokens) : 0;

      const predictedMs = Number(timings.predicted_ms);
      const seconds = Number.isFinite(predictedMs) && predictedMs > 0
        ? predictedMs / 1000
        : Math.max(0, Number(elapsedSeconds) || 0);

      let tokPerSecond = Number(timings.predicted_per_second);
      if (!Number.isFinite(tokPerSecond) || tokPerSecond < 0) {
        tokPerSecond = seconds > 0 ? tokens / seconds : 0;
      }

      const finishReason = source?.finish_reason ?? source?.choices?.[0]?.finish_reason ?? null;
      const ttftMs = resolveTimeToFirstTokenMs(source, firstTokenLatencyMs);
      const ttftText = ttftMs > 0 ? `${(ttftMs / 1000).toFixed(2)}s` : "--";
      return `TTFT ${ttftText} · ${tokPerSecond.toFixed(2)} tok/sec · ${tokens} tokens · ${seconds.toFixed(2)}s · Stop reason: ${formatStopReason(finishReason)}`;
    }

    function classifyPi5MemoryTier(totalBytes) {
      const value = Number(totalBytes);
      if (!Number.isFinite(value) || value <= 0) return null;
      const gib = value / (1024 ** 3);
      const supportedTiers = [1, 2, 4, 8, 16];
      let bestTier = supportedTiers[0];
      let bestDistance = Math.abs(gib - bestTier);
      for (const tier of supportedTiers.slice(1)) {
        const distance = Math.abs(gib - tier);
        if (distance < bestDistance) {
          bestTier = tier;
          bestDistance = distance;
        }
      }
      return `${bestTier}GB`;
    }

    function setSidebarNote(systemPayload) {
      const noteEl = document.getElementById("sidebarNote");
      if (!noteEl) return;
      const piModelName = String(systemPayload?.pi_model_name || "").trim();
      const memoryTier = classifyPi5MemoryTier(systemPayload?.memory_total_bytes);
      if (piModelName && memoryTier) {
        noteEl.textContent = `V0.3 Pre-Alpha · ${piModelName} · ${memoryTier}`;
        return;
      }
      noteEl.textContent = piModelName
        ? `V0.3 Pre-Alpha · ${piModelName}`
        : "V0.3 Pre-Alpha";
    }

    function setStatus(statusPayload) {
      latestStatus = statusPayload;
      const downloadText = formatSidebarStatusDetail(statusPayload);
      const text = `State: ${statusPayload.state} | ${downloadText}`;
      document.getElementById("statusText").textContent = text;
      renderStatusActions(statusPayload);
      setSidebarNote(statusPayload?.system);
      const modelNameField = document.getElementById("modelName");
      if (modelNameField) {
        const modelName = statusPayload?.model?.filename || "Unknown model";
        modelNameField.textContent = statusPayload?.model_present ? modelName : `${modelName} (not loaded)`;
      }
      const countdownSelect = document.getElementById("downloadCountdownEnabled");
      if (countdownSelect) {
        countdownSelect.value = statusPayload?.download?.countdown_enabled === false ? "false" : "true";
      }
      updateLlamaIndicator(statusPayload);
      renderDownloadPrompt(statusPayload);
      renderCompatibilityWarnings(statusPayload);
      renderLlamaRuntimeStatus(statusPayload);
      renderSystemRuntime(statusPayload?.system);
      renderSettingsWorkspace(statusPayload);
      renderUploadState(statusPayload);
      setSendEnabled();
    }

    async function pollStatus(options = {}) {
      const timeoutMs = Math.max(500, Number(options?.timeoutMs || STATUS_POLL_TIMEOUT_MS));
      const seq = ++statusPollSeq;
      const controller = new AbortController();
      const timeoutHandle = window.setTimeout(() => {
        controller.abort();
      }, timeoutMs);
      try {
        const res = await fetch("/status", { cache: "no-store", signal: controller.signal });
        const body = await res.json();
        if (seq < statusPollAppliedSeq) {
          return latestStatus;
        }
        statusPollAppliedSeq = seq;
        setStatus(body);
        return body;
      } catch (err) {
        if (seq < statusPollAppliedSeq) {
          return latestStatus;
        }
        statusPollAppliedSeq = seq;
        const statusErrText = err?.name === "AbortError" ? "request timeout" : String(err);
        if (latestStatus && typeof latestStatus === "object" && latestStatus.state && latestStatus.state !== "DOWN") {
          document.getElementById("statusText").textContent = `Status warning: ${statusErrText}`;
          renderStatusActions({});
          return latestStatus;
        }
        latestStatus = {
          state: "DOWN",
          model_present: false,
          model: { filename: "Unknown model", active_model_id: null },
          models: [],
          download: {
            percent: 0,
            bytes_downloaded: 0,
            bytes_total: 0,
            active: false,
            auto_start_seconds: 0,
            auto_start_remaining_seconds: 0,
            countdown_enabled: true,
            current_model_id: null,
          },
          upload: {
            active: false,
            model_id: null,
            bytes_total: 0,
            bytes_received: 0,
            percent: 0,
            error: null,
          },
          compatibility: {
            device_class: "unknown",
            large_model_warn_threshold_bytes: 0,
            warnings: [],
          },
          llama_runtime: {
            current: {
              install_dir: "",
              exists: false,
              has_server_binary: false,
              source_bundle_path: null,
              source_bundle_name: null,
              profile: null,
            },
            available_bundles: [],
            switch: {
              active: false,
              target_bundle_path: null,
              error: null,
            },
          },
          system: {
            available: false,
            cpu_percent: null,
            cpu_cores_percent: [],
            cpu_clock_arm_hz: null,
            memory_total_bytes: 0,
            memory_used_bytes: 0,
            memory_percent: null,
            swap_total_bytes: 0,
            swap_used_bytes: 0,
            swap_percent: null,
            temperature_c: null,
            gpu_clock_core_hz: null,
            gpu_clock_v3d_hz: null,
            updated_at_unix: null,
            throttling: { any_current: false, current_flags: [], history_flags: [] },
          },
        };
        document.getElementById("statusText").textContent = `Status error: ${statusErrText}`;
        renderStatusActions({});
        const modelNameField = document.getElementById("modelName");
        if (modelNameField) {
          modelNameField.textContent = "Unknown model (status unavailable)";
        }
        updateLlamaIndicator(latestStatus);
        renderDownloadPrompt(latestStatus);
        renderCompatibilityWarnings(latestStatus);
        renderSystemRuntime(latestStatus.system);
        renderSettingsWorkspace(latestStatus);
        renderUploadState(latestStatus);
        setSendEnabled();
        return latestStatus;
      } finally {
        window.clearTimeout(timeoutHandle);
      }
    }

    async function sendChat() {
      if (requestInFlight) return;
      if (imageCancelRecoveryTimer) {
        window.clearTimeout(imageCancelRecoveryTimer);
        imageCancelRecoveryTimer = null;
      }
      if (imageCancelRestartTimer) {
        window.clearTimeout(imageCancelRestartTimer);
        imageCancelRestartTimer = null;
      }
      const userPrompt = document.getElementById("userPrompt");
      if (pendingImage && activeRuntimeVisionCapability(latestStatus) === false) {
        clearPendingImage();
        showTextOnlyImageBlockedState(latestStatus);
        return;
      }
      const content = userPrompt.value.trim();
      if (!content && !pendingImage) return;
      const hasImageRequest = Boolean(pendingImage);
      const selectedImageSize = pendingImage ? (Number(pendingImage.size) || 0) : 0;
      const userMessage = { role: "user", content: buildUserMessageContent(content) };
      const userBubblePayload = buildUserBubblePayload(content);
      const requestStartMs = performance.now();
      const requestCtx = {
        controller: new AbortController(),
        stoppedByUser: false,
        hasImageRequest,
        prefillBucket: "",
        firstTokenLatencyMs: 0,
        generationStarted: false,
      };
      const streamStats = { timings: null, finish_reason: null };
      let activeAssistantView = null;
      activeRequest = requestCtx;

      const settings = collectSettings();

      const baseHistoryLength = chatHistory.length;
      const userView = appendMessage("user", userBubblePayload.text, {
        imageDataUrl: userBubblePayload.imageDataUrl,
        imageName: userBubblePayload.imageName,
        forceFollow: true,
      });
      activeAssistantView = appendMessage("assistant", "", { actionsHidden: true, forceFollow: true });
      const turn = {
        baseHistoryLength,
        userText: content,
        userView,
        assistantView: activeAssistantView,
      };
      userView.turnRef = turn;
      activeAssistantView.turnRef = turn;
      conversationTurns.push(turn);
      requestCtx.turn = turn;
      requestCtx.assistantView = activeAssistantView;
      userPrompt.value = "";
      clearPendingImage();
      focusPromptInput();
      requestInFlight = true;
      setSendEnabled();
      setCancelEnabled(true);

      try {
        const reqBody = {
          model: "qwen-local",
          messages: [],
          temperature: settings.temperature,
          top_p: settings.top_p,
          top_k: settings.top_k,
          repetition_penalty: settings.repetition_penalty,
          presence_penalty: settings.presence_penalty,
          max_tokens: settings.max_tokens,
          stream: settings.stream,
        };
        const resolvedSeed = resolveSeedForRequest(settings);
        if (resolvedSeed !== null) {
          reqBody.seed = resolvedSeed;
        }

        if (settings.system_prompt) {
          reqBody.messages.push({ role: "system", content: settings.system_prompt });
        }
        reqBody.messages = reqBody.messages.concat(chatHistory);
        reqBody.messages.push(userMessage);
        chatHistory.push(userMessage);

        const promptTokens = estimatePromptTokens(reqBody.messages);
        requestCtx.prefillBucket = choosePrefillBucket(hasImageRequest, promptTokens, selectedImageSize);
        beginPrefillProgress(requestCtx, {
          hasImage: hasImageRequest,
          promptTokens,
          imageBytes: selectedImageSize,
          bucket: requestCtx.prefillBucket,
        });

        const res = await fetch("/v1/chat/completions", {
          method: "POST",
          headers: { "content-type": "application/json" },
          signal: requestCtx.controller.signal,
          body: JSON.stringify(reqBody),
        });

        if (!res.ok) {
          stopPrefillProgress();
          const body = await res.json().catch(() => ({}));
          updateMessage(activeAssistantView, formatChatFailureMessage(res.status, body, requestCtx), { showActions: true });
          return;
        }

        if (settings.stream) {
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          const state = { buffer: "" };
          let assistantText = "";
          let assistantReasoningText = "";

          while (true) {
            const { done, value } = await reader.read();
            if (done) {
              const decoded = consumeSseDeltas(state, decoder.decode());
              for (const event of decoded.events) {
                const stop = event?.choices?.[0]?.finish_reason;
                if (stop !== null && stop !== undefined) {
                  streamStats.finish_reason = stop;
                }
                if (event?.timings && typeof event.timings === "object") {
                  streamStats.timings = event.timings;
                }
              }
              break;
            }

            const textChunk = decoder.decode(value, { stream: true });
            const parsed = consumeSseDeltas(state, textChunk);
            for (const delta of parsed.deltas) {
              if (!requestCtx.generationStarted) {
                requestCtx.generationStarted = true;
                requestCtx.firstTokenLatencyMs = Math.max(0, performance.now() - requestStartMs);
                const finishResult = await markPrefillGenerationStarted(requestCtx);
                throwIfRequestStoppedAfterPrefill(requestCtx, finishResult);
              }
              assistantText += delta;
              updateMessage(activeAssistantView, assistantText, { showActions: false });
            }
            for (const reasoningDelta of parsed.reasoningDeltas) {
              assistantReasoningText += reasoningDelta;
              if (!assistantText.trim()) {
                updateMessage(activeAssistantView, formatReasoningOnlyMessage(assistantReasoningText), { showActions: false });
              }
            }
            for (const event of parsed.events) {
              const stop = event?.choices?.[0]?.finish_reason;
              if (stop !== null && stop !== undefined) {
                streamStats.finish_reason = stop;
              }
              if (event?.timings && typeof event.timings === "object") {
                streamStats.timings = event.timings;
              }
            }
          }

          const tailParsed = consumeSseDeltas(state, "\\n\\n");
          for (const delta of tailParsed.deltas) {
            assistantText += delta;
          }
          for (const reasoningDelta of tailParsed.reasoningDeltas) {
            assistantReasoningText += reasoningDelta;
          }
          for (const event of tailParsed.events) {
            const stop = event?.choices?.[0]?.finish_reason;
            if (stop !== null && stop !== undefined) {
              streamStats.finish_reason = stop;
            }
            if (event?.timings && typeof event.timings === "object") {
              streamStats.timings = event.timings;
            }
          }
          if (!requestCtx.generationStarted) {
            requestCtx.generationStarted = true;
            requestCtx.firstTokenLatencyMs = Math.max(0, performance.now() - requestStartMs);
            const finishResult = await markPrefillGenerationStarted(requestCtx);
            throwIfRequestStoppedAfterPrefill(requestCtx, finishResult);
          }
          const finalAssistantText = assistantText.trim() || formatReasoningOnlyMessage(assistantReasoningText);
          updateMessage(activeAssistantView, finalAssistantText, { showActions: true });
          chatHistory.push({ role: "assistant", content: finalAssistantText });
          const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
          if (requestCtx.stoppedByUser) {
            streamStats.finish_reason = "cancelled";
          }
          setMessageMeta(activeAssistantView, formatAssistantStats(streamStats, elapsedSeconds, requestCtx.firstTokenLatencyMs));
          recordPrefillMetric(
            requestCtx.prefillBucket,
            resolvePromptPrefillMs(streamStats, requestCtx.firstTokenLatencyMs),
          );
          return;
        }

        const body = await res.json();
        if (!requestCtx.generationStarted) {
          requestCtx.generationStarted = true;
          requestCtx.firstTokenLatencyMs = Math.max(0, performance.now() - requestStartMs);
          const finishResult = await markPrefillGenerationStarted(requestCtx);
          throwIfRequestStoppedAfterPrefill(requestCtx, finishResult);
        }
        const message = body?.choices?.[0]?.message || {};
        const messageContent = typeof message?.content === "string" ? message.content.trim() : "";
        const msg = messageContent || formatReasoningOnlyMessage(message?.reasoning_content) || JSON.stringify(body);
        chatHistory.push({ role: "assistant", content: msg });
        updateMessage(activeAssistantView, msg, { showActions: true });
        const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
        setMessageMeta(activeAssistantView, formatAssistantStats(body, elapsedSeconds, requestCtx.firstTokenLatencyMs));
        recordPrefillMetric(
          requestCtx.prefillBucket,
          resolvePromptPrefillMs(body, requestCtx.firstTokenLatencyMs),
        );
      } catch (err) {
          if (requestCtx.stoppedByUser) {
            const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
          if (requestCtx.hideProcessingBubbleOnCancel === true) {
            return;
          }
          if (activeAssistantView) {
            const partial = activeAssistantView.bubble.textContent.trim();
            if (!partial) {
              updateMessage(activeAssistantView, "(stopped)", { showActions: true });
            } else {
              chatHistory.push({ role: "assistant", content: partial });
            }
            streamStats.finish_reason = "cancelled";
            setMessageMeta(activeAssistantView, formatAssistantStats(streamStats, elapsedSeconds, requestCtx.firstTokenLatencyMs));
          } else {
            const stoppedDiv = appendMessage("assistant", "(stopped)");
            setMessageMeta(stoppedDiv, formatAssistantStats({ finish_reason: "cancelled" }, elapsedSeconds, requestCtx.firstTokenLatencyMs));
          }
        } else {
          if (activeAssistantView) {
            updateMessage(activeAssistantView, `Request error: ${err}`, { showActions: true });
          } else {
            appendMessage("assistant", `Request error: ${err}`);
          }
        }
      } finally {
        requestInFlight = false;
        activeRequest = null;
        setSendEnabled();
        stopPrefillProgress();
        setComposerActivity("");
        setCancelEnabled(false);
        focusPromptInput();
      }
    }

    function stopGeneration() {
      if (!requestInFlight || !activeRequest) return;
      activeRequest.stoppedByUser = true;
      activeRequest.controller.abort();
    }

    async function requestLlamaCancelRecovery(reason = "cancelled") {
      try {
        const res = await fetch("/internal/cancel-llama", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ reason }),
        });
        if (!res.ok) return { cancelled: false, restarted: false };
        return await res.json().catch(() => ({ cancelled: false, restarted: false }));
      } catch (_err) {
        // Best-effort recovery only.
        return { cancelled: false, restarted: false };
      }
    }

    async function requestLlamaRestart(reason = "cancelled") {
      try {
        const res = await fetch("/internal/restart-llama", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ reason }),
        });
        if (!res.ok) return { restarted: false };
        return await res.json().catch(() => ({ restarted: false }));
      } catch (_err) {
        return { restarted: false };
      }
    }

    async function checkLlamaHealthStrict() {
      try {
        const res = await fetch("/internal/llama-healthz", { cache: "no-store" });
        if (!res.ok) return false;
        const payload = await res.json().catch(() => ({}));
        return payload?.healthy === true;
      } catch (_err) {
        return false;
      }
    }

    function scheduleImageCancelRestartFallback() {
      if (imageCancelRestartTimer) {
        window.clearTimeout(imageCancelRestartTimer);
      }
      imageCancelRestartTimer = window.setTimeout(async () => {
        imageCancelRestartTimer = null;
        if (requestInFlight) {
          return;
        }
        const healthy = await checkLlamaHealthStrict();
        if (healthy) {
          return;
        }
        setComposerActivity("Restarting model after stalled cancel...");
        setComposerStatusChip("Restarting model...", { phase: "cancel" });
        await requestLlamaRestart("image_cancel_stalled");
        await pollStatus();
        setComposerActivity("");
        hideComposerStatusChip();
      }, IMAGE_CANCEL_RESTART_DELAY_MS);
    }

    function queueImageCancelRecovery(requestCtx) {
      if (!requestCtx?.hasImageRequest) {
        return;
      }
      if (imageCancelRecoveryTimer) {
        window.clearTimeout(imageCancelRecoveryTimer);
      }
      imageCancelRecoveryTimer = window.setTimeout(async () => {
        imageCancelRecoveryTimer = null;
        if (requestInFlight) {
          return;
        }
        const healthy = await checkLlamaHealthStrict();
        if (healthy) {
          return;
        }
        setComposerActivity("Recovering model after image cancel...");
        setComposerStatusChip("Recovering model...", { phase: "cancel" });
        const recovery = await requestLlamaCancelRecovery("image_cancel_timeout");
        if (recovery?.cancelled) {
          setComposerActivity("");
          hideComposerStatusChip();
          return;
        }
        if (recovery?.restarted) {
          setComposerActivity("Restarting model...");
          await pollStatus();
          setComposerActivity("");
          hideComposerStatusChip();
          return;
        }
        setComposerActivity("Waiting for model to finish cancel...");
        setComposerStatusChip("Finalizing cancel...", { phase: "cancel" });
        scheduleImageCancelRestartFallback();
      }, IMAGE_CANCEL_RECOVERY_DELAY_MS);
    }

    function cancelCurrentWork() {
      if (pendingImageReader) {
        cancelPendingImageWork();
        clearPendingImage();
        setComposerActivity("Image load cancelled.");
        hideComposerStatusChip();
        setCancelEnabled(false);
        return;
      }
      if (requestInFlight) {
        const current = activeRequest;
        stopPrefillProgress({ resetUi: false });
        setComposerActivity("Cancelling...");
        setComposerStatusChip("Cancelling...", { phase: "cancel" });
        setCancelEnabled(false);
        if (current && current.assistantView?.bubble?.classList?.contains("processing")) {
          current.hideProcessingBubbleOnCancel = true;
          removeMessage(current.assistantView);
        }
        stopGeneration();
        queueImageCancelRecovery(current);
      }
    }

    function toggleTheme() {
      const current = document.documentElement.getAttribute("data-theme") || defaultSettings.theme;
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
      saveSettings({ theme: next });
    }

    bindSettings();
    bindSettingsModal();
    bindEditModal();
    bindMobileSidebar();
    bindMessagesScroller();
    setRuntimeDetailsExpanded(true);
    setInterval(() => {
      if (settingsModalOpen) return;
      pollStatus();
    }, 2000);
    pollStatus();

    document.getElementById("themeToggle").addEventListener("click", toggleTheme);
    document.getElementById("sidebarToggle").addEventListener("click", () => {
      setSidebarOpen(!document.body.classList.contains("sidebar-open"));
    });
    document.getElementById("sidebarCloseBtn").addEventListener("click", () => {
      setSidebarOpen(false);
    });
    document.getElementById("sidebarBackdrop").addEventListener("click", () => {
      setSidebarOpen(false);
    });
    document.getElementById("runtimeViewToggle").addEventListener("click", () => {
      setRuntimeDetailsExpanded(!runtimeDetailsExpanded);
    });
    document.getElementById("startDownloadBtn").addEventListener("click", startModelDownload);
    document.getElementById("statusResumeDownloadBtn").addEventListener("click", startModelDownload);
    document.getElementById("registerModelBtn").addEventListener("click", registerModelFromUrl);
    document.getElementById("uploadModelBtn").addEventListener("click", uploadLocalModel);
    document.getElementById("cancelUploadBtn").addEventListener("click", cancelLocalModelUpload);
    document.getElementById("purgeModelsBtn").addEventListener("click", purgeAllModels);
    document.getElementById("applyLargeModelOverrideBtn").addEventListener("click", applyLargeModelOverrideFromSettings);
    document.getElementById("compatibilityOverrideBtn").addEventListener("click", allowUnsupportedLargeModelFromWarning);
    document.getElementById("applyLlamaMemoryLoadingBtn").addEventListener("click", applyLlamaMemoryLoadingMode);
    document.getElementById("switchLlamaRuntimeBtn").addEventListener("click", switchLlamaRuntimeBundle);
    document.getElementById("capturePowerCalibrationSampleBtn").addEventListener("click", capturePowerCalibrationSample);
    document.getElementById("fitPowerCalibrationBtn").addEventListener("click", fitPowerCalibrationModel);
    document.getElementById("resetPowerCalibrationBtn").addEventListener("click", resetPowerCalibrationModel);
    document.getElementById("modelsList").addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const action = target.dataset?.action;
      const row = target.closest(".model-row");
      const modelId = row?.dataset?.modelId;
      const selectedModel = resolveSelectedSettingsModel(latestStatus);
      const selectedModelId = String(selectedModel?.id || "");
      const targetDiffers = Boolean(modelId) && String(modelId) !== selectedModelId;
      if (targetDiffers && selectedModelHasUnsavedChanges()) {
        blockModelSelectionChange();
        return;
      }
      if (!action) {
        if (modelId) {
          selectedSettingsModelId = String(modelId);
          renderSettingsWorkspace(latestStatus);
        }
        return;
      }
      if (action === "download") {
        startModelDownloadForModel(modelId);
      } else if (action === "cancel-download") {
        cancelActiveModelDownload(modelId);
      } else if (action === "activate") {
        activateSelectedModel(modelId);
      } else if (action === "move-to-ssd") {
        moveModelToSsd(modelId);
      } else if (action === "delete") {
        deleteSelectedModel(modelId);
      }
    });
    document.getElementById("resetRuntimeBtn").addEventListener("click", resetRuntimeHeavy);
    document.getElementById("attachImageBtn").addEventListener("click", openImagePicker);
    document.getElementById("cancelBtn").addEventListener("click", cancelCurrentWork);
    document.getElementById("clearImageBtn").addEventListener("click", (event) => {
      event.preventDefault();
      clearPendingImage();
    });
    document.getElementById("imageInput").addEventListener("change", (event) => {
      const file = event.target?.files?.[0] || null;
      handleImageSelected(file);
    });
    document.getElementById("sendBtn").addEventListener("click", (event) => {
      event.preventDefault();
      if (requestInFlight) {
        cancelCurrentWork();
        return;
      }
      sendChat();
    });
    document.getElementById("composerForm").addEventListener("submit", (event) => {
      event.preventDefault();
      sendChat();
    });
    const userPrompt = document.getElementById("userPrompt");
    userPrompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendChat();
      }
    });
  </script>
</body>
</html>
"""


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
                proc.terminate()
                await proc.wait()

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
    async def switch_llama_runtime_bundle(
        request: Request,
        runtime_cfg: RuntimeConfig = Depends(get_runtime),
    ) -> JSONResponse:
        if not runtime_cfg.enable_orchestrator:
            return JSONResponse(
                status_code=409,
                content={"switched": False, "reason": "orchestrator_disabled"},
            )

        payload = await request.json()
        bundle_path = str(payload.get("bundle_path") or "").strip()
        if not bundle_path:
            return JSONResponse(status_code=400, content={"switched": False, "reason": "bundle_path_required"})

        bundle = find_llama_runtime_bundle_by_path(runtime_cfg, bundle_path)
        if bundle is None:
            return JSONResponse(status_code=404, content={"switched": False, "reason": "bundle_not_found"})

        async with app.state.llama_runtime_switch_lock:
            switch_state = app.state.llama_runtime_switch_state
            if switch_state.get("active"):
                return JSONResponse(status_code=409, content={"switched": False, "reason": "switch_already_running"})

            switch_state.update(
                {
                    "active": True,
                    "target_bundle_path": str(bundle.get("path") or bundle_path),
                    "started_at_unix": int(time.time()),
                    "completed_at_unix": None,
                    "error": None,
                }
            )

            try:
                restarted, restart_reason = await restart_managed_llama_process(app)
                install_result = await install_llama_runtime_bundle(runtime_cfg, Path(str(bundle["path"])))
                if not install_result.get("ok"):
                    reason = str(install_result.get("reason") or "install_failed")
                    switch_state.update(
                        {
                            "active": False,
                            "completed_at_unix": int(time.time()),
                            "error": reason,
                            "last_bundle_path": switch_state.get("last_bundle_path"),
                        }
                    )
                    return JSONResponse(
                        status_code=500,
                        content={
                            "switched": False,
                            "reason": reason,
                            "bundle": bundle,
                            "restarted": restarted,
                            "restart_reason": restart_reason,
                        },
                    )

                marker = write_llama_runtime_bundle_marker(runtime_cfg, bundle)
                switch_state.update(
                    {
                        "active": False,
                        "target_bundle_path": None,
                        "completed_at_unix": int(time.time()),
                        "error": None,
                        "last_bundle_path": str(bundle.get("path") or ""),
                    }
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "switched": True,
                        "reason": "bundle_switched",
                        "bundle": bundle,
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
        return JSONResponse(
            status_code=200,
            content={
                "updated": False,
                "reason": "temporarily_disabled",
                "countdown_enabled": False,
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
                assert isinstance(models, list)
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
