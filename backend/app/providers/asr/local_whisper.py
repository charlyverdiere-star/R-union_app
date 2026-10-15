"""Whisper exécuté localement via faster-whisper.

Aucune donnée ne quitte la machine. En contrepartie, le modèle « large-v3 »
reste lent sur un processur seul (comptez, selon la machine, de une à trois
fois la durée de la réunion) ; un GPU change complètement la donne. Voir la
comparaison des moteurs dans le README.

Ce moteur ne fait pas d'identification des locuteurs : elle demanderait une
bibliotheque de diarisation supplementaire et un modèle additionnel.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import AsrCapabilities, AsrProvider, RawSegment, TranscriptionError, TranscriptionResult
from .openai_whisper import is_low_confidence


class LocalWhisperProvider(AsrProvider):
    name = "local-whisper"
    capabilities = AsrCapabilities(
        diarization=False, max_seconds=None, max_bytes=None, local=True
    )

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "auto",
        compute_type: str = "auto",
    ):
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def describe(self) -> str:
        return f"faster-whisper/{self._model_size}"

    def _load(self):
        """Charge le modèle au premier usage (téléchargement initial possible)."""
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError(
                "Le mode local nécessite faster-whisper. Installez-le avec "
                "« pip install -e .[local] ».",
                retryable=False,
            ) from exc

        device = self._device
        compute_type = self._compute_type
        if device == "auto":
            device = "cuda" if _cuda_available() else "cpu"
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"

        try:
            self._model = WhisperModel(
                self._model_size,
                device=device,
                compute_type=compute_type,
                cpu_threads=max(1, (os.cpu_count() or 2)),
            )
        except Exception as exc:  # modèle introuvable, mémoire insuffisante...
            raise TranscriptionError(
                f"Le modèle Whisper « {self._model_size} » n'a pas pu être charge : {exc}",
                retryable=False,
            ) from exc
        return self._model

    def transcribe(
        self, audio: Path, *, language: str, vocabulary: list[str], diarization: bool
    ) -> TranscriptionResult:
        model = self._load()
        warnings: list[str] = []
        if diarization:
            warnings.append(
                "Le moteur local n'identifie pas les locuteurs : "
                "aucune phrase n'est attribuée à une personne."
            )

        try:
            segments_iter, info = model.transcribe(
                str(audio),
                language=None if language in ("", "auto") else language,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                beam_size=5,
                condition_on_previous_text=True,
                initial_prompt=", ".join(vocabulary[:200]) if vocabulary else None,
            )
        except Exception as exc:
            raise TranscriptionError(
                f"La transcription locale a échoué : {exc}", retryable=False
            ) from exc

        segments = [
            RawSegment(
                start=float(seg.start),
                end=float(seg.end),
                text=seg.text.strip(),
                confidence=_confidence(getattr(seg, "avg_logprob", None)),
                low_confidence=is_low_confidence(
                    getattr(seg, "avg_logprob", None), getattr(seg, "no_speech_prob", None)
                ),
            )
            for seg in segments_iter
            if seg.text and seg.text.strip()
        ]
        if not segments:
            warnings.append("Aucune parole n'a été détectée dans cet audio.")

        return TranscriptionResult(
            segments=segments,
            language=getattr(info, "language", None) or language,
            model=self.describe(),
            warnings=warnings,
        )


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _confidence(avg_logprob: float | None) -> float | None:
    if avg_logprob is None:
        return None
    import math

    return round(min(1.0, max(0.0, math.exp(avg_logprob))), 3)
