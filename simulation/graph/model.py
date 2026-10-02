from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    code: str
    kind: str
    x: float | None
    y: float | None


@dataclass(frozen=True, slots=True)
class GraphTrack:
    id: str
    code: str
    purpose: str
    length_m: int
    block_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphBlock:
    id: str
    code: str
    track_id: str
    length_m: int
    availability: str


@dataclass(frozen=True, slots=True)
class GraphEdge:
    id: str
    code: str
    from_node_id: str
    to_node_id: str
    track_id: str | None
    block_id: str | None
    length_m: int


@dataclass(frozen=True, slots=True)
class GraphTransition:
    switch_id: str
    from_edge_id: str
    to_edge_id: str
    position: str


@dataclass(slots=True)
class StationGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    tracks: dict[str, GraphTrack] = field(default_factory=dict)
    blocks: dict[str, GraphBlock] = field(default_factory=dict)
    edges: dict[str, GraphEdge] = field(default_factory=dict)
    switch_nodes: dict[str, str] = field(default_factory=dict)
    switch_positions: dict[str, str] = field(default_factory=dict)
    transitions: tuple[GraphTransition, ...] = ()
    signal_edges: frozenset[str] = frozenset()

    def allowed_next_edges(self, incoming_edge_id: str) -> tuple[str, ...]:
        incoming = self.edges[incoming_edge_id]
        switch_id = next(
            (sid for sid, node_id in self.switch_nodes.items() if node_id == incoming.to_node_id),
            None,
        )
        if switch_id is not None:
            return tuple(
                transition.to_edge_id
                for transition in self.transitions
                if transition.switch_id == switch_id and transition.from_edge_id == incoming_edge_id
            )
        return tuple(
            edge.id for edge in self.edges.values() if edge.from_node_id == incoming.to_node_id
        )

    def paths(self, source_code: str, destination_code: str, limit: int = 20) -> list[list[str]]:
        source = next(node for node in self.nodes.values() if node.code == source_code)
        destination = next(node for node in self.nodes.values() if node.code == destination_code)
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in self.edges.values():
            outgoing[edge.from_node_id].append(edge.id)
        found: list[list[str]] = []
        queue: deque[tuple[str, str | None, tuple[str, ...], frozenset[str]]] = deque(
            (node_id, None, (), frozenset({source.id})) for node_id in (source.id,)
        )
        while queue and len(found) < limit:
            node_id, previous_edge_id, path, visited_nodes = queue.popleft()
            if node_id == destination.id:
                found.append(list(path))
                continue
            candidates = (
                outgoing[node_id]
                if previous_edge_id is None
                else self.allowed_next_edges(previous_edge_id)
            )
            for edge_id in candidates:
                edge = self.edges[edge_id]
                if edge.to_node_id not in visited_nodes:
                    queue.append(
                        (
                            edge.to_node_id,
                            edge.id,
                            (*path, edge.id),
                            visited_nodes | {edge.to_node_id},
                        )
                    )
        return found

    def validate(self) -> list[str]:
        errors: list[str] = []
        edges_by_node = {edge.id: edge for edge in self.edges.values()}
        if not self.nodes or not self.tracks or not self.edges:
            errors.append("topology_empty")
        for edge in self.edges.values():
            if edge.from_node_id not in self.nodes or edge.to_node_id not in self.nodes:
                errors.append(f"edge_endpoint_missing:{edge.code}")
            if edge.block_id is not None:
                block = self.blocks.get(edge.block_id)
                if block is None:
                    errors.append(f"edge_block_missing:{edge.code}")
                elif edge.track_id != block.track_id:
                    errors.append(f"edge_track_block_mismatch:{edge.code}")
        for track in self.tracks.values():
            blocks = [self.blocks[bid] for bid in track.block_ids if bid in self.blocks]
            if len(blocks) != len(track.block_ids):
                errors.append(f"track_block_missing:{track.code}")
            if len(blocks) < 2 or sum(block.length_m for block in blocks) != track.length_m:
                errors.append(f"track_length_or_segmentation_invalid:{track.code}")
        for transition in self.transitions:
            incoming = edges_by_node.get(transition.from_edge_id)
            outgoing = edges_by_node.get(transition.to_edge_id)
            node_id = self.switch_nodes.get(transition.switch_id)
            if incoming is None or outgoing is None or node_id is None:
                errors.append(f"switch_transition_reference_missing:{transition.switch_id}")
            elif incoming.to_node_id != node_id or outgoing.from_node_id != node_id:
                errors.append(f"switch_transition_disconnected:{transition.switch_id}")
        allowed_positions: dict[str, set[str]] = defaultdict(set)
        for transition in self.transitions:
            allowed_positions[transition.switch_id].add(transition.position)
        for switch_id, position in self.switch_positions.items():
            if position not in allowed_positions.get(switch_id, set()):
                errors.append(f"switch_position_invalid:{switch_id}")
        if self.signal_edges.difference(self.edges):
            errors.append("signal_edge_missing")
        return errors
