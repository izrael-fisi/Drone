import { observationKey, type Alert, type Recording, type SiteEvent, type Workspace } from "./monitoringModel.ts";

export const REPLAY_WINDOW_MINUTES = 15;

export function eventFromAlert(alert: Alert): SiteEvent {
  return {
    observation: alert.observation, sensorName: alert.sensorName, modality: alert.modality, origin: alert.origin,
    cameraId: alert.cameraId ?? alert.observation.recording?.cameraId ?? null, locationBasis: alert.locationBasis,
    boundaries: [{ id: alert.zoneId, name: alert.zoneName }], floorId: alert.floorId,
  };
}

export function incidentTimeline(workspace: Pick<Workspace, "siteEvents" | "alerts">, anchor: Alert): SiteEvent[] {
  const events = new Map<string, SiteEvent>();
  const time = Date.parse(anchor.observation.occurredAt);
  const range = REPLAY_WINDOW_MINUTES * 60_000;
  const inRange = (occurredAt: string) => Math.abs(Date.parse(occurredAt) - time) <= range;
  for (const event of workspace.siteEvents ?? []) {
    if (inRange(event.observation.occurredAt)) events.set(observationKey(event.observation), event);
  }
  for (const alert of [anchor, ...workspace.alerts]) {
    if (!inRange(alert.observation.occurredAt)) continue;
    const key = observationKey(alert.observation);
    const previous = events.get(key);
    if (!previous) events.set(key, eventFromAlert(alert));
    else {
      const boundaries = previous.boundaries.some((boundary) => boundary.id === alert.zoneId)
        ? previous.boundaries : [...previous.boundaries, { id: alert.zoneId, name: alert.zoneName }];
      const observation = previous.observation.recording || !alert.observation.recording ? previous.observation
        : { ...previous.observation, recording: alert.observation.recording };
      if (boundaries !== previous.boundaries || observation !== previous.observation) events.set(key, { ...previous, boundaries, observation });
    }
  }
  return [...events.values()].sort((a, b) => Date.parse(a.observation.occurredAt) - Date.parse(b.observation.occurredAt)
    || observationKey(a.observation).localeCompare(observationKey(b.observation)));
}

export function recordingsForEvent(event: SiteEvent, timeline: SiteEvent[]): Recording[] {
  const own = event.observation.recording;
  const boundaries = new Set(event.boundaries.map((boundary) => boundary.id));
  const eventTime = Date.parse(event.observation.occurredAt);
  const matching = timeline.flatMap((source) => {
    const recording = source.observation.recording;
    if (!recording?.durationSeconds) return [];
    if (source.floorId !== event.floorId) return [];
    const sameCamera = recording.cameraId === event.cameraId;
    if (!sameCamera && !source.boundaries.some((boundary) => boundaries.has(boundary.id))) return [];
    const delta = (eventTime - Date.parse(source.observation.occurredAt)) / 1000;
    const eventOffsetSeconds = recording.eventOffsetSeconds + delta;
    if (eventOffsetSeconds < 0 || eventOffsetSeconds >= recording.durationSeconds) return [];
    return [{ recording: { ...recording, eventOffsetSeconds }, sameCamera, distance: Math.abs(delta) }];
  });
  matching.sort((a, b) => Number(b.sameCamera) - Number(a.sameCamera) || a.distance - b.distance);
  const recordings = new Map<string, Recording>();
  if (own) recordings.set(own.cameraId, own);
  for (const { recording } of matching) {
    if (!recordings.has(recording.cameraId)) recordings.set(recording.cameraId, recording);
  }
  return [...recordings.values()];
}

export function recordingForEvent(event: SiteEvent, timeline: SiteEvent[]): Recording | undefined {
  return recordingsForEvent(event, timeline)[0];
}
