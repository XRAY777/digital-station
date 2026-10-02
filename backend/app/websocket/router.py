from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from datetime import UTC, datetime

from database.connection import make_session_factory
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from backend.app.auth.service import as_utc, hash_token, utc_now
from backend.app.models import (
    Permission,
    Role,
    RolePermission,
    Session,
    SimulationEvent,
    SimulationRun,
    Station,
    User,
    UserRole,
)
from backend.app.services.station_state import (
    block_states,
    signal_states,
    switch_states,
    train_states,
)
from backend.app.services.topology import demo_yard

router = APIRouter()


def _authorize(websocket: WebSocket, station_id: str) -> tuple[bool, str | None]:
    origin = websocket.headers.get("origin", "").rstrip("/")
    base_origin = str(websocket.base_url).rstrip("/")
    if base_origin.startswith("ws://"):
        base_origin = "http://" + base_origin[5:]
    elif base_origin.startswith("wss://"):
        base_origin = "https://" + base_origin[6:]
    allowed_origins = {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        base_origin,
    }
    allowed_origins.update(
        item.strip().rstrip("/")
        for item in os.getenv("STATION_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    )
    if not origin or origin not in allowed_origins:
        return False, None
    raw = websocket.cookies.get("station_access")
    if not raw:
        return False, None
    factory = make_session_factory(websocket.app.state.db_engine)
    with factory() as db:
        auth = db.scalar(select(Session).where(Session.access_token_hash == hash_token(raw)))
        if (
            auth is None
            or auth.revoked_at is not None
            or as_utc(auth.access_expires_at) <= utc_now()
            or as_utc(auth.expires_at) <= utc_now()
        ):
            return False, None
        user = db.get(User, auth.user_id)
        permission = db.scalar(
            select(Permission.id)
            .join(RolePermission)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole)
            .where(UserRole.user_id == auth.user_id, Permission.code == "station.read")
            .limit(1)
        )
        station = db.get(Station, station_id)
        return bool(user and user.is_active and permission and station), user.id if user else None


@router.websocket("/ws/station/{station_id}")
async def station_stream(websocket: WebSocket, station_id: str) -> None:
    allowed, _ = _authorize(websocket, station_id)
    if not allowed:
        await websocket.close(code=1008, reason="authentication_or_origin_denied")
        return
    await websocket.accept()
    try:
        subscription = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if subscription.get("type") != "SUBSCRIBE" or subscription.get("stationId") != station_id:
            await websocket.close(code=1008, reason="invalid_subscription")
            return
        last_sequence = max(0, int(subscription.get("lastSequence", 0)))
        factory = make_session_factory(websocket.app.state.db_engine)
        with factory() as db:
            run = db.scalar(
                select(SimulationRun)
                .where(SimulationRun.station_id == station_id)
                .order_by(SimulationRun.started_at.desc())
                .limit(1)
            )
            if run is None:
                await websocket.send_json(
                    {
                        "type": "SNAPSHOT",
                        "stationId": station_id,
                        "simulationRunId": None,
                        "sequence": 0,
                        "state": {},
                    }
                )
                return
            active_run_id = run.id
            station = db.get(Station, station_id)
            if station is None:
                await websocket.close(code=1008, reason="station_missing")
                return
            watermark = (
                db.scalar(
                    select(SimulationEvent.sequence)
                    .where(SimulationEvent.run_id == run.id)
                    .order_by(SimulationEvent.sequence.desc())
                    .limit(1)
                )
                or 0
            )
            first = (
                db.scalar(
                    select(SimulationEvent.sequence)
                    .where(SimulationEvent.run_id == run.id)
                    .order_by(SimulationEvent.sequence)
                    .limit(1)
                )
                or watermark + 1
            )
            yard = demo_yard(db)
            snapshot = {
                "station": {"id": station.id, "code": station.code},
                "simulation": {
                    "id": run.id,
                    "status": run.status,
                    "simulationTimeMs": run.simulation_time_ms,
                    "stateVersion": run.state_version,
                },
                "trains": [asdict(item) for item in train_states(db, run.id)],
                "blocks": [asdict(item) for item in block_states(db, run.id)],
                "switches": [asdict(item) for item in switch_states(db, yard.id)] if yard else [],
                "signals": [asdict(item) for item in signal_states(db, yard.id)] if yard else [],
            }
            if last_sequence and last_sequence + 1 < first:
                last_sequence = -1
            await websocket.send_json(
                jsonable_encoder(
                    {
                        "type": "SNAPSHOT",
                        "schemaVersion": 1,
                        "stationId": station_id,
                        "simulationRunId": run.id,
                        "sequence": watermark,
                        "stateVersion": run.state_version,
                        "state": snapshot,
                    }
                )
            )
            last_sequence = watermark
        heartbeat_at = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(0.25)
            with factory() as db:
                run = db.get(SimulationRun, active_run_id)
                if run is None:
                    await websocket.send_json({"type": "RESYNC_REQUIRED", "reason": "run_missing"})
                    return
                rows = db.scalars(
                    select(SimulationEvent)
                    .where(
                        SimulationEvent.run_id == run.id, SimulationEvent.sequence > last_sequence
                    )
                    .order_by(SimulationEvent.sequence)
                    .limit(1001)
                ).all()
                if len(rows) > 1000 or (rows and rows[0].sequence != last_sequence + 1):
                    await websocket.send_json({"type": "RESYNC_REQUIRED", "reason": "sequence_gap"})
                    return
                for event in rows:
                    await websocket.send_json(
                        {
                            "messageType": "FRAME",
                            "eventId": event.id,
                            "sequence": event.sequence,
                            "schemaVersion": event.schema_version,
                            "stationId": station_id,
                            "simulationRunId": run.id,
                            "stateVersion": event.state_version,
                            "simulationTimeMs": event.simulation_time_ms,
                            "occurredAt": (event.occurred_at or datetime.now(UTC)).isoformat(),
                            "type": event.event_type,
                            "payload": event.payload,
                        }
                    )
                    last_sequence = event.sequence
            if asyncio.get_running_loop().time() - heartbeat_at >= 5:
                allowed, _ = _authorize(websocket, station_id)
                if not allowed:
                    await websocket.close(code=1008, reason="session_revoked")
                    return
                await websocket.send_json(
                    {"type": "HEARTBEAT", "occurredAt": datetime.now(UTC).isoformat()}
                )
                heartbeat_at = asyncio.get_running_loop().time()
    except WebSocketDisconnect:
        return
