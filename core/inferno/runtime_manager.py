"""Runtime management for Inferno — slot discovery, compatibility, switching, settings.

This module owns the inference-facing runtime lifecycle: device
classification, family compatibility, slot discovery, runtime
installation/switching, and settings normalization.

Product-specific hardware probing (psutil, /proc reads, vcgencmd) and
system metrics stay in core.runtime_state. This module receives device
classification results via RuntimeStoreConfig or explicit parameters.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

logger = __import__("logging").getLogger("potato")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_RUNTIME_FAMILIES = ("ik_llama", "llama_cpp", "litert")

# Families that use an external server binary (bin/llama-server).
# LiteRT uses a Python adapter instead — no binary needed in the slot.
LLAMA_SERVER_RUNTIME_FAMILIES = ("ik_llama", "llama_cpp")

PI4_8GB_MEMORY_THRESHOLD_BYTES = 6 * 1024 * 1024 * 1024
PI4_INCOMPATIBLE_RUNTIMES = ("ik_llama", "litert")

DEVICE_CLOCK_LIMITS: dict[str, dict[str, int]] = {
    "pi5": {"cpu_max_hz": 2_400_000_000, "gpu_max_hz": 1_000_000_000},
    "pi4": {"cpu_max_hz": 1_800_000_000, "gpu_max_hz": 500_000_000},
}

LLAMA_RUNTIME_BUNDLE_MARKER_FILENAME = ".potato-llama-runtime-bundle.json"

MODEL_LOADING_INACTIVE: dict[str, Any] = {
    "active": False,
    "progress_percent": None,
    "resident_bytes": None,
    "model_size_bytes": None,
}

# Memory threshold for 16GB vs 8GB Pi classification.
MODEL_UPLOAD_PI_16GB_MEMORY_THRESHOLD_BYTES = 12 * 1024 * 1024 * 1024


# ---------------------------------------------------------------------------
# Store configuration
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RuntimeStoreConfig:
    """Filesystem and device context for runtime management operations.

    This bundles the paths and device info that the Potato layer injects
    so that Inferno never needs to import RuntimeConfig.
    """

    runtimes_dir: Path
    install_dir: Path
    settings_path: Path
    device_class: str
    total_memory_bytes: int


# ---------------------------------------------------------------------------
# Device classification
# ---------------------------------------------------------------------------


def classify_runtime_device(
    *,
    pi_model_name: str,
    total_memory_bytes: int,
) -> str:
    """Classify device hardware. Both params are required — no hardware probing."""
    model_name = (pi_model_name or "").strip().lower()
    if not model_name:
        return "unknown"
    if "raspberry pi" not in model_name:
        return "unknown"
    if "raspberry pi 5" in model_name:
        if total_memory_bytes >= MODEL_UPLOAD_PI_16GB_MEMORY_THRESHOLD_BYTES:
            return "pi5-16gb"
        return "pi5-8gb"
    if "raspberry pi 4" in model_name:
        if total_memory_bytes >= MODEL_UPLOAD_PI_16GB_MEMORY_THRESHOLD_BYTES:
            return "pi4-16gb"
        if total_memory_bytes >= PI4_8GB_MEMORY_THRESHOLD_BYTES:
            return "pi4-8gb"
        return "pi4-4gb"
    return "other-pi"


# ---------------------------------------------------------------------------
# Compatibility checks
# ---------------------------------------------------------------------------


def check_runtime_device_compatibility(
    device_class: str,
    runtime_family: str,
) -> dict[str, Any]:
    if device_class.startswith("pi4-") and runtime_family in PI4_INCOMPATIBLE_RUNTIMES:
        return {
            "compatible": False,
            "reason": (
                f"{runtime_family} requires ARMv8.2-A dot product instructions (Cortex-A76+). "
                f"Pi 4 (Cortex-A72, ARMv8.0-A) must use llama_cpp."
            ),
            "recommended_family": "llama_cpp",
        }
    return {"compatible": True, "reason": None, "recommended_family": None}


def get_device_clock_limits(device_class: str) -> dict[str, int]:
    for prefix, limits in DEVICE_CLOCK_LIMITS.items():
        if device_class.startswith(prefix):
            return dict(limits)
    return dict(DEVICE_CLOCK_LIMITS["pi5"])


# ---------------------------------------------------------------------------
# Settings normalization (pure)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Model loading progress (pure)
# ---------------------------------------------------------------------------


def compute_model_loading_progress(
    *,
    state: str,
    has_model: bool,
    model_size_bytes: int,
    no_mmap_env: str,
    llama_rss: dict[str, Any],
) -> dict[str, Any]:
    if state != "BOOTING" or not has_model or model_size_bytes <= 0:
        return dict(MODEL_LOADING_INACTIVE)
    if not llama_rss.get("available"):
        return dict(MODEL_LOADING_INACTIVE)
    if no_mmap_env == "1":
        resident_bytes = llama_rss.get("rss_anon_bytes")
    elif no_mmap_env == "auto":
        anon = llama_rss.get("rss_anon_bytes") or 0
        file = llama_rss.get("rss_file_bytes") or 0
        resident_bytes = max(anon, file) if (anon or file) else None
    else:
        resident_bytes = llama_rss.get("rss_file_bytes")
    if resident_bytes is None or not isinstance(resident_bytes, (int, float)):
        return dict(MODEL_LOADING_INACTIVE)
    resident_bytes = int(resident_bytes)
    progress_percent = min(100, max(0, int(resident_bytes * 100 / model_size_bytes)))
    return {
        "active": True,
        "progress_percent": progress_percent,
        "resident_bytes": resident_bytes,
        "model_size_bytes": model_size_bytes,
    }


# ---------------------------------------------------------------------------
# Atomic write utility
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import tempfile

        fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload))
        os.replace(tmp_name, path)
    except OSError:
        logger.warning("Could not persist JSON state to %s", path, exc_info=True)


# ---------------------------------------------------------------------------
# Slot discovery
# ---------------------------------------------------------------------------


def discover_runtime_slots(runtimes_dir: Path) -> list[dict[str, Any]]:
    """Discover installed runtime slots across all supported families."""
    slots: list[dict[str, Any]] = []
    for family in SUPPORTED_RUNTIME_FAMILIES:
        slot_dir = runtimes_dir / family
        if not slot_dir.is_dir():
            continue
        if family in LLAMA_SERVER_RUNTIME_FAMILIES:
            if not (slot_dir / "bin" / "llama-server").exists():
                continue
        else:
            if not (slot_dir / "runtime.json").exists():
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


def find_runtime_slot_by_family(runtimes_dir: Path, family: str) -> dict[str, Any] | None:
    """Find a runtime slot by family name."""
    for slot in discover_runtime_slots(runtimes_dir):
        if slot.get("family") == family:
            return slot
    return None


# ---------------------------------------------------------------------------
# Marker management
# ---------------------------------------------------------------------------


def read_llama_runtime_bundle_marker(install_dir: Path) -> dict[str, Any] | None:
    marker_path = install_dir / LLAMA_RUNTIME_BUNDLE_MARKER_FILENAME
    try:
        raw = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def write_llama_runtime_bundle_marker(install_dir: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "family": str(bundle.get("family") or ""),
        "source_bundle_path": str(bundle.get("path") or ""),
        "source_bundle_name": str(bundle.get("name") or bundle.get("family") or ""),
        "profile": str(bundle.get("profile") or "unknown"),
        "version_summary": bundle.get("version_summary") or bundle.get("version"),
        "llama_cpp_commit": bundle.get("llama_cpp_commit") or bundle.get("commit"),
        "switched_at_unix": int(time.time()),
    }
    _atomic_write_json(install_dir / LLAMA_RUNTIME_BUNDLE_MARKER_FILENAME, payload)
    return payload


def _detect_installed_runtime_family(install_dir: Path) -> str:
    """Detect the active runtime family from marker or installed runtime.json."""
    marker = read_llama_runtime_bundle_marker(install_dir)
    if isinstance(marker, dict) and marker.get("family"):
        return str(marker["family"])
    runtime_json = install_dir / "runtime.json"
    if runtime_json.exists():
        try:
            meta = json.loads(runtime_json.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("family"):
                return str(meta["family"])
        except (OSError, json.JSONDecodeError):
            pass
    return ""


def _read_installed_runtime_metadata(install_dir: Path) -> dict[str, Any]:
    """Read runtime metadata from marker first, then fallback to runtime.json."""
    marker = read_llama_runtime_bundle_marker(install_dir)
    if isinstance(marker, dict) and marker.get("family"):
        return marker
    runtime_json = install_dir / "runtime.json"
    if runtime_json.exists():
        try:
            meta = json.loads(runtime_json.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                return meta
        except (OSError, json.JSONDecodeError):
            pass
    return {}


# ---------------------------------------------------------------------------
# Bundle discovery (legacy bundle search)
# ---------------------------------------------------------------------------


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


def _default_llama_runtime_bundle_roots(base_dir: Path) -> list[Path]:
    return [
        base_dir / "llama-bundles",
        Path("/tmp/potato-qwen35-ab/references/old_reference_design/llama_cpp_binary"),
        Path("/tmp/potato-os/references/old_reference_design/llama_cpp_binary"),
    ]


def get_llama_runtime_bundle_roots(base_dir: Path) -> list[Path]:
    raw = os.getenv("POTATO_LLAMA_RUNTIME_BUNDLE_ROOTS", "").strip()
    candidates: list[Path]
    if raw:
        candidates = [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]
    else:
        candidates = _default_llama_runtime_bundle_roots(base_dir)

    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        roots.append(candidate)
    return roots


def discover_llama_runtime_bundles(bundle_roots: list[Path]) -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    for root in bundle_roots:
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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def find_llama_runtime_bundle_by_path(bundle_roots: list[Path], bundle_path: str) -> dict[str, Any] | None:
    candidate = str(bundle_path or "").strip()
    if not candidate:
        return None
    try:
        resolved = str(Path(candidate).resolve())
    except OSError:
        return None
    for bundle in discover_llama_runtime_bundles(bundle_roots):
        try:
            bundle_resolved = str(Path(str(bundle.get("path") or "")).resolve())
        except OSError:
            continue
        if bundle_resolved == resolved:
            return bundle
    return None


# ---------------------------------------------------------------------------
# Settings I/O
# ---------------------------------------------------------------------------


def read_llama_runtime_settings(settings_path: Path) -> dict[str, Any]:
    """Read runtime settings. Normalizes inferno-owned fields; power_calibration passes through."""
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "memory_loading_mode": normalize_llama_memory_loading_mode(raw.get("memory_loading_mode")),
        "allow_unsupported_large_models": normalize_allow_unsupported_large_models(
            raw.get("allow_unsupported_large_models")
        ),
        "power_calibration": raw.get("power_calibration") or {},
        "updated_at_unix": _safe_int(raw.get("updated_at_unix"), 0) or None,
    }


def write_llama_runtime_settings(
    settings_path: Path,
    *,
    memory_loading_mode: str | None = None,
    allow_unsupported_large_models: bool | None = None,
    power_calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = read_llama_runtime_settings(settings_path)
    payload = {
        "memory_loading_mode": normalize_llama_memory_loading_mode(
            current.get("memory_loading_mode") if memory_loading_mode is None else memory_loading_mode
        ),
        "allow_unsupported_large_models": normalize_allow_unsupported_large_models(
            current.get("allow_unsupported_large_models")
            if allow_unsupported_large_models is None
            else allow_unsupported_large_models
        ),
        "power_calibration": power_calibration if power_calibration is not None else current.get("power_calibration", {}),
        "updated_at_unix": int(time.time()),
    }
    _atomic_write_json(settings_path, payload)
    return payload


# ---------------------------------------------------------------------------
# Status builders
# ---------------------------------------------------------------------------


def build_llama_memory_loading_status(settings_path: Path) -> dict[str, Any]:
    settings = read_llama_runtime_settings(settings_path)
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


def build_llama_large_model_override_status(settings_path: Path) -> dict[str, Any]:
    settings = read_llama_runtime_settings(settings_path)
    enabled = normalize_allow_unsupported_large_models(settings.get("allow_unsupported_large_models"))
    return {
        "enabled": enabled,
        "label": "Try unsupported large model anyway" if enabled else "Use compatibility warnings (default)",
        "updated_at_unix": settings.get("updated_at_unix"),
    }


def build_large_model_compatibility(
    store: RuntimeStoreConfig,
    *,
    model_filename: str = "",
    model_size_bytes: int = 0,
    allow_override: bool | None = None,
    threshold_bytes: int = 0,
    storage_free_bytes: int = 0,
    pi_model_name: str = "",
) -> dict[str, Any]:
    """Build large model compatibility status.

    Potato provides pre-computed values (threshold, storage, pi_model_name)
    so inferno doesn't need to probe hardware or read env vars.
    """
    override_enabled = (
        normalize_allow_unsupported_large_models(allow_override)
        if allow_override is not None
        else normalize_allow_unsupported_large_models(
            read_llama_runtime_settings(store.settings_path).get("allow_unsupported_large_models")
        )
    )
    size_bytes = max(0, model_size_bytes)

    warnings: list[dict[str, Any]] = []
    if size_bytes > threshold_bytes > 0 and store.device_class != "pi5-16gb" and not override_enabled:
        filename = model_filename or "model.gguf"
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

    runtime_family = _detect_installed_runtime_family(store.install_dir)
    runtime_compat = check_runtime_device_compatibility(store.device_class, runtime_family)

    return {
        "device_class": store.device_class,
        "pi_model_name": pi_model_name,
        "memory_total_bytes": store.total_memory_bytes,
        "large_model_warn_threshold_bytes": threshold_bytes,
        "storage_free_bytes": storage_free_bytes,
        "supported_target": "raspberry-pi-5-16gb",
        "override_enabled": override_enabled,
        "runtime_compatibility": runtime_compat,
        "warnings": warnings,
    }


def build_llama_runtime_status(
    store: RuntimeStoreConfig,
    *,
    active_model_filename: str = "",
    switch_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build runtime status. switch_snapshot is pre-extracted from app.state by caller."""
    install_dir = store.install_dir
    metadata = _read_installed_runtime_metadata(install_dir)
    available_runtimes = discover_runtime_slots(store.runtimes_dir)

    current_family = str(metadata.get("family") or metadata.get("source_bundle_name") or "").strip()
    active_is_gguf = active_model_filename.lower().endswith(".gguf") if active_model_filename else True
    active_is_litertlm = active_model_filename.lower().endswith(".litertlm") if active_model_filename else False
    for slot in available_runtimes:
        slot["is_active"] = slot.get("family") == current_family
        compat = check_runtime_device_compatibility(
            store.device_class, slot.get("family", "")
        )
        family = slot.get("family", "")
        if family == "litert" and active_is_gguf:
            slot["compatible"] = False
        elif family in LLAMA_SERVER_RUNTIME_FAMILIES and active_is_litertlm:
            slot["compatible"] = False
        else:
            slot["compatible"] = compat["compatible"]

    snap = switch_snapshot or {}
    switch_section = {
        "active": bool(snap.get("active", False)),
        "target_family": snap.get("target_family"),
        "started_at_unix": snap.get("started_at_unix"),
        "completed_at_unix": snap.get("completed_at_unix"),
        "error": snap.get("error"),
    }

    detected_family = str(metadata.get("family") or "")
    runtime_type = "litert_adapter" if detected_family == "litert" else "llama_server"

    current = {
        "install_dir": str(install_dir),
        "exists": install_dir.exists(),
        "has_server_binary": (install_dir / "bin" / "llama-server").exists(),
        "runtime_type": runtime_type,
        "family": metadata.get("family"),
        "source_bundle_path": metadata.get("source_bundle_path"),
        "source_bundle_name": metadata.get("source_bundle_name"),
        "profile": metadata.get("profile"),
        "version_summary": metadata.get("version_summary") or metadata.get("version"),
        "llama_cpp_commit": metadata.get("llama_cpp_commit") or metadata.get("commit"),
        "switched_at_unix": metadata.get("switched_at_unix"),
    }

    return {
        "current": current,
        "available_runtimes": available_runtimes,
        "switch": switch_section,
        "memory_loading": build_llama_memory_loading_status(store.settings_path),
        "large_model_override": build_llama_large_model_override_status(store.settings_path),
    }


# ---------------------------------------------------------------------------
# Runtime installation (async)
# ---------------------------------------------------------------------------


async def install_llama_runtime_bundle(install_dir: Path, bundle_dir: Path) -> dict[str, Any]:
    """Install a runtime bundle to the install directory via rsync."""
    install_dir.mkdir(parents=True, exist_ok=True)

    # LiteRT has no binary to rsync — just ensure install dir exists.
    bundle_runtime_json = bundle_dir / "runtime.json"
    if bundle_runtime_json.exists():
        try:
            meta = json.loads(bundle_runtime_json.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and meta.get("family") == "litert":
                return {"ok": True, "reason": "litert_no_rsync_needed", "install_dir": str(install_dir)}
        except (OSError, json.JSONDecodeError):
            pass

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


# ---------------------------------------------------------------------------
# Compatibility enforcement (async)
# ---------------------------------------------------------------------------


async def ensure_compatible_runtime(store: RuntimeStoreConfig) -> tuple[bool, str]:
    """Auto-switch runtime if current is incompatible with device hardware."""
    current_family = _detect_installed_runtime_family(store.install_dir)
    compat = check_runtime_device_compatibility(store.device_class, current_family)
    if compat["compatible"]:
        return False, "compatible"
    recommended = compat.get("recommended_family") or ""
    if not recommended:
        logger.warning("Device %s incompatible with %s but no recommendation available", store.device_class, current_family)
        return False, "no_recommendation"
    slot = find_runtime_slot_by_family(store.runtimes_dir, recommended)
    if slot is None:
        logger.warning("Recommended runtime %s not available as a slot", recommended)
        return False, "slot_unavailable"
    slot_path = Path(slot["path"])
    logger.info(
        "Auto-switching runtime: %s -> %s (device %s incompatible with %s)",
        current_family, recommended, store.device_class, current_family,
    )
    result = await install_llama_runtime_bundle(store.install_dir, slot_path)
    if not isinstance(result, dict) or not result.get("ok", False):
        reason = result.get("reason", "unknown") if isinstance(result, dict) else "unknown"
        logger.error("Runtime install failed during auto-switch: %s", reason)
        return False, "install_failed"
    return True, "pi4_incompatible_runtime"
