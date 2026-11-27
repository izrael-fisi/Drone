import type { Alert, AlertStatus, EventType } from "./monitoringModel.ts";

export type CorrelationRule = { enabled: boolean; windowSeconds: number; graceSeconds?: number; includeHeldOpen?: boolean };
export const DEFAULT_CORRELATION_RULE: CorrelationRule = { enabled: false, windowSeconds: 45 };

export function pairKind(alert: Alert) {
  const event = alert.observation.eventType;
  if (alert.modality === "access" && event === "door-forced-open") return "door";
  if (alert.modality === "access" && event === "door-held-open") return "held";
  if (alert.modality === "vision" && event === "line-crossing") return "camera";
}

export function correlateAlert(alert: Alert, prior: Alert[], rule = DEFAULT_CORRELATION_RULE): Alert {
  const kind = pairKind(alert);
  if (!kind || !alert.cameraId || !rule.enabled || !Number.isInteger(rule.windowSeconds) || rule.windowSeconds < 5 || rule.windowSeconds > 180) return alert;
  const paired = new Set(prior.flatMap((entry) => entry.correlation ? [entry.correlation.incidentId] : []));
  const timestamp = (entry: Alert) => Date.parse(entry.observation.occurredAt) / 1000;
  const time = timestamp(alert);
  const available = prior.filter((entry) => !entry.correlation && entry.status !== "resolved" && !paired.has(entry.id)
    && entry.cameraId === alert.cameraId && entry.zoneId === alert.zoneId && entry.configurationVersion === alert.configurationVersion
    && (entry.origin === "Simulation") === (alert.origin === "Simulation"));
  const candidates = (type: typeof kind, center = time) => available.filter((entry) => pairKind(entry) === type &&
    Math.abs(timestamp(entry) - center) <= rule.windowSeconds)
    .sort((a, b) => Math.abs(timestamp(a) - center) - Math.abs(timestamp(b) - center));
  let match: Alert | undefined;
  if (kind === "door") {
    [match] = candidates("camera");
  } else if (kind === "camera") {
    [match] = candidates("door");
    if (!match && rule.includeHeldOpen) {
      const held = candidates("held");
      if (held.length === 1 && !candidates("camera", timestamp(held[0])).length) [match] = held;
    }
  } else if (rule.includeHeldOpen) {
    const cameras = candidates("camera");
    if (cameras.length === 1 && !candidates("held", timestamp(cameras[0])).length && !candidates("door", timestamp(cameras[0])).length) {
      [match] = cameras;
    }
  }
  if (!match) return alert;
  const seconds = kind === "camera" ? time - timestamp(match) : timestamp(match) - time;
  return { ...alert, correlation: { incidentId: match.id, seconds } };
}

export function previewCorrelation(alerts: Alert[], windowSeconds: number, includeHeldOpen = false) {
  const processed: Alert[] = [];
  for (const entry of [...alerts].sort((a, b) => Date.parse(a.observation.occurredAt) - Date.parse(b.observation.occurredAt))) {
    if (!pairKind(entry)) continue;
    processed.push(correlateAlert({ ...entry, status: "new", correlation: undefined }, processed, { enabled: true, windowSeconds, includeHeldOpen }));
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
