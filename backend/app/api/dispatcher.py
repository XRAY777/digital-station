from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from metrics.efficiency import calculate
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from backend.app.auth.dependencies import get_db_session, require_permission
from backend.app.auth.router import require_authenticated_csrf
from backend.app.auth.service import make_user_view
from backend.app.events.writer import append_event
from backend.app.models import (
    AIRecommendation,
    Block,
    Conflict,
    Crew,
    EfficiencyHistory,
    Incident,
    Locomotive,
    Occupancy,
    Operation,
    OptimizationPlan,
    OptimizationVariant,
    Platform,
    ResourceReservation,
    Route,
    RouteBlock,
    RouteEdge,
    RouteSignal,
    RouteSwitch,
    Schedule,
    ScheduleEvent,
    SimulationEvent,
    SimulationRun,
    Switch,
    Track,
    Train,
    User,
)
from backend.app.services.ai_assistant import (
    OfflineProvider,
    provider_from_environment,
    validate_recommendation,
)
from backend.app.services.audit import record_audit
from backend.app.services.idempotency import cached_command, remember_command
from backend.app.services.planning import efficiency_for_run, optimize_run
from backend.app.services.runtime_tick import route_activation_wait_reason
from backend.app.services.station_state import demo_station, latest_run
from backend.app.services.topology import demo_yard, load_graph, topology_payload

router = APIRouter(prefix="/api/dispatcher", tags=["dispatcher"])
ReadAccess = Annotated[User, Depends(require_permission("station.read"))]


@router.get("/snapshot")
def dispatcher_snapshot(
    user: ReadAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, object]:
    station = demo_station(db)
    yard = demo_yard(db)
    if station is None or yard is None or yard.station_id != station.id:
        raise HTTPException(status_code=404, detail="station_not_seeded")
    run = latest_run(db, station.id)
    if run is None:
        raise HTTPException(status_code=404, detail="simulation_run_missing")
    topology = topology_payload(db, yard)
    graph = load_graph(db, yard)
    tracks = db.scalars(select(Track).where(Track.yard_id == yard.id).order_by(Track.code)).all()
    blocks = db.scalars(
        select(Block)
        .join(Track, Track.id == Block.track_id)
        .where(Track.yard_id == yard.id)
        .order_by(Track.code, Block.ordinal)
    ).all()
    occupants = {
        row.block_id: row.train_id
        for row in db.scalars(
            select(Occupancy).where(Occupancy.run_id == run.id, Occupancy.released_at_ms.is_(None))
        ).all()
    }
    block_payload = [
        {
            "id": block.id,
            "code": block.code,
            "trackId": block.track_id,
            "lengthM": block.length_m,
            "availability": block.availability,
            "status": "BLOCKED"
            if block.availability != "OPEN"
            else ("OCCUPIED" if block.id in occupants else "FREE"),
            "occupiedByTrainId": occupants.get(block.id),
            "closureReason": block.closure_reason,
        }
        for block in blocks
    ]
    train_rows = db.scalars(
        select(Train).where(Train.station_id == station.id).order_by(Train.code)
    ).all()
    trains = [
        {
            "id": train.id,
            "code": train.code,
            "source": train.source,
            "category": train.category,
            "lengthM": train.length_m,
            "status": train.status,
            "direction": train.direction,
            "speedMps": train.speed_mps,
            "targetSpeedMps": train.target_speed_mps,
            "priority": train.priority,
            "destination": train.destination,
            "headPosition": {"edgeId": train.head_edge_id, "offsetM": train.head_offset_m},
            "currentTrackId": next(
                (b.track_id for b in blocks if occupants.get(b.id) == train.id), None
            ),
            "currentBlockIds": [
                block_id for block_id, owner in occupants.items() if owner == train.id
            ],
            "delaySeconds": max(
                (
                    s.delay_seconds
                    for s in db.scalars(
                        select(Schedule).where(
                            Schedule.run_id == run.id, Schedule.train_id == train.id
                        )
                    ).all()
                ),
                default=0,
            ),
        }
        for train in train_rows
    ]
    routes = db.scalars(
        select(Route).where(Route.run_id == run.id).order_by(Route.created_at.desc())
    ).all()
    route_payload = []
    for route in routes:
        activation_reason = (
            route_activation_wait_reason(db, run, route, graph, occupants)
            if route.status in ("RESERVED", "ACTIVE")
            else None
        )
        route_payload.append(
            {
                "id": route.id,
                "trainId": route.train_id,
                "status": route.status,
                "activationReason": activation_reason or (
                    "Waiting for activation" if route.status == "RESERVED" else None
                ),
                "graphVersion": route.graph_version,
                "failureReason": route.failure_reason,
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
            }
        )
    conflicts = db.scalars(
        select(Conflict)
        .where(Conflict.run_id == run.id)
        .order_by(Conflict.detected_at_ms.desc())
        .limit(100)
    ).all()
    incidents = db.scalars(
        select(Incident)
        .where(Incident.run_id == run.id)
        .order_by(Incident.opened_at.desc())
        .limit(100)
    ).all()
    schedules = db.scalars(
        select(Schedule).where(Schedule.run_id == run.id).order_by(Schedule.scheduled_at).limit(200)
    ).all()
    schedule_payload = []
    for schedule in schedules:
        actual = db.scalar(
            select(ScheduleEvent)
            .where(ScheduleEvent.schedule_id == schedule.id)
            .order_by(ScheduleEvent.actual_at.desc())
            .limit(1)
        )
        schedule_payload.append(
            {
                "id": schedule.id,
                "trainId": schedule.train_id,
                "scheduledAt": schedule.scheduled_at.isoformat(),
                "eventType": schedule.event_type,
                "platformId": schedule.platform_id,
                "priority": schedule.priority,
                "delaySeconds": schedule.delay_seconds,
                "actualAt": actual.actual_at.isoformat() if actual and actual.actual_at else None,
                "actualStatus": actual.status if actual else "PLANNED",
            }
        )
    plans = db.scalars(
        select(OptimizationPlan)
        .where(OptimizationPlan.run_id == run.id)
        .order_by(OptimizationPlan.generated_at.desc())
        .limit(20)
    ).all()
    plan_payload = [
        {
            "id": plan.id,
            "status": plan.status,
            "sourceStateVersion": plan.base_version,
            "generatedAt": plan.generated_at.isoformat(),
            "calculationTimeMs": plan.calculation_time_ms,
            "objective": plan.objective,
            "variants": [
                {
                    "id": variant.id,
                    "ordinal": variant.ordinal,
                    "score": variant.score,
                    "operations": variant.operations,
                    "explanation": variant.explanation,
                }
                for variant in db.scalars(
                    select(OptimizationVariant)
                    .where(OptimizationVariant.plan_id == plan.id)
                    .order_by(OptimizationVariant.ordinal)
                ).all()
            ],
        }
        for plan in plans
    ]
    reservations = db.scalars(
        select(ResourceReservation)
        .where(
            ResourceReservation.run_id == run.id,
            ResourceReservation.status.in_(("PLANNED", "ACTIVE", "HELD")),
        )
        .order_by(ResourceReservation.starts_at_ms)
    ).all()
    resources = {
        "locomotives": [
            {"id": row.id, "code": row.code, "status": row.status}
            for row in db.scalars(
                select(Locomotive)
                .where(Locomotive.station_id == station.id)
                .order_by(Locomotive.code)
            ).all()
        ],
        "crews": [
            {"id": row.id, "code": row.code, "status": row.status}
            for row in db.scalars(
                select(Crew).where(Crew.station_id == station.id).order_by(Crew.code)
            ).all()
        ],
        "platforms": [
            {"id": row.id, "code": row.code, "name": row.name, "lengthM": row.length_m}
            for row in db.scalars(
                select(Platform).where(Platform.yard_id == yard.id).order_by(Platform.code)
            ).all()
        ],
        "reservations": [
            {
                "id": row.id,
                "type": row.resource_type,
                "resourceId": row.resource_id,
                "startsAtMs": row.starts_at_ms,
                "endsAtMs": row.ends_at_ms,
                "status": row.status,
                "trainId": row.owner_train_id,
                "routeId": row.owner_route_id,
            }
            for row in reservations
        ],
    }
    latest_efficiency = db.scalar(
        select(EfficiencyHistory)
        .where(EfficiencyHistory.run_id == run.id)
        .order_by(EfficiencyHistory.recorded_at_ms.desc())
        .limit(1)
    )
    efficiency = (
        {"score": latest_efficiency.score, **latest_efficiency.components}
        if latest_efficiency
        else calculate(
            throughput_per_hour=float(run.config.get("throughputPerHour", 0)),
            average_delay_seconds=float(run.config.get("averageDelaySeconds", 0)),
            track_utilization=float(run.config.get("trackUtilization", 0)),
            active_conflicts=sum(row.status == "OPEN" for row in conflicts),
            resource_idle=float(run.config.get("resourceIdle", 0)),
        )
    )
    events = db.scalars(
        select(SimulationEvent)
        .where(SimulationEvent.run_id == run.id)
        .order_by(SimulationEvent.sequence.desc())
        .limit(100)
    ).all()
    return {
        "station": {
            "id": station.id,
            "code": station.code,
            "name": station.name,
            "timezone": station.timezone,
        },
        "user": make_user_view(db, user).model_dump(),
        "simulation": {
            "runId": run.id,
            "status": run.status,
            "simulationTimeMs": run.simulation_time_ms,
            "stateVersion": run.state_version,
            "clockRate": run.clock_rate,
        },
        "topology": topology,
        "tracks": [
            {
                "id": track.id,
                "code": track.code,
                "name": track.name,
                "purpose": track.purpose,
                "lengthM": track.length_m,
                "maxSpeedMps": track.max_speed_mps,
                "blockIds": [block.id for block in blocks if block.track_id == track.id],
                "status": next(
                    (
                        status
                        for status in ("BLOCKED", "MAINTENANCE", "OCCUPIED")
                        if any(
                            item["trackId"] == track.id and item["status"] == status
                            for item in block_payload
                        )
                    ),
                    "FREE",
                ),
            }
            for track in tracks
        ],
        "blocks": block_payload,
        "trains": trains,
        "routes": route_payload,
        "conflicts": [
            {
                "id": row.id,
                "kind": row.kind,
                "severity": row.severity,
                "status": row.status,
                "details": row.details,
                "detectedAtMs": row.detected_at_ms,
            }
            for row in conflicts
        ],
        "incidents": [
            {
                "id": row.id,
                "kind": row.kind,
                "type": row.kind,
                "severity": row.severity,
                "status": row.status,
                "description": row.payload.get("description", row.payload.get("summary", "")),
                "affectedEntities": row.payload.get("affectedEntities", {}),
                "source": row.source,
                "metadata": row.payload,
                "payload": row.payload,
                "openedAt": row.opened_at.isoformat(),
                "createdAt": row.opened_at.isoformat(),
                "resolvedAt": row.resolved_at.isoformat() if row.resolved_at else None,
            }
            for row in incidents
        ],
        "schedules": schedule_payload,
        "plans": plan_payload,
        "efficiency": efficiency,
        "ai": {
            "status": "OFFLINE" if provider_from_environment().name == "OFFLINE" else "CONFIGURED"
        },
        "resources": resources,
        "events": [
            {
                "eventId": row.id,
                "sequence": row.sequence,
                "simulationTimeMs": row.simulation_time_ms,
                "type": row.event_type,
                "entityId": row.entity_id,
                "payload": row.payload,
            }
            for row in reversed(events)
        ],
    }


class IncidentBody(BaseModel):
    kind: str = Field(min_length=2, max_length=32)
    severity: str = Field(default="WARNING", pattern="^(INFO|WARNING|CRITICAL)$")
    summary: str | None = Field(default=None, min_length=3, max_length=300)
    description: str | None = Field(default=None, min_length=3, max_length=500)
    source: str = Field(default="DISPATCHER", max_length=40)
    trainIds: list[str] = Field(default_factory=list, max_length=20)
    trackIds: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/incidents", status_code=201)
def create_incident(
    body: IncidentBody,
    _: Annotated[Any, Depends(require_authenticated_csrf)],
    user: Annotated[User, Depends(require_permission("incidents.manage"))],
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> Any:
    station = demo_station(db)
    run = latest_run(db, station.id) if station else None
    if station is None or run is None:
        raise HTTPException(status_code=404, detail="simulation_run_missing")
    allowed_types = {
        "TRAIN_DELAY",
        "TRACK_CLOSURE",
        "SWITCH_FAILURE",
        "ROUTE_CONFLICT",
        "PLATFORM_OCCUPIED",
        "LOCOMOTIVE_UNAVAILABLE",
        "CREW_UNAVAILABLE",
        "MULTIPLE_INCIDENTS",
        "EMERGENCY_STOP",
        "DISPATCHER_NOTE",
    }
    if body.kind not in allowed_types:
        raise HTTPException(status_code=422, detail="incident_type_invalid")
    command_payload = body.model_dump()
    replay = cached_command(db, station.id, request, user.id, command_payload)
    if replay is not None:
        return replay
    description = body.description or body.summary or body.kind.replace("_", " ")
    affected = {key: value for key, value in body.metadata.items()}
    affected.update({"trainIds": body.trainIds, "trackIds": body.trackIds})
    closure_block_ids: list[str] = []
    if body.kind == "TRACK_CLOSURE":
        if not body.trackIds:
            raise HTTPException(status_code=422, detail="incident_tracks_required")
        known = set(db.scalars(select(Track.id).where(Track.id.in_(body.trackIds))).all())
        if known != set(body.trackIds):
            raise HTTPException(status_code=404, detail="incident_track_not_found")
        blocks_to_close = db.scalars(select(Block).where(Block.track_id.in_(known))).all()
        for block in blocks_to_close:
            closure_block_ids.append(block.id)
            block.availability = "BLOCKED"
            block.closure_reason = description[:200]
            block.version += 1
    elif body.kind == "SWITCH_FAILURE":
        switch_id = str(body.metadata.get("switchId", ""))
        switch = db.get(Switch, switch_id)
        if switch is None:
            raise HTTPException(status_code=422, detail="incident_switch_required")
        switch.is_failed = True
        switch.failure_reason = description[:200]
        affected["switchIds"] = [switch.id]
    elif body.kind in {"LOCOMOTIVE_UNAVAILABLE", "CREW_UNAVAILABLE"}:
        resource_id = str(body.metadata.get("resourceId", ""))
        resource = (
            db.get(Locomotive, resource_id)
            if body.kind == "LOCOMOTIVE_UNAVAILABLE"
            else db.get(Crew, resource_id)
        )
        if resource is None:
            raise HTTPException(status_code=422, detail="incident_resource_required")
        resource.status = "UNAVAILABLE"
        affected["resourceIds"] = [resource.id]
    elif body.kind == "TRAIN_DELAY":
        delay_seconds = max(0, min(int(body.metadata.get("delaySeconds", 300)), 86_400))
        affected["delaySeconds"] = delay_seconds
        for schedule in db.scalars(
            select(Schedule).where(Schedule.run_id == run.id, Schedule.train_id.in_(body.trainIds))
        ).all():
            schedule.delay_seconds += delay_seconds
        for operation in db.scalars(
            select(Operation).where(
                Operation.run_id == run.id, Operation.train_id.in_(body.trainIds)
            )
        ).all():
            operation.payload = {
                **operation.payload,
                "delayMs": int(operation.payload.get("delayMs", 0)) + delay_seconds * 1000,
            }
    elif body.kind == "PLATFORM_OCCUPIED":
        platform_id = str(body.metadata.get("platformId", ""))
        if db.get(Platform, platform_id) is None:
            raise HTTPException(status_code=422, detail="incident_platform_required")
        affected["platformIds"] = [platform_id]
    elif body.kind == "EMERGENCY_STOP":
        from backend.app.services.simulation_clock import apply_clock_command

        station_row = station
        apply_clock_command(db, station_row, "STOP", None)

    incident = Incident(
        run_id=run.id,
        kind=body.kind,
        severity=body.severity,
        status="OPEN",
        source=body.source,
        payload={
            "severity": body.severity,
            "summary": description,
            "description": description,
            "affectedEntities": affected,
            "source": body.source,
            **affected,
            "createdBy": user.id,
        },
    )
    db.add(incident)
    db.flush()
    run.state_version += 1
    previous = db.scalar(
        select(OptimizationPlan)
        .where(OptimizationPlan.run_id == run.id)
        .order_by(OptimizationPlan.generated_at.desc())
        .limit(1)
    )
    efficiency_before = efficiency_for_run(db, station, run)
    previous_delay = sum(
        schedule.delay_seconds
        for schedule in db.scalars(select(Schedule).where(Schedule.run_id == run.id)).all()
    )
    try:
        new_plan, variants = optimize_run(db, run)
    except Exception:
        db.rollback()
        record_audit(
            db,
            station.id,
            request,
            "INCIDENT_CREATED",
            user=user,
            entity_type="incident",
            result="FAILURE",
            new_value={"kind": body.kind, "reason": "incident_replanning_failed"},
        )
        db.commit()
        raise HTTPException(status_code=503, detail="incident_replanning_failed") from None
    best = min(
        variants,
        key=lambda item: float(item.explanation.get("totalDelayMs", 0)),
        default=None,
    )
    incident.payload = {
        **incident.payload,
        "replanning": {
            "previousPlanId": previous.id if previous else None,
            "newPlanId": new_plan.id,
            "sourceStateVersion": run.state_version,
            "incidentId": incident.id,
            "affectedTrains": body.trainIds,
            "affectedResources": list(affected.get("resourceIds", [])) + body.trackIds,
            "calculationTimeMs": new_plan.calculation_time_ms,
            "expectedDelayBefore": previous_delay,
            "expectedDelayAfter": (
                best.explanation.get("totalDelayMs", previous_delay * 1000) / 1000
                if best
                else previous_delay
            ),
            "efficiencyBefore": efficiency_before.get("score"),
            "efficiencyAfter": efficiency_before.get("score"),
            "reason": description,
            "variants": [
                {
                    "id": row.id,
                    "ordinal": row.ordinal,
                    "score": row.score,
                    "operations": row.operations,
                    "explanation": row.explanation,
                }
                for row in variants
            ],
        },
    }
    append_event(
        db,
        run,
        "incident.created",
        {
            "incidentId": incident.id,
            "kind": incident.kind,
            "status": incident.status,
            "blockIds": closure_block_ids,
            **incident.payload,
        },
        entity_type="incident",
        entity_id=incident.id,
    )
    record_audit(
        db,
        station.id,
        request,
        "INCIDENT_CREATED",
        user=user,
        entity_type="incident",
        entity_id=incident.id,
        state_version=run.state_version,
        new_value=incident.payload,
    )
    result = {
        "id": incident.id,
        "type": incident.kind,
        "severity": incident.severity,
        "status": incident.status,
        "affectedEntities": affected,
        "description": description,
        "createdAt": incident.opened_at.isoformat(),
        "resolvedAt": None,
        "source": incident.source,
        "metadata": incident.payload,
        "stateVersion": run.state_version,
        "replanning": incident.payload["replanning"],
    }
    remember_command(db, station.id, request, user.id, command_payload, result, status_code=201)
    db.commit()
    return result


@router.post("/incidents/{incident_id}/resolve")
def resolve_incident(
    incident_id: str,
    _: Annotated[Any, Depends(require_authenticated_csrf)],
    user: Annotated[User, Depends(require_permission("incidents.manage"))],
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, object]:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident_not_found")
    run = db.get(SimulationRun, incident.run_id)
    if incident.status not in {"OPEN", "ACKNOWLEDGED", "RESOLVING"} or run is None:
        raise HTTPException(status_code=409, detail="incident_not_open")
    old_payload = dict(incident.payload)
    old_status = incident.status
    resolved_at = datetime.now(UTC)
    incident.status = "RESOLVED"
    incident.resolved_at = resolved_at
    incident.payload = {
        **incident.payload,
        "resolvedBy": user.id,
        "resolvedAt": resolved_at.isoformat(),
    }
    # Restore infrastructure only when no other active incident still affects it.
    other_incidents = db.scalars(
        select(Incident).where(
            Incident.run_id == run.id,
            Incident.id != incident.id,
            Incident.status.in_(("OPEN", "ACKNOWLEDGED", "RESOLVING")),
        )
    ).all()
    active_track_ids = {
        str(item) for other in other_incidents for item in other.payload.get("trackIds", [])
    }
    restored_block_ids: list[str] = []
    restored_switch_ids: list[str] = []
    if incident.kind == "TRACK_CLOSURE":
        for block in db.scalars(
            select(Block).where(Block.track_id.in_(incident.payload.get("trackIds", [])))
        ).all():
            if (
                block.track_id not in active_track_ids
                and block.closure_reason == incident.payload.get("description")
            ):
                block.availability = "OPEN"
                block.closure_reason = None
                block.version += 1
                restored_block_ids.append(block.id)
    elif incident.kind == "SWITCH_FAILURE":
        for switch_id in incident.payload.get("switchIds", []):
            if not any(
                switch_id in other.payload.get("switchIds", []) for other in other_incidents
            ):
                switch = db.get(Switch, str(switch_id))
                if switch is not None:
                    switch.is_failed = False
                    switch.failure_reason = None
                    restored_switch_ids.append(switch.id)
    elif incident.kind in {"LOCOMOTIVE_UNAVAILABLE", "CREW_UNAVAILABLE"}:
        for resource_id in incident.payload.get("resourceIds", []):
            if not any(
                resource_id in other.payload.get("resourceIds", []) for other in other_incidents
            ):
                resource = (
                    db.get(Locomotive, str(resource_id))
                    if incident.kind == "LOCOMOTIVE_UNAVAILABLE"
                    else db.get(Crew, str(resource_id))
                )
                if resource is not None and resource.status == "UNAVAILABLE":
                    resource.status = "AVAILABLE"
    run.state_version += 1
    append_event(
        db,
        run,
        "incident.resolved",
        {
            "incidentId": incident.id,
            "resolvedAt": resolved_at.isoformat(),
            "restoredBlockIds": restored_block_ids,
            "restoredSwitchIds": restored_switch_ids,
        },
        entity_type="incident",
        entity_id=incident.id,
    )
    record_audit(
        db,
        run.station_id,
        request,
        "INCIDENT_RESOLVED",
        user=user,
        entity_type="incident",
        entity_id=incident.id,
        state_version=run.state_version,
        old_value={"status": old_status, "payload": old_payload},
        new_value=incident.payload,
    )
    db.commit()
    return {
        "id": incident.id,
        "status": incident.status,
        "resolvedAt": resolved_at.isoformat(),
        "stateVersion": run.state_version,
    }


@router.post("/incidents/{incident_id}/acknowledge")
def acknowledge_incident(
    incident_id: str,
    _: Annotated[Any, Depends(require_authenticated_csrf)],
    user: Annotated[User, Depends(require_permission("incidents.manage"))],
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> Any:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident_not_found")
    run = db.get(SimulationRun, incident.run_id)
    if run is None:
        raise HTTPException(status_code=409, detail="incident_not_open")
    command_payload = {"incidentId": incident_id, "action": "acknowledge"}
    replay = cached_command(db, run.station_id, request, user.id, command_payload)
    if replay is not None:
        return replay
    if incident.status != "OPEN":
        raise HTTPException(status_code=409, detail="incident_not_open")
    old_status = incident.status
    incident.status = "ACKNOWLEDGED"
    incident.payload = {
        **incident.payload,
        "acknowledgedBy": user.id,
        "acknowledgedAt": datetime.now(UTC).isoformat(),
    }
    run.state_version += 1
    append_event(
        db,
        run,
        "incident.acknowledged",
        {"incidentId": incident.id},
        entity_type="incident",
        entity_id=incident.id,
    )
    record_audit(
        db,
        run.station_id,
        request,
        "INCIDENT_ACKNOWLEDGED",
        user=user,
        entity_type="incident",
        entity_id=incident.id,
        state_version=run.state_version,
        old_value={"status": old_status},
        new_value={"status": incident.status},
    )
    result = {"id": incident.id, "status": incident.status, "stateVersion": run.state_version}
    remember_command(db, run.station_id, request, user.id, command_payload, result)
    db.commit()
    return result


class AssistantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selectedTrainId: str | None = Field(default=None, max_length=36)
    selectedRouteId: str | None = Field(default=None, max_length=36)


@router.post("/ai/recommendations", dependencies=[Depends(require_authenticated_csrf)])
def create_ai_recommendation(
    body: AssistantBody,
    user: ReadAccess,
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, object]:
    station = demo_station(db)
    run = latest_run(db, station.id) if station else None
    if station is None or run is None:
        raise HTTPException(status_code=404, detail="simulation_run_missing")
    snapshot = dispatcher_snapshot(user, db)
    trains = cast(list[dict[str, Any]], snapshot["trains"])
    routes = cast(list[dict[str, Any]], snapshot["routes"])
    conflicts = cast(list[dict[str, Any]], snapshot["conflicts"])
    incidents = cast(list[dict[str, Any]], snapshot["incidents"])
    plans = [
        item
        for item in cast(list[dict[str, Any]], snapshot["plans"])
        if item["status"] == "FEASIBLE" and item["sourceStateVersion"] == run.state_version
    ]
    selected_train = next((item for item in trains if item["id"] == body.selectedTrainId), None)
    selected_route = next((item for item in routes if item["id"] == body.selectedRouteId), None)
    if body.selectedTrainId and selected_train is None:
        raise HTTPException(status_code=404, detail="train_not_found")
    if body.selectedRouteId and selected_route is None:
        raise HTTPException(status_code=404, detail="route_not_found")
    context = {
        "stationState": snapshot["simulation"],
        "selectedTrain": selected_train,
        "selectedRoute": selected_route,
        "conflicts": [item for item in conflicts if item["status"] == "OPEN"],
        "optimizationPlans": plans,
        "efficiency": snapshot["efficiency"],
        "incidents": [item for item in incidents if item["status"] == "OPEN"],
        "resources": snapshot["resources"],
    }
    provider = provider_from_environment()
    try:
        raw = provider.recommend(context)
        provider_name = provider.name
    except (ValueError, OSError, KeyError, IndexError, TypeError):
        provider = OfflineProvider()
        raw = provider.recommend(context)
        provider_name = "OFFLINE_FALLBACK"
    variants = {
        str(variant["id"]): str(plan["id"])
        for plan in plans
        for variant in cast(list[dict[str, Any]], plan["variants"])
    }
    recommendation = validate_recommendation(raw, variants)
    plan_id = recommendation["planId"]
    record = AIRecommendation(
        run_id=run.id,
        plan_id=plan_id if isinstance(plan_id, str) else None,
        provider=provider_name,
        explanation=json.dumps(recommendation, ensure_ascii=False),
    )
    db.add(record)
    append_event(
        db,
        run,
        "ai.recommendation.created",
        {
            "recommendationId": record.id,
            "planId": record.plan_id,
            "provider": provider_name,
            "sourceStateVersion": run.state_version,
        },
        entity_type="ai_recommendation",
        entity_id=record.id,
    )
    record_audit(
        db,
        station.id,
        request,
        "AI_RECOMMENDATION_CREATED",
        user=user,
        entity_type="ai_recommendation",
        entity_id=record.id,
        state_version=run.state_version,
        new_value=recommendation,
    )
    db.commit()
    return {
        "id": record.id,
        "provider": provider_name,
        "sourceStateVersion": run.state_version,
        **recommendation,
    }


@router.post("/ai/recommendations/{recommendation_id}/reject")
def reject_ai_recommendation(
    recommendation_id: str,
    _: Annotated[Any, Depends(require_authenticated_csrf)],
    user: Annotated[User, Depends(require_permission("plans.manage"))],
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, str]:
    row = db.get(AIRecommendation, recommendation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ai_recommendation_not_found")
    run = db.get(SimulationRun, row.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="simulation_run_missing")
    record_audit(
        db,
        run.station_id,
        request,
        "AI_RECOMMENDATION_REJECTED",
        user=user,
        entity_type="ai_recommendation",
        entity_id=row.id,
        state_version=run.state_version,
        new_value={"rejected": True},
    )
    append_event(
        db,
        run,
        "ai.recommendation.rejected",
        {"recommendationId": row.id},
        entity_type="ai_recommendation",
        entity_id=row.id,
    )
    db.commit()
    return {"status": "REJECTED"}
