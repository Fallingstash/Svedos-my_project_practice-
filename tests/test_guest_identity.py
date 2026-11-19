import os
import unittest
from unittest.mock import patch

from utils.guest_identity import (
    create_guest_id,
    get_guest_cookie_secret,
    sign_guest_id,
    verify_signed_guest_id,
)


class GuestIdentityTests(unittest.TestCase):
    def test_signed_cookie_round_trip_and_signature_verification(self):
        guest_id = "550e8400-e29b-41d4-a716-446655440000"
        secret = "test-secret"
        signed = sign_guest_id(guest_id, secret)

        self.assertEqual(verify_signed_guest_id(signed, secret), guest_id)
        self.assertIsNone(verify_signed_guest_id(signed, "wrong-secret"))
        altered = signed[:-1] + ("a" if signed[-1] != "a" else "b")
        self.assertIsNone(verify_signed_guest_id(altered, secret))

    def test_new_guest_gets_new_uuid(self):
        first = create_guest_id()
        second = create_guest_id()
        self.assertNotEqual(first, second)

    def test_cookie_secret_falls_back_to_server_environment(self):
        with patch.dict(
            os.environ,
            {"GUESTCOOKIESECRET": "test-secret"},
        ):
            self.assertEqual(get_guest_cookie_secret(), "test-secret")
