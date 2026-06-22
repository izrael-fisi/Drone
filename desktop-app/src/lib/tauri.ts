import { invoke } from "@tauri-apps/api/core";
import type {
  BBox,
  BuildDroneBundleRequest,
  BuildDroneBundleResult,
  CommandResult,
  Device,
  DownloadFileResult,
  DownloadProgress,
  ExtractedSupportBundleArtifact,
  FieldCollectionPlanFile,
  FieldEvidenceReportFile,
  FieldEvidenceTemplateFile,
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

export const cmd = {
  loadProfile: () => invoke<Profile>("load_profile"),
  saveProfile: (profile: Profile) => invoke<void>("save_profile", { profile }),
  loadDevices: () => invoke<Device[]>("load_devices"),
  saveDevices: (devices: Device[]) => invoke<void>("save_devices", { devices }),
  loadRegions: () => invoke<Region[]>("load_regions"),
  saveRegions: (regions: Region[]) => invoke<void>("save_regions", { regions }),
  estimateTiles: (bbox: BBox, zoom: number) =>
    invoke<TileEstimate>("estimate_tiles", { bbox, zoom }),
  downloadTiles: (bbox: BBox, zoom: number, outputDir: string, source = "esri", apiKey?: string) =>
    invoke("download_tiles", { bbox, zoom, outputDir, source, apiKey }),
  buildDroneBundle: (request: BuildDroneBundleRequest) =>
    invoke<BuildDroneBundleResult>("build_drone_bundle", { request }),
  importMapFile: (request: ImportMapFileRequest) =>
    invoke<ImportMapFileResult>("import_map_file", { request }),
  importElevationAssets: (request: ImportElevationAssetsRequest) =>
    invoke<ImportElevationAssetsResult>("import_elevation_assets", { request }),
  discoverPiDevices: (seedHosts: string[], port = 22) =>
    invoke<PiDiscoveryCandidate[]>("discover_pi_devices", { seedHosts, port }),
  localNetworkHints: () => invoke<LocalNetworkHint[]>("local_network_hints"),
  testSshConnection: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"]
  ) => invoke<{ ok: boolean; message: string; server_banner?: string; fingerprint?: string }>(
    "test_ssh_connection",
    { host, port, username, auth }
  ),
  sshRunCommand: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    command: string
  ) => invoke<CommandResult>("ssh_run_command", { host, port, username, auth, command }),
  sshUploadFiles: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    localPaths: string[],
    remoteDir: string
  ) => invoke<void>("ssh_upload_files", { host, port, username, auth, localPaths, remoteDir }),
  sshUploadDirectory: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    localDir: string,
    remoteDir: string
  ) => invoke<void>("ssh_upload_directory", { host, port, username, auth, localDir, remoteDir }),
  sshUploadProject: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    localDir: string,
    remoteDir: string
  ) => invoke<void>("ssh_upload_project", { host, port, username, auth, localDir, remoteDir }),
  sshDownloadFile: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    remotePath: string,
    localDir: string
  ) => invoke<DownloadFileResult>("ssh_download_file", { host, port, username, auth, remotePath, localDir }),
  sshCaptureCameraFrame: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    remoteProjectPath: string,
    width: number,
    height: number,
    timeoutMs: number
  ) => invoke<{
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
  readYamlConfig: (path: string) => invoke<Record<string, unknown>>("read_yaml_config", { path }),
  writeYamlConfig: (path: string, data: Record<string, unknown>) =>
    invoke<void>("write_yaml_config", { path, data }),
  listYamlConfigs: (dir: string) => invoke<string[]>("list_yaml_configs", { dir }),
  listAutonomyReadinessReports: (dir: string) =>
    invoke<AutonomyReadinessReportFile[]>("list_autonomy_readiness_reports", { dir }),
  listAutonomyEvidenceWorkflowReports: (dir: string) =>
    invoke<AutonomyEvidenceWorkflowReportFile[]>("list_autonomy_evidence_workflow_reports", { dir }),
  listFieldEvidenceReports: (dir: string) =>
    invoke<FieldEvidenceReportFile[]>("list_field_evidence_reports", { dir }),
  listFieldCollectionPlans: (dir: string) =>
    invoke<FieldCollectionPlanFile[]>("list_field_collection_plans", { dir }),
  listFieldEvidenceTemplates: (dir: string) =>
    invoke<FieldEvidenceTemplateFile[]>("list_field_evidence_templates", { dir }),
  listFeatureMethodBenchmarkReports: (dir: string) =>
    invoke<FeatureMethodBenchmarkReportFile[]>("list_feature_method_benchmark_reports", { dir }),
  listPx4PrereqReports: (dir: string) =>
    invoke<Px4PrereqReportFile[]>("list_px4_prereq_reports", { dir }),
  listPx4ReceiverReports: (dir: string) =>
    invoke<Px4ReceiverReportFile[]>("list_px4_receiver_reports", { dir }),
  listRosbagExportValidationReports: (dir: string) =>
    invoke<RosbagExportValidationReportFile[]>("list_rosbag_export_validation_reports", { dir }),
  listThresholdTuningReports: (dir: string) =>
    invoke<ThresholdTuningReportFile[]>("list_threshold_tuning_reports", { dir }),
  listSupportBundles: (dir: string) => invoke<SupportBundleFile[]>("list_support_bundles", { dir }),
  revealSupportBundle: (path: string) => invoke<void>("reveal_support_bundle", { path }),
  deleteSupportBundle: (path: string) => invoke<void>("delete_support_bundle", { path }),
  runLocalAutonomyReadinessAudit: (repoDir: string, downloadRoot?: string) =>
    invoke<CommandResult>("run_local_autonomy_readiness_audit", { repoDir, downloadRoot }),
  runLocalPx4SitlReceiverCapture: (repoDir: string, downloadRoot?: string) =>
    invoke<CommandResult>("run_local_px4_sitl_receiver_capture", { repoDir, downloadRoot }),
  runLocalRosbag2CliReview: (repoDir: string, downloadRoot?: string) =>
    invoke<CommandResult>("run_local_rosbag2_cli_review", { repoDir, downloadRoot }),
  readSupportBundleDetails: (path: string) =>
    invoke<SupportBundleDetails>("read_support_bundle_details", { path }),
  extractSupportBundleArtifact: (path: string, entryPath: string) =>
    invoke<ExtractedSupportBundleArtifact>("extract_support_bundle_artifact", { path, entryPath }),
};

export type { DownloadProgress };
