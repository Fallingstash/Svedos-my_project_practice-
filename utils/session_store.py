import json
import sqlite3
from datetime import datetime
from pathlib import Path


DATABASE_PATH = Path(__file__).resolve().parents[1] / "history" / "svedos.db"


def _connect():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            track_name TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            genre TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            track_path TEXT NOT NULL,
            reference_path TEXT,
            analysis_mode TEXT NOT NULL DEFAULT 'master',
            beat_path TEXT,
            vocal_path TEXT,
            segments_json TEXT NOT NULL,
            chat_history_json TEXT NOT NULL
        )
        """
    )
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(sessions)")
    }
    for column_name, declaration in (
        ("user_id", "TEXT"),
        ("display_name", "TEXT NOT NULL DEFAULT ''"),
        ("analysis_mode", "TEXT NOT NULL DEFAULT 'master'"),
        ("beat_path", "TEXT"),
        ("vocal_path", "TEXT"),
    ):
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE sessions ADD COLUMN {column_name} {declaration}"
            )
    connection.commit()
    return connection


def save_session(session):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    connection = _connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO sessions (
                id, user_id, track_name, display_name, genre, created_at, updated_at,
                track_path, reference_path, analysis_mode, beat_path, vocal_path,
                segments_json, chat_history_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                user_id = excluded.user_id,
                track_name = excluded.track_name,
                display_name = excluded.display_name,
                genre = excluded.genre,
                updated_at = excluded.updated_at,
                track_path = excluded.track_path,
                reference_path = excluded.reference_path,
                analysis_mode = excluded.analysis_mode,
                beat_path = excluded.beat_path,
                vocal_path = excluded.vocal_path,
                segments_json = excluded.segments_json,
                chat_history_json = excluded.chat_history_json
            WHERE sessions.user_id = excluded.user_id
            """,
            (
                session["id"],
                session["user_id"],
                session["track_name"],
                session.get("display_name", session["track_name"]),
                session["genre"],
                session.get("created_at", now),
                now,
                session["track_path"],
                session.get("reference_path"),
                session.get("analysis_mode", "master"),
                session.get("beat_path"),
                session.get("vocal_path"),
                json.dumps(session.get("segments", []), ensure_ascii=False),
                json.dumps(session.get("chat_history", []), ensure_ascii=False),
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError("Нельзя сохранить сессию другого пользователя.")
        connection.commit()
    finally:
        connection.close()


def list_sessions(user_id):
    connection = _connect()
    try:
        rows = connection.execute(
            """
            SELECT id, track_name, display_name, genre, created_at, updated_at,
                   track_path, reference_path, analysis_mode, beat_path,
                   vocal_path, segments_json, chat_history_json
            FROM sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    finally:
        connection.close()

    sessions = []
    for row in rows:
        chat_history = json.loads(row["chat_history_json"])
        latest_answer = next(
            (
                message["content"]
                for message in reversed(chat_history)
                if message["role"] == "assistant"
            ),
            "Пока нет ответа ИИ.",
        )
        sessions.append(
            {
                "id": row["id"],
                "track_name": row["track_name"],
                "display_name": row["display_name"] or row["track_name"],
                "genre": row["genre"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "latest_answer": latest_answer,
            }
        )
    return sessions


def load_session(session_id, user_id):
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        return None

    return {
        "id": row["id"],
        "track_name": row["track_name"],
        "display_name": row["display_name"] or row["track_name"],
        "genre": row["genre"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "track_path": row["track_path"],
        "reference_path": row["reference_path"],
        "analysis_mode": row["analysis_mode"],
        "beat_path": row["beat_path"],
        "vocal_path": row["vocal_path"],
        "segments": json.loads(row["segments_json"]),
        "chat_history": json.loads(row["chat_history_json"]),
    }


def rename_session(session_id, user_id, display_name):
    normalized_name = display_name.strip()
    if not normalized_name:
        raise ValueError("Название сессии не может быть пустым.")

    connection = _connect()
    try:
        cursor = connection.execute(
            "UPDATE sessions SET display_name = ? WHERE id = ? AND user_id = ?",
            (normalized_name, session_id, user_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Сессия не найдена.")
        connection.commit()
    finally:
        connection.close()
