import hashlib
import hmac
import os
from uuid import uuid4


COOKIE_NAME = "svedosguestid"
COOKIE_MAX_AGE_SECONDS = 365 * 24 * 60 * 60


def get_secret(name):
    try:
        import streamlit as st

        if name in st.secrets:
            value = st.secrets[name]
            if value:
                return str(value)
    except (FileNotFoundError, KeyError):
        pass
    value = os.environ.get(name)
    return str(value) if value else None


def get_guest_cookie_secret():
    secret = get_secret("GUESTCOOKIESECRET")
    if not secret:
        raise RuntimeError(
            "GUESTCOOKIESECRET не настроен. "
            "Постоянная гостевая идентичность недоступна."
        )
    return secret


def sign_guest_id(guest_id, secret):
    if not isinstance(guest_id, str) or not guest_id:
        raise ValueError("Некорректный гостевой идентификатор.")
    signature = hmac.new(
        secret.encode("utf-8"),
        guest_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{guest_id}.{signature}"


def verify_signed_guest_id(signed_value, secret):
    if not isinstance(signed_value, str) or "." not in signed_value:
        return None
    guest_id, signature = signed_value.rsplit(".", 1)
    expected = hmac.new(
        secret.encode("utf-8"),
        guest_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return guest_id


def create_guest_id():
    return str(uuid4())
