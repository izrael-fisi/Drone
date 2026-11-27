import { useEffect, useState, type ReactNode } from "react";
import { Bell, Clock, Film, Link2 } from "lucide-react";
import { EVENT_TYPES, MODALITY_LABEL, type Alert, type Workspace } from "./monitoringModel";
import { DEFAULT_CORRELATION_RULE, DEFAULT_INCIDENT_FILTERS, incidentRows, pairKind, previewCorrelation, type CorrelationRule, type IncidentFilters } from "./incidentQueue";

const STATES = { new: "New", acknowledged: "Investigating", resolved: "Resolved" } as const;
const TIME = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "short", timeZone: "UTC" });

function age(timestamp: string, now: number) {
  const minutes = Math.max(0, Math.floor((now - Date.parse(timestamp)) / 60_000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
}

export function IncidentQueue({ workspace, filters, onFilter, selected, gatewayRows, onOpen, onSaveSearch, onRemoveSearch, onRule }: {
  workspace: Workspace; filters: IncidentFilters; onFilter: (filters: IncidentFilters) => void; selected?: string;
  gatewayRows: ReactNode; onOpen: (alert: Alert, event?: Alert) => void; onSaveSearch: (name: string, filters: IncidentFilters) => void;
  onRemoveSearch: (name: string) => void; onRule?: (rule: CorrelationRule) => void;
}) {
  const [searchName, setSearchName] = useState("");
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const [preset, setPreset] = useState("");
  const [limit, setLimit] = useState(30);
  const [windowText, setWindowText] = useState(String((workspace.correlationRule ?? DEFAULT_CORRELATION_RULE).windowSeconds));
  const [grace, setGrace] = useState(String(workspace.correlationRule?.graceSeconds ?? 300));
  const [ruleEnabled, setRuleEnabled] = useState(workspace.correlationRule?.enabled ?? false);
  const [heldOpen, setHeldOpen] = useState(workspace.correlationRule?.includeHeldOpen ?? false);
  const [preview, setPreview] = useState<{ pairs: ReturnType<typeof previewCorrelation>; window: number }>();
  const [ruleSaved, setRuleSaved] = useState(false);
  const saved = workspace.savedIncidentSearches ?? [];
  const boundaries = new Map(workspace.boundaries.map((zone) => [zone.id, zone.name]));
  const devices = new Map(workspace.sensors.map((sensor) => [sensor.id, sensor.name]));
  for (const entry of workspace.alerts) {
    if (!boundaries.has(entry.zoneId)) boundaries.set(entry.zoneId, entry.zoneName);
    if (!devices.has(entry.observation.sensorId)) devices.set(entry.observation.sensorId, entry.sensorName);
  }
  const windowSeconds = Number(windowText);
  const validWindow = Number.isInteger(windowSeconds) && windowSeconds >= 5 && windowSeconds <= 180;
  const validGrace = grace !== "" && Number.isInteger(Number(grace)) && Number(grace) >= 0 && Number(grace) <= 3600;
  const dateError = !!(filters.start && filters.end && filters.start > filters.end);
  const rows = dateError ? [] : incidentRows(workspace.alerts, filters);

  function update<K extends keyof IncidentFilters>(field: K, value: IncidentFilters[K]) {
    setPreset(""); setLimit(30);
    onFilter({ ...filters, [field]: value });
  }

  return <div className="gv-feed gv-incident-queue" aria-label="Incident queue">
    <div className="gv-filters" role="group" aria-label="Filter alerts">
      {([["all", "All activity"], ["new", "New"], ["handoff", "Handoffs"]] as const).map(([value, label]) =>
        <button key={value} type="button" aria-pressed={filters.status === value} onClick={() => update("status", value)}>{label}</button>)}
    </div>
    <label className="gv-incident-field">Search incidents<input type="search" value={filters.query} placeholder="Event, device, or boundary"
      onChange={(event) => update("query", event.target.value)} /></label>
    <details className="gv-triage-section">
      <summary>History filters and saved searches</summary>
      <div className="gv-triage-content gv-queue-filters">
        <label>Incident status<select value={filters.status} onChange={(event) => update("status", event.target.value as IncidentFilters["status"])}>
          <option value="all">All activity</option><option value="new">New</option><option value="acknowledged">Investigating</option><option value="resolved">Resolved</option><option value="handoff">Pending handoff</option>
        </select></label>
        <label>Priority<select value={filters.priority} onChange={(event) => update("priority", event.target.value as IncidentFilters["priority"])}>
          <option value="all">All priorities</option><option>High</option><option>Medium</option>
        </select></label>
        <label>Boundary<select value={filters.boundary} onChange={(event) => update("boundary", event.target.value)}>
          <option value="">All boundaries</option>{[...boundaries].map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </select></label>
        <label>Device or linked camera<select value={filters.device} onChange={(event) => update("device", event.target.value)}>
          <option value="">All devices</option>{[...devices].map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </select></label>
        <label>Event type<select value={filters.event} onChange={(event) => update("event", event.target.value as IncidentFilters["event"])}>
          <option value="all">All event types</option>{Object.entries(EVENT_TYPES).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </select></label>
        <label>Footage<select value={filters.footage} onChange={(event) => update("footage", event.target.value as IncidentFilters["footage"])}>
          <option value="all">Any footage state</option><option value="recording">Recording attached</option>
        </select></label>
        <label>From date (UTC)<input type="date" value={filters.start} onChange={(event) => update("start", event.target.value)} /></label>
        <label>Through date (UTC)<input type="date" value={filters.end} onChange={(event) => update("end", event.target.value)} /></label>
        {dateError && <p role="alert">Choose an end date on or after the start date.</p>}
        <div className="gv-detail-actions"><button type="button" className="gv-button" onClick={() => {
          setPreset(""); setLimit(30); onFilter(DEFAULT_INCIDENT_FILTERS);
        }}>Reset filters</button></div>
        {!!saved.length && <><label>Saved incident search<select value={preset} onChange={(event) => {
          const next = saved.find(({ name }) => name === event.target.value);
          setPreset(event.target.value); if (next) { onFilter(next.filters); setLimit(30); }
        }}><option value="">Choose a saved search</option>{saved.map(({ name }) => <option key={name}>{name}</option>)}</select></label>
          {preset && <button type="button" className="gv-button" onClick={() => { onRemoveSearch(preset); setPreset(""); }}>Remove saved search</button>}</>}
        <form className="gv-incident-form" onSubmit={(event) => {
          event.preventDefault(); const name = searchName.trim(); if (!name || dateError) return;
          onSaveSearch(name, filters); setPreset(name); setSearchName("");
        }}>
          <label>Search name<input required maxLength={60} value={searchName} onChange={(event) => setSearchName(event.target.value)} /></label>
          <button className="gv-button" type="submit" disabled={!searchName.trim() || dateError || saved.length >= 20 && !saved.some(({ name }) => name === searchName.trim())}>Save incident search</button>
        </form>
      </div>
    </details>
    {onRule && <details className="gv-triage-section">
      <summary>Door and camera correlation</summary>
      <div className="gv-triage-content gv-incident-form">
        <p>Pair a door event with a line crossing from its linked camera in the same boundary.</p>
        <label>Correlation window (seconds)<input type="number" min={5} max={180} step={1} value={windowText} onChange={(event) => {
          setWindowText(event.target.value); setPreview(undefined); setRuleSaved(false);
        }} /></label>
        <label>Late arrival allowance (seconds)<input type="number" min={0} max={3600} step={1} value={grace} onChange={(event) => {
          setGrace(event.target.value); setRuleSaved(false);
        }} /></label>
        <label className="gv-checkbox"><input type="checkbox" checked={ruleEnabled} onChange={(event) => { setRuleEnabled(event.target.checked); setRuleSaved(false); }} />Group new matching events</label>
        <label className="gv-checkbox"><input type="checkbox" checked={heldOpen} onChange={(event) => {
          setHeldOpen(event.target.checked); setPreview(undefined); setRuleSaved(false);
        }} />Include held-open doors with one eligible crossing</label>
        <div className="gv-detail-actions">
          <button type="button" className="gv-button" disabled={!validWindow} onClick={() => setPreview({ pairs: previewCorrelation(workspace.alerts, windowSeconds, heldOpen), window: windowSeconds })}>Preview on history</button>
          <button type="button" className="gv-button" disabled={!validWindow || !validGrace} onClick={() => {
            onRule({ enabled: ruleEnabled, windowSeconds, graceSeconds: Number(grace), includeHeldOpen: heldOpen || undefined }); setRuleSaved(true);
          }}>Save correlation rule</button>
        </div>
        {preview && <><p role="status">{preview.pairs.length} matching {preview.pairs.length === 1 ? "pair" : "pairs"} within {preview.window} seconds in retained history.</p>
          {!!preview.pairs.length && <ul className="gv-triage-records">{preview.pairs.slice(-5).reverse().map((entry) => <li key={entry.id}>
            <strong>{entry.zoneName}</strong><small>{entry.sensorName} · {TIME.format(new Date(entry.observation.occurredAt))} UTC</small>
          </li>)}</ul>}</>}
        {ruleSaved && <p role="status">Correlation rule saved for new events.</p>}
      </div>
    </details>}
    {gatewayRows}
    <p className="gv-queue-count" role="status">{rows.length} matching {rows.length === 1 ? "incident" : "incidents"}</p>
    <div className="gv-alert-list">
      {rows.slice(0, limit).map(({ alert, companion, match, handoff }) => {
        const footage = [match, alert, companion].find((entry) => entry?.observation.recording);
        const door = companion && (pairKind(alert) === "camera" ? pairKind(companion) : pairKind(alert));
        return <div key={alert.id} className="gv-queue-item">
          <button type="button" className="gv-alert-row" data-focus-key={"alert" + alert.id} aria-current={selected === alert.id || undefined}
            onClick={() => onOpen(alert, match.id === alert.id ? undefined : match)}>
            <span className={`gv-severity-icon is-${alert.severity.toLowerCase()}`}><Bell aria-hidden="true" size={17} /></span>
            <span className="gv-alert-summary"><strong>{door === "held" ? "Held-open door · camera corroboration"
              : companion ? "Forced door · camera corroboration" : alert.observation.summary}</strong>
              <small>{alert.zoneName} · {companion ? "2 source events" : MODALITY_LABEL[alert.modality]}</small>
              <small>{alert.severity} priority · {STATES[alert.status]} · {alert.owner ?? "Unassigned"}</small>
              <small>{footage ? "Recording attached" : alert.cameraId ? "Camera linked" : "No camera evidence"}</small>
              {handoff && <small><Clock size={12} aria-hidden="true" /> {handoff.dueAt && Date.parse(handoff.dueAt) < now ? "Overdue" : "Handoff pending"} · {handoff.nextAction}</small>}
              {companion && <small><Link2 size={12} aria-hidden="true" /> Same boundary and linked camera</small>}
            </span>
            <span className="gv-alert-time"><time dateTime={alert.observation.occurredAt} title={TIME.format(new Date(alert.observation.occurredAt)) + " UTC"}>{age(alert.observation.occurredAt, now)}</time></span>
          </button>
          {footage && <button type="button" className="gv-button gv-queue-clip" onClick={() => onOpen(alert, footage)}><Film size={14} aria-hidden="true" /> Open event clip</button>}
        </div>;
      })}
      {rows.length > limit && <button type="button" className="gv-button" onClick={() => setLimit(limit + 30)}>Show more incidents</button>}
    </div>
  </div>;
}
