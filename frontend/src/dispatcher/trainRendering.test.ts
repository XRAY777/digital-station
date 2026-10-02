import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Station3D } from "./Station3D";
import { MapInspector, StationMap } from "./StationMap";
import { applyRealtimeFrame } from "./realtime";
import type { DispatcherSnapshot } from "./types";
import type { FrameMessage } from "../websocket/client";

const initial = {
  station: { id: "station", code: "DEMO-01", name: "Demo", timezone: "UTC" },
  user: { id: "user", username: "dispatcher", roles: ["DISPATCHER"] },
  simulation: { runId: "run", status: "RUNNING", simulationTimeMs: 0, stateVersion: 1, clockRate: 1 },
  topology: {
    yardId: "yard",
    nodes: [
      { id: "from", code: "W1", kind: "junction", x: 0, y: 0 },
      { id: "to", code: "T01-W", kind: "platform_end", x: 100, y: 0 },
    ],
    edges: [{ id: "edge", code: "E01-A", fromNodeId: "from", toNodeId: "to", trackId: "track", blockId: "block", lengthM: 100 }],
    tracks: [{ id: "track", code: "01", name: "Track 01", purpose: "PASSENGER", lengthM: 100, maxSpeedMps: 20, blockIds: ["block"], status: "OCCUPIED", platformIds: [] }],
    blocks: [{ id: "block", code: "01-A", trackId: "track", lengthM: 100, availability: "OPEN", status: "OCCUPIED", occupiedByTrainId: "p303", closureReason: null }],
    platforms: [], switches: [], signals: [],
  },
  tracks: [{ id: "track", code: "01", name: "Track 01", purpose: "PASSENGER", lengthM: 100, maxSpeedMps: 20, blockIds: ["block"], status: "OCCUPIED", platformIds: [] }],
  blocks: [{ id: "block", code: "01-A", trackId: "track", lengthM: 100, availability: "OPEN", status: "OCCUPIED", occupiedByTrainId: "p303", closureReason: null }],
  trains: [{ id: "p303", code: "П-303", category: "PASSENGER", lengthM: 250, status: "ON_TRACK", direction: "EASTBOUND", speedMps: 8, targetSpeedMps: 12, priority: 1, delaySeconds: 0, currentTrackId: "track", currentBlockIds: ["block"], headPosition: { edgeId: "edge", offsetM: 10 } }],
  routes: [{ id: "route", trainId: "p303", status: "RESERVED", activationReason: "Waiting for activation", graphVersion: 1, edgeIds: ["edge"], blockIds: ["block"], switchIds: [], signalIds: [] }],
  conflicts: [], incidents: [], schedules: [], plans: [], efficiency: { score: 50 },
  resources: { locomotives: [], crews: [], platforms: [], reservations: [] }, events: [],
} as unknown as DispatcherSnapshot;

const routeActivated = {
  type: "route.status.updated", messageType: "FRAME", eventId: "route-2", sequence: 2,
  schemaVersion: 1, stationId: "station", simulationRunId: "run", stateVersion: 2,
  simulationTimeMs: 250, occurredAt: "2026-10-02T00:00:00Z",
  payload: { routeId: "route", trainId: "p303", status: "ACTIVE", activationReason: null },
} as FrameMessage;

const position = {
  type: "train.position.updated", messageType: "FRAME", eventId: "position-3", sequence: 3,
  schemaVersion: 1, stationId: "station", simulationRunId: "run", stateVersion: 3,
  simulationTimeMs: 250, occurredAt: "2026-10-02T00:00:00Z",
  payload: { trainId: "p303", headEdgeId: "edge", headOffsetM: 40, speedMps: 8, blockIds: ["block"] },
} as FrameMessage;

describe("active train positions are rendered from realtime state", () => {
  it("moves P-303 in both 2D and 3D after an authoritative frame", () => {
    const waitingInspector = renderToStaticMarkup(createElement(MapInspector, {
      snapshot: initial, selection: { kind: "route", id: "route" },
      setActiveRouteId: () => undefined, canDispatch: true,
    }));
    expect(waitingInspector).toContain("RESERVED");
    expect(waitingInspector).toContain("Waiting for activation");
    expect(waitingInspector).not.toContain("PROPOSED ROUTE");

    const active = applyRealtimeFrame(initial, routeActivated);
    expect(active.routes[0]?.status).toBe("ACTIVE");
    expect(active.routes[0]?.activationReason).toBeNull();
    const updated = applyRealtimeFrame(active, position);
    expect(updated.trains[0]?.headPosition.offsetM).toBe(40);

    const renderMap = (snapshot: DispatcherSnapshot) => renderToStaticMarkup(createElement(StationMap, {
      snapshot, setSelection: () => undefined, activeRouteId: "route",
    }));
    const renderScene = (snapshot: DispatcherSnapshot) => renderToStaticMarkup(createElement(Station3D, {
      snapshot, selection: null, setSelection: () => undefined,
    }));
    const mapPosition = (markup: string) => markup.match(/class="map-train" transform="([^"]+)"/)?.[1];
    const scenePosition = (markup: string) => markup.match(/class="scene-train" transform="([^"]+)"/)?.[1];

    expect(mapPosition(renderMap(updated))).not.toBe(mapPosition(renderMap(initial)));
    expect(scenePosition(renderScene(updated))).not.toBe(scenePosition(renderScene(initial)));
  });
});
