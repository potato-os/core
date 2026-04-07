"""Tests for core.inferno.runtime_manager — classification, compatibility, slots, markers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from core.inferno.runtime_manager import (
        SUPPORTED_RUNTIME_FAMILIES,
        LLAMA_SERVER_RUNTIME_FAMILIES,
        PI4_INCOMPATIBLE_RUNTIMES,
        PI4_8GB_MEMORY_THRESHOLD_BYTES,
        DEVICE_CLOCK_LIMITS,
        MODEL_LOADING_INACTIVE,
        LLAMA_RUNTIME_BUNDLE_MARKER_FILENAME,
        RuntimeStoreConfig,
        classify_runtime_device,
        check_runtime_device_compatibility,
        get_device_clock_limits,
        normalize_llama_memory_loading_mode,
        normalize_allow_unsupported_large_models,
        llama_memory_loading_no_mmap_env,
        compute_model_loading_progress,
        discover_runtime_slots,
        find_runtime_slot_by_family,
        read_llama_runtime_bundle_marker,
        write_llama_runtime_bundle_marker,
        _detect_installed_runtime_family,
        _read_installed_runtime_metadata,
        read_llama_runtime_settings,
        write_llama_runtime_settings,
        build_llama_memory_loading_status,
        build_llama_large_model_override_status,
        build_large_model_compatibility,
        build_llama_runtime_status,
        install_llama_runtime_bundle,
        ensure_compatible_runtime,
    )
except ModuleNotFoundError:
    from inferno.runtime_manager import (  # type: ignore[no-redef]
        SUPPORTED_RUNTIME_FAMILIES,
        LLAMA_SERVER_RUNTIME_FAMILIES,
        PI4_INCOMPATIBLE_RUNTIMES,
        PI4_8GB_MEMORY_THRESHOLD_BYTES,
        DEVICE_CLOCK_LIMITS,
        MODEL_LOADING_INACTIVE,
        LLAMA_RUNTIME_BUNDLE_MARKER_FILENAME,
        RuntimeStoreConfig,
        classify_runtime_device,
        check_runtime_device_compatibility,
        get_device_clock_limits,
        normalize_llama_memory_loading_mode,
        normalize_allow_unsupported_large_models,
        llama_memory_loading_no_mmap_env,
        compute_model_loading_progress,
        discover_runtime_slots,
        find_runtime_slot_by_family,
        read_llama_runtime_bundle_marker,
        write_llama_runtime_bundle_marker,
        _detect_installed_runtime_family,
        _read_installed_runtime_metadata,
        read_llama_runtime_settings,
        write_llama_runtime_settings,
        build_llama_memory_loading_status,
        build_llama_large_model_override_status,
        build_large_model_compatibility,
        build_llama_runtime_status,
        install_llama_runtime_bundle,
        ensure_compatible_runtime,
    )


@pytest.fixture
def store(tmp_path: Path) -> RuntimeStoreConfig:
    """Create a RuntimeStoreConfig backed by temp directories."""
    runtimes_dir = tmp_path / "runtimes"
    runtimes_dir.mkdir()
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    settings_path = tmp_path / "state" / "llama_runtime.json"
    settings_path.parent.mkdir()
    return RuntimeStoreConfig(
        runtimes_dir=runtimes_dir,
        install_dir=install_dir,
        settings_path=settings_path,
        device_class="pi5-8gb",
        total_memory_bytes=8 * 1024**3,
    )


def _make_ik_llama_slot(runtimes_dir: Path) -> Path:
    """Create a minimal ik_llama slot for testing."""
    slot = runtimes_dir / "ik_llama"
    (slot / "bin").mkdir(parents=True)
    (slot / "bin" / "llama-server").write_bytes(b"fake")
    (slot / "runtime.json").write_text(
        json.dumps({"family": "ik_llama", "commit": "abc123", "profile": "pi5-opt"}),
        encoding="utf-8",
    )
    return slot


def _make_litert_slot(runtimes_dir: Path) -> Path:
    """Create a minimal litert slot for testing."""
    slot = runtimes_dir / "litert"
    slot.mkdir(parents=True)
    (slot / "runtime.json").write_text(
        json.dumps({"family": "litert", "version": "1.0"}),
        encoding="utf-8",
    )
    return slot


# -- Constants --


def test_supported_runtime_families():
    assert "ik_llama" in SUPPORTED_RUNTIME_FAMILIES
    assert "llama_cpp" in SUPPORTED_RUNTIME_FAMILIES
    assert "litert" in SUPPORTED_RUNTIME_FAMILIES


def test_llama_server_families():
    assert "ik_llama" in LLAMA_SERVER_RUNTIME_FAMILIES
    assert "llama_cpp" in LLAMA_SERVER_RUNTIME_FAMILIES
    assert "litert" not in LLAMA_SERVER_RUNTIME_FAMILIES


def test_pi4_incompatible_runtimes():
    assert "ik_llama" in PI4_INCOMPATIBLE_RUNTIMES
    assert "litert" in PI4_INCOMPATIBLE_RUNTIMES
    assert "llama_cpp" not in PI4_INCOMPATIBLE_RUNTIMES


# -- Device classification --


def test_classify_pi5_8gb():
    result = classify_runtime_device(
        pi_model_name="Raspberry Pi 5 Model B Rev 1.0",
        total_memory_bytes=8 * 1024**3,
    )
    assert result == "pi5-8gb"


def test_classify_pi5_16gb():
    result = classify_runtime_device(
        pi_model_name="Raspberry Pi 5 Model B Rev 1.0",
        total_memory_bytes=16 * 1024**3,
    )
    assert result == "pi5-16gb"


def test_classify_pi4_4gb():
    result = classify_runtime_device(
        pi_model_name="Raspberry Pi 4 Model B Rev 1.4",
        total_memory_bytes=4 * 1024**3,
    )
    assert result == "pi4-4gb"


def test_classify_pi4_8gb():
    result = classify_runtime_device(
        pi_model_name="Raspberry Pi 4 Model B Rev 1.4",
        total_memory_bytes=8 * 1024**3,
    )
    assert result == "pi4-8gb"


def test_classify_unknown_no_model_name():
    assert classify_runtime_device(pi_model_name="", total_memory_bytes=8 * 1024**3) == "unknown"


def test_classify_unknown_non_pi():
    assert classify_runtime_device(pi_model_name="Some Board", total_memory_bytes=8 * 1024**3) == "unknown"


def test_classify_other_pi():
    assert classify_runtime_device(pi_model_name="Raspberry Pi 3", total_memory_bytes=1 * 1024**3) == "other-pi"


# -- Compatibility --


def test_compatible_pi5_ik_llama():
    result = check_runtime_device_compatibility("pi5-8gb", "ik_llama")
    assert result["compatible"] is True


def test_incompatible_pi4_ik_llama():
    result = check_runtime_device_compatibility("pi4-4gb", "ik_llama")
    assert result["compatible"] is False
    assert result["recommended_family"] == "llama_cpp"


def test_incompatible_pi4_litert():
    result = check_runtime_device_compatibility("pi4-8gb", "litert")
    assert result["compatible"] is False


def test_compatible_pi4_llama_cpp():
    result = check_runtime_device_compatibility("pi4-4gb", "llama_cpp")
    assert result["compatible"] is True


# -- Clock limits --


def test_clock_limits_pi5():
    limits = get_device_clock_limits("pi5-8gb")
    assert "cpu_max_hz" in limits
    assert limits["cpu_max_hz"] == 2_400_000_000


def test_clock_limits_pi4():
    limits = get_device_clock_limits("pi4-4gb")
    assert limits["cpu_max_hz"] == 1_800_000_000


def test_clock_limits_unknown_defaults_to_pi5():
    limits = get_device_clock_limits("unknown")
    assert limits == get_device_clock_limits("pi5-8gb")


# -- Memory loading mode normalization --


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("full_ram", "full_ram"),
        ("no_mmap", "full_ram"),
        ("no-mmap", "full_ram"),
        ("1", "full_ram"),
        ("true", "full_ram"),
        ("mmap", "mmap"),
        ("mapped", "mmap"),
        ("0", "mmap"),
        ("false", "mmap"),
        ("auto", "auto"),
        ("", "auto"),
        (None, "auto"),
        ("bogus", "auto"),
    ],
)
def test_normalize_memory_loading_mode(raw, expected):
    assert normalize_llama_memory_loading_mode(raw) == expected


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("full_ram", "1"),
        ("mmap", "0"),
        ("auto", "auto"),
    ],
)
def test_memory_loading_no_mmap_env(mode, expected):
    assert llama_memory_loading_no_mmap_env(mode) == expected


# -- Large model override normalization --


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, True),
        (False, False),
        (None, False),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("no", False),
    ],
)
def test_normalize_allow_unsupported_large_models(raw, expected):
    assert normalize_allow_unsupported_large_models(raw) == expected


# -- Model loading progress --


def test_loading_progress_during_boot():
    result = compute_model_loading_progress(
        state="BOOTING",
        has_model=True,
        model_size_bytes=1_000_000,
        no_mmap_env="1",
        llama_rss={"available": True, "rss_anon_bytes": 500_000},
    )
    assert result["active"] is True
    assert result["progress_percent"] == 50


def test_loading_progress_not_booting():
    result = compute_model_loading_progress(
        state="IDLE",
        has_model=True,
        model_size_bytes=1_000_000,
        no_mmap_env="1",
        llama_rss={"available": True, "rss_anon_bytes": 500_000},
    )
    assert result["active"] is False


def test_loading_progress_no_model():
    result = compute_model_loading_progress(
        state="BOOTING",
        has_model=False,
        model_size_bytes=0,
        no_mmap_env="1",
        llama_rss={"available": True},
    )
    assert result["active"] is False


def test_loading_progress_mmap_mode():
    result = compute_model_loading_progress(
        state="BOOTING",
        has_model=True,
        model_size_bytes=1_000_000,
        no_mmap_env="0",
        llama_rss={"available": True, "rss_file_bytes": 750_000},
    )
    assert result["active"] is True
    assert result["progress_percent"] == 75


def test_loading_progress_auto_mode_uses_max():
    result = compute_model_loading_progress(
        state="BOOTING",
        has_model=True,
        model_size_bytes=1_000_000,
        no_mmap_env="auto",
        llama_rss={"available": True, "rss_anon_bytes": 300_000, "rss_file_bytes": 600_000},
    )
    assert result["active"] is True
    assert result["progress_percent"] == 60


def test_loading_progress_caps_at_100():
    result = compute_model_loading_progress(
        state="BOOTING",
        has_model=True,
        model_size_bytes=100,
        no_mmap_env="1",
        llama_rss={"available": True, "rss_anon_bytes": 200},
    )
    assert result["progress_percent"] == 100


def test_loading_inactive_constant():
    assert MODEL_LOADING_INACTIVE["active"] is False


# -- Slot discovery --


def test_discover_runtime_slots_finds_ik_llama(tmp_path):
    runtimes_dir = tmp_path / "runtimes"
    runtimes_dir.mkdir()
    _make_ik_llama_slot(runtimes_dir)
    slots = discover_runtime_slots(runtimes_dir)
    assert len(slots) == 1
    assert slots[0]["family"] == "ik_llama"
    assert slots[0]["commit"] == "abc123"


def test_discover_runtime_slots_finds_litert(tmp_path):
    runtimes_dir = tmp_path / "runtimes"
    runtimes_dir.mkdir()
    _make_litert_slot(runtimes_dir)
    slots = discover_runtime_slots(runtimes_dir)
    assert len(slots) == 1
    assert slots[0]["family"] == "litert"


def test_discover_runtime_slots_empty(tmp_path):
    runtimes_dir = tmp_path / "runtimes"
    runtimes_dir.mkdir()
    assert discover_runtime_slots(runtimes_dir) == []


def test_discover_runtime_slots_skips_incomplete_llama(tmp_path):
    runtimes_dir = tmp_path / "runtimes"
    (runtimes_dir / "ik_llama").mkdir(parents=True)
    # No bin/llama-server → should be skipped
    assert discover_runtime_slots(runtimes_dir) == []


def test_find_runtime_slot_by_family_found(tmp_path):
    runtimes_dir = tmp_path / "runtimes"
    runtimes_dir.mkdir()
    _make_ik_llama_slot(runtimes_dir)
    slot = find_runtime_slot_by_family(runtimes_dir, "ik_llama")
    assert slot is not None
    assert slot["family"] == "ik_llama"


def test_find_runtime_slot_by_family_not_found(tmp_path):
    runtimes_dir = tmp_path / "runtimes"
    runtimes_dir.mkdir()
    assert find_runtime_slot_by_family(runtimes_dir, "llama_cpp") is None


# -- Marker management --


def test_write_and_read_marker(tmp_path):
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    bundle = {"family": "ik_llama", "path": "/tmp/slot", "profile": "pi5-opt", "commit": "abc"}
    written = write_llama_runtime_bundle_marker(install_dir, bundle)
    assert written["family"] == "ik_llama"
    assert written["switched_at_unix"] > 0

    read_back = read_llama_runtime_bundle_marker(install_dir)
    assert read_back is not None
    assert read_back["family"] == "ik_llama"


def test_read_marker_missing(tmp_path):
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    assert read_llama_runtime_bundle_marker(install_dir) is None


def test_detect_installed_family_from_marker(tmp_path):
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    write_llama_runtime_bundle_marker(install_dir, {"family": "llama_cpp"})
    assert _detect_installed_runtime_family(install_dir) == "llama_cpp"


def test_detect_installed_family_from_runtime_json(tmp_path):
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    (install_dir / "runtime.json").write_text(
        json.dumps({"family": "ik_llama"}), encoding="utf-8"
    )
    assert _detect_installed_runtime_family(install_dir) == "ik_llama"


def test_detect_installed_family_empty(tmp_path):
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    assert _detect_installed_runtime_family(install_dir) == ""


def test_read_installed_runtime_metadata_prefers_marker(tmp_path):
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    write_llama_runtime_bundle_marker(install_dir, {"family": "llama_cpp", "commit": "xyz"})
    (install_dir / "runtime.json").write_text(
        json.dumps({"family": "ik_llama", "commit": "old"}), encoding="utf-8"
    )
    meta = _read_installed_runtime_metadata(install_dir)
    assert meta["family"] == "llama_cpp"


def test_read_installed_runtime_metadata_falls_back_to_json(tmp_path):
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    (install_dir / "runtime.json").write_text(
        json.dumps({"family": "ik_llama"}), encoding="utf-8"
    )
    meta = _read_installed_runtime_metadata(install_dir)
    assert meta["family"] == "ik_llama"


def test_read_installed_runtime_metadata_empty(tmp_path):
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    assert _read_installed_runtime_metadata(install_dir) == {}


# -- Settings I/O --


def test_read_settings_defaults(store):
    settings = read_llama_runtime_settings(store.settings_path)
    assert settings["memory_loading_mode"] == "auto"
    assert settings["allow_unsupported_large_models"] is False


def test_write_and_read_settings(store):
    write_llama_runtime_settings(store.settings_path, memory_loading_mode="full_ram")
    settings = read_llama_runtime_settings(store.settings_path)
    assert settings["memory_loading_mode"] == "full_ram"
    assert settings["updated_at_unix"] is not None


def test_write_settings_preserves_unset_fields(store):
    write_llama_runtime_settings(store.settings_path, memory_loading_mode="mmap")
    write_llama_runtime_settings(store.settings_path, allow_unsupported_large_models=True)
    settings = read_llama_runtime_settings(store.settings_path)
    assert settings["memory_loading_mode"] == "mmap"
    assert settings["allow_unsupported_large_models"] is True


def test_write_settings_passes_through_power_calibration(store):
    cal = {"mode": "custom", "a": 1.5, "b": 0.3}
    write_llama_runtime_settings(store.settings_path, power_calibration=cal)
    settings = read_llama_runtime_settings(store.settings_path)
    assert settings["power_calibration"]["mode"] == "custom"
    assert settings["power_calibration"]["a"] == 1.5


# -- Status builders --


def test_memory_loading_status_auto(store):
    status = build_llama_memory_loading_status(store.settings_path)
    assert status["mode"] == "auto"
    assert status["no_mmap_env"] == "auto"
    assert "Automatic" in status["label"]


def test_memory_loading_status_full_ram(store):
    write_llama_runtime_settings(store.settings_path, memory_loading_mode="full_ram")
    status = build_llama_memory_loading_status(store.settings_path)
    assert status["mode"] == "full_ram"
    assert status["no_mmap_env"] == "1"


def test_large_model_override_status_default(store):
    status = build_llama_large_model_override_status(store.settings_path)
    assert status["enabled"] is False
    assert "default" in status["label"]


def test_large_model_override_status_enabled(store):
    write_llama_runtime_settings(store.settings_path, allow_unsupported_large_models=True)
    status = build_llama_large_model_override_status(store.settings_path)
    assert status["enabled"] is True


def test_large_model_compatibility_no_warnings(store):
    result = build_large_model_compatibility(
        store,
        model_filename="small.gguf",
        model_size_bytes=100,
        threshold_bytes=5_000_000_000,
        storage_free_bytes=10_000_000_000,
    )
    assert result["device_class"] == "pi5-8gb"
    assert result["warnings"] == []


def test_large_model_compatibility_with_warning(store):
    result = build_large_model_compatibility(
        store,
        model_filename="huge.gguf",
        model_size_bytes=10_000_000_000,
        threshold_bytes=5_000_000_000,
        storage_free_bytes=20_000_000_000,
    )
    assert len(result["warnings"]) == 1
    assert result["warnings"][0]["code"] == "large_model_unsupported_pi_warning"


def test_large_model_compatibility_no_warning_on_16gb_pi5():
    store_16gb = RuntimeStoreConfig(
        runtimes_dir=Path("/tmp"),
        install_dir=Path("/tmp"),
        settings_path=Path("/tmp/nonexistent.json"),
        device_class="pi5-16gb",
        total_memory_bytes=16 * 1024**3,
    )
    result = build_large_model_compatibility(
        store_16gb,
        model_filename="huge.gguf",
        model_size_bytes=10_000_000_000,
        threshold_bytes=5_000_000_000,
    )
    assert result["warnings"] == []


def test_runtime_status_basic(store):
    _make_ik_llama_slot(store.runtimes_dir)
    write_llama_runtime_bundle_marker(store.install_dir, {"family": "ik_llama", "commit": "abc"})
    status = build_llama_runtime_status(store, active_model_filename="model.gguf")
    assert status["current"]["family"] == "ik_llama"
    assert len(status["available_runtimes"]) >= 1
    assert "memory_loading" in status
    assert "large_model_override" in status
    assert status["switch"]["active"] is False


def test_runtime_status_with_switch_snapshot(store):
    _make_ik_llama_slot(store.runtimes_dir)
    snap = {"active": True, "target_family": "llama_cpp"}
    status = build_llama_runtime_status(store, switch_snapshot=snap)
    assert status["switch"]["active"] is True
    assert status["switch"]["target_family"] == "llama_cpp"


# -- Async: install_llama_runtime_bundle --


@pytest.mark.anyio
async def test_install_litert_skips_rsync(tmp_path):
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "runtime.json").write_text(json.dumps({"family": "litert"}), encoding="utf-8")
    result = await install_llama_runtime_bundle(install_dir, bundle_dir)
    assert result["ok"] is True
    assert result["reason"] == "litert_no_rsync_needed"


# -- Async: ensure_compatible_runtime --


@pytest.mark.anyio
async def test_ensure_compatible_runtime_already_compatible(store):
    write_llama_runtime_bundle_marker(store.install_dir, {"family": "ik_llama"})
    switched, reason = await ensure_compatible_runtime(store)
    assert switched is False
    assert reason == "compatible"


@pytest.mark.anyio
async def test_ensure_compatible_runtime_no_slot_available(tmp_path):
    install_dir = tmp_path / "llama"
    install_dir.mkdir()
    runtimes_dir = tmp_path / "runtimes"
    runtimes_dir.mkdir()
    write_llama_runtime_bundle_marker(install_dir, {"family": "ik_llama"})
    pi4_store = RuntimeStoreConfig(
        runtimes_dir=runtimes_dir,
        install_dir=install_dir,
        settings_path=tmp_path / "settings.json",
        device_class="pi4-4gb",
        total_memory_bytes=4 * 1024**3,
    )
    switched, reason = await ensure_compatible_runtime(pi4_store)
    assert switched is False
    assert reason == "slot_unavailable"
