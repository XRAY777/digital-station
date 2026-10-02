from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.app.api.planning import accept_variant
from backend.app.models import (
    Base,
    MaintenanceWindow,
    Operation,
    OptimizationPlan,
    OptimizationVariant,
    Platform,
    ResourceReservation,
    Schedule,
    Train,
)
from backend.app.services.planning import PlanningError, record_actual_event, reserve_operation
from backend.app.services.station_state import demo_station, latest_run
from database.seed.demo import seed_demo
from fastapi import HTTPException
from schedules.resources import ReservationRequest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request


def make_session() -> tuple[Session, object]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    seed_demo(session)
    session.commit()
    return session, engine


def test_overlapping_resource_reservation_is_rejected_without_partial_row() -> None:
    session, engine = make_session()
    try:
        station = demo_station(session)
        assert station is not None
        run = latest_run(session, station.id)
        platform = session.scalar(select(Platform))
        assert run and platform
        reserve_operation(session, ReservationRequest(run.id, "PLATFORM", platform.id, 1000, 5000))
        session.commit()
        with pytest.raises(PlanningError, match="resource_overlap"):
            reserve_operation(
                session, ReservationRequest(run.id, "PLATFORM", platform.id, 5999, 1000)
            )
        session.rollback()
        count = session.scalar(
            select(func.count())
            .select_from(ResourceReservation)
            .where(ResourceReservation.resource_id == platform.id)
        )
        assert count == 1
    finally:
        session.close()
        engine.dispose()


def test_maintenance_window_blocks_reservation_before_infrastructure_mutates() -> None:
    session, engine = make_session()
    try:
        station = demo_station(session)
        assert station is not None
        run = latest_run(session, station.id)
        platform = session.scalar(select(Platform))
        assert run and platform
        anchor = (
            run.started_at.replace(tzinfo=UTC) if run.started_at.tzinfo is None else run.started_at
        )
        session.add(
            MaintenanceWindow(
                yard_id=platform.yard_id,
                resource_type="PLATFORM",
                resource_id=platform.id,
                starts_at=anchor + timedelta(seconds=10),
                ends_at=anchor + timedelta(seconds=20),
                reason="planned work",
            )
        )
        session.flush()
        with pytest.raises(PlanningError, match="resource_in_maintenance"):
            reserve_operation(
                session, ReservationRequest(run.id, "PLATFORM", platform.id, 15_000, 1000)
            )
        session.rollback()
        assert session.scalar(select(func.count()).select_from(ResourceReservation)) == 0
    finally:
        session.close()
        engine.dispose()


def test_run_state_version_is_available_to_reject_stale_optimizer_source() -> None:
    session, engine = make_session()
    try:
        station = demo_station(session)
        assert station is not None
        run = latest_run(session, station.id)
        assert run is not None
        source = run.state_version
        run.state_version += 1
        session.flush()
        assert run.state_version != source
    finally:
        session.close()
        engine.dispose()


def test_stale_optimization_plan_is_rejected_before_any_resource_write() -> None:
    session, engine = make_session()
    try:
        station = demo_station(session)
        assert station is not None
        run = latest_run(session, station.id)
        assert run is not None
        plan = OptimizationPlan(
            run_id=run.id, base_version=run.state_version, status="FEASIBLE", objective={}
        )
        session.add(plan)
        session.flush()
        session.add(
            OptimizationVariant(
                plan_id=plan.id,
                ordinal=1,
                score=100,
                operations=[
                    {"operationId": "missing", "trackId": "unsafe", "startsAtMs": 0, "endsAtMs": 10}
                ],
                explanation={},
            )
        )
        session.flush()
        run.state_version += 1
        session.flush()
        with pytest.raises(HTTPException) as error:
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/",
                    "headers": [],
                    "query_string": b"",
                    "server": ("test", 80),
                    "client": ("127.0.0.1", 1000),
                    "scheme": "http",
                }
            )
            accept_variant(
                plan.id,
                session.scalar(select(OptimizationVariant.id)),
                None,
                None,
                request,
                session,
            )
        assert error.value.status_code == 409
        assert plan.status == "STALE"
        assert session.scalar(select(func.count()).select_from(ResourceReservation)) == 0
    finally:
        session.close()
        engine.dispose()


def test_actual_delay_propagates_to_later_train_operations() -> None:
    session, engine = make_session()
    try:
        station = demo_station(session)
        assert station is not None
        run = latest_run(session, station.id)
        assert run is not None
        train_id = session.scalar(select(Train.id))
        assert train_id is not None
        scheduled = datetime(2026, 10, 2, 10, tzinfo=UTC)
        first = Schedule(
            train_id=train_id,
            run_id=run.id,
            scheduled_at=scheduled,
            event_type="ARRIVAL",
            delay_seconds=0,
        )
        later = Schedule(
            train_id=train_id,
            run_id=run.id,
            scheduled_at=scheduled + timedelta(minutes=5),
            event_type="DEPARTURE",
            delay_seconds=0,
        )
        session.add_all((first, later))
        session.flush()
        operation = Operation(
            run_id=run.id,
            train_id=train_id,
            kind="DEPARTURE",
            starts_at_ms=300_000,
            duration_ms=10_000,
            priority=50,
            payload={"scheduleId": later.id, "delayMs": 0},
        )
        session.add(operation)
        session.flush()
        record_actual_event(session, first, scheduled + timedelta(minutes=3))
        session.flush()
        assert later.delay_seconds == 180
        assert operation.payload["delayMs"] == 180_000
    finally:
        session.close()
        engine.dispose()
