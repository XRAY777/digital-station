import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchReadiness } from "./health";

afterEach(() => vi.unstubAllGlobals());

describe("readiness response", () => {
  it("accepts a 503 readiness document with degraded components", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({
        status: "not_ready",
        backend: "ok",
        database: "ok",
        simulation: "degraded",
        optimizer: "ok",
        websocket: "ok",
        ai: "offline",
      }), { status: 503 })),
    );
    const health = await fetchReadiness();
    expect(health.status).toBe("not_ready");
    expect(health.simulation).toBe("degraded");
  });
});
