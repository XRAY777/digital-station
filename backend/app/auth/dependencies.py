from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import UTC, datetime
from typing import Annotated

from database.connection import make_session_factory
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from backend.app.models import Permission, Role, RolePermission, Session, User, UserRole
from backend.app.services.audit import record_audit, station_id_for_audit


def get_db_session(request: Request) -> Generator[DbSession, None, None]:
    engine = request.app.state.db_engine
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_current_session(
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> Session:
    raw_token = request.cookies.get("station_access")
    if not raw_token:
        raise HTTPException(status_code=401, detail="authentication_required")
    from backend.app.auth.service import hash_token, utc_now

    auth_session = db.scalar(
        select(Session).where(Session.access_token_hash == hash_token(raw_token))
    )
    now = utc_now()
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or _as_utc(auth_session.access_expires_at) <= now
        or _as_utc(auth_session.expires_at) <= now
    ):
        raise HTTPException(status_code=401, detail="authentication_required")
    return auth_session


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def get_current_user(
    auth_session: Annotated[Session, Depends(get_current_session)],
    db: Annotated[DbSession, Depends(get_db_session)],
) -> User:
    user = db.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="authentication_required")
    return user


def require_permission(code: str) -> Callable[..., User]:
    def check_permission(
        request: Request,
        user: Annotated[User, Depends(get_current_user)],
        db: Annotated[DbSession, Depends(get_db_session)],
    ) -> User:
        allowed = db.scalar(
            select(Permission.id)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id, Permission.code == code)
            .limit(1)
        )
        if allowed is None:
            station_id = station_id_for_audit(db)
            if station_id is not None:
                record_audit(
                    db,
                    station_id,
                    request,
                    "ACCESS_DENIED",
                    user=user,
                    entity_type="endpoint",
                    entity_id=request.url.path,
                    result="DENIED",
                    commit=True,
                )
            raise HTTPException(status_code=403, detail="permission_denied")
        return user

    return check_permission
