from __future__ import annotations

from dataclasses import dataclass

from simulation.graph.model import StationGraph


@dataclass(frozen=True, slots=True)
class RoutePlan:
    edge_ids: tuple[str, ...]
    block_ids: tuple[str, ...]
    switch_positions: tuple[tuple[str, str], ...]
    conflict_zone_ids: tuple[str, ...]


def plan_route(
    graph: StationGraph,
    source_node_id: str,
    destination_node_id: str,
    edge_zones: dict[str, frozenset[str]],
    *,
    max_edges: int = 256,
) -> RoutePlan:
    if source_node_id not in graph.nodes or destination_node_id not in graph.nodes:
        raise ValueError("route_node_missing")
    queue: list[tuple[str, tuple[str, ...]]] = [(source_node_id, ())]
    seen: set[tuple[str, str | None]] = set()
    selected: tuple[str, ...] | None = None
    while queue:
        node_id, path = queue.pop(0)
        if node_id == destination_node_id and path:
            selected = path
            break
        if len(path) >= max_edges:
            continue
        candidate_ids = (
            graph.allowed_next_edges(path[-1])
            if path
            else tuple(e.id for e in graph.edges.values() if e.from_node_id == node_id)
        )
        for edge_id in sorted(candidate_ids, key=lambda value: graph.edges[value].code):
            edge = graph.edges[edge_id]
            if edge.from_node_id != node_id:
                continue
            state = (edge.to_node_id, edge_id)
            if state in seen:
                continue
            seen.add(state)
            queue.append((edge.to_node_id, (*path, edge_id)))
    if selected is None:
        raise ValueError("route_unreachable")

    block_ids = tuple(
        dict.fromkeys(
            block_id
            for edge_id in selected
            if (block_id := graph.edges[edge_id].block_id) is not None
        )
    )
    positions: dict[str, str] = {}
    for left, right in zip(selected, selected[1:], strict=False):
        for transition in graph.transitions:
            if transition.from_edge_id == left and transition.to_edge_id == right:
                positions[transition.switch_id] = transition.position
                break
    zones = tuple(
        sorted({zone for edge_id in selected for zone in edge_zones.get(edge_id, frozenset())})
    )
    return RoutePlan(selected, block_ids, tuple(sorted(positions.items())), zones)
