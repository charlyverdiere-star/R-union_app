"""Sélection du modèle de langage selon la configuration."""

from __future__ import annotations

from ...config import Settings
from .base import LlmError, LlmProvider, LlmResult, Usage

__all__ = ["LlmError", "LlmProvider", "LlmResult", "Usage", "build_llm_provider"]


def build_llm_provider(settings: Settings) -> LlmProvider:
    if settings.llm_provider == "anthropic":
        from .anthropic_client import AnthropicProvider

        return AnthropicProvider(
            settings.anthropic_api_key, settings.llm_model, settings.llm_effort
        )
    if settings.llm_provider == "test-replay":
        from .replay import ReplayLlmProvider

        return ReplayLlmProvider(settings.replay_llm_dir)
    raise LlmError(f"Fournisseur d'analyse inconnu : {settings.llm_provider}")
