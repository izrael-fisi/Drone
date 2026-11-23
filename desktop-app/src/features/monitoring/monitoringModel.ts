import type { HealthOutage, HealthReport, MaintenanceWindow } from "./applianceHealth.ts";
import type { GatewayIncident } from "./gatewayIncidents.ts";
import { correlateAlert, type IncidentFilters, type CorrelationRule } from "./incidentQueue.ts";

export type Position = [longitude: number, latitude: number];
export type SiteFloor = { id: string; building: string; name: string; imageUrl: string; corners: [Position, Position, Position, Position] };
export type SiteDependency = { id: string; name: string; kind: "gateway" | "switch" | "ups" };
export type RecordingCoverage = { incidentId: string; state: "awaiting" | "retrieving" | "available" | "gap" | "unavailable" | "unsupported"; checkedAt: number };
export type Modality = "vision" | "rf" | "access";
export type ZoneKind = "Perimeter" | "Restricted" | "Access";
export type AlertStatus = "new" | "acknowledged" | "resolved";
export const EVENT_TYPES = { "door-forced-open": "Door forced open", "door-held-open": "Door held open", "line-crossing": "Camera line crossing", motion: "Camera motion", tamper: "Camera tampering", "rf-detection": "RF detection",
  "zone-entry": "Camera zone entry", "zone-dwell": "Camera dwell", "restricted-stop": "Vehicle stop", "drone-sighting": "Possible drone sighting" } as const;
export type EventType = keyof typeof EVENT_TYPES;
export type AnalyticsKind = "line-crossing" | "zone-entry" | "zone-dwell" | "restricted-stop" | "drone-sighting";
export type AnalyticsObject = "person" | "vehicle" | "drone";
export type AnalyticsThresholds = { minimumScore: number; seconds: number; minimumPixels: number };
export type AnalyticsProfile = "all-day" | "day" | "night";
export const ANALYTICS_PLACEMENTS = { cloud: "Cloud", "on-premises": "On-premises" } as const;
export type AnalyticsPlacement = keyof typeof ANALYTICS_PLACEMENTS;
export type AnalyticsRule = AnalyticsThresholds & { id: string; name: string; kind: AnalyticsKind; object: AnalyticsObject; mode: "observe" | "alert";
  boundaryId: string; points: Position[]; direction: "either" | "positive" | "negative"; exclusions?: Position[][];
  schedule?: { dayStartsAt: string; nightStartsAt: string; night: AnalyticsThresholds } };
export type AnalyticsEvidence = { policyVersion: number; ruleId: string; ruleName?: string; object: AnalyticsObject; model: string;
  trackId: string; score: number; box: [number, number, number, number]; frame: [number, number]; source: "live" | "replay";
  mode?: AnalyticsRule["mode"]; profile?: AnalyticsProfile; crossing?: Position; placement?: AnalyticsPlacement };
export type AnalyticsWorker = { version: number; state: "starting" | "running" | "no-video" | "model-error" | "idle" | "delivery-delayed";
  models: string[]; pending: number; fps: number; frameAgeSeconds: number | null; reportedAt: number; placement?: AnalyticsPlacement };
export function analyticsWorkerState(worker: AnalyticsWorker | undefined, placement: AnalyticsPlacement | undefined, version: number, now: number):
  AnalyticsWorker["state"] | "waiting" | "stale" | "old-policy" {
  if (!worker || worker.placement !== placement) return "waiting";
  if (now - worker.reportedAt * 1000 > 35_000) return "stale";
  return worker.version === version ? worker.state : "old-policy";
}
export const INCIDENT_DISPOSITIONS = {
  confirmed: "Confirmed incident", "false-alarm": "False alarm", maintenance: "Maintenance",
  inconclusive: "Inconclusive",
} as const;
export type IncidentDisposition = keyof typeof INCIDENT_DISPOSITIONS;
export type ResponseStep = "verify" | "contact" | "document";
export type IncidentAction = { kind: "acknowledge" }
  | { kind: "claim"; name: string }
  | { kind: "note"; text: string }
  | { kind: "resolve"; disposition: IncidentDisposition }
  | { kind: "checklist"; step: ResponseStep; done: boolean }
  | { kind: "handoff"; nextAction: string; note: string; dueAt?: string }
  | { kind: "accept-handoff"; name: string }
  | { kind: "bookmark"; label: string; startAt: string; endAt: string };
export type IncidentActivity = IncidentAction & { occurredAt: string; operator?: string };

export interface DeviceGroup {
  id: string;
  name: string;
}

export interface CameraView {
  bearingDeg: number;
  fovDeg: number;
  rangeM: number;
}

export const CAMERA_VIEW_LIMITS = {
  bearingDeg: { min: 0, max: 360 },
  fovDeg: { min: 1, max: 360 },
  rangeM: { min: 1, max: 5000 },
} as const;

export function isCameraView(view: CameraView | null | undefined): view is CameraView {
  return !!view && (Object.keys(CAMERA_VIEW_LIMITS) as (keyof CameraView)[]).every((field) => {
    const { min, max } = CAMERA_VIEW_LIMITS[field];
    return Number.isFinite(view[field]) && view[field] >= min && view[field] <= max;
  });
}

export function httpEndpointUrl(value: string | null | undefined): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value.trim());
    return (url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

export const CAMERA_SOURCE_MAX_LENGTH = 80;
export type CameraGatewayKind = "onvif" | "rtsp";
export const VIDEO_ISSUES = { frozen: "Frozen video", obstructed: "Obstructed view", dark: "Dark image", defocused: "Defocused image" } as const;
export type VideoIssue = keyof typeof VIDEO_ISSUES;

export function cameraSourceLabel(value: unknown) {
  return typeof value === "string" ? value.trim().slice(0, CAMERA_SOURCE_MAX_LENGTH) : "";
}

export interface Sensor {
  id: string;
  name: string;
  modality: Modality;
  position: Position;
  floorId?: string;
  demo: boolean;
  groupId: string | null;
  labels: string[];
  mapLabel: string;
  cameraView: CameraView | null;
  cameraFeedUrl: string | null;
  cameraGridUrl?: string | null;
  cameraProvider?: string;
  cameraChannel?: string;
  cameraGatewayKind?: CameraGatewayKind;
  cameraVerifiedAt?: string;
  cameraInspection?: { issue: VideoIssue; confirmedAt: string; note: string };
  analyticsRules?: AnalyticsRule[];
  analyticsPlacement?: AnalyticsPlacement;
  linkedCameraId?: string | null;
  reportIntervalSeconds?: number;
  healthUrl?: string | null;
  gatewayId?: string;
  dependencyIds?: string[];
  healthReport?: HealthReport;
  healthOutages?: HealthOutage[];
  maintenanceWindows?: MaintenanceWindow[];
}

export interface Boundary {
  id: string;
  name: string;
  kind: ZoneKind;
  vertices: Position[];
  floorId?: string;
  sensorIds: string[];
  enabled: boolean;
  mapLabel?: string;
  labels?: string[];
  notes?: string;
  mapTone?: BoundaryTone;
  blocksCameraView?: boolean;
  responseTeam?: string;
}

export interface Recording {
  cameraId: string;
  url: string;
  format: "hls" | "mp4";
  eventOffsetSeconds: number;
  durationSeconds?: number;
}

export interface IncomingObservation {
  id: string;
  sensorId: string;
  occurredAt: string;
  position?: Position;
  summary: string;
  eventType?: EventType;
  gatewayReceivedAt?: number;
  recording?: Recording;
  analytics?: AnalyticsEvidence;
}

export interface Observation extends IncomingObservation {
  position: Position;
}

export interface Alert {
  id: string;
  observation: Observation;
  zoneId: string;
  zoneName: string;
  sensorName: string;
  modality: Modality;
  severity: "High" | "Medium";
  origin: "Simulation" | "Imported sample" | "Camera gateway" | "Site gateway";
  status: AlertStatus;
  reviewedAt?: string;
  cameraId?: string | null;
  locationBasis?: "reported" | "device";
  owner?: string;
  disposition?: IncidentDisposition;
  activity?: IncidentActivity[];
  responseSteps?: Partial<Record<ResponseStep, boolean>>;
  handoff?: { nextAction: string; note: string; occurredAt: string; operator: string; dueAt?: string; acceptedBy?: string };
  correlation?: { incidentId: string; seconds: number };
  configurationVersion?: number;
  responseTeam?: string;
  floorId?: string;
  evidenceBookmarks?: { id: string; label: string; startAt: string; endAt: string; operator: string }[];
}

export interface SiteEvent {
  observation: IncomingObservation;
  sensorName: string;
  modality: Modality;
  origin: Alert["origin"];
  cameraId: string | null;
  locationBasis?: Alert["locationBasis"];
  floorId?: string;
  boundaries: Array<{ id: string; name: string }>;
}

export interface Workspace {
  boundaries: Boundary[];
  sensors: Sensor[];
  groups: DeviceGroup[];
  alerts: Alert[];
  received: string[];
  gatewayIncidents?: GatewayIncident[];
  siteEvents?: SiteEvent[];
  correlationRule?: CorrelationRule;
  savedIncidentSearches?: { name: string; filters: IncidentFilters }[];
  floors?: SiteFloor[];
  dependencies?: SiteDependency[];
  dependencyHealth?: Record<string, HealthReport>;
  recordingCoverage?: RecordingCoverage[];
  recordingRecoveryEnabled?: boolean;
  analyticsWorkers?: Record<string, AnalyticsWorker>;
}

export const MODALITIES = ["vision", "rf", "access"] as const;
export const MODALITY_LABEL: Record<Modality, string> = {
  vision: "Vision", rf: "RF", access: "Access control",
};
export const ZONE_KINDS = ["Perimeter", "Restricted", "Access"] as const;
export const ZONE_COLORS: Record<ZoneKind, string> = {
  Perimeter: "#a2afa6", Restricted: "#b5a69d", Access: "#a8a7af",
};

export const BOUNDARY_TONES = {
  sage: { label: "Sage", color: "#a2afa6" },
  stone: { label: "Stone", color: "#b5a69d" },
  slate: { label: "Slate", color: "#a8a7af" },
  rose: { label: "Rose", color: "#c39a97" },
  copper: { label: "Copper", color: "#c68f72" },
} as const;
export type BoundaryTone = keyof typeof BOUNDARY_TONES;

export function boundaryColor(boundary: Boundary, visibility?: Visibility) {
  if (visibility || !boundary.enabled) return BOUNDARY_VISIBILITY[visibility ?? "paused"].color;
  return (boundary.mapTone && BOUNDARY_TONES[boundary.mapTone]?.color) || ZONE_COLORS[boundary.kind];
}

export const BOUNDARY_VISIBILITY = {
  unassigned: { label: "Unassigned", color: "#c39a97" },
  "no-live-detections": { label: "No live detections", color: "#b5aaa1" },
  paused: { label: "Paused", color: "#96949b" },
} as const;

export type Visibility = keyof typeof BOUNDARY_VISIBILITY;

export interface BoundaryPosture {
  boundary: Boundary;
  assignedCount: number;
  visibility: Visibility;
}

export function boundaryVisibility(boundary: Boundary, assignedCount: number): Visibility {
  if (!boundary.enabled) return "paused";
  // Video playback and imported samples do not establish detection adapter health.
  return assignedCount ? "no-live-detections" : "unassigned";
}

export function labelKey(label: string) {
  return label.normalize("NFKC").toLowerCase();
}

export function parseLabels(input: string) {
  const labels = new Map<string, string>();
  for (const entry of input.split(",")) {
    const label = entry.trim();
    const key = labelKey(label);
    if (label && !labels.has(key)) labels.set(key, label);
  }
  return [...labels.values()];
}

export function validLabels(labels: string[]) {
  return labels.length <= 8 && labels.every((label) => label.length <= 32);
}

export const SITE_CENTER: Position = [-122.3024, 37.7884];
export const SITE_ZOOM = 15.7;
export const SITE_BOUNDS: [Position, Position] = [[-122.3082, 37.7845], [-122.2953, 37.7925]];

const EPSILON = 1e-12;
const STORAGE_KEY = "proxigo.global-view.preview.v1";

function cross(a: Position, b: Position, c: Position) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

function onSegment(a: Position, b: Position, point: Position) {
  return Math.abs(cross(a, b, point)) <= EPSILON
    && point[0] >= Math.min(a[0], b[0]) && point[0] <= Math.max(a[0], b[0])
    && point[1] >= Math.min(a[1], b[1]) && point[1] <= Math.max(a[1], b[1]);
}

function intersects(a: Position, b: Position, c: Position, d: Position) {
  const ab = cross(a, b, c) * cross(a, b, d);
  const cd = cross(c, d, a) * cross(c, d, b);
  return (ab < 0 && cd < 0) || onSegment(a, b, c) || onSegment(a, b, d)
    || onSegment(c, d, a) || onSegment(c, d, b);
}

export function isPosition(value: unknown): value is Position {
  return Array.isArray(value) && value.length === 2
    && value.every((coordinate) => typeof coordinate === "number" && Number.isFinite(coordinate))
    && Math.abs(value[0]) <= 180 && Math.abs(value[1]) <= 85;
}

export function saveDeviceAssignments(workspace: Workspace, sensor: Sensor, boundaryIds: string[]): Workspace {
  const assigned = new Set(boundaryIds);
  const previous = workspace.sensors.find((entry) => entry.id === sensor.id);
  const removesCamera = previous?.modality === "vision" && sensor.modality !== "vision";
  const sensors = previous ? workspace.sensors.map((entry) => {
    if (entry.id === sensor.id) return sensor;
    if (removesCamera && entry.modality === "access" && entry.linkedCameraId === sensor.id) return { ...entry, linkedCameraId: null };
    return entry;
  }) : [...workspace.sensors, sensor];
  const boundaries = workspace.boundaries.map((boundary) => {
    const linked = boundary.sensorIds.includes(sensor.id);
    if (linked === assigned.has(boundary.id)) return boundary;
    return { ...boundary, sensorIds: linked ? boundary.sensorIds.filter((id) => id !== sensor.id) : [...boundary.sensorIds, sensor.id] };
  });
  return { ...workspace, sensors, boundaries };
}

export function validateBoundary(vertices: Position[]): string | null {
  if (vertices.length < 3) return "Place at least three vertices.";
  if (!vertices.every(isPosition)) return "Use valid map coordinates.";
  if (new Set(vertices.map((point) => point.join(","))).size !== vertices.length) return "Each vertex must be unique.";
  const longitudes = vertices.map(([longitude]) => longitude);
  if (Math.max(...longitudes) - Math.min(...longitudes) >= 180) return "Draw a local boundary on one side of the date line.";

  let area = 0;
  for (let i = 0; i < vertices.length; i++) {
    const next = (i + 1) % vertices.length;
    area += cross(vertices[0], vertices[i], vertices[next]);
    for (let j = i + 1; j < vertices.length; j++) {
      const following = (j + 1) % vertices.length;
      if (j === next || following === i) continue;
      if (intersects(vertices[i], vertices[next], vertices[j], vertices[following])) {
        return "Boundary edges must not cross.";
      }
    }
  }
  return Math.abs(area) <= EPSILON ? "Draw a boundary with an interior area." : null;
}

export function containsPoint(vertices: Position[], point: Position) {
  const [longitude, latitude] = point;
  let inside = false;
  for (let i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
    const a = vertices[i];
    const b = vertices[j];
    if (onSegment(a, b, point)) return true;
    if ((a[1] > latitude) !== (b[1] > latitude)
      && longitude < a[0] + (latitude - a[1]) * (b[0] - a[0]) / (b[1] - a[1])) inside = !inside;
  }
  return inside;
}

export function samplePosition(vertices: Position[]): Position {
  const latitudes = [...new Set(vertices.map(([, latitude]) => latitude))].sort((a, b) => a - b);
  const latitude = (latitudes[0] + latitudes[1]) / 2;
  const crossings = vertices.flatMap((a, index) => {
    const b = vertices[(index + 1) % vertices.length];
    return (a[1] > latitude) !== (b[1] > latitude)
      ? [a[0] + (latitude - a[1]) * (b[0] - a[0]) / (b[1] - a[1])] : [];
  }).sort((a, b) => a - b);
  return crossings.length >= 2 ? [(crossings[0] + crossings[1]) / 2, latitude] : vertices[0];
}

export function associatedCamera(sensor: Sensor | undefined, sensors: Sensor[]) {
  if (sensor?.modality === "vision") return sensor;
  return sensors.find((camera) => camera.id === sensor?.linkedCameraId && camera.modality === "vision");
}

export function observationKey(observation: Pick<IncomingObservation, "sensorId" | "id">) {
  return JSON.stringify([observation.sensorId, observation.id]);
}

export function evaluateObservation(workspace: Workspace, incoming: IncomingObservation, origin: Alert["origin"], allowUnlocated = false) {
  const sensor = workspace.sensors.find((entry) => entry.id === incoming.sensorId);
  if (!sensor) throw new Error(`Unknown sensor ID: ${incoming.sensorId}`);
  const key = observationKey(incoming);
  if (workspace.received.includes(key)) return { workspace, created: [], duplicate: true };

  const useDeviceLocation = sensor.modality === "access" || (sensor.modality === "vision" && (origin === "Camera gateway" || origin === "Site gateway" || incoming.eventType === "line-crossing"));
  const position = incoming.position ?? (useDeviceLocation ? sensor.position : undefined);
  if (!position && !allowUnlocated) throw new Error("A detection position is required for this device type.");
  const cameraId = associatedCamera(sensor, workspace.sensors)?.id ?? null;
  if (incoming.recording && incoming.recording.cameraId !== cameraId) {
    throw new Error("The recording must belong to the source camera or the device's linked camera.");
  }
  const locationBasis = incoming.position ? "reported" : position ? "device" : undefined;
  const matches = position ? workspace.boundaries.filter((zone) => zone.enabled
    && zone.floorId === sensor.floorId && zone.sensorIds.includes(sensor.id) && (locationBasis === "device" || containsPoint(zone.vertices, position))) : [];
  const created: Alert[] = position ? matches.map((zone) => ({
    id: JSON.stringify([sensor.id, incoming.id, zone.id]),
    observation: { ...incoming, position }, zoneId: zone.id, zoneName: zone.name, sensorName: sensor.name, modality: sensor.modality,
    severity: zone.kind === "Restricted" ? "High" : "Medium", origin, status: "new", cameraId, locationBasis, floorId: sensor.floorId,
    ...(zone.responseTeam ? { responseTeam: zone.responseTeam } : {}),
  } satisfies Alert)).map((alert) => correlateAlert(alert, workspace.alerts, workspace.correlationRule)) : [];
  const event: SiteEvent = {
    observation: { ...incoming, position }, sensorName: sensor.name, modality: sensor.modality, origin, cameraId, locationBasis, floorId: sensor.floorId,
    boundaries: matches.map(({ id, name }) => ({ id, name })),
  };
  return {
    workspace: { ...workspace, received: [...workspace.received, key], alerts: [...created, ...workspace.alerts],
      siteEvents: [event, ...(workspace.siteEvents ?? [])].slice(0, 500) },
    created,
    duplicate: false,
  };
}

function parseRecording(value: unknown): Recording | undefined {
  if (value === undefined) return;
  const recording = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const { cameraId, format, eventOffsetSeconds = 0, durationSeconds } = recording;
  const url = typeof recording.url === "string" && recording.url.length <= 4096 ? httpEndpointUrl(recording.url) : null;
  if (typeof cameraId !== "string" || !cameraId.trim() || cameraId.length > 128 || !url
    || (format !== "hls" && format !== "mp4") || typeof eventOffsetSeconds !== "number"
    || !Number.isFinite(eventOffsetSeconds) || eventOffsetSeconds < 0) {
    throw new Error("A recording needs a camera ID, an HTTP(S) URL, an hls or mp4 format, and an optional nonnegative event offset.");
  }
  if (durationSeconds !== undefined && (typeof durationSeconds !== "number" || !Number.isFinite(durationSeconds) || durationSeconds <= eventOffsetSeconds)) {
    throw new Error("The recording duration must be greater than its event offset.");
  }
  return { cameraId, url, format, eventOffsetSeconds, ...(durationSeconds === undefined ? {} : { durationSeconds }) };
}

export function parseObservation(value: unknown): IncomingObservation {
  if (!value || typeof value !== "object") throw new Error("Expected a JSON observation object.");
  const { id, sensorId, occurredAt, position, summary, recording, eventType, gatewayReceivedAt } = value as Record<string, unknown>;
  if (typeof id !== "string" || !id.trim() || id.length > 128
    || typeof sensorId !== "string" || !sensorId.trim() || sensorId.length > 128
    || typeof summary !== "string" || !summary.trim() || summary.length > 160
    || typeof occurredAt !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/.test(occurredAt)
    || !Number.isFinite(Date.parse(occurredAt)) || (position !== undefined && !isPosition(position))) {
    throw new Error("Include id, sensorId, summary, an ISO timestamp with a timezone, and a valid position if supplied.");
  }
  if (eventType !== undefined && (typeof eventType !== "string" || !Object.prototype.hasOwnProperty.call(EVENT_TYPES, eventType))) throw new Error("Unsupported event type.");
  return { id, sensorId, summary: summary.trim(), occurredAt: new Date(occurredAt).toISOString(), ...(eventType ? { eventType: eventType as EventType } : {}),
    ...(typeof gatewayReceivedAt === "number" && Number.isFinite(gatewayReceivedAt) && gatewayReceivedAt > 0 && gatewayReceivedAt < 8.64e12 ? { gatewayReceivedAt } : {}),
    position: isPosition(position) ? position : undefined, recording: parseRecording(recording) };
}

export async function readMonitoringFile(file: File): Promise<unknown> {
  if (file.size > 32_768) throw new Error("Choose one JSON file under 32 KB.");
  try { return JSON.parse(await file.text()); }
  catch (error) {
    if (error instanceof SyntaxError) throw new Error("The file contains invalid JSON.");
    throw error;
  }
}

export const SAMPLE_SUMMARIES: Record<Modality, string> = {
  vision: "Motion detected", rf: "RF energy detected", access: "Unscheduled gate opening",
};

const DEMO_GROUPS = [
  { id: "perimeter-devices", name: "Perimeter" },
  { id: "yard-devices", name: "Yard and loading" },
  { id: "access-devices", name: "Access points" },
];

const DEMO_DEVICE_METADATA: Record<string, Pick<Sensor, "groupId" | "labels" | "mapLabel">> = {
  "cam-01": { groupId: "perimeter-devices", labels: ["Outdoor", "Perimeter"], mapLabel: "North camera" },
  "rf-01": { groupId: "yard-devices", labels: ["Outdoor", "Yard"], mapLabel: "Yard RF" },
  "cam-02": { groupId: "yard-devices", labels: ["Outdoor", "Loading dock"], mapLabel: "Dock camera" },
  "gate-01": { groupId: "access-devices", labels: ["Outdoor", "Vehicle gate"], mapLabel: "East gate" },
};

const DEMO_CAMERA_VIEWS: Record<string, CameraView> = {
  "cam-01": { bearingDeg: 120, fovDeg: 80, rangeM: 610 },
  "cam-02": { bearingDeg: 301, fovDeg: 76, rangeM: 470 },
};

type StoredSensor = Omit<Sensor, "groupId" | "labels" | "mapLabel" | "cameraView" | "cameraFeedUrl">
  & Partial<Pick<Sensor, "groupId" | "labels" | "mapLabel" | "cameraView" | "cameraFeedUrl">>;

function migrateSensor(sensor: StoredSensor): Sensor {
  const metadata = sensor.demo ? DEMO_DEVICE_METADATA[sensor.id] : undefined;
  const view = sensor.cameraView === undefined && sensor.demo ? DEMO_CAMERA_VIEWS[sensor.id] : sensor.cameraView;
  return {
    ...sensor,
    groupId: sensor.groupId === undefined ? metadata?.groupId ?? null : sensor.groupId,
    labels: sensor.labels ?? metadata?.labels ?? [],
    mapLabel: sensor.mapLabel ?? metadata?.mapLabel ?? "",
    cameraView: sensor.modality === "vision" && isCameraView(view) ? view : null,
    cameraFeedUrl: sensor.modality === "vision" ? httpEndpointUrl(sensor.cameraFeedUrl) : null,
    cameraGridUrl: sensor.modality === "vision" ? httpEndpointUrl(sensor.cameraGridUrl) : null,
    cameraProvider: sensor.modality === "vision" ? cameraSourceLabel(sensor.cameraProvider) || undefined : undefined,
    cameraChannel: sensor.modality === "vision" ? cameraSourceLabel(sensor.cameraChannel) || undefined : undefined,
    cameraGatewayKind: sensor.modality === "vision" && (sensor.cameraGatewayKind === "rtsp" || sensor.cameraGatewayKind === "onvif")
      ? sensor.cameraGatewayKind : undefined,
    healthUrl: httpEndpointUrl(sensor.healthUrl),
    linkedCameraId: sensor.modality === "access"
      ? sensor.linkedCameraId === undefined && sensor.demo && sensor.id === "gate-01" ? "cam-02" : sensor.linkedCameraId ?? null
      : null,
  };
}

function demoWorkspace(): Workspace {
  const sensors = [
    { id: "cam-01", name: "North perimeter camera", modality: "vision", position: [-122.3065, 37.7911], demo: true },
    { id: "rf-01", name: "Yard RF receiver", modality: "rf", position: [-122.3026, 37.7890], demo: true },
    { id: "cam-02", name: "Loading dock camera", modality: "vision", position: [-122.2996, 37.7875], demo: true },
    { id: "gate-01", name: "East gate contact", modality: "access", position: [-122.2974, 37.7860], demo: true },
  ] satisfies StoredSensor[];
  const devices = sensors.map(migrateSensor);
  const boundaries: Boundary[] = [
    { id: "north-perimeter", name: "North perimeter", kind: "Perimeter", enabled: true, sensorIds: ["cam-01", "rf-01"],
      vertices: [[-122.3082, 37.7916], [-122.3054, 37.7925], [-122.3032, 37.7910], [-122.3069, 37.7897]] },
    { id: "restricted-yard", name: "Restricted yard", kind: "Restricted", enabled: true, sensorIds: ["rf-01", "cam-02"],
      vertices: [[-122.3047, 37.7895], [-122.3008, 37.7907], [-122.2985, 37.7880], [-122.3021, 37.7868]] },
    { id: "east-access", name: "East access road", kind: "Access", enabled: true, sensorIds: ["gate-01"],
      vertices: [[-122.3007, 37.7864], [-122.2966, 37.7873], [-122.2953, 37.7855], [-122.2994, 37.7845]] },
  ];
  let workspace: Workspace = { sensors: devices, groups: DEMO_GROUPS, boundaries, alerts: [], received: [] };
  const samples: { zone: number; sensor: number; position: Position; minutes: number; status: AlertStatus }[] = [
    { zone: 2, sensor: 3, position: [-122.2987, 37.7865], minutes: 46, status: "resolved" },
    { zone: 0, sensor: 0, position: [-122.3056, 37.7910], minutes: 19, status: "new" },
    { zone: 1, sensor: 1, position: [-122.3014, 37.7884], minutes: 4, status: "new" },
  ];
  for (const sample of samples) {
    const sensor = sensors[sample.sensor];
    const observation = {
      id: `demo-${boundaries[sample.zone].id}`, sensorId: sensor.id,
      occurredAt: new Date(Date.now() - sample.minutes * 60_000).toISOString(),
      position: sample.position, summary: SAMPLE_SUMMARIES[sensor.modality],
    };
    const result = evaluateObservation(workspace, observation, "Simulation");
    workspace = { ...result.workspace, alerts: result.workspace.alerts.map((alert) =>
      result.created.some((created) => created.id === alert.id) ? { ...alert, status: sample.status } : alert) };
  }
  return workspace;
}

export function loadWorkspace(): Workspace {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const workspace: Workspace = JSON.parse(stored);
      if (Array.isArray(workspace.boundaries) && Array.isArray(workspace.sensors)
        && Array.isArray(workspace.alerts) && Array.isArray(workspace.received)) {
        const sensors = workspace.sensors.map(migrateSensor);
        const groups = Array.isArray(workspace.groups) ? workspace.groups
          : DEMO_GROUPS.filter((group) => sensors.some((sensor) => sensor.groupId === group.id));
        return { ...workspace, sensors, groups };
      }
    }
  } catch (error) {
    console.warn("Unable to load the local monitoring workspace", error);
  }
  return demoWorkspace();
}

export function saveWorkspace(workspace: Workspace) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(workspace));
}
