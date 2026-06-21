import { useEffect, useMemo, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { open, save as saveDialog } from "@tauri-apps/plugin-dialog";
import { readFile, readTextFile, writeTextFile } from "@tauri-apps/plugin-fs";
import { Link, useNavigate } from "react-router-dom";
import { CircleMarker, ImageOverlay, MapContainer, Polygon, Polyline, useMap, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import {
  AlertTriangle,
  Archive,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ClipboardCheck,
  Cpu,
  Download,
  FileInput,
  Flag,
  FolderOpen,
  HardDrive,
  Layers3,
  Loader2,
  Map as MapIcon,
  Navigation,
  Play,
  PlaneTakeoff,
  RadioTower,
  Route,
  ScanSearch,
  Server,
  ShieldCheck,
  Trash2,
  Upload as UploadIcon,
} from "lucide-react";
import { cmd } from "../lib/tauri";
import { useAppStore } from "../lib/store";
import { loadPipelineConfig } from "../lib/pipelineConfig";
import type { PipelineConfig } from "../lib/pipelineConfig";
import { cn, generateId } from "../lib/utils";
import { SupportBundleList } from "../components/SupportBundleList";
import type {
  BuildDroneBundleResult,
  Device,
  Region,
  SupportBundleFile,
  UploadProgress,
} from "../lib/types";

type UploadPayload = UploadProgress;
type Waypoint = { lat: number; lon: number };
type BundleElevationHealth = NonNullable<BuildDroneBundleResult["geospatial_health"]>["elevation"];
type BundleTerrainProfile = NonNullable<BuildDroneBundleResult["geospatial_health"]>["terrain_profile"];
type BundleMapQuality = NonNullable<BuildDroneBundleResult["geospatial_health"]>["map_quality"];
type PlanLayer = "mission" | "fence" | "rally" | "vision";
type MissionItemType = "takeoff" | "waypoint" | "land";
type PlanPoint = Waypoint & { id: string };
type MissionPlanStateStatus = "invalid" | "not_built" | "stale_bundle" | "not_uploaded" | "uploaded" | "bundle_ready";
type MissionItem = PlanPoint & {
  type: MissionItemType;
  altitudeM: number;
  speedMps: number;
  holdSec: number;
};
type MissionDefaults = {
  altitudeM: number;
  speedMps: number;
};
type EstimatorHealthState = "unchecked" | "ready" | "degraded";
type GnssDeniedReadiness = {
  satellite_source_disabled: boolean;
  map_position_reset: PlanPoint | null;
  heading_deg: number | null;
  home_position: PlanPoint | null;
  estimator_health: EstimatorHealthState;
  updated_at: string | null;
};
type TerrainPlanningConstraints = {
  min_agl_m: number;
  max_terrain_relief_m: number;
  min_agl_to_gsd_ratio: number;
  max_route_segment_m: number;
};
type TerrainPlanningMetadata = {
  constraints: TerrainPlanningConstraints;
  offline_cache: {
    map_path: string | null;
    status: "ready" | "missing" | "not_selected";
  };
  route_segmentation: {
    max_segment_m: number;
    estimated_segment_count: number;
    mission_distance_m: number;
  };
};
type TerrainConstraintStatus = "passed" | "failed" | "unknown";
type PlanFileSource = "imported" | "exported";
type PersistedMissionPlannerState = {
  lastBuiltFingerprint?: string | null;
  lastUploadedFingerprint?: string | null;
  lastBuiltAt?: string | null;
  lastUploadedAt?: string | null;
  planFilePath?: string | null;
  planFileFingerprint?: string | null;
  planFileSavedAt?: string | null;
  planFileSource?: PlanFileSource | null;
};
type MissionBounds = [[number, number], [number, number]];
type MissionPlanPayload = {
  version: string;
  groundStation: string;
  activeLayer: PlanLayer;
  region: {
    id: string;
    name: string;
    bounds: { lat_min: number; lat_max: number; lon_min: number; lon_max: number };
    source?: Region["source"];
    output_path: string;
    gsd_m_per_px?: number;
    georef_confidence?: number;
  } | null;
  vehicle: {
    autopilot?: Device["autopilot"];
    mavlink_endpoint?: string;
  };
  mission: {
    altitude_m: number;
    speed_mps: number;
    items: MissionItem[];
  };
  geofence: {
    polygon: PlanPoint[];
  };
  rally_points: PlanPoint[];
  vision: {
    checkpoints: PlanPoint[];
    pipeline: PipelineConfig["pipeline"];
    feature_method: PipelineConfig["featureMethod"];
    max_features: number;
  };
  gnss_denied: GnssDeniedReadiness;
  terrain_planning: TerrainPlanningMetadata;
};

const DEFAULT_LOCAL_REPO = "/Users/izzyfisi/Documents/DRONE";
const PLAN_VERSION = "0.2.0";
const MISSION_PLANNER_STATE_KEY = "drone_mission_planner_state_v1";
const DEFAULT_MISSION_DEFAULTS: MissionDefaults = {
  altitudeM: 35,
  speedMps: 4,
};
const DEFAULT_GNSS_DENIED_READINESS: GnssDeniedReadiness = {
  satellite_source_disabled: false,
  map_position_reset: null,
  heading_deg: null,
  home_position: null,
  estimator_health: "unchecked",
  updated_at: null,
};
const DEFAULT_TERRAIN_CONSTRAINTS: TerrainPlanningConstraints = {
  min_agl_m: 20,
  max_terrain_relief_m: 40,
  min_agl_to_gsd_ratio: 40,
  max_route_segment_m: 500,
};
const LAYER_META: Record<PlanLayer, { label: string; hint: string; icon: typeof Route }> = {
  mission: { label: "Mission", hint: "Takeoff, waypoints, and landing", icon: Route },
  fence: { label: "GeoFence", hint: "Optional safety boundary", icon: ShieldCheck },
  rally: { label: "Rally", hint: "Emergency rally points", icon: Flag },
  vision: { label: "Vision Map", hint: "Localization checkpoints", icon: ScanSearch },
};

function shellQuote(value: string) {
  return `'${value.replace(/'/g, "'\"'\"'")}'`;
}

const SUPPORT_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/support-bundles";
const MODULE_SETUP_HANDOFF_KEY = "drone_module_setup_handoff";

function supportBundleCommand(remoteProject: string, remoteBundle: string, mavlinkEnv: string) {
  return [
    `cd ${shellQuote(remoteProject)}`,
    `VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ${mavlinkEnv}./scripts/pi/create_support_bundle.sh`,
    `latest=$(ls -t "$HOME/DroneTransfer/outgoing/support-bundles/"*.zip 2>/dev/null | head -n 1)`,
    `test -n "$latest"`,
    `echo "__VISION_NAV_SUPPORT_ZIP__=$latest"`,
  ].join(" && ");
}

function parseSupportBundleZip(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_SUPPORT_ZIP__="))
    ?.replace("__VISION_NAV_SUPPORT_ZIP__=", "");
}

function formatHealthLabel(value?: string | number | null) {
  if (value === undefined || value === null || value === "") return "n/a";
  return String(value).replace(/_/g, " ");
}

function checksumBadgeClass(status?: string) {
  if (status === "passed") return "badge-green";
  if (status === "failed") return "badge-red";
  return "badge-yellow";
}

function elevationHealthLabel(elevation?: BundleElevationHealth) {
  if (!elevation || elevation.status === "not_provided" || !elevation.asset_count) return "none";
  const parts = [];
  if (elevation.dem_present) parts.push("DEM");
  if (elevation.dsm_present) parts.push("DSM");
  return parts.length ? parts.join("+") : `${elevation.asset_count} asset${elevation.asset_count === 1 ? "" : "s"}`;
}

function terrainProfileLabel(profile?: BundleTerrainProfile) {
  if (!profile || profile.status === "not_provided") return "none";
  if (profile.status === "not_available") return "no mission";
  const minAgl = profile.estimated_agl_m?.min;
  if (typeof minAgl === "number") return `${minAgl.toFixed(1)} m`;
  return formatHealthLabel(profile.status);
}

function terrainConstraintClass(status: TerrainConstraintStatus) {
  if (status === "passed") return "badge-green";
  if (status === "failed") return "badge-red";
  return "badge-yellow";
}

function terrainConstraintChecks(profile: BundleTerrainProfile | undefined, constraints: TerrainPlanningConstraints) {
  const minAgl = profile?.estimated_agl_m?.min;
  const relief = profile?.terrain_elevation_m?.relief;
  const aglToGsd = profile?.min_agl_to_map_gsd_ratio;
  const available = profile && profile.status !== "not_provided" && profile.status !== "not_available";

  return [
    {
      label: "Min AGL",
      value: typeof minAgl === "number" ? `${minAgl.toFixed(1)} m` : "n/a",
      target: `>= ${constraints.min_agl_m} m`,
      status: available && typeof minAgl === "number"
        ? (minAgl >= constraints.min_agl_m ? "passed" : "failed")
        : "unknown",
    },
    {
      label: "Terrain relief",
      value: typeof relief === "number" ? `${relief.toFixed(1)} m` : "n/a",
      target: `<= ${constraints.max_terrain_relief_m} m`,
      status: available && typeof relief === "number"
        ? (relief <= constraints.max_terrain_relief_m ? "passed" : "failed")
        : "unknown",
    },
    {
      label: "AGL/GSD",
      value: typeof aglToGsd === "number" ? `${aglToGsd.toFixed(1)}x` : "n/a",
      target: `>= ${constraints.min_agl_to_gsd_ratio}x`,
      status: available && typeof aglToGsd === "number"
        ? (aglToGsd >= constraints.min_agl_to_gsd_ratio ? "passed" : "failed")
        : "unknown",
    },
  ] satisfies Array<{ label: string; value: string; target: string; status: TerrainConstraintStatus }>;
}

function qualityCellClass(quality?: string) {
  if (quality === "low") return "bg-red-400/80";
  if (quality === "fair") return "bg-amber-300/80";
  if (quality === "good") return "bg-cyan-300/80";
  if (quality === "dense") return "bg-emerald-300/80";
  return "bg-slate-500/60";
}

function mapQualityPercent(quality?: BundleMapQuality) {
  const ratio = quality?.low_texture_ratio;
  return typeof ratio === "number" ? `${Math.round(ratio * 100)}% low` : "n/a";
}

function profilePreviewPoints(profile?: BundleTerrainProfile) {
  return (profile?.preview_points ?? [])
    .map((point) => {
      const distance = Number(point.distance_m);
      const terrain = Number(point.terrain_elevation_m);
      const agl = Number(point.estimated_agl_m);
      if (!Number.isFinite(distance) || !Number.isFinite(terrain) || !Number.isFinite(agl)) return null;
      return {
        distance,
        terrain,
        aircraft: terrain + agl,
      };
    })
    .filter((point): point is { distance: number; terrain: number; aircraft: number } => point !== null);
}

function profilePolyline(points: { distance: number; value: number }[], width: number, height: number, padding: number) {
  if (points.length < 2) return "";
  const minX = Math.min(...points.map((point) => point.distance));
  const maxX = Math.max(...points.map((point) => point.distance));
  const minY = Math.min(...points.map((point) => point.value));
  const maxY = Math.max(...points.map((point) => point.value));
  const xRange = Math.max(maxX - minX, 1);
  const yRange = Math.max(maxY - minY, 1);
  return points
    .map((point) => {
      const x = padding + ((point.distance - minX) / xRange) * (width - padding * 2);
      const y = height - padding - ((point.value - minY) / yRange) * (height - padding * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function TerrainProfilePreview({ profile }: { profile?: BundleTerrainProfile }) {
  const preview = profilePreviewPoints(profile);
  if (preview.length < 2) return null;
  const width = 320;
  const height = 88;
  const padding = 10;
  const terrainPoints = preview.map((point) => ({ distance: point.distance, value: point.terrain }));
  const aircraftPoints = preview.map((point) => ({ distance: point.distance, value: point.aircraft }));
  const allY = [...terrainPoints, ...aircraftPoints].map((point) => point.value);
  const relief = profile?.terrain_elevation_m?.relief;
  const minAgl = profile?.estimated_agl_m?.min;

  return (
    <div className="rounded-md border border-emerald-500/15 bg-bg-base/40 px-2 py-1.5 space-y-1.5">
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="text-emerald-300/60">Terrain profile preview</span>
        <span className="font-mono text-emerald-300/80">
          {typeof minAgl === "number" ? `min AGL ${minAgl.toFixed(1)} m` : formatHealthLabel(profile?.status)}
        </span>
      </div>
      <svg className="h-20 w-full overflow-visible" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Terrain profile preview">
        <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} className="stroke-emerald-300/20" strokeWidth="1" />
        <polyline
          points={profilePolyline(terrainPoints, width, height, padding)}
          fill="none"
          className="stroke-amber-300/90"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <polyline
          points={profilePolyline(aircraftPoints, width, height, padding)}
          fill="none"
          className="stroke-cyan-300/90"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
      <div className="flex justify-between text-[10px] text-emerald-300/60">
        <span><span className="inline-block h-2 w-2 rounded-[2px] bg-amber-300/90 mr-1" />Terrain</span>
        <span><span className="inline-block h-2 w-2 rounded-[2px] bg-cyan-300/90 mr-1" />Flight</span>
        <span>{typeof relief === "number" ? `relief ${relief.toFixed(1)} m` : `${Math.round(Math.max(...allY) - Math.min(...allY))} m span`}</span>
      </div>
    </div>
  );
}

function defaultRemoteBundleDir(device?: Device) {
  const user = device?.username || "user";
  return `/home/${user}/drone-data/map_bundles/mission_bundle`;
}

function defaultRemoteProjectPath(device?: Device) {
  const user = device?.username || "user";
  return device?.remote_project_path || `/home/${user}/Drone`;
}

function localMosaicPath(region: Region): string {
  const root = region.output_path.replace(/[\\/]+$/, "");
  const separator = root.includes("\\") ? "\\" : "/";
  return `${root}${separator}satellite.png`;
}

function missionCenter(region?: { lat_min: number; lat_max: number; lon_min: number; lon_max: number }): [number, number] {
  if (!region) return [37.775, -122.418];
  return [(region.lat_min + region.lat_max) / 2, (region.lon_min + region.lon_max) / 2];
}

function missionBounds(region?: { lat_min: number; lat_max: number; lon_min: number; lon_max: number }): MissionBounds | undefined {
  if (!region) return undefined;
  return [[region.lat_min, region.lon_min], [region.lat_max, region.lon_max]];
}

function toRad(value: number) {
  return value * Math.PI / 180;
}

function toDeg(value: number) {
  return value * 180 / Math.PI;
}

function waypointDistanceM(a: Waypoint, b: Waypoint): number {
  const latCenter = ((a.lat + b.lat) / 2) * Math.PI / 180;
  const north = (b.lat - a.lat) * 111_320;
  const east = (b.lon - a.lon) * 111_320 * Math.cos(latCenter);
  return Math.sqrt(north * north + east * east);
}

function missionDistanceM(waypoints: Waypoint[]): number {
  return waypoints.slice(1).reduce((sum, waypoint, index) => sum + waypointDistanceM(waypoints[index], waypoint), 0);
}

function makePoint(lat: number, lon: number): PlanPoint {
  return { id: generateId(), lat, lon };
}

function makeMissionItem(
  lat: number,
  lon: number,
  altitudeM: number,
  speedMps: number,
  type: MissionItemType = "waypoint",
): MissionItem {
  return {
    id: generateId(),
    type,
    lat,
    lon,
    altitudeM,
    speedMps,
    holdSec: 0,
  };
}

function regionDimensionsM(region?: Region) {
  if (!region) return { widthM: 0, heightM: 0, areaHa: 0 };
  const southWest = { lat: region.lat_min, lon: region.lon_min };
  const southEast = { lat: region.lat_min, lon: region.lon_max };
  const northWest = { lat: region.lat_max, lon: region.lon_min };
  const widthM = waypointDistanceM(southWest, southEast);
  const heightM = waypointDistanceM(southWest, northWest);
  return { widthM, heightM, areaHa: (widthM * heightM) / 10_000 };
}

function planItemLabel(type: MissionItemType, index: number) {
  if (type === "takeoff") return "Takeoff";
  if (type === "land") return "Land";
  return `WP${index + 1}`;
}

function planPointLabel(point?: PlanPoint | null) {
  if (!point) return "unset";
  return `${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}`;
}

function qgcCommandForItem(type: MissionItemType) {
  if (type === "takeoff") return 22;
  if (type === "land") return 21;
  return 16;
}

function missionReadinessClass(ok: boolean) {
  return ok ? "badge-green" : "badge-yellow";
}

function missionPlanStateClass(status: MissionPlanStateStatus) {
  if (status === "uploaded" || status === "bundle_ready") return "badge-green";
  if (status === "invalid" || status === "stale_bundle") return "badge-red";
  return "badge-yellow";
}

function missionPlanStateCopy(status: MissionPlanStateStatus, activeDevice?: Device | null) {
  const piDevice = activeDevice?.kind === "pi5";
  if (status === "invalid") return { label: "Invalid", detail: "Resolve readiness checks before building a bundle." };
  if (status === "not_built") return { label: "Not built", detail: "Build a mission bundle before running or uploading." };
  if (status === "stale_bundle") return { label: "Stale bundle", detail: "Plan, map, device, or pipeline settings changed after the last build." };
  if (status === "not_uploaded") return { label: "Not uploaded", detail: piDevice ? "Bundle is built locally but has not been uploaded to the Pi." : "Bundle is built locally." };
  if (status === "uploaded") return { label: "Uploaded", detail: "Current bundle has been uploaded to the active Pi." };
  return { label: "Bundle ready", detail: "Current bundle is built for local validation." };
}

function loadMissionPlannerState(): PersistedMissionPlannerState {
  try {
    return JSON.parse(localStorage.getItem(MISSION_PLANNER_STATE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveMissionPlannerState(state: PersistedMissionPlannerState) {
  localStorage.setItem(MISSION_PLANNER_STATE_KEY, JSON.stringify(state));
}

function formatMissionStateTime(value: string | null) {
  if (!value) return "n/a";
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function planFileStateCopy(path: string | null, dirty: boolean, savedAt: string | null, source: PlanFileSource | null) {
  if (!path) return { label: "No plan file", detail: "Import or export a .plan file to track file-save status." };
  const name = path.split(/[\\/]/).pop() || path;
  if (dirty) return { label: "Unsaved changes", detail: `${name} has local edits that have not been exported.` };
  const verb = source === "imported" ? "Imported" : "Exported";
  return { label: "Saved", detail: `${verb} ${name}${savedAt ? ` at ${formatMissionStateTime(savedAt)}` : ""}.` };
}

function bearingDegrees(from: Waypoint, to: Waypoint) {
  const lat1 = toRad(from.lat);
  const lat2 = toRad(to.lat);
  const deltaLon = toRad(to.lon - from.lon);
  const y = Math.sin(deltaLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(deltaLon);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

function buildMissionPlanPayload({
  activeLayer,
  activeDevice,
  selectedRegion,
  missionDefaults,
  missionItems,
  fencePoints,
  rallyPoints,
  visionCheckpoints,
  pipelineConfig,
  gnssDeniedReadiness,
  terrainPlanning,
}: {
  activeLayer: PlanLayer;
  activeDevice?: Device;
  selectedRegion?: Region;
  missionDefaults: MissionDefaults;
  missionItems: MissionItem[];
  fencePoints: PlanPoint[];
  rallyPoints: PlanPoint[];
  visionCheckpoints: PlanPoint[];
  pipelineConfig: PipelineConfig;
  gnssDeniedReadiness: GnssDeniedReadiness;
  terrainPlanning: TerrainPlanningMetadata;
}): MissionPlanPayload {
  return {
    version: PLAN_VERSION,
    groundStation: "Drone Vision Nav Desktop",
    activeLayer,
    region: selectedRegion
      ? {
        id: selectedRegion.id,
        name: selectedRegion.name,
        bounds: {
          lat_min: selectedRegion.lat_min,
          lat_max: selectedRegion.lat_max,
          lon_min: selectedRegion.lon_min,
          lon_max: selectedRegion.lon_max,
        },
        source: selectedRegion.source,
        output_path: selectedRegion.output_path,
        gsd_m_per_px: selectedRegion.gsd_m_per_px,
        georef_confidence: selectedRegion.georef_confidence,
      }
      : null,
    vehicle: {
      autopilot: activeDevice?.autopilot,
      mavlink_endpoint: activeDevice?.mavlink_endpoint,
    },
    mission: {
      altitude_m: missionDefaults.altitudeM,
      speed_mps: missionDefaults.speedMps,
      items: missionItems,
    },
    geofence: {
      polygon: fencePoints,
    },
    rally_points: rallyPoints,
    vision: {
      checkpoints: visionCheckpoints,
      pipeline: pipelineConfig.pipeline,
      feature_method: pipelineConfig.featureMethod,
      max_features: pipelineConfig.maxFeatures,
    },
    gnss_denied: gnssDeniedReadiness,
    terrain_planning: terrainPlanning,
  };
}

function buildQgcPlan(plan: MissionPlanPayload) {
  const firstItem = plan.mission.items[0];
  const plannedHomePosition = firstItem
    ? [firstItem.lat, firstItem.lon, firstItem.altitudeM]
    : [plan.region?.bounds.lat_min ?? 0, plan.region?.bounds.lon_min ?? 0, plan.mission.altitude_m];

  return {
    fileType: "Plan",
    geoFence: {
      circles: [],
      polygons: plan.geofence.polygon.length > 2
        ? [{ inclusion: true, polygon: plan.geofence.polygon.map((point) => [point.lat, point.lon]) }]
        : [],
      version: 2,
    },
    groundStation: plan.groundStation,
    mission: {
      cruiseSpeed: plan.mission.speed_mps,
      firmwareType: plan.vehicle.autopilot === "ardupilot" ? 3 : 12,
      hoverSpeed: plan.mission.speed_mps,
      items: plan.mission.items.map((item, index) => ({
        AMSLAltAboveTerrain: null,
        Altitude: item.altitudeM,
        AltitudeMode: 1,
        autoContinue: true,
        command: qgcCommandForItem(item.type),
        doJumpId: index + 1,
        frame: 3,
        params: [item.holdSec, 0, 0, null, item.lat, item.lon, item.altitudeM],
        type: "SimpleItem",
      })),
      plannedHomePosition,
      vehicleType: 2,
      version: 2,
    },
    rallyPoints: {
      points: plan.rally_points.map((point) => [point.lat, point.lon, plan.mission.altitude_m]),
      version: 2,
    },
    version: 1,
    visionNavigation: {
      region: plan.region,
      pipeline: plan.vision.pipeline,
      feature_method: plan.vision.feature_method,
      max_features: plan.vision.max_features,
      checkpoints: plan.vision.checkpoints,
      gnss_denied: plan.gnss_denied,
      terrain_planning: plan.terrain_planning,
    },
  };
}

function normalizeGnssDeniedReadiness(value: unknown): GnssDeniedReadiness {
  const source = value && typeof value === "object" ? value as Partial<GnssDeniedReadiness> : {};
  const pointOrNull = (point: unknown): PlanPoint | null => {
    if (!point || typeof point !== "object") return null;
    const candidate = point as Partial<PlanPoint>;
    if (!Number.isFinite(candidate.lat) || !Number.isFinite(candidate.lon)) return null;
    return {
      id: candidate.id || generateId(),
      lat: Number(candidate.lat),
      lon: Number(candidate.lon),
    };
  };
  const estimatorHealth = ["unchecked", "ready", "degraded"].includes(String(source.estimator_health))
    ? source.estimator_health as EstimatorHealthState
    : "unchecked";
  return {
    satellite_source_disabled: source.satellite_source_disabled === true,
    map_position_reset: pointOrNull(source.map_position_reset),
    heading_deg: Number.isFinite(source.heading_deg) ? Number(source.heading_deg) : null,
    home_position: pointOrNull(source.home_position),
    estimator_health: estimatorHealth,
    updated_at: typeof source.updated_at === "string" ? source.updated_at : null,
  };
}

function normalizeTerrainPlanningConstraints(value: unknown): TerrainPlanningConstraints {
  const source = value && typeof value === "object" ? value as Partial<TerrainPlanningConstraints> : {};
  const safeNumber = (candidate: unknown, fallback: number) => {
    const value = Number(candidate);
    return Number.isFinite(value) && value >= 0 ? value : fallback;
  };
  return {
    min_agl_m: safeNumber(source.min_agl_m, DEFAULT_TERRAIN_CONSTRAINTS.min_agl_m),
    max_terrain_relief_m: safeNumber(source.max_terrain_relief_m, DEFAULT_TERRAIN_CONSTRAINTS.max_terrain_relief_m),
    min_agl_to_gsd_ratio: safeNumber(source.min_agl_to_gsd_ratio, DEFAULT_TERRAIN_CONSTRAINTS.min_agl_to_gsd_ratio),
    max_route_segment_m: safeNumber(source.max_route_segment_m, DEFAULT_TERRAIN_CONSTRAINTS.max_route_segment_m),
  };
}

function normalizeTerrainPlanningMetadata(value: unknown): TerrainPlanningMetadata {
  const source = value && typeof value === "object" ? value as Partial<TerrainPlanningMetadata> : {};
  return {
    constraints: normalizeTerrainPlanningConstraints(source.constraints ?? source),
    offline_cache: {
      map_path: typeof source.offline_cache?.map_path === "string" ? source.offline_cache.map_path : null,
      status: source.offline_cache?.status === "ready" || source.offline_cache?.status === "missing" || source.offline_cache?.status === "not_selected"
        ? source.offline_cache.status
        : "not_selected",
    },
    route_segmentation: {
      max_segment_m: Number(source.route_segmentation?.max_segment_m) || DEFAULT_TERRAIN_CONSTRAINTS.max_route_segment_m,
      estimated_segment_count: Number(source.route_segmentation?.estimated_segment_count) || 0,
      mission_distance_m: Number(source.route_segmentation?.mission_distance_m) || 0,
    },
  };
}

function parseImportedPlan(text: string): Partial<MissionPlanPayload> {
  const parsed = JSON.parse(text);
  if (parsed?.mission?.items && Array.isArray(parsed.mission.items)) {
    return {
      mission: {
        altitude_m: Number(parsed.mission.altitude_m ?? parsed.survey?.altitudeM ?? DEFAULT_MISSION_DEFAULTS.altitudeM),
        speed_mps: Number(parsed.mission.speed_mps ?? parsed.survey?.speedMps ?? DEFAULT_MISSION_DEFAULTS.speedMps),
        items: parsed.mission.items
          .filter((item: Partial<MissionItem>) => Number.isFinite(item.lat) && Number.isFinite(item.lon))
          .map((item: Partial<MissionItem>) => ({
            id: item.id || generateId(),
            type: item.type || "waypoint",
            lat: Number(item.lat),
            lon: Number(item.lon),
            altitudeM: Number(item.altitudeM ?? parsed.mission.altitude_m ?? parsed.survey?.altitudeM ?? DEFAULT_MISSION_DEFAULTS.altitudeM),
            speedMps: Number(item.speedMps ?? parsed.mission.speed_mps ?? parsed.survey?.speedMps ?? DEFAULT_MISSION_DEFAULTS.speedMps),
            holdSec: Number(item.holdSec ?? 0),
          })),
      },
      geofence: {
        polygon: Array.isArray(parsed.geofence?.polygon)
          ? parsed.geofence.polygon
            .filter((point: Partial<PlanPoint>) => Number.isFinite(point.lat) && Number.isFinite(point.lon))
            .map((point: Partial<PlanPoint>) => ({ id: point.id || generateId(), lat: Number(point.lat), lon: Number(point.lon) }))
          : [],
      },
      rally_points: Array.isArray(parsed.rally_points)
        ? parsed.rally_points
          .filter((point: Partial<PlanPoint>) => Number.isFinite(point.lat) && Number.isFinite(point.lon))
          .map((point: Partial<PlanPoint>) => ({ id: point.id || generateId(), lat: Number(point.lat), lon: Number(point.lon) }))
        : [],
      vision: parsed.vision,
      gnss_denied: normalizeGnssDeniedReadiness(parsed.gnss_denied ?? parsed.gnssDenied),
      terrain_planning: normalizeTerrainPlanningMetadata(parsed.terrain_planning ?? parsed.terrainPlanning),
    };
  }

  if (parsed?.fileType === "Plan" && Array.isArray(parsed?.mission?.items)) {
    const missionItems = parsed.mission.items
      .filter((item: { params?: unknown[] }) => Array.isArray(item.params) && Number.isFinite(item.params[4]) && Number.isFinite(item.params[5]))
      .map((item: { command?: number; params: unknown[] }) => ({
        id: generateId(),
        type: item.command === 22 ? "takeoff" : item.command === 21 ? "land" : "waypoint",
        lat: Number(item.params[4]),
        lon: Number(item.params[5]),
        altitudeM: Number(item.params[6] ?? parsed.mission.plannedHomePosition?.[2] ?? DEFAULT_MISSION_DEFAULTS.altitudeM),
        speedMps: Number(parsed.mission.cruiseSpeed ?? DEFAULT_MISSION_DEFAULTS.speedMps),
        holdSec: Number(item.params[0] ?? 0),
      }));
    const fencePolygon = Array.isArray(parsed.geoFence?.polygons?.[0]?.polygon)
      ? parsed.geoFence.polygons[0].polygon
        .filter((point: unknown[]) => Array.isArray(point) && Number.isFinite(point[0]) && Number.isFinite(point[1]))
        .map((point: unknown[]) => ({ id: generateId(), lat: Number(point[0]), lon: Number(point[1]) }))
      : [];
    const rallyPoints = Array.isArray(parsed.rallyPoints?.points)
      ? parsed.rallyPoints.points
        .filter((point: unknown[]) => Array.isArray(point) && Number.isFinite(point[0]) && Number.isFinite(point[1]))
        .map((point: unknown[]) => ({ id: generateId(), lat: Number(point[0]), lon: Number(point[1]) }))
      : [];
    return {
      mission: {
        altitude_m: Number(parsed.mission.plannedHomePosition?.[2] ?? DEFAULT_MISSION_DEFAULTS.altitudeM),
        speed_mps: Number(parsed.mission.cruiseSpeed ?? DEFAULT_MISSION_DEFAULTS.speedMps),
        items: missionItems,
      },
      geofence: { polygon: fencePolygon },
      rally_points: rallyPoints,
      vision: parsed.visionNavigation,
      gnss_denied: normalizeGnssDeniedReadiness(parsed.visionNavigation?.gnss_denied ?? parsed.visionNavigation?.gnssDenied),
      terrain_planning: normalizeTerrainPlanningMetadata(parsed.visionNavigation?.terrain_planning ?? parsed.visionNavigation?.terrainPlanning),
    };
  }

  throw new Error("Unsupported mission plan format");
}

function FitSelectedRegion({
  regionId,
  bounds,
  center,
}: {
  regionId?: string;
  bounds?: MissionBounds;
  center: [number, number];
}) {
  const map = useMap();
  useEffect(() => {
    if (bounds) map.fitBounds(bounds, { padding: [18, 18], animate: false });
    else map.setView(center, 13);
  }, [map, regionId]);
  return null;
}

function ClickLayer({ onAddPoint }: { onAddPoint: (waypoint: Waypoint) => void }) {
  useMapEvents({
    click(event) {
      onAddPoint({ lat: event.latlng.lat, lon: event.latlng.lng });
    },
  });
  return null;
}

function MissionMap({
  region,
  mosaicUrl,
  activeLayer,
  missionItems,
  fencePoints,
  rallyPoints,
  visionCheckpoints,
  onAddPoint,
}: {
  region?: Region;
  mosaicUrl: string | null;
  activeLayer: PlanLayer;
  missionItems: MissionItem[];
  fencePoints: PlanPoint[];
  rallyPoints: PlanPoint[];
  visionCheckpoints: PlanPoint[];
  onAddPoint: (waypoint: Waypoint) => void;
}) {
  const bounds = missionBounds(region);
  const center = missionCenter(region);
  const missionPath = missionItems.map((item) => ({ lat: item.lat, lon: item.lon }));

  return (
    <MapContainer
      center={center}
      zoom={region ? 16 : 13}
      bounds={bounds}
      className="w-full h-full"
      scrollWheelZoom
      attributionControl={false}
    >
      <FitSelectedRegion regionId={region?.id} bounds={bounds} center={center} />
      {mosaicUrl && bounds && <ImageOverlay key={mosaicUrl} url={mosaicUrl} bounds={bounds} opacity={1} />}
      <ClickLayer onAddPoint={onAddPoint} />
      {missionPath.length > 1 && (
        <Polyline
          positions={missionPath.map((waypoint) => [waypoint.lat, waypoint.lon])}
          pathOptions={{ color: "#06B6D4", weight: 3 }}
        />
      )}
      {fencePoints.length > 2 && (
        <Polygon
          positions={fencePoints.map((point) => [point.lat, point.lon])}
          pathOptions={{ color: "#F59E0B", fillColor: "#F59E0B", fillOpacity: 0.08, weight: 2 }}
        />
      )}
      {fencePoints.length > 1 && fencePoints.length <= 2 && (
        <Polyline
          positions={fencePoints.map((point) => [point.lat, point.lon])}
          pathOptions={{ color: "#F59E0B", weight: 2, dashArray: "6 6" }}
        />
      )}
      {missionItems.map((waypoint, index) => (
        <CircleMarker
          key={`${waypoint.lat}-${waypoint.lon}-${index}`}
          center={[waypoint.lat, waypoint.lon]}
          radius={waypoint.type === "takeoff" || waypoint.type === "land" ? 7 : 5}
          pathOptions={{
            color: waypoint.type === "takeoff" ? "#10B981" : waypoint.type === "land" ? "#EF4444" : "#22D3EE",
            fillColor: waypoint.type === "takeoff" ? "#10B981" : waypoint.type === "land" ? "#EF4444" : "#0891B2",
            fillOpacity: activeLayer === "mission" ? 0.95 : 0.55,
            weight: 2,
          }}
        />
      ))}
      {fencePoints.map((point, index) => (
        <CircleMarker
          key={`fence-${point.id}-${index}`}
          center={[point.lat, point.lon]}
          radius={5}
          pathOptions={{ color: "#F59E0B", fillColor: "#F59E0B", fillOpacity: activeLayer === "fence" ? 0.9 : 0.45, weight: 2 }}
        />
      ))}
      {rallyPoints.map((point, index) => (
        <CircleMarker
          key={`rally-${point.id}-${index}`}
          center={[point.lat, point.lon]}
          radius={6}
          pathOptions={{ color: "#34D399", fillColor: "#047857", fillOpacity: activeLayer === "rally" ? 0.95 : 0.55, weight: 2 }}
        />
      ))}
      {visionCheckpoints.map((point, index) => (
        <CircleMarker
          key={`vision-${point.id}-${index}`}
          center={[point.lat, point.lon]}
          radius={6}
          pathOptions={{ color: "#A78BFA", fillColor: "#7C3AED", fillOpacity: activeLayer === "vision" ? 0.95 : 0.55, weight: 2 }}
        />
      ))}
    </MapContainer>
  );
}

export function MissionPlanner() {
  const { devices, regions, activeDeviceId, setActiveDevice } = useAppStore();
  const navigate = useNavigate();
  const activeDevice = devices.find((d) => d.id === activeDeviceId);
  const pipelineConfig = useMemo(() => loadPipelineConfig(), []);
  const downloadedRegions = regions.filter((r) => r.last_downloaded);
  const persistedPlannerState = useMemo(() => loadMissionPlannerState(), []);

  const [selectedRegionId, setSelectedRegionId] = useState("");
  const selectedRegion = useMemo(
    () => regions.find((r) => r.id === selectedRegionId),
    [regions, selectedRegionId],
  );

  const [repoPath, setRepoPath] = useState(
    () => localStorage.getItem("drone_repo_path") || DEFAULT_LOCAL_REPO,
  );
  const [bundleOutputDir, setBundleOutputDir] = useState("");
  const [remoteBundleDir, setRemoteBundleDir] = useState(defaultRemoteBundleDir(activeDevice));
  const [enableMavlink, setEnableMavlink] = useState(false);
  const [activeLayer, setActiveLayer] = useState<PlanLayer>("mission");
  const [missionDefaults, setMissionDefaults] = useState<MissionDefaults>(DEFAULT_MISSION_DEFAULTS);
  const [missionPlacementType, setMissionPlacementType] = useState<MissionItemType>("takeoff");
  const [missionItems, setMissionItems] = useState<MissionItem[]>([]);
  const [selectedMissionItemId, setSelectedMissionItemId] = useState<string | null>(null);
  const [fencePoints, setFencePoints] = useState<PlanPoint[]>([]);
  const [rallyPoints, setRallyPoints] = useState<PlanPoint[]>([]);
  const [visionCheckpoints, setVisionCheckpoints] = useState<PlanPoint[]>([]);
  const [gnssDeniedReadiness, setGnssDeniedReadiness] = useState<GnssDeniedReadiness>(DEFAULT_GNSS_DENIED_READINESS);
  const [terrainConstraints, setTerrainConstraints] = useState<TerrainPlanningConstraints>(DEFAULT_TERRAIN_CONSTRAINTS);
  const [planMessage, setPlanMessage] = useState("");
  const [mosaicState, setMosaicState] = useState<{
    url: string | null;
    path: string;
    loading: boolean;
    error: string | null;
  }>({ url: null, path: "", loading: false, error: null });

  const [building, setBuilding] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [cmdRunning, setCmdRunning] = useState(false);
  const [fileProgress, setFileProgress] = useState<Record<string, number>>({});
  const [bundleResult, setBundleResult] = useState<BuildDroneBundleResult | null>(null);
  const [commandOutput, setCommandOutput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [supportBundles, setSupportBundles] = useState<SupportBundleFile[]>([]);
  const [lastBuiltFingerprint, setLastBuiltFingerprint] = useState<string | null>(() => persistedPlannerState.lastBuiltFingerprint ?? null);
  const [lastUploadedFingerprint, setLastUploadedFingerprint] = useState<string | null>(() => persistedPlannerState.lastUploadedFingerprint ?? null);
  const [lastBuiltAt, setLastBuiltAt] = useState<string | null>(() => persistedPlannerState.lastBuiltAt ?? null);
  const [lastUploadedAt, setLastUploadedAt] = useState<string | null>(() => persistedPlannerState.lastUploadedAt ?? null);
  const [planFilePath, setPlanFilePath] = useState<string | null>(() => persistedPlannerState.planFilePath ?? null);
  const [planFileFingerprint, setPlanFileFingerprint] = useState<string | null>(() => persistedPlannerState.planFileFingerprint ?? null);
  const [planFileSavedAt, setPlanFileSavedAt] = useState<string | null>(() => persistedPlannerState.planFileSavedAt ?? null);
  const [planFileSource, setPlanFileSource] = useState<PlanFileSource | null>(() => persistedPlannerState.planFileSource ?? null);
  const [pendingPlanFile, setPendingPlanFile] = useState<{ path: string; source: PlanFileSource; savedAt: string } | null>(null);

  const refreshSupportBundles = async () => {
    setSupportBundles(await cmd.listSupportBundles(SUPPORT_DOWNLOAD_DIR));
  };

  useEffect(() => {
    setRemoteBundleDir(defaultRemoteBundleDir(activeDevice));
  }, [activeDevice?.id]);

  useEffect(() => {
    refreshSupportBundles().catch(() => setSupportBundles([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;

    if (!selectedRegion) {
      setMosaicState({ url: null, path: "", loading: false, error: null });
      return () => {};
    }

    const path = localMosaicPath(selectedRegion);
    setMosaicState({ url: null, path, loading: true, error: null });

    readFile(path)
      .then((bytes) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
        setMosaicState({
          url: objectUrl,
          path,
          loading: false,
          error: null,
        });
      })
      .catch((e) => {
        if (cancelled) return;
        setMosaicState({
          url: null,
          path,
          loading: false,
          error: `Could not load saved map image at ${path}. ${String(e)}`,
        });
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [selectedRegion?.id, selectedRegion?.output_path]);

  const effectiveBundleDir =
    bundleOutputDir ||
    (selectedRegion ? `${selectedRegion.output_path.replace(/[\\/]$/, "")}/mission_bundle` : "");
  const missionPath = useMemo(
    () => missionItems.map((item) => ({ lat: item.lat, lon: item.lon })),
    [missionItems],
  );
  const missionDistance = useMemo(() => missionDistanceM(missionPath), [missionPath]);
  const selectedMissionItem = missionItems.find((item) => item.id === selectedMissionItemId);
  const gnssReferenceItem = selectedMissionItem
    ?? missionItems.find((item) => item.type === "takeoff")
    ?? missionItems[0];
  const effectiveMissionPlacementType = missionItems.length === 0 ? "takeoff" : missionPlacementType;
  const regionSize = useMemo(() => regionDimensionsM(selectedRegion), [selectedRegion]);
  const estimatedTimeMin = missionDefaults.speedMps > 0
    ? Math.ceil(missionDistance / missionDefaults.speedMps / 60)
    : 0;
  const georefConfidence = selectedRegion?.georef_confidence ?? (selectedRegion ? 1 : 0);
  const hasVisionReadyMap = !!selectedRegion && georefConfidence >= 0.7 && (selectedRegion.gsd_m_per_px ?? 1) <= 1;
  const terrainPlanningMetadata = useMemo<TerrainPlanningMetadata>(() => {
    const maxSegment = terrainConstraints.max_route_segment_m;
    const estimatedSegments = maxSegment > 0 && missionDistance > 0
      ? Math.max(1, Math.ceil(missionDistance / maxSegment))
      : 0;
    return {
      constraints: terrainConstraints,
      offline_cache: {
        map_path: selectedRegion ? mosaicState.path || null : null,
        status: !selectedRegion ? "not_selected" : mosaicState.error ? "missing" : "ready",
      },
      route_segmentation: {
        max_segment_m: maxSegment,
        estimated_segment_count: estimatedSegments,
        mission_distance_m: Number(missionDistance.toFixed(2)),
      },
    };
  }, [terrainConstraints, missionDistance, selectedRegion, mosaicState.error, mosaicState.path]);
  const terrainChecks = useMemo(
    () => terrainConstraintChecks(bundleResult?.geospatial_health?.terrain_profile, terrainConstraints),
    [bundleResult?.geospatial_health?.terrain_profile, terrainConstraints],
  );
  const planPayload = useMemo(
    () => buildMissionPlanPayload({
      activeLayer,
      activeDevice,
      selectedRegion,
      missionDefaults,
      missionItems,
      fencePoints,
      rallyPoints,
      visionCheckpoints,
      pipelineConfig,
      gnssDeniedReadiness,
      terrainPlanning: terrainPlanningMetadata,
    }),
    [
      activeLayer,
      activeDevice?.id,
      activeDevice?.autopilot,
      activeDevice?.mavlink_endpoint,
      selectedRegion,
      missionDefaults,
      missionItems,
      fencePoints,
      rallyPoints,
      visionCheckpoints,
      pipelineConfig,
      gnssDeniedReadiness,
      terrainPlanningMetadata,
    ],
  );
  const qgcPlan = useMemo(() => buildQgcPlan(planPayload), [planPayload]);
  const missionPlanJson = useMemo(() => JSON.stringify(planPayload, null, 2), [planPayload]);
  const qgcPlanJson = useMemo(() => JSON.stringify(qgcPlan, null, 2), [qgcPlan]);
  const planFingerprint = useMemo(
    () => JSON.stringify({
      mission: planPayload,
      qgc: qgcPlan,
      region_path: selectedRegion?.output_path,
      local_bundle_dir: effectiveBundleDir,
      remote_bundle_dir: remoteBundleDir,
    }),
    [planPayload, qgcPlan, selectedRegion?.output_path, effectiveBundleDir, remoteBundleDir],
  );
  const readinessChecks = [
    { label: "Map source", ok: !!selectedRegion },
    { label: "Mission path", ok: missionItems.length >= 2 },
    { label: "Vision map quality", ok: hasVisionReadyMap },
    { label: "Fence optional", ok: fencePoints.length === 0 || fencePoints.length >= 3 },
    { label: "MAVLink endpoint", ok: !enableMavlink || !!activeDevice?.mavlink_endpoint },
  ];
  const allReadinessChecksPass = readinessChecks.every((check) => check.ok);
  const missionPlanStateStatus: MissionPlanStateStatus = !allReadinessChecksPass
    ? "invalid"
    : !lastBuiltFingerprint
      ? "not_built"
      : lastBuiltFingerprint !== planFingerprint
        ? "stale_bundle"
        : activeDevice?.kind === "pi5"
          ? lastUploadedFingerprint === planFingerprint
            ? "uploaded"
            : "not_uploaded"
          : "bundle_ready";
  const missionPlanState = missionPlanStateCopy(missionPlanStateStatus, activeDevice);
  const planFileDirty = !!planFilePath && planFileFingerprint !== qgcPlanJson;
  const planFileState = planFileStateCopy(planFilePath, planFileDirty, planFileSavedAt, planFileSource);

  useEffect(() => {
    if (!pendingPlanFile) return;
    setPlanFilePath(pendingPlanFile.path);
    setPlanFileSource(pendingPlanFile.source);
    setPlanFileSavedAt(pendingPlanFile.savedAt);
    setPlanFileFingerprint(qgcPlanJson);
    setPendingPlanFile(null);
  }, [pendingPlanFile, qgcPlanJson]);

  useEffect(() => {
    saveMissionPlannerState({
      lastBuiltFingerprint,
      lastUploadedFingerprint,
      lastBuiltAt,
      lastUploadedAt,
      planFilePath,
      planFileFingerprint,
      planFileSavedAt,
      planFileSource,
    });
  }, [
    lastBuiltFingerprint,
    lastUploadedFingerprint,
    lastBuiltAt,
    lastUploadedAt,
    planFilePath,
    planFileFingerprint,
    planFileSavedAt,
    planFileSource,
  ]);

  const pickRepo = async () => {
    const dir = await open({ directory: true, multiple: false, title: "Select Drone repo folder" });
    if (dir && typeof dir === "string") {
      setRepoPath(dir);
      localStorage.setItem("drone_repo_path", dir);
    }
  };

  const pickBundleOutput = async () => {
    const dir = await open({ directory: true, multiple: false, title: "Select mission_bundle output folder" });
    if (dir && typeof dir === "string") setBundleOutputDir(dir);
  };

  const addPlanPoint = (point: Waypoint) => {
    setBundleResult(null);
    setPlanMessage("");
    if (!selectedRegion) {
      setPlanMessage("Select a map source before adding plan points.");
      return;
    }
    if (activeLayer === "mission") {
      const item = makeMissionItem(
        point.lat,
        point.lon,
        missionDefaults.altitudeM,
        missionDefaults.speedMps,
        effectiveMissionPlacementType,
      );
      setMissionItems((current) => {
        if (effectiveMissionPlacementType === "takeoff") {
          return [item, ...current.filter((candidate) => candidate.type !== "takeoff")];
        }
        if (effectiveMissionPlacementType === "land") {
          return [...current.filter((candidate) => candidate.type !== "land"), item];
        }
        return [...current, item];
      });
      setSelectedMissionItemId(item.id);
      if (effectiveMissionPlacementType !== "waypoint") setMissionPlacementType("waypoint");
    } else if (activeLayer === "fence") {
      setFencePoints((current) => [...current, makePoint(point.lat, point.lon)]);
    } else if (activeLayer === "rally") {
      setRallyPoints((current) => [...current, makePoint(point.lat, point.lon)]);
    } else {
      setVisionCheckpoints((current) => [...current, makePoint(point.lat, point.lon)]);
    }
  };

  const updateGnssDeniedReadiness = (patch: Partial<GnssDeniedReadiness>) => {
    setGnssDeniedReadiness((current) => ({
      ...current,
      ...patch,
      updated_at: new Date().toISOString(),
    }));
    setBundleResult(null);
  };

  const updateTerrainConstraint = (key: keyof TerrainPlanningConstraints, value: number) => {
    setTerrainConstraints((current) => ({
      ...current,
      [key]: Number.isFinite(value) && value >= 0 ? value : current[key],
    }));
    setBundleResult(null);
  };

  const setGnssMapPosition = () => {
    if (!gnssReferenceItem) {
      setPlanMessage("Select or create a mission item before setting map position.");
      return;
    }
    updateGnssDeniedReadiness({
      map_position_reset: makePoint(gnssReferenceItem.lat, gnssReferenceItem.lon),
    });
  };

  const setGnssHomePosition = () => {
    if (!gnssReferenceItem) {
      setPlanMessage("Select or create a mission item before setting home position.");
      return;
    }
    updateGnssDeniedReadiness({
      home_position: makePoint(gnssReferenceItem.lat, gnssReferenceItem.lon),
    });
  };

  const setGnssHeadingFromPath = () => {
    if (missionItems.length < 2) {
      setPlanMessage("Add at least two mission items before deriving heading from the path.");
      return;
    }
    updateGnssDeniedReadiness({
      heading_deg: Number(bearingDegrees(missionItems[0], missionItems[1]).toFixed(1)),
    });
  };

  const updateMissionItem = (id: string, patch: Partial<MissionItem>) => {
    setMissionItems((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)));
    setBundleResult(null);
  };

  const moveMissionItem = (id: string, direction: -1 | 1) => {
    setMissionItems((current) => {
      const index = current.findIndex((item) => item.id === id);
      const nextIndex = index + direction;
      if (index < 0 || nextIndex < 0 || nextIndex >= current.length) return current;
      const next = [...current];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
    setBundleResult(null);
  };

  const deleteMissionItem = (id: string) => {
    setMissionItems((current) => current.filter((item) => item.id !== id));
    if (selectedMissionItemId === id) setSelectedMissionItemId(null);
    setBundleResult(null);
  };

  const clearActiveLayer = () => {
    setBundleResult(null);
    if (activeLayer === "mission") {
      setMissionItems([]);
      setSelectedMissionItemId(null);
      setMissionPlacementType("takeoff");
    } else if (activeLayer === "fence") {
      setFencePoints([]);
    } else if (activeLayer === "rally") {
      setRallyPoints([]);
    } else {
      setVisionCheckpoints([]);
    }
  };

  const exportMissionPlan = async () => {
    const path = await saveDialog({
      title: "Export mission plan",
      defaultPath: selectedRegion ? `${selectedRegion.name.replace(/\s+/g, "_")}.plan` : "drone_mission.plan",
      filters: [{ name: "Mission Plan", extensions: ["plan", "json"] }],
    });
    if (!path) return;
    await writeTextFile(path, qgcPlanJson);
    const savedAt = new Date().toISOString();
    setPlanFilePath(path);
    setPlanFileSource("exported");
    setPlanFileSavedAt(savedAt);
    setPlanFileFingerprint(qgcPlanJson);
    setPlanMessage(`Exported mission plan to ${path}`);
  };

  const importMissionPlan = async () => {
    const path = await open({
      multiple: false,
      title: "Import mission plan",
      filters: [{ name: "Mission Plan", extensions: ["plan", "json"] }],
    });
    if (!path || typeof path !== "string") return;
    try {
      const imported = parseImportedPlan(await readTextFile(path));
      if (imported.mission?.items) {
        setMissionItems(imported.mission.items);
        setSelectedMissionItemId(imported.mission.items[0]?.id ?? null);
        setMissionPlacementType(imported.mission.items.length === 0 ? "takeoff" : "waypoint");
        setMissionDefaults((current) => ({
          altitudeM: Number(imported.mission?.altitude_m ?? current.altitudeM),
          speedMps: Number(imported.mission?.speed_mps ?? current.speedMps),
        }));
      }
      if (imported.geofence?.polygon) setFencePoints(imported.geofence.polygon);
      if (imported.rally_points) setRallyPoints(imported.rally_points);
      if (imported.vision?.checkpoints) setVisionCheckpoints(imported.vision.checkpoints);
      setGnssDeniedReadiness(imported.gnss_denied ?? DEFAULT_GNSS_DENIED_READINESS);
      setTerrainConstraints(imported.terrain_planning?.constraints ?? DEFAULT_TERRAIN_CONSTRAINTS);
      setBundleResult(null);
      setPendingPlanFile({ path, source: "imported", savedAt: new Date().toISOString() });
      setPlanMessage(`Imported mission plan from ${path}`);
    } catch (e) {
      setError(String(e));
    }
  };

  const buildBundle = async (): Promise<BuildDroneBundleResult | null> => {
    if (!selectedRegion || !effectiveBundleDir || !repoPath) return null;
    setBuilding(true);
    setError(null);
    setCommandOutput("");
    try {
      const result = await cmd.buildDroneBundle({
        region_dir: selectedRegion.output_path,
        output_dir: effectiveBundleDir,
        repo_path: repoPath,
        pipeline: pipelineConfig.pipeline,
        feature_method: pipelineConfig.featureMethod,
        max_features: pipelineConfig.maxFeatures,
        mission_plan_json: missionPlanJson,
        qgc_plan_json: qgcPlanJson,
      });
      setBundleResult(result);
      setLastBuiltFingerprint(planFingerprint);
      setLastBuiltAt(new Date().toISOString());
      if (lastUploadedFingerprint !== planFingerprint) setLastUploadedFingerprint(null);
      setLastUploadedAt(null);
      setCommandOutput([result.command, result.stdout, result.stderr].filter(Boolean).join("\n"));
      return result;
    } catch (e) {
      setError(String(e));
      return null;
    } finally {
      setBuilding(false);
    }
  };

  const uploadBundle = async (bundle: BuildDroneBundleResult | null = bundleResult) => {
    if (!activeDevice || activeDevice.kind !== "pi5" || !activeDevice.host || !activeDevice.auth || !bundle) return;
    setUploading(true);
    setError(null);
    setFileProgress({});
    const unlisten = await listen<UploadPayload>("upload-progress", (e) => {
      setFileProgress((p) => ({ ...p, [e.payload.file]: e.payload.percent }));
    });
    try {
      await cmd.sshUploadDirectory(
        activeDevice.host,
        activeDevice.port ?? 22,
        activeDevice.username ?? "user",
        activeDevice.auth,
        bundle.bundle_dir,
        remoteBundleDir,
      );
      setLastUploadedFingerprint(planFingerprint);
      setLastUploadedAt(new Date().toISOString());
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(false);
      unlisten();
    }
  };

  const buildAndDeployBundle = async () => {
    if (!activeDevice) return;
    const bundle = await buildBundle();
    if (!bundle) return;
    if (activeDevice.kind === "pi5") await uploadBundle(bundle);
  };

  const openBenchReportSetup = () => {
    if (!activeDevice || activeDevice.kind !== "pi5") return;
    sessionStorage.setItem(
      MODULE_SETUP_HANDOFF_KEY,
      JSON.stringify({
        version: 1,
        source: "mission-planner",
        action: "bench-report",
        created_at: new Date().toISOString(),
        device_id: activeDevice.id,
        device_name: activeDevice.name,
        remote_bundle_dir: remoteBundleDir,
        local_bundle_dir: bundleResult?.bundle_dir ?? effectiveBundleDir,
        region_id: selectedRegion?.id ?? null,
        region_name: selectedRegion?.name ?? null,
        plan_fingerprint: planFingerprint,
        mission_plan_state: missionPlanStateStatus,
        built_at: lastBuiltAt,
        uploaded_at: lastUploadedAt,
      }),
    );
    navigate("/devices");
  };

  const runPiCommand = async (label: string, command: string) => {
    if (!activeDevice || activeDevice.kind !== "pi5" || !activeDevice.host || !activeDevice.auth) return;
    setCmdRunning(true);
    setError(null);
    setCommandOutput(`$ ${label}\n`);
    try {
      const result = await cmd.sshRunCommand(
        activeDevice.host,
        activeDevice.port ?? 22,
        activeDevice.username ?? "user",
        activeDevice.auth,
        command,
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      setCommandOutput(`$ ${label}\n${output || "(no output)"}\n[exit ${result.exit_code}]`);
    } catch (e) {
      setError(String(e));
    } finally {
      setCmdRunning(false);
    }
  };

  const createAndDownloadSupportBundle = async () => {
    if (!activeDevice || activeDevice.kind !== "pi5" || !activeDevice.host || !activeDevice.auth) return;
    setCmdRunning(true);
    setError(null);
    setCommandOutput("$ create support bundle\n");
    try {
      const result = await cmd.sshRunCommand(
        activeDevice.host,
        activeDevice.port ?? 22,
        activeDevice.username ?? "user",
        activeDevice.auth,
        supportBundleCommand(remoteProject, remoteBundleDir, mavlinkEnv),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteZip = parseSupportBundleZip(output);
      if (result.exit_code !== 0 || !remoteZip) {
        setCommandOutput(`$ create support bundle\n${output || "(no output)"}\n[exit ${result.exit_code}]`);
        return;
      }

      setCommandOutput(`$ create support bundle\n${output}\n\n$ download support bundle\nDownloading ${remoteZip}...`);
      const downloaded = await cmd.sshDownloadFile(
        activeDevice.host,
        activeDevice.port ?? 22,
        activeDevice.username ?? "user",
        activeDevice.auth,
        remoteZip,
        SUPPORT_DOWNLOAD_DIR,
      );
      setCommandOutput(`$ create support bundle\n${output}\n\n$ download support bundle\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]`);
      await refreshSupportBundles();
    } catch (e) {
      setError(String(e));
    } finally {
      setCmdRunning(false);
    }
  };

  const remoteProject = defaultRemoteProjectPath(activeDevice);
  const mavlinkEnv = enableMavlink && activeDevice?.mavlink_endpoint
    ? `VISION_NAV_MAVLINK_ENDPOINT=${shellQuote(activeDevice.mavlink_endpoint)} `
    : "";

  if (!activeDevice) {
    return (
      <div className="p-6 flex flex-col items-center justify-center h-full animate-fade-in">
        <Server size={40} className="text-slate-600 mb-4" />
        <h2 className="section-title mb-2">No Device Selected</h2>
        <p className="text-slate-400 text-sm text-center mb-6">
          Select a runtime module before planning and deploying a mission.
        </p>
        <div className="flex gap-3">
          {devices.map((d) => (
            <button key={d.id} onClick={() => setActiveDevice(d.id)} className="btn-secondary">
              {d.kind === "pi5" ? <Server size={14} /> : <HardDrive size={14} />}
              {d.name}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      <div>
        <h1 className="section-title">Mission Planner</h1>
        <p className="text-slate-400 text-sm mt-1">
          Choose the flight area, sketch the drone path, build the vision bundle, and validate the runtime module.
        </p>
      </div>

      <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)] gap-6">
        <div className="card p-0 overflow-hidden h-[520px] relative">
          <MissionMap
            region={selectedRegion}
            mosaicUrl={mosaicState.url}
            activeLayer={activeLayer}
            missionItems={missionItems}
            fencePoints={fencePoints}
            rallyPoints={rallyPoints}
            visionCheckpoints={visionCheckpoints}
            onAddPoint={addPlanPoint}
          />
          {(mosaicState.loading || mosaicState.error) && (
            <div className={cn(
              "absolute left-4 bottom-4 max-w-[70%] rounded-lg border px-3 py-2 text-xs shadow-lg",
              mosaicState.error
                ? "bg-red-950/90 border-red-500/30 text-red-200"
                : "bg-bg-surface/90 border-border text-slate-300",
            )}>
              {mosaicState.loading ? (
                <span className="inline-flex items-center gap-2"><Loader2 size={13} className="animate-spin" /> Loading saved map image...</span>
              ) : (
                <span className="inline-flex items-start gap-2"><AlertTriangle size={13} className="mt-0.5 shrink-0" /> {mosaicState.error}</span>
              )}
            </div>
          )}
          {!selectedRegion && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="rounded-lg border border-border bg-bg-surface/90 px-4 py-3 text-center shadow-lg">
                <MapIcon size={18} className="text-cyan-400 mx-auto mb-2" />
                <div className="text-sm font-medium text-slate-200">Select a map source</div>
                <div className="text-xs text-slate-500 mt-1">The saved mosaic loads only after you choose one below.</div>
              </div>
            </div>
          )}
        </div>
        <div className="card space-y-4">
          <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
            <Layers3 size={14} className="text-cyan-400" /> Plan Editor
          </h3>

          <div className="grid grid-cols-4 gap-1">
            {(Object.keys(LAYER_META) as PlanLayer[]).map((layer) => {
              const meta = LAYER_META[layer];
              const Icon = meta.icon;
              return (
                <button
                  key={layer}
                  onClick={() => setActiveLayer(layer)}
                  className={cn(
                    "rounded-lg border px-2 py-2 text-xs font-medium flex items-center justify-center gap-1 transition-colors",
                    activeLayer === layer ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-300" : "border-border text-slate-400",
                  )}
                  title={meta.hint}
                >
                  <Icon size={12} /> {meta.label}
                </button>
              );
            })}
          </div>

          <div className="rounded-lg border border-border bg-bg-surface p-3 space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <button onClick={importMissionPlan} className="btn-secondary justify-center text-xs py-1.5">
                <FileInput size={13} /> Import
              </button>
              <button onClick={exportMissionPlan} className="btn-secondary justify-center text-xs py-1.5">
                <Download size={13} /> Export .plan
              </button>
            </div>
            {planMessage && <p className="text-[11px] text-emerald-400">{planMessage}</p>}
          </div>

          {activeLayer === "mission" && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="label">Altitude m</label>
                  <input
                    className="input-field"
                    type="number"
                    value={missionDefaults.altitudeM}
                    onChange={(e) => setMissionDefaults((current) => ({ ...current, altitudeM: Number(e.target.value) }))}
                  />
                </div>
                <div>
                  <label className="label">Speed m/s</label>
                  <input
                    className="input-field"
                    type="number"
                    value={missionDefaults.speedMps}
                    onChange={(e) => setMissionDefaults((current) => ({ ...current, speedMps: Number(e.target.value) }))}
                  />
                </div>
              </div>

              <div className="grid grid-cols-4 gap-2">
                <button
                  onClick={() => setMissionPlacementType("takeoff")}
                  disabled={!selectedRegion}
                  className={cn(
                    "btn-secondary justify-center text-xs py-1.5",
                    effectiveMissionPlacementType === "takeoff" && "border-cyan-500/40 bg-cyan-500/10 text-cyan-300",
                  )}
                >
                  <PlaneTakeoff size={13} /> Takeoff
                </button>
                <button
                  onClick={() => setMissionPlacementType("waypoint")}
                  disabled={!selectedRegion}
                  className={cn(
                    "btn-secondary justify-center text-xs py-1.5",
                    effectiveMissionPlacementType === "waypoint" && "border-cyan-500/40 bg-cyan-500/10 text-cyan-300",
                  )}
                >
                  <Route size={13} /> Waypoint
                </button>
                <button
                  onClick={() => setMissionPlacementType("land")}
                  disabled={!selectedRegion}
                  className={cn(
                    "btn-secondary justify-center text-xs py-1.5",
                    effectiveMissionPlacementType === "land" && "border-cyan-500/40 bg-cyan-500/10 text-cyan-300",
                  )}
                >
                  <Navigation size={13} /> Land
                </button>
                <button onClick={clearActiveLayer} className="btn-secondary justify-center text-xs py-1.5 text-red-400 border-red-500/20">
                  <Trash2 size={13} /> Clear
                </button>
              </div>

              <div className="max-h-44 overflow-y-auto space-y-1">
                {missionItems.length === 0 ? (
                  <p className="text-xs text-slate-500">Select a map source, then click the map to add waypoints.</p>
                ) : missionItems.map((item, index) => (
                  <div
                    key={item.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedMissionItemId(item.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") setSelectedMissionItemId(item.id);
                    }}
                    className={cn(
                      "w-full rounded-lg border px-2 py-2 text-left transition-colors cursor-pointer",
                      selectedMissionItemId === item.id ? "border-cyan-500/40 bg-cyan-500/5" : "border-border hover:border-border-strong",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-slate-200">{planItemLabel(item.type, index)}</span>
                      <span className="text-[10px] text-slate-500 font-mono">{item.altitudeM.toFixed(0)} m</span>
                      <div className="ml-auto flex items-center gap-1">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            moveMissionItem(item.id, -1);
                          }}
                          className="btn-ghost p-1"
                        >
                          <ArrowUp size={12} />
                        </button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            moveMissionItem(item.id, 1);
                          }}
                          className="btn-ghost p-1"
                        >
                          <ArrowDown size={12} />
                        </button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteMissionItem(item.id);
                          }}
                          className="btn-ghost p-1 text-red-400"
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>
                    <div className="text-[11px] text-slate-500 font-mono mt-1">
                      {item.lat.toFixed(6)}, {item.lon.toFixed(6)}
                    </div>
                  </div>
                ))}
              </div>

              {selectedMissionItem && (
                <div className="rounded-lg border border-border bg-bg-surface p-3 space-y-3">
                  <div className="text-xs font-medium text-slate-300">Selected item</div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="label">Latitude</label>
                      <input className="input-field font-mono text-xs" type="number" value={selectedMissionItem.lat} onChange={(e) => updateMissionItem(selectedMissionItem.id, { lat: Number(e.target.value) })} />
                    </div>
                    <div>
                      <label className="label">Longitude</label>
                      <input className="input-field font-mono text-xs" type="number" value={selectedMissionItem.lon} onChange={(e) => updateMissionItem(selectedMissionItem.id, { lon: Number(e.target.value) })} />
                    </div>
                    <div>
                      <label className="label">Altitude m</label>
                      <input className="input-field" type="number" value={selectedMissionItem.altitudeM} onChange={(e) => updateMissionItem(selectedMissionItem.id, { altitudeM: Number(e.target.value) })} />
                    </div>
                    <div>
                      <label className="label">Hold sec</label>
                      <input className="input-field" type="number" value={selectedMissionItem.holdSec} onChange={(e) => updateMissionItem(selectedMissionItem.id, { holdSec: Number(e.target.value) })} />
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {activeLayer !== "mission" && (
            <div className="rounded-lg border border-border bg-bg-surface p-3 space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-200">{LAYER_META[activeLayer].label}</div>
                  <p className="text-xs text-slate-500 mt-1">{LAYER_META[activeLayer].hint}. Click the map to add points.</p>
                </div>
                <button onClick={clearActiveLayer} className="btn-secondary text-xs py-1.5 px-2 text-red-400 border-red-500/20">
                  <Trash2 size={12} /> Clear
                </button>
              </div>
              <div className="max-h-44 overflow-y-auto space-y-1">
                {(activeLayer === "fence" ? fencePoints : activeLayer === "rally" ? rallyPoints : visionCheckpoints).length === 0 ? (
                  <p className="text-xs text-slate-500">No points yet.</p>
                ) : (activeLayer === "fence" ? fencePoints : activeLayer === "rally" ? rallyPoints : visionCheckpoints).map((point, index) => (
                  <div key={point.id} className="flex justify-between gap-3 text-[11px] font-mono text-slate-500">
                    <span>{activeLayer === "fence" ? `F${index + 1}` : activeLayer === "rally" ? `R${index + 1}` : `V${index + 1}`}</span>
                    <span className="truncate">{point.lat.toFixed(6)}, {point.lon.toFixed(6)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="rounded-lg border border-border bg-bg-surface p-3 space-y-2">
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div>
                <span className="block text-slate-500">Items</span>
                <span className="text-slate-200 font-medium">{missionItems.length}</span>
              </div>
              <div>
                <span className="block text-slate-500">Distance</span>
                <span className="text-slate-200 font-medium">{(missionDistance / 1000).toFixed(2)} km</span>
              </div>
              <div>
                <span className="block text-slate-500">Est. time</span>
                <span className="text-slate-200 font-medium">{estimatedTimeMin > 0 ? `${estimatedTimeMin} min` : "n/a"}</span>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <span className="block text-slate-500">Map area</span>
                <span className="text-slate-200 font-medium">{regionSize.areaHa > 0 ? `${regionSize.areaHa.toFixed(1)} ha` : "n/a"}</span>
              </div>
              <div>
                <span className="block text-slate-500">Map status</span>
                <span className="text-slate-200 font-medium">{selectedRegion ? "Selected" : "Not selected"}</span>
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-bg-surface p-3 space-y-2">
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs font-medium text-slate-300">Mission state</span>
              <span className={missionPlanStateClass(missionPlanStateStatus)}>
                {missionPlanStateStatus === "uploaded" || missionPlanStateStatus === "bundle_ready" ? (
                  <CheckCircle2 size={11} />
                ) : (
                  <AlertTriangle size={11} />
                )}
                {missionPlanState.label}
              </span>
            </div>
            <p className="text-[11px] text-slate-500">{missionPlanState.detail}</p>
            <div className="flex items-center justify-between gap-3 rounded-md border border-border/70 bg-bg-card px-2 py-1.5">
              <span className="text-[11px] text-slate-500">Plan file</span>
              <span className={planFileDirty ? "badge-yellow" : planFilePath ? "badge-green" : "badge-yellow"}>
                {planFileDirty || !planFilePath ? <AlertTriangle size={11} /> : <CheckCircle2 size={11} />}
                {planFileState.label}
              </span>
            </div>
            <p className="text-[11px] text-slate-500">{planFileState.detail}</p>
            <div className="grid grid-cols-2 gap-2 text-[11px] font-mono text-slate-500">
              <span>built {formatMissionStateTime(lastBuiltAt)}</span>
              <span>uploaded {formatMissionStateTime(lastUploadedAt)}</span>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {readinessChecks.map((check) => (
              <span key={check.label} className={missionReadinessClass(check.ok)}>
                {check.ok ? <CheckCircle2 size={11} /> : <AlertTriangle size={11} />}
                {check.label}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-[1.1fr_0.9fr] gap-6">
        <div className="space-y-4">
          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <MapIcon size={14} className="text-cyan-400" /> Flight Area / Map Source
            </h3>
            {downloadedRegions.length === 0 ? (
              <p className="text-xs text-slate-500">Download an area or import your own map from Maps first.</p>
            ) : (
              <div className="space-y-2">
                {downloadedRegions.map((region) => (
                  <button
                    key={region.id}
                    onClick={() => {
                      setSelectedRegionId(region.id);
                      setBundleResult(null);
                    }}
                    className={cn(
                      "w-full text-left rounded-lg border p-3 transition-colors",
                      selectedRegionId === region.id
                        ? "border-cyan-500/40 bg-cyan-500/5"
                        : "border-border hover:border-border-strong",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <MapIcon size={13} className="text-cyan-400 shrink-0" />
                      <span className="text-sm font-medium text-slate-200">{region.name}</span>
                      <span className="text-[10px] bg-bg-elevated border border-border rounded px-1.5 py-0.5 text-slate-500">
                        {region.source === "uploaded" ? "Uploaded" : region.source === "folder" ? "Folder" : "Tiles"}
                      </span>
                      {selectedRegionId === region.id && <CheckCircle2 size={13} className="text-cyan-400 ml-auto" />}
                    </div>
                    <div className="text-[11px] text-slate-500 font-mono mt-1 truncate">{region.output_path}</div>
                    {selectedRegionId === region.id && mosaicState.path && (
                      <div className={cn(
                        "text-[11px] font-mono mt-1 truncate",
                        mosaicState.error ? "text-red-400" : "text-slate-500",
                      )}>
                        image: {mosaicState.path}
                      </div>
                    )}
                    {region.gsd_m_per_px != null && (
                      <div className="text-[11px] text-slate-500 mt-1">{region.gsd_m_per_px.toFixed(2)} m/px</div>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="card space-y-4">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <Cpu size={14} className="text-cyan-400" /> Vision Pipeline
            </h3>
            <div className="grid grid-cols-3 gap-3 text-xs">
              <div>
                <span className="block text-slate-500">Mode</span>
                <span className="text-slate-200 font-medium">{pipelineConfig.pipeline === "classical" ? "Classical CPU" : "SP + LightGlue"}</span>
              </div>
              <div>
                <span className="block text-slate-500">Features</span>
                <span className="text-slate-200 font-medium">{pipelineConfig.featureMethod.toUpperCase()}</span>
              </div>
              <div>
                <span className="block text-slate-500">Max</span>
                <span className="text-slate-200 font-medium">{pipelineConfig.maxFeatures.toLocaleString()}</span>
              </div>
            </div>
            <Link to="/vision-pipeline" className="btn-secondary justify-center text-xs py-1.5">
              Open Vision Pipeline Settings
            </Link>
            <p className="text-[11px] text-slate-500">
              Mission bundles use the saved Vision Pipeline defaults. Edit them only on the Vision Pipeline page.
            </p>
          </div>

          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <Navigation size={14} className="text-cyan-400" /> GNSS-Denied Readiness
            </h3>
            <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <div>
                <div className="text-sm text-slate-200">Satellite source disabled</div>
                <div className="text-[11px] text-slate-500">
                  Reference {gnssReferenceItem ? planItemLabel(gnssReferenceItem.type, missionItems.indexOf(gnssReferenceItem)) : "unset"}
                </div>
              </div>
              <button
                onClick={() => updateGnssDeniedReadiness({ satellite_source_disabled: !gnssDeniedReadiness.satellite_source_disabled })}
                className={cn(
                  "w-11 h-6 rounded-full border transition-colors relative",
                  gnssDeniedReadiness.satellite_source_disabled ? "bg-cyan-500/20 border-cyan-500/50" : "bg-bg-elevated border-border",
                )}
              >
                <span
                  className={cn(
                    "absolute top-0.5 h-5 w-5 rounded-full bg-slate-300 transition-transform",
                    gnssDeniedReadiness.satellite_source_disabled ? "translate-x-5" : "translate-x-0.5",
                  )}
                />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              <div className="rounded-lg border border-border bg-bg-card px-2 py-1.5">
                <span className="block text-slate-500">Map reset</span>
                <span className="font-mono text-slate-300">{planPointLabel(gnssDeniedReadiness.map_position_reset)}</span>
              </div>
              <div className="rounded-lg border border-border bg-bg-card px-2 py-1.5">
                <span className="block text-slate-500">Home reset</span>
                <span className="font-mono text-slate-300">{planPointLabel(gnssDeniedReadiness.home_position)}</span>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label">Heading deg</label>
                <input
                  className="input-field"
                  type="number"
                  value={gnssDeniedReadiness.heading_deg ?? ""}
                  onChange={(event) => updateGnssDeniedReadiness({
                    heading_deg: event.target.value === "" ? null : Number(event.target.value),
                  })}
                />
              </div>
              <div>
                <label className="label">Estimator health</label>
                <select
                  className="input-field"
                  value={gnssDeniedReadiness.estimator_health}
                  onChange={(event) => updateGnssDeniedReadiness({ estimator_health: event.target.value as EstimatorHealthState })}
                >
                  <option value="unchecked">Unchecked</option>
                  <option value="ready">Ready</option>
                  <option value="degraded">Degraded</option>
                </select>
              </div>
            </div>
            <div className="grid grid-cols-4 gap-2">
              <button onClick={setGnssMapPosition} className="btn-secondary justify-center text-xs py-1.5 px-2">
                Map
              </button>
              <button onClick={setGnssHomePosition} className="btn-secondary justify-center text-xs py-1.5 px-2">
                Home
              </button>
              <button onClick={setGnssHeadingFromPath} className="btn-secondary justify-center text-xs py-1.5 px-2">
                Heading
              </button>
              <button onClick={() => updateGnssDeniedReadiness(DEFAULT_GNSS_DENIED_READINESS)} className="btn-secondary justify-center text-xs py-1.5 px-2 text-red-400 border-red-500/20">
                Clear
              </button>
            </div>
          </div>

          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <ScanSearch size={14} className="text-cyan-400" /> Terrain Planning
            </h3>
            <div className="flex items-start justify-between gap-3 rounded-lg border border-border bg-bg-surface px-3 py-2">
              <div className="min-w-0">
                <div className="text-sm text-slate-200">Offline map cache</div>
                <div className="text-[11px] text-slate-500 font-mono truncate">
                  {terrainPlanningMetadata.offline_cache.map_path ?? "No map selected"}
                </div>
              </div>
              <span className={terrainPlanningMetadata.offline_cache.status === "ready" ? "badge-green" : "badge-yellow"}>
                {terrainPlanningMetadata.offline_cache.status === "ready" ? <CheckCircle2 size={11} /> : <AlertTriangle size={11} />}
                {formatHealthLabel(terrainPlanningMetadata.offline_cache.status)}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label">Min AGL m</label>
                <input
                  className="input-field"
                  type="number"
                  min={0}
                  value={terrainConstraints.min_agl_m}
                  onChange={(event) => updateTerrainConstraint("min_agl_m", Number(event.target.value))}
                />
              </div>
              <div>
                <label className="label">Max relief m</label>
                <input
                  className="input-field"
                  type="number"
                  min={0}
                  value={terrainConstraints.max_terrain_relief_m}
                  onChange={(event) => updateTerrainConstraint("max_terrain_relief_m", Number(event.target.value))}
                />
              </div>
              <div>
                <label className="label">Min AGL/GSD</label>
                <input
                  className="input-field"
                  type="number"
                  min={0}
                  value={terrainConstraints.min_agl_to_gsd_ratio}
                  onChange={(event) => updateTerrainConstraint("min_agl_to_gsd_ratio", Number(event.target.value))}
                />
              </div>
              <div>
                <label className="label">Max segment m</label>
                <input
                  className="input-field"
                  type="number"
                  min={0}
                  value={terrainConstraints.max_route_segment_m}
                  onChange={(event) => updateTerrainConstraint("max_route_segment_m", Number(event.target.value))}
                />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2 text-[11px]">
              <div className="rounded-lg border border-border bg-bg-surface px-2 py-1.5">
                <span className="block text-slate-500">Distance</span>
                <span className="text-slate-200 font-medium">{(terrainPlanningMetadata.route_segmentation.mission_distance_m / 1000).toFixed(2)} km</span>
              </div>
              <div className="rounded-lg border border-border bg-bg-surface px-2 py-1.5">
                <span className="block text-slate-500">Segments</span>
                <span className="text-slate-200 font-medium">{terrainPlanningMetadata.route_segmentation.estimated_segment_count || "n/a"}</span>
              </div>
              <div className="rounded-lg border border-border bg-bg-surface px-2 py-1.5">
                <span className="block text-slate-500">Profile</span>
                <span className="text-slate-200 font-medium">{terrainProfileLabel(bundleResult?.geospatial_health?.terrain_profile)}</span>
              </div>
            </div>
            <div className="space-y-1">
              {terrainChecks.map((check) => (
                <div key={check.label} className="flex items-center justify-between gap-3 rounded-md border border-border/70 bg-bg-card px-2 py-1.5 text-[11px]">
                  <span className="text-slate-500">{check.label}</span>
                  <span className="text-slate-300 font-mono">{check.value}</span>
                  <span className={terrainConstraintClass(check.status)}>
                    {check.status === "passed" ? <CheckCircle2 size={11} /> : <AlertTriangle size={11} />}
                    {check.target}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <UploadIcon size={14} className="text-cyan-400" /> Mission Bundle
            </h3>
            <div>
              <label className="label">Drone repo path</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 font-mono text-xs" value={repoPath} onChange={(e) => setRepoPath(e.target.value)} />
                <button onClick={pickRepo} className="btn-secondary px-3"><FolderOpen size={14} /></button>
              </div>
            </div>
            <div>
              <label className="label">Bundle output directory</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 font-mono text-xs" value={effectiveBundleDir} onChange={(e) => setBundleOutputDir(e.target.value)} />
                <button onClick={pickBundleOutput} className="btn-secondary px-3"><FolderOpen size={14} /></button>
              </div>
            </div>
            {activeDevice.kind === "pi5" && (
              <div>
                <label className="label">Remote bundle directory</label>
                <input
                  className="input-field font-mono text-xs"
                  value={remoteBundleDir}
                  onChange={(e) => setRemoteBundleDir(e.target.value)}
                />
              </div>
            )}
            <button
              onClick={buildAndDeployBundle}
              disabled={!selectedRegion || !repoPath || building || uploading || (activeDevice.kind === "pi5" && (!activeDevice.host || !activeDevice.auth))}
              className="btn-primary w-full justify-center"
            >
              {building || uploading ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
              {activeDevice.kind === "pi5" ? "Build and Upload Mission Bundle" : "Build Mission Bundle"}
            </button>
            {activeDevice.kind === "pi5" && (
              <button
                onClick={openBenchReportSetup}
                disabled={missionPlanStateStatus !== "uploaded" || !bundleResult}
                className="btn-secondary w-full justify-center"
              >
                <Archive size={14} />
                Open Bench Report In Module Setup
              </button>
            )}
            {uploading && Object.keys(fileProgress).length > 0 && (
              <div className="space-y-2 max-h-40 overflow-y-auto">
                {Object.entries(fileProgress).map(([file, pct]) => (
                  <div key={file}>
                    <div className="flex justify-between text-[11px] text-slate-400 mb-1">
                      <span className="truncate font-mono">{file}</span>
                      <span>{pct.toFixed(0)}%</span>
                    </div>
                    <div className="h-1.5 bg-bg-elevated rounded-full overflow-hidden">
                      <div className="h-full bg-cyan-500 rounded-full transition-all" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-4">
          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <RadioTower size={14} className="text-cyan-400" /> Runtime And MAVLink
            </h3>
            <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <div>
                <div className="text-sm text-slate-200">Send MAVLink vision messages</div>
                <div className="text-[11px] text-slate-500 font-mono">{activeDevice.mavlink_endpoint || "No endpoint configured"}</div>
                <div className="text-[11px] text-slate-500">Optional barometer telemetry is read from MAVLink when available.</div>
              </div>
              <button
                onClick={() => setEnableMavlink((v) => !v)}
                className={cn(
                  "w-11 h-6 rounded-full border transition-colors relative",
                  enableMavlink ? "bg-cyan-500/20 border-cyan-500/50" : "bg-bg-elevated border-border",
                )}
              >
                <span
                  className={cn(
                    "absolute top-0.5 h-5 w-5 rounded-full bg-slate-300 transition-transform",
                    enableMavlink ? "translate-x-5" : "translate-x-0.5",
                  )}
                />
              </button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <button
                disabled={activeDevice.kind !== "pi5" || cmdRunning}
                onClick={() => runPiCommand(
                  "validate bundle",
                  `cd ${shellQuote(remoteProject)} && VISION_NAV_BUNDLE=${shellQuote(remoteBundleDir)} ./scripts/pi/validate_terrain_bundle.sh`,
                )}
                className="btn-secondary justify-center"
              >
                {cmdRunning ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />}
                Validate
              </button>
              <button
                disabled={activeDevice.kind !== "pi5" || cmdRunning}
                onClick={() => runPiCommand(
                  enableMavlink ? "run loop with mavlink" : "run loop",
                  `cd ${shellQuote(remoteProject)} && VISION_NAV_BUNDLE=${shellQuote(remoteBundleDir)} ${mavlinkEnv}VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh`,
                )}
                className="btn-secondary justify-center text-emerald-400 border-emerald-500/20"
              >
                <Play size={13} />
                Run 30 Frames
              </button>
              <button
                disabled={activeDevice.kind !== "pi5" || cmdRunning}
                onClick={createAndDownloadSupportBundle}
                className="btn-secondary justify-center text-amber-300 border-amber-500/20"
              >
                <Archive size={13} />
                Support Bundle
              </button>
            </div>
          </div>

          {bundleResult && (
            <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2 text-emerald-400 text-sm space-y-1">
              <div className="flex items-center gap-2">
                <CheckCircle2 size={15} /> Bundle ready: <span className="font-mono text-xs truncate">{bundleResult.bundle_dir}</span>
              </div>
              {(bundleResult.mission_plan_path || bundleResult.qgc_plan_path) && (
                <div className="flex items-center gap-2 text-[11px] text-emerald-300/80">
                  <ClipboardCheck size={12} /> Mission plan files included in bundle
                </div>
              )}
              {bundleResult.terrain_index_path && (
                <div className="grid grid-cols-6 gap-2 pt-1 text-[11px] text-emerald-300/80">
                  <div>
                    <span className="block text-emerald-300/60">Tiles</span>
                    <span className="font-mono">
                      {bundleResult.geospatial_health?.tile_index?.tile_count ?? bundleResult.terrain_tile_count ?? "ready"}
                    </span>
                  </div>
                  <div>
                    <span className="block text-emerald-300/60">Features</span>
                    <span className="font-mono">
                      {(bundleResult.geospatial_health?.tile_index?.feature_count ?? bundleResult.terrain_feature_count)?.toLocaleString() ?? "ready"}
                    </span>
                  </div>
                  <div>
                    <span className="block text-emerald-300/60">GSD</span>
                    <span className="font-mono">
                      {(bundleResult.geospatial_health?.georef?.gsd_m ?? bundleResult.terrain_gsd_m) != null
                        ? `${(bundleResult.geospatial_health?.georef?.gsd_m ?? bundleResult.terrain_gsd_m ?? 0).toFixed(2)} m/px`
                        : "set"}
                    </span>
                  </div>
                  <div>
                    <span className="block text-emerald-300/60">Map health</span>
                    <span className="font-mono uppercase">
                      {bundleResult.geospatial_health?.status ?? "passed"}
                    </span>
                  </div>
                  <div>
                    <span className="block text-emerald-300/60">Pi cost</span>
                    <span className="font-mono uppercase">
                      {bundleResult.geospatial_health?.map_quality?.estimated_pi_runtime_cost
                        ?? bundleResult.geospatial_health?.tile_index?.quality?.estimated_pi_runtime_cost
                      ?? "low"}
                    </span>
                  </div>
                  <div>
                    <span className="block text-emerald-300/60">Elevation</span>
                    <span className="font-mono uppercase">
                      {elevationHealthLabel(bundleResult.geospatial_health?.elevation)}
                    </span>
                  </div>
                </div>
              )}
              <div className="grid grid-cols-3 gap-2 pt-1 text-[11px]">
                <div className="rounded-md border border-emerald-500/15 bg-bg-base/40 px-2 py-1.5 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-emerald-300/60">Checksums</span>
                    <span className={checksumBadgeClass(bundleResult.geospatial_health?.checksums?.status)}>
                      {formatHealthLabel(bundleResult.geospatial_health?.checksums?.status)}
                    </span>
                  </div>
                  <div className="font-mono text-emerald-300/80 truncate">
                    {(bundleResult.geospatial_health?.checksums?.covered_file_count
                      ?? bundleResult.geospatial_health?.checksums?.entry_count
                      ?? 0).toLocaleString()} files
                    {bundleResult.geospatial_health?.checksums?.extra_file_count
                      ? `, ${bundleResult.geospatial_health.checksums.extra_file_count} extra`
                      : ""}
                  </div>
                </div>
                <div className="rounded-md border border-emerald-500/15 bg-bg-base/40 px-2 py-1.5 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-emerald-300/60">Source</span>
                    <span className="font-mono text-emerald-300/80 truncate">
                      {formatHealthLabel(bundleResult.geospatial_health?.source_provenance?.map_source)}
                    </span>
                  </div>
                  <div className="font-mono text-emerald-300/80 truncate">
                    {bundleResult.geospatial_health?.source_provenance?.original_file
                      || bundleResult.geospatial_health?.source_provenance?.map_name
                      || bundleResult.geospatial_health?.source_provenance?.orthophoto_path
                      || "map source"}
                  </div>
                </div>
                <div className="rounded-md border border-emerald-500/15 bg-bg-base/40 px-2 py-1.5 min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-emerald-300/60">Terrain profile</span>
                    <span className="font-mono text-emerald-300/80 truncate">
                      {formatHealthLabel(bundleResult.geospatial_health?.terrain_profile?.status)}
                    </span>
                  </div>
                  <div className="font-mono text-emerald-300/80 truncate">
                    Min AGL {terrainProfileLabel(bundleResult.geospatial_health?.terrain_profile)}
                    {bundleResult.geospatial_health?.terrain_profile?.terrain_elevation_m?.relief != null
                      ? `, relief ${bundleResult.geospatial_health.terrain_profile.terrain_elevation_m.relief.toFixed(1)} m`
                      : ""}
                  </div>
                </div>
              </div>
              <TerrainProfilePreview profile={bundleResult.geospatial_health?.terrain_profile} />
              {bundleResult.geospatial_health?.map_quality?.heatmap?.cells?.length ? (
                <div className="rounded-md border border-emerald-500/15 bg-bg-base/40 px-2 py-1.5 space-y-1.5">
                  <div className="flex items-center justify-between gap-2 text-[11px]">
                    <span className="text-emerald-300/60">Map quality heatmap</span>
                    <span className="font-mono text-emerald-300/80">
                      {mapQualityPercent(bundleResult.geospatial_health.map_quality)}
                    </span>
                  </div>
                  <div
                    className="grid gap-0.5"
                    style={{
                      gridTemplateColumns: `repeat(${Math.max(1, bundleResult.geospatial_health.map_quality.heatmap.col_count ?? 1)}, minmax(4px, 1fr))`,
                    }}
                  >
                    {bundleResult.geospatial_health.map_quality.heatmap.cells.map((cell) => (
                      <div
                        key={cell.tile_id ?? `${cell.row}-${cell.col}`}
                        className={cn("h-2 rounded-[2px]", qualityCellClass(cell.quality))}
                        title={`${cell.tile_id ?? "tile"}: ${formatHealthLabel(cell.quality)} ${Math.round(cell.feature_density_per_mpx ?? 0)} features/Mpx`}
                      />
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-emerald-300/60">
                    <span><span className="inline-block h-2 w-2 rounded-[2px] bg-red-400/80 mr-1" />Low</span>
                    <span><span className="inline-block h-2 w-2 rounded-[2px] bg-amber-300/80 mr-1" />Fair</span>
                    <span><span className="inline-block h-2 w-2 rounded-[2px] bg-cyan-300/80 mr-1" />Good</span>
                    <span><span className="inline-block h-2 w-2 rounded-[2px] bg-emerald-300/80 mr-1" />Dense</span>
                    {bundleResult.geospatial_health.map_quality.heatmap.omitted_tile_count
                      ? <span>{bundleResult.geospatial_health.map_quality.heatmap.omitted_tile_count} hidden</span>
                      : null}
                  </div>
                </div>
              ) : null}
              {bundleResult.geospatial_health?.source_provenance && (
                <div className="grid grid-cols-3 gap-2 text-[11px] text-emerald-300/70">
                  <div>
                    <span className="block text-emerald-300/50">Georef</span>
                    <span className="font-mono truncate block">
                      {formatHealthLabel(bundleResult.geospatial_health.source_provenance.georef_source)}
                    </span>
                  </div>
                  <div>
                    <span className="block text-emerald-300/50">CRS</span>
                    <span className="font-mono truncate block">
                      {formatHealthLabel(bundleResult.geospatial_health.source_provenance.georef_crs)}
                    </span>
                  </div>
                  <div>
                    <span className="block text-emerald-300/50">Confidence</span>
                    <span className="font-mono">
                      {bundleResult.geospatial_health.source_provenance.georef_confidence != null
                        ? `${Math.round(bundleResult.geospatial_health.source_provenance.georef_confidence * 100)}%`
                        : "n/a"}
                    </span>
                  </div>
                </div>
              )}
              {bundleResult.geospatial_health?.issues?.slice(0, 2).map((issue) => (
                <div
                  key={`${issue.severity}-${issue.message}`}
                  className={issue.severity === "error" ? "text-[11px] text-red-300" : "text-[11px] text-amber-200"}
                >
                  {issue.message}
                </div>
              ))}
            </div>
          )}

          {error && (
            <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 text-red-400 text-xs whitespace-pre-wrap">
              {error}
            </div>
          )}

          {commandOutput && (
            <pre className="bg-bg-base border border-border rounded-lg px-3 py-2.5 text-[11px] font-mono text-slate-300 whitespace-pre-wrap max-h-72 overflow-y-auto leading-relaxed">
              {cmdRunning ? commandOutput + "..." : commandOutput}
            </pre>
          )}
          <SupportBundleList bundles={supportBundles} downloadDir={SUPPORT_DOWNLOAD_DIR} />
        </div>
      </div>
    </div>
  );
}
