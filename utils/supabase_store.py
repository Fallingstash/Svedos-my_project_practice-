import hashlib
import json
import secrets
from datetime import datetime, timezone
from functools import wraps

import psycopg

from utils.config import get_server_secret, RATE_LIMIT_SECONDS


class SupabaseStoreError(RuntimeError):
    pass


class _RateLimitReject(Exception):
    pass


def _translate_database_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except psycopg.Error as error:
            raise SupabaseStoreError(
                "Ошибка запроса к Supabase PostgreSQL."
            ) from error

    return wrapped


class SupabaseStore:
    def __init__(self, connection=None):
        if connection is None:
            url = get_server_secret("SUPABASEDBURL")
            if not url:
                raise SupabaseStoreError("SUPABASEDBURL не настроен.")
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as error:
                raise SupabaseStoreError(
                    "Зависимость psycopg[binary] не установлена."
                ) from error
            try:
                connection = psycopg.connect(
                    url,
                    row_factory=dict_row,
                    autocommit=True,
                )
            except Exception as error:
                raise SupabaseStoreError(
                    "Не удалось подключиться к Supabase PostgreSQL."
                ) from error
        self.connection = connection

    def close(self):
        self.connection.close()

    @_translate_database_errors
    def _session_files(self, session_id, owner_id):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT object_key, role, original_name, content_type, byte_size
                FROM session_files
                WHERE session_id = %s AND owner_id = %s
                ORDER BY created_at
                """,
                (session_id, owner_id),
            )
            return cursor.fetchall()

    @staticmethod
    def _row_to_session(row, file_metadata=None):
        def json_value(value, default=None):
            if value is None:
                return default
            return value if isinstance(value, (list, dict)) else json.loads(value)

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "track_name": row["track_name"],
            "display_name": row["display_name"] or row["track_name"],
            "genre": row["genre"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
            "track_path": row["track_path"],
            "reference_path": row["reference_path"],
            "analysis_mode": row["analysis_mode"],
            "beat_path": row["beat_path"],
            "vocal_path": row["vocal_path"],
            "segments": json_value(row["segments_json"], []),
            "chat_history": json_value(row["chat_history_json"], []),
            "vibe_description": row["vibe_description"],
            "old_spectrum": json_value(row["old_spectrum_json"]),
            "old_frequencies": json_value(row["old_frequencies_json"]),
            "analysis_data": json_value(row.get("analysis_json")),
            "context_summary": row.get("context_summary") or "",
            "context_summary_message_count": row.get(
                "context_summary_message_count",
                0,
            ),
            "file_metadata": file_metadata
            or [],
        }

    def save_session(self, session, owner_id):
        if not owner_id or session.get("user_id") != owner_id:
            raise PermissionError("Сохранять сессию может только её владелец.")
        now = datetime.now(timezone.utc)
        params = (
            session["id"], owner_id, session["track_name"],
            session.get("display_name", session["track_name"]),
            session["genre"], session.get("created_at", now),
            now, session.get("track_path"), session.get("reference_path"),
            session.get("analysis_mode", "master"), session.get("beat_path"),
            session.get("vocal_path"),
            json.dumps(session.get("segments", []), ensure_ascii=False),
            json.dumps(session.get("chat_history", []), ensure_ascii=False),
            session.get("vibe_description", ""),
            json.dumps(session["old_spectrum"])
            if session.get("old_spectrum") is not None else None,
            json.dumps(session["old_frequencies"])
            if session.get("old_frequencies") is not None else None,
            json.dumps(session["analysis_data"], ensure_ascii=False)
            if session.get("analysis_data") is not None else None,
            session.get("context_summary", ""),
            session.get("context_summary_message_count", 0),
        )
        try:
            with self.connection.transaction():
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO sessions (
                          id, user_id, track_name, display_name, genre, created_at,
                          updated_at, track_path, reference_path, analysis_mode,
                          beat_path, vocal_path, segments_json, chat_history_json,
                          vibe_description, old_spectrum_json, old_frequencies_json,
                          analysis_json, context_summary,
                          context_summary_message_count
                        ) VALUES (
                          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          %s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb,
                          %s,%s::jsonb
                        )
                        ON CONFLICT (id) DO UPDATE SET
                          track_name = EXCLUDED.track_name,
                          display_name = CASE
                            WHEN sessions.display_name = sessions.track_name
                            THEN EXCLUDED.display_name ELSE sessions.display_name END,
                          genre = EXCLUDED.genre,
                          updated_at = EXCLUDED.updated_at,
                          track_path = EXCLUDED.track_path,
                          reference_path = EXCLUDED.reference_path,
                          analysis_mode = EXCLUDED.analysis_mode,
                          beat_path = EXCLUDED.beat_path,
                          vocal_path = EXCLUDED.vocal_path,
                          segments_json = EXCLUDED.segments_json,
                          chat_history_json = EXCLUDED.chat_history_json,
                          vibe_description = EXCLUDED.vibe_description,
                          old_spectrum_json = EXCLUDED.old_spectrum_json,
                          old_frequencies_json = EXCLUDED.old_frequencies_json,
                          analysis_json = EXCLUDED.analysis_json,
                          context_summary = EXCLUDED.context_summary,
                          context_summary_message_count =
                            EXCLUDED.context_summary_message_count
                        WHERE sessions.user_id = EXCLUDED.user_id
                        RETURNING id
                        """,
                        params,
                    )
                    if cursor.fetchone() is None:
                        raise PermissionError(
                            "Нельзя сохранить сессию другого пользователя."
                        )
                    for item in session.get("file_metadata", []):
                        object_key = item["object_key"]
                        if not object_key.startswith(f"{owner_id}/{session['id']}/"):
                            raise PermissionError(
                                "Метаданные содержат файл другой сессии."
                            )
                        cursor.execute(
                            """
                            INSERT INTO session_files (
                              session_id, owner_id, object_key, role,
                              original_name, content_type, byte_size
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (object_key) DO UPDATE SET
                              content_type = EXCLUDED.content_type,
                              byte_size = EXCLUDED.byte_size
                            WHERE session_files.session_id = EXCLUDED.session_id
                              AND session_files.owner_id = EXCLUDED.owner_id
                            RETURNING object_key
                            """,
                            (
                                session["id"], owner_id, object_key, item["role"],
                                item["original_name"], item.get("content_type"),
                                item["byte_size"],
                            ),
                        )
                        if cursor.fetchone() is None:
                            raise PermissionError(
                                "Файл уже привязан к другой сессии."
                            )
        except (PermissionError, ValueError):
            raise
        except Exception as error:
            raise SupabaseStoreError("Ошибка сохранения сессии в Supabase.") from error

    @_translate_database_errors
    def load_session(self, session_id, owner_id):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM sessions WHERE id = %s AND user_id = %s",
                (session_id, owner_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_session(
            row,
            self._session_files(session_id, owner_id),
        )

    @_translate_database_errors
    def list_sessions(self, owner_id):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, track_name, display_name, genre, created_at,
                       updated_at, chat_history_json
                FROM sessions WHERE user_id = %s ORDER BY updated_at DESC
                """,
                (owner_id,),
            )
            rows = cursor.fetchall()
        result = []
        for row in rows:
            history = row["chat_history_json"] or []
            latest = next(
                (message["content"] for message in reversed(history)
                 if message.get("role") == "assistant"),
                "Пока нет ответа ИИ.",
            )
            result.append({
                "id": row["id"],
                "track_name": row["track_name"],
                "display_name": row["display_name"] or row["track_name"],
                "genre": row["genre"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
                "latest_answer": latest,
            })
        return result

    @_translate_database_errors
    def _load_by_share_token(self, token):
        if not isinstance(token, str) or not token:
            return None
        token_hash = hashlib.sha256(token.encode("ascii", "strict")).hexdigest()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT sessions.*
                FROM sessions
                WHERE share_token_hash = %s AND share_enabled = true
                  AND share_revoked_at IS NULL
                """,
                (token_hash,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_session(
            row,
            self._session_files(row["id"], row["user_id"]),
        )

    @_translate_database_errors
    def load_shared_session(self, token):
        try:
            return self._load_by_share_token(token)
        except (UnicodeEncodeError, ValueError):
            return None

    @_translate_database_errors
    def create_share_token(self, session_id, owner_id):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE sessions
                SET share_token_hash = %s, shared_at = %s,
                    share_revoked_at = NULL, share_enabled = true
                WHERE id = %s AND user_id = %s
                RETURNING id
                """,
                (token_hash, datetime.now(timezone.utc), session_id, owner_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("Сессия не найдена.")
        return token

    @_translate_database_errors
    def revoke_share_token(self, session_id, owner_id):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE sessions
                SET share_token_hash = NULL, share_revoked_at = %s,
                    share_enabled = false
                WHERE id = %s AND user_id = %s
                RETURNING id
                """,
                (datetime.now(timezone.utc), session_id, owner_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("Сессия не найдена.")

    @_translate_database_errors
    def rename_session(self, session_id, owner_id, display_name):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE sessions SET display_name = %s, updated_at = %s
                WHERE id = %s AND user_id = %s RETURNING id
                """,
                (
                    display_name,
                    datetime.now(timezone.utc),
                    session_id,
                    owner_id,
                ),
            )
            if cursor.fetchone() is None:
                raise ValueError("Сессия не найдена.")

    def list_session_files(self, session_id, owner_id):
        return self._session_files(session_id, owner_id)

    @_translate_database_errors
    def delete_session_record(self, session_id, owner_id):
        with self.connection.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM message_feedback
                    WHERE session_id = %s AND user_id = %s
                    """,
                    (session_id, owner_id),
                )
                cursor.execute(
                    "DELETE FROM sessions WHERE id = %s AND user_id = %s RETURNING id",
                    (session_id, owner_id),
                )
                if cursor.fetchone() is None:
                    raise ValueError("Сессия не найдена.")

    @_translate_database_errors
    def save_message_feedback(self, session_id, owner_id, message_index, rating):
        with self.connection.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT chat_history_json FROM sessions
                    WHERE id = %s AND user_id = %s FOR UPDATE
                    """,
                    (session_id, owner_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("Сессия не найдена.")
                history = row["chat_history_json"] or []
                if (
                    message_index < 0
                    or message_index >= len(history)
                    or history[message_index].get("role") != "assistant"
                ):
                    raise ValueError("Ответ ИИ для оценки не найден.")
                cursor.execute(
                    """
                    INSERT INTO message_feedback
                      (session_id, message_index, user_id, rating)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (session_id, message_index)
                    DO UPDATE SET rating = EXCLUDED.rating
                    WHERE message_feedback.user_id = EXCLUDED.user_id
                    """,
                    (session_id, message_index, owner_id, rating),
                )

    @_translate_database_errors
    def get_message_feedbacks(self, session_id, owner_id):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT f.message_index, f.rating
                FROM message_feedback f
                JOIN sessions s ON s.id = f.session_id
                WHERE f.session_id = %s AND f.user_id = %s
                  AND s.user_id = %s
                """,
                (session_id, owner_id, owner_id),
            )
            return {
                row["message_index"]: row["rating"] for row in cursor.fetchall()
            }

    @_translate_database_errors
    def allow_ai_request(self, user_id, ip_hash=None):
        now = datetime.now(timezone.utc)
        keys = [f"user:{user_id}"]
        if ip_hash:
            keys.append(f"ip:{ip_hash}")
        try:
            with self.connection.transaction():
                with self.connection.cursor() as cursor:
                    for key in keys:
                        cursor.execute(
                            """
                            INSERT INTO request_rate_limits (
                              rate_key, last_request_at
                            ) VALUES (%s,%s)
                            ON CONFLICT (rate_key) DO UPDATE
                              SET last_request_at = EXCLUDED.last_request_at
                              WHERE request_rate_limits.last_request_at
                                <= EXCLUDED.last_request_at
                                  - (%s * interval '1 second')
                            RETURNING rate_key
                            """,
                            (key, now, RATE_LIMIT_SECONDS),
                        )
                        if cursor.fetchone() is None:
                            raise _RateLimitReject
        except _RateLimitReject:
            return False
        return True

    @_translate_database_errors
    def record_ai_usage(
        self, user_id, session_id, model, prompt_tokens,
        completion_tokens, latency_ms, attempts,
    ):
        import psycopg

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ai_request_logs (
                      user_id, session_id, model, prompt_tokens,
                      completion_tokens, latency_ms, attempts
                    )
                    SELECT %s,%s,%s,%s,%s,%s,%s
                    WHERE EXISTS (
                      SELECT 1 FROM sessions
                      WHERE id = %s AND user_id = %s
                    )
                    """,
                    (
                        user_id, session_id, model, prompt_tokens,
                        completion_tokens, latency_ms, attempts,
                        session_id, user_id,
                    ),
                )
        except psycopg.Error as error:
            raise SupabaseStoreError(
                "Не удалось записать диагностику AI-запроса."
            ) from error

    @_translate_database_errors
    def save_analysis(self, session_id, owner_id, analysis_data):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE sessions SET analysis_json = %s::jsonb, updated_at = %s
                WHERE id = %s AND user_id = %s
                """,
                (
                    json.dumps(analysis_data, ensure_ascii=False),
                    datetime.now(timezone.utc),
                    session_id,
                    owner_id,
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError("Сессия не найдена.")


def get_supabase_store():
    return SupabaseStore()
