import type { LatLon } from "../types";

/**
 * Site projects: a named place on the map (a facility, a survey site) with
 * the things an operator lays out there. Saved to disk by Rust
 * (`projects.json`); this module owns the schema.
 */

export type PlacedKind = "camera" | "sensor" | "marker" | "launch" | "geofence" | "keepout";

export interface PlacedItem {
  id: string;
  kind: PlacedKind;
  name: string;
  notes: string;
  /** Point items. */
  position?: LatLon;
  /** Area items (geofence, keep-out), at least 3 corners. */
  polygon?: LatLon[];
  /** Camera: direction the lens faces, degrees clockwise from north. */
  headingDeg?: number;
  /** Camera: horizontal field of view in degrees. */
  fovDeg?: number;
  /** Camera view distance or sensor coverage radius, metres. */
  rangeM?: number;
  createdAt: string;
  updatedAt: string;
}

export interface MapView {
  center: LatLon;
  zoom: number;
}

export interface Project {
  id: string;
  name: string;
  /** Where the project opens on the map. */
  view: MapView;
  items: PlacedItem[];
  createdAt: string;
  updatedAt: string;
}

export interface ProjectsDocument {
  version: 1;
  activeProjectId: string | null;
  projects: Project[];
}

export const EMPTY_DOCUMENT: ProjectsDocument = { version: 1, activeProjectId: null, projects: [] };

interface KindInfo {
  label: string;
  plural: string;
  geometry: "point" | "area";
  color: string;
  /** Defaults applied when an item of this kind is placed. */
  defaults?: Partial<Pick<PlacedItem, "headingDeg" | "fovDeg" | "rangeM">>;
}

export const KINDS: Record<PlacedKind, KindInfo> = {
  camera: { label: "Camera", plural: "Cameras", geometry: "point", color: "#38BDF8", defaults: { headingDeg: 0, fovDeg: 90, rangeM: 60 } },
  sensor: { label: "Sensor", plural: "Sensors", geometry: "point", color: "#2DD4BF", defaults: { rangeM: 40 } },
  marker: { label: "Marker", plural: "Markers", geometry: "point", color: "#E2E8F0" },
  launch: { label: "Launch pad", plural: "Launch pads", geometry: "point", color: "#FACC15" },
  geofence: { label: "Geofence", plural: "Geofences", geometry: "area", color: "#22C55E" },
  keepout: { label: "Keep-out zone", plural: "Keep-out zones", geometry: "area", color: "#EF4444" },
};

export const KIND_ORDER: PlacedKind[] = ["camera", "sensor", "marker", "launch", "geofence", "keepout"];

/** Parse a stored document, dropping anything malformed instead of failing the whole load. */
export function parseDocument(raw: unknown): ProjectsDocument {
  if (!raw || typeof raw !== "object") return EMPTY_DOCUMENT;
  const doc = raw as Partial<ProjectsDocument>;
  const isLatLon = (value: unknown): value is LatLon =>
    Array.isArray(value) && value.length === 2 && value.every((n) => typeof n === "number" && Number.isFinite(n));
  const projects = (Array.isArray(doc.projects) ? doc.projects : [])
    .filter((project): project is Project => Boolean(project && typeof project.id === "string" && project.view && isLatLon(project.view.center)))
    .map((project) => ({
      ...project,
      name: String(project.name ?? "Untitled project"),
      view: { center: project.view.center, zoom: Number.isFinite(project.view.zoom) ? project.view.zoom : 15 },
      items: (Array.isArray(project.items) ? project.items : []).filter(
        (item) =>
          item &&
          item.kind in KINDS &&
          (KINDS[item.kind].geometry === "point" ? isLatLon(item.position) : Array.isArray(item.polygon) && item.polygon.length >= 3 && item.polygon.every(isLatLon)),
      ),
    }));
  const activeProjectId = projects.some((project) => project.id === doc.activeProjectId) ? doc.activeProjectId ?? null : projects[0]?.id ?? null;
  return { version: 1, activeProjectId, projects };
}

// ---------------------------------------------------------------- geometry

const EARTH_RADIUS_M = 6_371_008.8;
const toRad = (deg: number) => (deg * Math.PI) / 180;
const toDeg = (rad: number) => (rad * 180) / Math.PI;

/** Point reached from `origin` after `distanceM` metres on `bearingDeg`. */
export function destination([lat, lon]: LatLon, bearingDeg: number, distanceM: number): LatLon {
  const angular = distanceM / EARTH_RADIUS_M;
  const bearing = toRad(bearingDeg);
  const lat1 = toRad(lat);
  const lon1 = toRad(lon);
  const lat2 = Math.asin(Math.sin(lat1) * Math.cos(angular) + Math.cos(lat1) * Math.sin(angular) * Math.cos(bearing));
  const lon2 = lon1 + Math.atan2(Math.sin(bearing) * Math.sin(angular) * Math.cos(lat1), Math.cos(angular) - Math.sin(lat1) * Math.sin(lat2));
  return [toDeg(lat2), ((toDeg(lon2) + 540) % 360) - 180];
}

/** Camera view cone: apex at the camera, arc at `rangeM`. */
export function viewCone(origin: LatLon, headingDeg: number, fovDeg: number, rangeM: number): LatLon[] {
  const fov = Math.min(Math.max(fovDeg, 1), 359);
  const steps = Math.max(4, Math.ceil(fov / 5));
  const arc = Array.from({ length: steps + 1 }, (_, i) => destination(origin, headingDeg - fov / 2 + (fov * i) / steps, rangeM));
  return [origin, ...arc];
}

/** Circle approximated by `segments` points. */
export function circle(center: LatLon, radiusM: number, segments = 48): LatLon[] {
  return Array.from({ length: segments }, (_, i) => destination(center, (360 * i) / segments, radiusM));
}

/** Planar area of a small polygon in square metres (equirectangular; fine at site scale). */
export function areaM2(polygon: LatLon[]): number {
  if (polygon.length < 3) return 0;
  const lat0 = toRad(polygon.reduce((sum, [lat]) => sum + lat, 0) / polygon.length);
  const xy = polygon.map(([lat, lon]) => [toRad(lon) * Math.cos(lat0) * EARTH_RADIUS_M, toRad(lat) * EARTH_RADIUS_M]);
  let twice = 0;
  for (let i = 0; i < xy.length; i++) {
    const [x1, y1] = xy[i];
    const [x2, y2] = xy[(i + 1) % xy.length];
    twice += x1 * y2 - x2 * y1;
  }
  return Math.abs(twice) / 2;
}

export function formatArea(m2: number) {
  return m2 >= 10_000 ? `${(m2 / 10_000).toFixed(2)} ha` : `${Math.round(m2).toLocaleString()} m²`;
}

export function newId(prefix: string) {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  return `${prefix}-${random}`;
}
