import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from utils import session_store


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "sessions.db"
        self.database_patch = patch.object(
            session_store,
            "DATABASE_PATH",
            self.database_path,
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.addCleanup(self.temporary_directory.cleanup)

    @staticmethod
    def make_session(session_id="session-1", user_id="owner-1"):
        return {
            "id": session_id,
            "user_id": user_id,
            "track_name": "track.wav",
            "display_name": "track.wav",
            "genre": "Рэп",
            "created_at": "2026-01-01T00:00:00+00:00",
            "track_path": "history/session-1/track.wav",
            "segments": [{"name": "Куплет", "start": 0, "end": 10}],
            "chat_history": [
                {"role": "user", "content": "Как звучит трек?"},
                {"role": "assistant", "content": "Проверь баланс."},
            ],
            "old_spectrum": [-12.0, -6.0],
            "old_frequencies": [0.0, 3.9],
        }

    def test_create_load_list_and_delete_are_owner_scoped(self):
        session_store.save_session(self.make_session())

        loaded = session_store.load_session("session-1", "owner-1")
        self.assertEqual(loaded["segments"][0]["name"], "Куплет")
        self.assertEqual(len(loaded["chat_history"]), 2)
        self.assertEqual(loaded["old_spectrum"], [-12.0, -6.0])
        self.assertEqual(loaded["old_frequencies"], [0.0, 3.9])
        self.assertIsNone(session_store.load_session("session-1", "other-user"))
        self.assertEqual(
            [item["id"] for item in session_store.list_sessions("owner-1")],
            ["session-1"],
        )
        self.assertEqual(session_store.list_sessions("other-user"), [])

        with self.assertRaises(ValueError):
            session_store.delete_session("session-1", "other-user")
        self.assertIsNotNone(session_store.load_session("session-1", "owner-1"))

        session_store.delete_session("session-1", "owner-1")
        self.assertIsNone(session_store.load_session("session-1", "owner-1"))

    def test_upsert_cannot_transfer_session_to_another_owner(self):
        session_store.save_session(self.make_session())
        attempted_transfer = self.make_session(user_id="other-user")
        attempted_transfer["track_name"] = "attacker.wav"

        with self.assertRaises(ValueError):
            session_store.save_session(attempted_transfer)

        loaded = session_store.load_session("session-1", "owner-1")
        self.assertEqual(loaded["track_name"], "track.wav")
        self.assertIsNone(
            session_store.load_session("session-1", "other-user")
        )

    def test_repeated_save_updates_session_without_changing_its_owner(self):
        session_store.save_session(self.make_session())
        session_store.rename_session("session-1", "owner-1", "Custom")
        updated = self.make_session()
        updated["chat_history"].append(
            {"role": "user", "content": "И еще вопрос."}
        )
        updated["display_name"] = "Renamed"
        session_store.save_session(updated)

        loaded = session_store.load_session("session-1", "owner-1")
        self.assertEqual(loaded["display_name"], "Custom")
        self.assertEqual(len(loaded["chat_history"]), 3)

    def test_existing_database_migrates_without_losing_session_history(self):
        connection = sqlite3.connect(self.database_path)
        connection.execute(
            """
            CREATE TABLE sessions (
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
                vibe_description TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            INSERT INTO sessions (
                id, user_id, track_name, display_name, genre, created_at,
                updated_at, track_path, reference_path, analysis_mode, beat_path,
                vocal_path, segments_json, chat_history_json, vibe_description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-session",
                "owner-1",
                "old.wav",
                "old.wav",
                "Рэп",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "history/legacy-session/old.wav",
                None,
                "master",
                None,
                None,
                "[]",
                '[{"role":"user","content":"saved"}]',
                "",
            ),
        )
        connection.commit()
        connection.close()

        loaded = session_store.load_session("legacy-session", "owner-1")

        self.assertEqual(loaded["chat_history"][0]["content"], "saved")
        self.assertIsNone(loaded["old_spectrum"])
        self.assertIsNone(loaded["old_frequencies"])

    def test_rename_and_message_rate_limit_are_scoped(self):
        session_store.save_session(self.make_session())
        with self.assertRaises(ValueError):
            session_store.rename_session("session-1", "other-user", "Changed")

        session_store.rename_session("session-1", "owner-1", "Mix v2")
        self.assertEqual(
            session_store.load_session("session-1", "owner-1")["display_name"],
            "Mix v2",
        )
        self.assertTrue(session_store.allow_ai_request("owner-1"))
        self.assertFalse(session_store.allow_ai_request("owner-1"))
        self.assertTrue(session_store.allow_ai_request("other-user"))

    def test_concurrent_requests_apply_the_rate_limit_once(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(
                    lambda _: session_store.allow_ai_request("parallel-user"),
                    range(6),
                )
            )

        self.assertEqual(sum(results), 1)

    def test_recovery_token_opens_only_one_session(self):
        session_store.save_session(self.make_session("session-1", "owner-1"))
        session_store.save_session(self.make_session("session-2", "owner-1"))

        token = session_store.create_recovery_token("session-1", "owner-1")

        loaded = session_store.load_session_for_recovery_token(token)
        self.assertEqual(loaded["id"], "session-1")
        self.assertIsNotNone(
            session_store.load_session_for_recovery_token(token)
        )
        self.assertIsNone(
            session_store.load_session_for_recovery_token("wrong-token")
        )

        updated = self.make_session("session-1", "attacker")
        updated["chat_history"] = [{"role":"assistant","content":"updated"}]
        session_store.save_session_for_recovery_token(token, updated)
        self.assertEqual(
            session_store.load_session("session-1", "owner-1")["chat_history"][0]["role"],
            "assistant",
        )
        self.assertIsNotNone(session_store.load_session("session-2", "owner-1"))

    def test_delete_session_removes_only_owned_files_inside_data_directory(self):
        with tempfile.TemporaryDirectory() as data_directory:
            data_root = Path(data_directory)
            with patch.object(session_store, "DATA_DIRECTORY", data_root):
                session_dir = data_root / "history" / "session-delete"
                session_dir.mkdir(parents=True)
                track = session_dir / "track.wav"
                track.write_bytes(b"audio")
                session_store.save_session({
                    **self.make_session("session-delete", "owner-1"),
                    "track_path": str(track),
                })

                session_store.delete_session("session-delete", "owner-1")

                self.assertFalse(track.exists())
                self.assertIsNone(
                    session_store.load_session("session-delete", "owner-1")
                )


if __name__ == "__main__":
    unittest.main()
