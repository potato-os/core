from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from core.main import _runtime_env, create_app, ensure_models_state, get_runtime, save_models_state


async def _healthy_true(_runtime):
    return True


def test_toggle_download_countdown_endpoint(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        off = client.post("/internal/download-countdown", json={"enabled": False})
        on = client.post("/internal/download-countdown", json={"enabled": True})

    assert off.status_code == 200
    assert off.json()["countdown_enabled"] is False
    assert off.json()["updated"] is True
    assert off.json()["reason"] == "countdown_updated"
    assert on.status_code == 200
    assert on.json()["countdown_enabled"] is True
    assert on.json()["updated"] is True
    assert on.json()["reason"] == "countdown_updated"


def test_switch_llama_runtime_by_family_installs_slot_and_reports_status(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    # Create a runtime slot at runtimes/ik_llama/
    slot = runtime.base_dir / "runtimes" / "ik_llama"
    (slot / "bin").mkdir(parents=True)
    (slot / "lib").mkdir(parents=True)
    (slot / "bin" / "llama-server").write_text("binary", encoding="utf-8")
    (slot / "runtime.json").write_text(
        json.dumps({"family": "ik_llama", "commit": "abc123", "profile": "pi5-opt"}),
        encoding="utf-8",
    )

    install_calls: list[str] = []

    async def _fake_install(_runtime, bundle_dir):
        install_calls.append(str(bundle_dir))
        install_dir = _runtime.base_dir / "llama"
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / "bin").mkdir(exist_ok=True)
        (install_dir / "lib").mkdir(exist_ok=True)
        (install_dir / "bin" / "llama-server").write_text("installed", encoding="utf-8")
        return {"ok": True, "install_dir": str(install_dir)}

    async def _fake_restart(_app):
        return False, "no_running_process"

    monkeypatch.setattr("core.main.install_llama_runtime_bundle", _fake_install)
    monkeypatch.setattr("core.main.restart_managed_llama_process", _fake_restart)

    with TestClient(app) as client:
        response = client.post("/internal/llama-runtime/switch", json={"family": "ik_llama"})
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["switched"] is True
    assert body["family"] == "ik_llama"
    assert install_calls == [str(slot)]

    status_body = status.json()
    assert "llama_runtime" in status_body
    marker_path = runtime.base_dir / "llama" / ".potato-llama-runtime-bundle.json"
    assert marker_path.exists()
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["family"] == "ik_llama"


def test_switch_llama_runtime_rejects_incompatible_family_on_pi4(runtime, monkeypatch):
    """POST /internal/llama-runtime/switch must reject ik_llama on Pi 4 server-side."""
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    # Create both runtime slots
    for family in ("ik_llama", "llama_cpp"):
        slot = runtime.base_dir / "runtimes" / family
        (slot / "bin").mkdir(parents=True)
        (slot / "lib").mkdir(parents=True)
        (slot / "bin" / "llama-server").write_text("binary", encoding="utf-8")
        (slot / "runtime.json").write_text(
            json.dumps({"family": family, "commit": "abc123"}),
            encoding="utf-8",
        )

    monkeypatch.setattr("core.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.setattr("core.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("core.routes.runtime._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.setattr("core.routes.runtime._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)

    with TestClient(app) as client:
        response = client.post("/internal/llama-runtime/switch", json={"family": "ik_llama"})

    assert response.status_code == 409
    body = response.json()
    assert body["switched"] is False
    assert body["reason"] == "incompatible_runtime"


def test_set_llama_memory_loading_mode_persists_and_restarts(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    async def _fake_restart(_app):
        return True, "terminated_running_process"

    monkeypatch.setattr("core.main.restart_managed_llama_process", _fake_restart)

    with TestClient(app) as client:
        response = client.post(
            "/internal/llama-runtime/memory-loading",
            json={"mode": "full_ram"},
        )
        status = client.get("/status")

    assert response.status_code == 200
    body = response.json()
    assert body["updated"] is True
    assert body["memory_loading"]["mode"] == "full_ram"
    assert body["memory_loading"]["no_mmap_env"] == "1"
    assert body["restarted"] is True

    status_body = status.json()
    assert status_body["llama_runtime"]["memory_loading"]["mode"] == "full_ram"
    assert status_body["llama_runtime"]["memory_loading"]["no_mmap_env"] == "1"


def test_set_large_model_override_persists_without_restart(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr("core.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.5")
    monkeypatch.setattr("core.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("core.runtime_state.get_large_model_warn_threshold_bytes", lambda: 1)
    with runtime.model_path.open("wb") as handle:
        handle.seek((6 * 1024 * 1024 * 1024) - 1)
        handle.write(b"x")

    with TestClient(app) as client:
        before = client.get("/status")
        response = client.post(
            "/internal/compatibility/large-model-override",
            json={"enabled": True},
        )
        after = client.get("/status")

    assert before.status_code == 200
    assert before.json()["compatibility"]["warnings"]
    assert response.status_code == 200
    body = response.json()
    assert body["updated"] is True
    assert body["override"]["enabled"] is True
    assert body["compatibility"]["override_enabled"] is True
    assert body["compatibility"]["warnings"] == []
    assert after.status_code == 200
    after_body = after.json()
    assert after_body["compatibility"]["override_enabled"] is True
    assert after_body["compatibility"]["warnings"] == []
    assert after_body["llama_runtime"]["large_model_override"]["enabled"] is True


def test_switch_to_litert_works_with_litertlm_model(runtime, monkeypatch):
    """POST /internal/llama-runtime/switch to litert succeeds when active model is .litertlm."""
    runtime.enable_orchestrator = True
    # Set a .litertlm model as active
    runtime.model_path = runtime.base_dir / "models" / "gemma-4-E2B-it.litertlm"
    runtime.model_path.write_bytes(b"litertlm")
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    # Write models state with a .litertlm active model
    runtime.models_state_path.write_text(json.dumps({
        "version": 1, "countdown_enabled": True, "default_model_downloaded_once": False,
        "active_model_id": "litert-model", "default_model_id": "default",
        "models": [
            {"id": "litert-model", "filename": "gemma-4-E2B-it.litertlm",
             "source_url": "https://example.com/gemma.litertlm", "source_type": "url",
             "status": "ready", "error": None},
        ],
    }), encoding="utf-8")

    # Create litert slot (no bin/llama-server, just runtime.json)
    slot = runtime.base_dir / "runtimes" / "litert"
    slot.mkdir(parents=True)
    (slot / "runtime.json").write_text(
        json.dumps({"family": "litert", "runtime_type": "litert_adapter", "version": "0.10.1"}),
        encoding="utf-8",
    )

    async def _fake_install(_runtime, bundle_dir):
        install_dir = _runtime.base_dir / "llama"
        install_dir.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "reason": "litert_no_rsync_needed", "install_dir": str(install_dir)}

    async def _fake_restart(_app):
        return False, "no_running_process"

    monkeypatch.setattr("core.main.install_llama_runtime_bundle", _fake_install)
    monkeypatch.setattr("core.main.restart_managed_llama_process", _fake_restart)
    monkeypatch.setattr("core.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.0")
    monkeypatch.setattr("core.runtime_state._detect_total_memory_bytes", lambda: 16 * 1024 * 1024 * 1024)
    monkeypatch.setattr("core.routes.runtime._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.0")
    monkeypatch.setattr("core.routes.runtime._detect_total_memory_bytes", lambda: 16 * 1024 * 1024 * 1024)

    with TestClient(app) as client:
        response = client.post("/internal/llama-runtime/switch", json={"family": "litert"})

    assert response.status_code == 200
    body = response.json()
    assert body["switched"] is True
    assert body["family"] == "litert"


def test_switch_to_litert_rejected_for_gguf_model(runtime, monkeypatch):
    """POST /internal/llama-runtime/switch to litert must fail when active model is GGUF."""
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    slot = runtime.base_dir / "runtimes" / "litert"
    slot.mkdir(parents=True)
    (slot / "runtime.json").write_text(
        json.dumps({"family": "litert"}), encoding="utf-8",
    )

    monkeypatch.setattr("core.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.0")
    monkeypatch.setattr("core.runtime_state._detect_total_memory_bytes", lambda: 16 * 1024 * 1024 * 1024)
    monkeypatch.setattr("core.routes.runtime._read_pi_device_model_name", lambda: "Raspberry Pi 5 Model B Rev 1.0")
    monkeypatch.setattr("core.routes.runtime._detect_total_memory_bytes", lambda: 16 * 1024 * 1024 * 1024)

    with TestClient(app) as client:
        response = client.post("/internal/llama-runtime/switch", json={"family": "litert"})

    assert response.status_code == 409
    assert response.json()["reason"] == "model_format_incompatible"


def test_switch_to_litert_rejected_on_pi4(runtime, monkeypatch):
    """POST /internal/llama-runtime/switch to litert rejected on Pi 4."""
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    slot = runtime.base_dir / "runtimes" / "litert"
    slot.mkdir(parents=True)
    (slot / "runtime.json").write_text(
        json.dumps({"family": "litert"}), encoding="utf-8",
    )

    monkeypatch.setattr("core.runtime_state._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.setattr("core.runtime_state._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)
    monkeypatch.setattr("core.routes.runtime._read_pi_device_model_name", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    monkeypatch.setattr("core.routes.runtime._detect_total_memory_bytes", lambda: 8 * 1024 * 1024 * 1024)

    with TestClient(app) as client:
        response = client.post("/internal/llama-runtime/switch", json={"family": "litert"})

    assert response.status_code == 409
    body = response.json()
    assert body["switched"] is False
    assert body["reason"] == "incompatible_runtime"


def test_power_calibration_sample_fit_and_reset_persist_in_status(runtime, monkeypatch):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    power_state = {"value": 4.0}

    def _fake_power_snapshot(*, now_unix=None):
        power_state["value"] += 1.5
        watts = power_state["value"]
        return {
            "available": True,
            "updated_at_unix": now_unix,
            "total_watts": watts,
            "rails_paired_count": 2,
            "method": "pmic_read_adc",
            "label": "PMIC rails estimate",
            "disclaimer": "test",
            "error": None,
        }

    monkeypatch.setattr("core.main._build_power_estimate_snapshot", _fake_power_snapshot)

    with TestClient(app) as client:
        s1 = client.post("/internal/power-calibration/sample", json={"wall_watts": 6.5})
        s2 = client.post("/internal/power-calibration/sample", json={"wall_watts": 10.1})
        fit = client.post("/internal/power-calibration/fit")
        status = client.get("/status")
        reset = client.post("/internal/power-calibration/reset")
        status_after_reset = client.get("/status")

    assert s1.status_code == 200
    assert s1.json()["captured"] is True
    assert s2.status_code == 200
    assert s2.json()["captured"] is True
    assert fit.status_code == 200
    assert fit.json()["updated"] is True
    assert fit.json()["calibration"]["mode"] == "custom"

    power = status.json()["system"]["power_estimate"]
    assert "raw_total_watts" in power
    assert "adjusted_total_watts" in power
    assert power["calibration"]["mode"] == "custom"
    assert power["calibration"]["sample_count"] >= 2

    assert reset.status_code == 200
    assert reset.json()["updated"] is True
    assert reset.json()["calibration"]["mode"] == "default"
    assert status_after_reset.json()["system"]["power_estimate"]["calibration"]["mode"] == "default"


def test_power_calibration_fit_requires_two_samples(runtime):
    runtime.enable_orchestrator = True
    app = create_app(runtime=runtime, enable_orchestrator=True)
    app.dependency_overrides[get_runtime] = lambda: runtime

    with TestClient(app) as client:
        response = client.post("/internal/power-calibration/fit")

    assert response.status_code == 400
    assert response.json()["reason"] == "insufficient_samples"



