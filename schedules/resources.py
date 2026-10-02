from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    run_id: str
    resource_type: str
    resource_id: str
    starts_at_ms: int
    duration_ms: int
    release_buffer_ms: int = 0

    @property
    def ends_at_ms(self) -> int:
        return self.starts_at_ms + self.duration_ms + self.release_buffer_ms


def overlaps(start: int, end: int, other_start: int, other_end: int) -> bool:
    """Half-open intervals allow a resource to be reused at its release instant."""
    return start < other_end and other_start < end
