"""Persistência em SQLite: vozes salvas e histórico de gerações."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import settings

_local = threading.local()
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS voices (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    ref_text     TEXT,
    language     TEXT,
    prompt_path  TEXT,
    sample_path  TEXT,
    duration     REAL DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS generations (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    mode         TEXT NOT NULL,
    text         TEXT NOT NULL,
    language     TEXT,
    instruct     TEXT,
    voice_id     TEXT,
    voice_name   TEXT,
    params       TEXT NOT NULL DEFAULT '{}',
    audio_path   TEXT,
    duration     REAL DEFAULT 0,
    error        TEXT,
    created_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL
);

CREATE INDEX IF NOT EXISTS idx_generations_created
    ON generations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_voices_created
    ON voices (created_at DESC);
"""


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def connect() -> sqlite3.Connection:
    """Conexão por thread — o worker de síntese roda fora do loop do FastAPI."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        settings.ensure_dirs()
        conn = sqlite3.connect(settings.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = connect()
    with _write_lock:
        conn.executescript(SCHEMA)
        # Gerações interrompidas por um restart nunca serão retomadas.
        conn.execute(
            "UPDATE generations SET status = 'error', error = ?, finished_at = ?"
            " WHERE status IN ('queued', 'running')",
            ("Interrompida pelo reinício do servidor.", time.time()),
        )
        conn.commit()


def _execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    conn = connect()
    with _write_lock:
        cursor = conn.execute(sql, tuple(params))
        conn.commit()
    return cursor


# --------------------------------------------------------------------------
# Vozes
# --------------------------------------------------------------------------


def create_voice(
    name: str,
    ref_text: str | None,
    language: str | None,
    prompt_path: str | None,
    sample_path: str | None,
    duration: float,
) -> str:
    voice_id = new_id()
    _execute(
        "INSERT INTO voices"
        " (id, name, ref_text, language, prompt_path, sample_path, duration,"
        "  created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            voice_id,
            name,
            ref_text,
            language,
            prompt_path,
            sample_path,
            duration,
            time.time(),
        ),
    )
    return voice_id


def list_voices() -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM voices ORDER BY created_at DESC"
    ).fetchall()


def get_voice(voice_id: str) -> sqlite3.Row | None:
    return connect().execute(
        "SELECT * FROM voices WHERE id = ?", (voice_id,)
    ).fetchone()


def rename_voice(voice_id: str, name: str) -> None:
    _execute("UPDATE voices SET name = ? WHERE id = ?", (name, voice_id))


def delete_voice(voice_id: str) -> None:
    voice = get_voice(voice_id)
    if voice is None:
        return
    for key in ("prompt_path", "sample_path"):
        path = voice[key]
        if path:
            Path(path).unlink(missing_ok=True)
    _execute("DELETE FROM voices WHERE id = ?", (voice_id,))


# --------------------------------------------------------------------------
# Gerações
# --------------------------------------------------------------------------


def create_generation(
    mode: str,
    text: str,
    language: str | None,
    instruct: str | None,
    voice_id: str | None,
    voice_name: str | None,
    params: dict[str, Any],
) -> str:
    generation_id = new_id()
    _execute(
        "INSERT INTO generations"
        " (id, status, mode, text, language, instruct, voice_id, voice_name,"
        "  params, created_at)"
        " VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            generation_id,
            mode,
            text,
            language,
            instruct,
            voice_id,
            voice_name,
            json.dumps(params, ensure_ascii=False),
            time.time(),
        ),
    )
    return generation_id


def mark_running(generation_id: str) -> None:
    _execute(
        "UPDATE generations SET status = 'running', started_at = ? WHERE id = ?",
        (time.time(), generation_id),
    )


def mark_done(generation_id: str, audio_path: str, duration: float) -> None:
    _execute(
        "UPDATE generations SET status = 'done', audio_path = ?, duration = ?,"
        " finished_at = ? WHERE id = ?",
        (audio_path, duration, time.time(), generation_id),
    )


def mark_error(generation_id: str, error: str) -> None:
    _execute(
        "UPDATE generations SET status = 'error', error = ?, finished_at = ?"
        " WHERE id = ?",
        (error[:2000], time.time(), generation_id),
    )


def get_generation(generation_id: str) -> sqlite3.Row | None:
    return connect().execute(
        "SELECT * FROM generations WHERE id = ?", (generation_id,)
    ).fetchone()


def list_generations(limit: int = 100, offset: int = 0) -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM generations ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()


def count_generations() -> int:
    row = connect().execute("SELECT COUNT(*) AS total FROM generations").fetchone()
    return int(row["total"]) if row else 0


def active_generations() -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM generations WHERE status IN ('queued', 'running')"
        " ORDER BY created_at ASC"
    ).fetchall()


def delete_generation(generation_id: str) -> None:
    generation = get_generation(generation_id)
    if generation is None:
        return
    if generation["audio_path"]:
        Path(generation["audio_path"]).unlink(missing_ok=True)
    _execute("DELETE FROM generations WHERE id = ?", (generation_id,))


def prune_history(limit: int) -> int:
    """Remove as gerações mais antigas que excedem ``limit``. Devolve quantas."""
    stale = connect().execute(
        "SELECT id FROM generations ORDER BY created_at DESC LIMIT -1 OFFSET ?",
        (limit,),
    ).fetchall()
    for row in stale:
        delete_generation(row["id"])
    return len(stale)


def clear_history() -> int:
    rows = connect().execute("SELECT id FROM generations").fetchall()
    for row in rows:
        delete_generation(row["id"])
    return len(rows)
