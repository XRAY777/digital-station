import type { AIRecommendation, DispatcherSnapshot } from "./types";
import type { ReplayState } from "./replay";

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    ...init,
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: unknown };
    const detail = typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`;
    throw new ApiError(response.status, detail);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export function fetchDispatcherSnapshot(signal?: AbortSignal): Promise<DispatcherSnapshot> {
  return request<DispatcherSnapshot>("/api/dispatcher/snapshot", { signal });
}

export function readApi<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function fetchReplay(timeMs: number, signal?: AbortSignal) {
  return request<{ mode: "REPLAY"; timeMs: number; stateVersion: number;
    state: ReplayState }>(`/api/dispatcher/replay?timeMs=${encodeURIComponent(timeMs)}`, { signal });
}

export async function command<T>(
  path: string, body: unknown, idempotencyKey = `web-${Date.now()}-${Math.random().toString(36).slice(2)}`,
): Promise<T> {
  const csrf = await request<{ csrfToken: string }>("/api/auth/csrf");
  return request<T>(path, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf.csrfToken, "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(body),
  });
}

export async function login(username: string, password: string): Promise<void> {
  const csrf = await request<{ csrfToken: string }>("/api/auth/csrf");
  await request("/api/auth/login", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf.csrfToken },
    body: JSON.stringify({ username, password }),
  });
}

export function askAssistant(selectedTrainId?: string, selectedRouteId?: string): Promise<AIRecommendation> {
  return command("/api/dispatcher/ai/recommendations", {
    selectedTrainId: selectedTrainId ?? null,
    selectedRouteId: selectedRouteId ?? null,
  });
}

export async function acceptVariant(planId: string, variantId: string): Promise<void> {
  await command(`/api/optimization/${encodeURIComponent(planId)}/variants/${encodeURIComponent(variantId)}/accept`, {});
}

export async function rejectAIRecommendation(id: string): Promise<void> {
  await command(`/api/dispatcher/ai/recommendations/${encodeURIComponent(id)}/reject`, {});
}

export async function exportReport(format: "csv" | "pdf"): Promise<Blob> {
  const end = new Date();
  const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  const params = new URLSearchParams({ from: start.toISOString(), to: end.toISOString() });
  const response = await fetch(`/api/dispatcher/reports.${format}?${params}`, { credentials: "include" });
  if (!response.ok) throw new ApiError(response.status, `report_export_${response.status}`);
  return response.blob();
}

export function websocketUrl(stationId: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/station/${encodeURIComponent(stationId)}`;
}
