from __future__ import annotations

from simulation.graph.model import (
    GraphBlock,
    GraphEdge,
    GraphNode,
    GraphTrack,
    GraphTransition,
    StationGraph,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Block,
    Edge,
    Node,
    Platform,
    PlatformTrack,
    Signal,
    Switch,
    SwitchTransition,
    Track,
    Yard,
)


def demo_yard(db: Session) -> Yard | None:
    return db.scalar(select(Yard).where(Yard.code == "MAIN"))


def load_graph(db: Session, yard: Yard) -> StationGraph:
    nodes = db.scalars(select(Node).where(Node.yard_id == yard.id)).all()
    tracks = db.scalars(select(Track).where(Track.yard_id == yard.id)).all()
    blocks = db.scalars(
        select(Block).join(Track, Block.track_id == Track.id).where(Track.yard_id == yard.id)
    ).all()
    edges = db.scalars(select(Edge).where(Edge.yard_id == yard.id)).all()
    switches = db.scalars(select(Switch).where(Switch.yard_id == yard.id)).all()
    switch_ids = [switch.id for switch in switches]
    transitions = (
        db.scalars(select(SwitchTransition).where(SwitchTransition.switch_id.in_(switch_ids))).all()
        if switch_ids
        else []
    )
    signals = db.scalars(select(Signal).where(Signal.yard_id == yard.id)).all()
    blocks_by_track: dict[str, list[Block]] = {}
    for block in blocks:
        blocks_by_track.setdefault(block.track_id, []).append(block)
    return StationGraph(
        nodes={node.id: GraphNode(node.id, node.code, node.kind, node.x, node.y) for node in nodes},
        tracks={
            track.id: GraphTrack(
                track.id,
                track.code,
                track.purpose,
                track.length_m,
                tuple(
                    b.id for b in sorted(blocks_by_track.get(track.id, []), key=lambda b: b.ordinal)
                ),
            )
            for track in tracks
        },
        blocks={
            block.id: GraphBlock(
                block.id, block.code, block.track_id, block.length_m, block.availability
            )
            for block in blocks
        },
        edges={
            edge.id: GraphEdge(
                edge.id,
                edge.code,
                edge.from_node_id,
                edge.to_node_id,
                edge.track_id,
                edge.block_id,
                edge.length_m,
            )
            for edge in edges
        },
        switch_nodes={switch.id: switch.node_id for switch in switches},
        switch_positions={switch.id: switch.state for switch in switches},
        transitions=tuple(
            GraphTransition(t.switch_id, t.from_edge_id, t.to_edge_id, t.position)
            for t in transitions
        ),
        signal_edges=frozenset(signal.edge_id for signal in signals),
    )


def topology_payload(db: Session, yard: Yard) -> dict[str, object]:
    graph = load_graph(db, yard)
    errors = graph.validate()
    if errors:
        raise ValueError(",".join(errors))
    platforms = db.scalars(select(Platform).where(Platform.yard_id == yard.id)).all()
    platform_tracks = db.execute(
        select(PlatformTrack.platform_id, PlatformTrack.track_id)
        .join(Platform, Platform.id == PlatformTrack.platform_id)
        .where(Platform.yard_id == yard.id)
    ).all()
    track_codes = {track.id: track.code for track in graph.tracks.values()}
    track_platforms: dict[str, list[str]] = {track_id: [] for track_id in track_codes}
    for platform_id, track_id in platform_tracks:
        track_platforms.setdefault(track_id, []).append(platform_id)
    return {
        "yardId": yard.id,
        "nodes": [
            {"id": n.id, "code": n.code, "kind": n.kind, "x": n.x, "y": n.y}
            for n in graph.nodes.values()
        ],
        "edges": [
            {
                "id": e.id,
                "code": e.code,
                "fromNodeId": e.from_node_id,
                "toNodeId": e.to_node_id,
                "trackId": e.track_id,
                "blockId": e.block_id,
                "lengthM": e.length_m,
            }
            for e in graph.edges.values()
        ],
        "tracks": [
            {
                "id": t.id,
                "code": t.code,
                "purpose": t.purpose,
                "lengthM": t.length_m,
                "blockIds": list(t.block_ids),
                "platformIds": track_platforms.get(t.id, []),
            }
            for t in graph.tracks.values()
        ],
        "blocks": [
            {
                "id": b.id,
                "code": b.code,
                "trackId": b.track_id,
                "lengthM": b.length_m,
                "availability": b.availability,
            }
            for b in graph.blocks.values()
        ],
        "platforms": [
            {"id": p.id, "code": p.code, "name": p.name, "lengthM": p.length_m} for p in platforms
        ],
        "switches": [
            {
                "id": switch.id,
                "code": switch.code,
                "nodeId": switch.node_id,
                "state": switch.state,
                "movementState": switch.movement_state,
                "isFailed": switch.is_failed,
            }
            for switch in db.scalars(select(Switch).where(Switch.yard_id == yard.id)).all()
        ],
        "signals": [
            {
                "id": signal.id,
                "code": signal.code,
                "edgeId": signal.edge_id,
                "aspect": signal.aspect,
                "reasonCodes": signal.reason_codes,
            }
            for signal in db.scalars(select(Signal).where(Signal.yard_id == yard.id)).all()
        ],
    }
