"""Deterministic, non-sensitive mock infrastructure and timetable."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.app.models import (
    Block,
    ConflictZone,
    Edge,
    EdgeConflictZone,
    Node,
    Occupancy,
    Operation,
    Permission,
    Platform,
    PlatformTrack,
    Role,
    RolePermission,
    Schedule,
    Signal,
    SimulationRun,
    StateSnapshot,
    Station,
    Switch,
    SwitchTransition,
    Track,
    Train,
    Yard,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def seed_demo(session: Session) -> str:
    """Insert the demo dataset once; return station id. Never creates credentials."""
    station = session.scalar(select(Station).where(Station.code == "DEMO-01"))
    if station is None:
        station = Station(
            code="DEMO-01",
            name="Демонстрационная станция",
            timezone="Asia/Qyzylorda",
            config={"profile": "mock-v1"},
        )
        session.add(station)
        session.flush()
        yard = Yard(station_id=station.id, code="MAIN", name="Сортировочный парк")
        session.add(yard)
        session.flush()
        west = Node(yard_id=yard.id, code="W1", kind="junction", x=0, y=300)
        east = Node(yard_id=yard.id, code="E1", kind="junction", x=1175, y=300)
        west_in = Node(yard_id=yard.id, code="WEST-APPROACH", kind="approach", x=-80, y=300)
        east_out = Node(yard_id=yard.id, code="EAST-APPROACH", kind="approach", x=1255, y=300)
        session.add_all([west, east, west_in, east_out])
        session.flush()
        tracks: dict[int, Track] = {}
        path_nodes: dict[int, tuple[Node, Node]] = {}
        for number in range(1, 11):
            purpose = "PASSENGER" if number <= 3 else "SORTING" if number <= 8 else "CARGO"
            track = Track(
                yard_id=yard.id,
                code=f"{number:02}",
                name=f"Путь {number}",
                purpose=purpose,
                length_m=850,
            )
            session.add(track)
            session.flush()
            tracks[number] = track
            start = Node(
                yard_id=yard.id,
                code=f"T{number:02}-W",
                kind="platform_end",
                x=120,
                y=100 + 44 * number,
            )
            end = Node(
                yard_id=yard.id,
                code=f"T{number:02}-E",
                kind="platform_end",
                x=1050,
                y=100 + 44 * number,
            )
            session.add_all([start, end])
            session.flush()
            path_nodes[number] = (start, end)
            session.add_all(
                [
                    Block(track_id=track.id, code=f"{number:02}-A", ordinal=1, length_m=425),
                    Block(track_id=track.id, code=f"{number:02}-B", ordinal=2, length_m=425),
                ]
            )
        session.flush()
        west_switch = Switch(yard_id=yard.id, node_id=west.id, code="SW-W", state="TRACK_01_EAST")
        east_switch = Switch(yard_id=yard.id, node_id=east.id, code="SW-E", state="TRACK_01_EAST")
        session.add_all([west_switch, east_switch])
        session.flush()
        west_approach = Edge(
            yard_id=yard.id,
            code="APP-W-IN",
            from_node_id=west_in.id,
            to_node_id=west.id,
            length_m=80,
        )
        west_departure = Edge(
            yard_id=yard.id,
            code="APP-W-OUT",
            from_node_id=west.id,
            to_node_id=west_in.id,
            length_m=80,
        )
        east_arrival = Edge(
            yard_id=yard.id,
            code="APP-E-IN",
            from_node_id=east_out.id,
            to_node_id=east.id,
            length_m=80,
        )
        east_departure = Edge(
            yard_id=yard.id,
            code="APP-E-OUT",
            from_node_id=east.id,
            to_node_id=east_out.id,
            length_m=80,
        )
        session.add_all([west_approach, west_departure, east_arrival, east_departure])
        session.flush()
        west_zone = ConflictZone(yard_id=yard.id, code="CZ-W", name="Западная горловина")
        east_zone = ConflictZone(yard_id=yard.id, code="CZ-E", name="Восточная горловина")
        session.add_all([west_zone, east_zone])
        session.flush()
        entries_by_track: dict[int, Edge] = {}
        for number, track in tracks.items():
            start, end = path_nodes[number]
            blocks = session.scalars(
                select(Block).where(Block.track_id == track.id).order_by(Block.ordinal)
            ).all()
            entry = Edge(
                yard_id=yard.id,
                code=f"E{number:02}-A",
                from_node_id=west.id,
                to_node_id=start.id,
                block_id=blocks[0].id,
                track_id=track.id,
                length_m=425,
            )
            entry_reverse = Edge(
                yard_id=yard.id,
                code=f"E{number:02}-A-REV",
                from_node_id=start.id,
                to_node_id=west.id,
                block_id=blocks[0].id,
                track_id=track.id,
                length_m=425,
            )
            through = Edge(
                yard_id=yard.id,
                code=f"E{number:02}-B",
                from_node_id=start.id,
                to_node_id=end.id,
                block_id=blocks[1].id,
                track_id=track.id,
                length_m=425,
            )
            through_reverse = Edge(
                yard_id=yard.id,
                code=f"E{number:02}-B-REV",
                from_node_id=end.id,
                to_node_id=start.id,
                block_id=blocks[1].id,
                track_id=track.id,
                length_m=425,
            )
            throat_exit = Edge(
                yard_id=yard.id,
                code=f"E{number:02}-OUT",
                from_node_id=end.id,
                to_node_id=east.id,
                track_id=track.id,
                length_m=0,
            )
            throat_entry = Edge(
                yard_id=yard.id,
                code=f"E{number:02}-OUT-REV",
                from_node_id=east.id,
                to_node_id=end.id,
                track_id=track.id,
                length_m=0,
            )
            session.add_all(
                [entry, entry_reverse, through, through_reverse, throat_exit, throat_entry]
            )
            session.flush()
            entries_by_track[number] = entry
            session.add_all(
                [
                    SwitchTransition(
                        switch_id=west_switch.id,
                        from_edge_id=west_approach.id,
                        to_edge_id=entry.id,
                        position=f"TRACK_{number:02}_EAST",
                    ),
                    SwitchTransition(
                        switch_id=west_switch.id,
                        from_edge_id=entry_reverse.id,
                        to_edge_id=west_departure.id,
                        position=f"TRACK_{number:02}_WEST",
                    ),
                    SwitchTransition(
                        switch_id=east_switch.id,
                        from_edge_id=throat_exit.id,
                        to_edge_id=east_departure.id,
                        position=f"TRACK_{number:02}_EAST",
                    ),
                    SwitchTransition(
                        switch_id=east_switch.id,
                        from_edge_id=east_arrival.id,
                        to_edge_id=throat_entry.id,
                        position=f"TRACK_{number:02}_WEST",
                    ),
                    Signal(yard_id=yard.id, edge_id=entry.id, code=f"SIG-W-{number:02}"),
                    Signal(yard_id=yard.id, edge_id=entry_reverse.id, code=f"SIG-WR-{number:02}"),
                    EdgeConflictZone(edge_id=entry.id, zone_id=west_zone.id),
                    EdgeConflictZone(edge_id=entry_reverse.id, zone_id=west_zone.id),
                    EdgeConflictZone(edge_id=throat_exit.id, zone_id=east_zone.id),
                    EdgeConflictZone(edge_id=throat_entry.id, zone_id=east_zone.id),
                ]
            )
        for code, name, track_numbers in (
            ("P1", "Платформа 1", (1,)),
            ("P2", "Платформа 2", (2, 3)),
        ):
            platform = Platform(yard_id=yard.id, code=code, name=name, length_m=400)
            session.add(platform)
            session.flush()
            session.add_all(
                [
                    PlatformTrack(platform_id=platform.id, track_id=tracks[n].id)
                    for n in track_numbers
                ]
            )
        session.flush()
        run = SimulationRun(
            station_id=station.id,
            status="STOPPED",
            simulation_time_ms=0,
            clock_rate=1,
            config={"dataset": "demo-v1"},
        )
        session.add(run)
        session.flush()
        blocks_on_first = session.scalars(
            select(Block).where(Block.track_id == tracks[1].id).order_by(Block.ordinal)
        ).all()
        train_by_code: dict[str, Train] = {}
        for code in ("П-303", "П-3001", "C-102", "Г-2002", "C6-4003", "Г-2004", "П-304"):
            on_station = code == "П-303"
            from_west = code in ("П-3001", "C-102") or on_station
            train = Train(
                station_id=station.id,
                code=code,
                category="PASSENGER" if code.startswith("П-") else "FREIGHT",
                length_m=250 if code.startswith("П-") else 500,
                status="ON_TRACK" if on_station else "APPROACH",
                source="STATION" if on_station else "WEST" if from_west else "EAST",
                destination="EAST" if from_west else "WEST",
                direction="EASTBOUND" if from_west else "WESTBOUND",
                head_edge_id=(
                    entries_by_track[1].id
                    if on_station
                    else west_approach.id
                    if from_west
                    else east_arrival.id
                ),
                head_offset_m=250 if on_station else 40,
            )
            session.add(train)
            train_by_code[code] = train
        session.flush()
        session.add(
            Occupancy(
                run_id=run.id,
                train_id=train_by_code["П-303"].id,
                block_id=blocks_on_first[0].id,
                entered_at_ms=0,
            )
        )
        arrivals = demo_arrival_times()
        platform_ids = {
            p.code: p.id
            for p in session.scalars(select(Platform).where(Platform.yard_id == yard.id)).all()
        }
        for code, when in arrivals.items():
            platform_code = "P1" if code.startswith("П-") else "P2"
            session.add(
                Schedule(
                    train_id=train_by_code[code].id,
                    run_id=run.id,
                    scheduled_at=when,
                    event_type="ARRIVAL",
                    platform_id=platform_ids[platform_code],
                )
            )
        roles = {}
        for code, name in (
            ("OBSERVER", "Наблюдатель"),
            ("DISPATCHER", "Диспетчер"),
            ("ADMIN", "Администратор"),
        ):
            role = session.scalar(select(Role).where(Role.code == code))
            if role is None:
                role = Role(code=code, name=name)
                session.add(role)
            roles[code] = role
        permissions = {}
        for code in (
            "station.read",
            "history.read",
            "reports.read",
            "routes.manage",
            "simulation.manage",
            "incidents.manage",
            "plans.manage",
            "users.manage",
            "settings.manage",
            "audit.read",
        ):
            permission = session.scalar(select(Permission).where(Permission.code == code))
            if permission is None:
                permission = Permission(code=code, description=code.replace("_", " ").lower())
                session.add(permission)
            permissions[code] = permission
        session.flush()
        role_codes = {
            "OBSERVER": ("station.read", "history.read", "reports.read"),
            "DISPATCHER": (
                "station.read",
                "history.read",
                "reports.read",
                "routes.manage",
                "simulation.manage",
                "incidents.manage",
                "plans.manage",
            ),
            "ADMIN": tuple(permissions),
        }
        existing_grants = set(session.execute(select(RolePermission.role_id, RolePermission.permission_id)))
        session.add_all(
            RolePermission(role_id=roles[role].id, permission_id=permissions[permission].id)
            for role, codes in role_codes.items()
            for permission in codes
            if (roles[role].id, permissions[permission].id) not in existing_grants
        )
    _ensure_demo_graph(session, station)
    _ensure_demo_operations(session, station)
    _ensure_initial_snapshot(session, station)
    session.commit()
    return station.id


def demo_arrival_times() -> dict[str, datetime]:
    """Reference arrival times corresponding to the supplied 08:xx station display."""
    return {
        code: datetime(2026, 10, 2, hour, minute, tzinfo=UTC)
        for code, hour, minute in (
            ("П-3001", 3, 10),
            ("C-102", 3, 42),
            ("Г-2002", 3, 8),
            ("C6-4003", 3, 16),
            ("Г-2004", 3, 17),
            ("П-304", 3, 41),
        )
    }


def _ensure_demo_operations(session: Session, station: Station) -> None:
    seed_run = session.scalar(
        select(SimulationRun)
        .where(SimulationRun.station_id == station.id)
        .order_by(SimulationRun.started_at, SimulationRun.id)
        .limit(1)
    )
    if seed_run is None:
        return
    reference = datetime(2026, 10, 2, 3, 0, tzinfo=UTC)
    arrivals = demo_arrival_times()
    for train in session.scalars(select(Train).where(Train.station_id == station.id)).all():
        if train.code not in arrivals:
            continue
        existing = session.scalar(
            select(Operation.id).where(
                Operation.run_id == seed_run.id,
                Operation.train_id == train.id,
                Operation.kind == "ARRIVAL",
            )
        )
        if existing is not None:
            continue
        start_ms = int((arrivals[train.code] - reference).total_seconds() * 1000)
        session.add(
            Operation(
                run_id=seed_run.id,
                train_id=train.id,
                kind="ARRIVAL",
                status="PLANNED",
                starts_at_ms=start_ms,
                ends_at_ms=start_ms + 120_000,
                duration_ms=120_000,
                priority=70 if train.category == "PASSENGER" else 50,
                payload={},
            )
        )
    session.flush()


def _ensure_demo_graph(session: Session, station: Station) -> None:
    if session.scalar(select(Node.id).limit(1)) is not None:
        return
    yard = session.scalar(select(Yard).where(Yard.station_id == station.id))
    if yard is None:
        return
    west = Node(yard_id=yard.id, code="W1", kind="junction", x=0, y=300)
    east = Node(yard_id=yard.id, code="E1", kind="junction", x=1175, y=300)
    west_in = Node(yard_id=yard.id, code="WEST-APPROACH", kind="approach", x=-80, y=300)
    east_out = Node(yard_id=yard.id, code="EAST-APPROACH", kind="approach", x=1255, y=300)
    session.add_all([west, east, west_in, east_out])
    session.flush()
    tracks = session.scalars(
        select(Track).where(Track.yard_id == yard.id).order_by(Track.code)
    ).all()
    path_nodes: dict[str, tuple[Node, Node]] = {}
    for index, track in enumerate(tracks, start=1):
        start = Node(
            yard_id=yard.id, code=f"T{index:02}-W", kind="platform_end", x=120, y=100 + 44 * index
        )
        end = Node(
            yard_id=yard.id, code=f"T{index:02}-E", kind="platform_end", x=1050, y=100 + 44 * index
        )
        session.add_all([start, end])
        session.flush()
        path_nodes[track.id] = (start, end)
    west_switch = Switch(yard_id=yard.id, node_id=west.id, code="SW-W", state="TRACK_01_EAST")
    east_switch = Switch(yard_id=yard.id, node_id=east.id, code="SW-E", state="TRACK_01_EAST")
    session.add_all([west_switch, east_switch])
    session.flush()
    west_in_edge = Edge(
        yard_id=yard.id, code="APP-W-IN", from_node_id=west_in.id, to_node_id=west.id, length_m=80
    )
    west_out_edge = Edge(
        yard_id=yard.id, code="APP-W-OUT", from_node_id=west.id, to_node_id=west_in.id, length_m=80
    )
    east_in_edge = Edge(
        yard_id=yard.id, code="APP-E-IN", from_node_id=east_out.id, to_node_id=east.id, length_m=80
    )
    east_out_edge = Edge(
        yard_id=yard.id, code="APP-E-OUT", from_node_id=east.id, to_node_id=east_out.id, length_m=80
    )
    session.add_all([west_in_edge, west_out_edge, east_in_edge, east_out_edge])
    west_zone = ConflictZone(yard_id=yard.id, code="CZ-W", name="Западная горловина")
    east_zone = ConflictZone(yard_id=yard.id, code="CZ-E", name="Восточная горловина")
    session.add_all([west_zone, east_zone])
    session.flush()
    entry_edges: dict[str, Edge] = {}
    for number, track in enumerate(tracks, start=1):
        start, end = path_nodes[track.id]
        blocks = session.scalars(
            select(Block).where(Block.track_id == track.id).order_by(Block.ordinal)
        ).all()
        if len(blocks) < 2:
            raise ValueError(f"track_requires_two_blocks:{track.code}")
        edges = [
            Edge(
                yard_id=yard.id,
                code=f"E{number:02}-A",
                from_node_id=west.id,
                to_node_id=start.id,
                track_id=track.id,
                block_id=blocks[0].id,
                length_m=blocks[0].length_m,
            ),
            Edge(
                yard_id=yard.id,
                code=f"E{number:02}-A-REV",
                from_node_id=start.id,
                to_node_id=west.id,
                track_id=track.id,
                block_id=blocks[0].id,
                length_m=blocks[0].length_m,
            ),
            Edge(
                yard_id=yard.id,
                code=f"E{number:02}-B",
                from_node_id=start.id,
                to_node_id=end.id,
                track_id=track.id,
                block_id=blocks[1].id,
                length_m=blocks[1].length_m,
            ),
            Edge(
                yard_id=yard.id,
                code=f"E{number:02}-B-REV",
                from_node_id=end.id,
                to_node_id=start.id,
                track_id=track.id,
                block_id=blocks[1].id,
                length_m=blocks[1].length_m,
            ),
            Edge(
                yard_id=yard.id,
                code=f"E{number:02}-OUT",
                from_node_id=end.id,
                to_node_id=east.id,
                track_id=track.id,
                length_m=0,
            ),
            Edge(
                yard_id=yard.id,
                code=f"E{number:02}-OUT-REV",
                from_node_id=east.id,
                to_node_id=end.id,
                track_id=track.id,
                length_m=0,
            ),
        ]
        session.add_all(edges)
        session.flush()
        entry, entry_reverse, _through, _through_reverse, throat_exit, throat_entry = edges
        entry_edges[track.id] = entry
        session.add_all(
            [
                SwitchTransition(
                    switch_id=west_switch.id,
                    from_edge_id=west_in_edge.id,
                    to_edge_id=entry.id,
                    position=f"TRACK_{number:02}_EAST",
                ),
                SwitchTransition(
                    switch_id=west_switch.id,
                    from_edge_id=entry_reverse.id,
                    to_edge_id=west_out_edge.id,
                    position=f"TRACK_{number:02}_WEST",
                ),
                SwitchTransition(
                    switch_id=east_switch.id,
                    from_edge_id=throat_exit.id,
                    to_edge_id=east_out_edge.id,
                    position=f"TRACK_{number:02}_EAST",
                ),
                SwitchTransition(
                    switch_id=east_switch.id,
                    from_edge_id=east_in_edge.id,
                    to_edge_id=throat_entry.id,
                    position=f"TRACK_{number:02}_WEST",
                ),
                Signal(yard_id=yard.id, edge_id=entry.id, code=f"SIG-W-{number:02}"),
                Signal(yard_id=yard.id, edge_id=entry_reverse.id, code=f"SIG-WR-{number:02}"),
                EdgeConflictZone(edge_id=entry.id, zone_id=west_zone.id),
                EdgeConflictZone(edge_id=entry_reverse.id, zone_id=west_zone.id),
                EdgeConflictZone(edge_id=throat_exit.id, zone_id=east_zone.id),
                EdgeConflictZone(edge_id=throat_entry.id, zone_id=east_zone.id),
            ]
        )
    session.flush()
    for train in session.scalars(select(Train).where(Train.station_id == station.id)).all():
        parked = train.code == "П-303"
        from_west = train.code in ("П-3001", "C-102")
        train.status = "ON_TRACK" if parked else "APPROACH"
        train.source = "STATION" if parked else "WEST" if from_west else "EAST"
        train.destination = "EAST" if (parked or from_west) else "WEST"
        train.direction = "EASTBOUND" if (parked or from_west) else "WESTBOUND"
        train.head_edge_id = (
            entry_edges[tracks[0].id].id
            if parked and tracks
            else west_in_edge.id
            if from_west
            else east_in_edge.id
        )
        train.head_offset_m = 250 if parked else 40
        train.speed_mps = 0
        train.target_speed_mps = 0
    session.flush()


def _ensure_initial_snapshot(session: Session, station: Station) -> None:
    run = session.scalar(
        select(SimulationRun)
        .where(SimulationRun.station_id == station.id)
        .order_by(SimulationRun.started_at, SimulationRun.id)
        .limit(1)
    )
    if (
        run is None
        or session.scalar(
            select(StateSnapshot.id).where(
                StateSnapshot.run_id == run.id,
                StateSnapshot.simulation_time_ms == 0,
            )
        )
        is not None
    ):
        return
    yard = session.scalar(select(Yard).where(Yard.station_id == station.id))
    trains = session.scalars(select(Train).where(Train.station_id == station.id)).all()
    occupancies = session.scalars(
        select(Occupancy).where(Occupancy.run_id == run.id, Occupancy.released_at_ms.is_(None))
    ).all()
    switches = (
        session.scalars(select(Switch).where(Switch.yard_id == yard.id)).all() if yard else []
    )
    signals = session.scalars(select(Signal).where(Signal.yard_id == yard.id)).all() if yard else []
    blocks = (
        session.scalars(
            select(Block).join(Track, Block.track_id == Track.id).where(Track.yard_id == yard.id)
        ).all()
        if yard
        else []
    )
    session.add(
        StateSnapshot(
            run_id=run.id,
            simulation_time_ms=0,
            state_version=1,
            state={
                "trains": {
                    train.id: {
                        "status": train.status,
                        "direction": train.direction,
                        "headEdgeId": train.head_edge_id,
                        "headOffsetM": train.head_offset_m,
                        "speedMps": train.speed_mps,
                        "targetSpeedMps": train.target_speed_mps,
                    }
                    for train in trains
                },
                "occupancies": [
                    {
                        "trainId": item.train_id,
                        "blockId": item.block_id,
                        "enteredAtMs": item.entered_at_ms,
                    }
                    for item in occupancies
                ],
                "switches": {item.id: item.state for item in switches},
                "signals": {item.id: item.aspect for item in signals},
                "blocks": {
                    item.id: {
                        "availability": item.availability,
                        "closureReason": item.closure_reason,
                    }
                    for item in blocks
                },
            },
        )
    )
