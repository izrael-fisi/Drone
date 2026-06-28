import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, Check, Download, Layers, LocateFixed, Map as MapIcon, Minus, Navigation, Plus, Route, Save, Scissors, X } from "lucide-react";
import type { CircleLayerSpecification, GeoJSONSource, LngLatBoundsLike, Map as MapLibreMap, MapMouseEvent, StyleSpecification, TransformConstrainFunction } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { CircleMarker, MapContainer, Pane, Polygon, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import { createSavedMissionId, loadSavedMissions, missionBoundsFromParts, SAVED_MISSIONS_CHANGED, upsertSavedMission } from "../lib/missions";
import { useShellStore } from "../lib/shellStore";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import type { DronePositionUpdate, Region, SavedMission } from "../lib/types";
import { cn, formatDate } from "../lib/utils";

function regionCenter(region?: Region): [number, number] {
  if (!region) return [37.775, -122.418];
  return [(region.lat_min + region.lat_max) / 2, (region.lon_min + region.lon_max) / 2];
}

function regionPolygon(region?: Region): [number, number][] {
  if (!region) return [];
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
};

type MapViewport = {
  center: [number, number];
  zoom: number;
};

type MapToolMode = "idle" | "border" | "waypoint";
type MapSelectionValue = "world" | string;

const MAP_MIN_ZOOM = 0;
const WEB_MERCATOR_LAT_LIMIT = 85.05112878;
const MAP_MAX_ZOOM = 19;

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
    const borderLine = lineFeature(`${mission.id}-border`, mission.border_points, {
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
  const { devices, regions, activeDeviceId, addRegion } = useAppStore();
  const { rightDockOpen, resetRightDock, pushRightDock, mapSearchTarget, selectedMissionId, setSelectedMissionId, setLivePosition } = useShellStore();
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
  const [waypoints, setWaypoints] = useState<[number, number][]>([]);
  const [savedMissions, setSavedMissions] = useState<SavedMission[]>(() => loadSavedMissions());
  const [missionMode, setMissionMode] = useState(false);
  const [selectedMissionIds, setSelectedMissionIds] = useState<string[]>([]);
  const positionPort = Number(localStorage.getItem("vision_nav_position_udp_port") || 17660);
  const currentPosition = positionLatLon(position);
  const searchedPosition: [number, number] | null = mapSearchTarget ? [mapSearchTarget.lat, mapSearchTarget.lon] : null;
  const mapCenter = searchedPosition ?? (activeMap ? regionCenter(activeMap) : [20, 0] as [number, number]);
  const mapPolygon = regionPolygon(activeMap);
  const positionState = positionTone(position);
  const mapState = selectedMap === "world" ? "ready" : activeMap?.active_bundle_path || activeMap?.lifecycle_state === "active" ? "active" : readyMaps.length ? "ready" : "warning";
  const mapZoom = clampMapZoom(mapSearchTarget?.zoom ?? (activeMap ? 14 : MAP_MIN_ZOOM));
  const [mapViewport, setMapViewport] = useState<MapViewport>({ center: normalizeMapCenter(mapCenter), zoom: mapZoom });
  const hudLeftClass = rightDockOpen ? "left-[444px]" : "left-20";
  const canSaveMission = borderPoints.length >= 3 || waypoints.length > 0;
  const selectedMissionIdSet = new Set(selectedMissionIds);
  const activeMissionOverlays = missionMode
    ? savedMissions.filter((mission) => selectedMissionIdSet.has(mission.id))
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
    if (!selectedMissionId || selectedMissionIds.length > 0) return;
    if (savedMissions.some((mission) => mission.id === selectedMissionId)) {
      setSelectedMissionIds([selectedMissionId]);
    }
  }, [savedMissions, selectedMissionId, selectedMissionIds.length]);

  useEffect(() => {
    setSelectedMissionIds((current) => current.filter((id) => savedMissions.some((mission) => mission.id === id)));
  }, [savedMissions]);

  useEffect(() => {
    if (selectedMap !== "world" && !regions.some((region) => region.id === selectedMap)) {
      setSelectedMap("world");
    }
  }, [regions, selectedMap]);

  const openMapsPanel = () => {
    resetRightDock();
    pushRightDock("maps");
  };

  const saveBorderCut = async () => {
    if (borderPoints.length < 3) return;
    const id = `manual-border-${Date.now()}`;
    const bounds = pointsBounds(borderPoints);
    const nextRegion: Region = {
      id,
      name: `Map cut ${new Date().toLocaleString()}`,
      ...bounds,
      zoom: 16,
      source: "folder",
      output_path: `manual-border://${id}`,
      lifecycle_state: "local",
      tile_count: borderPoints.length,
      location_label: "Manual border cut",
    };
    const nextRegions = [...regions, nextRegion];
    addRegion(nextRegion);
    setSelectedMap(id);
    setBorderPoints([]);
    setToolMode("idle");
    try {
      await cmd.saveRegions(nextRegions);
    } catch (error) {
      console.warn("Failed to persist manual border cut", error);
    }
  };

  const saveMission = () => {
    if (!canSaveMission) return;
    const now = new Date().toISOString();
    const bounds = missionBoundsFromParts(borderPoints, waypoints);
    const defaultName = `Mission ${new Date().toLocaleString()}`;
    const name = window.prompt("Mission name", defaultName)?.trim();
    if (!name) return;
    const center: [number, number] = bounds
      ? [(bounds.lat_min + bounds.lat_max) / 2, (bounds.lon_min + bounds.lon_max) / 2]
      : mapViewport.center;
    const mission: SavedMission = {
      id: createSavedMissionId(),
      name,
      created_at: now,
      updated_at: now,
      source: "dashboard",
      map_id: selectedMap === "world" ? "world" : activeMap?.id,
      map_label: selectedMap === "world" ? "World Map" : activeMap?.name ?? "Uploaded map",
      center,
      zoom: clampMapZoom(mapViewport.zoom),
      border_points: borderPoints,
      waypoints,
      bounds,
    };

    upsertSavedMission(mission);
    setSelectedMissionId(mission.id);
    setSelectedMissionIds([mission.id]);
    setMissionMode(true);
    resetRightDock();
    pushRightDock("missions");
  };

  const clearMapTool = () => {
    setToolMode("idle");
    setBorderPoints([]);
    setWaypoints([]);
  };

  const toggleMissionMode = () => {
    setMissionMode((current) => {
      const next = !current;
      if (next) {
        setToolMode("idle");
        setBorderPoints([]);
        setWaypoints([]);
        if (selectedMissionIds.length === 0 && savedMissions[0]) {
          setSelectedMissionIds([savedMissions[0].id]);
          setSelectedMissionId(savedMissions[0].id);
        }
      }
      return next;
    });
  };

  const toggleMissionSelection = (missionId: string) => {
    setSelectedMissionIds((current) => {
      const next = current.includes(missionId)
        ? current.filter((id) => id !== missionId)
        : [...current, missionId];
      setSelectedMissionId(next[next.length - 1] ?? null);
      return next;
    });
  };

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
          mapPolygon={!missionMode ? mapPolygon : []}
          borderPoints={!missionMode ? borderPoints : []}
          waypoints={!missionMode ? waypoints : []}
          missionMode={missionMode}
          activeMissionOverlays={activeMissionOverlays}
          currentPosition={currentPosition}
          positionState={positionState}
          mode={toolMode}
          setMapApi={setMapApi}
          onViewportChange={setMapViewport}
          onDrawPoint={(point) => {
            if (toolMode === "border") setBorderPoints((current) => [...current, point]);
            if (toolMode === "waypoint") setWaypoints((current) => [...current, point]);
          }}
        />
      </div>

      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-bg-base/45 via-transparent to-bg-base/50" />

      <MapRightControls
        regions={regions}
        activeMap={activeMap}
        selectedMap={selectedMap}
        onMapChange={(id) => setSelectedMap(id)}
        onOpenMapsPanel={openMapsPanel}
        mode={toolMode}
        borderPointCount={borderPoints.length}
        waypointCount={waypoints.length}
        onModeChange={setToolMode}
        onSaveBorder={saveBorderCut}
        onSaveMission={saveMission}
        onClearDrawing={clearMapTool}
        canSaveMission={canSaveMission}
        missions={savedMissions}
        missionMode={missionMode}
        selectedMissionIds={selectedMissionIds}
        onMissionModeToggle={toggleMissionMode}
        onToggleMissionSelection={toggleMissionSelection}
        onSelectAllMissions={() => setSelectedMissionIds(savedMissions.map((mission) => mission.id))}
        onClearMissionSelection={() => setSelectedMissionIds([])}
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
      <LeafletDrawController mode={mode} onPoint={onDrawPoint} />
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

function LeafletDrawController({ mode, onPoint }: { mode: MapToolMode; onPoint: (point: [number, number]) => void }) {
  useMapEvents({
    click(event) {
      if (mode === "idle") return;
      onPoint([event.latlng.lat, event.latlng.lng]);
    },
  });
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
    const drawLines = collection([
      lineFeature("draw-border-line", borderPoints, { kind: "draw-border", tint: "#FF6600" }),
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
    const clickHandler = (event: MapMouseEvent) => {
      if (mode === "idle") return;
      onDrawPoint([wrapMapLatitude(event.lngLat.lat), wrapMapLongitude(event.lngLat.lng)]);
    };
    map.on("click", clickHandler);
    map.getCanvas().style.cursor = mode === "idle" ? "" : "crosshair";
    return () => {
      map.off("click", clickHandler);
      map.getCanvas().style.cursor = "";
    };
  }, [mode, onDrawPoint]);

  return <div ref={containerRef} className="mission-map h-full w-full" />;
}

function setMapSourceData(map: MapLibreMap, sourceId: string, data: MapGeoJsonCollection) {
  mapLibreSource(map, sourceId)?.setData(data);
}

function addOperatorMapLayers(map: MapLibreMap) {
  const sourceIds = ["active-map", "draw-areas", "draw-lines", "draw-points", "mission-areas", "mission-lines", "mission-points", "aircraft"];
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
  borderPointCount,
  waypointCount,
  onModeChange,
  onSaveBorder,
  onSaveMission,
  onClearDrawing,
  canSaveMission,
  missions,
  missionMode,
  selectedMissionIds,
  onMissionModeToggle,
  onToggleMissionSelection,
  onSelectAllMissions,
  onClearMissionSelection,
  onZoomIn,
  onZoomOut,
  onRecenter,
  zoomReady,
}: {
  regions: Region[];
  activeMap?: Region;
  selectedMap: MapSelectionValue;
  onMapChange: (id: MapSelectionValue) => void;
  onOpenMapsPanel: () => void;
  mode: MapToolMode;
  borderPointCount: number;
  waypointCount: number;
  onModeChange: (mode: MapToolMode) => void;
  onSaveBorder: () => void;
  onSaveMission: () => void;
  onClearDrawing: () => void;
  canSaveMission: boolean;
  missions: SavedMission[];
  missionMode: boolean;
  selectedMissionIds: string[];
  onMissionModeToggle: () => void;
  onToggleMissionSelection: (missionId: string) => void;
  onSelectAllMissions: () => void;
  onClearMissionSelection: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onRecenter: () => void;
  zoomReady: boolean;
}) {
  const [mapMenuOpen, setMapMenuOpen] = useState(false);
  const [missionMenuOpen, setMissionMenuOpen] = useState(false);
  const [missionQuery, setMissionQuery] = useState("");
  const selectedLabel = selectedMap === "world" ? "World Map" : activeMap?.name ?? "Uploaded map missing";
  const drawingActive = mode !== "idle";
  const selectedCount = selectedMissionIds.length;
  const filteredMissions = missions.filter((mission) => {
    const normalized = missionQuery.trim().toLowerCase();
    return !normalized || `${mission.name} ${mission.map_label ?? ""}`.toLowerCase().includes(normalized);
  });

  return (
    <section className="absolute right-4 top-4 z-[1150] flex w-[264px] flex-col gap-2">
      <div className="rounded-lg border border-white/10 bg-bg-base/60 p-2 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl">
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
            label="Map Border"
            active={mode === "border"}
            disabled={missionMode}
            onClick={() => {
              if (missionMode) return;
              setMapMenuOpen(false);
              onModeChange(mode === "border" ? "idle" : "border");
            }}
          />
          <ToolButton
            Icon={Route}
            label="Waypoint"
            active={mode === "waypoint"}
            disabled={missionMode}
            onClick={() => {
              if (missionMode) return;
              setMapMenuOpen(false);
              onModeChange(mode === "waypoint" ? "idle" : "waypoint");
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
          <button
            type="button"
            onClick={onSaveMission}
            disabled={!canSaveMission || missionMode}
            className="flex h-8 flex-1 items-center justify-center gap-2 rounded-md border border-orange-500/45 bg-orange-500/18 px-2 text-xs text-orange-100 transition-colors hover:bg-orange-500/26 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.035] disabled:text-slate-600"
          >
            <Save size={13} />
            Save Mission
          </button>
          <span className={cn("ops-led rounded-full", selectedMap === "world" || activeMap ? "ops-led-active" : "ops-led-warning")} />
        </div>
        {drawingActive && (
          <div className="mt-2 rounded-md border border-white/10 bg-black/45 p-2">
            <div className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
              <span>{mode === "border" ? "Click map to cut border" : "Click map to plot path"}</span>
              <span className="font-data-mono text-orange-300">
                {mode === "border" ? borderPointCount : waypointCount} pts
              </span>
            </div>
            <div className="mt-2 flex gap-2">
              {mode === "border" && (
                <button
                  type="button"
                  onClick={onSaveBorder}
                  disabled={borderPointCount < 3}
                  className="flex h-7 flex-1 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.035] text-[11px] text-slate-300 transition-colors hover:border-orange-500/55 hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Check size={12} />
                  Save Cut
                </button>
              )}
              <button
                type="button"
                onClick={onClearDrawing}
                className="flex h-7 flex-1 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.035] text-[11px] text-slate-400 transition-colors hover:border-red-500/45 hover:text-red-200"
              >
                <X size={12} />
                Clear
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="rounded-lg border border-white/10 bg-bg-base/60 p-2 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl">
        <button
          type="button"
          onClick={() => {
            setMissionMenuOpen(true);
            onMissionModeToggle();
          }}
          className={cn(
            "flex h-9 w-full items-center justify-center rounded-md border px-3 text-xs font-bold uppercase tracking-[0.12em] transition-colors",
            missionMode
              ? "border-orange-500 bg-orange-500 text-black shadow-[0_10px_32px_rgba(255,102,0,0.24)]"
              : "border-orange-500/70 bg-orange-500/18 text-orange-100 hover:bg-orange-500/28",
          )}
        >
          Mission Mode
        </button>
        <div className="relative mt-2">
          <button
            type="button"
            onClick={() => setMissionMenuOpen((current) => !current)}
            className="flex h-9 w-full items-center gap-2 rounded-md border border-white/10 bg-black/55 px-2 text-left text-xs text-slate-200 outline-none transition-colors hover:border-orange-500/55"
          >
            <Route size={13} className="text-orange-300" />
            <span className="min-w-0 flex-1 truncate">
              {selectedCount ? `${selectedCount} mission${selectedCount === 1 ? "" : "s"} selected` : "Select missions"}
            </span>
            <span className="text-[10px] uppercase tracking-[0.08em] text-slate-600">
              Filter
            </span>
          </button>
          {missionMenuOpen && (
            <div className="absolute right-0 top-10 z-[1250] w-full overflow-hidden rounded-lg border border-white/10 bg-black/92 shadow-2xl">
              <div className="border-b border-white/10 p-2">
                <input
                  value={missionQuery}
                  onChange={(event) => setMissionQuery(event.target.value)}
                  className="h-8 w-full rounded-md border border-white/10 bg-white/[0.04] px-2 text-xs text-slate-200 outline-none placeholder:text-slate-600 focus:border-orange-500/55"
                  placeholder="Filter missions..."
                />
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={onSelectAllMissions}
                    className="h-7 rounded-md border border-white/10 text-[11px] text-slate-300 transition-colors hover:border-orange-500/45 hover:text-orange-200"
                  >
                    All
                  </button>
                  <button
                    type="button"
                    onClick={onClearMissionSelection}
                    className="h-7 rounded-md border border-white/10 text-[11px] text-slate-400 transition-colors hover:border-red-500/45 hover:text-red-200"
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
                      <button
                        key={mission.id}
                        type="button"
                        onClick={() => onToggleMissionSelection(mission.id)}
                        className={cn(
                          "flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-white/[0.06]",
                          selected ? "text-orange-200" : "text-slate-300",
                        )}
                      >
                        <span className={cn("h-3 w-3 rounded-sm border", selected ? "border-orange-500 bg-orange-500" : "border-white/20 bg-white/[0.03]")} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate">{mission.name}</span>
                          <span className="block truncate text-[10px] text-slate-600">{mission.map_label ?? "World Map"}</span>
                        </span>
                      </button>
                    );
                  })
                )}
              </div>
            </div>
          )}
        </div>
        {missionMode && (
          <div className="mt-2 rounded-md border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[11px] text-orange-100">
            Live mission overlays active
          </div>
        )}
      </div>

      <div className="ml-auto flex w-11 flex-col overflow-hidden rounded-lg border border-white/10 bg-bg-base/60 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl">
        <MapControlButton Icon={Plus} label="Zoom in" disabled={!zoomReady} onClick={onZoomIn} />
        <MapControlButton Icon={Minus} label="Zoom out" disabled={!zoomReady} onClick={onZoomOut} />
        <MapControlButton Icon={LocateFixed} label="Recenter" disabled={!zoomReady} onClick={onRecenter} />
      </div>
    </section>
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
