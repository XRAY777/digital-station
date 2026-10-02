export type HealthComponent =
  | "ok"
  | "offline"
  | "not_implemented"
  | "degraded"
  | "unavailable";

export type Health = {
  status: "ready" | "not_ready";
  backend: HealthComponent;
  database: HealthComponent;
  simulation: HealthComponent;
  optimizer: HealthComponent;
  websocket: HealthComponent;
  ai: HealthComponent;
};

export async function fetchReadiness(signal?: AbortSignal): Promise<Health> {
  const response = await fetch("/health", {
    headers: { Accept: "application/json" },
    signal,
  });
  const body: unknown = await response.json();

  if (!isHealth(body)) {
    throw new Error("Health API returned an invalid response");
  }
  if (!response.ok && response.status !== 503) {
    throw new Error(`Health API returned HTTP ${response.status}`);
  }
  return body;
}

function isHealth(value: unknown): value is Health {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  const allowed: readonly unknown[] = [
    "ok",
    "offline",
    "not_implemented",
    "degraded",
    "unavailable",
  ];
  return (
    (candidate.status === "ready" || candidate.status === "not_ready") &&
    ["backend", "database", "simulation", "optimizer", "websocket", "ai"].every((key) =>
      allowed.includes(candidate[key]),
    )
  );
}
