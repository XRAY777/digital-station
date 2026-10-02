from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from schedules.resources import ReservationRequest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from backend.app.auth.dependencies import get_db_session, require_permission
from backend.app.auth.router import require_authenticated_csrf
from backend.app.events.writer import append_event
from backend.app.models import (
    AIRecommendation,
    Operation,
    OptimizationPlan,
    OptimizationVariant,
    Platform,
    Schedule,
    SimulationRun,
    Station,
    Train,
    User,
)
from backend.app.services.audit import record_audit
from backend.app.services.idempotency import cached_command, remember_command
from backend.app.services.planning import (
    PlanningError,
    efficiency_for_run,
    optimize_run,
    record_actual_event,
    reserve_operation,
)

router = APIRouter(prefix="/api", tags=["planning"])
ReadAccess = Annotated[User, Depends(require_permission("station.read"))]
DispatchAccess = Annotated[User, Depends(require_permission("routes.manage"))]
PlanAccess = Annotated[User, Depends(require_permission("plans.manage"))]
SettingsAccess = Annotated[User, Depends(require_permission("settings.manage"))]


class ReservationBody(BaseModel):
    runId: str
    resourceType: str = Field(min_length=1, max_length=24)
    resourceId: str = Field(min_length=1, max_length=36)
    startsAtMs: int = Field(ge=0)
    durationMs: int = Field(gt=0)
    releaseBufferMs: int = Field(default=0, ge=0)
    trainId: str | None = None
    operationId: str | None = None


class ActualEventBody(BaseModel):
    actualAt: datetime
    status: str = "COMPLETED"


class ScheduleBody(BaseModel):
    trainId: str
    runId: str
    scheduledAt: datetime
    eventType: str = Field(pattern="^(ARRIVAL|DEPARTURE)$")
    platformId: str | None = None
    priority: int = Field(default=50, ge=0, le=100)
    durationMs: int = Field(default=60_000, gt=0)


class EfficiencyConfigBody(BaseModel):
    weights: dict[str, float]
    delayTargetSeconds: float = Field(gt=0)
    utilizationTarget: float = Field(ge=0, le=1)
    idleTarget: float = Field(gt=0, le=1)


@router.put(
    "/stations/{station_id}/efficiency-config", dependencies=[Depends(require_authenticated_csrf)]
)
def update_efficiency_config(
    station_id: str,
    body: EfficiencyConfigBody,
    user: SettingsAccess,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, object]:
    required = {"throughput", "delay", "track_utilization", "conflicts", "resource_idle"}
    if set(body.weights) != required or any(value < 0 for value in body.weights.values()):
        raise HTTPException(status_code=422, detail="efficiency_weights_invalid")
    if sum(body.weights.values()) <= 0:
        raise HTTPException(status_code=422, detail="efficiency_weights_invalid")
    station = db.get(Station, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="station_not_found")
    config = dict(station.config)
    config["efficiency"] = {
        "weights": body.weights,
        "delayTargetSeconds": body.delayTargetSeconds,
        "utilizationTarget": body.utilizationTarget,
        "idleTarget": body.idleTarget,
    }
    station.config = config
    db.commit()
    return {"status": "UPDATED", "efficiency": config["efficiency"]}


@router.get("/stations/{station_id}/efficiency-config")
def get_efficiency_config(
    station_id: str,
    _: SettingsAccess,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, object]:
    station = db.get(Station, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="station_not_found")
    return {"efficiency": dict(station.config).get("efficiency", {})}


@router.post("/schedules", dependencies=[Depends(require_authenticated_csrf)])
def create_schedule(
    body: ScheduleBody, user: DispatchAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, object]:
    run = db.get(SimulationRun, body.runId)
    train = db.get(Train, body.trainId)
    platform = db.get(Platform, body.platformId) if body.platformId else None
    if run is None or train is None or train.station_id != run.station_id:
        raise HTTPException(status_code=404, detail="schedule_train_or_run_not_found")
    if body.platformId and platform is None:
        raise HTTPException(status_code=404, detail="platform_not_found")
    scheduled_at = body.scheduledAt
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    anchor = run.started_at.replace(tzinfo=UTC) if run.started_at.tzinfo is None else run.started_at
    row = Schedule(
        train_id=train.id,
        run_id=run.id,
        scheduled_at=scheduled_at,
        event_type=body.eventType,
        platform_id=body.platformId,
        priority=body.priority,
    )
    db.add(row)
    db.flush()
    relative_start = max(0, round((scheduled_at - anchor).total_seconds() * 1000))
    operation = Operation(
        run_id=run.id,
        train_id=train.id,
        kind=body.eventType,
        starts_at_ms=relative_start,
        duration_ms=body.durationMs,
        platform_id=body.platformId,
        priority=body.priority,
        payload={"scheduleId": row.id},
    )
    db.add(operation)
    db.flush()
    reservation = None
    if platform:
        try:
            reservation = reserve_operation(
                db,
                ReservationRequest(
                    run.id, "PLATFORM", platform.id, relative_start, body.durationMs
                ),
                train_id=train.id,
                owner_operation_id=operation.id,
            )
        except PlanningError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    run.state_version += 1
    append_event(
        db,
        run,
        "schedule.created",
        {
            "scheduleId": row.id,
            "trainId": train.id,
            "eventType": row.event_type,
            "scheduledAt": row.scheduled_at.isoformat(),
            "platformId": row.platform_id,
            "reservationId": reservation.id if reservation else None,
            "operationId": operation.id,
        },
        entity_type="schedule",
        entity_id=row.id,
    )
    db.commit()
    return {
        "scheduleId": row.id,
        "operationId": operation.id,
        "reservationId": reservation.id if reservation else None,
        "stateVersion": run.state_version,
    }


@router.post("/resources/reservations", dependencies=[Depends(require_authenticated_csrf)])
def reserve_resource(
    body: ReservationBody, user: DispatchAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, object]:
    try:
        run = db.get(SimulationRun, body.runId)
        if run is None:
            raise PlanningError("simulation_run_not_found")
        item = reserve_operation(
            db,
            ReservationRequest(
                body.runId,
                body.resourceType,
                body.resourceId,
                body.startsAtMs,
                body.durationMs,
                body.releaseBufferMs,
            ),
            train_id=body.trainId,
            owner_operation_id=body.operationId,
        )
        run.state_version += 1
        append_event(
            db,
            run,
            "resource.reserved",
            {
                "reservationId": item.id,
                "resourceType": item.resource_type,
                "resourceId": item.resource_id,
                "startsAtMs": item.starts_at_ms,
                "endsAtMs": item.ends_at_ms,
            },
            entity_type="resource_reservation",
            entity_id=item.id,
        )
        db.commit()
    except PlanningError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "reservationId": item.id,
        "status": item.status,
        "startsAtMs": item.starts_at_ms,
        "endsAtMs": item.ends_at_ms,
    }


@router.get("/schedules")
def list_schedules(
    user: ReadAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, object]:
    rows = db.scalars(select(Schedule).order_by(Schedule.scheduled_at, Schedule.id)).all()
    return {
        "items": [
            {
                "id": row.id,
                "trainId": row.train_id,
                "runId": row.run_id,
                "scheduledAt": row.scheduled_at.isoformat(),
                "eventType": row.event_type,
                "platformId": row.platform_id,
                "delaySeconds": row.delay_seconds,
                "priority": row.priority,
            }
            for row in rows
        ]
    }


@router.post("/schedules/{schedule_id}/actual", dependencies=[Depends(require_authenticated_csrf)])
def post_actual(
    schedule_id: str,
    body: ActualEventBody,
    user: DispatchAccess,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, object]:
    row = db.get(Schedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="schedule_not_found")
    event = record_actual_event(db, row, body.actualAt, body.status)
    if row.run_id:
        run = db.get(SimulationRun, row.run_id)
        if run:
            run.state_version += 1
            append_event(
                db,
                run,
                "train.delay.updated",
                {
                    "scheduleId": row.id,
                    "trainId": row.train_id,
                    "delaySeconds": row.delay_seconds,
                    "status": event.status,
                },
                entity_type="schedule",
                entity_id=row.id,
            )
    db.commit()
    return {"eventId": event.id, "delaySeconds": row.delay_seconds, "status": event.status}


@router.post("/runs/{run_id}/optimization", dependencies=[Depends(require_authenticated_csrf)])
def create_optimization(
    run_id: str, user: PlanAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, object]:
    run = db.get(SimulationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="simulation_run_not_found")
    plan, variants = optimize_run(db, run)
    db.commit()
    return {
        "planId": plan.id,
        "sourceStateVersion": plan.base_version,
        "generatedAt": plan.objective.get("generatedAt"),
        "solverStatus": plan.status,
        "calculationTimeMs": plan.calculation_time_ms,
        "variants": [
            {
                "id": item.id,
                "ordinal": item.ordinal,
                "score": item.score,
                "operations": item.operations,
                "explanation": item.explanation,
            }
            for item in variants
        ],
    }


@router.post("/optimization/{plan_id}/variants/{variant_id}/accept")
def accept_variant(
    plan_id: str,
    variant_id: str,
    _: Annotated[object, Depends(require_authenticated_csrf)],
    user: PlanAccess,
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> Any:
    plan = db.get(OptimizationPlan, plan_id)
    variant = db.get(OptimizationVariant, variant_id)
    if plan is None or variant is None or variant.plan_id != plan_id:
        raise HTTPException(status_code=404, detail="optimization_variant_not_found")
    run = db.get(SimulationRun, plan.run_id)
    if run is not None:
        command_payload = {"planId": plan_id, "variantId": variant_id}
        replay = cached_command(
            db, run.station_id, request, user.id if user else "", command_payload
        )
        if replay is not None:
            return replay
    if plan.status != "FEASIBLE" or run is None or run.state_version != plan.base_version:
        if run is not None:
            record_audit(
                db,
                run.station_id,
                request,
                "PLAN_ACCEPTED",
                user=user,
                entity_type="optimization_plan",
                entity_id=plan.id,
                state_version=run.state_version,
                result="DENIED",
                old_value={"baseVersion": plan.base_version},
                new_value={"reason": "optimization_plan_stale"},
            )
        plan.status = "STALE"
        db.commit()
        raise HTTPException(status_code=409, detail="optimization_plan_stale")
    # Rebuild a fresh server snapshot and require a currently feasible result before any write.
    fresh_plan, fresh_variants = optimize_run(db, run)
    requested_signature = tuple(
        sorted(
            (str(row.get("operationId")), str(row.get("trackId")), int(row.get("startsAtMs", -1)))
            for row in variant.operations
        )
    )
    fresh_signatures = {
        tuple(
            sorted(
                (
                    str(row.get("operationId")),
                    str(row.get("trackId")),
                    int(row.get("startsAtMs", -1)),
                )
                for row in candidate.operations
            )
        )
        for candidate in fresh_variants
    }
    requested_ops = {str(row["operationId"]): row for row in variant.operations}
    if not requested_ops or requested_signature not in fresh_signatures:
        db.rollback()
        raise HTTPException(status_code=409, detail="optimization_variant_no_longer_safe")
    old_values: dict[str, object] = {}
    try:
        for operation_id, row in requested_ops.items():
            operation = db.get(Operation, operation_id)
            if operation is None or operation.run_id != run.id:
                raise PlanningError("optimization_operation_changed")
            old_values[operation_id] = {
                "trackId": operation.track_id,
                "startsAtMs": operation.starts_at_ms,
            }
            start_ms = int(row["startsAtMs"])
            duration_ms = max(1, int(row["endsAtMs"]) - start_ms)
            for resource_type, resource_id in (
                ("TRACK", row.get("trackId")),
                ("PLATFORM", row.get("platformId")),
                ("LOCOMOTIVE", row.get("locomotiveId")),
                ("CREW", row.get("crewId")),
            ):
                if resource_id:
                    reserve_operation(
                        db,
                        ReservationRequest(
                            run.id,
                            resource_type,
                            str(resource_id),
                            start_ms,
                            duration_ms,
                            operation.release_buffer_ms,
                        ),
                        train_id=operation.train_id,
                        owner_operation_id=operation.id,
                    )
            operation.track_id = str(row["trackId"])
            operation.starts_at_ms = start_ms
    except (PlanningError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    run.state_version += 1
    plan.status = "ACCEPTED"
    fresh_plan.status = "REVALIDATION_ONLY"
    append_event(
        db,
        run,
        "plan.accepted",
        {"planId": plan.id, "variantId": variant.id, "operationIds": sorted(requested_ops)},
        entity_type="optimization_plan",
        entity_id=plan.id,
    )
    is_ai = (
        db.scalar(select(AIRecommendation.id).where(AIRecommendation.plan_id == plan.id).limit(1))
        is not None
    )
    record_audit(
        db,
        run.station_id,
        request,
        "AI_RECOMMENDATION_ACCEPTED" if is_ai else "PLAN_ACCEPTED",
        user=user,
        entity_type="optimization_plan",
        entity_id=plan.id,
        state_version=run.state_version,
        old_value=old_values,
        new_value={"variantId": variant.id, "operations": requested_ops},
    )
    result = {"status": "ACCEPTED", "stateVersion": run.state_version}
    remember_command(db, run.station_id, request, user.id, command_payload, result)
    db.commit()
    return result


@router.post("/optimization/{plan_id}/reject")
def reject_plan(
    plan_id: str,
    _: Annotated[object, Depends(require_authenticated_csrf)],
    user: PlanAccess,
    request: Request,
    db: Annotated[DbSession, Depends(get_db_session)],
) -> dict[str, object]:
    plan = db.get(OptimizationPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="optimization_plan_not_found")
    if plan.status != "FEASIBLE":
        raise HTTPException(status_code=409, detail="optimization_plan_not_feasible")
    run = db.get(SimulationRun, plan.run_id)
    if run is None or run.state_version != plan.base_version:
        raise HTTPException(status_code=409, detail="optimization_plan_stale")
    plan.status = "REJECTED"
    run.state_version += 1
    append_event(
        db,
        run,
        "plan.rejected",
        {"planId": plan.id},
        entity_type="optimization_plan",
        entity_id=plan.id,
    )
    record_audit(
        db,
        run.station_id,
        request,
        "PLAN_REJECTED",
        user=user,
        entity_type="optimization_plan",
        entity_id=plan.id,
        state_version=run.state_version,
        old_value={"status": "FEASIBLE"},
        new_value={"status": "REJECTED"},
    )
    db.commit()
    return {"status": plan.status, "stateVersion": run.state_version}


@router.get("/runs/{run_id}/efficiency")
def efficiency(
    run_id: str, user: ReadAccess, db: Annotated[DbSession, Depends(get_db_session)]
) -> dict[str, object]:
    run = db.get(SimulationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="simulation_run_not_found")
    station = db.get(Station, run.station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="station_not_found")
    result = efficiency_for_run(db, station, run)
    db.commit()
    return {"current": result, "predicted": None}
