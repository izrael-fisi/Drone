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
  accent_color: "#06B6D4",
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
    case "local_network_hints":
    case "discover_pi_devices":
    case "list_support_bundles":
      return [] as T;
    case "receive_position_update":
      return null as T;
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
