from __future__ import annotations

import os

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import (
    Block,
    Conflict,
    EfficiencyHistory,
    Incident,
    Occupancy,
    Route,
    RouteBlock,
    RouteEdge,
    RouteSignal,
    RouteSwitch,
    Schedule,
    Signal,
    SimulationEvent,
    SimulationRun,
    StateSnapshot,
    Switch,
    Train,
)


def history_retention_hours() -> int:
    try:
        configured = int(os.getenv("STATION_HISTORY_HOURS", "48"))
    except ValueError:
        configured = 48
    return min(72, max(24, configured))


def capture_state(session: Session, run: SimulationRun) -> dict[str, object]:
    trains = session.scalars(select(Train).where(Train.station_id == run.station_id)).all()
    occupancies = session.scalars(
        select(Occupancy).where(Occupancy.run_id == run.id, Occupancy.released_at_ms.is_(None))
    ).all()
    switches = session.scalars(select(Switch)).all()
    signals = session.scalars(select(Signal)).all()
    blocks = session.scalars(select(Block)).all()
    routes = session.scalars(select(Route).where(Route.run_id == run.id)).all()
    conflicts = session.scalars(select(Conflict).where(Conflict.run_id == run.id)).all()
    incidents = session.scalars(select(Incident).where(Incident.run_id == run.id)).all()
    efficiency = session.scalar(
        select(EfficiencyHistory)
        .where(EfficiencyHistory.run_id == run.id)
        .order_by(EfficiencyHistory.recorded_at_ms.desc())
        .limit(1)
    )
    schedules = session.scalars(select(Schedule).where(Schedule.run_id == run.id)).all()
    return {
        "trains": {
            row.id: {
                "status": row.status,
                "direction": row.direction,
                "headEdgeId": row.head_edge_id,
                "headOffsetM": row.head_offset_m,
                "speedMps": row.speed_mps,
                "targetSpeedMps": row.target_speed_mps,
                "delaySeconds": max(
                    (s.delay_seconds for s in schedules if s.train_id == row.id), default=0
                ),
            }
            for row in trains
        },
        "occupancies": [
            {"trainId": row.train_id, "blockId": row.block_id, "enteredAtMs": row.entered_at_ms}
            for row in occupancies
        ],
        "switches": {
            row.id: {
                "state": row.state,
                "isFailed": row.is_failed,
                "failureReason": row.failure_reason,
            }
            for row in switches
        },
        "signals": {
            row.id: {"aspect": row.aspect, "reasonCodes": row.reason_codes} for row in signals
        },
        "blocks": {
            row.id: {
                "trackId": row.track_id,
                "availability": row.availability,
                "closureReason": row.closure_reason,
            }
            for row in blocks
        },
        "routes": {
            row.id: {
                "trainId": row.train_id,
                "status": row.status,
                "edgeIds": session.scalars(
                    select(RouteEdge.edge_id)
                    .where(RouteEdge.route_id == row.id)
                    .order_by(RouteEdge.ordinal)
                ).all(),
                "blockIds": session.scalars(
                    select(RouteBlock.block_id)
                    .where(RouteBlock.route_id == row.id)
                    .order_by(RouteBlock.ordinal)
                ).all(),
                "switchIds": session.scalars(
                    select(RouteSwitch.switch_id).where(RouteSwitch.route_id == row.id)
                ).all(),
                "signalIds": session.scalars(
                    select(RouteSignal.signal_id).where(RouteSignal.route_id == row.id)
                ).all(),
            }
            for row in routes
        },
        "conflicts": {
            row.id: {
                "kind": row.kind,
                "severity": row.severity,
                "status": row.status,
                "details": row.details,
                "detectedAtMs": row.detected_at_ms,
            }
            for row in conflicts
        },
        "incidents": {
            row.id: {
                "kind": row.kind,
                "severity": row.severity,
                "status": row.status,
                "source": row.source,
                "payload": row.payload,
                "openedAt": row.opened_at.isoformat(),
                "resolvedAt": row.resolved_at.isoformat() if row.resolved_at else None,
            }
            for row in incidents
        },
        "efficiency": {"score": efficiency.score, **efficiency.components} if efficiency else None,
    }


def maybe_capture_state(session: Session, run: SimulationRun) -> None:
    latest = session.scalar(
        select(StateSnapshot)
        .where(StateSnapshot.run_id == run.id)
        .order_by(StateSnapshot.simulation_time_ms.desc())
        .limit(1)
    )
    if latest is not None and run.simulation_time_ms - latest.simulation_time_ms < 60_000:
        return
    session.add(
        StateSnapshot(
            run_id=run.id,
            simulation_time_ms=run.simulation_time_ms,
            state_version=run.state_version,
            state=capture_state(session, run),
        )
    )
    cutoff = run.simulation_time_ms - history_retention_hours() * 3_600_000
    session.execute(
        delete(StateSnapshot).where(
            StateSnapshot.run_id == run.id, StateSnapshot.simulation_time_ms < cutoff
        )
    )
    session.execute(
        delete(SimulationEvent).where(
            SimulationEvent.run_id == run.id, SimulationEvent.simulation_time_ms < cutoff
        )
    )
    session.flush()
