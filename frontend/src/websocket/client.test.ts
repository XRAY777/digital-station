import { describe, expect, it } from "vitest";

import { reconnectDelay, sequenceDecision } from "./client";

describe("station websocket protocol", () => {
  it("deduplicates event ids and old sequences", () => {
    expect(sequenceDecision(4, new Set(["event-4"]), "event-4", 4)).toBe("DUPLICATE");
    expect(sequenceDecision(4, new Set(), "event-3", 3)).toBe("DUPLICATE");
  });

  it("requires resync after a missing sequence", () => {
    expect(sequenceDecision(4, new Set(), "event-6", 6)).toBe("RESYNC");
  });

  it("backs off exponentially with bounded jitter", () => {
    expect(reconnectDelay(0, () => 0)).toBe(500);
    expect(reconnectDelay(20, () => 1)).toBe(30_000);
  });
});
