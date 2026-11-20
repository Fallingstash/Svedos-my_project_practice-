import os
import unittest
from unittest.mock import patch

from utils.guest_identity import (
    create_guest_id,
    get_guest_cookie_secret,
    resolve_guest_identity,
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

    def test_identity_waits_for_cookie_component_before_creating_guest(self):
        guest_id, source = resolve_guest_identity(None, None, False, "test-secret")

        self.assertIsNone(guest_id)
        self.assertEqual(source, "loading")

    def test_valid_component_cookie_restores_guest_when_request_cookie_is_absent(self):
        secret = "test-secret"
        expected_guest_id = "550e8400-e29b-41d4-a716-446655440000"
        signed_cookie = sign_guest_id(expected_guest_id, secret)

        guest_id, source = resolve_guest_identity(
            None,
            signed_cookie,
            True,
            secret,
        )

        self.assertEqual(guest_id, expected_guest_id)
        self.assertEqual(source, "component")

    def test_valid_request_cookie_is_used_while_component_is_loading(self):
        secret = "test-secret"
        expected_guest_id = "550e8400-e29b-41d4-a716-446655440000"
        signed_cookie = sign_guest_id(expected_guest_id, secret)

        guest_id, source = resolve_guest_identity(
            signed_cookie,
            None,
            False,
            secret,
        )

        self.assertEqual(guest_id, expected_guest_id)
        self.assertEqual(source, "request")

    def test_invalid_component_cookie_never_restores_the_signed_identity(self):
        secret = "current-secret"
        invalid_cookie = sign_guest_id(
            "550e8400-e29b-41d4-a716-446655440000",
            "different-secret",
        )

        guest_id, source = resolve_guest_identity(
            None,
            invalid_cookie,
            True,
            secret,
        )

        self.assertNotEqual(
            guest_id,
            "550e8400-e29b-41d4-a716-446655440000",
        )
        self.assertEqual(source, "created")

    def test_cookie_secret_falls_back_to_server_environment(self):
        with patch.dict(
            os.environ,
            {"GUESTCOOKIESECRET": "test-secret"},
        ):
            self.assertEqual(get_guest_cookie_secret(), "test-secret")
