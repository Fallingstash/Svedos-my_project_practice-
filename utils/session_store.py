import hashlib
import json
import sqlite3
import os
import secrets
from datetime import datetime
from pathlib import Path
from time import time

from utils.config import RATE_LIMIT_SECONDS
from utils.config import get_storage_backend

DATA_DIRECTORY = (
    Path(os.environ["SVEDOS_DATA_DIR"]).expanduser().resolve()
    if os.environ.get("SVEDOS_DATA_DIR")
    else Path(__file__).resolve().parents[1]
)
DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = DATA_DIRECTORY / "history" / "svedos.db"


def _get_supabase_store():
    from utils.supabase_store import get_supabase_store

    return get_supabase_store()


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
            recovery_token_hash TEXT UNIQUE,
            share_token_hash TEXT UNIQUE,
            shared_at TEXT,
            share_revoked_at TEXT,
            share_enabled INTEGER NOT NULL DEFAULT 0,
            analysis_json TEXT,
            context_summary TEXT NOT NULL DEFAULT '',
            context_summary_message_count INTEGER NOT NULL DEFAULT 0
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
        ("share_token_hash", "TEXT"),
        ("shared_at", "TEXT"),
        ("share_revoked_at", "TEXT"),
        ("share_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("analysis_json", "TEXT"),
        ("context_summary", "TEXT NOT NULL DEFAULT ''"),
        ("context_summary_message_count", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column_name not in existing_columns:
            try:
                connection.execute(
                    f"ALTER TABLE sessions ADD COLUMN {column_name} {declaration}"
                )
            except sqlite3.OperationalError as error:
                if "duplicate column name" not in str(error).lower():
                    raise
            existing_columns.add(column_name)
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
        """
        CREATE TABLE IF NOT EXISTS session_files (
            object_key TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            role TEXT NOT NULL,
            original_name TEXT NOT NULL,
            content_type TEXT,
            byte_size INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    if "file_metadata_json" in existing_columns:
        legacy_rows = connection.execute(
            """
            SELECT id, user_id, created_at, file_metadata_json
            FROM sessions
            WHERE file_metadata_json IS NOT NULL
              AND file_metadata_json != '[]'
            """
        ).fetchall()
        for legacy_row in legacy_rows:
            metadata_items = json.loads(legacy_row["file_metadata_json"])
            if not isinstance(metadata_items, list):
                raise ValueError(
                    "Старые метаданные аудио имеют неверный формат."
                )
            for item in metadata_items:
                if not legacy_row["user_id"]:
                    continue
                try:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO session_files (
                            object_key, session_id, owner_id, role,
                            original_name, content_type, byte_size, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item["object_key"],
                            legacy_row["id"],
                            legacy_row["user_id"],
                            item["role"],
                            item["original_name"],
                            item.get("content_type"),
                            int(item["byte_size"]),
                            legacy_row["created_at"],
                        ),
                    )
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(
                        "Не удалось перенести старые метаданные аудио."
                    ) from error
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_request_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT,
            model TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            latency_ms INTEGER NOT NULL,
            attempts INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_recovery_token_hash "
        "ON sessions(recovery_token_hash)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_share_token_hash "
        "ON sessions(share_token_hash)"
    )
    connection.execute(
        "UPDATE sessions SET recovery_token_hash = NULL "
        "WHERE recovery_token_hash IS NOT NULL"
    )
    connection.commit()
    return connection


def save_session(session, owner_id=None):
    if not owner_id or session.get("user_id") != owner_id:
        raise PermissionError("Сохранять сессию может только её владелец.")
    if get_storage_backend() == "supabase":
        from utils.supabase_store import get_supabase_store
        store = get_supabase_store()
        try:
            return store.save_session(session, owner_id)
        finally:
            store.close()
    for item in session.get("file_metadata", []):
        if not item["object_key"]:
            raise ValueError("Метаданные аудиофайла не содержат object key.")
        if not (
            Path(item["object_key"]).is_absolute()
            and Path(DATA_DIRECTORY / "history" / session["id"]).resolve()
            in Path(item["object_key"]).resolve().parents
        ):
            raise PermissionError("Файл не принадлежит этой локальной сессии.")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    connection = _connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO sessions (
                id, user_id, track_name, display_name, genre, created_at, updated_at,
                track_path, reference_path, analysis_mode, beat_path, vocal_path,
                segments_json, chat_history_json, vibe_description,
                old_spectrum_json, old_frequencies_json, analysis_json,
                context_summary,
                context_summary_message_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                old_frequencies_json = excluded.old_frequencies_json,
                analysis_json = excluded.analysis_json,
                context_summary = excluded.context_summary,
                context_summary_message_count =
                    excluded.context_summary_message_count
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
                (
                    json.dumps(session["analysis_data"], ensure_ascii=False)
                    if session.get("analysis_data") is not None
                    else None
                ),
                session.get("context_summary", ""),
                session.get("context_summary_message_count", 0),
            ),
        )
        if cursor.rowcount == 0:
            raise PermissionError("Нельзя сохранить сессию другого пользователя.")
        created_at = now
        for item in session.get("file_metadata", []):
            connection.execute(
                """
                INSERT INTO session_files (
                    object_key, session_id, owner_id, role, original_name,
                    content_type, byte_size, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(object_key) DO UPDATE SET
                    content_type = excluded.content_type,
                    byte_size = excluded.byte_size
                WHERE session_files.session_id = excluded.session_id
                  AND session_files.owner_id = excluded.owner_id
                """,
                (
                    item["object_key"],
                    session["id"],
                    owner_id,
                    item["role"],
                    item["original_name"],
                    item.get("content_type"),
                    item["byte_size"],
                    created_at,
                ),
            )
            if connection.execute(
                "SELECT changes()"
            ).fetchone()[0] == 0:
                raise PermissionError("Файл уже привязан к другой сессии.")
        connection.commit()
    except sqlite3.Error as error:
        raise RuntimeError("Не удалось сохранить сессию в локальной базе.") from error
    finally:
        connection.close()


def allow_ai_request(user_id, ip_address=None):
    if get_storage_backend() == "supabase":
        ip_digest = (
            hashlib.sha256(ip_address.encode("utf-8")).hexdigest()
            if ip_address
            else None
        )
        store = _get_supabase_store()
        try:
            return store.allow_ai_request(user_id, ip_digest)
        finally:
            store.close()
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


def record_ai_usage(
    user_id,
    session_id,
    model,
    prompt_tokens,
    completion_tokens,
    latency_ms,
    attempts,
):
    if not session_id:
        return
    if get_storage_backend() == "supabase":
        store = _get_supabase_store()
        try:
            return store.record_ai_usage(
                user_id,
                session_id,
                model,
                prompt_tokens,
                completion_tokens,
                latency_ms,
                attempts,
            )
        finally:
            store.close()
    connection = _connect()
    try:
        try:
            connection.execute(
                """
                INSERT INTO ai_request_logs (
                    user_id, session_id, model, prompt_tokens, completion_tokens,
                    latency_ms, attempts, created_at
                )
                SELECT ?, ?, ?, ?, ?, ?, ?, ?
                WHERE EXISTS (
                    SELECT 1 FROM sessions WHERE id = ? AND user_id = ?
                )
                """,
                (
                    user_id,
                    session_id,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    latency_ms,
                    attempts,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    session_id,
                    user_id,
                ),
            )
            connection.commit()
        except sqlite3.Error as error:
            raise RuntimeError(
                "Не удалось записать диагностику AI-запроса."
            ) from error
    finally:
        connection.close()


def save_analysis(session_id, user_id, analysis_data):
    if get_storage_backend() == "supabase":
        store = _get_supabase_store()
        try:
            return store.save_analysis(session_id, user_id, analysis_data)
        finally:
            store.close()
    connection = _connect()
    try:
        cursor = connection.execute(
            "UPDATE sessions SET analysis_json = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (
                json.dumps(analysis_data, ensure_ascii=False),
                datetime.now().astimezone().isoformat(timespec="seconds"),
                session_id,
                user_id,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError("Сессия не найдена.")
        connection.commit()
    finally:
        connection.close()


def list_sessions(user_id):
    if get_storage_backend() == "supabase":
        store = _get_supabase_store()
        try:
            return store.list_sessions(user_id)
        finally:
            store.close()
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


def _session_file_metadata(connection, session_id, owner_id):
    rows = connection.execute(
        """
        SELECT object_key, role, original_name, content_type, byte_size
        FROM session_files WHERE session_id = ? AND owner_id = ?
        ORDER BY created_at
        """,
        (session_id, owner_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_session(session_id, user_id):
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        file_metadata = _session_file_metadata(
            connection,
            session_id,
            user_id,
        )
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
        "analysis_data": (
            json.loads(row["analysis_json"])
            if row["analysis_json"]
            else None
        ),
        "context_summary": row["context_summary"],
        "context_summary_message_count": row["context_summary_message_count"],
        "file_metadata": file_metadata,
    }


def load_session(session_id, user_id):
    if get_storage_backend() == "supabase":
        store = _get_supabase_store()
        try:
            return store.load_session(session_id, user_id)
        finally:
            store.close()
    return _load_session(session_id, user_id)


def create_recovery_token(session_id, user_id):
    raise RuntimeError(
        "Recovery-токены больше не создаются. Используйте явную публикацию сессии."
    )


def load_session_for_recovery_token(token):
    return None


def save_session_for_recovery_token(token, session):
    raise RuntimeError(
        "Публичная сессия доступна только для чтения и не сохраняется."
    )


def create_share_token(session_id, owner_id):
    if get_storage_backend() == "supabase":
        store = _get_supabase_store()
        try:
            return store.create_share_token(session_id, owner_id)
        finally:
            store.close()
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT id FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, owner_id),
        ).fetchone()
        if row is None:
            raise ValueError("Сессия не найдена.")

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        connection.execute(
            """
            UPDATE sessions
            SET share_token_hash = ?, shared_at = ?, share_revoked_at = NULL,
                share_enabled = 1
            WHERE id = ? AND user_id = ?
            """,
            (token_hash, now, session_id, owner_id),
        )
        connection.commit()
        return token
    finally:
        connection.close()


def load_shared_session(token):
    if get_storage_backend() == "supabase":
        store = _get_supabase_store()
        try:
            return store.load_shared_session(token)
        finally:
            store.close()
    if not isinstance(token, str) or not token:
        return None
    try:
        token_hash = hashlib.sha256(token.encode("ascii", "strict")).hexdigest()
    except UnicodeEncodeError:
        return None
    connection = _connect()
    try:
        row = connection.execute(
            """
            SELECT * FROM sessions
            WHERE share_token_hash = ? AND share_enabled = 1
              AND share_revoked_at IS NULL
            """,
            (token_hash,),
        ).fetchone()
        file_metadata = (
            _session_file_metadata(connection, row["id"], row["user_id"])
            if row is not None
            else []
        )
    finally:
        connection.close()
    if row is None:
        return None
    return _row_to_session(row, file_metadata)


def revoke_share_token(session_id, owner_id):
    if get_storage_backend() == "supabase":
        store = _get_supabase_store()
        try:
            return store.revoke_share_token(session_id, owner_id)
        finally:
            store.close()
    connection = _connect()
    try:
        cursor = connection.execute(
            """
            UPDATE sessions
            SET share_token_hash = NULL, share_revoked_at = ?, share_enabled = 0
            WHERE id = ? AND user_id = ?
            """,
            (
                datetime.now().astimezone().isoformat(timespec="seconds"),
                session_id,
                owner_id,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError("Сессия не найдена.")
        connection.commit()
    finally:
        connection.close()


def _row_to_session(row, file_metadata=()):
    session = {
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
        "analysis_data": (
            json.loads(row["analysis_json"])
            if row["analysis_json"]
            else None
        ),
        "context_summary": row["context_summary"],
        "context_summary_message_count": row["context_summary_message_count"],
        "file_metadata": list(file_metadata),
    }
    return session


def rename_session(session_id, user_id, display_name):
    if get_storage_backend() == "supabase":
        normalized_name = display_name.strip()
        if not normalized_name:
            raise ValueError("Название сессии не может быть пустым.")
        store = _get_supabase_store()
        try:
            return store.rename_session(session_id, user_id, normalized_name)
        finally:
            store.close()
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
    session = load_session(session_id, user_id)
    if session is None:
        raise ValueError("Сессия не найдена.")
    file_references = {
        item["object_key"] for item in session.get("file_metadata", [])
    }
    file_references.update(
        session.get(field)
        for field in ("track_path", "reference_path", "beat_path", "vocal_path")
        if session.get(field)
    )
    from utils.storage import delete_session_audio

    delete_session_audio(user_id, session_id, file_references)
    if get_storage_backend() == "supabase":
        store = _get_supabase_store()
        try:
            return store.delete_session_record(session_id, user_id)
        finally:
            store.close()
    connection = _connect()
    try:
        connection.execute(
            "DELETE FROM message_feedback WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )
        connection.execute(
            "DELETE FROM session_files WHERE session_id = ? AND owner_id = ?",
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


def save_message_feedback(session_id, user_id, message_index, rating):
    if get_storage_backend() == "supabase":
        if rating not in {"positive", "negative"}:
            raise ValueError("Неизвестная оценка сообщения.")
        store = _get_supabase_store()
        try:
            return store.save_message_feedback(
                session_id,
                user_id,
                message_index,
                rating,
            )
        finally:
            store.close()
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
    if get_storage_backend() == "supabase":
        store = _get_supabase_store()
        try:
            return store.get_message_feedbacks(session_id, user_id)
        finally:
            store.close()
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
