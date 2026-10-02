from __future__ import annotations

from typing import cast

from simulation.blocks.model import Availability, BlockState
from simulation.signals.model import Aspect, SignalState
from simulation.switches.model import SwitchState
from simulation.trains.model import Direction, TrainState
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Block,
    Edge,
    Occupancy,
    Signal,
    SimulationRun,
    Station,
    Switch,
    SwitchTransition,
    Track,
    Train,
)


def demo_station(db: Session) -> Station | None:
    return db.scalar(select(Station).where(Station.code == "DEMO-01"))


def latest_run(db: Session, station_id: str) -> SimulationRun | None:
    return db.scalar(
        select(SimulationRun)
        .where(SimulationRun.station_id == station_id)
        .order_by(SimulationRun.started_at.desc(), SimulationRun.id.desc())
        .limit(1)
    )


def occupied_blocks(db: Session, run_id: str | None) -> dict[str, str]:
    if run_id is None:
        return {}
    rows = db.scalars(
        select(Occupancy).where(
            Occupancy.run_id == run_id,
            Occupancy.released_at_ms.is_(None),
        )
    ).all()
    return {row.block_id: row.train_id for row in rows}


def block_states(db: Session, run_id: str | None) -> list[BlockState]:
    occupied = occupied_blocks(db, run_id)
    rows = db.scalars(
        select(Block).join(Track, Track.id == Block.track_id).order_by(Track.code, Block.ordinal)
    ).all()
    return [
        BlockState(
            id=block.id,
            code=block.code,
            track_id=block.track_id,
            length_m=block.length_m,
            availability=cast(Availability, block.availability),
            occupied_by_train_id=occupied.get(block.id),
            closure_reason=block.closure_reason,
            version=block.version,
        )
        for block in rows
    ]


def train_states(db: Session, run_id: str | None) -> list[TrainState]:
    occupied: dict[str, list[Block]] = {}
    if run_id is not None:
        rows = db.execute(
            select(Occupancy.train_id, Block)
            .join(Block, Block.id == Occupancy.block_id)
            .where(Occupancy.run_id == run_id, Occupancy.released_at_ms.is_(None))
            .order_by(Block.track_id, Block.ordinal)
        ).all()
        for train_id, block in rows:
            occupied.setdefault(train_id, []).append(block)
    trains = db.scalars(select(Train).order_by(Train.code)).all()
    train_edges = {edge.id: edge.track_id for edge in db.scalars(select(Edge)).all()}
    return [
        TrainState(
            id=train.id,
            code=train.code,
            category=train.category,
            length_m=train.length_m,
            max_speed_mps=train.max_speed_mps,
            speed_mps=train.speed_mps,
            target_speed_mps=train.target_speed_mps,
            direction=cast(Direction, train.direction),
            status=train.status,
            priority=train.priority,
            head_edge_id=train.head_edge_id,
            head_offset_m=train.head_offset_m,
            current_block_ids=tuple(block.id for block in occupied.get(train.id, [])),
            current_track_id=(
                train_edges.get(train.head_edge_id) if train.head_edge_id is not None else None
            )
            or (occupied[train.id][0].track_id if occupied.get(train.id) else None),
        )
        for train in trains
    ]


def switch_states(db: Session, yard_id: str) -> list[SwitchState]:
    switches = db.scalars(
        select(Switch).where(Switch.yard_id == yard_id).order_by(Switch.code)
    ).all()
    if not switches:
        return []
    position_rows = db.execute(
        select(SwitchTransition.switch_id, SwitchTransition.position)
        .where(SwitchTransition.switch_id.in_([switch.id for switch in switches]))
        .distinct()
    ).all()
    positions: dict[str, set[str]] = {}
    for switch_id, position in position_rows:
        positions.setdefault(switch_id, set()).add(position)
    return [
        SwitchState(
            id=switch.id,
            code=switch.code,
            node_id=switch.node_id,
            position=switch.state,
            allowed_positions=frozenset(positions.get(switch.id, set())),
            movement_state=switch.movement_state,
            is_failed=switch.is_failed,
        )
        for switch in switches
    ]


def signal_states(db: Session, yard_id: str) -> list[SignalState]:
    return [
        SignalState(
            signal.id,
            signal.code,
            signal.edge_id,
            cast(Aspect, signal.aspect),
            tuple(signal.reason_codes),
        )
        for signal in db.scalars(
            select(Signal).where(Signal.yard_id == yard_id).order_by(Signal.code)
        ).all()
    ]


def edge_lengths(db: Session, yard_id: str) -> dict[str, int]:
    return {
        edge.id: edge.length_m
        for edge in db.scalars(select(Edge).where(Edge.yard_id == yard_id)).all()
    }


__all__ = [
    "block_states",
    "demo_station",
    "edge_lengths",
    "latest_run",
    "signal_states",
    "switch_states",
    "train_states",
]
