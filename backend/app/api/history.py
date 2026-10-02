from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from backend.app.auth.dependencies import get_db_session, require_permission
from backend.app.auth.service import make_user_view
from backend.app.models import (
    AIRecommendation,
    AuditLog,
    Conflict,
    Crew,
    EfficiencyHistory,
    Incident,
    Locomotive,
    OptimizationPlan,
    Platform,
    ResourceReservation,
    Schedule,
    ScheduleEvent,
    SimulationEvent,
    StateSnapshot,
    Track,
    Train,
    User,
    Yard,
)
from backend.app.services.state_history import history_retention_hours
from backend.app.services.station_state import demo_station, latest_run

router = APIRouter(prefix="/api/dispatcher", tags=["history-reports"])
HistoryAccess = Annotated[User, Depends(require_permission("history.read"))]
ReportAccess = Annotated[User, Depends(require_permission("reports.read"))]


def _replay_reduce(state: dict[str, Any], event: SimulationEvent) -> None:
    payload = event.payload
    if event.event_type == "train.position.updated":
        train_id = str(payload.get("trainId", ""))
        trains = state.setdefault("trains", {})
        if train_id in trains:
            trains[train_id] = {
                **trains[train_id],
                "headEdgeId": payload.get("headEdgeId"),
                "headOffsetM": payload.get("headOffsetM", 0),
                "speedMps": payload.get("speedMps", 0),
            }
        rows = state.setdefault("occupancies", [])
        rows[:] = [row for row in rows if row.get("trainId") != train_id]
        rows.extend(
            {"trainId": train_id, "blockId": block_id, "enteredAtMs": event.simulation_time_ms}
            for block_id in payload.get("blockIds", [])
        )
    elif event.event_type == "switch.position.changed":
        switch_id = str(payload.get("switchId", ""))
        switches = state.setdefault("switches", {})
        switches[switch_id] = {"state": payload.get("position"), "isFailed": False}
    elif event.event_type == "train.delay.updated":
        train_id = str(payload.get("trainId", ""))
        trains = state.setdefault("trains", {})
        if train_id in trains:
            trains[train_id] = {
                **trains[train_id],
                "delaySeconds": payload.get(
                    "delaySeconds", trains[train_id].get("delaySeconds", 0)
                ),
            }
    elif event.event_type.startswith("incident."):
        incident_id = str(payload.get("incidentId", ""))
        incidents = state.setdefault("incidents", {})
        if event.event_type == "incident.created":
            incidents[incident_id] = {
                "kind": payload.get("kind"),
                "severity": payload.get("severity", "INFO"),
                "status": "OPEN",
                "payload": payload,
                "source": payload.get("source"),
                "openedAt": event.occurred_at.isoformat(),
                "resolvedAt": None,
            }
            kind = str(payload.get("kind", ""))
            track_ids = set(payload.get("trackIds", []))
            closed_ids = set(payload.get("blockIds", []))
            if kind == "TRACK_CLOSURE":
                for block_id, block in state.setdefault("blocks", {}).items():
                    if block_id in closed_ids or block.get("trackId") in track_ids:
                        state["blocks"][block_id] = {
                            **block,
                            "availability": "BLOCKED",
                            "closureReason": payload.get("description", payload.get("summary")),
                        }
            if kind == "SWITCH_FAILURE":
                for switch_id in payload.get("switchIds", []):
                    old = state.setdefault("switches", {}).get(switch_id, {})
                    state["switches"][switch_id] = {
                        **(old if isinstance(old, dict) else {}),
                        "isFailed": True,
                        "failureReason": payload.get("description", payload.get("summary")),
                    }
        elif incident_id in incidents:
            incidents[incident_id] = {
                **incidents[incident_id],
                "status": "RESOLVED" if event.event_type == "incident.resolved" else "ACKNOWLEDGED",
                "resolvedAt": payload.get("resolvedAt"),
            }
            if event.event_type == "incident.resolved":
                for block_id in payload.get("restoredBlockIds", []):
                    block = state.setdefault("blocks", {}).get(block_id)
                    if block:
                        state["blocks"][block_id] = {
                            **block,
                            "availability": "OPEN",
                            "closureReason": None,
                        }
                for switch_id in payload.get("restoredSwitchIds", []):
                    old = state.setdefault("switches", {}).get(switch_id, {})
                    state["switches"][switch_id] = {
                        **(old if isinstance(old, dict) else {}),
                        "isFailed": False,
                        "failureReason": None,
                    }
    elif event.event_type.startswith("route."):
        route_id = str(payload.get("routeId", ""))
        routes = state.setdefault("routes", {})
        status = payload.get("status") or {
            "route.reserved": "RESERVED",
            "route.cancelled": "CANCELLED",
        }.get(event.event_type)
        if event.event_type == "route.created":
            routes[route_id] = {
                "trainId": payload.get("trainId"),
                "status": status,
                "edgeIds": payload.get("edgeIds", []),
                "blockIds": payload.get("blockIds", []),
                "switchIds": payload.get("switchIds", []),
                "signalIds": payload.get("signalIds", []),
            }
        elif route_id in routes:
            routes[route_id] = {**routes[route_id], "status": status}
        for signal_id, signal_state in payload.get("signalStates", {}).items():
            state.setdefault("signals", {})[signal_id] = signal_state
    elif event.event_type.startswith("conflict."):
        conflict_id = str(payload.get("conflictId", ""))
        conflicts = state.setdefault("conflicts", {})
        if event.event_type == "conflict.resolved":
            if conflict_id in conflicts:
                conflicts[conflict_id] = {**conflicts[conflict_id], "status": "RESOLVED"}
        else:
            conflicts[conflict_id] = {**payload, "detectedAtMs": event.simulation_time_ms}


@router.get("/replay")
def replay_projection(
    user: HistoryAccess,
    db: Annotated[DbSession, Depends(get_db_session)],
    time_ms: Annotated[int, Query(ge=0, alias="timeMs")],
) -> dict[str, object]:
    station = demo_station(db)
    run = latest_run(db, station.id) if station else None
    if run is None or station is None:
        raise HTTPException(status_code=404, detail="simulation_run_missing")
    if time_ms > run.simulation_time_ms:
        raise HTTPException(status_code=422, detail="replay_time_after_live")
    cutoff = max(0, run.simulation_time_ms - history_retention_hours() * 3_600_000)
    if time_ms < cutoff:
        raise HTTPException(status_code=416, detail="replay_outside_retention")
    snapshot = db.scalar(
        select(StateSnapshot)
        .where(StateSnapshot.run_id == run.id, StateSnapshot.simulation_time_ms <= time_ms)
        .order_by(StateSnapshot.simulation_time_ms.desc())
        .limit(1)
    )
    if snapshot is None:
        raise HTTPException(status_code=416, detail="replay_snapshot_unavailable")
    state = dict(snapshot.state)
    # Older snapshots lack empty collections; never leak live records backward.
    for key in ("trains", "switches", "signals", "blocks", "routes", "conflicts", "incidents"):
        state.setdefault(key, {})
    state.setdefault("occupancies", [])
    events = db.scalars(
        select(SimulationEvent)
        .where(
            SimulationEvent.run_id == run.id,
            SimulationEvent.simulation_time_ms <= time_ms,
            (SimulationEvent.simulation_time_ms > snapshot.simulation_time_ms)
            | (
                (SimulationEvent.simulation_time_ms == snapshot.simulation_time_ms)
                & (SimulationEvent.state_version > snapshot.state_version)
            ),
        )
        .order_by(SimulationEvent.sequence)
    ).all()
    for event in events:
        _replay_reduce(state, event)
    efficiency = db.scalar(
        select(EfficiencyHistory)
        .where(EfficiencyHistory.run_id == run.id, EfficiencyHistory.recorded_at_ms <= time_ms)
        .order_by(EfficiencyHistory.recorded_at_ms.desc())
        .limit(1)
    )
    state["efficiency"] = (
        {"score": efficiency.score, **efficiency.components}
        if efficiency
        else state.get("efficiency") or {"score": 0}
    )
    return {
        "mode": "REPLAY",
        "stationId": station.id,
        "runId": run.id,
        "timeMs": time_ms,
        "stateVersion": max(
            (event.state_version for event in events), default=snapshot.state_version
        ),
        "state": state,
        "eventsApplied": len(events),
        "liveTimeMs": run.simulation_time_ms,
    }


@router.get("/audit")
def audit_log(
    user: HistoryAccess,
    db: Annotated[DbSession, Depends(get_db_session)],
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    user_id: Annotated[str | None, Query(alias="userId")] = None,
    action: str | None = None,
    entity: str | None = None,
    result: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, object]:
    station = demo_station(db)
    if station is None:
        raise HTTPException(status_code=404, detail="station_not_seeded")
    roles = set(make_user_view(db, user).roles)
    if not roles.intersection({"ADMIN", "DISPATCHER"}):
        raise HTTPException(status_code=403, detail="audit_access_denied")
    stmt = select(AuditLog).where(AuditLog.station_id == station.id)
    if from_time:
        stmt = stmt.where(AuditLog.occurred_at >= from_time)
    if to_time:
        stmt = stmt.where(AuditLog.occurred_at <= to_time)
    if user_id:
        stmt = stmt.where(AuditLog.actor_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity:
        stmt = stmt.where(AuditLog.entity_type == entity)
    if result:
        stmt = stmt.where(AuditLog.result == result)
    rows = db.scalars(stmt.order_by(AuditLog.occurred_at.desc()).limit(limit)).all()
    if "ADMIN" not in roles:
        allowed = {
            "ROUTE_CREATED",
            "ROUTE_RESERVED",
            "ROUTE_CANCELLED",
            "SWITCH_CHANGED",
            "INCIDENT_CREATED",
            "INCIDENT_ACKNOWLEDGED",
            "INCIDENT_RESOLVED",
            "PLAN_ACCEPTED",
            "AI_RECOMMENDATION_ACCEPTED",
        }
        rows = [row for row in rows if row.action in allowed]
    return {
        "items": [
            {
                "id": row.id,
                "timestamp": row.occurred_at.isoformat(),
                "userId": row.actor_id,
                "username": row.username,
                "role": row.role,
                "action": row.action,
                "entityType": row.entity_type,
                "entityId": row.entity_id,
                "oldValue": row.old_value,
                "newValue": row.new_value,
                "requestId": row.request_id,
                "sessionId": row.session_id,
                "ip": row.ip_address,
                "stateVersion": row.state_version,
                "result": row.result,
            }
            for row in rows
        ]
    }


def _report_data(db: DbSession, start: datetime, end: datetime) -> dict[str, Any]:
    station = demo_station(db)
    run = latest_run(db, station.id) if station else None
    if station is None or run is None:
        raise HTTPException(status_code=404, detail="simulation_run_missing")
    schedules = db.scalars(
        select(Schedule).where(
            Schedule.run_id == run.id, Schedule.scheduled_at >= start, Schedule.scheduled_at <= end
        )
    ).all()
    trains = db.scalars(select(Train).where(Train.station_id == station.id)).all()
    delays = [row.delay_seconds for row in schedules]
    schedule_ids = [row.id for row in schedules]
    actual_events = (
        db.scalars(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id.in_(schedule_ids), ScheduleEvent.status == "COMPLETED"
            )
        ).all()
        if schedule_ids
        else []
    )
    actual_schedule_ids = {row.schedule_id for row in actual_events}
    anchor = (
        run.started_at.replace(tzinfo=start.tzinfo)
        if run.started_at.tzinfo is None
        else run.started_at
    )
    start_ms = max(0, round((start - anchor).total_seconds() * 1000))
    end_ms = max(start_ms, round((end - anchor).total_seconds() * 1000))
    conflicts = db.scalars(
        select(Conflict).where(
            Conflict.run_id == run.id,
            Conflict.detected_at_ms >= start_ms,
            Conflict.detected_at_ms <= end_ms,
        )
    ).all()
    incidents = db.scalars(
        select(Incident).where(
            Incident.run_id == run.id, Incident.opened_at >= start, Incident.opened_at <= end
        )
    ).all()
    plans = db.scalars(
        select(OptimizationPlan).where(
            OptimizationPlan.run_id == run.id,
            OptimizationPlan.status == "ACCEPTED",
            OptimizationPlan.created_at >= start,
            OptimizationPlan.created_at <= end,
        )
    ).all()
    recommendations = db.scalars(
        select(AIRecommendation).where(
            AIRecommendation.run_id == run.id,
            AIRecommendation.created_at >= start,
            AIRecommendation.created_at <= end,
        )
    ).all()
    efficiency = db.scalars(
        select(EfficiencyHistory)
        .where(
            EfficiencyHistory.run_id == run.id,
            EfficiencyHistory.recorded_at_ms >= start_ms,
            EfficiencyHistory.recorded_at_ms <= end_ms,
            EfficiencyHistory.recorded_at_ms <= run.simulation_time_ms,
        )
        .order_by(EfficiencyHistory.recorded_at_ms)
    ).all()
    audit = db.scalars(
        select(AuditLog).where(
            AuditLog.station_id == station.id,
            AuditLog.occurred_at >= start,
            AuditLog.occurred_at <= end,
            AuditLog.action.in_(
                (
                    "EMERGENCY_ACTION",
                    "EMERGENCY_STOP",
                    "TRACK_BLOCKED",
                    "TRACK_UNBLOCKED",
                    "SWITCH_CHANGED",
                    "INCIDENT_CREATED",
                    "INCIDENT_RESOLVED",
                    "PLAN_ACCEPTED",
                    "ROUTE_RESERVED",
                    "ACCESS_DENIED",
                )
            ),
        )
    ).all()
    arrivals = sum(
        row.event_type == "ARRIVAL" and row.id in actual_schedule_ids for row in schedules
    )
    departures = sum(
        row.event_type == "DEPARTURE" and row.id in actual_schedule_ids for row in schedules
    )
    elapsed_hours = max((end - start).total_seconds() / 3600, 1 / 60)
    reservations = db.scalars(
        select(ResourceReservation).where(
            ResourceReservation.run_id == run.id,
            ResourceReservation.status.in_(("PLANNED", "ACTIVE", "HELD")),
            ResourceReservation.starts_at_ms < end_ms,
            ResourceReservation.ends_at_ms > start_ms,
        )
    ).all()
    interval_ms = max(1, end_ms - start_ms)
    tracks_count = max(
        1, len(db.scalars(select(Track.id).join(Yard).where(Yard.station_id == station.id)).all())
    )
    platforms_count = max(
        1,
        len(
            db.scalars(
                select(Platform.id)
                .join(Yard, Platform.yard_id == Yard.id)
                .where(Yard.station_id == station.id)
            ).all()
        ),
    )

    def used_ms(resource_type: str) -> int:
        return sum(
            max(0, min(row.ends_at_ms, end_ms) - max(row.starts_at_ms, start_ms))
            for row in reservations
            if row.resource_type == resource_type
        )

    resource_count = max(
        1,
        len(db.scalars(select(Locomotive.id).where(Locomotive.station_id == station.id)).all())
        + len(db.scalars(select(Crew.id).where(Crew.station_id == station.id)).all()),
    )
    return {
        "station": station,
        "run": run,
        "periodStart": start,
        "periodEnd": end,
        "trainCount": len(trains),
        "arrivals": arrivals,
        "departures": departures,
        "averageDelaySeconds": sum(delays) / len(delays) if delays else 0,
        "maximumDelaySeconds": max(delays, default=0),
        "throughput": (arrivals + departures) / elapsed_hours,
        "trackUtilization": min(100.0, 100 * used_ms("TRACK") / (interval_ms * tracks_count)),
        "platformUtilization": min(
            100.0, 100 * used_ms("PLATFORM") / (interval_ms * platforms_count)
        ),
        "resourceIdleTime": 100
        - min(
            100.0, 100 * (used_ms("LOCOMOTIVE") + used_ms("CREW")) / (interval_ms * resource_count)
        ),
        "conflictCount": len(conflicts),
        "incidentCount": len(incidents),
        "efficiency": efficiency[-1].score if efficiency else 0,
        "efficiencyHistory": [
            {"timeMs": row.recorded_at_ms, "score": row.score} for row in efficiency
        ],
        "plans": [{"id": row.id, "status": row.status} for row in plans],
        "recommendations": [
            {"provider": row.provider, "explanation": row.explanation} for row in recommendations
        ],
        "criticalAudit": [
            {
                "action": row.action,
                "username": row.username,
                "timestamp": row.occurred_at.isoformat(),
            }
            for row in audit
        ],
        "trains": [
            {
                "code": row.code,
                "category": row.category,
                "delaySeconds": max(
                    (s.delay_seconds for s in schedules if s.train_id == row.id), default=0
                ),
            }
            for row in trains
        ],
        "conflicts": [
            {"kind": row.kind, "severity": row.severity, "status": row.status} for row in conflicts
        ],
        "incidents": [
            {
                "kind": row.kind,
                "status": row.status,
                "description": row.payload.get("description", row.payload.get("summary", "")),
            }
            for row in incidents
        ],
    }


def _safe_csv_cell(value: object) -> str:
    text = str(value).replace("\x00", "")
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


@router.get("/reports.csv")
def export_csv(
    _: ReportAccess,
    db: Annotated[DbSession, Depends(get_db_session)],
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
) -> Response:
    if end <= start or end - start > timedelta(days=366):
        raise HTTPException(status_code=422, detail="report_period_invalid")
    data = _report_data(db, start, end)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "Station",
            "Period start",
            "Period end",
            "Trains",
            "Arrivals",
            "Departures",
            "Average delay seconds",
            "Maximum delay seconds",
            "Throughput per hour",
            "Track utilization",
            "Platform utilization",
            "Resource idle time",
            "Conflicts",
            "Incidents",
            "Efficiency",
        ]
    )
    writer.writerow(
        [
            _safe_csv_cell(data["station"].name),
            start.isoformat(),
            end.isoformat(),
            data["trainCount"],
            data["arrivals"],
            data["departures"],
            data["averageDelaySeconds"],
            data["maximumDelaySeconds"],
            data["throughput"],
            data["trackUtilization"],
            data["platformUtilization"],
            data["resourceIdleTime"],
            data["conflictCount"],
            data["incidentCount"],
            data["efficiency"],
        ]
    )
    writer.writerow([])
    writer.writerow(["Train", "Category", "Delay seconds"])
    for train in data["trains"]:
        writer.writerow(
            [
                _safe_csv_cell(train["code"]),
                _safe_csv_cell(train["category"]),
                train["delaySeconds"],
            ]
        )
    writer.writerow([])
    writer.writerow(["Efficiency history", "Simulation time ms", "Score"])
    for point in data["efficiencyHistory"]:
        writer.writerow(["Efficiency", point["timeMs"], point["score"]])
    body = "\ufeff" + output.getvalue()
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="station-report.csv"'},
    )


def _pdf_text(value: object) -> str:
    # Core PDF fonts are limited to WinAnsi; keep ASCII report fields readable.
    return (
        str(value)
        .encode("cp1252", errors="replace")
        .decode("cp1252")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _build_pdf(lines: list[str]) -> bytes:
    content = ["BT", "/F1 10 Tf", "48 790 Td", "14 TL"]
    for line in lines[:48]:
        content.extend([f"({_pdf_text(line)[:150]}) Tj", "T*"])
    content.append("ET")
    stream = "\n".join(content).encode("cp1252", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return bytes(output)


@router.get("/reports.pdf")
def export_pdf(
    _: ReportAccess,
    db: Annotated[DbSession, Depends(get_db_session)],
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
) -> Response:
    if end <= start or end - start > timedelta(days=366):
        raise HTTPException(status_code=422, detail="report_period_invalid")
    data = _report_data(db, start, end)
    lines = [
        "DIGITAL STATION REPORT",
        f"Station: {data['station'].code}",
        f"Period: {start.isoformat()} - {end.isoformat()}",
        "",
        "SUMMARY METRICS",
        f"Trains: {data['trainCount']} | arrivals: {data['arrivals']} "
        f"| departures: {data['departures']}",
        f"Average delay: {data['averageDelaySeconds']:.1f}s "
        f"| maximum: {data['maximumDelaySeconds']}s",
        f"Throughput/hour: {data['throughput']:.2f} "
        f"| track utilization: {data['trackUtilization']:.1f}%",
        f"Platform utilization: {data['platformUtilization']:.1f}% "
        f"| resource idle: {data['resourceIdleTime']:.1f}%",
        f"Conflicts: {data['conflictCount']} | incidents: {data['incidentCount']} "
        f"| efficiency: {data['efficiency']:.1f}",
        "Efficiency trend: "
        + ", ".join(
            f"{row['timeMs']}ms={row['score']:.1f}" for row in data["efficiencyHistory"][-10:]
        ),
        "",
        "TRAINS",
        *[
            f"{row['code']} | {row['category']} | delay {row['delaySeconds']}s"
            for row in data["trains"]
        ],
        "",
        "CONFLICTS",
        *[f"{row['severity']} {row['kind']} ({row['status']})" for row in data["conflicts"]],
        "",
        "INCIDENTS",
        *[f"{row['kind']} ({row['status']}): {row['description']}" for row in data["incidents"]],
        "",
        "APPLIED PLANS",
        *[str(row["id"]) for row in data["plans"]],
        "",
        "AI RECOMMENDATIONS",
        *[str(row["explanation"])[:120] for row in data["recommendations"]],
        "",
        "CRITICAL AUDIT",
        *[f"{row['timestamp']} {row['username']} {row['action']}" for row in data["criticalAudit"]],
    ]
    return Response(
        _build_pdf(lines),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="station-report.pdf"'},
    )
