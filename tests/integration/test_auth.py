from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from backend.app.api.history import _build_pdf, _safe_csv_cell
from backend.app.auth.service import password_hash
from backend.app.main import app
from backend.app.models import (
    AuditLog,
    Base,
    Role,
    SimulationEvent,
    SimulationRun,
    Train,
    User,
    UserRole,
)
from database.connection import make_engine, make_session_factory
from database.seed.demo import seed_demo
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool


def run(coroutine) -> None:
    asyncio.run(coroutine)


def setup_auth_app(database_url: str | None = None) -> tuple[FastAPI, object, object]:
    previous_engine = app.state.db_engine
    engine = (
        make_engine(database_url)
        if database_url
        else create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        seed_demo(db)
        observer = User(
            username="observer",
            password_hash=password_hash.hash("Correct horse battery 17!"),
        )
        db.add(observer)
        db.flush()
        role = db.scalar(select(Role).where(Role.code == "OBSERVER"))
        assert role is not None
        db.add(UserRole(user_id=observer.id, role_id=role.id))
        admin = User(
            username="admin",
            password_hash=password_hash.hash("Administrator password 88!"),
        )
        db.add(admin)
        db.flush()
        admin_role = db.scalar(select(Role).where(Role.code == "ADMIN"))
        assert admin_role is not None
        db.add(UserRole(user_id=admin.id, role_id=admin_role.id))
        db.commit()
    app.state.db_engine = engine
    return app, engine, previous_engine


def test_observer_login_refresh_replay_and_rbac() -> None:
    target_app, engine, previous_engine = setup_auth_app()

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=target_app),
            base_url="http://testserver",
        ) as client:
            origin = {"Origin": "http://testserver"}
            csrf_response = await client.get("/api/auth/csrf")
            csrf = csrf_response.json()["csrfToken"]
            login = await client.post(
                "/api/auth/login",
                headers={**origin, "X-CSRF-Token": csrf},
                json={"username": "observer", "password": "Correct horse battery 17!"},
            )
            assert login.status_code == 200
            assert "httponly" in login.headers["set-cookie"].lower()
            assert login.json()["user"]["roles"] == ["OBSERVER"]
            csrf = login.json()["csrfToken"]
            unsafe_route = await client.post(
                "/api/routes/forged-route/reserve",
                headers={**origin, "X-CSRF-Token": csrf},
                json={"blockIds": ["forged-block"], "signalAspect": "CLEAR"},
            )
            assert unsafe_route.status_code == 403
            snapshot = (await client.get("/api/dispatcher/snapshot")).json()
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            unsafe_switch = await client.post(
                f"/api/switches/{snapshot['topology']['switches'][0]['id']}/position",
                headers={**origin, "X-CSRF-Token": csrf},
                json={"position": "NORMAL"},
            )
            assert unsafe_switch.status_code == 403
            assert (await client.get("/api/auth/me")).status_code == 200

            denied = await client.post(
                "/api/users",
                headers={**origin, "X-CSRF-Token": csrf},
                json={
                    "username": "newdispatcher",
                    "password": "Strong dispatcher password 42!",
                    "role": "DISPATCHER",
                },
            )
            assert denied.status_code == 403

            old_refresh = client.cookies.get("station_refresh")
            refreshed = await client.post(
                "/api/auth/refresh",
                headers={**origin, "X-CSRF-Token": csrf},
            )
            assert refreshed.status_code == 200
            assert client.cookies.get("station_refresh") != old_refresh
            assert (await client.get("/api/auth/me")).status_code == 200

            client.cookies.set("station_refresh", old_refresh, path="/api/auth")
            replay = await client.post(
                "/api/auth/refresh",
                headers={**origin, "X-CSRF-Token": refreshed.json()["csrfToken"]},
            )
            assert replay.status_code == 401
            assert (await client.get("/api/auth/me")).status_code == 401

    try:
        run(exercise())
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()


def test_login_requires_origin_and_csrf_and_rate_limits_failures() -> None:
    target_app, engine, previous_engine = setup_auth_app()

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=target_app),
            base_url="http://testserver",
        ) as client:
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            bad_origin = await client.post(
                "/api/auth/login",
                headers={"Origin": "https://attacker.example", "X-CSRF-Token": csrf},
                json={"username": "observer", "password": "Correct horse battery 17!"},
            )
            assert bad_origin.status_code == 403
            missing_csrf = await client.post(
                "/api/auth/login",
                headers={"Origin": "http://testserver"},
                json={"username": "observer", "password": "Correct horse battery 17!"},
            )
            assert missing_csrf.status_code == 403

            for _ in range(5):
                failed = await client.post(
                    "/api/auth/login",
                    headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
                    json={"username": "observer", "password": "Incorrect password 123!"},
                )
                assert failed.status_code == 401
            limited = await client.post(
                "/api/auth/login",
                headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
                json={"username": "observer", "password": "Correct horse battery 17!"},
            )
            assert limited.status_code == 429
            assert "retry-after" in limited.headers

    try:
        run(exercise())
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()


def test_admin_user_management_and_logout() -> None:
    target_app, engine, previous_engine = setup_auth_app()

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=target_app),
            base_url="http://testserver",
        ) as client:
            origin = {"Origin": "http://testserver"}
            preauth_csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            login = await client.post(
                "/api/auth/login",
                headers={**origin, "X-CSRF-Token": preauth_csrf},
                json={"username": "admin", "password": "Administrator password 88!"},
            )
            assert login.status_code == 200
            csrf = login.json()["csrfToken"]
            topology = await client.get("/api/station/topology")
            assert topology.status_code == 200
            assert len(topology.json()["tracks"]) == 10
            assert len(topology.json()["edges"]) == 64
            blocks = await client.get("/api/blocks")
            assert len(blocks.json()["items"]) == 20
            trains = await client.get("/api/trains")
            parked = next(item for item in trains.json()["items"] if item["code"] == "П-303")
            assert parked["lengthM"] == 250
            assert len(parked["currentBlockIds"]) == 1
            assert (await client.get("/api/switches")).status_code == 200
            assert (await client.get("/api/signals")).status_code == 200

            initial_clock = (await client.get("/api/simulation")).json()
            assert initial_clock["status"] == "STOPPED"
            for command, rate in (
                ("SET_SPEED", 5),
                ("START", None),
                ("PAUSE", None),
                ("RESUME", None),
                ("STOP", None),
                ("START", None),
                ("STOP", None),
                ("RESET", None),
            ):
                body = {"command": command}
                if rate is not None:
                    body["rate"] = rate
                clock_response = await client.post(
                    "/api/simulation/control",
                    headers={**origin, "X-CSRF-Token": csrf},
                    json=body,
                )
                assert clock_response.status_code == 200
            reset_clock = clock_response.json()
            assert reset_clock["runId"] != initial_clock["runId"]
            assert reset_clock["status"] == "STOPPED"
            assert reset_clock["simulationTimeMs"] == 0
            assert (await client.get("/api/blocks")).json()["items"]

            listing = await client.get("/api/users")
            assert listing.status_code == 200
            assert len(listing.json()["items"]) == 2
            created = await client.post(
                "/api/users",
                headers={**origin, "X-CSRF-Token": csrf},
                json={
                    "username": "dispatcher",
                    "password": "Dispatch password 173!",
                    "role": "DISPATCHER",
                },
            )
            assert created.status_code == 201
            assert created.json()["roles"] == ["DISPATCHER"]
            logout = await client.post("/api/auth/logout", headers={**origin, "X-CSRF-Token": csrf})
            assert logout.status_code == 204
            assert (await client.get("/api/auth/me")).status_code == 401
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            dispatcher_login = await client.post(
                "/api/auth/login",
                headers={**origin, "X-CSRF-Token": csrf},
                json={"username": "dispatcher", "password": "Dispatch password 173!"},
            )
            assert dispatcher_login.status_code == 200
            csrf = dispatcher_login.json()["csrfToken"]
            forbidden_users = await client.post(
                "/api/users",
                headers={**origin, "X-CSRF-Token": csrf},
                json={
                    "username": "anotheruser",
                    "password": "Another password 173!",
                    "role": "OBSERVER",
                },
            )
            assert forbidden_users.status_code == 403

    try:
        run(exercise())
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()


def test_observer_can_read_dispatcher_snapshot_but_cannot_mutate_incidents() -> None:
    target_app, engine, previous_engine = setup_auth_app()

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=target_app), base_url="http://testserver"
        ) as client:
            origin = {"Origin": "http://testserver"}
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            login = await client.post(
                "/api/auth/login",
                headers={**origin, "X-CSRF-Token": csrf},
                json={"username": "observer", "password": "Correct horse battery 17!"},
            )
            assert login.status_code == 200
            snapshot = await client.get("/api/dispatcher/snapshot")
            assert snapshot.status_code == 200
            assert "OBSERVER" in snapshot.json()["user"]["roles"]
            assert len(snapshot.json()["topology"]["tracks"]) == 10
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            mutation = await client.post(
                "/api/dispatcher/incidents",
                headers={**origin, "X-CSRF-Token": csrf},
                json={"kind": "DISPATCHER_NOTE", "summary": "Observer attempt"},
            )
            assert mutation.status_code == 403
            assert (await client.get("/api/dispatcher/audit")).status_code == 403
            with make_session_factory(engine)() as db:
                denied = db.scalar(select(AuditLog).where(AuditLog.action == "ACCESS_DENIED"))
                assert denied is not None
                assert denied.username == "observer"
                assert denied.result == "DENIED"

    try:
        asyncio.run(exercise())
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()


def test_admin_incident_closure_replans_and_exports_server_reports() -> None:
    target_app, engine, previous_engine = setup_auth_app()

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=target_app), base_url="http://testserver"
        ) as client:
            origin = {"Origin": "http://testserver"}
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            login = await client.post(
                "/api/auth/login",
                headers={**origin, "X-CSRF-Token": csrf},
                json={"username": "admin", "password": "Administrator password 88!"},
            )
            assert login.status_code == 200
            snapshot = (await client.get("/api/dispatcher/snapshot")).json()
            track_id = snapshot["tracks"][0]["id"]
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            created = await client.post(
                "/api/dispatcher/incidents",
                headers={**origin, "X-CSRF-Token": csrf},
                json={
                    "kind": "TRACK_CLOSURE",
                    "severity": "CRITICAL",
                    "summary": "Inspection",
                    "trackIds": [track_id],
                },
            )
            assert created.status_code == 201, created.text
            payload = created.json()
            assert payload["replanning"]["incidentId"] == payload["id"]
            assert payload["replanning"]["newPlanId"]
            assert payload["severity"] == "CRITICAL"
            assert payload["createdAt"] and payload["resolvedAt"] is None
            after = (await client.get("/api/dispatcher/snapshot")).json()
            assert (
                next(track for track in after["tracks"] if track["id"] == track_id)["status"]
                == "BLOCKED"
            )
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            resolved = await client.post(
                f"/api/dispatcher/incidents/{payload['id']}/resolve",
                headers={**origin, "X-CSRF-Token": csrf},
            )
            assert resolved.status_code == 200
            assert resolved.json()["resolvedAt"]
            after_resolve = (await client.get("/api/dispatcher/snapshot")).json()
            assert (
                next(track for track in after_resolve["tracks"] if track["id"] == track_id)[
                    "status"
                ]
                != "BLOCKED"
            )
            historical = await client.get(
                "/api/dispatcher/replay",
                params={"timeMs": after_resolve["simulation"]["simulationTimeMs"]},
            )
            assert historical.status_code == 200
            assert historical.json()["state"]["incidents"][payload["id"]]["status"] == "RESOLVED"
            now = datetime.now(UTC)
            params = {
                "from": (now - timedelta(days=2)).isoformat(),
                "to": (now + timedelta(days=2)).isoformat(),
            }
            csv_response = await client.get("/api/dispatcher/reports.csv", params=params)
            assert csv_response.status_code == 200
            assert csv_response.content.startswith(b"\xef\xbb\xbf")
            pdf_response = await client.get("/api/dispatcher/reports.pdf", params=params)
            assert pdf_response.status_code == 200
            assert pdf_response.content.startswith(b"%PDF-1.4")
            assert pdf_response.content.endswith(b"%%EOF")
            assert b"DEMO-01" in pdf_response.content
            audit = await client.get("/api/dispatcher/audit", params={"action": "INCIDENT_CREATED"})
            assert audit.status_code == 200
            entry = audit.json()["items"][0]
            assert entry["username"] == "admin"
            assert entry["newValue"]
            assert entry["result"] == "SUCCESS"

    try:
        asyncio.run(exercise())
        assert _safe_csv_cell("=test").startswith("'")
        document = _build_pdf(["STATION REPORT", "real database data"])
        assert b"STATION REPORT" in document and b"xref" in document
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()


def test_replay_reduces_history_without_changing_live_run() -> None:
    target_app, engine, previous_engine = setup_auth_app()

    async def exercise() -> None:
        with make_session_factory(engine)() as db:
            run_row = db.scalar(select(SimulationRun))
            train = db.scalar(select(Train))
            assert run_row is not None and train is not None
            run_row.simulation_time_ms = 300_000
            run_row.state_version += 1
            db.add(
                SimulationEvent(
                    run_id=run_row.id,
                    sequence=1,
                    schema_version=1,
                    state_version=run_row.state_version,
                    simulation_time_ms=240_000,
                    event_type="train.position.updated",
                    entity_type="train",
                    entity_id=train.id,
                    payload={
                        "trainId": train.id,
                        "headEdgeId": "historical-edge",
                        "headOffsetM": 321,
                        "speedMps": 12,
                        "blockIds": [],
                    },
                )
            )
            run_id = run_row.id
            live_version = run_row.state_version
            db.commit()
        async with AsyncClient(
            transport=ASGITransport(app=target_app), base_url="http://testserver"
        ) as client:
            origin = {"Origin": "http://testserver"}
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            login = await client.post(
                "/api/auth/login",
                headers={**origin, "X-CSRF-Token": csrf},
                json={"username": "admin", "password": "Administrator password 88!"},
            )
            assert login.status_code == 200
            replay = await client.get("/api/dispatcher/replay", params={"timeMs": 240_000})
            assert replay.status_code == 200, replay.text
            payload = replay.json()
            assert payload["mode"] == "REPLAY"
            assert payload["state"]["trains"][train.id]["headOffsetM"] == 321
            assert payload["stateVersion"] == live_version
        with make_session_factory(engine)() as db:
            unchanged = db.get(SimulationRun, run_id)
            assert unchanged is not None
            assert unchanged.simulation_time_ms == 300_000
            assert unchanged.state_version == live_version

    try:
        asyncio.run(exercise())
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()


def test_multiple_incidents_each_replan_and_keep_station_consistent() -> None:
    target_app, engine, previous_engine = setup_auth_app()

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=target_app), base_url="http://testserver"
        ) as client:
            origin = {"Origin": "http://testserver"}
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            login = await client.post(
                "/api/auth/login",
                headers={**origin, "X-CSRF-Token": csrf},
                json={"username": "admin", "password": "Administrator password 88!"},
            )
            assert login.status_code == 200
            snapshot = (await client.get("/api/dispatcher/snapshot")).json()
            incidents = [
                {
                    "kind": "TRAIN_DELAY",
                    "severity": "WARNING",
                    "summary": "Late train",
                    "trainIds": [snapshot["trains"][0]["id"]],
                    "metadata": {"delaySeconds": 120},
                },
                {
                    "kind": "SWITCH_FAILURE",
                    "severity": "CRITICAL",
                    "summary": "Switch inspection",
                    "metadata": {"switchId": snapshot["topology"]["switches"][0]["id"]},
                },
            ]
            plans = []
            for incident in incidents:
                csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
                response = await client.post(
                    "/api/dispatcher/incidents",
                    headers={**origin, "X-CSRF-Token": csrf},
                    json=incident,
                )
                assert response.status_code == 201, response.text
                result = response.json()["replanning"]
                assert result["newPlanId"] and result["incidentId"]
                plans.append(result["newPlanId"])
            after = (await client.get("/api/dispatcher/snapshot")).json()
            assert len(set(plans)) == 2
            assert len([row for row in after["incidents"] if row["status"] == "OPEN"]) == 2
            assert after["simulation"]["stateVersion"] >= snapshot["simulation"]["stateVersion"] + 2

    try:
        asyncio.run(exercise())
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()


def test_incident_command_is_idempotent_with_reused_key() -> None:
    target_app, engine, previous_engine = setup_auth_app()

    async def exercise() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=target_app), base_url="http://testserver"
        ) as client:
            origin = {"Origin": "http://testserver"}
            csrf = (await client.get("/api/auth/csrf")).json()["csrfToken"]
            login = await client.post(
                "/api/auth/login",
                headers={**origin, "X-CSRF-Token": csrf},
                json={"username": "admin", "password": "Administrator password 88!"},
            )
            assert login.status_code == 200
            csrf = login.json()["csrfToken"]
            headers = {**origin, "X-CSRF-Token": csrf, "Idempotency-Key": "incident-note-001"}
            body = {"kind": "DISPATCHER_NOTE", "severity": "INFO", "summary": "Single apply"}
            first = await client.post("/api/dispatcher/incidents", headers=headers, json=body)
            assert first.status_code == 201
            replay = await client.post("/api/dispatcher/incidents", headers=headers, json=body)
            assert replay.status_code == 201
            assert replay.headers["idempotency-replayed"] == "true"
            assert replay.json()["id"] == first.json()["id"]
            changed_payload = await client.post(
                "/api/dispatcher/incidents",
                headers=headers,
                json={**body, "summary": "Different payload"},
            )
            assert changed_payload.status_code == 409
            snapshot = (await client.get("/api/dispatcher/snapshot")).json()
            assert sum(row["kind"] == "DISPATCHER_NOTE" for row in snapshot["incidents"]) == 1

    try:
        asyncio.run(exercise())
    finally:
        app.state.db_engine = previous_engine
        engine.dispose()
