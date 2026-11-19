import hashlib
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import patch

import psycopg

from utils.supabase_store import SupabaseStore, SupabaseStoreError


class FakeCursor:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, query, params=()):
        self.statements.append((query, params))

    def fetchone(self):
        return {"id": "session-1"}


class FakeConnection:
    def __init__(self):
        self.fake_cursor = FakeCursor()

    def cursor(self):
        return self.fake_cursor

    def transaction(self):
        return nullcontext()


class SupabaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        self.store = SupabaseStore(self.connection)

    def test_session_upsert_has_matching_parameters_and_keeps_owner(self):
        session = {
            "id": "session-1",
            "user_id": "owner-1",
            "track_name": "track.wav",
            "display_name": "Mix",
            "genre": "Рэп",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "track_path": "owner-1/session-1/track.wav",
            "segments": [{"name": "Куплет", "start": 0, "end": 10}],
            "chat_history": [{"role": "user", "content": "question"}],
            "context_summary": "earlier choices",
            "context_summary_message_count": 1,
        }

        self.store.save_session(session, "owner-1")

        query, params = self.connection.fake_cursor.statements[0]
        self.assertEqual(query.count("%s"), len(params))
        self.assertIn("WHERE sessions.user_id = EXCLUDED.user_id", query)
        self.assertIn("context_summary_message_count", query)
        self.assertEqual(params[18], "earlier choices")
        self.assertEqual(params[19], 1)

    def test_share_token_is_only_written_as_sha256_hash(self):
        with patch("utils.supabase_store.secrets.token_urlsafe", return_value="raw-share-token"):
            token = self.store.create_share_token("session-1", "owner-1")

        query, params = self.connection.fake_cursor.statements[0]
        self.assertEqual(token, "raw-share-token")
        self.assertIn("share_token_hash", query)
        self.assertIn(hashlib.sha256(token.encode("ascii")).hexdigest(), params)
        self.assertNotIn(token, params)

    def test_database_query_errors_are_reported_as_storage_errors(self):
        def fail_query(*args, **kwargs):
            raise psycopg.OperationalError("connection lost")

        self.connection.fake_cursor.execute = fail_query

        with self.assertRaisesRegex(
            SupabaseStoreError,
            "Supabase PostgreSQL",
        ):
            self.store.load_session("session-1", "owner-1")


if __name__ == "__main__":
    unittest.main()
