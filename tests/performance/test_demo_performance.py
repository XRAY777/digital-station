from __future__ import annotations

import statistics
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

from backend.app.auth.service import password_hash
from backend.app.main import app
from backend.app.models import Role, User, UserRole
from database.connection import make_session_factory
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.integration.test_auth import setup_auth_app

ORIGIN = {"Origin": "http://testserver"}


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    csrf = client.get("/api/auth/csrf").json()["csrfToken"]
    response = client.post(
        "/api/auth/login",
        headers={**ORIGIN, "X-CSRF-Token": csrf},
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {**ORIGIN, "X-CSRF-Token": response.json()["csrfToken"]}


def test_demo_load_with_two_trains_three_clients_and_incident_burst(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STATION_AI_PROVIDER", "none")
    target, engine, previous_engine = setup_auth_app(
        f"sqlite:///{(tmp_path / 'performance.db').as_posix()}"
    )
    factory = make_session_factory(engine)
    with factory.begin() as db:
        role = db.scalar(select(Role).where(Role.code == "DISPATCHER"))
        assert role is not None
        user = User(
            username="dispatcher",
            password_hash=password_hash.hash("Dispatcher password 84!"),
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
    try:
        with TestClient(target) as dispatcher:
            observer = TestClient(target)
            admin = TestClient(target)
            headers = _login(dispatcher, "dispatcher", "Dispatcher password 84!")
            _login(observer, "observer", "Correct horse battery 17!")
            _login(admin, "admin", "Administrator password 88!")
            snapshot = dispatcher.get("/api/dispatcher/snapshot").json()
            station_id = snapshot["station"]["id"]
            for source, target_code in (("WEST", "T02-E"), ("EAST", "T04-W")):
                train = next(train for train in snapshot["trains"] if train["source"] == source)
                node = next(
                    node for node in snapshot["topology"]["nodes"] if node["code"] == target_code
                )
                created = dispatcher.post(
                    "/api/routes",
                    headers=headers,
                    json={"train_id": train["id"], "destination_node_id": node["id"]},
                )
                assert created.status_code == 201
                reserved = dispatcher.post(
                    f"/api/routes/{created.json()['routeId']}/reserve",
                    headers=headers,
                    json={},
                )
                assert reserved.status_code == 200
            with ExitStack() as sockets:
                streams = [
                    sockets.enter_context(
                        peer.websocket_connect(f"/ws/station/{station_id}", headers=ORIGIN)
                    )
                    for peer in (dispatcher, observer, admin)
                ]
                for stream in streams:
                    stream.send_json({"type": "SUBSCRIBE", "stationId": station_id})
                    assert stream.receive_json()["type"] == "SNAPSHOT"
                assert (
                    dispatcher.post(
                        "/api/simulation/control", headers=headers, json={"command": "START"}
                    ).status_code
                    == 200
                )
                assert (
                    dispatcher.post(
                        "/api/simulation/control",
                        headers=headers,
                        json={"command": "SET_SPEED", "rate": 10},
                    ).status_code
                    == 200
                )
                tracemalloc.start()
                cpu_start = time.process_time()
                wall_start = time.perf_counter()
                time.sleep(1.2)
                current = dispatcher.get("/api/dispatcher/snapshot").json()
                moving = [train for train in current["trains"] if train["speedMps"] > 0]
                assert len(moving) >= 2
                position_events = [
                    event
                    for event in current["events"]
                    if event["type"] == "train.position.updated"
                ]
                assert len(position_events) >= 2  # 250 ms runtime tick comfortably exceeds 1 Hz
                samples = []
                for _ in range(20):
                    started = time.perf_counter()
                    response = dispatcher.get("/api/dispatcher/snapshot")
                    assert response.status_code == 200
                    samples.append((time.perf_counter() - started) * 1000)
                p95_snapshot_ms = sorted(samples)[18]
                assert p95_snapshot_ms < 500
                closures = []
                for code in ("02", "04"):
                    track = next(track for track in current["tracks"] if track["code"] == code)
                    started = time.perf_counter()
                    response = dispatcher.post(
                        "/api/dispatcher/incidents",
                        headers=headers,
                        json={
                            "kind": "TRACK_CLOSURE",
                            "severity": "CRITICAL",
                            "summary": f"Load closure {code}",
                            "trackIds": [track["id"]],
                        },
                    )
                    closures.append((time.perf_counter() - started) * 1000)
                    assert response.status_code == 201, response.text
                burst_start = time.perf_counter()

                def create_note(number: int):
                    return dispatcher.post(
                        "/api/dispatcher/incidents",
                        headers=headers,
                        json={"kind": "DISPATCHER_NOTE", "summary": f"Load note {number}"},
                    )

                with ThreadPoolExecutor(max_workers=3) as workers:
                    responses = list(workers.map(create_note, range(10)))
                for response in responses:
                    assert response.status_code == 201, response.text
                burst_ms = (time.perf_counter() - burst_start) * 1000
                assert max(closures) < 3_000
                after = dispatcher.get("/api/dispatcher/snapshot").json()
                assert len([row for row in after["conflicts"] if row["status"] == "OPEN"]) >= 2
                assert len([row for row in after["incidents"] if row["status"] == "OPEN"]) >= 12
                for stream in streams:
                    frame = stream.receive_json()
                    assert frame["messageType"] == "FRAME"
                wall_seconds = time.perf_counter() - wall_start
                cpu_seconds = time.process_time() - cpu_start
                _, peak_bytes = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                print(
                    "PERFORMANCE",
                    {
                        "snapshot_p95_ms": round(p95_snapshot_ms, 1),
                        "replan_max_ms": round(max(closures), 1),
                        "burst_10_ms": round(burst_ms, 1),
                        "cpu_ratio": round(cpu_seconds / wall_seconds, 2),
                        "traced_peak_mib": round(peak_bytes / 1024 / 1024, 1),
                        "position_events": len(position_events),
                        "clients": len(streams),
                    },
                )
                assert statistics.mean(samples) < 500
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()
