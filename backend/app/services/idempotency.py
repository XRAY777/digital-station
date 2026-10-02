from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import CommandResult

_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _key_and_hash(
    request: Request, actor_id: str, payload: dict[str, Any]
) -> tuple[str, str] | None:
    key = request.headers.get("Idempotency-Key")
    if key is None:
        return None
    if not _KEY.fullmatch(key):
        raise HTTPException(status_code=422, detail="idempotency_key_invalid")
    canonical = json.dumps(
        {"path": request.url.path, "actorId": actor_id, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return key, hashlib.sha256(canonical.encode()).hexdigest()


def cached_command(
    db: Session, station_id: str, request: Request, actor_id: str, payload: dict[str, Any]
) -> Response | None:
    data = _key_and_hash(request, actor_id, payload)
    if data is None:
        return None
    key, request_hash = data
    cached = db.scalar(
        select(CommandResult).where(
            CommandResult.station_id == station_id,
            CommandResult.idempotency_key == key,
        )
    )
    if cached is None:
        return None
    if cached.request_hash != request_hash:
        raise HTTPException(status_code=409, detail="idempotency_key_reused")
    return JSONResponse(
        cached.response,
        status_code=cached.status_code,
        headers={"Idempotency-Replayed": "true", "Cache-Control": "no-store"},
    )


def remember_command(
    db: Session,
    station_id: str,
    request: Request,
    actor_id: str,
    payload: dict[str, Any],
    response: dict[str, Any],
    *,
    status_code: int = 200,
) -> None:
    data = _key_and_hash(request, actor_id, payload)
    if data is None:
        return
    key, request_hash = data
    db.add(
        CommandResult(
            station_id=station_id,
            idempotency_key=key,
            request_hash=request_hash,
            status_code=status_code,
            response=response,
        )
    )
    db.flush()
