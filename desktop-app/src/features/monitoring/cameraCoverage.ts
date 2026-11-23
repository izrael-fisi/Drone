import clipping, { type MultiPolygon, type Polygon, type Ring } from "polygon-clipping";
import { isCameraView, type Boundary, type CameraView, type Position, type Sensor } from "./monitoringModel.ts";

const EARTH_RADIUS_M = 6_371_008.8;
const RADIANS = Math.PI / 180;

export const CAMERA_COVERAGE_COLORS = {
  view: { label: "Camera view", color: "#a5b9b0" },
  overlap: { label: "2+ camera views", color: "#b8a8ba" },
  gap: { label: "Camera gap", color: "#c08a8c" },
} as const;

export interface CameraSector { id: string; geometry: MultiPolygon }
export interface CameraCoverage {
  sectors: CameraSector[];
  overlaps: MultiPolygon;
  gaps: MultiPolygon;
  gapFractionByBoundary: Map<string, number>;
}

export function coveragePercent(fraction: number) {
  if (fraction > 0 && fraction < 0.01) return "<1%";
  if (fraction > 0.99 && fraction < 1) return ">99%";
  return `${Math.round(fraction * 100)}%`;
}

function destination([lng, lat]: Position, bearingDeg: number, distanceM: number): Position {
  const latitude = lat * RADIANS;
  const distance = distanceM / EARTH_RADIUS_M;
  const bearing = bearingDeg * RADIANS;
  const targetLatitude = Math.asin(Math.sin(latitude) * Math.cos(distance)
    + Math.cos(latitude) * Math.sin(distance) * Math.cos(bearing));
  const longitudeDelta = Math.atan2(Math.sin(bearing) * Math.sin(distance) * Math.cos(latitude),
    Math.cos(distance) - Math.sin(latitude) * Math.sin(targetLatitude));
  return [lng + longitudeDelta / RADIANS, targetLatitude / RADIANS];
}

export function cameraSector(position: Position, view: CameraView, blockers: Position[][] = []): MultiPolygon {
  const steps = Math.ceil(view.fovDeg / 3);
  const fullCircle = view.fovDeg === 360;
  const start = view.bearingDeg - view.fovDeg / 2;
  const unwrap = ([lng, lat]: Position): Position => [position[0] + ((lng - position[0] + 540) % 360 - 180), lat];
  const walls = blockers.flatMap((polygon) => polygon.map((vertex, index) => [unwrap(vertex), unwrap(polygon[(index + 1) % polygon.length])]));
  const offsets = new Set(Array.from({ length: fullCircle ? steps : steps + 1 }, (_, index) => view.fovDeg * index / steps));
  for (const [point] of walls) {
    const bearing = Math.atan2((point[0] - position[0]) * Math.cos(position[1] * RADIANS), point[1] - position[1]) / RADIANS;
    const offset = ((bearing - start) % 360 + 360) % 360;
    for (const sample of [offset - .001, offset, offset + .001]) {
      if (sample > 0 && sample < view.fovDeg) offsets.add(sample);
    }
  }
  const arc = [...offsets].sort((a, b) => a - b).map((offset): Position => {
    const end = destination(position, start + offset, view.rangeM);
    const dx = end[0] - position[0], dy = end[1] - position[1];
    let first = 1;
    for (const [a, b] of walls) {
      const wx = b[0] - a[0], wy = b[1] - a[1];
      const divisor = dx * wy - dy * wx;
      if (Math.abs(divisor) < 1e-18) continue;
      const x = a[0] - position[0], y = a[1] - position[1];
      const fraction = (x * wy - y * wx) / divisor;
      const alongWall = (x * dy - y * dx) / divisor;
      if (fraction > 1e-8 && fraction < first && alongWall >= 0 && alongWall <= 1) first = fraction;
    }
    return [position[0] + dx * first, position[1] + dy * first];
  });
  const ring: Ring = fullCircle ? [...arc, arc[0]].reverse() : [position, ...arc, position].reverse();
  const bands = ring.map(([lng]) => Math.floor((lng + 180) / 360));
  const first = Math.min(...bands);
  const last = Math.max(...bands);
  if (first === 0 && last === 0) return [[ring]];

  // Work in continuous longitude, then split at the seam before rendering or intersecting site polygons.
  const pieces: MultiPolygon = [];
  for (let band = first; band <= last; band++) {
    const west = band * 360 - 180;
    const east = west + 360;
    const world: Polygon = [[[west, -90], [east, -90], [east, 90], [west, 90], [west, -90]]];
    const clipped = clipping.intersection([ring], world);
    pieces.push(...clipped.map((polygon) => polygon.map((edge) => edge.map(([lng, lat]) => [lng - band * 360, lat] as Position))));
  }
  return pieces;
}

function ringArea(ring: Ring) {
  const [originLng, originLat] = ring[0];
  // For a site-scale area ratio, the same local degree-to-meter factor cancels for both shapes.
  let sum = 0;
  for (let index = 0; index < ring.length; index++) {
    const [lng, lat] = ring[index];
    const [nextLng, nextLat] = ring[(index + 1) % ring.length];
    sum += (lng - originLng) * (nextLat - originLat) - (nextLng - originLng) * (lat - originLat);
  }
  return Math.abs(sum) / 2;
}

function area(geometry: MultiPolygon) {
  return geometry.reduce((sum, [exterior, ...holes]) => sum + ringArea(exterior)
    - holes.reduce((total, hole) => total + ringArea(hole), 0), 0);
}

export function calculateCameraCoverage(sensors: Sensor[], boundaries: Boundary[], blockers = boundaries.filter((area) => area.blocksCameraView).map((area) => area.vertices)): CameraCoverage {
  const sectors = sensors.flatMap((sensor) => sensor.modality === "vision" && isCameraView(sensor.cameraView)
    ? [{ id: sensor.id, geometry: cameraSector(sensor.position, sensor.cameraView, blockers) }] : []);
  let union: MultiPolygon = [];
  const intersections: MultiPolygon[] = [];
  for (const { geometry } of sectors) {
    if (union.length) {
      const shared = clipping.intersection(union, geometry);
      if (shared.length) intersections.push(shared);
    }
    union = union.length ? clipping.union(union, geometry) : geometry;
  }

  const gapFractionByBoundary = new Map<string, number>();
  const boundaryGaps = boundaries.flatMap((boundary) => {
    if (!boundary.enabled) return [];
    const site: MultiPolygon = [[[...boundary.vertices, boundary.vertices[0]]]];
    const remainder = union.length ? clipping.difference(site, union) : site;
    const siteArea = area(site);
    if (siteArea > 0) gapFractionByBoundary.set(boundary.id, Math.max(0, Math.min(1, area(remainder) / siteArea)));
    return remainder.length ? [remainder] : [];
  });

  return {
    sectors,
    overlaps: intersections.length ? clipping.union(intersections[0], ...intersections.slice(1)) : [],
    gaps: boundaryGaps.length ? clipping.union(boundaryGaps[0], ...boundaryGaps.slice(1)) : [],
    gapFractionByBoundary,
  };
}
