from datetime import UTC, datetime, timedelta

import pytest
from simulation.clock import ClockState, ClockTransitionError


def test_clock_rates_and_full_lifecycle() -> None:
    start = datetime(2026, 10, 2, tzinfo=UTC)
    for rate in (1, 2, 5, 10):
        clock = ClockState("STOPPED", rate, 0)
        running = clock.apply("START", start)
        assert running.time_at(start + timedelta(seconds=3)) == 3000 * rate
        paused = running.apply("PAUSE", start + timedelta(seconds=3))
        resumed = paused.apply("RESUME", start + timedelta(seconds=10))
        assert resumed.time_at(start + timedelta(seconds=11)) == 3000 * rate + 1000 * rate
        stopped = resumed.apply("STOP", start + timedelta(seconds=11))
        reset = stopped.apply("RESET", start + timedelta(seconds=12))
        assert reset.mode == "STOPPED"
        assert reset.simulation_time_ms == 0


def test_clock_rejects_invalid_transitions_and_rates() -> None:
    now = datetime(2026, 10, 2, tzinfo=UTC)
    stopped = ClockState("STOPPED", 1, 0)
    with pytest.raises(ClockTransitionError):
        stopped.apply("PAUSE", now)
    with pytest.raises(ClockTransitionError):
        stopped.apply("SET_SPEED", now, 3)  # type: ignore[arg-type]
    with pytest.raises(ClockTransitionError):
        stopped.apply("START", now).apply("RESET", now)
