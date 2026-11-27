import { useId, useMemo } from "react";
import { ChevronRight, Pentagon } from "lucide-react";
import { HEALTH_LABELS, type ApplianceHealth } from "./applianceHealth";
import { ANALYTICS_PLACEMENTS, analyticsWorkerState, isCameraView, VIDEO_ISSUES, type Workspace } from "./monitoringModel";
import type { SiteAssets } from "./useSiteAssets";
import type { VideoCheck } from "./VideoPosture";

const FILTERS = { attention: "Coverage at risk", checks: "Checks due", incidents: "Open incidents" } as const;
type Filter = keyof typeof FILTERS;
export type SiteInsightsView = { filter?: Filter; expanded?: string; limit: number };
export const DEFAULT_INSIGHTS_VIEW: SiteInsightsView = { limit: 4 };
type Props = {
  workspace: Workspace; healthById: ReadonlyMap<string, ApplianceHealth>; videoChecks?: ReadonlyMap<string, VideoCheck>;
  assets?: SiteAssets; assetsError?: string; version: number; now: number; synchronizedAt?: number; scoped: boolean;
  connection: "local" | "syncing" | "ready" | "offline"; onRefresh: () => void;
  view: SiteInsightsView; onView: (view: SiteInsightsView) => void;
  onBoundary: (id: string) => void; onCamera: (id: string) => void; onIncident: (id: string) => void;
};

const WORKER_LABELS: Record<ReturnType<typeof analyticsWorkerState>, string> = { waiting: "Waiting for worker", stale: "Worker stopped reporting",
  "old-policy": "Waiting for published rules", starting: "Starting", running: "Analyzing live video", "no-video": "Worker has no video",
  "model-error": "Model error", idle: "Worker idle", "delivery-delayed": "Event delivery delayed" };

function buildInsights(workspace: Workspace, health: Props["healthById"], video: Props["videoChecks"], assets: SiteAssets | undefined, version: number, now: number) {
  const devices = new Map(workspace.sensors.map((sensor) => [sensor.id, sensor]));
  const latestChecks = new Map<string, NonNullable<SiteAssets["recordingChecks"]>[number]>();
  const recentChecks = assets?.recordingChecks ?? assets?.commissioning.map((check) => ({ ...check, recording: check.checks?.recording ?? check.passed })) ?? [];
  for (const check of recentChecks) {
    const key = `${check.boundaryId}:${check.cameraId}`;
    if (check.version === version && !latestChecks.has(key)) latestChecks.set(key, check);
  }
  const incidentsByBoundary = new Map<string, Workspace["alerts"]>();
  for (const incident of workspace.alerts) {
    if (incident.status === "resolved" || incident.correlation) continue;
    const incidents = incidentsByBoundary.get(incident.zoneId) ?? [];
    incidents.push(incident);
    incidentsByBoundary.set(incident.zoneId, incidents);
  }
  return workspace.boundaries.map((boundary) => {
    const assigned = boundary.sensorIds.flatMap((id) => devices.get(id) ?? []);
    const impaired = assigned.filter((sensor) => {
      const status = health.get(sensor.id)?.status;
      return status === "offline" || status === "stale";
    });
    const cameras = assigned.filter(({ modality }) => modality === "vision").map((camera) => {
      const check = latestChecks.get(`${boundary.id}:${camera.id}`);
      const issue = camera.cameraInspection?.issue;
      const signal = video?.get(camera.id);
      const suspected = signal?.state === "receiving" && signal.integrity !== "clear" ? signal.integrity : undefined;
      const videoLabel = issue ? `Confirmed ${VIDEO_ISSUES[issue].toLowerCase()}` : signal?.state === "unavailable" ? "Video unavailable"
        : suspected ? `Suspected ${VIDEO_ISSUES[suspected].toLowerCase()}` : signal?.state === "receiving" ? "Video received" : "Video unverified";
      const fieldVerified = isCameraView(camera.cameraView) && !!camera.cameraVerifiedAt;
      const videoAtRisk = !!issue || !!suspected || signal?.state === "unavailable";
      const recordingVerified = check?.recording ?? false;
      const rules = camera.analyticsRules?.filter(({ boundaryId }) => boundaryId === boundary.id) ?? [];
      const worker = rules.length && version && boundary.enabled ? analyticsWorkerState(workspace.analyticsWorkers?.[camera.id], camera.analyticsPlacement, version, now) : undefined;
      return { camera, videoLabel, fieldVerified, videoAtRisk, check, recordingVerified, rules, worker, signal };
    });
    const verified = cameras.filter(({ camera, fieldVerified, videoAtRisk }) => fieldVerified && !videoAtRisk && !impaired.includes(camera)).length;
    const recording = cameras.filter(({ recordingVerified }) => recordingVerified).length;
    const cameraRisk = cameras.some(({ videoAtRisk, worker }) => videoAtRisk || !!worker && worker !== "running");
    const attention = boundary.enabled && (impaired.length > 0 || cameraRisk);
    const checks = boundary.enabled && (cameras.some(({ fieldVerified, recordingVerified }) => !fieldVerified || !!assets && !recordingVerified)
      || boundary.kind === "Restricted" && cameras.length === 0);
    return { boundary, assigned, cameras, verified, recording, impaired, attention, checks, incidents: incidentsByBoundary.get(boundary.id) ?? [] };
  }).sort((left, right) => Number(right.attention) - Number(left.attention) || Number(right.incidents.length > 0) - Number(left.incidents.length > 0)
    || Number(right.checks) - Number(left.checks) || left.boundary.name.localeCompare(right.boundary.name));
}

export function SiteInsights({ workspace, healthById, videoChecks, assets, assetsError, version, now, synchronizedAt, scoped, connection,
  view, onView, onRefresh, onBoundary, onCamera, onIncident }: Props) {
  const id = useId();
  const { filter, expanded, limit } = view;
  const rows = useMemo(() => buildInsights(workspace, healthById, videoChecks, assets, version, now),
    [workspace, healthById, videoChecks, assets, version, now]);
  const totals = { attention: rows.filter((row) => row.attention).length, checks: rows.filter((row) => row.checks).length,
    incidents: rows.reduce((sum, row) => sum + row.incidents.length, 0) };
  const visible = filter ? rows.filter((row) => filter === "incidents" ? row.incidents.length > 0 : row[filter]) : rows;
  const simulated = !version && workspace.sensors.some(({ demo }) => demo);
  const connectionText = connection === "local" ? (simulated ? "Local preview with simulated data" : "Local workspace")
    : connection === "offline" ? "Site disconnected. Showing the last available data."
      : connection === "syncing" ? "Syncing site data…" : version ? `Published site · Version ${version}` : "No site configuration published";

  return <section className="gv-site-insights" aria-label="Site insights">
    <header className="gv-insights-header">
      <div><h3>Site insights</h3><small role="status">{connectionText}{scoped ? " · Your assigned devices" : ""}
        {connection !== "local" && simulated ? " · Preview data" : ""}</small></div>
      {synchronizedAt && connection !== "ready" && <time dateTime={new Date(synchronizedAt).toISOString()}>
        Last synced {new Date(synchronizedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time>}
    </header>
    <div className="gv-insights-metrics" aria-label="Filter boundaries by insight">
      {(Object.entries(FILTERS) as [Filter, string][]).map(([key, label]) => <button type="button" key={key} aria-pressed={filter === key}
        onClick={() => onView({ ...DEFAULT_INSIGHTS_VIEW, filter: filter === key ? undefined : key })}><strong>{totals[key]}</strong><span>{label}</span></button>)}
    </div>
    <div className="gv-insights-status">
      <small role="status">{visible.length > limit ? `${limit} of ` : ""}{visible.length} {visible.length === 1 ? "boundary" : "boundaries"}{filter ? ` · ${FILTERS[filter]}` : " shown"}</small>
      {filter && <button type="button" className="gv-button" onClick={() => onView(DEFAULT_INSIGHTS_VIEW)}>Clear insights filter</button>}
      {assetsError && connection === "ready" && <button type="button" className="gv-button" onClick={onRefresh}>Retry site checks</button>}
    </div>
    {assetsError && connection === "ready" && <p role="status" className="gv-posture-note">{assets ? "Showing the last available recording checks." : "Recording checks are unavailable."} {assetsError}</p>}
    <div className="gv-insights-boundaries">
      {visible.slice(0, limit).map(({ boundary, assigned, cameras, verified, recording, impaired, attention, checks, incidents }) => {
        const open = expanded === boundary.id;
        const descriptionId = `${id}-${boundary.id}`;
        return <article key={boundary.id} className="gv-insights-boundary">
          <button className="gv-posture-row" type="button" data-focus-key={`insight:${boundary.id}`} aria-label={`Inspect ${boundary.name}`} aria-expanded={open}
            aria-describedby={descriptionId} onClick={() => onView({ ...view, expanded: open ? undefined : boundary.id })}>
            <Pentagon aria-hidden="true" size={16} /><span><strong>{boundary.name}</strong><small id={descriptionId}>{boundary.kind} · {assigned.length} {assigned.length === 1 ? "device" : "devices"}
              {incidents.length > 0 && ` · ${incidents.length} open`}</small>
              {(!boundary.enabled || attention || checks) && <small>{!boundary.enabled ? "Monitoring paused" : attention ? "Coverage at risk" : "Checks due"}</small>}</span>
            <ChevronRight aria-hidden="true" size={14} />
          </button>
          {open && <div className="gv-insights-detail" role="region" aria-label={`Readiness for ${boundary.name}`}>
            <div className="gv-detail-actions"><button type="button" className="gv-button" data-focus-key={`insight-boundary:${boundary.id}`}
              onClick={() => onBoundary(boundary.id)}>Open boundary</button></div>
            <dl className="gv-insights-checks">
              <div><dt>Verified views</dt><dd>{verified} of {cameras.length} assigned</dd></div>
              <div><dt>Recording checks</dt><dd>{assets ? `${recording} of ${cameras.length} verified` : version ? "Unverified" : "Available on published sites"}</dd></div>
            </dl>
            {!!impaired.length && <ul className="gv-posture-note">{impaired.map((sensor) => <li key={sensor.id}>
              {sensor.name} · {HEALTH_LABELS[healthById.get(sensor.id)?.status ?? "unknown"]}</li>)}</ul>}
            {!cameras.length && <p className="gv-posture-note">No camera assigned to this boundary.</p>}
            {cameras.map(({ camera, fieldVerified, videoLabel, check, recordingVerified, rules, worker, signal }) => <div className="gv-insights-device" key={camera.id}>
              <button type="button" className="gv-button" data-focus-key={`insight-camera:${boundary.id}:${camera.id}`} onClick={() => onCamera(camera.id)}>{camera.name}</button>
              <small>{isCameraView(camera.cameraView) ? "Modeled view" : "No modeled view"} · {fieldVerified
                ? "Field check recorded " + new Date(camera.cameraVerifiedAt!).toLocaleDateString() : "Field of view unverified"}</small>
              <small>{videoLabel}{signal?.lastVideoAt ? " · " + new Date(signal.lastVideoAt).toLocaleString() : ""}</small>
              {signal?.clockCheckedAt && <small>Camera clock offset {signal.clockOffsetSeconds ?? 0}s · {new Date(signal.clockCheckedAt).toLocaleString()}</small>}
              {version > 0 && <small>{recordingVerified ? "Recording verified by drill" : check ? "Recording follow-up required" : "Recording unverified"}
                {check && ` · ${new Date(check.createdAt).toLocaleDateString()}`}</small>}
              {worker && <small>{camera.analyticsPlacement ? `${ANALYTICS_PLACEMENTS[camera.analyticsPlacement]} · ` : ""}{WORKER_LABELS[worker]}</small>}
              {!!rules.length && <ul aria-label={`${camera.name} rules`}>{rules.map((rule) => {
                const review = assets?.validatedRules?.find((item) => item.cameraId === camera.id && item.ruleId === rule.id);
                return <li key={rule.id}>{rule.name} · {rule.mode === "alert" ? "Alert mode" : "Observe mode"}
                  <small>{review ? "Rule validated · " + new Date(review.createdAt).toLocaleDateString() : "Rule validation due"}</small></li>;
              })}</ul>}
            </div>)}
            {!!incidents.length && <ul className="gv-insights-incidents" aria-label="Open boundary incidents">{incidents.map((incident) => <li key={incident.id}>
              <button type="button" className="gv-button" data-focus-key={`insight-incident:${boundary.id}:${incident.id}`} onClick={() => onIncident(incident.id)}>{incident.observation.summary}</button>
            </li>)}</ul>}
          </div>}
        </article>;
      })}
      {!visible.length && <p className="gv-posture-note">{filter ? "No matching boundaries." : "Add a boundary to see site readiness."}</p>}
      {visible.length > limit && <button className="gv-button" type="button" onClick={() => onView({ ...view, limit: limit + 6 })}>Show more boundaries</button>}
    </div>
  </section>;
}
