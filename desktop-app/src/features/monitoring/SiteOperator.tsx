import { useEffect, useMemo, useState } from "react";
import { applyIncidentAction } from "./incidentModel";
import { parseHealthReport, scheduleMaintenance, stopMaintenance, type HealthReport, type MaintenanceWindow } from "./applianceHealth";
import type { Alert, IncomingObservation, IncidentAction, Sensor, SiteEvent, Workspace } from "./monitoringModel";
import { readSiteEndpoint } from "./SiteTelemetry";

export const SITE_ROLES = { viewer: "Viewer", operator: "Operator", supervisor: "Supervisor", "org-admin": "Organization admin" } as const;
export type SiteRole = keyof typeof SITE_ROLES;
export type SiteOperator = { site: string; siteId: string; timeZone: string; organization: { id: string; name: string }; name: string; subject: string; role: SiteRole; cameras: "*" | string[]; automaticTriageAvailable?: boolean };
export type SiteSession = { base: string; token: string; operator: SiteOperator; enterprise?: boolean };
export type SiteInventory = Pick<Workspace, "sensors" | "boundaries" | "floors" | "groups" | "dependencies" | "correlationRule" | "procedures">;
type MonitorUpdate = { publishedVersion: number; account: SiteOperator; scopeRevision: string; unchanged?: boolean; eventCursor?: number;
  healthCursor?: number; configuration?: SiteInventory; siteEvents?: SiteEvent[]; alerts?: Alert[]; health?: unknown[] };
type SiteMonitorUpdate = MonitorUpdate & { mediaCursor?: number; recordingCoverage?: Workspace["recordingCoverage"]; recoveryEnabled?: boolean;
  analyticsWorkers?: Workspace["analyticsWorkers"];
  cameraChecks?: Record<string, { cameraVerifiedAt?: string | null; cameraInspection?: Sensor["cameraInspection"] | null }> };
type SharedIncident = { resource: "incident"; sequence: number; revision: number; incidentId: string; at: string; action: IncidentAction };
type SharedMaintenance = { resource: "maintenance"; sequence: number; sensorId: string; mode: "schedule" | "stop"; startsAt: string; at: string; endsAt?: string; reason?: string };
type SharedAction = SharedIncident | SharedMaintenance;
type IncidentHistory = { alerts: Alert[]; actions: SharedAction[]; events: SiteEvent[] };

export function canViewSiteCamera(session: SiteSession, cameraId?: string | null) {
  return session.operator.cameras === "*" || !!cameraId && session.operator.cameras.includes(cameraId);
}

export function canManageSite(session?: SiteSession) {
  return session?.operator.cameras === "*" && (session.operator.role === "supervisor" || session.operator.role === "org-admin");
}

export class SiteRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

export async function siteRequest(session: Pick<SiteSession, "base" | "token">, path: string, body?: unknown, signal = AbortSignal.timeout(6000)) {
  const headers = new Headers(body ? { "Content-Type": "application/json" } : undefined);
  if (session.token) headers.set("Authorization", "Bearer " + session.token);
  const response = await fetch(new URL("api/site/" + path, session.base), { headers, signal, cache: "no-store", redirect: "error",
    credentials: "same-origin", ...(body ? { method: "POST", body: JSON.stringify(body) } : {}) });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new SiteRequestError(typeof payload.error === "string" ? payload.error : `Site gateway returned ${response.status}.`, response.status);
  return payload;
}

export async function connectSite(base: string, token: string): Promise<SiteSession> {
  const login = { base, token: token.trim() };
  const operator = await siteRequest(login, "session");
  if (!isSiteOperator(operator)) throw new Error("The gateway did not return a site account.");
  return { ...login, operator };
}

export function isSiteOperator(value?: Partial<SiteOperator>): value is SiteOperator {
  return !!value && typeof value.name === "string" && typeof value.site === "string" && typeof value.siteId === "string" && typeof value.subject === "string"
    && typeof value.organization?.id === "string" && typeof value.organization?.name === "string" && !!value.role && Object.prototype.hasOwnProperty.call(SITE_ROLES, value.role)
    && (value.cameras === "*" || Array.isArray(value.cameras) && value.cameras.every((camera) => typeof camera === "string"));
}

export function siteInventory(workspace: SiteInventory): SiteInventory {
  return {
    sensors: workspace.sensors.map(({ healthReport: _health, healthOutages: _outages, maintenanceWindows: _maintenance, ...sensor }) => ({
      ...sensor, floorId: sensor.floorId ?? undefined, cameraGatewayKind: sensor.cameraGatewayKind ?? undefined,
    })),
    boundaries: workspace.boundaries.map((value) => ({ ...value, floorId: value.floorId ?? undefined })),
    floors: workspace.floors ?? [], groups: workspace.groups, dependencies: workspace.dependencies ?? [], correlationRule: workspace.correlationRule,
    procedures: workspace.procedures,
  };
}

function portalMedia(value: string | null | undefined, base: string, path: RegExp) {
  if (!value) return null;
  try {
    const match = new URL(value).pathname.match(path);
    return match ? new URL(match[0].slice(1), base).href : null;
  } catch { return null; }
}

export function enterpriseWorkspace(workspace: Workspace, base: string): Workspace {
  function observation<T extends IncomingObservation>(value: T): T {
    if (!value.recording) return value;
    const url = portalMedia(value.recording.url, base, /\/streams\/[a-f0-9]{48}\/clips\/[a-f0-9]{32}\.mp4$/);
    return { ...value, recording: url ? { ...value.recording, url } : undefined };
  }
  return { ...workspace,
    sensors: workspace.sensors.map((sensor) => ({ ...sensor, demo: false,
      cameraFeedUrl: sensor.cameraGatewayKind ? portalMedia(sensor.cameraFeedUrl, base, /\/streams\/[a-f0-9]{48}\/index\.m3u8$/) : null,
      cameraGridUrl: sensor.cameraGatewayKind ? portalMedia(sensor.cameraGridUrl, base, /\/streams\/[a-f0-9]{48}\/grid\/index\.m3u8$/) : null })),
    alerts: workspace.alerts.map((alert) => ({ ...alert, observation: observation(alert.observation) })),
    siteEvents: workspace.siteEvents?.map((event) => ({ ...event, observation: observation(event.observation) })),
  };
}

function publishedWorkspace(update: SiteMonitorUpdate, local: Workspace, previous?: Workspace) {
  if (!update.configuration) return;
  const health = new Map<string, HealthReport>();
  for (const value of update.health ?? []) {
    try { const report = parseHealthReport(value, Date.now()); health.set(report.sensorId, report); } catch { /* Ignore an invalid report. */ }
  }
  const config = siteInventory(update.configuration);
  return { ...config, sensors: config.sensors.map((sensor) => ({ ...sensor, healthReport: health.get(sensor.id),
    cameraVerifiedAt: update.cameraChecks?.[sensor.id]?.cameraVerifiedAt ?? sensor.cameraVerifiedAt,
    cameraInspection: update.cameraChecks?.[sensor.id]?.cameraInspection ?? undefined })),
    dependencyHealth: Object.fromEntries((config.dependencies ?? []).flatMap(({ id }) => health.has(id) ? [[id, health.get(id)!]] : [])),
    recordingCoverage: update.recordingCoverage, recordingRecoveryEnabled: update.recoveryEnabled, analyticsWorkers: update.analyticsWorkers,
    alerts: (update.alerts ?? []).map((alert) => ({ ...alert, floorId: alert.floorId ?? undefined })),
    siteEvents: (update.siteEvents ?? []).map((event) => ({ ...event, floorId: event.floorId ?? undefined })),
    received: [], savedIncidentSearches: previous?.savedIncidentSearches ?? local.savedIncidentSearches,
  } satisfies Workspace;
}

function project(workspace: Workspace, site: SiteSession, actions: SharedAction[]) {
  const { cameras } = site.operator;
  const allowedSensors = workspace.sensors.filter((sensor) => sensor.modality !== "vision" || cameras === "*" || cameras.includes(sensor.id));
  const incidents = new Map<string, SharedIncident[]>();
  const allowedIds = new Set(allowedSensors.map(({ id }) => id));
  let result = { ...workspace, sensors: allowedSensors.map((sensor) => ({ ...sensor, maintenanceWindows: undefined as MaintenanceWindow[] | undefined })) };
  for (const entry of actions) {
    if (entry.resource === "incident") {
      const prior = incidents.get(entry.incidentId);
      if (prior) prior.push(entry); else incidents.set(entry.incidentId, [entry]);
    } else if (result.sensors.some(({ id }) => id === entry.sensorId)) {
      try {
        const updated = entry.mode === "schedule" ? scheduleMaintenance(result, entry.sensorId, Date.parse(entry.startsAt), Date.parse(entry.endsAt ?? ""), entry.reason ?? "", Date.parse(entry.at))
          : stopMaintenance(result, entry.sensorId, new Date(entry.startsAt).toISOString(), Date.parse(entry.at));
        result = { ...result, sensors: updated.sensors.map((sensor) => ({ ...sensor, maintenanceWindows: sensor.maintenanceWindows })) };
      } catch { /* Older schedules can expire before this workstation first connects. */ }
    }
  }
  const alerts = workspace.alerts.filter((alert) => canViewSiteCamera(site, alert.cameraId)).map((alert): Alert => {
    const baseline: Alert = { ...alert, status: "new", owner: undefined, reviewedAt: undefined, activity: undefined, handoff: undefined,
      responseSteps: undefined, disposition: undefined, evidenceBookmarks: undefined };
    return (incidents.get(alert.id) ?? []).reduce((state, { action, at, sequence }) => applyIncidentAction(state, action, at, sequence), baseline);
  });
  return { workspace: { ...result, alerts, siteEvents: workspace.siteEvents?.filter((event) => canViewSiteCamera(site, event.cameraId)),
    gatewayIncidents: workspace.gatewayIncidents?.filter((incident) => incident.devices.every((device) => allowedIds.has(device.sensorId))) }, incidents };
}

export function useSiteOperator(session: SiteSession | undefined, local: Workspace) {
  const [snapshot, setSnapshot] = useState<{ session: SiteSession; actions: SharedAction[]; status: "syncing" | "ready" | "offline" | "revoked";
    account?: SiteOperator; publishedVersion?: number; published?: Workspace; synchronizedAt?: number; history?: IncidentHistory }>();
  const current = snapshot?.session === session ? snapshot : undefined;
  const effective = useMemo(() => session && current?.account ? { ...session, operator: current.account } : session, [session, current?.account]);
  const projection = useMemo(() => {
    if (!effective || current?.status === "revoked") return;
    const base = current?.published ? { ...current.published, savedIncidentSearches: local.savedIncidentSearches } : local;
    const history = current?.history;
    const alerts = history ? [...base.alerts, ...history.alerts.filter((entry) => !base.alerts.some(({ id }) => id === entry.id))] : base.alerts;
    const siteEvents = history ? [...base.siteEvents ?? [], ...history.events] : base.siteEvents;
    return project({ ...base, alerts, siteEvents }, effective, current?.actions ?? []);
  }, [local, effective, current?.published, current?.actions, current?.status, current?.history]);

  function append(active: SiteSession, events: SharedAction[]) {
    setSnapshot((current) => {
      if (current?.session !== active) return current;
      const known = new Set(current.actions.map(({ sequence }) => sequence));
      const added = events.filter(({ sequence }) => !known.has(sequence));
      return added.length ? { ...current, actions: [...current.actions, ...added].sort((a, b) => a.sequence - b.sequence) } : current;
    });
  }

  useEffect(() => {
    if (!session) return;
    const active = session;
    const controller = new AbortController();
    let after = 0;
    let version = 0, event = 0, health = 0, media = 0, scope = "";
    let timer: number;
    setSnapshot({ session: active, actions: [], status: "syncing" });
    async function poll() {
      let delay = 3000;
      let status: "syncing" | "ready" | "offline" | "revoked" = "offline";
      try {
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(6500)]);
        const monitor = await siteRequest(active, `monitoring?${new URLSearchParams({ version: String(version), event: String(event), health: String(health), media: String(media), scope })}`, undefined, signal) as SiteMonitorUpdate;
        const scopeChanged = monitor.scopeRevision !== scope;
        const cursor = scopeChanged ? 0 : after;
        const page = await siteRequest(active, `activity?after=${cursor}`, undefined, signal);
        if (!Array.isArray(page.events) || !Number.isSafeInteger(page.after) || page.after < cursor) throw new Error("Invalid activity cursor.");
        if (controller.signal.aborted) return;
        version = monitor.publishedVersion;
        event = monitor.eventCursor ?? event;
        health = monitor.healthCursor ?? health;
        media = monitor.mediaCursor ?? media;
        scope = monitor.scopeRevision;
        after = page.after;
        delay = page.hasMore ? 50 : 3000;
        status = page.hasMore ? "syncing" : "ready";
        setSnapshot((previous) => {
          if (previous?.session !== active) return previous;
          const known = scopeChanged ? [] : previous.actions;
          const ids = new Set(known.map(({ sequence }) => sequence));
          const added = (page.events as SharedAction[]).filter(({ sequence }) => !ids.has(sequence));
          const actions = scopeChanged || added.length ? [...known, ...added].sort((a, b) => a.sequence - b.sequence) : previous.actions;
          const accountChanged = scopeChanged || monitor.account?.automaticTriageAvailable !== previous.account?.automaticTriageAvailable;
          return { ...previous, account: accountChanged ? monitor.account : previous.account, history: scopeChanged ? undefined : previous.history,
            actions, publishedVersion: monitor.publishedVersion, synchronizedAt: Date.now(),
            published: monitor.unchanged ? previous.published : publishedWorkspace(monitor, local, previous.published), status };
        });
      } catch (error) {
        if (error instanceof SiteRequestError && ([401, 403].includes(error.status) || active.enterprise && error.status === 404)) status = "revoked";
      }
      if (controller.signal.aborted) return;
      if (status === "revoked") setSnapshot({ session: active, actions: [], status });
      else {
        if (status === "offline") setSnapshot((current) => current?.session === active && current.status !== status ? { ...current, status } : current);
        timer = window.setTimeout(poll, delay);
      }
    }
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [session]);

  return {
    workspace: current?.status === "revoked" ? { sensors: [], boundaries: [], groups: [], alerts: [], received: [] } as Workspace : projection?.workspace ?? local,
    ready: current?.status === "ready",
    session: effective, publishedVersion: current?.publishedVersion ?? 0,
    status: current?.status, synchronizedAt: current?.synchronizedAt,
    async history(incidentId: string, signal?: AbortSignal) {
      if (!session) return;
      const history = await siteRequest(session, "incident-history?" + new URLSearchParams({ incidentId }), undefined, signal) as IncidentHistory;
      if (signal?.aborted) return;
      setSnapshot((previous) => previous?.session === session && previous.status === "ready" ? { ...previous, history } : previous);
      append(session, history.actions);
      return history.alerts[0];
    },
    async incident(alert: Alert, action: IncidentAction) {
      if (!session) return;
      const revision = projection?.incidents.get(alert.id)?.slice(-1)[0]?.revision ?? 0;
      append(session, [await siteRequest(session, "incident", { incidentId: alert.id, cameraId: alert.cameraId ?? null, priority: alert.severity, revision, action })]);
    },
    async maintenance(body: { sensorId: string; mode: "schedule" | "stop"; startsAt: string; endsAt?: string; reason?: string }) {
      if (session) append(session, [await siteRequest(session, "maintenance", body)]);
    },
  };
}

export function SiteOperatorPanel({ session, status, onChange }: { session?: SiteSession; status?: string; onChange: (session?: SiteSession) => void }) {
  const [origin, setOrigin] = useState("http://127.0.0.1:8765");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const base = readSiteEndpoint(origin);
  return <details className="gv-triage-section">
    <summary>Shared site operator access</summary>
    <div className="gv-triage-content">
      {session ? <><p role="status">{session.operator.name} · {session.operator.role} · {session.operator.site}</p>
        <p>{session.operator.organization.name}</p>
        <p>{status === "ready" ? "Shared incident activity is current." : status === "offline" ? "Site connection interrupted. Shared changes are paused."
          : status === "revoked" ? "Site access ended. Disconnect and sign in again to continue." : "Syncing shared incident activity…"}</p>
        <button type="button" className="gv-button" onClick={() => onChange()}>Disconnect operator</button></>
        : <form className="gv-incident-form" onSubmit={async (event) => {
          event.preventDefault(); if (!base) return;
          setBusy(true); setError("");
          try {
            onChange(await connectSite(base, token)); setToken("");
          } catch (issue) { setError(issue instanceof Error ? issue.message : "Unable to connect to the shared site."); }
          finally { setBusy(false); }
        }}>
          <label>Shared site gateway URL<input type="url" required value={origin} onChange={(event) => setOrigin(event.target.value)} /></label>
          <label>Site operator token<input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></label>
          <p>For a configured single sign-on session, use the same-origin gateway URL and leave the token empty.</p>
          <button type="submit" className="gv-button" disabled={!base || busy}>Connect operator</button>
          {error && <p role="alert">{error}</p>}
        </form>}
    </div>
  </details>;
}

export function SiteCameraViewer({ session, camera }: { session?: SiteSession; camera: Sensor }) {
  const [viewer, setViewer] = useState<{ session: SiteSession; url: string; expiresAt: number }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!session || session.enterprise || !camera.cameraGatewayKind || !canViewSiteCamera(session, camera.id)) return null;
  const current = viewer?.session === session && viewer.expiresAt * 1000 > Date.now() ? viewer : undefined;
  return <div className="gv-detail-actions">
    <button className="gv-button" type="button" disabled={busy} onClick={async () => {
      setBusy(true); setError("");
      try {
        const link = await siteRequest(session, "viewer", { deviceId: camera.id });
        if (typeof link.streamPath !== "string" || !/^view\/[a-f0-9]{48}\/index\.m3u8$/.test(link.streamPath) || !Number.isFinite(link.expiresAt)) {
          throw new Error("The gateway did not return an authorized camera session.");
        }
        const url = new URL("/camera-viewer", location.origin);
        url.hash = encodeURIComponent(new URL(link.streamPath, session.base).href);
        setViewer({ session, url: url.href, expiresAt: link.expiresAt });
      } catch (issue) { setError(issue instanceof Error ? issue.message : "Could not authorize camera viewing."); }
      finally { setBusy(false); }
    }}>Authorize live view</button>
    {current && <a className="gv-button" href={current.url} rel="noreferrer" target="_blank">Open authorized live view</a>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
