"""Interface commune aux moteurs de reconnaissance vocale.

Chaque moteur déclare ses capacités ; le pipeline s'y adapte. En particulier,
un moteur capable d'avaler un fichier de plusieurs heures n'est jamais
découpe : cela evite de casser l'identification des locuteurs, qui n'est pas
cohérente d'un segment à l'autre.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class TranscriptionError(RuntimeError):
    """Échec de transcription. `retryable` indique si un nouvel essai a du sens."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class AsrCapabilities:
    diarization: bool = False
    #: durée maximale acceptee par appel ; None = pas de limite pratique
    max_seconds: float | None = None
    #: taille maximale acceptee par appel, en octets ; None = pas de limite
    max_bytes: int | None = None
    #: True si le traitement se fait intégralement sur la machine locale
    local: bool = False


@dataclass
class RawSegment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None
    low_confidence: bool = False


@dataclass
class TranscriptionResult:
    segments: list[RawSegment]
    language: str
    model: str
    warnings: list[str] = field(default_factory=list)


class AsrProvider(ABC):
    """Contrat implemente par chaque moteur."""

    name: str = "abstrait"
    capabilities: AsrCapabilities = AsrCapabilities()

    @abstractmethod
    def transcribe(
        self,
        audio: Path,
        *,
        language: str,
        vocabulary: list[str],
        diarization: bool,
    ) -> TranscriptionResult:
        """Transcrit un fichier audio. Les horodatages sont relatifs au fichier."""

    def describe(self) -> str:
        return self.name
