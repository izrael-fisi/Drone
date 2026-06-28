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
};

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

function estimateTilesFallback(args?: Record<string, unknown>): TileEstimate {
  const bbox = args?.bbox as BBox | undefined;
  const zoom = Number(args?.zoom ?? 16);
  const latSpan = bbox ? Math.max(0, bbox.lat_max - bbox.lat_min) : 0;
  const lonSpan = bbox ? Math.max(0, bbox.lon_max - bbox.lon_min) : 0;
  const scale = Math.max(1, 2 ** Math.max(0, zoom - 12));
  const nx = Math.max(1, Math.ceil(lonSpan * scale * 12));
  const ny = Math.max(1, Math.ceil(latSpan * scale * 12));
  const tile_count = nx * ny;
  return {
    estimated_mb: tile_count * 0.18,
    gsd_m_per_px: 156543.03392 / 2 ** zoom,
    nx,
    ny,
    tile_count,
    too_large: tile_count > 5000,
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
    case "estimate_tiles":
      return estimateTilesFallback(args) as T;
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
  estimateTiles: (bbox: BBox, zoom: number) =>
    invokeCommand<TileEstimate>("estimate_tiles", { bbox, zoom }),
  downloadTiles: (bbox: BBox, zoom: number, outputDir: string, source = "esri", apiKey?: string) =>
    invokeCommand<DownloadTilesResult>("download_tiles", { bbox, zoom, outputDir, source, apiKey }),
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
