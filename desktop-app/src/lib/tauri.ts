import { invoke } from "@tauri-apps/api/core";
import type { Device, DownloadProgress, DownloadTilesResult, DronePositionUpdate, EdgeApiDeviceStatus, EdgeApiMavlinkHeartbeat, EdgeApiMavlinkPosition, EdgeApiMissionPlannerLaunch, EdgeApiMissionPlannerStatus, EdgeApiQGroundControlLaunch, EdgeApiQGroundControlStatus, LocalNetworkHint, MapDownloadRequest, MapProvider, MapUsageEstimate, MapUsageEstimateRequest, PiDiscoveryCandidate, Profile, Region } from "./types";

/** Rust commands. The app runs only inside Tauri; there is no browser fallback. */
const invokeCommand = <T,>(command: string, args?: Record<string, unknown>) => invoke<T>(command, args);

export const cmd = {
  loadProfile: () => invokeCommand<Profile>("load_profile"),
  saveProfile: (profile: Profile) => invokeCommand<void>("save_profile", { profile }),
  loadDevices: () => invokeCommand<Device[]>("load_devices"),
  saveDevices: (devices: Device[]) => invokeCommand<void>("save_devices", { devices }),
  loadRegions: () => invokeCommand<Region[]>("load_regions"),
  loadProjects: () => invokeCommand<unknown>("load_projects"),
  saveProjects: (document: unknown) => invokeCommand<void>("save_projects", { document }),
  saveRegions: (regions: Region[]) => invokeCommand<void>("save_regions", { regions }),
  listMapProviders: () => invokeCommand<MapProvider[]>("list_map_providers"),
  estimateMapUsage: (request: MapUsageEstimateRequest) =>
    invokeCommand<MapUsageEstimate>("estimate_map_usage", { request }),
  downloadMapRegion: (request: MapDownloadRequest) =>
    invokeCommand<DownloadTilesResult>("download_map_region", { request }),
  discoverPiDevices: (seedHosts: string[], port = 22) =>
    invokeCommand<PiDiscoveryCandidate[]>("discover_pi_devices", { seedHosts, port }),
  localNetworkHints: () => invokeCommand<LocalNetworkHint[]>("local_network_hints"),
  edgeApiDevice: (baseUrl: string) =>
    invokeCommand<EdgeApiDeviceStatus>("edge_api_device", { baseUrl }),
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
  receivePositionUpdate: (port: number, timeoutMs = 250) =>
    invokeCommand<DronePositionUpdate | null>("receive_position_update", { port, timeoutMs }),
};

export type { DownloadProgress };
