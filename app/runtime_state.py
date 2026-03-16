from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

logger = logging.getLogger("potato")

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover - optional on non-Pi dev hosts
    psutil = None  # type: ignore[assignment]

MODEL_UPLOAD_LIMIT_8GB_BYTES = 8 * 1024 * 1024 * 1024
MODEL_UPLOAD_LIMIT_16GB_BYTES = 16 * 1024 * 1024 * 1024
MODEL_UPLOAD_PI_16GB_MEMORY_THRESHOLD_BYTES = 12 * 1024 * 1024 * 1024
LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT = 5 * 1024 * 1024 * 1024
LLAMA_RUNTIME_BUNDLE_MARKER_FILENAME = ".potato-llama-runtime-bundle.json"
POWER_CALIBRATION_DEFAULT_A = 1.260204
POWER_CALIBRATION_DEFAULT_B = 0.704251
POWER_CALIBRATION_MAX_SAMPLES = 64
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
SYSTEM_STATIC_INFO_CACHE_TTL_SECONDS = 60
SYSTEM_POWER_ESTIMATE_DISCLAIMER = (
    "Estimated from PMIC rails; excludes main 5V input current/peripherals/HATs and conversion losses."
)

_SYSTEM_STATIC_INFO_CACHE: dict[str, Any] = {
    "expires_at_unix": 0,
    "value": None,
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
    llama_runtime_settings_path: Path | None = None
    runtime_reset_service: str = "potato-runtime-reset.service"

    def __post_init__(self) -> None:
        if self.ensure_model_script is None:
            self.ensure_model_script = self.base_dir / "bin" / "ensure_model.sh"
        if self.start_llama_script is None:
            self.start_llama_script = self.base_dir / "bin" / "start_llama.sh"
        if self.llama_runtime_settings_path is None:
            self.llama_runtime_settings_path = self.base_dir / "state" / "llama_runtime.json"

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
        model_path = Path(os.getenv("POTATO_MODEL_PATH", str(base_dir / "models" / "Qwen3-VL-4B-Instruct-Q4_K_M.gguf")))
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
        llama_runtime_settings_path = Path(
            os.getenv("POTATO_LLAMA_RUNTIME_SETTINGS_PATH", str(base_dir / "state" / "llama_runtime.json"))
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
            llama_runtime_settings_path=llama_runtime_settings_path,
            runtime_reset_service=runtime_reset_service,
            enable_orchestrator=enable_orchestrator,
            auto_download_idle_seconds=auto_download_idle_seconds,
            allow_fake_fallback=allow_fake_fallback,
        )


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


def _detect_total_memory_bytes() -> int | None:
    if psutil is None:
        return None
    try:
        memory = psutil.virtual_memory()
    except Exception:
        return None
    total = _safe_int(getattr(memory, "total", None), default=0)
    return total if total > 0 else None


def get_model_upload_max_bytes() -> int | None:
    raw_override = os.getenv("POTATO_MODEL_UPLOAD_MAX_BYTES", "").strip()
    if raw_override:
        lowered = raw_override.lower()
        if lowered in {"0", "none", "no-limit", "nolimit", "unlimited"}:
            return None
        parsed = _safe_int(raw_override, default=-1)
        if parsed > 0:
            return parsed
        logger.warning("Invalid POTATO_MODEL_UPLOAD_MAX_BYTES=%r; falling back to auto limit", raw_override)

    total_memory_bytes = _detect_total_memory_bytes()
    if total_memory_bytes is None:
        return MODEL_UPLOAD_LIMIT_16GB_BYTES
    if total_memory_bytes >= MODEL_UPLOAD_PI_16GB_MEMORY_THRESHOLD_BYTES:
        return MODEL_UPLOAD_LIMIT_16GB_BYTES
    return MODEL_UPLOAD_LIMIT_8GB_BYTES


def _read_pi_device_model_name() -> str | None:
    path = Path("/proc/device-tree/model")
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    text = raw.replace(b"\x00", b"").decode("utf-8", errors="replace").strip()
    return text or None


def classify_runtime_device(
    *,
    total_memory_bytes: int | None = None,
    pi_model_name: str | None = None,
) -> str:
    model_name = (pi_model_name or "").strip().lower()
    if not model_name:
        return "unknown"
    if "raspberry pi" not in model_name:
        return "unknown"
    if "raspberry pi 5" in model_name:
        total = total_memory_bytes if total_memory_bytes is not None else _detect_total_memory_bytes()
        if total is not None and total >= MODEL_UPLOAD_PI_16GB_MEMORY_THRESHOLD_BYTES:
            return "pi5-16gb"
        return "pi5-8gb"
    return "other-pi"


def get_large_model_warn_threshold_bytes() -> int:
    raw = os.getenv("POTATO_UNSUPPORTED_PI_LARGE_MODEL_WARN_BYTES", "").strip()
    if not raw:
        return LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT
    parsed = _safe_int(raw, default=-1)
    if parsed > 0:
        return parsed
    logger.warning(
        "Invalid POTATO_UNSUPPORTED_PI_LARGE_MODEL_WARN_BYTES=%r; using default %d",
        raw,
        LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT,
    )
    return LARGE_MODEL_UNSUPPORTED_PI_WARN_BYTES_DEFAULT


def normalize_llama_memory_loading_mode(raw_mode: Any) -> str:
    value = str(raw_mode or "").strip().lower()
    if value in {"full_ram", "no_mmap", "no-mmap", "1", "true", "on"}:
        return "full_ram"
    if value in {"mmap", "mapped", "0", "false", "off"}:
        return "mmap"
    return "auto"


def llama_memory_loading_no_mmap_env(mode: str) -> str:
    normalized = normalize_llama_memory_loading_mode(mode)
    if normalized == "full_ram":
        return "1"
    if normalized == "mmap":
        return "0"
    return "auto"


def normalize_allow_unsupported_large_models(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return False
    value = str(raw_value).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _safe_positive_float(raw_value: Any) -> float | None:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _get_power_calibration_default_coefficients() -> tuple[float, float]:
    raw_a = os.getenv("POTATO_POWER_ESTIMATE_ADJUST_A", "").strip()
    raw_b = os.getenv("POTATO_POWER_ESTIMATE_ADJUST_B", "").strip()
    a = _safe_positive_float(raw_a) if raw_a else None
    b = None
    if raw_b:
        try:
            parsed_b = float(raw_b)
            if math.isfinite(parsed_b):
                b = parsed_b
        except (TypeError, ValueError):
            b = None
    return (
        a if a is not None else POWER_CALIBRATION_DEFAULT_A,
        b if b is not None else POWER_CALIBRATION_DEFAULT_B,
    )


def _default_power_calibration_settings() -> dict[str, Any]:
    default_a, default_b = _get_power_calibration_default_coefficients()
    return {
        "mode": "default",
        "a": default_a,
        "b": default_b,
        "fitted_at_unix": None,
        "sample_count": 0,
        "samples": [],
    }


def _normalize_power_calibration_samples(raw_samples: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_samples, list):
        return []
    samples: list[dict[str, Any]] = []
    for item in raw_samples:
        if not isinstance(item, dict):
            continue
        raw_pmic_watts = _safe_positive_float(item.get("raw_pmic_watts"))
        wall_watts = _safe_positive_float(item.get("wall_watts"))
        if raw_pmic_watts is None or wall_watts is None:
            continue
        samples.append(
            {
                "raw_pmic_watts": round(raw_pmic_watts, 4),
                "wall_watts": round(wall_watts, 4),
                "captured_at_unix": _safe_int(item.get("captured_at_unix"), 0) or None,
            }
        )
    if len(samples) > POWER_CALIBRATION_MAX_SAMPLES:
        samples = samples[-POWER_CALIBRATION_MAX_SAMPLES:]
    return samples


def normalize_power_calibration_settings(raw_value: Any) -> dict[str, Any]:
    defaults = _default_power_calibration_settings()
    raw = raw_value if isinstance(raw_value, dict) else {}
    mode_raw = str(raw.get("mode") or "").strip().lower()
    mode = "custom" if mode_raw == "custom" else "default"
    a = raw.get("a")
    b = raw.get("b")
    try:
        a_value = float(a)
    except (TypeError, ValueError):
        a_value = defaults["a"]
    try:
        b_value = float(b)
    except (TypeError, ValueError):
        b_value = defaults["b"]
    if not math.isfinite(a_value) or a_value <= 0:
        a_value = defaults["a"]
    if not math.isfinite(b_value):
        b_value = defaults["b"]
    samples = _normalize_power_calibration_samples(raw.get("samples"))
    sample_count = _safe_int(raw.get("sample_count"), len(samples))
    if sample_count < len(samples):
        sample_count = len(samples)
    fitted_at_unix = _safe_int(raw.get("fitted_at_unix"), 0) or None
    if mode != "custom":
        fitted_at_unix = None
    return {
        "mode": mode,
        "a": round(float(a_value), 6),
        "b": round(float(b_value), 6),
        "fitted_at_unix": fitted_at_unix,
        "sample_count": max(0, int(sample_count)),
        "samples": samples,
    }


def _fit_linear_power_calibration(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(samples) < 2:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for item in samples:
        raw = _safe_positive_float(item.get("raw_pmic_watts"))
        wall = _safe_positive_float(item.get("wall_watts"))
        if raw is None or wall is None:
            continue
        xs.append(raw)
        ys.append(wall)
    if len(xs) < 2:
        return None
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    var_x = sum((x - x_mean) ** 2 for x in xs)
    if var_x <= 0:
        return None
    cov_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    a = cov_xy / var_x
    b = y_mean - (a * x_mean)
    if not math.isfinite(a) or not math.isfinite(b) or a <= 0:
        return None
    return {
        "a": round(float(a), 6),
        "b": round(float(b), 6),
        "sample_count": len(xs),
    }


def _apply_power_calibration(raw_pmic_watts: Any, *, a: Any, b: Any) -> float | None:
    raw = _safe_positive_float(raw_pmic_watts)
    if raw is None:
        return None
    try:
        a_val = float(a)
        b_val = float(b)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(a_val) or not math.isfinite(b_val):
        return None
    adjusted = (raw * a_val) + b_val
    if not math.isfinite(adjusted) or adjusted <= 0:
        return None
    return adjusted


def _llama_runtime_settings_path(runtime: RuntimeConfig) -> Path:
    if runtime.llama_runtime_settings_path is not None:
        return runtime.llama_runtime_settings_path
    return runtime.base_dir / "state" / "llama_runtime.json"


def read_llama_runtime_settings(runtime: RuntimeConfig) -> dict[str, Any]:
    path = _llama_runtime_settings_path(runtime)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    mode = normalize_llama_memory_loading_mode(raw.get("memory_loading_mode"))
    return {
        "memory_loading_mode": mode,
        "allow_unsupported_large_models": normalize_allow_unsupported_large_models(
            raw.get("allow_unsupported_large_models")
        ),
        "power_calibration": normalize_power_calibration_settings(raw.get("power_calibration")),
        "updated_at_unix": _safe_int(raw.get("updated_at_unix"), 0) or None,
    }


def write_llama_runtime_settings(
    runtime: RuntimeConfig,
    *,
    memory_loading_mode: str | None = None,
    allow_unsupported_large_models: bool | None = None,
    power_calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = read_llama_runtime_settings(runtime)
    payload = {
        "memory_loading_mode": normalize_llama_memory_loading_mode(
            current.get("memory_loading_mode") if memory_loading_mode is None else memory_loading_mode
        ),
        "allow_unsupported_large_models": normalize_allow_unsupported_large_models(
            current.get("allow_unsupported_large_models")
            if allow_unsupported_large_models is None
            else allow_unsupported_large_models
        ),
        "power_calibration": normalize_power_calibration_settings(
            current.get("power_calibration") if power_calibration is None else power_calibration
        ),
        "updated_at_unix": int(time.time()),
    }
    _atomic_write_json(_llama_runtime_settings_path(runtime), payload)
    return payload


def build_llama_memory_loading_status(runtime: RuntimeConfig) -> dict[str, Any]:
    settings = read_llama_runtime_settings(runtime)
    mode = normalize_llama_memory_loading_mode(settings.get("memory_loading_mode"))
    no_mmap_env = llama_memory_loading_no_mmap_env(mode)
    return {
        "mode": mode,
        "no_mmap_env": no_mmap_env,
        "label": (
            "Full RAM load (--no-mmap)"
            if mode == "full_ram"
            else "Memory-mapped (mmap)"
            if mode == "mmap"
            else "Automatic (profile-based)"
        ),
        "updated_at_unix": settings.get("updated_at_unix"),
    }


def build_llama_large_model_override_status(runtime: RuntimeConfig) -> dict[str, Any]:
    settings = read_llama_runtime_settings(runtime)
    enabled = normalize_allow_unsupported_large_models(settings.get("allow_unsupported_large_models"))
    return {
        "enabled": enabled,
        "label": "Try unsupported large model anyway" if enabled else "Use compatibility warnings (default)",
        "updated_at_unix": settings.get("updated_at_unix"),
    }


def build_power_calibration_status(runtime: RuntimeConfig) -> dict[str, Any]:
    settings = read_llama_runtime_settings(runtime)
    calibration = normalize_power_calibration_settings(settings.get("power_calibration"))
    mode = str(calibration.get("mode") or "default")
    return {
        "mode": "custom" if mode == "custom" else "default",
        "a": calibration.get("a"),
        "b": calibration.get("b"),
        "sample_count": _safe_int(calibration.get("sample_count"), 0),
        "fitted_at_unix": calibration.get("fitted_at_unix"),
        "label": "Meter-calibrated" if mode == "custom" else "Default correction",
    }


def _append_power_calibration_sample(
    runtime: RuntimeConfig,
    *,
    raw_pmic_watts: float,
    wall_watts: float,
    captured_at_unix: int | None = None,
) -> dict[str, Any]:
    current = read_llama_runtime_settings(runtime)
    calibration = normalize_power_calibration_settings(current.get("power_calibration"))
    samples = list(calibration.get("samples") or [])
    samples.append(
        {
            "raw_pmic_watts": round(float(raw_pmic_watts), 4),
            "wall_watts": round(float(wall_watts), 4),
            "captured_at_unix": int(time.time()) if captured_at_unix is None else int(captured_at_unix),
        }
    )
    samples = _normalize_power_calibration_samples(samples)
    calibration["samples"] = samples
    calibration["sample_count"] = len(samples)
    saved = write_llama_runtime_settings(runtime, power_calibration=calibration)
    return normalize_power_calibration_settings(saved.get("power_calibration"))


def _fit_and_persist_power_calibration(runtime: RuntimeConfig) -> tuple[bool, str, dict[str, Any]]:
    current = read_llama_runtime_settings(runtime)
    calibration = normalize_power_calibration_settings(current.get("power_calibration"))
    samples = list(calibration.get("samples") or [])
    if len(samples) < 2:
        return False, "insufficient_samples", calibration
    fit = _fit_linear_power_calibration(samples)
    if fit is None:
        return False, "degenerate_samples", calibration
    calibration.update(
        {
            "mode": "custom",
            "a": fit["a"],
            "b": fit["b"],
            "sample_count": fit["sample_count"],
            "fitted_at_unix": int(time.time()),
            "samples": _normalize_power_calibration_samples(samples),
        }
    )
    saved = write_llama_runtime_settings(runtime, power_calibration=calibration)
    return True, "power_calibration_fitted", normalize_power_calibration_settings(saved.get("power_calibration"))


def _reset_power_calibration(runtime: RuntimeConfig) -> dict[str, Any]:
    calibration = _default_power_calibration_settings()
    saved = write_llama_runtime_settings(runtime, power_calibration=calibration)
    return normalize_power_calibration_settings(saved.get("power_calibration"))


def _default_llama_runtime_bundle_roots(runtime: RuntimeConfig) -> list[Path]:
    return [
        runtime.base_dir / "llama-bundles",
        Path("/tmp/potato-qwen35-ab/references/old_reference_design/llama_cpp_binary"),
        Path("/tmp/potato-os/references/old_reference_design/llama_cpp_binary"),
    ]


def get_llama_runtime_bundle_roots(runtime: RuntimeConfig) -> list[Path]:
    raw = os.getenv("POTATO_LLAMA_RUNTIME_BUNDLE_ROOTS", "").strip()
    candidates: list[Path]
    if raw:
        candidates = [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]
    else:
        candidates = _default_llama_runtime_bundle_roots(runtime)

    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        roots.append(candidate)
    return roots


def _llama_runtime_bundle_profile_from_name(bundle_name: str) -> str | None:
    lowered = bundle_name.lower()
    if lowered.endswith("_pi5-opt"):
        return "pi5-opt"
    if lowered.endswith("_baseline"):
        return "baseline"
    return None


def _llama_runtime_bundle_readme_fields(bundle_dir: Path) -> dict[str, str]:
    readme = bundle_dir / "README.txt"
    try:
        text = readme.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    fields: dict[str, str] = {}
    version_lines: list[str] = []
    in_version = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if in_version and version_lines:
                break
            continue
        if line.lower().startswith("profile:"):
            fields["profile"] = line.split(":", 1)[1].strip()
            continue
        if line.lower().startswith("llama.cpp commit:"):
            fields["llama_cpp_commit"] = line.split(":", 1)[1].strip()
            continue
        if line.lower() == "version:":
            in_version = True
            continue
        if in_version and not line.lower().startswith("contents:"):
            version_lines.append(line)
            continue
        if in_version and line.lower().startswith("contents:"):
            break
    if version_lines:
        fields["version_summary"] = version_lines[0]
    return fields


def discover_llama_runtime_bundles(runtime: RuntimeConfig) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    for root in get_llama_runtime_bundle_roots(runtime):
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for bundle_dir in children:
            name = bundle_dir.name
            if not bundle_dir.is_dir() or not name.startswith("llama_server_bundle_"):
                continue
            server_path = bundle_dir / "bin" / "llama-server"
            if not server_path.exists():
                continue
            readme_fields = _llama_runtime_bundle_readme_fields(bundle_dir)
            profile = (
                str(readme_fields.get("profile") or "").strip()
                or _llama_runtime_bundle_profile_from_name(name)
                or "unknown"
            )
            try:
                mtime_unix = int(bundle_dir.stat().st_mtime)
            except OSError:
                mtime_unix = 0
            bundles.append(
                {
                    "path": str(bundle_dir),
                    "name": name,
                    "root": str(root),
                    "profile": profile,
                    "is_pi5_optimized": profile == "pi5-opt",
                    "has_bench": (bundle_dir / "bin" / "llama-bench").exists(),
                    "has_lib_dir": (bundle_dir / "lib").is_dir(),
                    "version_summary": readme_fields.get("version_summary"),
                    "llama_cpp_commit": readme_fields.get("llama_cpp_commit"),
                    "mtime_unix": mtime_unix,
                }
            )
    bundles.sort(key=lambda item: (int(item.get("mtime_unix") or 0), str(item.get("name") or "")), reverse=True)
    return bundles


def _llama_runtime_install_dir(runtime: RuntimeConfig) -> Path:
    return runtime.base_dir / "llama"


def _llama_runtime_marker_path(runtime: RuntimeConfig) -> Path:
    return _llama_runtime_install_dir(runtime) / LLAMA_RUNTIME_BUNDLE_MARKER_FILENAME


def read_llama_runtime_bundle_marker(runtime: RuntimeConfig) -> dict[str, Any] | None:
    marker_path = _llama_runtime_marker_path(runtime)
    try:
        raw = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def write_llama_runtime_bundle_marker(runtime: RuntimeConfig, bundle: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "family": str(bundle.get("family") or ""),
        "source_bundle_path": str(bundle.get("path") or ""),
        "source_bundle_name": str(bundle.get("name") or bundle.get("family") or ""),
        "profile": str(bundle.get("profile") or "unknown"),
        "version_summary": bundle.get("version_summary") or bundle.get("version"),
        "llama_cpp_commit": bundle.get("llama_cpp_commit") or bundle.get("commit"),
        "switched_at_unix": int(time.time()),
    }
    _atomic_write_json(_llama_runtime_marker_path(runtime), payload)
    return payload


def build_large_model_compatibility(
    runtime: RuntimeConfig,
    *,
    model_filename: str | None = None,
    model_size_bytes: int | None = None,
    allow_override: bool | None = None,
) -> dict[str, Any]:
    total_memory_bytes = _detect_total_memory_bytes()
    pi_model_name = _read_pi_device_model_name()
    device_class = classify_runtime_device(
        total_memory_bytes=total_memory_bytes,
        pi_model_name=pi_model_name,
    )
    threshold_bytes = get_large_model_warn_threshold_bytes()
    override_enabled = (
        normalize_allow_unsupported_large_models(allow_override)
        if allow_override is not None
        else normalize_allow_unsupported_large_models(
            read_llama_runtime_settings(runtime).get("allow_unsupported_large_models")
        )
    )
    size_bytes = int(model_size_bytes) if isinstance(model_size_bytes, int) else _safe_int(model_size_bytes, 0)
    if size_bytes <= 0:
        size_bytes = 0

    warnings: list[dict[str, Any]] = []
    if size_bytes > threshold_bytes and device_class != "pi5-16gb" and not override_enabled:
        filename = str(model_filename or runtime.model_path.name or "model.gguf")
        warnings.append(
            {
                "code": "large_model_unsupported_pi_warning",
                "severity": "warning",
                "message": (
                    f"{filename} is larger than the unsupported-device warning threshold "
                    f"({threshold_bytes} bytes). Qwen3.5-35B-A3B is validated on Raspberry Pi 5 16GB only."
                ),
                "model_filename": filename,
                "model_size_bytes": size_bytes,
            }
        )

    return {
        "device_class": device_class,
        "pi_model_name": pi_model_name,
        "memory_total_bytes": total_memory_bytes or 0,
        "large_model_warn_threshold_bytes": threshold_bytes,
        "supported_target": "raspberry-pi-5-16gb",
        "override_enabled": override_enabled,
        "warnings": warnings,
    }


SUPPORTED_RUNTIME_FAMILIES = ("ik_llama", "llama_cpp")


def _runtime_slots_dir(runtime: RuntimeConfig) -> Path:
    return runtime.base_dir / "runtimes"


def discover_runtime_slots(runtime: RuntimeConfig) -> list[dict[str, Any]]:
    """Discover the two explicit runtime slots (ik_llama, llama_cpp)."""
    slots: list[dict[str, Any]] = []
    slots_dir = _runtime_slots_dir(runtime)
    for family in SUPPORTED_RUNTIME_FAMILIES:
        slot_dir = slots_dir / family
        server_bin = slot_dir / "bin" / "llama-server"
        if not slot_dir.is_dir() or not server_bin.exists():
            continue
        metadata: dict[str, Any] = {"family": family, "path": str(slot_dir)}
        runtime_json = slot_dir / "runtime.json"
        if runtime_json.exists():
            try:
                meta = json.loads(runtime_json.read_text(encoding="utf-8"))
                if isinstance(meta, dict):
                    metadata.update(meta)
            except (OSError, json.JSONDecodeError):
                pass
        metadata.setdefault("commit", "unknown")
        metadata.setdefault("profile", "unknown")
        metadata.setdefault("repo", "")
        metadata.setdefault("build_timestamp", "")
        metadata.setdefault("version", "")
        slots.append(metadata)
    return slots


def find_runtime_slot_by_family(runtime: RuntimeConfig, family: str) -> dict[str, Any] | None:
    """Find a runtime slot by family name."""
    for slot in discover_runtime_slots(runtime):
        if slot.get("family") == family:
            return slot
    return None


def build_llama_runtime_status(runtime: RuntimeConfig, app: FastAPI | None = None) -> dict[str, Any]:
    install_dir = _llama_runtime_install_dir(runtime)
    marker = read_llama_runtime_bundle_marker(runtime) or {}
    available_runtimes = discover_runtime_slots(runtime)

    # Mark which slot is currently active based on the marker
    current_family = str(marker.get("family") or marker.get("source_bundle_name") or "").strip()
    for slot in available_runtimes:
        slot["is_active"] = slot.get("family") == current_family

    switch_snapshot = {
        "active": False,
        "target_family": None,
        "started_at_unix": None,
        "completed_at_unix": None,
        "error": None,
    }
    if app is not None:
        raw = getattr(app.state, "llama_runtime_switch_state", None)
        if isinstance(raw, dict):
            switch_snapshot.update(
                {
                    "active": bool(raw.get("active", False)),
                    "target_family": raw.get("target_family"),
                    "started_at_unix": raw.get("started_at_unix"),
                    "completed_at_unix": raw.get("completed_at_unix"),
                    "error": raw.get("error"),
                }
            )

    current = {
        "install_dir": str(install_dir),
        "exists": install_dir.exists(),
        "has_server_binary": (install_dir / "bin" / "llama-server").exists(),
        "family": marker.get("family"),
        "source_bundle_path": marker.get("source_bundle_path"),
        "source_bundle_name": marker.get("source_bundle_name"),
        "profile": marker.get("profile"),
        "version_summary": marker.get("version_summary"),
        "llama_cpp_commit": marker.get("llama_cpp_commit"),
        "switched_at_unix": marker.get("switched_at_unix"),
    }

    return {
        "current": current,
        "available_runtimes": available_runtimes,
        "switch": switch_snapshot,
        "memory_loading": build_llama_memory_loading_status(runtime),
        "large_model_override": build_llama_large_model_override_status(runtime),
    }


async def install_llama_runtime_bundle(runtime: RuntimeConfig, bundle_dir: Path) -> dict[str, Any]:
    install_dir = _llama_runtime_install_dir(runtime)
    install_dir.mkdir(parents=True, exist_ok=True)

    rsync = shutil.which("rsync")
    if not rsync:
        return {"ok": False, "reason": "rsync_not_available", "install_dir": str(install_dir)}

    proc = await asyncio.create_subprocess_exec(
        rsync,
        "-a",
        "--delete",
        f"{bundle_dir}/",
        f"{install_dir}/",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _stderr = await proc.communicate()
    if stdout:
        logger.info("llama runtime rsync: %s", stdout.decode("utf-8", errors="replace").rstrip())
    if proc.returncode != 0:
        return {
            "ok": False,
            "reason": "rsync_failed",
            "returncode": proc.returncode,
            "install_dir": str(install_dir),
        }

    for rel in ("bin/llama-server", "run-llama-server.sh", "run-llama-bench.sh"):
        path = install_dir / rel
        try:
            if path.exists():
                path.chmod(path.stat().st_mode | 0o111)
        except OSError:
            logger.warning("Could not chmod runtime bundle file: %s", path, exc_info=True)

    return {"ok": True, "reason": "installed", "install_dir": str(install_dir)}


def find_llama_runtime_bundle_by_path(runtime: RuntimeConfig, bundle_path: str) -> dict[str, Any] | None:
    candidate = str(bundle_path or "").strip()
    if not candidate:
        return None
    try:
        resolved = str(Path(candidate).resolve())
    except OSError:
        return None
    for bundle in discover_llama_runtime_bundles(runtime):
        try:
            bundle_resolved = str(Path(str(bundle.get("path") or "")).resolve())
        except OSError:
            continue
        if bundle_resolved == resolved:
            return bundle
    return None


def default_system_metrics_snapshot() -> dict[str, Any]:
    return {
        "available": False,
        "updated_at_unix": None,
        "pi_model_name": None,
        "os_pretty_name": None,
        "kernel_release": None,
        "kernel_version": None,
        "bootloader_version": _default_bootloader_version_snapshot(),
        "firmware_version": _default_firmware_version_snapshot(),
        "power_estimate": _default_power_estimate_snapshot(),
        "cpu_percent": None,
        "cpu_cores_percent": [],
        "cpu_clock_arm_hz": None,
        "memory_total_bytes": 0,
        "memory_used_bytes": 0,
        "memory_percent": None,
        "swap_label": "swap",
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
    current_flags = [label for bit, label in THROTTLE_FLAG_BITS.items() if raw_value & (1 << bit)]
    history_flags = [label for bit, label in THROTTLE_HISTORY_BITS.items() if raw_value & (1 << bit)]
    return {
        "raw": f"0x{raw_value:x}",
        "any_current": len(current_flags) > 0,
        "any_history": len(history_flags) > 0,
        "current_flags": current_flags,
        "history_flags": history_flags,
    }


def _default_bootloader_version_snapshot() -> dict[str, Any]:
    return {
        "available": False,
        "date": None,
        "version": None,
        "timestamp": None,
        "update_time": None,
        "capabilities": None,
        "raw": None,
    }


def _default_firmware_version_snapshot() -> dict[str, Any]:
    return {
        "available": False,
        "date": None,
        "build_info": None,
        "version": None,
        "raw": None,
    }


def _default_power_estimate_snapshot() -> dict[str, Any]:
    return {
        "available": False,
        "updated_at_unix": None,
        "total_watts": None,
        "rails_paired_count": 0,
        "method": "pmic_read_adc",
        "label": "PMIC rails estimate",
        "disclaimer": SYSTEM_POWER_ESTIMATE_DISCLAIMER,
        "error": None,
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


def _parse_vcgencmd_bootloader_version(raw_text: str | None) -> dict[str, Any]:
    payload = _default_bootloader_version_snapshot()
    if not raw_text:
        return payload
    lines = [line.strip() for line in str(raw_text).splitlines() if line.strip()]
    if not lines:
        return payload
    payload["raw"] = str(raw_text)
    payload["date"] = lines[0]
    for line in lines[1:]:
        if " " not in line:
            continue
        key, value = line.split(" ", 1)
        value = value.strip() or None
        if key == "version":
            payload["version"] = value
        elif key == "timestamp":
            parsed = _safe_int(value, default=-1) if value is not None else -1
            if parsed >= 0:
                payload["timestamp"] = parsed
        elif key in {"update-time", "update_time"}:
            parsed = _safe_int(value, default=-1) if value is not None else -1
            if parsed >= 0:
                payload["update_time"] = parsed
        elif key == "capabilities":
            payload["capabilities"] = value
    payload["available"] = bool(payload["date"] or payload["version"])
    return payload


def _parse_vcgencmd_firmware_version(raw_text: str | None) -> dict[str, Any]:
    payload = _default_firmware_version_snapshot()
    if not raw_text:
        return payload
    lines = [line.strip() for line in str(raw_text).splitlines() if line.strip()]
    if not lines:
        return payload
    payload["raw"] = str(raw_text)
    payload["date"] = lines[0]
    if len(lines) >= 3:
        payload["build_info"] = " | ".join(lines[1:-1]) or None
        last_line = lines[-1]
        payload["version"] = last_line.replace("version ", "", 1) if last_line.startswith("version ") else last_line
    elif len(lines) == 2:
        payload["build_info"] = lines[1]
    payload["available"] = bool(payload["date"] or payload["version"])
    return payload


def _parse_vcgencmd_pmic_read_adc(raw_text: str | None) -> dict[str, Any]:
    payload = _default_power_estimate_snapshot()
    if not raw_text:
        payload["error"] = "vcgencmd_unavailable"
        return payload

    voltages: dict[str, float] = {}
    currents: dict[str, float] = {}
    for line in str(raw_text).splitlines():
        text = line.strip()
        if not text:
            continue
        match = re.match(r"^([A-Za-z0-9_]+)\s+[^=]+=([0-9]+(?:\.[0-9]+)?)([AV])$", text)
        if not match:
            continue
        label = match.group(1)
        value = _safe_float(match.group(2), default=float("nan"))
        unit = match.group(3)
        if not math.isfinite(value):
            continue
        if unit == "V" and label.endswith("_V"):
            voltages[label[:-2]] = value
        elif unit == "A" and label.endswith("_A"):
            currents[label[:-2]] = value

    paired_keys = sorted(set(voltages.keys()) & set(currents.keys()))
    if not paired_keys:
        payload["error"] = "no_paired_rails"
        return payload

    total_watts = sum(voltages[key] * currents[key] for key in paired_keys)
    payload["available"] = True
    payload["rails_paired_count"] = len(paired_keys)
    payload["total_watts"] = total_watts
    payload["error"] = None
    return payload


def _read_os_release_pretty_name() -> str | None:
    path = Path("/etc/os-release")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in raw.splitlines():
        text = line.strip()
        if not text.startswith("PRETTY_NAME="):
            continue
        value = text.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value or None
    return None


def _read_kernel_version_info() -> dict[str, str | None]:
    try:
        uname = os.uname()
        raw_release = getattr(uname, "release", "")
        raw_version = getattr(uname, "version", "")
        kernel_release = str(raw_release).strip() or None
        kernel_version = str(raw_version).strip() or None
    except Exception:
        kernel_release = None
        kernel_version = None
    if kernel_release is None:
        try:
            kernel_release = platform.release() or None
        except Exception:
            kernel_release = None
    if kernel_version is None:
        try:
            kernel_version = platform.version() or None
        except Exception:
            kernel_version = None
    return {
        "kernel_release": kernel_release,
        "kernel_version": kernel_version,
    }


def _read_swap_label() -> str:
    path = Path("/proc/swaps")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return "swap"

    for line in raw.splitlines()[1:]:
        text = line.strip()
        if not text:
            continue
        filename = text.split()[0]
        if "zram" in filename.lower():
            return "zram"
    return "swap"


def _collect_static_platform_info_uncached() -> dict[str, Any]:
    kernel_info = _read_kernel_version_info()
    return {
        "pi_model_name": _read_pi_device_model_name(),
        "os_pretty_name": _read_os_release_pretty_name(),
        "kernel_release": kernel_info.get("kernel_release"),
        "kernel_version": kernel_info.get("kernel_version"),
        "bootloader_version": _parse_vcgencmd_bootloader_version(_run_vcgencmd("bootloader_version")),
        "firmware_version": _parse_vcgencmd_firmware_version(_run_vcgencmd("version")),
    }


def _collect_static_platform_info_cached(*, now_unix: int | None = None) -> dict[str, Any]:
    now = int(time.time()) if now_unix is None else int(now_unix)
    cached_value = _SYSTEM_STATIC_INFO_CACHE.get("value")
    cached_expires = _safe_int(_SYSTEM_STATIC_INFO_CACHE.get("expires_at_unix"), default=0)
    if isinstance(cached_value, dict) and now < cached_expires:
        return dict(cached_value)

    value = _collect_static_platform_info_uncached()
    _SYSTEM_STATIC_INFO_CACHE["value"] = dict(value)
    _SYSTEM_STATIC_INFO_CACHE["expires_at_unix"] = now + SYSTEM_STATIC_INFO_CACHE_TTL_SECONDS
    return value


def _build_power_estimate_snapshot(*, now_unix: int | None = None) -> dict[str, Any]:
    now = int(time.time()) if now_unix is None else int(now_unix)
    payload = _parse_vcgencmd_pmic_read_adc(_run_vcgencmd("pmic_read_adc"))
    payload["updated_at_unix"] = now
    if isinstance(payload.get("total_watts"), (int, float)):
        payload["total_watts"] = round(float(payload["total_watts"]), 3)
    return payload


def _parse_vcgencmd_temp(raw_text: str | None) -> float | None:
    if not raw_text:
        return None
    match = re.search(r"temp=([0-9]+(?:\.[0-9]+)?)'C", raw_text)
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


def build_power_estimate_status(runtime: RuntimeConfig, power_snapshot: Any) -> dict[str, Any]:
    base = _default_power_estimate_snapshot()
    if isinstance(power_snapshot, dict):
        payload = {**base, **power_snapshot}
    else:
        payload = base

    raw_watts = None
    if isinstance(payload.get("total_watts"), (int, float)) and math.isfinite(float(payload["total_watts"])):
        raw_watts = round(float(payload["total_watts"]), 3)
    payload["total_watts"] = raw_watts
    payload["raw_total_watts"] = raw_watts

    calibration = build_power_calibration_status(runtime)
    payload["calibration"] = calibration
    payload["confidence"] = "meter-calibrated" if calibration.get("mode") == "custom" else "experimental-default"

    adjusted = _apply_power_calibration(raw_watts, a=calibration.get("a"), b=calibration.get("b"))
    payload["adjusted_total_watts"] = round(adjusted, 3) if adjusted is not None else None
    payload["adjusted_label"] = "Estimated total power" if payload["adjusted_total_watts"] is not None else None
    payload["estimated_total_disclaimer"] = (
        "PMIC raw excludes direct 5V loads (USB/HAT/NVMe); estimated total uses "
        + ("meter-calibrated" if calibration.get("mode") == "custom" else "default")
        + " correction."
    )
    return payload


def collect_system_metrics_snapshot() -> dict[str, Any]:
    snapshot = default_system_metrics_snapshot()
    snapshot["updated_at_unix"] = int(time.time())
    now_unix = int(snapshot["updated_at_unix"])

    metrics_collected = False
    snapshot["swap_label"] = _read_swap_label()

    static_info = _collect_static_platform_info_cached(now_unix=now_unix)
    if isinstance(static_info, dict):
        snapshot["pi_model_name"] = static_info.get("pi_model_name")
        snapshot["os_pretty_name"] = static_info.get("os_pretty_name")
        snapshot["kernel_release"] = static_info.get("kernel_release")
        snapshot["kernel_version"] = static_info.get("kernel_version")
        bootloader = static_info.get("bootloader_version")
        firmware = static_info.get("firmware_version")
        if isinstance(bootloader, dict):
            snapshot["bootloader_version"] = {**_default_bootloader_version_snapshot(), **bootloader}
            metrics_collected = metrics_collected or bool(snapshot["bootloader_version"].get("available"))
        if isinstance(firmware, dict):
            snapshot["firmware_version"] = {**_default_firmware_version_snapshot(), **firmware}
            metrics_collected = metrics_collected or bool(snapshot["firmware_version"].get("available"))
        metrics_collected = metrics_collected or any(
            bool(snapshot.get(key)) for key in ("pi_model_name", "os_pretty_name", "kernel_release", "kernel_version")
        )

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
            snapshot["swap_label"] = _read_swap_label()
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

    power_estimate = _build_power_estimate_snapshot(now_unix=now_unix)
    if isinstance(power_estimate, dict):
        snapshot["power_estimate"] = {**_default_power_estimate_snapshot(), **power_estimate}
        if snapshot["power_estimate"].get("available") is True:
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


def get_free_storage_bytes(runtime: RuntimeConfig) -> int | None:
    if psutil is None:
        return None
    probe_path = runtime.base_dir if runtime.base_dir.exists() else Path("/")
    try:
        usage = psutil.disk_usage(str(probe_path))
        return int(max(0, usage.free))
    except OSError:
        return None


def get_preferred_model_offload_dir(runtime: RuntimeConfig) -> Path | None:
    if psutil is None:
        return None

    try:
        partitions = list(psutil.disk_partitions(all=False))
    except Exception:
        return None

    def _candidate_rank(device: str) -> int:
        lowered = device.lower()
        if lowered.startswith("/dev/nvme"):
            return 0
        if lowered.startswith("/dev/sd"):
            return 1
        return 99

    ranked = sorted(
        (
            partition
            for partition in partitions
            if str(getattr(partition, "device", "") or "").strip()
            and str(getattr(partition, "mountpoint", "") or "").strip()
        ),
        key=lambda part: (_candidate_rank(str(getattr(part, "device", "") or "")), str(getattr(part, "mountpoint", ""))),
    )

    for partition in ranked:
        device = str(getattr(partition, "device", "") or "").strip()
        mountpoint = Path(str(getattr(partition, "mountpoint", "") or "").strip())
        opts = str(getattr(partition, "opts", "") or "").lower()
        if _candidate_rank(device) >= 99:
            continue
        if str(mountpoint) in {"/", "/boot", "/boot/firmware"}:
            continue
        if "rw" not in {part.strip() for part in opts.split(",") if part.strip()}:
            continue
        try:
            if not mountpoint.exists() or not mountpoint.is_dir():
                continue
        except OSError:
            continue
        if not os.access(mountpoint, os.W_OK | os.X_OK):
            continue
        target_dir = mountpoint / "potato-models"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(target_dir, os.W_OK | os.X_OK):
            return target_dir
    return None


def build_model_storage_target_status(runtime: RuntimeConfig) -> dict[str, Any]:
    payload = {
        "ssd": {
            "available": False,
            "mount_point": None,
            "models_dir": None,
            "free_bytes": 0,
            "label": "No writable SSD detected",
        }
    }
    target_dir = get_preferred_model_offload_dir(runtime)
    if target_dir is None:
        return payload

    free_bytes = 0
    if psutil is not None:
        try:
            usage = psutil.disk_usage(str(target_dir))
            free_bytes = int(max(0, usage.free))
        except Exception:
            free_bytes = 0

    payload["ssd"] = {
        "available": True,
        "mount_point": str(target_dir.parent),
        "models_dir": str(target_dir),
        "free_bytes": free_bytes,
        "label": "Mounted SSD",
    }
    return payload


def compute_required_download_bytes(total_bytes: int, partial_bytes: int = 0) -> int:
    required = max(0, int(total_bytes) - max(0, int(partial_bytes)))
    return int(required)


def is_likely_too_large_for_storage(
    *,
    total_bytes: int,
    free_bytes: int | None,
    partial_bytes: int = 0,
) -> bool:
    if int(total_bytes) <= 0:
        return False
    if free_bytes is None:
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
            async with client.stream("GET", source_url, headers={"range": "bytes=0-0"}) as response:
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
        except httpx.HTTPError:
            return 0
        return 0


async def check_llama_health(runtime: RuntimeConfig, *, busy_is_healthy: bool = True) -> bool:
    timeout = httpx.Timeout(2.0, connect=1.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for path in ("/health", "/v1/models"):
            try:
                response = await client.get(f"{runtime.llama_base_url}{path}")
            except httpx.ReadTimeout:
                if busy_is_healthy:
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


def get_monotonic_time() -> float:
    return asyncio.get_running_loop().time()
