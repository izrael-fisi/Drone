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
export type TileSource = MapProviderId;
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

export interface MapCoverageSurveyRequest {
  bbox: BBox;
  min_zoom: number;
  max_zoom: number;
  cut_shape?: "box" | "polygon" | string;
  polygon_points?: [number, number][];
  provider_ids?: MapProviderId[];
  sample_budget?: number;
  custom_providers?: MapProvider[];
  api_keys?: Record<string, string>;
}

export interface MapPatchTileRecord {
  x: number;
  y: number;
  zoom: number;
  provider_id?: MapProviderId | null;
  classification: string;
  byte_size: number;
  fallback_reason?: string | null;
}

export interface MapPatchManifest {
  schema_version: string;
  bbox: BBox;
  cut_shape?: "box" | "polygon" | string | null;
  polygon_points?: [number, number][] | null;
  zoom: number;
  area_km2: number;
  provider_ids: MapProviderId[];
  survey_id?: string | null;
  min_zoom?: number;
  zoom_levels?: number[];
  multi_layer_map?: boolean;
  tile_count: number;
  actual_mb: number;
  provider_tile_counts: Record<string, number>;
  failed_tiles: MapPatchTileRecord[];
  tile_sources: MapPatchTileRecord[];
  generated_assets: string[];
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
export type AuthMethod = "password" | "key";
export type VisionPipeline = "classical" | "neural";
export type FeatureMethod = "orb" | "akaze" | "sift";
export type RuntimeProfileId = "pi5_full" | "pi5_low_memory" | "desktop_high_compute";
export type CameraProfileId = "rgb_global_shutter" | "rgb_rolling_shutter" | "thermal_low_res" | "eo_generic";
export type MapLifecycleState = "local" | "built" | "uploaded" | "active" | "stale" | "failed";
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

export interface EdgeApiHealth {
  ok: boolean;
  schema_version?: string;
  service?: string;
  timestamp_utc?: string;
  error?: string;
  message?: string;
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

export interface EdgeApiRuntimeStatus {
  ok: boolean;
  status_found?: boolean;
  path?: string;
  size_bytes?: number;
  modified_unix_ms?: number;
  roots?: string[];
  status?: Record<string, unknown> | null;
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

export interface VehicleConfig {
  id: string;
  device_id: string;
  mount_offset_m?: { x: number; y: number; z: number };
  yaw_offset_deg?: number;
  mavlink_source?: string;
  updated_at?: string;
}

export interface CameraConfig {
  id: string;
  device_id?: string;
  profile: CameraProfileId | string;
  calibration_state: "not_started" | "capturing" | "ready" | "failed" | string;
  intrinsics?: Record<string, number>;
  updated_at?: string;
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

export interface RuntimeConnectionState {
  device_id?: string;
  connected: boolean;
  product_state: "running" | "stopped" | "server_error" | "disconnected" | string;
  mavlink_source?: string;
  message?: string;
}

export interface RecordingState {
  active: boolean;
  started_at?: string;
  storage_path?: string;
  local_only?: boolean;
}

export interface ModelSet {
  id: string;
  name: string;
  superpoint_path: string;
  lightglue_path: string;
  is_active: boolean;
  downloaded: boolean;
}

export interface TileEstimate {
  tile_count: number;
  nx: number;
  ny: number;
  estimated_mb: number;
  gsd_m_per_px: number;
  too_large: boolean;
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

export interface UploadProgress {
  file: string;
  bytes_sent: number;
  total_bytes: number;
  percent: number;
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

export interface DownloadFileResult {
  remote_path: string;
  local_path: string;
  bytes_received: number;
}

export interface FieldCollectionPlanCondition {
  condition?: string;
  label?: string;
  expected?: "good_map" | "degraded" | "wrong_map" | string;
  status?: "registered" | "registered_missing_log" | "placeholder" | "missing" | string;
  notes?: string;
  case_name?: string;
  manifest_log_path?: string;
  manifest_log_exists?: boolean;
  source_log?: string;
  legacy_source_log?: string;
  capture_output_dir?: string;
  runtime_status_path?: string;
  field_log_capture_report?: string;
  has_capture_command?: boolean;
  has_preflight_command?: boolean;
  has_preflight_capture_command?: boolean;
  has_metadata_update_command?: boolean;
  has_register_command?: boolean;
  preflight_command?: string;
  preflight_capture_command?: string;
  capture_command?: string;
  metadata_update_command?: string;
  bundle?: string;
  capture_metadata?: Record<string, unknown>;
  register_command?: string;
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

export interface SupportBundleFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  summary?: {
    bundle_id?: string;
    bundle_health_status?: "passed" | "degraded" | "failed" | string;
    checksum_status?: "missing" | "passed" | "failed" | string;
    covered_file_count?: number;
    elevation_status?: "not_provided" | "passed" | "degraded" | "failed" | string;
    elevation_asset_count?: number;
    vertical_sanity_ready?: boolean;
    map_source?: string;
    source_name?: string;
    georef_source?: string;
    georef_crs?: string;
    georef_confidence?: number;
    replay_gate_status?: "passed" | "failed" | "degraded" | string;
    replay_case_count?: number;
    gnss_denied_plan_status?: "passed" | "failed" | "degraded" | string;
    gnss_denied_plan_check_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    gnss_denied_plan_check_report_count?: number;
    gnss_denied_plan_check_missing_count?: number;
    px4_sitl_evidence_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    px4_sitl_sample_count?: number;
    px4_sitl_prereq_status?: "passed" | "failed" | "degraded" | "not_checked" | "not_provided" | string;
    px4_params_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    px4_ev_ctrl?: number;
    ardupilot_params_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    ardupilot_source_set?: number;
    ardupilot_posxy_source?: number;
    feature_method_benchmark_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    feature_method_benchmark_recommended?: string;
    feature_method_benchmark_report_count?: number;
    field_evidence_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    field_evidence_field_case_count?: number;
    field_evidence_capture_metadata_issue_count?: number;
    field_evidence_report_count?: number;
    field_collection_plan_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    field_collection_plan_registered_count?: number;
    field_collection_plan_required_count?: number;
    field_collection_plan_report_count?: number;
    field_collection_plan_pending_capture_command_count?: number;
    field_collection_plan_pending_metadata_update_command_count?: number;
    field_collection_plan_pending_registration_command_count?: number;
    field_collection_plan_capture_output_dir_count?: number;
    field_collection_plan_runtime_status_path_count?: number;
    field_collection_plan_condition_source_log_count?: number;
    field_capture_preflight_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    field_capture_preflight_report_count?: number;
    field_capture_preflight_ready_for_capture_count?: number;
    field_capture_preflight_ready_for_registration_count?: number;
    field_capture_preflight_failed_check_count?: number;
    field_capture_preflight_degraded_check_count?: number;
    field_capture_preflight_next_action_count?: number;
    field_capture_preflight_blocked_action_count?: number;
    threshold_tuning_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    threshold_tuning_field_case_count?: number;
    threshold_tuning_capture_metadata_issue_count?: number;
    threshold_tuning_report_count?: number;
    rosbag_export_validation_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    rosbag_export_validation_report_count?: number;
    rosbag_export_validation_message_count?: number;
    rosbag_export_validation_topic_count?: number;
    rosbag2_cli_review_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    rosbag2_cli_review_report_count?: number;
    evidence_workflow_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    evidence_workflow_validation_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    evidence_workflow_runtime_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    evidence_workflow_provenance_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    evidence_workflow_step_count?: number;
    evidence_workflow_issue_count?: number;
    evidence_workflow_repo_commit?: string;
    bench_readiness_status?: "passed" | "failed" | "degraded" | string;
    bench_readiness_failed_count?: number;
    bench_readiness_degraded_count?: number;
    flight_evidence_total_distance_m?: number;
    flight_evidence_max_altitude_m?: number;
    flight_evidence_duration_s?: number;
    accepted_vision_fix_count?: number;
    rejected_vision_fix_count?: number;
    gps_vs_vision_median_distance_m?: number;
    dead_reckoning_duration_s?: number;
    source_transition_count?: number;
  };
}

export interface SupportBundleDetails {
  manifest: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  bundle_health?: Record<string, unknown>;
  entry_count: number;
  artifacts: Array<{
    name: string;
    path: string;
    kind: string;
    size_bytes: number;
  }>;
  logs: Array<{
    name: string;
    total_records?: number;
    accepted_rate?: number;
    status_counts?: Record<string, number>;
    reason_counts?: Record<string, number>;
    external_position?: Record<string, unknown>;
  }>;
  runtime_statuses: Array<Record<string, unknown>>;
  log_previews: Array<{
    name: string;
    truncated: boolean;
    records: Array<{
      line_number: number;
      sequence?: number;
      timestamp_utc?: string;
      timestamp_us?: number;
      status?: string;
      reason?: string;
      tile_id?: string;
      map_id?: string;
      confidence?: number;
      inliers?: number;
      reprojection_error_px?: number;
      external_position_status?: string;
      external_position_message_type?: string;
      external_position_warnings?: string[];
    }>;
  }>;
  log_timelines: Array<{
    name: string;
    path: string;
    size_bytes: number;
    total_records?: number;
    invalid_records: number;
    accepted_rate?: number;
    status_counts?: Record<string, number>;
    reason_counts?: Record<string, number>;
    external_position_status_counts?: Record<string, number>;
    external_position_warning_counts?: Record<string, number>;
    first_sequence?: number;
    last_sequence?: number;
    first_timestamp_us?: number;
    last_timestamp_us?: number;
    average_confidence?: number;
    average_inliers?: number;
    average_reprojection_error_px?: number;
    segments: Array<{
      index: number;
      start_line: number;
      end_line: number;
      total_records: number;
      accepted_rate?: number;
      dominant_status?: string;
      average_confidence?: number;
      average_inliers?: number;
      average_reprojection_error_px?: number;
    }>;
    truncated: boolean;
  }>;
  image_previews: Array<{
    name: string;
    path: string;
    mime_type: string;
    size_bytes: number;
    base64_data: string;
  }>;
  replay_reports: Array<{
    case_name?: string;
    expected?: string;
    status?: "passed" | "failed" | "degraded" | string;
    accepted_rate?: number;
    total_records?: number;
    issues: string[];
  }>;
  gnss_denied_plan_check_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    source_path?: string;
    mission_plan_path?: string;
    mission_plan_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    message?: string;
    missing_checks: string[];
    failed_checks: string[];
    field_ready?: Record<string, unknown>;
  }>;
  px4_evidence_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    expected_message?: string;
    sample_count?: number;
    observed_rate_hz?: number;
    latest_sample_age_s?: number;
    last_position?: unknown;
    mavlink_version?: number;
    has_udp_14550?: boolean;
    issues: string[];
  }>;
  px4_param_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    ev_ctrl?: number;
    hgt_ref?: number;
    gps_ctrl?: number;
    ev_noise_mode?: number;
    ev_delay_ms?: number;
    issues: string[];
  }>;
  ardupilot_param_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    source_set?: number;
    viso_type?: number;
    posxy_source?: number;
    velxy_source?: number;
    posz_source?: number;
    velz_source?: number;
    yaw_source?: number;
    source_switch_channels?: unknown;
    issues: string[];
  }>;
  feature_method_benchmark_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    case_name?: string;
    expected?: string;
    recommended_method?: string;
    methods: Array<{
      method?: string;
      status?: "passed" | "failed" | "degraded" | "not_available" | string;
      accepted_rate?: number;
      total_records?: number;
    }>;
  }>;
  field_evidence_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    manifest_path?: string;
    coverage_status?: "passed" | "failed" | "degraded" | string;
    replay_status?: "passed" | "failed" | "degraded" | string;
    case_count?: number;
    field_case_count?: number;
    capture_metadata_issue_count?: number;
    covered_conditions?: unknown;
    required_conditions?: unknown;
    requirements: Array<{
      key?: string;
      status?: "covered" | "missing" | "synthetic_only" | "failed" | string;
      case_count?: number;
      field_case_count?: number;
    }>;
  }>;
  field_collection_plan_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    site_name?: string;
    manifest_path?: string;
    bundle?: string;
    source_log?: string;
    capture_root?: string;
    pending_preflight_command_count?: number;
    pending_preflight_capture_command_count?: number;
    pending_capture_command_count?: number;
    pending_metadata_update_command_count?: number;
    pending_registration_command_count?: number;
    capture_output_dir_count?: number;
    runtime_status_path_count?: number;
        condition_source_log_count?: number;
        summary: {
          required_count?: number;
          registered_count?: number;
          registered_missing_log_count?: number;
          placeholder_count?: number;
          missing_count?: number;
        };
        next_condition?: FieldCollectionPlanCondition;
        conditions: FieldCollectionPlanCondition[];
      }>;
  field_capture_preflight_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    plan_path?: string;
    repo_root?: string;
    condition?: string;
    case_name?: string;
    expected?: string;
    bundle_path?: string;
    bundle_validation_command?: string;
    ready_for_capture?: boolean;
    ready_for_registration?: boolean;
    capture_output_dir?: string;
    source_log?: string;
    runtime_status_path?: string;
    field_log_capture_report?: string;
    preflight_capture_command?: string;
    capture_script_path?: string;
    capture_script_hint?: string;
    summary?: unknown;
    checks: Array<{
      name?: string;
      status?: "passed" | "failed" | "degraded" | string;
      message?: string;
      path?: string;
      desktop_action?: string;
      validation_command?: string;
      missing?: unknown;
      issue_count?: number;
      bundle_diagnostic?: {
        bundle_exists?: boolean;
        missing_required_files: string[];
        search_root_count?: number;
        search_roots: string[];
        bundle_candidate_count?: number;
        map_source_candidate_count?: number;
        bundle_candidates: Array<{
          path?: string;
          bundle_id?: string;
          tile_index_exists?: boolean;
          field_proof_warning?: string;
        }>;
        map_source_candidates: Array<{
          path?: string;
          name?: string;
          source?: string;
          georef_source?: string;
          source_format?: string;
          requires_import?: boolean;
        }>;
        mission_plan_candidate_count?: number;
        mission_plan_candidates: Array<{
          path?: string;
          type?: string;
          plan_type?: string;
          name?: string;
          mission_item_count?: number;
          gnss_denied_status?: string;
          has_gnss_denied?: boolean;
        }>;
        recommended_actions: Array<{
          id?: string;
          status?: string;
          title?: string;
          desktop_action?: string;
          command?: string;
          notes?: string;
          bundle_path?: string;
          map_source_path?: string;
          mission_plan_path?: string;
          qgc_plan_path?: string;
        }>;
      };
    }>;
    next_actions: Array<{
      id?: string;
      status?: "ready" | "blocked" | "action_required" | string;
      title?: string;
      desktop_action?: string;
      command?: string;
      waits_on: string[];
      bundle_path?: string;
      capture_output_dir?: string;
      source_log?: string;
      runtime_status_path?: string;
      field_log_capture_report?: string;
      preflight_capture_command?: string;
      capture_script_path?: string;
      capture_script_hint?: string;
      notes?: string;
      bundle_diagnostic?: {
        bundle_exists?: boolean;
        missing_required_files: string[];
        search_root_count?: number;
        search_roots: string[];
        bundle_candidate_count?: number;
        map_source_candidate_count?: number;
        bundle_candidates: Array<{
          path?: string;
          bundle_id?: string;
          tile_index_exists?: boolean;
          field_proof_warning?: string;
        }>;
        map_source_candidates: Array<{
          path?: string;
          name?: string;
          source?: string;
          georef_source?: string;
          source_format?: string;
          requires_import?: boolean;
        }>;
        mission_plan_candidate_count?: number;
        mission_plan_candidates: Array<{
          path?: string;
          type?: string;
          plan_type?: string;
          name?: string;
          mission_item_count?: number;
          gnss_denied_status?: string;
          has_gnss_denied?: boolean;
        }>;
        recommended_actions: Array<{
          id?: string;
          status?: string;
          title?: string;
          desktop_action?: string;
          command?: string;
          notes?: string;
          bundle_path?: string;
          map_source_path?: string;
          mission_plan_path?: string;
          qgc_plan_path?: string;
        }>;
      };
    }>;
  }>;
  field_log_capture_report_summary?: {
    status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    report_count?: number;
    record_count?: number;
    registration_ready_count?: number;
    metadata_ready_count?: number;
    preflight_ready_for_capture_count?: number;
    preflight_ready_for_registration_count?: number;
    issue_count?: number;
    auto_added_field_collection_capture_report_count?: number;
    field_collection_plan_capture_reports: string[];
  };
  field_log_capture_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    generated_at_utc?: string;
    host?: string;
    device_name?: string;
    command_source?: string;
    command?: string;
    exit_code?: number;
    case_name?: string;
    expected?: "good_map" | "degraded" | "wrong_map" | string;
    condition?: string;
    conditions?: string;
    capture_output_dir?: string;
    metadata_ready?: boolean;
    metadata_issues: string[];
    preflight_status?: "passed" | "failed" | "degraded" | string;
    preflight_ready_for_capture?: boolean;
    preflight_ready_for_registration?: boolean;
    remote_terrain_log?: string;
    remote_runtime_status?: string;
    local_terrain_log?: string;
    local_runtime_status?: string;
    metadata_update_command?: string;
    register_command?: string;
    registration_ready?: boolean;
    runtime_status?: unknown;
  }>;
  threshold_tuning_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    method?: string;
    manifest_path?: string;
    coverage_status?: "passed" | "failed" | "degraded" | string;
    replay_status?: "passed" | "failed" | "degraded" | string;
    case_count?: number;
    field_case_count?: number;
    capture_metadata_issue_count?: number;
    covered_conditions?: unknown;
    margins?: unknown;
  }>;
  rosbag_export_validation_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    format?: string;
    artifact_path?: string;
    metadata_path?: string;
    message_count?: number;
    topic_count?: number;
    topics: string[];
    issues: string[];
  }>;
  rosbag2_cli_review_reports: Array<{
    path?: string;
    status?: "passed" | "failed" | "degraded" | string;
    artifact_path?: string;
    bag_dir?: string;
    validation_status?: "passed" | "failed" | "degraded" | string;
    validation_format?: string;
    ros2_cli_status?: "passed" | "failed" | "degraded" | string;
    ros2_cli_exit_code?: number;
    issues: string[];
  }>;
  autonomy_evidence_workflow_validation?: AutonomyEvidenceWorkflowReportFile["workflow_validation_summary"];
  bench_readiness?: {
    status?: "passed" | "failed" | "degraded" | string;
    failed_count?: number;
    degraded_count?: number;
    passed_count?: number;
    checks: Array<{
      name?: string;
      status?: "passed" | "failed" | "degraded" | string;
      message?: string;
    }>;
    next_actions?: Array<{
      check?: string;
      status?: "passed" | "failed" | "degraded" | string;
      title?: string;
      desktop_action?: string;
      command?: string;
      notes?: string;
      message?: string;
      field_condition?: string;
      field_label?: string;
      field_expected?: string;
      field_capture_output_dir?: string;
      field_source_log?: string;
      field_runtime_status_path?: string;
      field_bundle?: string;
      field_metadata_update_command?: string;
      field_register_command?: string;
    }>;
  };
}

export interface ExtractedSupportBundleArtifact {
  name: string;
  entry_path: string;
  path: string;
  size_bytes: number;
}

export interface AutonomyReadinessPlanSourceSnapshot {
  path?: string;
  exists?: boolean;
  source_sha256?: string;
  source_size_bytes?: number;
  required_marker_count?: number;
  missing_markers: string[];
  highest_value_reference_count?: number;
  fit_criteria_count?: number;
  architecture_section_count?: number;
  near_term_item_count?: number;
  avoid_choice_count?: number;
  track_count?: number;
  done_count?: number;
  in_progress_count?: number;
  task_count?: number;
  next_task_count?: number;
  acceptance_check_count?: number;
  execution_order_count?: number;
}

export interface AutonomyReadinessPlanSnapshot {
  schema_version?: string;
  research_doc?: AutonomyReadinessPlanSourceSnapshot;
  implementation_plan?: AutonomyReadinessPlanSourceSnapshot;
}

export interface AutonomyReadinessAuditMetadata {
  schema_version?: string;
  generated_at_utc?: string;
  repo?: {
    detected?: boolean;
    root?: string;
    path?: string;
    branch?: string;
    commit?: string;
    dirty?: boolean;
    remote?: string;
  };
}

export interface AutonomyReadinessBenchSubcheck {
  name?: string;
  status?: "passed" | "failed" | "degraded" | string;
  message?: string;
}

export interface AutonomyReadinessBenchEvidenceAction {
  label?: string;
  desktop_action?: string;
  command?: string;
  blocked_by?: string;
  notes?: string;
}

export interface AutonomyReadinessEvidenceBlocker {
  name?: string;
  status?: "passed" | "failed" | "degraded" | string;
  message?: string;
  missing_conditions: string[];
  bench_subchecks: AutonomyReadinessBenchSubcheck[];
  expected_bench_inputs?: string[];
  support_bundle_command?: string;
  bench_evidence_actions?: AutonomyReadinessBenchEvidenceAction[];
}

export interface AutonomyReadinessProofRunbook {
  schema_version?: string;
  ready_for_goal_completion?: boolean;
  phases_truncated?: boolean;
  summary: {
    phase_count?: number;
    passed?: number;
    action_required?: number;
    blocked?: number;
  };
  phases: Array<{
    id?: string;
    title?: string;
    status?: "passed" | "failed" | "degraded" | "action_required" | "blocked" | string;
    depends_on: string[];
    dependency_status: Record<string, string>;
    checks: Array<{
      name?: string;
      status?: "passed" | "failed" | "degraded" | "action_required" | "blocked" | string;
      message?: string;
    }>;
    actions: Array<{
      check?: string;
      status?: "passed" | "failed" | "degraded" | "action_required" | "blocked" | string;
      title?: string;
      desktop_action?: string;
      command?: string;
      notes?: string;
      missing_conditions: string[];
      bench_subcheck?: string;
      bench_message?: string;
      bench_subchecks: AutonomyReadinessBenchSubcheck[];
    }>;
    actions_truncated?: boolean;
    commands: string[];
    notes?: string;
  }>;
}

export interface AutonomyEvidencePackageFieldCapturePreflightDiagnostic {
  status?: "passed" | "failed" | "degraded" | string;
  path?: string;
  schema_version?: string;
  condition?: string;
  case_name?: string;
  expected?: string;
  bundle_path?: string;
  capture_output_dir?: string;
  source_log?: string;
  runtime_status_path?: string;
  field_log_capture_report?: string;
  capture_script_path?: string;
  capture_script_hint?: string;
  ready_for_capture?: boolean;
  ready_for_registration?: boolean;
  failed_checks: Array<{
    name?: string;
    status?: "passed" | "failed" | "degraded" | string;
    message?: string;
    path?: string;
    desktop_action?: string;
    validation_command?: string;
    missing?: unknown;
    issue_count?: number;
  }>;
  degraded_checks: Array<{
    name?: string;
    status?: "passed" | "failed" | "degraded" | string;
    message?: string;
    path?: string;
    desktop_action?: string;
    validation_command?: string;
    missing?: unknown;
    issue_count?: number;
  }>;
  next_actions: Array<{
    id?: string;
    status?: "ready" | "blocked" | "action_required" | string;
    title?: string;
    desktop_action?: string;
    command?: string;
    waits_on: string[];
    bundle_path?: string;
    capture_output_dir?: string;
    source_log?: string;
    runtime_status_path?: string;
    field_log_capture_report?: string;
    preflight_capture_command?: string;
    capture_script_path?: string;
    capture_script_hint?: string;
    notes?: string;
  }>;
}

export interface AutonomyReadinessReportFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  handoff_path?: string;
  handoff_size_bytes?: number;
  handoff_modified_unix_ms?: number;
  evidence_package_path?: string;
  evidence_package_size_bytes?: number;
  evidence_package_modified_unix_ms?: number;
  evidence_package_summary?: {
    schema_version?: string;
    readiness_status?: "passed" | "failed" | "degraded" | string;
    ready_for_goal_completion?: boolean;
    readiness_report_metadata?: AutonomyReadinessAuditMetadata;
    plan_snapshot?: AutonomyReadinessPlanSnapshot;
    proof_item_count?: number;
    proof_item_passed_count?: number;
    completion_blocker_count?: number;
    external_blocker_count?: number;
    included_count?: number;
    missing_count?: number;
    skipped_count?: number;
    proof_runbook_summary?: AutonomyReadinessProofRunbook;
    command_bundle?: {
      guided_workflow_commands?: string[];
      prerequisite_fix_commands?: string[];
      next_action_commands: string[];
      immediate_next_action_commands?: string[];
      blocked_follow_up_commands?: string[];
      field_collection_preflight_commands?: string[];
      field_collection_preflight_capture_commands?: string[];
      field_collection_capture_commands?: string[];
      field_collection_metadata_update_commands?: string[];
      field_collection_registration_commands: string[];
      command_count?: number;
      command_items?: Array<{
        group?: string;
        command?: string;
        desktop_action?: string;
      }>;
    };
    workflow_validation_summary?: AutonomyEvidenceWorkflowReportFile["workflow_validation_summary"];
    field_capture_preflight_diagnostic?: AutonomyEvidencePackageFieldCapturePreflightDiagnostic;
    proof_items: AutonomyReadinessEvidenceBlocker[];
    included_artifacts: Array<{
      label?: string;
      path?: string;
      reason?: string;
      status?: "passed" | "failed" | "degraded" | string;
      message?: string;
      source?: string;
      missing_conditions: string[];
    }>;
    missing_artifacts: Array<{
      label?: string;
      path?: string;
      reason?: string;
      status?: "passed" | "failed" | "degraded" | string;
      message?: string;
      source?: string;
      missing_conditions: string[];
    }>;
    skipped_artifacts: Array<{
      label?: string;
      path?: string;
      reason?: string;
      status?: "passed" | "failed" | "degraded" | string;
      message?: string;
      source?: string;
      missing_conditions: string[];
    }>;
  };
  workflow_report_path?: string;
  workflow_report_local_path?: string;
  workflow_validation_path?: string;
  workflow_validation_local_path?: string;
  workflow_log_archive_path?: string;
  workflow_log_archive_local_path?: string;
  metadata?: AutonomyReadinessAuditMetadata;
  summary: {
    status?: "passed" | "failed" | "degraded" | string;
    failed_count?: number;
    degraded_count?: number;
    passed_count?: number;
    support_bundle_bench_readiness_status?: "passed" | "failed" | "degraded" | string;
    px4_receiver_proof_status?: "passed" | "failed" | "degraded" | string;
    field_collection_plan_status?: "passed" | "failed" | "degraded" | string;
    field_evidence_proof_status?: "passed" | "failed" | "degraded" | string;
    feature_method_benchmark_status?: "passed" | "failed" | "degraded" | string;
    threshold_tuning_status?: "passed" | "failed" | "degraded" | string;
    rosbag_export_validation_status?: "passed" | "failed" | "degraded" | string;
    rosbag2_cli_review_status?: "passed" | "failed" | "degraded" | string;
  };
  checks: Array<{
    name?: string;
    status?: "passed" | "failed" | "degraded" | string;
    message?: string;
  }>;
  next_actions: Array<{
    check?: string;
    status?: "passed" | "failed" | "degraded" | string;
    title?: string;
    desktop_action?: string;
    command?: string;
    notes?: string;
    missing_conditions: string[];
    bench_subcheck?: string;
    bench_message?: string;
    bench_subchecks?: Array<{
      name?: string;
      status?: "passed" | "failed" | "degraded" | string;
      message?: string;
    }>;
  }>;
  command_bundle?: {
    guided_workflow_commands?: string[];
    prerequisite_fix_commands?: string[];
    next_action_commands: string[];
    immediate_next_action_commands?: string[];
    blocked_follow_up_commands?: string[];
    field_collection_preflight_commands?: string[];
    field_collection_preflight_capture_commands?: string[];
    field_collection_capture_commands?: string[];
    field_collection_metadata_update_commands?: string[];
    field_collection_registration_commands: string[];
    command_count?: number;
    command_items?: Array<{
      group?: string;
      command?: string;
      desktop_action?: string;
    }>;
  };
  plan_snapshot?: AutonomyReadinessPlanSnapshot;
  evidence_manifest?: {
    schema_version?: string;
    ready_for_goal_completion?: boolean;
    proof_items: AutonomyReadinessEvidenceBlocker[];
    completion_blockers: AutonomyReadinessEvidenceBlocker[];
    external_blockers: AutonomyReadinessEvidenceBlocker[];
  };
      field_collection_plan?: {
        path: string;
        status?: "passed" | "failed" | "degraded" | string;
        site_name?: string;
        manifest_path?: string;
    bundle?: string;
        pending_preflight_command_count?: number;
        pending_preflight_capture_command_count?: number;
        pending_capture_command_count?: number;
        pending_metadata_update_command_count?: number;
        pending_registration_command_count?: number;
    summary: {
      required_count?: number;
      registered_count?: number;
      registered_missing_log_count?: number;
          placeholder_count?: number;
          missing_count?: number;
        };
        next_condition?: FieldCollectionPlanCondition;
        pending_conditions: FieldCollectionPlanCondition[];
      };
  proof_runbook?: AutonomyReadinessProofRunbook;
}

export interface AutonomyEvidenceWorkflowValidationNextStep {
  name?: string;
  status?: "passed" | "failed" | "degraded" | "skipped" | "missing" | string;
  exit_code?: number;
  notes?: string;
  command?: string;
  desktop_action?: string;
  metadata_update_command?: string;
  bundle_path?: string;
  expected_log?: string;
  output_dir?: string;
  runtime_status_path?: string;
  field_log_capture_report?: string;
  capture_script_path?: string;
  capture_script_hint?: string;
  capture_command_after_bundle?: string;
}

export interface AutonomyEvidenceWorkflowValidationStepResult {
  name?: string;
  status?: "passed" | "failed" | "degraded" | "skipped" | string;
  exit_code?: number;
  notes?: string;
  workflow_notes?: string;
  command?: string;
  desktop_action?: string;
  bundle_path?: string;
  expected_log?: string;
  output_dir?: string;
  runtime_status_path?: string;
  field_log_capture_report?: string;
  capture_script_path?: string;
  capture_script_hint?: string;
  preflight_report?: string;
  preflight_status?: "passed" | "failed" | "degraded" | "skipped" | string;
  ready_for_capture?: boolean;
  ready_for_registration?: boolean;
  capture_command_after_preflight?: string;
  preflight_capture_command?: string;
  metadata_update_command?: string;
  current_selected_condition?: string;
  current_selected_case?: string;
  current_selected_log?: string;
  blocked_by?: string;
  required_log?: string;
  required_runtime_status?: string;
  current_preflight_allows_capture?: boolean;
  current_preflight_report?: string;
  current_preflight_status?: "passed" | "failed" | "degraded" | "skipped" | string;
  current_ready_for_registration?: boolean;
  guidance?: string;
}

export interface AutonomyEvidenceWorkflowReportFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  status?: "passed" | "failed" | "degraded" | string;
  generated_at?: string;
  workflow_dir?: string;
  summary: {
    passed?: number;
    failed?: number;
    skipped?: number;
  };
  steps: Array<{
    name?: string;
    status?: "passed" | "failed" | "degraded" | "skipped" | string;
    exit_code?: number;
    log_path?: string;
    notes?: string;
  }>;
  marker_count: number;
  workflow_logs_path?: string;
  workflow_logs_local_path?: string;
  workflow_validation_path?: string;
  workflow_validation_local_path?: string;
  workflow_validation_summary?: {
    status?: "passed" | "failed" | "degraded" | string;
    workflow_status?: "passed" | "failed" | "degraded" | string;
    step_count?: number;
    marker_count?: number;
    issue_count: number;
    missing_required_step_count?: number;
    active_required_step_count?: number;
    downstream_blocked_step_count?: number;
    superseded_step_count?: number;
    issues: string[];
    next_required_step?: AutonomyEvidenceWorkflowValidationNextStep;
    checks: Array<{
      name?: string;
      status?: "passed" | "failed" | "degraded" | string;
      message?: string;
      marker_count?: number;
      missing_markers: string[];
      present_markers: string[];
      missing_steps: string[];
      non_passed_count?: number;
      non_passed_steps: AutonomyEvidenceWorkflowValidationStepResult[];
      blocked_count?: number;
      blocked_steps: AutonomyEvidenceWorkflowValidationStepResult[];
      superseded_count?: number;
      superseded_steps: AutonomyEvidenceWorkflowValidationStepResult[];
    }>;
    log_archive?: string;
  };
  support_bundle_path?: string;
  support_bundle_local_path?: string;
  field_evidence_report_path?: string;
  field_evidence_report_local_path?: string;
  feature_method_report_path?: string;
  feature_method_report_local_path?: string;
  threshold_report_path?: string;
  threshold_report_local_path?: string;
  rosbag_validation_path?: string;
  rosbag_validation_local_path?: string;
  readiness_report_path?: string;
  readiness_report_local_path?: string;
  handoff_path?: string;
  handoff_local_path?: string;
  evidence_package_path?: string;
  evidence_package_local_path?: string;
  field_collection_plan_path?: string;
  field_collection_plan_local_path?: string;
  field_collection_plan_markdown_path?: string;
  field_collection_plan_markdown_local_path?: string;
  field_metadata_update_command?: string;
  px4_receiver_report_path?: string;
  px4_receiver_report_local_path?: string;
  px4_prereq_report_path?: string;
  px4_prereq_report_local_path?: string;
}

export interface Px4ReceiverReportFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  report: {
    status?: "passed" | "failed" | "degraded" | string;
    expected_message?: string;
      sample_count?: number;
      observed_rate_hz?: number;
      latest_sample_age_s?: number;
    last_position?: unknown;
    mavlink_version?: number;
    has_udp_14550?: boolean;
    issues: string[];
  };
}

export interface Px4PrereqReportFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  report: {
    status?: "passed" | "failed" | "degraded" | "not_checked" | string;
    generated_at?: string;
    session_dir?: string;
    capture_dir?: string;
    receiver_report?: string;
    px4_dir?: string;
    px4_target?: string;
    tmux_session?: string;
    checks: Array<{
      name?: string;
      status?: "passed" | "failed" | "skipped" | "not_checked" | string;
      message?: string;
    }>;
    next_actions: string[];
    fix_commands?: Array<{
      label?: string;
      command?: string;
      condition?: string;
    }>;
  };
}

export interface FieldEvidenceReportFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  report: {
    status?: "passed" | "failed" | "degraded" | string;
    manifest_path?: string;
    coverage_status?: "passed" | "failed" | "degraded" | string;
    replay_status?: "passed" | "failed" | "degraded" | string;
    case_count?: number;
    field_case_count?: number;
    capture_metadata_issue_count?: number;
    covered_conditions?: unknown;
    required_conditions?: unknown;
    requirements: Array<{
      key?: string;
      status?: "covered" | "missing" | "synthetic_only" | "failed" | string;
      case_count?: number;
      field_case_count?: number;
    }>;
  };
}

export interface FieldEvidenceTemplateFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  site_name?: string;
  case_count: number;
  placeholder_count: number;
  required_conditions: string[];
  conditions: string[];
  placeholder_conditions: string[];
  registered_conditions: string[];
}

export interface FieldCollectionPlanFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  markdown_path?: string;
  markdown_size_bytes?: number;
  markdown_modified_unix_ms?: number;
  status?: "passed" | "failed" | "degraded" | string;
  site_name?: string;
  manifest_path?: string;
  bundle?: string;
  capture_root?: string;
  pending_preflight_command_count?: number;
  pending_preflight_capture_command_count?: number;
  pending_capture_command_count?: number;
  pending_metadata_update_command_count?: number;
  pending_registration_command_count?: number;
  capture_output_dir_count?: number;
  runtime_status_path_count?: number;
  condition_source_log_count?: number;
      summary: {
        required_count?: number;
        registered_count?: number;
        registered_missing_log_count?: number;
        placeholder_count?: number;
        missing_count?: number;
      };
      next_condition?: FieldCollectionPlanCondition;
      conditions: FieldCollectionPlanCondition[];
    }

export interface FeatureMethodBenchmarkReportFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  report: {
    status?: "passed" | "failed" | "degraded" | "not_available" | string;
    case_name?: string;
    expected?: "good_map" | "degraded" | "wrong_map" | string;
    recommended_method?: string;
    methods: Array<{
      method?: string;
      status?: "passed" | "failed" | "degraded" | "not_available" | string;
      accepted_rate?: number;
      total_records?: number;
    }>;
  };
}

export interface ThresholdTuningReportFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  report: {
    status?: "passed" | "failed" | "degraded" | string;
    method?: string;
    manifest_path?: string;
    coverage_status?: "passed" | "failed" | "degraded" | string;
    replay_status?: "passed" | "failed" | "degraded" | string;
    case_count?: number;
    field_case_count?: number;
    capture_metadata_issue_count?: number;
    covered_conditions?: unknown;
    margins?: unknown;
  };
}

export interface RosbagExportValidationReportFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  report: {
    status?: "passed" | "failed" | "degraded" | string;
    format?: string;
    artifact_path?: string;
    metadata_path?: string;
    message_count?: number;
    topic_count?: number;
    topics: string[];
    issues: string[];
  };
}

export interface FieldLogCaptureReportFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  report: {
    status?: "passed" | "failed" | "degraded" | string;
    generated_at_utc?: string;
    host?: string;
    device_name?: string;
    command_source?: string;
    command?: string;
    exit_code?: number;
    case_name?: string;
    expected?: "good_map" | "degraded" | "wrong_map" | string;
    condition?: string;
    conditions?: string;
    capture_output_dir?: string;
    metadata_ready?: boolean;
    metadata_issues: string[];
    preflight_status?: "passed" | "failed" | "degraded" | string;
    preflight_ready_for_capture?: boolean;
    preflight_ready_for_registration?: boolean;
    remote_terrain_log?: string;
    remote_runtime_status?: string;
    local_terrain_log?: string;
    local_runtime_status?: string;
    metadata_update_command?: string;
    register_command?: string;
    registration_ready?: boolean;
    runtime_status?: unknown;
  };
}

export interface BuildDroneBundleRequest {
  region_dir: string;
  output_dir: string;
  repo_path: string;
  pipeline: VisionPipeline;
  feature_method: FeatureMethod;
  max_features: number;
  mission_plan_json?: string;
  qgc_plan_json?: string;
  runtime_profile?: RuntimeProfileId;
  camera_profile?: CameraProfileId;
  hardware_profile?: Record<string, unknown>;
}

export interface BuildDroneBundleResult {
  bundle_dir: string;
  manifest_path: string;
  stac_manifest_path?: string;
  orthophoto_path: string;
  features_path: string;
  terrain_index_path?: string;
  terrain_config_path?: string;
  terrain_tile_count?: number;
  terrain_feature_count?: number;
  terrain_gsd_m?: number;
  terrain_tile_size_px?: number;
  geospatial_health?: {
    status?: "passed" | "degraded" | "failed";
    georef?: {
      crs?: string;
      gsd_m?: number;
      confidence?: number;
    };
    stac?: {
      status?: "passed" | "degraded" | "failed";
    };
    tile_index?: {
      status?: "passed" | "degraded" | "failed";
      tile_count?: number;
      feature_count?: number;
      quality?: {
        estimated_pi_runtime_cost?: "low" | "moderate" | "high";
        low_texture_tile_count?: number;
        low_texture_ratio?: number;
      };
    };
    map_quality?: {
      estimated_pi_runtime_cost?: "low" | "moderate" | "high";
      low_texture_tile_count?: number;
      low_texture_ratio?: number;
      heatmap?: {
        row_count?: number;
        col_count?: number;
        cell_count?: number;
        omitted_tile_count?: number;
        cells?: Array<{
          tile_id?: string;
          row?: number;
          col?: number;
          keypoint_count?: number;
          feature_density_per_mpx?: number;
          quality?: "low" | "fair" | "good" | "dense" | string;
          local_bounds_m?: {
            east_min?: number;
            east_max?: number;
            north_min?: number;
            north_max?: number;
          };
        }>;
      };
    };
    elevation?: {
      status?: "not_provided" | "passed" | "degraded" | "failed";
      required?: boolean;
      asset_count?: number;
      dem_present?: boolean;
      dsm_present?: boolean;
      vertical_sanity_ready?: boolean;
      assets?: Array<{
        kind?: "dem" | "dsm" | string;
        path?: string;
        exists?: boolean;
        status?: "passed" | "degraded" | "missing" | string;
      }>;
    };
    terrain_profile?: {
      status?: "not_provided" | "not_available" | "passed" | "degraded" | "failed" | string;
      mission_path?: string;
      reason?: string;
      surface_source?: "dem" | "dsm" | string;
      surface_path?: string;
      mission_item_count?: number;
      sample_count?: number;
      sampled_count?: number;
      path_length_m?: number;
      coordinate_mapping?: string;
      altitude_reference?: string;
      terrain_elevation_m?: {
        min?: number;
        max?: number;
        start?: number;
        end?: number;
        relief?: number;
      };
      mission_altitude_m?: {
        min?: number;
        max?: number;
      };
      estimated_agl_m?: {
        min?: number;
        max?: number;
        mean?: number;
      };
      min_agl_to_map_gsd_ratio?: number;
      preview_points?: Array<{
        distance_m?: number;
        terrain_elevation_m?: number;
        mission_altitude_m?: number;
        estimated_agl_m?: number;
      }>;
      issues?: Array<{ severity: "error" | "warning" | "info"; message: string }>;
    };
    checksums?: {
      status?: "missing" | "passed" | "failed";
      required?: boolean;
      checksum_file?: string;
      entry_count?: number;
      covered_file_count?: number;
      extra_file_count?: number;
      missing?: string[];
      mismatched?: Array<{ path: string; expected: string; actual: string }>;
      extra_files_sample?: string[];
      ignored_volatile_entries?: string[];
    };
    source_provenance?: {
      bundle_id?: string;
      map_source?: string;
      map_id?: string;
      map_name?: string;
      metadata_path?: string;
      original_file?: string;
      orthophoto_path?: string;
      width_px?: number;
      height_px?: number;
      zoom?: number;
      georef_source?: string;
      georef_confidence?: number;
      georef_crs?: string;
      gsd_m?: number;
      origin_lat?: number;
      origin_lon?: number;
      terrain_builder?: string;
      terrain_built_at?: string;
      feature_method?: string;
      max_features?: number;
      pipeline?: string;
    };
    issues?: Array<{ severity: "error" | "warning" | "info"; message: string }>;
  };
  checksums_path: string;
  mission_plan_path?: string;
  qgc_plan_path?: string;
  command: string;
  stdout: string;
  stderr: string;
  exit_code: number;
}

export interface ImportMapFileRequest {
  map_path: string;
  output_dir: string;
  name: string;
  origin_lat?: number;
  origin_lon?: number;
  gsd_m_per_px?: number;
  origin_pixel_x?: number;
  origin_pixel_y?: number;
  rotation_deg?: number;
}

export interface ImportMapFileResult {
  output_dir: string;
  mosaic_path: string;
  metadata_path: string;
  width_px: number;
  height_px: number;
  gsd_m_per_px: number;
  origin_lat: number;
  origin_lon: number;
  origin_pixel_x: number;
  origin_pixel_y: number;
  rotation_deg: number;
  georef_source: string;
  georef_confidence: number;
  georef_crs?: string;
  source: "uploaded";
}

export interface ImportElevationAssetsRequest {
  region_dir: string;
  dem_path?: string;
  dsm_path?: string;
}

export interface ImportElevationAssetsResult {
  region_dir: string;
  dem_path?: string;
  dsm_path?: string;
  asset_count: number;
  metadata_path: string;
}

export interface CommandResult {
  exit_code: number;
  stdout: string;
  stderr: string;
}

export interface BBox {
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
}
