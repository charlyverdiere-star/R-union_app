"""Configuration de l'application, lue depuis l'environnement ou un fichier .env.

Aucun secret n'est écrit en dur : toutes les clés proviennent de l'environnement
et ne sont jamais renvoyées au navigateur (voir `Settings.public_state`).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AsrProvider = Literal["deepgram", "assemblyai", "openai", "local-whisper", "test-replay"]
LlmProvider = Literal["anthropic", "test-replay"]
ArchiveFormat = Literal["original", "flac", "wav"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("MEETING_ASSISTANT_ENV_FILE", PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Serveur ---
    host: str = "127.0.0.1"
    port: int = 8420
    data_dir: Path = PROJECT_ROOT / "data"
    auth_token: str = ""

    # --- Transcription ---
    asr_provider: AsrProvider = "deepgram"
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-3"
    assemblyai_api_key: str = ""
    openai_api_key: str = ""
    openai_asr_model: str = "whisper-1"
    whisper_model: str = "large-v3"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    asr_language: str = "fr"
    asr_diarization: bool = True
    asr_vocabulary: str = ""

    # --- Analyse ---
    llm_provider: LlmProvider = "anthropic"
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"

    # --- Audio ---
    archive_format: ArchiveFormat = "original"
    chunk_target_seconds: int = Field(default=600, ge=60, le=3600)
    max_upload_mb: int = Field(default=2048, ge=1)

    # --- Confidentialité ---
    retention_days: int = Field(default=0, ge=0)

    # --- Chemins de test (fournisseurs « test-replay ») ---
    replay_transcript_path: Path | None = None
    replay_llm_dir: Path | None = None

    @field_validator("data_dir", mode="after")
    @classmethod
    def _absolute_data_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @property
    def vocabulary_terms(self) -> list[str]:
        return [t.strip() for t in self.asr_vocabulary.split(",") if t.strip()]

    # ------------------------------------------------------------------
    # Diagnostic de configuration
    # ------------------------------------------------------------------
    def asr_problem(self) -> str | None:
        """Retourne la raison pour laquelle la transcription ne peut pas tourner."""
        missing = {
            "deepgram": ("DEEPGRAM_API_KEY", self.deepgram_api_key),
            "assemblyai": ("ASSEMBLYAI_API_KEY", self.assemblyai_api_key),
            "openai": ("OPENAI_API_KEY", self.openai_api_key),
        }
        if self.asr_provider in missing:
            name, value = missing[self.asr_provider]
            if not value:
                return f"{name} n'est pas renseigné dans le fichier .env."
        if self.asr_provider == "local-whisper":
            try:
                import faster_whisper  # noqa: F401
            except ImportError:
                return (
                    "Le mode local nécessite faster-whisper : "
                    "installez-le avec « pip install -e .[local] »."
                )
        if self.asr_provider == "test-replay" and not self.replay_transcript_path:
            return "REPLAY_TRANSCRIPT_PATH n'est pas renseigné."
        return None

    def llm_problem(self) -> str | None:
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            return "ANTHROPIC_API_KEY n'est pas renseigné dans le fichier .env."
        if self.llm_provider == "test-replay" and not self.replay_llm_dir:
            return "REPLAY_LLM_DIR n'est pas renseigné."
        return None

    def public_state(self) -> dict:
        """État transmis au navigateur. Ne contient aucune clé, seulement des
        booléens et des noms de fournisseurs, afin que l'interface puisse
        avertir l'utilisateur avant qu'il ne lance un traitement."""
        return {
            "asr_provider": self.asr_provider,
            "asr_ready": self.asr_problem() is None,
            "asr_problem": self.asr_problem(),
            "asr_is_local": self.asr_provider == "local-whisper",
            "asr_is_test": self.asr_provider == "test-replay",
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_ready": self.llm_problem() is None,
            "llm_problem": self.llm_problem(),
            "llm_is_test": self.llm_provider == "test-replay",
            "language": self.asr_language,
            "diarization": self.asr_diarization,
            "archive_format": self.archive_format,
            "retention_days": self.retention_days,
            "max_upload_mb": self.max_upload_mb,
            "auth_required": bool(self.auth_token),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Utilisé par les tests pour recharger la configuration."""
    get_settings.cache_clear()
