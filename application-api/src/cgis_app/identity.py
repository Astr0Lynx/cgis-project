"""Accounts, sessions, personal access tokens and login throttling (storage layer, no HTTP).

Roles: ``admin`` (upload/validate/publish/version datasets, set access policy, read/export) and
``user`` (read/export permitted datasets only). The effective role of a request is the account role,
reduced to ``user`` for a ``read``-scoped personal access token.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from . import security
from .config import Settings

log = logging.getLogger(__name__)

ROLES = ("admin", "user")


@dataclass(frozen=True)
class User:
    user_id: uuid.UUID
    username: str
    role: str
    disabled: bool
    password_hash: str
    password_changed_at: datetime

    def public(self) -> dict[str, Any]:
        return {"user_id": str(self.user_id), "username": self.username, "role": self.role}


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of one request."""

    user_id: uuid.UUID
    username: str
    account_role: str
    session_id: uuid.UUID
    kind: str  # browser | bearer | pat
    scope: str  # full | read | admin
    expires_at: datetime
    csrf_token: str | None = None

    @property
    def role(self) -> str:
        if self.kind == "pat" and self.scope == "read":
            return "user"
        return self.account_role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict[str, Any]:
        return {"user_id": str(self.user_id), "username": self.username, "role": self.role}


class IdentityError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user(row: dict | None) -> User | None:
    if row is None:
        return None
    return User(row["user_id"], row["username"], row["role"], row["disabled"], row["password_hash"],
                row["password_changed_at"])


# ------------------------------------------------------------------ accounts
def get_user(conn: psycopg.Connection, username: str) -> User | None:
    return _user(conn.execute("SELECT * FROM cgis_app.users WHERE username = %s",
                              (security.normalize_username(username),)).fetchone())


def get_user_by_id(conn: psycopg.Connection, user_id: uuid.UUID) -> User | None:
    return _user(conn.execute("SELECT * FROM cgis_app.users WHERE user_id = %s", (user_id,)).fetchone())


def list_users(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT user_id, username, role, disabled, created_at, password_changed_at FROM cgis_app.users "
        "ORDER BY username").fetchall()


def check_new_password(settings: Settings, username: str, password: str) -> None:
    problems = security.password_problems(password, username, settings.password_min_length)
    if problems:
        raise IdentityError("WEAK_PASSWORD", "password " + "; ".join(problems))


def create_user(conn: psycopg.Connection, settings: Settings, username: str, password: str, role: str) -> User:
    name = security.normalize_username(username)
    if not security.USERNAME_RE.match(name):
        raise IdentityError("INVALID_USERNAME", "username must be 2-63 characters: lowercase letters, digits, "
                                                "'.', '_' or '-', starting with a letter or digit")
    if role not in ROLES:
        raise IdentityError("INVALID_ROLE", f"role must be one of {', '.join(ROLES)}")
    check_new_password(settings, name, password)
    try:
        row = conn.execute(
            "INSERT INTO cgis_app.users (username, role, password_hash) VALUES (%s, %s, %s) RETURNING *",
            (name, role, security.hash_password(password))).fetchone()
    except psycopg.errors.UniqueViolation:
        raise IdentityError("USER_EXISTS", f"user {name!r} already exists") from None
    log.info("created user %s (role %s)", name, role)
    return _user(row)  # type: ignore[return-value]


def set_password(conn: psycopg.Connection, settings: Settings, user: User, password: str,
                 *, keep_session: uuid.UUID | None = None) -> None:
    check_new_password(settings, user.username, password)
    with conn.transaction():
        conn.execute("UPDATE cgis_app.users SET password_hash = %s, password_changed_at = now() WHERE user_id = %s",
                     (security.hash_password(password), user.user_id))
        revoke_user_sessions(conn, user.user_id, "password_changed", keep=keep_session)
    log.info("password changed for user %s; other sessions revoked", user.username)


def set_role(conn: psycopg.Connection, user: User, role: str) -> None:
    if role not in ROLES:
        raise IdentityError("INVALID_ROLE", f"role must be one of {', '.join(ROLES)}")
    with conn.transaction():
        conn.execute("UPDATE cgis_app.users SET role = %s WHERE user_id = %s", (role, user.user_id))
        revoke_user_sessions(conn, user.user_id, "role_changed")


def set_disabled(conn: psycopg.Connection, user: User, disabled: bool) -> None:
    with conn.transaction():
        conn.execute("UPDATE cgis_app.users SET disabled = %s WHERE user_id = %s", (disabled, user.user_id))
        if disabled:
            revoke_user_sessions(conn, user.user_id, "account_disabled")


def ensure_user(conn: psycopg.Connection, settings: Settings, username: str, password: str, role: str,
                *, reset_password: bool = False) -> str:
    """Bootstrap helper: create the account if absent; never silently change an existing one's role."""
    existing = get_user(conn, username)
    if existing is None:
        create_user(conn, settings, username, password, role)
        return "created"
    if existing.role != role:
        raise IdentityError("ROLE_MISMATCH", f"bootstrap user {existing.username!r} exists with role "
                                             f"{existing.role!r}, not {role!r}; change it explicitly")
    if reset_password and not security.verify_password(existing.password_hash, password):
        set_password(conn, settings, existing, password)
        return "password_reset"
    return "unchanged"


# ------------------------------------------------------------------ sessions and tokens
def create_session(conn: psycopg.Connection, settings: Settings, user: User, kind: str, *,
                   scope: str = "full", name: str | None = None, ttl_seconds: int | None = None) -> dict[str, Any]:
    token = security.new_token(kind)
    csrf = security.new_csrf_token() if kind == "browser" else None
    if kind == "pat":
        ttl = ttl_seconds or settings.token_default_ttl_days * 86400
        idle = None
    else:
        ttl = settings.session_ttl_seconds
        idle = settings.session_idle_seconds
    expires = _now() + timedelta(seconds=ttl)
    row = conn.execute(
        "INSERT INTO cgis_app.sessions (token_hash, user_id, kind, scope, name, csrf_token, expires_at, idle_seconds) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING session_id, expires_at, created_at",
        (security.token_digest(token), user.user_id, kind, scope, name, csrf, expires, idle)).fetchone()
    return {"token": token, "csrf_token": csrf, "session_id": row["session_id"], "expires_at": row["expires_at"],
            "created_at": row["created_at"]}


_RESOLVE_SQL = """
SELECT s.session_id, s.kind, s.scope, s.csrf_token, s.expires_at, s.idle_seconds, s.last_seen_at,
       s.revoked_at, s.revoked_reason, s.created_at,
       u.user_id, u.username, u.role, u.disabled, u.password_changed_at
  FROM cgis_app.sessions s JOIN cgis_app.users u USING (user_id)
 WHERE s.token_hash = %s
"""


def resolve_token(conn: psycopg.Connection, token: str, *, touch_after_seconds: int = 60) -> Principal:
    """Return the principal for a presented secret or raise IdentityError (expired/revoked/unknown)."""
    if not security.looks_like_token(token):
        raise IdentityError("INVALID_TOKEN", "the credential is not valid")
    row = conn.execute(_RESOLVE_SQL, (security.token_digest(token),)).fetchone()
    if row is None:
        raise IdentityError("INVALID_TOKEN", "the credential is not valid")
    now = _now()
    if row["revoked_at"] is not None:
        raise IdentityError("SESSION_REVOKED", "the session or token was revoked; sign in again")
    if row["disabled"]:
        raise IdentityError("ACCOUNT_DISABLED", "the account is disabled")
    if row["expires_at"] <= now:
        raise IdentityError("SESSION_EXPIRED", "the session or token has expired; sign in again")
    if row["idle_seconds"] is not None and row["last_seen_at"] + timedelta(seconds=row["idle_seconds"]) <= now:
        raise IdentityError("SESSION_EXPIRED", "the session expired after inactivity; sign in again")
    if row["kind"] == "pat" and row["scope"] == "admin" and row["role"] != "admin":
        raise IdentityError("INVALID_TOKEN", "the token's admin scope no longer matches the account role")
    if (now - row["last_seen_at"]).total_seconds() >= touch_after_seconds:
        conn.execute("UPDATE cgis_app.sessions SET last_seen_at = now() WHERE session_id = %s", (row["session_id"],))
    return Principal(user_id=row["user_id"], username=row["username"], account_role=row["role"],
                     session_id=row["session_id"], kind=row["kind"], scope=row["scope"],
                     expires_at=row["expires_at"], csrf_token=row["csrf_token"])


def revoke_session(conn: psycopg.Connection, session_id: uuid.UUID, reason: str) -> bool:
    cur = conn.execute("UPDATE cgis_app.sessions SET revoked_at = now(), revoked_reason = %s "
                       "WHERE session_id = %s AND revoked_at IS NULL", (reason, session_id))
    return cur.rowcount == 1


def revoke_user_sessions(conn: psycopg.Connection, user_id: uuid.UUID, reason: str,
                         keep: uuid.UUID | None = None) -> int:
    cur = conn.execute("UPDATE cgis_app.sessions SET revoked_at = now(), revoked_reason = %s "
                       "WHERE user_id = %s AND revoked_at IS NULL AND session_id IS DISTINCT FROM %s",
                       (reason, user_id, keep))
    return cur.rowcount


def list_tokens(conn: psycopg.Connection, user_id: uuid.UUID) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT session_id AS token_id, name, scope, created_at, last_seen_at, expires_at, revoked_at "
        "FROM cgis_app.sessions WHERE user_id = %s AND kind = 'pat' ORDER BY created_at DESC LIMIT 200",
        (user_id,)).fetchall()


def count_live_tokens(conn: psycopg.Connection, user_id: uuid.UUID) -> int:
    return conn.execute("SELECT count(*) AS n FROM cgis_app.sessions WHERE user_id = %s AND kind = 'pat' "
                        "AND revoked_at IS NULL AND expires_at > now()", (user_id,)).fetchone()["n"]


def revoke_token(conn: psycopg.Connection, user_id: uuid.UUID, token_id: uuid.UUID) -> bool:
    cur = conn.execute("UPDATE cgis_app.sessions SET revoked_at = now(), revoked_reason = 'revoked_by_owner' "
                       "WHERE session_id = %s AND user_id = %s AND kind = 'pat' AND revoked_at IS NULL",
                       (token_id, user_id))
    return cur.rowcount == 1


def purge_expired_sessions(conn: psycopg.Connection, older_than_days: int = 30) -> int:
    cur = conn.execute("DELETE FROM cgis_app.sessions WHERE expires_at < now() - make_interval(days => %s) "
                       "OR revoked_at < now() - make_interval(days => %s)", (older_than_days, older_than_days))
    return cur.rowcount


# ------------------------------------------------------------------ login throttling
def throttle_status(conn: psycopg.Connection, keys: list[str]) -> int:
    """Seconds until login is allowed again for any of ``keys`` (0 = allowed)."""
    row = conn.execute("SELECT max(extract(epoch FROM locked_until - now())) AS wait FROM cgis_app.login_attempts "
                       "WHERE key = ANY(%s) AND locked_until > now()", (keys,)).fetchone()
    wait = row["wait"] if row else None
    return int(wait) + 1 if wait and wait > 0 else 0


def record_failure(conn: psycopg.Connection, settings: Settings, key: str, limit: int) -> None:
    conn.execute(
        """
        INSERT INTO cgis_app.login_attempts AS a (key, failures, window_started_at)
        VALUES (%(key)s, 1, now())
        ON CONFLICT (key) DO UPDATE SET
            failures = CASE WHEN a.window_started_at < now() - make_interval(secs => %(window)s)
                            THEN 1 ELSE a.failures + 1 END,
            window_started_at = CASE WHEN a.window_started_at < now() - make_interval(secs => %(window)s)
                                     THEN now() ELSE a.window_started_at END
        """, {"key": key[:200], "window": settings.login_window_seconds})
    conn.execute("UPDATE cgis_app.login_attempts SET locked_until = now() + make_interval(secs => %s), "
                 "failures = 0, window_started_at = now() WHERE key = %s AND failures >= %s",
                 (settings.login_lockout_seconds, key[:200], limit))


def clear_failures(conn: psycopg.Connection, key: str) -> None:
    conn.execute("DELETE FROM cgis_app.login_attempts WHERE key = %s", (key[:200],))
