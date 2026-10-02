from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Literal, cast


@dataclass(frozen=True, slots=True)
class OperationInput:
    id: str
    train_id: str
    track_id: str | None
    platform_id: str | None
    starts_at_ms: int
    duration_ms: int
    delay_ms: int
    priority: int
    locomotive_id: str | None = None
    crew_id: str | None = None


@dataclass(frozen=True, slots=True)
class TrackInput:
    id: str
    open: bool = True
    platform_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Snapshot:
    state_version: int
    operations: tuple[OperationInput, ...]
    tracks: tuple[TrackInput, ...]
    unavailable_resources: frozenset[str] = frozenset()
    closed_tracks: frozenset[str] = frozenset()
    blocked_intervals: tuple[tuple[str, int, int, str | None], ...] = ()


@dataclass(frozen=True, slots=True)
class Variant:
    operations: tuple[dict[str, object], ...]
    total_delay_ms: int
    affected_operations: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Result:
    status: Literal["FEASIBLE", "INFEASIBLE", "TIMEOUT"]
    source_state_version: int
    variants: tuple[Variant, ...]
    calculation_time_ms: int


def optimize(snapshot: Snapshot, *, time_limit_ms: int = 500) -> Result:
    """Generate up to three deterministic feasible alternatives without mutating input."""
    started = monotonic()
    candidates = sorted(snapshot.tracks, key=lambda track: track.id)
    operations = sorted(snapshot.operations, key=lambda op: (-op.priority, op.starts_at_ms, op.id))
    plans: list[Variant] = []
    signatures: set[tuple[tuple[str, str | None], ...]] = set()
    for rotation in range(min(3, max(1, len(candidates)))):
        if (monotonic() - started) * 1000 >= time_limit_ms:
            return _result("TIMEOUT", snapshot, (), started)
        assigned: list[dict[str, object]] = []
        occupied: dict[str, list[tuple[int, int]]] = {}
        delay_by_train: dict[str, int] = {}
        reasons: set[str] = set()
        impossible = False
        for op in operations:
            if (monotonic() - started) * 1000 >= time_limit_ms:
                return _result("TIMEOUT", snapshot, tuple(plans), started)
            eligible = [
                track
                for track in candidates
                if track.open
                and track.id not in snapshot.closed_tracks
                and (not op.platform_id or op.platform_id in track.platform_ids)
            ]
            if op.track_id:
                eligible = [track for track in eligible if track.id == op.track_id] + [
                    track for track in eligible if track.id != op.track_id
                ]
            eligible = eligible[rotation:] + eligible[:rotation]
            start = op.starts_at_ms + op.delay_ms + delay_by_train.get(op.train_id, 0)
            proposed_start = start
            duration = max(1, op.duration_ms)
            chosen = None
            chosen_start = start
            resources_available = all(
                not resource or resource not in snapshot.unavailable_resources
                for resource in (op.locomotive_id, op.crew_id, op.platform_id)
            )
            if resources_available:
                for track in eligible:
                    candidate_start = start
                    reservations = sorted(occupied.get(track.id, []))
                    resource_ids = (track.id, op.platform_id, op.locomotive_id, op.crew_id)
                    for _ in range(len(snapshot.blocked_intervals) + len(reservations) + 1):
                        collisions = [
                            (blocked_start, blocked_end)
                            for (
                                resource_id,
                                blocked_start,
                                blocked_end,
                                owner_operation_id,
                            ) in snapshot.blocked_intervals
                            if resource_id in resource_ids
                            and owner_operation_id != op.id
                            and candidate_start < blocked_end
                            and blocked_start < candidate_start + duration
                        ]
                        collisions.extend(
                            (reserved_start, reserved_end)
                            for reserved_start, reserved_end in reservations
                            if candidate_start < reserved_end
                            and reserved_start < candidate_start + duration
                        )
                        if not collisions:
                            break
                        candidate_start = max(candidate_start, min(end for _, end in collisions))
                    if not any(
                        candidate_start < end and begin < candidate_start + duration
                        for (
                            resource_id,
                            begin,
                            end,
                            owner_operation_id,
                        ) in snapshot.blocked_intervals
                        if resource_id in resource_ids and owner_operation_id != op.id
                    ) and all(
                        candidate_start + duration <= reserved_start
                        or reserved_end <= candidate_start
                        for reserved_start, reserved_end in reservations
                    ):
                        chosen = track
                        chosen_start = candidate_start
                        break
            if chosen is None:
                impossible = True
                break
            start = chosen_start
            moved = op.track_id is not None and chosen.id != op.track_id
            assigned.append(
                {
                    "operationId": op.id,
                    "trainId": op.train_id,
                    "trackId": chosen.id,
                    "platformId": op.platform_id,
                    "locomotiveId": op.locomotive_id,
                    "crewId": op.crew_id,
                    "startsAtMs": start,
                    "endsAtMs": start + duration,
                    "priority": op.priority,
                }
            )
            occupied.setdefault(chosen.id, []).append((start, start + duration))
            if moved:
                reasons.add(f"operation {op.id} reassigned to an available track")
            if start > proposed_start:
                reasons.add(f"operation {op.id} delayed to avoid a resource conflict")
                delay_by_train[op.train_id] = max(
                    delay_by_train.get(op.train_id, 0), start - op.starts_at_ms
                )
        if impossible:
            continue
        signature = tuple((str(row["operationId"]), str(row["trackId"])) for row in assigned)
        if signature in signatures:
            continue
        signatures.add(signature)
        delays = sum(
            max(
                0,
                int(cast(int, row["startsAtMs"]))
                - next(op.starts_at_ms for op in operations if op.id == row["operationId"]),
            )
            for row in assigned
        )
        plans.append(
            Variant(
                tuple(assigned),
                delays,
                tuple(
                    str(row["operationId"])
                    for row in assigned
                    if any(
                        reason.startswith(f"operation {row['operationId']} ") for reason in reasons
                    )
                ),
                tuple(sorted(reasons)) or ("existing assignments remain feasible",),
            )
        )
    status: Literal["FEASIBLE", "INFEASIBLE", "TIMEOUT"] = "FEASIBLE" if plans else "INFEASIBLE"
    return _result(status, snapshot, tuple(plans), started)


def _result(
    status: Literal["FEASIBLE", "INFEASIBLE", "TIMEOUT"],
    snapshot: Snapshot,
    variants: tuple[Variant, ...],
    started: float,
) -> Result:
    return Result(
        status, snapshot.state_version, variants, max(0, round((monotonic() - started) * 1000))
    )
