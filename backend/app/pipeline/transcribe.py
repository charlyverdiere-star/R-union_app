"""Étape B — transcription.

Le découpage n'intervient que si le moteur l'impose : garder le fichier entier
préserve la cohérence de l'identification des locuteurs et evite les coupures
de contexte. Le résultat de chaque segment est mis en cache sur le disque, ce
qui permet de reprendre une transcription interrompue sans tout refaire.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..models import Segment, Transcript
from ..providers.asr import (
    AsrProvider,
    RawSegment,
    TranscriptionError,
    TranscriptionResult,
    build_asr_provider,
)
from ..storage import MeetingPaths
from . import audio as audio_module

ProgressCallback = Callable[[float, str], None]

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (2.0, 8.0)


def run_transcription(
    paths: MeetingPaths,
    settings: Settings,
    *,
    duration: float,
    progress: ProgressCallback | None = None,
    provider: AsrProvider | None = None,
) -> Transcript:
    """Transcrit le fichier de travail et retourne une transcription complète."""
    provider = provider or build_asr_provider(settings)
    report = progress or (lambda *_: None)
    warnings: list[str] = []

    chunks = _plan(paths, provider, duration, settings, warnings)
    report(0.05, f"{len(chunks)} segment(s) à transcrire")

    collected: list[RawSegment] = []
    language = settings.asr_language
    for position, chunk in enumerate(chunks):
        cache = paths.chunk_transcript(chunk.index)
        if cache.exists():
            result = _load_cache(cache)
            report(
                0.05 + 0.9 * (position + 1) / len(chunks),
                f"Segment {position + 1}/{len(chunks)} déjà transcrit",
            )
        else:
            report(
                0.05 + 0.9 * position / len(chunks),
                f"Transcription du segment {position + 1}/{len(chunks)}",
            )
            result = _transcribe_with_retry(provider, chunk.path, settings)
            _save_cache(cache, result)

        language = result.language or language
        for warning in result.warnings:
            if warning not in warnings:
                warnings.append(warning)
        for segment in result.segments:
            collected.append(
                RawSegment(
                    start=segment.start + chunk.start,
                    end=segment.end + chunk.start,
                    text=segment.text,
                    speaker=segment.speaker,
                    confidence=segment.confidence,
                    low_confidence=segment.low_confidence,
                )
            )

    report(0.97, "Assemblage de la transcription")
    segments = _finalise(collected)

    if not segments:
        warnings.append(
            "Aucune parole n'a été détectée. Vérifiez que le microphone a bien "
            "capte du son pendant la réunion."
        )

    transcript = Transcript(
        language=language,
        provider=provider.name,
        model=provider.describe(),
        duration=duration,
        segments=segments,
        warnings=warnings,
        diarization=any(s.speaker for s in segments),
    )
    _add_quality_warnings(transcript)
    return transcript


# ---------------------------------------------------------------------------
# Découpage
# ---------------------------------------------------------------------------


def _plan(
    paths: MeetingPaths,
    provider: AsrProvider,
    duration: float,
    settings: Settings,
    warnings: list[str],
) -> list[audio_module.Chunk]:
    limit = provider.capabilities.max_seconds
    if limit is None or duration <= limit:
        return [audio_module.Chunk(index=0, path=paths.work_audio, start=0.0, end=duration)]

    target = min(float(settings.chunk_target_seconds), limit)
    if provider.capabilities.diarization and settings.asr_diarization:
        warnings.append(
            "L'audio a dû être découpé : l'identification des locuteurs n'est "
            "pas garantie cohérente d'un segment à l'autre."
        )
    return audio_module.prepare_chunks(paths.work_audio, paths.chunks_dir, duration, target)


# ---------------------------------------------------------------------------
# Appels au moteur
# ---------------------------------------------------------------------------


def _transcribe_with_retry(
    provider: AsrProvider, path: Path, settings: Settings
) -> TranscriptionResult:
    last: TranscriptionError | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return provider.transcribe(
                path,
                language=settings.asr_language,
                vocabulary=settings.vocabulary_terms,
                diarization=settings.asr_diarization and provider.capabilities.diarization,
            )
        except TranscriptionError as exc:
            last = exc
            if not exc.retryable or attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    raise last  # pragma: no cover - la boucle sort toujours avant


def _save_cache(path: Path, result: TranscriptionResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "language": result.language,
                "model": result.model,
                "warnings": result.warnings,
                "segments": [asdict(s) for s in result.segments],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def _load_cache(path: Path) -> TranscriptionResult:
    data = json.loads(path.read_text(encoding="utf-8"))
    return TranscriptionResult(
        segments=[RawSegment(**s) for s in data["segments"]],
        language=data.get("language", ""),
        model=data.get("model", ""),
        warnings=data.get("warnings", []),
    )


# ---------------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------------


def _finalise(raw: list[RawSegment]) -> list[Segment]:
    """Trie chronologiquement, écarte les doublons exacts et numérote."""
    ordered = sorted(raw, key=lambda s: (s.start, s.end))
    segments: list[Segment] = []
    previous_key: tuple[float, str] | None = None

    for item in ordered:
        text = item.text.strip()
        if not text:
            continue
        key = (round(item.start, 2), text)
        if key == previous_key:
            continue  # même phrase au même instant : doublon de recouvrement
        previous_key = key
        segments.append(
            Segment(
                id=f"s{len(segments) + 1:04d}",
                start=round(item.start, 3),
                end=round(max(item.end, item.start), 3),
                text=text,
                speaker=item.speaker,
                confidence=item.confidence,
                low_confidence=item.low_confidence,
            )
        )
    return segments


def _add_quality_warnings(transcript: Transcript) -> None:
    ratio = transcript.low_confidence_ratio
    if ratio > 0.25:
        transcript.warnings.append(
            f"{ratio:.0%} des passages ont une confiance faible : la qualité "
            "audio est probablement dégradée. Relisez la transcription avant "
            "de diffuser les documents."
        )
    elif ratio > 0.08:
        transcript.warnings.append(
            f"{ratio:.0%} des passages sont signalés comme incertains et sont "
            "mis en évidence dans la transcription."
        )

    covered = sum(s.end - s.start for s in transcript.segments)
    if transcript.duration > 60 and covered < transcript.duration * 0.35:
        transcript.warnings.append(
            "De longues plages sans parole ont été détectées : l'enregistrement "
            "contient peut-être des silences ou un problème de captation."
        )


def renumber(transcript: Transcript) -> Transcript:
    """Renumérote après une correction manuelle (suppression/ajout de segment)."""
    for index, segment in enumerate(transcript.segments, start=1):
        segment.id = f"s{index:04d}"
    return transcript
