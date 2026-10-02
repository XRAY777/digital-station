from __future__ import annotations

import time
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta

from backend.app.auth.service import password_hash
from backend.app.main import app
from backend.app.models import Operation, Role, User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.integration.test_auth import setup_auth_app

ORIGIN = {"Origin": "http://testserver"}


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    csrf = client.get("/api/auth/csrf").json()["csrfToken"]
    response = client.post(
        "/api/auth/login",
        headers={**ORIGIN, "X-CSRF-Token": csrf},
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return {**ORIGIN, "X-CSRF-Token": response.json()["csrfToken"]}


def test_dispatcher_demo_flow_and_reconnect_after_reset(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STATION_AI_PROVIDER", "none")
    target, engine, previous_engine = setup_auth_app(
        f"sqlite:///{(tmp_path / 'demo.db').as_posix()}"
    )
    from database.connection import make_session_factory

    factory = make_session_factory(engine)
    with factory.begin() as db:
        role = db.scalar(select(Role).where(Role.code == "DISPATCHER"))
        assert role is not None
        dispatcher = User(
            username="dispatcher",
            password_hash=password_hash.hash("Dispatcher password 84!"),
        )
        db.add(dispatcher)
        db.flush()
        db.add(UserRole(user_id=dispatcher.id, role_id=role.id))
    try:
        with TestClient(target) as client:
            admin = TestClient(target)
            observer = TestClient(target)
            headers = _login(client, "dispatcher", "Dispatcher password 84!")
            _login(admin, "admin", "Administrator password 88!")
            _login(observer, "observer", "Correct horse battery 17!")
            initial = client.get("/api/dispatcher/snapshot").json()
            assert initial["station"]["code"] == "DEMO-01"
            assert len(initial["trains"]) == 7
            assert initial["efficiency"]["score"] >= 0
            run_id = initial["simulation"]["runId"]
            station_id = initial["station"]["id"]
            train = next(
                item
                for item in initial["trains"]
                if item["source"] == "WEST" and item["category"] == "PASSENGER"
            )
            destination = next(
                node for node in initial["topology"]["nodes"] if node["code"] == "T02-E"
            )
            route = client.post(
                "/api/routes",
                headers=headers,
                json={"train_id": train["id"], "destination_node_id": destination["id"]},
            )
            assert route.status_code == 201, route.text
            route_id = route.json()["routeId"]
            reserved = client.post(f"/api/routes/{route_id}/reserve", headers=headers, json={})
            assert reserved.status_code == 200, reserved.text
            after_reserve = client.get("/api/dispatcher/snapshot").json()
            active_route = next(item for item in after_reserve["routes"] if item["id"] == route_id)
            assert len(active_route["blockIds"]) == 2
            assert active_route["switchIds"]
            assert all(
                any(
                    reservation["resourceId"] == block_id and reservation["routeId"] == route_id
                    for reservation in after_reserve["resources"]["reservations"]
                )
                for block_id in active_route["blockIds"]
            )
            forbidden = observer.post(
                "/api/dispatcher/incidents",
                headers={
                    **ORIGIN,
                    "X-CSRF-Token": observer.get("/api/auth/csrf").json()["csrfToken"],
                },
                json={"kind": "DISPATCHER_NOTE", "summary": "Denied"},
            )
            assert forbidden.status_code == 403

            with ExitStack() as sockets:
                streams = [
                    sockets.enter_context(
                        peer.websocket_connect(f"/ws/station/{station_id}", headers=ORIGIN)
                    )
                    for peer in (client, admin, observer)
                ]
                for stream in streams:
                    stream.send_json(
                        {"type": "SUBSCRIBE", "stationId": station_id, "lastSequence": 0}
                    )
                    snapshot = stream.receive_json()
                    assert snapshot["type"] == "SNAPSHOT"
                    assert snapshot["simulationRunId"] == run_id
                assert (
                    client.post(
                        "/api/simulation/control", headers=headers, json={"command": "START"}
                    ).status_code
                    == 200
                )
                assert (
                    client.post(
                        "/api/simulation/control",
                        headers=headers,
                        json={"command": "SET_SPEED", "rate": 10},
                    ).status_code
                    == 200
                )
                for stream in streams:
                    frame = stream.receive_json()
                    assert frame["messageType"] == "FRAME"
                    assert frame["stateVersion"] >= initial["simulation"]["stateVersion"]

            time.sleep(0.6)
            moving = client.get("/api/dispatcher/snapshot").json()
            moved_train = next(item for item in moving["trains"] if item["id"] == train["id"])
            assert moved_train["headPosition"] != train["headPosition"]
            assert moving["simulation"]["simulationTimeMs"] > 0
            assert any(signal["aspect"] == "CLEAR" for signal in moving["topology"]["signals"])
            schedule = next(item for item in moving["schedules"] if item["trainId"] == train["id"])
            delayed = client.post(
                "/api/dispatcher/incidents",
                headers=headers,
                json={
                    "kind": "TRAIN_DELAY",
                    "summary": "Train delayed",
                    "trainIds": [train["id"]],
                    "metadata": {"delaySeconds": 120},
                },
            )
            assert delayed.status_code == 201, delayed.text
            after_delay = client.get("/api/dispatcher/snapshot").json()
            assert (
                next(item for item in after_delay["schedules"] if item["id"] == schedule["id"])[
                    "delaySeconds"
                ]
                >= 120
            )
            track_id = next(
                track["id"]
                for track in moving["tracks"]
                if set(active_route["blockIds"]).issubset(set(track["blockIds"]))
            )
            closure = client.post(
                "/api/dispatcher/incidents",
                headers=headers,
                json={
                    "kind": "TRACK_CLOSURE",
                    "severity": "CRITICAL",
                    "summary": "Demo closure",
                    "trackIds": [track_id],
                },
            )
            assert closure.status_code == 201, closure.text
            assert closure.json()["replanning"]["newPlanId"]
            time.sleep(0.4)
            conflict_state = client.get("/api/dispatcher/snapshot").json()
            assert any(item["status"] == "OPEN" for item in conflict_state["conflicts"])
            assert any(
                item["id"] == track_id and item["status"] == "BLOCKED"
                for item in conflict_state["tracks"]
            )
            assert (
                client.post(
                    "/api/simulation/control", headers=headers, json={"command": "PAUSE"}
                ).status_code
                == 200
            )
            plan = client.post(f"/api/runs/{run_id}/optimization", headers=headers, json={})
            assert plan.status_code == 200, plan.text
            assert plan.json()["solverStatus"] == "FEASIBLE"
            assert len(plan.json()["variants"]) >= 2
            ai = client.post("/api/dispatcher/ai/recommendations", headers=headers, json={})
            assert ai.status_code == 200, ai.text
            assert ai.json()["provider"] == "OFFLINE"
            assert ai.json()["recommendedVariantId"]
            chosen_plan = ai.json()["planId"]
            chosen_variant = ai.json()["recommendedVariantId"]
            accepted = client.post(
                f"/api/optimization/{chosen_plan}/variants/{chosen_variant}/accept",
                headers=headers,
                json={},
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["status"] == "ACCEPTED"
            live_before_replay = client.get("/api/dispatcher/snapshot").json()
            replay = client.get("/api/dispatcher/replay", params={"timeMs": 0})
            assert replay.status_code == 200, replay.text
            assert replay.json()["mode"] == "REPLAY"
            live_after_replay = client.get("/api/dispatcher/snapshot").json()
            assert live_after_replay["simulation"] == live_before_replay["simulation"]
            audit = client.get("/api/dispatcher/audit")
            assert audit.status_code == 200
            assert any(
                item["action"] == "AI_RECOMMENDATION_ACCEPTED" for item in audit.json()["items"]
            )
            now = datetime.now(UTC)
            interval = {
                "from": (now - timedelta(days=2)).isoformat(),
                "to": (now + timedelta(days=2)).isoformat(),
            }
            csv = client.get("/api/dispatcher/reports.csv", params=interval)
            pdf = client.get("/api/dispatcher/reports.pdf", params=interval)
            assert csv.status_code == 200 and csv.content.startswith(b"\xef\xbb\xbf")
            assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
            # Continue through a fresh safe route while the conflicting track remains closed.
            east_train = next(item for item in initial["trains"] if item["source"] == "EAST")
            east_destination = next(
                item for item in initial["topology"]["nodes"] if item["code"] == "T04-W"
            )
            next_route = client.post(
                "/api/routes",
                headers=headers,
                json={"train_id": east_train["id"], "destination_node_id": east_destination["id"]},
            )
            assert next_route.status_code == 201, next_route.text
            reservation = client.post(
                f"/api/routes/{next_route.json()['routeId']}/reserve",
                headers=headers,
                json={},
            )
            assert reservation.status_code == 200, reservation.text
            assert (
                client.post(
                    "/api/simulation/control", headers=headers, json={"command": "RESUME"}
                ).status_code
                == 200
            )
            running_before = client.get("/api/dispatcher/snapshot").json()
            assert client.get("/api/dispatcher/replay", params={"timeMs": 0}).status_code == 200
            time.sleep(0.4)
            running_after = client.get("/api/dispatcher/snapshot").json()
            assert (
                running_after["simulation"]["simulationTimeMs"]
                > running_before["simulation"]["simulationTimeMs"]
            )
            assert (
                next(t for t in running_after["trains"] if t["id"] == east_train["id"])[
                    "headPosition"
                ]
                != east_train["headPosition"]
            )
            assert client.get("/health").status_code == 200
            assert (
                client.post(
                    "/api/simulation/control", headers=headers, json={"command": "PAUSE"}
                ).status_code
                == 200
            )
            reset = client.post(
                "/api/simulation/control", headers=headers, json={"command": "RESET"}
            )
            assert reset.status_code == 200, reset.text
            new_run_id = reset.json()["runId"]
            assert new_run_id != run_id
            with factory() as db:
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(Operation)
                        .where(Operation.run_id == new_run_id)
                    )
                    == 6
                )
            with client.websocket_connect(f"/ws/station/{station_id}", headers=ORIGIN) as stream:
                stream.send_json(
                    {
                        "type": "SUBSCRIBE",
                        "stationId": station_id,
                        "simulationRunId": run_id,
                        "lastSequence": 999,
                    }
                )
                fresh = stream.receive_json()
                assert fresh["type"] == "SNAPSHOT"
                assert fresh["simulationRunId"] == new_run_id
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()


def test_planning_and_ai_mutations_require_csrf() -> None:
    target, engine, previous_engine = setup_auth_app()
    try:
        with TestClient(target) as client:
            headers = _login(client, "admin", "Administrator password 88!")
            run_id = client.get("/api/dispatcher/snapshot").json()["simulation"]["runId"]
            denied_plan = client.post(f"/api/runs/{run_id}/optimization", json={})
            denied_ai = client.post("/api/dispatcher/ai/recommendations", json={})
            assert denied_plan.status_code == 403
            assert denied_ai.status_code == 403
            assert (
                client.post(
                    f"/api/runs/{run_id}/optimization", headers=headers, json={}
                ).status_code
                == 200
            )
            assert (
                client.post(
                    "/api/dispatcher/ai/recommendations", headers=headers, json={}
                ).status_code
                == 200
            )
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()


def test_active_p303_route_moves_and_streams_authoritative_position(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STATION_AI_PROVIDER", "none")
    target, engine, previous_engine = setup_auth_app(
        f"sqlite:///{(tmp_path / 'p303-movement.db').as_posix()}"
    )
    from database.connection import make_session_factory

    factory = make_session_factory(engine)
    with factory.begin() as db:
        role = db.scalar(select(Role).where(Role.code == "DISPATCHER"))
        assert role is not None
        user = User(
            username="movement-dispatcher",
            password_hash=password_hash.hash("Dispatcher password 84!"),
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
    try:
        with TestClient(target) as client:
            headers = _login(client, "movement-dispatcher", "Dispatcher password 84!")
            initial = client.get("/api/dispatcher/snapshot").json()
            train = next(row for row in initial["trains"] if row["code"] == "П-303")
            destination = next(
                row for row in initial["topology"]["nodes"] if row["code"] == "T02-E"
            )
            created = client.post(
                "/api/routes",
                headers=headers,
                json={"train_id": train["id"], "destination_node_id": destination["id"]},
            )
            assert created.status_code == 201, created.text
            route_id = created.json()["routeId"]
            reserved = client.post(f"/api/routes/{route_id}/reserve", headers=headers, json={})
            assert reserved.status_code == 200, reserved.text
            waiting = client.get("/api/dispatcher/snapshot").json()
            reserved_view = next(row for row in waiting["routes"] if row["id"] == route_id)
            assert reserved_view["status"] == "RESERVED"
            assert reserved_view["activationReason"] == "Waiting for simulation"
            waiting_train = next(row for row in waiting["trains"] if row["id"] == train["id"])
            assert waiting_train["targetSpeedMps"] > 0
            held_resources = [
                row for row in waiting["resources"]["reservations"]
                if row["routeId"] == route_id
            ]
            assert any(row["type"] == "BLOCK" and row["status"] == "HELD" for row in held_resources)
            assert any(
                row["type"] == "CONFLICT_ZONE" and row["status"] == "HELD"
                for row in held_resources
            )
            station_id = initial["station"]["id"]
            run_id = initial["simulation"]["runId"]
            with client.websocket_connect(f"/ws/station/{station_id}", headers=ORIGIN) as stream:
                stream.send_json({"type": "SUBSCRIBE", "stationId": station_id})
                assert stream.receive_json()["type"] == "SNAPSHOT"
                started = client.post(
                    "/api/simulation/control", headers=headers, json={"command": "START"}
                )
                assert started.status_code == 200, started.text
                accelerated = client.post(
                    "/api/simulation/control",
                    headers=headers,
                    json={"command": "SET_SPEED", "rate": 10},
                )
                assert accelerated.status_code == 200, accelerated.text
                position_event = None
                activation_event = None
                edge_changed = False
                block_changed = False
                for _ in range(64):
                    message = stream.receive_json()
                    if (
                        message.get("type") == "route.status.updated"
                        and message.get("payload", {}).get("routeId") == route_id
                    ):
                        activation_event = message
                    if (
                        message.get("type") == "train.position.updated"
                        and message.get("payload", {}).get("trainId") == train["id"]
                    ):
                        position_event = message
                        edge_changed = (
                            message["payload"]["headEdgeId"] != train["headPosition"]["edgeId"]
                        )
                        block_changed = message["payload"]["blockIds"] != train["currentBlockIds"]
                        if edge_changed and block_changed:
                            break
                assert position_event is not None, "P-303 position event not delivered"
                assert activation_event is not None, "route activation event not delivered"
                assert activation_event["payload"]["status"] == "ACTIVE"
                assert edge_changed, "P-303 never advanced to a new edge"
                assert block_changed, "P-303 occupancy footprint never advanced to another block"
                assert position_event["simulationRunId"] == run_id
                assert position_event["payload"]["speedMps"] > 0

            moved_snapshot = client.get("/api/dispatcher/snapshot").json()
            moved = next(row for row in moved_snapshot["trains"] if row["id"] == train["id"])
            assert moved_snapshot["simulation"]["status"] == "RUNNING"
            active_route = next(row for row in moved_snapshot["routes"] if row["id"] == route_id)
            assert active_route["status"] == "ACTIVE"
            assert active_route["activationReason"] is None
            assert any(
                signal["id"] in active_route["signalIds"] and signal["aspect"] == "CLEAR"
                for signal in moved_snapshot["topology"]["signals"]
            )
            active_resources = [
                row for row in moved_snapshot["resources"]["reservations"]
                if row["routeId"] == route_id
            ]
            assert active_resources and all(row["status"] == "HELD" for row in active_resources)
            assert moved["headPosition"] != train["headPosition"]
            assert moved["headPosition"]["edgeId"] != train["headPosition"]["edgeId"]
            assert moved["currentBlockIds"] != train["currentBlockIds"]
            assert (
                position_event["payload"]["headEdgeId"] != train["headPosition"]["edgeId"]
                or position_event["payload"]["headOffsetM"]
                != train["headPosition"]["offsetM"]
            )
            assert moved["speedMps"] > 0
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()
