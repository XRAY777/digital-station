import { useMemo, useState, type PointerEvent } from "react";

import type { DispatcherSnapshot, InspectorSelection } from "./types";

import { projectStationPoint } from "./geometry";

export type CameraMode = "OVERVIEW" | "DISPATCHER" | "FOLLOW TRAIN" | "FREE CAMERA" | "PLATFORM VIEW";
type Point = { x: number; y: number };

function stationPoint(snapshot: DispatcherSnapshot, nodeId: string, center: Point, yaw: number, height = 0) {
  const node = snapshot.topology.nodes.find((item) => item.id === nodeId);
  if (!node || node.x === null || node.y === null) return null;
  return projectStationPoint(node.x, node.y, height, center, yaw);
}

export function Station3D({ snapshot, selection, setSelection }: {
  snapshot: DispatcherSnapshot; selection: InspectorSelection;
  setSelection: (selection: InspectorSelection) => void;
}) {
  const [camera, setCamera] = useState<CameraMode>("OVERVIEW");
  const [yaw, setYaw] = useState(-0.22);
  const [drag, setDrag] = useState<{ x: number; yaw: number } | null>(null);
  const selectedTrainId = selection?.kind === "train" ? selection.id : null;
  const followedTrain = snapshot.trains.find((train) => train.id === selectedTrainId);
  const followedEdge = snapshot.topology.edges.find((edge) => edge.id === followedTrain?.headPosition.edgeId);
  const followedNode = followedEdge && snapshot.topology.nodes.find((node) => node.id === followedEdge.fromNodeId);
  const center = useMemo(() => followedNode?.x !== null && followedNode?.x !== undefined
    && followedNode.y !== null && camera === "FOLLOW TRAIN"
    ? { x: followedNode.x + (followedEdge?.lengthM ?? 0) / 2, y: followedNode.y }
    : { x: 587, y: 300 }, [camera, followedEdge, followedNode]);
  const effectiveYaw = camera === "DISPATCHER" ? -0.62 : camera === "PLATFORM VIEW" ? 0.45 : yaw;
  const projectedEdges = useMemo(() => snapshot.topology.edges.map((edge) => ({
    edge,
    from: stationPoint(snapshot, edge.fromNodeId, center, effectiveYaw),
    to: stationPoint(snapshot, edge.toNodeId, center, effectiveYaw),
  })), [snapshot, center, effectiveYaw]);

  function onPointerDown(event: PointerEvent<SVGSVGElement>) {
    if (camera !== "FREE CAMERA") return;
    setDrag({ x: event.clientX, yaw });
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function onPointerMove(event: PointerEvent<SVGSVGElement>) {
    if (drag && camera === "FREE CAMERA") setYaw(drag.yaw + (event.clientX - drag.x) * 0.006);
  }

  return <div className="station-3d" data-testid="station-3d">
    <div className="camera-toolbar" role="group" aria-label="Режим камеры">
      {(["OVERVIEW", "DISPATCHER", "FOLLOW TRAIN", "FREE CAMERA", "PLATFORM VIEW"] as const).map((mode) =>
        <button key={mode} className={camera === mode ? "selected" : ""} type="button"
          aria-pressed={camera === mode} onClick={() => setCamera(mode)}>{mode}</button>)}
    </div>
    <svg className="station-scene" viewBox="0 0 1440 560" role="img"
      aria-label="Трёхмерная проекция станции из той же модели состояния"
      onPointerDown={onPointerDown} onPointerMove={onPointerMove}
      onPointerUp={() => setDrag(null)} onPointerCancel={() => setDrag(null)}>
      <defs>
        <linearGradient id="ground" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#242923" /><stop offset="1" stopColor="#171a16" />
        </linearGradient>
        <linearGradient id="platform-side" x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="#656a61" /><stop offset="1" stopColor="#393e37" />
        </linearGradient>
      </defs>
      <rect width="1440" height="560" fill="url(#ground)" />
      <path d="M 135 413 L 1280 413 L 1362 482 L 73 482 Z" fill="#20241f" stroke="#343a32" />
      <path d="M 126 390 L 1287 390 L 1328 414 L 91 414 Z" fill="#2b3029" opacity=".8" />
      {snapshot.topology.tracks.map((track) => {
        const edges = projectedEdges.filter(({ edge }) => edge.trackId === track.id);
        const points = edges.flatMap(({ from, to }) => from && to ? [from, to] : []);
        if (points.length < 2) return null;
        const mid = points[Math.floor(points.length / 2)];
        const activeRoute = snapshot.routes.some((route) => route.status === "ACTIVE"
          && route.edgeIds.some((id) => edges.some((item) => item.edge.id === id)));
        const reserved = snapshot.routes.some((route) => ["RESERVED", "PROPOSED"].includes(route.status)
          && route.edgeIds.some((id) => edges.some((item) => item.edge.id === id)));
        const color = activeRoute ? "#82aa71" : reserved ? "#5680a5" : "#868b81";
        const angle = Math.atan2(points[points.length - 1].y - points[0].y,
          points[points.length - 1].x - points[0].x) * 180 / Math.PI;
        const half = track.lengthM / 2;
        return <g key={track.id}>
          {track.platformIds?.length ? <g transform={`translate(${mid.x} ${mid.y - 7}) rotate(${angle})`}>
            <rect x={-half * 0.34} y="-17" width={half * 0.68} height="34"
              fill="url(#platform-side)" stroke="#858a81" strokeWidth="1" />
            <line x1={-half * 0.34} y1="17" x2={half * 0.34} y2="17"
              stroke="#30352f" strokeWidth="5" />
          </g> : null}
          {edges.map(({ edge, from, to }) => from && to ? <g key={edge.id}>
            <line x1={from.x} y1={from.y + 3} x2={to.x} y2={to.y + 3}
              stroke="#363b34" strokeWidth="8" />
            {Array.from({ length: 13 }, (_, index) => {
              const t = (index + 1) / 14;
              return <line key={`${edge.id}-tie-${index}`}
                x1={from.x + (to.x - from.x) * t - 4} y1={from.y + (to.y - from.y) * t}
                x2={from.x + (to.x - from.x) * t + 4} y2={from.y + (to.y - from.y) * t}
                stroke="#555a51" strokeWidth="2" />;
            })}
            <line x1={from.x} y1={from.y - 1} x2={to.x} y2={to.y - 1}
              stroke={color} strokeWidth="2.2" />
            <line x1={from.x} y1={from.y + 5} x2={to.x} y2={to.y + 5}
              stroke={color} strokeWidth="2.2" />
          </g> : null)}
          <text x={mid.x} y={mid.y - 24} className="scene-label">ПУТЬ {track.code}</text>
        </g>;
      })}
      {snapshot.topology.switches.map((sw) => {
        const point = stationPoint(snapshot, sw.nodeId, center, effectiveYaw, 2);
        return point ? <g key={sw.id} onClick={() => setSelection({ kind: "switch", id: sw.id })}>
          <circle cx={point.x} cy={point.y} r="8" fill={sw.isFailed ? "#c45c52" : "#d2a05b"}
            stroke="#d8d7ca" /><text x={point.x + 11} y={point.y - 9} className="scene-label">{sw.code}</text>
        </g> : null;
      })}
      {snapshot.topology.signals.map((signal) => {
        const edge = projectedEdges.find((item) => item.edge.id === signal.edgeId);
        if (!edge?.from || !edge.to) return null;
        const point = { x: edge.from.x * .18 + edge.to.x * .82,
          y: edge.from.y * .18 + edge.to.y * .82 };
        const signalColor = signal.aspect === "CLEAR" ? "#87b57a" : signal.aspect === "CAUTION" ? "#d7a454" : "#c4544c";
        return <g key={signal.id} onClick={() => setSelection({ kind: "signal", id: signal.id })}>
          <line x1={point.x} y1={point.y} x2={point.x} y2={point.y - 25}
            stroke="#a7aaa0" strokeWidth="2" /><circle cx={point.x} cy={point.y - 28} r="5" fill={signalColor} />
        </g>;
      })}
      {snapshot.trains.map((train) => {
        const edge = projectedEdges.find((item) => item.edge.id === train.headPosition.edgeId);
        if (!edge?.from || !edge.to) return null;
        const ratio = Math.max(0, Math.min(1, train.headPosition.offsetM / Math.max(1, edge.edge.lengthM)));
        const x = edge.from.x + (edge.to.x - edge.from.x) * ratio;
        const y = edge.from.y + (edge.to.y - edge.from.y) * ratio - 7;
        const angle = Math.atan2(edge.to.y - edge.from.y, edge.to.x - edge.from.x) * 180 / Math.PI;
        return <g key={train.id} className="scene-train" transform={`translate(${x} ${y}) rotate(${angle})`}
          onClick={() => setSelection({ kind: "train", id: train.id })}>
          <rect x="-31" y="-8" width="62" height="16" rx="5"
            fill={train.delaySeconds > 0 ? "#ba753f" : "#39809c"} stroke="#d9ded5" />
          <rect x="-22" y="-4" width="9" height="5" fill="#b3c4bd" />
          <rect x="-7" y="-4" width="9" height="5" fill="#b3c4bd" />
          <rect x="8" y="-4" width="9" height="5" fill="#b3c4bd" />
          <text x="0" y="21" className="scene-label">{train.code}</text>
        </g>;
      })}
      <g className="station-building"><path d="M 550 174 L 700 145 L 850 174 L 850 198 L 550 198 Z"
        fill="#41473f" stroke="#73796f" /><path d="M 572 175 L 700 151 L 828 175" fill="none" stroke="#a4a89c" />
        <text x="700" y="191" className="scene-label">ВОКЗАЛ</text></g>
      <text x="36" y="530" className="scene-caption">3D VIEW · {camera} · SAME LIVE STATE</text>
      {camera === "FOLLOW TRAIN" && followedTrain && <text x="1030" y="530" className="scene-caption">FOLLOWING {followedTrain.code}</text>}
    </svg>
  </div>;
}
