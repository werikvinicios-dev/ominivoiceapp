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
    id             TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    mode           TEXT NOT NULL,
    text           TEXT NOT NULL,
    language       TEXT,
    instruct       TEXT,
    voice_id       TEXT,
    voice_name     TEXT,
    params         TEXT NOT NULL DEFAULT '{}',
    audio_path     TEXT,
    duration       REAL DEFAULT 0,
    error          TEXT,
    created_at     REAL NOT NULL,
    started_at     REAL,
    finished_at    REAL,
    is_preview     INTEGER NOT NULL DEFAULT 0,
    segments_total INTEGER NOT NULL DEFAULT 0,
    segments_done  INTEGER NOT NULL DEFAULT 0,
    warnings       TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    text       TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS prefs (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_generations_created
    ON generations (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_voices_created
    ON voices (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_projects_updated
    ON projects (updated_at DESC);
"""

#: Colunas acrescentadas depois da v1: aplicadas em bancos já existentes.
_MIGRATIONS = {
    "generations": {
        "is_preview": "INTEGER NOT NULL DEFAULT 0",
        "segments_total": "INTEGER NOT NULL DEFAULT 0",
        "segments_done": "INTEGER NOT NULL DEFAULT 0",
        "warnings": "TEXT NOT NULL DEFAULT '[]'",
    }
}


def _migrate(conn: sqlite3.Connection) -> None:
    """Acrescenta colunas novas a bancos criados por versões anteriores."""
    for table, columns in _MIGRATIONS.items():
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for column, definition in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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


def init_db() -> list[sqlite3.Row]:
    """Cria/migra o banco e devolve as gerações a retomar.

    Gerações interrompidas não viram erro: voltam para a fila. Os segmentos
    já sintetizados ficam em disco, então a retomada continua de onde parou —
    importante no Colab, onde a sessão pode cair no meio de um capítulo.
    """
    conn = connect()
    with _write_lock:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # Prévias interrompidas não valem a pena retomar.
        conn.execute(
            "UPDATE generations SET status = 'error', error = ?, finished_at = ?"
            " WHERE status IN ('queued', 'running') AND is_preview = 1",
            ("Prévia interrompida pelo reinício do servidor.", time.time()),
        )
        conn.execute(
            "UPDATE generations SET status = 'queued', started_at = NULL"
            " WHERE status = 'running' AND is_preview = 0"
        )
        conn.commit()
        return conn.execute(
            "SELECT * FROM generations WHERE status = 'queued' AND is_preview = 0"
            " ORDER BY created_at ASC"
        ).fetchall()


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
    *,
    is_preview: bool = False,
    segments_total: int = 0,
    warnings: list[dict[str, Any]] | None = None,
) -> str:
    generation_id = new_id()
    _execute(
        "INSERT INTO generations"
        " (id, status, mode, text, language, instruct, voice_id, voice_name,"
        "  params, created_at, is_preview, segments_total, warnings)"
        " VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            1 if is_preview else 0,
            segments_total,
            json.dumps(warnings or [], ensure_ascii=False),
        ),
    )
    return generation_id


def update_progress(generation_id: str, done: int, total: int) -> None:
    _execute(
        "UPDATE generations SET segments_done = ?, segments_total = ? WHERE id = ?",
        (done, total, generation_id),
    )


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
        "SELECT * FROM generations WHERE is_preview = 0"
        " ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()


def count_generations() -> int:
    row = connect().execute(
        "SELECT COUNT(*) AS total FROM generations WHERE is_preview = 0"
    ).fetchone()
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
        "SELECT id FROM generations WHERE is_preview = 0"
        " ORDER BY created_at DESC LIMIT -1 OFFSET ?",
        (limit,),
    ).fetchall()
    for row in stale:
        delete_generation(row["id"])
    return len(stale)


def prune_previews(keep: int = 5) -> int:
    """Mantém só as prévias mais recentes — elas não vão para o histórico."""
    stale = connect().execute(
        "SELECT id FROM generations WHERE is_preview = 1"
        " ORDER BY created_at DESC LIMIT -1 OFFSET ?",
        (keep,),
    ).fetchall()
    for row in stale:
        delete_generation(row["id"])
    return len(stale)


def clear_history() -> int:
    rows = connect().execute(
        "SELECT id FROM generations WHERE is_preview = 0"
    ).fetchall()
    for row in rows:
        delete_generation(row["id"])
    return len(rows)


# --------------------------------------------------------------------------
# Preferências (ajustáveis pela tela de Ajustes)
# --------------------------------------------------------------------------


def get_prefs() -> dict[str, str]:
    return {
        row["key"]: row["value"]
        for row in connect().execute("SELECT key, value FROM prefs")
    }


def set_pref(key: str, value: str) -> None:
    _execute(
        "INSERT INTO prefs (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --------------------------------------------------------------------------
# Projetos (textos salvos)
# --------------------------------------------------------------------------


def create_project(name: str, text: str) -> str:
    project_id = new_id()
    now = time.time()
    _execute(
        "INSERT INTO projects (id, name, text, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (project_id, name, text, now, now),
    )
    return project_id


def update_project(project_id: str, name: str, text: str) -> None:
    _execute(
        "UPDATE projects SET name = ?, text = ?, updated_at = ? WHERE id = ?",
        (name, text, time.time(), project_id),
    )


def list_projects() -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM projects ORDER BY updated_at DESC"
    ).fetchall()


def get_project(project_id: str) -> sqlite3.Row | None:
    return connect().execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()


def delete_project(project_id: str) -> None:
    _execute("DELETE FROM projects WHERE id = ?", (project_id,))
