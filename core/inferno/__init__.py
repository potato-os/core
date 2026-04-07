"""Inferno -- Potato OS inference layer.

Inferno owns the runtime-facing side of inference: backend proxying,
model family classification, and adapter processes.  Everything between
"Potato says run this model" and "here is the OpenAI-compatible response"
lives here.

Boundary contract
-----------------
Potato (caller) provides:
    - Base URL of the active inference runtime
    - Chat backend mode ("llama" | "fake" | "auto")
    - Model filename and optional source URL (for projector resolution)
    - POTATO_MODEL_PATH env var (for LiteRT adapter startup)
    - Hardware/device/OS inputs (memory, device class, runtime binaries)

Inferno (this package) provides:
    - BackendProxyError            exception for proxy failures
    - BackendResponse              dataclass for HTTP responses (body or stream)
    - ChatCompletionRepository     protocol for backend implementations
    - LlamaCppRepository           real llama.cpp HTTP proxy
    - FakeLlamaRepository          fake backend for dev/test
    - ChatRepositoryManager        dispatch to named backends
    - is_qwen35_filename           detect Qwen 3.5 model files
    - is_gemma4_filename           detect Gemma 4 model files
    - projector_repo_for_model     resolve HuggingFace projector repo
    - recommended_runtime_for_model  preferred runtime family for a model
    - litert_adapter               standalone FastAPI app (core.inferno.litert_adapter:app)

Inferno does NOT import from core.model_state, core.runtime_state,
core.settings, core.deps, or any apps/ code.  The dependency arrow
points one way: Potato -> Inferno, never Inferno -> Potato.
"""

from .backend import (
    BackendProxyError,
    BackendResponse,
    ChatCompletionRepository,
    ChatRepositoryManager,
    FakeLlamaRepository,
    LlamaCppRepository,
)
from .model_families import (
    is_gemma4_filename,
    is_qwen35_filename,
    projector_repo_for_model,
    recommended_runtime_for_model,
)

__all__ = [
    "BackendProxyError",
    "BackendResponse",
    "ChatCompletionRepository",
    "ChatRepositoryManager",
    "FakeLlamaRepository",
    "LlamaCppRepository",
    "is_gemma4_filename",
    "is_qwen35_filename",
    "projector_repo_for_model",
    "recommended_runtime_for_model",
]
