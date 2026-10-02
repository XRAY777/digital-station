from typing import Literal

from pydantic import BaseModel

ComponentState = Literal[
    "ok",
    "offline",
    "not_implemented",
    "degraded",
    "unavailable",
]


class HealthResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    backend: ComponentState
    database: ComponentState
    simulation: ComponentState
    optimizer: ComponentState
    websocket: ComponentState
    ai: ComponentState
