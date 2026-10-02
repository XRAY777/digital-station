import type { DispatcherSnapshot } from "./types";

export type ReplayState = {
  trains?: Record<string, Record<string, unknown>>;
  occupancies?: { trainId: string; blockId: string }[];
  switches?: Record<string, string | Record<string, unknown>>;
  signals?: Record<string, string | Record<string, unknown>>;
  blocks?: Record<string, Record<string, unknown>>;
  routes?: Record<string, Record<string, unknown>>;
  conflicts?: Record<string, Record<string, unknown>>;
  incidents?: Record<string, Record<string, unknown>>;
  efficiency?: DispatcherSnapshot["efficiency"];
};

export function applyReplayProjection(
  live: DispatcherSnapshot,
  projection: { timeMs: number; state: ReplayState },
): DispatcherSnapshot {
  const state = projection.state;
  const ownerByBlock = new Map((state.occupancies ?? []).map((row) => [row.blockId, row.trainId]));
  const trains = live.trains.map((train) => {
    const historical = state.trains?.[train.id];
    return historical ? { ...train,
      status: String(historical.status ?? train.status),
      direction: String(historical.direction ?? train.direction),
      speedMps: Number(historical.speedMps ?? train.speedMps),
      headPosition: { edgeId: typeof historical.headEdgeId === "string" ? historical.headEdgeId : null,
        offsetM: Number(historical.headOffsetM ?? 0) },
      currentTrackId: typeof historical.headEdgeId === "string"
        ? live.topology.edges.find((edge) => edge.id === historical.headEdgeId)?.trackId ?? null
        : train.currentTrackId,
      delaySeconds: Number(historical.delaySeconds ?? train.delaySeconds),
      currentBlockIds: (state.occupancies ?? []).filter((item) => item.trainId === train.id).map((item) => item.blockId),
    } : train;
  });
  const blocks = live.blocks.map((block) => {
    const historical = state.blocks?.[block.id];
    const owner = ownerByBlock.get(block.id) ?? null;
    const availability = String(historical?.availability ?? block.availability);
    return { ...block, availability, closureReason: typeof historical?.closureReason === "string"
      ? historical.closureReason : null, occupiedByTrainId: owner,
      status: availability !== "OPEN" ? availability : owner ? "OCCUPIED" : "FREE" };
  });
  const tracks = live.tracks.map((track) => ({ ...track, status:
    blocks.some((block) => track.blockIds.includes(block.id) && block.status === "BLOCKED") ? "BLOCKED" :
      blocks.some((block) => track.blockIds.includes(block.id) && block.status === "OCCUPIED") ? "OCCUPIED" :
        blocks.some((block) => track.blockIds.includes(block.id) && block.status !== "FREE") ? "MAINTENANCE" : "FREE" }));
  const routes = state.routes ? Object.entries(state.routes).map(([id, row]) => ({
    id, trainId: String(row.trainId ?? ""), status: String(row.status ?? "PROPOSED"),
    graphVersion: 1, edgeIds: Array.isArray(row.edgeIds) ? row.edgeIds.map(String) : [],
    blockIds: Array.isArray(row.blockIds) ? row.blockIds.map(String) : [],
    switchIds: Array.isArray(row.switchIds) ? row.switchIds.map(String) : [],
    signalIds: Array.isArray(row.signalIds) ? row.signalIds.map(String) : [],
  })) : live.routes;
  const conflicts = state.conflicts ? Object.entries(state.conflicts).map(([id, row]) => ({
    id, kind: String(row.kind ?? "UNKNOWN"), severity: String(row.severity ?? "WARNING"),
    status: String(row.status ?? "OPEN"), details: (row.details ?? {}) as Record<string, unknown>,
    detectedAtMs: Number(row.detectedAtMs ?? 0),
  })) : live.conflicts;
  const incidents = state.incidents ? Object.entries(state.incidents).map(([id, row]) => ({
    id, kind: String(row.kind ?? "UNKNOWN"), status: String(row.status ?? "OPEN"),
    severity: String(row.severity ?? "INFO"), source: typeof row.source === "string" ? row.source : null,
    payload: (row.payload ?? {}) as Record<string, unknown>,
    openedAt: String(row.openedAt ?? ""),
    createdAt: String(row.openedAt ?? ""),
    resolvedAt: typeof row.resolvedAt === "string" ? row.resolvedAt : null,
  })) : live.incidents;
  const switchStates = state.switches ?? {};
  const signalStates = state.signals ?? {};
  return { ...live,
    simulation: { ...live.simulation, simulationTimeMs: projection.timeMs },
    trains, blocks, tracks, routes, conflicts, incidents,
    efficiency: state.efficiency ?? live.efficiency,
    topology: { ...live.topology,
      switches: live.topology.switches.map((item) => {
        const historical = switchStates[item.id];
        return historical ? { ...item,
          state: typeof historical === "string" ? historical : String(historical.state ?? item.state),
          isFailed: typeof historical === "string" ? false : Boolean(historical.isFailed ?? item.isFailed),
        } : item;
      }),
      signals: live.topology.signals.map((item) => {
        const historical = signalStates[item.id];
        return historical ? { ...item,
          aspect: typeof historical === "string" ? historical : String(historical.aspect ?? item.aspect),
          reasonCodes: typeof historical === "string" ? [] : (Array.isArray(historical.reasonCodes)
            ? historical.reasonCodes.map(String) : item.reasonCodes),
        } : item;
      }),
    },
  };
}
