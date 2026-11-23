import type { Alert, AlertStatus, EventType } from "./monitoringModel.ts";

export type CorrelationRule = { enabled: boolean; windowSeconds: number; graceSeconds?: number };
export const DEFAULT_CORRELATION_RULE: CorrelationRule = { enabled: false, windowSeconds: 45 };

function pairTime(a: Alert, b: Alert, windowSeconds: number) {
  if (!a.cameraId || a.cameraId !== b.cameraId || a.zoneId !== b.zoneId
    || (a.origin === "Simulation") !== (b.origin === "Simulation")) return;
  const [door, camera] = a.modality === "access" ? [a, b] : [b, a];
  if (door.modality !== "access" || door.observation.eventType !== "door-forced-open"
    || camera.modality !== "vision" || camera.observation.eventType !== "line-crossing") return;
  const seconds = (Date.parse(camera.observation.occurredAt) - Date.parse(door.observation.occurredAt)) / 1000;
  if (Math.abs(seconds) <= windowSeconds) return seconds;
}

export function correlateAlert(alert: Alert, prior: Alert[], rule = DEFAULT_CORRELATION_RULE): Alert {
  if (!rule.enabled || !Number.isInteger(rule.windowSeconds) || rule.windowSeconds < 5 || rule.windowSeconds > 180) return alert;
  const paired = new Set(prior.flatMap((entry) => entry.correlation ? [entry.correlation.incidentId] : []));
  const match = prior.flatMap((entry) => {
    if (entry.correlation || entry.status === "resolved" || paired.has(entry.id)) return [];
    const seconds = pairTime(alert, entry, rule.windowSeconds);
    return seconds === undefined ? [] : [{ incidentId: entry.id, seconds }];
  }).sort((a, b) => Math.abs(a.seconds) - Math.abs(b.seconds))[0];
  return match ? { ...alert, correlation: match } : alert;
}

export function previewCorrelation(alerts: Alert[], windowSeconds: number) {
  const processed: Alert[] = [];
  for (const entry of [...alerts].sort((a, b) => Date.parse(a.observation.occurredAt) - Date.parse(b.observation.occurredAt))) {
    if (entry.observation.eventType !== "door-forced-open" && entry.observation.eventType !== "line-crossing") continue;
    processed.push(correlateAlert({ ...entry, status: "new", correlation: undefined }, processed, { enabled: true, windowSeconds }));
  }
  return processed.filter((entry) => entry.correlation);
}

export type IncidentFilters = {
  query: string; status: AlertStatus | "all" | "handoff"; priority: Alert["severity"] | "all";
  boundary: string; device: string; event: EventType | "all"; start: string; end: string; footage: "all" | "recording";
};
export const DEFAULT_INCIDENT_FILTERS: IncidentFilters = {
  query: "", status: "all", priority: "all", boundary: "", device: "", event: "all", start: "", end: "", footage: "all",
};

export function incidentRows(alerts: Alert[], filters: IncidentFilters) {
  const byId = new Map(alerts.map((alert) => [alert.id, alert]));
  const companions = new Map(alerts.flatMap((alert) => alert.correlation ? [[alert.correlation.incidentId, alert]] : []));
  const tokens = filters.query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const start = filters.start ? Date.parse(filters.start + "T00:00:00Z") : -Infinity;
  const end = filters.end ? Date.parse(filters.end + "T00:00:00Z") + 86_400_000 : Infinity;
  return alerts.flatMap((alert) => {
    if (alert.correlation && byId.has(alert.correlation.incidentId)) return [];
    const companion = companions.get(alert.id);
    const members = companion ? [alert, companion] : [alert];
    const handoff = alert.status !== "resolved" && alert.handoff && !alert.handoff.acceptedBy ? alert.handoff : undefined;
    if ((filters.status === "handoff" ? !handoff : filters.status !== "all" && alert.status !== filters.status)
      || (filters.priority !== "all" && filters.priority !== alert.severity) || (filters.boundary && filters.boundary !== alert.zoneId)) return [];
    const text = members.map((entry) => [entry.observation.summary, entry.zoneName, entry.sensorName, entry.owner, entry.handoff?.nextAction].join(" ")).join(" ").toLowerCase();
    if (!tokens.every((token) => text.includes(token))) return [];
    const match = members.find((entry) => {
      const observed = Date.parse(entry.observation.occurredAt);
      return observed >= start && observed < end
        && (!filters.device || filters.device === entry.observation.sensorId || filters.device === entry.cameraId)
        && (filters.event === "all" || filters.event === entry.observation.eventType)
        && (filters.footage === "all" || !!entry.observation.recording);
    });
    return match ? [{ alert, companion, match, handoff }] : [];
  }).sort((a, b) => Number(a.alert.status === "resolved") - Number(b.alert.status === "resolved")
    || Number(b.alert.severity === "High") - Number(a.alert.severity === "High")
    || Date.parse(b.alert.observation.occurredAt) - Date.parse(a.alert.observation.occurredAt));
}
