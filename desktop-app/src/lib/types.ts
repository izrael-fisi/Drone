export interface Profile {
  name: string;
  email: string;
  org: string;
  accent_color: string;
  onboarding_complete: boolean;
  mapbox_key?: string;
  bing_key?: string;
}

export type TileSource = "esri" | "mapbox" | "bing";
export type MapSource = TileSource | "uploaded" | "folder";

export type DeviceKind = "pi5" | "local";
export type AuthMethod = "password" | "key";
export type VisionPipeline = "classical" | "neural";
export type FeatureMethod = "orb" | "akaze" | "sift";

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
  georef_source?: string;
  georef_confidence?: number;
  georef_crs?: string;
  file_size_mb?: number;
  location_label?: string;
  elevation_dem_path?: string;
  elevation_dsm_path?: string;
  elevation_asset_count?: number;
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

export interface DownloadFileResult {
  remote_path: string;
  local_path: string;
  bytes_received: number;
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
    px4_sitl_evidence_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    px4_sitl_sample_count?: number;
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
    field_evidence_report_count?: number;
    threshold_tuning_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    threshold_tuning_field_case_count?: number;
    threshold_tuning_report_count?: number;
    bench_readiness_status?: "passed" | "failed" | "degraded" | string;
    bench_readiness_failed_count?: number;
    bench_readiness_degraded_count?: number;
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
  px4_evidence_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    expected_message?: string;
    sample_count?: number;
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
    covered_conditions?: unknown;
    required_conditions?: unknown;
    requirements: Array<{
      key?: string;
      status?: "covered" | "missing" | "synthetic_only" | "failed" | string;
      case_count?: number;
      field_case_count?: number;
    }>;
  }>;
  threshold_tuning_reports: Array<{
    status?: "passed" | "failed" | "degraded" | string;
    method?: string;
    manifest_path?: string;
    coverage_status?: "passed" | "failed" | "degraded" | string;
    replay_status?: "passed" | "failed" | "degraded" | string;
    case_count?: number;
    field_case_count?: number;
    covered_conditions?: unknown;
    margins?: unknown;
  }>;
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
  };
}

export interface ExtractedSupportBundleArtifact {
  name: string;
  entry_path: string;
  path: string;
  size_bytes: number;
}

export interface AutonomyReadinessReportFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  handoff_path?: string;
  handoff_size_bytes?: number;
  handoff_modified_unix_ms?: number;
  summary: {
    status?: "passed" | "failed" | "degraded" | string;
    failed_count?: number;
    degraded_count?: number;
    passed_count?: number;
    support_bundle_bench_readiness_status?: "passed" | "failed" | "degraded" | string;
    px4_receiver_proof_status?: "passed" | "failed" | "degraded" | string;
    field_evidence_proof_status?: "passed" | "failed" | "degraded" | string;
    feature_method_benchmark_status?: "passed" | "failed" | "degraded" | string;
    threshold_tuning_status?: "passed" | "failed" | "degraded" | string;
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
  evidence_manifest?: {
    schema_version?: string;
    ready_for_goal_completion?: boolean;
    completion_blockers: Array<{
      name?: string;
      status?: "passed" | "failed" | "degraded" | string;
      message?: string;
      missing_conditions: string[];
      bench_subchecks: Array<{
        name?: string;
        status?: "passed" | "failed" | "degraded" | string;
        message?: string;
      }>;
    }>;
    external_blockers: Array<{
      name?: string;
      status?: "passed" | "failed" | "degraded" | string;
      message?: string;
      missing_conditions: string[];
      bench_subchecks: Array<{
        name?: string;
        status?: "passed" | "failed" | "degraded" | string;
        message?: string;
      }>;
    }>;
  };
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
    latest_sample_age_s?: number;
    last_position?: unknown;
    mavlink_version?: number;
    has_udp_14550?: boolean;
    issues: string[];
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
    covered_conditions?: unknown;
    margins?: unknown;
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
