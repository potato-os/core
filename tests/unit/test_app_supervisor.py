from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest


# ── Manifest parsing ────────────────────────────────────────────────


def test_manifest_parses_valid_app_json(tmp_path: Path):
    from core.app_manifest import AppManifest

    app_dir = tmp_path / "myapp"
    app_dir.mkdir()
    manifest_path = app_dir / "app.json"
    manifest_path.write_text(json.dumps({
        "id": "myapp",
        "name": "My App",
        "version": "1.0.0",
        "entry": "main.py",
        "critical": True,
        "has_ui": False,
        "ui_path": "",
        "socket": "myapp.sock",
        "inferno": False,
        "description": "A test app",
    }))

    manifest = AppManifest.from_file(manifest_path)

    assert manifest.id == "myapp"
    assert manifest.name == "My App"
    assert manifest.version == "1.0.0"
    assert manifest.entry == "main.py"
    assert manifest.critical is True
    assert manifest.has_ui is False
    assert manifest.socket == "myapp.sock"
    assert manifest.inferno is False


def test_manifest_validate_returns_errors_for_missing_fields(tmp_path: Path):
    from core.app_manifest import AppManifest

    app_dir = tmp_path / "bad"
    app_dir.mkdir()
    manifest_path = app_dir / "app.json"
    manifest_path.write_text(json.dumps({"id": "bad"}))

    manifest = AppManifest.from_file(manifest_path)
    errors = manifest.validate()

    assert len(errors) > 0
    assert any("name" in e for e in errors)
    assert any("entry" in e for e in errors)


def test_manifest_from_file_raises_on_invalid_json(tmp_path: Path):
    from core.app_manifest import AppManifest

    app_dir = tmp_path / "broken"
    app_dir.mkdir()
    manifest_path = app_dir / "app.json"
    manifest_path.write_text("not json at all")

    with pytest.raises(ValueError, match="invalid"):
        AppManifest.from_file(manifest_path)


def test_manifest_from_file_raises_on_missing_file(tmp_path: Path):
    from core.app_manifest import AppManifest

    with pytest.raises(FileNotFoundError):
        AppManifest.from_file(tmp_path / "nonexistent" / "app.json")


# ── App discovery ───────────────────────────────────────────────────


def test_discover_apps_finds_valid_manifests(tmp_path: Path):
    from core.app_manifest import discover_apps

    for app_id in ("alpha", "beta"):
        d = tmp_path / app_id
        d.mkdir()
        (d / "app.json").write_text(json.dumps({
            "id": app_id,
            "name": app_id.title(),
            "version": "0.1.0",
            "entry": "main.py",
            "critical": False,
            "has_ui": False,
            "ui_path": "",
            "socket": f"{app_id}.sock",
            "inferno": False,
            "description": f"App {app_id}",
        }))

    manifests = discover_apps(tmp_path)

    assert len(manifests) == 2
    ids = {m.id for m in manifests}
    assert ids == {"alpha", "beta"}


def test_discover_apps_skips_invalid_manifests(tmp_path: Path):
    from core.app_manifest import discover_apps

    good = tmp_path / "good"
    good.mkdir()
    (good / "app.json").write_text(json.dumps({
        "id": "good",
        "name": "Good App",
        "version": "0.1.0",
        "entry": "main.py",
        "critical": False,
        "has_ui": False,
        "ui_path": "",
        "socket": "good.sock",
        "inferno": False,
        "description": "Valid",
    }))

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "app.json").write_text("broken json")

    manifests = discover_apps(tmp_path)

    assert len(manifests) == 1
    assert manifests[0].id == "good"


def test_discover_apps_returns_empty_for_no_apps(tmp_path: Path):
    from core.app_manifest import discover_apps

    manifests = discover_apps(tmp_path)

    assert manifests == []


def test_discover_apps_skips_dirs_without_manifest(tmp_path: Path):
    from core.app_manifest import discover_apps

    (tmp_path / "no_manifest").mkdir()
    (tmp_path / "no_manifest" / "main.py").write_text("pass")

    manifests = discover_apps(tmp_path)

    assert manifests == []


def test_skeleton_app_manifest_is_valid():
    from core.app_manifest import AppManifest

    manifest_path = Path(__file__).parent.parent.parent / "apps" / "skeleton" / "app.json"
    manifest = AppManifest.from_file(manifest_path)
    errors = manifest.validate()

    assert errors == [], f"Skeleton manifest has validation errors: {errors}"
    assert manifest.id == "skeleton"
    assert manifest.critical is True


# ── App instance + restart logic ────────────────────────────────────


def _make_manifest(**overrides):
    from core.app_manifest import AppManifest
    defaults = {
        "id": "test", "name": "Test", "version": "0.1.0", "entry": "main.py",
        "critical": True, "has_ui": False, "ui_path": "", "socket": "test.sock",
        "inferno": False, "description": "test",
    }
    defaults.update(overrides)
    return AppManifest(**defaults)


class _FakeProcess:
    def __init__(self, *, returncode=None, pid=9999):
        self.returncode = returncode
        self.pid = pid
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    async def wait(self):
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode


def test_app_instance_starts_with_correct_defaults():
    from core.app_supervisor import AppInstance

    inst = AppInstance(manifest=_make_manifest())

    assert inst.status == "discovered"
    assert inst.process is None
    assert inst.consecutive_failures == 0
    assert inst.last_started_at is None
    assert inst.next_restart_at is None


def test_compute_backoff_increases_exponentially():
    from core.app_supervisor import compute_restart_backoff

    assert compute_restart_backoff(0) == 1.0
    assert compute_restart_backoff(1) == 2.0
    assert compute_restart_backoff(2) == 4.0
    assert compute_restart_backoff(3) == 8.0
    assert compute_restart_backoff(10) == 60.0  # capped


def test_is_crash_loop_detects_rapid_failures():
    from core.app_supervisor import is_crash_loop

    now = time.monotonic()
    crash_times = [now - i * 10 for i in range(5)]  # 5 crashes within 50s
    assert is_crash_loop(crash_times, window_s=120, threshold=5) is True


def test_is_crash_loop_returns_false_for_spread_failures():
    from core.app_supervisor import is_crash_loop

    now = time.monotonic()
    crash_times = [now - i * 60 for i in range(5)]  # 5 crashes over 5 minutes
    assert is_crash_loop(crash_times, window_s=120, threshold=5) is False


def test_is_crash_loop_returns_false_for_few_failures():
    from core.app_supervisor import is_crash_loop

    now = time.monotonic()
    crash_times = [now - 1, now - 2]  # only 2 crashes
    assert is_crash_loop(crash_times, window_s=120, threshold=5) is False


def test_build_app_env_includes_required_vars(tmp_path: Path):
    from core.app_supervisor import build_app_env

    manifest = _make_manifest(id="myapp", socket="myapp.sock", inferno=True)
    env = build_app_env(
        manifest,
        inferno_url="http://127.0.0.1:8080",
        socket_dir=tmp_path / "sockets",
        data_dir=tmp_path / "data" / "myapp",
        apps_dir=tmp_path / "apps" / "myapp",
    )

    assert env["POTATO_APP_ID"] == "myapp"
    assert env["POTATO_INFERNO_URL"] == "http://127.0.0.1:8080"
    assert "myapp.sock" in env["POTATO_SOCKET_PATH"]
    assert env["POTATO_DATA_DIR"] == str(tmp_path / "data" / "myapp")


def test_build_app_env_omits_inferno_url_when_not_needed(tmp_path: Path):
    from core.app_supervisor import build_app_env

    manifest = _make_manifest(id="nomodel", inferno=False)
    env = build_app_env(
        manifest,
        inferno_url="http://127.0.0.1:8080",
        socket_dir=tmp_path,
        data_dir=tmp_path,
        apps_dir=tmp_path,
    )

    assert "POTATO_INFERNO_URL" not in env


# ── Unix socket IPC ─────────────────────────────────────────────────


def test_health_check_over_real_unix_socket():
    import tempfile

    from core.app_supervisor import check_app_health

    # Use /tmp directly — macOS AF_UNIX has 104-char path limit
    sock_dir = Path(tempfile.mkdtemp(prefix="pt_"))
    socket_path = sock_dir / "t.sock"
    manifest = _make_manifest(socket="t.sock")

    async def run():
        from core.app_supervisor import AppInstance

        instance = AppInstance(manifest=manifest)

        async def handle_client(reader, writer):
            async for line in reader:
                msg = json.loads(line.strip())
                if msg.get("type") == "health_check":
                    writer.write(json.dumps({"type": "health", "status": "ok"}).encode() + b"\n")
                    await writer.drain()
                    break
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handle_client, path=str(socket_path))
        async with server:
            result = await check_app_health(instance, sock_dir)
            assert result is True

    try:
        asyncio.run(run())
    finally:
        if socket_path.exists():
            socket_path.unlink()
        sock_dir.rmdir()


def test_health_check_returns_false_when_socket_missing(tmp_path: Path):
    from core.app_supervisor import AppInstance, check_app_health

    manifest = _make_manifest(socket="missing.sock")
    instance = AppInstance(manifest=manifest)

    result = asyncio.run(check_app_health(instance, tmp_path))

    assert result is False
