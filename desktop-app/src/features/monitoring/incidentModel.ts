import { associatedCamera, containsPoint, INCIDENT_DISPOSITIONS, type Alert, type IncidentAction, type IncidentActivity, type Sensor } from "./monitoringModel.ts";
import type { CameraCoverage } from "./cameraCoverage.ts";

export const RESPONSE_STEPS = {
  verify: "Review the source event and available camera evidence",
  contact: "Notify the responsible site contact if needed",
  document: "Record the outcome and any follow-up",
} as const;

export function incidentCameras(alert: Alert, sensors: Sensor[], coverage: CameraCoverage | null) {
  const linked = incidentCamera(alert, sensors);
  const nearby = coverage?.sectors.filter((sector) =>
    sector.geometry.some(([outline, ...holes]) => containsPoint(outline, alert.observation.position)
      && !holes.some((hole) => containsPoint(hole, alert.observation.position)))) ?? [];
  return [
    ...(linked ? [{ camera: linked, reason: "Linked to source" }] : []),
    ...sensors.filter((camera) => camera.modality === "vision" && camera.id !== linked?.id && nearby.some(({ id }) => id === camera.id))
      .map((camera) => ({ camera, reason: "Modeled view includes event" })),
  ].filter(({ camera }) => camera.cameraFeedUrl || camera.cameraGridUrl || camera.demo);
}

export function incidentCamera(alert: Alert, sensors: Sensor[]) {
  if (alert.cameraId !== undefined) return sensors.find((camera) => camera.id === alert.cameraId && camera.modality === "vision");
  const source = sensors.find((sensor) => sensor.id === alert.observation.sensorId);
  return source?.modality === alert.modality ? associatedCamera(source, sensors) : undefined;
}

export function applyIncidentAction(alert: Alert, action: IncidentAction, occurredAt: string): Alert {
  if (alert.status === "resolved" && action.kind !== "bookmark") return alert;
  let patch: Partial<Alert> = {};
  let activity: IncidentActivity;
  switch (action.kind) {
    case "acknowledge":
      if (alert.status !== "new") return alert;
      patch = { status: "acknowledged" };
      activity = { ...action, occurredAt, operator: alert.owner };
      break;
    case "claim": {
      const name = action.name.trim();
      if (!name || name.length > 80 || name === alert.owner) return alert;
      patch = { owner: name, status: "acknowledged" };
      activity = { kind: "claim", name, occurredAt, operator: name };
      break;
    }
    case "note": {
      const text = action.text.trim();
      if (!alert.owner || !text || text.length > 500) return alert;
      activity = { kind: "note", text, occurredAt, operator: alert.owner };
      break;
    }
    case "resolve":
      if (!alert.owner || !Object.prototype.hasOwnProperty.call(INCIDENT_DISPOSITIONS, action.disposition)) return alert;
      patch = { status: "resolved", disposition: action.disposition };
      activity = { ...action, occurredAt, operator: alert.owner };
      break;
    case "checklist":
      if (!alert.owner || !Object.prototype.hasOwnProperty.call(RESPONSE_STEPS, action.step)) return alert;
      patch = { responseSteps: { ...alert.responseSteps, [action.step]: action.done } };
      activity = { ...action, occurredAt, operator: alert.owner };
      break;
    case "handoff": {
      const nextAction = action.nextAction.trim(), note = action.note.trim();
      const due = action.dueAt ? Date.parse(action.dueAt) : undefined;
      if (!alert.owner || !nextAction || nextAction.length > 180 || note.length > 500 || (due !== undefined && !Number.isFinite(due))) return alert;
      const dueAt = due === undefined ? undefined : new Date(due).toISOString();
      patch = { handoff: { nextAction, note, occurredAt, operator: alert.owner, dueAt } };
      activity = { kind: "handoff", nextAction, note, occurredAt, operator: alert.owner, dueAt };
      break;
    }
    case "accept-handoff": {
      const name = action.name.trim();
      if (!alert.handoff || alert.handoff.acceptedBy || !name || name.length > 80 || name === alert.owner) return alert;
      patch = { owner: name, status: "acknowledged", handoff: { ...alert.handoff, acceptedBy: name } };
      activity = { kind: "accept-handoff", name, occurredAt, operator: name };
      break;
    }
    case "bookmark": {
      const bookmarks = alert.evidenceBookmarks ?? [];
      const label = action.label.trim();
      const start = Date.parse(action.startAt), end = Date.parse(action.endAt), anchor = Date.parse(alert.observation.occurredAt);
      if (!alert.owner || !label || label.length > 80 || bookmarks.length >= 30 || !(start < end)
        || start < anchor - 900_000 || end > anchor + 900_000) return alert;
      patch = { evidenceBookmarks: [...bookmarks, { id: occurredAt + "-" + bookmarks.length, label,
        startAt: action.startAt, endAt: action.endAt, operator: alert.owner }] };
      activity = { ...action, label, occurredAt, operator: alert.owner };
      break;
    }
  }
  return { ...alert, ...patch, reviewedAt: occurredAt, activity: [...(alert.activity ?? []), activity] };
}
