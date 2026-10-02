from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Direction = Literal["EASTBOUND", "WESTBOUND"]


@dataclass(frozen=True, slots=True)
class TrainState:
    id: str
    code: str
    category: str
    length_m: int
    max_speed_mps: float
    speed_mps: float
    target_speed_mps: float
    direction: Direction
    status: str
    priority: int
    head_edge_id: str | None
    head_offset_m: float
    current_block_ids: tuple[str, ...]
    current_track_id: str | None

    def validate(self, edge_lengths: dict[str, int], block_tracks: dict[str, str]) -> list[str]:
        errors: list[str] = []
        if self.length_m <= 0:
            errors.append("train_length_invalid")
        if not 0 <= self.speed_mps <= self.max_speed_mps:
            errors.append("train_speed_invalid")
        if not 0 <= self.target_speed_mps <= self.max_speed_mps:
            errors.append("train_target_speed_invalid")
        if self.head_edge_id is None:
            if self.head_offset_m != 0:
                errors.append("train_head_edge_missing")
        elif self.head_edge_id not in edge_lengths or not (
            0 <= self.head_offset_m <= edge_lengths[self.head_edge_id]
        ):
            errors.append("train_head_position_invalid")
        if self.direction not in ("EASTBOUND", "WESTBOUND"):
            errors.append("train_direction_invalid")
        if any(block_id not in block_tracks for block_id in self.current_block_ids):
            errors.append("train_footprint_block_missing")
        return errors
