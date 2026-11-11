# Password hashing (argon2id, RFC 9106 low-memory profile, bounded concurrency) and opaque credential
# secrets (256-bit, typed prefixes, stored only as SHA-256 digests).

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


# Set how many argon2 hashes may run at once. Called by app.create_app.
def configure(hash_concurrency: int) -> None:
    global _hash_slots
    _hash_slots = threading.BoundedSemaphore(max(1, hash_concurrency))


# argon2id hash of a password. Used by identity and the login rehash.
def hash_password(password: str) -> str:
    with _hash_slots:
        return _hasher.hash(password)


# Constant-work check; False for unknown users (dummy hash). Used by login, password change, bootstrap.
def verify_password(stored_hash: str | None, password: str) -> bool:
    with _hash_slots:
        try:
            return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


# True when a stored hash uses outdated parameters. Used by routes_auth.login.
def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


_COMMON = {"password", "passw0rd", "123456789012", "qwertyuiop12", "letmein12345", "changeme1234",
           "administrator", "cybergis1234", "cgis-admin-1234"}


# Reasons a new password is too weak (length, placeholder, username, repetition). Used by identity.
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


# Lowercase, trimmed username. Used by identity and routes_auth.login.
def normalize_username(raw: str) -> str:
    return raw.strip().lower()


# New random secret with its kind prefix (cgis_s_/cgis_b_/cgis_pat_). Used by identity.create_session.
def new_token(kind: str) -> str:
    return TOKEN_PREFIXES[kind] + secrets.token_urlsafe(32)


# Random synchronizer CSRF token for browser sessions. Used by identity.create_session.
def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


# SHA-256 digest stored instead of a secret. Used by identity.
def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


# Cheap format check before any DB lookup. Used by identity.resolve_token.
def looks_like_token(value: str) -> bool:
    return bool(TOKEN_RE.match(value))


# Timing-safe string comparison (False if either is empty). Used by auth.check_csrf.
def constant_time_equals(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
