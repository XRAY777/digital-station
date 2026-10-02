from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from backend.app.auth.schemas import UserView
from backend.app.models import CsrfContext, Role, Session, SessionRefreshToken, User, UserRole

password_hash = PasswordHash.recommended()
dummy_password_hash = password_hash.hash("invalid-user-password-sentinel")
ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(hours=8)
PREAUTH_TTL = timedelta(minutes=10)


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    candidate = hashed_password or dummy_password_hash
    valid, _ = password_hash.verify_and_update(plain_password, candidate)
    return bool(hashed_password) and valid


def user_roles(db: DbSession, user_id: str) -> list[str]:
    return list(
        db.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.code)
        ).all()
    )


def make_user_view(db: DbSession, user: User) -> UserView:
    return UserView(id=user.id, username=user.username, roles=user_roles(db, user.id))


def create_session(db: DbSession, user: User) -> tuple[Session, str, str, str]:
    now = utc_now()
    access_token, refresh_token, csrf_token = new_token(), new_token(), new_token()
    auth_session = Session(
        user_id=user.id,
        access_token_hash=hash_token(access_token),
        refresh_token_hash=hash_token(refresh_token),
        family_id=new_token(),
        access_expires_at=now + ACCESS_TTL,
        expires_at=now + REFRESH_TTL,
        csrf_token=csrf_token,
    )
    db.add(auth_session)
    db.flush()
    return auth_session, access_token, refresh_token, csrf_token


def get_preauth_context(db: DbSession, context_token: str | None) -> tuple[CsrfContext, str, str]:
    now = utc_now()
    row = None
    if context_token:
        row = db.scalar(
            select(CsrfContext).where(
                CsrfContext.context_token_hash == hash_token(context_token),
                CsrfContext.expires_at > now,
            )
        )
    if row is not None and context_token is not None:
        csrf_token = new_token()
        row.csrf_token_hash = hash_token(csrf_token)
        row.expires_at = now + PREAUTH_TTL
        return row, context_token, csrf_token
    context_token, csrf_token = new_token(), new_token()
    row = CsrfContext(
        context_token_hash=hash_token(context_token),
        csrf_token_hash=hash_token(csrf_token),
        expires_at=now + PREAUTH_TTL,
    )
    db.add(row)
    return row, context_token, csrf_token


def revoke_family(db: DbSession, family_id: str) -> None:
    db.execute(
        update(Session)
        .where(Session.family_id == family_id, Session.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )


def rotate_refresh(db: DbSession, raw_refresh: str) -> tuple[Session, str, str, str] | None:
    token_hash = hash_token(raw_refresh)
    now = utc_now()
    auth_session = db.scalar(
        select(Session).where(Session.refresh_token_hash == token_hash).with_for_update()
    )
    if auth_session is None:
        used = db.scalar(
            select(SessionRefreshToken).where(SessionRefreshToken.token_hash == token_hash)
        )
        if used is not None:
            prior_session = db.get(Session, used.session_id)
            if prior_session is not None:
                revoke_family(db, prior_session.family_id)
                db.commit()
        return None
    if auth_session.revoked_at is not None or as_utc(auth_session.expires_at) <= now:
        return None
    old_hash = auth_session.refresh_token_hash
    access_token, refresh_token, csrf_token = new_token(), new_token(), new_token()
    db.add(SessionRefreshToken(token_hash=old_hash, session_id=auth_session.id, consumed_at=now))
    auth_session.access_token_hash = hash_token(access_token)
    auth_session.refresh_token_hash = hash_token(refresh_token)
    auth_session.access_expires_at = min(now + ACCESS_TTL, as_utc(auth_session.expires_at))
    auth_session.csrf_token = csrf_token
    db.flush()
    return auth_session, access_token, refresh_token, csrf_token
