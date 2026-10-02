from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EfficiencyConfig:
    weights: tuple[tuple[str, float], ...] = (
        ("throughput", 0.30),
        ("delay", 0.25),
        ("track_utilization", 0.20),
        ("conflicts", 0.15),
        ("resource_idle", 0.10),
    )
    delay_target_seconds: float = 300
    utilization_target: float = 0.75
    idle_target: float = 0.20


def calculate(
    *,
    throughput_per_hour: float,
    average_delay_seconds: float,
    track_utilization: float,
    active_conflicts: int,
    resource_idle: float,
    config: EfficiencyConfig | None = None,
) -> dict[str, object]:
    config = config or EfficiencyConfig()
    weights = dict(config.weights)
    if set(weights) != {"throughput", "delay", "track_utilization", "conflicts", "resource_idle"}:
        raise ValueError("efficiency_weights_invalid")
    if any(weight < 0 for weight in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError("efficiency_weights_invalid")
    total = sum(weights.values())
    factors = {
        "throughput": min(100.0, max(0.0, throughput_per_hour * 10)),
        "delay": max(
            0.0, 100 * (1 - max(0.0, average_delay_seconds) / max(1, config.delay_target_seconds))
        ),
        "track_utilization": max(
            0.0, 100 * (1 - abs(track_utilization - config.utilization_target))
        ),
        "conflicts": max(0.0, 100 - max(0, active_conflicts) * 20),
        "resource_idle": max(
            0.0, 100 * (1 - max(0.0, resource_idle) / max(0.01, config.idle_target))
        ),
    }
    contributions = {key: round(value * weights[key] / total, 2) for key, value in factors.items()}
    return {
        "score": round(min(100, max(0, sum(contributions.values()))), 2),
        "factors": factors,
        "contributions": contributions,
        "weights": weights,
        "thresholds": {
            "delayTargetSeconds": config.delay_target_seconds,
            "utilizationTarget": config.utilization_target,
            "idleTarget": config.idle_target,
        },
    }
