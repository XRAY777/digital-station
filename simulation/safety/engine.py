from __future__ import annotations

from dataclasses import dataclass

from simulation.graph.model import StationGraph
from simulation.routes.engine import RoutePlan


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    allowed: bool
    reasons: tuple[str, ...]
    blocked_resource_ids: tuple[str, ...] = ()


def validate_route(
    graph: StationGraph,
    plan: RoutePlan,
    train_id: str,
    *,
    occupied_by_block: dict[str, str],
    reserved_by_block: dict[str, str],
    reserved_zones: dict[str, str],
    switch_locks: dict[str, str],
    switch_failures: frozenset[str],
) -> SafetyDecision:
    reasons: list[str] = []
    blocked: list[str] = []
    for block_id in plan.block_ids:
        block = graph.blocks.get(block_id)
        if block is None or block.availability != "OPEN":
            reasons.append("BLOCK_UNAVAILABLE")
            blocked.append(block_id)
        owner = occupied_by_block.get(block_id)
        if owner is not None and owner != train_id:
            reasons.append("BLOCK_OCCUPIED")
            blocked.append(block_id)
        owner = reserved_by_block.get(block_id)
        if owner is not None and owner != train_id:
            reasons.append("BLOCK_RESERVED")
            blocked.append(block_id)
    for zone_id in plan.conflict_zone_ids:
        owner = reserved_zones.get(zone_id)
        if owner is not None and owner != train_id:
            reasons.append("CONFLICT_ZONE_RESERVED")
            blocked.append(zone_id)
    for switch_id, position in plan.switch_positions:
        if switch_id in switch_failures:
            reasons.append("SWITCH_FAILED")
            blocked.append(switch_id)
        lock_owner = switch_locks.get(switch_id)
        if lock_owner is not None and lock_owner != train_id:
            reasons.append("SWITCH_LOCKED")
            blocked.append(switch_id)
        current = graph.switch_positions.get(switch_id)
        if current is not None and current != position:
            pass  # The request will command the switch only after all resources are secured.
    return SafetyDecision(not reasons, tuple(dict.fromkeys(reasons)), tuple(dict.fromkeys(blocked)))


def validate_switch_move(
    switch_id: str,
    target_position: str,
    *,
    allowed_positions: frozenset[str],
    active_route_locks: frozenset[str],
    occupied_switch_ids: frozenset[str],
    failed_switch_ids: frozenset[str],
) -> SafetyDecision:
    reasons = []
    if target_position not in allowed_positions:
        reasons.append("SWITCH_POSITION_INVALID")
    if switch_id in active_route_locks:
        reasons.append("SWITCH_ROUTE_LOCKED")
    if switch_id in occupied_switch_ids:
        reasons.append("SWITCH_OCCUPIED_BY_TRAIN")
    if switch_id in failed_switch_ids:
        reasons.append("SWITCH_FAILED")
    return SafetyDecision(not reasons, tuple(reasons), (switch_id,) if reasons else ())
