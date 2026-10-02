from __future__ import annotations

from typing import cast

from simulation.graph.model import StationGraph
from simulation.movement.engine import MovementResult, advance_train
from simulation.trains.model import Direction, TrainState
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.events.writer import append_event
from backend.app.models import (
    Edge,
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
    Train,
    Yard,
)
from backend.app.services.conflicts import reconcile_conflicts, scan_resource_conflicts
from backend.app.services.state_history import maybe_capture_state
from backend.app.services.station_state import occupied_blocks
from backend.app.services.topology import load_graph


class TickRejected(ValueError):
    pass


def route_activation_wait_reason(
    session: Session,
    run: SimulationRun,
    route: Route,
    graph: StationGraph,
    occupancy: dict[str, str],
) -> str | None:
    """Return the safety gate preventing a route from being authorized to move."""
    if run.status != "RUNNING":
        return "Waiting for simulation"

    switch_rows = session.scalars(
        select(RouteSwitch).where(RouteSwitch.route_id == route.id)
    ).all()
    for row in switch_rows:
        switch = session.get(Switch, row.switch_id)
        if switch is None or switch.is_failed:
            return "Waiting for switch"

    route_blocks = set(
        session.scalars(select(RouteBlock.block_id).where(RouteBlock.route_id == route.id)).all()
    )
    held_blocks = set(
        session.scalars(
            select(ResourceReservation.resource_id).where(
                ResourceReservation.owner_route_id == route.id,
                ResourceReservation.resource_type == "BLOCK",
                ResourceReservation.status == "HELD",
            )
        ).all()
    )
    if route_blocks - held_blocks:
        return "Waiting for block"
    for block_id in route_blocks:
        block = graph.blocks.get(block_id)
        if (
            block is None
            or block.availability != "OPEN"
            or occupancy.get(block_id) not in (None, route.train_id)
        ):
            return "Waiting for block"
    # HELD conflict-zone and block reservations owned by this route protect its path;
    # they are permissions for this route, not blockers to its own activation.
    return None


def advance_simulation_tick(
    session: Session, run: SimulationRun, yard: Yard, elapsed_ms: int
) -> int:
    """Calculate every train first, then commit the whole tick in the caller's transaction."""
    if run.status != "RUNNING" or elapsed_ms <= 0:
        raise TickRejected("tick_requires_running_clock_and_positive_elapsed")
    graph = load_graph(session, yard)
    errors = graph.validate()
    if errors:
        raise TickRejected("station_topology_invalid")
    current_occupancy = occupied_blocks(session, run.id)
    routes = session.scalars(
        select(Route).where(Route.run_id == run.id, Route.status.in_(("RESERVED", "ACTIVE")))
    ).all()
    route_by_train = {route.train_id: route for route in routes}
    route_status_events: list[Route] = []
    route_edges: dict[str, tuple[str, ...]] = {}
    for route in routes:
        route_edges[route.id] = tuple(
            session.scalars(
                select(RouteEdge.edge_id)
                .where(RouteEdge.route_id == route.id)
                .order_by(RouteEdge.ordinal)
            ).all()
        )

    # Physical switch completion and signal authority are derived from active server routes.
    for route in routes:
        edge_ids = route_edges[route.id]
        switch_rows = session.scalars(
            select(RouteSwitch)
            .where(RouteSwitch.route_id == route.id)
            .order_by(RouteSwitch.ordinal)
        ).all()
        for switch_row in switch_rows:
            switch = session.get(Switch, switch_row.switch_id)
            if switch is None or switch.is_failed:
                continue
            if switch.state != switch_row.position or switch.movement_state == "MOVING":
                switch.state = switch_row.position
                switch.movement_state = "SETTLED"
        wait_reason = route_activation_wait_reason(
            session, run, route, graph, current_occupancy
        )
        if wait_reason is None and route.status == "RESERVED":
            route.status = "ACTIVE"
            route_status_events.append(route)
        authorized = wait_reason is None and route.status == "ACTIVE"
        signal_rows = session.scalars(
            select(RouteSignal).where(RouteSignal.route_id == route.id)
        ).all()
        for signal_row in signal_rows:
            signal = session.get(Signal, signal_row.signal_id)
            if signal is None:
                continue
            if authorized and signal.edge_id in edge_ids:
                signal.aspect = "CLEAR"
                signal.authority_end_node_id = graph.edges[edge_ids[-1]].to_node_id
                signal.reason_codes = []
            else:
                signal.aspect = "STOP"
                signal.authority_end_node_id = None
                signal.reason_codes = ["AUTHORITY_UNAVAILABLE"]

    proposals: dict[str, MovementResult] = {}
    for train_id, route in route_by_train.items():
        train = session.get(Train, train_id)
        if train is None or train.status not in ("ON_TRACK", "APPROACH", "WAITING"):
            continue
        edge_ids = route_edges[route.id]
        if not edge_ids or train.head_edge_id not in edge_ids:
            raise TickRejected("train_position_outside_reserved_route")
        held_blocks = frozenset(
            session.scalars(
                select(ResourceReservation.resource_id).where(
                    ResourceReservation.owner_route_id == route.id,
                    ResourceReservation.resource_type == "BLOCK",
                    ResourceReservation.status == "HELD",
                )
            ).all()
        )
        own_blocks = frozenset(
            block_id for block_id, owner in current_occupancy.items() if owner == train_id
        )
        stop_edges = frozenset(
            session.scalars(
                select(Signal.edge_id)
                .join(RouteSignal, RouteSignal.signal_id == Signal.id)
                .where(RouteSignal.route_id == route.id, Signal.aspect == "STOP")
            ).all()
        )
        proposals[train_id] = advance_train(
            graph,
            _train_state(session, train, current_occupancy),
            edge_ids,
            elapsed_ms,
            run.clock_rate,
            permitted_block_ids=held_blocks | own_blocks,
            occupied_by_block=current_occupancy,
            stop_before_edge_ids=stop_edges,
        )

    next_owners: dict[str, str] = {}
    for train_id, result in proposals.items():
        for block_id in result.occupied_block_ids:
            existing = current_occupancy.get(block_id)
            proposed = next_owners.get(block_id)
            if (existing is not None and existing != train_id) or (
                proposed is not None and proposed != train_id
            ):
                raise TickRejected("BLOCK_OCCUPIED_BY_OTHER_TRAIN")
            next_owners[block_id] = train_id

    simulation_time = run.simulation_time_ms + elapsed_ms * run.clock_rate
    for train_id, result in proposals.items():
        train = session.get(Train, train_id)
        assert train is not None
        train.head_edge_id = result.head_edge_id
        train.head_offset_m = result.head_offset_m
        train.speed_mps = result.speed_mps
        existing_rows = session.scalars(
            select(Occupancy).where(
                Occupancy.run_id == run.id,
                Occupancy.train_id == train_id,
                Occupancy.released_at_ms.is_(None),
            )
        ).all()
        existing_blocks = {occupancy_row.block_id: occupancy_row for occupancy_row in existing_rows}
        for block_id, occupancy_row in existing_blocks.items():
            if block_id not in result.occupied_block_ids:
                occupancy_row.released_at_ms = simulation_time
        for block_id in result.occupied_block_ids:
            if block_id not in existing_blocks:
                session.add(
                    Occupancy(
                        run_id=run.id,
                        train_id=train_id,
                        block_id=block_id,
                        entered_at_ms=simulation_time,
                    )
                )
    for train_id, result in proposals.items():
        route = route_by_train[train_id]
        route_path = route_edges[route.id]
        if not route_path or result.head_edge_id != route_path[-1]:
            continue
        final_edge = graph.edges[route_path[-1]]
        if result.head_offset_m < final_edge.length_m - 1e-6:
            continue
        route_blocks = set(
            session.scalars(
                select(ResourceReservation.resource_id).where(
                    ResourceReservation.owner_route_id == route.id,
                    ResourceReservation.resource_type == "BLOCK",
                    ResourceReservation.status == "HELD",
                )
            ).all()
        )
        if route_blocks.intersection(result.occupied_block_ids):
            continue
        route.status = "COMPLETED"
        route_status_events.append(route)
        for reservation in session.scalars(
            select(ResourceReservation).where(
                ResourceReservation.owner_route_id == route.id,
                ResourceReservation.status == "HELD",
            )
        ).all():
            reservation.status = "RELEASED"
        for signal_row in session.scalars(
            select(RouteSignal).where(RouteSignal.route_id == route.id)
        ).all():
            signal = session.get(Signal, signal_row.signal_id)
            if signal is not None:
                signal.aspect = "STOP"
                signal.authority_end_node_id = None
                signal.reason_codes = ["ROUTE_COMPLETED"]
    run.simulation_time_ms = simulation_time
    if proposals or route_status_events:
        run.state_version += 1
        for route in route_status_events:
            append_event(
                session,
                run,
                "route.status.updated",
                {
                    "routeId": route.id,
                    "trainId": route.train_id,
                    "status": route.status,
                    "activationReason": None,
                },
                entity_type="route",
                entity_id=route.id,
            )
        for train_id, result in proposals.items():
            append_event(
                session,
                run,
                "train.position.updated",
                {
                    "trainId": train_id,
                    "headEdgeId": result.head_edge_id,
                    "headOffsetM": result.head_offset_m,
                    "speedMps": result.speed_mps,
                    "blockIds": list(result.occupied_block_ids),
                },
                entity_type="train",
                entity_id=train_id,
            )
    reconcile_conflicts(
        session,
        run.id,
        simulation_time,
        scan_resource_conflicts(session, run),
    )
    maybe_capture_state(session, run)
    session.flush()
    return len(proposals)


def _train_state(session: Session, train: Train, occupancy: dict[str, str]) -> TrainState:
    edge = session.get(Edge, train.head_edge_id)
    return TrainState(
        id=train.id,
        code=train.code,
        category=train.category,
        length_m=train.length_m,
        max_speed_mps=train.max_speed_mps,
        speed_mps=train.speed_mps,
        target_speed_mps=train.target_speed_mps,
        direction=cast(Direction, train.direction),
        status=train.status,
        priority=train.priority,
        head_edge_id=train.head_edge_id,
        head_offset_m=train.head_offset_m,
        current_block_ids=tuple(
            block_id for block_id, owner in occupancy.items() if owner == train.id
        ),
        current_track_id=edge.track_id if edge is not None else None,
    )
