from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Envelope:
    event_id: str
    sequence: int
    schema_version: int
    station_id: str
    run_id: str
    state_version: int
    simulation_time_ms: int
    occurred_at: str
    event_type: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "eventId": self.event_id,
            "sequence": self.sequence,
            "schemaVersion": self.schema_version,
            "stationId": self.station_id,
            "simulationRunId": self.run_id,
            "stateVersion": self.state_version,
            "simulationTimeMs": self.simulation_time_ms,
            "occurredAt": self.occurred_at,
            "type": self.event_type,
            "payload": self.payload,
        }


def apply_sequence(
    last_sequence: int, seen_ids: set[str], message: dict[str, Any]
) -> tuple[str, int]:
    event_id, sequence = str(message["eventId"]), int(message["sequence"])
    if event_id in seen_ids or sequence <= last_sequence:
        return "DUPLICATE", last_sequence
    if sequence != last_sequence + 1:
        return "RESYNC", last_sequence
    seen_ids.add(event_id)
    return "APPLY", sequence


def bounded_enqueue(queue: list[dict[str, Any]], message: dict[str, Any], limit: int) -> bool:
    """Coalesce uncommitted positions; return False when critical delivery needs resync."""
    if message.get("type") == "train.position.updated":
        entity = message.get("payload", {}).get("trainId")
        for index in range(len(queue) - 1, -1, -1):
            old = queue[index]
            if (
                old.get("type") == "train.position.updated"
                and old.get("payload", {}).get("trainId") == entity
            ):
                queue[index] = message
                return True
    if len(queue) >= limit:
        return False
    queue.append(message)
    return True
