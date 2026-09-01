"""Desk password gate. Email is hardcoded; password is ADMIN_PASSWORD env.

Salted pbkdf2_hmac SHA-256 + hmac.compare_digest. Session is an httponly HMAC
cookie, never the plaintext password. Drinks service does not use this gate.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import Request
from fastapi.responses import Response

ADMIN_EMAIL = "glenn.will799@gmail.com"
COOKIE_NAME = "bakery_desk"
PBKDF2_ITERS = 120_000
SESSION_MAX_AGE = 12 * 60 * 60
_COOKIE_NS = b"bakery-desk-session-v1"


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _admin_password() -> str:
    return (os.environ.get("ADMIN_PASSWORD") or "").strip()


def _digest_eq(left: str, right: str) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def _email_digest(email: str) -> str:
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()


def _hash_password(plain: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", (plain or "").encode("utf-8"), salt, PBKDF2_ITERS)


def verify_credentials(email: str, password: str) -> bool:
    """True only when email matches ADMIN_EMAIL and password matches env.

    Hashes the submitted password and the env password with a fresh salt; never
    compares or returns plaintext. Empty ADMIN_PASSWORD cannot succeed.
    """
    expected = _admin_password()
    if not expected:
        return False
    salt = os.urandom(16)
    expected_hash = _hash_password(expected, salt)
    got_hash = _hash_password(password, salt)
    email_ok = _digest_eq(_email_digest(email), _email_digest(ADMIN_EMAIL))
    password_ok = hmac.compare_digest(expected_hash, got_hash)
    return bool(email_ok and password_ok)


def _cookie_key() -> bytes:
    password = _admin_password()
    if not password:
        return b""
    return hmac.new(_COOKIE_NS, password.encode("utf-8"), hashlib.sha256).digest()


def _cookie_secure() -> bool:
    if (os.environ.get("K_SERVICE") or "").strip():
        return True
    return (os.environ.get("SESSION_HTTPS") or "").strip() == "1"


def make_session_value() -> str:
    issued = str(int(time.time()))
    payload = f"v1.{issued}"
    key = _cookie_key()
    mac = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def session_ok(request: Request) -> bool:
    if not _admin_password():
        return False
    raw = request.cookies.get(COOKIE_NAME) or ""
    parts = raw.rsplit(".", 1)
    if len(parts) != 2:
        return False
    payload, mac = parts
    chunks = payload.split(".")
    if len(chunks) != 2 or chunks[0] != "v1":
        return False
    try:
        issued = int(chunks[1])
    except ValueError:
        return False
    now = int(time.time())
    if issued < now - SESSION_MAX_AGE or issued > now + 60:
        return False
    key = _cookie_key()
    if not key:
        return False
    expected = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not _digest_eq(mac, expected):
        return False
    return True


def set_session_cookie(response: Response) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_session_value(),
        max_age=SESSION_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def path_is_protected(path: str) -> bool:
    if path == "/":
        return True
    return (
        path.startswith("/reports")
        or path.startswith("/weekend")
        or path.startswith("/loyalty")
    )
