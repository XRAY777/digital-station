from __future__ import annotations

from simulation.conflicts.engine import ConflictFinding
from simulation.graph.model import StationGraph
from simulation.routes.engine import RoutePlan, plan_route
from simulation.safety.engine import validate_route, validate_switch_move
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import (
    Edge,
    EdgeConflictZone,
    Occupancy,
    ResourceReservation,
    Route,
    RouteBlock,
    RouteEdge,
    RouteSignal,
    RouteSwitch,
    Signal,
    SimulationRun,
    Switch,
    SwitchTransition,
    Train,
    Yard,
)
from backend.app.services.conflicts import reconcile_conflicts
from backend.app.services.station_state import occupied_blocks
from backend.app.services.topology import load_graph


class RouteCommandError(Exception):
    def __init__(self, code: str, resources: tuple[str, ...] = ()) -> None:
        self.code = code
        self.resources = resources
        super().__init__(code)

    def __str__(self) -> str:
        return self.code


def _plan_for_train(
    session: Session, graph: StationGraph, train: Train, destination_node_id: str
) -> RoutePlan:
    if train.head_edge_id is None or train.head_edge_id not in graph.edges:
        raise RouteCommandError("TRAIN_POSITION_MISSING")
    head = graph.edges[train.head_edge_id]
    # Directed edges already encode travel direction; continue from the current edge's exit node.
    source = head.to_node_id
    destination = next(
        (
            node
            for node in graph.nodes.values()
            if node.id == destination_node_id or node.code == destination_node_id
        ),
        None,
    )
    if destination is None:
        raise RouteCommandError("DESTINATION_NODE_NOT_FOUND")
    # Scope conflict zones to this graph's directed edges.
    edge_ids = tuple(graph.edges)
    if edge_ids:
        zone_rows = session.execute(
            select(EdgeConflictZone.edge_id, EdgeConflictZone.zone_id).where(
                EdgeConflictZone.edge_id.in_(edge_ids)
            )
        ).all()
    zones: dict[str, set[str]] = {}
    for edge_id, zone_id in zone_rows:
        zones.setdefault(edge_id, set()).add(zone_id)
    plan = plan_route(
        graph,
        source,
        destination.id,
        {edge_id: frozenset(values) for edge_id, values in zones.items()},
    )
    edge_ids_in_route = tuple(dict.fromkeys((train.head_edge_id, *plan.edge_ids)))
    blocks = tuple(
        dict.fromkeys(
            block_id
            for edge_id in edge_ids_in_route
            if (block_id := graph.edges[edge_id].block_id) is not None
        )
    )
    positions = dict(plan.switch_positions)
    for left, right in zip(edge_ids_in_route, edge_ids_in_route[1:], strict=False):
        for transition in graph.transitions:
            if transition.from_edge_id == left and transition.to_edge_id == right:
                positions[transition.switch_id] = transition.position
                break
    edge_zones = {edge_id: set(values) for edge_id, values in zones.items()}
    route_zones = tuple(
        sorted({zone for edge_id in edge_ids_in_route for zone in edge_zones.get(edge_id, set())})
    )
    return RoutePlan(edge_ids_in_route, blocks, tuple(sorted(positions.items())), route_zones)


def create_route(
    session: Session,
    run: SimulationRun,
    yard: Yard,
    train_id: str,
    destination_node_id: str,
) -> Route:
    train = session.scalar(
        select(Train).where(Train.id == train_id, Train.station_id == yard.station_id)
    )
    if train is None:
        raise RouteCommandError("TRAIN_NOT_FOUND")
    graph = load_graph(session, yard)
    errors = graph.validate()
    if errors:
        raise RouteCommandError("TOPOLOGY_INVALID", tuple(errors))
    try:
        plan = _plan_for_train(session, graph, train, destination_node_id)
    except ValueError as exc:
        raise RouteCommandError(str(exc)) from exc
    route = Route(run_id=run.id, train_id=train.id, status="PROPOSED", graph_version=1)
    session.add(route)
    session.flush()
    _store_plan(session, route.id, plan)
    session.flush()
    return route


def reserve_route(session: Session, run_id: str, route_id: str, yard: Yard) -> Route:
    run = session.scalar(select(SimulationRun).where(SimulationRun.id == run_id).with_for_update())
    route = session.scalar(select(Route).where(Route.id == route_id, Route.run_id == run_id))
    if run is None or route is None:
        raise RouteCommandError("ROUTE_NOT_FOUND")
    if route.status != "PROPOSED":
        raise RouteCommandError("ROUTE_NOT_PROPOSED")
    train = session.get(Train, route.train_id)
    if train is None:
        raise RouteCommandError("TRAIN_NOT_FOUND")
    graph = load_graph(session, yard)
    plan = _stored_plan(session, route.id, graph)
    occupancies = occupied_blocks(session, run.id)
    block_reservations = _held_reservations(session, run.id, "BLOCK")
    zone_reservations = _held_reservations(session, run.id, "CONFLICT_ZONE")
    switch_reservations = _held_reservations(session, run.id, "SWITCH")
    locked_switches = {key: value for key, value in switch_reservations.items()}
    failed = frozenset(
        switch.id
        for switch in session.scalars(select(Switch).where(Switch.yard_id == yard.id)).all()
        if switch.is_failed
    )
    decision = validate_route(
        graph,
        plan,
        train.id,
        occupied_by_block=occupancies,
        reserved_by_block=block_reservations,
        reserved_zones=zone_reservations,
        switch_locks=locked_switches,
        switch_failures=failed,
    )
    if not decision.allowed:
        resource_types = {
            **{block_id: "BLOCK" for block_id in plan.block_ids},
            **{zone_id: "CONFLICT_ZONE" for zone_id in plan.conflict_zone_ids},
            **{switch_id: "SWITCH" for switch_id, _ in plan.switch_positions},
        }
        failure_reason: dict[str, object] = {
            "reasons": list(decision.reasons),
            "resources": [
                {"id": resource_id, "type": resource_types.get(resource_id, "UNKNOWN")}
                for resource_id in decision.blocked_resource_ids
            ],
        }
        route.status = "CONFLICTED"
        route.failure_reason = failure_reason
        reconcile_conflicts(
            session,
            run.id,
            run.simulation_time_ms,
            (
                ConflictFinding(
                    "ROUTE_SAFETY_REJECTION",
                    "CRITICAL",
                    {"routeId": route.id, "trainId": train.id, "failureReason": failure_reason},
                ),
            ),
        )
        session.flush()
        raise RouteCommandError("SAFETY_CONFLICT", decision.blocked_resource_ids)

    try:
        resources = [
            *(("BLOCK", resource_id) for resource_id in plan.block_ids),
            *(("CONFLICT_ZONE", resource_id) for resource_id in plan.conflict_zone_ids),
            *(("SWITCH", switch_id) for switch_id, _ in plan.switch_positions),
        ]
        for kind, resource_id in resources:
            session.add(
                ResourceReservation(
                    run_id=run.id,
                    resource_type=kind,
                    resource_id=resource_id,
                    starts_at_ms=run.simulation_time_ms,
                    ends_at_ms=2**62,
                    owner_train_id=train.id,
                    owner_route_id=route.id,
                    status="HELD",
                )
            )
        session.flush()
    except IntegrityError as exc:
        raise RouteCommandError(
            "RESOURCE_RACE", tuple(resource for _, resource in resources)
        ) from exc

    route.status = "RESERVED"
    if train.target_speed_mps <= 0:
        train.target_speed_mps = min(12.0, train.max_speed_mps)
    switches = {switch.id: switch for switch in session.scalars(select(Switch)).all()}
    for switch_id, target in plan.switch_positions:
        switch = switches[switch_id]
        if switch.state != target:
            switch.movement_state = "MOVING"
    session.add_all(
        RouteSignal(route_id=route.id, signal_id=signal.id)
        for signal in session.scalars(select(Signal).where(Signal.edge_id.in_(plan.edge_ids))).all()
    )
    session.flush()
    return route


def cancel_route(session: Session, run_id: str, route_id: str) -> Route:
    route = session.scalar(select(Route).where(Route.id == route_id, Route.run_id == run_id))
    if route is None:
        raise RouteCommandError("ROUTE_NOT_FOUND")
    if route.status not in ("PROPOSED", "RESERVED"):
        raise RouteCommandError("ROUTE_CANNOT_CANCEL")
    run_occupancies = session.scalars(
        select(Occupancy).where(
            Occupancy.run_id == run_id,
            Occupancy.released_at_ms.is_(None),
            Occupancy.train_id == route.train_id,
        )
    ).all()
    route_blocks = set(
        session.scalars(select(RouteBlock.block_id).where(RouteBlock.route_id == route.id)).all()
    )
    if route.status == "RESERVED" and any(row.block_id in route_blocks for row in run_occupancies):
        raise RouteCommandError("ROUTE_PROTECTS_OCCUPIED_TRAIN")
    for reservation in session.scalars(
        select(ResourceReservation).where(
            ResourceReservation.run_id == run_id,
            ResourceReservation.owner_route_id == route.id,
            ResourceReservation.status == "HELD",
        )
    ).all():
        reservation.status = "RELEASED"
    route.status = "CANCELLED"
    return route


def command_switch_position(
    session: Session, run_id: str, yard: Yard, switch_id: str, target_position: str
) -> Switch:
    switch = session.scalar(select(Switch).where(Switch.id == switch_id, Switch.yard_id == yard.id))
    if switch is None:
        raise RouteCommandError("SWITCH_NOT_FOUND")
    # Static allowed positions come from topology transitions, including positions not yet used.
    allowed = frozenset(
        session.scalars(
            select(SwitchTransition.position)
            .where(SwitchTransition.switch_id == switch.id)
            .distinct()
        ).all()
    )
    locks = frozenset(
        session.scalars(
            select(ResourceReservation.resource_id).where(
                ResourceReservation.run_id == run_id,
                ResourceReservation.resource_type == "SWITCH",
                ResourceReservation.resource_id == switch.id,
                ResourceReservation.status == "HELD",
            )
        ).all()
    )
    transitions = session.scalars(
        select(SwitchTransition).where(SwitchTransition.switch_id == switch.id)
    ).all()
    adjacent_nodes: set[str] = set()
    for transition in transitions:
        incoming = session.get(Edge, transition.from_edge_id)
        outgoing = session.get(Edge, transition.to_edge_id)
        if incoming is not None:
            adjacent_nodes.add(incoming.from_node_id)
        if outgoing is not None:
            adjacent_nodes.add(outgoing.to_node_id)
    adjacent_blocks = set(
        session.scalars(
            select(Edge.block_id).where(
                Edge.yard_id == yard.id,
                ((Edge.from_node_id.in_(adjacent_nodes)) | (Edge.to_node_id.in_(adjacent_nodes))),
                Edge.block_id.is_not(None),
            )
        ).all()
    )
    occupied = occupied_blocks(session, run_id)
    occupied_switches = (
        frozenset({switch.id})
        if any(block_id in adjacent_blocks for block_id in occupied)
        else frozenset()
    )
    failed = frozenset({switch.id}) if switch.is_failed else frozenset()
    decision = validate_switch_move(
        switch.id,
        target_position,
        allowed_positions=allowed,
        active_route_locks=locks,
        occupied_switch_ids=occupied_switches,
        failed_switch_ids=failed,
    )
    if not decision.allowed:
        raise RouteCommandError("SAFETY_CONFLICT", decision.blocked_resource_ids)
    switch.state = target_position
    switch.movement_state = "SETTLED"
    return switch


def _held_reservations(session: Session, run_id: str, kind: str) -> dict[str, str]:
    return {
        row.resource_id: row.owner_train_id or ""
        for row in session.scalars(
            select(ResourceReservation).where(
                ResourceReservation.run_id == run_id,
                ResourceReservation.resource_type == kind,
                ResourceReservation.status == "HELD",
            )
        ).all()
    }


def _store_plan(session: Session, route_id: str, plan: RoutePlan) -> None:
    session.add_all(
        [
            RouteEdge(route_id=route_id, ordinal=i, edge_id=edge_id)
            for i, edge_id in enumerate(plan.edge_ids)
        ]
    )
    session.add_all(
        [
            RouteBlock(route_id=route_id, ordinal=i, block_id=block_id)
            for i, block_id in enumerate(plan.block_ids)
        ]
    )
    session.add_all(
        [
            RouteSwitch(route_id=route_id, ordinal=i, switch_id=switch_id, position=position)
            for i, (switch_id, position) in enumerate(plan.switch_positions)
        ]
    )


def _stored_plan(session: Session, route_id: str, graph: StationGraph) -> RoutePlan:
    route_edges = session.scalars(
        select(RouteEdge).where(RouteEdge.route_id == route_id).order_by(RouteEdge.ordinal)
    ).all()
    route_blocks = session.scalars(
        select(RouteBlock).where(RouteBlock.route_id == route_id).order_by(RouteBlock.ordinal)
    ).all()
    route_switches = session.scalars(
        select(RouteSwitch).where(RouteSwitch.route_id == route_id).order_by(RouteSwitch.ordinal)
    ).all()
    if not route_edges:
        raise RouteCommandError("ROUTE_PLAN_MISSING")
    block_ids = tuple(row.block_id for row in route_blocks)
    edge_ids = tuple(row.edge_id for row in route_edges)
    zone_rows = session.execute(select(EdgeConflictZone.edge_id, EdgeConflictZone.zone_id)).all()
    zones: dict[str, set[str]] = {}
    for edge_id, zone_id in zone_rows:
        zones.setdefault(edge_id, set()).add(zone_id)
    for left, right in zip(edge_ids, edge_ids[1:], strict=False):
        if right not in graph.allowed_next_edges(left):
            raise RouteCommandError("ROUTE_PLAN_STALE")
    derived_blocks = tuple(
        dict.fromkeys(
            graph.edges[edge_id].block_id
            for edge_id in edge_ids
            if graph.edges[edge_id].block_id is not None
        )
    )
    if derived_blocks != block_ids:
        raise RouteCommandError("ROUTE_PLAN_STALE")
    zone_ids = tuple(sorted({zone for edge_id in edge_ids for zone in zones.get(edge_id, set())}))
    return RoutePlan(
        edge_ids,
        block_ids,
        tuple((row.switch_id, row.position) for row in route_switches),
        zone_ids,
    )
