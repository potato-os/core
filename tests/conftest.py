from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import RuntimeConfig, create_app, get_runtime


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeConfig:
    base = tmp_path / "potato"
    model_dir = base / "models"
    state_dir = base / "state"
    model_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    return RuntimeConfig(
        base_dir=base,
        model_path=model_dir / "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
        download_state_path=state_dir / "download.json",
        models_state_path=state_dir / "models.json",
        llama_base_url="http://llama.test:8080",
        chat_backend_mode="auto",
        web_port=1983,
        llama_port=8080,
        enable_orchestrator=False,
    )


@pytest.fixture
def client(runtime: RuntimeConfig) -> TestClient:
    app = create_app(runtime=runtime, enable_orchestrator=False)
    app.dependency_overrides[get_runtime] = lambda: runtime
    with TestClient(app) as c:
        yield c
