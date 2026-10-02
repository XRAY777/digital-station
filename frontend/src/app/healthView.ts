import type { Health, HealthComponent } from "../services/health";

export type ServiceRow = {
  name: string;
  description: string;
  state: "online" | "offline" | "pending" | "degraded";
  label: string;
};

const serviceInfo: Record<keyof Omit<Health, "status">, [string, string]> = {
  backend: ["Backend API", "HTTP-интерфейс и OpenAPI"],
  database: ["Database", "Хранение станции и истории"],
  simulation: ["Simulation Runtime", "Состояние и движение поездов"],
  optimizer: ["Optimization", "Расчёт допустимых вариантов"],
  websocket: ["Realtime", "Поток обновлений станции"],
  ai: ["AI Assistant", "Необязательные объяснения решений"],
};

function displayState(value: HealthComponent): ServiceRow["state"] {
  if (value === "ok") return "online";
  if (value === "degraded") return "degraded";
  if (value === "offline" || value === "unavailable") return "offline";
  return "pending";
}

export function serviceRows(health: Health | null): ServiceRow[] {
  return (Object.keys(serviceInfo) as (keyof typeof serviceInfo)[]).map((key) => {
    const [name, description] = serviceInfo[key];
    const state = health ? displayState(health[key]) : "offline";
    const label =
      state === "online"
        ? "ONLINE"
        : state === "offline"
          ? "OFFLINE"
          : state === "degraded"
            ? "DEGRADED"
            : "В ПЛАНЕ";
    return { name, description, state, label };
  });
}
