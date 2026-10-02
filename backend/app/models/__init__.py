"""Portable SQLAlchemy schema for the station prototype."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Station(Base, IdMixin, TimestampMixin):
    __tablename__ = "stations"
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Qyzylorda")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Yard(Base, IdMixin, TimestampMixin):
    __tablename__ = "yards"
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(160))
    __table_args__ = (UniqueConstraint("station_id", "code"),)


class Node(Base, IdMixin):
    __tablename__ = "nodes"
    yard_id: Mapped[str] = mapped_column(ForeignKey("yards.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(24))
    x: Mapped[float | None]
    y: Mapped[float | None]
    __table_args__ = (
        UniqueConstraint("yard_id", "code"),
        CheckConstraint("kind IN ('approach','junction','buffer','platform_end')"),
    )


class Track(Base, IdMixin, TimestampMixin):
    __tablename__ = "tracks"
    yard_id: Mapped[str] = mapped_column(ForeignKey("yards.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(24))
    length_m: Mapped[int] = mapped_column(Integer)
    max_speed_mps: Mapped[float] = mapped_column(default=25)
    is_electrified: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("yard_id", "code"),
        CheckConstraint("length_m > 0"),
        CheckConstraint("max_speed_mps > 0"),
    )


class Block(Base, IdMixin):
    __tablename__ = "blocks"
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    ordinal: Mapped[int] = mapped_column(Integer)
    length_m: Mapped[int] = mapped_column(Integer)
    availability: Mapped[str] = mapped_column(String(20), default="OPEN", server_default="OPEN")
    closure_reason: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __table_args__ = (
        UniqueConstraint("track_id", "code"),
        UniqueConstraint("track_id", "ordinal"),
        CheckConstraint("length_m > 0"),
    )


class Edge(Base, IdMixin):
    __tablename__ = "edges"
    yard_id: Mapped[str] = mapped_column(ForeignKey("yards.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    from_node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    to_node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    block_id: Mapped[str | None] = mapped_column(ForeignKey("blocks.id"), index=True)
    track_id: Mapped[str | None] = mapped_column(
        ForeignKey("tracks.id", name="fk_edges_track_id_tracks"), index=True
    )
    length_m: Mapped[int] = mapped_column(Integer)
    geometry: Mapped[list[dict[str, float]] | None] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint("yard_id", "code"),
        CheckConstraint("length_m >= 0"),
    )


class Switch(Base, IdMixin):
    __tablename__ = "switches"
    yard_id: Mapped[str] = mapped_column(ForeignKey("yards.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), unique=True)
    code: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(24), default="STRAIGHT")
    movement_state: Mapped[str] = mapped_column(
        String(16), default="SETTLED", server_default="SETTLED"
    )
    is_failed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    __table_args__ = (UniqueConstraint("yard_id", "code"),)


class SwitchTransition(Base, IdMixin):
    __tablename__ = "switch_transitions"
    switch_id: Mapped[str] = mapped_column(
        ForeignKey("switches.id", ondelete="CASCADE"), index=True
    )
    from_edge_id: Mapped[str] = mapped_column(ForeignKey("edges.id"))
    to_edge_id: Mapped[str] = mapped_column(ForeignKey("edges.id"))
    position: Mapped[str] = mapped_column(String(24))
    __table_args__ = (UniqueConstraint("switch_id", "from_edge_id", "to_edge_id"),)


class Signal(Base, IdMixin):
    __tablename__ = "signals"
    yard_id: Mapped[str] = mapped_column(ForeignKey("yards.id", ondelete="CASCADE"), index=True)
    edge_id: Mapped[str] = mapped_column(ForeignKey("edges.id"), unique=True)
    code: Mapped[str] = mapped_column(String(40))
    aspect: Mapped[str] = mapped_column(String(24), default="STOP")
    authority_end_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("nodes.id", name="fk_signals_authority_end_node_id_nodes")
    )
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    __table_args__ = (UniqueConstraint("yard_id", "code"),)


class Platform(Base, IdMixin):
    __tablename__ = "platforms"
    yard_id: Mapped[str] = mapped_column(ForeignKey("yards.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    length_m: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("yard_id", "code"),
        CheckConstraint("length_m > 0"),
    )


class PlatformTrack(Base):
    __tablename__ = "platform_tracks"
    platform_id: Mapped[str] = mapped_column(
        ForeignKey("platforms.id", ondelete="CASCADE"), primary_key=True
    )
    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True
    )


class ConflictZone(Base, IdMixin):
    __tablename__ = "conflict_zones"
    yard_id: Mapped[str] = mapped_column(ForeignKey("yards.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint("yard_id", "code"),)


class EdgeConflictZone(Base):
    __tablename__ = "edge_conflict_zones"
    edge_id: Mapped[str] = mapped_column(
        ForeignKey("edges.id", ondelete="CASCADE"), primary_key=True
    )
    zone_id: Mapped[str] = mapped_column(
        ForeignKey("conflict_zones.id", ondelete="CASCADE"), primary_key=True
    )


class Train(Base, IdMixin, TimestampMixin):
    __tablename__ = "trains"
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(24))
    length_m: Mapped[int] = mapped_column(Integer)
    max_speed_mps: Mapped[float] = mapped_column(default=25)
    status: Mapped[str] = mapped_column(String(24), default="WAITING")
    source: Mapped[str] = mapped_column(String(12), default="WEST")
    destination: Mapped[str] = mapped_column(String(12), default="EAST")
    direction: Mapped[str] = mapped_column(
        String(16), default="EASTBOUND", server_default="EASTBOUND"
    )
    head_edge_id: Mapped[str | None] = mapped_column(
        ForeignKey("edges.id", name="fk_trains_head_edge_id_edges")
    )
    head_offset_m: Mapped[float] = mapped_column(default=0, server_default="0")
    speed_mps: Mapped[float] = mapped_column(default=0, server_default="0")
    target_speed_mps: Mapped[float] = mapped_column(default=0, server_default="0")
    priority: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    __table_args__ = (
        UniqueConstraint("station_id", "code"),
        CheckConstraint("length_m > 0"),
    )


class TrainType(Base, IdMixin):
    __tablename__ = "train_types"
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(120))
    max_length_m: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("station_id", "code"),
        CheckConstraint("max_length_m > 0"),
    )


class SimulationRun(Base, IdMixin):
    __tablename__ = "simulation_runs"
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="STOPPED", server_default="STOPPED")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    simulation_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    clock_rate: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    clock_anchor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    control_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Occupancy(Base, IdMixin):
    __tablename__ = "train_block_occupancy"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    train_id: Mapped[str] = mapped_column(ForeignKey("trains.id", ondelete="CASCADE"), index=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("blocks.id", ondelete="CASCADE"), index=True)
    entered_at_ms: Mapped[int] = mapped_column(Integer)
    released_at_ms: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        Index(
            "uq_current_block_occupancy",
            "run_id",
            "block_id",
            unique=True,
            sqlite_where=(released_at_ms.is_(None)),
            postgresql_where=(released_at_ms.is_(None)),
        ),
    )


class Route(Base, IdMixin):
    __tablename__ = "routes"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    train_id: Mapped[str] = mapped_column(ForeignKey("trains.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="PROPOSED")
    graph_version: Mapped[int] = mapped_column(Integer, default=1)
    failure_reason: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RouteBlock(Base):
    __tablename__ = "route_blocks"
    route_id: Mapped[str] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("blocks.id"), index=True)


class RouteEdge(Base):
    __tablename__ = "route_edges"
    route_id: Mapped[str] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    edge_id: Mapped[str] = mapped_column(ForeignKey("edges.id"), index=True)


class RouteSwitch(Base):
    __tablename__ = "route_switches"
    route_id: Mapped[str] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    switch_id: Mapped[str] = mapped_column(ForeignKey("switches.id"), index=True)
    position: Mapped[str] = mapped_column(String(24))


class RouteSignal(Base):
    __tablename__ = "route_signals"
    route_id: Mapped[str] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), primary_key=True
    )
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id"), primary_key=True)


class Schedule(Base, IdMixin):
    __tablename__ = "schedules"
    train_id: Mapped[str] = mapped_column(ForeignKey("trains.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="SET NULL"), index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(20))
    platform_id: Mapped[str | None] = mapped_column(ForeignKey("platforms.id", ondelete="SET NULL"))
    delay_seconds: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    __table_args__ = (CheckConstraint("event_type IN ('ARRIVAL','DEPARTURE')"),)


class ScheduleEvent(Base, IdMixin):
    __tablename__ = "schedule_events"
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), index=True
    )
    actual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="PLANNED")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Operation(Base, IdMixin):
    __tablename__ = "operations"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    train_id: Mapped[str] = mapped_column(ForeignKey("trains.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="PLANNED")
    starts_at_ms: Mapped[int] = mapped_column(Integer)
    ends_at_ms: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    release_buffer_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    track_id: Mapped[str | None] = mapped_column(
        ForeignKey("tracks.id", name="fk_operations_track_id_tracks", ondelete="SET NULL")
    )
    platform_id: Mapped[str | None] = mapped_column(
        ForeignKey("platforms.id", name="fk_operations_platform_id_platforms", ondelete="SET NULL")
    )
    locomotive_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "locomotives.id", name="fk_operations_locomotive_id_locomotives", ondelete="SET NULL"
        )
    )
    crew_id: Mapped[str | None] = mapped_column(
        ForeignKey("crews.id", name="fk_operations_crew_id_crews", ondelete="SET NULL")
    )
    priority: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ResourceReservation(Base, IdMixin):
    __tablename__ = "resource_reservations"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    resource_type: Mapped[str] = mapped_column(String(24))
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    starts_at_ms: Mapped[int] = mapped_column(Integer)
    ends_at_ms: Mapped[int] = mapped_column(Integer)
    owner_train_id: Mapped[str | None] = mapped_column(ForeignKey("trains.id", ondelete="SET NULL"))
    owner_route_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "routes.id", name="fk_resource_reservations_owner_route_id_routes", ondelete="CASCADE"
        ),
        index=True,
    )
    owner_operation_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "operations.id",
            name="fk_resource_reservations_owner_operation_id_operations",
            ondelete="CASCADE",
        ),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(20), default="HELD")
    __table_args__ = (
        CheckConstraint("ends_at_ms > starts_at_ms", name="ck_resource_reservations_interval"),
        Index(
            "uq_active_resource_reservation",
            "run_id",
            "resource_type",
            "resource_id",
            unique=True,
            sqlite_where=(status == "HELD"),
            postgresql_where=(status == "HELD"),
        ),
    )


class MaintenanceWindow(Base, IdMixin):
    __tablename__ = "maintenance_windows"
    yard_id: Mapped[str] = mapped_column(ForeignKey("yards.id", ondelete="CASCADE"), index=True)
    resource_type: Mapped[str] = mapped_column(String(24))
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(240))
    __table_args__ = (CheckConstraint("ends_at > starts_at"),)


class Locomotive(Base, IdMixin):
    __tablename__ = "locomotives"
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="AVAILABLE")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("station_id", "code"),)


class Crew(Base, IdMixin):
    __tablename__ = "crews"
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="AVAILABLE")
    qualifications: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("station_id", "code"),)


class Conflict(Base, IdMixin):
    __tablename__ = "conflicts"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    detected_at_ms: Mapped[int] = mapped_column(Integer)
    resolved_at_ms: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("run_id", "fingerprint", name="uq_open_conflict_fingerprint"),
    )


class Incident(Base, IdMixin):
    __tablename__ = "incidents"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), default="INFO", server_default="INFO")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    source: Mapped[str | None] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OptimizationPlan(Base, IdMixin):
    __tablename__ = "optimization_plans"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    base_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED")
    calculation_time_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    objective: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OptimizationVariant(Base, IdMixin):
    __tablename__ = "optimization_variants"
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("optimization_plans.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(default=0)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("plan_id", "ordinal"),)


class AIRecommendation(Base, IdMixin):
    __tablename__ = "ai_recommendations"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("optimization_plans.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(80))
    explanation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Role(Base, IdMixin):
    __tablename__ = "roles"
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100))


class Permission(Base, IdMixin):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(200))


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[str] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class Session(Base, IdMixin):
    __tablename__ = "sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    access_token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    csrf_token: Mapped[str] = mapped_column(String(64))


class SessionRefreshToken(Base):
    __tablename__ = "session_refresh_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CsrfContext(Base, IdMixin):
    __tablename__ = "csrf_contexts"
    context_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    dimension: Mapped[str] = mapped_column(String(16), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StationMetric(Base, IdMixin):
    __tablename__ = "station_metrics"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    recorded_at_ms: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column()
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EfficiencyHistory(Base, IdMixin):
    __tablename__ = "efficiency_history"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    recorded_at_ms: Mapped[int] = mapped_column(Integer, index=True)
    score: Mapped[float] = mapped_column()
    components: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SimulationEvent(Base, IdMixin):
    __tablename__ = "simulation_events"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    state_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    simulation_time_ms: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)


class StateSnapshot(Base, IdMixin):
    __tablename__ = "state_snapshots"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), index=True
    )
    simulation_time_ms: Mapped[int] = mapped_column(Integer, index=True)
    state_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)


class AuditLog(Base, IdMixin):
    __tablename__ = "audit_logs"
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), index=True)
    username: Mapped[str | None] = mapped_column(String(80))
    role: Mapped[str | None] = mapped_column(String(40))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    state_version: Mapped[int | None] = mapped_column(Integer)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(20))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CommandResult(Base, IdMixin):
    __tablename__ = "command_results"
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(128))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("station_id", "idempotency_key"),)


class OutboxEvent(Base, IdMixin):
    __tablename__ = "outbox_events"
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class SystemSetting(Base, IdMixin):
    __tablename__ = "system_settings"
    station_id: Mapped[str] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("station_id", "key"),)


__all__ = ["Base"]
