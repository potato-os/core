"""FastAPI dependency functions shared across all routes."""

from __future__ import annotations

from fastapi import Request

try:
    from app.runtime_state import RuntimeConfig
    from app.repositories.chat_repository import ChatRepositoryManager
except ModuleNotFoundError:
    from runtime_state import RuntimeConfig  # type: ignore[no-redef]
    from repositories.chat_repository import ChatRepositoryManager  # type: ignore[no-redef]


def get_runtime(request: Request) -> RuntimeConfig:
    return request.app.state.runtime


def get_chat_repository(request: Request) -> ChatRepositoryManager:
    return request.app.state.chat_repository
