export type SnapshotMessage = {
  type: "SNAPSHOT";
  schemaVersion: number;
  stationId: string;
  simulationRunId: string | null;
  sequence: number;
  stateVersion: number;
  state: Record<string, unknown>;
};

export type FrameMessage = {
  type: string;
  messageType: "FRAME";
  eventId: string;
  sequence: number;
  schemaVersion: number;
  stationId: string;
  simulationRunId: string;
  stateVersion: number;
  simulationTimeMs: number;
  occurredAt: string;
  payload: Record<string, unknown>;
};

export type StationMessage = SnapshotMessage | FrameMessage | {
  type: string;
  messageType?: string;
  [key: string]: unknown;
};

export function sequenceDecision(
  lastSequence: number,
  seen: ReadonlySet<string>,
  eventId: string,
  sequence: number,
): "APPLY" | "DUPLICATE" | "RESYNC" {
  if (seen.has(eventId) || sequence <= lastSequence) return "DUPLICATE";
  return sequence === lastSequence + 1 ? "APPLY" : "RESYNC";
}

export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(30_000, 1_000 * 2 ** Math.min(attempt, 5));
  return Math.floor(ceiling * (0.5 + Math.max(0, Math.min(1, random())) * 0.5));
}

export class StationSocketClient {
  private socket: WebSocket | null = null;
  private stopped = true;
  private attempt = 0;
  private sequence = 0;
  private runId: string | null = null;
  private readonly seen = new Set<string>();
  private reconnectTimer: number | null = null;
  private heartbeatMonitor: number | null = null;
  private lastHeartbeat = 0;

  constructor(
    private readonly url: string,
    private readonly stationId: string,
    private readonly onMessage: (message: StationMessage) => void,
    private readonly onStale: (stale: boolean) => void = () => undefined,
    private readonly socketFactory: (url: string) => WebSocket = (address) => new WebSocket(address),
    private readonly random: () => number = Math.random,
    private readonly onStatus: (status: "ONLINE" | "RECONNECTING" | "STALE DATA" | "OFFLINE") => void = () => undefined,
  ) {}

  connect(): void {
    this.stopped = false;
    this.open();
  }

  close(): void {
    this.stopped = true;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    if (this.heartbeatMonitor !== null) window.clearInterval(this.heartbeatMonitor);
    this.reconnectTimer = null;
    this.heartbeatMonitor = null;
    this.socket?.close(1000, "client_closed");
    this.socket = null;
  }

  private open(): void {
    if (this.stopped) return;
    const socket = this.socketFactory(this.url);
    this.socket = socket;
    socket.onopen = () => {
      this.lastHeartbeat = Date.now();
      if (this.heartbeatMonitor === null) {
        this.heartbeatMonitor = window.setInterval(() => {
          const idleMs = Date.now() - this.lastHeartbeat;
          this.onStale(idleMs > 10_000);
          if (idleMs > 10_000) this.onStatus("STALE DATA");
          if (idleMs > 15_000) this.socket?.close(4000, "heartbeat_timeout");
        }, 1_000);
      }
      this.onStale(false);
      this.onStatus("ONLINE");
      socket.send(JSON.stringify({ type: "SUBSCRIBE", stationId: this.stationId,
        simulationRunId: this.runId, lastSequence: this.sequence }));
    };
    socket.onmessage = (event: MessageEvent<string>) => this.receive(event.data);
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      if (this.socket === socket) this.socket = null;
      this.scheduleReconnect();
    };
  }

  private receive(raw: string): void {
    let message: StationMessage;
    try {
      message = JSON.parse(raw) as StationMessage;
    } catch {
      this.socket?.close(1002, "invalid_json");
      return;
    }
    if (message.type === "HEARTBEAT") {
      this.lastHeartbeat = Date.now();
      this.attempt = 0;
      this.onStale(false);
      this.onStatus("ONLINE");
      return;
    }
    if (message.type === "RESYNC_REQUIRED") {
      this.sequence = 0;
      this.runId = null;
      this.socket?.close(1012, "resync_required");
      return;
    }
    if (message.type === "SNAPSHOT") {
      const snapshot = message as SnapshotMessage;
      if (snapshot.schemaVersion !== 1 || snapshot.stationId !== this.stationId) return;
      this.sequence = snapshot.sequence;
      this.runId = snapshot.simulationRunId;
      this.seen.clear();
      this.onMessage(snapshot);
      return;
    }
    if (!("messageType" in message) || message.messageType !== "FRAME") return;
    const frame = message as FrameMessage;
    if (frame.schemaVersion !== 1 || frame.stationId !== this.stationId
        || (this.runId && frame.simulationRunId !== this.runId)) return;
    const decision = sequenceDecision(this.sequence, this.seen, frame.eventId, frame.sequence);
    if (decision === "DUPLICATE") return;
    if (decision === "RESYNC") {
      this.socket?.close(1012, "sequence_gap");
      return;
    }
    this.sequence = frame.sequence;
    this.attempt = 0;
    this.seen.add(frame.eventId);
    if (this.seen.size > 2_000) this.seen.delete(this.seen.values().next().value ?? "");
    this.lastHeartbeat = Date.now();
    this.onStale(false);
    this.onStatus("ONLINE");
    this.onMessage(frame);
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer !== null) return;
    this.onStale(Date.now() - this.lastHeartbeat > 2_000);
    this.onStatus(this.attempt >= 2 ? "OFFLINE" : "RECONNECTING");
    const delay = reconnectDelay(this.attempt++, this.random);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }
}
