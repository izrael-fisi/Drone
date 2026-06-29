import type {
  BBox,
  BuildDroneBundleRequest,
  BuildDroneBundleResult,
  CommandResult,
  Device,
  DownloadFileResult,
  DownloadProgress,
  DownloadTilesResult,
  DronePositionUpdate,
  EdgeApiDeviceStatus,
  EdgeApiHealth,
  EdgeApiMavlinkHeartbeat,
  EdgeApiMavlinkPosition,
  EdgeApiMissionPlannerLaunch,
  EdgeApiMissionPlannerStatus,
  EdgeApiQGroundControlLaunch,
  EdgeApiQGroundControlStatus,
  EdgeApiRuntimeStatus,
  ExtractedSupportBundleArtifact,
  FieldCollectionPlanFile,
  FieldEvidenceReportFile,
  FieldEvidenceTemplateFile,
  FieldLogCaptureReportFile,
  FeatureMethodBenchmarkReportFile,
  ImportElevationAssetsRequest,
  ImportElevationAssetsResult,
  ImportMapFileRequest,
  ImportMapFileResult,
  LocalNetworkHint,
  MapCoverageSurvey,
  MapCoverageSurveyRequest,
  MapDownloadRequest,
  MapProvider,
  MapProviderId,
  MapUsageEstimate,
  MapUsageEstimateRequest,
  PiDiscoveryCandidate,
  Profile,
  Px4PrereqReportFile,
  Px4ReceiverReportFile,
  RosbagExportValidationReportFile,
  Region,
  AutonomyEvidenceWorkflowReportFile,
  AutonomyReadinessReportFile,
  SupportBundleFile,
  SupportBundleDetails,
  ThresholdTuningReportFile,
  TileEstimate,
} from "./types";

const DEV_PROFILE: Profile = {
  accent_color: "#FF6600",
  email: "",
  name: "Izrael",
  onboarding_complete: true,
  org: "Drone Vision Nav",
  max_map_download_size_gb: 20,
};

const FALLBACK_MAP_PROVIDERS: MapProvider[] = [
  {
    id: "openfreemap-vector",
    label: "OpenFreeMap Vector",
    kind: "vector",
    url_template: "https://tiles.openfreemap.org/planet",
    tile_scheme: "vector",
    attribution: "OpenStreetMap contributors / OpenFreeMap",
    min_zoom: 0,
    max_native_zoom: 14,
    max_zoom: 23,
    requires_api_key: false,
    coverage_mode: "global-vector-labels",
    default_priority: 900,
    enabled: false,
    notes: "Vector labels and basemap only; not a high-resolution satellite source.",
    average_tile_kb: 35,
  },
  {
    id: "usgs-imagery",
    label: "USGS Imagery",
    kind: "raster",
    url_template: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
    tile_scheme: "arcgis",
    attribution: "USGS National Map",
    min_zoom: 0,
    max_native_zoom: 23,
    max_zoom: 23,
    requires_api_key: false,
    coverage_mode: "survey-required-us",
    default_priority: 10,
    enabled: true,
    notes: "Free U.S. imagery. Coverage and detail vary; use survey before large downloads.",
    average_tile_kb: 95,
  },
  {
    id: "esri-world-imagery",
    label: "Esri World Imagery",
    kind: "raster",
    url_template: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    tile_scheme: "arcgis",
    attribution: "Esri World Imagery",
    min_zoom: 0,
    max_native_zoom: 23,
    max_zoom: 23,
    requires_api_key: false,
    coverage_mode: "global-fallback",
    default_priority: 20,
    enabled: true,
    notes: "Global fallback imagery. High zoom availability varies by location.",
    average_tile_kb: 80,
  },
  {
    id: "mapbox-satellite",
    label: "Mapbox Satellite",
    kind: "raster",
    url_template: "https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}.jpg90?access_token={key}",
    tile_scheme: "zxy",
    attribution: "Mapbox / OpenStreetMap",
    min_zoom: 0,
    max_native_zoom: 22,
    max_zoom: 22,
    requires_api_key: true,
    coverage_mode: "paid-global",
    default_priority: 30,
    enabled: true,
    notes: "Optional paid/API-key provider.",
    average_tile_kb: 120,
  },
  {
    id: "bing-aerial",
    label: "Bing Aerial",
    kind: "raster",
    url_template: "https://t{s}.ssl.ak.tiles.virtualearth.net/tiles/a{q}.jpeg?g=7&token={key}",
    tile_scheme: "quadkey",
    attribution: "Microsoft Bing Maps",
    min_zoom: 0,
    max_native_zoom: 20,
    max_zoom: 20,
    requires_api_key: true,
    coverage_mode: "paid-global",
    default_priority: 40,
    enabled: true,
    notes: "Optional paid/API-key provider using quadkey addressing.",
    average_tile_kb: 105,
  },
  {
    id: "custom-zxy",
    label: "Custom Z/X/Y Tiles",
    kind: "custom",
    url_template: null,
    tile_scheme: "zxy",
    attribution: "Custom",
    min_zoom: 0,
    max_native_zoom: 23,
    max_zoom: 23,
    requires_api_key: false,
    coverage_mode: "custom",
    default_priority: 700,
    enabled: false,
    notes: "Template-ready slot for third-party raster tiles.",
    average_tile_kb: 100,
  },
  {
    id: "custom-arcgis",
    label: "Custom ArcGIS Tiles",
    kind: "custom",
    url_template: null,
    tile_scheme: "arcgis",
    attribution: "Custom",
    min_zoom: 0,
    max_native_zoom: 23,
    max_zoom: 23,
    requires_api_key: false,
    coverage_mode: "custom",
    default_priority: 710,
    enabled: false,
    notes: "Template-ready slot for third-party ArcGIS tiled services.",
    average_tile_kb: 100,
  },
  {
    id: "custom-wmts",
    label: "Custom WMTS",
    kind: "custom",
    url_template: null,
    tile_scheme: "wmts",
    attribution: "Custom",
    min_zoom: 0,
    max_native_zoom: 23,
    max_zoom: 23,
    requires_api_key: false,
    coverage_mode: "custom",
    default_priority: 720,
    enabled: false,
    notes: "Metadata slot for WMTS integration; downloader needs a concrete URL template.",
    average_tile_kb: 100,
  },
  {
    id: "pmtiles",
    label: "PMTiles Archive",
    kind: "archive",
    url_template: null,
    tile_scheme: "pmtiles",
    attribution: "Custom PMTiles",
    min_zoom: 0,
    max_native_zoom: 23,
    max_zoom: 23,
    requires_api_key: false,
    coverage_mode: "static-archive",
    default_priority: 730,
    enabled: false,
    notes: "Preferred static archive path for third-party geotiles and future offline packs.",
    average_tile_kb: 80,
  },
];

function hasTauriRuntime() {
  if (typeof window === "undefined") return false;
  const tauriInternals = (
    window as Window & { __TAURI_INTERNALS__?: { invoke?: unknown } }
  ).__TAURI_INTERNALS__;
  return typeof tauriInternals?.invoke === "function";
}

function readLocalJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
}

function readLocalArray<T>(key: string): T[] {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function writeLocalJson(key: string, value: unknown) {
  localStorage.setItem(key, JSON.stringify(value));
}

function canonicalProviderId(id: string): MapProviderId {
  if (id === "esri") return "esri-world-imagery";
  if (id === "mapbox") return "mapbox-satellite";
  if (id === "bing") return "bing-aerial";
  return id as MapProviderId;
}

function apiKeyForProvider(apiKeys: Record<string, string> | undefined, providerId: string) {
  if (!apiKeys) return "";
  return apiKeys[providerId] ?? apiKeys[providerId.replace(/-(satellite|aerial)$/, "")] ?? "";
}

function bboxAreaKm2Fallback(bbox: BBox): number {
  const latMin = Math.min(bbox.lat_min, bbox.lat_max);
  const latMax = Math.max(bbox.lat_min, bbox.lat_max);
  const crossesAntimeridian = bbox.lon_min > bbox.lon_max;
  const lonSpan = crossesAntimeridian ? 360 - (bbox.lon_min - bbox.lon_max) : Math.abs(bbox.lon_max - bbox.lon_min);
  const latCenter = ((latMin + latMax) / 2) * (Math.PI / 180);
  const ns = Math.abs(latMax - latMin) * 111.32;
  const ew = Math.min(360, Math.abs(lonSpan)) * 111.32 * Math.abs(Math.cos(latCenter));
  return ns * ew;
}

function polygonAreaKm2Fallback(points?: [number, number][]): number | null {
  if (!points || points.length < 3) return null;
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

function lonToTileX(lon: number, zoom: number) {
  const n = 2 ** Math.min(23, zoom);
  const wrapped = ((((lon + 180) % 360) + 360) % 360) - 180;
  return Math.max(0, Math.min(n - 1, Math.floor(((wrapped + 180) / 360) * n)));
}

function latToTileY(lat: number, zoom: number) {
  const n = 2 ** Math.min(23, zoom);
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat));
  const rad = clamped * Math.PI / 180;
  return Math.max(0, Math.min(n - 1, Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n)));
}

function fallbackTileShape(bbox: BBox, zoom: number) {
  const z = Math.min(23, Math.max(0, Math.round(zoom)));
  const xA = lonToTileX(bbox.lon_min, z);
  const xB = lonToTileX(bbox.lon_max, z);
  const yA = latToTileY(bbox.lat_min, z);
  const yB = latToTileY(bbox.lat_max, z);
  const yMin = Math.min(yA, yB);
  const yMax = Math.max(yA, yB);
  const height = yMax - yMin + 1;
  const width = bbox.lon_min <= bbox.lon_max
    ? Math.abs(xB - xA) + 1
    : (2 ** z - Math.max(xA, xB)) + Math.min(xA, xB) + 1;
  return { nx: Math.max(1, width), ny: Math.max(1, height), tileCount: Math.max(1, width * height), zoom: z };
}

function estimateMapUsageFallback(args?: Record<string, unknown>): MapUsageEstimate {
  const request = args?.request as MapUsageEstimateRequest | undefined;
  const bbox = request?.bbox ?? (args?.bbox as BBox | undefined) ?? { lat_min: 0, lat_max: 0, lon_min: 0, lon_max: 0 };
  const requestedZoom = Number(request?.zoom ?? args?.zoom ?? 16);
  const providerIds = request?.provider_ids?.length
    ? request.provider_ids.map((id) => canonicalProviderId(id))
    : ["usgs-imagery", "esri-world-imagery"];
  const providers = providerIds
    .map((id) => FALLBACK_MAP_PROVIDERS.find((provider) => provider.id === id))
    .filter((provider): provider is MapProvider => Boolean(provider))
    .sort((a, b) => a.default_priority - b.default_priority);
  const maxZoom = providers.length ? Math.max(...providers.map((provider) => provider.max_zoom)) : 23;
  const zoom = Math.min(maxZoom, Math.max(0, Math.round(requestedZoom)));
  const shape = fallbackTileShape(bbox, zoom);
  const areaKm2 = request?.cut_shape === "polygon"
    ? polygonAreaKm2Fallback(request.polygon_points) ?? bboxAreaKm2Fallback(bbox)
    : bboxAreaKm2Fallback(bbox);
  const latCenter = (bbox.lat_min + bbox.lat_max) / 2;
  const gsdMPerPx = 40075016.686 * Math.cos(Math.max(-85.05112878, Math.min(85.05112878, latCenter)) * Math.PI / 180) / (256 * 2 ** zoom);
  const warnings: string[] = [];
  if (areaKm2 > 100) warnings.push(`Selected area is ${areaKm2.toFixed(1)} km2. Downloads over 100 km2 require explicit confirmation.`);
  if (shape.tileCount > 5000) warnings.push(`Selected area covers ${shape.tileCount.toLocaleString()} tiles at zoom ${zoom}. Large downloads may be slow and memory intensive.`);
  const providerBreakdown = providers.map((provider) => {
    const overzoomed = zoom > provider.max_native_zoom;
    if (overzoomed) warnings.push(`${provider.label} is overzoomed above native zoom ${provider.max_native_zoom}.`);
    const sourceMb = shape.tileCount * provider.average_tile_kb / 1024;
    return {
      provider_id: provider.id,
      label: provider.label,
      tile_count: shape.tileCount,
      estimated_source_mb: sourceMb,
      estimated_disk_mb: sourceMb + shape.tileCount * 0.035,
      gsd_m_per_px: gsdMPerPx,
      overzoomed,
      key_required: provider.requires_api_key && !apiKeyForProvider(request?.api_keys, provider.id),
      enabled: provider.enabled && Boolean(provider.url_template),
    };
  });
  return {
    bbox,
    zoom,
    area_km2: areaKm2,
    tile_count: shape.tileCount,
    nx: shape.nx,
    ny: shape.ny,
    estimated_source_mb: providerBreakdown[0]?.estimated_source_mb ?? shape.tileCount * 0.08,
    estimated_disk_mb: providerBreakdown[0]?.estimated_disk_mb ?? shape.tileCount * 0.11,
    gsd_m_per_px: gsdMPerPx,
    too_large: shape.tileCount > 5000,
    over_100_km2: areaKm2 > 100,
    warnings,
    provider_breakdown: providerBreakdown,
  };
}

function estimateTilesFallback(args?: Record<string, unknown>): TileEstimate {
  const usage = estimateMapUsageFallback({
    request: {
      bbox: args?.bbox as BBox,
      zoom: Number(args?.zoom ?? 16),
      provider_ids: ["esri-world-imagery"],
    },
  });
  return {
    estimated_mb: usage.estimated_source_mb,
    gsd_m_per_px: usage.gsd_m_per_px,
    nx: usage.nx,
    ny: usage.ny,
    tile_count: usage.tile_count,
    too_large: usage.too_large,
  };
}

function surveyMapCoverageFallback(args?: Record<string, unknown>): MapCoverageSurvey {
  const request = args?.request as MapCoverageSurveyRequest;
  const providerIds = request.provider_ids?.length
    ? request.provider_ids.map((id) => canonicalProviderId(id))
    : ["usgs-imagery", "esri-world-imagery"];
  const providers = providerIds
    .map((id) => FALLBACK_MAP_PROVIDERS.find((provider) => provider.id === id))
    .filter((provider): provider is MapProvider => Boolean(provider));
  const minZoom = Math.min(request.min_zoom, request.max_zoom);
  const maxZoom = Math.max(request.min_zoom, request.max_zoom);
  const provider_results = providers.flatMap((provider) => {
    const keyMissing = provider.requires_api_key && !apiKeyForProvider(request.api_keys, provider.id);
    return Array.from({ length: maxZoom - minZoom + 1 }, (_, index) => {
      const zoom = minZoom + index;
      const usage = estimateMapUsageFallback({ request: { bbox: request.bbox, zoom, provider_ids: [provider.id], api_keys: request.api_keys } });
      const classification = keyMissing || !provider.enabled || !provider.url_template ? "missing" : "available";
      const sampleCount = Math.min(request.sample_budget ?? 24, usage.tile_count, 24);
      const qualityScore = classification === "available" ? (provider.id === "usgs-imagery" ? 0.66 : 0.58) : 0;
      return {
        provider_id: provider.id,
        label: provider.label,
        zoom,
        tile_count: usage.tile_count,
        sampled_count: sampleCount,
        available_count: classification === "available" ? sampleCount : 0,
        valid_count: classification === "available" ? Math.max(0, sampleCount - 2) : 0,
        missing_count: classification === "missing" ? sampleCount : 0,
        blank_count: 0,
        low_detail_count: classification === "available" ? Math.min(2, sampleCount) : 0,
        average_tile_kb: provider.average_tile_kb,
        quality_score: qualityScore,
        classification,
        samples: [],
      };
    });
  });
  return {
    id: `browser-survey-${Date.now()}`,
    bbox: request.bbox,
    min_zoom: minZoom,
    max_zoom: maxZoom,
    sample_budget: request.sample_budget ?? 24,
    generated_unix_ms: Date.now(),
    recommended_provider_order: provider_results
      .filter((result) => result.zoom === maxZoom)
      .sort((a, b) => b.quality_score - a.quality_score)
      .map((result) => result.provider_id),
    provider_results,
  };
}

function normalizeEdgeApiBaseUrl(value: unknown) {
  const raw = String(value ?? "").trim().replace(/\/+$/, "");
  if (!raw) throw new Error("Edge API URL is empty");
  return raw.startsWith("http://") || raw.startsWith("https://") ? raw : `http://${raw}`;
}

async function edgeApiFetch<T>(baseUrl: unknown, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${normalizeEdgeApiBaseUrl(baseUrl)}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) throw new Error(`Edge API returned HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

async function discoverPiDevicesFallback(args?: Record<string, unknown>): Promise<PiDiscoveryCandidate[]> {
  const seedHosts = Array.isArray(args?.seedHosts) ? args.seedHosts.map(String) : [];
  const savedHosts = readLocalArray<Device>("drone_dev_devices")
    .map((device) => device.host)
    .filter((host): host is string => Boolean(host));
  const hosts = Array.from(
    new Set([
      ...seedHosts,
      ...savedHosts,
      "dronecompute",
      "dronecompute.local",
      "raspberrypi.local",
      "raspberrypi",
    ].map((host) => host.trim()).filter(Boolean)),
  );
  const candidates = await Promise.all(
    hosts.map(async (host) => {
      const started = Date.now();
      try {
        const device = await edgeApiFetch<EdgeApiDeviceStatus>(`http://${host}:5000`, "/api/v1/device");
        const ip = device.ips?.find((item) => item.startsWith("192.168.")) ?? device.ips?.[0];
        const candidate: PiDiscoveryCandidate = {
          host,
          port: 22,
          source: "edge_api",
          ssh_open: true,
          resolved_ip: ip,
          ssh_banner: device.hostname ? `Edge API ${device.hostname}` : undefined,
          message: `Edge API online at ${device.hostname ?? host}`,
          last_seen_unix_ms: started,
        };
        return candidate;
      } catch {
        return null;
      }
    }),
  );
  return candidates.filter((candidate): candidate is PiDiscoveryCandidate => Boolean(candidate));
}

async function fallbackInvoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  switch (command) {
    case "load_profile":
      return readLocalJson("drone_dev_profile", DEV_PROFILE) as T;
    case "save_profile":
      writeLocalJson("drone_dev_profile", args?.profile ?? DEV_PROFILE);
      return undefined as T;
    case "load_devices":
      return readLocalArray<Device>("drone_dev_devices") as T;
    case "save_devices":
      writeLocalJson("drone_dev_devices", args?.devices ?? []);
      return undefined as T;
    case "load_regions":
      return readLocalArray<Region>("drone_dev_regions") as T;
    case "save_regions":
      writeLocalJson("drone_dev_regions", args?.regions ?? []);
      return undefined as T;
    case "list_map_providers":
      return FALLBACK_MAP_PROVIDERS as T;
    case "estimate_tiles":
      return estimateTilesFallback(args) as T;
    case "estimate_map_usage":
      return estimateMapUsageFallback(args) as T;
    case "survey_map_coverage":
      return surveyMapCoverageFallback(args) as T;
    case "download_map_region":
      throw new Error("Provider-aware map downloads require the Tauri desktop runtime.");
    case "discover_pi_devices":
      return discoverPiDevicesFallback(args) as T;
    case "local_network_hints":
    case "list_support_bundles":
      return [] as T;
    case "receive_position_update":
      return null as T;
    case "edge_api_health":
      return edgeApiFetch<T>(args?.baseUrl, "/health");
    case "edge_api_device":
      return edgeApiFetch<T>(args?.baseUrl, "/api/v1/device");
    case "edge_api_status":
      return edgeApiFetch<T>(args?.baseUrl, "/api/v1/status");
    case "edge_api_mavlink_heartbeat":
      return edgeApiFetch<T>(args?.baseUrl, "/api/v1/mavlink/heartbeat", {
        method: "POST",
        body: JSON.stringify({
          endpoint: args?.endpoint,
          timeout_s: args?.timeoutS ?? 4,
        }),
      });
    case "edge_api_mavlink_position":
      return edgeApiFetch<T>(args?.baseUrl, "/api/v1/mavlink/position", {
        method: "POST",
        body: JSON.stringify({
          endpoint: args?.endpoint,
          timeout_s: args?.timeoutS ?? 2,
          autopilot: args?.autopilot,
        }),
      });
    case "edge_api_qgroundcontrol_status":
      return edgeApiFetch<T>(args?.baseUrl, "/api/v1/qgroundcontrol");
    case "edge_api_qgroundcontrol_launch":
      return edgeApiFetch<T>(args?.baseUrl, "/api/v1/qgroundcontrol/launch", {
        method: "POST",
        body: JSON.stringify({
          stop_status_bridge: args?.stopStatusBridge ?? false,
        }),
      });
    case "edge_api_mission_planner_status":
      return edgeApiFetch<T>(args?.baseUrl, "/api/v1/mission-planner");
    case "edge_api_mission_planner_launch":
      return edgeApiFetch<T>(args?.baseUrl, "/api/v1/mission-planner/launch", {
        method: "POST",
        body: JSON.stringify({
          stop_status_bridge: args?.stopStatusBridge ?? false,
        }),
      });
    default:
      throw new Error(`Command ${command} requires the Tauri desktop runtime.`);
  }
}

function invokeCommand<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  if (!hasTauriRuntime()) {
    return fallbackInvoke<T>(command, args);
  }
  return import("@tauri-apps/api/core").then(({ invoke }) => invoke<T>(command, args));
}

export const cmd = {
  loadProfile: () => invokeCommand<Profile>("load_profile"),
  saveProfile: (profile: Profile) => invokeCommand<void>("save_profile", { profile }),
  loadDevices: () => invokeCommand<Device[]>("load_devices"),
  saveDevices: (devices: Device[]) => invokeCommand<void>("save_devices", { devices }),
  loadRegions: () => invokeCommand<Region[]>("load_regions"),
  saveRegions: (regions: Region[]) => invokeCommand<void>("save_regions", { regions }),
  listMapProviders: () => invokeCommand<MapProvider[]>("list_map_providers"),
  estimateTiles: (bbox: BBox, zoom: number) =>
    invokeCommand<TileEstimate>("estimate_tiles", { bbox, zoom }),
  estimateMapUsage: (request: MapUsageEstimateRequest) =>
    invokeCommand<MapUsageEstimate>("estimate_map_usage", { request }),
  surveyMapCoverage: (request: MapCoverageSurveyRequest) =>
    invokeCommand<MapCoverageSurvey>("survey_map_coverage", { request }),
  downloadTiles: (bbox: BBox, zoom: number, outputDir: string, source = "esri", apiKey?: string) =>
    invokeCommand<DownloadTilesResult>("download_tiles", { bbox, zoom, outputDir, source, apiKey }),
  downloadMapRegion: (request: MapDownloadRequest) =>
    invokeCommand<DownloadTilesResult>("download_map_region", { request }),
  buildDroneBundle: (request: BuildDroneBundleRequest) =>
    invokeCommand<BuildDroneBundleResult>("build_drone_bundle", { request }),
  importMapFile: (request: ImportMapFileRequest) =>
    invokeCommand<ImportMapFileResult>("import_map_file", { request }),
  importElevationAssets: (request: ImportElevationAssetsRequest) =>
    invokeCommand<ImportElevationAssetsResult>("import_elevation_assets", { request }),
  discoverPiDevices: (seedHosts: string[], port = 22) =>
    invokeCommand<PiDiscoveryCandidate[]>("discover_pi_devices", { seedHosts, port }),
  localNetworkHints: () => invokeCommand<LocalNetworkHint[]>("local_network_hints"),
  edgeApiHealth: (baseUrl: string) =>
    invokeCommand<EdgeApiHealth>("edge_api_health", { baseUrl }),
  edgeApiDevice: (baseUrl: string) =>
    invokeCommand<EdgeApiDeviceStatus>("edge_api_device", { baseUrl }),
  edgeApiStatus: (baseUrl: string) =>
    invokeCommand<EdgeApiRuntimeStatus>("edge_api_status", { baseUrl }),
  edgeApiMavlinkHeartbeat: (baseUrl: string, endpoint: string, timeoutS = 4) =>
    invokeCommand<EdgeApiMavlinkHeartbeat>("edge_api_mavlink_heartbeat", { baseUrl, endpoint, timeoutS }),
  edgeApiMavlinkPosition: (baseUrl: string, endpoint: string, timeoutS = 2, autopilot?: Device["autopilot"]) =>
    invokeCommand<EdgeApiMavlinkPosition>("edge_api_mavlink_position", { baseUrl, endpoint, timeoutS, autopilot }),
  edgeApiQGroundControlStatus: (baseUrl: string) =>
    invokeCommand<EdgeApiQGroundControlStatus>("edge_api_qgroundcontrol_status", { baseUrl }),
  edgeApiQGroundControlLaunch: (baseUrl: string, stopStatusBridge = false) =>
    invokeCommand<EdgeApiQGroundControlLaunch>("edge_api_qgroundcontrol_launch", { baseUrl, stopStatusBridge }),
  edgeApiMissionPlannerStatus: (baseUrl: string) =>
    invokeCommand<EdgeApiMissionPlannerStatus>("edge_api_mission_planner_status", { baseUrl }),
  edgeApiMissionPlannerLaunch: (baseUrl: string, stopStatusBridge = false) =>
    invokeCommand<EdgeApiMissionPlannerLaunch>("edge_api_mission_planner_launch", { baseUrl, stopStatusBridge }),
  testSshConnection: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"]
  ) => invokeCommand<{ ok: boolean; message: string; server_banner?: string; fingerprint?: string }>(
    "test_ssh_connection",
    { host, port, username, auth }
  ),
  sshRunCommand: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    command: string
  ) => invokeCommand<CommandResult>("ssh_run_command", { host, port, username, auth, command }),
  sshUploadFiles: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    localPaths: string[],
    remoteDir: string
  ) => invokeCommand<void>("ssh_upload_files", { host, port, username, auth, localPaths, remoteDir }),
  sshUploadDirectory: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    localDir: string,
    remoteDir: string
  ) => invokeCommand<void>("ssh_upload_directory", { host, port, username, auth, localDir, remoteDir }),
  sshUploadProject: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    localDir: string,
    remoteDir: string
  ) => invokeCommand<void>("ssh_upload_project", { host, port, username, auth, localDir, remoteDir }),
  sshDownloadFile: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    remotePath: string,
    localDir: string
  ) => invokeCommand<DownloadFileResult>("ssh_download_file", { host, port, username, auth, remotePath, localDir }),
  sshCaptureCameraFrame: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    remoteProjectPath: string,
    width: number,
    height: number,
    timeoutMs: number
  ) => invokeCommand<{
    mime_type: string;
    base64_data: string;
    remote_path: string;
    stdout: string;
    stderr: string;
  }>("ssh_capture_camera_frame", {
    host,
    port,
    username,
    auth,
    remoteProjectPath,
    width,
    height,
    timeoutMs,
  }),
  readYamlConfig: (path: string) => invokeCommand<Record<string, unknown>>("read_yaml_config", { path }),
  writeYamlConfig: (path: string, data: Record<string, unknown>) =>
    invokeCommand<void>("write_yaml_config", { path, data }),
  listYamlConfigs: (dir: string) => invokeCommand<string[]>("list_yaml_configs", { dir }),
  listAutonomyReadinessReports: (dir: string) =>
    invokeCommand<AutonomyReadinessReportFile[]>("list_autonomy_readiness_reports", { dir }),
  listAutonomyEvidenceWorkflowReports: (dir: string) =>
    invokeCommand<AutonomyEvidenceWorkflowReportFile[]>("list_autonomy_evidence_workflow_reports", { dir }),
  listFieldEvidenceReports: (dir: string) =>
    invokeCommand<FieldEvidenceReportFile[]>("list_field_evidence_reports", { dir }),
  listFieldCollectionPlans: (dir: string) =>
    invokeCommand<FieldCollectionPlanFile[]>("list_field_collection_plans", { dir }),
  listFieldEvidenceTemplates: (dir: string) =>
    invokeCommand<FieldEvidenceTemplateFile[]>("list_field_evidence_templates", { dir }),
  listFeatureMethodBenchmarkReports: (dir: string) =>
    invokeCommand<FeatureMethodBenchmarkReportFile[]>("list_feature_method_benchmark_reports", { dir }),
  listPx4PrereqReports: (dir: string) =>
    invokeCommand<Px4PrereqReportFile[]>("list_px4_prereq_reports", { dir }),
  listPx4ReceiverReports: (dir: string) =>
    invokeCommand<Px4ReceiverReportFile[]>("list_px4_receiver_reports", { dir }),
  listRosbagExportValidationReports: (dir: string) =>
    invokeCommand<RosbagExportValidationReportFile[]>("list_rosbag_export_validation_reports", { dir }),
  listFieldLogCaptureReports: (dir: string) =>
    invokeCommand<FieldLogCaptureReportFile[]>("list_field_log_capture_reports", { dir }),
  listThresholdTuningReports: (dir: string) =>
    invokeCommand<ThresholdTuningReportFile[]>("list_threshold_tuning_reports", { dir }),
  listSupportBundles: (dir: string) => invokeCommand<SupportBundleFile[]>("list_support_bundles", { dir }),
  revealSupportBundle: (path: string) => invokeCommand<void>("reveal_support_bundle", { path }),
  deleteSupportBundle: (path: string) => invokeCommand<void>("delete_support_bundle", { path }),
  runLocalAutonomyReadinessAudit: (repoDir: string, downloadRoot?: string) =>
    invokeCommand<CommandResult>("run_local_autonomy_readiness_audit", { repoDir, downloadRoot }),
  runLocalPx4SitlPrereqSetup: (repoDir: string, downloadRoot?: string) =>
    invokeCommand<CommandResult>("run_local_px4_sitl_prereq_setup", { repoDir, downloadRoot }),
  runLocalPx4SitlReceiverCapture: (repoDir: string, downloadRoot?: string) =>
    invokeCommand<CommandResult>("run_local_px4_sitl_receiver_capture", { repoDir, downloadRoot }),
  runLocalRosbag2CliReview: (repoDir: string, downloadRoot?: string) =>
    invokeCommand<CommandResult>("run_local_rosbag2_cli_review", { repoDir, downloadRoot }),
  readSupportBundleDetails: (path: string) =>
    invokeCommand<SupportBundleDetails>("read_support_bundle_details", { path }),
  extractSupportBundleArtifact: (path: string, entryPath: string) =>
    invokeCommand<ExtractedSupportBundleArtifact>("extract_support_bundle_artifact", { path, entryPath }),
  receivePositionUpdate: (port: number, timeoutMs = 250) =>
    invokeCommand<DronePositionUpdate | null>("receive_position_update", { port, timeoutMs }),
};

export type { DownloadProgress };
