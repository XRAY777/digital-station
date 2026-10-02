from __future__ import annotations

import hmac
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from backend.app.auth.dependencies import (
    get_current_session,
    get_current_user,
    get_db_session,
    require_permission,
)
from backend.app.auth.schemas import (
    AuthResponse,
    Credentials,
    CsrfResponse,
    UserCreate,
    UserList,
    UserView,
)
from backend.app.auth.service import (
    ACCESS_TTL,
    as_utc,
    create_session,
    get_preauth_context,
    hash_token,
    make_user_view,
    password_hash,
    rotate_refresh,
    utc_now,
    verify_password,
)
from backend.app.models import (
    CsrfContext,
    LoginAttempt,
    Role,
    Session,
    SessionRefreshToken,
    User,
    UserRole,
)
from backend.app.services.audit import record_audit, station_id_for_audit

router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])
ACCESS_COOKIE = "station_access"
REFRESH_COOKIE = "station_refresh"
PREAUTH_COOKIE = "station_pre_auth"
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_LOCK = timedelta(minutes=15)
LOGIN_FAILURE_LIMIT = 5


def _allowed_origins(request: Request) -> set[str]:
    configured = os.getenv("STATION_ALLOWED_ORIGINS")
    origins = {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    }
    origins.add(str(request.base_url).rstrip("/"))
    if configured:
        origins.update(
            origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()
        )
    return origins


def _require_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None or origin.rstrip("/") not in _allowed_origins(request):
        raise HTTPException(status_code=403, detail="origin_denied")


def _require_csrf(request: Request, expected: str) -> None:
    _require_origin(request)
    supplied = request.headers.get("x-csrf-token", "")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="csrf_invalid")


def _set_session_cookies(
    response: Response,
    request: Request,
    access_token: str,
    refresh_token: str,
    auth_session: Session,
) -> None:
    secure = request.url.scheme == "https"
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=max(
            1, int(min(ACCESS_TTL, as_utc(auth_session.expires_at) - utc_now()).total_seconds())
        ),
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=max(1, int((as_utc(auth_session.expires_at) - utc_now()).total_seconds())),
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/api/auth",
    )


def _clear_auth_cookies(response: Response, request: Request) -> None:
    secure = request.url.scheme == "https"
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=secure, httponly=True, samesite="strict")
    response.delete_cookie(
        REFRESH_COOKIE,
        path="/api/auth",
        secure=secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        PREAUTH_COOKIE,
        path="/api/auth",
        secure=secure,
        httponly=True,
        samesite="strict",
    )


def _preauth_csrf(request: Request, db: DbSession) -> str:
    context_token = request.cookies.get(PREAUTH_COOKIE)
    csrf_header = request.headers.get("x-csrf-token", "")
    if not context_token or not csrf_header:
        raise HTTPException(status_code=403, detail="csrf_invalid")
    row = db.scalar(
        select(CsrfContext).where(
            CsrfContext.context_token_hash == hash_token(context_token),
            CsrfContext.expires_at > utc_now(),
        )
    )
    if row is None or not hmac.compare_digest(row.csrf_token_hash, hash_token(csrf_header)):
        raise HTTPException(status_code=403, detail="csrf_invalid")
    return context_token


def require_authenticated_csrf(
    request: Request,
    auth_session: Annotated[Session, Depends(get_current_session)],
) -> Session:
    _require_csrf(request, auth_session.csrf_token)
    return auth_session


@router.get("/csrf", response_model=CsrfResponse)
def csrf_token(
    request: Request,
    response: Response,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> CsrfResponse:
    access_token = request.cookies.get(ACCESS_COOKIE)
    if access_token:
        auth_session = db.scalar(
            select(Session).where(Session.access_token_hash == hash_token(access_token))
        )
        if (
            auth_session is not None
            and auth_session.revoked_at is None
            and as_utc(auth_session.access_expires_at) > utc_now()
            and as_utc(auth_session.expires_at) > utc_now()
        ):
            return CsrfResponse(csrfToken=auth_session.csrf_token)
    _, raw_context, csrf = get_preauth_context(db, request.cookies.get(PREAUTH_COOKIE))
    db.commit()
    response.set_cookie(
        PREAUTH_COOKIE,
        raw_context,
        max_age=600,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/api/auth",
    )
    return CsrfResponse(csrfToken=csrf)


@router.post("/login", response_model=AuthResponse)
def login(
    request: Request,
    response: Response,
    credentials: Credentials,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> AuthResponse:
    _require_origin(request)
    context_token = _preauth_csrf(request, db)
    attempt_keys = _login_attempt_keys(request, credentials.username)
    _check_login_limits(db, attempt_keys)
    user = db.scalar(select(User).where(User.username == credentials.username))
    if (
        user is None
        or not user.is_active
        or not verify_password(credentials.password, user.password_hash)
    ):
        _record_login_failure(db, attempt_keys)
        station_id = station_id_for_audit(db)
        if station_id is not None:
            record_audit(
                db,
                station_id,
                request,
                "LOGIN",
                new_value={"usernameAttempted": credentials.username},
                result="DENIED",
            )
            db.commit()
        raise HTTPException(status_code=401, detail="invalid_credentials")
    _clear_account_failures(db, attempt_keys[0])
    auth_session, access_token, refresh_token, csrf = create_session(db, user)
    station_id = station_id_for_audit(db)
    if station_id is not None:
        record_audit(
            db,
            station_id,
            request,
            "LOGIN",
            user=user,
            entity_type="session",
            entity_id=auth_session.id,
            session_id=auth_session.id,
            new_value={"username": user.username},
        )
    context_hash = hash_token(context_token)
    db.execute(delete(CsrfContext).where(CsrfContext.context_token_hash == context_hash))
    db.commit()
    _set_session_cookies(response, request, access_token, refresh_token, auth_session)
    response.delete_cookie(PREAUTH_COOKIE, path="/api/auth", httponly=True, samesite="strict")
    return AuthResponse(user=make_user_view(db, user), csrfToken=csrf)


@router.post("/refresh", response_model=AuthResponse)
def refresh(
    request: Request,
    response: Response,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> AuthResponse:
    _require_origin(request)
    raw_refresh = request.cookies.get(REFRESH_COOKIE)
    if raw_refresh is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    token_digest = hash_token(raw_refresh)
    auth_session = db.scalar(select(Session).where(Session.refresh_token_hash == token_digest))
    if auth_session is None:
        _revoke_reused_refresh(db, token_digest, request)
        _clear_auth_cookies(response, request)
        raise HTTPException(status_code=401, detail="authentication_required")
    _require_csrf(request, auth_session.csrf_token)
    rotated = rotate_refresh(db, raw_refresh)
    if rotated is None:
        db.rollback()
        _clear_auth_cookies(response, request)
        raise HTTPException(status_code=401, detail="authentication_required")
    auth_session, access_token, refresh_token, csrf = rotated
    user = db.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        db.rollback()
        _clear_auth_cookies(response, request)
        raise HTTPException(status_code=401, detail="authentication_required")
    db.commit()
    _set_session_cookies(response, request, access_token, refresh_token, auth_session)
    return AuthResponse(user=make_user_view(db, user), csrfToken=csrf)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> Response:
    _require_origin(request)
    raw_refresh = request.cookies.get(REFRESH_COOKIE)
    raw_access = request.cookies.get(ACCESS_COOKIE)
    auth_session = None
    if raw_refresh:
        auth_session = db.scalar(
            select(Session).where(Session.refresh_token_hash == hash_token(raw_refresh))
        )
    if auth_session is None and raw_access:
        auth_session = db.scalar(
            select(Session).where(Session.access_token_hash == hash_token(raw_access))
        )
    if auth_session is not None and auth_session.revoked_at is None:
        _require_csrf(request, auth_session.csrf_token)
        user = db.get(User, auth_session.user_id)
        station_id = station_id_for_audit(db)
        if user is not None and station_id is not None:
            record_audit(
                db,
                station_id,
                request,
                "LOGOUT",
                user=user,
                entity_type="session",
                entity_id=auth_session.id,
                session_id=auth_session.id,
                old_value={"active": True},
                new_value={"active": False},
            )
        auth_session.revoked_at = utc_now()
        db.commit()
    else:
        _clear_auth_cookies(response, request)
        return response
    _clear_auth_cookies(response, request)
    response.status_code = 204
    return response


@router.get("/me", response_model=UserView)
def me(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[DbSession, Depends(get_db_session)],
) -> UserView:
    return make_user_view(db, user)


@users_router.get("", response_model=UserList)
def list_users(
    _: Annotated[User, Depends(require_permission("users.manage"))],
    db: Annotated[DbSession, Depends(get_db_session)],
) -> UserList:
    users = db.scalars(select(User).order_by(User.username)).all()
    return UserList(items=[make_user_view(db, user) for user in users])


@users_router.post("", response_model=UserView, status_code=201)
def create_user(
    request: Request,
    body: UserCreate,
    _: Annotated[Session, Depends(require_authenticated_csrf)],
    actor: Annotated[User, Depends(require_permission("users.manage"))],
    db: Annotated[DbSession, Depends(get_db_session)],
) -> UserView:
    _require_origin(request)
    if db.scalar(select(User.id).where(User.username == body.username)) is not None:
        raise HTTPException(status_code=409, detail="username_unavailable")
    role = db.scalar(select(Role).where(Role.code == body.role))
    if role is None:
        raise HTTPException(status_code=422, detail="role_unavailable")
    user = User(username=body.username, password_hash=password_hash.hash(body.password))
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=role.id))
    station_id = station_id_for_audit(db)
    if station_id is not None:
        record_audit(
            db,
            station_id,
            request,
            "USER_CREATED",
            user=actor,
            entity_type="user",
            entity_id=user.id,
            new_value={"username": user.username, "role": role.code},
        )
    db.commit()
    return make_user_view(db, user)


def _login_attempt_keys(request: Request, username: str) -> list[tuple[str, str]]:
    remote_ip = request.client.host if request.client is not None else "unknown"
    return [
        ("account", hash_token(f"account:{username}")),
        ("ip", hash_token(f"ip:{remote_ip}")),
    ]


def _check_login_limits(db: DbSession, keys: list[tuple[str, str]]) -> None:
    now = utc_now()
    for dimension, key_hash in keys:
        attempt = db.get(LoginAttempt, (dimension, key_hash))
        if attempt is not None and as_utc(attempt.window_started_at) + LOGIN_WINDOW <= now:
            attempt.failed_count = 0
            attempt.window_started_at = now
            attempt.locked_until = None
        if attempt is not None and attempt.locked_until is not None:
            retry_at = as_utc(attempt.locked_until)
            if retry_at > now:
                raise HTTPException(
                    status_code=429,
                    detail="login_rate_limited",
                    headers={"Retry-After": str(max(1, int((retry_at - now).total_seconds())))},
                )


def _record_login_failure(db: DbSession, keys: list[tuple[str, str]]) -> None:
    now = utc_now()
    for dimension, key_hash in keys:
        attempt = db.get(LoginAttempt, (dimension, key_hash))
        if attempt is None:
            attempt = LoginAttempt(
                dimension=dimension,
                key_hash=key_hash,
                failed_count=0,
                window_started_at=now,
            )
            db.add(attempt)
        elif as_utc(attempt.window_started_at) + LOGIN_WINDOW <= now:
            attempt.failed_count = 0
            attempt.window_started_at = now
        attempt.failed_count += 1
        if attempt.failed_count >= LOGIN_FAILURE_LIMIT:
            attempt.locked_until = now + LOGIN_LOCK
    db.commit()


def _clear_account_failures(db: DbSession, key: tuple[str, str]) -> None:
    attempt = db.get(LoginAttempt, key)
    if attempt is not None:
        attempt.failed_count = 0
        attempt.locked_until = None


def _revoke_reused_refresh(db: DbSession, token_digest: str, request: Request) -> None:
    used = db.scalar(
        select(SessionRefreshToken).where(SessionRefreshToken.token_hash == token_digest)
    )
    if used is None:
        return
    auth_session = db.get(Session, used.session_id)
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.now(UTC)
        user = db.get(User, auth_session.user_id)
        station_id = station_id_for_audit(db)
        if station_id is not None:
            record_audit(
                db,
                station_id,
                request,
                "SESSION_REUSE_DETECTED",
                user=user,
                entity_type="session",
                entity_id=auth_session.id,
                session_id=auth_session.id,
                old_value={"revoked": False},
                new_value={"revoked": True},
                result="DENIED",
            )
        db.commit()
