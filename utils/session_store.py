import hashlib
import json
import sqlite3
import os
import secrets
from datetime import datetime
from pathlib import Path
from time import time

from utils.config import RATE_LIMIT_SECONDS

DATA_DIRECTORY = (
    Path(os.environ["SVEDOS_DATA_DIR"]).expanduser().resolve()
    if os.environ.get("SVEDOS_DATA_DIR")
    else Path(__file__).resolve().parents[1]
)
DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = DATA_DIRECTORY / "history" / "svedos.db"


def _connect():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
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
            chat_history_json TEXT NOT NULL,
            vibe_description TEXT NOT NULL DEFAULT '',
            old_spectrum_json TEXT,
            old_frequencies_json TEXT,
            recovery_token_hash TEXT UNIQUE
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
        ("vibe_description", "TEXT NOT NULL DEFAULT ''"),
        ("old_spectrum_json", "TEXT"),
        ("old_frequencies_json", "TEXT"),
        ("recovery_token_hash", "TEXT"),
    ):
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE sessions ADD COLUMN {column_name} {declaration}"
            )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS message_feedback (
            session_id TEXT NOT NULL,
            message_index INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            rating TEXT NOT NULL CHECK (rating IN ('positive', 'negative')),
            PRIMARY KEY (session_id, message_index)
        )
        """
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_recovery_token_hash "
        "ON sessions(recovery_token_hash)"
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
                segments_json, chat_history_json, vibe_description,
                old_spectrum_json, old_frequencies_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                user_id = excluded.user_id,
                track_name = excluded.track_name,
                display_name = CASE
                    WHEN sessions.display_name = sessions.track_name
                    THEN excluded.display_name
                    ELSE sessions.display_name
                END,
                genre = excluded.genre,
                updated_at = excluded.updated_at,
                track_path = excluded.track_path,
                reference_path = excluded.reference_path,
                analysis_mode = excluded.analysis_mode,
                beat_path = excluded.beat_path,
                vocal_path = excluded.vocal_path,
                segments_json = excluded.segments_json,
                chat_history_json = excluded.chat_history_json,
                vibe_description = excluded.vibe_description,
                old_spectrum_json = excluded.old_spectrum_json,
                old_frequencies_json = excluded.old_frequencies_json
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
                session.get("vibe_description", ""),
                (
                    json.dumps(session["old_spectrum"])
                    if session.get("old_spectrum") is not None
                    else None
                ),
                (
                    json.dumps(session["old_frequencies"])
                    if session.get("old_frequencies") is not None
                    else None
                ),
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError("Нельзя сохранить сессию другого пользователя.")
        connection.commit()
    finally:
        connection.close()


def allow_ai_request(user_id, ip_address=None):
    now = time()
    rate_limit_keys = {f"user:{user_id}"}
    if ip_address:
        ip_digest = hashlib.sha256(ip_address.encode("utf-8")).hexdigest()
        rate_limit_keys.add(f"ip:{ip_digest}")

    connection = _connect()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS request_rate_limits (
                rate_key TEXT PRIMARY KEY,
                last_request_at REAL NOT NULL
            )
            """
        )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM request_rate_limits WHERE last_request_at < ?",
            (now - 86400,),
        )
        for rate_key in rate_limit_keys:
            row = connection.execute(
                "SELECT last_request_at FROM request_rate_limits WHERE rate_key = ?",
                (rate_key,),
            ).fetchone()
            if (
                row is not None
                and now - row["last_request_at"] < RATE_LIMIT_SECONDS
            ):
                connection.rollback()
                return False

        connection.executemany(
            """
            INSERT INTO request_rate_limits (rate_key, last_request_at)
            VALUES (?, ?)
            ON CONFLICT(rate_key) DO UPDATE SET
                last_request_at = excluded.last_request_at
            """,
            ((rate_key, now) for rate_key in rate_limit_keys),
        )
        connection.commit()
        return True
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


def _load_session(session_id, user_id):
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
        "user_id": row["user_id"],
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
        "vibe_description": row["vibe_description"],
        "old_spectrum": (
            json.loads(row["old_spectrum_json"])
            if row["old_spectrum_json"]
            else None
        ),
        "old_frequencies": (
            json.loads(row["old_frequencies_json"])
            if row["old_frequencies_json"]
            else None
        ),
    }


def load_session(session_id, user_id):
    return _load_session(session_id, user_id)


def create_recovery_token(session_id, user_id):
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT id FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        if row is None:
            raise ValueError("Сессия не найдена.")

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        connection.execute(
            "UPDATE sessions SET recovery_token_hash = ? WHERE id = ?",
            (token_hash, session_id),
        )
        connection.commit()
        return token
    finally:
        connection.close()


def load_session_for_recovery_token(token):
    if not isinstance(token, str) or not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT * FROM sessions WHERE recovery_token_hash = ?",
            (token_hash,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None

    return {
        "id": row["id"],
        "user_id": row["user_id"],
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
        "vibe_description": row["vibe_description"],
        "old_spectrum": (
            json.loads(row["old_spectrum_json"])
            if row["old_spectrum_json"]
            else None
        ),
        "old_frequencies": (
            json.loads(row["old_frequencies_json"])
            if row["old_frequencies_json"]
            else None
        ),
    }


def save_session_for_recovery_token(token, session):
    if not isinstance(token, str) or not token:
        raise ValueError("Недействительная ссылка восстановления.")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT id, user_id FROM sessions WHERE recovery_token_hash = ?",
            (token_hash,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("Ссылка восстановления недействительна.")
    if session["id"] != row["id"]:
        raise ValueError("Токен не относится к этой сессии.")
    owner_session = dict(session)
    owner_session["user_id"] = row["user_id"]
    return save_session(owner_session)


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


def delete_session(session_id, user_id):
    connection = _connect()
    try:
        row = connection.execute(
            """
            SELECT track_path, reference_path, beat_path, vocal_path
            FROM sessions WHERE id = ? AND user_id = ?
            """,
            (session_id, user_id),
        ).fetchone()
        if row is None:
            raise ValueError("Сессия не найдена.")

        connection.execute(
            "DELETE FROM message_feedback WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )
        cursor = connection.execute(
            "DELETE FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Сессия не найдена.")
        connection.commit()
    finally:
        connection.close()

    session_directories = {
        (DATA_DIRECTORY / session_id).resolve(),
        (DATA_DIRECTORY / "history" / session_id).resolve(),
        (DATA_DIRECTORY.parent / "history" / session_id).resolve(),
    }
    protected_paths = set()
    connection = _connect()
    try:
        remaining = connection.execute(
            """
            SELECT track_path, reference_path, beat_path, vocal_path
            FROM sessions
            """
        ).fetchall()
        for remaining_row in remaining:
            for column in ("track_path", "reference_path", "beat_path", "vocal_path"):
                path = remaining_row[column]
                if path:
                    protected_paths.add(str(Path(path).resolve()))
    finally:
        connection.close()

    for column in ("track_path", "reference_path", "beat_path", "vocal_path"):
        path_value = row[column]
        if not path_value:
            continue
        try:
            file_path = Path(path_value).resolve()
        except (OSError, RuntimeError):
            continue
        if str(file_path) in protected_paths:
            continue
        if not any(
            file_path == directory or directory in file_path.parents
            for directory in session_directories
        ):
            continue
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            continue


def save_message_feedback(session_id, user_id, message_index, rating):
    if rating not in {"positive", "negative"}:
        raise ValueError("Неизвестная оценка сообщения.")

    connection = _connect()
    try:
        row = connection.execute(
            "SELECT chat_history_json FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        if row is None:
            raise ValueError("Сессия не найдена.")

        chat_history = json.loads(row["chat_history_json"])
        if (
            message_index < 0
            or message_index >= len(chat_history)
            or chat_history[message_index].get("role") != "assistant"
        ):
            raise ValueError("Ответ ИИ для оценки не найден.")

        connection.execute(
            """
            INSERT INTO message_feedback (session_id, message_index, user_id, rating)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, message_index) DO UPDATE SET
                user_id = excluded.user_id,
                rating = excluded.rating
            WHERE message_feedback.user_id = excluded.user_id
            """,
            (session_id, message_index, user_id, rating),
        )
        connection.commit()
    finally:
        connection.close()


def get_message_feedbacks(session_id, user_id):
    connection = _connect()
    try:
        return {
            row["message_index"]: row["rating"]
            for row in connection.execute(
                """
                SELECT feedback.message_index, feedback.rating
                FROM message_feedback AS feedback
                JOIN sessions ON sessions.id = feedback.session_id
                WHERE feedback.session_id = ?
                  AND feedback.user_id = ?
                  AND sessions.user_id = ?
                """,
                (session_id, user_id, user_id),
            )
        }
    finally:
        connection.close()
