import type { DispatcherSnapshot, TrainDto } from "./types";
import type { FrameMessage } from "../websocket/client";

export function applyRealtimeFrame(
  current: DispatcherSnapshot,
  frame: FrameMessage,
): DispatcherSnapshot {
  if (frame.simulationRunId !== current.simulation.runId
      || frame.stateVersion < current.simulation.stateVersion) return current;
  const payload = frame.payload;
  let trains = current.trains;
  let routes = current.routes;
  let blocks = current.blocks;
  let tracks = current.tracks;
  if (frame.type === "train.position.updated" && typeof payload.trainId === "string") {
    const blockIds = Array.isArray(payload.blockIds)
      ? payload.blockIds.filter((item): item is string => typeof item === "string")
      : null;
    trains = current.trains.map((train): TrainDto => train.id !== payload.trainId ? train : {
      ...train,
      headPosition: {
        edgeId: typeof payload.headEdgeId === "string" ? payload.headEdgeId : train.headPosition.edgeId,
        offsetM: typeof payload.headOffsetM === "number" ? payload.headOffsetM : train.headPosition.offsetM,
      },
      speedMps: typeof payload.speedMps === "number" ? payload.speedMps : train.speedMps,
      currentBlockIds: blockIds ?? train.currentBlockIds,
      currentTrackId: typeof payload.headEdgeId === "string"
        ? current.topology.edges.find((edge) => edge.id === payload.headEdgeId)?.trackId ?? null
        : train.currentTrackId,
    });
    if (blockIds) {
      const currentTrainId = payload.trainId;
      blocks = current.blocks.map((block) => {
        const occupiedByTrainId = blockIds.includes(block.id) ? currentTrainId
          : block.occupiedByTrainId === currentTrainId ? null : block.occupiedByTrainId;
        return { ...block, occupiedByTrainId,
          status: occupiedByTrainId ? "OCCUPIED" : block.availability === "OPEN" ? "FREE" : block.availability };
      });
      tracks = current.tracks.map((track) => {
        const trackBlocks = blocks.filter((block) => block.trackId === track.id);
        return { ...track, status: trackBlocks.some((block) => block.status === "OCCUPIED")
          ? "OCCUPIED" : trackBlocks.some((block) => !["FREE", "OPEN"].includes(block.status))
            ? trackBlocks.find((block) => !["FREE", "OPEN"].includes(block.status))!.status : "FREE" };
      });
    }
  } else if (frame.type === "train.delay.updated" && typeof payload.trainId === "string") {
    trains = current.trains.map((train) => train.id === payload.trainId
      ? { ...train, delaySeconds: Number(payload.delaySeconds ?? train.delaySeconds) }
      : train);
  } else if (frame.type === "route.status.updated" && typeof payload.routeId === "string") {
    routes = current.routes.map((route) => route.id === payload.routeId ? {
      ...route,
      status: typeof payload.status === "string" ? payload.status : route.status,
      activationReason: typeof payload.activationReason === "string" || payload.activationReason === null
        ? payload.activationReason : route.activationReason,
    } : route);
  }
  const event = {
    eventId: frame.eventId, sequence: frame.sequence,
    simulationTimeMs: frame.simulationTimeMs, type: frame.type,
    entityId: typeof payload.trainId === "string" ? payload.trainId
      : typeof payload.routeId === "string" ? payload.routeId : null, payload,
  };
  return {
    ...current,
    simulation: { ...current.simulation, stateVersion: frame.stateVersion,
      simulationTimeMs: frame.simulationTimeMs },
    trains,
    routes,
    blocks,
    tracks,
    events: [...current.events.filter((item) => item.eventId !== event.eventId), event].slice(-100),
  };
}

export function visibleTrainPositions(snapshot: DispatcherSnapshot) {
  return snapshot.trains.map((train) => ({
    id: train.id,
    edgeId: train.headPosition.edgeId,
    offsetM: train.headPosition.offsetM,
    currentBlockIds: train.currentBlockIds,
  }));
}
