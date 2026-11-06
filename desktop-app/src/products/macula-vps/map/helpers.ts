import type { DronePositionUpdate, MapProvider, MapUsageEstimate, Profile, Region } from "../../../lib/types";
import { clampMapLatitude, clampMapZoom, pointsBounds, wrapMapLongitude } from "../../../platform/map/geo";

/** Macula VPS map tooling helpers: regions, map-cut geometry, providers, telemetry labels. */

export function regionCenter(region?: Region): [number, number] {
  if (!region) return [37.775, -122.418];
  if (region.polygon_points?.length) {
    const lats = region.polygon_points.map(([lat]) => lat);
    const lons = region.polygon_points.map(([, lon]) => lon);
    return [
      (Math.min(...lats) + Math.max(...lats)) / 2,
      (Math.min(...lons) + Math.max(...lons)) / 2,
    ];
  }
  return [(region.lat_min + region.lat_max) / 2, (region.lon_min + region.lon_max) / 2];
}

export function regionPolygon(region?: Region): [number, number][] {
  if (!region) return [];
  if (region.polygon_points?.length) return region.polygon_points;
  return [
    [region.lat_min, region.lon_min],
    [region.lat_min, region.lon_max],
    [region.lat_max, region.lon_max],
    [region.lat_max, region.lon_min],
  ];
}


export function bboxAreaKm2(bounds: ReturnType<typeof pointsBounds>) {
  const latCenter = ((bounds.lat_min + bounds.lat_max) / 2) * (Math.PI / 180);
  const northSouthKm = (bounds.lat_max - bounds.lat_min) * 111.32;
  const eastWestKm = (bounds.lon_max - bounds.lon_min) * 111.32 * Math.cos(latCenter);
  return Math.abs(northSouthKm * eastWestKm);
}

export function polygonAreaKm2(points: [number, number][]) {
  if (points.length < 3) return 0;
  const latCenter = points.reduce((sum, [lat]) => sum + lat, 0) / points.length;
  const cosLat = Math.max(1e-9, Math.abs(Math.cos(latCenter * Math.PI / 180)));
  const projected = points.map(([lat, lon]) => [lon * 111.32 * cosLat, lat * 111.32]);
  let area = 0;
  for (let index = 0; index < projected.length; index += 1) {
    const [x1, y1] = projected[index];
    const [x2, y2] = projected[(index + 1) % projected.length];
    area += x1 * y2 - x2 * y1;
  }
  return Math.abs(area / 2);
}

export function orderPolygonPoints(points: [number, number][]) {
  if (points.length < 3) return points;
  const centerLat = points.reduce((sum, [lat]) => sum + lat, 0) / points.length;
  const centerLon = points.reduce((sum, [, lon]) => sum + lon, 0) / points.length;
  const cosLat = Math.max(1e-9, Math.abs(Math.cos(centerLat * Math.PI / 180)));
  return [...points].sort((a, b) => {
    const angleA = Math.atan2(a[0] - centerLat, (a[1] - centerLon) * cosLat);
    const angleB = Math.atan2(b[0] - centerLat, (b[1] - centerLon) * cosLat);
    return angleA - angleB;
  });
}


export function providerShortLabel(id: string) {
  if (id === "usgs-imagery") return "USGS";
  if (id === "esri-world-imagery") return "Esri";
  if (id === "mapbox-satellite") return "Mapbox";
  if (id === "bing-aerial") return "Bing";
  return id.replace(/-/g, " ");
}

export function slugifyPathSegment(value: string) {
  return (value || "map-cut")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || "map-cut";
}

export function providerApiKeys(profile: Profile | null): Record<string, string> {
  const mapbox = profile?.mapbox_key?.trim() ?? "";
  const bing = profile?.bing_key?.trim() ?? "";
  return {
    "mapbox-satellite": mapbox,
    mapbox,
    "bing-aerial": bing,
    bing,
  };
}

export function providerNeedsMissingKey(provider: MapProvider | undefined, apiKeys: Record<string, string>) {
  if (!provider?.requires_api_key) return false;
  return !(apiKeys[provider.id] || apiKeys[String(provider.id).replace(/-(satellite|aerial)$/, "")]);
}

export function isDownloadProviderReady(provider: MapProvider, apiKeys: Record<string, string>) {
  return provider.kind !== "vector" && provider.enabled && Boolean(provider.url_template) && !providerNeedsMissingKey(provider, apiKeys);
}

export function sumSelectedProviderTotals(estimate: MapUsageEstimate): MapUsageEstimate {
  if (!estimate.provider_breakdown.length) return estimate;
  return {
    ...estimate,
    estimated_source_mb: estimate.provider_breakdown.reduce((sum, provider) => sum + provider.estimated_source_mb, 0),
    estimated_disk_mb: estimate.provider_breakdown.reduce((sum, provider) => sum + provider.estimated_disk_mb, 0),
  };
}

export function tileCoordinateForPoint(point: [number, number], zoom: number) {
  const z = clampMapZoom(Math.round(zoom));
  const scale = 2 ** z;
  const lat = clampMapLatitude(point[0]);
  const lon = wrapMapLongitude(point[1]);
  const latRad = lat * Math.PI / 180;
  const x = Math.floor(((lon + 180) / 360) * scale);
  const y = Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * scale);
  return {
    z,
    x: Math.max(0, Math.min(scale - 1, x)),
    y: Math.max(0, Math.min(scale - 1, y)),
  };
}

export function satellitePreviewUrl(point: [number, number] | null, zoom: number) {
  if (!point) return null;
  const tile = tileCoordinateForPoint(point, zoom);
  return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${tile.z}/${tile.y}/${tile.x}`;
}

export function combineLayeredEstimates(estimates: MapUsageEstimate[]): MapUsageEstimate {
  const ordered = [...estimates].sort((a, b) => a.zoom - b.zoom);
  const highest = ordered[ordered.length - 1];
  if (!highest) {
    throw new Error("No layer estimates available");
  }
  const providerBreakdown = new Map<string, MapUsageEstimate["provider_breakdown"][number]>();
  for (const estimate of ordered) {
    for (const provider of estimate.provider_breakdown) {
      const current = providerBreakdown.get(provider.provider_id);
      if (!current) {
        providerBreakdown.set(provider.provider_id, { ...provider });
      } else {
        current.tile_count += provider.tile_count;
        current.estimated_source_mb += provider.estimated_source_mb;
        current.estimated_disk_mb += provider.estimated_disk_mb;
        current.gsd_m_per_px = provider.gsd_m_per_px;
        current.overzoomed = current.overzoomed || provider.overzoomed;
        current.key_required = current.key_required || provider.key_required;
        current.enabled = current.enabled && provider.enabled;
      }
    }
  }
  const tileCount = ordered.reduce((sum, estimate) => sum + estimate.tile_count, 0);
  const layerRange = `Z${ordered[0].zoom}-Z${highest.zoom}`;
  const warnings = Array.from(new Set([
    ...ordered.flatMap((estimate) => estimate.warnings),
    `Multi-layer map includes ${layerRange}. Size estimate includes every selected zoom level.`,
  ]));
  return {
    ...highest,
    tile_count: tileCount,
    estimated_source_mb: Array.from(providerBreakdown.values()).reduce((sum, provider) => sum + provider.estimated_source_mb, 0),
    estimated_disk_mb: Array.from(providerBreakdown.values()).reduce((sum, provider) => sum + provider.estimated_disk_mb, 0),
    too_large: ordered.some((estimate) => estimate.too_large),
    over_100_km2: ordered.some((estimate) => estimate.over_100_km2),
    warnings,
    provider_breakdown: Array.from(providerBreakdown.values()),
  };
}

export function positionLatLon(position: DronePositionUpdate | null): [number, number] | null {
  const lat = position?.lat_lon?.lat;
  const lon = position?.lat_lon?.lon;
  return typeof lat === "number" && Number.isFinite(lat) && typeof lon === "number" && Number.isFinite(lon)
    ? [lat, lon]
    : null;
}

export function positionLabel(position: DronePositionUpdate | null) {
  if (!position) return "No packet";
  if (position.source_state === "gps_primary") return "GPS primary";
  if (position.source_state === "vision_correction") return "Vision fix";
  if (position.source_state === "dead_reckoning_between_fixes") return "Dead reckoning";
  if (position.source_state === "gps_degraded") return "GPS degraded";
  if (position.source_state === "no_position") return "No position";
  if (position.source === "gps") return "GPS primary";
  if (position.source === "vision") return "Vision fallback";
  return String(position.source_state ?? position.source ?? "Unknown").replace(/_/g, " ");
}

export function positionTone(position: DronePositionUpdate | null) {
  if (!position) return "offline";
  if (position.source_state === "gps_primary" || position.source === "gps") return "ready";
  if (position.source_state === "vision_correction" || position.source === "vision") return "active";
  if (position.source_state === "dead_reckoning_between_fixes" || position.source_state === "gps_degraded") return "warning";
  return "critical";
}

export function toneClass(tone: string) {
  if (tone === "ready") return "text-status-ready";
  if (tone === "active") return "text-status-active";
  if (tone === "warning") return "text-status-warning";
  if (tone === "critical") return "text-status-critical";
  return "text-slate-500";
}

export function ledClass(tone: string) {
  if (tone === "ready") return "ops-led-ready";
  if (tone === "active") return "ops-led-active";
  if (tone === "warning") return "ops-led-warning";
  if (tone === "critical") return "ops-led-critical";
  return "ops-led-offline";
}


export type CutShape = "box" | "polygon";
export type MapToolMode = "idle" | "box" | "polygon" | "waypoint";
export type MapSelectionValue = "world" | string;
export type CutInstallProgress = {
  label: string;
  percent: number;
  current?: number;
  total?: number;
  tone: "active" | "success" | "error";
};

