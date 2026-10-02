from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from backend.app.models import (
    Base,
    Block,
    Conflict,
    Edge,
    Occupancy,
    ResourceReservation,
    Route,
    RouteBlock,
    Signal,
    SimulationRun,
    Switch,
    SwitchTransition,
    Train,
    Yard,
)
from backend.app.services.conflicts import reconcile_conflicts, scan_resource_conflicts
from backend.app.services.route_engine import (
    RouteCommandError,
    cancel_route,
    command_switch_position,
    create_route,
    reserve_route,
)
from backend.app.services.runtime_tick import advance_simulation_tick
from backend.app.services.station_state import demo_station, latest_run
from backend.app.services.topology import demo_yard
from database.seed.demo import seed_demo
from simulation.conflicts.engine import ConflictFinding
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def make_session() -> tuple[Session, object]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    seed_demo(session)
    session.commit()
    return session, engine


def route_context(session: Session) -> tuple[SimulationRun, Yard, Train, Train]:
    station = demo_station(session)
    yard = demo_yard(session)
    assert station is not None and yard is not None
    run = latest_run(session, station.id)
    west_one = session.scalar(select(Train).where(Train.code == "П-3001"))
    west_two = session.scalar(select(Train).where(Train.code == "C-102"))
    assert run is not None and west_one is not None and west_two is not None
    return run, yard, west_one, west_two


def release_seed_occupancy(session: Session, run: SimulationRun) -> None:
    for row in session.scalars(
        select(Occupancy).where(Occupancy.run_id == run.id, Occupancy.released_at_ms.is_(None))
    ).all():
        row.released_at_ms = run.simulation_time_ms
    session.flush()


def test_route_reservation_is_atomic_and_excludes_competing_train() -> None:
    session, engine = make_session()
    try:
        run, yard, train_one, train_two = route_context(session)
        release_seed_occupancy(session, run)
        first = create_route(session, run, yard, train_one.id, "EAST-APPROACH")
        reserve_route(session, run.id, first.id, yard)
        session.commit()
        assert first.status == "RESERVED"
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResourceReservation)
                .where(
                    ResourceReservation.owner_route_id == first.id,
                    ResourceReservation.status == "HELD",
                )
            )
            > 1
        )

        second = create_route(session, run, yard, train_two.id, "EAST-APPROACH")
        session.commit()
        with pytest.raises(RouteCommandError, match="SAFETY_CONFLICT"):
            reserve_route(session, run.id, second.id, yard)
        session.commit()
        assert session.get(Route, second.id).status == "CONFLICTED"
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResourceReservation)
                .where(ResourceReservation.owner_route_id == second.id)
            )
            == 0
        )
    finally:
        session.close()
        engine.dispose()


def test_concurrent_route_reserve_allows_only_one_owner(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'race.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            seed_demo(db)
            run, yard, train_one, train_two = route_context(db)
            release_seed_occupancy(db, run)
            first = create_route(db, run, yard, train_one.id, "EAST-APPROACH")
            second = create_route(db, run, yard, train_two.id, "EAST-APPROACH")
            run_id, yard_id, route_ids = run.id, yard.id, (first.id, second.id)
            db.commit()
        barrier = Barrier(2)

        def reserve(route_id: str) -> str:
            with Session(engine, expire_on_commit=False) as db:
                yard_row = db.get(Yard, yard_id)
                assert yard_row is not None
                barrier.wait(timeout=5)
                try:
                    reserve_route(db, run_id, route_id, yard_row)
                    db.commit()
                    return "ACCEPTED"
                except (RouteCommandError, IntegrityError, OperationalError):
                    db.rollback()
                    return "REJECTED"

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(reserve, route_ids))
        assert results.count("ACCEPTED") == 1
        assert results.count("REJECTED") == 1
        with Session(engine) as db:
            owners = set(
                db.scalars(
                    select(ResourceReservation.owner_route_id).where(
                        ResourceReservation.run_id == run_id,
                        ResourceReservation.status == "HELD",
                    )
                )
            )
            assert owners == {route_ids[results.index("ACCEPTED")]}
    finally:
        engine.dispose()


def test_one_occupied_block_leaves_no_partial_route_reservations() -> None:
    session, engine = make_session()
    try:
        run, yard, train_one, train_two = route_context(session)
        release_seed_occupancy(session, run)
        route = create_route(session, run, yard, train_one.id, "EAST-APPROACH")
        block_ids = session.scalars(
            select(RouteBlock.block_id).where(RouteBlock.route_id == route.id)
        ).all()
        assert len(block_ids) > 1
        session.add(
            Occupancy(
                run_id=run.id,
                train_id=train_two.id,
                block_id=block_ids[-1],
                entered_at_ms=0,
            )
        )
        session.flush()
        session.commit()
        with pytest.raises(RouteCommandError, match="SAFETY_CONFLICT"):
            reserve_route(session, run.id, route.id, yard)
        session.commit()
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResourceReservation)
                .where(
                    ResourceReservation.owner_route_id == route.id,
                    ResourceReservation.status == "HELD",
                )
            )
            == 0
        )
        assert session.get(Route, route.id).status == "CONFLICTED"
    finally:
        session.close()
        engine.dispose()


def test_failed_reservation_flush_rolls_back_all_resources() -> None:
    session, engine = make_session()
    try:
        run, yard, train, _ = route_context(session)
        release_seed_occupancy(session, run)
        route = create_route(session, run, yard, train.id, "EAST-APPROACH")
        session.commit()

        def fail_after_first_resource(_mapper, _connection, _target) -> None:
            raise RuntimeError("simulated_partial_reservation_failure")

        event.listen(ResourceReservation, "after_insert", fail_after_first_resource)
        try:
            with pytest.raises(RuntimeError, match="simulated_partial"):
                reserve_route(session, run.id, route.id, yard)
        finally:
            event.remove(ResourceReservation, "after_insert", fail_after_first_resource)
            session.rollback()
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResourceReservation)
                .where(ResourceReservation.owner_route_id == route.id)
            )
            == 0
        )
        assert session.get(Route, route.id).status == "PROPOSED"
    finally:
        session.close()
        engine.dispose()


def test_switch_with_tail_occupancy_or_active_route_lock_cannot_move() -> None:
    session, engine = make_session()
    try:
        run, yard, train, _ = route_context(session)
        release_seed_occupancy(session, run)
        route = create_route(session, run, yard, train.id, "EAST-APPROACH")
        reserve_route(session, run.id, route.id, yard)
        session.commit()
        locked_switch_id = session.scalar(
            select(ResourceReservation.resource_id).where(
                ResourceReservation.owner_route_id == route.id,
                ResourceReservation.resource_type == "SWITCH",
            )
        )
        assert locked_switch_id is not None
        switch = session.get(Switch, locked_switch_id)
        assert switch is not None
        target = session.scalar(
            select(SwitchTransition.position).where(
                SwitchTransition.switch_id == switch.id,
                SwitchTransition.position != switch.state,
            )
        )
        assert target is not None
        with pytest.raises(RouteCommandError, match="SAFETY_CONFLICT"):
            command_switch_position(session, run.id, yard, switch.id, target)
        session.rollback()
        assert session.get(Switch, switch.id).state != target

        other_switch = session.scalar(select(Switch).where(Switch.id != switch.id))
        assert other_switch is not None
        target = session.scalar(
            select(SwitchTransition.position).where(
                SwitchTransition.switch_id == other_switch.id,
                SwitchTransition.position != other_switch.state,
            )
        )
        transition_rows = session.scalars(
            select(SwitchTransition).where(SwitchTransition.switch_id == other_switch.id)
        ).all()
        adjacent_nodes = set()
        for transition in transition_rows:
            incoming = session.get(Edge, transition.from_edge_id)
            outgoing = session.get(Edge, transition.to_edge_id)
            assert incoming is not None and outgoing is not None
            adjacent_nodes.update((incoming.from_node_id, outgoing.to_node_id))
        adjacent_block = session.scalar(
            select(Edge.block_id).where(
                Edge.yard_id == yard.id,
                (Edge.from_node_id.in_(adjacent_nodes)) | (Edge.to_node_id.in_(adjacent_nodes)),
                Edge.block_id.is_not(None),
            )
        )
        assert target is not None and adjacent_block is not None
        session.add(
            Occupancy(
                run_id=run.id,
                train_id=train.id,
                block_id=adjacent_block,
                entered_at_ms=0,
            )
        )
        session.commit()
        with pytest.raises(RouteCommandError, match="SAFETY_CONFLICT"):
            command_switch_position(session, run.id, yard, other_switch.id, target)
        session.rollback()
        assert session.get(Switch, other_switch.id).state != target
    finally:
        session.close()
        engine.dispose()


def test_server_tick_x10_stops_before_closed_block_signal() -> None:
    session, engine = make_session()
    try:
        run, yard, train, _ = route_context(session)
        release_seed_occupancy(session, run)
        route = create_route(session, run, yard, train.id, "EAST-APPROACH")
        reserve_route(session, run.id, route.id, yard)
        first_block_id = session.scalar(
            select(RouteBlock.block_id)
            .where(RouteBlock.route_id == route.id)
            .order_by(RouteBlock.ordinal)
        )
        assert first_block_id is not None
        block = session.get(Block, first_block_id)
        assert block is not None
        block.availability = "BLOCKED"
        train.speed_mps = 8
        train.target_speed_mps = 10
        run.status = "RUNNING"
        run.clock_rate = 10
        session.commit()

        advance_simulation_tick(session, run, yard, 10_000)
        session.commit()
        blocked_signal_edges = set(
            session.scalars(select(Signal.edge_id).where(Signal.aspect == "STOP")).all()
        )
        assert train.head_edge_id not in blocked_signal_edges
        assert (
            session.scalar(
                select(Occupancy.id).where(
                    Occupancy.run_id == run.id,
                    Occupancy.block_id == first_block_id,
                    Occupancy.released_at_ms.is_(None),
                )
            )
            is None
        )
        assert run.simulation_time_ms == 100_000
    finally:
        session.close()
        engine.dispose()


def test_conflict_deduplicates_repeats_and_resolves_only_when_absent() -> None:
    session, engine = make_session()
    try:
        run, _, _, _ = route_context(session)
        finding = ConflictFinding(
            "BLOCK_AUTHORITY_BREACH", "CRITICAL", {"blockId": "occupied-1", "trainIds": ["a", "b"]}
        )
        first = reconcile_conflicts(session, run.id, 1000, (finding,))[0]
        repeated = reconcile_conflicts(session, run.id, 2000, (finding,))[0]
        session.flush()
        assert first.id == repeated.id
        assert (
            session.scalar(
                select(func.count())
                .select_from(Conflict)
                .where(Conflict.run_id == run.id, Conflict.status == "OPEN")
            )
            == 1
        )
        reconcile_conflicts(session, run.id, 3000, ())
        session.flush()
        assert repeated.status == "RESOLVED"
        assert repeated.resolved_at_ms == 3000
        assert repeated.fingerprint is None
    finally:
        session.close()
        engine.dispose()


def test_route_conflict_persists_until_competing_reservation_is_released() -> None:
    session, engine = make_session()
    try:
        run, yard, train_one, train_two = route_context(session)
        release_seed_occupancy(session, run)
        first = create_route(session, run, yard, train_one.id, "EAST-APPROACH")
        reserve_route(session, run.id, first.id, yard)
        session.commit()
        second = create_route(session, run, yard, train_two.id, "EAST-APPROACH")
        session.commit()
        with pytest.raises(RouteCommandError, match="SAFETY_CONFLICT"):
            reserve_route(session, run.id, second.id, yard)
        session.commit()

        conflict = reconcile_conflicts(
            session, run.id, 1000, scan_resource_conflicts(session, run)
        )[0]
        session.commit()
        assert conflict.status == "OPEN"
        cancel_route(session, run.id, first.id)
        session.commit()
        reconcile_conflicts(session, run.id, 2000, scan_resource_conflicts(session, run))
        session.commit()
        assert conflict.status == "RESOLVED"
        assert conflict.resolved_at_ms == 2000
    finally:
        session.close()
        engine.dispose()
