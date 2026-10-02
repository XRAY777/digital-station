import type { Dispatch, SetStateAction } from "react";

import type { DispatcherSnapshot, InspectorSelection, RouteDto } from "./types";

type Props = {
  snapshot: DispatcherSnapshot;
  selection?: InspectorSelection;
  setSelection: Dispatch<SetStateAction<InspectorSelection>>;
  activeRouteId: string | null;
  setActiveRouteId?: (id: string | null) => void;
  preview?: { planId: string; variantId: string } | null;
  focusConflictId?: string | null;
  onRequestRoute?: (trainId: string) => void;
  canDispatch?: boolean;
};

const statusColor: Record<string, string> = {
  FREE: "#62665f", OPEN: "#62665f", OCCUPIED: "#b9554d", BLOCKED: "#b9554d",
  MAINTENANCE: "#b9554d", RESERVED: "#55799b", ACTIVE: "#83aa72", PROPOSED: "#5b88ad",
  STOP: "#c45f56", RED: "#c45f56", YELLOW: "#d29b55", CLEAR: "#83aa72", GREEN: "#83aa72",
};

function codeOrder(code: string): number {
  const number = Number.parseInt(code.match(/\d+/)?.[0] ?? "999", 10);
  return Number.isFinite(number) ? number : 999;
}

function routeColor(route: RouteDto | undefined): string | undefined {
  if (!route) return undefined;
  return route.status === "ACTIVE" || route.status === "RESERVED" ? "#83aa72"
    : route.status === "PROPOSED" ? "#5b88ad" : undefined;
}

function edgePoint(snapshot: DispatcherSnapshot, edgeId: string, fraction: number) {
  const edge = snapshot.topology.edges.find((item) => item.id === edgeId);
  if (!edge) return null;
  const from = snapshot.topology.nodes.find((item) => item.id === edge.fromNodeId);
  const to = snapshot.topology.nodes.find((item) => item.id === edge.toNodeId);
  if (!from || !to || from.x === null || from.y === null || to.x === null || to.y === null) return null;
  const t = Math.max(0, Math.min(1, fraction));
  return { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t };
}

export function StationMap({ snapshot, setSelection, activeRouteId,
  preview, focusConflictId }: Props) {
  const nodes = snapshot.topology.nodes.filter((node): node is typeof node & { x: number; y: number } => node.x !== null && node.y !== null);
  const minX = Math.min(...nodes.map((node) => node.x ?? 0));
  const maxX = Math.max(...nodes.map((node) => node.x ?? 0));
  const minY = Math.min(...nodes.map((node) => node.y ?? 0));
  const maxY = Math.max(...nodes.map((node) => node.y ?? 0));
  const selectedRoute = snapshot.routes.find((route) => route.id === activeRouteId);
  const previewPlan = preview && snapshot.plans.find((plan) => plan.id === preview.planId);
  const previewVariant = previewPlan?.variants.find((variant) => variant.id === preview?.variantId);
  const previewTracks = new Set(previewVariant?.operations.map((operation) => String(operation.trackId)) ?? []);
  const focusConflict = snapshot.conflicts.find((conflict) => conflict.id === focusConflictId);
  const focusBlockId = String(focusConflict?.details.blockId ?? "");
  const focusTrackId = String(focusConflict?.details.trackId ??
    snapshot.blocks.find((block) => block.id === focusBlockId)?.trackId ?? "");
  const focusTrain = String(focusConflict?.details.trainId ?? "");
  const focusTrack = snapshot.tracks.find((track) => track.id === focusTrackId);
  const focusedY = focusTrack ? snapshot.topology.edges
    .filter((edge) => edge.trackId === focusTrack.id)
    .map((edge) => edgePoint(snapshot, edge.id, 0.5)?.y ?? null)
    .filter((value): value is number => value !== null)[0] : null;
  const viewY = focusedY ?? (minY + maxY) / 2;
  const viewHeight = focusedY === null ? maxY - minY + 90 : 160;
  const viewWidth = focusedY === null ? maxX - minX + 130 : 520;
  const viewX = focusedY === null ? minX - 70 : Math.max(minX - 70,
    Math.min(maxX - viewWidth + 60, (focusTrack ? minX + viewWidth / 2 : minX) - viewWidth / 2));
  const trainPoints = snapshot.trains.map((train) => ({
    train, point: train.headPosition.edgeId
      ? edgePoint(snapshot, train.headPosition.edgeId,
        train.headPosition.offsetM / Math.max(1,
          snapshot.topology.edges.find((edge) => edge.id === train.headPosition.edgeId)?.lengthM ?? 1))
      : null,
  }));
  const platformTracks = new Map<string, string[]>();
  for (const track of snapshot.topology.tracks) {
    for (const platformId of track.platformIds ?? []) {
      platformTracks.set(platformId, [...(platformTracks.get(platformId) ?? []), track.id]);
    }
  }

  return <svg className="station-map" data-testid="station-map" role="img"
    aria-label="Интерактивная схема станции" viewBox={`${viewX} ${viewY - viewHeight / 2} ${viewWidth} ${viewHeight}`}>
    <defs>
      <pattern id="map-grid" width="40" height="40" patternUnits="userSpaceOnUse">
        <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#20241f" strokeWidth="1" />
      </pattern>
    </defs>
    <rect x={minX - 70} y={minY - 45} width={maxX - minX + 130} height={maxY - minY + 90}
      fill="url(#map-grid)" />
    <text x={minX - 60} y={maxY + 5} className="map-direction">← ЗАПАД</text>
    <text x={maxX - 5} y={maxY + 5} className="map-direction">ВОСТОК →</text>
    <text x={(minX + maxX) / 2} y={minY - 22} className="map-zone">ПЛАТФОРМЫ И ГЛАВНЫЕ ПУТИ</text>
    <text x={(minX + maxX) / 2 - 200} y={(minY + maxY) / 2 + 5} className="map-zone">СОРТИРОВОЧНЫЙ ПАРК</text>
    <text x={(minX + maxX) / 2 - 200} y={maxY - 5} className="map-zone">ПТО И ГРУЗОВОЙ ФРОНТ</text>
    <g className="approach-links">
      {nodes.filter((node) => node.kind === "approach").map((node) => <circle key={node.id}
        cx={node.x} cy={node.y} r="5" />)}
    </g>
    {[...snapshot.topology.tracks].sort((a, b) => codeOrder(a.code) - codeOrder(b.code)).map((track, index) => {
      const trackEdges = snapshot.topology.edges.filter((edge) => edge.trackId === track.id);
      const status = snapshot.tracks.find((item) => item.id === track.id)?.status ?? "FREE";
      const currentRoute = snapshot.routes.find((route) => route.id === activeRouteId
        && route.edgeIds.some((id) => trackEdges.some((edge) => edge.id === id)));
      const routeColorValue = routeColor(currentRoute);
      const isPreview = previewTracks.has(track.id);
      return <g key={track.id} className="map-track" data-track-id={track.id}
        role="button" tabIndex={0} aria-label={`Путь ${track.code}: ${status}`}
        onClick={() => setSelection({ kind: "track", id: track.id })}
        onKeyDown={(event) => { if (event.key === "Enter") setSelection({ kind: "track", id: track.id }); }}>
        {trackEdges.map((edge) => {
          const from = nodes.find((node) => node.id === edge.fromNodeId);
          const to = nodes.find((node) => node.id === edge.toNodeId);
          if (!from || !to) return null;
          const block = snapshot.blocks.find((item) => item.id === edge.blockId);
          const conflict = snapshot.conflicts.some((item) => item.status === "OPEN"
            && JSON.stringify(item.details).includes(edge.blockId ?? "\u0000"));
          const color = conflict ? "#bc5b50" : routeColorValue ?? statusColor[block?.status ?? status] ?? statusColor.FREE;
          return <g key={edge.id} className="map-edge">
            {isPreview && <line x1={from.x} y1={from.y} x2={to.x} y2={to.y}
              className="route-preview-underlay" />}
            <line x1={from.x} y1={from.y} x2={to.x} y2={to.y}
              stroke={color} strokeWidth={routeColorValue || conflict || isPreview ? 5 : 3}
              strokeDasharray={isPreview && !routeColorValue ? "10 7" : undefined}
              data-block-id={edge.blockId ?? undefined} />
            <line x1={from.x} y1={from.y - 4} x2={to.x} y2={to.y - 4}
              stroke={color} strokeWidth="1.2" opacity=".7" />
          </g>;
        })}
        <rect x={(minX + maxX) / 2 - 18} y={trackEdges.length
          ? (edgePoint(snapshot, trackEdges[0].id, 0.5)?.y ?? minY) - 10
          : minY - 10} width="36" height="21" rx="6" className="track-label" />
        <text x={(minX + maxX) / 2} y={trackEdges.length
          ? (edgePoint(snapshot, trackEdges[0].id, 0.5)?.y ?? minY) + 4
          : minY + 4} className="track-label-text">{track.code.replace(/\D/g, "") || index + 1}</text>
        {track.platformIds?.length ? <title>{track.platformIds.map((id) =>
          snapshot.topology.platforms.find((platform) => platform.id === id)?.name).filter(Boolean).join(" · ")}</title> : null}
      </g>;
    })}
    {snapshot.topology.edges.filter((edge) => edge.trackId === null).map((edge) => {
      const from = nodes.find((node) => node.id === edge.fromNodeId);
      const to = nodes.find((node) => node.id === edge.toNodeId);
      return from && to ? <line key={edge.id} x1={from.x} y1={from.y} x2={to.x} y2={to.y}
        stroke="#51554e" strokeWidth="3" /> : null;
    })}
    {snapshot.topology.switches.map((sw) => {
      const node = nodes.find((item) => item.id === sw.nodeId);
      return node ? <g key={sw.id} className="map-object" role="button" tabIndex={0}
        aria-label={`Стрелка ${sw.code} ${sw.state}`} onClick={() => setSelection({ kind: "switch", id: sw.id })}>
        <circle cx={node.x} cy={node.y} r="8" fill={sw.isFailed ? "#bf5a50" : "#1b1e1a"}
          stroke={sw.isFailed ? "#bf5a50" : "#d29b55"} strokeWidth="2" />
        <text x={(node.x ?? 0) + 12} y={(node.y ?? 0) - 7} className="map-object-label">{sw.code}</text>
      </g> : null;
    })}
    {snapshot.topology.signals.map((signal) => {
      const point = edgePoint(snapshot, signal.edgeId, 0.82);
      return point ? <g key={signal.id} className="map-object" role="button" tabIndex={0}
        aria-label={`Сигнал ${signal.code} ${signal.aspect}`} onClick={() => setSelection({ kind: "signal", id: signal.id })}>
        <line x1={point.x} y1={point.y - 11} x2={point.x} y2={point.y + 2}
          stroke={statusColor[signal.aspect] ?? "#bfc1b8"} strokeWidth="2" />
        <circle cx={point.x} cy={point.y - 12} r="3.5" fill={statusColor[signal.aspect] ?? "#777"} />
      </g> : null;
    })}
    {trainPoints.map(({ train, point }) => point ? <g key={train.id} className="map-train"
      transform={`translate(${point.x} ${point.y})`}
      role="button" tabIndex={0} aria-label={`Поезд ${train.code}`} onClick={() => setSelection({ kind: "train", id: train.id })}>
      <rect x="-15" y="-8" width="30" height="16" rx="5"
        fill={train.delaySeconds > 0 ? "#bd753e" : "#287c9b"} stroke="#d9e0db" strokeWidth="1" />
      <text x="0" y="3" className="map-train-label">{train.code}</text>
    </g> : null)}
    {selectedRoute ? <g className="route-key"><rect x={minX + 20} y={minY - 40} width="112" height="22" rx="4" />
      <text x={minX + 32} y={minY - 25}>{selectedRoute.status === "ACTIVE" ? "ACTIVE ROUTE" : "PROPOSED ROUTE"}</text></g> : null}
    {previewVariant ? <g className="route-key preview-key"><rect x={minX + 140} y={minY - 40} width="142" height="22" rx="4" />
      <text x={minX + 152} y={minY - 25}>PROPOSED PREVIEW</text></g> : null}
    {focusTrain && <text x={minX + 20} y={minY - 18} className="map-focus-label">CONFLICT FOCUS · {focusTrain}</text>}
  </svg>;
}

export function MapInspector({ snapshot, selection, setActiveRouteId,
  onRequestRoute, canDispatch }: {
    snapshot: DispatcherSnapshot; selection: InspectorSelection;
    setActiveRouteId: (id: string | null) => void; onRequestRoute?: (trainId: string) => void;
    canDispatch: boolean;
  }) {
  if (!selection) return <div className="inspector-empty">Выберите поезд или объект станции</div>;
  if (selection.kind === "track") {
    const track = snapshot.tracks.find((item) => item.id === selection.id);
    if (!track) return null;
    const blocks = snapshot.blocks.filter((item) => item.trackId === track.id);
    const route = snapshot.routes.find((item) => item.status === "ACTIVE" && item.blockIds.some((id) => track.blockIds.includes(id)));
    const train = snapshot.trains.find((item) => blocks.some((block) => block.occupiedByTrainId === item.id));
    const reservation = snapshot.resources.reservations.find((item) => item.type === "TRACK" && item.resourceId === track.id);
    return <Inspector title={`Путь ${track.code}`} rows={[
      ["Статус", track.status], ["Занят поездом", train?.code ?? "—"],
      ["Зарезервирован", reservation?.trainId ?? reservation?.routeId ?? "—"],
      ["Длина", `${track.lengthM} м`], ["Блоки", blocks.map((item) => item.code).join(", ") || "—"],
      ["Активный маршрут", route?.id.slice(0, 8) ?? "—"],
      ["Ограничения", blocks.filter((item) => item.closureReason).map((item) => item.closureReason).join(", ") || "—"],
    ]} />;
  }
  if (selection.kind === "train") {
    const train = snapshot.trains.find((item) => item.id === selection.id);
    if (!train) return null;
    const route = snapshot.routes.find((item) => item.trainId === train.id && ["ACTIVE", "RESERVED", "PROPOSED"].includes(item.status));
    const schedule = snapshot.schedules.find((item) => item.trainId === train.id);
    const edge = snapshot.topology.edges.find((item) => item.id === train.headPosition.edgeId);
    const nextSignal = snapshot.topology.signals.find((item) => item.edgeId === edge?.id);
    return <><Inspector title={`Поезд ${train.code}`} rows={[
      ["Тип / статус", `${train.category} · ${train.status}`], ["Скорость", `${train.speedMps.toFixed(1)} м/с`],
      ["Задержка", `${Math.floor(train.delaySeconds / 60)} мин`],
      ["Текущий путь", snapshot.tracks.find((item) => item.id === train.currentTrackId)?.code ?? "Подход"],
      ["Занятые блоки", train.currentBlockIds.map((id) => snapshot.blocks.find((item) => item.id === id)?.code ?? id).join(", ") || "—"],
      ["Маршрут", route?.status ?? "—"], ["Следующий сигнал", nextSignal?.code ?? "—"],
      ["Назначение", train.destination ?? "—"], ["Расписание", schedule?.scheduledAt ?? "—"],
    ]} />{canDispatch && <button className="quiet-button" onClick={() => onRequestRoute?.(train.id)}>Задать маршрут</button>}</>;
  }
  if (selection.kind === "switch") {
    const sw = snapshot.topology.switches.find((item) => item.id === selection.id);
    const route = snapshot.routes.find((item) => item.switchIds.includes(selection.id) && ["ACTIVE", "RESERVED"].includes(item.status));
    return sw ? <Inspector title={`Стрелка ${sw.code}`} rows={[
      ["ID", sw.id], ["Положение", sw.state], ["Заблокирована", route ? "Да" : "Нет"],
      ["Отказ", sw.isFailed ? "Да" : "Нет"], ["Маршрут", route?.id.slice(0, 8) ?? "—"],
    ]} /> : null;
  }
  if (selection.kind === "signal") {
    const signal = snapshot.topology.signals.find((item) => item.id === selection.id);
    const route = snapshot.routes.find((item) => item.signalIds.includes(selection.id));
    return signal ? <Inspector title={`Сигнал ${signal.code}`} rows={[
      ["ID", signal.id], ["Аспект", signal.aspect], ["Блок", snapshot.topology.edges.find((item) => item.id === signal.edgeId)?.blockId ?? "—"],
      ["Маршрут", route?.id.slice(0, 8) ?? "—"], ["Причина", signal.reasonCodes.join(", ") || "—"],
    ]} /> : null;
  }
  if (selection.kind === "route") {
    const route = snapshot.routes.find((item) => item.id === selection.id);
    return route ? <><Inspector title={`Маршрут ${route.id.slice(0, 8)}`} rows={[
      ["Состояние", route.status === "ACTIVE" ? "ACTIVE ROUTE" : route.status],
      ["Ожидание", route.activationReason ?? "—"],
      ["Поезд", snapshot.trains.find((item) => item.id === route.trainId)?.code ?? "—"],
      ["Блоки", route.blockIds.map((id) => snapshot.blocks.find((item) => item.id === id)?.code ?? id).join(" → ")],
    ]} />{route.status === "PROPOSED" && canDispatch && <button className="quiet-button"
      onClick={() => setActiveRouteId(route.id)}>Показать маршрут</button>}</> : null;
  }
  const conflict = snapshot.conflicts.find((item) => item.id === selection.id);
  return conflict ? <Inspector title={`Конфликт · ${conflict.severity}`} rows={[
    ["Причина", conflict.kind], ["Статус", conflict.status],
    ["Связанные данные", Object.entries(conflict.details).map(([key, value]) => `${key}: ${String(value)}`).join(" · ")],
  ]} /> : null;
}

function Inspector({ title, rows }: { title: string; rows: [string, string][] }) {
  return <section className="inspector"><h3>{title}</h3>{rows.map(([key, value]) =>
    <div className="inspector-row" key={key}><span>{key}</span><strong>{value}</strong></div>)}</section>;
}
