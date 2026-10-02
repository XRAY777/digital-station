export type Role = "ADMIN" | "DISPATCHER" | "OBSERVER" | string;

export type Point = { x: number | null; y: number | null };
export type NodeDto = { id: string; code: string; kind: string; x: number | null; y: number | null };
export type EdgeDto = {
  id: string;
  code: string;
  fromNodeId: string;
  toNodeId: string;
  trackId: string | null;
  blockId: string | null;
  lengthM: number;
};
export type TrackDto = {
  id: string; code: string; name: string; purpose: string; lengthM: number;
  maxSpeedMps: number; blockIds: string[]; status: string;
  platformIds?: string[];
};
export type BlockDto = {
  id: string; code: string; trackId: string; lengthM: number;
  availability: string; status: string; occupiedByTrainId: string | null;
  closureReason: string | null;
};
export type TrainDto = {
  id: string; code: string; category: string; lengthM: number; status: string;
  direction: string; speedMps: number; targetSpeedMps: number; priority: number;
  source?: string; destination?: string; delaySeconds: number;
  currentTrackId: string | null; currentBlockIds: string[];
  headPosition: { edgeId: string | null; offsetM: number };
};
export type RouteDto = {
  id: string; trainId: string; status: string; graphVersion: number;
  edgeIds: string[]; blockIds: string[]; switchIds: string[]; signalIds: string[];
  activationReason?: string | null;
  failureReason?: unknown;
};
export type DispatcherSnapshot = {
  station: { id: string; code: string; name: string; timezone: string };
  user: { id: string; username: string; roles: Role[] };
  simulation: { runId: string; status: string; simulationTimeMs: number;
    stateVersion: number; clockRate: number };
  topology: {
    yardId: string; nodes: NodeDto[]; edges: EdgeDto[]; tracks: TrackDto[];
    blocks: BlockDto[]; platforms: { id: string; code: string; name: string; lengthM: number }[];
    switches: { id: string; code: string; nodeId: string; state: string;
      movementState: string; isFailed: boolean }[];
    signals: { id: string; code: string; edgeId: string; aspect: string; reasonCodes: string[] }[];
  };
  tracks: TrackDto[]; blocks: BlockDto[]; trains: TrainDto[]; routes: RouteDto[];
  conflicts: { id: string; kind: string; severity: string; status: string;
    details: Record<string, unknown>; detectedAtMs: number }[];
  incidents: { id: string; kind: string; status: string; payload: Record<string, unknown>;
    severity?: string; description?: string; source?: string | null;
    affectedEntities?: Record<string, unknown>; metadata?: Record<string, unknown>;
    openedAt: string; createdAt?: string; resolvedAt?: string | null }[];
  schedules: { id: string; trainId: string; scheduledAt: string; eventType: string;
    platformId: string | null; priority: number; delaySeconds: number;
    actualAt: string | null; actualStatus: string }[];
  plans: { id: string; status: string; sourceStateVersion: number; generatedAt: string;
    calculationTimeMs: number; objective: Record<string, unknown>;
    variants: { id: string; ordinal: number; score: number; operations: Record<string, unknown>[];
      explanation: Record<string, unknown> }[] }[];
  efficiency: { score: number; factors?: Record<string, number>; contributions?: Record<string, number> };
  ai?: { status: "OFFLINE" | "CONFIGURED" };
  resources: {
    locomotives: { id: string; code: string; status: string }[];
    crews: { id: string; code: string; status: string }[];
    platforms: { id: string; code: string; name: string; lengthM: number }[];
    reservations: { id: string; type: string; resourceId: string; startsAtMs: number;
      endsAtMs: number; status: string; trainId: string | null; routeId: string | null }[];
  };
  events: { eventId: string; sequence: number; simulationTimeMs: number; type: string;
    entityId: string | null; payload: Record<string, unknown> }[];
};

export type InspectorSelection =
  | { kind: "track"; id: string }
  | { kind: "train"; id: string }
  | { kind: "switch"; id: string }
  | { kind: "signal"; id: string }
  | { kind: "conflict"; id: string }
  | { kind: "route"; id: string }
  | null;

export type AIRecommendation = {
  id: string; provider: string; sourceStateVersion: number; summary: string; problem: string;
  reasoningSummary: string; recommendedVariantId: string | null; planId: string | null;
  advantages: string[]; risks: string[]; affectedTrains: string[];
  expectedImpact: Record<string, unknown>;
};
