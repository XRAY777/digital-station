from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from simulation.clock import (
    ClockCommand,
    ClockMode,
    ClockRate,
    ClockState,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Block,
    Occupancy,
    Operation,
    Schedule,
    Signal,
    SimulationRun,
    StateSnapshot,
    Station,
    Switch,
    Train,
)


def clock_state(run: SimulationRun) -> ClockState:
    return ClockState(
        mode=cast(ClockMode, run.status),
        rate=cast(ClockRate, run.clock_rate),
        simulation_time_ms=run.simulation_time_ms,
        anchor_at=run.clock_anchor_at,
    )


def clock_payload(run: SimulationRun, now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now(UTC)
    state = clock_state(run)
    return {
        "runId": run.id,
        "status": state.mode,
        "rate": state.rate,
        "simulationTimeMs": state.time_at(now),
        "serverTime": now.isoformat(),
    }


def apply_clock_command(
    db: Session,
    station: Station,
    command: ClockCommand,
    rate: ClockRate | None = None,
) -> SimulationRun:
    run = db.scalar(
        select(SimulationRun)
        .where(SimulationRun.station_id == station.id)
        .order_by(SimulationRun.started_at.desc(), SimulationRun.id.desc())
        .limit(1)
        .with_for_update()
    )
    if run is None:
        raise LookupError("simulation_run_missing")
    now = datetime.now(UTC)
    state = clock_state(run)
    next_state = state.apply(command, now, rate)
    if command == "RESET":
        template = _seed_snapshot(db, station.id)
        run.simulation_time_ms = state.time_at(now)
        run.status = "STOPPED"
        run.clock_anchor_at = None
        reset_run = SimulationRun(
            station_id=station.id,
            status="STOPPED",
            started_at=now,
            simulation_time_ms=0,
            clock_rate=1,
            config=dict(template[1].config) if template is not None else dict(run.config),
        )
        db.add(reset_run)
        db.flush()
        if template is not None:
            snapshot, seed_run = template
            _restore_snapshot(db, snapshot.state)
            _copy_occupancies(db, reset_run.id, snapshot.state)
            _copy_schedules(db, seed_run.id, reset_run.id)
            _copy_operations(db, seed_run.id, reset_run.id)
            db.add(
                StateSnapshot(
                    run_id=reset_run.id,
                    simulation_time_ms=0,
                    state_version=1,
                    state=snapshot.state,
                )
            )
        else:
            _copy_current_run(db, run, reset_run)
        db.flush()
        return reset_run
    run.simulation_time_ms = next_state.simulation_time_ms
    run.clock_rate = next_state.rate
    run.status = next_state.mode
    run.clock_anchor_at = next_state.anchor_at
    db.flush()
    return run


def _seed_snapshot(db: Session, station_id: str) -> tuple[StateSnapshot, SimulationRun] | None:
    row = db.execute(
        select(StateSnapshot, SimulationRun)
        .join(SimulationRun, SimulationRun.id == StateSnapshot.run_id)
        .where(SimulationRun.station_id == station_id, StateSnapshot.simulation_time_ms == 0)
        .order_by(SimulationRun.started_at, StateSnapshot.id)
        .limit(1)
    ).first()
    return (row[0], row[1]) if row is not None else None


def _restore_snapshot(db: Session, state: dict[str, object]) -> None:
    train_states = state.get("trains", {})
    if isinstance(train_states, dict):
        for train_id, values in train_states.items():
            train = db.get(Train, train_id)
            if train is not None and isinstance(values, dict):
                train.status = str(values.get("status", train.status))
                train.direction = str(values.get("direction", train.direction))
                train.head_edge_id = values.get("headEdgeId")
                train.head_offset_m = float(values.get("headOffsetM", 0))
                train.speed_mps = float(values.get("speedMps", 0))
                train.target_speed_mps = float(values.get("targetSpeedMps", 0))
    switch_states = state.get("switches", {})
    if isinstance(switch_states, dict):
        for switch_id, position in switch_states.items():
            switch = db.get(Switch, switch_id)
            if switch is not None:
                switch.state = str(position)
                switch.movement_state = "SETTLED"
                switch.is_failed = False
                switch.failure_reason = None
    signal_states = state.get("signals", {})
    if isinstance(signal_states, dict):
        for signal_id, aspect in signal_states.items():
            signal = db.get(Signal, signal_id)
            if signal is not None:
                signal.aspect = str(aspect)
                signal.reason_codes = []
    block_states = state.get("blocks", {})
    if isinstance(block_states, dict):
        for block_id, values in block_states.items():
            block = db.get(Block, block_id)
            if block is not None and isinstance(values, dict):
                block.availability = str(values.get("availability", "OPEN"))
                block.closure_reason = values.get("closureReason")
                block.version += 1


def _copy_occupancies(db: Session, run_id: str, state: dict[str, object]) -> None:
    rows = state.get("occupancies", [])
    if not isinstance(rows, list):
        return
    for row in rows:
        if isinstance(row, dict) and row.get("trainId") and row.get("blockId"):
            db.add(
                Occupancy(
                    run_id=run_id,
                    train_id=str(row["trainId"]),
                    block_id=str(row["blockId"]),
                    entered_at_ms=int(row.get("enteredAtMs", 0)),
                )
            )


def _copy_schedules(db: Session, source_run_id: str, target_run_id: str) -> None:
    for schedule in db.scalars(select(Schedule).where(Schedule.run_id == source_run_id)).all():
        db.add(
            Schedule(
                run_id=target_run_id,
                train_id=schedule.train_id,
                scheduled_at=schedule.scheduled_at,
                event_type=schedule.event_type,
                platform_id=schedule.platform_id,
                delay_seconds=schedule.delay_seconds,
            )
        )


def _copy_operations(db: Session, source_run_id: str, target_run_id: str) -> None:
    for operation in db.scalars(select(Operation).where(Operation.run_id == source_run_id)).all():
        db.add(
            Operation(
                run_id=target_run_id,
                train_id=operation.train_id,
                kind=operation.kind,
                status="PLANNED",
                starts_at_ms=operation.starts_at_ms,
                ends_at_ms=operation.ends_at_ms,
                duration_ms=operation.duration_ms,
                release_buffer_ms=operation.release_buffer_ms,
                track_id=operation.track_id,
                platform_id=operation.platform_id,
                locomotive_id=operation.locomotive_id,
                crew_id=operation.crew_id,
                priority=operation.priority,
                payload=dict(operation.payload),
            )
        )


def _copy_current_run(db: Session, source: SimulationRun, target: SimulationRun) -> None:
    current = db.scalars(
        select(Occupancy).where(
            Occupancy.run_id == source.id,
            Occupancy.released_at_ms.is_(None),
        )
    ).all()
    for occupancy in current:
        db.add(
            Occupancy(
                run_id=target.id,
                train_id=occupancy.train_id,
                block_id=occupancy.block_id,
                entered_at_ms=occupancy.entered_at_ms,
            )
        )
    _copy_schedules(db, source.id, target.id)
    _copy_operations(db, source.id, target.id)
