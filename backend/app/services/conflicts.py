from __future__ import annotations

from simulation.conflicts.engine import ConflictFinding
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.events.writer import append_event
from backend.app.models import (
    Block,
    Conflict,
    Occupancy,
    ResourceReservation,
    Route,
    SimulationRun,
    Switch,
)


def reconcile_conflicts(
    session: Session,
    run_id: str,
    simulation_time_ms: int,
    findings: tuple[ConflictFinding, ...],
) -> tuple[Conflict, ...]:
    """Upsert stable open findings and resolve only findings absent from this scan."""
    run = session.get(SimulationRun, run_id)
    if run is None:
        raise LookupError("simulation_run_not_found")
    active = {finding.fingerprint: finding for finding in findings}
    rows = session.scalars(select(Conflict).where(Conflict.run_id == run_id)).all()
    existing = {row.fingerprint: row for row in rows if row.fingerprint is not None}
    result: list[Conflict] = []
    for fingerprint, finding in active.items():
        row = existing.pop(fingerprint, None)
        if row is None:
            row = Conflict(
                run_id=run_id,
                kind=finding.kind,
                severity=finding.severity,
                status="OPEN",
                fingerprint=fingerprint,
                details={**finding.details, "fingerprint": fingerprint},
                detected_at_ms=simulation_time_ms,
            )
            session.add(row)
            append_event(
                session,
                run,
                "conflict.opened",
                {
                    "conflictId": row.id,
                    "kind": row.kind,
                    "severity": row.severity,
                    "status": row.status,
                    "details": row.details,
                },
                entity_type="conflict",
                entity_id=row.id,
            )
        else:
            changed = (
                row.status != "OPEN"
                or row.severity != finding.severity
                or row.details != {**finding.details, "fingerprint": fingerprint}
            )
            row.severity = finding.severity
            row.status = "OPEN"
            row.fingerprint = fingerprint
            row.details = {**finding.details, "fingerprint": fingerprint}
            row.detected_at_ms = simulation_time_ms
            row.resolved_at_ms = None
            if changed:
                append_event(
                    session,
                    run,
                    "conflict.updated",
                    {
                        "conflictId": row.id,
                        "kind": row.kind,
                        "severity": row.severity,
                        "status": row.status,
                        "details": row.details,
                    },
                    entity_type="conflict",
                    entity_id=row.id,
                )
        result.append(row)
    for row in existing.values():
        if row.status == "OPEN":
            row.status = "RESOLVED"
            row.resolved_at_ms = simulation_time_ms
            row.fingerprint = None
            append_event(
                session,
                run,
                "conflict.resolved",
                {"conflictId": row.id, "kind": row.kind, "status": row.status},
                entity_type="conflict",
                entity_id=row.id,
            )
    return tuple(result)


def scan_resource_conflicts(session: Session, run: SimulationRun) -> tuple[ConflictFinding, ...]:
    findings: list[ConflictFinding] = []
    reservations = session.scalars(
        select(ResourceReservation).where(
            ResourceReservation.run_id == run.id,
            ResourceReservation.status == "HELD",
        )
    ).all()
    held_by_resource: dict[tuple[str, str], list[ResourceReservation]] = {}
    for reservation in reservations:
        key = (reservation.resource_type, reservation.resource_id)
        held_by_resource.setdefault(key, []).append(reservation)
    for (resource_type, resource_id), rows in held_by_resource.items():
        owners = sorted({row.owner_train_id or "unknown" for row in rows})
        if len(rows) > 1 or len(owners) > 1:
            findings.append(
                ConflictFinding(
                    "RESOURCE_RESERVATION_COLLISION",
                    "CRITICAL",
                    {"resourceType": resource_type, "resourceId": resource_id, "trainIds": owners},
                )
            )
    occupied = session.scalars(
        select(Occupancy).where(Occupancy.run_id == run.id, Occupancy.released_at_ms.is_(None))
    ).all()
    occupied_by_block = {row.block_id: row.train_id for row in occupied}
    for reservation in reservations:
        if reservation.resource_type == "BLOCK":
            occupant = occupied_by_block.get(reservation.resource_id)
            if occupant is not None and occupant != reservation.owner_train_id:
                findings.append(
                    ConflictFinding(
                        "BLOCK_OCCUPIED_AGAINST_RESERVATION",
                        "CRITICAL",
                        {
                            "blockId": reservation.resource_id,
                            "occupyingTrainId": occupant,
                            "reservedTrainId": reservation.owner_train_id,
                            "routeId": reservation.owner_route_id,
                        },
                    )
                )
            block = session.get(Block, reservation.resource_id)
            if block is not None and block.availability != "OPEN":
                findings.append(
                    ConflictFinding(
                        "BLOCK_CLOSED_WITH_ACTIVE_ROUTE",
                        "HIGH",
                        {
                            "blockId": block.id,
                            "availability": block.availability,
                            "reason": block.closure_reason,
                            "routeId": reservation.owner_route_id,
                        },
                    )
                )
        elif reservation.resource_type == "SWITCH":
            switch = session.get(Switch, reservation.resource_id)
            if switch is not None and switch.is_failed:
                findings.append(
                    ConflictFinding(
                        "SWITCH_FAILED_ON_ACTIVE_ROUTE",
                        "CRITICAL",
                        {
                            "switchId": switch.id,
                            "failureReason": switch.failure_reason,
                            "routeId": reservation.owner_route_id,
                        },
                    )
                )
    for route in session.scalars(
        select(Route).where(Route.run_id == run.id, Route.status == "CONFLICTED")
    ).all():
        failure = route.failure_reason
        if not isinstance(failure, dict):
            continue
        reasons = set(failure.get("reasons", []))
        resources = failure.get("resources", [])
        if not isinstance(resources, list):
            continue
        still_present = False
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            conflict_resource_id = resource.get("id")
            conflict_resource_type = resource.get("type")
            if not isinstance(conflict_resource_id, str):
                continue
            held_by_other = any(
                reservation.resource_id == conflict_resource_id
                and reservation.owner_train_id != route.train_id
                for reservation in reservations
            )
            if conflict_resource_type == "BLOCK":
                block = session.get(Block, conflict_resource_id)
                still_present |= (
                    "BLOCK_UNAVAILABLE" in reasons
                    and block is not None
                    and block.availability != "OPEN"
                )
                still_present |= "BLOCK_OCCUPIED" in reasons and occupied_by_block.get(
                    conflict_resource_id
                ) not in (None, route.train_id)
                still_present |= "BLOCK_RESERVED" in reasons and held_by_other
            elif conflict_resource_type == "CONFLICT_ZONE":
                still_present |= "CONFLICT_ZONE_RESERVED" in reasons and held_by_other
            elif conflict_resource_type == "SWITCH":
                switch = session.get(Switch, conflict_resource_id)
                still_present |= (
                    "SWITCH_FAILED" in reasons and switch is not None and switch.is_failed
                )
                still_present |= "SWITCH_LOCKED" in reasons and held_by_other
        if still_present:
            findings.append(
                ConflictFinding(
                    "ROUTE_SAFETY_REJECTION",
                    "CRITICAL",
                    {
                        "routeId": route.id,
                        "trainId": route.train_id,
                        "failureReason": failure,
                    },
                )
            )
    return tuple(findings)
