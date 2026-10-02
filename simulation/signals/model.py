from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Aspect = Literal["STOP", "RESTRICTED", "CLEAR"]


@dataclass(frozen=True, slots=True)
class SignalState:
    id: str
    code: str
    edge_id: str
    aspect: Aspect
    reason_codes: tuple[str, ...]


def derive_aspect(
    *, has_authority: bool, route_valid: bool, switches_set: bool, path_clear: bool
) -> Aspect:
    if not has_authority or not route_valid or not switches_set or not path_clear:
        return "STOP"
    return "CLEAR"
