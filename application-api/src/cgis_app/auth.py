"""Request authentication and authorization (FastAPI dependencies).

Credential sources, in order:

1. ``Authorization: Bearer <token>`` — a bearer session (``POST /auth/login`` with ``token: true``) or a
   personal access token (``POST /auth/tokens``). Not ambient, so no CSRF check.
2. ``Authorization: Basic base64(<any user>:<personal access token>)`` — for clients that only support
   HTTP Basic (QGIS, GDAL ``GDAL_HTTP_USERPWD``, curl ``-u``). Account passwords are NEVER accepted here.
3. The ``cgis_session`` cookie (HttpOnly, SameSite=Strict) for browsers. Every unsafe method
   (POST/PUT/PATCH/DELETE) additionally needs ``X-CSRF-Token`` equal to the session's token and, when the
   browser sends ``Origin``/``Sec-Fetch-Site``, a same-origin request.

A token type is only valid in its own channel (a browser session secret is refused in an Authorization
header, a bearer/PAT secret is refused as a cookie). Authorization is enforced per route in the
backend: hiding a button in the UI is never the control.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import logging
from typing import Annotated

from fastapi import Depends, Request

from . import errors, identity, security
from .errors import ApiError
from .identity import IdentityError, Principal

log = logging.getLogger(__name__)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-csrf-token"


def _unauthenticated(code: str = errors.UNAUTHENTICATED, message: str = "sign in to use this endpoint") -> ApiError:
    return ApiError(401, code, message, headers={"WWW-Authenticate": 'Bearer realm="cgis"'})


def client_ip(request: Request) -> str:
    settings = request.app.state.settings
    if settings.trust_proxy_headers:
        raw = request.headers.get("x-real-ip", "").strip()
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def _credential(request: Request) -> tuple[str, str] | None:
    """Return (channel, secret) or None; raises 401 for malformed Authorization headers."""
    header = request.headers.get("authorization")
    if header:
        scheme, _, value = header.partition(" ")
        scheme = scheme.lower()
        value = value.strip()
        if scheme == "bearer" and value:
            return "bearer", value
        if scheme == "basic" and value:
            try:
                decoded = base64.b64decode(value, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                raise _unauthenticated(errors.INVALID_TOKEN, "malformed HTTP Basic credentials") from None
            _, sep, secret = decoded.partition(":")
            if not sep or not secret.startswith(security.TOKEN_PREFIXES["pat"]):
                raise _unauthenticated(errors.INVALID_TOKEN,
                                       "HTTP Basic accepts a personal access token as the password, never an "
                                       "account password; create one with POST /auth/tokens")
            return "basic", secret
        raise _unauthenticated(errors.INVALID_TOKEN, "unsupported Authorization scheme; use Bearer")
    cookie = request.cookies.get(request.app.state.settings.session_cookie)
    if cookie:
        return "cookie", cookie
    return None


def _check_same_origin(request: Request) -> None:
    settings = request.app.state.settings
    origin = request.headers.get("origin")
    if origin is not None:
        if origin.lower().rstrip("/") not in settings.allowed_origins:
            raise ApiError(403, errors.ORIGIN_FORBIDDEN, "cross-origin requests are not allowed")
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.lower() not in ("same-origin", "none"):
        raise ApiError(403, errors.ORIGIN_FORBIDDEN, "cross-site requests are not allowed")


def check_csrf(request: Request, principal: Principal, method: str | None = None) -> None:
    if (method or request.method).upper() in SAFE_METHODS:
        return
    _check_same_origin(request)
    supplied = request.headers.get(CSRF_HEADER)
    if not security.constant_time_equals(supplied, principal.csrf_token):
        raise ApiError(403, errors.CSRF_FAILED, "missing or invalid X-CSRF-Token header for a cookie session; "
                                                "read csrf_token from POST /auth/login or GET /auth/me")


def resolve(request: Request, *, method: str | None = None) -> Principal | None:
    """Authenticate the request (cached per request). Raises 401/403 for bad credentials or CSRF."""
    if hasattr(request.state, "principal"):
        return request.state.principal
    found = _credential(request)
    request.state.auth_channel = found[0] if found else None
    principal = None
    if found is not None:
        channel, secret = found
        db = request.app.state.db
        try:
            with db.connection() as conn:
                principal = identity.resolve_token(conn, secret)
        except IdentityError as exc:
            request.state.auth_error = exc.code
            raise _unauthenticated(exc.code, exc.message) from None
        if channel == "cookie" and principal.kind != "browser":
            raise _unauthenticated(errors.INVALID_TOKEN, "this credential cannot be used as a session cookie")
        if channel in ("bearer", "basic") and principal.kind == "browser":
            raise _unauthenticated(errors.INVALID_TOKEN, "browser session secrets are only valid as a cookie")
        if channel == "basic" and principal.kind != "pat":
            raise _unauthenticated(errors.INVALID_TOKEN, "HTTP Basic accepts personal access tokens only")
        if principal.kind == "browser":
            check_csrf(request, principal, method)
    request.state.principal = principal
    return principal


def optional_principal(request: Request) -> Principal | None:
    return resolve(request)


def require_user(request: Request) -> Principal:
    principal = resolve(request)
    if principal is None:
        raise _unauthenticated()
    return principal


def require_admin(request: Request) -> Principal:
    principal = require_user(request)
    if not principal.is_admin:
        raise ApiError(403, errors.FORBIDDEN, "this action requires the admin role",
                       {"role": principal.role, "required_role": "admin"})
    return principal


def require_full_session(request: Request) -> Principal:
    """Account management (tokens, password) needs a sign-in session, not a personal access token."""
    principal = require_user(request)
    if principal.kind == "pat":
        raise ApiError(403, errors.FORBIDDEN, "personal access tokens cannot manage tokens or passwords; "
                                              "sign in with a password session")
    return principal


OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]
UserPrincipal = Annotated[Principal, Depends(require_user)]
AdminPrincipal = Annotated[Principal, Depends(require_admin)]
SessionPrincipal = Annotated[Principal, Depends(require_full_session)]
