import { describe, expect, it } from "vitest";

import { applyRealtimeFrame, visibleTrainPositions } from "./realtime";
import type { DispatcherSnapshot } from "./types";
import type { FrameMessage } from "../websocket/client";

const initial = {
  simulation: { runId: "run-1", status: "RUNNING", simulationTimeMs: 100, stateVersion: 4, clockRate: 10 },
  trains: [{ id: "train-1", code: "T1", category: "PASSENGER", lengthM: 120, status: "MOVING",
    direction: "EAST", speedMps: 8, targetSpeedMps: 10, priority: 1, delaySeconds: 0,
    currentTrackId: "track-1", currentBlockIds: ["block-1"], headPosition: { edgeId: "edge-1", offsetM: 10 } }],
  topology: { edges: [{ id: "edge-1", trackId: "track-1" }, { id: "edge-2", trackId: "track-2" }] },
  blocks: [
    { id: "block-1", code: "B1", trackId: "track-1", lengthM: 100, availability: "OPEN", status: "OCCUPIED", occupiedByTrainId: "train-1", closureReason: null },
    { id: "block-2", code: "B2", trackId: "track-2", lengthM: 100, availability: "OPEN", status: "FREE", occupiedByTrainId: null, closureReason: null },
  ],
  tracks: [
    { id: "track-1", code: "T1", name: "Track 1", purpose: "MAIN", lengthM: 100, maxSpeedMps: 20, blockIds: ["block-1"], status: "OCCUPIED" },
    { id: "track-2", code: "T2", name: "Track 2", purpose: "MAIN", lengthM: 100, maxSpeedMps: 20, blockIds: ["block-2"], status: "FREE" },
  ],
  events: [],
} as unknown as DispatcherSnapshot;

const frame = {
  type: "train.position.updated", messageType: "FRAME", eventId: "evt-1", sequence: 1,
  schemaVersion: 1, stationId: "station-1", simulationRunId: "run-1", stateVersion: 5,
  simulationTimeMs: 200, occurredAt: "2026-01-01T00:00:00Z",
  payload: { trainId: "train-1", headEdgeId: "edge-2", headOffsetM: 40, speedMps: 9, blockIds: ["block-2"] },
} as FrameMessage;

describe("shared dispatcher realtime state", () => {
  it("applies one authoritative train update for every view and preserves simulation speed", () => {
    const updated = applyRealtimeFrame(initial, frame);
    expect(visibleTrainPositions(updated)).toEqual([{
      id: "train-1", edgeId: "edge-2", offsetM: 40, currentBlockIds: ["block-2"],
    }]);
    expect(updated.simulation.clockRate).toBe(10);
    expect(updated.simulation.simulationTimeMs).toBe(200);
    expect(updated.blocks.map((block) => block.occupiedByTrainId)).toEqual([null, "train-1"]);
    expect(updated.tracks.map((track) => track.status)).toEqual(["FREE", "OCCUPIED"]);
  });

  it("ignores stale frames and frames from another run", () => {
    expect(applyRealtimeFrame(initial, { ...frame, stateVersion: 3 })).toBe(initial);
    expect(applyRealtimeFrame(initial, { ...frame, simulationRunId: "run-2" })).toBe(initial);
  });
});
