from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Availability = Literal["OPEN", "BLOCKED", "MAINTENANCE"]


@dataclass(frozen=True, slots=True)
class BlockState:
    id: str
    code: str
    track_id: str
    length_m: int
    availability: Availability
    occupied_by_train_id: str | None = None
    closure_reason: str | None = None
    version: int = 1

    @property
    def display_status(self) -> str:
        if self.occupied_by_train_id is not None:
            return "OCCUPIED"
        if self.availability == "BLOCKED":
            return "BLOCKED"
        if self.availability == "MAINTENANCE":
            return "MAINTENANCE"
        return "FREE"
