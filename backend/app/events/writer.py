from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models import SimulationEvent, SimulationRun


def append_event(
    session: Session,
    run: SimulationRun,
    event_type: str,
    payload: dict[str, object],
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> SimulationEvent:
    sequence = (
        session.scalar(
            select(func.max(SimulationEvent.sequence)).where(SimulationEvent.run_id == run.id)
        )
        or 0
    ) + 1
    event = SimulationEvent(
        run_id=run.id,
        sequence=sequence,
        schema_version=1,
        state_version=run.state_version,
        simulation_time_ms=run.simulation_time_ms,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event
