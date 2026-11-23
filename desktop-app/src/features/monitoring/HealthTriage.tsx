import { useEffect, useRef, useState, type FormEvent } from "react";
import { ChevronRight, Clock3, Pentagon } from "lucide-react";
import { activeMaintenance, getOutageHistory, HEALTH_LABELS, type ApplianceHealth, type HealthStatus } from "./applianceHealth";
import { HealthIndicator } from "./DeviceHealth";
import { deviceIcon } from "./DeviceInventory";
import type { Boundary, Sensor } from "./monitoringModel";

export type TriageFocus = { kind: "sensor" | "boundary"; id: string };
const localTime = new Intl.DateTimeFormat(undefined, {
  year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
});

function Time({ value }: { value: string }) {
  return <time dateTime={value}>{localTime.format(new Date(value))}</time>;
}

function localInputValue(timestamp: number) {
  const date = new Date(timestamp);
  return new Date(timestamp - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function readLocalTime(value: FormDataEntryValue | null) {
  const timestamp = Date.parse(String(value));
  return Number.isFinite(timestamp) && localInputValue(timestamp) === value ? timestamp : NaN;
}

type MaintenanceActions = {
  onSchedule: (sensorId: string, start: number, end: number, reason: string) => string | undefined | Promise<string | undefined>;
  onStop: (sensorId: string, startsAt: string) => void | Promise<void>;
};

function Maintenance({ sensors, now, preferred, onSchedule, onStop }: MaintenanceActions & {
  sensors: Sensor[]; now: number; preferred?: string;
}) {
  const [scheduling, setScheduling] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const deviceField = useRef<HTMLSelectElement>(null);
  const scheduleButton = useRef<HTMLButtonElement>(null);
  const moveFocus = useRef(false);
  const windows = sensors.flatMap((sensor) => (sensor.maintenanceWindows ?? [])
    .filter((window) => Date.parse(window.endsAt) > now)
    .map((window) => ({ sensor, window, active: Date.parse(window.startsAt) <= now })))
    .sort((a, b) => a.window.startsAt.localeCompare(b.window.startsAt));

  useEffect(() => {
    if (!moveFocus.current) return;
    moveFocus.current = false;
    (scheduling ? deviceField.current : scheduleButton.current)?.focus();
  }, [scheduling]);

  function openScheduler(open: boolean) {
    moveFocus.current = true;
    setScheduling(open);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setSaving(true);
    try {
      const issue = await onSchedule(String(fields.get("device")), readLocalTime(fields.get("start")), readLocalTime(fields.get("end")), String(fields.get("reason")));
      setError(issue ?? "");
      if (!issue) { openScheduler(false); setMessage("Maintenance scheduled."); }
    } catch (issue) { setError(issue instanceof Error ? issue.message : "Unable to schedule maintenance."); }
    finally { setSaving(false); }
  }

  return <details className="gv-triage-section">
    <summary>Maintenance <span>{windows.length}</span></summary>
    <div className="gv-triage-content">
      {scheduling ? <form className="gv-incident-form gv-maintenance-form" onSubmit={save}>
        <div className="gv-incident-field">
          <label htmlFor="gv-maintenance-device">Device</label>
          <select ref={deviceField} id="gv-maintenance-device" name="device" defaultValue={preferred ?? ""} required>
            <option value="">Choose device</option>
            {sensors.map((sensor) => <option key={sensor.id} value={sensor.id}>{sensor.name}</option>)}
          </select>
        </div>
        <label>Start (local time)<input name="start" type="datetime-local" defaultValue={localInputValue(Date.now())} required /></label>
        <label>End (local time)<input name="end" type="datetime-local" defaultValue={localInputValue(Date.now() + 3_600_000)} required /></label>
        <label>Reason<textarea name="reason" rows={2} maxLength={200} required /></label>
        <div className="gv-detail-actions">
          <button type="submit" className="gv-button gv-primary" disabled={saving}>Save maintenance</button>
          <button type="button" className="gv-button" disabled={saving} onClick={() => { openScheduler(false); setError(""); }}>Cancel scheduling</button>
        </div>
      </form> : <button ref={scheduleButton} type="button" className="gv-button" disabled={!sensors.length}
        onClick={() => { setMessage(""); openScheduler(true); }}><Clock3 size={15} /> Schedule maintenance</button>}
      {message && <p role="status">{message}</p>}
      {error && <p className="gv-maintenance-error" role="alert">{error}</p>}
      {!windows.length && <p className="gv-posture-note">No active or scheduled maintenance.</p>}
      <ul className="gv-triage-records">
        {windows.map(({ sensor, window, active }) => <li key={sensor.id + window.startsAt}>
          <strong>{sensor.name}</strong>
          <span>{active ? "Active" : "Scheduled"} · {window.reason}</span>
          <small><Time value={window.startsAt} /> to <Time value={window.endsAt} /></small>
          <button type="button" className="gv-button" aria-label={(active ? "End maintenance for " : "Cancel maintenance for ") + sensor.name}
            disabled={saving} onClick={async () => {
              setSaving(true); setError(""); setMessage("");
              try {
                await onStop(sensor.id, window.startsAt);
                (scheduling ? deviceField.current : scheduleButton.current)?.focus();
                setMessage(active ? "Maintenance ended." : "Maintenance canceled.");
              } catch (issue) { setError(issue instanceof Error ? issue.message : "Unable to end maintenance."); }
              finally { setSaving(false); }
            }}>
            {active ? "End maintenance" : "Cancel maintenance"}
          </button>
        </li>)}
      </ul>
    </div>
  </details>;
}

export type HealthTriageProps = MaintenanceActions & {
  sensors: Sensor[]; healthById: ReadonlyMap<string, ApplianceHealth>; now: number;
  filter: HealthStatus | null; focus: TriageFocus | null;
  scope: { devices: Sensor[]; boundaries: Boundary[] } | null;
  onFilter: (status: HealthStatus | null) => void;
  onFocus: (target: TriageFocus) => void;
};

export function HealthTriage({ sensors, healthById, now, filter, focus, scope, onFilter, onFocus, onSchedule, onStop }: HealthTriageProps) {
  const counts = { online: 0, offline: 0, stale: 0, unknown: 0 };
  for (const health of healthById.values()) counts[health.status]++;
  const focusedDevice = focus?.kind === "sensor" ? sensors.find((sensor) => sensor.id === focus.id) : undefined;
  const entries = (focusedDevice ? [focusedDevice] : sensors).flatMap((sensor) => getOutageHistory(sensor, now)
    .map((outage) => ({ sensor, outage }))).sort((a, b) => b.outage.startedAt.localeCompare(a.outage.startedAt)).slice(0, 50);

  return <section className="gv-posture-health" aria-label="Appliance health">
    <h3>Appliance health</h3>
    <ul className="gv-health-legend gv-health-counts" aria-label="Appliance health summary">
      {(Object.keys(HEALTH_LABELS) as HealthStatus[]).map((status) => <li key={status}>
        <button type="button" aria-pressed={filter === status} onClick={() => onFilter(filter === status ? null : status)}>
          <HealthIndicator health={{ status }} /><strong>{counts[status]}</strong>
        </button>
      </li>)}
    </ul>
    {Array.from(healthById.values()).some((health) => health.simulated) && <p>Includes simulated devices.</p>}
    {scope && <section className="gv-triage-matches" aria-label="Affected devices and boundaries">
      <header>
        <h4>{scope.devices.length} {scope.devices.length === 1 ? "device" : "devices"} · {scope.boundaries.length} {scope.boundaries.length === 1 ? "boundary" : "boundaries"}</h4>
        <button type="button" className="gv-button" onClick={(event) => {
          event.currentTarget.closest(".gv-posture-health")?.querySelector<HTMLButtonElement>(".gv-health-counts [aria-pressed='true']")?.focus();
          onFilter(null);
        }}>Clear health filter</button>
      </header>
      {!scope.devices.length && <p>No devices match this status.</p>}
      {scope.devices.map((sensor) => {
        const Icon = deviceIcon[sensor.modality];
        const maintenance = activeMaintenance(sensor, now);
        return <button type="button" key={sensor.id} className="gv-posture-row" data-focus-key={"sensor" + sensor.id}
          aria-pressed={focus?.kind === "sensor" && focus.id === sensor.id}
          onClick={() => onFocus({ kind: "sensor", id: sensor.id })}>
          <Icon size={16} /><span><strong>{sensor.name}</strong>
            {maintenance && <small>Maintenance active</small>}
            {healthById.get(sensor.id)?.simulated && <small>Simulated device</small>}
          </span><ChevronRight size={14} />
        </button>;
      })}
      {scope.boundaries.map((boundary) => <button type="button" key={boundary.id} className="gv-posture-row" data-focus-key={"boundary" + boundary.id}
        aria-pressed={focus?.kind === "boundary" && focus.id === boundary.id} onClick={() => onFocus({ kind: "boundary", id: boundary.id })}>
        <Pentagon size={16} /><span><strong>{boundary.name}</strong><small>{boundary.enabled ? "Assigned boundary" : "Paused boundary"}</small></span><ChevronRight size={14} />
      </button>)}
    </section>}
    <details className="gv-triage-section">
      <summary>Outage history <span>{entries.length}</span></summary>
      <div className="gv-triage-content">
        {focusedDevice && <p>{focusedDevice.name}</p>}
        {!entries.length && <p className="gv-posture-note">No recorded outages.</p>}
        <ul className="gv-triage-records">
          {entries.map(({ sensor, outage }) => {
            const maintenance = activeMaintenance(sensor, Date.parse(outage.startedAt));
            return <li key={sensor.id + outage.startedAt}>
              <strong>{sensor.name}</strong>
              <span>{HEALTH_LABELS[outage.cause]} · {outage.recoveredAt ? "Recovered" : "Ongoing"}</span>
              <small>Started <Time value={outage.startedAt} /></small>
              {outage.recoveredAt && <small>Recovered <Time value={outage.recoveredAt} /></small>}
              {maintenance && <small>Started during maintenance · {maintenance.reason}</small>}
            </li>;
          })}
        </ul>
      </div>
    </details>
    <Maintenance sensors={sensors} now={now} preferred={focusedDevice?.id} onSchedule={onSchedule} onStop={onStop} />
  </section>;
}
