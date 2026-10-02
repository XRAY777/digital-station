import { describe, expect, it } from "vitest";

import { applyReplayProjection } from "./replay";
import type { DispatcherSnapshot } from "./types";

const live = {
  simulation: { runId: "run", status: "RUNNING", simulationTimeMs: 600_000, stateVersion: 8, clockRate: 5 },
  trains: [{ id: "train", code: "T1", category: "PASSENGER", lengthM: 100, status: "MOVING",
    direction: "EAST", speedMps: 8, targetSpeedMps: 10, priority: 1, delaySeconds: 0,
    currentTrackId: "track", currentBlockIds: [], headPosition: { edgeId: "edge", offsetM: 90 } }],
  blocks: [{ id: "block", code: "B1", trackId: "track", lengthM: 100, availability: "OPEN",
    status: "FREE", occupiedByTrainId: null, closureReason: null }],
  tracks: [{ id: "track", code: "T1", name: "Track 1", purpose: "MAIN", lengthM: 100,
    maxSpeedMps: 20, blockIds: ["block"], status: "FREE" }],
  routes: [], conflicts: [], incidents: [], efficiency: { score: 90 },
    topology: { nodes: [], edges: [{ id: "old-edge", trackId: "track" }], tracks: [], blocks: [], platforms: [], yardId: "yard",
    switches: [{ id: "switch", code: "SW1", nodeId: "n1", state: "NORMAL", movementState: "IDLE", isFailed: false }],
    signals: [{ id: "signal", code: "S1", edgeId: "edge", aspect: "STOP", reasonCodes: [] }] },
} as unknown as DispatcherSnapshot;

describe("historical replay projection", () => {
  it("projects train, occupancy, infrastructure, and efficiency without mutating live data", () => {
    const projected = applyReplayProjection(live, { timeMs: 240_000, state: {
      trains: { train: { headEdgeId: "old-edge", headOffsetM: 20, speedMps: 3, delaySeconds: 60 } },
      occupancies: [{ trainId: "train", blockId: "block" }],
      blocks: { block: { availability: "OPEN", closureReason: null } },
      switches: { switch: { state: "REVERSE", isFailed: true } },
      signals: { signal: { aspect: "PROCEED", reasonCodes: [] } },
      efficiency: { score: 72 },
    } });
    expect(projected.trains[0]?.headPosition).toEqual({ edgeId: "old-edge", offsetM: 20 });
    expect(projected.trains[0]?.currentTrackId).toBe("track");
    expect(projected.blocks[0]?.occupiedByTrainId).toBe("train");
    expect(projected.topology.switches[0]?.isFailed).toBe(true);
    expect(projected.topology.signals[0]?.aspect).toBe("PROCEED");
    expect(projected.efficiency.score).toBe(72);
    expect(live.trains[0]?.headPosition.offsetM).toBe(90);
    expect(live.blocks[0]?.occupiedByTrainId).toBeNull();
  });
});
