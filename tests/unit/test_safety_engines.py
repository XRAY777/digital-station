from __future__ import annotations

from simulation.conflicts.engine import ConflictFinding
from simulation.graph.model import GraphBlock, GraphEdge, GraphNode, StationGraph
from simulation.movement.engine import advance_train
from simulation.routes.engine import RoutePlan, plan_route
from simulation.safety.engine import validate_route, validate_switch_move
from simulation.trains.model import TrainState


def line_graph() -> StationGraph:
    return StationGraph(
        nodes={
            "n0": GraphNode("n0", "N0", "approach", 0, 0),
            "n1": GraphNode("n1", "N1", "junction", 1, 0),
            "n2": GraphNode("n2", "N2", "buffer", 2, 0),
        },
        blocks={
            "b1": GraphBlock("b1", "B1", "t1", 10, "OPEN"),
            "b2": GraphBlock("b2", "B2", "t1", 10, "OPEN"),
        },
        edges={
            "e1": GraphEdge("e1", "E1", "n0", "n1", "t1", "b1", 10),
            "e2": GraphEdge("e2", "E2", "n1", "n2", "t1", "b2", 10),
        },
    )


def train(*, edge: str = "e1", offset: float = 0, speed: float = 10) -> TrainState:
    return TrainState(
        id="t1",
        code="T1",
        category="PASSENGER",
        length_m=15,
        max_speed_mps=20,
        speed_mps=speed,
        target_speed_mps=20,
        direction="EASTBOUND",
        status="ON_TRACK",
        priority=1,
        head_edge_id=edge,
        head_offset_m=offset,
        current_block_ids=("b1",),
        current_track_id="track1",
    )


def test_x10_movement_brakes_at_red_signal_and_respects_train_footprint() -> None:
    graph = line_graph()
    result = advance_train(
        graph,
        train(offset=2),
        ("e1", "e2"),
        10_000,
        10,
        permitted_block_ids=frozenset({"b1", "b2"}),
        occupied_by_block={"b1": "t1"},
        stop_before_edge_ids=frozenset({"e2"}),
    )
    assert result.head_edge_id == "e1"
    assert result.head_offset_m < 10
    assert result.speed_mps == 0
    assert result.stopped_reason == "signal_stop"
    assert result.occupied_block_ids == ("b1",)


def test_movement_accounts_for_tail_across_multiple_blocks() -> None:
    result = advance_train(
        line_graph(),
        train(edge="e2", offset=5),
        ("e1", "e2"),
        0,
        10,
        permitted_block_ids=frozenset({"b1", "b2"}),
        occupied_by_block={"b1": "t1", "b2": "t1"},
    )
    assert set(result.occupied_block_ids) == {"b1", "b2"}


def test_route_planner_uses_only_connected_station_edges() -> None:
    plan = plan_route(line_graph(), "n0", "n2", {})
    assert plan.edge_ids == ("e1", "e2")
    assert plan.block_ids == ("b1", "b2")


def test_route_safety_rejects_occupied_reserved_and_conflicting_resources() -> None:
    graph = line_graph()
    plan = RoutePlan(("e1", "e2"), ("b1", "b2"), (), ("zone1",))
    first = validate_route(
        graph,
        plan,
        "train1",
        occupied_by_block={},
        reserved_by_block={},
        reserved_zones={},
        switch_locks={},
        switch_failures=frozenset(),
    )
    assert first.allowed
    second = validate_route(
        graph,
        plan,
        "train2",
        occupied_by_block={"b1": "train1"},
        reserved_by_block={"b2": "train1"},
        reserved_zones={"zone1": "train1"},
        switch_locks={},
        switch_failures=frozenset(),
    )
    assert not second.allowed
    assert {"BLOCK_OCCUPIED", "BLOCK_RESERVED", "CONFLICT_ZONE_RESERVED"} <= set(second.reasons)


def test_switch_safety_rejects_train_occupancy_and_active_route_lock() -> None:
    decision = validate_switch_move(
        "switch1",
        "DIVERGING",
        allowed_positions=frozenset({"STRAIGHT", "DIVERGING"}),
        active_route_locks=frozenset({"switch1"}),
        occupied_switch_ids=frozenset({"switch1"}),
        failed_switch_ids=frozenset(),
    )
    assert not decision.allowed
    assert "SWITCH_ROUTE_LOCKED" in decision.reasons
    assert "SWITCH_OCCUPIED_BY_TRAIN" in decision.reasons


def test_conflict_fingerprint_is_stable_for_same_reason() -> None:
    first = ConflictFinding("BLOCK_OCCUPIED", "CRITICAL", {"blockId": "b1"})
    repeated = ConflictFinding("BLOCK_OCCUPIED", "CRITICAL", {"blockId": "b1"})
    assert first.fingerprint == repeated.fingerprint
