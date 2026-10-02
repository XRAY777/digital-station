from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

ClockMode = Literal["STOPPED", "RUNNING", "PAUSED"]
ClockCommand = Literal["START", "PAUSE", "RESUME", "STOP", "RESET", "SET_SPEED"]
ClockRate = Literal[1, 2, 5, 10]
ALLOWED_RATES = (1, 2, 5, 10)


class ClockTransitionError(ValueError):
    pass


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ClockState:
    mode: ClockMode
    rate: ClockRate
    simulation_time_ms: int
    anchor_at: datetime | None = None

    def time_at(self, now: datetime) -> int:
        if self.mode != "RUNNING" or self.anchor_at is None:
            return self.simulation_time_ms
        elapsed_ms = max(0, int((as_utc(now) - as_utc(self.anchor_at)).total_seconds() * 1000))
        return self.simulation_time_ms + elapsed_ms * self.rate

    def apply(
        self,
        command: ClockCommand,
        now: datetime,
        rate: ClockRate | None = None,
    ) -> ClockState:
        simulation_time = self.time_at(now)
        if command == "RESET":
            if self.mode == "RUNNING":
                raise ClockTransitionError("pause_or_stop_before_reset")
            return ClockState("STOPPED", self.rate, 0)
        if command == "START":
            if self.mode != "STOPPED":
                raise ClockTransitionError("start_requires_stopped_clock")
            return ClockState("RUNNING", self.rate, simulation_time, now)
        if command == "PAUSE":
            if self.mode != "RUNNING":
                raise ClockTransitionError("pause_requires_running_clock")
            return ClockState("PAUSED", self.rate, simulation_time)
        if command == "RESUME":
            if self.mode != "PAUSED":
                raise ClockTransitionError("resume_requires_paused_clock")
            return ClockState("RUNNING", self.rate, simulation_time, now)
        if command == "STOP":
            return ClockState("STOPPED", self.rate, simulation_time)
        if command == "SET_SPEED":
            if rate not in ALLOWED_RATES:
                raise ClockTransitionError("unsupported_clock_rate")
            return ClockState(
                self.mode, rate, simulation_time, now if self.mode == "RUNNING" else None
            )
        raise ClockTransitionError("unknown_clock_command")
