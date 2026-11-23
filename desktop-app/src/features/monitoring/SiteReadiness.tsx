import { useMemo, useState } from "react";
import { ChevronRight, Network, Pentagon } from "lucide-react";
import { calculateCameraCoverage, coveragePercent } from "./cameraCoverage";
import { getApplianceHealth, type ApplianceHealth } from "./applianceHealth";
import { httpEndpointUrl, type Alert, type Boundary, type SiteDependency, type Workspace } from "./monitoringModel";
import { canManageSite, siteRequest, type SiteSession } from "./SiteOperator";
import type { SiteAssets } from "./useSiteAssets";
const NO_DEPENDENCIES: SiteDependency[] = [];
const DRILL_STEPS = { door: "Observed the door event", incident: "Confirmed the linked incident and boundary",
  recording: "Played the camera clip at the event time", notification: "Confirmed delivery at the test destination" } as const;
const OUTAGE_STATES = new Set(["offline", "stale"]);

export function SiteReadiness({ session, workspace, health, version, now, editing, assets, refreshAssets, onDependency, onBoundary, onIncident }: {
  session: SiteSession; workspace: Workspace; health: ReadonlyMap<string, ApplianceHealth>; version: number; now: number; editing: boolean;
  assets?: SiteAssets; refreshAssets: () => void;
  onDependency: (dependency: SiteDependency) => void; onBoundary: (boundary: Boundary) => void; onIncident: (alert: Alert) => void;
}) {
  const dependencies = workspace.dependencies ?? NO_DEPENDENCIES;
  const [scenario, setScenario] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [selectedDoor, setSelectedDoor] = useState("");
  const [boundaryId, setBoundaryId] = useState("");
  const [delivery, setDelivery] = useState("");
  const manager = canManageSite(session);
  const door = workspace.sensors.find(({ id }) => id === selectedDoor);
  const candidateBoundaries = door?.linkedCameraId ? workspace.boundaries.filter((boundary) => boundary.enabled
    && boundary.sensorIds.includes(door.id) && boundary.sensorIds.includes(door.linkedCameraId!)) : [];
  const targetBoundary = candidateBoundaries.find(({ id }) => id === boundaryId) ?? candidateBoundaries[0];

  const scenarios = useMemo(() => {
    if (!scenario) return [];
    const [type, id] = scenario.split(":");
    const cameras = workspace.sensors.filter((sensor) => sensor.modality === "vision" && !sensor.cameraInspection
      && !OUTAGE_STATES.has(health.get(sensor.id)?.status ?? "unknown"));
    const verified = new Set(assets?.commissioning.filter((record) => record.passed && record.version === version).map((record) => record.cameraId));
    const remove = new Set(cameras.filter((camera) => type === "camera" ? camera.id === id : camera.dependencyIds?.includes(id)).map(({ id }) => id));
    const result = [];
    for (const floor of new Set(workspace.boundaries.map((area) => area.floorId))) {
      const zones = workspace.boundaries.filter((area) => area.floorId === floor);
      const source = cameras.filter((camera) => camera.floorId === floor);
      const checked = source.filter((camera) => camera.cameraVerifiedAt || verified.has(camera.id));
      try {
        const gap = calculateCameraCoverage(source, zones).gapFractionByBoundary;
        const after = calculateCameraCoverage(source.filter((camera) => !remove.has(camera.id)), zones).gapFractionByBoundary;
        const known = calculateCameraCoverage(checked, zones).gapFractionByBoundary;
        const remaining = calculateCameraCoverage(checked.filter((camera) => !remove.has(camera.id)), zones).gapFractionByBoundary;
        for (const zone of zones.filter(({ enabled }) => enabled)) {
          const before = gap.get(zone.id) ?? 1;
          const next = after.get(zone.id) ?? 1;
          const lostVerified = (known.get(zone.id) ?? 1) < .9999 && (remaining.get(zone.id) ?? 1) >= .9999;
          if (next > before + .0001 || lostVerified) result.push({ zone, before, next, lostVerified });
        }
      } catch { /* Invalid draft geometry is unavailable for planning. */ }
    }
    return result.sort((a, b) => Number(b.zone.kind === "Restricted") - Number(a.zone.kind === "Restricted") || (b.next - b.before) - (a.next - a.before));
  }, [assets?.commissioning, health, scenario, version, workspace.boundaries, workspace.sensors]);

  const outages = dependencies.flatMap((device) => {
    const assigned = workspace.sensors.filter((sensor) => sensor.dependencyIds?.includes(device.id));
    const report = workspace.dependencyHealth?.[device.id];
    const status = getApplianceHealth({ id: device.id, demo: false, healthReport: report }, now);
    const failed = assigned.filter((sensor) => OUTAGE_STATES.has(health.get(sensor.id)?.status ?? "unknown"));
    if (status.status !== "offline" && (status.status === "online" || failed.length < 2)) return [];
    const confirmed = status.status === "offline";
    const devices = confirmed ? assigned : failed;
    const ids = new Set(devices.map(({ id }) => id));
    const start = report?.occurredAt ?? failed.map((sensor) => health.get(sensor.id)?.lastReport ?? "").filter(Boolean).sort()[0] ?? "unreported";
    return [{ device, devices, start, confirmed, boundaries: workspace.boundaries.filter((area) => area.sensorIds.some((id) => ids.has(id))) }];
  });

  async function request(path: "assets" | "commissioning-test", body: Record<string, unknown>) {
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await siteRequest(session, path, { ...body, version });
      if (path === "commissioning-test") { setDelivery(response.deliveryId); setMessage("Test notification delivered."); }
      else refreshAssets();
      if (body?.operation === "commission") setMessage(`Commissioning result saved for version ${version}.`);
      if (body?.operation === "link-ticket") setMessage("Maintenance ticket linked to the outage.");
    } catch (issue) { setError(issue instanceof Error ? issue.message : "Could not update site readiness."); }
    finally { setBusy(false); }
  }

  if (!version && !editing) return null;
  return <details className="gv-triage-section">
    <summary>Infrastructure and site readiness</summary>
    <div className="gv-triage-content gv-incident-form">
      {editing && <form className="gv-incident-form" onSubmit={(event) => {
        event.preventDefault(); const fields = new FormData(event.currentTarget);
        const id = String(fields.get("id")).trim(), name = String(fields.get("name")).trim();
        if (!/^[A-Za-z0-9_-]{1,96}$/.test(id) || !name) return;
        onDependency({ id, name, kind: fields.get("kind") as SiteDependency["kind"] });
      }}>
        <h4>Draft infrastructure</h4>
        <label>Dependency ID<input name="id" pattern="[A-Za-z0-9_\\-]+" maxLength={96} required /></label>
        <label>Dependency name<input name="name" maxLength={60} required /></label>
        <label>Infrastructure type<select name="kind"><option value="switch">PoE switch</option><option value="ups">UPS</option><option value="gateway">Gateway</option></select></label>
        <button type="submit" className="gv-button">Save dependency to draft</button>
        <ul className="gv-triage-records">{dependencies.map((item) => <li key={item.id}><strong>{item.name}</strong><small>{item.id} · {item.kind}</small></li>)}</ul>
      </form>}
      {!!version && !editing && <>
        <h4>Infrastructure outages</h4>
        {!outages.length && <p>No dependency outages are currently reported or suspected.</p>}
        {outages.map(({ device, devices, boundaries, start, confirmed }) => {
          const ticket = assets?.tickets.find(({ dependencyId, outageStart }) => dependencyId === device.id && outageStart === start);
          return <details className="gv-triage-section" key={device.id}>
            <summary>{device.name} · {confirmed ? "Reported offline" : "Suspected source"} · {devices.length} affected</summary>
            <div className="gv-triage-content gv-incident-form">
              <p>{devices.map(({ name }) => name).join(", ") || "No devices assigned."}</p>
              {boundaries.map((boundary) => <button type="button" className="gv-posture-row" key={boundary.id} onClick={() => onBoundary(boundary)}>
                <Network size={14} aria-hidden="true" /><strong>{boundary.name}</strong><ChevronRight size={14} aria-hidden="true" />
              </button>)}
              {ticket && <a className="gv-button" href={ticket.url} target="_blank" rel="noreferrer">Open maintenance ticket</a>}
              {manager && <form className="gv-incident-form" onSubmit={(event) => {
                event.preventDefault(); const url = httpEndpointUrl(String(new FormData(event.currentTarget).get("ticket")));
                if (url) void request("assets", { operation: "link-ticket", dependencyId: device.id, outageStart: start, url });
              }}><label>Maintenance ticket URL<input required name="ticket" type="url" defaultValue={ticket?.url ?? ""} /></label>
                <button className="gv-button" type="submit" disabled={busy}>Link maintenance ticket</button></form>}
            </div>
          </details>;
        })}
      </>}
      <details className="gv-triage-section">
        <summary>Plan a coverage outage</summary>
        <div className="gv-triage-content gv-incident-form">
          <label>Simulated offline device<select value={scenario} onChange={(event) => setScenario(event.target.value)}>
            <option value="">Choose a camera or dependency</option>
            {workspace.sensors.filter((sensor) => sensor.modality === "vision").map((sensor) => <option key={sensor.id} value={`camera:${sensor.id}`}>{sensor.name}</option>)}
            {dependencies.map((item) => <option key={item.id} value={`dependency:${item.id}`}>{item.name} and its assigned cameras</option>)}
          </select></label>
          {scenario && !scenarios.length && <p role="status">No additional modeled coverage gaps for this scenario.</p>}
          {scenarios.map(({ zone, before, next, lostVerified }) => <button type="button" className="gv-posture-row" onClick={() => onBoundary(zone)} key={zone.id}>
            <Pentagon size={14} aria-hidden="true" /><span><strong>{zone.name}</strong><small>{coveragePercent(1 - before)} to {coveragePercent(1 - next)} modeled coverage{lostVerified ? " · Last verified view lost" : ""}</small></span><ChevronRight size={14} aria-hidden="true" />
          </button>)}
        </div>
      </details>
      {!!version && !editing && <details className="gv-triage-section">
        <summary>Commissioning and replacement</summary>
        <div className="gv-triage-content gv-incident-form">
          {manager && <form key={`${version}:${selectedDoor}:${targetBoundary?.id ?? ""}`} className="gv-incident-form" onSubmit={(event) => {
            event.preventDefault(); if (!door?.linkedCameraId || !targetBoundary) return;
            const fields = new FormData(event.currentTarget);
            void request("assets", { operation: "commission", sensorId: door.id, cameraId: door.linkedCameraId, boundaryId: targetBoundary.id,
              checks: Object.fromEntries(Object.keys(DRILL_STEPS).map((step) => [step, fields.has(step) || step === "notification" && !!delivery])), note: fields.get("note") });
          }}>
            <label>Commissioning door<select value={selectedDoor} onChange={(event) => { setSelectedDoor(event.target.value); setBoundaryId(""); setDelivery(""); }}>
              <option value="">Choose door</option>{workspace.sensors.filter((sensor) => sensor.modality === "access" && sensor.linkedCameraId)
                .map((sensor) => <option key={sensor.id} value={sensor.id}>{sensor.name}</option>)}
            </select></label>
            {door && <><label>Commissioning boundary<select value={targetBoundary?.id ?? ""} onChange={(event) => setBoundaryId(event.target.value)}>
              {candidateBoundaries.map((area) => <option key={area.id} value={area.id}>{area.name}</option>)}
            </select></label>
            {workspace.alerts.filter((alert) => alert.zoneId === targetBoundary?.id && alert.cameraId === door.linkedCameraId && !alert.correlation).slice(0, 3)
              .map((alert) => <button type="button" className="gv-button" key={alert.id} onClick={() => onIncident(alert)}>Review · {alert.observation.summary}</button>)}
            {Object.entries(DRILL_STEPS).map(([step, label]) => <label key={step} className="gv-checkbox"><input name={step} type="checkbox" />{label}</label>)}
            {assets?.testNotificationsEnabled && <button type="button" className="gv-button" disabled={busy} onClick={() => void request("commissioning-test", {})}>Send drill notification</button>}
            {delivery && <p>Delivery receipt {delivery}</p>}
            <label>Drill note<textarea name="note" maxLength={300} rows={2} /></label>
            <button type="submit" className="gv-button" disabled={busy || !targetBoundary}>Save commissioning result</button></>}
          </form>}
          {assets?.commissioning.map((record) => <p key={`${record.createdAt}:${record.sensorId}`}>
            {workspace.sensors.find(({ id }) => id === record.sensorId)?.name ?? record.sensorId} · Version {record.version} · {record.passed ? "Passed" : "Follow-up required"}{record.version !== version ? " · Recheck after configuration change" : ""}
          </p>)}
        </div>
      </details>}
      {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
    </div>
  </details>;
}
