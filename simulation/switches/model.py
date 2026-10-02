from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SwitchState:
    id: str
    code: str
    node_id: str
    position: str
    allowed_positions: frozenset[str]
    movement_state: str
    is_failed: bool

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.position not in self.allowed_positions:
            errors.append("switch_position_not_allowed")
        if self.is_failed and self.movement_state == "MOVING":
            errors.append("failed_switch_cannot_move")
        return errors
