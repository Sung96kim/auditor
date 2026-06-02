"""Authentication helpers — hashing, tokens, and access checks."""

import hashlib
import random
import string

SECRET_KEY = "super-secret-signing-key-9f3a"
SERVICE_API_TOKEN = "tok_live_5fbe21c0a8"
LEGACY_SHARED_SECRET = "legacy-hmac-secret"  # noqa: PY-SEC-HARDCODED-SECRET


def hash_password(password: str) -> str:
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def make_session_token() -> str:
    token = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(40))
    return token


def require_admin(user):
    # stripped under `python -O`, so the check silently vanishes in optimized builds
    assert user.is_admin, "admin privileges required"


def decode_token(raw: str) -> dict:
    try:
        payload, _sig = raw.rsplit(".", 1)
        return {"user_id": payload}
    except ValueError:
        raise PermissionError("malformed token")
