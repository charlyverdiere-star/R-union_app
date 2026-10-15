"""Étape A — préparation de l'audio.

Trois responsabilités :
  1. produire le **livrable 1** : l'enregistrement original, sans réencodage
     destructif (un simple remux répare au passage un flux interrompu) ;
  2. dériver un fichier de travail normalise (16 kHz mono) pour la
     transcription — ce fichier n'est pas un livrable ;
  3. découper les longues réunions en segments, de preference sur des
     silences, en conservant l'ordre chronologique et les repérés temporels.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import storage

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

# Conteneur retenu pour chaque codec lors d'un remux sans perte.
LOSSLESS_REMUX = {
    "opus": ".ogg",
    "vorbis": ".ogg",
    "aac": ".m4a",
    "alac": ".m4a",
    "mp3": ".mp3",
    "flac": ".flac",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
    "pcm_f32le": ".wav",
}


class AudioError(RuntimeError):
    """Erreur exploitable par l'interface (message destiné à l'utilisateur)."""


@dataclass(frozen=True)
class AudioInfo:
    duration: float
    codec: str
    sample_rate: int
    channels: int
    bit_rate: int
    size_bytes: int
    format_name: str

    @property
    def is_silent_length(self) -> bool:
        return self.duration < 1.0


@dataclass(frozen=True)
class Chunk:
    index: int
    path: Path
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _run(cmd: list[str], *, timeout: int = 3600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:  # ffmpeg absent du système
        raise AudioError(
            "ffmpeg est introuvable. Installez-le puis relancez l'application "
            "(voir la section « Installation » du README)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError("Le traitement audio a dépassé le temps imparti.") from exc


# ---------------------------------------------------------------------------
# Analyse du fichier
# ---------------------------------------------------------------------------


def probe(path: Path, *, require_duration: bool = True) -> AudioInfo:
    """Lit les caractéristiques du fichier. Lève `AudioError` s'il est inexploitable.

    `require_duration=False` tolère une durée indéterminée : c'est le cas d'un
    enregistrement reçu du navigateur, dont la durée n'est connue qu'une fois
    le flux remis dans un conteneur propre.
    """
    if not path.exists() or path.stat().st_size == 0:
        raise AudioError("Le fichier audio est vide ou absent.")

    result = _run(
        [
            FFPROBE, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        timeout=300,
    )
    if result.returncode != 0:
        raise AudioError(
            "Le fichier audio n'a pas pu être lu. Il est peut-être corrompu ou "
            "dans un format non reconnu."
        )

    data = json.loads(result.stdout or "{}")
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise AudioError("Ce fichier ne contient aucune piste audio.")

    stream = streams[0]
    fmt = data.get("format", {})
    duration = _first_float(stream.get("duration"), fmt.get("duration"))
    if duration <= 0:
        # Flux écrit au fil de l'eau ou interrompu : on mesure en décodant.
        duration = _measured_duration(path)
    if duration <= 0 and require_duration:
        raise AudioError(
            "La durée de l'enregistrement n'a pas pu être déterminée : le "
            "fichier est probablement incomplet ou ne contient aucun son."
        )

    return AudioInfo(
        duration=duration,
        codec=stream.get("codec_name", "inconnu"),
        sample_rate=int(stream.get("sample_rate") or 0),
        channels=int(stream.get("channels") or 0),
        bit_rate=int(_first_float(stream.get("bit_rate"), fmt.get("bit_rate"))),
        size_bytes=path.stat().st_size,
        format_name=fmt.get("format_name", ""),
    )


def _first_float(*values) -> float:
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0.0


def _measured_duration(path: Path) -> float:
    """Durée réelle obtenue en décodant intégralement le flux.

    Indispensable pour un enregistrement produit par le navigateur : le
    conteneur WebM écrit au fil de l'eau n'annonce aucune durée et `ffprobe`
    répond « N/A ». L'option « -stats » est obligatoire — sans elle, le niveau
    de journalisation « error » supprime les lignes de progression d'où la
    durée est extraite.
    """
    result = _run(
        [FFMPEG, "-v", "error", "-stats", "-err_detect", "ignore_err",
         "-i", str(path), "-f", "null", "-"],
        timeout=1800,
    )
    matches = re.findall(r"time=(\d+):(\d+):(\d+\.?\d*)", result.stderr or "")
    if not matches:
        return 0.0
    hours, minutes, seconds = matches[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


# ---------------------------------------------------------------------------
# Livrable 1 : l'archive
# ---------------------------------------------------------------------------


def build_archive(
    incoming: Path,
    paths: storage.MeetingPaths,
    *,
    archive_format: str = "original",
    original_extension: str | None = None,
) -> tuple[Path, AudioInfo]:
    """Produit l'archive définitive de la réunion.

    En mode « original » (défaut) l'audio n'est jamais réencodé : on se contente
    d'un remux, qui recopie les paquets tels quels dans un conteneur propre et
    répare un enregistrement interrompu. Transcoder un flux déjà compresse
    (Opus, AAC) vers FLAC ou WAV n'ameliore pas la qualité et multiplie la
    taille par cinq environ : ce n'est propose que si l'utilisateur l'exigé.
    """
    paths.ensure()
    info = probe(incoming, require_duration=False)

    if archive_format == "flac":
        target = paths.archive(".flac")
        _encode(incoming, target, ["-c:a", "flac", "-compression_level", "8"])
    elif archive_format == "wav":
        target = paths.archive(".wav")
        _encode(incoming, target, ["-c:a", "pcm_s16le"])
    else:
        extension = LOSSLESS_REMUX.get(info.codec) or original_extension or ".mka"
        target = paths.archive(extension)
        if not _remux(incoming, target):
            # Dernier recours : conteneur Matroska, toujours en copie de flux.
            target = paths.archive(".mka")
            if not _remux(incoming, target):
                raise AudioError(
                    "L'enregistrement n'a pas pu être finalisé. Le fichier brut "
                    "reste disponible dans le dossier de la réunion."
                )

    return target, probe(target)


def _remux(source: Path, target: Path) -> bool:
    """Recopie les paquets audio sans les décoder. Retourne False en cas d'échec."""
    result = _run([
        FFMPEG, "-y", "-v", "error", "-err_detect", "ignore_err",
        "-i", str(source), "-map", "0:a:0", "-c:a", "copy", str(target),
    ])
    return result.returncode == 0 and target.exists() and target.stat().st_size > 0


def _encode(source: Path, target: Path, codec_args: list[str]) -> None:
    result = _run([
        FFMPEG, "-y", "-v", "error", "-err_detect", "ignore_err",
        "-i", str(source), "-map", "0:a:0", *codec_args, str(target),
    ])
    if result.returncode != 0 or not target.exists():
        raise AudioError(f"La conversion audio a échoué : {result.stderr.strip()[:300]}")


# ---------------------------------------------------------------------------
# Fichier de travail
# ---------------------------------------------------------------------------


def make_work_audio(archive: Path, target: Path) -> Path:
    """WAV 16 kHz mono : format attendu par tous les moteurs de reconnaissance."""
    target.parent.mkdir(parents=True, exist_ok=True)
    result = _run([
        FFMPEG, "-y", "-v", "error", "-err_detect", "ignore_err",
        "-i", str(archive), "-map", "0:a:0",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
    ])
    if result.returncode != 0 or not target.exists():
        raise AudioError(
            "La préparation de l'audio pour la transcription a échoué : "
            f"{result.stderr.strip()[:300]}"
        )
    return target


# ---------------------------------------------------------------------------
# Découpage des longues réunions
# ---------------------------------------------------------------------------


def detect_silences(path: Path, noise_db: int = -30, min_duration: float = 0.6) -> list[tuple[float, float]]:
    """Intervalles de silence repérés par ffmpeg, utilisés comme points de coupe."""
    result = _run([
        FFMPEG, "-v", "info", "-i", str(path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ])
    stderr = result.stderr or ""
    starts = [float(m) for m in re.findall(r"silence_start: (-?[\d.]+)", stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: (-?[\d.]+)", stderr)]
    return [(s, e) for s, e in zip(starts, ends) if e > s]


def plan_chunks(
    duration: float,
    silences: list[tuple[float, float]],
    target_seconds: float,
    *,
    tolerance: float | None = None,
) -> list[tuple[float, float]]:
    """Calcule les bornes des segments.

    Chaque coupe est ramenée au centre du silence le plus proche de la borne
    idéale, ce qui évite de couper au milieu d'une phrase. À défaut de silence
    exploitable, la coupe reste à la borne ideale. Les segments sont jointifs :
    aucun recouvrement, donc aucun risque de doublon ni de perte.
    """
    if duration <= target_seconds:
        return [(0.0, duration)]

    tol = tolerance if tolerance is not None else min(90.0, target_seconds * 0.25)
    midpoints = sorted((s + e) / 2 for s, e in silences)
    minimum_chunk = target_seconds * 0.25

    points = [0.0]
    while duration - points[-1] > target_seconds:
        ideal = points[-1] + target_seconds
        candidates = [
            m for m in midpoints
            if abs(m - ideal) <= tol and m > points[-1] + minimum_chunk and m < duration
        ]
        cut = min(candidates, key=lambda m: abs(m - ideal)) if candidates else ideal
        points.append(round(cut, 3))
    points.append(duration)

    return [(points[i], points[i + 1]) for i in range(len(points) - 1)]


def split_audio(
    source: Path,
    output_dir: Path,
    bounds: list[tuple[float, float]],
) -> list[Chunk]:
    """Extrait chaque segment dans un fichier WAV distinct."""
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Chunk] = []
    for index, (start, end) in enumerate(bounds):
        target = output_dir / f"chunk-{index:03d}.wav"
        result = _run([
            FFMPEG, "-y", "-v", "error",
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target),
        ])
        if result.returncode != 0 or not target.exists():
            raise AudioError(
                f"Le découpage du segment {index + 1} a échoué : {result.stderr.strip()[:200]}"
            )
        chunks.append(Chunk(index=index, path=target, start=start, end=end))
    return chunks


def prepare_chunks(
    work_audio: Path,
    output_dir: Path,
    duration: float,
    target_seconds: float,
) -> list[Chunk]:
    """Découpe si nécessaire ; sinon retourne un segment unique couvrant tout."""
    if duration <= target_seconds:
        return [Chunk(index=0, path=work_audio, start=0.0, end=duration)]
    silences = detect_silences(work_audio)
    bounds = plan_chunks(duration, silences, target_seconds)
    return split_audio(work_audio, output_dir, bounds)


def human_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"
