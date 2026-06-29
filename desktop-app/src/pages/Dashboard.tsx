import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, Check, ChevronDown, Download, Layers, LocateFixed, Map as MapIcon, Minus, Navigation, Pencil, Plus, Route, Save, Scissors, Undo2, X } from "lucide-react";
import type { CircleLayerSpecification, GeoJSONSource, LngLatBoundsLike, Map as MapLibreMap, MapMouseEvent, StyleSpecification, TransformConstrainFunction } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import L from "leaflet";
import { listen } from "@tauri-apps/api/event";
import { homeDir, join } from "@tauri-apps/api/path";
import { CircleMarker, MapContainer, Pane, Polygon, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import { createSavedMissionId, loadSavedMissions, missionBoundsFromParts, SAVED_MISSIONS_CHANGED, upsertSavedMission } from "../lib/missions";
import { useShellStore } from "../lib/shellStore";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import type { DownloadProgress, DronePositionUpdate, MapProvider, MapProviderId, MapUsageEstimate, Profile, Region, SavedMission } from "../lib/types";
import { cn, formatDate, formatMegabytes } from "../lib/utils";

function regionCenter(region?: Region): [number, number] {
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

function regionPolygon(region?: Region): [number, number][] {
  if (!region) return [];
  if (region.polygon_points?.length) return region.polygon_points;
  return [
    [region.lat_min, region.lon_min],
    [region.lat_min, region.lon_max],
    [region.lat_max, region.lon_max],
    [region.lat_max, region.lon_min],
  ];
}

function pointsBounds(points: [number, number][]) {
  const lats = points.map(([lat]) => lat);
  const lons = points.map(([, lon]) => lon);
  return {
    lat_min: Math.min(...lats),
    lat_max: Math.max(...lats),
    lon_min: Math.min(...lons),
    lon_max: Math.max(...lons),
  };
}

function bboxAreaKm2(bounds: ReturnType<typeof pointsBounds>) {
  const latCenter = ((bounds.lat_min + bounds.lat_max) / 2) * (Math.PI / 180);
  const northSouthKm = (bounds.lat_max - bounds.lat_min) * 111.32;
  const eastWestKm = (bounds.lon_max - bounds.lon_min) * 111.32 * Math.cos(latCenter);
  return Math.abs(northSouthKm * eastWestKm);
}

function polygonAreaKm2(points: [number, number][]) {
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

function orderPolygonPoints(points: [number, number][]) {
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

function rectangleFromCorners(start: [number, number], end: [number, number]): [number, number][] {
  const latMin = Math.min(start[0], end[0]);
  const latMax = Math.max(start[0], end[0]);
  const lonMin = Math.min(start[1], end[1]);
  const lonMax = Math.max(start[1], end[1]);
  return [
    [latMin, lonMin],
    [latMin, lonMax],
    [latMax, lonMax],
    [latMax, lonMin],
  ];
}

function providerShortLabel(id: string) {
  if (id === "usgs-imagery") return "USGS";
  if (id === "esri-world-imagery") return "Esri";
  if (id === "mapbox-satellite") return "Mapbox";
  if (id === "bing-aerial") return "Bing";
  return id.replace(/-/g, " ");
}

function slugifyPathSegment(value: string) {
  return (value || "map-cut")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || "map-cut";
}

function providerApiKeys(profile: Profile | null): Record<string, string> {
  const mapbox = profile?.mapbox_key?.trim() ?? "";
  const bing = profile?.bing_key?.trim() ?? "";
  return {
    "mapbox-satellite": mapbox,
    mapbox,
    "bing-aerial": bing,
    bing,
  };
}

function providerNeedsMissingKey(provider: MapProvider | undefined, apiKeys: Record<string, string>) {
  if (!provider?.requires_api_key) return false;
  return !(apiKeys[provider.id] || apiKeys[String(provider.id).replace(/-(satellite|aerial)$/, "")]);
}

function isDownloadProviderReady(provider: MapProvider, apiKeys: Record<string, string>) {
  return provider.kind !== "vector" && provider.enabled && Boolean(provider.url_template) && !providerNeedsMissingKey(provider, apiKeys);
}

function sumSelectedProviderTotals(estimate: MapUsageEstimate): MapUsageEstimate {
  if (!estimate.provider_breakdown.length) return estimate;
  return {
    ...estimate,
    estimated_source_mb: estimate.provider_breakdown.reduce((sum, provider) => sum + provider.estimated_source_mb, 0),
    estimated_disk_mb: estimate.provider_breakdown.reduce((sum, provider) => sum + provider.estimated_disk_mb, 0),
  };
}

function tileCoordinateForPoint(point: [number, number], zoom: number) {
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

function satellitePreviewUrl(point: [number, number] | null, zoom: number) {
  if (!point) return null;
  const tile = tileCoordinateForPoint(point, zoom);
  return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${tile.z}/${tile.y}/${tile.x}`;
}

function combineLayeredEstimates(estimates: MapUsageEstimate[]): MapUsageEstimate {
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

function positionLatLon(position: DronePositionUpdate | null): [number, number] | null {
  const lat = position?.lat_lon?.lat;
  const lon = position?.lat_lon?.lon;
  return typeof lat === "number" && Number.isFinite(lat) && typeof lon === "number" && Number.isFinite(lon)
    ? [lat, lon]
    : null;
}

function positionLabel(position: DronePositionUpdate | null) {
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

function positionTone(position: DronePositionUpdate | null) {
  if (!position) return "offline";
  if (position.source_state === "gps_primary" || position.source === "gps") return "ready";
  if (position.source_state === "vision_correction" || position.source === "vision") return "active";
  if (position.source_state === "dead_reckoning_between_fixes" || position.source_state === "gps_degraded") return "warning";
  return "critical";
}

function toneClass(tone: string) {
  if (tone === "ready") return "text-status-ready";
  if (tone === "active") return "text-status-active";
  if (tone === "warning") return "text-status-warning";
  if (tone === "critical") return "text-status-critical";
  return "text-slate-500";
}

function ledClass(tone: string) {
  if (tone === "ready") return "ops-led-ready";
  if (tone === "active") return "ops-led-active";
  if (tone === "warning") return "ops-led-warning";
  if (tone === "critical") return "ops-led-critical";
  return "ops-led-offline";
}

type MapApi = {
  zoomIn: () => void;
  zoomOut: () => void;
  recenter: () => void;
  focus: (center: [number, number], zoom: number) => void;
  fitPoints: (points: [number, number][], maxZoom?: number) => void;
};

type MapViewport = {
  center: [number, number];
  zoom: number;
};

type CutShape = "box" | "polygon";
type MapToolMode = "idle" | "box" | "polygon" | "waypoint";
type MapSelectionValue = "world" | string;
type CutInstallProgress = {
  label: string;
  percent: number;
  current?: number;
  total?: number;
  tone: "active" | "success" | "error";
};

const MAP_MIN_ZOOM = 0;
const WEB_MERCATOR_LAT_LIMIT = 85.05112878;
const MAP_MAX_ZOOM = 23;
const PLACEHOLDER_ORG_COUNTRY_CENTER: [number, number] = [39.8283, -98.5795];
const PLACEHOLDER_ORG_COUNTRY_ZOOM = 3;

type MapGeoJsonFeature = {
  type: "Feature";
  properties: Record<string, string | number | boolean | null>;
  geometry:
    | { type: "Point"; coordinates: [number, number] }
    | { type: "LineString"; coordinates: [number, number][] }
    | { type: "Polygon"; coordinates: [Array<[number, number]>] };
};

type MapGeoJsonCollection = {
  type: "FeatureCollection";
  features: MapGeoJsonFeature[];
};

type MapLibreRuntime = typeof import("maplibre-gl") & {
  supported?: (options?: { failIfMajorPerformanceCaveat?: boolean }) => boolean;
};

const EMPTY_GEOJSON: MapGeoJsonCollection = { type: "FeatureCollection", features: [] };
const VECTOR_LABEL_GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const VECTOR_LABEL_SOURCE_URL = "https://tiles.openfreemap.org/planet";

function vectorLabelTextField(): ["coalesce", ["get", string], ["get", string], ["get", string]] {
  return ["coalesce", ["get", "name_en"], ["get", "name:latin"], ["get", "name"]];
}

const SATELLITE_STYLE: StyleSpecification = {
  version: 8,
  glyphs: VECTOR_LABEL_GLYPHS,
  projection: { type: "globe" },
  sky: {
    "sky-color": "#02050a",
    "horizon-color": "#05080c",
    "fog-color": "#02050a",
    "sky-horizon-blend": 0.18,
    "horizon-fog-blend": 0.9,
    "atmosphere-blend": 0.22,
  },
  sources: {
    imagery: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      maxzoom: MAP_MAX_ZOOM,
    },
    openmaptiles: {
      type: "vector",
      url: VECTOR_LABEL_SOURCE_URL,
    },
  },
  layers: [
    {
      id: "background",
      type: "background",
      paint: {
        "background-color": "#02050a",
      },
    },
    {
      id: "imagery",
      type: "raster",
      source: "imagery",
      paint: {
        "raster-brightness-min": 0,
        "raster-brightness-max": 0.72,
        "raster-contrast": 0.08,
        "raster-saturation": -0.22,
        "raster-fade-duration": 140,
        "raster-resampling": "linear",
      },
    },
    {
      id: "operator-country-boundary",
      type: "line",
      source: "openmaptiles",
      "source-layer": "boundary",
      filter: ["all", ["==", ["get", "admin_level"], 2], ["!=", ["get", "maritime"], 1], ["!=", ["get", "disputed"], 1]],
      paint: {
        "line-color": "rgba(224, 228, 226, 0.54)",
        "line-opacity": ["interpolate", ["linear"], ["zoom"], 0, 0.28, 3, 0.48, 7, 0.7],
        "line-width": ["interpolate", ["linear"], ["zoom"], 0, 0.5, 4, 0.9, 9, 1.4],
      },
    },
    {
      id: "operator-state-boundary",
      type: "line",
      source: "openmaptiles",
      "source-layer": "boundary",
      minzoom: 4,
      filter: ["all", ["==", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]],
      paint: {
        "line-color": "rgba(188, 195, 192, 0.35)",
        "line-opacity": ["interpolate", ["linear"], ["zoom"], 4, 0.18, 8, 0.46],
        "line-width": ["interpolate", ["linear"], ["zoom"], 4, 0.35, 9, 0.9],
        "line-dasharray": [2, 2],
      },
    },
    {
      id: "operator-country-label",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      filter: ["==", ["get", "class"], "country"],
      layout: {
        "text-field": vectorLabelTextField(),
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 0, 10, 2, 13, 5, 18, 8, 22],
        "text-max-width": 9,
        "symbol-sort-key": ["coalesce", ["get", "rank"], 99],
        "text-allow-overlap": false,
        "text-ignore-placement": false,
      },
      paint: {
        "text-color": "#f1f5f2",
        "text-halo-color": "rgba(2, 5, 10, 0.9)",
        "text-halo-width": 2,
        "text-halo-blur": 0.7,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 0, 0.88, 7, 0.72, 9, 0],
      },
    },
    {
      id: "operator-state-label",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      minzoom: 4,
      maxzoom: 9,
      filter: ["==", ["get", "class"], "state"],
      layout: {
        "text-field": vectorLabelTextField(),
        "text-font": ["Noto Sans Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 7, 14, 9, 16],
        "text-transform": "uppercase",
        "text-letter-spacing": 0.08,
        "text-max-width": 10,
        "symbol-sort-key": ["coalesce", ["get", "rank"], 99],
      },
      paint: {
        "text-color": "#cfd8d4",
        "text-halo-color": "rgba(2, 5, 10, 0.88)",
        "text-halo-width": 1.6,
        "text-halo-blur": 0.6,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 4, 0.52, 7, 0.76, 9, 0],
      },
    },
    {
      id: "operator-city-label",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      minzoom: 3,
      filter: ["==", ["get", "class"], "city"],
      layout: {
        "text-field": vectorLabelTextField(),
        "text-font": ["Noto Sans Regular"],
        "text-size": ["interpolate", ["exponential", 1.12], ["zoom"], 3, 10, 6, 13, 10, 18, 14, 22],
        "text-max-width": 9,
        "symbol-sort-key": ["coalesce", ["get", "rank"], 99],
        "text-allow-overlap": false,
        "text-ignore-placement": false,
      },
      paint: {
        "text-color": "#f2f7f4",
        "text-halo-color": "rgba(2, 5, 10, 0.92)",
        "text-halo-width": 1.8,
        "text-halo-blur": 0.55,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.78, 5, 0.95],
      },
    },
    {
      id: "operator-town-label",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      minzoom: 6,
      filter: ["==", ["get", "class"], "town"],
      layout: {
        "text-field": vectorLabelTextField(),
        "text-font": ["Noto Sans Regular"],
        "text-size": ["interpolate", ["exponential", 1.1], ["zoom"], 6, 10, 10, 14, 14, 18],
        "text-max-width": 9,
        "symbol-sort-key": ["coalesce", ["get", "rank"], 99],
      },
      paint: {
        "text-color": "#dde5e1",
        "text-halo-color": "rgba(2, 5, 10, 0.9)",
        "text-halo-width": 1.5,
        "text-halo-blur": 0.5,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 6, 0.64, 8, 0.86],
      },
    },
  ],
};

function preferredMapRenderer(): "leaflet" | "maplibre" {
  return "maplibre";
}

function wrapMapLongitude(lon: number) {
  return ((((lon + 180) % 360) + 360) % 360) - 180;
}

function clampMapLatitude(lat: number) {
  return Math.max(-WEB_MERCATOR_LAT_LIMIT, Math.min(WEB_MERCATOR_LAT_LIMIT, lat));
}

function wrapMapLatitude(lat: number) {
  return clampMapLatitude(lat);
}

function clampMapZoom(zoom: number) {
  return Math.max(MAP_MIN_ZOOM, Math.min(MAP_MAX_ZOOM, zoom));
}

function normalizeMapCenter(center: [number, number]): [number, number] {
  return [wrapMapLatitude(center[0]), wrapMapLongitude(center[1])];
}

const constrainGlobeTransform: TransformConstrainFunction = (lngLat, zoom) => {
  const center = lngLat.wrap();
  center.lat = clampMapLatitude(center.lat);
  return { center, zoom: clampMapZoom(zoom) };
};

function toLngLat(point: [number, number]): [number, number] {
  return [wrapMapLongitude(point[1]), wrapMapLatitude(point[0])];
}

function pointFeature(id: string, point: [number, number], properties: MapGeoJsonFeature["properties"] = {}): MapGeoJsonFeature {
  return {
    type: "Feature",
    properties: { id, ...properties },
    geometry: { type: "Point", coordinates: toLngLat(point) },
  };
}

function lineFeature(id: string, points: [number, number][], properties: MapGeoJsonFeature["properties"] = {}): MapGeoJsonFeature | null {
  if (points.length < 2) return null;
  return {
    type: "Feature",
    properties: { id, ...properties },
    geometry: { type: "LineString", coordinates: points.map(toLngLat) },
  };
}

function polygonFeature(id: string, points: [number, number][], properties: MapGeoJsonFeature["properties"] = {}): MapGeoJsonFeature | null {
  if (points.length < 3) return null;
  const closed = [...points, points[0]].map(toLngLat);
  return {
    type: "Feature",
    properties: { id, ...properties },
    geometry: { type: "Polygon", coordinates: [closed] },
  };
}

function collection(features: Array<MapGeoJsonFeature | null | undefined>): MapGeoJsonCollection {
  return {
    type: "FeatureCollection",
    features: features.filter((feature): feature is MapGeoJsonFeature => Boolean(feature)),
  };
}

function missionOverlayCollections(missions: SavedMission[]) {
  const areaFeatures: MapGeoJsonFeature[] = [];
  const lineFeatures: MapGeoJsonFeature[] = [];
  const pointFeatures: MapGeoJsonFeature[] = [];

  missions.forEach((mission, missionIndex) => {
    const tint = missionIndex % 2 === 0 ? "#FF6600" : "#F59E0B";
    const polygon = polygonFeature(`${mission.id}-area`, mission.border_points, {
      tint,
      mission: mission.name,
      kind: "mission-area",
    });
    if (polygon) areaFeatures.push(polygon);
    const borderLinePoints = mission.border_points.length >= 3 ? [...mission.border_points, mission.border_points[0]] : mission.border_points;
    const borderLine = lineFeature(`${mission.id}-border`, borderLinePoints, {
      tint,
      kind: "mission-border",
    });
    if (borderLine) lineFeatures.push(borderLine);
    const waypointLine = lineFeature(`${mission.id}-path`, mission.waypoints, {
      tint: "#FF6600",
      kind: "waypoint-path",
    });
    if (waypointLine) lineFeatures.push(waypointLine);

    mission.border_points.forEach((point, pointIndex) => {
      pointFeatures.push(pointFeature(`${mission.id}-border-${pointIndex}`, point, {
        tint,
        kind: "border",
        role: "border",
      }));
    });
    mission.waypoints.forEach((point, pointIndex) => {
      const isStart = pointIndex === 0;
      const isLand = mission.waypoints.length > 1 && pointIndex === mission.waypoints.length - 1;
      pointFeatures.push(pointFeature(`${mission.id}-waypoint-${pointIndex}`, point, {
        kind: "waypoint",
        role: isStart ? "start" : isLand ? "land" : "waypoint",
      }));
    });
  });

  return {
    areas: collection(areaFeatures),
    lines: collection(lineFeatures),
    points: collection(pointFeatures),
  };
}

function mapBoundsFromPoints(points: [number, number][]): LngLatBoundsLike | null {
  if (points.length === 0) return null;
  const bounds = pointsBounds(points);
  return [
    [wrapMapLongitude(bounds.lon_min), wrapMapLatitude(bounds.lat_min)],
    [wrapMapLongitude(bounds.lon_max), wrapMapLatitude(bounds.lat_max)],
  ];
}

function missionBoundsForMap(missions: SavedMission[]): LngLatBoundsLike | null {
  const points = missions.flatMap((mission) => [...mission.border_points, ...mission.waypoints]);
  return mapBoundsFromPoints(points);
}

function mapLibreSource(map: MapLibreMap, sourceId: string): GeoJSONSource | null {
  const source = map.getSource(sourceId);
  return source && "setData" in source ? (source as GeoJSONSource) : null;
}

export function Dashboard() {
  const { profile, devices, regions, activeDeviceId, addRegion } = useAppStore();
  const { rightDockOpen, resetRightDock, pushRightDock, mapSearchTarget, selectedMissionId, setSelectedMissionId, setLivePosition, setMapSearchTarget } = useShellStore();
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const readyMaps = regions.filter((region) => region.last_downloaded);
  const [selectedMap, setSelectedMap] = useState<MapSelectionValue>("world");
  const selectedRegion = selectedMap !== "world" ? regions.find((region) => region.id === selectedMap) : undefined;
  const activeMap = selectedRegion;
  const [position, setPosition] = useState<DronePositionUpdate | null>(null);
  const [telemetryMessage, setTelemetryMessage] = useState("listening");
  const [mapApi, setMapApi] = useState<MapApi | null>(null);
  const [toolMode, setToolMode] = useState<MapToolMode>("idle");
  const [borderPoints, setBorderPoints] = useState<[number, number][]>([]);
  const [borderCutShape, setBorderCutShape] = useState<CutShape>("polygon");
  const [waypoints, setWaypoints] = useState<[number, number][]>([]);
  const [drawingMessage, setDrawingMessage] = useState<string | null>(null);
  const [cutEstimate, setCutEstimate] = useState<MapUsageEstimate | null>(null);
  const [cutEstimateLoading, setCutEstimateLoading] = useState(false);
  const [cutEstimateError, setCutEstimateError] = useState<string | null>(null);
  const [cutDownloadZoom, setCutDownloadZoom] = useState(18);
  const [multiLayerMap, setMultiLayerMap] = useState(false);
  const [downloadProviders, setDownloadProviders] = useState<MapProvider[]>([]);
  const [selectedProviderIds, setSelectedProviderIds] = useState<MapProviderId[]>([]);
  const [cutLimitOverride, setCutLimitOverride] = useState(false);
  const [cutInstalling, setCutInstalling] = useState(false);
  const [cutInstallProgress, setCutInstallProgress] = useState<CutInstallProgress | null>(null);
  const [defaultOutputRoot, setDefaultOutputRoot] = useState("");
  const [savedMissions, setSavedMissions] = useState<SavedMission[]>(() => loadSavedMissions());
  const [missionMode, setMissionMode] = useState(false);
  const [selectedMissionIds, setSelectedMissionIds] = useState<string[]>([]);
  const [editingMissionId, setEditingMissionId] = useState<string | null>(null);
  const positionPort = Number(localStorage.getItem("vision_nav_position_udp_port") || 17660);
  const currentPosition = positionLatLon(position);
  const searchedPosition: [number, number] | null = mapSearchTarget ? [mapSearchTarget.lat, mapSearchTarget.lon] : null;
  const mapCenter = searchedPosition ?? (activeMap ? regionCenter(activeMap) : PLACEHOLDER_ORG_COUNTRY_CENTER);
  const mapPolygon = regionPolygon(activeMap);
  const positionState = positionTone(position);
  const showAircraftMarker = Boolean(activeDevice && currentPosition && (positionState === "ready" || positionState === "active"));
  const mapState = selectedMap === "world" ? "ready" : activeMap?.active_bundle_path || activeMap?.lifecycle_state === "active" ? "active" : readyMaps.length ? "ready" : "warning";
  const mapZoom = clampMapZoom(mapSearchTarget?.zoom ?? (activeMap ? 14 : PLACEHOLDER_ORG_COUNTRY_ZOOM));
  const [mapViewport, setMapViewport] = useState<MapViewport>({ center: normalizeMapCenter(mapCenter), zoom: mapZoom });
  const orderedBorderPoints = useMemo(
    () => borderCutShape === "polygon" ? orderPolygonPoints(borderPoints) : borderPoints,
    [borderCutShape, borderPoints],
  );
  const cutEstimateBounds = useMemo(
    () => orderedBorderPoints.length ? pointsBounds(orderedBorderPoints) : null,
    [orderedBorderPoints],
  );
  const cutEstimateReady = borderCutShape === "box" ? orderedBorderPoints.length === 4 : orderedBorderPoints.length >= 3;
  const cutEstimateZoom = Math.max(15, Math.min(MAP_MAX_ZOOM, Math.round(cutDownloadZoom)));
  const fallbackCutAreaKm2 = cutEstimateBounds
    ? borderCutShape === "polygon" ? polygonAreaKm2(orderedBorderPoints) : bboxAreaKm2(cutEstimateBounds)
    : 0;
  const cutPreviewPoint: [number, number] | null = cutEstimateBounds
    ? [
        (cutEstimateBounds.lat_min + cutEstimateBounds.lat_max) / 2,
        (cutEstimateBounds.lon_min + cutEstimateBounds.lon_max) / 2,
      ]
    : null;
  const cutPreviewUrl = satellitePreviewUrl(cutPreviewPoint, cutEstimateZoom);
  const apiKeys = useMemo(() => providerApiKeys(profile), [profile]);
  const readyDownloadProviders = useMemo(
    () => downloadProviders.filter((provider) => provider.kind !== "vector" && provider.enabled && Boolean(provider.url_template)),
    [downloadProviders],
  );
  const selectedReadyProviderIds = useMemo(
    () => selectedProviderIds.filter((id) => {
      const provider = readyDownloadProviders.find((item) => item.id === id);
      return provider && isDownloadProviderReady(provider, apiKeys);
    }),
    [apiKeys, readyDownloadProviders, selectedProviderIds],
  );
  const maxMapAreaKm2 = profile?.max_map_area_km2;
  const maxDownloadSizeGb = profile?.max_map_download_size_gb ?? 20;
  const mapLimitWarnings = useMemo(() => {
    if (!cutEstimate) return [];
    const warnings: string[] = [];
    if (typeof maxMapAreaKm2 === "number" && maxMapAreaKm2 > 0 && cutEstimate.area_km2 > maxMapAreaKm2) {
      warnings.push(`Map area ${cutEstimate.area_km2.toFixed(2)} km2 exceeds settings limit ${maxMapAreaKm2.toFixed(2)} km2.`);
    }
    if (typeof maxDownloadSizeGb === "number" && maxDownloadSizeGb > 0 && cutEstimate.estimated_disk_mb > maxDownloadSizeGb * 1024) {
      warnings.push(`Estimated disk size ${formatMegabytes(cutEstimate.estimated_disk_mb)} exceeds settings limit ${formatMegabytes(maxDownloadSizeGb * 1024)}.`);
    }
    return warnings;
  }, [cutEstimate, maxDownloadSizeGb, maxMapAreaKm2]);
  const mapLimitExceeded = mapLimitWarnings.length > 0;
  const hudLeftClass = rightDockOpen ? "left-[444px]" : "left-20";
  const missionsForActiveMap = useMemo(
    () => activeMap ? savedMissions.filter((mission) => mission.map_id === activeMap.id) : [],
    [activeMap, savedMissions],
  );
  const editingMission = editingMissionId ? savedMissions.find((mission) => mission.id === editingMissionId) : undefined;
  const canSaveMission = Boolean(activeMap) && (borderPoints.length >= 3 || waypoints.length > 0);
  const selectedMissionIdSet = useMemo(() => new Set(selectedMissionIds), [selectedMissionIds]);
  const activeMissionOverlays = missionMode && activeMap
    ? missionsForActiveMap.filter((mission) => selectedMissionIdSet.has(mission.id))
    : [];

  useEffect(() => {
    const refreshMissions = () => setSavedMissions(loadSavedMissions());
    window.addEventListener(SAVED_MISSIONS_CHANGED, refreshMissions);
    window.addEventListener("storage", refreshMissions);
    return () => {
      window.removeEventListener(SAVED_MISSIONS_CHANGED, refreshMissions);
      window.removeEventListener("storage", refreshMissions);
    };
  }, []);

  useEffect(() => {
    if (!activeMap || !selectedMissionId || selectedMissionIds.length > 0) return;
    if (missionsForActiveMap.some((mission) => mission.id === selectedMissionId)) {
      setSelectedMissionIds([selectedMissionId]);
    }
  }, [activeMap, missionsForActiveMap, selectedMissionId, selectedMissionIds.length]);

  useEffect(() => {
    setSelectedMissionIds((current) => {
      const next = current.filter((id) => missionsForActiveMap.some((mission) => mission.id === id));
      return next.length === current.length ? current : next;
    });
    if (editingMissionId && !savedMissions.some((mission) => mission.id === editingMissionId)) {
      setEditingMissionId(null);
    }
    if (missionMode && (!activeMap || missionsForActiveMap.length === 0)) {
      setMissionMode(false);
    }
  }, [activeMap, editingMissionId, missionMode, missionsForActiveMap, savedMissions]);

  useEffect(() => {
    if (selectedMap !== "world" && !regions.some((region) => region.id === selectedMap)) {
      setSelectedMap("world");
    }
  }, [regions, selectedMap]);

  useEffect(() => {
    let cancelled = false;
    async function loadDefaultOutputRoot() {
      try {
        const home = await homeDir();
        const root = await join(home, "DroneVisionNav", "maps");
        if (!cancelled) setDefaultOutputRoot(root);
      } catch {
        if (!cancelled) setDefaultOutputRoot("~/DroneVisionNav/maps");
      }
    }
    void loadDefaultOutputRoot();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    cmd.listMapProviders()
      .then((items) => setDownloadProviders(items.filter((provider) => provider.kind !== "vector")))
      .catch(() => setDownloadProviders([]));
  }, []);

  useEffect(() => {
    if (!readyDownloadProviders.length) return;
    setSelectedProviderIds((current) => {
      const validCurrent = current.filter((id) => {
        const provider = readyDownloadProviders.find((item) => item.id === id);
        return provider && isDownloadProviderReady(provider, apiKeys);
      });
      if (validCurrent.length) return validCurrent;
      const defaults = ["usgs-imagery", "esri-world-imagery"]
        .map((id) => readyDownloadProviders.find((provider) => provider.id === id && isDownloadProviderReady(provider, apiKeys))?.id)
        .filter((id): id is MapProviderId => Boolean(id));
      return defaults.length ? defaults : readyDownloadProviders.filter((provider) => isDownloadProviderReady(provider, apiKeys)).slice(0, 1).map((provider) => provider.id);
    });
  }, [apiKeys, readyDownloadProviders]);

  useEffect(() => {
    if (!mapLimitExceeded) setCutLimitOverride(false);
  }, [mapLimitExceeded]);

  useEffect(() => {
    if (!mapApi || mapSearchTarget) return;
    if (activeMap) {
      mapApi.fitPoints(regionPolygon(activeMap), 14);
      return;
    }
    if (selectedMap === "world") {
      mapApi.focus(PLACEHOLDER_ORG_COUNTRY_CENTER, PLACEHOLDER_ORG_COUNTRY_ZOOM);
    }
  }, [activeMap, mapApi, mapSearchTarget, selectedMap]);

  useEffect(() => {
    if (!cutEstimateReady || !cutEstimateBounds) {
      setCutEstimate(null);
      setCutEstimateError(null);
      setCutEstimateLoading(false);
      return;
    }
    if (!selectedReadyProviderIds.length) {
      setCutEstimate(null);
      setCutEstimateError(null);
      setCutEstimateLoading(false);
      return;
    }

    let cancelled = false;
    setCutEstimateLoading(true);
    setCutEstimateError(null);
    const zoomLevels = multiLayerMap
      ? Array.from({ length: cutEstimateZoom - 15 + 1 }, (_, index) => 15 + index)
      : [cutEstimateZoom];
    Promise.all(zoomLevels.map((zoomLevel) => cmd.estimateMapUsage({
        bbox: cutEstimateBounds,
        zoom: zoomLevel,
        cut_shape: borderCutShape,
        polygon_points: borderCutShape === "polygon" ? orderedBorderPoints : undefined,
        provider_ids: selectedReadyProviderIds,
        api_keys: apiKeys,
      })))
      .then((estimate) => {
        if (cancelled) return;
        setCutEstimate(multiLayerMap ? combineLayeredEstimates(estimate) : sumSelectedProviderTotals(estimate[0]));
      })
      .catch((error) => {
        if (!cancelled) {
          setCutEstimate(null);
          setCutEstimateError(String(error));
        }
      })
      .finally(() => {
        if (!cancelled) setCutEstimateLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiKeys, borderCutShape, cutEstimateBounds, cutEstimateReady, cutEstimateZoom, multiLayerMap, orderedBorderPoints, selectedReadyProviderIds]);

  const openMapsPanel = () => {
    resetRightDock();
    pushRightDock("maps");
  };

  const saveBorderCut = async () => {
    if (cutInstalling) return;
    if (!cutEstimateBounds || !cutEstimateReady) return;
    if (!selectedReadyProviderIds.length) {
      setCutInstallProgress({
        label: "Select at least one ready provider before installing the cut.",
        percent: 0,
        tone: "error",
      });
      return;
    }
    if (cutEstimate?.over_100_km2) {
      const confirmed = window.confirm(`Selected map cut is ${cutEstimate.area_km2.toFixed(1)} km2. Continue installing this large map?`);
      if (!confirmed) return;
    }
    const id = `manual-border-${Date.now()}`;
    const savedPoints = borderCutShape === "polygon" ? orderPolygonPoints(borderPoints) : borderPoints;
    const bounds = pointsBounds(savedPoints);
    const name = `Map cut ${new Date().toLocaleString()}`;
    const outputDir = `${defaultOutputRoot || "~/DroneVisionNav/maps"}/${slugifyPathSegment(name)}-${id.replace(/^manual-border-/, "")}`;
    const nextRegion: Region = {
      id,
      name,
      ...bounds,
      zoom: cutEstimateZoom,
      source: "folder",
      output_path: outputDir,
      cut_shape: borderCutShape,
      polygon_points: savedPoints,
      lifecycle_state: "local",
      tile_count: cutEstimate?.tile_count ?? savedPoints.length,
      file_size_mb: cutEstimate?.estimated_disk_mb,
      gsd_m_per_px: cutEstimate?.gsd_m_per_px,
      multi_layer_map: multiLayerMap,
      min_zoom: multiLayerMap ? 15 : cutEstimateZoom,
      zoom_levels: multiLayerMap
        ? Array.from({ length: cutEstimateZoom - 15 + 1 }, (_, index) => 15 + index)
        : [cutEstimateZoom],
      location_label: borderCutShape === "box" ? "Manual box cut" : "Manual polygon cut",
    };
    setCutInstalling(true);
    setCutInstallProgress({
      label: "Preparing map install...",
      percent: 4,
      tone: "active",
    });
    let unlisten: (() => void) | null = null;
    try {
      unlisten = await listen<DownloadProgress>("tile-progress", (event) => {
        setCutInstallProgress({
          label: "Installing map tiles...",
          percent: Math.max(4, Math.min(98, event.payload.percent)),
          current: event.payload.current,
          total: event.payload.total,
          tone: "active",
        });
      });
      const result = await cmd.downloadMapRegion({
        bbox: bounds,
        zoom: cutEstimateZoom,
        min_zoom: multiLayerMap ? 15 : cutEstimateZoom,
        multi_layer_map: multiLayerMap,
        output_dir: outputDir,
        cut_shape: borderCutShape,
        polygon_points: borderCutShape === "polygon" ? savedPoints : undefined,
        provider_ids: selectedReadyProviderIds,
        api_keys: apiKeys,
        confirm_over_100_km2: true,
        allow_large_tile_count: cutLimitOverride,
      });
      const installedRegion: Region = {
        ...nextRegion,
        last_downloaded: new Date().toISOString(),
        tile_count: result.tile_count,
        file_size_mb: result.actual_mb ?? cutEstimate?.estimated_disk_mb,
        gsd_m_per_px: result.gsd_m_per_px,
        georef_source: result.georef_source,
        georef_confidence: result.georef_confidence,
        georef_crs: result.georef_crs,
      };
      const nextRegions = [...regions, installedRegion];
      addRegion(installedRegion);
      await cmd.saveRegions(nextRegions);
      setSelectedMap(installedRegion.id);
      setMapSearchTarget(null);
      mapApi?.fitPoints(regionPolygon(installedRegion), 14);
      setBorderPoints([]);
      setToolMode("idle");
      setDrawingMessage(null);
      setCutInstallProgress({
        label: "Map cut installed and saved.",
        percent: 100,
        current: result.tile_count,
        total: result.tile_count,
        tone: "success",
      });
    } catch (error) {
      console.warn("Failed to install manual border cut", error);
      setCutInstallProgress({
        label: `Map install failed: ${String(error)}`,
        percent: 100,
        tone: "error",
      });
    } finally {
      unlisten?.();
      setCutInstalling(false);
    }
  };

  const saveMission = () => {
    if (!activeMap) {
      setDrawingMessage("Select or install a saved map before saving a mission.");
      openMapsPanel();
      return;
    }
    if (!canSaveMission) return;
    const now = new Date().toISOString();
    const existing = editingMissionId ? savedMissions.find((mission) => mission.id === editingMissionId) : undefined;
    const bounds = missionBoundsFromParts(borderPoints, waypoints);
    const defaultName = existing?.name ?? `Mission ${new Date().toLocaleString()}`;
    const name = window.prompt("Mission name", defaultName)?.trim();
    if (!name) return;
    const center: [number, number] = bounds
      ? [(bounds.lat_min + bounds.lat_max) / 2, (bounds.lon_min + bounds.lon_max) / 2]
      : mapViewport.center;
    const mission: SavedMission = {
      id: existing?.id ?? createSavedMissionId(),
      name,
      created_at: existing?.created_at ?? now,
      updated_at: now,
      source: existing?.source ?? "dashboard",
      map_id: activeMap.id,
      map_label: activeMap.name,
      center,
      zoom: clampMapZoom(mapViewport.zoom),
      border_points: borderCutShape === "polygon" ? orderPolygonPoints(borderPoints) : borderPoints,
      waypoints,
      bounds,
    };

    upsertSavedMission(mission);
    setSavedMissions((current) => [mission, ...current.filter((item) => item.id !== mission.id)]);
    setSelectedMissionId(mission.id);
    setSelectedMissionIds([mission.id]);
    setMissionMode(true);
    setEditingMissionId(null);
    setToolMode("idle");
    setDrawingMessage(null);
    resetRightDock();
    pushRightDock("missions");
  };

  const clearMapTool = () => {
    if (cutInstalling) return;
    setToolMode("idle");
    setBorderCutShape("polygon");
    setBorderPoints([]);
    setWaypoints([]);
    setDrawingMessage(null);
    setCutInstallProgress(null);
    setEditingMissionId(null);
  };

  const handleToolModeChange = (mode: MapToolMode) => {
    if (cutInstalling) return;
    if (mode === "waypoint" && !activeMap) {
      setDrawingMessage("Select or install a saved map before plotting mission waypoints.");
      openMapsPanel();
      return;
    }
    setCutInstallProgress(null);
    setDrawingMessage(null);
    setToolMode(mode);
    if (mode === "box") {
      setBorderCutShape("box");
      setBorderPoints([]);
    } else if (mode === "polygon") {
      setBorderCutShape("polygon");
      setBorderPoints([]);
    }
  };

  const startMissionDraft = () => {
    if (!activeMap) {
      setDrawingMessage("Select or install a saved map before creating a mission.");
      openMapsPanel();
      return;
    }
    if (editingMissionId) {
      setMissionMode(false);
      setToolMode("waypoint");
      setDrawingMessage(`Editing ${editingMission?.name ?? "mission"}. Add waypoints or select a different map, then Save Mission.`);
      return;
    }
    setMissionMode(false);
    setEditingMissionId(null);
    setBorderCutShape("polygon");
    setBorderPoints([]);
    setWaypoints([]);
    setToolMode("waypoint");
    setSelectedMissionIds([]);
    setSelectedMissionId(null);
    setDrawingMessage(`Creating mission for ${activeMap.name}. Click the map to add waypoints, then Save Mission.`);
    mapApi?.fitPoints(regionPolygon(activeMap), 14);
  };

  const editMission = (missionId: string) => {
    const mission = savedMissions.find((item) => item.id === missionId);
    if (!mission) return;
    const missionMap = mission.map_id ? regions.find((region) => region.id === mission.map_id) : undefined;
    if (missionMap && selectedMap !== missionMap.id) {
      setSelectedMap(missionMap.id);
      setMapSearchTarget(null);
      mapApi?.fitPoints(regionPolygon(missionMap), 14);
    }
    setMissionMode(false);
    setEditingMissionId(mission.id);
    setSelectedMissionIds([mission.id]);
    setSelectedMissionId(mission.id);
    setBorderCutShape("polygon");
    setBorderPoints(mission.border_points ?? []);
    setWaypoints(mission.waypoints ?? []);
    setToolMode("waypoint");
    setDrawingMessage(`Editing ${mission.name}. Add waypoints or select a different map, then Save Mission.`);
  };

  const undoWaypoint = () => {
    setWaypoints((current) => current.slice(0, -1));
  };

  const toggleMissionMode = () => {
    if (missionMode) {
      setMissionMode(false);
      return;
    }
    if (!activeMap) {
      setMissionMode(false);
      setDrawingMessage("Create, import, or select a saved map before Mission Mode.");
      openMapsPanel();
      return;
    }
    if (missionsForActiveMap.length === 0) {
      startMissionDraft();
      return;
    }
    setToolMode("idle");
    setBorderCutShape("polygon");
    setBorderPoints([]);
    setWaypoints([]);
    setDrawingMessage(null);
    setMissionMode(true);
    const currentValid = selectedMissionIds.find((id) => missionsForActiveMap.some((mission) => mission.id === id));
    const nextMissionId = currentValid ?? missionsForActiveMap[0]?.id;
    if (nextMissionId) {
      setSelectedMissionIds([nextMissionId]);
      setSelectedMissionId(nextMissionId);
    }
  };

  const toggleMissionSelection = (missionId: string) => {
    if (!missionsForActiveMap.some((mission) => mission.id === missionId)) return;
    setSelectedMissionIds((current) => {
      const next = current.includes(missionId)
        ? current.filter((id) => id !== missionId)
        : [...current, missionId];
      setSelectedMissionId(next[next.length - 1] ?? null);
      return next;
    });
  };

  const handleDrawBox = useCallback((points: [number, number][]) => {
    setBorderCutShape("box");
    setBorderPoints(points);
    setDrawingMessage(null);
  }, []);

  const handleDrawPoint = useCallback((point: [number, number]) => {
    if (toolMode === "polygon") {
      setBorderCutShape("polygon");
      setBorderPoints((current) => {
        setDrawingMessage(null);
        return orderPolygonPoints([...current, point]);
      });
    }
    if (toolMode === "waypoint") {
      setDrawingMessage(null);
      setWaypoints((current) => [...current, point]);
    }
  }, [toolMode]);

  const toggleDownloadProvider = useCallback((providerId: MapProviderId) => {
    setSelectedProviderIds((current) => (
      current.includes(providerId)
        ? current.filter((id) => id !== providerId)
        : [...current, providerId]
    ));
    setCutLimitOverride(false);
  }, []);

  const handleMapSelectionChange = useCallback((id: MapSelectionValue) => {
    setMapSearchTarget(null);
    setSelectedMap(id);
    setMissionMode(false);
    setSelectedMissionIds([]);
    setSelectedMissionId(null);
    const region = id !== "world" ? regions.find((item) => item.id === id) : undefined;
    if (region) {
      setDrawingMessage(editingMissionId ? `Editing mission map changed to ${region.name}. Save Mission to update.` : null);
      mapApi?.fitPoints(regionPolygon(region), 14);
    } else if (id === "world") {
      setDrawingMessage(editingMissionId ? "Select a saved map before saving this mission edit." : null);
      mapApi?.focus(PLACEHOLDER_ORG_COUNTRY_CENTER, PLACEHOLDER_ORG_COUNTRY_ZOOM);
    }
  }, [editingMissionId, mapApi, regions, setMapSearchTarget, setSelectedMissionId]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      try {
        let packet = await cmd.receivePositionUpdate(positionPort, 300);
        if (!packet && activeDevice?.host && activeDevice.mavlink_endpoint) {
          const edgePacket = await cmd.edgeApiMavlinkPosition(
            `http://${activeDevice.host}:5000`,
            activeDevice.mavlink_endpoint,
            1.2,
            activeDevice.autopilot,
          );
          if (edgePacket.ok && positionLatLon(edgePacket)) {
            packet = edgePacket;
          } else if (!cancelled) {
            setTelemetryMessage(edgePacket.message ?? "MAVLink position waiting");
          }
        }
        if (cancelled) return;
        if (packet) {
          setPosition(packet);
          setLivePosition(packet);
          const mavlinkMessage = (packet as { mavlink?: { message_type?: string } }).mavlink?.message_type ?? null;
          setTelemetryMessage(mavlinkMessage ? `MAVLink ${mavlinkMessage}` : `packet ${packet.sequence ?? "n/a"}`);
        } else {
          setTelemetryMessage("waiting");
        }
      } catch (error) {
        if (!cancelled) setTelemetryMessage(`unavailable: ${String(error)}`);
      } finally {
        if (!cancelled) timer = setTimeout(poll, 1600);
      }
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [activeDevice?.autopilot, activeDevice?.host, activeDevice?.mavlink_endpoint, positionPort, setLivePosition]);

  return (
    <div className="ops-screen-bg relative h-full min-h-[calc(100vh-127px)] overflow-hidden animate-fade-in">
      <div className="absolute inset-0">
        <OperatorMap
          center={normalizeMapCenter(mapCenter)}
          zoom={mapZoom}
          mapPolygon={mapPolygon}
          borderPoints={!missionMode ? orderedBorderPoints : []}
          waypoints={!missionMode ? waypoints : []}
          missionMode={missionMode}
          activeMissionOverlays={activeMissionOverlays}
          currentPosition={showAircraftMarker ? currentPosition : null}
          positionState={positionState}
          mode={toolMode}
          setMapApi={setMapApi}
          onViewportChange={setMapViewport}
          onDrawBox={handleDrawBox}
          onDrawPoint={handleDrawPoint}
        />
      </div>

      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-bg-base/45 via-transparent to-bg-base/50" />

      <MapRightControls
        regions={regions}
        activeMap={activeMap}
        selectedMap={selectedMap}
        onMapChange={handleMapSelectionChange}
        onOpenMapsPanel={openMapsPanel}
        mode={toolMode}
        cutShape={borderCutShape}
        borderPointCount={borderPoints.length}
        waypointCount={waypoints.length}
        drawingMessage={drawingMessage}
        onModeChange={handleToolModeChange}
        onSaveBorder={saveBorderCut}
        onSaveMission={saveMission}
        onClearDrawing={clearMapTool}
        canSaveMission={canSaveMission}
        missionDraftActive={toolMode === "waypoint" || Boolean(editingMissionId)}
        editingMissionName={editingMission?.name}
        onStartMissionDraft={startMissionDraft}
        onEditMission={editMission}
        onUndoWaypoint={undoWaypoint}
        cutEstimate={cutEstimate}
        cutEstimateLoading={cutEstimateLoading}
        cutEstimateError={cutEstimateError}
        cutEstimateZoom={cutEstimateZoom}
        onCutEstimateZoomChange={setCutDownloadZoom}
        cutInstalling={cutInstalling}
        cutInstallProgress={cutInstallProgress}
        multiLayerMap={multiLayerMap}
        onMultiLayerMapChange={setMultiLayerMap}
        providers={readyDownloadProviders}
        selectedProviderIds={selectedProviderIds}
        apiKeys={apiKeys}
        onToggleProvider={toggleDownloadProvider}
        fallbackCutAreaKm2={fallbackCutAreaKm2}
        cutPreviewUrl={cutPreviewUrl}
        mapLimitWarnings={mapLimitWarnings}
        mapLimitOverride={cutLimitOverride}
        onMapLimitOverrideChange={setCutLimitOverride}
        cutSaveBlocked={mapLimitExceeded && !cutLimitOverride}
        missions={missionsForActiveMap}
        missionMode={missionMode}
        missionModeDisabled={!activeMap || missionsForActiveMap.length === 0}
        missionCreateRequired={Boolean(activeMap && missionsForActiveMap.length === 0)}
        activeMapName={activeMap?.name}
        selectedMissionIds={selectedMissionIds}
        onMissionModeToggle={toggleMissionMode}
        onToggleMissionSelection={toggleMissionSelection}
        onSelectAllMissions={() => setSelectedMissionIds(missionsForActiveMap.map((mission) => mission.id))}
        onClearMissionSelection={() => setSelectedMissionIds([])}
      />
      <MapZoomControls
        onZoomIn={() => mapApi?.zoomIn()}
        onZoomOut={() => mapApi?.zoomOut()}
        onRecenter={() => mapApi?.recenter()}
        zoomReady={Boolean(mapApi)}
      />

      <section className={cn("pointer-events-none absolute top-3 z-[1100] w-[320px] rounded-lg border border-border bg-bg-base/92 p-3 shadow-2xl backdrop-blur-sm transition-[left] duration-200", hudLeftClass)}>
        <div className="flex items-center gap-2">
          <span className={cn("ops-led", activeDevice ? "ops-led-ready" : "ops-led-offline")} />
          <Activity size={13} className={activeDevice ? "text-status-ready" : "text-slate-500"} />
          <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">Connection</span>
          <span className="ml-auto truncate font-data-mono text-[10px] text-slate-400">{activeDevice?.name ?? "Offline"}</span>
        </div>
        <div className="mt-3 grid gap-2">
          <HudRow
            Icon={MapIcon}
            label="Active Map"
            value={selectedMap === "world" ? "World Map" : activeMap?.name ?? "No map selected"}
            detail={selectedMap === "world" ? "Global imagery base layer" : activeMap?.last_downloaded ? formatDate(activeMap.last_downloaded) : "Use Maps panel"}
            tone={mapState}
          />
          <HudRow
            Icon={Navigation}
            label="Position"
            value={positionLabel(position)}
            detail={currentPosition ? `${currentPosition[0].toFixed(6)}, ${currentPosition[1].toFixed(6)}` : `UDP ${positionPort} ${telemetryMessage}`}
            tone={positionState}
          />
        </div>
      </section>
    </div>
  );
}

type OperatorMapProps = {
  center: [number, number];
  zoom: number;
  mapPolygon: [number, number][];
  borderPoints: [number, number][];
  waypoints: [number, number][];
  missionMode: boolean;
  activeMissionOverlays: SavedMission[];
  currentPosition: [number, number] | null;
  positionState: string;
  mode: MapToolMode;
  setMapApi: (api: MapApi) => void;
  onViewportChange: (viewport: MapViewport) => void;
  onDrawBox: (points: [number, number][]) => void;
  onDrawPoint: (point: [number, number]) => void;
};

function OperatorMap(props: OperatorMapProps) {
  const [renderer, setRenderer] = useState<"leaflet" | "maplibre">(() => preferredMapRenderer());
  const [mapLibreFailed, setMapLibreFailed] = useState(false);

  if (renderer === "maplibre" && !mapLibreFailed) {
    return <OperatorMapLibreMap {...props} onRendererFailure={() => {
      setMapLibreFailed(true);
      setRenderer("leaflet");
    }} />;
  }

  return <OperatorLeafletMap {...props} />;
}

function OperatorLeafletMap({
  center,
  zoom,
  mapPolygon,
  borderPoints,
  waypoints,
  missionMode,
  activeMissionOverlays,
  currentPosition,
  positionState,
  mode,
  setMapApi,
  onViewportChange,
  onDrawBox,
  onDrawPoint,
}: OperatorMapProps) {
  return (
    <MapContainer
      center={center}
      zoom={zoom}
      minZoom={MAP_MIN_ZOOM}
      maxZoom={MAP_MAX_ZOOM}
      worldCopyJump
      maxBoundsViscosity={1}
      className="mission-map h-full w-full"
      scrollWheelZoom
      attributionControl={false}
      zoomControl={false}
    >
      <LeafletViewportController center={center} zoom={zoom} setMapApi={setMapApi} onViewportChange={onViewportChange} />
      <LeafletDrawController mode={mode} onPoint={onDrawPoint} onBox={onDrawBox} />
      <TileLayer
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        maxZoom={MAP_MAX_ZOOM}
        updateWhenIdle
        updateWhenZooming={false}
        keepBuffer={2}
      />
      <Pane name="mission-labels" className="mission-map-label-pane" style={{ zIndex: 420 }}>
        <TileLayer
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
          maxZoom={MAP_MAX_ZOOM}
          opacity={0.92}
          updateWhenIdle
          updateWhenZooming={false}
          keepBuffer={1}
        />
      </Pane>
      {mapPolygon.length > 0 && (
        <Polygon positions={mapPolygon} pathOptions={{ color: "#FF6600", fillColor: "#FF6600", fillOpacity: 0.08, weight: 2 }} />
      )}
      {missionMode && activeMissionOverlays.map((mission, index) => (
        <LeafletMissionOverlay key={mission.id} mission={mission} index={index} />
      ))}
      {!missionMode && borderPoints.length > 0 && (
        <LeafletDrawnBorder borderPoints={borderPoints} />
      )}
      {!missionMode && waypoints.length > 0 && (
        <LeafletWaypointPath waypoints={waypoints} />
      )}
      {currentPosition && (
        <CircleMarker
          center={currentPosition}
          radius={9}
          pathOptions={{
            color: positionState === "ready" ? "#63D706" : positionState === "active" ? "#FF6600" : "#F59E0B",
            fillColor: positionState === "ready" ? "#2E8F49" : positionState === "active" ? "#B84A00" : "#9B6B16",
            fillOpacity: 0.95,
            weight: 3,
          }}
        />
      )}
    </MapContainer>
  );
}

function LeafletViewportController({
  center,
  zoom,
  setMapApi,
  onViewportChange,
}: {
  center: [number, number];
  zoom: number;
  setMapApi: (api: MapApi) => void;
  onViewportChange: (viewport: MapViewport) => void;
}) {
  const map = useMap();
  const centerLat = center[0];
  const centerLon = center[1];

  useEffect(() => {
    setMapApi({
      zoomIn: () => map.zoomIn(),
      zoomOut: () => {
        if (map.getZoom() > MAP_MIN_ZOOM) map.zoomOut();
      },
      recenter: () => map.setView(normalizeMapCenter(center), clampMapZoom(zoom), { animate: true }),
      focus: (targetCenter, targetZoom) => map.setView(normalizeMapCenter(targetCenter), clampMapZoom(targetZoom), { animate: true }),
      fitPoints: (points, maxZoom = 14) => {
        if (!points.length) return;
        map.fitBounds(L.latLngBounds(points.map(([lat, lon]) => L.latLng(lat, lon))), {
          animate: true,
          padding: [56, 56],
          maxZoom: clampMapZoom(maxZoom),
        });
      },
    });
  }, [centerLat, centerLon, map, setMapApi, zoom]);

  useEffect(() => {
    map.setMinZoom(MAP_MIN_ZOOM);
    map.setMaxBounds([[-WEB_MERCATOR_LAT_LIMIT, -180], [WEB_MERCATOR_LAT_LIMIT, 180]]);
    if (map.getZoom() < MAP_MIN_ZOOM) map.setZoom(MAP_MIN_ZOOM, { animate: false });
  }, [map]);

  useEffect(() => {
    const updateViewport = () => {
      const current = map.getCenter();
      onViewportChange({
        center: normalizeMapCenter([current.lat, current.lng]),
        zoom: clampMapZoom(map.getZoom()),
      });
    };

    map.on("moveend", updateViewport);
    map.on("zoomend", updateViewport);
    updateViewport();
    return () => {
      map.off("moveend", updateViewport);
      map.off("zoomend", updateViewport);
    };
  }, [map, onViewportChange]);

  useEffect(() => {
    map.setView(normalizeMapCenter(center), clampMapZoom(zoom), { animate: true });
  }, [centerLat, centerLon, map, zoom]);

  return null;
}

function LeafletDrawController({
  mode,
  onPoint,
  onBox,
}: {
  mode: MapToolMode;
  onPoint: (point: [number, number]) => void;
  onBox: (points: [number, number][]) => void;
}) {
  const boxStartRef = useRef<L.LatLng | null>(null);
  const boxPreviewRef = useRef<L.Rectangle | null>(null);
  const boxDragCleanupRef = useRef<(() => void) | null>(null);

  const clearBoxPreview = (map: L.Map) => {
    if (boxPreviewRef.current) {
      map.removeLayer(boxPreviewRef.current);
      boxPreviewRef.current = null;
    }
  };

  const map = useMapEvents({
    click(event) {
      if (mode === "idle" || mode === "box") return;
      onPoint([event.latlng.lat, event.latlng.lng]);
    },
    mousedown(event) {
      if (mode !== "box") return;
      L.DomEvent.stop(event.originalEvent);
      boxDragCleanupRef.current?.();
      boxStartRef.current = event.latlng;
      map.dragging.disable();
      const updatePreview = (nativeEvent: MouseEvent) => {
        if (!boxStartRef.current) return;
        const latLng = map.mouseEventToLatLng(nativeEvent);
        clearBoxPreview(map);
        boxPreviewRef.current = L.rectangle(
          [
            [boxStartRef.current.lat, boxStartRef.current.lng],
            [latLng.lat, latLng.lng],
          ],
          { color: "#FF6600", fillColor: "#FF6600", fillOpacity: 0.1, dashArray: "8 8", weight: 2 },
        ).addTo(map);
      };
      const finishDrag = (nativeEvent: MouseEvent) => {
        const start = boxStartRef.current;
        boxStartRef.current = null;
        document.removeEventListener("mousemove", updatePreview);
        document.removeEventListener("mouseup", finishDrag);
        boxDragCleanupRef.current = null;
        map.dragging.enable();
        clearBoxPreview(map);
        if (!start) return;
        const end = map.mouseEventToLatLng(nativeEvent);
        if (Math.abs(start.lat - end.lat) < 1e-7 || Math.abs(start.lng - end.lng) < 1e-7) return;
        onBox(rectangleFromCorners([start.lat, start.lng], [end.lat, end.lng]));
      };
      document.addEventListener("mousemove", updatePreview);
      document.addEventListener("mouseup", finishDrag);
      boxDragCleanupRef.current = () => {
        document.removeEventListener("mousemove", updatePreview);
        document.removeEventListener("mouseup", finishDrag);
        map.dragging.enable();
      };
    },
  });
  useEffect(() => {
    const container = map.getContainer();
    container.style.cursor = mode === "idle" ? "" : "crosshair";
    return () => {
      container.style.cursor = "";
      boxDragCleanupRef.current?.();
      boxDragCleanupRef.current = null;
      map.dragging.enable();
      clearBoxPreview(map);
      boxStartRef.current = null;
    };
  }, [map, mode]);
  return null;
}

function LeafletDrawnBorder({ borderPoints }: { borderPoints: [number, number][] }) {
  return (
    <>
      {borderPoints.length >= 3 ? (
        <Polygon
          positions={borderPoints}
          pathOptions={{ color: "#FF6600", fillColor: "#FF6600", fillOpacity: 0.1, dashArray: "8 8", weight: 2 }}
        />
      ) : (
        <Polyline positions={borderPoints} pathOptions={{ color: "#FF6600", dashArray: "8 8", weight: 2 }} />
      )}
      {borderPoints.map((point, index) => (
        <CircleMarker
          key={`border-${index}-${point[0]}-${point[1]}`}
          center={point}
          radius={5}
          pathOptions={{ color: "#FF6600", fillColor: "#0B0C0D", fillOpacity: 0.95, weight: 2 }}
        >
          <Tooltip direction="top" offset={[0, -5]} opacity={0.9}>
            Cut {index + 1}
          </Tooltip>
        </CircleMarker>
      ))}
    </>
  );
}

function LeafletWaypointPath({ waypoints }: { waypoints: [number, number][] }) {
  return (
    <>
      {waypoints.length > 1 && (
        <Polyline positions={waypoints} pathOptions={{ color: "#FF6600", weight: 3, opacity: 0.9 }} />
      )}
      {waypoints.map((point, index) => {
        const isStart = index === 0;
        const isLand = waypoints.length > 1 && index === waypoints.length - 1;
        const color = isStart ? "#63D706" : isLand ? "#EF4444" : "#FF6600";
        const label = isStart ? "Start" : isLand ? "Land" : `WP ${index + 1}`;
        return (
          <CircleMarker
            key={`waypoint-${index}-${point[0]}-${point[1]}`}
            center={point}
            radius={7}
            pathOptions={{ color, fillColor: color, fillOpacity: 0.9, weight: 3 }}
          >
            <Tooltip direction="top" offset={[0, -7]} opacity={0.95} permanent>
              {label}
            </Tooltip>
          </CircleMarker>
        );
      })}
    </>
  );
}

function LeafletMissionOverlay({ mission, index }: { mission: SavedMission; index: number }) {
  const tint = index % 2 === 0 ? "#FF6600" : "#F59E0B";
  const hasBorder = mission.border_points.length >= 3;

  return (
    <>
      {mission.border_points.length > 0 && (
        <>
          {hasBorder ? (
            <Polygon
              positions={mission.border_points}
              pathOptions={{ color: tint, fillColor: tint, fillOpacity: 0.08, dashArray: "8 8", weight: 2 }}
            />
          ) : (
            <Polyline positions={mission.border_points} pathOptions={{ color: tint, dashArray: "8 8", weight: 2 }} />
          )}
          {mission.border_points.map((point, pointIndex) => (
            <CircleMarker
              key={`${mission.id}-border-${pointIndex}-${point[0]}-${point[1]}`}
              center={point}
              radius={5}
              pathOptions={{ color: tint, fillColor: "#0B0C0D", fillOpacity: 0.95, weight: 2 }}
            />
          ))}
        </>
      )}
      {mission.waypoints.length > 0 && (
        <LeafletWaypointPath waypoints={mission.waypoints} />
      )}
    </>
  );
}

function OperatorMapLibreMap({
  center,
  zoom,
  mapPolygon,
  borderPoints,
  waypoints,
  missionMode,
  activeMissionOverlays,
  currentPosition,
  positionState,
  mode,
  setMapApi,
  onViewportChange,
  onDrawBox,
  onDrawPoint,
  onRendererFailure,
}: OperatorMapProps & {
  onRendererFailure: () => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const centerKey = `${center[0].toFixed(6)}:${center[1].toFixed(6)}`;
  const missionKey = activeMissionOverlays.map((mission) => mission.id).join("|");

  const overlayData = useMemo(() => {
    const activeMap = collection([polygonFeature("active-map", mapPolygon, { kind: "active-map", tint: "#FF6600" })]);
    const drawAreas = collection([polygonFeature("draw-border-area", borderPoints, { kind: "draw-area", tint: "#FF6600" })]);
    const borderLinePoints = borderPoints.length >= 3 ? [...borderPoints, borderPoints[0]] : borderPoints;
    const drawLines = collection([
      lineFeature("draw-border-line", borderLinePoints, { kind: "draw-border", tint: "#FF6600" }),
      lineFeature("draw-waypoint-line", waypoints, { kind: "draw-waypoint", tint: "#FF6600" }),
    ]);
    const drawPoints = collection([
      ...borderPoints.map((point, index) => pointFeature(`draw-border-${index}`, point, { kind: "border", role: "border" })),
      ...waypoints.map((point, index) => {
        const isStart = index === 0;
        const isLand = waypoints.length > 1 && index === waypoints.length - 1;
        return pointFeature(`draw-waypoint-${index}`, point, {
          kind: "waypoint",
          role: isStart ? "start" : isLand ? "land" : "waypoint",
        });
      }),
    ]);
    const missions = missionOverlayCollections(activeMissionOverlays);
    const aircraft = currentPosition
      ? collection([pointFeature("aircraft", currentPosition, { kind: "aircraft", role: "aircraft", state: positionState })])
      : EMPTY_GEOJSON;

    return { activeMap, drawAreas, drawLines, drawPoints, missions, aircraft };
  }, [activeMissionOverlays, borderPoints, currentPosition, mapPolygon, positionState, waypoints]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    let map: MapLibreMap | null = null;

    async function loadMapLibre() {
      try {
        const module = await import("maplibre-gl");
        if (cancelled || !containerRef.current) return;
        const maplibreApi = (module.default ?? module) as unknown as MapLibreRuntime;
        if (typeof maplibreApi.supported === "function" && !maplibreApi.supported()) {
          onRendererFailure();
          return;
        }
        map = new maplibreApi.Map({
          container: containerRef.current,
          style: SATELLITE_STYLE,
          center: toLngLat(center),
          zoom: clampMapZoom(zoom),
          minZoom: MAP_MIN_ZOOM,
          maxZoom: MAP_MAX_ZOOM,
          attributionControl: false,
          renderWorldCopies: false,
          transformConstrain: constrainGlobeTransform,
          fadeDuration: 140,
          refreshExpiredTiles: false,
          maxTileCacheSize: 128,
          maxTileCacheZoomLevels: 4,
          cancelPendingTileRequestsWhileZooming: false,
          collectResourceTiming: false,
          pixelRatio: Math.min(window.devicePixelRatio || 1, 1.5),
          validateStyle: false,
          canvasContextAttributes: {
            antialias: false,
            powerPreference: "high-performance",
            preserveDrawingBuffer: false,
          },
        });
        mapRef.current = map;
        map.dragRotate.disable();
        map.touchZoomRotate.disableRotation();
        map.on("load", () => {
          if (!map) return;
          addOperatorMapLayers(map);
          setReady(true);
        });
        map.on("error", (event) => {
          const message = String(event.error?.message ?? "");
          if (message.toLowerCase().includes("webgl") || message.toLowerCase().includes("context")) {
            console.warn("MapLibre runtime error; falling back to Leaflet", event.error);
            onRendererFailure();
          }
        });

        const updateViewport = () => {
          if (!map) return;
          const current = map.getCenter();
          onViewportChange({
            center: normalizeMapCenter([current.lat, current.lng]),
            zoom: clampMapZoom(map.getZoom()),
          });
        };
        map.on("moveend", updateViewport);
        map.on("zoomend", updateViewport);
      } catch (error) {
        console.warn("MapLibre initialization failed; falling back to Leaflet", error);
        onRendererFailure();
      }
    }

    void loadMapLibre();
    return () => {
      cancelled = true;
      map?.remove();
      mapRef.current = null;
      setReady(false);
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    setMapApi({
      zoomIn: () => map.zoomIn({ duration: 180 }),
      zoomOut: () => {
        if (map.getZoom() > MAP_MIN_ZOOM) map.zoomOut({ duration: 180 });
      },
      recenter: () => map.easeTo({ center: toLngLat(center), zoom: clampMapZoom(zoom), duration: 220 }),
      focus: (targetCenter, targetZoom) => map.easeTo({ center: toLngLat(normalizeMapCenter(targetCenter)), zoom: clampMapZoom(targetZoom), duration: 260 }),
      fitPoints: (points, maxZoom = 14) => {
        const bounds = mapBoundsFromPoints(points);
        if (!bounds) return;
        map.fitBounds(bounds, { padding: 72, maxZoom: clampMapZoom(maxZoom), duration: 260 });
      },
    });
  }, [centerKey, setMapApi, zoom]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.jumpTo({ center: toLngLat(center), zoom: clampMapZoom(zoom) });
  }, [centerKey, ready, zoom]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !missionMode || activeMissionOverlays.length === 0) return;
    const bounds = missionBoundsForMap(activeMissionOverlays);
    if (!bounds) return;
    map.fitBounds(bounds, { padding: 72, maxZoom: 16, duration: 240 });
  }, [missionKey, missionMode, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    setMapSourceData(map, "active-map", overlayData.activeMap);
    setMapSourceData(map, "draw-areas", overlayData.drawAreas);
    setMapSourceData(map, "draw-lines", overlayData.drawLines);
    setMapSourceData(map, "draw-points", overlayData.drawPoints);
    setMapSourceData(map, "mission-areas", overlayData.missions.areas);
    setMapSourceData(map, "mission-lines", overlayData.missions.lines);
    setMapSourceData(map, "mission-points", overlayData.missions.points);
    setMapSourceData(map, "aircraft", overlayData.aircraft);
  }, [overlayData, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let boxStart: [number, number] | null = null;
    let stopBoxDrag: (() => void) | null = null;

    const setBoxPreview = (points: [number, number][] | null) => {
      if (!map) return;
      const linePoints = points && points.length >= 3 ? [...points, points[0]] : points;
      setMapSourceData(
        map,
        "box-preview-area",
        points ? collection([polygonFeature("box-preview", points, { kind: "draw-area", tint: "#FF6600" })]) : EMPTY_GEOJSON,
      );
      setMapSourceData(
        map,
        "box-preview-line",
        points ? collection([lineFeature("box-preview-line", linePoints ?? [], { kind: "draw-border", tint: "#FF6600" })]) : EMPTY_GEOJSON,
      );
    };
    const pointFromMouseEvent = (event: MouseEvent): [number, number] => {
      const rect = map.getCanvas().getBoundingClientRect();
      const lngLat = map.unproject([event.clientX - rect.left, event.clientY - rect.top]);
      return [wrapMapLatitude(lngLat.lat), wrapMapLongitude(lngLat.lng)];
    };
    const cleanupBoxDrag = () => {
      stopBoxDrag?.();
      stopBoxDrag = null;
      boxStart = null;
      map.dragPan.enable();
      setBoxPreview(null);
    };

    const clickHandler = (event: MapMouseEvent) => {
      if (mode === "idle" || mode === "box") return;
      onDrawPoint([wrapMapLatitude(event.lngLat.lat), wrapMapLongitude(event.lngLat.lng)]);
    };
    const mouseDownHandler = (event: MapMouseEvent) => {
      if (mode !== "box") return;
      event.preventDefault();
      event.originalEvent.stopPropagation();
      cleanupBoxDrag();
      boxStart = [wrapMapLatitude(event.lngLat.lat), wrapMapLongitude(event.lngLat.lng)];
      map.dragPan.disable();
      const mouseMoveHandler = (nativeEvent: MouseEvent) => {
        if (!boxStart) return;
        setBoxPreview(rectangleFromCorners(boxStart, pointFromMouseEvent(nativeEvent)));
      };
      const mouseUpHandler = (nativeEvent: MouseEvent) => {
        const start = boxStart;
        document.removeEventListener("mousemove", mouseMoveHandler);
        document.removeEventListener("mouseup", mouseUpHandler);
        stopBoxDrag = null;
        boxStart = null;
        map.dragPan.enable();
        setBoxPreview(null);
        if (!start) return;
        const end = pointFromMouseEvent(nativeEvent);
        if (Math.abs(start[0] - end[0]) < 1e-7 || Math.abs(start[1] - end[1]) < 1e-7) return;
        onDrawBox(rectangleFromCorners(start, end));
      };
      document.addEventListener("mousemove", mouseMoveHandler);
      document.addEventListener("mouseup", mouseUpHandler);
      stopBoxDrag = () => {
        document.removeEventListener("mousemove", mouseMoveHandler);
        document.removeEventListener("mouseup", mouseUpHandler);
      };
    };
    map.on("click", clickHandler);
    map.on("mousedown", mouseDownHandler);
    map.getCanvas().style.cursor = mode === "idle" ? "" : "crosshair";
    return () => {
      map.off("click", clickHandler);
      map.off("mousedown", mouseDownHandler);
      map.getCanvas().style.cursor = "";
      cleanupBoxDrag();
    };
  }, [mode, onDrawBox, onDrawPoint]);

  return <div ref={containerRef} className="mission-map h-full w-full" />;
}

function setMapSourceData(map: MapLibreMap, sourceId: string, data: MapGeoJsonCollection) {
  mapLibreSource(map, sourceId)?.setData(data);
}

function addOperatorMapLayers(map: MapLibreMap) {
  const sourceIds = ["active-map", "draw-areas", "draw-lines", "draw-points", "box-preview-area", "box-preview-line", "mission-areas", "mission-lines", "mission-points", "aircraft"];
  sourceIds.forEach((sourceId) => {
    if (!map.getSource(sourceId)) {
      map.addSource(sourceId, { type: "geojson", data: EMPTY_GEOJSON });
    }
  });

  map.addLayer({
    id: "active-map-fill",
    type: "fill",
    source: "active-map",
    paint: { "fill-color": "#FF6600", "fill-opacity": 0.08 },
  });
  map.addLayer({
    id: "active-map-outline",
    type: "line",
    source: "active-map",
    paint: { "line-color": "#FF6600", "line-width": 2 },
  });
  map.addLayer({
    id: "draw-area-fill",
    type: "fill",
    source: "draw-areas",
    paint: { "fill-color": "#FF6600", "fill-opacity": 0.1 },
  });
  map.addLayer({
    id: "box-preview-fill",
    type: "fill",
    source: "box-preview-area",
    paint: { "fill-color": "#FF6600", "fill-opacity": 0.12 },
  });
  map.addLayer({
    id: "box-preview-line",
    type: "line",
    source: "box-preview-line",
    paint: {
      "line-color": "#FF6600",
      "line-width": 2,
      "line-opacity": 0.95,
      "line-dasharray": [2, 2],
    },
  });
  map.addLayer({
    id: "draw-lines",
    type: "line",
    source: "draw-lines",
    paint: {
      "line-color": ["coalesce", ["get", "tint"], "#FF6600"],
      "line-width": ["case", ["==", ["get", "kind"], "draw-waypoint"], 3, 2],
      "line-opacity": 0.95,
      "line-dasharray": ["case", ["==", ["get", "kind"], "draw-border"], ["literal", [2, 2]], ["literal", [1, 0]]],
    },
  });
  map.addLayer({
    id: "mission-area-fill",
    type: "fill",
    source: "mission-areas",
    paint: {
      "fill-color": ["coalesce", ["get", "tint"], "#FF6600"],
      "fill-opacity": 0.08,
    },
  });
  map.addLayer({
    id: "mission-lines",
    type: "line",
    source: "mission-lines",
    paint: {
      "line-color": ["coalesce", ["get", "tint"], "#FF6600"],
      "line-width": ["case", ["==", ["get", "kind"], "waypoint-path"], 3, 2],
      "line-opacity": 0.95,
      "line-dasharray": ["case", ["==", ["get", "kind"], "mission-border"], ["literal", [2, 2]], ["literal", [1, 0]]],
    },
  });
  map.addLayer({
    id: "draw-points",
    type: "circle",
    source: "draw-points",
    paint: waypointCirclePaint(),
  });
  map.addLayer({
    id: "mission-points",
    type: "circle",
    source: "mission-points",
    paint: waypointCirclePaint(),
  });
  map.addLayer({
    id: "aircraft-ring",
    type: "circle",
    source: "aircraft",
    paint: {
      "circle-radius": 14,
      "circle-color": "rgba(99, 215, 6, 0.16)",
      "circle-stroke-color": "#63D706",
      "circle-stroke-width": 1,
    },
  });
  map.addLayer({
    id: "aircraft-core",
    type: "circle",
    source: "aircraft",
    paint: {
      "circle-radius": 7,
      "circle-color": ["case", ["==", ["get", "state"], "ready"], "#2E8F49", ["==", ["get", "state"], "active"], "#B84A00", "#9B6B16"],
      "circle-stroke-color": ["case", ["==", ["get", "state"], "ready"], "#63D706", ["==", ["get", "state"], "active"], "#FF6600", "#F59E0B"],
      "circle-stroke-width": 3,
    },
  });
}

function waypointCirclePaint(): NonNullable<CircleLayerSpecification["paint"]> {
  return {
    "circle-radius": ["case", ["==", ["get", "kind"], "waypoint"], 7, 5],
    "circle-color": [
      "match",
      ["get", "role"],
      "start",
      "#63D706",
      "land",
      "#EF4444",
      "waypoint",
      "#FF6600",
      "#0B0C0D",
    ],
    "circle-opacity": 0.95,
    "circle-stroke-color": [
      "match",
      ["get", "role"],
      "start",
      "#63D706",
      "land",
      "#EF4444",
      "waypoint",
      "#FF6600",
      ["coalesce", ["get", "tint"], "#FF6600"],
    ],
    "circle-stroke-width": 2,
  } as unknown as NonNullable<CircleLayerSpecification["paint"]>;
}

function MapRightControls({
  regions,
  activeMap,
  selectedMap,
  onMapChange,
  onOpenMapsPanel,
  mode,
  cutShape,
  borderPointCount,
  waypointCount,
  drawingMessage,
  onModeChange,
  onSaveBorder,
  onSaveMission,
  onClearDrawing,
  canSaveMission,
  missionDraftActive,
  editingMissionName,
  onStartMissionDraft,
  onEditMission,
  onUndoWaypoint,
  cutEstimate,
  cutEstimateLoading,
  cutEstimateError,
  cutEstimateZoom,
  onCutEstimateZoomChange,
  cutInstalling,
  cutInstallProgress,
  multiLayerMap,
  onMultiLayerMapChange,
  providers,
  selectedProviderIds,
  apiKeys,
  onToggleProvider,
  fallbackCutAreaKm2,
  cutPreviewUrl,
  mapLimitWarnings,
  mapLimitOverride,
  onMapLimitOverrideChange,
  cutSaveBlocked,
  missions,
  missionMode,
  missionModeDisabled,
  missionCreateRequired,
  activeMapName,
  selectedMissionIds,
  onMissionModeToggle,
  onToggleMissionSelection,
  onSelectAllMissions,
  onClearMissionSelection,
}: {
  regions: Region[];
  activeMap?: Region;
  selectedMap: MapSelectionValue;
  onMapChange: (id: MapSelectionValue) => void;
  onOpenMapsPanel: () => void;
  mode: MapToolMode;
  cutShape: CutShape;
  borderPointCount: number;
  waypointCount: number;
  drawingMessage: string | null;
  onModeChange: (mode: MapToolMode) => void;
  onSaveBorder: () => void;
  onSaveMission: () => void;
  onClearDrawing: () => void;
  canSaveMission: boolean;
  missionDraftActive: boolean;
  editingMissionName?: string;
  onStartMissionDraft: () => void;
  onEditMission: (missionId: string) => void;
  onUndoWaypoint: () => void;
  cutEstimate: MapUsageEstimate | null;
  cutEstimateLoading: boolean;
  cutEstimateError: string | null;
  cutEstimateZoom: number;
  onCutEstimateZoomChange: (zoom: number) => void;
  cutInstalling: boolean;
  cutInstallProgress: CutInstallProgress | null;
  multiLayerMap: boolean;
  onMultiLayerMapChange: (enabled: boolean) => void;
  providers: MapProvider[];
  selectedProviderIds: MapProviderId[];
  apiKeys: Record<string, string>;
  onToggleProvider: (providerId: MapProviderId) => void;
  fallbackCutAreaKm2: number;
  cutPreviewUrl: string | null;
  mapLimitWarnings: string[];
  mapLimitOverride: boolean;
  onMapLimitOverrideChange: (enabled: boolean) => void;
  cutSaveBlocked: boolean;
  missions: SavedMission[];
  missionMode: boolean;
  missionModeDisabled: boolean;
  missionCreateRequired: boolean;
  activeMapName?: string;
  selectedMissionIds: string[];
  onMissionModeToggle: () => void;
  onToggleMissionSelection: (missionId: string) => void;
  onSelectAllMissions: () => void;
  onClearMissionSelection: () => void;
}) {
  const [mapMenuOpen, setMapMenuOpen] = useState(false);
  const [missionMenuOpen, setMissionMenuOpen] = useState(false);
  const [missionCreateOpen, setMissionCreateOpen] = useState(false);
  const [missionQuery, setMissionQuery] = useState("");
  const selectedLabel = selectedMap === "world" ? "World Map" : activeMap?.name ?? "Uploaded map missing";
  const drawingActive = mode !== "idle";
  const cutDrawingActive = mode === "box" || mode === "polygon";
  const selectedCount = selectedMissionIds.length;
  const missionButtonLabel = missionMode
    ? "Mission Mode"
    : missionModeDisabled
    ? missionCreateRequired
      ? "Mission Required"
      : "Select Map First"
    : "Mission Mode";
  const missionSelectorDisabled = missionModeDisabled || missionCreateRequired;
  const filteredMissions = missions.filter((mission) => {
    const normalized = missionQuery.trim().toLowerCase();
    return !normalized || `${mission.name} ${mission.map_label ?? ""}`.toLowerCase().includes(normalized);
  });

  useEffect(() => {
    if (drawingActive) setMissionMenuOpen(false);
  }, [drawingActive]);

  useEffect(() => {
    if (missionCreateRequired || missionDraftActive) setMissionCreateOpen(true);
  }, [missionCreateRequired, missionDraftActive]);

  return (
    <section className="absolute right-4 top-4 z-[1150] flex max-h-[calc(100%-2rem)] w-[264px] flex-col gap-2 overflow-y-auto pr-1 [scrollbar-width:thin] [scrollbar-color:rgba(255,102,0,0.45)_rgba(255,255,255,0.08)]">
      <div className="rounded-lg border border-white/10 bg-[#07090b]/90 p-2 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl">
        <div className="mb-2 flex items-center gap-2 px-1">
          <Layers size={14} className="text-cyan-400" />
          <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">
            Map
          </span>
          <span className="ml-auto truncate text-[11px] text-slate-500">
            {regions.length ? `${regions.length} available` : "none"}
          </span>
        </div>
        <div className="relative">
          <button
            type="button"
            onClick={() => setMapMenuOpen((current) => !current)}
            className="flex h-9 w-full items-center gap-2 rounded-md border border-white/10 bg-black/55 px-2 text-left text-xs text-slate-200 outline-none transition-colors hover:border-orange-500/55"
            title="Map selection"
          >
            <MapIcon size={13} className="text-orange-300" />
            <span className="min-w-0 flex-1 truncate">{selectedLabel}</span>
            <span className="text-[10px] uppercase tracking-[0.08em] text-slate-600">
              {selectedMap === "world" ? "Base" : "Upload"}
            </span>
          </button>
          {mapMenuOpen && (
            <div className="absolute right-0 top-10 z-[1250] w-full overflow-hidden rounded-lg border border-white/10 bg-black/90 shadow-2xl">
              <button
                type="button"
                onClick={() => {
                  onMapChange("world");
                  setMapMenuOpen(false);
                }}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-white/[0.06]",
                  selectedMap === "world" ? "text-orange-300" : "text-slate-300",
                )}
              >
                <MapIcon size={13} />
                World Map
              </button>
              <div className="border-t border-white/10 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-600">
                Uploaded Maps
              </div>
              {regions.length === 0 ? (
                <div className="px-3 py-2 text-xs text-slate-600">No uploaded maps</div>
              ) : (
                regions.map((region) => (
                  <button
                    key={region.id}
                    type="button"
                    onClick={() => {
                      onMapChange(region.id);
                      setMapMenuOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-white/[0.06]",
                      selectedMap === region.id ? "text-orange-300" : "text-slate-300",
                    )}
                  >
                    <Layers size={13} />
                    <span className="min-w-0 flex-1 truncate">{region.name}</span>
                  </button>
                ))
              )}
              <button
                type="button"
                onClick={() => {
                  setMapMenuOpen(false);
                  onOpenMapsPanel();
                }}
                className="flex w-full items-center justify-center gap-2 border-t border-white/10 px-3 py-2 text-xs text-slate-300 transition-colors hover:bg-orange-500/10 hover:text-orange-200"
              >
                <Download size={13} />
                Import Map
              </button>
            </div>
          )}
        </div>

        <div className="mt-2 grid grid-cols-2 gap-2">
          <ToolButton
            Icon={Scissors}
            label="Box Cut"
            active={mode === "box"}
            disabled={missionMode}
            onClick={() => {
              if (missionMode) return;
              setMapMenuOpen(false);
              onModeChange(mode === "box" ? "idle" : "box");
            }}
          />
          <ToolButton
            Icon={MapIcon}
            label="Polygon"
            active={mode === "polygon"}
            disabled={missionMode}
            onClick={() => {
              if (missionMode) return;
              setMapMenuOpen(false);
              onModeChange(mode === "polygon" ? "idle" : "polygon");
            }}
          />
        </div>

        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setMapMenuOpen(false);
              onOpenMapsPanel();
            }}
            className="flex h-8 flex-1 items-center justify-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-2 text-xs text-slate-300 transition-colors hover:border-orange-500/55 hover:text-slate-100"
          >
            <MapIcon size={13} />
            Manage
          </button>
          <span className={cn("ops-led rounded-full", selectedMap === "world" || activeMap ? "ops-led-active" : "ops-led-warning")} />
        </div>
        {drawingActive && (
          <div className="mt-2 rounded-md border border-white/10 bg-black/45 p-2">
            <div className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
              <span>
                {mode === "box"
                  ? "Drag map to size box cut"
                  : mode === "polygon"
                  ? "Click boundary points in any order"
                  : "Click map to plot path"}
              </span>
              <span className="font-data-mono text-orange-300">
                {cutDrawingActive ? borderPointCount : waypointCount} pts
              </span>
            </div>
            {drawingMessage && (
              <div className="mt-2 rounded border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[10px] text-orange-200">
                {drawingMessage}
              </div>
            )}
            <div className="mt-2 flex gap-2">
              {cutDrawingActive && (
                <button
                  type="button"
                  onClick={onSaveBorder}
                  disabled={(cutShape === "box" ? borderPointCount !== 4 : borderPointCount < 3) || cutSaveBlocked || cutInstalling}
                  className="flex h-7 flex-1 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.035] text-[11px] text-slate-300 transition-colors hover:border-orange-500/55 hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Check size={12} />
                  {cutInstalling ? "Installing" : "Save Cut"}
                </button>
              )}
              <button
                type="button"
                onClick={onClearDrawing}
                disabled={cutInstalling}
                className="flex h-7 flex-1 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.035] text-[11px] text-slate-400 transition-colors hover:border-red-500/45 hover:text-red-200 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <X size={12} />
                Clear
              </button>
            </div>
            {cutInstallProgress && (
              <CutInstallProgressMeter progress={cutInstallProgress} />
            )}
            {cutDrawingActive && (
              <MapCutEstimateCard
                estimate={cutEstimate}
                loading={cutEstimateLoading}
                error={cutEstimateError}
                cutShape={cutShape}
                fallbackAreaKm2={fallbackCutAreaKm2}
                zoom={cutEstimateZoom}
                onZoomChange={onCutEstimateZoomChange}
                multiLayerMap={multiLayerMap}
                onMultiLayerMapChange={onMultiLayerMapChange}
                providers={providers}
                selectedProviderIds={selectedProviderIds}
                apiKeys={apiKeys}
                onToggleProvider={onToggleProvider}
                previewUrl={cutPreviewUrl}
                pointCount={borderPointCount}
                mapLimitWarnings={mapLimitWarnings}
                mapLimitOverride={mapLimitOverride}
                onMapLimitOverrideChange={onMapLimitOverrideChange}
              />
            )}
          </div>
        )}
        {!drawingActive && cutInstallProgress && (
          <CutInstallProgressMeter progress={cutInstallProgress} />
        )}
      </div>

      <div className="rounded-lg border border-white/10 bg-[#07090b]/90 p-2 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl">
        <button
          type="button"
          onClick={() => {
            if (missionModeDisabled) return;
            setMissionMenuOpen(false);
            onMissionModeToggle();
          }}
          disabled={missionModeDisabled}
          className={cn(
            "flex h-9 w-full items-center justify-center rounded-md border px-3 text-xs font-bold uppercase tracking-[0.12em] transition-colors disabled:cursor-not-allowed disabled:opacity-45",
            missionMode
              ? "border-orange-500 bg-orange-500 text-black shadow-[0_10px_32px_rgba(255,102,0,0.24)]"
              : missionCreateRequired
              ? "border-orange-500/70 bg-orange-500/24 text-orange-100 hover:bg-orange-500/34"
              : "border-orange-500/70 bg-orange-500/18 text-orange-100 hover:bg-orange-500/28",
          )}
        >
          {missionButtonLabel}
        </button>
        {!missionMode && (!missionModeDisabled || missionCreateRequired) ? (
          <div className="mt-2 rounded-md border border-white/10 bg-black/45">
            <button
              type="button"
              onClick={() => setMissionCreateOpen((current) => !current)}
              disabled={!activeMapName}
              className="flex h-8 w-full items-center justify-between gap-2 px-2 text-left text-xs text-slate-200 transition-colors hover:text-orange-100 disabled:cursor-not-allowed disabled:opacity-45"
            >
              <span className="flex min-w-0 items-center gap-2">
                <Route size={13} className="text-orange-300" />
                <span className="truncate">{editingMissionName ? "Edit Mission" : "Create Mission"}</span>
              </span>
              <ChevronDown size={13} className={cn("text-slate-500 transition-transform", missionCreateOpen && "rotate-180")} />
            </button>
            {missionCreateOpen && (
              <div className="space-y-2 border-t border-white/10 p-2">
                {editingMissionName && (
                  <div className="rounded border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[10px] text-orange-100">
                    Editing {editingMissionName}. Select another saved map before saving to move it.
                  </div>
                )}
                <ToolButton
                  Icon={Route}
                  label="Waypoint"
                  active={mode === "waypoint"}
                  disabled={missionMode || !activeMapName}
                  onClick={() => {
                    setMissionMenuOpen(false);
                    onStartMissionDraft();
                  }}
                />
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={onUndoWaypoint}
                    disabled={waypointCount === 0 || missionMode}
                    className="flex h-7 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.035] text-[11px] text-slate-400 transition-colors hover:border-orange-500/45 hover:text-orange-200 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Undo2 size={12} />
                    Undo
                  </button>
                  <button
                    type="button"
                    onClick={onSaveMission}
                    disabled={!canSaveMission || missionMode}
                    className="flex h-7 items-center justify-center gap-1 rounded-md border border-orange-500/45 bg-orange-500/18 text-[11px] text-orange-100 transition-colors hover:bg-orange-500/26 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.035] disabled:text-slate-600"
                  >
                    <Save size={12} />
                    {editingMissionName ? "Update" : "Save"}
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : null}
        <div className="relative mt-2">
          <button
            type="button"
            onClick={() => {
              if (missionSelectorDisabled) return;
              setMissionMenuOpen((current) => !current);
            }}
            disabled={missionSelectorDisabled}
            className="flex h-9 w-full items-center gap-2 rounded-md border border-white/10 bg-black/55 px-2 text-left text-xs text-slate-200 outline-none transition-colors hover:border-orange-500/55 disabled:cursor-not-allowed disabled:opacity-45"
          >
            <Route size={13} className="text-orange-300" />
            <span className="min-w-0 flex-1 truncate">
              {missionModeDisabled
                ? "Select saved map"
                : missionCreateRequired
                ? "No mission saved"
                : selectedCount
                ? `${selectedCount} mission${selectedCount === 1 ? "" : "s"} selected`
                : "Select missions"}
            </span>
            <span className="text-[10px] uppercase tracking-[0.08em] text-slate-600">
              Filter
            </span>
          </button>
          {missionMenuOpen && (
            <div className="mt-2 w-full overflow-hidden rounded-lg border border-white/10 bg-[#050607]/95 shadow-2xl ring-1 ring-black/70">
              <div className="border-b border-white/10 p-2">
                <input
                  value={missionQuery}
                  onChange={(event) => setMissionQuery(event.target.value)}
                  className="h-8 w-full rounded-md border border-white/10 bg-black/75 px-2 text-xs text-slate-200 outline-none placeholder:text-slate-500 focus:border-orange-500/55"
                  placeholder="Filter missions..."
                />
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={onSelectAllMissions}
                    className="h-7 rounded-md border border-white/10 bg-black/55 text-[11px] text-slate-300 transition-colors hover:border-orange-500/45 hover:text-orange-200"
                  >
                    All
                  </button>
                  <button
                    type="button"
                    onClick={onClearMissionSelection}
                    className="h-7 rounded-md border border-white/10 bg-black/55 text-[11px] text-slate-400 transition-colors hover:border-red-500/45 hover:text-red-200"
                  >
                    Clear
                  </button>
                </div>
              </div>
              <div className="max-h-52 overflow-y-auto py-1">
                {filteredMissions.length === 0 ? (
                  <div className="px-3 py-3 text-xs text-slate-600">No saved missions</div>
                ) : (
                  filteredMissions.map((mission) => {
                    const selected = selectedMissionIds.includes(mission.id);
                    return (
                      <div
                        key={mission.id}
                        className={cn(
                          "flex w-full items-center gap-1 px-2 py-1.5 text-left text-xs transition-colors hover:bg-white/[0.06]",
                          selected ? "text-orange-200" : "text-slate-300",
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => onToggleMissionSelection(mission.id)}
                          className="flex min-w-0 flex-1 items-center gap-2 rounded px-1 py-1 text-left"
                        >
                          <span className={cn("h-3 w-3 rounded-sm border", selected ? "border-orange-500 bg-orange-500" : "border-white/20 bg-white/[0.03]")} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate">{mission.name}</span>
                            <span className="block truncate text-[10px] text-slate-600">{mission.map_label ?? "World Map"}</span>
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setMissionMenuOpen(false);
                            onEditMission(mission.id);
                          }}
                          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.035] text-slate-500 transition-colors hover:border-orange-500/45 hover:text-orange-200"
                          title="Edit mission"
                        >
                          <Pencil size={12} />
                        </button>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          )}
        </div>
        {missionMode && (
          <div className="mt-2 rounded-md border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[11px] text-orange-100">
            Live mission overlays active for {activeMapName ?? "selected map"}
          </div>
        )}
        {!missionMode && missionModeDisabled && (
          <div className="mt-2 rounded-md border border-white/10 bg-black/45 px-2 py-1.5 text-[11px] text-slate-500">
            Select or install a saved map before Mission Mode.
          </div>
        )}
        {!missionMode && !missionModeDisabled && missionCreateRequired && (
          <div className="mt-2 rounded-md border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[11px] text-orange-100">
            No mission saved for {activeMapName ?? "this map"}. Use Create Mission to plot waypoints, then Save Mission.
          </div>
        )}
      </div>

    </section>
  );
}

function MapZoomControls({
  onZoomIn,
  onZoomOut,
  onRecenter,
  zoomReady,
}: {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onRecenter: () => void;
  zoomReady: boolean;
}) {
  return (
    <div className="absolute bottom-20 right-4 z-[1080] flex w-11 flex-col overflow-hidden rounded-lg border border-white/10 bg-bg-base/45 opacity-55 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl transition-opacity hover:opacity-95">
      <MapControlButton Icon={Plus} label="Zoom in" disabled={!zoomReady} onClick={onZoomIn} />
      <MapControlButton Icon={Minus} label="Zoom out" disabled={!zoomReady} onClick={onZoomOut} />
      <MapControlButton Icon={LocateFixed} label="Recenter" disabled={!zoomReady} onClick={onRecenter} />
    </div>
  );
}

function CutInstallProgressMeter({ progress }: { progress: CutInstallProgress }) {
  const percent = Math.max(0, Math.min(100, progress.percent));
  const toneClass = progress.tone === "success"
    ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-200"
    : progress.tone === "error"
    ? "border-red-500/25 bg-red-500/10 text-red-200"
    : "border-orange-500/25 bg-orange-500/10 text-orange-100";
  const barClass = progress.tone === "success" ? "bg-emerald-400" : progress.tone === "error" ? "bg-red-400" : "bg-orange-500";

  return (
    <div className={cn("mt-2 rounded-md border px-2 py-2", toneClass)}>
      <div className="flex items-center justify-between gap-2 text-[10px]">
        <span className="truncate">{progress.label}</span>
        <span className="shrink-0 font-data-mono">
          {progress.current != null && progress.total != null
            ? `${progress.current.toLocaleString()} / ${progress.total.toLocaleString()}`
            : `${Math.round(percent)}%`}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-black/45">
        <div
          className={cn("h-full rounded-full transition-all duration-200", barClass)}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

function MapCutEstimateCard({
  estimate,
  loading,
  error,
  cutShape,
  fallbackAreaKm2,
  zoom,
  onZoomChange,
  multiLayerMap,
  onMultiLayerMapChange,
  providers,
  selectedProviderIds,
  apiKeys,
  onToggleProvider,
  previewUrl,
  pointCount,
  mapLimitWarnings,
  mapLimitOverride,
  onMapLimitOverrideChange,
}: {
  estimate: MapUsageEstimate | null;
  loading: boolean;
  error: string | null;
  cutShape: CutShape;
  fallbackAreaKm2: number;
  zoom: number;
  onZoomChange: (zoom: number) => void;
  multiLayerMap: boolean;
  onMultiLayerMapChange: (enabled: boolean) => void;
  providers: MapProvider[];
  selectedProviderIds: MapProviderId[];
  apiKeys: Record<string, string>;
  onToggleProvider: (providerId: MapProviderId) => void;
  previewUrl: string | null;
  pointCount: number;
  mapLimitWarnings: string[];
  mapLimitOverride: boolean;
  onMapLimitOverrideChange: (enabled: boolean) => void;
}) {
  const providerMax = Math.max(...(estimate?.provider_breakdown.map((item) => item.estimated_source_mb) ?? [1]), 1);
  const ready = cutShape === "box" ? pointCount === 4 : pointCount >= 3;
  const resolutionLabel = estimate ? `${estimate.gsd_m_per_px.toFixed(2)} m/px` : "n/a";
  const zoomLabel = multiLayerMap ? `Z15-Z${zoom}` : `Z${zoom}`;
  const [resolutionOpen, setResolutionOpen] = useState(true);
  const [providersOpen, setProvidersOpen] = useState(true);
  const [estimateOpen, setEstimateOpen] = useState(false);
  return (
    <div className="mt-2 rounded-md border border-white/10 bg-white/[0.035] p-2">
      <button
        type="button"
        onClick={() => setResolutionOpen((current) => !current)}
        className="mb-2 flex w-full items-center justify-between gap-2 rounded border border-white/10 bg-black/25 px-2 py-1.5 text-left transition-colors hover:border-orange-500/35"
      >
        <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">
          Download Resolution
        </span>
        <span className="flex items-center gap-1.5">
          <span className="rounded border border-orange-500/30 bg-orange-500/10 px-1.5 py-0.5 font-data-mono text-[10px] uppercase text-orange-200">
            {zoomLabel}
          </span>
          <ChevronDown size={13} className={cn("text-slate-500 transition-transform", resolutionOpen && "rotate-180")} />
        </span>
      </button>
      {resolutionOpen && (
        <div className="mb-2 rounded border border-white/10 bg-black/30 px-2 py-2">
          <label className="flex items-center justify-between text-[10px] text-slate-500">
            <span>Tile zoom</span>
            <span className="font-data-mono text-orange-200">Z{zoom}</span>
          </label>
          <input
            type="range"
            min={15}
            max={MAP_MAX_ZOOM}
            step={1}
            value={zoom}
            onChange={(event) => onZoomChange(Number(event.target.value))}
            className="mt-2 w-full accent-orange-500"
          />
          <div className="mt-1 flex justify-between text-[9px] text-slate-600">
            <span>faster</span>
            <span>higher detail</span>
          </div>
        </div>
      )}
      <div className="mb-2 rounded border border-white/10 bg-black/30">
        <button
          type="button"
          onClick={() => setProvidersOpen((current) => !current)}
          className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left transition-colors hover:bg-white/[0.035]"
        >
          <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">
            Map Providers
          </span>
          <span className="flex items-center gap-1.5">
            <span className="font-data-mono text-[10px] text-slate-600">
              {selectedProviderIds.length || 0} selected
            </span>
            <ChevronDown size={13} className={cn("text-slate-500 transition-transform", providersOpen && "rotate-180")} />
          </span>
        </button>
        {providersOpen && (
          <div className="space-y-1 border-t border-white/10 px-2 py-2">
            {providers.length ? providers.map((provider) => {
              const selected = selectedProviderIds.includes(provider.id);
              const keyMissing = providerNeedsMissingKey(provider, apiKeys);
              const readyProvider = isDownloadProviderReady(provider, apiKeys);
              return (
                <label
                  key={provider.id}
                  className={cn(
                    "flex items-center gap-2 rounded border px-2 py-1.5 text-[10px]",
                    selected
                      ? "border-orange-500/35 bg-orange-500/10 text-orange-100"
                      : readyProvider
                      ? "border-white/10 bg-white/[0.025] text-slate-300"
                      : "border-white/5 bg-white/[0.015] text-slate-600",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={!readyProvider}
                    onChange={() => onToggleProvider(provider.id)}
                    className="accent-orange-500 disabled:opacity-40"
                  />
                  <span className="min-w-0 flex-1 truncate">{provider.label}</span>
                  <span className={cn("shrink-0 font-data-mono", keyMissing ? "text-amber-400" : "text-slate-500")}>
                    {keyMissing ? "key" : `Z${provider.max_native_zoom}`}
                  </span>
                </label>
              );
            }) : (
              <div className="rounded border border-white/10 bg-white/[0.025] px-2 py-1.5 text-[10px] text-slate-600">
                No download providers available.
              </div>
            )}
            {providers.length > 0 && selectedProviderIds.length === 0 && (
              <div className="rounded border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[10px] text-orange-200">
                Select at least one ready provider to calculate the download estimate.
              </div>
            )}
          </div>
        )}
      </div>
      {!ready ? (
        <div className="rounded border border-white/10 bg-black/35 px-2 py-2 text-[10px] text-slate-500">
          {cutShape === "box" ? "Drag a box to calculate size." : "Place at least 3 boundary points to calculate size."}
        </div>
      ) : error ? (
        <div className="rounded border border-red-500/25 bg-red-500/10 px-2 py-2 text-[10px] text-red-200">
          Estimate unavailable: {error}
        </div>
      ) : (
        <>
          <button
            type="button"
            onClick={() => setEstimateOpen((current) => !current)}
            className="mb-2 flex w-full items-center justify-between rounded border border-white/10 bg-black/25 px-2 py-1.5 text-left transition-colors hover:border-orange-500/35"
          >
            <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">
              Download Estimate
            </span>
            <ChevronDown size={13} className={cn("text-slate-500 transition-transform", estimateOpen && "rotate-180")} />
          </button>
          {estimateOpen && (
            <>
          <div className="mb-2 overflow-hidden rounded-md border border-white/10 bg-black/35">
            {previewUrl ? (
              <div className="relative aspect-video">
                <img
                  src={previewUrl}
                  alt={`Satellite resolution preview at zoom ${zoom}`}
                  className="h-full w-full object-cover"
                  draggable={false}
                />
                <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.12)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.12)_1px,transparent_1px)] bg-[length:32px_32px]" />
                <div className="absolute bottom-1 left-1 rounded bg-black/70 px-1.5 py-0.5 font-data-mono text-[9px] text-slate-200">
                  Preview {resolutionLabel}
                </div>
              </div>
            ) : (
              <div className="flex aspect-video items-center justify-center px-3 text-center text-[10px] text-slate-600">
                Draw a cut to preview selected tile resolution.
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <EstimateMetric label="Area" value={`${(estimate?.area_km2 ?? fallbackAreaKm2).toFixed(2)} km2`} active />
            <EstimateMetric label="Download" value={estimate ? formatMegabytes(estimate.estimated_source_mb) : loading ? "..." : "n/a"} active />
            <EstimateMetric label="Tiles" value={estimate ? `${estimate.tile_count.toLocaleString()} @ ${zoomLabel}` : zoomLabel} />
            <EstimateMetric label="Resolution" value={resolutionLabel} />
          </div>
          <div className="mt-2 rounded border border-white/10 bg-black/30 px-2 py-2">
            <div className="mb-1 flex items-center justify-between text-[10px]">
              <span className="text-slate-500">Disk size</span>
              <span className="font-data-mono text-slate-300">{estimate ? formatMegabytes(estimate.estimated_disk_mb) : loading ? "calculating" : "n/a"}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-orange-500 transition-all"
                style={{ width: `${Math.max(8, Math.min(100, ((estimate?.estimated_disk_mb ?? 0) / Math.max(estimate?.estimated_disk_mb ?? 1, 1)) * 100))}%` }}
              />
            </div>
          </div>
          <label className="mt-2 flex items-start gap-2 rounded border border-white/10 bg-black/30 px-2 py-2 text-[10px] text-slate-300">
            <input
              type="checkbox"
              checked={multiLayerMap}
              onChange={(event) => onMultiLayerMapChange(event.target.checked)}
              className="mt-0.5 accent-orange-500"
            />
            <span>
              <span className="block font-medium text-slate-200">Multi-Layer Map</span>
              <span className="block text-slate-500">
                Include every zoom level from Z15 through Z{zoom}; estimate updates with the extra tile layers.
              </span>
            </span>
          </label>
          {mapLimitWarnings.length > 0 && (
            <label className="mt-2 flex items-start gap-2 rounded border border-orange-500/25 bg-orange-500/10 px-2 py-2 text-[10px] text-orange-100">
              <input
                type="checkbox"
                checked={mapLimitOverride}
                onChange={(event) => onMapLimitOverrideChange(event.target.checked)}
                className="mt-0.5 accent-orange-500"
              />
              <span>
                <span className="block font-medium">Override map download warning</span>
                {mapLimitWarnings.map((warning) => (
                  <span key={warning} className="mt-1 block text-orange-200/85">{warning}</span>
                ))}
              </span>
            </label>
          )}
          <div className="mt-2 space-y-1">
            {(estimate?.provider_breakdown.length ? estimate.provider_breakdown : []).map((provider) => (
              <div key={provider.provider_id} className="grid grid-cols-[54px_1fr_52px] items-center gap-2 text-[10px]">
                <span className="truncate text-slate-500">{providerShortLabel(provider.provider_id)}</span>
                <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                  <div
                    className={cn("h-full rounded-full", provider.key_required ? "bg-amber-400" : provider.overzoomed ? "bg-slate-500" : "bg-orange-500")}
                    style={{ width: `${Math.max(6, Math.min(100, (provider.estimated_source_mb / providerMax) * 100))}%` }}
                  />
                </div>
                <span className="text-right font-data-mono text-slate-400">
                  {provider.key_required ? "key" : provider.overzoomed ? "over" : formatMegabytes(provider.estimated_source_mb)}
                </span>
              </div>
            ))}
            {loading && !estimate && (
              <div className="rounded border border-white/10 bg-black/30 px-2 py-1.5 text-[10px] text-slate-500">
                Calculating provider coverage and tile footprint...
              </div>
            )}
          </div>
          </>
          )}
        </>
      )}
    </div>
  );
}

function EstimateMetric({ label, value, active = false }: { label: string; value: string; active?: boolean }) {
  return (
    <div className="rounded border border-white/10 bg-black/30 px-2 py-1.5">
      <div className="text-[10px] text-slate-500">{label}</div>
      <div className={cn("mt-0.5 truncate font-data-mono text-[11px]", active ? "text-orange-200" : "text-slate-300")}>{value}</div>
    </div>
  );
}

function MapControlButton({
  Icon,
  label,
  disabled,
  onClick,
}: {
  Icon: typeof Plus;
  label: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex h-11 w-11 items-center justify-center border-b border-white/10 text-slate-300 transition-colors last:border-b-0 hover:bg-cyan-500/10 hover:text-cyan-300 disabled:cursor-not-allowed disabled:opacity-40"
      title={label}
    >
      <Icon size={17} />
    </button>
  );
}

function ToolButton({
  Icon,
  label,
  active,
  onClick,
  disabled = false,
}: {
  Icon: typeof Scissors;
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex h-8 items-center justify-center gap-1 rounded-md border px-2 text-[11px] transition-colors",
        active
          ? "border-orange-500/75 bg-orange-500/18 text-orange-200"
          : "border-white/10 bg-white/[0.035] text-slate-400 hover:border-orange-500/55 hover:text-slate-100",
        disabled && "cursor-not-allowed opacity-45 hover:border-white/10 hover:text-slate-400",
      )}
    >
      <Icon size={12} />
      <span className="truncate">{label}</span>
    </button>
  );
}

function HudRow({
  Icon,
  label,
  value,
  detail,
  tone,
}: {
  Icon: typeof MapIcon;
  label: string;
  value: string;
  detail: string;
  tone: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-bg-card/80 px-3 py-2">
      <div className="flex items-center gap-2">
        <span className={cn("ops-led", ledClass(tone))} />
        <Icon size={13} className={toneClass(tone)} />
        <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">{label}</span>
      </div>
      <div className={cn("mt-1 truncate font-data-mono text-sm font-semibold", toneClass(tone))}>{value}</div>
      <div className="truncate font-data-mono text-[10px] text-slate-500">{detail}</div>
    </div>
  );
}
