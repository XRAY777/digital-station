import { describe, expect, it } from "vitest";

import { serviceRows } from "./healthView";
import type { Health } from "../services/health";

const startingHealth: Health = {
  status: "not_ready",
  backend: "ok",
  database: "not_implemented",
  simulation: "not_implemented",
  optimizer: "not_implemented",
  websocket: "not_implemented",
  ai: "offline",
};

describe("serviceRows", () => {
  it("shows implemented and future services with distinct states", () => {
    const rows = serviceRows(startingHealth);

    expect(rows[0]).toMatchObject({ name: "Backend API", state: "online" });
    expect(rows[1]).toMatchObject({ name: "Database", state: "pending" });
    expect(rows[5]).toMatchObject({ name: "AI Assistant", state: "offline" });
  });

  it("does not present an unreachable API as a pending component report", () => {
    expect(serviceRows(null).every((row) => row.state === "offline")).toBe(true);
  });
});
