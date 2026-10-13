from __future__ import annotations

from ..config import Settings
from .base import LLMBlocked, LLMClient, LLMError, LLMRequest, LLMResponse
from .fake import FakeLLMClient
from .router import ModelRouter
from .tokenfactory import TokenFactoryClient

__all__ = [
    "FakeLLMClient",
    "LLMBlocked",
    "LLMClient",
    "LLMError",
    "LLMRequest",
    "LLMResponse",
    "ModelRouter",
    "TokenFactoryClient",
    "build_llm_client",
]


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_offline:
        return FakeLLMClient()
    return TokenFactoryClient(settings.nebius_api_key or "", settings.nebius_base_url)
