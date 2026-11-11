"""Password hashing (argon2id) and opaque credential secrets.

* Passwords: argon2id with argon2-cffi's RFC 9106 "low memory" profile (t=3, m=64 MiB, p=4). At most
  ``hash_concurrency`` hashes run at once so a burst of logins cannot exhaust the 16 GB budget.
* Session/bearer/personal-access tokens: 256-bit ``secrets`` values. Only SHA-256 digests are stored;
  a high-entropy random secret does not need a slow hash.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

TOKEN_PREFIXES = {"browser": "cgis_s_", "bearer": "cgis_b_", "pat": "cgis_pat_"}
TOKEN_RE = re.compile(r"^cgis_(s|b|pat)_[A-Za-z0-9_-]{43}$")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")

_hasher = PasswordHasher()  # argon2id, RFC 9106 low-memory parameters
_hash_slots = threading.BoundedSemaphore(2)
# Verifying against this when the username is unknown keeps response timing uniform.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))


def configure(hash_concurrency: int) -> None:
    global _hash_slots
    _hash_slots = threading.BoundedSemaphore(max(1, hash_concurrency))


def hash_password(password: str) -> str:
    with _hash_slots:
        return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Constant-work verification; also returns False for unknown users (``stored_hash=None``)."""
    with _hash_slots:
        try:
            return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


_COMMON = {"password", "passw0rd", "123456789012", "qwertyuiop12", "letmein12345", "changeme1234",
           "administrator", "cybergis1234", "cgis-admin-1234"}


def password_problems(password: str, username: str, min_length: int) -> list[str]:
    problems = []
    if len(password) < min_length:
        problems.append(f"must be at least {min_length} characters")
    if len(password) > 1024:
        problems.append("must be at most 1024 characters")
    lowered = password.lower()
    if re.search(r"change[-_]?me", lowered) or lowered in _COMMON:
        problems.append("is a placeholder or a common password")
    if username and username.lower() in lowered:
        problems.append("must not contain the username")
    if len(set(password)) < 6:
        problems.append("is too repetitive")
    return problems


def normalize_username(raw: str) -> str:
    return raw.strip().lower()


def new_token(kind: str) -> str:
    return TOKEN_PREFIXES[kind] + secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def looks_like_token(value: str) -> bool:
    return bool(TOKEN_RE.match(value))


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
