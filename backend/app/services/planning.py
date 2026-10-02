from __future__ import annotations

from datetime import UTC, datetime, timedelta

from metrics.efficiency import EfficiencyConfig, calculate
from optimization.engine import OperationInput, Snapshot, TrackInput, optimize
from schedules.resources import ReservationRequest, overlaps
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from backend.app.models import (
    Block,
    Conflict,
    Crew,
    Edge,
    EfficiencyHistory,
    Incident,
    Locomotive,
    MaintenanceWindow,
    Occupancy,
    Operation,
    OptimizationPlan,
    OptimizationVariant,
    Platform,
    PlatformTrack,
    ResourceReservation,
    Schedule,
    ScheduleEvent,
    SimulationRun,
    Station,
    SwitchTransition,
    Track,
    Yard,
)


class PlanningError(ValueError):
    pass


def reserve_operation(
    session: Session,
    request: ReservationRequest,
    *,
    train_id: str | None = None,
    owner_operation_id: str | None = None,
) -> ResourceReservation:
    if request.starts_at_ms < 0 or request.duration_ms <= 0 or request.release_buffer_ms < 0:
        raise PlanningError("reservation_interval_invalid")
    # A real row update serializes reservation checks on SQLite as well as PostgreSQL.
    result = session.execute(
        update(SimulationRun)
        .where(SimulationRun.id == request.run_id)
        .values(state_version=SimulationRun.state_version)
    )
    locked = result.rowcount  # type: ignore[attr-defined]
    if not locked:
        raise PlanningError("simulation_run_not_found")
    run = session.get(SimulationRun, request.run_id)
    assert run is not None
    resource_type = request.resource_type.upper()
    model_by_type = {
        "PLATFORM": Platform,
        "LOCOMOTIVE": Locomotive,
        "CREW": Crew,
        "TRACK": Track,
    }
    resource_model = model_by_type.get(resource_type)
    resource = session.get(resource_model, request.resource_id) if resource_model else None
    if resource is None:
        raise PlanningError("resource_not_found")
    if isinstance(resource, (Locomotive, Crew)) and resource.station_id != run.station_id:
        raise PlanningError("resource_station_mismatch")
    if isinstance(resource, (Platform, Track)):
        yard_station = session.scalar(select(Yard.station_id).where(Yard.id == resource.yard_id))
        if yard_station != run.station_id:
            raise PlanningError("resource_station_mismatch")
    if isinstance(resource, (Locomotive, Crew)) and resource.status != "AVAILABLE":
        raise PlanningError("resource_unavailable")
    if isinstance(resource, Track):
        occupied_by = session.scalar(
            select(Occupancy.train_id)
            .join(Block, Block.id == Occupancy.block_id)
            .where(
                Occupancy.run_id == run.id,
                Occupancy.released_at_ms.is_(None),
                Block.track_id == resource.id,
            )
        )
        if occupied_by and occupied_by != train_id:
            raise PlanningError("track_occupied")
        closed = session.scalar(
            select(Block.id).where(Block.track_id == resource.id, Block.availability != "OPEN")
        )
        if closed:
            raise PlanningError("track_unavailable")
        reserved_block_owner = session.scalar(
            select(ResourceReservation.owner_train_id)
            .join(Block, Block.id == ResourceReservation.resource_id)
            .where(
                ResourceReservation.run_id == run.id,
                ResourceReservation.resource_type == "BLOCK",
                ResourceReservation.status.in_(("ACTIVE", "HELD")),
                Block.track_id == resource.id,
            )
        )
        if reserved_block_owner != train_id:
            reserved_block = session.scalar(
                select(ResourceReservation.id)
                .join(Block, Block.id == ResourceReservation.resource_id)
                .where(
                    ResourceReservation.run_id == run.id,
                    ResourceReservation.resource_type == "BLOCK",
                    ResourceReservation.status.in_(("ACTIVE", "HELD")),
                    Block.track_id == resource.id,
                )
            )
            if reserved_block:
                raise PlanningError("track_reserved")
    anchor = run.started_at.replace(tzinfo=UTC) if run.started_at.tzinfo is None else run.started_at
    maintenance_start = anchor + timedelta(milliseconds=request.starts_at_ms)
    maintenance_end = anchor + timedelta(milliseconds=request.ends_at_ms)
    maintenance = session.scalar(
        select(MaintenanceWindow.id).where(
            MaintenanceWindow.resource_type == resource_type,
            MaintenanceWindow.resource_id == request.resource_id,
            MaintenanceWindow.starts_at < maintenance_end,
            MaintenanceWindow.ends_at > maintenance_start,
        )
    )
    if maintenance:
        raise PlanningError("resource_in_maintenance")
    reservation_query = select(ResourceReservation).where(
        ResourceReservation.run_id == request.run_id,
        ResourceReservation.resource_type == resource_type,
        ResourceReservation.resource_id == request.resource_id,
        ResourceReservation.status.in_(("PLANNED", "ACTIVE", "HELD")),
    )
    if owner_operation_id:
        reservation_query = reservation_query.where(
            or_(
                ResourceReservation.owner_operation_id.is_(None),
                ResourceReservation.owner_operation_id != owner_operation_id,
            )
        )
    rows = session.scalars(reservation_query.with_for_update()).all()
    end = request.ends_at_ms
    if any(overlaps(request.starts_at_ms, end, row.starts_at_ms, row.ends_at_ms) for row in rows):
        raise PlanningError("resource_overlap")
    if owner_operation_id:
        own_reservation = session.scalar(
            select(ResourceReservation)
            .where(
                ResourceReservation.run_id == request.run_id,
                ResourceReservation.resource_type == resource_type,
                ResourceReservation.resource_id == request.resource_id,
                ResourceReservation.owner_operation_id == owner_operation_id,
                ResourceReservation.status == "PLANNED",
            )
            .with_for_update()
        )
        if own_reservation:
            own_reservation.starts_at_ms = request.starts_at_ms
            own_reservation.ends_at_ms = end
            session.flush()
            return own_reservation
    reservation = ResourceReservation(
        run_id=request.run_id,
        resource_type=resource_type,
        resource_id=request.resource_id,
        starts_at_ms=request.starts_at_ms,
        ends_at_ms=end,
        owner_train_id=train_id,
        owner_operation_id=owner_operation_id,
        status="PLANNED",
    )
    session.add(reservation)
    session.flush()
    return reservation


def optimize_run(
    session: Session, run: SimulationRun, *, time_limit_ms: int = 500
) -> tuple[OptimizationPlan, list[OptimizationVariant]]:
    operations = session.scalars(
        select(Operation)
        .where(Operation.run_id == run.id, Operation.status.in_(("PLANNED", "ACTIVE")))
        .order_by(Operation.starts_at_ms, Operation.id)
    ).all()
    tracks = session.scalars(select(Track).order_by(Track.id)).all()
    closed_track_ids = set(
        session.scalars(select(Block.track_id).where(Block.availability != "OPEN"))
    )
    closed_track_ids.update(
        session.scalars(
            select(Block.track_id)
            .join(Occupancy, Occupancy.block_id == Block.id)
            .where(Occupancy.run_id == run.id, Occupancy.released_at_ms.is_(None))
        )
    )
    for incident in session.scalars(
        select(Incident).where(
            Incident.run_id == run.id, Incident.status.in_(("OPEN", "ACKNOWLEDGED", "RESOLVING"))
        )
    ):
        closed_track_ids.update(str(value) for value in incident.payload.get("trackIds", []))
        incident_blocks = incident.payload.get("blockIds", [])
        if incident_blocks:
            closed_track_ids.update(
                session.scalars(
                    select(Block.track_id).where(
                        Block.id.in_([str(value) for value in incident_blocks])
                    )
                ).all()
            )
        closed_track_ids.update(str(value) for value in incident.payload.get("platformIds", []))
        switch_ids = incident.payload.get("switchIds", [])
        if switch_ids:
            failed_track_ids = session.scalars(
                select(Edge.track_id)
                .join(SwitchTransition, SwitchTransition.from_edge_id == Edge.id)
                .where(SwitchTransition.switch_id.in_([str(value) for value in switch_ids]))
            ).all()
            closed_track_ids.update(str(value) for value in failed_track_ids if value)
    locked_blocks = session.scalars(
        select(ResourceReservation.resource_id).where(
            ResourceReservation.run_id == run.id,
            ResourceReservation.resource_type == "BLOCK",
            ResourceReservation.status.in_(("ACTIVE", "HELD")),
        )
    ).all()
    if locked_blocks:
        closed_track_ids.update(
            session.scalars(select(Block.track_id).where(Block.id.in_(locked_blocks)))
        )
    platform_map: dict[str, set[str]] = {}
    for platform_id, track_id in session.execute(
        select(PlatformTrack.platform_id, PlatformTrack.track_id)
    ):
        platform_map.setdefault(track_id, set()).add(platform_id)
    unavailable: set[str] = set()
    unavailable.update(
        session.scalars(select(Locomotive.id).where(Locomotive.status != "AVAILABLE"))
    )
    unavailable.update(session.scalars(select(Crew.id).where(Crew.status != "AVAILABLE")))
    blocked_intervals: list[tuple[str, int, int, str | None]] = []
    anchor = run.started_at.replace(tzinfo=UTC) if run.started_at.tzinfo is None else run.started_at
    windows = session.scalars(select(MaintenanceWindow)).all()
    for window in windows:
        start = (
            window.starts_at.replace(tzinfo=UTC)
            if window.starts_at.tzinfo is None
            else window.starts_at
        )
        end = (
            window.ends_at.replace(tzinfo=UTC) if window.ends_at.tzinfo is None else window.ends_at
        )
        blocked_intervals.append(
            (
                window.resource_id,
                round((start - anchor).total_seconds() * 1000),
                round((end - anchor).total_seconds() * 1000),
                None,
            )
        )
    reservations = session.scalars(
        select(ResourceReservation).where(
            ResourceReservation.run_id == run.id,
            ResourceReservation.status.in_(("PLANNED", "ACTIVE", "HELD")),
        )
    ).all()
    for reservation in reservations:
        blocked_intervals.append(
            (
                reservation.resource_id,
                reservation.starts_at_ms,
                reservation.ends_at_ms,
                reservation.owner_operation_id,
            )
        )
    snapshot = Snapshot(
        state_version=run.state_version,
        operations=tuple(
            OperationInput(
                id=o.id,
                train_id=o.train_id,
                track_id=o.track_id,
                platform_id=o.platform_id,
                starts_at_ms=o.starts_at_ms,
                duration_ms=max(o.duration_ms, (o.ends_at_ms or o.starts_at_ms) - o.starts_at_ms),
                delay_ms=int(o.payload.get("delayMs", 0)),
                priority=o.priority,
                locomotive_id=o.locomotive_id,
                crew_id=o.crew_id,
            )
            for o in operations
        ),
        tracks=tuple(
            TrackInput(t.id, t.id not in closed_track_ids, frozenset(platform_map.get(t.id, set())))
            for t in tracks
        ),
        unavailable_resources=frozenset(unavailable),
        closed_tracks=frozenset(closed_track_ids),
        blocked_intervals=tuple(blocked_intervals),
    )
    result = optimize(snapshot, time_limit_ms=time_limit_ms)
    plan = OptimizationPlan(
        run_id=run.id,
        base_version=result.source_state_version,
        status=result.status,
        calculation_time_ms=result.calculation_time_ms,
        objective={"solverStatus": result.status, "generatedAt": datetime.now(UTC).isoformat()},
    )
    session.add(plan)
    session.flush()
    variants = [
        OptimizationVariant(
            plan_id=plan.id,
            ordinal=i,
            score=max(0, 100 - v.total_delay_ms / 1000),
            operations=list(v.operations),
            explanation={
                "totalDelayMs": v.total_delay_ms,
                "affectedOperations": list(v.affected_operations),
                "reasons": list(v.reasons),
            },
        )
        for i, v in enumerate(result.variants, 1)
    ]
    session.add_all(variants)
    session.flush()
    return plan, variants


def efficiency_for_run(session: Session, station: Station, run: SimulationRun) -> dict[str, object]:
    config = station.config.get("efficiency", {})
    weights = config.get("weights")
    efficiency_config = EfficiencyConfig(
        weights=tuple(sorted(weights.items()))
        if isinstance(weights, dict)
        else EfficiencyConfig().weights,
        delay_target_seconds=float(config.get("delayTargetSeconds", 300)),
        utilization_target=float(config.get("utilizationTarget", 0.75)),
        idle_target=float(config.get("idleTarget", 0.20)),
    )
    total_tracks = session.scalar(select(func.count()).select_from(Track)) or 0
    occupied_track_ids = set(
        session.scalars(
            select(Block.track_id)
            .join(Occupancy, Occupancy.block_id == Block.id)
            .where(Occupancy.run_id == run.id, Occupancy.released_at_ms.is_(None))
        )
    )
    reserved_track_count = (
        session.scalar(
            select(func.count(func.distinct(ResourceReservation.resource_id))).where(
                ResourceReservation.run_id == run.id,
                ResourceReservation.resource_type == "TRACK",
                ResourceReservation.status.in_(("ACTIVE", "HELD")),
            )
        )
        or 0
    )
    active_conflicts = (
        session.scalar(
            select(func.count())
            .select_from(Conflict)
            .where(Conflict.run_id == run.id, Conflict.status == "OPEN")
        )
        or 0
    )
    resource_total = (session.scalar(select(func.count()).select_from(Locomotive)) or 0) + (
        session.scalar(select(func.count()).select_from(Crew)) or 0
    )
    resource_available = (
        session.scalar(
            select(func.count()).select_from(Locomotive).where(Locomotive.status == "AVAILABLE")
        )
        or 0
    ) + (
        session.scalar(select(func.count()).select_from(Crew).where(Crew.status == "AVAILABLE"))
        or 0
    )
    delays = session.scalars(select(Schedule.delay_seconds).where(Schedule.run_id == run.id)).all()
    result = calculate(
        throughput_per_hour=float(run.config.get("throughputPerHour", 0)),
        average_delay_seconds=float(
            run.config.get("averageDelaySeconds", sum(delays) / len(delays) if delays else 0)
        ),
        track_utilization=float(
            run.config.get(
                "trackUtilization",
                min(1.0, (len(occupied_track_ids) + reserved_track_count) / max(1, total_tracks)),
            )
        ),
        active_conflicts=int(run.config.get("activeConflicts", active_conflicts)),
        resource_idle=float(
            run.config.get("resourceIdle", resource_available / max(1, resource_total))
        ),
        config=efficiency_config,
    )
    session.add(
        EfficiencyHistory(
            run_id=run.id,
            recorded_at_ms=run.simulation_time_ms,
            score=result["score"],
            components=result,
        )
    )
    return result


def record_actual_event(
    session: Session, schedule: Schedule, actual_at: datetime, status: str = "COMPLETED"
) -> ScheduleEvent:
    if actual_at.tzinfo is None:
        actual_at = actual_at.replace(tzinfo=UTC)
    scheduled = schedule.scheduled_at
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    prior_delay = schedule.delay_seconds
    schedule.delay_seconds = max(0, round((actual_at - scheduled).total_seconds()))
    delay_delta_ms = (schedule.delay_seconds - prior_delay) * 1000
    if delay_delta_ms and schedule.run_id:
        following = session.scalars(
            select(Schedule).where(
                Schedule.run_id == schedule.run_id,
                Schedule.train_id == schedule.train_id,
            )
        ).all()
        operations = session.scalars(
            select(Operation).where(
                Operation.run_id == schedule.run_id,
                Operation.train_id == schedule.train_id,
            )
        ).all()
        for later in following:
            later_at = later.scheduled_at
            if later_at.tzinfo is None:
                later_at = later_at.replace(tzinfo=UTC)
            if later.id == schedule.id or later_at <= scheduled:
                continue
            later.delay_seconds = max(0, later.delay_seconds + delay_delta_ms // 1000)
            for operation in operations:
                if operation.payload.get("scheduleId") == later.id:
                    payload = dict(operation.payload)
                    payload["delayMs"] = max(0, int(payload.get("delayMs", 0)) + delay_delta_ms)
                    operation.payload = payload
    event = ScheduleEvent(
        schedule_id=schedule.id,
        actual_at=actual_at,
        status=status,
        details={"delaySeconds": schedule.delay_seconds},
    )
    session.add(event)
    return event
