# Request authentication/authorization as FastAPI dependencies. Credentials: Bearer (session or personal
# access token), HTTP Basic with a token as password (never an account password), or the cgis_session cookie
# (+X-CSRF-Token and same-origin on unsafe methods). Each secret is valid only in its own channel.

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


# 401 ApiError with WWW-Authenticate. Used by resolve, require_user and routes_auth.verify.
def _unauthenticated(code: str = errors.UNAUTHENTICATED, message: str = "sign in to use this endpoint") -> ApiError:
    return ApiError(401, code, message, headers={"WWW-Authenticate": 'Bearer realm="cgis"'})


# Caller address (X-Real-IP only when APP_TRUST_PROXY_HEADERS). Used by routes_auth.login throttling.
def client_ip(request: Request) -> str:
    settings = request.app.state.settings
    if settings.trust_proxy_headers:
        raw = request.headers.get("x-real-ip", "").strip()
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


# (channel, secret) from Authorization or the cookie, or None; 401 for malformed headers. Used by resolve.
def _credential(request: Request) -> tuple[str, str] | None:
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


# 403 ORIGIN_FORBIDDEN for foreign Origin/Sec-Fetch-Site. Used by check_csrf and login.
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


# Enforce same-origin and X-CSRF-Token for unsafe methods of a cookie session. Used by resolve.
def check_csrf(request: Request, principal: Principal, method: str | None = None) -> None:
    if (method or request.method).upper() in SAFE_METHODS:
        return
    _check_same_origin(request)
    supplied = request.headers.get(CSRF_HEADER)
    if not security.constant_time_equals(supplied, principal.csrf_token):
        raise ApiError(403, errors.CSRF_FAILED, "missing or invalid X-CSRF-Token header for a cookie session; "
                                                "read csrf_token from POST /auth/login or GET /auth/me")


# Authenticate the request once (cached in request.state); raises 401/403. Used by the dependencies
# below and by /auth/logout, /auth/me, /auth/verify (with the original method).
def resolve(request: Request, *, method: str | None = None) -> Principal | None:
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


# Dependency: principal or None. Used by the catalog routes (OptionalPrincipal).
def optional_principal(request: Request) -> Principal | None:
    return resolve(request)


# Dependency: any signed-in principal, else 401. Used by export routes (UserPrincipal).
def require_user(request: Request) -> Principal:
    principal = resolve(request)
    if principal is None:
        raise _unauthenticated()
    return principal


# Dependency: admin role, else 403 FORBIDDEN. Used by every /admin route (AdminPrincipal).
def require_admin(request: Request) -> Principal:
    principal = require_user(request)
    if not principal.is_admin:
        raise ApiError(403, errors.FORBIDDEN, "this action requires the admin role",
                       {"role": principal.role, "required_role": "admin"})
    return principal


# Dependency: signed in with a session, not a personal access token. Used by /auth/tokens and
# /auth/password (SessionPrincipal).
def require_full_session(request: Request) -> Principal:
    principal = require_user(request)
    if principal.kind == "pat":
        raise ApiError(403, errors.FORBIDDEN, "personal access tokens cannot manage tokens or passwords; "
                                              "sign in with a password session")
    return principal


OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]
UserPrincipal = Annotated[Principal, Depends(require_user)]
AdminPrincipal = Annotated[Principal, Depends(require_admin)]
SessionPrincipal = Annotated[Principal, Depends(require_full_session)]
