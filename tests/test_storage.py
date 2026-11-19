import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from utils import storage
from utils.storage import AudioStorageError, SupabaseAudioStorage


class FakeBucket:
    def __init__(self):
        self.uploaded = None
        self.removed = []
        self.upload_error = None

    def upload(self, key, path, options):
        if self.upload_error:
            raise self.upload_error
        self.uploaded = (key, Path(path).read_bytes(), options)

    def remove(self, paths):
        self.removed.extend(paths)


class FakeStorageClient:
    def __init__(self):
        self.bucket = FakeBucket()
        self.storage = SimpleNamespace(from_=lambda _: self.bucket)


class SupabaseAudioStorageTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeStorageClient()
        secrets = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASESERVICEROLE_KEY": "server-only-test-key",
        }
        self.secret_patch = patch.object(
            storage,
            "get_server_secret",
            side_effect=lambda name: secrets.get(name),
        )
        self.secret_patch.start()
        self.addCleanup(self.secret_patch.stop)

    def test_upload_uses_streamable_file_and_scoped_object_key(self):
        adapter = SupabaseAudioStorage(self.client)

        key = adapter.save_upload(
            "owner-1",
            "session-1",
            "track.wav",
            memoryview(b"audio bytes"),
            "audio/wav",
        )

        self.assertTrue(key.startswith("owner-1/session-1/"))
        self.assertEqual(self.client.bucket.uploaded[1], b"audio bytes")
        self.assertEqual(
            self.client.bucket.uploaded[2]["content-type"],
            "audio/wav",
        )

    def test_failed_upload_attempts_to_remove_a_partially_created_object(self):
        self.client.bucket.upload_error = RuntimeError("connection lost")
        adapter = SupabaseAudioStorage(self.client)

        with self.assertRaises(AudioStorageError):
            adapter.save_upload(
                "owner-1",
                "session-1",
                "track.wav",
                memoryview(b"audio bytes"),
                "audio/wav",
            )

        self.assertEqual(len(self.client.bucket.removed), 1)
        self.assertTrue(
            self.client.bucket.removed[0].startswith("owner-1/session-1/")
        )

    def test_download_streams_authenticated_bytes_to_owned_file(self):
        adapter = SupabaseAudioStorage(self.client)
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            iter_bytes=lambda size: iter((b"audio ", b"data")),
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "restored.wav"
            with patch.object(storage.httpx, "stream", return_value=Context(response)) as stream:
                adapter.download_to_path(
                    "owner-1",
                    "session-1",
                    "owner-1/session-1/track.wav",
                    destination,
                )

            self.assertEqual(destination.read_bytes(), b"audio data")
            self.assertEqual(
                stream.call_args.kwargs["headers"]["Authorization"],
                "Bearer server-only-test-key",
            )

    def test_download_rejects_another_owners_key_before_network(self):
        adapter = SupabaseAudioStorage(self.client)
        with patch.object(storage.httpx, "stream") as stream:
            with self.assertRaises(PermissionError):
                adapter.download_to_path(
                    "owner-1",
                    "session-1",
                    "owner-2/session-1/track.wav",
                    Path("unused.wav"),
                )
        stream.assert_not_called()

    def test_failed_stream_removes_partial_file_and_reports_error(self):
        adapter = SupabaseAudioStorage(self.client)
        def fail_after_partial(*args, **kwargs):
            class BrokenResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *exc_info):
                    return False

                def iter_bytes(self, size):
                    yield b"partial"
                    raise storage.httpx.ReadError("connection lost")

                def raise_for_status(self):
                    return None

            return BrokenResponse()

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "broken.wav"
            with patch.object(storage.httpx, "stream", new=fail_after_partial):
                with self.assertRaises(AudioStorageError):
                    adapter.download_to_path(
                        "owner-1",
                        "session-1",
                        "owner-1/session-1/track.wav",
                        destination,
                    )
            self.assertFalse(destination.exists())

    def test_public_audio_access_requires_a_current_matching_share_token(self):
        session = {
            "id": "session-1",
            "user_id": "owner-1",
            "track_path": "owner-1/session-1/track.wav",
            "file_metadata": [],
        }
        with patch(
            "utils.session_store.load_shared_session",
            side_effect=[session, None],
        ) as load_shared:
            authorized, owner_id = storage.authorize_session_audio(
                "session-1",
                "owner-1/session-1/track.wav",
                share_token="live-token",
            )
            self.assertEqual(authorized["id"], "session-1")
            self.assertEqual(owner_id, "owner-1")

            with self.assertRaises(PermissionError):
                storage.authorize_session_audio(
                    "session-1",
                    "owner-1/session-1/track.wav",
                    share_token="revoked-token",
                )
        self.assertEqual(load_shared.call_count, 2)


class Context:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, *exc_info):
        return False


if __name__ == "__main__":
    unittest.main()
