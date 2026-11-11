# /auth routes: login, logout, /me, gateway auth_request check (/verify), personal access tokens, password change.

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from . import auth, errors, identity, security
from .auth import SessionPrincipal
from .errors import ApiError
from .identity import IdentityError, Principal
from .models import ErrorResponse, LoginRequest, PasswordChange, Session, TokenCreate, TokenCreated, TokenInfo

log = logging.getLogger("cgis_app.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

_E = {code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 422, 429)}


# Session response body (anonymous shape when principal is None). Used by login and me.
def _session_body(principal: Principal | None, settings, *, csrf: str | None = None,
                  access_token: str | None = None, reason: str | None = None) -> dict[str, Any]:
    if principal is None:
        body: dict[str, Any] = {"authenticated": False, "user": None, "expires_at": None, "csrf_token": None}
        if reason:
            body["reason"] = reason
        return body
    method = {"browser": "cookie", "bearer": "bearer", "pat": "token"}[principal.kind]
    body = {
        "authenticated": True,
        "user": principal.public(),
        "expires_at": principal.expires_at.isoformat(),
        "idle_timeout_seconds": None if principal.kind == "pat" else settings.session_idle_seconds,
        "auth_method": method,
        "csrf_token": csrf if csrf is not None else principal.csrf_token,
    }
    if access_token:
        body["access_token"] = access_token
        body["token_type"] = "Bearer"
    return body


# Set the HttpOnly SameSite=Strict session cookie. Used by login.
def _set_cookie(response: Response, settings, token: str) -> None:
    response.set_cookie(settings.session_cookie, token, max_age=settings.session_ttl_seconds, path="/",
                        secure=settings.cookie_secure, httponly=True, samesite="strict")


# Delete the session cookie. Used by logout and me.
def _clear_cookie(response: Response, settings) -> None:
    response.delete_cookie(settings.session_cookie, path="/", secure=settings.cookie_secure, httponly=True,
                           samesite="strict")


# POST /auth/login: throttled password check, then a cookie session (+CSRF) or a bearer token.
@router.post("/login", summary="Sign in (browser cookie session, or bearer token with token=true)",
             response_model=Session, responses={**_E, 200: {"description": "Signed in. Browser mode sets the "
                                                                          "HttpOnly cgis_session cookie."}})
def login(req: LoginRequest, request: Request, response: Response):
    settings = request.app.state.settings
    db = request.app.state.db
    auth._check_same_origin(request)  # login CSRF: a foreign page must not sign the browser in
    username = security.normalize_username(req.username)
    ip = auth.client_ip(request)
    keys = [f"user:{username}", f"ip:{ip}"]
    with db.connection() as conn:
        wait = identity.throttle_status(conn, keys)
        if wait:
            log.warning("login throttled for user=%s ip=%s", username, ip)
            raise ApiError(429, errors.RATE_LIMITED, "too many failed sign-in attempts; try again later",
                           {"retry_after_seconds": wait}, headers={"Retry-After": str(wait)})
        user = identity.get_user(conn, username) if security.USERNAME_RE.match(username) else None
        ok = security.verify_password(user.password_hash if user else None, req.password)
        if not ok or user is None:
            identity.record_failure(conn, settings, keys[0], settings.login_max_failures)
            identity.record_failure(conn, settings, keys[1], settings.ip_max_failures)
            log.info("login failed for user=%s ip=%s", username, ip)
            raise ApiError(401, errors.INVALID_CREDENTIALS, "unknown username or wrong password")
        if user.disabled:
            log.info("login refused for disabled user=%s", username)
            raise ApiError(403, errors.ACCOUNT_DISABLED, "the account is disabled")
        identity.clear_failures(conn, keys[0])
        if security.needs_rehash(user.password_hash):
            conn.execute("UPDATE cgis_app.users SET password_hash = %s WHERE user_id = %s",
                         (security.hash_password(req.password), user.user_id))
        kind = "bearer" if req.token else "browser"
        created = identity.create_session(conn, settings, user, kind)
    principal = Principal(user_id=user.user_id, username=user.username, account_role=user.role,
                          session_id=created["session_id"], kind=kind, scope="full",
                          expires_at=created["expires_at"], csrf_token=created["csrf_token"])
    log.info("login ok user=%s role=%s method=%s", user.username, user.role, kind)
    if kind == "browser":
        _set_cookie(response, settings, created["token"])
        return _session_body(principal, settings)
    return _session_body(principal, settings, access_token=created["token"])


# POST /auth/logout: revoke the current session and clear the cookie (idempotent).
@router.post("/logout", status_code=204, summary="Sign out: revoke the current session (idempotent)",
             responses={**_E, 204: {"description": "Signed out; the cookie is cleared."}})
def logout(request: Request):
    settings = request.app.state.settings
    response = Response(status_code=204)
    try:
        principal = auth.resolve(request)
    except ApiError as exc:
        if exc.status == 401:  # already expired/revoked: still clear the cookie
            _clear_cookie(response, settings)
            return response
        raise
    if principal is not None and principal.kind != "pat":
        with request.app.state.db.connection() as conn:
            identity.revoke_session(conn, principal.session_id, "logout")
        log.info("logout user=%s", principal.username)
    _clear_cookie(response, settings)
    return response


# GET /auth/me: current identity; 200 anonymous (and a cleared cookie) for a stale cookie.
@router.get("/me", response_model=Session, summary="Current identity and role (200 with authenticated=false "
                                                    "for anonymous callers)")
def me(request: Request):
    settings = request.app.state.settings
    try:
        principal = auth.resolve(request)
    except ApiError as exc:
        # An expired/revoked browser cookie simply means "signed out": answer 200 so a page load logs no
        # error, and clear the stale cookie. Bad bearer/Basic credentials stay a 401.
        if exc.status != 401 or getattr(request.state, "auth_channel", None) != "cookie":
            raise
        response = JSONResponse(_session_body(None, settings, reason=exc.code))
        _clear_cookie(response, settings)
        return response
    return _session_body(principal, settings)


# GET /auth/verify: nginx auth_request target for protected read routes (infra/gateway).
@router.get("/verify", status_code=204, summary="Gateway auth_request check (204 allowed, 401/403 refused)",
            responses={**_E, 204: {"description": "Allowed; X-Cgis-User / X-Cgis-Role headers describe the caller."}})
def verify(request: Request, require: str = Query("user", pattern="^(user|admin)$")):
    """For nginx ``auth_request``: the gateway forwards the original request's credentials and sets
    ``X-Original-Method``, so CSRF is enforced for cookie sessions on unsafe methods there too."""
    method = request.headers.get("x-original-method", "GET").upper()
    principal = auth.resolve(request, method=method)
    if principal is None:
        raise auth._unauthenticated()
    if require == "admin" and not principal.is_admin:
        raise ApiError(403, errors.FORBIDDEN, "this route requires the admin role")
    return Response(status_code=204, headers={"X-Cgis-User": principal.username, "X-Cgis-Role": principal.role})


# ------------------------------------------------------------------ personal access tokens
# TokenInfo body for a token row. Used by list_tokens.
def _token_info(row: dict) -> dict[str, Any]:
    return {"token_id": str(row["token_id"]), "name": row["name"], "scope": row["scope"],
            "created_at": row["created_at"], "last_seen_at": row.get("last_seen_at"),
            "expires_at": row["expires_at"], "revoked_at": row.get("revoked_at")}


# POST /auth/tokens: create a personal access token (admin scope for admins only), shown once.
@router.post("/tokens", status_code=201, response_model=TokenCreated, responses=_E,
             summary="Create a personal access token for QGIS/GDAL/scripts (shown once)")
def create_token(req: TokenCreate, request: Request, principal: SessionPrincipal):
    settings = request.app.state.settings
    if req.scope == "admin" and principal.account_role != "admin":
        raise ApiError(403, errors.FORBIDDEN, "only administrators can create admin-scoped tokens")
    days = req.expires_in_days or settings.token_default_ttl_days
    if days > settings.token_max_ttl_days:
        raise ApiError(422, errors.INVALID_REQUEST, f"expires_in_days must be at most {settings.token_max_ttl_days}")
    with request.app.state.db.connection() as conn:
        if identity.count_live_tokens(conn, principal.user_id) >= settings.max_tokens_per_user:
            raise ApiError(409, errors.TOO_MANY_TOKENS, "too many active tokens; revoke one first",
                           {"limit": settings.max_tokens_per_user})
        user = identity.get_user_by_id(conn, principal.user_id)
        created = identity.create_session(conn, settings, user, "pat", scope=req.scope, name=req.name,
                                          ttl_seconds=days * 86400)
    log.info("token created user=%s scope=%s days=%s", principal.username, req.scope, days)
    return {"token_id": str(created["session_id"]), "name": req.name, "scope": req.scope,
            "created_at": created["created_at"], "expires_at": created["expires_at"], "token": created["token"]}


# GET /auth/tokens: my tokens.
@router.get("/tokens", response_model=list[TokenInfo], responses=_E, summary="List my personal access tokens")
def list_tokens(request: Request, principal: SessionPrincipal):
    with request.app.state.db.connection() as conn:
        return [_token_info(r) for r in identity.list_tokens(conn, principal.user_id)]


# DELETE /auth/tokens/{id}: revoke one of my tokens.
@router.delete("/tokens/{token_id}", status_code=204, responses=_E, summary="Revoke one of my tokens")
def revoke_token(token_id: uuid.UUID, request: Request, principal: SessionPrincipal):
    with request.app.state.db.connection() as conn:
        if not identity.revoke_token(conn, principal.user_id, token_id):
            raise ApiError(404, errors.TOKEN_NOT_FOUND, "no active token with that id")
    return Response(status_code=204)


# POST /auth/password: verify the current password, set a new one, revoke other sessions.
@router.post("/password", status_code=204, responses=_E,
             summary="Change my password (revokes every other session and token)")
def change_password(req: PasswordChange, request: Request, principal: SessionPrincipal):
    settings = request.app.state.settings
    with request.app.state.db.connection() as conn:
        user = identity.get_user_by_id(conn, principal.user_id)
        if user is None or not security.verify_password(user.password_hash, req.current_password):
            raise ApiError(401, errors.INVALID_CREDENTIALS, "the current password is wrong")
        try:
            identity.set_password(conn, settings, user, req.new_password, keep_session=principal.session_id)
        except IdentityError as exc:
            raise ApiError(422, errors.WEAK_PASSWORD, exc.message) from None
    return Response(status_code=204)
