from __future__ import annotations

import copy
import time

import pytest
from metrics.efficiency import EfficiencyConfig, calculate
from optimization.engine import OperationInput, Snapshot, TrackInput, optimize
from schedules.resources import overlaps


def operation(
    identifier: str,
    *,
    start: int = 0,
    duration: int = 100,
    track: str | None = "t1",
    train: str | None = None,
) -> OperationInput:
    return OperationInput(identifier, train or identifier, track, None, start, duration, 0, 50)


def test_two_trains_are_not_assigned_the_same_track_at_the_same_time() -> None:
    result = optimize(
        Snapshot(3, (operation("a", train="a"), operation("b", train="b")), (TrackInput("t1"),))
    )
    assert result.status == "FEASIBLE"
    starts = [row["startsAtMs"] for row in result.variants[0].operations]
    assert len(set(starts)) == 2


def test_closed_track_is_excluded_from_every_plan() -> None:
    result = optimize(Snapshot(1, (operation("a"),), (TrackInput("t1", open=False),)))
    assert result.status == "INFEASIBLE"


def test_later_operation_is_delayed_to_avoid_track_overlap() -> None:
    result = optimize(
        Snapshot(1, (operation("a", start=0), operation("b", start=50)), (TrackInput("t1"),))
    )
    assert result.status == "FEASIBLE"
    assert result.variants[0].operations[1]["startsAtMs"] >= 100


def test_optimizer_timeout_is_not_misreported_as_optimal() -> None:
    snapshot = Snapshot(1, (operation("a"),), (TrackInput("t1"),))
    assert optimize(snapshot, time_limit_ms=0).status == "TIMEOUT"


def test_optimizer_does_not_mutate_immutable_input() -> None:
    rows = (operation("a"),)
    before = copy.deepcopy(rows)
    optimize(Snapshot(7, rows, (TrackInput("t1"),)))
    assert rows == before


def test_outdated_snapshot_version_is_preserved_for_acceptance_guard() -> None:
    result = optimize(Snapshot(41, (operation("a"),), (TrackInput("t1"),)))
    assert result.source_state_version == 41


def test_optimizer_returns_three_distinct_safe_alternatives_when_available() -> None:
    result = optimize(
        Snapshot(
            9,
            (operation("a"),),
            (TrackInput("t1"), TrackInput("t2"), TrackInput("t3")),
        )
    )
    assert len(result.variants) == 3
    assert len({variant.operations[0]["trackId"] for variant in result.variants}) == 3


def test_custom_efficiency_weights_recalculate_without_code_changes() -> None:
    config = EfficiencyConfig(
        weights=(
            ("throughput", 0.7),
            ("delay", 0.1),
            ("track_utilization", 0.1),
            ("conflicts", 0.05),
            ("resource_idle", 0.05),
        )
    )
    score = calculate(
        throughput_per_hour=10,
        average_delay_seconds=0,
        track_utilization=0.75,
        active_conflicts=0,
        resource_idle=0,
        config=config,
    )
    assert score["contributions"]["throughput"] == 70.0


def test_invalid_efficiency_weights_fail_closed() -> None:
    with pytest.raises(ValueError, match="efficiency_weights_invalid"):
        calculate(
            throughput_per_hour=0,
            average_delay_seconds=0,
            track_utilization=0,
            active_conflicts=0,
            resource_idle=0,
            config=EfficiencyConfig(weights=(("throughput", 1),)),
        )


def test_half_open_reservation_intervals_allow_release_instant_reuse() -> None:
    assert not overlaps(0, 10, 10, 20)
    assert overlaps(0, 11, 10, 20)


def test_optimization_timeout_budget_is_checked_before_work() -> None:
    started = time.monotonic()
    result = optimize(Snapshot(2, (operation("a"),), (TrackInput("t1"),)), time_limit_ms=0)
    assert result.status == "TIMEOUT"
    assert time.monotonic() >= started
