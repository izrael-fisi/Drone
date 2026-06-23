use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose, Engine as _};
use serde::Serialize;
use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::UNIX_EPOCH;
use zip::ZipArchive;

const LOG_PREVIEW_LIMIT: usize = 5;
const IMAGE_PREVIEW_LIMIT: usize = 4;
const IMAGE_PREVIEW_MAX_BYTES: u64 = 1_500_000;
const SUPPORT_ARTIFACT_MAX_BYTES: u64 = 50 * 1024 * 1024;
const LOG_TIMELINE_SEGMENTS: usize = 24;
const LOG_TIMELINE_MAX_BYTES: u64 = 50 * 1024 * 1024;

#[derive(Serialize)]
pub struct SupportBundleSummary {
    pub bundle_id: Option<String>,
    pub bundle_health_status: Option<String>,
    pub checksum_status: Option<String>,
    pub covered_file_count: Option<u64>,
    pub elevation_status: Option<String>,
    pub elevation_asset_count: Option<u64>,
    pub vertical_sanity_ready: Option<bool>,
    pub map_source: Option<String>,
    pub source_name: Option<String>,
    pub georef_source: Option<String>,
    pub georef_crs: Option<String>,
    pub georef_confidence: Option<f64>,
    pub replay_gate_status: Option<String>,
    pub replay_case_count: Option<u64>,
    pub gnss_denied_plan_status: Option<String>,
    pub px4_sitl_evidence_status: Option<String>,
    pub px4_sitl_sample_count: Option<u64>,
    pub px4_sitl_prereq_status: Option<String>,
    pub px4_params_status: Option<String>,
    pub px4_ev_ctrl: Option<i64>,
    pub ardupilot_params_status: Option<String>,
    pub ardupilot_source_set: Option<i64>,
    pub ardupilot_posxy_source: Option<i64>,
    pub feature_method_benchmark_status: Option<String>,
    pub feature_method_benchmark_recommended: Option<String>,
    pub feature_method_benchmark_report_count: Option<u64>,
    pub field_evidence_status: Option<String>,
    pub field_evidence_field_case_count: Option<u64>,
    pub field_evidence_capture_metadata_issue_count: Option<u64>,
    pub field_evidence_report_count: Option<u64>,
    pub field_collection_plan_status: Option<String>,
    pub field_collection_plan_registered_count: Option<u64>,
    pub field_collection_plan_required_count: Option<u64>,
    pub field_collection_plan_report_count: Option<u64>,
    pub field_collection_plan_pending_capture_command_count: Option<u64>,
    pub field_collection_plan_pending_metadata_update_command_count: Option<u64>,
    pub field_collection_plan_pending_registration_command_count: Option<u64>,
    pub field_collection_plan_capture_output_dir_count: Option<u64>,
    pub field_collection_plan_runtime_status_path_count: Option<u64>,
    pub field_collection_plan_condition_source_log_count: Option<u64>,
    pub field_capture_preflight_status: Option<String>,
    pub field_capture_preflight_report_count: Option<u64>,
    pub field_capture_preflight_ready_for_capture_count: Option<u64>,
    pub field_capture_preflight_ready_for_registration_count: Option<u64>,
    pub field_capture_preflight_failed_check_count: Option<u64>,
    pub field_capture_preflight_degraded_check_count: Option<u64>,
    pub field_capture_preflight_next_action_count: Option<u64>,
    pub field_capture_preflight_blocked_action_count: Option<u64>,
    pub threshold_tuning_status: Option<String>,
    pub threshold_tuning_field_case_count: Option<u64>,
    pub threshold_tuning_capture_metadata_issue_count: Option<u64>,
    pub threshold_tuning_report_count: Option<u64>,
    pub rosbag_export_validation_status: Option<String>,
    pub rosbag_export_validation_report_count: Option<u64>,
    pub rosbag_export_validation_message_count: Option<u64>,
    pub rosbag_export_validation_topic_count: Option<u64>,
    pub rosbag2_cli_review_status: Option<String>,
    pub rosbag2_cli_review_report_count: Option<u64>,
    pub evidence_workflow_status: Option<String>,
    pub evidence_workflow_validation_status: Option<String>,
    pub evidence_workflow_runtime_status: Option<String>,
    pub evidence_workflow_provenance_status: Option<String>,
    pub evidence_workflow_step_count: Option<u64>,
    pub evidence_workflow_issue_count: Option<u64>,
    pub evidence_workflow_repo_commit: Option<String>,
    pub bench_readiness_status: Option<String>,
    pub bench_readiness_failed_count: Option<u64>,
    pub bench_readiness_degraded_count: Option<u64>,
}

#[derive(Serialize)]
pub struct SupportBundleFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub summary: Option<SupportBundleSummary>,
}

#[derive(Serialize)]
pub struct LocalCommandResult {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
}

#[derive(Serialize)]
pub struct SupportBundleLogSummary {
    pub name: String,
    pub total_records: Option<u64>,
    pub accepted_rate: Option<f64>,
    pub status_counts: Option<serde_json::Value>,
    pub reason_counts: Option<serde_json::Value>,
    pub external_position: Option<serde_json::Value>,
}

#[derive(Serialize)]
pub struct SupportBundleReplayReport {
    pub case_name: Option<String>,
    pub expected: Option<String>,
    pub status: Option<String>,
    pub accepted_rate: Option<f64>,
    pub total_records: Option<u64>,
    pub issues: Vec<String>,
}

#[derive(Serialize)]
pub struct SupportBundlePx4EvidenceReport {
    pub status: Option<String>,
    pub expected_message: Option<String>,
    pub sample_count: Option<u64>,
    pub observed_rate_hz: Option<f64>,
    pub latest_sample_age_s: Option<f64>,
    pub last_position: Option<serde_json::Value>,
    pub mavlink_version: Option<u64>,
    pub has_udp_14550: Option<bool>,
    pub issues: Vec<String>,
}

#[derive(Serialize)]
pub struct Px4ReceiverReportFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub report: SupportBundlePx4EvidenceReport,
}

#[derive(Serialize)]
pub struct Px4PrereqCheck {
    pub name: Option<String>,
    pub status: Option<String>,
    pub message: Option<String>,
}

#[derive(Serialize)]
pub struct Px4PrereqFixCommand {
    pub label: Option<String>,
    pub command: Option<String>,
    pub condition: Option<String>,
}

#[derive(Serialize)]
pub struct Px4PrereqReport {
    pub status: Option<String>,
    pub generated_at: Option<String>,
    pub session_dir: Option<String>,
    pub capture_dir: Option<String>,
    pub receiver_report: Option<String>,
    pub px4_dir: Option<String>,
    pub px4_target: Option<String>,
    pub tmux_session: Option<String>,
    pub checks: Vec<Px4PrereqCheck>,
    pub next_actions: Vec<String>,
    pub fix_commands: Vec<Px4PrereqFixCommand>,
}

#[derive(Serialize)]
pub struct Px4PrereqReportFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub report: Px4PrereqReport,
}

#[derive(Serialize)]
pub struct SupportBundlePx4ParamReport {
    pub status: Option<String>,
    pub ev_ctrl: Option<i64>,
    pub hgt_ref: Option<i64>,
    pub gps_ctrl: Option<i64>,
    pub ev_noise_mode: Option<i64>,
    pub ev_delay_ms: Option<f64>,
    pub issues: Vec<String>,
}

#[derive(Serialize)]
pub struct SupportBundleArduPilotParamReport {
    pub status: Option<String>,
    pub source_set: Option<i64>,
    pub viso_type: Option<i64>,
    pub posxy_source: Option<i64>,
    pub velxy_source: Option<i64>,
    pub posz_source: Option<i64>,
    pub velz_source: Option<i64>,
    pub yaw_source: Option<i64>,
    pub source_switch_channels: Option<serde_json::Value>,
    pub issues: Vec<String>,
}

#[derive(Serialize)]
pub struct SupportBundleFeatureMethodReport {
    pub method: Option<String>,
    pub status: Option<String>,
    pub accepted_rate: Option<f64>,
    pub total_records: Option<u64>,
}

#[derive(Serialize)]
pub struct SupportBundleFeatureMethodBenchmarkReport {
    pub status: Option<String>,
    pub case_name: Option<String>,
    pub expected: Option<String>,
    pub recommended_method: Option<String>,
    pub methods: Vec<SupportBundleFeatureMethodReport>,
}

#[derive(Serialize)]
pub struct FeatureMethodBenchmarkReportFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub report: SupportBundleFeatureMethodBenchmarkReport,
}

#[derive(Serialize)]
pub struct SupportBundleFieldEvidenceCondition {
    pub key: Option<String>,
    pub status: Option<String>,
    pub case_count: Option<u64>,
    pub field_case_count: Option<u64>,
}

#[derive(Serialize)]
pub struct SupportBundleFieldEvidenceReport {
    pub status: Option<String>,
    pub manifest_path: Option<String>,
    pub coverage_status: Option<String>,
    pub replay_status: Option<String>,
    pub case_count: Option<u64>,
    pub field_case_count: Option<u64>,
    pub capture_metadata_issue_count: Option<u64>,
    pub covered_conditions: Option<serde_json::Value>,
    pub required_conditions: Option<serde_json::Value>,
    pub requirements: Vec<SupportBundleFieldEvidenceCondition>,
}

#[derive(Serialize)]
pub struct FieldEvidenceReportFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub report: SupportBundleFieldEvidenceReport,
}

#[derive(Serialize)]
pub struct FieldEvidenceTemplateFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub site_name: Option<String>,
    pub case_count: u64,
    pub placeholder_count: u64,
    pub required_conditions: Vec<String>,
    pub conditions: Vec<String>,
    pub placeholder_conditions: Vec<String>,
    pub registered_conditions: Vec<String>,
}

#[derive(Serialize)]
pub struct FieldCollectionPlanSummary {
    pub required_count: Option<u64>,
    pub registered_count: Option<u64>,
    pub registered_missing_log_count: Option<u64>,
    pub placeholder_count: Option<u64>,
    pub missing_count: Option<u64>,
}

#[derive(Serialize, Clone)]
pub struct FieldCollectionPlanCondition {
    pub condition: Option<String>,
    pub label: Option<String>,
    pub expected: Option<String>,
    pub status: Option<String>,
    pub notes: Option<String>,
    pub case_name: Option<String>,
    pub manifest_log_path: Option<String>,
    pub manifest_log_exists: Option<bool>,
    pub source_log: Option<String>,
    pub legacy_source_log: Option<String>,
    pub capture_output_dir: Option<String>,
    pub runtime_status_path: Option<String>,
    pub has_preflight_command: Option<bool>,
    pub has_capture_command: Option<bool>,
    pub has_preflight_capture_command: Option<bool>,
    pub has_metadata_update_command: Option<bool>,
    pub has_register_command: Option<bool>,
    pub bundle: Option<String>,
    pub capture_metadata: Option<serde_json::Value>,
    pub preflight_command: Option<String>,
    pub preflight_capture_command: Option<String>,
    pub capture_command: Option<String>,
    pub metadata_update_command: Option<String>,
    pub register_command: Option<String>,
}

#[derive(Serialize)]
pub struct FieldCollectionPlanFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub markdown_path: Option<String>,
    pub markdown_size_bytes: Option<u64>,
    pub markdown_modified_unix_ms: Option<u128>,
    pub status: Option<String>,
    pub site_name: Option<String>,
    pub manifest_path: Option<String>,
    pub bundle: Option<String>,
    pub capture_root: Option<String>,
    pub pending_preflight_command_count: Option<u64>,
    pub pending_preflight_capture_command_count: Option<u64>,
    pub pending_capture_command_count: Option<u64>,
    pub pending_metadata_update_command_count: Option<u64>,
    pub pending_registration_command_count: Option<u64>,
    pub capture_output_dir_count: Option<u64>,
    pub runtime_status_path_count: Option<u64>,
    pub condition_source_log_count: Option<u64>,
    pub next_condition: Option<FieldCollectionPlanCondition>,
    pub summary: FieldCollectionPlanSummary,
    pub conditions: Vec<FieldCollectionPlanCondition>,
}

#[derive(Serialize)]
pub struct SupportBundleFieldCollectionPlanReport {
    pub status: Option<String>,
    pub site_name: Option<String>,
    pub manifest_path: Option<String>,
    pub bundle: Option<String>,
    pub source_log: Option<String>,
    pub capture_root: Option<String>,
    pub pending_preflight_command_count: Option<u64>,
    pub pending_preflight_capture_command_count: Option<u64>,
    pub pending_capture_command_count: Option<u64>,
    pub pending_metadata_update_command_count: Option<u64>,
    pub pending_registration_command_count: Option<u64>,
    pub capture_output_dir_count: Option<u64>,
    pub runtime_status_path_count: Option<u64>,
    pub condition_source_log_count: Option<u64>,
    pub next_condition: Option<FieldCollectionPlanCondition>,
    pub summary: FieldCollectionPlanSummary,
    pub conditions: Vec<FieldCollectionPlanCondition>,
}

#[derive(Serialize)]
pub struct SupportBundleDiagnosticBundleCandidate {
    pub path: Option<String>,
    pub bundle_id: Option<String>,
    pub tile_index_exists: Option<bool>,
    pub field_proof_warning: Option<String>,
}

#[derive(Serialize)]
pub struct SupportBundleDiagnosticMapSourceCandidate {
    pub path: Option<String>,
    pub name: Option<String>,
    pub source: Option<String>,
    pub georef_source: Option<String>,
    pub source_format: Option<String>,
    pub requires_import: Option<bool>,
}

#[derive(Serialize)]
pub struct SupportBundleDiagnosticMissionPlanCandidate {
    pub path: Option<String>,
    pub plan_type: Option<String>,
    pub name: Option<String>,
    pub mission_item_count: Option<u64>,
    pub gnss_denied_status: Option<String>,
    pub has_gnss_denied: Option<bool>,
}

#[derive(Serialize)]
pub struct SupportBundleDiagnosticAction {
    pub id: Option<String>,
    pub status: Option<String>,
    pub title: Option<String>,
    pub desktop_action: Option<String>,
    pub command: Option<String>,
    pub notes: Option<String>,
    pub bundle_path: Option<String>,
    pub map_source_path: Option<String>,
    pub mission_plan_path: Option<String>,
    pub qgc_plan_path: Option<String>,
}

#[derive(Serialize)]
pub struct SupportBundleDiagnostic {
    pub bundle_exists: Option<bool>,
    pub missing_required_files: Vec<String>,
    pub search_root_count: Option<u64>,
    pub search_roots: Vec<String>,
    pub bundle_candidate_count: Option<u64>,
    pub map_source_candidate_count: Option<u64>,
    pub mission_plan_candidate_count: Option<u64>,
    pub bundle_candidates: Vec<SupportBundleDiagnosticBundleCandidate>,
    pub map_source_candidates: Vec<SupportBundleDiagnosticMapSourceCandidate>,
    pub mission_plan_candidates: Vec<SupportBundleDiagnosticMissionPlanCandidate>,
    pub recommended_actions: Vec<SupportBundleDiagnosticAction>,
}

#[derive(Serialize)]
pub struct SupportBundleFieldCapturePreflightCheck {
    pub name: Option<String>,
    pub status: Option<String>,
    pub message: Option<String>,
    pub path: Option<String>,
    pub desktop_action: Option<String>,
    pub validation_command: Option<String>,
    pub missing: Option<serde_json::Value>,
    pub issue_count: Option<u64>,
    pub bundle_diagnostic: Option<SupportBundleDiagnostic>,
}

#[derive(Serialize)]
pub struct SupportBundleFieldCapturePreflightAction {
    pub id: Option<String>,
    pub status: Option<String>,
    pub title: Option<String>,
    pub desktop_action: Option<String>,
    pub command: Option<String>,
    pub waits_on: Vec<String>,
    pub bundle_path: Option<String>,
    pub capture_output_dir: Option<String>,
    pub source_log: Option<String>,
    pub runtime_status_path: Option<String>,
    pub preflight_capture_command: Option<String>,
    pub notes: Option<String>,
    pub bundle_diagnostic: Option<SupportBundleDiagnostic>,
}

#[derive(Serialize)]
pub struct SupportBundleFieldCapturePreflightReport {
    pub status: Option<String>,
    pub plan_path: Option<String>,
    pub repo_root: Option<String>,
    pub condition: Option<String>,
    pub case_name: Option<String>,
    pub expected: Option<String>,
    pub bundle_path: Option<String>,
    pub bundle_validation_command: Option<String>,
    pub ready_for_capture: Option<bool>,
    pub ready_for_registration: Option<bool>,
    pub capture_output_dir: Option<String>,
    pub source_log: Option<String>,
    pub runtime_status_path: Option<String>,
    pub preflight_capture_command: Option<String>,
    pub summary: Option<serde_json::Value>,
    pub checks: Vec<SupportBundleFieldCapturePreflightCheck>,
    pub next_actions: Vec<SupportBundleFieldCapturePreflightAction>,
}

#[derive(Serialize)]
pub struct SupportBundleThresholdTuningReport {
    pub status: Option<String>,
    pub method: Option<String>,
    pub manifest_path: Option<String>,
    pub coverage_status: Option<String>,
    pub replay_status: Option<String>,
    pub case_count: Option<u64>,
    pub field_case_count: Option<u64>,
    pub capture_metadata_issue_count: Option<u64>,
    pub covered_conditions: Option<serde_json::Value>,
    pub margins: Option<serde_json::Value>,
}

#[derive(Serialize)]
pub struct SupportBundleRosbagExportValidationReport {
    pub status: Option<String>,
    pub format: Option<String>,
    pub artifact_path: Option<String>,
    pub metadata_path: Option<String>,
    pub message_count: Option<u64>,
    pub topic_count: Option<u64>,
    pub topics: Vec<String>,
    pub issues: Vec<String>,
}

#[derive(Serialize)]
pub struct SupportBundleRosbag2CliReviewReport {
    pub path: Option<String>,
    pub status: Option<String>,
    pub artifact_path: Option<String>,
    pub bag_dir: Option<String>,
    pub validation_status: Option<String>,
    pub validation_format: Option<String>,
    pub ros2_cli_status: Option<String>,
    pub ros2_cli_exit_code: Option<i64>,
    pub issues: Vec<String>,
}

#[derive(Serialize)]
pub struct RosbagExportValidationReportFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub report: SupportBundleRosbagExportValidationReport,
}

#[derive(Serialize)]
pub struct ThresholdTuningReportFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub report: SupportBundleThresholdTuningReport,
}

#[derive(Serialize)]
pub struct SupportBundleBenchReadinessCheck {
    pub name: Option<String>,
    pub status: Option<String>,
    pub message: Option<String>,
}

#[derive(Serialize)]
pub struct SupportBundleBenchReadinessReport {
    pub status: Option<String>,
    pub failed_count: Option<u64>,
    pub degraded_count: Option<u64>,
    pub passed_count: Option<u64>,
    pub checks: Vec<SupportBundleBenchReadinessCheck>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessCheck {
    pub name: Option<String>,
    pub status: Option<String>,
    pub message: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessBenchSubcheck {
    pub name: Option<String>,
    pub status: Option<String>,
    pub message: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessBenchEvidenceAction {
    pub label: Option<String>,
    pub desktop_action: Option<String>,
    pub command: Option<String>,
    pub blocked_by: Option<String>,
    pub notes: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessNextAction {
    pub check: Option<String>,
    pub status: Option<String>,
    pub title: Option<String>,
    pub desktop_action: Option<String>,
    pub command: Option<String>,
    pub notes: Option<String>,
    pub missing_conditions: Vec<String>,
    pub bench_subcheck: Option<String>,
    pub bench_message: Option<String>,
    pub bench_subchecks: Vec<AutonomyReadinessBenchSubcheck>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessEvidenceBlocker {
    pub name: Option<String>,
    pub status: Option<String>,
    pub message: Option<String>,
    pub missing_conditions: Vec<String>,
    pub bench_subchecks: Vec<AutonomyReadinessBenchSubcheck>,
    pub expected_bench_inputs: Vec<String>,
    pub support_bundle_command: Option<String>,
    pub bench_evidence_actions: Vec<AutonomyReadinessBenchEvidenceAction>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessEvidenceManifest {
    pub schema_version: Option<String>,
    pub ready_for_goal_completion: Option<bool>,
    pub proof_items: Vec<AutonomyReadinessEvidenceBlocker>,
    pub completion_blockers: Vec<AutonomyReadinessEvidenceBlocker>,
    pub external_blockers: Vec<AutonomyReadinessEvidenceBlocker>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessProofRunbookCheck {
    pub name: Option<String>,
    pub status: Option<String>,
    pub message: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessProofRunbookAction {
    pub check: Option<String>,
    pub status: Option<String>,
    pub title: Option<String>,
    pub desktop_action: Option<String>,
    pub command: Option<String>,
    pub notes: Option<String>,
    pub missing_conditions: Vec<String>,
    pub bench_subcheck: Option<String>,
    pub bench_message: Option<String>,
    pub bench_subchecks: Vec<AutonomyReadinessBenchSubcheck>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessProofRunbookPhase {
    pub id: Option<String>,
    pub title: Option<String>,
    pub status: Option<String>,
    pub depends_on: Vec<String>,
    pub dependency_status: BTreeMap<String, String>,
    pub checks: Vec<AutonomyReadinessProofRunbookCheck>,
    pub actions: Vec<AutonomyReadinessProofRunbookAction>,
    pub actions_truncated: Option<bool>,
    pub commands: Vec<String>,
    pub notes: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessProofRunbookSummary {
    pub phase_count: Option<u64>,
    pub passed: Option<u64>,
    pub action_required: Option<u64>,
    pub blocked: Option<u64>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessProofRunbook {
    pub schema_version: Option<String>,
    pub ready_for_goal_completion: Option<bool>,
    pub phases_truncated: Option<bool>,
    pub summary: AutonomyReadinessProofRunbookSummary,
    pub phases: Vec<AutonomyReadinessProofRunbookPhase>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessPlanSourceSnapshot {
    pub path: Option<String>,
    pub exists: Option<bool>,
    pub source_sha256: Option<String>,
    pub source_size_bytes: Option<u64>,
    pub required_marker_count: Option<u64>,
    pub missing_markers: Vec<String>,
    pub highest_value_reference_count: Option<u64>,
    pub fit_criteria_count: Option<u64>,
    pub architecture_section_count: Option<u64>,
    pub near_term_item_count: Option<u64>,
    pub avoid_choice_count: Option<u64>,
    pub track_count: Option<u64>,
    pub done_count: Option<u64>,
    pub in_progress_count: Option<u64>,
    pub task_count: Option<u64>,
    pub next_task_count: Option<u64>,
    pub acceptance_check_count: Option<u64>,
    pub execution_order_count: Option<u64>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessPlanSnapshot {
    pub schema_version: Option<String>,
    pub research_doc: Option<AutonomyReadinessPlanSourceSnapshot>,
    pub implementation_plan: Option<AutonomyReadinessPlanSourceSnapshot>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessSummary {
    pub status: Option<String>,
    pub failed_count: Option<u64>,
    pub degraded_count: Option<u64>,
    pub passed_count: Option<u64>,
    pub support_bundle_bench_readiness_status: Option<String>,
    pub px4_receiver_proof_status: Option<String>,
    pub field_collection_plan_status: Option<String>,
    pub field_evidence_proof_status: Option<String>,
    pub feature_method_benchmark_status: Option<String>,
    pub threshold_tuning_status: Option<String>,
    pub rosbag_export_validation_status: Option<String>,
    pub rosbag2_cli_review_status: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyEvidencePackageArtifactSummary {
    pub label: Option<String>,
    pub path: Option<String>,
    pub reason: Option<String>,
    pub status: Option<String>,
    pub message: Option<String>,
    pub source: Option<String>,
    pub missing_conditions: Vec<String>,
}

#[derive(Serialize)]
pub struct AutonomyEvidencePackageSummary {
    pub schema_version: Option<String>,
    pub readiness_status: Option<String>,
    pub ready_for_goal_completion: Option<bool>,
    pub readiness_report_metadata: Option<AutonomyReadinessAuditMetadata>,
    pub plan_snapshot: Option<AutonomyReadinessPlanSnapshot>,
    pub proof_item_count: Option<u64>,
    pub proof_item_passed_count: Option<u64>,
    pub completion_blocker_count: Option<u64>,
    pub external_blocker_count: Option<u64>,
    pub included_count: Option<u64>,
    pub missing_count: Option<u64>,
    pub skipped_count: Option<u64>,
    pub proof_runbook_summary: Option<AutonomyReadinessProofRunbook>,
    pub command_bundle: Option<AutonomyReadinessCommandBundle>,
    pub workflow_validation_summary: Option<AutonomyEvidenceWorkflowValidationSummary>,
    pub proof_items: Vec<AutonomyReadinessEvidenceBlocker>,
    pub included_artifacts: Vec<AutonomyEvidencePackageArtifactSummary>,
    pub missing_artifacts: Vec<AutonomyEvidencePackageArtifactSummary>,
    pub skipped_artifacts: Vec<AutonomyEvidencePackageArtifactSummary>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessAuditMetadata {
    pub schema_version: Option<String>,
    pub generated_at_utc: Option<String>,
    pub repo: Option<AutonomyReadinessAuditRepoMetadata>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessAuditRepoMetadata {
    pub detected: Option<bool>,
    pub root: Option<String>,
    pub path: Option<String>,
    pub branch: Option<String>,
    pub commit: Option<String>,
    pub dirty: Option<bool>,
    pub remote: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessReportFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub handoff_path: Option<String>,
    pub handoff_size_bytes: Option<u64>,
    pub handoff_modified_unix_ms: Option<u128>,
    pub evidence_package_path: Option<String>,
    pub evidence_package_size_bytes: Option<u64>,
    pub evidence_package_modified_unix_ms: Option<u128>,
    pub evidence_package_summary: Option<AutonomyEvidencePackageSummary>,
    pub metadata: Option<AutonomyReadinessAuditMetadata>,
    pub workflow_report_path: Option<String>,
    pub workflow_report_local_path: Option<String>,
    pub workflow_validation_path: Option<String>,
    pub workflow_validation_local_path: Option<String>,
    pub workflow_log_archive_path: Option<String>,
    pub workflow_log_archive_local_path: Option<String>,
    pub summary: AutonomyReadinessSummary,
    pub checks: Vec<AutonomyReadinessCheck>,
    pub next_actions: Vec<AutonomyReadinessNextAction>,
    pub command_bundle: Option<AutonomyReadinessCommandBundle>,
    pub evidence_manifest: Option<AutonomyReadinessEvidenceManifest>,
    pub proof_runbook: Option<AutonomyReadinessProofRunbook>,
    pub plan_snapshot: Option<AutonomyReadinessPlanSnapshot>,
    pub field_collection_plan: Option<AutonomyReadinessFieldCollectionPlan>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessCommandBundle {
    pub guided_workflow_commands: Vec<String>,
    pub prerequisite_fix_commands: Vec<String>,
    pub next_action_commands: Vec<String>,
    pub immediate_next_action_commands: Vec<String>,
    pub blocked_follow_up_commands: Vec<String>,
    pub field_collection_capture_commands: Vec<String>,
    pub field_collection_metadata_update_commands: Vec<String>,
    pub field_collection_registration_commands: Vec<String>,
    pub command_count: Option<u64>,
    pub command_items: Vec<AutonomyReadinessCommandItem>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessCommandItem {
    pub group: Option<String>,
    pub command: Option<String>,
    pub desktop_action: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyReadinessFieldCollectionPlan {
    pub path: String,
    pub status: Option<String>,
    pub site_name: Option<String>,
    pub manifest_path: Option<String>,
    pub bundle: Option<String>,
    pub summary: FieldCollectionPlanSummary,
    pub next_condition: Option<FieldCollectionPlanCondition>,
    pub pending_conditions: Vec<FieldCollectionPlanCondition>,
}

#[derive(Serialize)]
pub struct AutonomyEvidenceWorkflowStep {
    pub name: Option<String>,
    pub status: Option<String>,
    pub exit_code: Option<i64>,
    pub log_path: Option<String>,
    pub notes: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyEvidenceWorkflowSummary {
    pub passed: Option<u64>,
    pub failed: Option<u64>,
    pub skipped: Option<u64>,
}

#[derive(Serialize)]
pub struct AutonomyEvidenceWorkflowValidationCheck {
    pub name: Option<String>,
    pub status: Option<String>,
    pub message: Option<String>,
    pub marker_count: Option<u64>,
    pub missing_markers: Vec<String>,
    pub present_markers: Vec<String>,
    pub missing_steps: Vec<String>,
    pub non_passed_count: Option<u64>,
    pub non_passed_steps: Vec<AutonomyEvidenceWorkflowValidationStepResult>,
}

#[derive(Serialize)]
pub struct AutonomyEvidenceWorkflowValidationStepResult {
    pub name: Option<String>,
    pub status: Option<String>,
    pub exit_code: Option<i64>,
    pub notes: Option<String>,
    pub current_preflight_allows_capture: Option<bool>,
    pub current_preflight_report: Option<String>,
    pub current_preflight_status: Option<String>,
    pub current_ready_for_registration: Option<bool>,
    pub guidance: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyEvidenceWorkflowValidationNextStep {
    pub name: Option<String>,
    pub status: Option<String>,
    pub exit_code: Option<i64>,
    pub notes: Option<String>,
    pub command: Option<String>,
    pub desktop_action: Option<String>,
    pub metadata_update_command: Option<String>,
    pub bundle_path: Option<String>,
    pub expected_log: Option<String>,
    pub output_dir: Option<String>,
    pub runtime_status_path: Option<String>,
    pub capture_command_after_bundle: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyEvidenceWorkflowValidationSummary {
    pub status: Option<String>,
    pub workflow_status: Option<String>,
    pub step_count: Option<u64>,
    pub marker_count: Option<u64>,
    pub issue_count: u64,
    pub issues: Vec<String>,
    pub next_required_step: Option<AutonomyEvidenceWorkflowValidationNextStep>,
    pub checks: Vec<AutonomyEvidenceWorkflowValidationCheck>,
    pub log_archive: Option<String>,
}

#[derive(Serialize)]
pub struct AutonomyEvidenceWorkflowReportFile {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub modified_unix_ms: Option<u128>,
    pub status: Option<String>,
    pub generated_at: Option<String>,
    pub workflow_dir: Option<String>,
    pub summary: AutonomyEvidenceWorkflowSummary,
    pub steps: Vec<AutonomyEvidenceWorkflowStep>,
    pub marker_count: u64,
    pub workflow_logs_path: Option<String>,
    pub workflow_logs_local_path: Option<String>,
    pub workflow_validation_path: Option<String>,
    pub workflow_validation_local_path: Option<String>,
    pub workflow_validation_summary: Option<AutonomyEvidenceWorkflowValidationSummary>,
    pub support_bundle_path: Option<String>,
    pub support_bundle_local_path: Option<String>,
    pub field_evidence_report_path: Option<String>,
    pub field_evidence_report_local_path: Option<String>,
    pub feature_method_report_path: Option<String>,
    pub feature_method_report_local_path: Option<String>,
    pub threshold_report_path: Option<String>,
    pub threshold_report_local_path: Option<String>,
    pub rosbag_validation_path: Option<String>,
    pub rosbag_validation_local_path: Option<String>,
    pub readiness_report_path: Option<String>,
    pub readiness_report_local_path: Option<String>,
    pub handoff_path: Option<String>,
    pub handoff_local_path: Option<String>,
    pub evidence_package_path: Option<String>,
    pub evidence_package_local_path: Option<String>,
    pub field_collection_plan_path: Option<String>,
    pub field_collection_plan_local_path: Option<String>,
    pub field_collection_plan_markdown_path: Option<String>,
    pub field_collection_plan_markdown_local_path: Option<String>,
    pub field_metadata_update_command: Option<String>,
    pub px4_receiver_report_path: Option<String>,
    pub px4_receiver_report_local_path: Option<String>,
    pub px4_prereq_report_path: Option<String>,
    pub px4_prereq_report_local_path: Option<String>,
}

#[derive(Serialize)]
pub struct SupportBundleLogRecordPreview {
    pub line_number: usize,
    pub sequence: Option<u64>,
    pub timestamp_utc: Option<String>,
    pub timestamp_us: Option<u64>,
    pub status: Option<String>,
    pub reason: Option<String>,
    pub tile_id: Option<String>,
    pub map_id: Option<String>,
    pub confidence: Option<f64>,
    pub inliers: Option<u64>,
    pub reprojection_error_px: Option<f64>,
    pub external_position_status: Option<String>,
    pub external_position_message_type: Option<String>,
    pub external_position_warnings: Option<Vec<String>>,
}

#[derive(Serialize)]
pub struct SupportBundleLogPreview {
    pub name: String,
    pub records: Vec<SupportBundleLogRecordPreview>,
    pub truncated: bool,
}

#[derive(Serialize)]
pub struct SupportBundleLogTimelineSegment {
    pub index: usize,
    pub start_line: usize,
    pub end_line: usize,
    pub total_records: u64,
    pub accepted_rate: Option<f64>,
    pub dominant_status: Option<String>,
    pub average_confidence: Option<f64>,
    pub average_inliers: Option<f64>,
    pub average_reprojection_error_px: Option<f64>,
}

#[derive(Serialize)]
pub struct SupportBundleLogTimeline {
    pub name: String,
    pub path: String,
    pub size_bytes: u64,
    pub total_records: Option<u64>,
    pub invalid_records: u64,
    pub accepted_rate: Option<f64>,
    pub status_counts: Option<serde_json::Value>,
    pub reason_counts: Option<serde_json::Value>,
    pub external_position_status_counts: Option<serde_json::Value>,
    pub external_position_warning_counts: Option<serde_json::Value>,
    pub first_sequence: Option<u64>,
    pub last_sequence: Option<u64>,
    pub first_timestamp_us: Option<u64>,
    pub last_timestamp_us: Option<u64>,
    pub average_confidence: Option<f64>,
    pub average_inliers: Option<f64>,
    pub average_reprojection_error_px: Option<f64>,
    pub segments: Vec<SupportBundleLogTimelineSegment>,
    pub truncated: bool,
}

#[derive(Serialize)]
pub struct SupportBundleImagePreview {
    pub name: String,
    pub path: String,
    pub mime_type: String,
    pub size_bytes: u64,
    pub base64_data: String,
}

#[derive(Serialize, Clone)]
pub struct SupportBundleArtifactEntry {
    pub name: String,
    pub path: String,
    pub kind: String,
    pub size_bytes: u64,
}

#[derive(Serialize)]
pub struct ExtractedSupportBundleArtifact {
    pub name: String,
    pub entry_path: String,
    pub path: String,
    pub size_bytes: u64,
}

#[derive(Serialize)]
pub struct SupportBundleDetails {
    pub manifest: serde_json::Value,
    pub metadata: Option<serde_json::Value>,
    pub bundle_health: Option<serde_json::Value>,
    pub logs: Vec<SupportBundleLogSummary>,
    pub runtime_statuses: Vec<serde_json::Value>,
    pub log_previews: Vec<SupportBundleLogPreview>,
    pub log_timelines: Vec<SupportBundleLogTimeline>,
    pub image_previews: Vec<SupportBundleImagePreview>,
    pub replay_reports: Vec<SupportBundleReplayReport>,
    pub px4_evidence_reports: Vec<SupportBundlePx4EvidenceReport>,
    pub px4_param_reports: Vec<SupportBundlePx4ParamReport>,
    pub ardupilot_param_reports: Vec<SupportBundleArduPilotParamReport>,
    pub feature_method_benchmark_reports: Vec<SupportBundleFeatureMethodBenchmarkReport>,
    pub field_evidence_reports: Vec<SupportBundleFieldEvidenceReport>,
    pub field_collection_plan_reports: Vec<SupportBundleFieldCollectionPlanReport>,
    pub field_capture_preflight_reports: Vec<SupportBundleFieldCapturePreflightReport>,
    pub threshold_tuning_reports: Vec<SupportBundleThresholdTuningReport>,
    pub rosbag_export_validation_reports: Vec<SupportBundleRosbagExportValidationReport>,
    pub rosbag2_cli_review_reports: Vec<SupportBundleRosbag2CliReviewReport>,
    pub autonomy_evidence_workflow_validation: Option<AutonomyEvidenceWorkflowValidationSummary>,
    pub bench_readiness: Option<SupportBundleBenchReadinessReport>,
    pub artifacts: Vec<SupportBundleArtifactEntry>,
    pub entry_count: usize,
}

#[tauri::command]
pub fn read_yaml_config(path: String) -> Result<serde_json::Value, String> {
    let text = std::fs::read_to_string(&path)
        .with_context(|| format!("Cannot read {path}"))
        .map_err(|e| e.to_string())?;
    let val: serde_yaml::Value = serde_yaml::from_str(&text).map_err(|e| e.to_string())?;
    serde_json::to_value(val).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn write_yaml_config(path: String, data: serde_json::Value) -> Result<(), String> {
    let yaml_val: serde_yaml::Value = serde_json::from_value(data).map_err(|e| e.to_string())?;
    let text = serde_yaml::to_string(&yaml_val).map_err(|e| e.to_string())?;
    if let Some(parent) = Path::new(&path).parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(&path, text).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn list_yaml_configs(dir: String) -> Result<Vec<String>, String> {
    let path = Path::new(&dir);
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(path).map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) == Some("yaml") {
            files.push(p.to_string_lossy().into_owned());
        }
    }
    Ok(files)
}

#[tauri::command]
pub fn list_support_bundles(dir: String) -> Result<Vec<SupportBundleFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("zip") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        files.push(SupportBundleFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("support.zip")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            summary: read_support_bundle_summary(&p),
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

#[tauri::command]
pub fn reveal_support_bundle(path: String) -> Result<(), String> {
    let path = expand_local_path(&path).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Err(format!("Support bundle does not exist: {}", path.display()));
    }
    reveal_path(&path).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn delete_support_bundle(path: String) -> Result<(), String> {
    let path = expand_local_path(&path).map_err(|e| e.to_string())?;
    if path.extension().and_then(|ext| ext.to_str()) != Some("zip") {
        return Err("Only support bundle ZIP files can be deleted from the app.".to_string());
    }
    if !path.exists() {
        return Ok(());
    }
    std::fs::remove_file(&path)
        .with_context(|| format!("Cannot delete support bundle {}", path.display()))
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn run_local_autonomy_readiness_audit(
    repo_dir: String,
    download_root: Option<String>,
) -> Result<LocalCommandResult, String> {
    tokio::task::spawn_blocking(move || {
        run_local_autonomy_readiness_audit_inner(&repo_dir, download_root.as_deref())
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}

fn run_local_autonomy_readiness_audit_inner(
    repo_dir: &str,
    download_root: Option<&str>,
) -> Result<LocalCommandResult> {
    let repo = expand_local_path(repo_dir)?;
    let script = repo.join("scripts/dev/run_local_autonomy_readiness_audit.sh");
    if !script.is_file() {
        return Err(anyhow!(
            "Missing local autonomy readiness wrapper: {}",
            script.display()
        ));
    }

    let mut command = Command::new("bash");
    command.arg(&script).current_dir(&repo);
    if let Some(root) = download_root.filter(|value| !value.trim().is_empty()) {
        command.env(
            "VISION_NAV_DESKTOP_TRANSFER_FROM_PI",
            expand_local_path(root)?,
        );
    }
    let output = command
        .output()
        .with_context(|| format!("Failed to run {}", script.display()))?;
    Ok(LocalCommandResult {
        exit_code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).to_string(),
        stderr: String::from_utf8_lossy(&output.stderr).to_string(),
    })
}

#[tauri::command]
pub async fn run_local_rosbag2_cli_review(
    repo_dir: String,
    download_root: Option<String>,
) -> Result<LocalCommandResult, String> {
    tokio::task::spawn_blocking(move || {
        run_local_rosbag2_cli_review_inner(&repo_dir, download_root.as_deref())
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}

fn run_local_rosbag2_cli_review_inner(
    repo_dir: &str,
    download_root: Option<&str>,
) -> Result<LocalCommandResult> {
    let repo = expand_local_path(repo_dir)?;
    let script = repo.join("scripts/dev/run_rosbag2_cli_review.sh");
    if !script.is_file() {
        return Err(anyhow!(
            "Missing local rosbag2 CLI review wrapper: {}",
            script.display()
        ));
    }

    let download_root = expand_local_path(download_root.unwrap_or("~/DroneTransfer/from-pi"))?;
    let terrain_dir = download_root.join("terrain-match");
    std::fs::create_dir_all(&terrain_dir)
        .with_context(|| format!("Cannot create {}", terrain_dir.display()))?;
    let source_log = terrain_dir.join("terrain_matches.jsonl");
    let export_dir = terrain_dir.join("rosbag2-native");
    let review_report = terrain_dir.join("rosbag2-cli-review.json");

    let output = Command::new("bash")
        .arg(&script)
        .current_dir(&repo)
        .env("VISION_NAV_ROSBAG_SOURCE_LOG", &source_log)
        .env("VISION_NAV_ROSBAG2_EXPORT_DIR", &export_dir)
        .env("VISION_NAV_ROSBAG2_CLI_REVIEW", &review_report)
        .output()
        .with_context(|| format!("Failed to run {}", script.display()))?;
    Ok(LocalCommandResult {
        exit_code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).to_string(),
        stderr: String::from_utf8_lossy(&output.stderr).to_string(),
    })
}

#[tauri::command]
pub async fn run_local_px4_sitl_receiver_capture(
    repo_dir: String,
    download_root: Option<String>,
) -> Result<LocalCommandResult, String> {
    tokio::task::spawn_blocking(move || {
        run_local_px4_sitl_receiver_capture_inner(&repo_dir, download_root.as_deref())
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn run_local_px4_sitl_prereq_setup(
    repo_dir: String,
    download_root: Option<String>,
) -> Result<LocalCommandResult, String> {
    tokio::task::spawn_blocking(move || {
        run_local_px4_sitl_prereq_setup_inner(&repo_dir, download_root.as_deref())
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}

fn run_local_px4_sitl_prereq_setup_inner(
    repo_dir: &str,
    download_root: Option<&str>,
) -> Result<LocalCommandResult> {
    let repo = expand_local_path(repo_dir)?;
    let script = repo.join("scripts/dev/setup_px4_sitl_prereqs.sh");
    if !script.is_file() {
        return Err(anyhow!(
            "Missing local PX4 SITL prerequisite setup helper: {}",
            script.display()
        ));
    }

    let download_root = expand_local_path(download_root.unwrap_or("~/DroneTransfer/from-pi"))?;
    let session_dir = download_root.join("px4-sitl-evidence");
    std::fs::create_dir_all(&session_dir)
        .with_context(|| format!("Cannot create {}", session_dir.display()))?;
    let px4_dir = match std::env::var_os("VISION_NAV_PX4_AUTOPILOT_DIR") {
        Some(value) => PathBuf::from(value),
        None => expand_local_path("~/PX4-Autopilot")?,
    };

    let output = Command::new("bash")
        .arg(&script)
        .arg("--clone-px4")
        .arg("--px4-dir")
        .arg(&px4_dir)
        .current_dir(&repo)
        .env("VISION_NAV_SITL_SMOKE_DIR", &session_dir)
        .output()
        .with_context(|| format!("Failed to run {}", script.display()))?;
    Ok(LocalCommandResult {
        exit_code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).to_string(),
        stderr: String::from_utf8_lossy(&output.stderr).to_string(),
    })
}

fn run_local_px4_sitl_receiver_capture_inner(
    repo_dir: &str,
    download_root: Option<&str>,
) -> Result<LocalCommandResult> {
    let repo = expand_local_path(repo_dir)?;
    let script = repo.join("scripts/dev/run_px4_sitl_external_vision_capture.sh");
    if !script.is_file() {
        return Err(anyhow!(
            "Missing local PX4 SITL receiver capture wrapper: {}",
            script.display()
        ));
    }

    let download_root = expand_local_path(download_root.unwrap_or("~/DroneTransfer/from-pi"))?;
    let session_dir = download_root.join("px4-sitl-evidence");
    std::fs::create_dir_all(&session_dir)
        .with_context(|| format!("Cannot create {}", session_dir.display()))?;

    let output = Command::new("bash")
        .arg(&script)
        .current_dir(&repo)
        .env("VISION_NAV_SITL_SMOKE_DIR", &session_dir)
        .output()
        .with_context(|| format!("Failed to run {}", script.display()))?;
    Ok(LocalCommandResult {
        exit_code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).to_string(),
        stderr: String::from_utf8_lossy(&output.stderr).to_string(),
    })
}

#[tauri::command]
pub fn read_support_bundle_details(path: String) -> Result<SupportBundleDetails, String> {
    let path = expand_local_path(&path).map_err(|e| e.to_string())?;
    let file = File::open(&path)
        .with_context(|| format!("Cannot open support bundle {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut archive = ZipArchive::new(file).map_err(|e| e.to_string())?;
    let entry_count = archive.len();
    let manifest = read_json_entry(&mut archive, "support_manifest.json")?
        .ok_or_else(|| "Missing support_manifest.json".to_string())?;
    let mut logs = Vec::new();
    let mut runtime_statuses: Vec<serde_json::Value> = manifest
        .pointer("/logs/runtime_statuses")
        .and_then(|value| value.as_array())
        .cloned()
        .unwrap_or_default();
    let mut log_previews = Vec::new();
    let mut log_timelines = Vec::new();
    let mut image_previews = Vec::new();
    let mut replay_reports = Vec::new();
    let mut px4_evidence_reports = Vec::new();
    let mut px4_param_reports = Vec::new();
    let mut ardupilot_param_reports = Vec::new();
    let mut feature_method_benchmark_reports = Vec::new();
    let mut field_evidence_reports = Vec::new();
    let mut field_collection_plan_reports = Vec::new();
    let mut field_capture_preflight_reports = Vec::new();
    let mut threshold_tuning_reports = Vec::new();
    let mut rosbag_export_validation_reports = Vec::new();
    let mut rosbag2_cli_review_reports = Vec::new();
    let mut autonomy_evidence_workflow_validation = manifest
        .pointer("/autonomy_evidence_workflow/validation_summary")
        .and_then(workflow_validation_summary_from_json);
    let mut artifacts = Vec::new();
    let mut bench_readiness = manifest
        .get("bench_readiness")
        .map(bench_readiness_report_from_json);
    for index in 0..archive.len() {
        let (name, size_bytes) = {
            let entry = archive.by_index(index).map_err(|e| e.to_string())?;
            (entry.name().to_string(), entry.size())
        };
        if let Some(artifact) = support_artifact_entry(&name, size_bytes) {
            artifacts.push(artifact);
        }
        if name.starts_with("summaries/")
            && name.ends_with(".summary.json")
            && !name.contains("/replay_gates/")
            && !name.starts_with("summaries/autonomy_evidence_workflow/")
        {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                logs.push(log_summary_from_json(&name, &value));
            }
        } else if runtime_statuses.is_empty()
            && name.starts_with("logs/")
            && name.ends_with(".runtime_status.json")
        {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                runtime_statuses.push(value);
            }
        } else if name.starts_with("logs/") && name.ends_with(".jsonl") {
            if let Some(preview) = read_log_preview_entry(&mut archive, &name)? {
                log_previews.push(preview);
            }
            if let Some(timeline) = read_log_timeline_entry(&mut archive, &name, size_bytes)? {
                log_timelines.push(timeline);
            }
        } else if name.starts_with("summaries/replay_gates/") && name.ends_with(".gate.json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                replay_reports.push(replay_report_from_json(&value));
            }
        } else if name.starts_with("summaries/px4_sitl_evidence/") && name.ends_with(".json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                px4_evidence_reports.push(px4_evidence_report_from_json(&value));
            }
        } else if name.starts_with("summaries/px4_params/") && name.ends_with(".json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                px4_param_reports.push(px4_param_report_from_json(&value));
            }
        } else if name.starts_with("summaries/ardupilot_params/") && name.ends_with(".json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                ardupilot_param_reports.push(ardupilot_param_report_from_json(&value));
            }
        } else if name.starts_with("summaries/feature_method_benchmarks/")
            && name.ends_with(".json")
        {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                feature_method_benchmark_reports.push(feature_method_report_from_json(&value));
            }
        } else if name.starts_with("summaries/field_evidence/") && name.ends_with(".json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                field_evidence_reports.push(field_evidence_report_from_json(&value));
            }
        } else if name.starts_with("summaries/field_collection_plans/") && name.ends_with(".json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                if let Some(report) = field_collection_plan_report_from_json(&value) {
                    field_collection_plan_reports.push(report);
                }
            }
        } else if name.starts_with("summaries/field_capture_preflights/") && name.ends_with(".json")
        {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                if let Some(report) = field_capture_preflight_report_from_json(&value) {
                    field_capture_preflight_reports.push(report);
                }
            }
        } else if name.starts_with("summaries/threshold_tuning/") && name.ends_with(".json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                threshold_tuning_reports.push(threshold_tuning_report_from_json(&value));
            }
        } else if name.starts_with("summaries/rosbag_export_validations/")
            && name.ends_with(".json")
        {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                rosbag_export_validation_reports
                    .push(rosbag_export_validation_report_from_json(&value));
            }
        } else if name.starts_with("summaries/rosbag2_cli_reviews/") && name.ends_with(".json") {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                rosbag2_cli_review_reports.push(rosbag2_cli_review_report_from_json(&value));
            }
        } else if name == "summaries/autonomy_evidence_workflow/workflow_validation.summary.json"
            || name == "summaries/autonomy_evidence_workflow/workflow_validation.computed.json"
        {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                if let Some(summary) = workflow_validation_summary_from_json(&value) {
                    autonomy_evidence_workflow_validation = Some(summary);
                }
            }
        } else if name == "summaries/bench_readiness.json" {
            if let Some(value) = read_json_entry(&mut archive, &name)? {
                bench_readiness = Some(bench_readiness_report_from_json(&value));
            }
        } else if image_previews.len() < IMAGE_PREVIEW_LIMIT
            && should_preview_image_entry(&name, size_bytes)
        {
            if let Some(preview) = read_image_preview_entry(&mut archive, &name, size_bytes)? {
                image_previews.push(preview);
            }
        }
    }
    Ok(SupportBundleDetails {
        metadata: manifest.get("metadata").cloned(),
        bundle_health: manifest.pointer("/bundle/health").cloned(),
        logs,
        runtime_statuses,
        log_previews,
        log_timelines,
        image_previews,
        replay_reports,
        px4_evidence_reports,
        px4_param_reports,
        ardupilot_param_reports,
        feature_method_benchmark_reports,
        field_evidence_reports,
        field_collection_plan_reports,
        field_capture_preflight_reports,
        threshold_tuning_reports,
        rosbag_export_validation_reports,
        rosbag2_cli_review_reports,
        autonomy_evidence_workflow_validation,
        bench_readiness,
        artifacts,
        entry_count,
        manifest,
    })
}

#[tauri::command]
pub fn extract_support_bundle_artifact(
    path: String,
    entry_path: String,
) -> Result<ExtractedSupportBundleArtifact, String> {
    let bundle_path = expand_local_path(&path).map_err(|e| e.to_string())?;
    if bundle_path.extension().and_then(|ext| ext.to_str()) != Some("zip") {
        return Err("Only support bundle ZIP files can be extracted from the app.".to_string());
    }
    let file = File::open(&bundle_path)
        .with_context(|| format!("Cannot open support bundle {}", bundle_path.display()))
        .map_err(|e| e.to_string())?;
    let mut archive = ZipArchive::new(file).map_err(|e| e.to_string())?;
    let mut entry = archive
        .by_name(&entry_path)
        .with_context(|| format!("Support bundle entry not found: {entry_path}"))
        .map_err(|e| e.to_string())?;
    if entry.is_dir() {
        return Err("Support bundle directories cannot be extracted directly.".to_string());
    }
    let size_bytes = entry.size();
    let artifact = support_artifact_entry(&entry_path, size_bytes).ok_or_else(|| {
        "This support bundle entry is not an extractable diagnostic artifact.".to_string()
    })?;
    let rel_path = safe_zip_entry_rel_path(&entry_path)?;
    let stem = bundle_path
        .file_stem()
        .and_then(|name| name.to_str())
        .unwrap_or("support-bundle");
    let output_root = bundle_path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(format!("{stem}-artifacts"));
    let output_path = output_root.join(rel_path);
    if let Some(parent) = output_path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("Cannot create {}", parent.display()))
            .map_err(|e| e.to_string())?;
    }
    let mut output = File::create(&output_path)
        .with_context(|| format!("Cannot write {}", output_path.display()))
        .map_err(|e| e.to_string())?;
    std::io::copy(&mut entry, &mut output)
        .with_context(|| format!("Cannot extract support bundle entry {entry_path}"))
        .map_err(|e| e.to_string())?;
    Ok(ExtractedSupportBundleArtifact {
        name: artifact.name,
        entry_path,
        path: output_path.to_string_lossy().into_owned(),
        size_bytes,
    })
}

#[tauri::command]
pub fn list_autonomy_readiness_reports(
    dir: String,
) -> Result<Vec<AutonomyReadinessReportFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let text = match std::fs::read_to_string(&p) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let (summary, checks, next_actions, evidence_manifest) =
            match autonomy_readiness_report_from_json(&value) {
                Some(value) => value,
                None => continue,
            };
        let command_bundle = autonomy_readiness_command_bundle_from_json(&value);
        let plan_snapshot = autonomy_readiness_plan_snapshot_from_json(value.get("plan_snapshot"));
        let proof_runbook = autonomy_readiness_proof_runbook_from_json(value.get("proof_runbook"));
        let audit_metadata = autonomy_readiness_audit_metadata_from_json(value.get("metadata"));
        let field_collection_plan = autonomy_readiness_field_collection_plan_from_json(&value, &p);
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        let handoff_path = p.with_extension("md");
        let handoff_metadata = handoff_path
            .metadata()
            .ok()
            .filter(|_| handoff_path.is_file());
        let evidence_package_path = p.with_extension("evidence.zip");
        let evidence_package_metadata = evidence_package_path
            .metadata()
            .ok()
            .filter(|_| evidence_package_path.is_file());
        let evidence_package_summary = evidence_package_metadata
            .as_ref()
            .and_then(|_| read_autonomy_evidence_package_summary(&evidence_package_path));
        let workflow_report_path =
            autonomy_readiness_input_path(&value, "evidence_workflow_report");
        let workflow_report_local_path =
            workflow_artifact_local_path(&p, workflow_report_path.as_deref(), None);
        let workflow_validation_path =
            autonomy_readiness_input_path(&value, "evidence_workflow_validation_report");
        let workflow_validation_local_path =
            workflow_artifact_local_path(&p, workflow_validation_path.as_deref(), None);
        let workflow_log_archive_path =
            autonomy_readiness_input_path(&value, "evidence_workflow_log_archive");
        let workflow_log_archive_local_path =
            workflow_artifact_local_path(&p, workflow_log_archive_path.as_deref(), None);
        files.push(AutonomyReadinessReportFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("autonomy_readiness_report.json")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            handoff_path: handoff_metadata
                .as_ref()
                .map(|_| handoff_path.to_string_lossy().into_owned()),
            handoff_size_bytes: handoff_metadata.as_ref().map(|metadata| metadata.len()),
            handoff_modified_unix_ms: handoff_metadata
                .and_then(|metadata| metadata.modified().ok())
                .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
                .map(|duration| duration.as_millis()),
            evidence_package_path: evidence_package_metadata
                .as_ref()
                .map(|_| evidence_package_path.to_string_lossy().into_owned()),
            evidence_package_size_bytes: evidence_package_metadata
                .as_ref()
                .map(|metadata| metadata.len()),
            evidence_package_modified_unix_ms: evidence_package_metadata
                .and_then(|metadata| metadata.modified().ok())
                .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
                .map(|duration| duration.as_millis()),
            evidence_package_summary,
            metadata: audit_metadata,
            workflow_report_path,
            workflow_report_local_path,
            workflow_validation_path,
            workflow_validation_local_path,
            workflow_log_archive_path,
            workflow_log_archive_local_path,
            summary,
            checks,
            next_actions,
            command_bundle,
            evidence_manifest,
            proof_runbook,
            plan_snapshot,
            field_collection_plan,
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

fn autonomy_readiness_input_path(value: &serde_json::Value, key: &str) -> Option<String> {
    value
        .pointer(&format!("/inputs/{key}"))
        .and_then(|value| value.as_str())
        .filter(|value| !value.is_empty())
        .map(|value| value.to_string())
}

#[tauri::command]
pub fn list_autonomy_evidence_workflow_reports(
    dir: String,
) -> Result<Vec<AutonomyEvidenceWorkflowReportFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let text = match std::fs::read_to_string(&p) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let mut report = match autonomy_evidence_workflow_report_from_json(&value) {
            Some(value) => value,
            None => continue,
        };
        populate_workflow_report_local_artifacts(&mut report, &p);
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        files.push(AutonomyEvidenceWorkflowReportFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("autonomy_evidence_workflow.json")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            ..report
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

#[tauri::command]
pub fn list_field_evidence_reports(dir: String) -> Result<Vec<FieldEvidenceReportFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let text = match std::fs::read_to_string(&p) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if !value.get("coverage").is_some_and(|value| value.is_object())
            || !value
                .get("replay_gates")
                .is_some_and(|value| value.is_object())
        {
            continue;
        }
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        files.push(FieldEvidenceReportFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("field_evidence_report.json")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            report: field_evidence_report_from_json(&value),
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

#[tauri::command]
pub fn list_field_evidence_templates(
    dir: String,
) -> Result<Vec<FieldEvidenceTemplateFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let text = match std::fs::read_to_string(&p) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let template = match value.get("template").and_then(|value| value.as_object()) {
            Some(value) => value,
            None => continue,
        };
        if template
            .get("schema_version")
            .and_then(|value| value.as_str())
            != Some("vision_nav_field_evidence_template_v1")
        {
            continue;
        }
        let cases = value
            .get("cases")
            .and_then(|value| value.as_array())
            .cloned()
            .unwrap_or_default();
        let mut conditions = vec![];
        let mut placeholder_conditions = vec![];
        let mut registered_conditions = vec![];
        for case in &cases {
            let case_conditions = json_string_array(case.get("conditions"));
            let is_placeholder = case.get("template_status").is_some();
            for condition in case_conditions {
                if !conditions.contains(&condition) {
                    conditions.push(condition.clone());
                }
                if is_placeholder {
                    if !placeholder_conditions.contains(&condition) {
                        placeholder_conditions.push(condition);
                    }
                } else if !registered_conditions.contains(&condition) {
                    registered_conditions.push(condition);
                }
            }
        }
        let placeholder_count = cases
            .iter()
            .filter(|case| case.get("template_status").is_some())
            .count() as u64;
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        files.push(FieldEvidenceTemplateFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("field_manifest.template.json")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            site_name: json_string(template.get("site_name")),
            case_count: cases.len() as u64,
            placeholder_count,
            required_conditions: json_string_array(template.get("required_conditions")),
            conditions,
            placeholder_conditions,
            registered_conditions,
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

#[tauri::command]
pub fn list_field_collection_plans(dir: String) -> Result<Vec<FieldCollectionPlanFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let text = match std::fs::read_to_string(&p) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let mut plan = match field_collection_plan_from_json(&value) {
            Some(value) => value,
            None => continue,
        };
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        let markdown_path = p.with_extension("md");
        let (markdown_path, markdown_size_bytes, markdown_modified_unix_ms) =
            if markdown_path.exists() {
                let markdown_metadata = std::fs::metadata(&markdown_path).ok();
                (
                    Some(markdown_path.to_string_lossy().into_owned()),
                    markdown_metadata.as_ref().map(|metadata| metadata.len()),
                    markdown_metadata
                        .and_then(|metadata| metadata.modified().ok())
                        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
                        .map(|duration| duration.as_millis()),
                )
            } else {
                (None, None, None)
            };
        plan.name = p
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("field_collection_plan.json")
            .to_string();
        plan.path = p.to_string_lossy().into_owned();
        plan.size_bytes = metadata.len();
        plan.modified_unix_ms = modified_unix_ms;
        plan.markdown_path = markdown_path;
        plan.markdown_size_bytes = markdown_size_bytes;
        plan.markdown_modified_unix_ms = markdown_modified_unix_ms;
        files.push(plan);
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

#[tauri::command]
pub fn list_feature_method_benchmark_reports(
    dir: String,
) -> Result<Vec<FeatureMethodBenchmarkReportFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let text = match std::fs::read_to_string(&p) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if !value.get("methods").is_some_and(|value| value.is_array()) {
            continue;
        }
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        files.push(FeatureMethodBenchmarkReportFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("feature-method-benchmark.json")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            report: feature_method_report_from_json(&value),
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

#[tauri::command]
pub fn list_px4_receiver_reports(dir: String) -> Result<Vec<Px4ReceiverReportFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let text = match std::fs::read_to_string(&p) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if !value.get("listener").is_some_and(|value| value.is_object()) {
            continue;
        }
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        files.push(Px4ReceiverReportFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("receiver_evidence.json")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            report: px4_evidence_report_from_json(&value),
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

#[tauri::command]
pub fn list_px4_prereq_reports(dir: String) -> Result<Vec<Px4PrereqReportFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let text = match std::fs::read_to_string(&p) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let Some(report) = px4_prereq_report_from_json(&value) else {
            continue;
        };
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        files.push(Px4PrereqReportFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("px4_sitl_capture_prereqs.json")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            report,
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

#[tauri::command]
pub fn list_threshold_tuning_reports(
    dir: String,
) -> Result<Vec<ThresholdTuningReportFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let text = match std::fs::read_to_string(&p) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if !value.get("method").is_some_and(|value| value.is_string())
            || !value.get("summary").is_some_and(|value| value.is_object())
        {
            continue;
        }
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        files.push(ThresholdTuningReportFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("threshold_tuning_report.json")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            report: threshold_tuning_report_from_json(&value),
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

#[tauri::command]
pub fn list_rosbag_export_validation_reports(
    dir: String,
) -> Result<Vec<RosbagExportValidationReportFile>, String> {
    let path = expand_local_path(&dir).map_err(|e| e.to_string())?;
    if !path.exists() {
        return Ok(vec![]);
    }
    let entries = std::fs::read_dir(&path)
        .with_context(|| format!("Cannot read {}", path.display()))
        .map_err(|e| e.to_string())?;
    let mut files = vec![];
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(value) => value,
            Err(_) => continue,
        };
        let text = match std::fs::read_to_string(&p) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let value: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if value.get("schema_version").and_then(|value| value.as_str())
            != Some("vision_nav_rosbag_export_validation_v1")
        {
            continue;
        }
        let modified_unix_ms = metadata
            .modified()
            .ok()
            .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
            .map(|duration| duration.as_millis());
        files.push(RosbagExportValidationReportFile {
            name: p
                .file_name()
                .and_then(|name| name.to_str())
                .unwrap_or("rosbag-jsonl-validation.json")
                .to_string(),
            path: p.to_string_lossy().into_owned(),
            size_bytes: metadata.len(),
            modified_unix_ms,
            report: rosbag_export_validation_report_from_json(&value),
        });
    }
    files.sort_by(|a, b| {
        b.modified_unix_ms
            .cmp(&a.modified_unix_ms)
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(files)
}

fn read_json_entry(
    archive: &mut ZipArchive<File>,
    name: &str,
) -> Result<Option<serde_json::Value>, String> {
    let mut entry = match archive.by_name(name) {
        Ok(entry) => entry,
        Err(zip::result::ZipError::FileNotFound) => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    let mut text = String::new();
    entry.read_to_string(&mut text).map_err(|e| e.to_string())?;
    serde_json::from_str(&text)
        .map(Some)
        .with_context(|| format!("Invalid JSON in support bundle entry {name}"))
        .map_err(|e| e.to_string())
}

fn read_autonomy_evidence_package_summary(path: &Path) -> Option<AutonomyEvidencePackageSummary> {
    let file = File::open(path).ok()?;
    let mut archive = ZipArchive::new(file).ok()?;
    let manifest = read_json_entry(&mut archive, "manifest.json")
        .ok()
        .flatten()?;
    let schema_version = json_string(manifest.get("schema_version"));
    if schema_version.as_deref() != Some("vision_nav_autonomy_evidence_package_v1") {
        return None;
    }
    let proof_summary = manifest.get("proof_summary");
    Some(AutonomyEvidencePackageSummary {
        schema_version,
        readiness_status: json_string(manifest.get("readiness_status")),
        ready_for_goal_completion: manifest
            .get("ready_for_goal_completion")
            .and_then(|value| value.as_bool()),
        readiness_report_metadata: autonomy_readiness_audit_metadata_from_json(
            manifest.get("readiness_report_metadata"),
        ),
        plan_snapshot: autonomy_readiness_plan_snapshot_from_json(manifest.get("plan_snapshot")),
        proof_item_count: proof_summary
            .and_then(|value| value.get("proof_item_count"))
            .and_then(|value| value.as_u64()),
        proof_item_passed_count: proof_summary
            .and_then(|value| value.get("proof_item_passed_count"))
            .and_then(|value| value.as_u64()),
        completion_blocker_count: proof_summary
            .and_then(|value| value.get("completion_blocker_count"))
            .and_then(|value| value.as_u64()),
        external_blocker_count: proof_summary
            .and_then(|value| value.get("external_blocker_count"))
            .and_then(|value| value.as_u64()),
        included_count: json_array_count(manifest.get("included")),
        missing_count: json_array_count(manifest.get("missing")),
        skipped_count: json_array_count(manifest.get("skipped")),
        proof_runbook_summary: autonomy_readiness_proof_runbook_from_json(
            manifest.get("proof_runbook_summary"),
        ),
        command_bundle: autonomy_readiness_command_bundle_from_bundle_json(
            manifest.get("command_bundle"),
        ),
        workflow_validation_summary: manifest
            .get("workflow_validation_summary")
            .and_then(workflow_validation_summary_from_json),
        proof_items: autonomy_evidence_blockers_from_value(
            proof_summary.and_then(|value| value.get("proof_items")),
        ),
        included_artifacts: evidence_package_artifacts(manifest.get("included")),
        missing_artifacts: evidence_package_artifacts(manifest.get("missing")),
        skipped_artifacts: evidence_package_artifacts(manifest.get("skipped")),
    })
}

fn json_array_count(value: Option<&serde_json::Value>) -> Option<u64> {
    value
        .and_then(|value| value.as_array())
        .map(|values| values.len() as u64)
}

fn evidence_package_artifacts(
    value: Option<&serde_json::Value>,
) -> Vec<AutonomyEvidencePackageArtifactSummary> {
    value
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .take(12)
                .filter_map(|item| {
                    if !item.is_object() {
                        return None;
                    }
                    Some(AutonomyEvidencePackageArtifactSummary {
                        label: json_string(item.get("label")),
                        path: json_string(item.get("path")),
                        reason: json_string(item.get("reason")),
                        status: json_string(item.get("status")),
                        message: json_string(item.get("message")),
                        source: json_string(item.get("source")),
                        missing_conditions: json_string_array(item.get("missing_conditions")),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

fn log_summary_from_json(name: &str, value: &serde_json::Value) -> SupportBundleLogSummary {
    SupportBundleLogSummary {
        name: display_entry_name(name),
        total_records: value.get("total_records").and_then(|value| value.as_u64()),
        accepted_rate: value.get("accepted_rate").and_then(|value| value.as_f64()),
        status_counts: value.get("status_counts").cloned(),
        reason_counts: value.get("reason_counts").cloned(),
        external_position: value.get("external_position").cloned(),
    }
}

fn display_entry_name(name: &str) -> String {
    Path::new(name)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(name)
        .to_string()
}

fn read_log_preview_entry(
    archive: &mut ZipArchive<File>,
    name: &str,
) -> Result<Option<SupportBundleLogPreview>, String> {
    let entry = match archive.by_name(name) {
        Ok(entry) => entry,
        Err(zip::result::ZipError::FileNotFound) => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    let mut records = Vec::new();
    let mut truncated = false;
    let reader = BufReader::new(entry);
    for (line_index, line) in reader.lines().enumerate() {
        let line = line.map_err(|e| e.to_string())?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if records.len() >= LOG_PREVIEW_LIMIT {
            truncated = true;
            break;
        }
        let value = match serde_json::from_str::<serde_json::Value>(trimmed) {
            Ok(value) => value,
            Err(_) => {
                records.push(SupportBundleLogRecordPreview {
                    line_number: line_index + 1,
                    sequence: None,
                    timestamp_utc: None,
                    timestamp_us: None,
                    status: Some("invalid_json".to_string()),
                    reason: Some("Could not parse JSONL record".to_string()),
                    tile_id: None,
                    map_id: None,
                    confidence: None,
                    inliers: None,
                    reprojection_error_px: None,
                    external_position_status: None,
                    external_position_message_type: None,
                    external_position_warnings: None,
                });
                continue;
            }
        };
        records.push(log_record_preview_from_json(line_index + 1, &value));
    }
    Ok(Some(SupportBundleLogPreview {
        name: display_entry_name(name),
        records,
        truncated,
    }))
}

fn log_record_preview_from_json(
    line_number: usize,
    value: &serde_json::Value,
) -> SupportBundleLogRecordPreview {
    let result = value.get("result").unwrap_or(value);
    let external_position = value.get("external_position_health");
    let external_position_warnings = external_position
        .map(|value| json_string_array(value.get("last_warnings")))
        .filter(|warnings| !warnings.is_empty());
    SupportBundleLogRecordPreview {
        line_number,
        sequence: value
            .get("sequence")
            .or_else(|| result.get("sequence"))
            .and_then(|value| value.as_u64()),
        timestamp_utc: json_string(
            value
                .get("timestamp_utc")
                .or_else(|| result.get("timestamp_utc")),
        ),
        timestamp_us: value
            .get("timestamp_us")
            .or_else(|| result.get("timestamp_us"))
            .and_then(|value| value.as_u64()),
        status: json_string(result.get("status")),
        reason: json_string(result.get("reason")),
        tile_id: json_string(result.get("tile_id")),
        map_id: json_string(result.get("map_id")),
        confidence: result.get("confidence").and_then(|value| value.as_f64()),
        inliers: result.get("inliers").and_then(|value| value.as_u64()),
        reprojection_error_px: result
            .get("reprojection_error_px")
            .and_then(|value| value.as_f64()),
        external_position_status: external_position
            .and_then(|value| json_string(value.get("status"))),
        external_position_message_type: external_position
            .and_then(|value| json_string(value.get("message_type"))),
        external_position_warnings,
    }
}

#[derive(Default)]
struct TimelineAccumulator {
    total_records: u64,
    invalid_records: u64,
    accepted_records: u64,
    status_counts: BTreeMap<String, u64>,
    reason_counts: BTreeMap<String, u64>,
    external_position_status_counts: BTreeMap<String, u64>,
    external_position_warning_counts: BTreeMap<String, u64>,
    first_sequence: Option<u64>,
    last_sequence: Option<u64>,
    first_timestamp_us: Option<u64>,
    last_timestamp_us: Option<u64>,
    confidence_sum: f64,
    confidence_count: u64,
    inliers_sum: f64,
    inliers_count: u64,
    reprojection_sum: f64,
    reprojection_count: u64,
}

#[derive(Default)]
struct TimelineSegmentAccumulator {
    start_line: usize,
    end_line: usize,
    total_records: u64,
    accepted_records: u64,
    status_counts: BTreeMap<String, u64>,
    confidence_sum: f64,
    confidence_count: u64,
    inliers_sum: f64,
    inliers_count: u64,
    reprojection_sum: f64,
    reprojection_count: u64,
}

struct TimelineRecordMetrics {
    line_number: usize,
    status: String,
    reason: Option<String>,
    external_position_status: Option<String>,
    external_position_warnings: Vec<String>,
    sequence: Option<u64>,
    timestamp_us: Option<u64>,
    confidence: Option<f64>,
    inliers: Option<f64>,
    reprojection_error_px: Option<f64>,
    invalid_json: bool,
}

fn read_log_timeline_entry(
    archive: &mut ZipArchive<File>,
    name: &str,
    size_bytes: u64,
) -> Result<Option<SupportBundleLogTimeline>, String> {
    if size_bytes > LOG_TIMELINE_MAX_BYTES {
        return Ok(Some(SupportBundleLogTimeline {
            name: display_entry_name(name),
            path: name.to_string(),
            size_bytes,
            total_records: None,
            invalid_records: 0,
            accepted_rate: None,
            status_counts: None,
            reason_counts: None,
            external_position_status_counts: None,
            external_position_warning_counts: None,
            first_sequence: None,
            last_sequence: None,
            first_timestamp_us: None,
            last_timestamp_us: None,
            average_confidence: None,
            average_inliers: None,
            average_reprojection_error_px: None,
            segments: vec![],
            truncated: true,
        }));
    }

    let mut accumulator = TimelineAccumulator::default();
    {
        let entry = match archive.by_name(name) {
            Ok(entry) => entry,
            Err(zip::result::ZipError::FileNotFound) => return Ok(None),
            Err(error) => return Err(error.to_string()),
        };
        let reader = BufReader::new(entry);
        for (line_index, line) in reader.lines().enumerate() {
            let line = line.map_err(|e| e.to_string())?;
            let Some(metrics) = timeline_record_metrics_from_line(line_index + 1, &line) else {
                continue;
            };
            update_timeline_accumulator(&mut accumulator, &metrics);
        }
    }

    let segment_count = usize::min(LOG_TIMELINE_SEGMENTS, accumulator.total_records as usize);
    let mut segment_accumulators = Vec::new();
    if segment_count > 0 {
        segment_accumulators.resize_with(segment_count, TimelineSegmentAccumulator::default);
        let entry = match archive.by_name(name) {
            Ok(entry) => entry,
            Err(zip::result::ZipError::FileNotFound) => return Ok(None),
            Err(error) => return Err(error.to_string()),
        };
        let reader = BufReader::new(entry);
        let mut record_index = 0usize;
        for (line_index, line) in reader.lines().enumerate() {
            let line = line.map_err(|e| e.to_string())?;
            let Some(metrics) = timeline_record_metrics_from_line(line_index + 1, &line) else {
                continue;
            };
            let bucket = usize::min(
                (record_index * segment_count) / accumulator.total_records as usize,
                segment_count - 1,
            );
            update_timeline_segment(&mut segment_accumulators[bucket], &metrics);
            record_index += 1;
        }
    }

    Ok(Some(SupportBundleLogTimeline {
        name: display_entry_name(name),
        path: name.to_string(),
        size_bytes,
        total_records: Some(accumulator.total_records),
        invalid_records: accumulator.invalid_records,
        accepted_rate: ratio(accumulator.accepted_records, accumulator.total_records),
        status_counts: counts_value(&accumulator.status_counts),
        reason_counts: counts_value(&accumulator.reason_counts),
        external_position_status_counts: counts_value(&accumulator.external_position_status_counts),
        external_position_warning_counts: counts_value(
            &accumulator.external_position_warning_counts,
        ),
        first_sequence: accumulator.first_sequence,
        last_sequence: accumulator.last_sequence,
        first_timestamp_us: accumulator.first_timestamp_us,
        last_timestamp_us: accumulator.last_timestamp_us,
        average_confidence: average(accumulator.confidence_sum, accumulator.confidence_count),
        average_inliers: average(accumulator.inliers_sum, accumulator.inliers_count),
        average_reprojection_error_px: average(
            accumulator.reprojection_sum,
            accumulator.reprojection_count,
        ),
        segments: segment_accumulators
            .into_iter()
            .enumerate()
            .filter_map(|(index, segment)| timeline_segment_from_accumulator(index, segment))
            .collect(),
        truncated: false,
    }))
}

fn timeline_record_metrics_from_line(
    line_number: usize,
    line: &str,
) -> Option<TimelineRecordMetrics> {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return None;
    }
    let value = match serde_json::from_str::<serde_json::Value>(trimmed) {
        Ok(value) => value,
        Err(_) => {
            return Some(TimelineRecordMetrics {
                line_number,
                status: "invalid_json".to_string(),
                reason: Some("Could not parse JSONL record".to_string()),
                external_position_status: None,
                external_position_warnings: vec![],
                sequence: None,
                timestamp_us: None,
                confidence: None,
                inliers: None,
                reprojection_error_px: None,
                invalid_json: true,
            });
        }
    };
    let result = value.get("result").unwrap_or(&value);
    let external_position = value.get("external_position_health");
    Some(TimelineRecordMetrics {
        line_number,
        status: json_string(result.get("status")).unwrap_or_else(|| "unknown".to_string()),
        reason: json_string(result.get("reason")),
        external_position_status: external_position
            .and_then(|value| json_string(value.get("status"))),
        external_position_warnings: external_position
            .map(|value| json_string_array(value.get("last_warnings")))
            .unwrap_or_default(),
        sequence: value
            .get("sequence")
            .or_else(|| result.get("sequence"))
            .and_then(|value| value.as_u64()),
        timestamp_us: value
            .get("timestamp_us")
            .or_else(|| result.get("timestamp_us"))
            .and_then(|value| value.as_u64()),
        confidence: result.get("confidence").and_then(|value| value.as_f64()),
        inliers: result.get("inliers").and_then(|value| value.as_f64()),
        reprojection_error_px: result
            .get("reprojection_error_px")
            .and_then(|value| value.as_f64()),
        invalid_json: false,
    })
}

fn update_timeline_accumulator(
    accumulator: &mut TimelineAccumulator,
    metrics: &TimelineRecordMetrics,
) {
    accumulator.total_records += 1;
    if metrics.invalid_json {
        accumulator.invalid_records += 1;
    }
    if metrics.status == "accepted" {
        accumulator.accepted_records += 1;
    }
    increment_count(&mut accumulator.status_counts, &metrics.status);
    if let Some(reason) = &metrics.reason {
        increment_count(&mut accumulator.reason_counts, reason);
    }
    if let Some(status) = &metrics.external_position_status {
        increment_count(&mut accumulator.external_position_status_counts, status);
    }
    for warning in &metrics.external_position_warnings {
        increment_count(&mut accumulator.external_position_warning_counts, warning);
    }
    if accumulator.first_sequence.is_none() {
        accumulator.first_sequence = metrics.sequence;
    }
    if metrics.sequence.is_some() {
        accumulator.last_sequence = metrics.sequence;
    }
    if accumulator.first_timestamp_us.is_none() {
        accumulator.first_timestamp_us = metrics.timestamp_us;
    }
    if metrics.timestamp_us.is_some() {
        accumulator.last_timestamp_us = metrics.timestamp_us;
    }
    if let Some(confidence) = metrics.confidence {
        accumulator.confidence_sum += confidence;
        accumulator.confidence_count += 1;
    }
    if let Some(inliers) = metrics.inliers {
        accumulator.inliers_sum += inliers;
        accumulator.inliers_count += 1;
    }
    if let Some(error) = metrics.reprojection_error_px {
        accumulator.reprojection_sum += error;
        accumulator.reprojection_count += 1;
    }
}

fn update_timeline_segment(
    segment: &mut TimelineSegmentAccumulator,
    metrics: &TimelineRecordMetrics,
) {
    if segment.total_records == 0 {
        segment.start_line = metrics.line_number;
    }
    segment.end_line = metrics.line_number;
    segment.total_records += 1;
    if metrics.status == "accepted" {
        segment.accepted_records += 1;
    }
    increment_count(&mut segment.status_counts, &metrics.status);
    if let Some(confidence) = metrics.confidence {
        segment.confidence_sum += confidence;
        segment.confidence_count += 1;
    }
    if let Some(inliers) = metrics.inliers {
        segment.inliers_sum += inliers;
        segment.inliers_count += 1;
    }
    if let Some(error) = metrics.reprojection_error_px {
        segment.reprojection_sum += error;
        segment.reprojection_count += 1;
    }
}

fn timeline_segment_from_accumulator(
    index: usize,
    segment: TimelineSegmentAccumulator,
) -> Option<SupportBundleLogTimelineSegment> {
    if segment.total_records == 0 {
        return None;
    }
    Some(SupportBundleLogTimelineSegment {
        index,
        start_line: segment.start_line,
        end_line: segment.end_line,
        total_records: segment.total_records,
        accepted_rate: ratio(segment.accepted_records, segment.total_records),
        dominant_status: dominant_status(&segment.status_counts),
        average_confidence: average(segment.confidence_sum, segment.confidence_count),
        average_inliers: average(segment.inliers_sum, segment.inliers_count),
        average_reprojection_error_px: average(
            segment.reprojection_sum,
            segment.reprojection_count,
        ),
    })
}

fn increment_count(counts: &mut BTreeMap<String, u64>, key: &str) {
    *counts.entry(key.to_string()).or_insert(0) += 1;
}

fn counts_value(counts: &BTreeMap<String, u64>) -> Option<serde_json::Value> {
    if counts.is_empty() {
        return None;
    }
    serde_json::to_value(counts).ok()
}

fn average(sum: f64, count: u64) -> Option<f64> {
    if count == 0 {
        None
    } else {
        Some(sum / count as f64)
    }
}

fn ratio(numerator: u64, denominator: u64) -> Option<f64> {
    if denominator == 0 {
        None
    } else {
        Some(numerator as f64 / denominator as f64)
    }
}

fn dominant_status(counts: &BTreeMap<String, u64>) -> Option<String> {
    counts
        .iter()
        .max_by(|(left_key, left_value), (right_key, right_value)| {
            left_value
                .cmp(right_value)
                .then_with(|| right_key.cmp(left_key))
        })
        .map(|(key, _)| key.clone())
}

fn replay_report_from_json(value: &serde_json::Value) -> SupportBundleReplayReport {
    let issues = value
        .get("issues")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    item.get("message")
                        .and_then(|message| message.as_str())
                        .map(|message| message.to_string())
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let metrics = value.get("metrics");
    SupportBundleReplayReport {
        case_name: json_string(value.get("case_name")),
        expected: json_string(value.get("expected")),
        status: json_string(value.get("status")),
        accepted_rate: metrics
            .and_then(|value| value.get("accepted_rate"))
            .and_then(|value| value.as_f64()),
        total_records: metrics
            .and_then(|value| value.get("total_records"))
            .and_then(|value| value.as_u64()),
        issues,
    }
}

fn px4_evidence_report_from_json(value: &serde_json::Value) -> SupportBundlePx4EvidenceReport {
    let issues = value
        .get("issues")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    item.get("message")
                        .and_then(|message| message.as_str())
                        .map(|message| message.to_string())
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SupportBundlePx4EvidenceReport {
        status: json_string(value.get("status")),
        expected_message: json_string(value.get("expected_message")),
        sample_count: value
            .pointer("/listener/sample_count")
            .and_then(|value| value.as_u64()),
        observed_rate_hz: value
            .pointer("/listener/observed_rate_hz")
            .and_then(|value| value.as_f64()),
        latest_sample_age_s: value
            .pointer("/listener/latest_sample_age_s")
            .and_then(|value| value.as_f64()),
        last_position: value.pointer("/listener/last_position").cloned(),
        mavlink_version: value
            .pointer("/mavlink_status/mavlink_version")
            .and_then(|value| value.as_u64()),
        has_udp_14550: value
            .pointer("/mavlink_status/has_udp_14550")
            .and_then(|value| value.as_bool()),
        issues,
    }
}

fn px4_prereq_report_from_json(value: &serde_json::Value) -> Option<Px4PrereqReport> {
    if value.get("schema_version").and_then(|value| value.as_str())
        != Some("vision_nav_px4_sitl_capture_prereqs_v1")
    {
        return None;
    }
    let checks = value
        .get("checks")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .map(|item| Px4PrereqCheck {
                    name: json_string(item.get("name")),
                    status: json_string(item.get("status")),
                    message: json_string(item.get("message")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let fix_commands = value
        .get("fix_commands")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .map(|item| Px4PrereqFixCommand {
                    label: json_string(item.get("label")),
                    command: json_string(item.get("command")),
                    condition: json_string(item.get("condition")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    Some(Px4PrereqReport {
        status: json_string(value.get("status")),
        generated_at: json_string(value.get("generated_at")),
        session_dir: json_string(value.get("session_dir")),
        capture_dir: json_string(value.get("capture_dir")),
        receiver_report: json_string(value.get("receiver_report")),
        px4_dir: json_string(value.get("px4_dir")),
        px4_target: json_string(value.get("px4_target")),
        tmux_session: json_string(value.get("tmux_session")),
        checks,
        next_actions: json_string_array(value.get("next_actions")),
        fix_commands,
    })
}

fn px4_param_report_from_json(value: &serde_json::Value) -> SupportBundlePx4ParamReport {
    let issues = value
        .get("issues")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    item.get("message")
                        .and_then(|message| message.as_str())
                        .map(|message| message.to_string())
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SupportBundlePx4ParamReport {
        status: json_string(value.get("status")),
        ev_ctrl: value
            .pointer("/parameters/EKF2_EV_CTRL")
            .and_then(|value| value.as_i64()),
        hgt_ref: value
            .pointer("/parameters/EKF2_HGT_REF")
            .and_then(|value| value.as_i64()),
        gps_ctrl: value
            .pointer("/parameters/EKF2_GPS_CTRL")
            .and_then(|value| value.as_i64()),
        ev_noise_mode: value
            .pointer("/parameters/EKF2_EV_NOISE_MD")
            .and_then(|value| value.as_i64()),
        ev_delay_ms: value
            .pointer("/parameters/EKF2_EV_DELAY")
            .and_then(|value| value.as_f64()),
        issues,
    }
}

fn ardupilot_param_report_from_json(
    value: &serde_json::Value,
) -> SupportBundleArduPilotParamReport {
    let issues = value
        .get("issues")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    item.get("message")
                        .and_then(|message| message.as_str())
                        .map(|message| message.to_string())
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let source_set = value
        .pointer("/parameters/source_set")
        .and_then(|value| value.as_i64());
    let source_prefix = format!("EK3_SRC{}", source_set.unwrap_or(1));
    SupportBundleArduPilotParamReport {
        status: json_string(value.get("status")),
        source_set,
        viso_type: value
            .pointer("/parameters/VISO_TYPE")
            .and_then(|value| value.as_i64()),
        posxy_source: value
            .pointer(&format!("/parameters/{}_POSXY", source_prefix))
            .and_then(|value| value.as_i64()),
        velxy_source: value
            .pointer(&format!("/parameters/{}_VELXY", source_prefix))
            .and_then(|value| value.as_i64()),
        posz_source: value
            .pointer(&format!("/parameters/{}_POSZ", source_prefix))
            .and_then(|value| value.as_i64()),
        velz_source: value
            .pointer(&format!("/parameters/{}_VELZ", source_prefix))
            .and_then(|value| value.as_i64()),
        yaw_source: value
            .pointer(&format!("/parameters/{}_YAW", source_prefix))
            .and_then(|value| value.as_i64()),
        source_switch_channels: value.pointer("/parameters/source_switch_channels").cloned(),
        issues,
    }
}

fn feature_method_report_from_json(
    value: &serde_json::Value,
) -> SupportBundleFeatureMethodBenchmarkReport {
    let methods = value
        .get("methods")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .map(|item| {
                    let metrics = item.pointer("/gate/metrics");
                    SupportBundleFeatureMethodReport {
                        method: json_string(item.get("method")),
                        status: json_string(item.get("status")),
                        accepted_rate: metrics
                            .and_then(|metrics| metrics.get("accepted_rate"))
                            .and_then(|value| value.as_f64()),
                        total_records: metrics
                            .and_then(|metrics| metrics.get("total_records"))
                            .and_then(|value| value.as_u64()),
                    }
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SupportBundleFeatureMethodBenchmarkReport {
        status: json_string(value.get("status")),
        case_name: json_string(value.get("case_name")),
        expected: json_string(value.get("expected")),
        recommended_method: json_string(value.get("recommended_method")),
        methods,
    }
}

fn field_evidence_report_from_json(value: &serde_json::Value) -> SupportBundleFieldEvidenceReport {
    let summary = value.get("summary");
    let requirements = value
        .pointer("/coverage/requirements")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .map(|item| SupportBundleFieldEvidenceCondition {
                    key: json_string(item.get("key")),
                    status: json_string(item.get("status")),
                    case_count: item.get("case_count").and_then(|value| value.as_u64()),
                    field_case_count: item
                        .get("field_case_count")
                        .and_then(|value| value.as_u64()),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SupportBundleFieldEvidenceReport {
        status: json_string(value.get("status")),
        manifest_path: json_string(value.get("manifest_path")),
        coverage_status: json_string(summary.and_then(|value| value.get("coverage_status"))),
        replay_status: json_string(summary.and_then(|value| value.get("replay_status"))),
        case_count: summary
            .and_then(|value| value.get("case_count"))
            .and_then(|value| value.as_u64()),
        field_case_count: summary
            .and_then(|value| value.get("field_case_count"))
            .and_then(|value| value.as_u64()),
        capture_metadata_issue_count: summary
            .and_then(|value| value.get("capture_metadata_issue_count"))
            .and_then(|value| value.as_u64()),
        covered_conditions: summary
            .and_then(|value| value.get("covered_conditions"))
            .cloned(),
        required_conditions: summary
            .and_then(|value| value.get("required_conditions"))
            .cloned(),
        requirements,
    }
}

fn threshold_tuning_report_from_json(
    value: &serde_json::Value,
) -> SupportBundleThresholdTuningReport {
    let summary = value.get("summary");
    SupportBundleThresholdTuningReport {
        status: json_string(value.get("status")),
        method: json_string(value.get("method")),
        manifest_path: json_string(value.get("manifest_path")),
        coverage_status: json_string(summary.and_then(|value| value.get("coverage_status"))),
        replay_status: json_string(summary.and_then(|value| value.get("replay_status"))),
        case_count: summary
            .and_then(|value| value.get("case_count"))
            .and_then(|value| value.as_u64()),
        field_case_count: summary
            .and_then(|value| value.get("field_case_count"))
            .and_then(|value| value.as_u64()),
        capture_metadata_issue_count: summary
            .and_then(|value| value.get("capture_metadata_issue_count"))
            .and_then(|value| value.as_u64()),
        covered_conditions: summary
            .and_then(|value| value.get("covered_conditions"))
            .cloned()
            .or_else(|| value.get("conditions").cloned()),
        margins: value.pointer("/metrics/margins").cloned(),
    }
}

fn rosbag_export_validation_report_from_json(
    value: &serde_json::Value,
) -> SupportBundleRosbagExportValidationReport {
    let topics = value
        .get("topics")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let name = json_string(item.get("name"))?;
                    let message_type =
                        json_string(item.get("type")).unwrap_or_else(|| "unknown".to_string());
                    let count = item
                        .get("message_count")
                        .and_then(|value| value.as_u64())
                        .unwrap_or(0);
                    Some(format!("{name} ({message_type}, {count})"))
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let issues = value
        .get("issues")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    json_string(item.get("message")).or_else(|| json_string(Some(item)))
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SupportBundleRosbagExportValidationReport {
        status: json_string(value.get("status")),
        format: json_string(value.get("format")),
        artifact_path: json_string(value.get("artifact_path")),
        metadata_path: json_string(value.get("metadata_path")),
        message_count: value.get("message_count").and_then(|value| value.as_u64()),
        topic_count: value.get("topic_count").and_then(|value| value.as_u64()),
        topics,
        issues,
    }
}

fn rosbag2_cli_review_report_from_json(
    value: &serde_json::Value,
) -> SupportBundleRosbag2CliReviewReport {
    let issues = value
        .get("issues")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    json_string(item.get("message")).or_else(|| json_string(Some(item)))
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SupportBundleRosbag2CliReviewReport {
        path: json_string(value.get("path")),
        status: json_string(value.get("status")),
        artifact_path: json_string(value.get("artifact_path")),
        bag_dir: json_string(value.get("bag_dir")),
        validation_status: json_string(value.get("validation_status")),
        validation_format: json_string(value.get("validation_format")),
        ros2_cli_status: json_string(value.pointer("/ros2_cli/status"))
            .or_else(|| json_string(value.get("ros2_cli_status"))),
        ros2_cli_exit_code: value
            .pointer("/ros2_cli/exit_code")
            .or_else(|| value.get("ros2_cli_exit_code"))
            .and_then(|value| value.as_i64()),
        issues,
    }
}

fn bench_readiness_report_from_json(
    value: &serde_json::Value,
) -> SupportBundleBenchReadinessReport {
    let checks = value
        .get("checks")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .map(|item| SupportBundleBenchReadinessCheck {
                    name: json_string(item.get("name")),
                    status: json_string(item.get("status")),
                    message: json_string(item.get("message")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SupportBundleBenchReadinessReport {
        status: json_string(value.get("status")),
        failed_count: value
            .pointer("/summary/failed")
            .and_then(|value| value.as_u64()),
        degraded_count: value
            .pointer("/summary/degraded")
            .and_then(|value| value.as_u64()),
        passed_count: value
            .pointer("/summary/passed")
            .and_then(|value| value.as_u64()),
        checks,
    }
}

fn autonomy_readiness_report_from_json(
    value: &serde_json::Value,
) -> Option<(
    AutonomyReadinessSummary,
    Vec<AutonomyReadinessCheck>,
    Vec<AutonomyReadinessNextAction>,
    Option<AutonomyReadinessEvidenceManifest>,
)> {
    let checks_value = value.get("checks")?.as_array()?;
    if !value
        .get("summary")
        .is_some_and(|summary| summary.is_object())
    {
        return None;
    }
    let checks = checks_value
        .iter()
        .filter(|item| item.is_object())
        .map(|item| AutonomyReadinessCheck {
            name: json_string(item.get("name")),
            status: json_string(item.get("status")),
            message: json_string(item.get("message")),
        })
        .collect::<Vec<_>>();
    if checks.is_empty() {
        return None;
    }
    let next_actions = value
        .get("next_actions")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| AutonomyReadinessNextAction {
                    check: json_string(item.get("check")),
                    status: json_string(item.get("status")),
                    title: json_string(item.get("title")),
                    desktop_action: json_string(item.get("desktop_action")),
                    command: json_string(item.get("command")),
                    notes: json_string(item.get("notes")),
                    bench_subcheck: json_string(item.get("bench_subcheck")),
                    bench_message: json_string(item.get("bench_message")),
                    bench_subchecks: autonomy_bench_subchecks_from_value(
                        item.get("bench_subchecks"),
                    ),
                    missing_conditions: json_string_array(item.get("missing_conditions")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let check_status = |name: &str| {
        checks
            .iter()
            .find(|check| check.name.as_deref() == Some(name))
            .and_then(|check| check.status.clone())
    };
    Some((
        AutonomyReadinessSummary {
            status: json_string(value.get("status")),
            failed_count: value
                .pointer("/summary/failed")
                .and_then(|value| value.as_u64()),
            degraded_count: value
                .pointer("/summary/degraded")
                .and_then(|value| value.as_u64()),
            passed_count: value
                .pointer("/summary/passed")
                .and_then(|value| value.as_u64()),
            support_bundle_bench_readiness_status: check_status("support_bundle_bench_readiness"),
            px4_receiver_proof_status: check_status("px4_receiver_proof"),
            field_collection_plan_status: check_status("field_collection_plan"),
            field_evidence_proof_status: check_status("field_evidence_proof"),
            feature_method_benchmark_status: check_status("feature_method_benchmark"),
            threshold_tuning_status: check_status("threshold_tuning"),
            rosbag_export_validation_status: check_status("rosbag_export_validation"),
            rosbag2_cli_review_status: check_status("rosbag2_cli_review"),
        },
        checks,
        next_actions,
        autonomy_evidence_manifest_from_json(value.get("evidence_manifest")),
    ))
}

fn autonomy_readiness_field_collection_plan_from_json(
    value: &serde_json::Value,
    report_path: &Path,
) -> Option<AutonomyReadinessFieldCollectionPlan> {
    let input_path = value
        .pointer("/inputs/field_collection_plan")
        .and_then(|value| value.as_str())
        .filter(|value| !value.is_empty())
        .and_then(|path| expand_local_path(path).ok());
    let sibling_path = report_path
        .parent()
        .map(|parent| parent.join("field_collection_plan.json"));
    let path = [input_path, sibling_path]
        .into_iter()
        .flatten()
        .find(|path| path.is_file())?;
    let text = std::fs::read_to_string(&path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let plan = field_collection_plan_from_json(&value)?;
    let next_condition = plan.next_condition.clone().or_else(|| {
        plan.conditions
            .iter()
            .find(|condition| condition.status.as_deref() != Some("registered"))
            .cloned()
    });
    let pending_conditions = plan
        .conditions
        .into_iter()
        .filter(|condition| condition.status.as_deref() != Some("registered"))
        .collect::<Vec<_>>();
    Some(AutonomyReadinessFieldCollectionPlan {
        path: path.to_string_lossy().into_owned(),
        status: plan.status,
        site_name: plan.site_name,
        manifest_path: plan.manifest_path,
        bundle: plan.bundle,
        summary: plan.summary,
        next_condition,
        pending_conditions,
    })
}

fn autonomy_readiness_command_bundle_from_json(
    value: &serde_json::Value,
) -> Option<AutonomyReadinessCommandBundle> {
    let bundle = value.get("command_bundle")?;
    autonomy_readiness_command_bundle_from_bundle_json(Some(bundle))
}

fn autonomy_readiness_command_bundle_from_bundle_json(
    bundle: Option<&serde_json::Value>,
) -> Option<AutonomyReadinessCommandBundle> {
    let bundle = bundle?;
    if !bundle.is_object() {
        return None;
    }
    Some(AutonomyReadinessCommandBundle {
        guided_workflow_commands: json_string_array(bundle.get("guided_workflow_commands")),
        prerequisite_fix_commands: json_string_array(bundle.get("prerequisite_fix_commands")),
        next_action_commands: json_string_array(bundle.get("next_action_commands")),
        immediate_next_action_commands: json_string_array(
            bundle.get("immediate_next_action_commands"),
        ),
        blocked_follow_up_commands: json_string_array(bundle.get("blocked_follow_up_commands")),
        field_collection_capture_commands: json_string_array(
            bundle.get("field_collection_capture_commands"),
        ),
        field_collection_metadata_update_commands: json_string_array(
            bundle.get("field_collection_metadata_update_commands"),
        ),
        field_collection_registration_commands: json_string_array(
            bundle.get("field_collection_registration_commands"),
        ),
        command_count: bundle.get("command_count").and_then(|value| value.as_u64()),
        command_items: autonomy_readiness_command_items(bundle.get("command_items")),
    })
}

fn autonomy_readiness_command_items(
    value: Option<&serde_json::Value>,
) -> Vec<AutonomyReadinessCommandItem> {
    value
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let command = json_string(item.get("command"));
                    if command.is_none() {
                        return None;
                    }
                    Some(AutonomyReadinessCommandItem {
                        group: json_string(item.get("group")),
                        command,
                        desktop_action: json_string(item.get("desktop_action")),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

fn autonomy_evidence_manifest_from_json(
    value: Option<&serde_json::Value>,
) -> Option<AutonomyReadinessEvidenceManifest> {
    let value = value?;
    if !value.is_object() {
        return None;
    }
    Some(AutonomyReadinessEvidenceManifest {
        schema_version: json_string(value.get("schema_version")),
        ready_for_goal_completion: value
            .get("ready_for_goal_completion")
            .and_then(|value| value.as_bool()),
        proof_items: autonomy_evidence_blockers_from_value(value.get("proof_items")),
        completion_blockers: autonomy_evidence_blockers_from_value(
            value.get("completion_blockers"),
        ),
        external_blockers: autonomy_evidence_blockers_from_value(value.get("external_blockers")),
    })
}

fn autonomy_readiness_audit_metadata_from_json(
    value: Option<&serde_json::Value>,
) -> Option<AutonomyReadinessAuditMetadata> {
    let value = value?;
    if !value.is_object() {
        return None;
    }
    Some(AutonomyReadinessAuditMetadata {
        schema_version: json_string(value.get("schema_version")),
        generated_at_utc: json_string(value.get("generated_at_utc")),
        repo: autonomy_readiness_audit_repo_metadata_from_json(value.get("repo")),
    })
}

fn autonomy_readiness_audit_repo_metadata_from_json(
    value: Option<&serde_json::Value>,
) -> Option<AutonomyReadinessAuditRepoMetadata> {
    let value = value?;
    if !value.is_object() {
        return None;
    }
    Some(AutonomyReadinessAuditRepoMetadata {
        detected: value.get("detected").and_then(|value| value.as_bool()),
        root: json_string(value.get("root")),
        path: json_string(value.get("path")),
        branch: json_string(value.get("branch")),
        commit: json_string(value.get("commit")),
        dirty: value.get("dirty").and_then(|value| value.as_bool()),
        remote: json_string(value.get("remote")),
    })
}

fn autonomy_readiness_proof_runbook_from_json(
    value: Option<&serde_json::Value>,
) -> Option<AutonomyReadinessProofRunbook> {
    let value = value?;
    if !value.is_object() {
        return None;
    }
    Some(AutonomyReadinessProofRunbook {
        schema_version: json_string(value.get("schema_version")),
        ready_for_goal_completion: value
            .get("ready_for_goal_completion")
            .and_then(|value| value.as_bool()),
        phases_truncated: value
            .get("phases_truncated")
            .and_then(|value| value.as_bool()),
        summary: autonomy_readiness_proof_runbook_summary_from_json(value.get("summary")),
        phases: autonomy_readiness_proof_runbook_phases_from_json(value.get("phases")),
    })
}

fn autonomy_readiness_proof_runbook_summary_from_json(
    value: Option<&serde_json::Value>,
) -> AutonomyReadinessProofRunbookSummary {
    AutonomyReadinessProofRunbookSummary {
        phase_count: value
            .and_then(|value| value.get("phase_count"))
            .and_then(|value| value.as_u64()),
        passed: value
            .and_then(|value| value.get("passed"))
            .and_then(|value| value.as_u64()),
        action_required: value
            .and_then(|value| value.get("action_required"))
            .and_then(|value| value.as_u64()),
        blocked: value
            .and_then(|value| value.get("blocked"))
            .and_then(|value| value.as_u64()),
    }
}

fn autonomy_readiness_proof_runbook_phases_from_json(
    value: Option<&serde_json::Value>,
) -> Vec<AutonomyReadinessProofRunbookPhase> {
    value
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| AutonomyReadinessProofRunbookPhase {
                    id: json_string(item.get("id")),
                    title: json_string(item.get("title")),
                    status: json_string(item.get("status")),
                    depends_on: json_string_array(item.get("depends_on")),
                    dependency_status: autonomy_readiness_dependency_status_from_json(
                        item.get("dependency_status"),
                    ),
                    checks: autonomy_readiness_proof_runbook_checks_from_json(item.get("checks")),
                    actions: autonomy_readiness_proof_runbook_actions_from_json(
                        item.get("actions"),
                    ),
                    actions_truncated: item
                        .get("actions_truncated")
                        .and_then(|value| value.as_bool()),
                    commands: json_string_array(item.get("commands")),
                    notes: json_string(item.get("notes")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn autonomy_readiness_dependency_status_from_json(
    value: Option<&serde_json::Value>,
) -> BTreeMap<String, String> {
    value
        .and_then(|value| value.as_object())
        .map(|items| {
            items
                .iter()
                .filter_map(|(key, value)| {
                    value.as_str().map(|text| (key.clone(), text.to_string()))
                })
                .collect::<BTreeMap<_, _>>()
        })
        .unwrap_or_default()
}

fn autonomy_readiness_proof_runbook_checks_from_json(
    value: Option<&serde_json::Value>,
) -> Vec<AutonomyReadinessProofRunbookCheck> {
    value
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| AutonomyReadinessProofRunbookCheck {
                    name: json_string(item.get("name")),
                    status: json_string(item.get("status")),
                    message: json_string(item.get("message")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn autonomy_readiness_proof_runbook_actions_from_json(
    value: Option<&serde_json::Value>,
) -> Vec<AutonomyReadinessProofRunbookAction> {
    value
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| AutonomyReadinessProofRunbookAction {
                    check: json_string(item.get("check")),
                    status: json_string(item.get("status")),
                    title: json_string(item.get("title")),
                    desktop_action: json_string(item.get("desktop_action")),
                    command: json_string(item.get("command")),
                    notes: json_string(item.get("notes")),
                    missing_conditions: json_string_array(item.get("missing_conditions")),
                    bench_subcheck: json_string(item.get("bench_subcheck")),
                    bench_message: json_string(item.get("bench_message")),
                    bench_subchecks: autonomy_bench_subchecks_from_value(
                        item.get("bench_subchecks"),
                    ),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn autonomy_readiness_plan_snapshot_from_json(
    value: Option<&serde_json::Value>,
) -> Option<AutonomyReadinessPlanSnapshot> {
    let value = value?;
    if !value.is_object() {
        return None;
    }
    Some(AutonomyReadinessPlanSnapshot {
        schema_version: json_string(value.get("schema_version")),
        research_doc: autonomy_readiness_plan_source_from_json(value.get("research_doc")),
        implementation_plan: autonomy_readiness_plan_source_from_json(
            value.get("implementation_plan"),
        ),
    })
}

fn autonomy_readiness_plan_source_from_json(
    value: Option<&serde_json::Value>,
) -> Option<AutonomyReadinessPlanSourceSnapshot> {
    let value = value?;
    if !value.is_object() {
        return None;
    }
    Some(AutonomyReadinessPlanSourceSnapshot {
        path: json_string(value.get("path")),
        exists: value.get("exists").and_then(|value| value.as_bool()),
        source_sha256: json_string(value.get("source_sha256")),
        source_size_bytes: value
            .get("source_size_bytes")
            .and_then(|value| value.as_u64()),
        required_marker_count: value
            .get("required_marker_count")
            .and_then(|value| value.as_u64()),
        missing_markers: json_string_array(value.get("missing_markers")),
        highest_value_reference_count: value
            .get("highest_value_reference_count")
            .and_then(|value| value.as_u64()),
        fit_criteria_count: value
            .get("fit_criteria_count")
            .and_then(|value| value.as_u64()),
        architecture_section_count: value
            .get("architecture_section_count")
            .and_then(|value| value.as_u64()),
        near_term_item_count: value
            .get("near_term_item_count")
            .and_then(|value| value.as_u64()),
        avoid_choice_count: value
            .get("avoid_choice_count")
            .and_then(|value| value.as_u64()),
        track_count: value.get("track_count").and_then(|value| value.as_u64()),
        done_count: value.get("done_count").and_then(|value| value.as_u64()),
        in_progress_count: value
            .get("in_progress_count")
            .and_then(|value| value.as_u64()),
        task_count: value.get("task_count").and_then(|value| value.as_u64()),
        next_task_count: value
            .get("next_task_count")
            .and_then(|value| value.as_u64()),
        acceptance_check_count: value
            .get("acceptance_check_count")
            .and_then(|value| value.as_u64()),
        execution_order_count: value
            .get("execution_order_count")
            .and_then(|value| value.as_u64()),
    })
}

fn autonomy_evidence_workflow_report_from_json(
    value: &serde_json::Value,
) -> Option<AutonomyEvidenceWorkflowReportFile> {
    if value.get("schema_version").and_then(|value| value.as_str())
        != Some("vision_nav_autonomy_evidence_workflow_v1")
    {
        return None;
    }
    let steps = value
        .get("steps")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| AutonomyEvidenceWorkflowStep {
                    name: json_string(item.get("name")),
                    status: json_string(item.get("status")),
                    exit_code: item.get("exit_code").and_then(|value| value.as_i64()),
                    log_path: json_string(item.get("log_path")),
                    notes: json_string(item.get("notes")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if steps.is_empty() {
        return None;
    }
    let markers = value.get("markers").and_then(|value| value.as_object());
    let marker = |name: &str| {
        markers
            .and_then(|items| items.get(name))
            .and_then(|value| value.as_str())
            .filter(|value| !value.is_empty())
            .map(|value| value.to_string())
    };
    Some(AutonomyEvidenceWorkflowReportFile {
        name: String::new(),
        path: String::new(),
        size_bytes: 0,
        modified_unix_ms: None,
        status: json_string(value.get("status")),
        generated_at: json_string(value.get("generated_at")),
        workflow_dir: json_string(value.get("workflow_dir")),
        summary: AutonomyEvidenceWorkflowSummary {
            passed: value
                .pointer("/summary/passed")
                .and_then(|value| value.as_u64()),
            failed: value
                .pointer("/summary/failed")
                .and_then(|value| value.as_u64()),
            skipped: value
                .pointer("/summary/skipped")
                .and_then(|value| value.as_u64()),
        },
        steps,
        marker_count: markers.map(|items| items.len() as u64).unwrap_or(0),
        workflow_logs_path: marker("__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__")
            .or_else(|| json_string(value.get("log_archive"))),
        workflow_logs_local_path: None,
        workflow_validation_path: marker("__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__")
            .or_else(|| json_string(value.get("validation_report"))),
        workflow_validation_local_path: None,
        workflow_validation_summary: None,
        support_bundle_path: marker("__VISION_NAV_SUPPORT_ZIP__"),
        support_bundle_local_path: None,
        field_evidence_report_path: marker("__VISION_NAV_FIELD_EVIDENCE_REPORT__"),
        field_evidence_report_local_path: None,
        feature_method_report_path: marker("__VISION_NAV_FEATURE_METHOD_REPORT__"),
        feature_method_report_local_path: None,
        threshold_report_path: marker("__VISION_NAV_THRESHOLD_REPORT__"),
        threshold_report_local_path: None,
        rosbag_validation_path: marker("__VISION_NAV_ROSBAG_EXPORT_VALIDATION__"),
        rosbag_validation_local_path: None,
        readiness_report_path: marker("__VISION_NAV_AUTONOMY_REPORT__"),
        readiness_report_local_path: None,
        handoff_path: marker("__VISION_NAV_AUTONOMY_HANDOFF__"),
        handoff_local_path: None,
        evidence_package_path: marker("__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__"),
        evidence_package_local_path: None,
        field_collection_plan_path: marker("__VISION_NAV_FIELD_COLLECTION_PLAN__"),
        field_collection_plan_local_path: None,
        field_collection_plan_markdown_path: marker("__VISION_NAV_FIELD_COLLECTION_PLAN_MD__"),
        field_collection_plan_markdown_local_path: None,
        field_metadata_update_command: marker("__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__"),
        px4_receiver_report_path: marker("__VISION_NAV_PX4_SITL_REPORT__"),
        px4_receiver_report_local_path: None,
        px4_prereq_report_path: marker("__VISION_NAV_PX4_SITL_PREREQS__"),
        px4_prereq_report_local_path: None,
    })
}

fn populate_workflow_report_local_artifacts(
    report: &mut AutonomyEvidenceWorkflowReportFile,
    report_path: &Path,
) {
    report.workflow_logs_local_path =
        workflow_artifact_local_path(report_path, report.workflow_logs_path.as_deref(), None);
    report.workflow_validation_local_path = workflow_artifact_local_path(
        report_path,
        report.workflow_validation_path.as_deref(),
        None,
    );
    report.workflow_validation_summary = report
        .workflow_validation_local_path
        .as_deref()
        .and_then(read_workflow_validation_summary);
    report.support_bundle_local_path = workflow_artifact_local_path(
        report_path,
        report.support_bundle_path.as_deref(),
        Some("support-bundles"),
    );
    report.field_evidence_report_local_path = workflow_artifact_local_path(
        report_path,
        report.field_evidence_report_path.as_deref(),
        None,
    );
    report.feature_method_report_local_path = workflow_artifact_local_path(
        report_path,
        report.feature_method_report_path.as_deref(),
        Some("feature-method-bench"),
    );
    report.threshold_report_local_path =
        workflow_artifact_local_path(report_path, report.threshold_report_path.as_deref(), None);
    report.rosbag_validation_local_path = workflow_artifact_local_path(
        report_path,
        report.rosbag_validation_path.as_deref(),
        Some("terrain-match"),
    );
    report.readiness_report_local_path =
        workflow_artifact_local_path(report_path, report.readiness_report_path.as_deref(), None);
    report.handoff_local_path =
        workflow_artifact_local_path(report_path, report.handoff_path.as_deref(), None);
    report.evidence_package_local_path =
        workflow_artifact_local_path(report_path, report.evidence_package_path.as_deref(), None);
    report.field_collection_plan_local_path = workflow_artifact_local_path(
        report_path,
        report.field_collection_plan_path.as_deref(),
        None,
    );
    report.field_collection_plan_markdown_local_path = workflow_artifact_local_path(
        report_path,
        report.field_collection_plan_markdown_path.as_deref(),
        None,
    );
    report.px4_receiver_report_local_path = workflow_artifact_local_path(
        report_path,
        report.px4_receiver_report_path.as_deref(),
        Some("px4-sitl-evidence"),
    );
    report.px4_prereq_report_local_path = workflow_artifact_local_path(
        report_path,
        report.px4_prereq_report_path.as_deref(),
        Some("px4-sitl-evidence"),
    );
}

fn workflow_artifact_local_path(
    report_path: &Path,
    remote_path: Option<&str>,
    sibling_dir: Option<&str>,
) -> Option<String> {
    let remote_path = remote_path.filter(|value| !value.is_empty())?;
    let remote = Path::new(remote_path);
    if remote.is_file() {
        return Some(remote.to_string_lossy().into_owned());
    }
    let file_name = remote.file_name()?;
    let report_dir = report_path.parent()?;
    let artifact_dir = sibling_dir
        .and_then(|dir| report_dir.parent().map(|parent| parent.join(dir)))
        .unwrap_or_else(|| report_dir.to_path_buf());
    let candidate = artifact_dir.join(file_name);
    candidate
        .is_file()
        .then(|| candidate.to_string_lossy().into_owned())
}

fn read_workflow_validation_summary(
    path: &str,
) -> Option<AutonomyEvidenceWorkflowValidationSummary> {
    let text = std::fs::read_to_string(Path::new(path)).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    workflow_validation_summary_from_json(&value)
}

fn workflow_validation_summary_from_json(
    value: &serde_json::Value,
) -> Option<AutonomyEvidenceWorkflowValidationSummary> {
    if value.get("schema_version").and_then(|value| value.as_str())
        != Some("vision_nav_autonomy_evidence_workflow_validation_v1")
    {
        return None;
    }
    let issues = json_string_array(value.get("issues"));
    let checks = value
        .get("checks")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| {
                    let details = item.get("details");
                    let missing_markers = details
                        .and_then(|details| details.get("missing_markers"))
                        .or_else(|| item.get("missing_markers"));
                    let present_markers = details
                        .and_then(|details| details.get("present_markers"))
                        .or_else(|| item.get("present_markers"));
                    let marker_count = details
                        .and_then(|details| details.get("marker_count"))
                        .or_else(|| item.get("marker_count"))
                        .and_then(|value| value.as_u64());
                    let missing_steps = details
                        .and_then(|details| details.get("missing_steps"))
                        .or_else(|| item.get("missing_steps"));
                    let non_passed_count = details
                        .and_then(|details| details.get("non_passed_count"))
                        .or_else(|| item.get("non_passed_count"))
                        .and_then(|value| value.as_u64());
                    let non_passed_steps = details
                        .and_then(|details| details.get("non_passed_steps"))
                        .or_else(|| item.get("non_passed_steps"))
                        .and_then(|value| value.as_array())
                        .map(|items| {
                            items
                                .iter()
                                .filter(|item| item.is_object())
                                .map(|item| AutonomyEvidenceWorkflowValidationStepResult {
                                    name: json_string(item.get("name")),
                                    status: json_string(item.get("status")),
                                    exit_code: item
                                        .get("exit_code")
                                        .and_then(|value| value.as_i64()),
                                    notes: json_string(item.get("notes")),
                                    current_preflight_allows_capture: item
                                        .get("current_preflight_allows_capture")
                                        .and_then(|value| value.as_bool()),
                                    current_preflight_report: json_string(item.get("current_preflight_report")),
                                    current_preflight_status: json_string(item.get("current_preflight_status")),
                                    current_ready_for_registration: item
                                        .get("current_ready_for_registration")
                                        .and_then(|value| value.as_bool()),
                                    guidance: json_string(item.get("guidance")),
                                })
                                .collect::<Vec<_>>()
                        })
                        .unwrap_or_default();
                    AutonomyEvidenceWorkflowValidationCheck {
                        name: json_string(item.get("name")),
                        status: json_string(item.get("status")),
                        message: json_string(item.get("message")),
                        marker_count,
                        missing_markers: json_string_array(missing_markers),
                        present_markers: json_string_array(present_markers),
                        missing_steps: json_string_array(missing_steps),
                        non_passed_count,
                        non_passed_steps,
                    }
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    Some(AutonomyEvidenceWorkflowValidationSummary {
        status: json_string(value.get("status")),
        workflow_status: json_string(value.get("workflow_status")),
        step_count: value.get("step_count").and_then(|value| value.as_u64()),
        marker_count: value.get("marker_count").and_then(|value| value.as_u64()),
        issue_count: issues.len() as u64,
        issues,
        next_required_step: workflow_validation_next_step_from_json(
            value.get("next_required_step"),
        ),
        checks,
        log_archive: json_string(value.get("log_archive")),
    })
}

fn workflow_validation_next_step_from_json(
    value: Option<&serde_json::Value>,
) -> Option<AutonomyEvidenceWorkflowValidationNextStep> {
    let value = value?;
    if !value.is_object() {
        return None;
    }
    Some(AutonomyEvidenceWorkflowValidationNextStep {
        name: json_string(value.get("name")),
        status: json_string(value.get("status")),
        exit_code: value.get("exit_code").and_then(|value| value.as_i64()),
        notes: json_string(value.get("notes")),
        command: json_string(value.get("command")),
        desktop_action: json_string(value.get("desktop_action")),
        metadata_update_command: json_string(value.get("metadata_update_command")),
        bundle_path: json_string(value.get("bundle_path")),
        expected_log: json_string(value.get("expected_log")),
        output_dir: json_string(value.get("output_dir")),
        runtime_status_path: json_string(value.get("runtime_status_path")),
        capture_command_after_bundle: json_string(value.get("capture_command_after_bundle")),
    })
}

fn field_collection_plan_from_json(value: &serde_json::Value) -> Option<FieldCollectionPlanFile> {
    if value.get("schema_version").and_then(|value| value.as_str())
        != Some("vision_nav_field_collection_plan_v1")
    {
        return None;
    }
    let conditions = value
        .get("conditions")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .filter_map(field_collection_plan_condition_from_json)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let next_condition = value
        .get("next_condition")
        .and_then(field_collection_plan_condition_from_json)
        .or_else(|| {
            conditions
                .iter()
                .find(|condition| condition.status.as_deref() != Some("registered"))
                .cloned()
        });
    Some(FieldCollectionPlanFile {
        name: String::new(),
        path: String::new(),
        size_bytes: 0,
        modified_unix_ms: None,
        markdown_path: None,
        markdown_size_bytes: None,
        markdown_modified_unix_ms: None,
        status: json_string(value.get("status")),
        site_name: json_string(value.get("site_name")),
        manifest_path: json_string(value.get("manifest_path")),
        bundle: json_string(value.get("bundle")),
        capture_root: json_string(value.get("capture_root")),
        pending_preflight_command_count: value
            .get("pending_preflight_command_count")
            .and_then(|value| value.as_u64()),
        pending_preflight_capture_command_count: value
            .get("pending_preflight_capture_command_count")
            .and_then(|value| value.as_u64()),
        pending_capture_command_count: value
            .get("pending_capture_command_count")
            .and_then(|value| value.as_u64()),
        pending_metadata_update_command_count: value
            .get("pending_metadata_update_command_count")
            .and_then(|value| value.as_u64()),
        pending_registration_command_count: value
            .get("pending_registration_command_count")
            .and_then(|value| value.as_u64()),
        capture_output_dir_count: value
            .get("capture_output_dir_count")
            .and_then(|value| value.as_u64()),
        runtime_status_path_count: value
            .get("runtime_status_path_count")
            .and_then(|value| value.as_u64()),
        condition_source_log_count: value
            .get("condition_source_log_count")
            .and_then(|value| value.as_u64()),
        next_condition,
        summary: FieldCollectionPlanSummary {
            required_count: value
                .pointer("/summary/required_count")
                .and_then(|value| value.as_u64()),
            registered_count: value
                .pointer("/summary/registered_count")
                .and_then(|value| value.as_u64()),
            registered_missing_log_count: value
                .pointer("/summary/registered_missing_log_count")
                .and_then(|value| value.as_u64()),
            placeholder_count: value
                .pointer("/summary/placeholder_count")
                .and_then(|value| value.as_u64()),
            missing_count: value
                .pointer("/summary/missing_count")
                .and_then(|value| value.as_u64()),
        },
        conditions,
    })
}

fn field_collection_plan_condition_from_json(
    item: &serde_json::Value,
) -> Option<FieldCollectionPlanCondition> {
    if !item.is_object() {
        return None;
    }
    let capture_command = json_string(item.get("capture_command"));
    let preflight_command = json_string(item.get("preflight_command"));
    let capture_output_dir = json_string(item.get("capture_output_dir"));
    let capture_command = capture_command_with_runtime_status(capture_command, capture_output_dir.as_deref());
    let preflight_capture_command = json_string(item.get("preflight_capture_command"))
        .or_else(|| command_sequence(preflight_command.clone(), capture_command.clone()));
    Some(FieldCollectionPlanCondition {
        condition: json_string(item.get("condition")),
        label: json_string(item.get("label")),
        expected: json_string(item.get("expected")),
        status: json_string(item.get("status")),
        notes: json_string(item.get("notes")),
        case_name: json_string(item.get("case_name")),
        manifest_log_path: json_string(item.get("manifest_log_path")),
        manifest_log_exists: item
            .get("manifest_log_exists")
            .and_then(|value| value.as_bool()),
        source_log: json_string(item.get("source_log")),
        legacy_source_log: json_string(item.get("legacy_source_log")),
        capture_output_dir: capture_output_dir.clone(),
        runtime_status_path: json_string(item.get("runtime_status_path")),
        has_preflight_command: item
            .get("has_preflight_command")
            .and_then(|value| value.as_bool())
            .or_else(|| preflight_command.as_ref().map(|value| !value.is_empty())),
        has_capture_command: item
            .get("has_capture_command")
            .and_then(|value| value.as_bool())
            .or_else(|| capture_command.as_ref().map(|value| !value.is_empty())),
        has_preflight_capture_command: item
            .get("has_preflight_capture_command")
            .and_then(|value| value.as_bool())
            .or_else(|| preflight_capture_command.as_ref().map(|value| !value.is_empty())),
        has_metadata_update_command: item
            .get("has_metadata_update_command")
            .and_then(|value| value.as_bool())
            .or_else(|| {
                json_string(item.get("metadata_update_command")).map(|value| !value.is_empty())
            }),
        has_register_command: item
            .get("has_register_command")
            .and_then(|value| value.as_bool())
            .or_else(|| json_string(item.get("register_command")).map(|value| !value.is_empty())),
        bundle: json_string(item.get("bundle")),
        capture_metadata: item.get("capture_metadata").cloned(),
        preflight_command,
        preflight_capture_command,
        capture_command,
        metadata_update_command: json_string(item.get("metadata_update_command")),
        register_command: json_string(item.get("register_command")),
    })
}

fn command_sequence(first: Option<String>, second: Option<String>) -> Option<String> {
    let parts: Vec<String> = [first, second]
        .into_iter()
        .flatten()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .collect();
    if parts.is_empty() {
        None
    } else {
        Some(parts.join(" && "))
    }
}

fn capture_command_with_runtime_status(command: Option<String>, runtime_status_root: Option<&str>) -> Option<String> {
    command.map(|value| {
        let read_command = runtime_status_root
            .filter(|root| !root.trim().is_empty())
            .map(|root| {
                format!(
                    "VISION_NAV_RUNTIME_STATUS_ROOTS={} ./scripts/pi/read_runtime_status.sh",
                    shell_env_value(root.trim())
                )
            })
            .unwrap_or_else(|| "./scripts/pi/read_runtime_status.sh".to_string());
        if value.contains("read_runtime_status.sh") {
            if runtime_status_root.is_some() && !value.contains("VISION_NAV_RUNTIME_STATUS_ROOTS") {
                value.replace("./scripts/pi/read_runtime_status.sh", &read_command)
            } else {
                value
            }
        } else {
            format!("{value} && {read_command}")
        }
    })
}

fn shell_env_value(value: &str) -> String {
    let expandable_path = ["$HOME/", "${HOME}/", "$PWD/", "${PWD}/"]
        .iter()
        .any(|prefix| value.starts_with(prefix));
    let has_unsafe = value
        .chars()
        .any(|ch| ch.is_whitespace() || matches!(ch, '"' | '\'' | '`' | ';' | '&' | '|' | '<' | '>'));
    if expandable_path && !has_unsafe {
        value.to_string()
    } else {
        format!("'{}'", value.replace('\'', "'\"'\"'"))
    }
}

fn field_collection_plan_report_from_json(
    value: &serde_json::Value,
) -> Option<SupportBundleFieldCollectionPlanReport> {
    let plan = field_collection_plan_from_json(value)?;
    Some(SupportBundleFieldCollectionPlanReport {
        status: plan.status,
        site_name: plan.site_name,
        manifest_path: plan.manifest_path,
        bundle: plan.bundle,
        source_log: json_string(value.get("source_log")),
        capture_root: plan.capture_root,
        pending_preflight_command_count: plan.pending_preflight_command_count,
        pending_preflight_capture_command_count: plan.pending_preflight_capture_command_count,
        pending_capture_command_count: plan.pending_capture_command_count,
        pending_metadata_update_command_count: plan.pending_metadata_update_command_count,
        pending_registration_command_count: plan.pending_registration_command_count,
        capture_output_dir_count: plan.capture_output_dir_count,
        runtime_status_path_count: plan.runtime_status_path_count,
        condition_source_log_count: plan.condition_source_log_count,
        next_condition: plan.next_condition,
        summary: plan.summary,
        conditions: plan.conditions,
    })
}

fn field_capture_preflight_check_from_json(
    value: &serde_json::Value,
) -> SupportBundleFieldCapturePreflightCheck {
    let bundle_diagnostic = value
        .get("bundle_diagnostic")
        .or_else(|| value.pointer("/details/bundle_diagnostic"))
        .or_else(|| value.pointer("/details/diagnostic"))
        .and_then(support_bundle_diagnostic_from_json);
    SupportBundleFieldCapturePreflightCheck {
        name: json_string(value.get("name")),
        status: json_string(value.get("status")),
        message: json_string(value.get("message")),
        path: json_string(value.get("path"))
            .or_else(|| json_string(value.pointer("/details/path"))),
        desktop_action: json_string(value.get("desktop_action"))
            .or_else(|| json_string(value.pointer("/details/desktop_action"))),
        validation_command: json_string(value.get("validation_command"))
            .or_else(|| json_string(value.pointer("/details/validation_command"))),
        missing: value
            .get("missing")
            .cloned()
            .or_else(|| value.pointer("/details/missing").cloned()),
        issue_count: value
            .get("issue_count")
            .or_else(|| value.pointer("/details/issue_count"))
            .and_then(|value| value.as_u64()),
        bundle_diagnostic,
    }
}

fn support_bundle_diagnostic_from_json(
    value: &serde_json::Value,
) -> Option<SupportBundleDiagnostic> {
    if !value.is_object() {
        return None;
    }
    let bundle_candidates = value
        .get("bundle_candidates")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| SupportBundleDiagnosticBundleCandidate {
                    path: json_string(item.get("path")),
                    bundle_id: json_string(item.get("bundle_id")),
                    tile_index_exists: item
                        .get("tile_index_exists")
                        .and_then(|value| value.as_bool()),
                    field_proof_warning: json_string(item.get("field_proof_warning")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let map_source_candidates = value
        .get("map_source_candidates")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| SupportBundleDiagnosticMapSourceCandidate {
                    path: json_string(item.get("path")),
                    name: json_string(item.get("name")),
                    source: json_string(item.get("source")),
                    georef_source: json_string(item.get("georef_source")),
                    source_format: json_string(item.get("source_format")),
                    requires_import: item
                        .get("requires_import")
                        .and_then(|value| value.as_bool()),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let mission_plan_candidates = value
        .get("mission_plan_candidates")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| SupportBundleDiagnosticMissionPlanCandidate {
                    path: json_string(item.get("path")),
                    plan_type: json_string(item.get("type")),
                    name: json_string(item.get("name")),
                    mission_item_count: item
                        .get("mission_item_count")
                        .and_then(|value| value.as_u64()),
                    gnss_denied_status: json_string(item.get("gnss_denied_status")),
                    has_gnss_denied: item
                        .get("has_gnss_denied")
                        .and_then(|value| value.as_bool()),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let recommended_actions = value
        .get("recommended_actions")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| SupportBundleDiagnosticAction {
                    id: json_string(item.get("id")),
                    status: json_string(item.get("status")),
                    title: json_string(item.get("title")),
                    desktop_action: json_string(item.get("desktop_action")),
                    command: json_string(item.get("command")),
                    notes: json_string(item.get("notes")),
                    bundle_path: json_string(item.get("bundle_path")),
                    map_source_path: json_string(item.get("map_source_path")),
                    mission_plan_path: json_string(item.get("mission_plan_path")),
                    qgc_plan_path: json_string(item.get("qgc_plan_path")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let search_roots = json_string_array(value.get("search_roots"));
    Some(SupportBundleDiagnostic {
        bundle_exists: value.get("bundle_exists").and_then(|value| value.as_bool()),
        missing_required_files: json_string_array(value.get("missing_required_files")),
        search_root_count: value
            .get("search_root_count")
            .and_then(|value| value.as_u64())
            .or_else(|| (!search_roots.is_empty()).then_some(search_roots.len() as u64)),
        search_roots,
        bundle_candidate_count: value
            .get("bundle_candidate_count")
            .and_then(|value| value.as_u64()),
        map_source_candidate_count: value
            .get("map_source_candidate_count")
            .and_then(|value| value.as_u64()),
        mission_plan_candidate_count: value
            .get("mission_plan_candidate_count")
            .and_then(|value| value.as_u64()),
        bundle_candidates,
        map_source_candidates,
        mission_plan_candidates,
        recommended_actions,
    })
}

fn field_capture_preflight_action_from_json(
    value: &serde_json::Value,
) -> SupportBundleFieldCapturePreflightAction {
    SupportBundleFieldCapturePreflightAction {
        id: json_string(value.get("id")),
        status: json_string(value.get("status")),
        title: json_string(value.get("title")),
        desktop_action: json_string(value.get("desktop_action")),
        command: json_string(value.get("command")),
        waits_on: json_string_array(value.get("waits_on")),
        bundle_path: json_string(value.get("bundle_path")),
        capture_output_dir: json_string(value.get("capture_output_dir")),
        source_log: json_string(value.get("source_log")),
        runtime_status_path: json_string(value.get("runtime_status_path")),
        preflight_capture_command: json_string(value.get("preflight_capture_command")),
        notes: json_string(value.get("notes")),
        bundle_diagnostic: value
            .get("bundle_diagnostic")
            .and_then(support_bundle_diagnostic_from_json),
    }
}

fn field_capture_preflight_report_from_json(
    value: &serde_json::Value,
) -> Option<SupportBundleFieldCapturePreflightReport> {
    if value.get("schema_version").and_then(|value| value.as_str())
        != Some("vision_nav_field_capture_preflight_v1")
    {
        return None;
    }
    let checks = value
        .get("checks")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .map(field_capture_preflight_check_from_json)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let next_actions = value
        .get("next_actions")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .map(field_capture_preflight_action_from_json)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    Some(SupportBundleFieldCapturePreflightReport {
        status: json_string(value.get("status")),
        plan_path: json_string(value.get("plan_path")),
        repo_root: json_string(value.get("repo_root")),
        condition: json_string(value.get("condition")),
        case_name: json_string(value.get("case_name")),
        expected: json_string(value.get("expected")),
        bundle_path: json_string(value.get("bundle_path")),
        bundle_validation_command: json_string(value.get("bundle_validation_command")),
        ready_for_capture: value
            .get("ready_for_capture")
            .and_then(|value| value.as_bool()),
        ready_for_registration: value
            .get("ready_for_registration")
            .and_then(|value| value.as_bool()),
        capture_output_dir: json_string(value.get("capture_output_dir")),
        source_log: json_string(value.get("source_log")),
        runtime_status_path: json_string(value.get("runtime_status_path")),
        preflight_capture_command: json_string(value.get("preflight_capture_command")),
        summary: value.get("summary").cloned(),
        checks,
        next_actions,
    })
}

fn autonomy_evidence_blockers_from_value(
    value: Option<&serde_json::Value>,
) -> Vec<AutonomyReadinessEvidenceBlocker> {
    value
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| AutonomyReadinessEvidenceBlocker {
                    name: json_string(item.get("name")),
                    status: json_string(item.get("status")),
                    message: json_string(item.get("message")),
                    missing_conditions: json_string_array(item.get("missing_conditions")),
                    bench_subchecks: autonomy_bench_subchecks_from_value(
                        item.get("bench_subchecks"),
                    ),
                    expected_bench_inputs: json_string_array(item.get("expected_bench_inputs")),
                    support_bundle_command: json_string(item.get("support_bundle_command")),
                    bench_evidence_actions: autonomy_bench_evidence_actions_from_value(
                        item.get("bench_evidence_actions"),
                    ),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn autonomy_bench_evidence_actions_from_value(
    value: Option<&serde_json::Value>,
) -> Vec<AutonomyReadinessBenchEvidenceAction> {
    value
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| AutonomyReadinessBenchEvidenceAction {
                    label: json_string(item.get("label")),
                    desktop_action: json_string(item.get("desktop_action")),
                    command: json_string(item.get("command")),
                    blocked_by: json_string(item.get("blocked_by")),
                    notes: json_string(item.get("notes")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn autonomy_bench_subchecks_from_value(
    value: Option<&serde_json::Value>,
) -> Vec<AutonomyReadinessBenchSubcheck> {
    value
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter(|item| item.is_object())
                .map(|item| AutonomyReadinessBenchSubcheck {
                    name: json_string(item.get("name")),
                    status: json_string(item.get("status")),
                    message: json_string(item.get("message")),
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn json_string_array(value: Option<&serde_json::Value>) -> Vec<String> {
    value
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str().map(|text| text.to_string()))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn image_mime_type(name: &str) -> Option<&'static str> {
    match Path::new(name)
        .extension()
        .and_then(|extension| extension.to_str())
        .map(|extension| extension.to_ascii_lowercase())
        .as_deref()
    {
        Some("png") => Some("image/png"),
        Some("jpg") | Some("jpeg") => Some("image/jpeg"),
        Some("webp") => Some("image/webp"),
        Some("gif") => Some("image/gif"),
        Some("bmp") => Some("image/bmp"),
        _ => None,
    }
}

fn should_preview_image_entry(name: &str, size_bytes: u64) -> bool {
    if size_bytes == 0 || size_bytes > IMAGE_PREVIEW_MAX_BYTES || image_mime_type(name).is_none() {
        return false;
    }
    let lower = name.to_ascii_lowercase();
    if lower.starts_with("bundle/ortho/")
        || lower.starts_with("bundle/imagery/")
        || lower.starts_with("bundle/index/")
        || lower.starts_with("bundle/elevation/")
        || lower.contains("/imagery/tiles/")
        || lower.contains("/index/descriptors/")
        || lower.ends_with("/satellite.png")
    {
        return false;
    }
    lower.starts_with("extras/")
        || lower.starts_with("logs/")
        || lower.starts_with("summaries/")
        || [
            "camera",
            "frame",
            "debug",
            "match",
            "replay",
            "smoke",
            "calibration",
            "preview",
        ]
        .iter()
        .any(|token| lower.contains(token))
}

fn read_image_preview_entry(
    archive: &mut ZipArchive<File>,
    name: &str,
    size_bytes: u64,
) -> Result<Option<SupportBundleImagePreview>, String> {
    let mime_type = match image_mime_type(name) {
        Some(value) => value,
        None => return Ok(None),
    };
    if !should_preview_image_entry(name, size_bytes) {
        return Ok(None);
    }
    let mut entry = match archive.by_name(name) {
        Ok(entry) => entry,
        Err(zip::result::ZipError::FileNotFound) => return Ok(None),
        Err(error) => return Err(error.to_string()),
    };
    let mut bytes = Vec::new();
    entry.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    if bytes.len() as u64 > IMAGE_PREVIEW_MAX_BYTES {
        return Ok(None);
    }
    Ok(Some(SupportBundleImagePreview {
        name: Path::new(name)
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or(name)
            .to_string(),
        path: name.to_string(),
        mime_type: mime_type.to_string(),
        size_bytes,
        base64_data: general_purpose::STANDARD.encode(bytes),
    }))
}

fn support_artifact_entry(name: &str, size_bytes: u64) -> Option<SupportBundleArtifactEntry> {
    if size_bytes > SUPPORT_ARTIFACT_MAX_BYTES || safe_zip_entry_rel_path(name).is_err() {
        return None;
    }
    let lower = name.to_ascii_lowercase();
    let kind = support_artifact_kind(&lower)?;
    Some(SupportBundleArtifactEntry {
        name: Path::new(name)
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or(name)
            .to_string(),
        path: name.to_string(),
        kind,
        size_bytes,
    })
}

fn support_artifact_kind(lower_name: &str) -> Option<String> {
    if lower_name == "support_manifest.json" {
        return Some("manifest".to_string());
    }
    if lower_name.starts_with("logs/")
        && has_extension(lower_name, &["jsonl", "json", "log", "txt", "csv"])
    {
        return Some("runtime log".to_string());
    }
    if lower_name.starts_with("summaries/replay_gates/") && lower_name.ends_with(".gate.json") {
        return Some("replay gate report".to_string());
    }
    if lower_name.starts_with("summaries/px4_sitl_evidence/") && lower_name.ends_with(".json") {
        return Some("px4 receiver report".to_string());
    }
    if lower_name.starts_with("summaries/px4_params/") && lower_name.ends_with(".json") {
        return Some("px4 parameter report".to_string());
    }
    if lower_name.starts_with("summaries/ardupilot_params/") && lower_name.ends_with(".json") {
        return Some("ardupilot parameter report".to_string());
    }
    if lower_name.starts_with("summaries/feature_method_benchmarks/")
        && lower_name.ends_with(".json")
    {
        return Some("feature benchmark report".to_string());
    }
    if lower_name.starts_with("summaries/field_evidence/") && lower_name.ends_with(".json") {
        return Some("field evidence report".to_string());
    }
    if lower_name.starts_with("summaries/field_collection_plans/") && lower_name.ends_with(".json")
    {
        return Some("field collection plan".to_string());
    }
    if lower_name.starts_with("summaries/field_capture_preflights/")
        && lower_name.ends_with(".json")
    {
        return Some("field capture preflight".to_string());
    }
    if lower_name.starts_with("summaries/threshold_tuning/") && lower_name.ends_with(".json") {
        return Some("threshold tuning report".to_string());
    }
    if lower_name.starts_with("summaries/rosbag_export_validations/")
        && lower_name.ends_with(".json")
    {
        return Some("rosbag export validation".to_string());
    }
    if lower_name.starts_with("summaries/rosbag2_cli_reviews/") && lower_name.ends_with(".json") {
        return Some("rosbag2 cli review".to_string());
    }
    if lower_name.starts_with("summaries/autonomy_evidence_workflow/")
        && lower_name.ends_with(".json")
    {
        return Some("evidence workflow summary".to_string());
    }
    if lower_name == "summaries/bench_readiness.json" {
        return Some("bench readiness report".to_string());
    }
    if lower_name.starts_with("summaries/")
        && has_extension(lower_name, &["json", "jsonl", "txt", "log", "csv"])
    {
        return Some("summary".to_string());
    }
    if lower_name.starts_with("extras/")
        && !is_heavy_support_asset(lower_name)
        && has_extension(
            lower_name,
            &[
                "json", "jsonl", "txt", "log", "csv", "md", "yaml", "yml", "params", "ulg",
                "px4log", "png", "jpg", "jpeg", "webp", "bmp",
            ],
        )
    {
        if lower_name.starts_with("extras/field_collection_plans/") {
            return Some("field collection artifact".to_string());
        }
        if lower_name.starts_with("extras/field_capture_preflights/") {
            return Some("field capture preflight artifact".to_string());
        }
        if lower_name.starts_with("extras/rosbag2_cli_reviews/") {
            return Some("rosbag2 cli artifact".to_string());
        }
        if lower_name.starts_with("extras/autonomy_evidence_workflow/") {
            return Some("evidence workflow artifact".to_string());
        }
        return Some("extra artifact".to_string());
    }
    if lower_name.starts_with("bundle/")
        && !is_heavy_support_asset(lower_name)
        && (lower_name.ends_with(".json")
            || lower_name.ends_with(".yaml")
            || lower_name.ends_with(".yml")
            || lower_name.ends_with(".plan")
            || lower_name.ends_with(".sha256"))
    {
        return Some("bundle metadata".to_string());
    }
    None
}

fn has_extension(lower_name: &str, extensions: &[&str]) -> bool {
    let Some(extension) = Path::new(lower_name)
        .extension()
        .and_then(|value| value.to_str())
    else {
        return false;
    };
    extensions.iter().any(|candidate| extension == *candidate)
}

fn is_heavy_support_asset(lower_name: &str) -> bool {
    lower_name.starts_with("bundle/ortho/")
        || lower_name.starts_with("bundle/imagery/")
        || lower_name.starts_with("bundle/index/")
        || lower_name.starts_with("bundle/elevation/")
        || lower_name.contains("/imagery/tiles/")
        || lower_name.contains("/index/descriptors/")
        || lower_name.ends_with("/satellite.png")
        || lower_name.ends_with(".npz")
        || lower_name.ends_with(".sqlite")
        || lower_name.ends_with(".tif")
        || lower_name.ends_with(".tiff")
        || lower_name.ends_with(".cog")
}

fn safe_zip_entry_rel_path(entry_path: &str) -> Result<PathBuf, String> {
    let mut rel = PathBuf::new();
    for part in entry_path.split(|ch| ch == '/' || ch == '\\') {
        if part.is_empty() || part == "." || part == ".." {
            return Err("Support bundle entry has an unsafe path.".to_string());
        }
        if part.contains(':') {
            return Err("Support bundle entry has an unsafe path.".to_string());
        }
        rel.push(part);
    }
    if rel.as_os_str().is_empty() {
        return Err("Support bundle entry path is empty.".to_string());
    }
    Ok(rel)
}

fn reveal_path(path: &Path) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        let status = Command::new("open")
            .arg("-R")
            .arg(path)
            .status()
            .context("Failed to run macOS open command")?;
        if status.success() {
            return Ok(());
        }
        return Err(anyhow!("macOS open command failed with {status}"));
    }

    #[cfg(target_os = "windows")]
    {
        let status = if path.is_file() {
            Command::new("explorer")
                .arg(format!("/select,{}", path.display()))
                .status()
                .context("Failed to run Windows Explorer")?
        } else {
            Command::new("explorer")
                .arg(path)
                .status()
                .context("Failed to run Windows Explorer")?
        };
        if status.success() {
            return Ok(());
        }
        return Err(anyhow!("Windows Explorer failed with {status}"));
    }

    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    {
        let target = if path.is_file() {
            path.parent().unwrap_or(path)
        } else {
            path
        };
        let status = Command::new("xdg-open")
            .arg(target)
            .status()
            .context("Failed to run xdg-open")?;
        if status.success() {
            return Ok(());
        }
        Err(anyhow!("xdg-open failed with {status}"))
    }
}

fn read_support_bundle_summary(path: &Path) -> Option<SupportBundleSummary> {
    let file = File::open(path).ok()?;
    let mut archive = ZipArchive::new(file).ok()?;
    let mut manifest_entry = archive.by_name("support_manifest.json").ok()?;
    let mut text = String::new();
    manifest_entry.read_to_string(&mut text).ok()?;
    let manifest: serde_json::Value = serde_json::from_str(&text).ok()?;
    support_summary_from_manifest(&manifest)
}

fn json_string(value: Option<&serde_json::Value>) -> Option<String> {
    value
        .and_then(|value| value.as_str())
        .filter(|value| !value.is_empty())
        .map(|value| value.to_string())
}

fn bench_readiness_check_status(manifest: &serde_json::Value, name: &str) -> Option<String> {
    manifest
        .pointer("/bench_readiness/checks")
        .and_then(|value| value.as_array())
        .and_then(|checks| {
            checks.iter().find(|check| {
                check
                    .get("name")
                    .and_then(|value| value.as_str())
                    .is_some_and(|check_name| check_name == name)
            })
        })
        .and_then(|check| json_string(check.get("status")))
}

fn gnss_denied_plan_summary_status(manifest: &serde_json::Value) -> Option<String> {
    bench_readiness_check_status(manifest, "gnss_denied_plan").or_else(|| {
        json_string(manifest.pointer("/bundle/mission_plan/gnss_denied/status")).map(|status| {
            if status == "ready" {
                "passed".to_string()
            } else if status == "incomplete" {
                "failed".to_string()
            } else {
                status
            }
        })
    })
}

fn workflow_validation_check<'a>(
    validation_summary: Option<&'a serde_json::Value>,
    name: &str,
) -> Option<&'a serde_json::Value> {
    validation_summary
        .and_then(|value| value.get("checks"))
        .and_then(|value| value.as_array())
        .and_then(|checks| {
            checks.iter().find(|check| {
                check
                    .get("name")
                    .and_then(|value| value.as_str())
                    .is_some_and(|check_name| check_name == name)
            })
        })
}

fn workflow_validation_check_status(
    validation_summary: Option<&serde_json::Value>,
    name: &str,
) -> Option<String> {
    workflow_validation_check(validation_summary, name)
        .and_then(|check| json_string(check.get("status")))
}

fn workflow_validation_provenance_commit(
    validation_summary: Option<&serde_json::Value>,
) -> Option<String> {
    json_string(
        validation_summary
            .and_then(|value| value.get("workflow_provenance"))
            .and_then(|value| value.get("repo_commit")),
    )
    .or_else(|| {
        let check = workflow_validation_check(validation_summary, "workflow_provenance")?;
        json_string(check.get("repo_commit")).or_else(|| {
            json_string(
                check
                    .get("details")
                    .and_then(|value| value.get("repo_commit")),
            )
        })
    })
}

fn support_summary_from_manifest(manifest: &serde_json::Value) -> Option<SupportBundleSummary> {
    let health = manifest.pointer("/bundle/health");
    let provenance = manifest.pointer("/bundle/health/source_provenance");
    let evidence_workflow_validation =
        manifest.pointer("/autonomy_evidence_workflow/validation_summary");
    let source_name = json_string(provenance.and_then(|value| value.get("original_file")))
        .or_else(|| json_string(provenance.and_then(|value| value.get("map_name"))))
        .or_else(|| json_string(provenance.and_then(|value| value.get("orthophoto_path"))));
    let summary = SupportBundleSummary {
        bundle_id: json_string(manifest.pointer("/bundle/bundle_id")),
        bundle_health_status: json_string(health.and_then(|value| value.get("status"))),
        checksum_status: json_string(manifest.pointer("/bundle/health/checksums/status")),
        covered_file_count: manifest
            .pointer("/bundle/health/checksums/covered_file_count")
            .or_else(|| manifest.pointer("/bundle/health/checksums/entry_count"))
            .and_then(|value| value.as_u64()),
        elevation_status: json_string(manifest.pointer("/bundle/health/elevation/status")),
        elevation_asset_count: manifest
            .pointer("/bundle/health/elevation/asset_count")
            .and_then(|value| value.as_u64()),
        vertical_sanity_ready: manifest
            .pointer("/bundle/health/elevation/vertical_sanity_ready")
            .and_then(|value| value.as_bool()),
        map_source: json_string(provenance.and_then(|value| value.get("map_source"))),
        source_name,
        georef_source: json_string(provenance.and_then(|value| value.get("georef_source"))),
        georef_crs: json_string(provenance.and_then(|value| value.get("georef_crs"))),
        georef_confidence: provenance
            .and_then(|value| value.get("georef_confidence"))
            .and_then(|value| value.as_f64()),
        replay_gate_status: json_string(manifest.pointer("/replay_gates/status")),
        replay_case_count: manifest
            .pointer("/replay_gates/case_count")
            .and_then(|value| value.as_u64()),
        gnss_denied_plan_status: gnss_denied_plan_summary_status(manifest),
        px4_sitl_evidence_status: json_string(manifest.pointer("/px4_sitl_evidence/status")),
        px4_sitl_sample_count: manifest
            .pointer("/px4_sitl_evidence/listener/sample_count")
            .and_then(|value| value.as_u64()),
        px4_sitl_prereq_status: json_string(manifest.pointer("/px4_sitl_prereqs/status")),
        px4_params_status: json_string(manifest.pointer("/px4_params/status")),
        px4_ev_ctrl: manifest
            .pointer("/px4_params/parameters/EKF2_EV_CTRL")
            .and_then(|value| value.as_i64()),
        ardupilot_params_status: json_string(manifest.pointer("/ardupilot_params/status")),
        ardupilot_source_set: manifest
            .pointer("/ardupilot_params/parameters/source_set")
            .and_then(|value| value.as_i64()),
        ardupilot_posxy_source: manifest
            .pointer("/ardupilot_params/parameters/EK3_SRC1_POSXY")
            .or_else(|| manifest.pointer("/ardupilot_params/parameters/EK3_SRC2_POSXY"))
            .or_else(|| manifest.pointer("/ardupilot_params/parameters/EK3_SRC3_POSXY"))
            .and_then(|value| value.as_i64()),
        feature_method_benchmark_status: json_string(
            manifest.pointer("/feature_method_benchmarks/status"),
        ),
        feature_method_benchmark_recommended: json_string(
            manifest.pointer("/feature_method_benchmarks/reports/0/recommended_method"),
        ),
        feature_method_benchmark_report_count: manifest
            .pointer("/feature_method_benchmarks/report_count")
            .and_then(|value| value.as_u64()),
        field_evidence_status: json_string(manifest.pointer("/field_evidence/status")),
        field_evidence_field_case_count: manifest
            .pointer("/field_evidence/field_case_count")
            .and_then(|value| value.as_u64()),
        field_evidence_capture_metadata_issue_count: manifest
            .pointer("/field_evidence/capture_metadata_issue_count")
            .and_then(|value| value.as_u64()),
        field_evidence_report_count: manifest
            .pointer("/field_evidence/report_count")
            .and_then(|value| value.as_u64()),
        field_collection_plan_status: json_string(
            manifest.pointer("/field_collection_plans/status"),
        ),
        field_collection_plan_registered_count: manifest
            .pointer("/field_collection_plans/registered_count")
            .and_then(|value| value.as_u64()),
        field_collection_plan_required_count: manifest
            .pointer("/field_collection_plans/required_count")
            .and_then(|value| value.as_u64()),
        field_collection_plan_report_count: manifest
            .pointer("/field_collection_plans/report_count")
            .and_then(|value| value.as_u64()),
        field_collection_plan_pending_capture_command_count: manifest
            .pointer("/field_collection_plans/pending_capture_command_count")
            .and_then(|value| value.as_u64()),
        field_collection_plan_pending_metadata_update_command_count: manifest
            .pointer("/field_collection_plans/pending_metadata_update_command_count")
            .and_then(|value| value.as_u64()),
        field_collection_plan_pending_registration_command_count: manifest
            .pointer("/field_collection_plans/pending_registration_command_count")
            .and_then(|value| value.as_u64()),
        field_collection_plan_capture_output_dir_count: manifest
            .pointer("/field_collection_plans/capture_output_dir_count")
            .and_then(|value| value.as_u64()),
        field_collection_plan_runtime_status_path_count: manifest
            .pointer("/field_collection_plans/runtime_status_path_count")
            .and_then(|value| value.as_u64()),
        field_collection_plan_condition_source_log_count: manifest
            .pointer("/field_collection_plans/condition_source_log_count")
            .and_then(|value| value.as_u64()),
        field_capture_preflight_status: json_string(
            manifest.pointer("/field_capture_preflights/status"),
        ),
        field_capture_preflight_report_count: manifest
            .pointer("/field_capture_preflights/report_count")
            .and_then(|value| value.as_u64()),
        field_capture_preflight_ready_for_capture_count: manifest
            .pointer("/field_capture_preflights/ready_for_capture_count")
            .and_then(|value| value.as_u64()),
        field_capture_preflight_ready_for_registration_count: manifest
            .pointer("/field_capture_preflights/ready_for_registration_count")
            .and_then(|value| value.as_u64()),
        field_capture_preflight_failed_check_count: manifest
            .pointer("/field_capture_preflights/failed_check_count")
            .and_then(|value| value.as_u64()),
        field_capture_preflight_degraded_check_count: manifest
            .pointer("/field_capture_preflights/degraded_check_count")
            .and_then(|value| value.as_u64()),
        field_capture_preflight_next_action_count: manifest
            .pointer("/field_capture_preflights/next_action_count")
            .and_then(|value| value.as_u64()),
        field_capture_preflight_blocked_action_count: manifest
            .pointer("/field_capture_preflights/blocked_action_count")
            .and_then(|value| value.as_u64()),
        threshold_tuning_status: json_string(manifest.pointer("/threshold_tuning/status")),
        threshold_tuning_field_case_count: manifest
            .pointer("/threshold_tuning/field_case_count")
            .and_then(|value| value.as_u64()),
        threshold_tuning_capture_metadata_issue_count: manifest
            .pointer("/threshold_tuning/capture_metadata_issue_count")
            .and_then(|value| value.as_u64()),
        threshold_tuning_report_count: manifest
            .pointer("/threshold_tuning/report_count")
            .and_then(|value| value.as_u64()),
        rosbag_export_validation_status: json_string(
            manifest.pointer("/rosbag_export_validations/status"),
        ),
        rosbag_export_validation_report_count: manifest
            .pointer("/rosbag_export_validations/report_count")
            .and_then(|value| value.as_u64()),
        rosbag_export_validation_message_count: manifest
            .pointer("/rosbag_export_validations/message_count")
            .and_then(|value| value.as_u64()),
        rosbag_export_validation_topic_count: manifest
            .pointer("/rosbag_export_validations/topic_count")
            .and_then(|value| value.as_u64()),
        rosbag2_cli_review_status: json_string(manifest.pointer("/rosbag2_cli_reviews/status")),
        rosbag2_cli_review_report_count: manifest
            .pointer("/rosbag2_cli_reviews/report_count")
            .and_then(|value| value.as_u64()),
        evidence_workflow_status: json_string(
            manifest.pointer("/autonomy_evidence_workflow/status"),
        ),
        evidence_workflow_validation_status: json_string(
            evidence_workflow_validation.and_then(|value| value.get("status")),
        ),
        evidence_workflow_runtime_status: json_string(
            evidence_workflow_validation.and_then(|value| value.get("workflow_status")),
        ),
        evidence_workflow_provenance_status: workflow_validation_check_status(
            evidence_workflow_validation,
            "workflow_provenance",
        ),
        evidence_workflow_step_count: evidence_workflow_validation
            .and_then(|value| value.get("step_count"))
            .and_then(|value| value.as_u64()),
        evidence_workflow_issue_count: evidence_workflow_validation
            .and_then(|value| value.get("issue_count"))
            .and_then(|value| value.as_u64()),
        evidence_workflow_repo_commit: workflow_validation_provenance_commit(
            evidence_workflow_validation,
        ),
        bench_readiness_status: json_string(manifest.pointer("/bench_readiness/status")),
        bench_readiness_failed_count: manifest
            .pointer("/bench_readiness/summary/failed")
            .and_then(|value| value.as_u64()),
        bench_readiness_degraded_count: manifest
            .pointer("/bench_readiness/summary/degraded")
            .and_then(|value| value.as_u64()),
    };
    if summary.bundle_id.is_none()
        && summary.bundle_health_status.is_none()
        && summary.checksum_status.is_none()
        && summary.elevation_status.is_none()
        && summary.replay_gate_status.is_none()
        && summary.gnss_denied_plan_status.is_none()
        && summary.px4_sitl_evidence_status.is_none()
        && summary.px4_sitl_prereq_status.is_none()
        && summary.px4_params_status.is_none()
        && summary.ardupilot_params_status.is_none()
        && summary.feature_method_benchmark_status.is_none()
        && summary.field_evidence_status.is_none()
        && summary.field_collection_plan_status.is_none()
        && summary.field_capture_preflight_status.is_none()
        && summary.threshold_tuning_status.is_none()
        && summary.rosbag_export_validation_status.is_none()
        && summary.rosbag2_cli_review_status.is_none()
        && summary.evidence_workflow_status.is_none()
        && summary.bench_readiness_status.is_none()
    {
        return None;
    }
    Some(summary)
}

fn expand_local_path(path: &str) -> Result<PathBuf> {
    if path.is_empty() {
        return Err(anyhow!("Local directory is empty"));
    }
    if path == "~" {
        return std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| anyhow!("HOME is not set"));
    }
    if let Some(rest) = path.strip_prefix("~/") {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| anyhow!("HOME is not set"))?;
        return Ok(home.join(rest));
    }
    Ok(PathBuf::from(path))
}

#[cfg(test)]
mod tests {
    use super::{
        delete_support_bundle, expand_local_path, extract_support_bundle_artifact,
        list_autonomy_evidence_workflow_reports, list_autonomy_readiness_reports,
        list_feature_method_benchmark_reports, list_field_collection_plans,
        list_field_evidence_reports, list_field_evidence_templates, list_px4_prereq_reports,
        list_px4_receiver_reports, list_rosbag_export_validation_reports,
        list_threshold_tuning_reports, read_support_bundle_details,
        run_local_autonomy_readiness_audit_inner, run_local_px4_sitl_prereq_setup_inner,
        run_local_px4_sitl_receiver_capture_inner, run_local_rosbag2_cli_review_inner,
        support_bundle_diagnostic_from_json,
        support_summary_from_manifest,
    };
    use std::fs::{self, File};
    use std::io::Write;
    use std::path::Path;
    use std::time::{SystemTime, UNIX_EPOCH};
    use zip::write::SimpleFileOptions;

    #[test]
    fn expands_home_prefixed_support_bundle_dir() {
        let expanded = expand_local_path("~/DroneTransfer/from-pi/support-bundles")
            .expect("expand support path");
        assert!(expanded.ends_with("DroneTransfer/from-pi/support-bundles"));
    }

    #[test]
    fn runs_local_autonomy_readiness_audit_wrapper() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let repo = std::env::temp_dir().join(format!("drone-local-readiness-{stamp}"));
        let script_dir = repo.join("scripts/dev");
        fs::create_dir_all(&script_dir).expect("create script dir");
        let transfer_root = repo.join("from-pi");
        fs::create_dir_all(&transfer_root).expect("create transfer root");
        fs::write(
            script_dir.join("run_local_autonomy_readiness_audit.sh"),
            "#!/usr/bin/env bash\nprintf 'root=%s\\n' \"$VISION_NAV_DESKTOP_TRANSFER_FROM_PI\"\nprintf '__VISION_NAV_AUTONOMY_REPORT__=%s/out.json\\n' \"$PWD\"\nexit 7\n",
        )
        .expect("write wrapper");

        let result = run_local_autonomy_readiness_audit_inner(
            repo.to_str().expect("repo path"),
            Some(transfer_root.to_str().expect("transfer path")),
        )
        .expect("run wrapper");

        assert_eq!(result.exit_code, 7);
        assert!(result.stdout.contains("root="));
        assert!(result.stdout.contains("__VISION_NAV_AUTONOMY_REPORT__="));
        assert!(result.stderr.is_empty());
        let _ = fs::remove_dir_all(&repo);
    }

    #[test]
    fn runs_local_rosbag2_cli_review_wrapper_with_download_paths() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let repo = std::env::temp_dir().join(format!("drone-local-rosbag2-review-{stamp}"));
        let script_dir = repo.join("scripts/dev");
        fs::create_dir_all(&script_dir).expect("create script dir");
        fs::write(
            script_dir.join("run_rosbag2_cli_review.sh"),
            "#!/usr/bin/env bash\nprintf 'source=%s\\n' \"$VISION_NAV_ROSBAG_SOURCE_LOG\"\nprintf 'export=%s\\n' \"$VISION_NAV_ROSBAG2_EXPORT_DIR\"\nprintf '__VISION_NAV_ROSBAG2_CLI_REVIEW__=%s\\n' \"$VISION_NAV_ROSBAG2_CLI_REVIEW\"\nexit 5\n",
        )
        .expect("write wrapper");
        let transfer_root = repo.join("from-pi");

        let result = run_local_rosbag2_cli_review_inner(
            repo.to_str().expect("repo path"),
            Some(transfer_root.to_str().expect("transfer path")),
        )
        .expect("run wrapper");

        assert_eq!(result.exit_code, 5);
        assert!(result
            .stdout
            .contains("terrain-match/terrain_matches.jsonl"));
        assert!(result.stdout.contains("terrain-match/rosbag2-native"));
        assert!(result.stdout.contains("__VISION_NAV_ROSBAG2_CLI_REVIEW__="));
        assert!(transfer_root.join("terrain-match").is_dir());
        let _ = fs::remove_dir_all(&repo);
    }

    #[test]
    fn runs_local_px4_sitl_receiver_capture_wrapper_with_download_paths() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let repo = std::env::temp_dir().join(format!("drone-local-px4-sitl-capture-{stamp}"));
        let script_dir = repo.join("scripts/dev");
        fs::create_dir_all(&script_dir).expect("create script dir");
        fs::write(
            script_dir.join("run_px4_sitl_external_vision_capture.sh"),
            "#!/usr/bin/env bash\nprintf 'session=%s\\n' \"$VISION_NAV_SITL_SMOKE_DIR\"\nprintf '__VISION_NAV_PX4_SITL_REPORT__=%s/receiver_evidence.json\\n' \"$VISION_NAV_SITL_SMOKE_DIR\"\nexit 6\n",
        )
        .expect("write wrapper");
        let transfer_root = repo.join("from-pi");

        let result = run_local_px4_sitl_receiver_capture_inner(
            repo.to_str().expect("repo path"),
            Some(transfer_root.to_str().expect("transfer path")),
        )
        .expect("run wrapper");

        assert_eq!(result.exit_code, 6);
        assert!(result.stdout.contains("px4-sitl-evidence"));
        assert!(result.stdout.contains("__VISION_NAV_PX4_SITL_REPORT__="));
        assert!(transfer_root.join("px4-sitl-evidence").is_dir());
        let _ = fs::remove_dir_all(&repo);
    }

    #[test]
    fn runs_local_px4_sitl_prereq_setup_wrapper_as_dry_run() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let repo = std::env::temp_dir().join(format!("drone-local-px4-sitl-prereqs-{stamp}"));
        let script_dir = repo.join("scripts/dev");
        fs::create_dir_all(&script_dir).expect("create script dir");
        fs::write(
            script_dir.join("setup_px4_sitl_prereqs.sh"),
            "#!/usr/bin/env bash\nprintf 'args=%s\\n' \"$*\"\nprintf 'session=%s\\n' \"$VISION_NAV_SITL_SMOKE_DIR\"\nexit 0\n",
        )
        .expect("write helper");
        let transfer_root = repo.join("from-pi");

        let result = run_local_px4_sitl_prereq_setup_inner(
            repo.to_str().expect("repo path"),
            Some(transfer_root.to_str().expect("transfer path")),
        )
        .expect("run helper");

        assert_eq!(result.exit_code, 0);
        assert!(result.stdout.contains("--clone-px4"));
        assert!(result.stdout.contains("--px4-dir"));
        assert!(result.stdout.contains("px4-sitl-evidence"));
        assert!(transfer_root.join("px4-sitl-evidence").is_dir());
        let _ = fs::remove_dir_all(&repo);
    }

    #[test]
    fn extracts_support_bundle_summary_from_manifest() {
        let manifest = serde_json::json!({
            "bundle": {
                "bundle_id": "mission-bundle",
                "health": {
                    "status": "passed",
                    "checksums": {
                        "status": "passed",
                        "covered_file_count": 12
                    },
                    "source_provenance": {
                        "map_source": "uploaded_geotiff",
                        "original_file": "field-map.tif",
                        "georef_source": "geotiff_embedded",
                        "georef_crs": "EPSG:4326",
                        "georef_confidence": 0.95
                    },
                    "elevation": {
                        "status": "passed",
                        "asset_count": 2,
                        "vertical_sanity_ready": true
                    }
                },
                "mission_plan": {
                    "status": "loaded",
                    "gnss_denied": {
                        "status": "ready"
                    }
                }
            },
            "replay_gates": {
                "status": "passed",
                "case_count": 3
            },
            "px4_sitl_evidence": {
                "status": "passed",
                "listener": {
                    "sample_count": 2
                }
            },
            "px4_sitl_prereqs": {
                "status": "failed"
            },
            "px4_params": {
                "status": "degraded",
                "parameters": {
                    "EKF2_EV_CTRL": 1
                }
            },
            "ardupilot_params": {
                "status": "passed",
                "parameters": {
                    "source_set": 1,
                    "EK3_SRC1_POSXY": 6
                }
            },
            "feature_method_benchmarks": {
                "status": "passed",
                "report_count": 1,
                "reports": [
                    {
                        "recommended_method": "orb"
                    }
                ]
            },
            "field_evidence": {
                "status": "passed",
                "report_count": 1,
                "field_case_count": 8,
                "capture_metadata_issue_count": 0
            },
            "field_collection_plans": {
                "status": "degraded",
                "report_count": 1,
                "registered_count": 3,
                "required_count": 8,
                "pending_capture_command_count": 5,
                "pending_metadata_update_command_count": 4,
                "pending_registration_command_count": 5,
                "capture_output_dir_count": 8,
                "runtime_status_path_count": 8,
                "condition_source_log_count": 8
            },
            "field_capture_preflights": {
                "status": "failed",
                "report_count": 1,
                "ready_for_capture_count": 0,
                "ready_for_registration_count": 0,
                "failed_check_count": 1,
                "degraded_check_count": 1,
                "next_action_count": 2,
                "blocked_action_count": 1
            },
            "threshold_tuning": {
                "status": "passed",
                "report_count": 1,
                "field_case_count": 8,
                "capture_metadata_issue_count": 0
            },
            "rosbag_export_validations": {
                "status": "passed",
                "report_count": 1,
                "message_count": 4,
                "topic_count": 3
            },
            "rosbag2_cli_reviews": {
                "status": "passed",
                "report_count": 1
            },
            "autonomy_evidence_workflow": {
                "status": "degraded",
                "validation_summary": {
                    "schema_version": "vision_nav_autonomy_evidence_workflow_validation_v1",
                    "status": "degraded",
                    "workflow_status": "failed",
                    "step_count": 12,
                    "issue_count": 2,
                    "workflow_provenance": {
                        "repo_commit": "abcdef123456"
                    },
                    "checks": [
                        {
                            "name": "workflow_provenance",
                            "status": "passed",
                            "details": {
                                "repo_commit": "abcdef123456"
                            }
                        }
                    ]
                }
            },
            "bench_readiness": {
                "status": "degraded",
                "summary": {
                    "failed": 0,
                    "degraded": 1,
                    "passed": 4
                },
                "checks": [
                    {
                        "name": "gnss_denied_plan",
                        "status": "passed"
                    }
                ]
            }
        });
        let summary = support_summary_from_manifest(&manifest).expect("support summary");
        assert_eq!(summary.bundle_id.as_deref(), Some("mission-bundle"));
        assert_eq!(summary.bundle_health_status.as_deref(), Some("passed"));
        assert_eq!(summary.checksum_status.as_deref(), Some("passed"));
        assert_eq!(summary.covered_file_count, Some(12));
        assert_eq!(summary.elevation_status.as_deref(), Some("passed"));
        assert_eq!(summary.elevation_asset_count, Some(2));
        assert_eq!(summary.vertical_sanity_ready, Some(true));
        assert_eq!(summary.map_source.as_deref(), Some("uploaded_geotiff"));
        assert_eq!(summary.source_name.as_deref(), Some("field-map.tif"));
        assert_eq!(summary.replay_gate_status.as_deref(), Some("passed"));
        assert_eq!(summary.replay_case_count, Some(3));
        assert_eq!(summary.gnss_denied_plan_status.as_deref(), Some("passed"));
        assert_eq!(summary.px4_sitl_evidence_status.as_deref(), Some("passed"));
        assert_eq!(summary.px4_sitl_sample_count, Some(2));
        assert_eq!(summary.px4_sitl_prereq_status.as_deref(), Some("failed"));
        assert_eq!(summary.px4_params_status.as_deref(), Some("degraded"));
        assert_eq!(summary.px4_ev_ctrl, Some(1));
        assert_eq!(summary.ardupilot_params_status.as_deref(), Some("passed"));
        assert_eq!(summary.ardupilot_source_set, Some(1));
        assert_eq!(summary.ardupilot_posxy_source, Some(6));
        assert_eq!(
            summary.feature_method_benchmark_status.as_deref(),
            Some("passed")
        );
        assert_eq!(
            summary.feature_method_benchmark_recommended.as_deref(),
            Some("orb")
        );
        assert_eq!(summary.feature_method_benchmark_report_count, Some(1));
        assert_eq!(summary.field_evidence_status.as_deref(), Some("passed"));
        assert_eq!(summary.field_evidence_field_case_count, Some(8));
        assert_eq!(summary.field_evidence_capture_metadata_issue_count, Some(0));
        assert_eq!(summary.field_evidence_report_count, Some(1));
        assert_eq!(
            summary.field_collection_plan_status.as_deref(),
            Some("degraded")
        );
        assert_eq!(summary.field_collection_plan_registered_count, Some(3));
        assert_eq!(summary.field_collection_plan_required_count, Some(8));
        assert_eq!(summary.field_collection_plan_report_count, Some(1));
        assert_eq!(
            summary.field_collection_plan_pending_capture_command_count,
            Some(5)
        );
        assert_eq!(
            summary.field_collection_plan_pending_metadata_update_command_count,
            Some(4)
        );
        assert_eq!(
            summary.field_collection_plan_runtime_status_path_count,
            Some(8)
        );
        assert_eq!(
            summary.field_collection_plan_condition_source_log_count,
            Some(8)
        );
        assert_eq!(
            summary.field_capture_preflight_status.as_deref(),
            Some("failed")
        );
        assert_eq!(summary.field_capture_preflight_report_count, Some(1));
        assert_eq!(
            summary.field_capture_preflight_ready_for_capture_count,
            Some(0)
        );
        assert_eq!(
            summary.field_capture_preflight_ready_for_registration_count,
            Some(0)
        );
        assert_eq!(summary.field_capture_preflight_failed_check_count, Some(1));
        assert_eq!(
            summary.field_capture_preflight_degraded_check_count,
            Some(1)
        );
        assert_eq!(summary.field_capture_preflight_next_action_count, Some(2));
        assert_eq!(
            summary.field_capture_preflight_blocked_action_count,
            Some(1)
        );
        assert_eq!(summary.threshold_tuning_status.as_deref(), Some("passed"));
        assert_eq!(summary.threshold_tuning_field_case_count, Some(8));
        assert_eq!(
            summary.threshold_tuning_capture_metadata_issue_count,
            Some(0)
        );
        assert_eq!(summary.threshold_tuning_report_count, Some(1));
        assert_eq!(
            summary.rosbag_export_validation_status.as_deref(),
            Some("passed")
        );
        assert_eq!(summary.rosbag_export_validation_report_count, Some(1));
        assert_eq!(summary.rosbag_export_validation_message_count, Some(4));
        assert_eq!(summary.rosbag_export_validation_topic_count, Some(3));
        assert_eq!(summary.rosbag2_cli_review_status.as_deref(), Some("passed"));
        assert_eq!(summary.rosbag2_cli_review_report_count, Some(1));
        assert_eq!(
            summary.evidence_workflow_status.as_deref(),
            Some("degraded")
        );
        assert_eq!(
            summary.evidence_workflow_validation_status.as_deref(),
            Some("degraded")
        );
        assert_eq!(
            summary.evidence_workflow_runtime_status.as_deref(),
            Some("failed")
        );
        assert_eq!(
            summary.evidence_workflow_provenance_status.as_deref(),
            Some("passed")
        );
        assert_eq!(summary.evidence_workflow_step_count, Some(12));
        assert_eq!(summary.evidence_workflow_issue_count, Some(2));
        assert_eq!(
            summary.evidence_workflow_repo_commit.as_deref(),
            Some("abcdef123456")
        );
        assert_eq!(summary.bench_readiness_status.as_deref(), Some("degraded"));
        assert_eq!(summary.bench_readiness_failed_count, Some(0));
        assert_eq!(summary.bench_readiness_degraded_count, Some(1));
    }

    #[test]
    fn delete_support_bundle_rejects_non_zip_files() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let path = std::env::temp_dir().join(format!("drone-support-delete-{stamp}.txt"));
        std::fs::write(&path, "not a zip").expect("write temp file");
        let result = delete_support_bundle(path.to_string_lossy().into_owned());
        let _ = std::fs::remove_file(&path);
        assert!(result.is_err());
    }

    #[test]
    fn lists_autonomy_readiness_reports_from_json_dir() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("drone-readiness-reports-{stamp}"));
        std::fs::create_dir_all(&dir).expect("create readiness dir");
        let field_collection_plan_path = dir.join("field_collection_plan.json");
        std::fs::write(
            &field_collection_plan_path,
            serde_json::json!({
                "schema_version": "vision_nav_field_collection_plan_v1",
                "status": "degraded",
                "site_name": "test-range",
                "manifest_path": "field_manifest.json",
                "bundle": "mission-bundle",
                    "summary": {
                        "required_count": 3,
                        "registered_count": 1,
                        "registered_missing_log_count": 1,
                        "placeholder_count": 1,
                        "missing_count": 0
                    },
                    "next_condition": {
                        "condition": "blur",
                        "label": "Blur",
                        "expected": "degraded",
                        "status": "placeholder",
                        "case_name": "blur",
                        "manifest_log_exists": false,
                        "source_log": "field-captures/blur/terrain_matches.jsonl",
                        "capture_command": "./scripts/pi/run_terrain_nav_loop.sh --condition blur",
                        "register_command": "./scripts/pi/register_field_replay_case.sh --condition blur"
                    },
                    "conditions": [
                        {"condition": "good_texture", "label": "Good texture", "expected": "good_map", "status": "registered", "case_name": "good-texture", "manifest_log_exists": true},
                        {"condition": "blur", "label": "Blur", "expected": "degraded", "status": "placeholder", "case_name": "blur", "manifest_log_exists": false},
                    {"condition": "wrong_map", "label": "Wrong map", "expected": "wrong_map", "status": "registered_missing_log", "case_name": "wrong-map", "manifest_log_exists": false}
                ]
            })
            .to_string(),
        )
        .expect("write field collection plan");
        let report_path = dir.join("autonomy_readiness_report.json");
        std::fs::write(
            &report_path,
            serde_json::json!({
                "status": "failed",
                "metadata": {
                    "schema_version": "vision_nav_autonomy_readiness_audit_metadata_v1",
                    "generated_at_utc": "2026-06-22T07:00:00+00:00",
                    "repo": {
                        "detected": true,
                        "root": "/repo/drone",
                        "branch": "main",
                        "commit": "abcdef1234567890",
                        "dirty": false,
                        "remote": "https://github.com/izrael-fisi/Drone.git"
                    }
                },
                "inputs": {
                    "field_collection_plan": "/home/user/DroneTransfer/outgoing/replay-cases/field_collection_plan.json",
                    "evidence_workflow_report": "/home/user/DroneTransfer/outgoing/replay-cases/autonomy_evidence_workflow.json",
                    "evidence_workflow_validation_report": "/home/user/DroneTransfer/outgoing/replay-cases/autonomy_evidence_workflow.validation.json",
                    "evidence_workflow_log_archive": "/home/user/DroneTransfer/outgoing/replay-cases/autonomy_evidence_workflow.logs.tar.gz"
                },
                "summary": {"failed": 4, "degraded": 1, "passed": 4},
                "checks": [
                    {"name": "research_doc", "status": "passed", "message": "Research doc ready."},
                    {"name": "support_bundle_bench_readiness", "status": "failed", "message": "Support bundle missing."},
                    {"name": "px4_receiver_proof", "status": "failed", "message": "Receiver proof missing."},
                    {"name": "field_collection_plan", "status": "failed", "message": "Field collection plan is incomplete."},
                    {"name": "field_evidence_proof", "status": "degraded", "message": "Needs more logs."},
                    {"name": "feature_method_benchmark", "status": "passed", "message": "Benchmark present."},
                    {"name": "threshold_tuning", "status": "passed", "message": "Threshold report present."},
                    {"name": "rosbag_export_validation", "status": "passed", "message": "ROS bag validation present."},
                    {"name": "rosbag2_cli_review", "status": "failed", "message": "Native rosbag2 CLI review missing."}
                ],
                "next_actions": [
                    {
                        "check": "support_bundle_bench_readiness",
                        "status": "failed",
                        "title": "Create a support bundle with bench evidence.",
                        "desktop_action": "Module Setup > Bench Report",
                        "command": "./scripts/pi/create_support_bundle.sh",
                        "notes": "Run after evidence is available.",
                        "bench_subchecks": [
                            {
                                "name": "runtime_status",
                                "status": "degraded",
                                "message": "Runtime status snapshot was not provided."
                            }
                        ]
                    },
                    {
                        "check": "support_bundle_bench_readiness.runtime_status",
                        "status": "degraded",
                        "title": "Fetch a fresh runtime status snapshot.",
                        "desktop_action": "Module Setup > Runtime Status, then Bench Report",
                        "command": "./scripts/pi/read_runtime_status.sh",
                        "notes": "The support bundle should include runtime_status.json.",
                        "bench_subcheck": "runtime_status",
                        "bench_message": "Runtime status snapshot was not provided."
                    },
                    {
                        "check": "px4_receiver_proof",
                        "status": "failed",
                        "title": "Capture PX4 external-vision receiver proof.",
                        "desktop_action": "PX4 SITL capture harness",
                        "command": "./scripts/dev/run_px4_sitl_external_vision_capture.sh",
                        "notes": "Capture vehicle_visual_odometry.",
                        "missing_conditions": ["good_texture", "wrong_map"]
                    }
                ],
                "command_bundle": {
                    "guided_workflow_commands": [
                        "./scripts/pi/run_autonomy_evidence_workflow.sh"
                    ],
                    "prerequisite_fix_commands": [
                        "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot"
                    ],
                    "next_action_commands": [
                        "./scripts/dev/run_px4_sitl_external_vision_capture.sh",
                        "./scripts/pi/create_support_bundle.sh",
                        "./scripts/pi/run_threshold_tuning_report.sh"
                    ],
                    "immediate_next_action_commands": [
                        "./scripts/dev/run_px4_sitl_external_vision_capture.sh",
                        "./scripts/pi/create_support_bundle.sh"
                    ],
                    "blocked_follow_up_commands": [
                        "./scripts/pi/run_threshold_tuning_report.sh"
                    ],
                    "field_collection_capture_commands": [
                        "./scripts/pi/run_terrain_nav_loop.sh --condition blur"
                    ],
                    "field_collection_metadata_update_commands": [
                        "./scripts/pi/update_field_capture_metadata.sh --condition blur"
                    ],
                    "field_collection_registration_commands": [
                        "./scripts/pi/register_field_replay_case.sh --condition blur"
                    ],
                    "command_count": 8,
                    "command_items": [
                        {
                            "group": "immediate_next_action",
                            "command": "./scripts/dev/run_px4_sitl_external_vision_capture.sh",
                            "desktop_action": "PX4 SITL capture harness"
                        }
                    ]
                },
                "plan_snapshot": {
                    "schema_version": "vision_nav_autonomy_plan_snapshot_v1",
                    "research_doc": {
                        "path": "docs/autonomy-ground-control-research.md",
                        "exists": true,
                        "required_marker_count": 3,
                        "missing_markers": [],
                        "highest_value_reference_count": 18,
                        "near_term_item_count": 7
                    },
                    "implementation_plan": {
                        "path": "docs/autonomy-ground-control-implementation-plan.md",
                        "exists": true,
                        "required_marker_count": 6,
                        "missing_markers": [],
                        "track_count": 6,
                        "done_count": 84,
                        "task_count": 13,
                        "acceptance_check_count": 19
                    }
                },
                    "evidence_manifest": {
                        "schema_version": "vision_nav_autonomy_evidence_manifest_v1",
                        "ready_for_goal_completion": false,
                        "proof_items": [
                            {
                                "name": "research_doc",
                                "status": "passed",
                                "message": "Research doc ready."
                            },
                            {
                                "name": "support_bundle_bench_readiness",
                                "status": "failed",
                                "message": "Support bundle missing.",
                                "bench_subchecks": [
                                    {
                                        "name": "runtime_status",
                                        "status": "degraded",
                                        "message": "Runtime status snapshot was not provided."
                                    }
                                ],
                                "expected_bench_inputs": [
                                    "runtime_status.json"
                                ],
                                "support_bundle_command": "./scripts/pi/create_support_bundle.sh",
                                "bench_evidence_actions": [
                                    {
                                        "label": "Capture runtime status",
                                        "desktop_action": "Module Setup > Runtime Status",
                                        "command": "./scripts/pi/read_runtime_status.sh",
                                        "notes": "Collect the status snapshot before the support bundle."
                                    },
                                    {
                                        "label": "Create bench report",
                                        "desktop_action": "Module Setup > Bench Report",
                                        "command": "./scripts/pi/create_support_bundle.sh",
                                        "blocked_by": "runtime_status",
                                        "notes": "Package the bench evidence after required inputs exist."
                                    }
                                ]
                            },
                            {
                                "name": "px4_receiver_proof",
                                "status": "failed",
                                "message": "Receiver proof missing.",
                                "missing_conditions": ["good_texture", "wrong_map"]
                            }
                        ],
                        "completion_blockers": [
                            {
                                "name": "support_bundle_bench_readiness",
                            "status": "failed",
                            "message": "Support bundle missing.",
                            "bench_subchecks": [
                                {
                                    "name": "runtime_status",
                                    "status": "degraded",
                                    "message": "Runtime status snapshot was not provided."
                                }
                            ],
                            "expected_bench_inputs": [
                                "runtime_status.json"
                            ],
                            "support_bundle_command": "./scripts/pi/create_support_bundle.sh",
                            "bench_evidence_actions": [
                                {
                                    "label": "Capture runtime status",
                                    "desktop_action": "Module Setup > Runtime Status",
                                    "command": "./scripts/pi/read_runtime_status.sh",
                                    "notes": "Collect the status snapshot before the support bundle."
                                },
                                {
                                    "label": "Create bench report",
                                    "desktop_action": "Module Setup > Bench Report",
                                    "command": "./scripts/pi/create_support_bundle.sh",
                                    "blocked_by": "runtime_status",
                                    "notes": "Package the bench evidence after required inputs exist."
                                }
                            ]
                        },
                        {
                            "name": "px4_receiver_proof",
                            "status": "failed",
                            "message": "Receiver proof missing."
                        }
                    ],
                    "external_blockers": [
                        {
                            "name": "support_bundle_bench_readiness",
                            "status": "failed",
                            "message": "Support bundle missing.",
                            "bench_subchecks": [
                                {
                                    "name": "runtime_status",
                                    "status": "degraded",
                                    "message": "Runtime status snapshot was not provided."
                                }
                            ],
                            "expected_bench_inputs": [
                                "runtime_status.json"
                            ],
                            "support_bundle_command": "./scripts/pi/create_support_bundle.sh",
                            "bench_evidence_actions": [
                                {
                                    "label": "Capture runtime status",
                                    "desktop_action": "Module Setup > Runtime Status",
                                    "command": "./scripts/pi/read_runtime_status.sh",
                                    "notes": "Collect the status snapshot before the support bundle."
                                },
                                {
                                    "label": "Create bench report",
                                    "desktop_action": "Module Setup > Bench Report",
                                    "command": "./scripts/pi/create_support_bundle.sh",
                                    "blocked_by": "runtime_status",
                                    "notes": "Package the bench evidence after required inputs exist."
                                }
                            ]
                        },
                        {
                            "name": "px4_receiver_proof",
                            "status": "failed",
                            "message": "Receiver proof missing.",
                            "missing_conditions": ["good_texture", "wrong_map"]
                        }
                    ]
                },
                "proof_runbook": {
                    "schema_version": "vision_nav_autonomy_proof_runbook_v1",
                    "ready_for_goal_completion": false,
                    "summary": {
                        "phase_count": 2,
                        "passed": 1,
                        "action_required": 1,
                        "blocked": 0
                    },
                    "phases": [
                        {
                            "id": "plan_source",
                            "title": "Confirm source plan coverage",
                            "status": "passed",
                            "depends_on": [],
                            "dependency_status": {},
                            "checks": [
                                {"name": "research_doc", "status": "passed", "message": "Research doc ready."}
                            ],
                            "actions": [],
                            "commands": [],
                            "notes": "Keep source docs present."
                        },
                        {
                            "id": "bench_foundation",
                            "title": "Create bench evidence package",
                            "status": "action_required",
                            "depends_on": ["plan_source"],
                            "dependency_status": {"plan_source": "passed"},
                            "checks": [
                                {"name": "support_bundle_bench_readiness", "status": "failed", "message": "Support bundle missing."},
                                {"name": "px4_receiver_proof", "status": "failed", "message": "Receiver proof missing."}
                            ],
                            "actions": [
                                {
                                    "check": "support_bundle_bench_readiness",
                                    "status": "failed",
                                    "desktop_action": "Module Setup > Bench Report",
                                    "command": "./scripts/pi/create_support_bundle.sh",
                                    "missing_conditions": ["good_texture"]
                                }
                            ],
                            "commands": ["./scripts/pi/create_support_bundle.sh"],
                            "notes": "Capture bench proof."
                        }
                    ]
                }
            })
            .to_string(),
        )
        .expect("write readiness report");
        std::fs::write(dir.join("autonomy_evidence_workflow.json"), "{}")
            .expect("write workflow report artifact");
        std::fs::write(dir.join("autonomy_evidence_workflow.validation.json"), "{}")
            .expect("write workflow validation artifact");
        std::fs::write(dir.join("autonomy_evidence_workflow.logs.tar.gz"), "logs")
            .expect("write workflow log archive artifact");
        std::fs::write(
            dir.join("threshold_tuning_report.json"),
            serde_json::json!({"status": "passed", "method": "field-replay-gate-threshold-audit"})
                .to_string(),
        )
        .expect("write unrelated report");
        std::fs::write(
            dir.join("autonomy_readiness_report.md"),
            "# Autonomy Readiness Handoff\n\n- Status: failed\n",
        )
        .expect("write readiness handoff");
        let package_path = dir.join("autonomy_readiness_report.evidence.zip");
        {
            let file = File::create(&package_path).expect("create readiness evidence package");
            let mut zip = zip::ZipWriter::new(file);
            let options = SimpleFileOptions::default();
            zip.start_file("manifest.json", options)
                .expect("start package manifest");
            zip.write_all(
                serde_json::json!({
                    "schema_version": "vision_nav_autonomy_evidence_package_v1",
                    "readiness_status": "failed",
                    "ready_for_goal_completion": false,
                    "readiness_report_metadata": {
                        "schema_version": "vision_nav_autonomy_readiness_audit_metadata_v1",
                        "generated_at_utc": "2026-06-22T07:05:00+00:00",
                        "repo": {
                            "detected": true,
                            "root": "/repo/drone",
                            "branch": "main",
                            "commit": "fedcba9876543210",
                            "dirty": true,
                            "remote": "https://github.com/izrael-fisi/Drone.git"
                        }
                    },
                    "plan_snapshot": {
                        "schema_version": "vision_nav_autonomy_plan_snapshot_v1",
                        "research_doc": {
                            "path": "docs/autonomy-ground-control-research.md",
                            "exists": true,
                            "required_marker_count": 3,
                            "missing_markers": [],
                            "highest_value_reference_count": 18,
                            "near_term_item_count": 7
                        },
                        "implementation_plan": {
                            "path": "docs/autonomy-ground-control-implementation-plan.md",
                            "exists": true,
                            "required_marker_count": 6,
                            "missing_markers": [],
                            "track_count": 6,
                            "done_count": 84,
                            "task_count": 13,
                            "acceptance_check_count": 19
                        }
                    },
                    "proof_summary": {
                        "proof_item_count": 3,
                        "proof_item_passed_count": 1,
                        "completion_blocker_count": 2,
                        "external_blocker_count": 2,
                        "proof_items": [
                            {"name": "research_doc", "status": "passed", "message": "Research doc ready."},
                            {
                                "name": "support_bundle_bench_readiness",
                                "status": "failed",
                                "message": "Support bundle missing.",
                                "bench_subchecks": [
                                    {
                                        "name": "runtime_status",
                                        "status": "degraded",
                                        "message": "Runtime status snapshot was not provided."
                                    }
                                ],
                                "expected_bench_inputs": [
                                    "runtime_status.json"
                                ],
                                "support_bundle_command": "./scripts/pi/create_support_bundle.sh",
                                "bench_evidence_actions": [
                                    {
                                        "label": "Capture runtime status",
                                        "desktop_action": "Module Setup > Runtime Status",
                                        "command": "./scripts/pi/read_runtime_status.sh",
                                        "notes": "Collect the status snapshot before the support bundle."
                                    },
                                    {
                                        "label": "Create bench report",
                                        "desktop_action": "Module Setup > Bench Report",
                                        "command": "./scripts/pi/create_support_bundle.sh",
                                        "blocked_by": "runtime_status",
                                        "notes": "Package the bench evidence after required inputs exist."
                                    }
                                ]
                            },
                            {
                                "name": "px4_receiver_proof",
                                "status": "failed",
                                "message": "Receiver proof missing.",
                                "missing_conditions": ["good_texture", "wrong_map"]
                            }
                        ]
                    },
                    "proof_runbook_summary": {
                        "schema_version": "vision_nav_autonomy_proof_runbook_v1",
                        "ready_for_goal_completion": false,
                        "phases_truncated": false,
                        "summary": {
                            "phase_count": 2,
                            "passed": 1,
                            "action_required": 1,
                            "blocked": 0
                        },
                        "phases": [
                            {
                                "id": "plan_source",
                                "title": "Confirm source plan coverage",
                                "status": "passed",
                                "depends_on": [],
                                "checks": [
                                    {"name": "research_doc", "status": "passed", "message": "Research doc ready."}
                                ],
                                "commands": []
                            },
                            {
                                "id": "bench_foundation",
                                "title": "Create bench evidence package",
                                "status": "action_required",
                                "depends_on": ["plan_source"],
                                "checks": [
                                    {"name": "support_bundle_bench_readiness", "status": "failed", "message": "Support bundle missing."}
                                ],
                                "actions": [
                                    {
                                        "check": "support_bundle_bench_readiness",
                                        "status": "failed",
                                        "desktop_action": "Module Setup > Bench Report",
                                        "command": "./scripts/pi/create_support_bundle.sh"
                                    }
                                ],
                                "commands": ["./scripts/pi/create_support_bundle.sh"]
                            }
                        ]
                    },
                    "command_bundle": {
                        "guided_workflow_commands": [
                            "./scripts/pi/run_autonomy_evidence_workflow.sh"
                        ],
                        "prerequisite_fix_commands": [
                            "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot"
                        ],
                        "next_action_commands": [
                            "./scripts/dev/run_px4_sitl_external_vision_capture.sh",
                            "./scripts/pi/create_support_bundle.sh",
                            "./scripts/pi/run_threshold_tuning_report.sh"
                        ],
                        "immediate_next_action_commands": [
                            "./scripts/dev/run_px4_sitl_external_vision_capture.sh",
                            "./scripts/pi/create_support_bundle.sh"
                        ],
                        "blocked_follow_up_commands": [
                            "./scripts/pi/run_threshold_tuning_report.sh"
                        ],
                        "field_collection_capture_commands": [
                            "./scripts/pi/run_terrain_nav_loop.sh --condition blur"
                        ],
                        "field_collection_metadata_update_commands": [
                            "./scripts/pi/update_field_capture_metadata.sh --condition blur"
                        ],
                        "field_collection_registration_commands": [
                            "./scripts/pi/register_field_replay_case.sh --condition blur"
                        ],
                        "command_count": 8,
                        "command_items": [
                            {
                                "group": "blocked_follow_up",
                                "command": "./scripts/pi/run_threshold_tuning_report.sh",
                                "desktop_action": "Module Setup > Threshold Tuning"
                            }
                        ]
                    },
                    "workflow_validation_summary": {
                        "schema_version": "vision_nav_autonomy_evidence_workflow_validation_v1",
                        "status": "degraded",
                        "workflow_status": "failed",
                        "step_count": 10,
                        "marker_count": 8,
                        "issue_count": 1,
                        "issues": ["Some required workflow steps did not pass."],
                        "next_required_step": {
                            "name": "register_field_replay_case",
                            "status": "skipped",
                            "exit_code": 0,
                            "notes": "Waiting for field replay capture.",
                            "command": "./scripts/pi/register_field_replay_case.sh",
                            "desktop_action": "Module Setup > Field Evidence Case > Register",
                            "metadata_update_command": "./scripts/pi/update_field_capture_metadata.sh --condition blur"
                        },
                        "checks": [
                            {"name": "schema", "status": "passed", "message": "Workflow JSON is valid."},
                            {
                                "name": "required_step_results",
                                "status": "degraded",
                                "message": "Some required steps were skipped or failed.",
                                "non_passed_count": 2,
                                "non_passed_steps": [
                                    {
                                        "name": "register_field_replay_case",
                                        "status": "skipped",
                                        "exit_code": 0,
                                        "notes": "Waiting for field replay capture.",
                                        "current_preflight_allows_capture": true,
                                        "current_preflight_report": "/home/user/DroneTransfer/outgoing/replay-cases/field_capture_preflight.json",
                                        "current_preflight_status": "degraded",
                                        "current_ready_for_registration": false,
                                        "guidance": "A newer field-capture preflight report is capture-ready."
                                    },
                                    {
                                        "name": "run_autonomy_readiness_audit",
                                        "status": "failed",
                                        "exit_code": 1
                                    }
                                ]
                            },
                            {
                                "name": "final_proof_markers",
                                "status": "degraded",
                                "marker_count": 8,
                                "missing_markers": ["__VISION_NAV_FIELD_EVIDENCE_REPORT__"],
                                "present_markers": ["__VISION_NAV_SUPPORT_ZIP__", "__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__"]
                            }
                        ]
                    },
                    "included": [
                        {"label": "autonomy_report"},
                        {"label": "autonomy_handoff"},
                        {"label": "input:evidence_workflow_log_archive", "path": "/home/user/DroneTransfer/outgoing/replay-cases/autonomy_evidence_workflow.logs.tar.gz"}
                    ],
                    "missing": [
                        {
                            "label": "proof:field_evidence_proof",
                            "reason": "proof_gate_not_passed",
                            "status": "failed",
                            "message": "Real field evidence is required for autonomy readiness.",
                            "source": "support_bundle",
                            "missing_conditions": ["good_texture", "wrong_map"]
                        }
                    ],
                    "skipped": [
                        {"label": "large_support_bundle", "reason": "too_large"}
                    ]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write package manifest");
            zip.finish().expect("finish readiness evidence package");
        }
        let reports = list_autonomy_readiness_reports(dir.to_string_lossy().into_owned())
            .expect("list reports");
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].name, "autonomy_readiness_report.json");
        assert_eq!(reports[0].summary.status.as_deref(), Some("failed"));
        assert_eq!(
            reports[0]
                .handoff_path
                .as_deref()
                .and_then(|path| Path::new(path).file_name())
                .and_then(|name| name.to_str()),
            Some("autonomy_readiness_report.md")
        );
        assert!(reports[0].handoff_size_bytes.is_some());
        assert_eq!(
            reports[0]
                .evidence_package_path
                .as_deref()
                .and_then(|path| Path::new(path).file_name())
                .and_then(|name| name.to_str()),
            Some("autonomy_readiness_report.evidence.zip")
        );
        assert!(reports[0].evidence_package_size_bytes.is_some());
        assert_eq!(
            reports[0].workflow_report_path.as_deref(),
            Some("/home/user/DroneTransfer/outgoing/replay-cases/autonomy_evidence_workflow.json")
        );
        assert!(reports[0]
            .workflow_report_local_path
            .as_deref()
            .is_some_and(|path| path.ends_with("autonomy_evidence_workflow.json")));
        assert!(reports[0]
            .workflow_validation_local_path
            .as_deref()
            .is_some_and(|path| path.ends_with("autonomy_evidence_workflow.validation.json")));
        assert!(reports[0]
            .workflow_log_archive_local_path
            .as_deref()
            .is_some_and(|path| path.ends_with("autonomy_evidence_workflow.logs.tar.gz")));
        let audit_metadata = reports[0].metadata.as_ref().expect("audit metadata");
        assert_eq!(
            audit_metadata.schema_version.as_deref(),
            Some("vision_nav_autonomy_readiness_audit_metadata_v1")
        );
        assert_eq!(
            audit_metadata.generated_at_utc.as_deref(),
            Some("2026-06-22T07:00:00+00:00")
        );
        assert_eq!(
            audit_metadata
                .repo
                .as_ref()
                .and_then(|repo| repo.commit.as_deref()),
            Some("abcdef1234567890")
        );
        assert_eq!(
            audit_metadata.repo.as_ref().and_then(|repo| repo.dirty),
            Some(false)
        );
        let plan_snapshot = reports[0].plan_snapshot.as_ref().expect("plan snapshot");
        assert_eq!(
            plan_snapshot.schema_version.as_deref(),
            Some("vision_nav_autonomy_plan_snapshot_v1")
        );
        assert_eq!(
            plan_snapshot
                .research_doc
                .as_ref()
                .and_then(|snapshot| snapshot.highest_value_reference_count),
            Some(18)
        );
        assert_eq!(
            plan_snapshot
                .implementation_plan
                .as_ref()
                .and_then(|snapshot| snapshot.track_count),
            Some(6)
        );
        let proof_runbook = reports[0].proof_runbook.as_ref().expect("proof runbook");
        assert_eq!(
            proof_runbook.schema_version.as_deref(),
            Some("vision_nav_autonomy_proof_runbook_v1")
        );
        assert_eq!(proof_runbook.summary.phase_count, Some(2));
        assert_eq!(proof_runbook.summary.action_required, Some(1));
        assert_eq!(proof_runbook.phases.len(), 2);
        assert_eq!(
            proof_runbook.phases[1].dependency_status.get("plan_source"),
            Some(&"passed".to_string())
        );
        assert_eq!(
            proof_runbook.phases[1].commands[0],
            "./scripts/pi/create_support_bundle.sh"
        );
        assert_eq!(
            proof_runbook.phases[1].actions[0].missing_conditions,
            vec!["good_texture".to_string()]
        );
        let package_summary = reports[0]
            .evidence_package_summary
            .as_ref()
            .expect("evidence package summary");
        assert_eq!(package_summary.readiness_status.as_deref(), Some("failed"));
        assert_eq!(package_summary.ready_for_goal_completion, Some(false));
        assert_eq!(
            package_summary
                .readiness_report_metadata
                .as_ref()
                .and_then(|metadata| metadata.repo.as_ref())
                .and_then(|repo| repo.commit.as_deref()),
            Some("fedcba9876543210")
        );
        assert_eq!(
            package_summary
                .readiness_report_metadata
                .as_ref()
                .and_then(|metadata| metadata.repo.as_ref())
                .and_then(|repo| repo.dirty),
            Some(true)
        );
        assert_eq!(
            package_summary
                .plan_snapshot
                .as_ref()
                .and_then(|snapshot| snapshot.implementation_plan.as_ref())
                .and_then(|snapshot| snapshot.done_count),
            Some(84)
        );
        assert_eq!(package_summary.proof_item_count, Some(3));
        assert_eq!(package_summary.proof_item_passed_count, Some(1));
        assert_eq!(package_summary.completion_blocker_count, Some(2));
        assert_eq!(package_summary.external_blocker_count, Some(2));
        assert_eq!(package_summary.proof_items.len(), 3);
        assert_eq!(
            package_summary.proof_items[2].missing_conditions,
            vec!["good_texture".to_string(), "wrong_map".to_string()]
        );
        assert_eq!(
            package_summary.proof_items[1].expected_bench_inputs,
            vec!["runtime_status.json".to_string()]
        );
        assert_eq!(
            package_summary.proof_items[1]
                .support_bundle_command
                .as_deref(),
            Some("./scripts/pi/create_support_bundle.sh")
        );
        assert_eq!(
            package_summary.proof_items[1].bench_evidence_actions[0]
                .desktop_action
                .as_deref(),
            Some("Module Setup > Runtime Status")
        );
        let package_runbook = package_summary
            .proof_runbook_summary
            .as_ref()
            .expect("package proof runbook summary");
        assert_eq!(package_runbook.phases_truncated, Some(false));
        assert_eq!(package_runbook.summary.passed, Some(1));
        assert_eq!(
            package_runbook.phases[1].actions[0].command.as_deref(),
            Some("./scripts/pi/create_support_bundle.sh")
        );
        let package_command_bundle = package_summary
            .command_bundle
            .as_ref()
            .expect("package command bundle");
        assert_eq!(
            package_command_bundle.guided_workflow_commands[0],
            "./scripts/pi/run_autonomy_evidence_workflow.sh"
        );
        assert_eq!(
            package_command_bundle.prerequisite_fix_commands[0],
            "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot"
        );
        assert_eq!(
            package_command_bundle.immediate_next_action_commands[0],
            "./scripts/dev/run_px4_sitl_external_vision_capture.sh"
        );
        assert_eq!(
            package_command_bundle.blocked_follow_up_commands[0],
            "./scripts/pi/run_threshold_tuning_report.sh"
        );
        assert_eq!(
            package_command_bundle.field_collection_metadata_update_commands[0],
            "./scripts/pi/update_field_capture_metadata.sh --condition blur"
        );
        assert_eq!(package_command_bundle.command_count, Some(8));
        assert_eq!(
            package_command_bundle.command_items[0]
                .desktop_action
                .as_deref(),
            Some("Module Setup > Threshold Tuning")
        );
        let package_validation = package_summary
            .workflow_validation_summary
            .as_ref()
            .expect("package workflow validation summary");
        assert_eq!(package_validation.status.as_deref(), Some("degraded"));
        assert_eq!(
            package_validation.workflow_status.as_deref(),
            Some("failed")
        );
        assert_eq!(package_validation.issue_count, 1);
        assert_eq!(
            package_validation
                .next_required_step
                .as_ref()
                .and_then(|step| step.command.as_deref()),
            Some("./scripts/pi/register_field_replay_case.sh")
        );
        assert_eq!(
            package_validation
                .next_required_step
                .as_ref()
                .and_then(|step| step.metadata_update_command.as_deref()),
            Some("./scripts/pi/update_field_capture_metadata.sh --condition blur")
        );
        assert_eq!(package_validation.checks.len(), 3);
        assert_eq!(
            package_validation.checks[1].name.as_deref(),
            Some("required_step_results")
        );
        assert_eq!(package_validation.checks[1].non_passed_count, Some(2));
        assert_eq!(
            package_validation.checks[1].non_passed_steps[0]
                .name
                .as_deref(),
            Some("register_field_replay_case")
        );
        assert_eq!(
            package_validation.checks[1].non_passed_steps[0]
                .status
                .as_deref(),
            Some("skipped")
        );
        assert_eq!(
            package_validation.checks[1].non_passed_steps[0].current_preflight_allows_capture,
            Some(true)
        );
        assert_eq!(
            package_validation.checks[1].non_passed_steps[0]
                .current_preflight_status
                .as_deref(),
            Some("degraded")
        );
        assert_eq!(
            package_validation.checks[1].non_passed_steps[0].current_ready_for_registration,
            Some(false)
        );
        assert_eq!(
            package_validation.checks[1].non_passed_steps[0]
                .guidance
                .as_deref(),
            Some("A newer field-capture preflight report is capture-ready.")
        );
        assert_eq!(
            package_validation.checks[2].missing_markers,
            vec!["__VISION_NAV_FIELD_EVIDENCE_REPORT__".to_string()]
        );
        assert_eq!(package_summary.included_count, Some(3));
        assert_eq!(package_summary.missing_count, Some(1));
        assert_eq!(package_summary.skipped_count, Some(1));
        assert_eq!(
            package_summary.included_artifacts[2].label.as_deref(),
            Some("input:evidence_workflow_log_archive")
        );
        assert_eq!(
            package_summary.missing_artifacts[0].label.as_deref(),
            Some("proof:field_evidence_proof")
        );
        assert_eq!(
            package_summary.missing_artifacts[0].reason.as_deref(),
            Some("proof_gate_not_passed")
        );
        assert_eq!(
            package_summary.missing_artifacts[0].status.as_deref(),
            Some("failed")
        );
        assert_eq!(
            package_summary.missing_artifacts[0].message.as_deref(),
            Some("Real field evidence is required for autonomy readiness.")
        );
        assert_eq!(
            package_summary.missing_artifacts[0].source.as_deref(),
            Some("support_bundle")
        );
        assert_eq!(
            package_summary.missing_artifacts[0].missing_conditions,
            vec!["good_texture".to_string(), "wrong_map".to_string()]
        );
        assert_eq!(
            package_summary.skipped_artifacts[0].label.as_deref(),
            Some("large_support_bundle")
        );
        assert_eq!(
            package_summary.skipped_artifacts[0].reason.as_deref(),
            Some("too_large")
        );
        assert_eq!(reports[0].summary.failed_count, Some(4));
        assert_eq!(
            reports[0]
                .summary
                .support_bundle_bench_readiness_status
                .as_deref(),
            Some("failed")
        );
        assert_eq!(
            reports[0].summary.field_collection_plan_status.as_deref(),
            Some("failed")
        );
        assert_eq!(
            reports[0].summary.field_evidence_proof_status.as_deref(),
            Some("degraded")
        );
        assert_eq!(
            reports[0]
                .summary
                .rosbag_export_validation_status
                .as_deref(),
            Some("passed")
        );
        assert_eq!(
            reports[0].summary.rosbag2_cli_review_status.as_deref(),
            Some("failed")
        );
        assert_eq!(reports[0].checks.len(), 9);
        assert_eq!(reports[0].next_actions.len(), 3);
        assert_eq!(
            reports[0].next_actions[0].desktop_action.as_deref(),
            Some("Module Setup > Bench Report")
        );
        assert_eq!(reports[0].next_actions[0].bench_subchecks.len(), 1);
        assert_eq!(
            reports[0].next_actions[0].bench_subchecks[0]
                .name
                .as_deref(),
            Some("runtime_status")
        );
        assert_eq!(
            reports[0].next_actions[1].bench_subcheck.as_deref(),
            Some("runtime_status")
        );
        assert_eq!(reports[0].next_actions[2].missing_conditions.len(), 2);
        let command_bundle = reports[0].command_bundle.as_ref().expect("command bundle");
        assert_eq!(
            command_bundle.guided_workflow_commands[0],
            "./scripts/pi/run_autonomy_evidence_workflow.sh"
        );
        assert_eq!(
            command_bundle.prerequisite_fix_commands[0],
            "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot"
        );
        assert_eq!(command_bundle.next_action_commands.len(), 3);
        assert_eq!(
            command_bundle.next_action_commands[0],
            "./scripts/dev/run_px4_sitl_external_vision_capture.sh"
        );
        assert_eq!(
            command_bundle.next_action_commands[2],
            "./scripts/pi/run_threshold_tuning_report.sh"
        );
        assert_eq!(
            command_bundle.immediate_next_action_commands[0],
            "./scripts/dev/run_px4_sitl_external_vision_capture.sh"
        );
        assert_eq!(
            command_bundle.blocked_follow_up_commands[0],
            "./scripts/pi/run_threshold_tuning_report.sh"
        );
        assert_eq!(
            command_bundle.field_collection_capture_commands[0],
            "./scripts/pi/run_terrain_nav_loop.sh --condition blur"
        );
        assert_eq!(
            command_bundle.field_collection_metadata_update_commands[0],
            "./scripts/pi/update_field_capture_metadata.sh --condition blur"
        );
        assert_eq!(
            command_bundle.field_collection_registration_commands[0],
            "./scripts/pi/register_field_replay_case.sh --condition blur"
        );
        assert_eq!(command_bundle.command_count, Some(8));
        assert_eq!(
            command_bundle.command_items[0].desktop_action.as_deref(),
            Some("PX4 SITL capture harness")
        );
        let field_collection_plan = reports[0]
            .field_collection_plan
            .as_ref()
            .expect("field collection plan summary");
        assert_eq!(field_collection_plan.status.as_deref(), Some("degraded"));
        assert_eq!(
            field_collection_plan.site_name.as_deref(),
            Some("test-range")
        );
        assert_eq!(
            Path::new(&field_collection_plan.path)
                .file_name()
                .and_then(|name| name.to_str()),
            Some("field_collection_plan.json")
        );
        assert_eq!(field_collection_plan.summary.registered_count, Some(1));
        assert_eq!(field_collection_plan.summary.required_count, Some(3));
        let next_condition = field_collection_plan
            .next_condition
            .as_ref()
            .expect("next field collection condition");
        assert_eq!(next_condition.condition.as_deref(), Some("blur"));
        assert_eq!(
            next_condition.capture_command.as_deref(),
            Some("./scripts/pi/run_terrain_nav_loop.sh --condition blur && ./scripts/pi/read_runtime_status.sh")
        );
        assert_eq!(field_collection_plan.pending_conditions.len(), 2);
        assert_eq!(
            field_collection_plan.pending_conditions[0]
                .condition
                .as_deref(),
            Some("blur")
        );
        let evidence = reports[0]
            .evidence_manifest
            .as_ref()
            .expect("evidence manifest");
        assert_eq!(evidence.ready_for_goal_completion, Some(false));
        assert_eq!(evidence.proof_items.len(), 3);
        assert_eq!(
            evidence.proof_items[0].name.as_deref(),
            Some("research_doc")
        );
        assert_eq!(
            evidence.proof_items[1].bench_subchecks[0].name.as_deref(),
            Some("runtime_status")
        );
        assert_eq!(evidence.external_blockers.len(), 2);
        assert_eq!(
            evidence.external_blockers[0].bench_subchecks[0]
                .name
                .as_deref(),
            Some("runtime_status")
        );
        assert_eq!(
            evidence.external_blockers[0].expected_bench_inputs,
            vec!["runtime_status.json".to_string()]
        );
        assert_eq!(
            evidence.external_blockers[0].bench_evidence_actions[1]
                .blocked_by
                .as_deref(),
            Some("runtime_status")
        );
        assert_eq!(evidence.external_blockers[1].missing_conditions.len(), 2);
    }

    #[test]
    fn lists_autonomy_evidence_workflow_reports_from_json_dir() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let base = std::env::temp_dir().join(format!("drone-autonomy-workflow-reports-{stamp}"));
        let dir = base.join("replay-cases");
        std::fs::create_dir_all(&dir).expect("create workflow report dir");
        std::fs::create_dir_all(base.join("support-bundles")).expect("create support dir");
        std::fs::create_dir_all(base.join("feature-method-bench")).expect("create feature dir");
        std::fs::create_dir_all(base.join("px4-sitl-evidence")).expect("create px4 dir");
        std::fs::create_dir_all(base.join("terrain-match")).expect("create terrain-match dir");
        std::fs::write(
            dir.join("autonomy_evidence_workflow.json"),
            serde_json::json!({
                "schema_version": "vision_nav_autonomy_evidence_workflow_v1",
                "status": "failed",
                "generated_at": "2026-06-21T12:00:00Z",
                "workflow_dir": "/home/user/Drone/.vision_nav/autonomy_evidence_workflow",
                "log_archive": "/tmp/autonomy_evidence_workflow.logs.tar.gz",
                "validation_report": "/tmp/autonomy_evidence_workflow.validation.json",
                "summary": {"passed": 3, "failed": 1, "skipped": 2},
                "steps": [
                    {
                        "name": "field_template",
                        "status": "passed",
                        "exit_code": 0,
                        "log_path": "/tmp/field_template.log",
                        "notes": "created template"
                    },
                    {
                        "name": "field_case",
                        "status": "skipped",
                        "notes": "no field case supplied"
                    },
                    {
                        "name": "autonomy_readiness",
                        "status": "failed",
                        "exit_code": 1,
                        "log_path": "/tmp/autonomy_readiness.log"
                    }
                ],
                "markers": {
                    "__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__": "/tmp/autonomy_evidence_workflow.logs.tar.gz",
                    "__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__": "/tmp/autonomy_evidence_workflow.validation.json",
                    "__VISION_NAV_SUPPORT_ZIP__": "/tmp/support.zip",
                    "__VISION_NAV_FIELD_EVIDENCE_REPORT__": "/tmp/field_evidence_report.json",
                    "__VISION_NAV_FEATURE_METHOD_REPORT__": "/tmp/feature_method_benchmark.json",
                    "__VISION_NAV_THRESHOLD_REPORT__": "/tmp/threshold_tuning_report.json",
                    "__VISION_NAV_ROSBAG_EXPORT_VALIDATION__": "/tmp/rosbag-jsonl-validation.json",
                    "__VISION_NAV_AUTONOMY_REPORT__": "/tmp/autonomy_readiness_report.json",
                    "__VISION_NAV_AUTONOMY_HANDOFF__": "/tmp/autonomy_readiness_report.md",
                    "__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__": "/tmp/autonomy_readiness_report.evidence.zip",
                    "__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__": "./scripts/pi/update_field_capture_metadata.sh --condition low_light",
                    "__VISION_NAV_PX4_SITL_REPORT__": "/tmp/receiver_evidence.json",
                    "__VISION_NAV_PX4_SITL_PREREQS__": "/tmp/px4_sitl_capture_prereqs.json"
                }
            })
            .to_string(),
        )
        .expect("write workflow report");
        std::fs::write(
            dir.join("autonomy_readiness_report.json"),
            serde_json::json!({"status": "failed", "checks": [], "summary": {}}).to_string(),
        )
        .expect("write unrelated report");
        std::fs::write(dir.join("autonomy_evidence_workflow.logs.tar.gz"), "logs")
            .expect("write local workflow logs artifact");
        std::fs::write(
            dir.join("autonomy_evidence_workflow.validation.json"),
            serde_json::json!({
                "schema_version": "vision_nav_autonomy_evidence_workflow_validation_v1",
                "status": "degraded",
                "workflow_status": "failed",
                "step_count": 3,
                "marker_count": 13,
                "log_archive": "/tmp/autonomy_evidence_workflow.logs.tar.gz",
                "issues": ["Workflow status is failed; the report is useful, but readiness proof is incomplete."],
                "next_required_step": {
                    "name": "register_field_replay_case",
                    "status": "skipped",
                    "exit_code": 0,
                    "notes": "Set field case variables.",
                    "command": "./scripts/pi/register_field_replay_case.sh",
                    "desktop_action": "Module Setup > Field Evidence Case > Register",
                    "bundle_path": "/home/user/drone-data/map_bundles/mission_bundle",
                    "expected_log": "/home/user/DroneTransfer/outgoing/field-captures/good_texture/terrain_matches.jsonl",
                    "output_dir": "/home/user/DroneTransfer/outgoing/field-captures/good_texture",
                    "runtime_status_path": "/home/user/DroneTransfer/outgoing/field-captures/good_texture/runtime_status.json",
                    "capture_command_after_bundle": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && VISION_NAV_RUNTIME_STATUS_ROOTS=/home/user/DroneTransfer/outgoing/field-captures/good_texture ./scripts/pi/read_runtime_status.sh"
                },
                "checks": [
                    {
                        "name": "required_steps",
                        "status": "passed",
                        "message": "Required workflow steps were recorded.",
                        "details": {"step_count": 3}
                    },
                    {
                        "name": "required_step_results",
                        "status": "degraded",
                        "message": "Some required workflow steps did not pass.",
                        "details": {
                            "non_passed_count": 2,
                            "missing_steps": [],
                            "non_passed_steps": [
                                {
                                    "name": "register_field_replay_case",
                                    "status": "skipped",
                                    "exit_code": 0,
                                    "notes": "Set field case variables.",
                                    "current_preflight_allows_capture": true,
                                    "current_preflight_report": "/home/user/DroneTransfer/outgoing/replay-cases/field_capture_preflight.json",
                                    "current_preflight_status": "degraded",
                                    "current_ready_for_registration": false,
                                    "guidance": "A newer field-capture preflight report is capture-ready."
                                },
                                {
                                    "name": "run_autonomy_readiness_audit",
                                    "status": "failed",
                                    "exit_code": 1,
                                    "notes": "Final autonomy readiness failed."
                                }
                            ]
                        }
                    },
                    {
                        "name": "final_proof_markers",
                        "status": "degraded",
                        "message": "Missing final proof artifact markers.",
                        "details": {
                            "marker_count": 13,
                            "missing_markers": [
                                "__VISION_NAV_FIELD_COLLECTION_PLAN__",
                                "__VISION_NAV_ROSBAG2_CLI_REVIEW__"
                            ],
                            "present_markers": [
                                "__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__",
                                "__VISION_NAV_SUPPORT_ZIP__",
                                "__VISION_NAV_PX4_SITL_REPORT__",
                                "__VISION_NAV_PX4_SITL_PREREQS__"
                            ]
                        }
                    }
                ]
            })
            .to_string(),
        )
        .expect("write local workflow validation artifact");
        std::fs::write(base.join("support-bundles").join("support.zip"), "zip")
            .expect("write local support artifact");
        std::fs::write(dir.join("field_evidence_report.json"), "{}")
            .expect("write local field artifact");
        std::fs::write(
            base.join("feature-method-bench")
                .join("feature_method_benchmark.json"),
            "{}",
        )
        .expect("write local feature artifact");
        std::fs::write(dir.join("threshold_tuning_report.json"), "{}")
            .expect("write local threshold artifact");
        std::fs::write(
            base.join("terrain-match")
                .join("rosbag-jsonl-validation.json"),
            "{}",
        )
        .expect("write local rosbag validation artifact");
        std::fs::write(dir.join("autonomy_readiness_report.md"), "# handoff")
            .expect("write local handoff artifact");
        std::fs::write(dir.join("autonomy_readiness_report.evidence.zip"), "zip")
            .expect("write local evidence package artifact");
        std::fs::write(
            base.join("px4-sitl-evidence")
                .join("receiver_evidence.json"),
            "{}",
        )
        .expect("write local px4 artifact");
        std::fs::write(
            base.join("px4-sitl-evidence")
                .join("px4_sitl_capture_prereqs.json"),
            "{}",
        )
        .expect("write local px4 prereq artifact");
        let reports = list_autonomy_evidence_workflow_reports(dir.to_string_lossy().into_owned())
            .expect("list workflow reports");
        let _ = std::fs::remove_dir_all(&base);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].name, "autonomy_evidence_workflow.json");
        assert_eq!(reports[0].status.as_deref(), Some("failed"));
        assert_eq!(
            reports[0].generated_at.as_deref(),
            Some("2026-06-21T12:00:00Z")
        );
        assert_eq!(reports[0].summary.passed, Some(3));
        assert_eq!(reports[0].summary.failed, Some(1));
        assert_eq!(reports[0].summary.skipped, Some(2));
        assert_eq!(reports[0].steps.len(), 3);
        assert_eq!(reports[0].steps[0].name.as_deref(), Some("field_template"));
        assert_eq!(reports[0].steps[0].exit_code, Some(0));
        assert_eq!(reports[0].steps[1].status.as_deref(), Some("skipped"));
        assert_eq!(reports[0].marker_count, 13);
        assert_eq!(
            reports[0].workflow_logs_path.as_deref(),
            Some("/tmp/autonomy_evidence_workflow.logs.tar.gz")
        );
        assert!(reports[0].workflow_logs_local_path.as_deref().is_some_and(
            |path| path.ends_with("replay-cases/autonomy_evidence_workflow.logs.tar.gz")
        ));
        assert_eq!(
            reports[0].workflow_validation_path.as_deref(),
            Some("/tmp/autonomy_evidence_workflow.validation.json")
        );
        assert!(reports[0]
            .workflow_validation_local_path
            .as_deref()
            .is_some_and(
                |path| path.ends_with("replay-cases/autonomy_evidence_workflow.validation.json")
            ));
        let validation = reports[0]
            .workflow_validation_summary
            .as_ref()
            .expect("workflow validation summary");
        assert_eq!(validation.status.as_deref(), Some("degraded"));
        assert_eq!(validation.workflow_status.as_deref(), Some("failed"));
        assert_eq!(validation.step_count, Some(3));
        assert_eq!(validation.marker_count, Some(13));
        assert_eq!(validation.issue_count, 1);
        assert_eq!(validation.issues.len(), 1);
        let next_step = validation
            .next_required_step
            .as_ref()
            .expect("workflow validation next required step");
        assert_eq!(
            next_step.name.as_deref(),
            Some("register_field_replay_case")
        );
        assert_eq!(next_step.status.as_deref(), Some("skipped"));
        assert_eq!(
            next_step.command.as_deref(),
            Some("./scripts/pi/register_field_replay_case.sh")
        );
        assert_eq!(
            next_step.desktop_action.as_deref(),
            Some("Module Setup > Field Evidence Case > Register")
        );
        assert_eq!(
            next_step.bundle_path.as_deref(),
            Some("/home/user/drone-data/map_bundles/mission_bundle")
        );
        assert_eq!(
            next_step.expected_log.as_deref(),
            Some("/home/user/DroneTransfer/outgoing/field-captures/good_texture/terrain_matches.jsonl")
        );
        assert_eq!(
            next_step.output_dir.as_deref(),
            Some("/home/user/DroneTransfer/outgoing/field-captures/good_texture")
        );
        assert_eq!(
            next_step.runtime_status_path.as_deref(),
            Some(
                "/home/user/DroneTransfer/outgoing/field-captures/good_texture/runtime_status.json"
            )
        );
        assert_eq!(
            next_step.capture_command_after_bundle.as_deref(),
            Some("VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && VISION_NAV_RUNTIME_STATUS_ROOTS=/home/user/DroneTransfer/outgoing/field-captures/good_texture ./scripts/pi/read_runtime_status.sh")
        );
        assert_eq!(validation.checks.len(), 3);
        assert_eq!(
            validation.checks[1].name.as_deref(),
            Some("required_step_results")
        );
        assert_eq!(validation.checks[1].status.as_deref(), Some("degraded"));
        assert_eq!(validation.checks[1].non_passed_count, Some(2));
        assert_eq!(validation.checks[1].missing_steps.len(), 0);
        assert_eq!(validation.checks[1].non_passed_steps.len(), 2);
        assert_eq!(
            validation.checks[1].non_passed_steps[0].name.as_deref(),
            Some("register_field_replay_case")
        );
        assert_eq!(
            validation.checks[1].non_passed_steps[0].status.as_deref(),
            Some("skipped")
        );
        assert_eq!(validation.checks[1].non_passed_steps[0].exit_code, Some(0));
        assert_eq!(
            validation.checks[1].non_passed_steps[0].current_preflight_allows_capture,
            Some(true)
        );
        assert_eq!(
            validation.checks[1].non_passed_steps[0]
                .current_preflight_report
                .as_deref(),
            Some("/home/user/DroneTransfer/outgoing/replay-cases/field_capture_preflight.json")
        );
        assert_eq!(
            validation.checks[1].non_passed_steps[0]
                .current_preflight_status
                .as_deref(),
            Some("degraded")
        );
        assert_eq!(
            validation.checks[1].non_passed_steps[0].current_ready_for_registration,
            Some(false)
        );
        assert_eq!(
            validation.checks[1].non_passed_steps[0].guidance.as_deref(),
            Some("A newer field-capture preflight report is capture-ready.")
        );
        assert_eq!(
            validation.checks[2].name.as_deref(),
            Some("final_proof_markers")
        );
        assert_eq!(validation.checks[2].status.as_deref(), Some("degraded"));
        assert_eq!(validation.checks[2].marker_count, Some(13));
        assert_eq!(
            validation.checks[2].missing_markers,
            vec![
                "__VISION_NAV_FIELD_COLLECTION_PLAN__".to_string(),
                "__VISION_NAV_ROSBAG2_CLI_REVIEW__".to_string()
            ]
        );
        assert_eq!(
            validation.checks[2].present_markers,
            vec![
                "__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__".to_string(),
                "__VISION_NAV_SUPPORT_ZIP__".to_string(),
                "__VISION_NAV_PX4_SITL_REPORT__".to_string(),
                "__VISION_NAV_PX4_SITL_PREREQS__".to_string()
            ]
        );
        assert_eq!(
            reports[0].field_metadata_update_command.as_deref(),
            Some("./scripts/pi/update_field_capture_metadata.sh --condition low_light")
        );
        assert_eq!(
            reports[0].support_bundle_path.as_deref(),
            Some("/tmp/support.zip")
        );
        assert!(reports[0]
            .support_bundle_local_path
            .as_deref()
            .is_some_and(|path| path.ends_with("support-bundles/support.zip")));
        assert_eq!(
            reports[0].field_evidence_report_path.as_deref(),
            Some("/tmp/field_evidence_report.json")
        );
        assert!(reports[0]
            .field_evidence_report_local_path
            .as_deref()
            .is_some_and(|path| path.ends_with("replay-cases/field_evidence_report.json")));
        assert_eq!(
            reports[0].feature_method_report_path.as_deref(),
            Some("/tmp/feature_method_benchmark.json")
        );
        assert!(reports[0]
            .feature_method_report_local_path
            .as_deref()
            .is_some_and(
                |path| path.ends_with("feature-method-bench/feature_method_benchmark.json")
            ));
        assert_eq!(
            reports[0].threshold_report_path.as_deref(),
            Some("/tmp/threshold_tuning_report.json")
        );
        assert!(reports[0]
            .threshold_report_local_path
            .as_deref()
            .is_some_and(|path| path.ends_with("replay-cases/threshold_tuning_report.json")));
        assert_eq!(
            reports[0].rosbag_validation_path.as_deref(),
            Some("/tmp/rosbag-jsonl-validation.json")
        );
        assert!(reports[0]
            .rosbag_validation_local_path
            .as_deref()
            .is_some_and(|path| path.ends_with("terrain-match/rosbag-jsonl-validation.json")));
        assert_eq!(
            reports[0].readiness_report_path.as_deref(),
            Some("/tmp/autonomy_readiness_report.json")
        );
        assert_eq!(
            reports[0].handoff_path.as_deref(),
            Some("/tmp/autonomy_readiness_report.md")
        );
        assert!(reports[0]
            .handoff_local_path
            .as_deref()
            .is_some_and(|path| path.ends_with("replay-cases/autonomy_readiness_report.md")));
        assert_eq!(
            reports[0].evidence_package_path.as_deref(),
            Some("/tmp/autonomy_readiness_report.evidence.zip")
        );
        assert!(reports[0]
            .evidence_package_local_path
            .as_deref()
            .is_some_and(
                |path| path.ends_with("replay-cases/autonomy_readiness_report.evidence.zip")
            ));
        assert_eq!(
            reports[0].px4_receiver_report_path.as_deref(),
            Some("/tmp/receiver_evidence.json")
        );
        assert!(reports[0]
            .px4_receiver_report_local_path
            .as_deref()
            .is_some_and(|path| path.ends_with("px4-sitl-evidence/receiver_evidence.json")));
        assert_eq!(
            reports[0].px4_prereq_report_path.as_deref(),
            Some("/tmp/px4_sitl_capture_prereqs.json")
        );
        assert!(reports[0]
            .px4_prereq_report_local_path
            .as_deref()
            .is_some_and(|path| path.ends_with("px4-sitl-evidence/px4_sitl_capture_prereqs.json")));
    }

    #[test]
    fn lists_field_evidence_reports_from_json_dir() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("drone-field-evidence-reports-{stamp}"));
        std::fs::create_dir_all(&dir).expect("create evidence dir");
        std::fs::write(
            dir.join("field_evidence_report.json"),
            serde_json::json!({
                "status": "failed",
                "manifest_path": "field_manifest.json",
                "coverage": {
                    "requirements": [
                        {"key": "good_texture", "status": "covered", "case_count": 1, "field_case_count": 1},
                        {"key": "low_texture", "status": "missing", "case_count": 0, "field_case_count": 0}
                    ]
                },
                "replay_gates": {"status": "passed", "case_count": 1, "reports": []},
                "summary": {
                    "coverage_status": "failed",
                    "replay_status": "passed",
                    "case_count": 1,
                    "field_case_count": 1,
                    "capture_metadata_issue_count": 2,
                    "covered_conditions": ["good_texture"],
                    "required_conditions": ["good_texture", "low_texture"]
                }
            })
            .to_string(),
        )
        .expect("write field evidence report");
        std::fs::write(
            dir.join("autonomy_readiness_report.json"),
            serde_json::json!({"status": "failed", "checks": [], "summary": {}}).to_string(),
        )
        .expect("write unrelated report");
        let reports =
            list_field_evidence_reports(dir.to_string_lossy().into_owned()).expect("list reports");
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].name, "field_evidence_report.json");
        assert_eq!(reports[0].report.status.as_deref(), Some("failed"));
        assert_eq!(reports[0].report.coverage_status.as_deref(), Some("failed"));
        assert_eq!(reports[0].report.replay_status.as_deref(), Some("passed"));
        assert_eq!(reports[0].report.field_case_count, Some(1));
        assert_eq!(reports[0].report.capture_metadata_issue_count, Some(2));
        assert_eq!(reports[0].report.requirements.len(), 2);
        assert_eq!(
            reports[0].report.requirements[0].status.as_deref(),
            Some("covered")
        );
        assert_eq!(
            reports[0].report.requirements[1].status.as_deref(),
            Some("missing")
        );
    }

    #[test]
    fn lists_field_evidence_templates_from_json_dir() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("drone-field-template-reports-{stamp}"));
        std::fs::create_dir_all(&dir).expect("create field template dir");
        std::fs::write(
            dir.join("field_manifest.template.json"),
            serde_json::json!({
                "version": "0.1.0",
                "template": {
                    "schema_version": "vision_nav_field_evidence_template_v1",
                    "site_name": "site-a",
                    "required_conditions": ["good_texture", "wrong_map"]
                },
                "cases": [
                    {
                        "case_name": "site-a-good-texture",
                        "expected": "good_map",
                        "dataset_type": "field",
                        "conditions": ["good_texture"],
                        "log": "field/good_texture/terrain_matches.jsonl",
                        "template_status": "replace_log_path_and_notes_after_capture"
                    },
                    {
                        "case_name": "site-a-wrong-map",
                        "expected": "wrong_map",
                        "dataset_type": "field",
                        "conditions": ["wrong_map"],
                        "log": "field/wrong_map/terrain_matches.jsonl"
                    }
                ]
            })
            .to_string(),
        )
        .expect("write field evidence template");
        std::fs::write(
            dir.join("field_evidence_report.json"),
            serde_json::json!({"status": "failed", "coverage": {}, "replay_gates": {}}).to_string(),
        )
        .expect("write unrelated report");
        let templates = list_field_evidence_templates(dir.to_string_lossy().into_owned())
            .expect("list templates");
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(templates.len(), 1);
        assert_eq!(templates[0].name, "field_manifest.template.json");
        assert_eq!(templates[0].site_name.as_deref(), Some("site-a"));
        assert_eq!(templates[0].case_count, 2);
        assert_eq!(templates[0].placeholder_count, 1);
        assert_eq!(
            templates[0].required_conditions,
            vec!["good_texture".to_string(), "wrong_map".to_string()]
        );
        assert_eq!(
            templates[0].conditions,
            vec!["good_texture".to_string(), "wrong_map".to_string()]
        );
        assert_eq!(
            templates[0].placeholder_conditions,
            vec!["good_texture".to_string()]
        );
        assert_eq!(
            templates[0].registered_conditions,
            vec!["wrong_map".to_string()]
        );
    }

    #[test]
    fn lists_field_collection_plans_from_json_dir() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("drone-field-collection-plans-{stamp}"));
        std::fs::create_dir_all(&dir).expect("create field collection plan dir");
        std::fs::write(
            dir.join("field_collection_plan.json"),
            serde_json::json!({
                "schema_version": "vision_nav_field_collection_plan_v1",
                "status": "degraded",
                "site_name": "site-a",
                "manifest_path": "/home/user/DroneTransfer/outgoing/replay-cases/field_manifest.json",
                "bundle": "/home/user/drone-data/map_bundles/mission_bundle",
                "pending_metadata_update_command_count": 1,
                "summary": {
                    "required_count": 8,
                    "registered_count": 1,
                    "registered_missing_log_count": 1,
                    "placeholder_count": 6,
                    "missing_count": 0
                },
                    "next_condition": {
                        "condition": "low_texture",
                        "label": "Low texture",
                        "expected": "degraded",
                        "status": "placeholder",
                        "case_name": "site-a-low-texture",
                        "manifest_log_exists": false,
                        "source_log": "/home/user/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl",
                        "capture_output_dir": "/tmp/field-captures/low_texture",
                        "capture_command": "VISION_NAV_OUTPUT_DIR=/tmp/field-captures/low_texture ./scripts/pi/run_terrain_nav_loop.sh",
                        "metadata_update_command": "VISION_NAV_FIELD_CONDITION=low_texture ./scripts/pi/update_field_capture_metadata.sh",
                        "register_command": "VISION_NAV_FIELD_CASE_NAME=site-a-low-texture ./scripts/pi/register_field_replay_case.sh"
                    },
                    "conditions": [
                    {
                        "condition": "good_texture",
                        "label": "Good texture, matching map",
                        "expected": "good_map",
                        "status": "registered",
                        "case_name": "site-a-good-texture",
                        "manifest_log_path": "field/good_texture/terrain_matches.jsonl",
                        "manifest_log_exists": true,
                        "register_command": "VISION_NAV_FIELD_CASE_NAME=site-a-good-texture ./scripts/pi/register_field_replay_case.sh"
                    },
                    {
                        "condition": "low_texture",
                        "label": "Low texture",
                        "expected": "degraded",
                        "status": "placeholder",
                        "case_name": "site-a-low-texture",
                        "manifest_log_exists": false,
                        "source_log": "/home/user/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl",
                        "bundle": "/home/user/drone-data/map_bundles/mission_bundle",
                        "notes": "low texture validation pass",
                        "capture_metadata": {
                            "site_name": "site-a",
                            "condition": "low_texture",
                            "expected_behavior": "degraded",
                            "operator": "Ada"
                        }
                    }
                ]
            })
            .to_string(),
        )
        .expect("write field collection plan");
        std::fs::write(
            dir.join("field_collection_plan.md"),
            "# Field Evidence Collection Plan\n\n- [x] Good texture\n",
        )
        .expect("write field collection markdown");
        std::fs::write(
            dir.join("field_manifest.template.json"),
            serde_json::json!({"template": {"schema_version": "vision_nav_field_evidence_template_v1"}, "cases": []})
                .to_string(),
        )
        .expect("write unrelated template");
        let plans =
            list_field_collection_plans(dir.to_string_lossy().into_owned()).expect("list plans");
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(plans.len(), 1);
        assert_eq!(plans[0].name, "field_collection_plan.json");
        assert_eq!(plans[0].status.as_deref(), Some("degraded"));
        assert_eq!(plans[0].site_name.as_deref(), Some("site-a"));
        assert_eq!(plans[0].summary.required_count, Some(8));
        assert_eq!(plans[0].summary.registered_count, Some(1));
        assert_eq!(plans[0].summary.placeholder_count, Some(6));
        assert_eq!(plans[0].pending_metadata_update_command_count, Some(1));
        let next_condition = plans[0]
            .next_condition
            .as_ref()
            .expect("field collection next condition");
        assert_eq!(next_condition.condition.as_deref(), Some("low_texture"));
        assert_eq!(
                next_condition.capture_command.as_deref(),
                Some("VISION_NAV_OUTPUT_DIR=/tmp/field-captures/low_texture ./scripts/pi/run_terrain_nav_loop.sh && VISION_NAV_RUNTIME_STATUS_ROOTS='/tmp/field-captures/low_texture' ./scripts/pi/read_runtime_status.sh")
            );
        assert_eq!(
            next_condition.metadata_update_command.as_deref(),
            Some("VISION_NAV_FIELD_CONDITION=low_texture ./scripts/pi/update_field_capture_metadata.sh")
        );
        assert_eq!(plans[0].conditions.len(), 2);
        assert_eq!(
            plans[0].conditions[0].condition.as_deref(),
            Some("good_texture")
        );
        assert_eq!(plans[0].conditions[0].status.as_deref(), Some("registered"));
        assert_eq!(plans[0].conditions[0].manifest_log_exists, Some(true));
        assert!(plans[0].conditions[0].register_command.is_some());
        assert_eq!(
            plans[0].conditions[1].notes.as_deref(),
            Some("low texture validation pass")
        );
        assert_eq!(
            plans[0].conditions[1].source_log.as_deref(),
            Some("/home/user/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl")
        );
        assert_eq!(
            plans[0].conditions[1]
                .capture_metadata
                .as_ref()
                .and_then(|value| value.get("operator"))
                .and_then(|value| value.as_str()),
            Some("Ada")
        );
        assert_eq!(
            plans[0]
                .markdown_path
                .as_deref()
                .and_then(|path| Path::new(path).file_name())
                .and_then(|name| name.to_str()),
            Some("field_collection_plan.md")
        );
        assert!(plans[0].markdown_size_bytes.is_some());
    }

    #[test]
    fn lists_px4_receiver_reports_from_json_dir() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("drone-px4-receiver-reports-{stamp}"));
        std::fs::create_dir_all(&dir).expect("create px4 report dir");
        std::fs::write(
            dir.join("receiver_evidence.json"),
            serde_json::json!({
                "status": "passed",
                "expected_message": "odometry",
                "listener": {
                    "sample_count": 4,
                    "observed_rate_hz": 5.0,
                    "latest_sample_age_s": 0.3,
                    "last_position": [1.0, 2.0, -3.0]
                },
                "mavlink_status": {
                    "mavlink_version": 2,
                    "has_udp_14550": true
                },
                "issues": []
            })
            .to_string(),
        )
        .expect("write receiver report");
        std::fs::write(
            dir.join("autonomy_readiness_report.json"),
            serde_json::json!({"status": "failed", "checks": [], "summary": {}}).to_string(),
        )
        .expect("write unrelated report");
        let reports =
            list_px4_receiver_reports(dir.to_string_lossy().into_owned()).expect("list reports");
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].name, "receiver_evidence.json");
        assert_eq!(reports[0].report.status.as_deref(), Some("passed"));
        assert_eq!(
            reports[0].report.expected_message.as_deref(),
            Some("odometry")
        );
        assert_eq!(reports[0].report.sample_count, Some(4));
        assert_eq!(reports[0].report.observed_rate_hz, Some(5.0));
        assert_eq!(reports[0].report.mavlink_version, Some(2));
        assert_eq!(reports[0].report.has_udp_14550, Some(true));
    }

    #[test]
    fn lists_px4_prereq_reports_from_json_dir() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("drone-px4-prereq-reports-{stamp}"));
        std::fs::create_dir_all(&dir).expect("create px4 report dir");
        std::fs::write(
            dir.join("px4_sitl_capture_prereqs.json"),
            serde_json::json!({
                "schema_version": "vision_nav_px4_sitl_capture_prereqs_v1",
                "status": "failed",
                "generated_at": "2026-06-22T11:28:21Z",
                "session_dir": "/tmp/px4-sitl-evidence",
                "capture_dir": "/tmp/px4-sitl-evidence/receiver_capture",
                "receiver_report": "/tmp/px4-sitl-evidence/receiver_evidence.json",
                "px4_dir": "/tmp/PX4-Autopilot",
                "px4_target": "px4_sitl gz_x500",
                "tmux_session": "vision-nav-px4-sitl",
                "checks": [
                    {"name": "tmux_installed", "status": "failed", "message": "Install tmux."},
                    {"name": "px4_autopilot_dir", "status": "passed", "message": "PX4 found."}
                ],
                "next_actions": ["Install tmux."],
                "fix_commands": [
                    {
                        "label": "Install tmux with Homebrew",
                        "command": "brew install tmux",
                        "condition": "tmux_installed"
                    }
                ]
            })
            .to_string(),
        )
        .expect("write prereq report");
        std::fs::write(
            dir.join("receiver_evidence.json"),
            serde_json::json!({"status": "passed", "listener": {"sample_count": 1}}).to_string(),
        )
        .expect("write unrelated receiver report");
        let reports =
            list_px4_prereq_reports(dir.to_string_lossy().into_owned()).expect("list reports");
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].name, "px4_sitl_capture_prereqs.json");
        assert_eq!(reports[0].report.status.as_deref(), Some("failed"));
        assert_eq!(
            reports[0].report.px4_target.as_deref(),
            Some("px4_sitl gz_x500")
        );
        assert_eq!(reports[0].report.checks.len(), 2);
        assert_eq!(
            reports[0].report.checks[0].name.as_deref(),
            Some("tmux_installed")
        );
        assert_eq!(reports[0].report.next_actions, vec!["Install tmux."]);
        assert_eq!(reports[0].report.fix_commands.len(), 1);
        assert_eq!(
            reports[0].report.fix_commands[0].command.as_deref(),
            Some("brew install tmux")
        );
    }

    #[test]
    fn lists_feature_method_benchmark_reports_from_json_dir() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("drone-feature-bench-reports-{stamp}"));
        std::fs::create_dir_all(&dir).expect("create benchmark dir");
        std::fs::write(
            dir.join("feature-method-benchmark.json"),
            serde_json::json!({
                "status": "passed",
                "case_name": "field-good-texture",
                "expected": "good_map",
                "recommended_method": "orb",
                "methods": [
                    {
                        "method": "orb",
                        "status": "passed",
                        "gate": {"metrics": {"accepted_rate": 0.9, "total_records": 10}}
                    },
                    {
                        "method": "neural",
                        "status": "not_available",
                        "reason": "not generated"
                    }
                ]
            })
            .to_string(),
        )
        .expect("write feature benchmark report");
        std::fs::write(
            dir.join("field_evidence_report.json"),
            serde_json::json!({"status": "failed", "coverage": {}, "replay_gates": {}}).to_string(),
        )
        .expect("write unrelated report");
        let reports = list_feature_method_benchmark_reports(dir.to_string_lossy().into_owned())
            .expect("list reports");
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].name, "feature-method-benchmark.json");
        assert_eq!(reports[0].report.status.as_deref(), Some("passed"));
        assert_eq!(reports[0].report.recommended_method.as_deref(), Some("orb"));
        assert_eq!(reports[0].report.methods.len(), 2);
        assert_eq!(reports[0].report.methods[0].method.as_deref(), Some("orb"));
        assert_eq!(reports[0].report.methods[0].accepted_rate, Some(0.9));
        assert_eq!(reports[0].report.methods[0].total_records, Some(10));
    }

    #[test]
    fn lists_threshold_tuning_reports_from_json_dir() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("drone-threshold-tuning-reports-{stamp}"));
        std::fs::create_dir_all(&dir).expect("create threshold dir");
        std::fs::write(
            dir.join("threshold_tuning_report.json"),
            serde_json::json!({
                "status": "passed",
                "method": "field-replay-gate-threshold-audit",
                "manifest_path": "field_manifest.json",
                "summary": {
                    "coverage_status": "passed",
                    "replay_status": "passed",
                    "case_count": 8,
                    "field_case_count": 8,
                    "capture_metadata_issue_count": 0,
                    "covered_conditions": ["good_texture", "wrong_map"]
                },
                "metrics": {
                    "margins": {
                        "good_map_accepted_rate": 0.25,
                        "wrong_map_accepted_rate": 0.1
                    }
                }
            })
            .to_string(),
        )
        .expect("write threshold report");
        std::fs::write(
            dir.join("feature-method-benchmark.json"),
            serde_json::json!({"status": "passed", "methods": []}).to_string(),
        )
        .expect("write unrelated report");
        let reports = list_threshold_tuning_reports(dir.to_string_lossy().into_owned())
            .expect("list reports");
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].name, "threshold_tuning_report.json");
        assert_eq!(reports[0].report.status.as_deref(), Some("passed"));
        assert_eq!(
            reports[0].report.method.as_deref(),
            Some("field-replay-gate-threshold-audit")
        );
        assert_eq!(reports[0].report.field_case_count, Some(8));
        assert_eq!(reports[0].report.capture_metadata_issue_count, Some(0));
        assert!(reports[0].report.margins.is_some());
    }

    #[test]
    fn lists_rosbag_export_validation_reports_from_json_dir() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("drone-rosbag-validation-reports-{stamp}"));
        std::fs::create_dir_all(&dir).expect("create rosbag validation dir");
        std::fs::write(
            dir.join("rosbag-jsonl-validation.json"),
            serde_json::json!({
                "schema_version": "vision_nav_rosbag_export_validation_v1",
                "status": "passed",
                "artifact_path": "/tmp/rosbag-jsonl",
                "metadata_path": "/tmp/rosbag-jsonl/metadata.json",
                "format": "vision_nav_rosbag_jsonl_v1",
                "message_count": 4,
                "topic_count": 3,
                "topics": [
                    {"name": "/vision_nav/odometry", "type": "nav_msgs/msg/Odometry", "message_count": 1},
                    {"name": "/diagnostics", "type": "diagnostic_msgs/msg/DiagnosticArray", "message_count": 2},
                    {"name": "/vision_nav/camera/image/compressed", "type": "sensor_msgs/msg/CompressedImage", "message_count": 1}
                ],
                "issues": []
            })
            .to_string(),
        )
        .expect("write rosbag validation report");
        std::fs::write(
            dir.join("threshold_tuning_report.json"),
            serde_json::json!({"status": "passed", "method": "field-replay-gate-threshold-audit", "summary": {}}).to_string(),
        )
        .expect("write unrelated report");
        let reports = list_rosbag_export_validation_reports(dir.to_string_lossy().into_owned())
            .expect("list reports");
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].name, "rosbag-jsonl-validation.json");
        assert_eq!(reports[0].report.status.as_deref(), Some("passed"));
        assert_eq!(
            reports[0].report.format.as_deref(),
            Some("vision_nav_rosbag_jsonl_v1")
        );
        assert_eq!(reports[0].report.message_count, Some(4));
        assert_eq!(reports[0].report.topic_count, Some(3));
        assert_eq!(reports[0].report.topics.len(), 3);
    }

    #[test]
    fn parses_bundle_diagnostic_mission_plan_candidates() {
        let diagnostic = support_bundle_diagnostic_from_json(&serde_json::json!({
            "bundle_exists": false,
            "missing_required_files": ["manifest.json"],
            "search_roots": ["/home/user/DroneTransfer/outgoing"],
            "mission_plan_candidate_count": 2,
            "mission_plan_candidates": [
                {
                    "path": "/home/user/DroneTransfer/outgoing/mission_plan.json",
                    "type": "mission_plan_json",
                    "name": "mission_plan.json",
                    "mission_item_count": 3,
                    "gnss_denied_status": "passed",
                    "has_gnss_denied": true
                },
                {
                    "path": "/home/user/DroneTransfer/outgoing/qgc.plan",
                    "type": "qgc_plan_json",
                    "name": "qgc.plan",
                    "mission_item_count": 3,
                    "gnss_denied_status": "passed",
                    "has_gnss_denied": true
                }
            ],
            "recommended_actions": [
                {
                    "id": "build_from_detected_map_source",
                    "status": "optional",
                    "title": "Use a detected saved map source to build the runtime bundle.",
                    "desktop_action": "Mission Planner > Select Map Source, Build Bundle, Upload Bundle",
                    "notes": "Detected mission plan inputs are included.",
                    "command": "VISION_NAV_MAP_SOURCE=/home/user/maps/field VISION_NAV_MISSION_PLAN_JSON=/home/user/DroneTransfer/outgoing/mission_plan.json VISION_NAV_QGC_PLAN_JSON=/home/user/DroneTransfer/outgoing/qgc.plan ./scripts/pi/build_bundle_from_map_source.sh",
                    "mission_plan_path": "/home/user/DroneTransfer/outgoing/mission_plan.json",
                    "qgc_plan_path": "/home/user/DroneTransfer/outgoing/qgc.plan"
                }
            ]
        }))
        .expect("bundle diagnostic");
        assert_eq!(diagnostic.mission_plan_candidate_count, Some(2));
        assert_eq!(
            diagnostic.mission_plan_candidates[0].plan_type.as_deref(),
            Some("mission_plan_json")
        );
        assert_eq!(
            diagnostic.mission_plan_candidates[1].plan_type.as_deref(),
            Some("qgc_plan_json")
        );
        assert_eq!(
            diagnostic.mission_plan_candidates[0]
                .gnss_denied_status
                .as_deref(),
            Some("passed")
        );
        assert_eq!(diagnostic.mission_plan_candidates[0].has_gnss_denied, Some(true));
        assert_eq!(
            diagnostic.recommended_actions[0]
                .mission_plan_path
                .as_deref(),
            Some("/home/user/DroneTransfer/outgoing/mission_plan.json")
        );
        assert_eq!(
            diagnostic.recommended_actions[0].qgc_plan_path.as_deref(),
            Some("/home/user/DroneTransfer/outgoing/qgc.plan")
        );
    }

    #[test]
    fn reads_support_bundle_details_from_zip() {
        const TINY_PNG: &[u8] = &[
            137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 1, 0, 0, 0, 1,
            8, 6, 0, 0, 0, 31, 21, 196, 137, 0, 0, 0, 13, 73, 68, 65, 84, 120, 156, 99, 248, 255,
            255, 63, 0, 5, 254, 2, 254, 167, 53, 129, 132, 0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96,
            130,
        ];
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time")
            .as_nanos();
        let path = std::env::temp_dir().join(format!("drone-support-details-{stamp}.zip"));
        {
            let file = File::create(&path).expect("create zip");
            let mut zip = zip::ZipWriter::new(file);
            let options = SimpleFileOptions::default();
            zip.start_file("support_manifest.json", options)
                .expect("manifest entry");
            zip.write_all(
                serde_json::json!({
                    "metadata": {"vision_nav": {"project_version": "0.1.0"}},
                    "bundle": {"health": {"status": "passed"}},
                    "logs": {
                        "runtime_statuses": [
                            {
                                "schema_version": "vision_nav_runtime_status_v1",
                                "active_map": {"bundle_id": "mission-bundle"},
                                "output": {"log_path": "terrain_matches.jsonl"},
                                "last_match": {"status": "accepted", "tile_id": "tile_001"},
                                "estimator": {"health": "tracking"}
                            }
                        ]
                    },
                    "autonomy_evidence_workflow": {
                        "status": "degraded"
                    }
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write manifest");
            zip.start_file("summaries/terrain_matches.summary.json", options)
                .expect("summary entry");
            zip.write_all(
                serde_json::json!({
                    "total_records": 4,
                    "accepted_rate": 0.5,
                    "status_counts": {"accepted": 2, "rejected": 2},
                    "external_position": {
                        "warning_counts": {"velocity_covariance_missing": 1}
                    }
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write summary");
            zip.start_file("logs/terrain_matches.jsonl", options)
                .expect("log entry");
            zip.write_all(
                [
                    serde_json::json!({
                        "sequence": 1,
                        "result": {
                            "status": "accepted",
                            "tile_id": "tile_001",
                            "confidence": 0.8,
                            "inliers": 24,
                            "reprojection_error_px": 1.5
                        },
                        "external_position_health": {
                            "status": "healthy",
                            "message_type": "odometry",
                            "last_warnings": ["velocity_covariance_missing"]
                        }
                    })
                    .to_string(),
                    serde_json::json!({
                        "sequence": 2,
                        "result": {
                            "status": "rejected",
                            "reason": "low_inliers"
                        }
                    })
                    .to_string(),
                ]
                .join("\n")
                .as_bytes(),
            )
            .expect("write log");
            zip.start_file("summaries/replay_gates/unit.gate.json", options)
                .expect("gate entry");
            zip.write_all(
                serde_json::json!({
                    "case_name": "unit",
                    "expected": "good_map",
                    "status": "failed",
                    "metrics": {"accepted_rate": 0.25, "total_records": 4},
                    "issues": [{"message": "low accepted rate"}]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write gate");
            zip.start_file(
                "summaries/px4_sitl_evidence/receiver_evidence.json",
                options,
            )
            .expect("px4 evidence entry");
            zip.write_all(
                serde_json::json!({
                    "status": "passed",
                    "expected_message": "odometry",
                    "listener": {
                        "sample_count": 2,
                        "latest_sample_age_s": 0.02,
                        "last_position": [0.35, 0.3, -1.5]
                    },
                    "mavlink_status": {
                        "mavlink_version": 2,
                        "has_udp_14550": true
                    },
                    "issues": []
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write px4 evidence");
            zip.start_file("summaries/px4_params/param_check.json", options)
                .expect("px4 params entry");
            zip.write_all(
                serde_json::json!({
                    "status": "degraded",
                    "parameters": {
                        "EKF2_EV_CTRL": 1,
                        "EKF2_HGT_REF": 0,
                        "EKF2_GPS_CTRL": 7,
                        "EKF2_EV_NOISE_MD": 0,
                        "EKF2_EV_DELAY": 80.0
                    },
                    "issues": [{"message": "confirm extrinsics"}]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write px4 params");
            zip.start_file("summaries/ardupilot_params/param_check.json", options)
                .expect("ardupilot params entry");
            zip.write_all(
                serde_json::json!({
                    "status": "passed",
                    "parameters": {
                        "source_set": 1,
                        "VISO_TYPE": 3,
                        "EK3_SRC1_POSXY": 6,
                        "EK3_SRC1_VELXY": 0,
                        "EK3_SRC1_POSZ": 1,
                        "EK3_SRC1_VELZ": 0,
                        "EK3_SRC1_YAW": 1,
                        "source_switch_channels": ["RC8_OPTION"]
                    },
                    "issues": []
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write ardupilot params");
            zip.start_file(
                "summaries/feature_method_benchmarks/unit-method-benchmark-01.json",
                options,
            )
            .expect("feature method benchmark entry");
            zip.write_all(
                serde_json::json!({
                    "status": "passed",
                    "case_name": "unit-method-benchmark",
                    "expected": "good_map",
                    "recommended_method": "orb",
                    "methods": [
                        {
                            "method": "orb",
                            "status": "passed",
                            "gate": {"metrics": {"accepted_rate": 1.0, "total_records": 2}}
                        },
                        {
                            "method": "akaze",
                            "status": "failed",
                            "gate": {"metrics": {"accepted_rate": 0.0, "total_records": 2}}
                        }
                    ]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write feature method benchmark");
            zip.start_file("summaries/field_evidence/field_manifest-01.json", options)
                .expect("field evidence entry");
            zip.write_all(
                serde_json::json!({
                    "status": "passed",
                    "manifest_path": "field_manifest.json",
                    "coverage": {
                        "requirements": [
                            {"key": "good_texture", "status": "covered", "case_count": 1, "field_case_count": 1},
                            {"key": "low_texture", "status": "covered", "case_count": 1, "field_case_count": 1},
                            {"key": "blur", "status": "covered", "case_count": 1, "field_case_count": 1}
                        ]
                    },
                    "summary": {
                        "coverage_status": "passed",
                        "replay_status": "passed",
                        "case_count": 8,
                        "field_case_count": 8,
                        "capture_metadata_issue_count": 0,
                        "covered_conditions": ["good_texture", "low_texture", "blur"],
                        "required_conditions": ["good_texture", "low_texture", "blur"]
                    }
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write field evidence");
            zip.start_file(
                "summaries/field_collection_plans/field_manifest-01.json",
                options,
            )
            .expect("field collection plan entry");
            zip.write_all(
                serde_json::json!({
                    "schema_version": "vision_nav_field_collection_plan_v1",
                    "status": "degraded",
                    "site_name": "unit-field",
                    "manifest_path": "field_manifest.json",
                    "bundle": "mission-bundle",
                    "source_log": "terrain_matches.jsonl",
                    "capture_root": "field-captures",
                    "pending_capture_command_count": 1,
                    "pending_metadata_update_command_count": 1,
                    "pending_registration_command_count": 1,
                    "capture_output_dir_count": 2,
                    "runtime_status_path_count": 2,
                    "condition_source_log_count": 2,
                        "summary": {
                            "required_count": 3,
                            "registered_count": 1,
                            "registered_missing_log_count": 1,
                            "placeholder_count": 1,
                            "missing_count": 0
                        },
                        "next_condition": {
                            "condition": "blur",
                            "label": "Blur",
                            "expected": "degraded",
                            "status": "placeholder",
                            "case_name": "unit-blur",
                            "manifest_log_exists": false,
                            "source_log": "field-captures/blur/terrain_matches.jsonl",
                            "capture_output_dir": "field-captures/blur",
                            "runtime_status_path": "field-captures/blur/runtime_status.json",
                            "capture_command": "./scripts/pi/run_terrain_nav_loop.sh --condition blur",
                            "metadata_update_command": "./scripts/pi/update_field_capture_metadata.sh --condition blur",
                            "register_command": "./scripts/pi/register_field_replay_case.sh --condition blur"
                        },
                        "conditions": [
                        {"condition": "good_texture", "label": "Good texture", "expected": "good_map", "status": "registered", "case_name": "unit-good", "manifest_log_exists": true, "source_log": "field-captures/good/terrain_matches.jsonl", "capture_output_dir": "field-captures/good", "runtime_status_path": "field-captures/good/runtime_status.json", "has_capture_command": true, "has_metadata_update_command": true, "has_register_command": true},
                        {"condition": "blur", "label": "Blur", "expected": "degraded", "status": "placeholder", "case_name": "unit-blur", "manifest_log_exists": false, "source_log": "field-captures/blur/terrain_matches.jsonl", "capture_output_dir": "field-captures/blur", "runtime_status_path": "field-captures/blur/runtime_status.json", "has_capture_command": true, "has_metadata_update_command": true, "has_register_command": true}
                    ]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write field collection plan");
            zip.start_file(
                "summaries/field_capture_preflights/good_texture-01.json",
                options,
            )
            .expect("field capture preflight entry");
            zip.write_all(
                serde_json::json!({
                    "schema_version": "vision_nav_field_capture_preflight_v1",
                    "status": "failed",
                    "plan_path": "field_collection_plan.json",
                    "repo_root": "/home/user/Drone",
                    "condition": "good_texture",
                    "case_name": "unit-good",
                    "expected": "good_map",
                    "bundle_path": "/home/user/drone-data/map_bundles/mission_bundle",
                    "bundle_validation_command": "VISION_NAV_BUNDLE=/home/user/drone-data/map_bundles/mission_bundle ./scripts/pi/validate_terrain_bundle.sh",
                    "ready_for_capture": false,
                    "ready_for_registration": false,
                    "capture_output_dir": "field-captures/good_texture",
                    "source_log": "field-captures/good_texture/terrain_matches.jsonl",
                    "runtime_status_path": "field-captures/good_texture/runtime_status.json",
                    "summary": {"passed": 5, "degraded": 1, "failed": 1},
                    "checks": [
                        {
                            "name": "bundle_path",
                            "status": "failed",
                            "message": "Mission bundle is missing.",
                            "details": {
                                "path": "/home/user/drone-data/map_bundles/mission_bundle",
                                "desktop_action": "Mission Planner > Build Bundle, Upload Bundle",
                                "validation_command": "VISION_NAV_BUNDLE=/home/user/drone-data/map_bundles/mission_bundle ./scripts/pi/validate_terrain_bundle.sh"
                            },
                            "bundle_diagnostic": {
                                "bundle_exists": false,
                                "missing_required_files": ["manifest.json", "ortho/map.png", "features/map_features.npz", "index/tiles.sqlite"],
                                "search_roots": ["/home/user/DroneVisionNav/maps", "/home/user/drone-data/map_bundles"],
                                "bundle_candidate_count": 1,
                                "map_source_candidate_count": 1,
                                "bundle_candidates": [
                                    {
                                        "path": "/home/user/Drone/map_bundles/example",
                                        "bundle_id": "example-single-image",
                                        "tile_index_exists": false,
                                        "field_proof_warning": "Example or synthetic bundles are useful for tooling smoke tests but do not satisfy real field evidence."
                                    }
                                ],
                                "map_source_candidates": [
                                    {
                                        "path": "/home/user/maps/field-area",
                                        "name": "Field Area",
                                        "source": "uploaded_geotiff",
                                        "georef_source": "geotiff_embedded",
                                        "source_format": "saved_map_source",
                                        "requires_import": false
                                    }
                                ],
                                "recommended_actions": [
                                    {
                                        "id": "build_or_upload_selected_bundle",
                                        "status": "action_required",
                                        "title": "Build and upload the selected Mission Planner terrain bundle.",
                                        "desktop_action": "Mission Planner > Build Bundle, Upload Bundle",
                                        "notes": "Set VISION_NAV_MISSION_PLAN_JSON when rebuilding from a shell.",
                                        "command": "VISION_NAV_BUNDLE=/home/user/drone-data/map_bundles/mission_bundle ./scripts/pi/validate_terrain_bundle.sh"
                                    }
                                ]
                            }
                        },
                        {
                            "name": "capture_metadata",
                            "status": "degraded",
                            "message": "Capture metadata still needs operator-filled field values.",
                            "details": {
                                "issue_count": 2,
                                "missing": ["operator", "lighting"]
                            }
                        }
                    ],
                    "next_actions": [
                        {
                            "id": "prepare_bundle",
                            "status": "action_required",
                            "title": "Build, upload, or validate the selected terrain bundle.",
                            "desktop_action": "Mission Planner > Build Bundle, Upload Bundle",
                            "command": "VISION_NAV_BUNDLE=/home/user/drone-data/map_bundles/mission_bundle ./scripts/pi/validate_terrain_bundle.sh",
                            "bundle_path": "/home/user/drone-data/map_bundles/mission_bundle",
                            "bundle_diagnostic": {
                                "bundle_exists": false,
                                "missing_required_files": ["manifest.json", "ortho/map.png"],
                                "search_roots": ["/home/user/DroneVisionNav/maps", "/home/user/drone-data/map_bundles"],
                                "bundle_candidate_count": 1,
                                "map_source_candidate_count": 1,
                                "bundle_candidates": [
                                    {
                                        "path": "/home/user/Drone/map_bundles/example",
                                        "bundle_id": "example-single-image",
                                        "tile_index_exists": false,
                                        "field_proof_warning": "Example or synthetic bundles are useful for tooling smoke tests but do not satisfy real field evidence."
                                    }
                                ],
                                "map_source_candidates": [
                                    {
                                        "path": "/home/user/maps/field-area",
                                        "name": "Field Area",
                                        "source": "uploaded_geotiff",
                                        "georef_source": "geotiff_embedded",
                                        "source_format": "saved_map_source",
                                        "requires_import": false
                                    }
                                ],
                                "recommended_actions": [
                                    {
                                        "id": "build_or_upload_selected_bundle",
                                        "status": "action_required",
                                        "title": "Build and upload the selected Mission Planner terrain bundle.",
                                        "desktop_action": "Mission Planner > Build Bundle, Upload Bundle",
                                        "command": "VISION_NAV_BUNDLE=/home/user/drone-data/map_bundles/mission_bundle ./scripts/pi/validate_terrain_bundle.sh"
                                    }
                                ]
                            }
                        },
                        {
                            "id": "capture_field_terrain_log",
                            "status": "blocked",
                            "title": "Capture the terrain log and runtime status for this condition.",
                            "desktop_action": "Module Setup > Field Log Capture",
                            "command": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && ./scripts/pi/read_runtime_status.sh",
                            "waits_on": ["bundle_path"],
                            "source_log": "field-captures/good_texture/terrain_matches.jsonl",
                            "runtime_status_path": "field-captures/good_texture/runtime_status.json"
                        }
                    ]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write field capture preflight");
            zip.start_file("summaries/threshold_tuning/field_manifest-01.json", options)
                .expect("threshold tuning entry");
            zip.write_all(
                serde_json::json!({
                    "status": "passed",
                    "method": "field-replay-gate-threshold-audit",
                    "manifest_path": "field_manifest.json",
                    "summary": {
                        "coverage_status": "passed",
                        "replay_status": "passed",
                        "case_count": 8,
                        "field_case_count": 8,
                        "capture_metadata_issue_count": 0,
                        "covered_conditions": ["good_texture", "low_texture", "blur"]
                    },
                    "metrics": {
                        "margins": {
                            "good_map_accepted_rate": 0.25,
                            "wrong_map_accepted_rate": 0.0
                        }
                    }
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write threshold tuning");
            zip.start_file(
                "summaries/rosbag_export_validations/vision_nav_rosbag_jsonl_v1-01.json",
                options,
            )
            .expect("rosbag export validation entry");
            zip.write_all(
                serde_json::json!({
                    "schema_version": "vision_nav_rosbag_export_validation_v1",
                    "status": "passed",
                    "artifact_path": "/tmp/rosbag-jsonl",
                    "metadata_path": "/tmp/rosbag-jsonl/metadata.json",
                    "format": "vision_nav_rosbag_jsonl_v1",
                    "message_count": 4,
                    "topic_count": 3,
                    "topics": [
                        {"name": "/vision_nav/odometry", "type": "nav_msgs/msg/Odometry", "message_count": 1},
                        {"name": "/diagnostics", "type": "diagnostic_msgs/msg/DiagnosticArray", "message_count": 2},
                        {"name": "/vision_nav/camera/image/compressed", "type": "sensor_msgs/msg/CompressedImage", "message_count": 1}
                    ],
                    "issues": []
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write rosbag export validation");
            zip.start_file(
                "summaries/rosbag2_cli_reviews/rosbag2-native-01.json",
                options,
            )
            .expect("rosbag2 cli review entry");
            zip.write_all(
                serde_json::json!({
                    "schema_version": "vision_nav_rosbag2_cli_review_v1",
                    "status": "passed",
                    "artifact_path": "/tmp/rosbag2-native",
                    "bag_dir": "/tmp/rosbag2-native",
                    "validation_status": "passed",
                    "validation_format": "vision_nav_rosbag2_v1",
                    "ros2_cli": {
                        "status": "passed",
                        "exit_code": 0
                    },
                    "issues": []
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write rosbag2 cli review");
            zip.start_file(
                "summaries/autonomy_evidence_workflow/workflow_validation.summary.json",
                options,
            )
            .expect("workflow validation entry");
            zip.write_all(
                serde_json::json!({
                    "schema_version": "vision_nav_autonomy_evidence_workflow_validation_v1",
                    "status": "degraded",
                    "workflow_status": "failed",
                    "step_count": 10,
                    "marker_count": 8,
                    "issues": ["Some required workflow steps did not pass."],
                    "next_required_step": {
                        "name": "register_field_replay_case",
                        "status": "skipped",
                        "exit_code": 0,
                        "command": "./scripts/pi/register_field_replay_case.sh",
                        "desktop_action": "Module Setup > Field Evidence Case > Register"
                    },
                    "checks": [
                        {
                            "name": "workflow_provenance",
                            "status": "passed",
                            "message": "Workflow provenance is present."
                        },
                        {
                            "name": "required_step_results",
                            "status": "degraded",
                            "non_passed_count": 1,
                            "non_passed_steps": [
                                {
                                    "name": "register_field_replay_case",
                                    "status": "skipped",
                                    "exit_code": 0,
                                    "current_preflight_allows_capture": true,
                                    "current_preflight_report": "/home/user/DroneTransfer/outgoing/replay-cases/field_capture_preflight.json",
                                    "current_preflight_status": "degraded",
                                    "current_ready_for_registration": false,
                                    "guidance": "A newer field-capture preflight report is capture-ready."
                                }
                            ]
                        }
                    ]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write workflow validation");
            zip.start_file("summaries/bench_readiness.json", options)
                .expect("bench readiness entry");
            zip.write_all(
                serde_json::json!({
                    "status": "degraded",
                    "summary": {"failed": 0, "degraded": 1, "passed": 4},
                    "checks": [
                        {"name": "bundle_health", "status": "passed", "message": "Terrain bundle health passed."},
                        {"name": "px4_params", "status": "degraded", "message": "PX4 parameter check is degraded."}
                    ]
                })
                .to_string()
                .as_bytes(),
            )
            .expect("write bench readiness");
            zip.start_file("extras/camera-health/frame.png", options)
                .expect("image entry");
            zip.write_all(TINY_PNG).expect("write image");
            zip.start_file(
                "extras/field_collection_plans/field_collection_plan.md",
                options,
            )
            .expect("field collection markdown entry");
            zip.write_all(b"# Field Evidence Collection Plan\n")
                .expect("write field collection markdown");
            zip.start_file(
                "extras/field_capture_preflights/field_capture_preflight.json",
                options,
            )
            .expect("field capture preflight artifact entry");
            zip.write_all(br#"{"schema_version":"vision_nav_field_capture_preflight_v1"}"#)
                .expect("write field capture preflight artifact");
            zip.start_file(
                "extras/rosbag2_cli_reviews/rosbag2-cli-review.json",
                options,
            )
            .expect("rosbag2 cli artifact entry");
            zip.write_all(br#"{"status":"passed"}"#)
                .expect("write rosbag2 cli artifact");
            zip.start_file("extras/autonomy_evidence_workflow/workflow.log", options)
                .expect("workflow log artifact entry");
            zip.write_all(b"workflow complete\n")
                .expect("write workflow log artifact");
            zip.start_file("bundle/ortho/map.png", options)
                .expect("map asset entry");
            zip.write_all(TINY_PNG).expect("write map asset");
            zip.finish().expect("finish zip");
        }
        let details = read_support_bundle_details(path.to_string_lossy().into_owned())
            .expect("read support details");
        assert_eq!(details.entry_count, 22);
        assert_eq!(details.logs.len(), 1);
        assert_eq!(details.logs[0].total_records, Some(4));
        assert_eq!(
            details.logs[0]
                .external_position
                .as_ref()
                .and_then(|value| value.pointer("/warning_counts/velocity_covariance_missing"))
                .and_then(|value| value.as_u64()),
            Some(1)
        );
        assert_eq!(details.runtime_statuses.len(), 1);
        assert_eq!(
            details.runtime_statuses[0]
                .pointer("/last_match/status")
                .and_then(|value| value.as_str()),
            Some("accepted")
        );
        assert_eq!(
            details.runtime_statuses[0]
                .pointer("/estimator/health")
                .and_then(|value| value.as_str()),
            Some("tracking")
        );
        assert_eq!(details.log_previews.len(), 1);
        assert_eq!(details.log_previews[0].records.len(), 2);
        assert_eq!(
            details.log_previews[0].records[0].external_position_warnings,
            Some(vec!["velocity_covariance_missing".to_string()])
        );
        assert_eq!(details.log_timelines.len(), 1);
        assert_eq!(details.log_timelines[0].total_records, Some(2));
        assert_eq!(details.log_timelines[0].accepted_rate, Some(0.5));
        assert_eq!(
            details.log_timelines[0]
                .external_position_warning_counts
                .as_ref()
                .and_then(|value| value.pointer("/velocity_covariance_missing"))
                .and_then(|value| value.as_u64()),
            Some(1)
        );
        assert_eq!(details.log_timelines[0].first_sequence, Some(1));
        assert_eq!(details.log_timelines[0].last_sequence, Some(2));
        assert_eq!(details.log_timelines[0].segments.len(), 2);
        assert_eq!(
            details.log_timelines[0].segments[0]
                .dominant_status
                .as_deref(),
            Some("accepted")
        );
        assert_eq!(
            details.log_timelines[0].segments[1]
                .dominant_status
                .as_deref(),
            Some("rejected")
        );
        assert!(details
            .artifacts
            .iter()
            .any(|artifact| artifact.path == "logs/terrain_matches.jsonl"));
        assert!(details
            .artifacts
            .iter()
            .any(|artifact| artifact.path == "summaries/replay_gates/unit.gate.json"));
        assert!(!details
            .artifacts
            .iter()
            .any(|artifact| artifact.path == "bundle/ortho/map.png"));
        assert_eq!(details.image_previews.len(), 1);
        assert_eq!(details.image_previews[0].name, "frame.png");
        assert_eq!(details.image_previews[0].mime_type, "image/png");
        assert!(!details.image_previews[0].base64_data.is_empty());
        assert_eq!(
            details.log_previews[0].records[0].tile_id.as_deref(),
            Some("tile_001")
        );
        assert_eq!(
            details.log_previews[0].records[1].reason.as_deref(),
            Some("low_inliers")
        );
        assert_eq!(details.replay_reports.len(), 1);
        assert_eq!(details.replay_reports[0].status.as_deref(), Some("failed"));
        assert_eq!(
            details.replay_reports[0].issues,
            vec!["low accepted rate".to_string()]
        );
        assert_eq!(details.px4_evidence_reports.len(), 1);
        assert_eq!(
            details.px4_evidence_reports[0].status.as_deref(),
            Some("passed")
        );
        assert_eq!(details.px4_evidence_reports[0].sample_count, Some(2));
        assert_eq!(details.px4_evidence_reports[0].has_udp_14550, Some(true));
        assert_eq!(details.px4_param_reports.len(), 1);
        assert_eq!(
            details.px4_param_reports[0].status.as_deref(),
            Some("degraded")
        );
        assert_eq!(details.px4_param_reports[0].ev_ctrl, Some(1));
        assert_eq!(details.px4_param_reports[0].hgt_ref, Some(0));
        assert_eq!(details.ardupilot_param_reports.len(), 1);
        assert_eq!(
            details.ardupilot_param_reports[0].status.as_deref(),
            Some("passed")
        );
        assert_eq!(details.ardupilot_param_reports[0].source_set, Some(1));
        assert_eq!(details.ardupilot_param_reports[0].posxy_source, Some(6));
        assert_eq!(details.feature_method_benchmark_reports.len(), 1);
        assert_eq!(
            details.feature_method_benchmark_reports[0]
                .recommended_method
                .as_deref(),
            Some("orb")
        );
        assert_eq!(
            details.feature_method_benchmark_reports[0].methods[0]
                .method
                .as_deref(),
            Some("orb")
        );
        assert_eq!(
            details.feature_method_benchmark_reports[0].methods[0].accepted_rate,
            Some(1.0)
        );
        assert_eq!(details.field_evidence_reports.len(), 1);
        assert_eq!(
            details.field_evidence_reports[0].status.as_deref(),
            Some("passed")
        );
        assert_eq!(
            details.field_evidence_reports[0].coverage_status.as_deref(),
            Some("passed")
        );
        assert_eq!(
            details.field_evidence_reports[0].replay_status.as_deref(),
            Some("passed")
        );
        assert_eq!(details.field_evidence_reports[0].field_case_count, Some(8));
        assert_eq!(
            details.field_evidence_reports[0].capture_metadata_issue_count,
            Some(0)
        );
        assert_eq!(details.field_evidence_reports[0].requirements.len(), 3);
        assert_eq!(
            details.field_evidence_reports[0].requirements[0]
                .key
                .as_deref(),
            Some("good_texture")
        );
        assert_eq!(
            details.field_evidence_reports[0].requirements[0]
                .status
                .as_deref(),
            Some("covered")
        );
        assert_eq!(details.field_collection_plan_reports.len(), 1);
        assert_eq!(
            details.field_collection_plan_reports[0].status.as_deref(),
            Some("degraded")
        );
        assert_eq!(
            details.field_collection_plan_reports[0]
                .site_name
                .as_deref(),
            Some("unit-field")
        );
        assert_eq!(
            details.field_collection_plan_reports[0]
                .summary
                .registered_count,
            Some(1)
        );
        assert_eq!(
            details.field_collection_plan_reports[0].pending_capture_command_count,
            Some(1)
        );
        assert_eq!(
            details.field_collection_plan_reports[0].pending_metadata_update_command_count,
            Some(1)
        );
        assert_eq!(
            details.field_collection_plan_reports[0].runtime_status_path_count,
            Some(2)
        );
        let next_condition = details.field_collection_plan_reports[0]
            .next_condition
            .as_ref()
            .expect("support field collection next condition");
        assert_eq!(next_condition.condition.as_deref(), Some("blur"));
        assert_eq!(
            next_condition.capture_command.as_deref(),
            Some("./scripts/pi/run_terrain_nav_loop.sh --condition blur && VISION_NAV_RUNTIME_STATUS_ROOTS='field-captures/blur' ./scripts/pi/read_runtime_status.sh")
        );
        assert_eq!(
            next_condition.register_command.as_deref(),
            Some("./scripts/pi/register_field_replay_case.sh --condition blur")
        );
        assert_eq!(details.field_collection_plan_reports[0].conditions.len(), 2);
        assert_eq!(
            details.field_collection_plan_reports[0].conditions[1]
                .source_log
                .as_deref(),
            Some("field-captures/blur/terrain_matches.jsonl")
        );
        assert_eq!(
            details.field_collection_plan_reports[0].conditions[1]
                .runtime_status_path
                .as_deref(),
            Some("field-captures/blur/runtime_status.json")
        );
        assert_eq!(
            details.field_collection_plan_reports[0].conditions[1].has_capture_command,
            Some(true)
        );
        assert!(details.artifacts.iter().any(|artifact| {
            artifact.path == "summaries/field_collection_plans/field_manifest-01.json"
                && artifact.kind == "field collection plan"
        }));
        assert!(details.artifacts.iter().any(|artifact| {
            artifact.path == "extras/field_collection_plans/field_collection_plan.md"
                && artifact.kind == "field collection artifact"
        }));
        assert_eq!(details.field_capture_preflight_reports.len(), 1);
        assert_eq!(
            details.field_capture_preflight_reports[0].status.as_deref(),
            Some("failed")
        );
        assert_eq!(
            details.field_capture_preflight_reports[0]
                .condition
                .as_deref(),
            Some("good_texture")
        );
        assert_eq!(
            details.field_capture_preflight_reports[0].ready_for_capture,
            Some(false)
        );
        assert_eq!(details.field_capture_preflight_reports[0].checks.len(), 2);
        assert_eq!(
            details.field_capture_preflight_reports[0].checks[0]
                .validation_command
                .as_deref(),
            Some("VISION_NAV_BUNDLE=/home/user/drone-data/map_bundles/mission_bundle ./scripts/pi/validate_terrain_bundle.sh")
        );
        let bundle_diagnostic = details.field_capture_preflight_reports[0].checks[0]
            .bundle_diagnostic
            .as_ref()
            .expect("bundle diagnostic parsed");
        assert_eq!(
            bundle_diagnostic.missing_required_files,
            vec![
                "manifest.json".to_string(),
                "ortho/map.png".to_string(),
                "features/map_features.npz".to_string(),
                "index/tiles.sqlite".to_string()
            ]
        );
        assert_eq!(
            bundle_diagnostic.search_roots[0],
            "/home/user/DroneVisionNav/maps"
        );
        assert_eq!(bundle_diagnostic.search_root_count, Some(2));
        assert_eq!(bundle_diagnostic.bundle_candidate_count, Some(1));
        assert_eq!(
            bundle_diagnostic.bundle_candidates[0].field_proof_warning.as_deref(),
            Some("Example or synthetic bundles are useful for tooling smoke tests but do not satisfy real field evidence.")
        );
        assert_eq!(
            bundle_diagnostic.map_source_candidates[0].name.as_deref(),
            Some("Field Area")
        );
        assert_eq!(
            bundle_diagnostic.map_source_candidates[0]
                .source_format
                .as_deref(),
            Some("saved_map_source")
        );
        assert_eq!(
            bundle_diagnostic.map_source_candidates[0].requires_import,
            Some(false)
        );
        assert_eq!(
            bundle_diagnostic.recommended_actions[0]
                .desktop_action
                .as_deref(),
            Some("Mission Planner > Build Bundle, Upload Bundle")
        );
        assert_eq!(
            bundle_diagnostic.recommended_actions[0].notes.as_deref(),
            Some("Set VISION_NAV_MISSION_PLAN_JSON when rebuilding from a shell.")
        );
        assert_eq!(
            details.field_capture_preflight_reports[0].checks[1].issue_count,
            Some(2)
        );
        assert_eq!(
            details.field_capture_preflight_reports[0].next_actions[1].waits_on,
            vec!["bundle_path".to_string()]
        );
        let action_bundle_diagnostic = details.field_capture_preflight_reports[0].next_actions[0]
            .bundle_diagnostic
            .as_ref()
            .expect("preflight action bundle diagnostic parsed");
        assert_eq!(
            action_bundle_diagnostic.missing_required_files,
            vec!["manifest.json".to_string(), "ortho/map.png".to_string()]
        );
        assert_eq!(
            action_bundle_diagnostic.bundle_candidates[0]
                .bundle_id
                .as_deref(),
            Some("example-single-image")
        );
        assert!(details.artifacts.iter().any(|artifact| {
            artifact.path == "summaries/field_capture_preflights/good_texture-01.json"
                && artifact.kind == "field capture preflight"
        }));
        assert!(details.artifacts.iter().any(|artifact| {
            artifact.path == "extras/field_capture_preflights/field_capture_preflight.json"
                && artifact.kind == "field capture preflight artifact"
        }));
        assert_eq!(details.threshold_tuning_reports.len(), 1);
        assert_eq!(
            details.threshold_tuning_reports[0].status.as_deref(),
            Some("passed")
        );
        assert_eq!(
            details.threshold_tuning_reports[0].method.as_deref(),
            Some("field-replay-gate-threshold-audit")
        );
        assert_eq!(
            details.threshold_tuning_reports[0]
                .coverage_status
                .as_deref(),
            Some("passed")
        );
        assert_eq!(
            details.threshold_tuning_reports[0].replay_status.as_deref(),
            Some("passed")
        );
        assert_eq!(
            details.threshold_tuning_reports[0].field_case_count,
            Some(8)
        );
        assert_eq!(
            details.threshold_tuning_reports[0].capture_metadata_issue_count,
            Some(0)
        );
        assert_eq!(details.rosbag_export_validation_reports.len(), 1);
        assert_eq!(
            details.rosbag_export_validation_reports[0]
                .status
                .as_deref(),
            Some("passed")
        );
        assert_eq!(
            details.rosbag_export_validation_reports[0]
                .format
                .as_deref(),
            Some("vision_nav_rosbag_jsonl_v1")
        );
        assert_eq!(
            details.rosbag_export_validation_reports[0].message_count,
            Some(4)
        );
        assert_eq!(details.rosbag_export_validation_reports[0].topics.len(), 3);
        assert!(details.artifacts.iter().any(|artifact| {
            artifact.path
                == "summaries/rosbag_export_validations/vision_nav_rosbag_jsonl_v1-01.json"
                && artifact.kind == "rosbag export validation"
        }));
        assert_eq!(details.rosbag2_cli_review_reports.len(), 1);
        assert_eq!(
            details.rosbag2_cli_review_reports[0].status.as_deref(),
            Some("passed")
        );
        assert_eq!(
            details.rosbag2_cli_review_reports[0].bag_dir.as_deref(),
            Some("/tmp/rosbag2-native")
        );
        assert_eq!(
            details.rosbag2_cli_review_reports[0]
                .validation_format
                .as_deref(),
            Some("vision_nav_rosbag2_v1")
        );
        assert_eq!(
            details.rosbag2_cli_review_reports[0]
                .ros2_cli_status
                .as_deref(),
            Some("passed")
        );
        assert_eq!(
            details.rosbag2_cli_review_reports[0].ros2_cli_exit_code,
            Some(0)
        );
        assert!(details.artifacts.iter().any(|artifact| {
            artifact.path == "summaries/rosbag2_cli_reviews/rosbag2-native-01.json"
                && artifact.kind == "rosbag2 cli review"
        }));
        assert!(details.artifacts.iter().any(|artifact| {
            artifact.path == "extras/rosbag2_cli_reviews/rosbag2-cli-review.json"
                && artifact.kind == "rosbag2 cli artifact"
        }));
        let workflow_validation = details
            .autonomy_evidence_workflow_validation
            .as_ref()
            .expect("workflow validation summary");
        assert_eq!(workflow_validation.status.as_deref(), Some("degraded"));
        assert_eq!(
            workflow_validation.workflow_status.as_deref(),
            Some("failed")
        );
        assert_eq!(workflow_validation.step_count, Some(10));
        assert_eq!(workflow_validation.issue_count, 1);
        assert_eq!(
            workflow_validation
                .next_required_step
                .as_ref()
                .and_then(|step| step.command.as_deref()),
            Some("./scripts/pi/register_field_replay_case.sh")
        );
        assert_eq!(
            workflow_validation.checks[0].name.as_deref(),
            Some("workflow_provenance")
        );
        assert_eq!(
            workflow_validation.checks[1].non_passed_steps[0]
                .name
                .as_deref(),
            Some("register_field_replay_case")
        );
        assert_eq!(
            workflow_validation.checks[1].non_passed_steps[0].current_preflight_allows_capture,
            Some(true)
        );
        assert_eq!(
            workflow_validation.checks[1].non_passed_steps[0]
                .current_preflight_status
                .as_deref(),
            Some("degraded")
        );
        assert_eq!(
            workflow_validation.checks[1].non_passed_steps[0]
                .guidance
                .as_deref(),
            Some("A newer field-capture preflight report is capture-ready.")
        );
        assert!(details.artifacts.iter().any(|artifact| {
            artifact.path == "summaries/autonomy_evidence_workflow/workflow_validation.summary.json"
                && artifact.kind == "evidence workflow summary"
        }));
        assert!(details.artifacts.iter().any(|artifact| {
            artifact.path == "extras/autonomy_evidence_workflow/workflow.log"
                && artifact.kind == "evidence workflow artifact"
        }));
        let readiness = details.bench_readiness.expect("bench readiness report");
        assert_eq!(readiness.status.as_deref(), Some("degraded"));
        assert_eq!(readiness.degraded_count, Some(1));
        assert_eq!(readiness.checks.len(), 2);
        assert_eq!(readiness.checks[1].name.as_deref(), Some("px4_params"));
        let extracted = extract_support_bundle_artifact(
            path.to_string_lossy().into_owned(),
            "logs/terrain_matches.jsonl".to_string(),
        )
        .expect("extract log artifact");
        let extracted_path = std::path::PathBuf::from(&extracted.path);
        assert!(extracted_path.exists());
        let extracted_text = std::fs::read_to_string(&extracted_path).expect("read extracted log");
        assert!(extracted_text.contains("tile_001"));
        assert!(extract_support_bundle_artifact(
            path.to_string_lossy().into_owned(),
            "bundle/ortho/map.png".to_string(),
        )
        .is_err());
        if let Some(root) = extracted_path.ancestors().find(|candidate| {
            candidate
                .file_name()
                .and_then(|name| name.to_str())
                .map(|name| name.ends_with("-artifacts"))
                .unwrap_or(false)
        }) {
            let _ = std::fs::remove_dir_all(root);
        }
        let _ = std::fs::remove_file(&path);
    }
}
