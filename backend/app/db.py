"""Persistance SQLite.

Une seule base, un seul fichier, aucune dépendance externe. La recherche
plein texte s'appuie sur FTS5 (inclus dans SQLite) et indexe le titre, le
contexte et le texte transcrit.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .config import get_settings
from .models import Meeting, MeetingStatus, StageState, STAGE_ORDER

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id                     TEXT PRIMARY KEY,
    title                  TEXT NOT NULL,
    meeting_date           TEXT NOT NULL,
    context                TEXT,
    notes                  TEXT,
    status                 TEXT NOT NULL,
    source                 TEXT NOT NULL DEFAULT 'recording',
    duration_seconds       REAL NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    documents_generated_at TEXT,
    transcribed_at         TEXT,
    error                  TEXT,
    stages                 TEXT NOT NULL DEFAULT '[]',
    audio_filename         TEXT,
    audio_bytes            INTEGER NOT NULL DEFAULT 0,
    language               TEXT
);

CREATE INDEX IF NOT EXISTS idx_meetings_date ON meetings(meeting_date DESC);
CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);

CREATE VIRTUAL TABLE IF NOT EXISTS meeting_search
USING fts5(meeting_id UNINDEXED, title, context, content, tokenize='unicode61 remove_diacritics 2');
"""


def _db_path() -> Path:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "app.db"


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_meeting(row: sqlite3.Row) -> Meeting:
    stages = [StageState.model_validate(s) for s in json.loads(row["stages"])]
    known = {s.stage for s in stages}
    stages.extend(StageState(stage=st) for st in STAGE_ORDER if st not in known)
    stages.sort(key=lambda s: STAGE_ORDER.index(s.stage))
    return Meeting(
        id=row["id"],
        title=row["title"],
        meeting_date=row["meeting_date"],
        context=row["context"],
        notes=row["notes"],
        status=MeetingStatus(row["status"]),
        source=row["source"],
        duration_seconds=row["duration_seconds"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        documents_generated_at=_parse_dt(row["documents_generated_at"]),
        transcribed_at=_parse_dt(row["transcribed_at"]),
        error=row["error"],
        stages=stages,
        audio_filename=row["audio_filename"],
        audio_bytes=row["audio_bytes"],
        language=row["language"],
    )


# ---------------------------------------------------------------------------
# Opérations
# ---------------------------------------------------------------------------


def save_meeting(meeting: Meeting) -> None:
    meeting.updated_at = datetime.now(meeting.created_at.tzinfo)
    payload = {
        "id": meeting.id,
        "title": meeting.title,
        "meeting_date": meeting.meeting_date,
        "context": meeting.context,
        "notes": meeting.notes,
        "status": str(meeting.status),
        "source": meeting.source,
        "duration_seconds": meeting.duration_seconds,
        "created_at": _iso(meeting.created_at),
        "updated_at": _iso(meeting.updated_at),
        "documents_generated_at": _iso(meeting.documents_generated_at),
        "transcribed_at": _iso(meeting.transcribed_at),
        "error": meeting.error,
        "stages": json.dumps([s.model_dump(mode="json") for s in meeting.stages]),
        "audio_filename": meeting.audio_filename,
        "audio_bytes": meeting.audio_bytes,
        "language": meeting.language,
    }
    columns = ", ".join(payload)
    placeholders = ", ".join(f":{k}" for k in payload)
    updates = ", ".join(f"{k}=excluded.{k}" for k in payload if k != "id")
    with connection() as conn:
        conn.execute(
            f"INSERT INTO meetings ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            payload,
        )


def get_meeting(meeting_id: str) -> Meeting | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    return _row_to_meeting(row) if row else None


def list_meetings(
    query: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
) -> list[Meeting]:
    """Liste filtrée. `query` cherche dans le titre, le contexte ET la
    transcription via l'index plein texte."""
    sql = "SELECT m.* FROM meetings m"
    params: list = []
    where: list[str] = []

    if query:
        sql += (
            " JOIN meeting_search s ON s.meeting_id = m.id"
            " AND meeting_search MATCH ?"
        )
        params.append(_fts_query(query))
    if status:
        where.append("m.status = ?")
        params.append(status)
    if date_from:
        where.append("m.meeting_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("m.meeting_date <= ?")
        params.append(date_to)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY m.id ORDER BY m.meeting_date DESC, m.created_at DESC LIMIT ?"
    params.append(limit)

    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_meeting(r) for r in rows]


def _fts_query(raw: str) -> str:
    """Transforme une saisie libre en requête FTS5 sûre (préfixes, ET implicite)."""
    tokens = [t for t in "".join(c if c.isalnum() else " " for c in raw).split() if t]
    if not tokens:
        return '""'
    return " AND ".join(f'"{t}"*' for t in tokens)


def index_meeting(meeting_id: str, title: str, context: str | None, content: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM meeting_search WHERE meeting_id = ?", (meeting_id,))
        conn.execute(
            "INSERT INTO meeting_search (meeting_id, title, context, content) VALUES (?,?,?,?)",
            (meeting_id, title, context or "", content),
        )


def delete_meeting(meeting_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        conn.execute("DELETE FROM meeting_search WHERE meeting_id = ?", (meeting_id,))


def meetings_older_than(iso_date: str) -> list[str]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id FROM meetings WHERE meeting_date < ?", (iso_date,)
        ).fetchall()
    return [r["id"] for r in rows]
