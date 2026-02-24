from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

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

logger = logging.getLogger("potato")
logging.basicConfig(level=logging.INFO)

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover - optional on non-Pi dev hosts
    psutil = None  # type: ignore[assignment]

MODEL_FILENAME = "Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
MODEL_URL = (
    "https://huggingface.co/unsloth/Qwen3-VL-4B-Instruct-GGUF/resolve/main/"
    "Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
)
MODELS_STATE_VERSION = 1
MAX_MODEL_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
MODEL_UPLOAD_PURGE_WAIT_TIMEOUT_SECONDS = 20.0

DEFAULT_CHAT_SETTINGS = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "repetition_penalty": 1.0,
    "presence_penalty": 1.5,
    "max_tokens": 16384,
    "stream": True,
}

THROTTLE_FLAG_BITS = {
    0: "Undervoltage",
    1: "Frequency capped",
    2: "Throttled",
    3: "Soft temp limit",
}
THROTTLE_HISTORY_BITS = {
    16: "Undervoltage occurred",
    17: "Frequency capped occurred",
    18: "Throttling occurred",
    19: "Soft temp limit occurred",
}


def _empty_model_upload_state() -> dict[str, Any]:
    return {
        "active": False,
        "model_id": None,
        "bytes_total": 0,
        "bytes_received": 0,
        "percent": 0,
        "error": None,
    }


@dataclass
class RuntimeConfig:
    base_dir: Path
    model_path: Path
    download_state_path: Path
    models_state_path: Path
    llama_base_url: str
    chat_backend_mode: str
    web_port: int
    llama_port: int
    enable_orchestrator: bool
    auto_download_idle_seconds: int = 300
    allow_fake_fallback: bool = False
    ensure_model_script: Path | None = None
    start_llama_script: Path | None = None
    runtime_reset_service: str = "potato-runtime-reset.service"

    def __post_init__(self) -> None:
        if self.ensure_model_script is None:
            self.ensure_model_script = self.base_dir / "bin" / "ensure_model.sh"
        if self.start_llama_script is None:
            self.start_llama_script = self.base_dir / "bin" / "start_llama.sh"

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        base_dir_env = os.getenv("POTATO_BASE_DIR")
        if base_dir_env:
            base_dir = Path(base_dir_env)
        else:
            preferred = Path("/opt/potato")
            if preferred.exists() and os.access(preferred, os.W_OK):
                base_dir = preferred
            else:
                base_dir = Path.home() / ".cache" / "potato-os"
        model_path = Path(os.getenv("POTATO_MODEL_PATH", str(base_dir / "models" / MODEL_FILENAME)))
        download_state_path = Path(
            os.getenv("POTATO_DOWNLOAD_STATE_PATH", str(base_dir / "state" / "download.json"))
        )
        models_state_path = Path(
            os.getenv("POTATO_MODELS_STATE_PATH", str(base_dir / "state" / "models.json"))
        )
        llama_base_url = os.getenv("POTATO_LLAMA_BASE_URL", "http://127.0.0.1:8080")
        chat_backend_mode = os.getenv("POTATO_CHAT_BACKEND", "llama").strip().lower()
        web_port = int(os.getenv("POTATO_WEB_PORT", "1983"))
        llama_port = int(os.getenv("POTATO_LLAMA_PORT", "8080"))
        ensure_model_script = Path(
            os.getenv("POTATO_ENSURE_MODEL_SCRIPT", str(base_dir / "bin" / "ensure_model.sh"))
        )
        start_llama_script = Path(
            os.getenv("POTATO_START_LLAMA_SCRIPT", str(base_dir / "bin" / "start_llama.sh"))
        )
        runtime_reset_service = os.getenv("POTATO_RUNTIME_RESET_SERVICE", "potato-runtime-reset.service").strip()
        enable_orchestrator = os.getenv("POTATO_ENABLE_ORCHESTRATOR", "1") == "1"
        auto_download_idle_seconds = max(0, _safe_int(os.getenv("POTATO_AUTO_DOWNLOAD_IDLE_SECONDS", "300"), 300))
        allow_fake_fallback = os.getenv("POTATO_ALLOW_FAKE_FALLBACK", "0") == "1"

        return cls(
            base_dir=base_dir,
            model_path=model_path,
            download_state_path=download_state_path,
            models_state_path=models_state_path,
            llama_base_url=llama_base_url.rstrip("/"),
            chat_backend_mode=chat_backend_mode,
            web_port=web_port,
            llama_port=llama_port,
            ensure_model_script=ensure_model_script,
            start_llama_script=start_llama_script,
            runtime_reset_service=runtime_reset_service,
            enable_orchestrator=enable_orchestrator,
            auto_download_idle_seconds=auto_download_idle_seconds,
            allow_fake_fallback=allow_fake_fallback,
        )


def get_runtime(request: Request) -> RuntimeConfig:
    return request.app.state.runtime


def get_chat_repository(request: Request) -> ChatRepositoryManager:
    return request.app.state.chat_repository


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.warning("Could not persist JSON state to %s", path, exc_info=True)


def _model_file_path(runtime: RuntimeConfig, filename: str) -> Path:
    return runtime.base_dir / "models" / filename


def _sanitize_filename(filename: str) -> str:
    candidate = Path(filename).name.strip()
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate)
    candidate = candidate.lstrip(".")
    return candidate or "model.gguf"


def _slugify_id(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug or "model"


def _unique_model_id(base_id: str, existing_ids: set[str]) -> str:
    candidate = base_id
    idx = 2
    while candidate in existing_ids:
        candidate = f"{base_id}-{idx}"
        idx += 1
    return candidate


def _unique_filename(base_name: str, existing_names: set[str]) -> str:
    stem = Path(base_name).stem
    suffix = Path(base_name).suffix or ".gguf"
    candidate = f"{stem}{suffix}"
    idx = 2
    while candidate in existing_names:
        candidate = f"{stem}-{idx}{suffix}"
        idx += 1
    return candidate


def validate_model_url(source_url: str) -> tuple[bool, str, str]:
    parsed = urlparse(source_url.strip())
    if parsed.scheme != "https":
        return False, "https_required", ""
    basename = unquote(Path(parsed.path).name)
    if not basename:
        return False, "filename_missing", ""
    if not basename.lower().endswith(".gguf"):
        return False, "gguf_required", ""
    safe_name = _sanitize_filename(basename)
    if not safe_name.lower().endswith(".gguf"):
        safe_name = f"{Path(safe_name).stem}.gguf"
    return True, "", safe_name


def _default_model_record(_runtime: RuntimeConfig) -> dict[str, Any]:
    return {
        "id": "default",
        "filename": MODEL_FILENAME,
        "source_url": MODEL_URL,
        "source_type": "url",
        "status": "not_downloaded",
        "error": None,
    }


def _normalize_models_state(runtime: RuntimeConfig, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = raw or {}
    models_raw = payload.get("models")
    models: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_filenames: set[str] = set()

    if isinstance(models_raw, list):
        for item in models_raw:
            if not isinstance(item, dict):
                continue
            source_url = str(item.get("source_url") or "")
            filename = _sanitize_filename(str(item.get("filename") or ""))
            if not filename.lower().endswith(".gguf"):
                filename = f"{Path(filename).stem}.gguf"
            item_id_raw = str(item.get("id") or _slugify_id(Path(filename).stem))
            item_id = _unique_model_id(_slugify_id(item_id_raw), seen_ids)
            filename = _unique_filename(filename, seen_filenames)
            seen_ids.add(item_id)
            seen_filenames.add(filename)
            models.append(
                {
                    "id": item_id,
                    "filename": filename,
                    "source_url": source_url or None,
                    "source_type": "upload" if not source_url else str(item.get("source_type") or "url"),
                    "status": str(item.get("status") or "not_downloaded"),
                    "error": item.get("error"),
                }
            )

    if not models:
        default_model = _default_model_record(runtime)
        models.append(default_model)
        seen_ids.add(default_model["id"])
        seen_filenames.add(default_model["filename"])
    elif "default" not in seen_ids:
        default_model = _default_model_record(runtime)
        default_model["id"] = _unique_model_id("default", seen_ids)
        default_model["filename"] = _unique_filename(default_model["filename"], seen_filenames)
        models.insert(0, default_model)
        seen_ids.add(default_model["id"])
        seen_filenames.add(default_model["filename"])

    active_model_id = str(payload.get("active_model_id") or "default")
    if active_model_id not in seen_ids:
        active_model_id = models[0]["id"]

    default_model_id = str(payload.get("default_model_id") or "default")
    if default_model_id not in seen_ids:
        default_model_id = "default" if "default" in seen_ids else models[0]["id"]

    current_download_model_id = payload.get("current_download_model_id")
    if current_download_model_id not in seen_ids:
        current_download_model_id = None

    return {
        "version": MODELS_STATE_VERSION,
        "countdown_enabled": bool(payload.get("countdown_enabled", True)),
        "default_model_downloaded_once": bool(payload.get("default_model_downloaded_once", False)),
        "active_model_id": active_model_id,
        "default_model_id": default_model_id,
        "current_download_model_id": current_download_model_id,
        "models": models,
    }


def ensure_models_state(runtime: RuntimeConfig) -> dict[str, Any]:
    raw: dict[str, Any] | None = None
    if runtime.models_state_path.exists():
        try:
            loaded = json.loads(runtime.models_state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError):
            raw = None

    normalized = _normalize_models_state(runtime, raw)
    default_model_id = str(normalized.get("default_model_id") or "default")
    default_model = get_model_by_id(normalized, default_model_id)
    if isinstance(default_model, dict):
        default_filename = str(default_model.get("filename") or "")
        if default_filename == MODEL_FILENAME and model_file_present(runtime, default_filename):
            normalized["default_model_downloaded_once"] = True
    _atomic_write_json(runtime.models_state_path, normalized)
    return normalized


def save_models_state(runtime: RuntimeConfig, state: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_models_state(runtime, state)
    _atomic_write_json(runtime.models_state_path, normalized)
    return normalized


def get_model_by_id(state: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    for item in state.get("models", []):
        if isinstance(item, dict) and item.get("id") == model_id:
            return item
    return None


def resolve_active_model(state: dict[str, Any], runtime: RuntimeConfig) -> tuple[dict[str, Any], Path]:
    active_id = str(state.get("active_model_id") or "")
    model = get_model_by_id(state, active_id)
    if model is None:
        model = state["models"][0]
        state["active_model_id"] = model["id"]
    path = _model_file_path(runtime, str(model["filename"]))
    runtime.model_path = path
    return model, path


def default_system_metrics_snapshot() -> dict[str, Any]:
    return {
        "available": False,
        "updated_at_unix": None,
        "cpu_percent": None,
        "cpu_cores_percent": [],
        "cpu_clock_arm_hz": None,
        "memory_total_bytes": 0,
        "memory_used_bytes": 0,
        "memory_percent": None,
        "swap_total_bytes": 0,
        "swap_used_bytes": 0,
        "swap_percent": None,
        "storage_total_bytes": 0,
        "storage_used_bytes": 0,
        "storage_free_bytes": 0,
        "storage_percent": None,
        "temperature_c": None,
        "gpu_clock_core_hz": None,
        "gpu_clock_v3d_hz": None,
        "throttling": {
            "raw": None,
            "any_current": False,
            "any_history": False,
            "current_flags": [],
            "history_flags": [],
        },
    }


def decode_throttled_bits(raw_value: int) -> dict[str, Any]:
    current_flags = [
        label
        for bit, label in THROTTLE_FLAG_BITS.items()
        if raw_value & (1 << bit)
    ]
    history_flags = [
        label
        for bit, label in THROTTLE_HISTORY_BITS.items()
        if raw_value & (1 << bit)
    ]
    return {
        "raw": f"0x{raw_value:x}",
        "any_current": len(current_flags) > 0,
        "any_history": len(history_flags) > 0,
        "current_flags": current_flags,
        "history_flags": history_flags,
    }


def _run_vcgencmd(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["vcgencmd", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _parse_vcgencmd_temp(raw_text: str | None) -> float | None:
    if not raw_text:
        return None
    match = re.search(r"temp=([0-9]+(?:\\.[0-9]+)?)'C", raw_text)
    if not match:
        return None
    return _safe_float(match.group(1), default=0.0)


def _parse_vcgencmd_clock_hz(raw_text: str | None) -> int | None:
    if not raw_text:
        return None
    match = re.search(r"=([0-9]+)$", raw_text)
    if not match:
        return None
    value = _safe_int(match.group(1), default=-1)
    return value if value >= 0 else None


def _read_sysfs_temp() -> float | None:
    zone_path = Path("/sys/class/thermal/thermal_zone0/temp")
    if not zone_path.exists():
        return None
    try:
        milli_c = _safe_int(zone_path.read_text(encoding="utf-8").strip(), default=-1)
    except OSError:
        return None
    if milli_c < 0:
        return None
    return milli_c / 1000.0


def prime_system_metrics_counters() -> None:
    if psutil is None:
        return
    try:
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
    except Exception:
        return


def collect_system_metrics_snapshot() -> dict[str, Any]:
    snapshot = default_system_metrics_snapshot()
    snapshot["updated_at_unix"] = int(time.time())

    metrics_collected = False

    if psutil is not None:
        try:
            cpu_total = psutil.cpu_percent(interval=None)
            cpu_cores = psutil.cpu_percent(interval=None, percpu=True)
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            snapshot["cpu_percent"] = round(_safe_float(cpu_total), 2)
            snapshot["cpu_cores_percent"] = [round(_safe_float(value), 2) for value in list(cpu_cores)]
            cpu_freq = psutil.cpu_freq()
            if cpu_freq is not None:
                freq_current_mhz = _safe_float(getattr(cpu_freq, "current", None), default=-1)
                if freq_current_mhz > 0:
                    snapshot["cpu_clock_arm_hz"] = int(round(freq_current_mhz * 1_000_000))
            snapshot["memory_total_bytes"] = int(memory.total)
            snapshot["memory_used_bytes"] = int(memory.used)
            snapshot["memory_percent"] = round(_safe_float(memory.percent), 2)
            snapshot["swap_total_bytes"] = int(swap.total)
            snapshot["swap_used_bytes"] = int(swap.used)
            snapshot["swap_percent"] = round(_safe_float(swap.percent), 2)
            storage = psutil.disk_usage("/")
            snapshot["storage_total_bytes"] = int(storage.total)
            snapshot["storage_used_bytes"] = int(storage.used)
            snapshot["storage_free_bytes"] = int(storage.free)
            snapshot["storage_percent"] = round(_safe_float(storage.percent), 2)
            metrics_collected = True
        except Exception:
            logger.exception("system metrics collection failed (psutil)")

    temp_c = _parse_vcgencmd_temp(_run_vcgencmd("measure_temp"))
    if temp_c is None:
        temp_c = _read_sysfs_temp()
    if temp_c is not None:
        snapshot["temperature_c"] = round(temp_c, 2)
        metrics_collected = True

    core_hz = _parse_vcgencmd_clock_hz(_run_vcgencmd("measure_clock", "core"))
    if core_hz is not None:
        snapshot["gpu_clock_core_hz"] = core_hz
        metrics_collected = True

    v3d_hz = _parse_vcgencmd_clock_hz(_run_vcgencmd("measure_clock", "v3d"))
    if v3d_hz is not None:
        snapshot["gpu_clock_v3d_hz"] = v3d_hz
        metrics_collected = True

    arm_hz = _parse_vcgencmd_clock_hz(_run_vcgencmd("measure_clock", "arm"))
    if arm_hz is not None:
        snapshot["cpu_clock_arm_hz"] = arm_hz
        metrics_collected = True

    throttled_text = _run_vcgencmd("get_throttled")
    if throttled_text:
        match = re.search(r"0x([0-9a-fA-F]+)", throttled_text)
        if match:
            raw_value = int(match.group(1), 16)
            snapshot["throttling"] = decode_throttled_bits(raw_value)
            metrics_collected = True

    snapshot["available"] = bool(metrics_collected)
    return snapshot


async def system_metrics_loop(app: FastAPI) -> None:
    while True:
        try:
            app.state.system_metrics_snapshot = collect_system_metrics_snapshot()
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("system metrics loop error")
            await asyncio.sleep(2)


def read_download_progress(runtime: RuntimeConfig) -> dict[str, Any]:
    progress = {
        "bytes_total": 0,
        "bytes_downloaded": 0,
        "percent": 0,
        "speed_bps": 0,
        "eta_seconds": 0,
        "error": None,
    }

    if not runtime.download_state_path.exists():
        return progress

    try:
        raw = json.loads(runtime.download_state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return progress

    progress["bytes_total"] = _safe_int(raw.get("bytes_total"), 0)
    progress["bytes_downloaded"] = _safe_int(raw.get("bytes_downloaded"), 0)
    progress["percent"] = _safe_int(raw.get("percent"), 0)
    progress["speed_bps"] = _safe_int(raw.get("speed_bps"), 0)
    progress["eta_seconds"] = _safe_int(raw.get("eta_seconds"), 0)
    progress["error"] = raw.get("error")

    if progress["bytes_total"] > 0 and progress["percent"] == 0:
        progress["percent"] = int(progress["bytes_downloaded"] * 100 / progress["bytes_total"])

    return progress


def get_free_storage_bytes(runtime: RuntimeConfig) -> int:
    if psutil is None:
        return 0
    probe_path = runtime.base_dir if runtime.base_dir.exists() else Path("/")
    try:
        usage = psutil.disk_usage(str(probe_path))
        return int(max(0, usage.free))
    except OSError:
        return 0


def compute_required_download_bytes(total_bytes: int, partial_bytes: int = 0) -> int:
    required = max(0, int(total_bytes) - max(0, int(partial_bytes)))
    return int(required)


def is_likely_too_large_for_storage(
    *,
    total_bytes: int,
    free_bytes: int,
    partial_bytes: int = 0,
) -> bool:
    if int(total_bytes) <= 0:
        return False
    required = compute_required_download_bytes(total_bytes, partial_bytes)
    return int(free_bytes) < required


async def fetch_remote_content_length_bytes(source_url: str) -> int:
    timeout = httpx.Timeout(8.0, connect=3.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            response = await client.head(source_url)
            header = response.headers.get("content-length")
            if header:
                return max(0, int(header))
        except (httpx.HTTPError, ValueError):
            pass
        try:
            response = await client.get(source_url, headers={"range": "bytes=0-0"})
        except httpx.HTTPError:
            return 0
        content_range = response.headers.get("content-range", "")
        if "/" in content_range:
            try:
                return max(0, int(content_range.split("/")[-1]))
            except ValueError:
                return 0
        header = response.headers.get("content-length")
        try:
            return max(0, int(header)) if header else 0
        except ValueError:
            return 0


async def check_llama_health(runtime: RuntimeConfig, *, busy_is_healthy: bool = True) -> bool:
    timeout = httpx.Timeout(2.0, connect=1.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for path in ("/health", "/v1/models"):
            try:
                response = await client.get(f"{runtime.llama_base_url}{path}")
            except httpx.ReadTimeout:
                if busy_is_healthy:
                    # Long multimodal requests can hold the single inference slot.
                    # A read timeout here usually means "busy", not "down".
                    return True
                continue
            except httpx.HTTPError:
                continue
            if response.status_code < 500:
                return True
    return False


async def probe_llama_inference_slot(runtime: RuntimeConfig) -> bool:
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
                f"{runtime.llama_base_url}/v1/chat/completions",
                json=payload,
            )
        return response.status_code < 500
    except httpx.HTTPError:
        return False


async def request_llama_slot_cancel(runtime: RuntimeConfig) -> tuple[bool, str]:
    timeout = httpx.Timeout(3.0, connect=1.0)
    actions = ("erase",)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for action in actions:
            try:
                response = await client.post(f"{runtime.llama_base_url}/slots/0?action={action}")
            except httpx.HTTPError:
                continue
            if response.status_code < 400:
                return True, action

    return False, "none"


async def restart_managed_llama_process(app: FastAPI) -> tuple[bool, str]:
    proc = app.state.llama_process
    if proc is None or proc.returncode is not None:
        return False, "no_running_process"

    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=3.0)

    app.state.llama_process = None
    return True, "terminated_running_process"


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


def get_monotonic_time() -> float:
    return asyncio.get_running_loop().time()


def model_present(runtime: RuntimeConfig) -> bool:
    state = ensure_models_state(runtime)
    _, active_model_path = resolve_active_model(state, runtime)
    try:
        return active_model_path.exists() and active_model_path.stat().st_size > 0
    except OSError:
        return False


def model_file_present(runtime: RuntimeConfig, filename: str) -> bool:
    path = _model_file_path(runtime, filename)
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


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
    llama_healthy = False

    if has_model:
        llama_healthy = await check_llama_health(runtime)

    active_backend, fallback_active = _resolve_backend_active(runtime, has_model, llama_healthy)
    effective_mode = runtime.chat_backend_mode
    if effective_mode not in {"auto", "llama", "fake"}:
        effective_mode = "llama"
    if effective_mode == "fake" and not runtime.allow_fake_fallback:
        effective_mode = "llama"

    if active_backend == "fake":
        state = "READY"
    elif has_model and llama_healthy:
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
                **model_progress,
            }
        )

    download_payload = dict(download)
    download_payload["active"] = bool(download_active)
    download_payload["auto_start_seconds"] = int(max(0, runtime.auto_download_idle_seconds))
    download_payload["auto_start_remaining_seconds"] = int(max(0, auto_start_remaining_seconds))
    download_payload["countdown_enabled"] = bool(models_state.get("countdown_enabled", True))
    download_payload["auto_download_completed_once"] = bool(models_state.get("default_model_downloaded_once", False))
    download_payload["current_model_id"] = current_download_model_id

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

    return {
        "state": state,
        "model_present": has_model,
        "model": {
            "filename": active_model_path.name,
            "active_model_id": models_state.get("active_model_id"),
        },
        "models": models_payload,
        "download": download_payload,
        "upload": upload_snapshot,
        "llama_server": {
            "running": llama_healthy,
            "healthy": llama_healthy,
            "url": runtime.llama_base_url,
        },
        "backend": {
            "mode": effective_mode,
            "active": active_backend,
            "fallback_active": fallback_active,
        },
        "system": system_snapshot if isinstance(system_snapshot, dict) else default_system_metrics_snapshot(),
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


async def cancel_model_download(app: FastAPI, runtime: RuntimeConfig) -> tuple[bool, str]:
    async with app.state.download_lock:
        task = app.state.model_download_task
        if not is_download_task_active(task):
            return False, "not_running"

        proc = app.state.model_download_process
        if proc is not None and proc.returncode is None:
            proc.terminate()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        state = ensure_models_state(runtime)
        current_model_id = state.get("current_download_model_id")
        if isinstance(current_model_id, str):
            _upsert_model_status(
                runtime,
                model_id=current_model_id,
                status="not_downloaded",
                error=None,
                current_download_model_id=None,
            )
        else:
            state["current_download_model_id"] = None
            save_models_state(runtime, state)
        return True, "cancelled"


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


def set_download_countdown_enabled(runtime: RuntimeConfig, enabled: bool) -> dict[str, Any]:
    state = ensure_models_state(runtime)
    state["countdown_enabled"] = bool(enabled)
    return save_models_state(runtime, state)


def register_model_url(runtime: RuntimeConfig, source_url: str, alias: str | None = None) -> tuple[bool, str, dict[str, Any] | None]:
    ok, reason, filename = validate_model_url(source_url)
    if not ok:
        return False, reason, None

    state = ensure_models_state(runtime)
    models = state.get("models", [])
    assert isinstance(models, list)
    existing_ids = {str(item.get("id")) for item in models if isinstance(item, dict)}
    existing_names = {str(item.get("filename")) for item in models if isinstance(item, dict)}

    for item in models:
        if isinstance(item, dict) and str(item.get("source_url") or "") == source_url:
            saved = save_models_state(runtime, state)
            model = get_model_by_id(saved, str(item.get("id") or ""))
            return True, "already_exists", model

    preferred_name = filename
    if alias:
        alias_safe = _sanitize_filename(alias)
        if not alias_safe.lower().endswith(".gguf"):
            alias_safe = f"{Path(alias_safe).stem}.gguf"
        if alias_safe:
            preferred_name = alias_safe

    final_name = _unique_filename(preferred_name, existing_names)
    model_id = _unique_model_id(_slugify_id(Path(final_name).stem), existing_ids)
    model_record = {
        "id": model_id,
        "filename": final_name,
        "source_url": source_url,
        "source_type": "url",
        "status": "ready" if model_file_present(runtime, final_name) else "not_downloaded",
        "error": None,
    }
    models.append(model_record)
    saved = save_models_state(runtime, state)
    created = get_model_by_id(saved, model_id)
    return True, "registered", created


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
    runtime.model_path = _model_file_path(runtime, filename)
    restarted, _reason = await restart_managed_llama_process(app)
    return True, "activated", restarted


def delete_model(runtime: RuntimeConfig, *, model_id: str) -> tuple[bool, str, bool, int, bool]:
    state = ensure_models_state(runtime)
    active_model_id = str(state.get("active_model_id") or "")
    target = get_model_by_id(state, model_id)
    if target is None:
        return False, "model_not_found", False, 0, False
    was_active = model_id == active_model_id

    filename = str(target.get("filename") or "")
    models = state.get("models", [])
    assert isinstance(models, list)

    same_filename_elsewhere = any(
        isinstance(item, dict)
        and str(item.get("id") or "") != model_id
        and str(item.get("filename") or "") == filename
        for item in models
    )

    deleted_file = False
    freed_bytes = 0
    if filename and not same_filename_elsewhere:
        model_path = _model_file_path(runtime, filename)
        if model_path.exists():
            try:
                freed_bytes = max(0, model_path.stat().st_size)
            except OSError:
                freed_bytes = 0
            try:
                model_path.unlink(missing_ok=True)
                deleted_file = True
            except OSError:
                return False, "delete_failed", False, 0, was_active

    remaining_models = [
        item
        for item in models
        if not (isinstance(item, dict) and str(item.get("id") or "") == model_id)
    ]
    state["models"] = remaining_models
    if was_active:
        next_active_id: str | None = None
        for item in remaining_models:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("id") or "")
            candidate_name = str(item.get("filename") or "")
            if candidate_id and model_file_present(runtime, candidate_name):
                next_active_id = candidate_id
                break
        if next_active_id is None:
            for item in remaining_models:
                if isinstance(item, dict) and item.get("id"):
                    next_active_id = str(item["id"])
                    break
        if next_active_id is None:
            next_active_id = str(state.get("default_model_id") or "default")
        state["active_model_id"] = next_active_id
    save_models_state(runtime, state)
    return True, "deleted", deleted_file, freed_bytes, was_active


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
        if models_dir.exists():
            for path in models_dir.iterdir():
                if not path.is_file():
                    continue
                try:
                    file_size = max(0, int(path.stat().st_size))
                except OSError:
                    file_size = 0
                try:
                    path.unlink(missing_ok=True)
                    deleted_files += 1
                    freed_bytes += file_size
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
                        app.state.llama_process = await asyncio.create_subprocess_exec(
                            str(runtime.start_llama_script),
                            env=_runtime_env(runtime),
                        )
                        logger.info("Started llama-server process")
                    else:
                        logger.warning("start_llama script missing: %s", runtime.start_llama_script)

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
        merged.setdefault(key, value)
    return merged


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
      font-size: 12px;
      line-height: 1.45;
      color: var(--text-muted);
      white-space: pre-wrap;
    }

    .runtime-details {
      margin-top: 8px;
      font-size: 12px;
      line-height: 1.4;
      color: var(--text-muted);
      display: grid;
      gap: 4px;
    }

    .runtime-details[hidden] {
      display: none;
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
    }

    .message-bubble {
      border: 1px solid color-mix(in srgb, var(--border) 82%, transparent);
      border-radius: 18px;
      padding: 13px 15px;
      white-space: pre-wrap;
      line-height: 1.5;
      font-size: 15px;
      box-shadow: 0 3px 14px rgba(16, 23, 42, 0.06);
    }

    .message-bubble.with-image {
      display: flex;
      flex-direction: column;
      gap: 8px;
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

    .message-meta {
      color: var(--text-muted);
      font-size: 12.5px;
      line-height: 1.35;
      padding: 0 4px;
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

    .image-meta {
      font-size: 12px;
      color: var(--text-muted);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 5px 10px;
      background: var(--panel-muted);
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

    .settings {
      border: 1px solid var(--border);
      border-radius: 14px;
      background: var(--panel);
      padding: 10px 12px 12px;
      box-shadow: var(--shadow-soft);
    }

    .settings summary {
      cursor: pointer;
      font-weight: 600;
      color: var(--text);
    }

    .settings-grid {
      margin-top: 10px;
      display: grid;
      gap: 10px;
      grid-template-columns: 1fr;
    }

    .settings-grid label {
      font-size: 13px;
      color: var(--text-muted);
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .settings-grid input,
    .settings-grid select,
    .settings-grid textarea {
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--panel-muted);
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
    }

    .settings-grid textarea {
      min-height: 70px;
      resize: vertical;
    }

    .settings-grid .full {
      grid-column: 1 / -1;
    }

    .settings-action-row {
      display: flex;
      gap: 8px;
      align-items: center;
    }

    .settings-action-row .ghost-btn {
      width: 100%;
      text-align: center;
    }

    .model-row {
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--panel-muted);
      padding: 8px;
      display: grid;
      gap: 6px;
    }

    .model-row-head {
      font-size: 12px;
      color: var(--text);
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
    }

    .model-row-name {
      font-weight: 600;
      overflow-wrap: anywhere;
    }

    .model-status-pill {
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 11px;
      color: var(--text-muted);
      background: var(--panel);
      white-space: nowrap;
    }

    .model-row-actions {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }

    .model-row-actions .ghost-btn {
      width: auto;
      font-size: 12px;
      padding: 5px 10px;
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
      grid-template-columns: 320px 1fr;
    }

    .sidebar {
      padding: 18px 14px;
      background: var(--panel-muted);
      gap: 12px;
      overflow: auto;
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

    .settings {
      box-shadow: none;
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
        <p class="sidebar-note">Local-first chat frontend on your Pi.</p>
        <div id="statusText" class="status-card">Checking status...</div>
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
          <div id="runtimeCompact" class="runtime-compact">CPU -- | Cores -- | GPU -- | Swap -- | Throttle --</div>
          <div id="runtimeDetails" class="runtime-details" hidden>
            <div id="runtimeDetailCpu">CPU total: --</div>
            <div id="runtimeDetailCores">CPU cores: --</div>
            <div id="runtimeDetailCpuClock">CPU clock: --</div>
            <div id="runtimeDetailMemory">Memory: --</div>
            <div id="runtimeDetailSwap">Swap: --</div>
            <div id="runtimeDetailStorage">Storage free: --</div>
            <div id="runtimeDetailTemp">Temperature: --</div>
            <div id="runtimeDetailGpu">GPU clock: --</div>
            <div id="runtimeDetailThrottle">Throttling: --</div>
            <div id="runtimeDetailThrottleHistory">Throttling history: --</div>
            <div id="runtimeDetailUpdated">Updated: --</div>
          </div>
        </div>
      </section>
      <details class="settings">
        <summary>Settings</summary>
        <div class="settings-grid">
          <label class="full">System Prompt (optional)
            <textarea id="systemPrompt" placeholder="Set assistant behavior for this chat"></textarea>
          </label>
          <label class="full">Loaded Model
            <input id="modelName" type="text" value="Checking..." readonly>
          </label>
          <label class="full">Auto-download default model
            <select id="downloadCountdownEnabled">
              <option value="true">Enabled</option>
              <option value="false">Paused</option>
            </select>
          </label>
          <label class="full">Add model by URL
            <input id="modelUrlInput" type="url" placeholder="https://.../model.gguf">
          </label>
          <div class="settings-action-row full">
            <button id="registerModelBtn" class="ghost-btn" type="button">Add URL model</button>
          </div>
          <label class="full">Upload local GGUF to Pi
            <input id="modelUploadInput" type="file" accept=".gguf,application/octet-stream">
          </label>
          <div class="settings-action-row full">
            <button id="uploadModelBtn" class="ghost-btn" type="button">Upload model</button>
            <button id="cancelUploadBtn" class="ghost-btn" type="button" hidden>Cancel upload</button>
            <button id="purgeModelsBtn" class="ghost-btn danger-btn" type="button">Delete all models</button>
          </div>
          <div id="modelUploadStatus" class="runtime-compact full">No upload in progress.</div>
          <div class="full">
            <h3 style="margin: 0 0 8px; font-size: 12px; color: var(--text-muted); text-transform: uppercase;">Available Models</h3>
            <div id="modelsList" class="runtime-details"></div>
          </div>
          <label>Streaming
            <select id="stream">
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          </label>
          <label>Generation Mode
            <select id="generationMode">
              <option value="random">Random</option>
              <option value="deterministic">Deterministic</option>
            </select>
          </label>
          <label>Seed
            <input id="seed" type="number" step="1">
          </label>
          <label>Temperature
            <input id="temperature" type="number" step="0.1" min="0" max="2">
          </label>
          <label>Top P
            <input id="top_p" type="number" step="0.1" min="0" max="1">
          </label>
          <label>Top K
            <input id="top_k" type="number" step="1" min="0">
          </label>
          <label>Repetition Penalty
            <input id="repetition_penalty" type="number" step="0.1" min="0">
          </label>
          <label>Presence Penalty
            <input id="presence_penalty" type="number" step="0.1">
          </label>
          <label>Max Tokens
            <input id="max_tokens" type="number" step="1" min="1">
          </label>
          <div class="settings-action-row full">
            <button id="resetRuntimeBtn" class="ghost-btn danger-btn" type="button">Unload model + clean memory + restart</button>
          </div>
        </div>
      </details>
    </aside>

    <main class="chat-shell">
      <header class="chat-header">
        <div class="header-primary">
          <button id="sidebarToggle" class="sidebar-toggle" type="button" aria-label="Open sidebar" aria-controls="sidebarPanel" aria-expanded="false" hidden>
            <span class="bars" aria-hidden="true">≡</span>
          </button>
          <h1>Potato OS Chat</h1>
        </div>
        <div class="header-actions">
          <span id="statusBadge" class="badge offline">
            <span id="statusDot" class="indicator-dot offline" aria-hidden="true"></span>
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
        <div id="imagePreviewWrap" class="image-preview-wrap" hidden>
          <img id="imagePreview" alt="Selected upload preview">
        </div>
        <div id="composerActivity" class="assistive-live" aria-live="polite"></div>
      </form>

    </main>
  </div>

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
    const PREFILL_PROGRESS_CAP = 95;
    const PREFILL_PROGRESS_FLOOR = 6;
    const PREFILL_TICK_MS = 180;
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
    let uploadRequest = null;
    let runtimeResetInFlight = false;
    let runtimeReconnectWatchActive = false;
    let runtimeReconnectWatchTimer = null;
    let runtimeReconnectAttempts = 0;
    let statusPollSeq = 0;
    let statusPollAppliedSeq = 0;
    let runtimeDetailsExpanded = false;
    let mobileSidebarMql = null;
    const chatHistory = [];
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
        return { ...defaultSettings, theme: detectSystemTheme() };
      }
      try {
        const parsed = { ...defaultSettings, ...JSON.parse(raw) };
        return {
          ...parsed,
          generation_mode: normalizeGenerationMode(parsed.generation_mode),
          seed: normalizeSeedValue(parsed.seed, defaultSettings.seed),
          theme: normalizeTheme(parsed.theme, detectSystemTheme()),
        };
      } catch (_err) {
        return { ...defaultSettings, theme: detectSystemTheme() };
      }
    }

    function saveSettings(settings) {
      localStorage.setItem(settingsKey, JSON.stringify(settings));
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

    function collectSettings() {
      const generationMode = normalizeGenerationMode(document.getElementById("generationMode").value);
      const seed = normalizeSeedValue(document.getElementById("seed").value, defaultSettings.seed);
      return {
        temperature: parseNumber("temperature", defaultSettings.temperature),
        top_p: parseNumber("top_p", defaultSettings.top_p),
        top_k: parseNumber("top_k", defaultSettings.top_k),
        repetition_penalty: parseNumber("repetition_penalty", defaultSettings.repetition_penalty),
        presence_penalty: parseNumber("presence_penalty", defaultSettings.presence_penalty),
        max_tokens: parseNumber("max_tokens", defaultSettings.max_tokens),
        stream: document.getElementById("stream").value === "true",
        generation_mode: generationMode,
        seed,
        theme: document.documentElement.getAttribute("data-theme") || defaultSettings.theme,
        system_prompt: document.getElementById("systemPrompt").value.trim(),
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

    function openImagePicker() {
      if (requestInFlight) return;
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
          setSidebarOpen(false);
        }
      });

      sync();
    }

    function applyTheme(theme) {
      const resolved = theme === "light" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", resolved);
      const toggle = document.getElementById("themeToggle");
      const target = resolved === "dark" ? "light" : "dark";
      toggle.setAttribute("aria-label", `Switch to ${target} theme`);
      toggle.setAttribute("title", `Switch to ${target} theme`);
    }

    function persistSettingsFromInputs() {
      const current = collectSettings();
      saveSettings(current);
      applyTheme(current.theme);
    }

    function bindSettings() {
      const settings = loadSettings();
      const normalizedGenerationMode = normalizeGenerationMode(settings.generation_mode);
      const normalizedSeed = normalizeSeedValue(settings.seed, defaultSettings.seed);

      document.getElementById("temperature").value = String(settings.temperature);
      document.getElementById("top_p").value = String(settings.top_p);
      document.getElementById("top_k").value = String(settings.top_k);
      document.getElementById("repetition_penalty").value = String(settings.repetition_penalty);
      document.getElementById("presence_penalty").value = String(settings.presence_penalty);
      document.getElementById("max_tokens").value = String(settings.max_tokens);
      document.getElementById("stream").value = String(settings.stream);
      document.getElementById("generationMode").value = normalizedGenerationMode;
      document.getElementById("seed").value = String(normalizedSeed);
      document.getElementById("systemPrompt").value = settings.system_prompt;
      updateSeedFieldState(normalizedGenerationMode);

      applyTheme(settings.theme);

      document.querySelectorAll("details input, details select, details textarea").forEach((el) => {
        el.addEventListener("change", persistSettingsFromInputs);
      });
      document.getElementById("generationMode").addEventListener("change", (event) => {
        const generationMode = normalizeGenerationMode(event.target?.value);
        updateSeedFieldState(generationMode);
        persistSettingsFromInputs();
      });
    }

    function setSendEnabled() {
      const sendBtn = document.getElementById("sendBtn");
      const ready = latestStatus && latestStatus.state === "READY";
      if (requestInFlight) {
        sendBtn.disabled = false;
        sendBtn.textContent = "Stop";
        sendBtn.classList.add("stop-mode");
        return;
      }
      sendBtn.textContent = "Send";
      sendBtn.classList.remove("stop-mode");
      sendBtn.disabled = !ready;
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
      };

      setComposerActivity("Preparing prompt...");
      setComposerStatusChip(`Preparing prompt • ${Math.round(initialProgress)}%`, { phase: "prefill" });

      activePrefillProgress.timerId = window.setInterval(() => {
        const active = activePrefillProgress;
        if (!active || active.requestCtx !== requestCtx) return;
        const elapsedMs = Math.max(0, performance.now() - active.startedAtMs);
        const normalized = Math.max(0, elapsedMs / Math.max(active.etaMs, 1));
        const eased = 1 - Math.exp(-3.2 * Math.min(1.4, normalized));
        let target = PREFILL_PROGRESS_FLOOR + ((PREFILL_PROGRESS_CAP - PREFILL_PROGRESS_FLOOR) * eased);
        if (normalized > 0.75) {
          target -= Math.min(2.8, (normalized - 0.75) * 7.5);
        }
        if (elapsedMs > active.etaMs) {
          target += ((elapsedMs - active.etaMs) / 1000) * 0.22;
        }
        active.progress = Math.max(active.progress, Math.min(PREFILL_PROGRESS_CAP, target));
        const percent = Math.round(Math.min(PREFILL_PROGRESS_CAP, active.progress));
        setComposerStatusChip(`Preparing prompt • ${percent}%`, { phase: "prefill" });
      }, PREFILL_TICK_MS);
    }

    function markPrefillGenerationStarted(requestCtx) {
      const active = activePrefillProgress;
      if (!active || active.requestCtx !== requestCtx) return;
      if (active.timerId !== null) {
        window.clearInterval(active.timerId);
      }
      active.timerId = null;
      setComposerActivity("Generating...");
      setComposerStatusChip("Generating...", { phase: "generating" });
    }

    function stopPrefillProgress(options = {}) {
      const active = activePrefillProgress;
      if (active && active.timerId !== null) {
        window.clearInterval(active.timerId);
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

    function setCancelEnabled(enabled) {
      const cancelBtn = document.getElementById("cancelBtn");
      if (!cancelBtn) return;
      const show = Boolean(enabled);
      cancelBtn.hidden = !show;
      cancelBtn.disabled = !show;
    }

    function renderBubbleContent(bubble, content, options = {}) {
      if (!bubble) return;
      const text = String(content || "");
      const imageDataUrl = typeof options.imageDataUrl === "string" ? options.imageDataUrl : "";
      const imageName = typeof options.imageName === "string" ? options.imageName : "uploaded image";

      if (!imageDataUrl) {
        bubble.classList.remove("with-image");
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
      const row = document.createElement("div");
      row.className = `message-row ${role}`;

      const stack = document.createElement("div");
      stack.className = "message-stack";

      const bubble = document.createElement("div");
      bubble.className = "message-bubble";
      renderBubbleContent(bubble, content, options);

      const meta = document.createElement("div");
      meta.className = "message-meta";
      meta.hidden = true;

      stack.appendChild(bubble);
      stack.appendChild(meta);
      row.appendChild(stack);
      box.appendChild(row);
      box.scrollTop = box.scrollHeight;
      return { bubble, meta };
    }

    function updateMessage(messageView, content, options = {}) {
      const bubble = messageView?.bubble || messageView;
      if (!bubble) return;
      renderBubbleContent(bubble, content, options);
      const box = document.getElementById("messages");
      box.scrollTop = box.scrollHeight;
    }

    function setMessageMeta(messageView, content) {
      const meta = messageView?.meta;
      if (!meta) return;
      const text = String(content || "").trim();
      meta.hidden = text.length === 0;
      meta.textContent = text;
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
      const label = document.getElementById("statusLabel");
      if (!badge || !dot || !label) return;
      const backendMode = String(
        statusPayload?.backend?.active
        || statusPayload?.backend?.mode
        || ""
      ).toLowerCase();
      const modelFilename = String(statusPayload?.model?.filename || "").trim();
      const modelSuffix = modelFilename ? `:${modelFilename}` : "";
      const isReady = String(statusPayload?.state || "").toUpperCase() === "READY";
      const statusState = String(statusPayload?.state || "").toUpperCase();
      const hasModel = statusPayload?.model_present === true;
      const llamaHealthy = statusPayload?.llama_server?.healthy === true;
      const isHealthy = isLocalModelConnected(statusPayload) || (backendMode === "fake" && isReady);
      const isLoading = backendMode === "llama" && hasModel && !llamaHealthy && statusState === "BOOTING";
      const isFailed = backendMode === "llama" && statusState === "ERROR";
      badge.classList.remove("online", "loading", "failed", "offline");
      dot.classList.remove("online", "loading", "failed", "offline");
      if (backendMode === "fake" && isReady) {
        badge.classList.add("online");
        dot.classList.add("online");
        label.textContent = "CONNECTED:Fake Backend";
      } else if (isHealthy) {
        badge.classList.add("online");
        dot.classList.add("online");
        label.textContent = `CONNECTED:llama.cpp${modelSuffix}`;
      } else if (isLoading) {
        badge.classList.add("loading");
        dot.classList.add("loading");
        label.textContent = `LOADING:llama.cpp${modelSuffix}`;
      } else if (isFailed) {
        badge.classList.add("failed");
        dot.classList.add("failed");
        label.textContent = `FAILED:llama.cpp${modelSuffix}`;
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
      if (
        downloadError === "insufficient_storage"
        || (Number.isFinite(freeBytes) && freeBytes < 512 * 1024 * 1024)
      ) {
        const free = formatBytes(statusPayload?.system?.storage_free_bytes);
        hint.textContent = `Not enough free storage for this model. Free space: ${free}. Delete model files and retry.`;
      } else if (!countdownEnabled) {
        hint.textContent = "Auto-download is paused. Start manually or re-enable it in settings.";
      } else if (Number.isFinite(autoStartRemaining) && autoStartRemaining > 0) {
        hint.textContent = `Auto-download starts in ${formatCountdownSeconds(autoStartRemaining)} if idle.`;
      } else {
        hint.textContent = "Auto-download starts soon if idle.";
      }

      if (downloadStartInFlight) {
        startBtn.textContent = "Starting...";
        startBtn.disabled = true;
      } else {
        startBtn.textContent = "Start download now";
        startBtn.disabled = false;
      }
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
        toggle.textContent = runtimeDetailsExpanded ? "Show compact" : "Show details";
        toggle.setAttribute("aria-expanded", runtimeDetailsExpanded ? "true" : "false");
      }
    }

    function renderSystemRuntime(systemPayload) {
      const compact = document.getElementById("runtimeCompact");
      if (!compact) return;

      const available = systemPayload?.available === true;
      const cpuDetail = document.getElementById("runtimeDetailCpu");
      const coresDetail = document.getElementById("runtimeDetailCores");
      const cpuClockDetail = document.getElementById("runtimeDetailCpuClock");
      const memoryDetail = document.getElementById("runtimeDetailMemory");
      const swapDetail = document.getElementById("runtimeDetailSwap");
      const storageDetail = document.getElementById("runtimeDetailStorage");
      const tempDetail = document.getElementById("runtimeDetailTemp");
      const gpuDetail = document.getElementById("runtimeDetailGpu");
      const throttleDetail = document.getElementById("runtimeDetailThrottle");
      const throttleHistoryDetail = document.getElementById("runtimeDetailThrottleHistory");
      const updatedDetail = document.getElementById("runtimeDetailUpdated");

      if (!available) {
        compact.textContent = "CPU -- | Cores -- | GPU -- | Swap -- | Throttle --";
        if (cpuDetail) cpuDetail.textContent = "CPU total: --";
        if (coresDetail) coresDetail.textContent = "CPU cores: --";
        if (cpuClockDetail) cpuClockDetail.textContent = "CPU clock: --";
        if (memoryDetail) memoryDetail.textContent = "Memory: --";
        if (swapDetail) swapDetail.textContent = "Swap: --";
        if (storageDetail) storageDetail.textContent = "Storage free: --";
        if (tempDetail) tempDetail.textContent = "Temperature: --";
        if (gpuDetail) gpuDetail.textContent = "GPU clock: --";
        if (throttleDetail) throttleDetail.textContent = "Throttling: --";
        if (throttleHistoryDetail) throttleHistoryDetail.textContent = "Throttling history: --";
        if (updatedDetail) updatedDetail.textContent = "Updated: --";
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
      const swapPercent = formatPercent(systemPayload?.swap_percent, 0);
      const storageFree = formatBytes(systemPayload?.storage_free_bytes);
      const storagePercent = formatPercent(systemPayload?.storage_percent, 0);
      const throttlingNow = systemPayload?.throttling?.any_current === true ? "Yes" : "No";
      compact.textContent = `CPU ${cpuTotal} @ ${cpuClock} | Cores ${coresText} | GPU ${gpuCompact} | Swap ${swapPercent} | Free ${storageFree} | Throttle ${throttlingNow}`;

      if (cpuDetail) cpuDetail.textContent = `CPU total: ${cpuTotal}`;
      if (coresDetail) coresDetail.textContent = `CPU cores: ${coresText}`;
      if (cpuClockDetail) cpuClockDetail.textContent = `CPU clock: ${cpuClock}`;
      applyRuntimeMetricSeverity(cpuClockDetail, percentFromRatio(systemPayload?.cpu_clock_arm_hz, CPU_CLOCK_MAX_HZ_PI5));

      const memUsed = formatBytes(systemPayload?.memory_used_bytes);
      const memTotal = formatBytes(systemPayload?.memory_total_bytes);
      const memPercent = formatPercent(systemPayload?.memory_percent, 0);
      if (memoryDetail) memoryDetail.textContent = `Memory: ${memUsed} / ${memTotal} (${memPercent})`;
      applyRuntimeMetricSeverity(memoryDetail, systemPayload?.memory_percent);

      const swapUsed = formatBytes(systemPayload?.swap_used_bytes);
      const swapTotal = formatBytes(systemPayload?.swap_total_bytes);
      if (swapDetail) swapDetail.textContent = `Swap: ${swapUsed} / ${swapTotal} (${swapPercent})`;
      applyRuntimeMetricSeverity(swapDetail, systemPayload?.swap_percent);

      const storageUsed = formatBytes(systemPayload?.storage_used_bytes);
      const storageTotal = formatBytes(systemPayload?.storage_total_bytes);
      if (storageDetail) storageDetail.textContent = `Storage free: ${storageFree} (${storageUsed} / ${storageTotal} used, ${storagePercent})`;
      applyRuntimeMetricSeverity(storageDetail, systemPayload?.storage_percent);

      const tempRaw = systemPayload?.temperature_c;
      const tempValue = typeof tempRaw === "number" ? tempRaw : Number.NaN;
      if (tempDetail) {
        tempDetail.textContent = Number.isFinite(tempValue)
          ? `Temperature: ${tempValue.toFixed(1)}°C`
          : "Temperature: --";
      }
      applyRuntimeMetricSeverity(tempDetail, tempValue);

      if (gpuDetail) gpuDetail.textContent = `GPU clock: core ${gpuCore}, v3d ${gpuV3d}`;
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
          ? `Throttling: Yes (${currentFlags.join(", ")})`
          : "Throttling: No";
      }
      if (throttleHistoryDetail) {
        throttleHistoryDetail.textContent = historyFlags.length > 0
          ? `Throttling history: ${historyFlags.join(", ")}`
          : "Throttling history: None";
      }

      const updatedTs = Number(systemPayload?.updated_at_unix);
      if (updatedDetail) {
        updatedDetail.textContent = Number.isFinite(updatedTs) && updatedTs > 0
          ? `Updated: ${new Date(updatedTs * 1000).toLocaleTimeString()}`
          : "Updated: --";
      }
    }

    function setModelUploadStatus(message) {
      const el = document.getElementById("modelUploadStatus");
      if (!el) return;
      el.textContent = String(message || "No upload in progress.");
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

    function renderModelsList(statusPayload) {
      const container = document.getElementById("modelsList");
      if (!container) return;
      const models = Array.isArray(statusPayload?.models) ? statusPayload.models : [];
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

        const head = document.createElement("div");
        head.className = "model-row-head";
        const name = document.createElement("span");
        name.className = "model-row-name";
        name.textContent = String(model?.filename || "unknown.gguf");
        const status = document.createElement("span");
        status.className = "model-status-pill";
        status.textContent = String(model?.status || "unknown");
        head.appendChild(name);
        head.appendChild(status);

        const actions = document.createElement("div");
        actions.className = "model-row-actions";
        if (model?.status === "downloading") {
          const cancelBtn = document.createElement("button");
          cancelBtn.type = "button";
          cancelBtn.className = "ghost-btn";
          cancelBtn.dataset.action = "cancel-download";
          cancelBtn.textContent = "Cancel download";
          actions.appendChild(cancelBtn);
        } else if (model?.status !== "ready" && model?.source_type === "url") {
          const downloadBtn = document.createElement("button");
          downloadBtn.type = "button";
          downloadBtn.className = "ghost-btn";
          downloadBtn.dataset.action = "download";
          downloadBtn.textContent = "Download";
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
        if (String(model?.id || "").length > 0) {
          const deleteBtn = document.createElement("button");
          deleteBtn.type = "button";
          deleteBtn.className = "ghost-btn danger-btn";
          deleteBtn.dataset.action = "delete";
          deleteBtn.textContent = "Delete file";
          actions.appendChild(deleteBtn);
        }
        if (model?.is_active === true) {
          const activeLabel = document.createElement("span");
          activeLabel.className = "runtime-compact";
          activeLabel.textContent = "Active";
          actions.appendChild(activeLabel);
        }
        if (model?.status === "downloading") {
          const progress = document.createElement("span");
          progress.className = "runtime-compact";
          progress.textContent = `${Number(model?.percent || 0)}% (${formatBytes(model?.bytes_downloaded)} / ${formatBytes(model?.bytes_total)})`;
          actions.appendChild(progress);
        }
        row.appendChild(head);
        row.appendChild(actions);
        container.appendChild(row);
      }
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
        appendMessage("assistant", "Enter a model URL ending with .gguf.");
        return;
      }
      modelActionInFlight = true;
      try {
        const { res, body } = await postJson("/internal/models/register", { source_url: sourceUrl });
        if (!res.ok) {
          appendMessage("assistant", `Could not add model URL (${body?.reason || res.status}).`);
          return;
        }
        if (input) input.value = "";
      } catch (err) {
        appendMessage("assistant", `Could not add model URL: ${err}`);
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

    async function cancelActiveModelDownload() {
      if (modelActionInFlight) return;
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

    async function deleteSelectedModel(modelId) {
      if (!modelId) return;
      if (modelActionInFlight) return;
      const confirmed = window.confirm("Delete this model file and unregister it?");
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
        const res = await fetch("/internal/start-model-download", {
          method: "POST",
          headers: { "content-type": "application/json" },
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          const reason = body?.reason ? ` (${body.reason})` : "";
          appendMessage("assistant", `Could not start model download${reason}.`);
          return;
        }
        if (!body?.started && body?.reason === "already_running") {
          setComposerActivity("Model download already running.");
        } else if (!body?.started && body?.reason === "model_present") {
          setComposerActivity("Model already present.");
        } else if (!body?.started && body?.reason === "insufficient_storage") {
          setComposerActivity("Model likely too large for free storage. Delete files and retry.");
        } else if (body?.started) {
          setComposerActivity("Model download started.");
        }
      } catch (err) {
        appendMessage("assistant", `Could not start model download: ${err}`);
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
      if (!chunkText) return { deltas: [], events: [] };
      state.buffer += chunkText.replace(/\\r\\n/g, "\\n");
      const deltas = [];
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
        } catch (_err) {
          // Ignore partial/non-JSON events and continue.
        }
      }

      return { deltas, events };
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

    function formatAssistantStats(source, elapsedSeconds = 0) {
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
      return `${tokPerSecond.toFixed(2)} tok/sec, ${tokens} tokens, ${seconds.toFixed(2)}s Stop reason: ${formatStopReason(finishReason)}`;
    }

    function setStatus(statusPayload) {
      latestStatus = statusPayload;
      const downloaded = formatBytes(statusPayload.download.bytes_downloaded);
      const total = formatBytes(statusPayload.download.bytes_total);
      const text = `State: ${statusPayload.state} | Download: ${statusPayload.download.percent}% (${downloaded} / ${total})`;
      document.getElementById("statusText").textContent = text;
      const modelNameField = document.getElementById("modelName");
      if (modelNameField) {
        const modelName = statusPayload?.model?.filename || "Unknown model";
        modelNameField.value = statusPayload?.model_present ? modelName : `${modelName} (not loaded)`;
      }
      const countdownSelect = document.getElementById("downloadCountdownEnabled");
      if (countdownSelect) {
        countdownSelect.value = statusPayload?.download?.countdown_enabled === false ? "false" : "true";
      }
      updateLlamaIndicator(statusPayload);
      renderDownloadPrompt(statusPayload);
      renderSystemRuntime(statusPayload?.system);
      renderModelsList(statusPayload);
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
        const modelNameField = document.getElementById("modelName");
        if (modelNameField) {
          modelNameField.value = "Unknown model (status unavailable)";
        }
        updateLlamaIndicator(latestStatus);
        renderDownloadPrompt(latestStatus);
        renderSystemRuntime(latestStatus.system);
        renderModelsList(latestStatus);
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
      saveSettings(settings);

      appendMessage("user", userBubblePayload.text, {
        imageDataUrl: userBubblePayload.imageDataUrl,
        imageName: userBubblePayload.imageName,
      });
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
          appendMessage("assistant", `Request failed (${res.status}): ${JSON.stringify(body)}`);
          return;
        }

        if (settings.stream) {
          const assistantDiv = appendMessage("assistant", "");
          activeAssistantView = assistantDiv;
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          const state = { buffer: "" };
          let assistantText = "";

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
                markPrefillGenerationStarted(requestCtx);
              }
              assistantText += delta;
              updateMessage(assistantDiv, assistantText);
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
            markPrefillGenerationStarted(requestCtx);
          }
          updateMessage(assistantDiv, assistantText.trim() || "(empty response)");
          chatHistory.push({ role: "assistant", content: assistantText.trim() || "(empty response)" });
          const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
          if (requestCtx.stoppedByUser) {
            streamStats.finish_reason = "cancelled";
          }
          setMessageMeta(assistantDiv, formatAssistantStats(streamStats, elapsedSeconds));
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
          markPrefillGenerationStarted(requestCtx);
        }
        const msg = body.choices?.[0]?.message?.content || JSON.stringify(body);
        chatHistory.push({ role: "assistant", content: msg });
        const assistantDiv = appendMessage("assistant", msg);
        const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
        setMessageMeta(assistantDiv, formatAssistantStats(body, elapsedSeconds));
        recordPrefillMetric(
          requestCtx.prefillBucket,
          resolvePromptPrefillMs(body, requestCtx.firstTokenLatencyMs),
        );
      } catch (err) {
        if (requestCtx.stoppedByUser) {
          const elapsedSeconds = Math.max(0, (performance.now() - requestStartMs) / 1000);
          if (activeAssistantView) {
            const partial = activeAssistantView.bubble.textContent.trim();
            if (!partial) {
              updateMessage(activeAssistantView, "(stopped)");
            } else {
              chatHistory.push({ role: "assistant", content: partial });
            }
            streamStats.finish_reason = "cancelled";
            setMessageMeta(activeAssistantView, formatAssistantStats(streamStats, elapsedSeconds));
          } else {
            const stoppedDiv = appendMessage("assistant", "(stopped)");
            setMessageMeta(stoppedDiv, formatAssistantStats({ finish_reason: "cancelled" }, elapsedSeconds));
          }
        } else {
          appendMessage("assistant", `Request error: ${err}`);
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
        stopGeneration();
        queueImageCancelRecovery(current);
      }
    }

    function toggleTheme() {
      const current = document.documentElement.getAttribute("data-theme") || defaultSettings.theme;
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
      saveSettings(collectSettings());
    }

    bindSettings();
    bindMobileSidebar();
    setRuntimeDetailsExpanded(false);
    appendMessage("assistant", "Potato OS is online. Ask anything to get started.");
    setInterval(pollStatus, 2000);
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
    document.getElementById("downloadCountdownEnabled").addEventListener("change", (event) => {
      const enabled = event.target?.value !== "false";
      updateCountdownPreference(enabled);
    });
    document.getElementById("registerModelBtn").addEventListener("click", registerModelFromUrl);
    document.getElementById("uploadModelBtn").addEventListener("click", uploadLocalModel);
    document.getElementById("cancelUploadBtn").addEventListener("click", cancelLocalModelUpload);
    document.getElementById("purgeModelsBtn").addEventListener("click", purgeAllModels);
    document.getElementById("modelsList").addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const action = target.dataset?.action;
      if (!action) return;
      const row = target.closest(".model-row");
      const modelId = row?.dataset?.modelId;
      if (action === "download") {
        startModelDownloadForModel(modelId);
      } else if (action === "cancel-download") {
        cancelActiveModelDownload();
      } else if (action === "activate") {
        activateSelectedModel(modelId);
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
    app = FastAPI(title="Potato Web", version="0.2")
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
    app.state.startup_monotonic = None
    app.state.orchestrator_task = None
    app.state.chat_repository = ChatRepositoryManager(
        llama=LlamaCppRepository(app.state.runtime.llama_base_url),
        fake=FakeLlamaRepository(),
    )

    if enable_orchestrator is not None:
        app.state.runtime.enable_orchestrator = enable_orchestrator

    @app.on_event("startup")
    async def _startup() -> None:
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

    @app.on_event("shutdown")
    async def _shutdown() -> None:
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
        state = set_download_countdown_enabled(runtime_cfg, enabled)
        return JSONResponse(
            status_code=200,
            content={
                "updated": True,
                "countdown_enabled": bool(state.get("countdown_enabled", True)),
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
        return JSONResponse(status_code=200, content={"ok": True, "reason": reason, "model": model})

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
                    "restarted": restarted,
                    "restart_reason": restart_reason,
                },
            )
        status_code = 404 if reason == "model_not_found" else 400
        return JSONResponse(
            status_code=status_code,
            content={"deleted": False, "reason": reason, "model_id": model_id, "deleted_file": False, "freed_bytes": 0},
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

            app.state.model_upload_cancel_requested = False
            app.state.model_upload_state = {
                "active": True,
                "model_id": None,
                "bytes_total": _safe_int(request.headers.get("content-length"), 0),
                "bytes_received": 0,
                "percent": 0,
                "error": None,
            }

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
                        if total_received > MAX_MODEL_UPLOAD_BYTES:
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
                return JSONResponse(
                    status_code=200,
                    content={"uploaded": True, "model": model, "switched": True, "restarted": restarted},
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

        payload = _merge_defaults(payload)
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
