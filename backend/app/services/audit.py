from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from backend.app.auth.service import hash_token, make_user_view
from backend.app.models import AuditLog, Session, Station, User


def record_audit(
    db: DbSession,
    station_id: str,
    request: Request,
    action: str,
    *,
    user: User | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    state_version: int | None = None,
    old_value: dict[str, object] | None = None,
    new_value: dict[str, object] | None = None,
    result: str = "SUCCESS",
    commit: bool = False,
    session_id: str | None = None,
) -> AuditLog:
    raw_session = request.cookies.get("station_access")
    auth = (
        db.scalar(select(Session).where(Session.access_token_hash == hash_token(raw_session)))
        if raw_session
        else None
    )
    role = None
    if user is not None:
        view = make_user_view(db, user)
        role = ",".join(view.roles)
    row = AuditLog(
        station_id=station_id,
        actor_id=user.id if user else None,
        username=user.username if user else None,
        role=role,
        action=action[:80],
        entity_type=entity_type[:40] if entity_type else None,
        entity_id=entity_id[:36] if entity_id else None,
        request_id=request.headers.get("x-request-id") or str(uuid4()),
        session_id=session_id or (auth.id if auth else None),
        ip_address=request.client.host[:64] if request.client else None,
        state_version=state_version,
        old_value=old_value,
        new_value=new_value,
        result=result,
        details={},
    )
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return row


def station_id_for_audit(db: DbSession) -> str | None:
    return db.scalar(select(Station.id).order_by(Station.id).limit(1))
