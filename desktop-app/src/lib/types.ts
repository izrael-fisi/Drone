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
  has_capture_command?: boolean;
  has_preflight_command?: boolean;
  has_metadata_update_command?: boolean;
  has_register_command?: boolean;
  preflight_command?: string;
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
        }>;
        recommended_actions: Array<{
          id?: string;
          status?: string;
          title?: string;
          desktop_action?: string;
          command?: string;
          bundle_path?: string;
          map_source_path?: string;
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
      notes?: string;
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
  capture_command_after_bundle?: string;
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
      non_passed_steps: Array<{
        name?: string;
        status?: "passed" | "failed" | "degraded" | "skipped" | string;
        exit_code?: number;
        notes?: string;
      }>;
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
