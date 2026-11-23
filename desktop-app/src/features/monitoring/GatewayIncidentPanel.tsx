import { ChevronLeft, ChevronRight, Crosshair, Network, Pentagon, Radio, X } from "lucide-react";
import { gatewayBoundaries, type GatewayIncident } from "./gatewayIncidents";
import { HEALTH_LABELS, type ApplianceHealth } from "./applianceHealth";
import type { TriageFocus } from "./HealthTriage";

const utcTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" });

function utc(value: string) {
  return utcTime.format(new Date(value)) + " UTC";
}

function status(incident: GatewayIncident) {
  return incident.recoveredAt ? "Recovered" : incident.devices.some((device) => device.recoveredAt) ? "Recovering" : "Outage active";
}

function affectedLabel(count: number) {
  return `${count} ${count === 1 ? "device" : "devices"} still affected`;
}

export function GatewayIncidentRows({ incidents, onOpen }: { incidents: GatewayIncident[]; onOpen: (id: string) => void }) {
  if (!incidents.length) return null;
  return <section className="gv-gateway-rows" aria-label="Gateway outages">
    <h3>Gateway outages</h3>
    {incidents.map((incident) => <button key={incident.id} type="button" className="gv-alert-row"
      data-focus-key={"gateway" + incident.id} onClick={() => onOpen(incident.id)}>
      <span className="gv-severity-icon is-medium"><Network size={16} /></span>
      <span className="gv-alert-summary">
        <strong>Gateway outage · {incident.gatewayId}</strong>
        <small>{status(incident)} · {affectedLabel(incident.devices.filter((device) => !device.recoveredAt).length)}</small>
      </span>
      <ChevronRight size={14} />
    </button>)}
  </section>;
}

export function GatewayIncidentPanel({ incident, healthById, focus, onBack, onClose, onFocus, onFit }: {
  incident: GatewayIncident; healthById: ReadonlyMap<string, ApplianceHealth>; focus: TriageFocus | null;
  onBack: () => void; onClose: () => void; onFocus: (target: TriageFocus) => void; onFit?: () => void;
}) {
  const boundaries = gatewayBoundaries(incident);
  const affected = incident.devices.filter((device) => !device.recoveredAt).length;
  return <div className="gv-incident gv-gateway-incident">
    <header className="gv-panel-heading">
      <button type="button" className="gv-incident-back" onClick={onBack}><ChevronLeft size={16} /> Back to alerts</button>
      <button type="button" className="gv-icon-button" aria-label="Close gateway incident" onClick={onClose}><X size={17} /></button>
    </header>
    <section className="gv-details gv-incident-summary">
      <div className="gv-detail-label"><span>Gateway outage</span><span>{status(incident)}</span></div>
      <h3>{incident.gatewayId}</h3>
      <p className="gv-gateway-progress" role="status">
        {affectedLabel(affected)} · {incident.devices.length - affected} recovered
      </p>
      <dl>
        <div><dt>Started</dt><dd><time dateTime={incident.startedAt}>{utc(incident.startedAt)}</time></dd></div>
        {incident.recoveredAt && <div><dt>Recovered</dt><dd><time dateTime={incident.recoveredAt}>{utc(incident.recoveredAt)}</time></dd></div>}
        <div><dt>Evidence</dt><dd>Correlated device reporting loss</dd></div>
      </dl>
      {onFit && <button type="button" className="gv-button gv-gateway-fit" onClick={onFit}><Crosshair size={15} /> Fit outage on map</button>}
    </section>
    <section className="gv-details gv-gateway-scope" aria-label="Devices in gateway outage">
      <h4><Radio size={15} /> Devices <span>{incident.devices.length}</span></h4>
      {incident.devices.map((device) => {
        const health = healthById.get(device.sensorId);
        const state = device.recoveredAt ? "Recovered" : health?.status === "offline" || health?.status === "stale"
          ? HEALTH_LABELS[health.status] : "Awaiting an online report";
        return <button key={device.sensorId} type="button" className="gv-posture-row"
          aria-pressed={focus?.kind === "sensor" && focus.id === device.sensorId} onClick={() => onFocus({ kind: "sensor", id: device.sensorId })}>
          <Radio size={15} /><span><strong>{device.name}</strong><small>{state}</small>
            {device.recoveredAt && <small><time dateTime={device.recoveredAt}>{utc(device.recoveredAt)}</time></small>}</span><ChevronRight size={14} />
        </button>;
      })}
    </section>
    <section className="gv-details gv-gateway-scope" aria-label="Boundaries in gateway outage">
      <h4><Pentagon size={15} /> Assigned boundaries <span>{boundaries.length}</span></h4>
      {boundaries.map((boundary) => <button type="button" key={boundary.id} className="gv-posture-row"
        aria-pressed={focus?.kind === "boundary" && focus.id === boundary.id} onClick={() => onFocus({ kind: "boundary", id: boundary.id })}>
        <Pentagon size={15} /><span><strong>{boundary.name}</strong><small>{affectedLabel(boundary.affected)}</small></span><ChevronRight size={14} />
      </button>)}
      {!boundaries.length && <p>No boundary assignments for these devices.</p>}
    </section>
  </div>;
}
