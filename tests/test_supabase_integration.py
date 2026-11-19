import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from utils import session_store
from utils.storage import (
    authorize_session_audio,
    delete_session_audio,
    load_audio_bytes,
    save_audio_upload,
)


_REQUIRED_ENVIRONMENT = (
    "SVEDOS_ENV",
    "SVEDOSSTORAGEBACKEND",
    "SUPABASEDBURL",
    "SUPABASE_URL",
    "SUPABASESERVICEROLE_KEY",
)
_HAS_SUPABASE_CONFIGURATION = (
    os.environ.get("SVEDOS_ENV", "").lower() == "production"
    and os.environ.get("SVEDOSSTORAGEBACKEND", "").lower() == "supabase"
    and all(os.environ.get(name) for name in _REQUIRED_ENVIRONMENT[2:])
)


@unittest.skipUnless(
    _HAS_SUPABASE_CONFIGURATION,
    "Set Supabase integration variables in the local environment to run this test.",
)
class SupabaseLifecycleIntegrationTests(unittest.TestCase):
    def test_owner_audio_share_revoke_and_delete_lifecycle(self):
        owner_id = str(uuid4())
        session_id = str(uuid4())
        uploaded_file = SimpleNamespace(
            name="integration-test.wav",
            type="audio/wav",
            getbuffer=lambda: memoryview(b"temporary integration audio"),
        )
        object_key = None
        session_saved = False

        try:
            object_key, file_metadata = save_audio_upload(
                owner_id,
                session_id,
                uploaded_file,
                "track",
            )
            session_store.save_session(
                {
                    "id": session_id,
                    "user_id": owner_id,
                    "track_name": uploaded_file.name,
                    "display_name": uploaded_file.name,
                    "genre": "Рэп",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "track_path": object_key,
                    "reference_path": None,
                    "analysis_mode": "master",
                    "beat_path": None,
                    "vocal_path": None,
                    "segments": [{"name": "Intro", "start": 0, "end": 1}],
                    "chat_history": [
                        {"role": "user", "content": "Integration check."}
                    ],
                    "analysis_data": {"loudness": -12.0},
                    "file_metadata": [file_metadata],
                },
                owner_id,
            )
            session_saved = True

            loaded = session_store.load_session(session_id, owner_id)
            self.assertEqual(loaded["segments"][0]["name"], "Intro")
            self.assertEqual(loaded["chat_history"][0]["content"], "Integration check.")
            self.assertEqual(loaded["file_metadata"][0]["object_key"], object_key)
            self.assertIsNone(session_store.load_session(session_id, str(uuid4())))
            self.assertIn(
                session_id,
                {item["id"] for item in session_store.list_sessions(owner_id)},
            )

            session_store.rename_session(session_id, owner_id, "Integration mix")
            session_store.save_analysis(
                session_id,
                owner_id,
                {"loudness": -10.0, "segment_count": 1},
            )
            self.assertEqual(
                session_store.load_session(session_id, owner_id)["display_name"],
                "Integration mix",
            )
            self.assertEqual(
                session_store.load_session(session_id, owner_id)["analysis_data"],
                {"loudness": -10.0, "segment_count": 1},
            )
            self.assertEqual(
                load_audio_bytes(session_id, object_key, user_id=owner_id),
                b"temporary integration audio",
            )

            share_token = session_store.create_share_token(session_id, owner_id)
            self.assertEqual(
                session_store.load_shared_session(share_token)["id"],
                session_id,
            )
            self.assertEqual(
                load_audio_bytes(
                    session_id,
                    object_key,
                    share_token=share_token,
                ),
                b"temporary integration audio",
            )
            session_store.revoke_share_token(session_id, owner_id)
            self.assertIsNone(session_store.load_shared_session(share_token))
            with self.assertRaises(PermissionError):
                authorize_session_audio(
                    session_id,
                    object_key,
                    share_token=share_token,
                )

            session_store.delete_session(session_id, owner_id)
            session_saved = False
            self.assertIsNone(session_store.load_session(session_id, owner_id))
        finally:
            if session_saved:
                session_store.delete_session(session_id, owner_id)
            elif object_key:
                delete_session_audio(owner_id, session_id, {object_key})


if __name__ == "__main__":
    unittest.main()
