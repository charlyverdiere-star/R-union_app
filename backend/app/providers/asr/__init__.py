"""Sélection du moteur de reconnaissance vocale selon la configuration."""

from __future__ import annotations

from ...config import Settings
from .base import (
    AsrCapabilities,
    AsrProvider,
    RawSegment,
    TranscriptionError,
    TranscriptionResult,
)

__all__ = [
    "AsrCapabilities",
    "AsrProvider",
    "RawSegment",
    "TranscriptionError",
    "TranscriptionResult",
    "build_asr_provider",
]


def build_asr_provider(settings: Settings) -> AsrProvider:
    provider = settings.asr_provider
    if provider == "deepgram":
        from .deepgram import DeepgramProvider

        return DeepgramProvider(settings.deepgram_api_key, settings.deepgram_model)
    if provider == "assemblyai":
        from .assemblyai import AssemblyAIProvider

        return AssemblyAIProvider(settings.assemblyai_api_key)
    if provider == "openai":
        from .openai_whisper import OpenAIWhisperProvider

        return OpenAIWhisperProvider(settings.openai_api_key, settings.openai_asr_model)
    if provider == "local-whisper":
        from .local_whisper import LocalWhisperProvider

        return LocalWhisperProvider(
            settings.whisper_model, settings.whisper_device, settings.whisper_compute_type
        )
    if provider == "test-replay":
        from .replay import ReplayProvider

        return ReplayProvider(settings.replay_transcript_path)
    raise TranscriptionError(f"Moteur de transcription inconnu : {provider}")
