from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, model_validator
from simulation.blocks.model import BlockState
from simulation.clock import ClockTransitionError
from simulation.graph.model import StationGraph
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session as DbSession

from backend.app.auth.dependencies import (
    get_db_session,
    require_permission,
)
from backend.app.auth.router import require_authenticated_csrf
from backend.app.events.writer import append_event
from backend.app.models import (
    Conflict,
    Route,
    RouteBlock,
    RouteEdge,
    RouteSignal,
    RouteSwitch,
    Signal,
    SimulationRun,
    Station,
    Switch,
    Track,
    User,
    Yard,
)
from backend.app.services.audit import record_audit
from backend.app.services.idempotency import cached_command, remember_command
from backend.app.services.route_engine import (
    RouteCommandError,
    cancel_route,
    command_switch_position,
    create_route,
    reserve_route,
)
from backend.app.services.simulation_clock import (
    apply_clock_command,
    clock_payload,
)
from backend.app.services.station_state import (
    block_states,
    demo_station,
    edge_lengths,
    latest_run,
    signal_states,
    switch_states,
    train_states,
)
from backend.app.services.topology import demo_yard, load_graph, topology_payload

router = APIRouter(prefix="/api", tags=["station"])
ReadAccess = Annotated[User, Depends(require_permission("station.read"))]


def _yard_or_404(db: DbSession) -> tuple[str, Yard]:
    station = demo_station(db)
    yard = demo_yard(db)
    if station is None or yard is None or yard.station_id != station.id:
        raise HTTPException(status_code=404, detail="station_not_seeded")
    return station.id, yard


def _validated_graph(db: DbSession, yard: Yard) -> StationGraph:
    graph = load_graph(db, yard)
    if graph.validate():
        raise HTTPException(status_code=503, detail="station_topology_invalid")
    return graph


def _route_signal_projection(db: DbSession, route_id: str) -> dict[str, object]:
    signal_ids = db.scalars(
        select(RouteSignal.signal_id).where(RouteSignal.route_id == route_id)
    ).all()
    rows = db.scalars(select(Signal).where(Signal.id.in_(signal_ids))).all() if signal_ids else []
    return {row.id: {"aspect": row.aspect, "reasonCodes": row.reason_codes} for row in rows}


@router.get("/station/topology")
def station_topology(
    user: ReadAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, object]:
    _, yard = _yard_or_404(db)
    try:
        return topology_payload(db, yard)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="station_topology_invalid") from exc


@router.get("/tracks")
def get_tracks(
    user: ReadAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, list[dict[str, object]]]:
    station_id, _ = _yard_or_404(db)
    run = latest_run(db, station_id)
    blocks = block_states(db, run.id if run else None)
    by_track: dict[str, list[BlockState]] = {}
    for block in blocks:
        by_track.setdefault(block.track_id, []).append(block)
    tracks = db.scalars(select(Track).order_by(Track.code)).all()
    items = []
    for track in tracks:
        track_blocks = by_track.get(track.id, [])
        statuses = {block.display_status for block in track_blocks}
        status = next(
            (value for value in ("OCCUPIED", "BLOCKED", "MAINTENANCE") if value in statuses), "FREE"
        )
        items.append(
            {
                "id": track.id,
                "code": track.code,
                "name": track.name,
                "purpose": track.purpose,
                "lengthM": track.length_m,
                "maxSpeedMps": track.max_speed_mps,
                "status": status,
                "blockIds": [block.id for block in track_blocks],
                "availableBlockCount": sum(block.availability == "OPEN" for block in track_blocks),
            }
        )
    return {"items": items}


@router.get("/blocks")
def get_blocks(
    user: ReadAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, list[dict[str, object]]]:
    station_id, _ = _yard_or_404(db)
    run = latest_run(db, station_id)
    current_run_id = run.id if run is not None else None
    items: list[dict[str, object]] = [
        {
            "id": block.id,
            "code": block.code,
            "trackId": block.track_id,
            "lengthM": block.length_m,
            "availability": block.availability,
            "status": block.display_status,
            "occupiedByTrainId": block.occupied_by_train_id,
            "closureReason": block.closure_reason,
            "version": block.version,
        }
        for block in block_states(db, current_run_id)
    ]
    return {"items": items}


@router.get("/switches")
def get_switches(
    user: ReadAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, list[dict[str, object]]]:
    _, yard = _yard_or_404(db)
    items = []
    for switch in switch_states(db, yard.id):
        if switch.validate():
            raise HTTPException(status_code=503, detail="switch_state_invalid")
        items.append(
            {
                "id": switch.id,
                "code": switch.code,
                "nodeId": switch.node_id,
                "position": switch.position,
                "allowedPositions": sorted(switch.allowed_positions),
                "movementState": switch.movement_state,
                "isFailed": switch.is_failed,
            }
        )
    return {"items": items}


@router.get("/signals")
def get_signals(
    user: ReadAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, list[dict[str, object]]]:
    _, yard = _yard_or_404(db)
    return {
        "items": [
            {
                "id": signal.id,
                "code": signal.code,
                "edgeId": signal.edge_id,
                "aspect": signal.aspect,
                "reasonCodes": list(signal.reason_codes),
            }
            for signal in signal_states(db, yard.id)
        ]
    }


@router.get("/trains")
def get_trains(
    user: ReadAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, list[dict[str, object]]]:
    station_id, yard = _yard_or_404(db)
    graph = _validated_graph(db, yard)
    track_by_id = {track.id: track for track in graph.tracks.values()}
    block_to_track = {block.id: block.track_id for block in graph.blocks.values()}
    lengths = edge_lengths(db, yard.id)
    items = []
    run = latest_run(db, station_id)
    for train in train_states(db, run.id if run else None):
        if train.validate(lengths, block_to_track):
            raise HTTPException(status_code=503, detail="train_state_invalid")
        occupied_blocks = [graph.blocks[block_id] for block_id in train.current_block_ids]
        items.append(
            {
                "id": train.id,
                "code": train.code,
                "category": train.category,
                "lengthM": train.length_m,
                "maxSpeedMps": train.max_speed_mps,
                "speedMps": train.speed_mps,
                "targetSpeedMps": train.target_speed_mps,
                "direction": train.direction,
                "status": train.status,
                "priority": train.priority,
                "headPosition": {"edgeId": train.head_edge_id, "offsetM": train.head_offset_m},
                "currentBlockIds": list(train.current_block_ids),
                "currentTrackId": train.current_track_id,
                "currentTrackCode": track_by_id[train.current_track_id].code
                if train.current_track_id is not None
                else None,
                "footprintLengthM": train.length_m,
                "occupiedBlockLengthM": sum(block.length_m for block in occupied_blocks),
            }
        )
    return {"items": items}


@router.get("/simulation")
def get_simulation(
    _: ReadAccess,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, object]:
    station_id, _yard = _yard_or_404(db)
    run = latest_run(db, station_id)
    if run is None:
        raise HTTPException(status_code=404, detail="simulation_run_missing")
    return clock_payload(run)


@router.get("/conflicts")
def get_conflicts(
    _: ReadAccess,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, list[dict[str, object]]]:
    station_id, _yard = _yard_or_404(db)
    run = latest_run(db, station_id)
    if run is None:
        return {"items": []}
    rows = db.scalars(
        select(Conflict)
        .where(Conflict.run_id == run.id)
        .order_by(Conflict.detected_at_ms.desc(), Conflict.id)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "kind": row.kind,
                "severity": row.severity,
                "status": row.status,
                "reason": row.details,
                "detectedAtMs": row.detected_at_ms,
                "resolvedAtMs": row.resolved_at_ms,
            }
            for row in rows
        ]
    }


class ClockControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: Literal["START", "PAUSE", "RESUME", "STOP", "RESET", "SET_SPEED"]
    rate: Literal[1, 2, 5, 10] | None = None

    @model_validator(mode="after")
    def validate_rate(self) -> ClockControl:
        if (self.command == "SET_SPEED") != (self.rate is not None):
            raise ValueError("rate_required_only_for_set_speed")
        return self


@router.post("/simulation/control")
def control_simulation(
    body: ClockControl,
    _: Annotated[object, Depends(require_authenticated_csrf)],
    user: Annotated[User, Depends(require_permission("simulation.manage"))],
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> Any:
    station_id, _yard = _yard_or_404(db)
    station = db.get(Station, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="station_not_seeded")
    command_payload = body.model_dump()
    replay = cached_command(db, station_id, request, user.id, command_payload)
    if replay is not None:
        return replay
    try:
        run = apply_clock_command(
            db,
            station,
            body.command,
            body.rate,
        )
    except ClockTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = clock_payload(run)
    remember_command(db, station_id, request, user.id, command_payload, result)
    db.commit()
    return result


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    train_id: str
    destination_node_id: str


def _active_run(db: DbSession, station_id: str) -> SimulationRun:
    run = latest_run(db, station_id)
    if run is None:
        raise HTTPException(status_code=404, detail="simulation_run_missing")
    return run


@router.post("/routes", status_code=201)
def request_route(
    body: RouteRequest,
    _: Annotated[object, Depends(require_authenticated_csrf)],
    user: Annotated[User, Depends(require_permission("routes.manage"))],
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, object]:
    station_id, yard = _yard_or_404(db)
    run = _active_run(db, station_id)
    try:
        route = create_route(db, run, yard, body.train_id, body.destination_node_id)
        run.state_version += 1
        append_event(
            db,
            run,
            "route.created",
            {
                "routeId": route.id,
                "trainId": route.train_id,
                "status": route.status,
                "edgeIds": db.scalars(
                    select(RouteEdge.edge_id)
                    .where(RouteEdge.route_id == route.id)
                    .order_by(RouteEdge.ordinal)
                ).all(),
                "blockIds": db.scalars(
                    select(RouteBlock.block_id)
                    .where(RouteBlock.route_id == route.id)
                    .order_by(RouteBlock.ordinal)
                ).all(),
                "switchIds": db.scalars(
                    select(RouteSwitch.switch_id).where(RouteSwitch.route_id == route.id)
                ).all(),
                "signalIds": db.scalars(
                    select(RouteSignal.signal_id).where(RouteSignal.route_id == route.id)
                ).all(),
            },
            entity_type="route",
            entity_id=route.id,
        )
        record_audit(
            db,
            run.station_id,
            request,
            "ROUTE_CREATED",
            user=user,
            entity_type="route",
            entity_id=route.id,
            state_version=run.state_version,
            new_value={"trainId": route.train_id, "status": route.status},
        )
        db.commit()
    except RouteCommandError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.code) from exc
    return {"routeId": route.id, "runId": route.run_id, "status": route.status}


@router.post("/routes/{route_id}/reserve")
def route_reserve(
    route_id: str,
    _: Annotated[object, Depends(require_authenticated_csrf)],
    user: Annotated[User, Depends(require_permission("routes.manage"))],
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> Any:
    station_id, yard = _yard_or_404(db)
    run = _active_run(db, station_id)
    command_payload = {"routeId": route_id}
    replay = cached_command(db, station_id, request, user.id, command_payload)
    if replay is not None:
        return replay
    try:
        route = db.get(Route, route_id)
        old_status = route.status if route else None
        route = reserve_route(db, run.id, route_id, yard)
        run.state_version += 1
        append_event(
            db,
            run,
            "route.reserved",
            {
                "routeId": route.id,
                "trainId": route.train_id,
                "status": route.status,
                "signalStates": _route_signal_projection(db, route.id),
            },
            entity_type="route",
            entity_id=route.id,
        )
        record_audit(
            db,
            run.station_id,
            request,
            "ROUTE_RESERVED",
            user=user,
            entity_type="route",
            entity_id=route.id,
            state_version=run.state_version,
            old_value={"status": old_status},
            new_value={"status": route.status},
        )
        result = {"routeId": route.id, "runId": route.run_id, "status": route.status}
        remember_command(db, station_id, request, user.id, command_payload, result)
        db.commit()
    except RouteCommandError as exc:
        if exc.code == "SAFETY_CONFLICT":
            run.state_version += 1
            record_audit(
                db,
                run.station_id,
                request,
                "ROUTE_RESERVED",
                user=user,
                entity_type="route",
                entity_id=route_id,
                state_version=run.state_version,
                result="DENIED",
                new_value={"reason": exc.code},
            )
            db.commit()
        else:
            db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "resourceIds": list(exc.resources)},
        ) from exc
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="RESOURCE_RACE") from exc
    return result


@router.post("/routes/{route_id}/cancel")
def route_cancel(
    route_id: str,
    _: Annotated[object, Depends(require_authenticated_csrf)],
    user: Annotated[User, Depends(require_permission("routes.manage"))],
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, object]:
    station_id, _yard = _yard_or_404(db)
    run = _active_run(db, station_id)
    try:
        route = db.get(Route, route_id)
        old_status = route.status if route else None
        route = cancel_route(db, run.id, route_id)
        run.state_version += 1
        append_event(
            db,
            run,
            "route.cancelled",
            {
                "routeId": route.id,
                "trainId": route.train_id,
                "status": route.status,
                "signalStates": _route_signal_projection(db, route.id),
            },
            entity_type="route",
            entity_id=route.id,
        )
        record_audit(
            db,
            run.station_id,
            request,
            "ROUTE_CANCELLED",
            user=user,
            entity_type="route",
            entity_id=route.id,
            state_version=run.state_version,
            old_value={"status": old_status},
            new_value={"status": route.status},
        )
        db.commit()
    except RouteCommandError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.code) from exc
    return {"routeId": route.id, "runId": route.run_id, "status": route.status}


class SwitchPositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position: str


@router.post("/switches/{switch_id}/position")
def set_switch_position(
    switch_id: str,
    body: SwitchPositionRequest,
    _: Annotated[object, Depends(require_authenticated_csrf)],
    user: Annotated[User, Depends(require_permission("routes.manage"))],
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, object]:
    station_id, yard = _yard_or_404(db)
    run = _active_run(db, station_id)
    try:
        switch = db.get(Switch, switch_id)
        old_state = switch.state if switch else None
        switch = command_switch_position(db, run.id, yard, switch_id, body.position)
        run.state_version += 1
        append_event(
            db,
            run,
            "switch.position.changed",
            {"switchId": switch.id, "oldPosition": old_state, "position": switch.state},
            entity_type="switch",
            entity_id=switch.id,
        )
        record_audit(
            db,
            run.station_id,
            request,
            "SWITCH_CHANGED",
            user=user,
            entity_type="switch",
            entity_id=switch.id,
            state_version=run.state_version,
            old_value={"position": old_state},
            new_value={"position": switch.state},
        )
        db.commit()
    except RouteCommandError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "resourceIds": list(exc.resources)},
        ) from exc
    return {"switchId": switch.id, "position": switch.state, "movementState": switch.movement_state}
