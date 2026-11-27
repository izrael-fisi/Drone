import { useEffect, useState } from "react";
import { ObservationRecording } from "./CctvReview";
import { SharedDockSchedule } from "./SharedDockSchedule";
import type { SiteSession } from "./SiteOperator";
import { analyticsWorkerState, type Workspace } from "./monitoringModel";

function elapsed(seconds: number) {
  const value = Math.max(0, Math.floor(seconds));
  if (value < 60) return `${value} s`;
  return `${Math.floor(value / 60)} min ${value % 60} s`;
}

export function DockOccupancyPanel({ workspace, version, ready, boundaryId, onCamera, session }: {
  workspace: Workspace; version: number; ready: boolean; boundaryId?: string; onCamera: (id: string) => void; session?: SiteSession;
}) {
  const [now, setNow] = useState(Date.now);
  const [viewing, setViewing] = useState("");
  const docks = workspace.sensors.flatMap((camera) => (camera.analyticsRules ?? [])
    .filter((rule) => rule.trackOccupancy && (!boundaryId || rule.boundaryId === boundaryId))
    .map((rule) => ({ camera, rule, boundary: workspace.boundaries.find(({ id }) => id === rule.boundaryId) })));
  const hasDocks = docks.length > 0;
  useEffect(() => {
    if (!hasDocks) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasDocks]);
  if (!hasDocks) return null;
  return <section className="gv-triage-content" aria-label="Dock occupancy">
    <h4>Dock occupancy</h4>
    <ul className="gv-triage-records">{docks.map(({ camera, rule, boundary }) => {
      const worker = workspace.analyticsWorkers?.[camera.id];
      const snapshot = worker?.dockOccupancy?.find(({ ruleId }) => ruleId === rule.id);
      const current = ready && boundary?.enabled && analyticsWorkerState(worker, camera.analyticsPlacement, version, now) === "running"
        && snapshot && now - snapshot.sampledAt * 1000 <= 15_000 ? snapshot : undefined;
      const state = current?.state === "occupied" ? "Occupied" : current?.state === "vacant" ? "Vacant" : "Unknown";
      const event = current?.arrivalEventId ? workspace.siteEvents?.find((item) =>
        item.cameraId === camera.id && item.observation.id === current.arrivalEventId &&
        item.observation.analytics?.ruleId === rule.id && item.observation.analytics.policyVersion === version) : undefined;
      const key = camera.id + "/" + rule.id;
      const open = event && viewing === event.observation.id;
      return <li key={key}>
        <strong>{boundary?.name ?? rule.name} · {state}</strong>
        <small>{camera.name} · {rule.name}</small>
        {state === "Occupied" && current?.changedAt !== undefined &&
          <small>Observed occupied for {elapsed(now / 1000 - current.changedAt)}</small>}
        <small>{current ? `Sampled ${elapsed(now / 1000 - current.sampledAt)} ago` : "Waiting for a current camera sample"}</small>
        <div className="gv-detail-actions">
          <button type="button" className="gv-button" onClick={() => onCamera(camera.id)}>Open camera</button>
          {event && <button type="button" className="gv-button" aria-expanded={!!open}
            onClick={() => setViewing(open ? "" : event.observation.id)}>{open ? "Close arrival recording" : "Review arrival"}</button>}
        </div>
        {open && event && <ObservationRecording event={event} camera={camera} />}
      </li>;
    })}</ul>
    {!boundaryId && ready && session && <SharedDockSchedule session={session} />}
  </section>;
}
