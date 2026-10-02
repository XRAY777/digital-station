from __future__ import annotations

from dataclasses import dataclass

from simulation.graph.model import StationGraph
from simulation.trains.model import TrainState


@dataclass(frozen=True, slots=True)
class MovementResult:
    head_edge_id: str
    head_offset_m: float
    speed_mps: float
    occupied_block_ids: tuple[str, ...]
    moved_m: float
    stopped_reason: str | None = None


def _footprint(
    graph: StationGraph,
    route_edge_ids: tuple[str, ...],
    edge_index: int,
    offset_m: float,
    length_m: int,
) -> tuple[str, ...]:
    remaining = float(length_m)
    footprint: list[str] = []
    edge = graph.edges[route_edge_ids[edge_index]]
    if edge.block_id is not None and (edge.length_m == 0 or offset_m > 0):
        footprint.append(edge.block_id)
    remaining -= offset_m
    for previous_index in range(edge_index - 1, -1, -1):
        if remaining <= 0:
            break
        previous = graph.edges[route_edge_ids[previous_index]]
        if previous.block_id is not None and previous.block_id not in footprint:
            footprint.append(previous.block_id)
        remaining -= previous.length_m
    return tuple(footprint)


def advance_train(
    graph: StationGraph,
    train: TrainState,
    route_edge_ids: tuple[str, ...],
    elapsed_ms: int,
    clock_rate: int,
    *,
    permitted_block_ids: frozenset[str],
    occupied_by_block: dict[str, str],
    stop_before_edge_ids: frozenset[str] = frozenset(),
    acceleration_mps2: float = 0.8,
    service_brake_mps2: float = 1.0,
) -> MovementResult:
    """Advance a train along an authorized edge sequence, never over a denied boundary."""
    if elapsed_ms < 0 or clock_rate not in (1, 2, 5, 10):
        raise ValueError("invalid_elapsed_or_clock_rate")
    if acceleration_mps2 <= 0 or service_brake_mps2 <= 0:
        raise ValueError("invalid_train_dynamics")
    if train.head_edge_id is None or train.head_edge_id not in route_edge_ids:
        raise ValueError("train_not_on_route")
    route = list(route_edge_ids)
    index = route.index(train.head_edge_id)
    if index == 0:
        route, index = _prepend_occupied_tail(graph, route, train, index)
    route_edge_ids = tuple(route)
    for left, right in zip(route_edge_ids, route_edge_ids[1:], strict=False):
        if right not in graph.allowed_next_edges(left):
            raise ValueError("route_not_connected_on_graph")
    seconds = elapsed_ms * clock_rate / 1000
    remaining_route = route_edge_ids[index:]
    head_edge = graph.edges[train.head_edge_id]
    distance_to_next = max(0.0, head_edge.length_m - train.head_offset_m)
    denied_reason: str | None = None
    for edge_id in remaining_route[1:]:
        edge = graph.edges[edge_id]
        if edge_id in stop_before_edge_ids:
            denied_reason = "signal_stop"
            break
        if edge.block_id is not None and edge.block_id not in permitted_block_ids:
            denied_reason = "block_not_authorized"
            break
        owner = occupied_by_block.get(edge.block_id) if edge.block_id is not None else None
        if owner is not None and owner != train.id:
            denied_reason = "block_occupied"
            break
        distance_to_next += edge.length_m
    if denied_reason is None:
        denied_reason = "route_end"

    braking_distance = train.speed_mps**2 / (2 * service_brake_mps2)
    if denied_reason and distance_to_next <= braking_distance + 1e-9:
        target_speed = 0.0
        acceleration = -service_brake_mps2
    else:
        target_speed = min(train.target_speed_mps, train.max_speed_mps)
        acceleration = acceleration_mps2 if target_speed > train.speed_mps else -service_brake_mps2
        if target_speed == train.speed_mps:
            acceleration = 0.0

    end_speed = max(0.0, min(target_speed, train.speed_mps + acceleration * seconds))
    moved = max(0.0, (train.speed_mps + end_speed) * seconds / 2)
    if denied_reason:
        margin = 0.0 if denied_reason == "route_end" else 1e-6
        safe_distance = max(0.0, distance_to_next - margin)
        if moved > safe_distance:
            moved = safe_distance
            end_speed = 0.0

    distance_left = moved
    edge_index = index
    offset = train.head_offset_m
    while edge_index < len(route_edge_ids):
        edge = graph.edges[route_edge_ids[edge_index]]
        available = max(0.0, edge.length_m - offset)
        if edge.length_m == 0 or distance_left >= available:
            if edge.length_m > 0:
                distance_left -= available
            if edge_index + 1 >= len(route_edge_ids):
                offset = float(edge.length_m)
                break
            edge_index += 1
            offset = 0.0
            if distance_left == 0:
                break
        else:
            offset += distance_left
            distance_left = 0.0
            break
    new_edge_id = route_edge_ids[edge_index]
    footprint = _footprint(graph, route_edge_ids, edge_index, offset, train.length_m)
    return MovementResult(
        new_edge_id,
        offset,
        end_speed,
        footprint,
        moved,
        denied_reason if denied_reason and moved >= distance_to_next - 1e-6 else None,
    )


def _prepend_occupied_tail(
    graph: StationGraph, route: list[str], train: TrainState, index: int
) -> tuple[list[str], int]:
    remaining = max(0.0, train.length_m - train.head_offset_m)
    current = graph.edges[route[index]]
    occupied = set(train.current_block_ids)
    switch_at_node = {node_id: switch_id for switch_id, node_id in graph.switch_nodes.items()}
    while remaining > 0:
        candidates = [
            edge for edge in graph.edges.values() if edge.to_node_id == current.from_node_id
        ]
        switch_id = switch_at_node.get(current.from_node_id)
        if switch_id is not None:
            valid = {
                transition.from_edge_id
                for transition in graph.transitions
                if transition.switch_id == switch_id and transition.to_edge_id == current.id
            }
            candidates = [edge for edge in candidates if edge.id in valid]
        candidates = [
            edge for edge in candidates if edge.block_id is None or edge.block_id in occupied
        ]
        if len(candidates) != 1:
            break
        previous = candidates[0]
        route.insert(0, previous.id)
        index += 1
        remaining -= previous.length_m
        current = previous
    return route, index
