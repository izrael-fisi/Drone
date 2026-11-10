export interface Profile {
  name: string;
  email: string;
  org: string;
  accent_color: string;
  onboarding_complete: boolean;
  mapbox_key?: string;
  bing_key?: string;
  max_map_area_km2?: number;
  max_map_download_size_gb?: number;
  // Proxigo cloud auth — persisted across restarts
  proxigo_access_token?: string;
  proxigo_refresh_token?: string;
  proxigo_token_expires_at?: number;
  proxigo_user_id?: string;
  proxigo_email?: string;
  proxigo_module_serial?: string;
}

export type BuiltInMapProviderId =
  | "openfreemap-vector"
  | "usgs-imagery"
  | "esri-world-imagery"
  | "mapbox-satellite"
  | "bing-aerial"
  | "custom-zxy"
  | "custom-arcgis"
  | "custom-wmts"
  | "pmtiles"
  | "esri"
  | "mapbox"
  | "bing";
export type MapProviderId = BuiltInMapProviderId | (string & {});
export type MapSource = MapProviderId | "uploaded" | "folder";

export interface MapProvider {
  id: MapProviderId;
  label: string;
  kind: "raster" | "vector" | "custom" | "archive" | string;
  url_template?: string | null;
  tile_scheme: "zxy" | "arcgis" | "quadkey" | "vector" | "wmts" | "pmtiles" | string;
  attribution: string;
  min_zoom: number;
  max_native_zoom: number;
  max_zoom: number;
  requires_api_key: boolean;
  coverage_mode: string;
  default_priority: number;
  enabled: boolean;
  notes: string;
  average_tile_kb: number;
}

export interface MapProviderBreakdown {
  provider_id: MapProviderId;
  label: string;
  tile_count: number;
  estimated_source_mb: number;
  estimated_disk_mb: number;
  gsd_m_per_px: number;
  overzoomed: boolean;
  key_required: boolean;
  enabled: boolean;
}

export interface MapUsageEstimate {
  bbox: BBox;
  zoom: number;
  area_km2: number;
  tile_count: number;
  nx: number;
  ny: number;
  estimated_source_mb: number;
  estimated_disk_mb: number;
  gsd_m_per_px: number;
  too_large: boolean;
  over_100_km2: boolean;
  warnings: string[];
  provider_breakdown: MapProviderBreakdown[];
}

export interface MapUsageEstimateRequest {
  bbox: BBox;
  zoom: number;
  cut_shape?: "box" | "polygon" | string;
  polygon_points?: [number, number][];
  provider_ids?: MapProviderId[];
  custom_providers?: MapProvider[];
  api_keys?: Record<string, string>;
}

export interface MapCoverageSample {
  provider_id: MapProviderId;
  zoom: number;
  x: number;
  y: number;
  status: number;
  classification: "available" | "missing" | "blank" | "low-detail" | "valid" | string;
  byte_size: number;
  quality_score: number;
  error?: string | null;
}

export interface MapCoverageProviderZoom {
  provider_id: MapProviderId;
  label: string;
  zoom: number;
  tile_count: number;
  sampled_count: number;
  available_count: number;
  valid_count: number;
  missing_count: number;
  blank_count: number;
  low_detail_count: number;
  average_tile_kb: number;
  quality_score: number;
  classification: "available" | "missing" | "blank" | "low-detail" | "valid" | string;
  samples: MapCoverageSample[];
}

export interface MapCoverageSurvey {
  id: string;
  bbox: BBox;
  min_zoom: number;
  max_zoom: number;
  sample_budget: number;
  generated_unix_ms: number;
  recommended_provider_order: MapProviderId[];
  provider_results: MapCoverageProviderZoom[];
}

export interface MapDownloadRequest {
  bbox: BBox;
  zoom: number;
  min_zoom?: number;
  multi_layer_map?: boolean;
  output_dir: string;
  cut_shape?: "box" | "polygon" | string;
  polygon_points?: [number, number][];
  provider_ids?: MapProviderId[];
  custom_providers?: MapProvider[];
  api_keys?: Record<string, string>;
  coverage_survey?: MapCoverageSurvey | null;
  confirm_over_100_km2?: boolean;
  allow_large_tile_count?: boolean;
}

export type DeviceKind = "pi5" | "local";
export type VisionPipeline = "pi5_crossview_nav_v1";
export type FeatureMethod = "xfeat_lighterglue";
export type RuntimeProfileId = "pi5_full" | "pi5_low_memory" | "desktop_high_compute";
export type CameraProfileId = "rgb_global_shutter" | "rgb_rolling_shutter" | "thermal_low_res" | "eo_generic";
export type MapLifecycleState = "local" | "org" | "built" | "uploaded" | "active" | "stale" | "failed";
export type PositionSourceState =
  | "gps_primary"
  | "vision_correction"
  | "dead_reckoning_between_fixes"
  | "gps_degraded"
  | "no_position"
  | string;

export interface Device {
  id: string;
  name: string;
  kind: DeviceKind;
  host?: string;
  port?: number;
  username?: string;
  auth?: { type: "Password"; password: string } | { type: "Key"; key_path: string; passphrase?: string };
  remote_project_path?: string;
  known_fingerprint?: string;
  mavlink_endpoint?: string; // e.g. "serial:/dev/ttyAMA0:921600" | "udp:14550" | "tcp:host:port"
  autopilot?: "px4" | "ardupilot";
  vision_pipeline?: VisionPipeline;
  feature_method?: FeatureMethod;
  runtime_profile?: RuntimeProfileId;
  camera_profile?: CameraProfileId;
  hardware_profile?: {
    module_weight_g?: number | null;
    estimated_bom_usd?: number | null;
    camera_cost_usd?: number | null;
    sensor_compliance_notes?: string;
    mount_vibration_notes?: string;
  };
}

export interface EdgeApiServiceStatus {
  unit: string;
  active: string;
  enabled: string;
  properties?: Record<string, string>;
  errors?: string[];
}

export interface EdgeApiDeviceStatus {
  ok: boolean;
  schema_version?: string;
  timestamp_utc?: string;
  hostname?: string;
  fqdn?: string;
  ips?: string[];
  user?: string;
  home?: string;
  repo_root?: string;
  default_mavlink_endpoint?: string;
  default_serial_baud?: number;
  serial_devices?: Array<{
    path: string;
    resolved_path?: string | null;
    is_symlink?: boolean;
    mode?: string;
    group_read_write?: boolean;
  }>;
  os?: Record<string, string>;
  services?: Record<string, EdgeApiServiceStatus>;
  error?: string;
  message?: string;
}

export interface EdgeApiMavlinkHeartbeat {
  ok: boolean;
  connected?: boolean;
  endpoint?: string;
  status?: "heartbeat" | "timeout" | "error" | string;
  message?: string;
  target_system?: number | null;
  target_component?: number | null;
  duration_s?: number;
  heartbeat?: {
    type?: number | null;
    autopilot?: number | null;
    base_mode?: number | null;
    system_status?: number | null;
    mavlink_version?: number | null;
  };
  error?: string;
}

export interface EdgeApiQGroundControlStatus {
  ok: boolean;
  installed: boolean;
  executable_path?: string | null;
  appimage_path?: string | null;
  safe_wrapper_path?: string | null;
  display?: {
    available: boolean;
    display?: string | null;
    wayland_display?: string | null;
    session_type?: string | null;
  };
  running?: boolean;
  processes?: string[];
  serial_endpoint?: string | null;
  serial_users?: string;
  launch_available?: boolean;
  message?: string;
  error?: string;
}

export interface EdgeApiQGroundControlLaunch {
  ok: boolean;
  launched?: boolean;
  pid?: number | null;
  status?: EdgeApiQGroundControlStatus;
  command?: Record<string, unknown>;
  message?: string;
  error?: string;
}

export interface EdgeApiMissionPlannerStatus {
  ok: boolean;
  installed: boolean;
  executable_path?: string | null;
  mono_path?: string | null;
  install_path?: string | null;
  display?: {
    available: boolean;
    display?: string | null;
    wayland_display?: string | null;
    session_type?: string | null;
  };
  running?: boolean;
  processes?: string[];
  serial_endpoint?: string | null;
  serial_users?: string;
  launch_available?: boolean;
  compatibility?: "windows-native" | "mono-experimental" | "not-installed" | string;
  message?: string;
  error?: string;
}

export interface EdgeApiMissionPlannerLaunch {
  ok: boolean;
  launched?: boolean;
  pid?: number | null;
  status?: EdgeApiMissionPlannerStatus;
  command?: Record<string, unknown>;
  message?: string;
  error?: string;
}

export interface Region {
  id: string;
  name: string;
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
  zoom: number;
  source?: MapSource;
  output_path: string;
  last_downloaded?: string;
  tile_count?: number;
  gsd_m_per_px?: number;
  min_zoom?: number;
  zoom_levels?: number[];
  multi_layer_map?: boolean;
  georef_source?: string;
  georef_confidence?: number;
  georef_crs?: string;
  file_size_mb?: number;
  location_label?: string;
  elevation_dem_path?: string;
  elevation_dsm_path?: string;
  elevation_asset_count?: number;
  lifecycle_state?: MapLifecycleState;
  active_bundle_path?: string;
  active_bundle_state?: "configured" | "active" | "missing" | "failed" | string;
  map_age_or_season_notes?: string;
  feature_count?: number;
  weak_feature_regions?: string[];
  estimated_pi_runtime_cost?: "low" | "moderate" | "high" | string;
  runtime_profile?: RuntimeProfileId;
  camera_profile?: CameraProfileId;
  home_position?: {
    lat: number;
    lon: number;
    alt_m?: number | null;
  };
  takeoff_position?: {
    lat: number;
    lon: number;
    alt_m?: number | null;
  };
  runtime_state?: {
    uploaded_at?: string;
    active_on_device?: boolean;
    last_error?: string;
  };
  cut_shape?: "box" | "polygon" | string;
  polygon_points?: [number, number][];
}

export interface SavedMission {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  source: "dashboard" | "mission-planner" | string;
  map_id?: string;
  map_label?: string;
  center: [number, number];
  zoom: number;
  border_points: [number, number][];
  waypoints: [number, number][];
  bounds?: {
    lat_min: number;
    lat_max: number;
    lon_min: number;
    lon_max: number;
  };
}

export interface FlightRecord {
  id: string;
  name: string;
  source: "device" | "edge" | "local" | string;
  started_at?: string;
  duration_s?: number;
  support_bundle_path?: string;
  gps_vs_vision_median_distance_m?: number;
  notes?: string;
}

export interface DownloadTilesResult {
  mosaic_path: string;
  metadata_path: string;
  coverage_manifest_path?: string | null;
  width_px: number;
  height_px: number;
  gsd_m_per_px: number;
  origin_lat: number;
  origin_lon: number;
  tile_count: number;
  georef_source: string;
  georef_confidence: number;
  georef_crs: string;
  actual_mb?: number;
  provider_tile_counts?: Record<string, number>;
}

export interface DownloadProgress {
  current: number;
  total: number;
  percent: number;
  tile_x: number;
  tile_y: number;
}

export interface DronePositionUpdate {
  schema_version: "vision_nav_position_update_v1" | "vision_nav_position_update_v2";
  timestamp_utc?: string;
  sequence?: number;
  status?: "accepted" | "degraded" | "unavailable" | string;
  source?: "gps" | "vision" | "gps_degraded" | "none" | string;
  source_state?: PositionSourceState;
  source_transition_reason?: string;
  source_priority?: string;
  lat_lon?: { lat?: number | null; lon?: number | null };
  altitude_m?: number | null;
  local_enu_m?: { x?: number | null; y?: number | null; z?: number | null };
  confidence?: number | null;
  covariance?: Record<string, number | null>;
  last_vision_fix_utc?: string | null;
  seconds_since_vision_fix?: number | null;
  meters_since_vision_fix?: number | null;
  vision_fix_interval_m?: number | null;
  dead_reckoning_active?: boolean;
  fix_cadence?: {
    last_vision_fix_utc?: string | null;
    last_vision_fix_sequence?: number | null;
    seconds_since_vision_fix?: number | null;
    meters_since_vision_fix?: number | null;
    vision_fix_interval_m?: number | null;
  };
  gps_health?: {
    healthy?: boolean;
    reason?: string;
    fix_type?: number | null;
    satellites_visible?: number | null;
    eph_m?: number | null;
    h_acc_m?: number | null;
    confidence?: number | null;
  };
  vision_health?: {
    available?: boolean;
    status?: string;
    confidence?: number | null;
    tile_id?: string | null;
    inliers?: number | null;
    reprojection_error_px?: number | null;
  };
  learned_runtime?: {
    profile_id?: string | null;
    model_release_id?: string | null;
    provider?: string | null;
    provider_ready?: boolean | null;
    navigation_mode?: "TRACKING" | "RECOVERING" | "NO_GLOBAL_FIX" | string;
    shadow_mode?: boolean | null;
    compatibility?: { compatible?: boolean; reasons?: string[] } | null;
  };
}

export interface EdgeApiMavlinkPosition extends DronePositionUpdate {
  ok?: boolean;
  endpoint?: string;
  message?: string;
  duration_s?: number;
  autopilot?: "px4" | "ardupilot" | "unknown" | string;
  mavlink?: {
    endpoint?: string;
    message_type?: string;
    autopilot?: "px4" | "ardupilot" | "unknown" | string;
    duration_s?: number;
  };
}

export interface PiDiscoveryCandidate {
  host: string;
  port: number;
  source: "saved" | "mdns" | "arp" | string;
  ssh_open: boolean;
  resolved_ip?: string;
  ssh_banner?: string;
  message: string;
  last_seen_unix_ms: number;
}

export interface LocalNetworkHint {
  interface_name: string;
  ipv4: string;
  network_hint: string;
  source: string;
  likely_active: boolean;
}

export interface BBox {
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
}
