import os
import unittest
from unittest.mock import patch

from utils import config


class StorageConfigurationTests(unittest.TestCase):
    def test_production_does_not_fall_back_to_local_without_supabase_config(self):
        with patch.dict(os.environ, {"SVEDOS_ENV": "production"}, clear=True):
            with patch.object(
                config,
                "get_server_secret",
                side_effect=lambda name: (
                    "production" if name == "SVEDOS_ENV" else None
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "production"):
                    config.validate_storage_configuration()

    def test_supabase_requires_database_storage_and_public_url_settings(self):
        values = {
            "SVEDOSSTORAGEBACKEND": "supabase",
            "SUPABASEDBURL": "postgresql://db",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASESERVICEROLE_KEY": "server-key",
            "APPBASEURL": "https://example.test",
            "GUESTCOOKIESECRET": "test-cookie-secret-at-least-thirty-two-bytes",
            "API_KEY": "openrouter-test-key",
        }
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                config,
                "get_server_secret",
                side_effect=lambda name: (
                    "production" if name == "SVEDOS_ENV" else values.get(name)
                ),
            ):
                self.assertEqual(
                    config.validate_storage_configuration(),
                    "supabase",
                )

    def test_production_rejects_short_guest_cookie_secrets(self):
        values = {
            "SVEDOSSTORAGEBACKEND": "supabase",
            "SUPABASEDBURL": "postgresql://db",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASESERVICEROLE_KEY": "server-key",
            "APPBASEURL": "https://example.test",
            "GUESTCOOKIESECRET": "too-short",
            "API_KEY": "openrouter-test-key",
        }
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                config,
                "get_server_secret",
                side_effect=lambda name: (
                    "production" if name == "SVEDOS_ENV" else values.get(name)
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "32 байт"):
                    config.validate_storage_configuration()

    def test_production_mode_from_streamlit_secrets_never_falls_back_to_local(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                config,
                "get_server_secret",
                side_effect=lambda name: (
                    "production" if name == "SVEDOS_ENV" else None
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "SVEDOSSTORAGEBACKEND"):
                    config.validate_storage_configuration()


if __name__ == "__main__":
    unittest.main()
