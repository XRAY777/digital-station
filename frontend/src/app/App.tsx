import { useCallback, useEffect, useMemo, useState } from "react";

import { Station3D } from "../dispatcher/Station3D";
import { acceptVariant, ApiError, askAssistant, command, exportReport, fetchDispatcherSnapshot, fetchReplay, login, readApi, rejectAIRecommendation, websocketUrl } from "../dispatcher/api";
import { MapInspector, StationMap } from "../dispatcher/StationMap";
import { applyRealtimeFrame } from "../dispatcher/realtime";
import { applyReplayProjection } from "../dispatcher/replay";
import type { AIRecommendation, DispatcherSnapshot, InspectorSelection } from "../dispatcher/types";
import { StationSocketClient, type FrameMessage, type StationMessage } from "../websocket/client";

type LinkStatus = "ONLINE" | "RECONNECTING" | "STALE DATA" | "OFFLINE";
const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function App() {
  const [snapshot, setSnapshot] = useState<DispatcherSnapshot | null>(null);
  const [error, setError] = useState("");
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<LinkStatus>("OFFLINE");
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const [view, setView] = useState<"2D" | "3D">("2D");
  const [selection, setSelection] = useState<InspectorSelection>(null);
  const [activeRouteId, setActiveRouteId] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ planId: string; variantId: string } | null>(null);
  const [focusConflictId, setFocusConflictId] = useState<string | null>(null);
  const [recommendation, setRecommendation] = useState<AIRecommendation | null>(null);
  const [routeTrainId, setRouteTrainId] = useState("");
  const [destinationNodeId, setDestinationNodeId] = useState("");
  const [showRouteForm, setShowRouteForm] = useState(false);
  const [adminInfo, setAdminInfo] = useState("");
  const [replayAt, setReplayAt] = useState<number | null>(null);
  const [replayProjection, setReplayProjection] = useState<{ timeMs: number; stateVersion: number; state: import("../dispatcher/replay").ReplayState } | null>(null);
  const [playingReplay, setPlayingReplay] = useState(false);
  const [auditRows, setAuditRows] = useState<Record<string, unknown>[]>([]);
  const [auditAction, setAuditAction] = useState("");
  const [auditResult, setAuditResult] = useState("");
  const [auditUser, setAuditUser] = useState("");
  const [auditEntity, setAuditEntity] = useState("");
  const [auditFrom, setAuditFrom] = useState("");
  const [auditTo, setAuditTo] = useState("");
  const runId = snapshot?.simulation.runId;
  const liveSimulationMs = snapshot?.simulation.simulationTimeMs;
  const replayActive = replayAt !== null;
  const stationId = snapshot?.station.id;

  const refresh = useCallback(async () => {
    const next = await fetchDispatcherSnapshot();
    setSnapshot(next);
    setLastUpdate(Date.now());
    setError("");
  }, []);

  useEffect(() => { queueMicrotask(() => void refresh().catch((reason: unknown) => {
    if (!(reason instanceof ApiError && reason.status === 401)) setError(String(reason));
  })); }, [refresh]);

  useEffect(() => {
    if (!stationId) return;
    let refreshTimer: number | undefined;
    const socket = new StationSocketClient(websocketUrl(stationId), stationId,
      (message: StationMessage) => {
        if (message.type === "SNAPSHOT") {
          void refresh().catch((reason: unknown) => setError(String(reason)));
        } else if ("messageType" in message && message.messageType === "FRAME") {
          setSnapshot((current) => current ? applyRealtimeFrame(current, message as FrameMessage) : current);
          setLastUpdate(Date.now());
          if (!message.type.startsWith("train.")) {
            window.clearTimeout(refreshTimer);
            refreshTimer = window.setTimeout(() => void refresh().catch((reason: unknown) => setError(String(reason))), 350);
          }
        }
      }, (stale) => { if (stale) setLink("STALE DATA"); }, undefined, Math.random, setLink);
    socket.connect();
    return () => { socket.close(); window.clearTimeout(refreshTimer); };
  }, [stationId, refresh]);

  useEffect(() => {
    if (!runId || replayAt === null) return;
    const controller = new AbortController();
    void fetchReplay(replayAt, controller.signal).then(setReplayProjection)
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, [runId, replayAt]);

  useEffect(() => {
    if (!playingReplay || !replayActive) return;
    const timer = window.setInterval(() => setReplayAt((time) => time === null ? null
      : Math.min(liveSimulationMs ?? time, time + 5_000)), 1000);
    return () => window.clearInterval(timer);
  }, [playingReplay, replayActive, liveSimulationMs]);

  const roles = snapshot?.user.roles ?? [];
  const canDispatch = roles.some((role) => role === "ADMIN" || role === "DISPATCHER");
  const liveActionsEnabled = canDispatch && replayAt === null;
  const isAdmin = roles.includes("ADMIN");
  const selectedTrain = selection?.kind === "train" ? selection.id : undefined;
  const staleRecommendation = !!recommendation && !!snapshot
    && recommendation.sourceStateVersion !== snapshot.simulation.stateVersion;
  const simulationDate = snapshot ? clock(snapshot.simulation.simulationTimeMs) : "--:--:--";
  const currentPlan = useMemo(() => snapshot?.plans.find((plan) => plan.id === preview?.planId), [snapshot?.plans, preview?.planId]);
  const displaySnapshot = useMemo(() => snapshot && replayProjection && replayAt !== null
    ? applyReplayProjection(snapshot, { timeMs: replayProjection.timeMs, state: replayProjection.state })
    : snapshot, [snapshot, replayProjection, replayAt]);
  const viewSnapshot = displaySnapshot ?? snapshot!;
  const activeRoute = viewSnapshot?.routes.find((route) => route.id === activeRouteId);

  async function loadAudit() {
    const params = new URLSearchParams({ limit: "100" });
    if (auditAction) params.set("action", auditAction);
    if (auditResult) params.set("result", auditResult);
    if (auditUser) params.set("userId", auditUser);
    if (auditEntity) params.set("entity", auditEntity);
    if (auditFrom) params.set("from", new Date(auditFrom).toISOString());
    if (auditTo) params.set("to", new Date(auditTo).toISOString());
    const result = await readApi<{ items: Record<string, unknown>[] }>(`/api/dispatcher/audit?${params}`);
    setAuditRows(result.items);
  }

  async function downloadReport(format: "csv" | "pdf") {
    try {
      const blob = await exportReport(format);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `station-report.${format}`; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function perform(action: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await action(); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  async function showAdminInfo(section: "USERS" | "SETTINGS" | "SYSTEM INFO") {
    if (!snapshot) return;
    setError("");
    try {
      let result: unknown;
      if (section === "USERS") result = await readApi("/api/users");
      else if (section === "SETTINGS") result = await readApi(`/api/stations/${snapshot.station.id}/efficiency-config`);
      else {
        const response = await fetch("/health", { credentials: "include" });
        result = { readinessStatus: response.status, services: await response.json(),
          stationStateVersion: snapshot.simulation.stateVersion };
      }
      setAdminInfo(JSON.stringify(result, null, 2));
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  if (!snapshot) return <main className="auth-shell"><form className="auth-card" onSubmit={(event) => {
    event.preventDefault(); setBusy(true); setError("");
    void login(loginName, password).then(refresh).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setBusy(false));
  }}><div className="brand-mark">DS</div><p className="eyebrow">DIGITAL STATION CONTROL</p><h1>Вход диспетчера</h1>
    {error && <div className="error-banner">{error}</div>}<label>Пользователь<input autoComplete="username" value={loginName} onChange={(e) => setLoginName(e.target.value)} required /></label>
    <label>Пароль<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
    <button className="primary-button" disabled={busy}>{busy ? "Подключение…" : "Войти"}</button>
  </form></main>;

  return <main className="control-room">
    <header className="topbar"><div className="brand"><div className="brand-mark">DS</div><div><strong>DIGITAL STATION</strong><small>{snapshot.station.name} · {snapshot.station.code}</small></div></div>
      <div className={`connection ${link.toLowerCase().replaceAll(" ", "-")}`}><i />{link}</div>
      {replayAt !== null && <div className="replay-badge">REPLAY MODE · {clock(replayAt)}</div>}
      <div className="header-metric"><small>SIMULATION</small><strong>{replayAt !== null ? clock(replayAt) : simulationDate} <span>{snapshot.simulation.status}</span></strong></div>
      <div className="header-metric efficiency"><small>STATION EFFICIENCY</small><strong>{Math.round(viewSnapshot.efficiency.score)}<span>/100</span></strong></div>
      <div className="header-meta"><small>STATE v{replayProjection?.stateVersion ?? snapshot.simulation.stateVersion}</small><small>UPDATED {lastUpdate ? clock(lastUpdate) : "—"}</small></div>
      <div className="report-buttons"><button onClick={() => void downloadReport("csv")}>CSV</button><button onClick={() => void downloadReport("pdf")}>PDF</button></div>
      <div className="role-pill">{roles.join(" · ") || "OBSERVER"}</div>
    </header>
    {error && <div className="error-banner inline-error" role="alert">{error}<button onClick={() => setError("")}>×</button></div>}
    <section className="workspace">
      <aside className="panel train-panel"><PanelTitle title="ПОЕЗДА" count={viewSnapshot.trains.length} /><div className="train-list">
        {viewSnapshot.trains.map((train) => <button key={train.id} className={`train-row ${selection?.kind === "train" && selection.id === train.id ? "selected" : ""}`} onClick={() => setSelection({ kind: "train", id: train.id })}>
          <span className="train-dot" data-delayed={train.delaySeconds > 0} /><span className="train-info"><b>{train.code}</b><small>{train.category} · {viewSnapshot.tracks.find((track) => track.id === train.currentTrackId)?.code ?? "APPROACH"}</small></span>
          <span className={train.delaySeconds > 0 ? "delay warning" : "delay"}>{train.delaySeconds > 0 ? `+${Math.ceil(train.delaySeconds / 60)}m` : "ON TIME"}</span>
        </button>)}
      </div><div className="mini-summary"><span>В ДВИЖЕНИИ <b>{viewSnapshot.trains.filter((t) => t.speedMps > 0).length}</b></span><span>С ЗАДЕРЖКОЙ <b>{viewSnapshot.trains.filter((t) => t.delaySeconds > 0).length}</b></span></div>
      {liveActionsEnabled && <div className="sim-controls"><small>SIMULATION CLOCK · x{snapshot.simulation.clockRate}</small><div>{["START", "PAUSE", "RESUME", "STOP", "RESET"].map((name) => <button key={name} disabled={busy} onClick={() => void perform(() => command("/api/simulation/control", { command: name }))}>{name}</button>)}</div><div>{[1, 2, 5, 10].map((rate) => <button key={rate} className={snapshot.simulation.clockRate === rate ? "selected" : ""} disabled={busy} onClick={() => void perform(() => command("/api/simulation/control", { command: "SET_SPEED", rate }))}>x{rate}</button>)}</div></div>}
      </aside>
      <section className="panel station-panel"><div className="station-toolbar"><div><span className="eyebrow">{replayAt === null ? "LIVE INFRASTRUCTURE" : "HISTORICAL PROJECTION"}</span><h2>{snapshot.station.name}</h2></div><div className="view-switch"><button className={view === "2D" ? "selected" : ""} onClick={() => setView("2D")}>2D DISPATCHER</button><button className={view === "3D" ? "selected" : ""} onClick={() => setView("3D")}>3D STATION</button></div></div>
        <div className="map-viewport">{view === "2D" ? <StationMap snapshot={viewSnapshot} selection={selection} setSelection={setSelection} activeRouteId={activeRouteId} setActiveRouteId={setActiveRouteId} preview={preview} focusConflictId={focusConflictId} onRequestRoute={(id) => { setRouteTrainId(id); setShowRouteForm(true); }} canDispatch={liveActionsEnabled} /> : <Station3D snapshot={viewSnapshot} selection={selection} setSelection={setSelection} />}</div>
        <div className="map-legend"><span><i className="free" />FREE</span><span><i className="reserved" />RESERVED</span><span><i className="safe" />ACTIVE ROUTE</span><span><i className="conflict" />CONFLICT</span>{activeRoute && <button onClick={() => setActiveRouteId(null)}>CLEAR ROUTE · {activeRoute.status}</button>}</div>
      </section>
      <aside className="right-column">{liveActionsEnabled && <DemoControls snapshot={snapshot} busy={busy} onApply={perform} />}<section className="panel"><PanelTitle title="ALERTS" count={viewSnapshot.incidents.filter((x) => x.status !== "RESOLVED").length} />{viewSnapshot.incidents.filter((x) => x.status !== "RESOLVED").slice(0, 4).map((incident) => <div className="alert-row" key={incident.id}><b>{incident.kind} · {incident.status}</b><span>{String(incident.payload.summary ?? "Open incident")}</span>{liveActionsEnabled && incident.status === "OPEN" && <button disabled={busy} onClick={() => void perform(() => command(`/api/dispatcher/incidents/${incident.id}/acknowledge`, {}))}>ACK</button>}{liveActionsEnabled && <button disabled={busy} onClick={() => void perform(() => command(`/api/dispatcher/incidents/${incident.id}/resolve`, {}))}>RESOLVE</button>}<ReplanSummary snapshot={viewSnapshot} incident={incident} /></div>)}{viewSnapshot.incidents.every((x) => x.status === "RESOLVED") && <Empty>Нет открытых инцидентов</Empty>}{liveActionsEnabled && <button className="quiet-button full-button" onClick={() => void perform(() => command("/api/dispatcher/incidents", { kind: "DISPATCHER_NOTE", severity: "INFO", summary: "Запись диспетчера", trainIds: [], trackIds: [] }))}>＋ Добавить инцидент</button>}</section>
        <section className="panel conflicts-panel"><PanelTitle title="CONFLICTS" count={viewSnapshot.conflicts.filter((item) => item.status === "OPEN").length} />{viewSnapshot.conflicts.filter((item) => item.status === "OPEN").slice(0, 4).map((conflict) => <button className="conflict-row" key={conflict.id} onClick={() => { setSelection({ kind: "conflict", id: conflict.id }); setFocusConflictId(conflict.id); }}><span className={`severity ${conflict.severity.toLowerCase()}`} /> <span><b>{conflict.kind}</b><small>{Object.values(conflict.details).slice(0, 2).join(" · ")}</small></span><span className="conflict-severity">{conflict.severity}</span><span className="show-map" onClick={(e) => { e.stopPropagation(); setSelection({ kind: "conflict", id: conflict.id }); setFocusConflictId(conflict.id); }}>SHOW ON MAP</span></button>)}{!viewSnapshot.conflicts.some((item) => item.status === "OPEN") && <Empty>Нет активных конфликтов</Empty>}</section>
        <section className="panel ai-panel"><PanelTitle title="AI DISPATCH ASSISTANT" badge={recommendation?.provider === "OFFLINE_FALLBACK" || recommendation?.provider === "OFFLINE" || (!recommendation && snapshot.ai?.status === "OFFLINE") ? "AI OFFLINE" : undefined} />{recommendation ? <><h3>{recommendation.summary}</h3><p>{recommendation.problem} · {recommendation.reasoningSummary}</p>{staleRecommendation && <div className="warning-banner">Состояние изменилось. ACCEPT будет повторно проверен сервером.</div>}<div className="ai-meta">Риски: {recommendation.risks.join("; ") || "не указаны"}</div>{recommendation.recommendedVariantId && <div className="ai-actions"><button onClick={() => { const plan = snapshot.plans.find((p) => p.variants.some((v) => v.id === recommendation.recommendedVariantId)); if (plan) { setPreview({ planId: plan.id, variantId: recommendation.recommendedVariantId! }); setActiveRouteId(null); } }}>SHOW PREVIEW</button><button className="primary-button" disabled={!liveActionsEnabled || busy || !recommendation.planId} onClick={() => recommendation.planId && recommendation.recommendedVariantId && void perform(async () => { await acceptVariant(recommendation.planId!, recommendation.recommendedVariantId!); setRecommendation(null); })}>ACCEPT</button><button disabled={!liveActionsEnabled || busy} onClick={() => void perform(async () => { await rejectAIRecommendation(recommendation.id); setRecommendation(null); })}>REJECT</button></div>}</> : <><p>Рекомендации по текущим данным станции.</p><button className="quiet-button full-button" disabled={busy || replayAt !== null} onClick={() => void askAssistant(selectedTrain, activeRouteId ?? undefined).then(setRecommendation).catch((reason: unknown) => setError(String(reason)))}>Спросить AI</button></>}</section>
      </aside>
    </section>
    <ReplayBar snapshot={snapshot} replayAt={replayAt} setReplayAt={setReplayAt} playing={playingReplay} setPlaying={setPlayingReplay} onReturnLive={() => { setPlayingReplay(false); setReplayAt(null); setReplayProjection(null); void refresh().catch((reason: unknown) => setError(String(reason))); }} />
    <section className="bottom-grid"><section className="panel"><PanelTitle title="INSPECTOR" /> <MapInspector snapshot={viewSnapshot} selection={selection} setActiveRouteId={setActiveRouteId} onRequestRoute={(id) => { setRouteTrainId(id); setShowRouteForm(true); }} canDispatch={liveActionsEnabled} /></section>
      <section className="panel"><PanelTitle title="ROUTES" count={viewSnapshot.routes.length} />{liveActionsEnabled && <button className="quiet-button" onClick={() => { setRouteTrainId(snapshot.trains[0]?.id ?? ""); setShowRouteForm(true); }}>＋ Новый маршрут</button>}<div className="compact-list">{viewSnapshot.routes.slice(0, 5).map((route) => <button key={route.id} className="compact-row" onClick={() => { setActiveRouteId(route.id); setPreview(null); setSelection({ kind: "route", id: route.id }); }}><b>{viewSnapshot.trains.find((t) => t.id === route.trainId)?.code ?? route.trainId.slice(0, 8)}</b><span className={route.status === "ACTIVE" ? "tag safe-tag" : "tag"}>{route.status}</span><small>{route.activationReason ?? (route.status === "RESERVED" ? "Waiting for activation" : `${route.blockIds.length} blocks`)}</small>{route.status === "PROPOSED" && liveActionsEnabled && <span onClick={(e) => { e.stopPropagation(); void perform(() => command(`/api/routes/${route.id}/reserve`, {})); }}>RESERVE</span>}</button>)}</div></section>
      <section className="panel"><PanelTitle title="OPTIMIZATION PLANS" count={viewSnapshot.plans.length} />{viewSnapshot.plans.slice(0, 4).map((plan) => <div className="plan-row" key={plan.id}><b>{plan.status}</b><small>v{plan.sourceStateVersion} · {plan.variants.length} variants</small>{plan.variants.slice(0, 2).map((variant) => <div className="plan-actions" key={variant.id}><button onClick={() => { setPreview({ planId: plan.id, variantId: variant.id }); setActiveRouteId(null); }}>{`PREVIEW #${variant.ordinal} · ${variant.score.toFixed(1)}`}</button>{liveActionsEnabled && <button disabled={busy || plan.sourceStateVersion !== snapshot.simulation.stateVersion} onClick={() => void perform(async () => { await acceptVariant(plan.id, variant.id); setPreview(null); })}>APPLY</button>}</div>)}</div>)}{currentPlan && <small className="muted">PREVIEW ONLY — {currentPlan.id.slice(0, 8)}</small>}</section>
      <section className="panel"><PanelTitle title="EVENTS / TIMELINE" count={viewSnapshot.events.length} /><div className="event-list">{[...viewSnapshot.events].reverse().slice(0, 7).map((event) => <div className="event-row" key={event.eventId}><time>{clock(event.simulationTimeMs)}</time><b>{event.type}</b><small>{event.entityId?.slice(0, 8) ?? "system"}</small></div>)}</div></section>
      <section className="panel"><PanelTitle title="RESOURCES" count={viewSnapshot.resources.reservations.length} /><div className="resource-stats"><span>LOCOMOTIVES <b>{viewSnapshot.resources.locomotives.filter((x) => x.status === "AVAILABLE").length}/{viewSnapshot.resources.locomotives.length}</b></span><span>CREWS <b>{viewSnapshot.resources.crews.filter((x) => x.status === "AVAILABLE").length}/{viewSnapshot.resources.crews.length}</b></span><span>PLATFORMS <b>{viewSnapshot.resources.platforms.length}</b></span></div>{viewSnapshot.resources.reservations.slice(0, 3).map((r) => <small className="reservation" key={r.id}>{r.type} · {r.status}</small>)}</section>
    </section>
    {canDispatch && <section className="panel audit-panel"><div className="audit-heading"><PanelTitle title="AUDIT LOG" count={auditRows.length} /><button onClick={() => void loadAudit().catch((reason: unknown) => setError(String(reason)))}>Загрузить</button></div><div className="audit-filters"><input aria-label="From date" type="datetime-local" value={auditFrom} onChange={(e) => setAuditFrom(e.target.value)} /><input aria-label="To date" type="datetime-local" value={auditTo} onChange={(e) => setAuditTo(e.target.value)} /><input aria-label="User ID filter" placeholder="User ID" value={auditUser} onChange={(e) => setAuditUser(e.target.value)} /><input aria-label="Entity type filter" placeholder="Entity type" value={auditEntity} onChange={(e) => setAuditEntity(e.target.value)} /><input aria-label="Action filter" placeholder="Action" value={auditAction} onChange={(e) => setAuditAction(e.target.value)} /><select aria-label="Result filter" value={auditResult} onChange={(e) => setAuditResult(e.target.value)}><option value="">All results</option><option value="SUCCESS">Success</option><option value="DENIED">Denied</option><option value="FAILED">Failed</option><option value="FAILURE">Failure</option></select></div><div className="audit-list">{auditRows.map((row, index) => <div className="audit-row" key={`${String(row.action)}-${String(row.entityId)}-${index}`}><b>{String(row.action)}</b><span>{String(row.timestamp ?? "")} · {String(row.username ?? "system")} ({String(row.role ?? "system")})</span><small>{String(row.entityType ?? "")} · {String(row.entityId ?? "")} · {String(row.result)} · v{String(row.stateVersion ?? "-")}</small><details><summary>Детали</summary><pre>{JSON.stringify({ oldValue: row.oldValue, newValue: row.newValue, requestId: row.requestId, sessionId: row.sessionId, ip: row.ip }, null, 2)}</pre></details></div>)}{auditRows.length === 0 && <Empty>Нажмите «Загрузить» для просмотра разрешённых записей</Empty>}</div></section>}
    {isAdmin && <footer className="admin-footer">ADMIN SYSTEM AREA · <button onClick={() => void showAdminInfo("SYSTEM INFO")}>SYSTEM INFO</button><button onClick={() => void showAdminInfo("USERS")}>USERS</button><button onClick={() => void showAdminInfo("SETTINGS")}>SETTINGS</button>{adminInfo && <pre className="admin-info">{adminInfo}</pre>}</footer>}
    {showRouteForm && liveActionsEnabled && <div className="modal-backdrop" role="presentation" onClick={() => setShowRouteForm(false)}><form className="route-modal" onSubmit={(event) => { event.preventDefault(); void perform(async () => { await command("/api/routes", { train_id: routeTrainId, destination_node_id: destinationNodeId }); setShowRouteForm(false); }); }} onClick={(e) => e.stopPropagation()}><h2>Новый маршрут</h2><label>Поезд<select value={routeTrainId} onChange={(e) => setRouteTrainId(e.target.value)}>{snapshot.trains.map((train) => <option key={train.id} value={train.id}>{train.code}</option>)}</select></label><label>Узел назначения<select required value={destinationNodeId} onChange={(e) => setDestinationNodeId(e.target.value)}><option value="">Выберите узел</option>{snapshot.topology.nodes.map((node) => <option key={node.id} value={node.id}>{node.code} · {node.kind}</option>)}</select></label><div><button type="button" onClick={() => setShowRouteForm(false)}>Отмена</button><button className="primary-button" disabled={busy || !routeTrainId}>Проверить и создать</button></div></form></div>}
  </main>;
}

function PanelTitle({ title, count, badge }: { title: string; count?: number; badge?: string }) {
  return <div className="panel-title"><h3>{title}</h3>{badge && <span className="tag warning-tag">{badge}</span>}{count !== undefined && <span className="count">{count}</span>}</div>;
}
function Empty({ children }: { children: string }) { return <div className="empty-state">{children}</div>; }

function DemoControls({ snapshot, busy, onApply }: {
  snapshot: DispatcherSnapshot;
  busy: boolean;
  onApply: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [trainId, setTrainId] = useState("");
  const [trackId, setTrackId] = useState("");
  const [delayMinutes, setDelayMinutes] = useState(2);
  const chosenTrain = trainId || snapshot.trains.find((train) => train.source === "WEST" && train.category === "PASSENGER")?.id || snapshot.trains[0]?.id || "";
  const chosenTrack = trackId || snapshot.tracks.find((track) => track.code === "02")?.id || snapshot.tracks[0]?.id || "";
  return <section className="panel demo-controls"><PanelTitle title="DEMO COMMANDS" />
    <label>Поезд<select aria-label="Поезд для задержки" value={chosenTrain} onChange={(event) => setTrainId(event.target.value)}>{snapshot.trains.map((train) => <option key={train.id} value={train.id}>{train.code}</option>)}</select></label>
    <label>Задержка, мин<input aria-label="Задержка в минутах" type="number" min="1" max="1440" value={delayMinutes} onChange={(event) => setDelayMinutes(Number(event.target.value))} /></label>
    <button disabled={busy || !chosenTrain || !Number.isInteger(delayMinutes) || delayMinutes < 1 || delayMinutes > 1440}
      onClick={() => void onApply(() => command("/api/dispatcher/incidents", { kind: "TRAIN_DELAY", severity: "WARNING", summary: "Демонстрационная задержка", trainIds: [chosenTrain], metadata: { delaySeconds: delayMinutes * 60 } }))}>Ввести задержку</button>
    <label>Путь<select aria-label="Путь для закрытия" value={chosenTrack} onChange={(event) => setTrackId(event.target.value)}>{snapshot.tracks.map((track) => <option key={track.id} value={track.id}>{track.code}</option>)}</select></label>
    <button disabled={busy || !chosenTrack} onClick={() => void onApply(() => command("/api/dispatcher/incidents", { kind: "TRACK_CLOSURE", severity: "CRITICAL", summary: "Демонстрационное закрытие пути", trackIds: [chosenTrack] }))}>Закрыть путь</button>
    <button disabled={busy} onClick={() => void onApply(() => command(`/api/runs/${snapshot.simulation.runId}/optimization`, {}))}>Рассчитать варианты</button>
  </section>;
}

function ReplanSummary({ snapshot, incident }: {
  snapshot: DispatcherSnapshot;
  incident: DispatcherSnapshot["incidents"][number];
}) {
  const result = incident.payload.replanning as Record<string, unknown> | undefined;
  if (!result) return null;
  const oldPlanId = typeof result.previousPlanId === "string" ? result.previousPlanId : null;
  const newPlanId = typeof result.newPlanId === "string" ? result.newPlanId : null;
  const oldPlan = snapshot.plans.find((plan) => plan.id === oldPlanId);
  const newPlan = snapshot.plans.find((plan) => plan.id === newPlanId);
  const delayBefore = Number(result.expectedDelayBefore ?? 0);
  const delayAfter = Number(result.expectedDelayAfter ?? delayBefore);
  return <details className="replan-summary"><summary>План до / после</summary><div><b>СТАРЫЙ</b><span>{oldPlan?.id.slice(0, 8) ?? "—"} · {oldPlan?.variants.length ?? 0} вариантов</span><small>Задержка {Math.round(delayBefore / 60)} мин</small></div><div><b>НОВЫЙ</b><span>{newPlan?.id.slice(0, 8) ?? "—"} · {newPlan?.variants.length ?? 0} вариантов</span><small>Задержка {Math.round(delayAfter / 60)} мин</small></div><small>Перепланирование подготовлено; live state не применён.</small></details>;
}

function ReplayBar({ snapshot, replayAt, setReplayAt, playing, setPlaying, onReturnLive }: {
  snapshot: DispatcherSnapshot; replayAt: number | null; setReplayAt: (time: number | null) => void;
  playing: boolean; setPlaying: (value: boolean) => void; onReturnLive: () => void;
}) {
  const live = snapshot.simulation.simulationTimeMs;
  const start = Math.max(0, live - 15 * 60_000);
  const cursor = replayAt ?? live;
  const marked = snapshot.events.filter((event) => event.simulationTimeMs >= start && event.simulationTimeMs <= live);
  return <section className="panel replay-panel"><div className="replay-controls"><b>{replayAt === null ? "LIVE" : "REPLAY MODE"}</b><button onClick={onReturnLive}>RETURN TO LIVE</button><button disabled={replayAt === null} onClick={() => setPlaying(!playing)}>{playing ? "PAUSE" : "PLAY"}</button><button disabled={replayAt === null || cursor <= start} onClick={() => setReplayAt(Math.max(start, cursor - 60_000))}>−1 min</button><button disabled={replayAt === null || cursor >= live} onClick={() => setReplayAt(Math.min(live, cursor + 60_000))}>+1 min</button><time>{clock(cursor)}</time></div><input aria-label="Replay timeline" type="range" min={start} max={Math.max(start + 1, live)} value={cursor} onChange={(e) => { setPlaying(false); setReplayAt(Number(e.target.value)); }} /><div className="timeline-marks">{marked.slice(-24).map((event) => <span key={event.eventId} title={`${event.type} · ${clock(event.simulationTimeMs)}`} data-type={event.type} />)}</div></section>;
}
