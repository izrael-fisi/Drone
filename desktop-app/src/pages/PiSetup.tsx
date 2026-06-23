import { useEffect, useMemo, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { open as openDialog, save as saveDialog } from "@tauri-apps/plugin-dialog";
import { readTextFile, writeTextFile } from "@tauri-apps/plugin-fs";
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  ClipboardCheck,
  Copy,
  Camera,
  Eye,
  EyeOff,
  FileText,
  FolderOpen,
  HardDriveUpload,
  KeyRound,
  Loader2,
  Lock,
  RefreshCw,
  Save,
  Server,
  ShieldCheck,
  TestTube2,
  Terminal,
  Wifi,
  XCircle,
} from "lucide-react";
import { cmd } from "../lib/tauri";
import { useAppStore } from "../lib/store";
import { cn, generateId } from "../lib/utils";
import {
  candidateHost,
  candidateName,
  discoveryChecklistText,
  discoveryStatusSummary,
  discoveryTroubleshooting,
  loadDiscoveryHistory,
  mergeDiscoveryHistory,
  networkHintKey,
  networkHintLabel,
  saveDiscoveryHistory,
  selectedNetworkHint,
} from "../lib/discovery";
import { SupportBundleList } from "../components/SupportBundleList";
import type {
  AutonomyEvidenceWorkflowReportFile,
  AutonomyReadinessReportFile,
  Device,
  FieldCollectionPlanCondition,
  FieldCollectionPlanFile,
  FieldEvidenceReportFile,
  FieldEvidenceTemplateFile,
  FeatureMethodBenchmarkReportFile,
  LocalNetworkHint,
  PiDiscoveryCandidate,
  Px4PrereqReportFile,
  Px4ReceiverReportFile,
  RosbagExportValidationReportFile,
  SupportBundleDetails,
  SupportBundleFile,
  ThresholdTuningReportFile,
  UploadProgress,
} from "../lib/types";

const DEFAULT_LOCAL_REPO = "/Users/izzyfisi/Documents/DRONE";
const HOST_SUGGESTIONS = ["dronecompute.local", "raspberrypi.local", "192.168.1.158"];
const SUPPORT_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/support-bundles";
const AUTONOMY_REPORT_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/replay-cases";
const FEATURE_BENCH_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/feature-method-bench";
const PX4_RECEIVER_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/px4-sitl-evidence";
const ROSBAG_VALIDATION_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/terrain-match";
const RUNTIME_STATUS_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/runtime-status";
const DESKTOP_TRANSFER_FROM_PI_DIR = "~/DroneTransfer/from-pi";
const MODULE_SETUP_HANDOFF_KEY = "drone_module_setup_handoff";
const FIELD_CASE_FORM_STORAGE_KEY = "drone_field_case_form";
const SUPPORT_EVIDENCE_ENV =
  'VISION_NAV_PX4_SITL_SESSION="$HOME/px4-sitl-evidence" VISION_NAV_PX4_PARAMS="$HOME/px4.params" VISION_NAV_ARDUPILOT_PARAMS="$HOME/ardupilot.params" ';

type WorkflowValidationSummary = NonNullable<AutonomyEvidenceWorkflowReportFile["workflow_validation_summary"]>;
type WorkflowValidationCheck = WorkflowValidationSummary["checks"][number];
type EvidencePackageArtifact = NonNullable<AutonomyReadinessReportFile["evidence_package_summary"]>["missing_artifacts"][number];
type AutonomyCommandBundle = NonNullable<AutonomyReadinessReportFile["command_bundle"]>;
type FieldCapturePreflightReport = SupportBundleDetails["field_capture_preflight_reports"][number];

type AuthForm = "password" | "key";
type StepStatus = "idle" | "running" | "passed" | "failed";
type FieldExpected = "good_map" | "degraded" | "wrong_map";

const FIELD_EXPECTED_OPTIONS: Array<{ value: FieldExpected; label: string }> = [
  { value: "good_map", label: "Good Map" },
  { value: "degraded", label: "Degraded" },
  { value: "wrong_map", label: "Wrong Map" },
];

const FIELD_CONDITION_OPTIONS = [
  "good_texture",
  "low_texture",
  "blur",
  "seasonal_change",
  "lighting_change",
  "altitude_scale_change",
  "repeated_patterns",
  "wrong_map",
];

interface SetupResult {
  status: StepStatus;
  output?: string;
  exitCode?: number;
}

interface PiForm {
  name: string;
  host: string;
  port: number;
  username: string;
  authMethod: AuthForm;
  password: string;
  keyPath: string;
  passphrase: string;
  remotePath: string;
  mavlinkEndpoint: string;
}

interface FieldCaseForm {
  caseName: string;
  expected: FieldExpected;
  conditions: string;
  fieldLog: string;
  captureOutputDir: string;
  runtimeStatusPath: string;
  siteName: string;
  operator: string;
  captureDateUtc: string;
  locationLabel: string;
  flightAltitudeAglM: string;
  speedMps: string;
  lighting: string;
  weather: string;
  terrainTexture: string;
  mapAgeOrSeasonNotes: string;
  cameraFocusExposureNotes: string;
  imuPx4StateNotes: string;
  safetyNotes: string;
  notes: string;
  replace: boolean;
  strict: boolean;
}

interface SetupStep {
  id: string;
  title: string;
  detail: string;
  command?: () => string;
  requiresSudo?: boolean;
  recommended?: boolean;
  localOnly?: boolean;
}

interface ModuleSetupHandoff {
  version: number;
  source: "mission-planner";
  action: "bench-report" | "field-capture-preflight";
  created_at: string;
  device_id: string;
  device_name?: string;
  remote_bundle_dir?: string;
  local_bundle_dir?: string;
  region_id?: string | null;
  region_name?: string | null;
  plan_fingerprint?: string;
  mission_plan_state?: string;
  built_at?: string | null;
  uploaded_at?: string | null;
}

function shellQuote(value: string) {
  return `'${value.replace(/'/g, "'\"'\"'")}'`;
}

function shellEnvValue(value: string) {
  if (/^\$(HOME|PWD)\//.test(value) && !/[\s"'`;&|<>]/.test(value)) return value;
  return shellQuote(value);
}

function runtimeStatusReadCommand(runtimeStatusRoot?: string) {
  const root = runtimeStatusRoot?.trim();
  return root
    ? `VISION_NAV_RUNTIME_STATUS_ROOTS=${shellEnvValue(root)} ./scripts/pi/read_runtime_status.sh`
    : "./scripts/pi/read_runtime_status.sh";
}

function safeReportName(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "module";
}

function supportBundleDiagnosticsSnapshot(bundle: SupportBundleFile, details: SupportBundleDetails) {
  return {
    name: bundle.name,
    path: bundle.path,
    size_bytes: bundle.size_bytes,
    entry_count: details.entry_count,
    bench_readiness: details.bench_readiness
      ? {
          status: details.bench_readiness.status,
          failed_count: details.bench_readiness.failed_count,
          degraded_count: details.bench_readiness.degraded_count,
          passed_count: details.bench_readiness.passed_count,
          checks: details.bench_readiness.checks.slice(0, 12),
        }
      : null,
    logs: details.logs.slice(0, 8),
    runtime_statuses: details.runtime_statuses.slice(0, 4),
    log_timelines: details.log_timelines.slice(0, 5).map((timeline) => ({
      name: timeline.name,
      path: timeline.path,
      size_bytes: timeline.size_bytes,
      total_records: timeline.total_records,
      invalid_records: timeline.invalid_records,
      accepted_rate: timeline.accepted_rate,
      status_counts: timeline.status_counts,
      reason_counts: timeline.reason_counts,
      external_position_status_counts: timeline.external_position_status_counts,
      first_sequence: timeline.first_sequence,
      last_sequence: timeline.last_sequence,
      first_timestamp_us: timeline.first_timestamp_us,
      last_timestamp_us: timeline.last_timestamp_us,
      average_confidence: timeline.average_confidence,
      average_inliers: timeline.average_inliers,
      average_reprojection_error_px: timeline.average_reprojection_error_px,
      truncated: timeline.truncated,
      segments: timeline.segments.slice(0, 24),
    })),
    artifacts: details.artifacts.slice(0, 24).map((artifact) => ({
      name: artifact.name,
      path: artifact.path,
      kind: artifact.kind,
      size_bytes: artifact.size_bytes,
    })),
    image_artifacts: details.image_previews.slice(0, 8).map((image) => ({
      name: image.name,
      path: image.path,
      mime_type: image.mime_type,
      size_bytes: image.size_bytes,
    })),
    replay_reports: details.replay_reports.slice(0, 12),
    px4_evidence_reports: details.px4_evidence_reports.slice(0, 4),
    px4_param_reports: details.px4_param_reports.slice(0, 4),
    ardupilot_param_reports: details.ardupilot_param_reports.slice(0, 4),
    feature_method_benchmark_reports: details.feature_method_benchmark_reports.slice(0, 4),
    field_evidence_reports: details.field_evidence_reports.slice(0, 4),
    field_collection_plan_reports: details.field_collection_plan_reports.slice(0, 4),
    field_capture_preflight_reports: details.field_capture_preflight_reports.slice(0, 4),
    threshold_tuning_reports: details.threshold_tuning_reports.slice(0, 4),
    rosbag2_cli_review_reports: details.rosbag2_cli_review_reports.slice(0, 4),
  };
}

function parseSupportBundleZip(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_SUPPORT_ZIP__="))
    ?.replace("__VISION_NAV_SUPPORT_ZIP__=", "");
}

function parseBundleDiagnosticReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_BUNDLE_DIAGNOSTIC__="))
    ?.replace("__VISION_NAV_BUNDLE_DIAGNOSTIC__=", "");
}

function parseAutonomyReadinessReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_AUTONOMY_REPORT__="))
    ?.replace("__VISION_NAV_AUTONOMY_REPORT__=", "");
}

function parseAutonomyReadinessHandoff(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_AUTONOMY_HANDOFF__="))
    ?.replace("__VISION_NAV_AUTONOMY_HANDOFF__=", "");
}

function parseAutonomyEvidencePackage(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__="))
    ?.replace("__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__=", "");
}

function parseAutonomyEvidenceWorkflowReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_EVIDENCE_WORKFLOW_REPORT__="))
    ?.replace("__VISION_NAV_EVIDENCE_WORKFLOW_REPORT__=", "");
}

function parseAutonomyEvidenceWorkflowLogs(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__="))
    ?.replace("__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__=", "");
}

function parseAutonomyEvidenceWorkflowValidation(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__="))
    ?.replace("__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__=", "");
}

function parseThresholdTuningReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_THRESHOLD_REPORT__="))
    ?.replace("__VISION_NAV_THRESHOLD_REPORT__=", "");
}

function parseRosbagExportValidationReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_ROSBAG_EXPORT_VALIDATION__="))
    ?.replace("__VISION_NAV_ROSBAG_EXPORT_VALIDATION__=", "");
}

function parseRosbagSourceLog(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_ROSBAG_SOURCE_LOG__="))
    ?.replace("__VISION_NAV_ROSBAG_SOURCE_LOG__=", "");
}

function parseRosbag2CliReviewReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_ROSBAG2_CLI_REVIEW__="))
    ?.replace("__VISION_NAV_ROSBAG2_CLI_REVIEW__=", "");
}

function parseFieldEvidenceReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_FIELD_EVIDENCE_REPORT__="))
    ?.replace("__VISION_NAV_FIELD_EVIDENCE_REPORT__=", "");
}

function parseFieldCapturePreflightReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_FIELD_CAPTURE_PREFLIGHT__="))
    ?.replace("__VISION_NAV_FIELD_CAPTURE_PREFLIGHT__=", "");
}

function parseFieldCapturePreflightJson(text: string): FieldCapturePreflightReport | null {
  try {
    const parsed = JSON.parse(text) as FieldCapturePreflightReport & { schema_version?: string };
    if (parsed.schema_version !== "vision_nav_field_capture_preflight_v1") return null;
    if (!Array.isArray(parsed.checks) || !Array.isArray(parsed.next_actions)) return null;
    return parsed;
  } catch {
    return null;
  }
}

async function readFieldCapturePreflightReport(path: string) {
  try {
    return parseFieldCapturePreflightJson(await readTextFile(path));
  } catch {
    return null;
  }
}

function parseFieldCaptureReady(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_FIELD_CAPTURE_READY__="))
    ?.replace("__VISION_NAV_FIELD_CAPTURE_READY__=", "") === "1";
}

function parseFieldEvidenceTemplate(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_FIELD_TEMPLATE__="))
    ?.replace("__VISION_NAV_FIELD_TEMPLATE__=", "");
}

function parseFieldEvidenceManifest(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_FIELD_MANIFEST__="))
    ?.replace("__VISION_NAV_FIELD_MANIFEST__=", "");
}

function parseFieldCollectionPlan(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_FIELD_COLLECTION_PLAN__="))
    ?.replace("__VISION_NAV_FIELD_COLLECTION_PLAN__=", "");
}

function parseFieldCollectionPlanMarkdown(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_FIELD_COLLECTION_PLAN_MD__="))
    ?.replace("__VISION_NAV_FIELD_COLLECTION_PLAN_MD__=", "");
}

function parseFeatureMethodReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_FEATURE_METHOD_REPORT__="))
    ?.replace("__VISION_NAV_FEATURE_METHOD_REPORT__=", "");
}

function parsePx4SitlReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_PX4_SITL_REPORT__="))
    ?.replace("__VISION_NAV_PX4_SITL_REPORT__=", "");
}

function parsePx4SitlPrereqs(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_PX4_SITL_PREREQS__="))
    ?.replace("__VISION_NAV_PX4_SITL_PREREQS__=", "");
}

function parseRuntimeStatusPath(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_RUNTIME_STATUS__="))
    ?.replace("__VISION_NAV_RUNTIME_STATUS__=", "");
}

function parseTerrainRuntimeLog(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_TERRAIN_LOG__="))
    ?.replace("__VISION_NAV_TERRAIN_LOG__=", "");
}

function parseRuntimeStatusJson(output: string): Record<string, unknown> | null {
  const raw = output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_RUNTIME_STATUS_JSON__="))
    ?.replace("__VISION_NAV_RUNTIME_STATUS_JSON__=", "");
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function runtimeStatusCommand(remoteProject: string) {
  return `cd ${shellQuote(remoteProject)} && ./scripts/pi/read_runtime_status.sh`;
}

function bundleDiagnosticCommand(remoteProject: string, remoteBundle: string) {
  return [
    `cd ${shellQuote(remoteProject)}`,
    `mkdir -p "$HOME/DroneTransfer/outgoing/replay-cases"`,
    `diagnostic="$HOME/DroneTransfer/outgoing/replay-cases/bundle_input_diagnostic_$(date -u +%Y%m%dT%H%M%SZ).json"`,
    `VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} VISION_NAV_BUNDLE_DIAGNOSTIC_JSON="$diagnostic" ./scripts/pi/diagnose_mission_bundle.sh`,
  ].join("; ");
}

function fieldLogCaptureCommand(
  remoteProject: string,
  remoteBundle: string,
  mavlinkEndpoint: string,
  fieldCase: FieldCaseForm,
) {
  const captureOutputDir = fieldCase.captureOutputDir.trim();
  const env = [
    `VISION_NAV_BUNDLE=${shellQuote(remoteBundle)}`,
    captureOutputDir ? `VISION_NAV_OUTPUT_DIR=${shellQuote(captureOutputDir)}` : "",
    "VISION_NAV_COUNT=30",
    "VISION_NAV_INTERVAL_S=1.0",
    mavlinkEndpoint.trim() ? `VISION_NAV_MAVLINK_ENDPOINT=${shellQuote(mavlinkEndpoint.trim())}` : "",
    "VISION_NAV_MAVLINK_MESSAGE=odometry",
  ].filter(Boolean).join(" ");
  const runtimeStatusRoot = captureOutputDir || "$HOME/DroneTransfer/outgoing/terrain-match";
  return `cd ${shellQuote(remoteProject)} && ${env} ./scripts/pi/run_terrain_nav_loop.sh && ${runtimeStatusReadCommand(runtimeStatusRoot)}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function stringField(value: unknown) {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return undefined;
}

function numberField(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function compactUtcNow() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function firstFieldCondition(conditions: string) {
  return conditions.split(/[,\s]+/).map((value) => value.trim()).find(Boolean) ?? "";
}

function parseOptionalFloat(value: string) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function fieldExpected(value?: string): FieldExpected | undefined {
  if (value === "good_map" || value === "degraded" || value === "wrong_map") return value;
  return undefined;
}

function usableFieldText(value: unknown) {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed || trimmed.toLowerCase().startsWith("todo")) return undefined;
  return trimmed;
}

function keepOrUse(current: string, next: unknown) {
  return usableFieldText(current) ?? usableFieldText(next) ?? current;
}

function metadataNumberText(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : undefined;
}

function fieldCaseFromCollectionPlanCondition(
  current: FieldCaseForm,
  plan: FieldCollectionPlanFile,
  condition: FieldCollectionPlanFile["conditions"][number],
): FieldCaseForm {
  const metadata = asRecord(condition.capture_metadata);
  const conditionKey =
    usableFieldText(condition.condition) ??
    usableFieldText(metadata?.condition) ??
    firstFieldCondition(current.conditions);
  const expected =
    fieldExpected(condition.expected) ??
    fieldExpected(stringField(metadata?.expected_behavior)) ??
    current.expected;
  return {
    ...current,
    caseName: usableFieldText(condition.case_name) ?? current.caseName,
    expected,
    conditions: conditionKey || current.conditions,
    fieldLog: usableFieldText(condition.source_log) ?? current.fieldLog,
    captureOutputDir: usableFieldText(condition.capture_output_dir) ?? current.captureOutputDir,
    runtimeStatusPath: usableFieldText(condition.runtime_status_path) ?? current.runtimeStatusPath,
    siteName: keepOrUse(current.siteName, metadata?.site_name ?? plan.site_name),
    operator: keepOrUse(current.operator, metadata?.operator),
    captureDateUtc: keepOrUse(current.captureDateUtc, metadata?.capture_date_utc),
    locationLabel: keepOrUse(current.locationLabel, metadata?.location_label),
    flightAltitudeAglM: current.flightAltitudeAglM || metadataNumberText(metadata?.flight_altitude_agl_m) || "",
    speedMps: current.speedMps || metadataNumberText(metadata?.speed_mps) || "",
    lighting: keepOrUse(current.lighting, metadata?.lighting),
    weather: keepOrUse(current.weather, metadata?.weather),
    terrainTexture: keepOrUse(current.terrainTexture, metadata?.terrain_texture),
    mapAgeOrSeasonNotes: keepOrUse(current.mapAgeOrSeasonNotes, metadata?.map_age_or_season_notes),
    cameraFocusExposureNotes: keepOrUse(current.cameraFocusExposureNotes, metadata?.camera_focus_exposure_notes),
    imuPx4StateNotes: keepOrUse(current.imuPx4StateNotes, metadata?.imu_px4_state_notes),
    safetyNotes: keepOrUse(current.safetyNotes, metadata?.safety_notes),
    notes: usableFieldText(condition.notes) ?? usableFieldText(metadata?.notes) ?? current.notes,
  };
}

function fieldCaptureMetadata(remoteBundle: string, fieldCase: FieldCaseForm) {
  return {
    schema_version: "vision_nav_field_capture_metadata_v1",
    site_name: fieldCase.siteName.trim(),
    condition: firstFieldCondition(fieldCase.conditions),
    expected_behavior: fieldCase.expected,
    bundle: remoteBundle,
    operator: fieldCase.operator.trim(),
    capture_date_utc: fieldCase.captureDateUtc.trim(),
    location_label: fieldCase.locationLabel.trim(),
    flight_altitude_agl_m: parseOptionalFloat(fieldCase.flightAltitudeAglM),
    speed_mps: parseOptionalFloat(fieldCase.speedMps),
    lighting: fieldCase.lighting.trim(),
    weather: fieldCase.weather.trim(),
    terrain_texture: fieldCase.terrainTexture.trim(),
    map_age_or_season_notes: fieldCase.mapAgeOrSeasonNotes.trim(),
    camera_focus_exposure_notes: fieldCase.cameraFocusExposureNotes.trim(),
    imu_px4_state_notes: fieldCase.imuPx4StateNotes.trim(),
    safety_notes: fieldCase.safetyNotes.trim(),
    notes: fieldCase.notes.trim(),
  };
}

function fieldCaptureMetadataIssues(fieldCase: FieldCaseForm) {
  const altitude = parseOptionalFloat(fieldCase.flightAltitudeAglM);
  const speed = parseOptionalFloat(fieldCase.speedMps);
  const requiredText: Array<[string, string]> = [
    ["site", fieldCase.siteName],
    ["operator", fieldCase.operator],
    ["capture UTC", fieldCase.captureDateUtc],
    ["location", fieldCase.locationLabel],
    ["lighting", fieldCase.lighting],
    ["weather", fieldCase.weather],
    ["terrain texture", fieldCase.terrainTexture],
    ["map age", fieldCase.mapAgeOrSeasonNotes],
    ["focus/exposure", fieldCase.cameraFocusExposureNotes],
    ["IMU/PX4 state", fieldCase.imuPx4StateNotes],
    ["safety", fieldCase.safetyNotes],
  ];
  return [
    firstFieldCondition(fieldCase.conditions) ? "" : "condition",
    ...requiredText
      .filter(([, value]) => !value.trim() || value.trim().toLowerCase().startsWith("todo"))
      .map(([label]) => label),
    altitude !== null && altitude > 0 ? "" : "altitude AGL",
    speed !== null && speed >= 0 ? "" : "speed",
  ].filter(Boolean);
}

function fieldCaptureMetadataReady(fieldCase: FieldCaseForm) {
  return fieldCaptureMetadataIssues(fieldCase).length === 0;
}

function readModuleSetupHandoff(): ModuleSetupHandoff | null {
  try {
    const raw = sessionStorage.getItem(MODULE_SETUP_HANDOFF_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ModuleSetupHandoff;
    if (
      parsed.source !== "mission-planner" ||
      !["bench-report", "field-capture-preflight"].includes(parsed.action)
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function benchReportCommand(remoteProject: string, remoteBundle: string, mavlinkEndpoint: string) {
  const mavlinkEnv = mavlinkEndpoint ? `VISION_NAV_MAVLINK_ENDPOINT=${shellQuote(mavlinkEndpoint)} ` : "";
  return [
    `cd ${shellQuote(remoteProject)}`,
    "set +e",
    `VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ./scripts/pi/validate_terrain_bundle.sh`,
    "validate_exit=$?",
    `VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ${mavlinkEnv}${SUPPORT_EVIDENCE_ENV}./scripts/pi/create_support_bundle.sh`,
    "support_exit=$?",
    `latest=$(ls -t "$HOME/DroneTransfer/outgoing/support-bundles/"*.zip 2>/dev/null | head -n 1)`,
    `test -n "$latest" && echo "__VISION_NAV_SUPPORT_ZIP__=$latest"`,
    `if [ "$support_exit" -ne 0 ]; then exit "$support_exit"; fi`,
    `if [ -z "$latest" ]; then exit 1; fi`,
    `exit "$validate_exit"`,
  ].join("; ");
}

function fieldEvidenceCommand(remoteProject: string, remoteBundle: string, fieldCase: FieldCaseForm) {
  const captureMetadata = JSON.stringify(fieldCaptureMetadata(remoteBundle, fieldCase));
  const env = [
    `VISION_NAV_FIELD_CASE_NAME=${shellQuote(fieldCase.caseName)}`,
    `VISION_NAV_FIELD_EXPECTED=${shellQuote(fieldCase.expected)}`,
    `VISION_NAV_FIELD_CONDITIONS=${shellQuote(fieldCase.conditions)}`,
    fieldCase.fieldLog ? `VISION_NAV_FIELD_LOG=${shellQuote(fieldCase.fieldLog)}` : "",
    `VISION_NAV_FIELD_BUNDLE=${shellQuote(remoteBundle)}`,
    `VISION_NAV_FIELD_CAPTURE_METADATA=${shellQuote(captureMetadata)}`,
    fieldCase.notes ? `VISION_NAV_FIELD_NOTES=${shellQuote(fieldCase.notes)}` : "",
    fieldCase.replace ? "VISION_NAV_FIELD_REPLACE=1" : "",
    fieldCase.strict ? "VISION_NAV_FIELD_GATE_STRICT=1" : "",
  ].filter(Boolean).join(" ");
  return `cd ${shellQuote(remoteProject)} && ${env} ./scripts/pi/register_field_replay_case.sh`;
}

function fieldCapturePreflightCommand(remoteProject: string, fieldCase: FieldCaseForm) {
  const condition = firstFieldCondition(fieldCase.conditions);
  const env = condition ? `VISION_NAV_FIELD_CONDITION=${shellQuote(condition)} ` : "";
  return `cd ${shellQuote(remoteProject)} && ${env}./scripts/pi/preflight_field_capture.sh`;
}

function fieldMetadataUpdateCommand(remoteProject: string, remoteBundle: string, fieldCase: FieldCaseForm) {
  const condition = firstFieldCondition(fieldCase.conditions);
  const captureMetadata = JSON.stringify(fieldCaptureMetadata(remoteBundle, fieldCase));
  const env = [
    `VISION_NAV_FIELD_CONDITION=${shellQuote(condition)}`,
    `VISION_NAV_FIELD_CAPTURE_METADATA=${shellQuote(captureMetadata)}`,
  ].join(" ");
  return `cd ${shellQuote(remoteProject)} && ${env} ./scripts/pi/update_field_capture_metadata.sh`;
}

function fieldEvidenceTemplateCommand(remoteProject: string, remoteBundle: string, siteName: string) {
  const env = [
    `VISION_NAV_FIELD_SITE_NAME=${shellQuote(siteName)}`,
    `VISION_NAV_FIELD_BUNDLE=${shellQuote(remoteBundle)}`,
    "VISION_NAV_FIELD_TEMPLATE_FORCE=1",
  ].join(" ");
  return `cd ${shellQuote(remoteProject)} && ${env} ./scripts/pi/create_field_evidence_template.sh`;
}

function fieldCollectionPlanCommand(remoteProject: string, remoteBundle: string, siteName: string) {
  const env = [
    `VISION_NAV_FIELD_SITE_NAME=${shellQuote(siteName)}`,
    `VISION_NAV_FIELD_BUNDLE=${shellQuote(remoteBundle)}`,
  ].join(" ");
  return `cd ${shellQuote(remoteProject)} && ${env} ./scripts/pi/create_field_collection_plan.sh`;
}

function featureMethodBenchmarkCommand(remoteProject: string, remoteBundle: string, fieldCase: FieldCaseForm) {
  const env = [
    `VISION_NAV_FEATURE_BENCH_BUNDLE=${shellQuote(remoteBundle)}`,
    `VISION_NAV_FEATURE_BENCH_CASE_NAME=${shellQuote(fieldCase.caseName || "feature-method-benchmark")}`,
    `VISION_NAV_FEATURE_BENCH_EXPECTED=${shellQuote(fieldCase.expected)}`,
    "VISION_NAV_FEATURE_BENCH_METHODS=orb,akaze,sift,neural",
  ].join(" ");
  return `cd ${shellQuote(remoteProject)} && ${env} ./scripts/pi/run_feature_method_benchmark.sh`;
}

function autonomyEvidenceWorkflowCommand(remoteProject: string, remoteBundle: string, fieldCase: FieldCaseForm) {
  const captureMetadata = JSON.stringify(fieldCaptureMetadata(remoteBundle, fieldCase));
  const includeFieldRegistration = fieldCase.caseName.trim() && firstFieldCondition(fieldCase.conditions) && fieldCaptureMetadataReady(fieldCase);
  const env = [
    `VISION_NAV_BUNDLE=${shellQuote(remoteBundle)}`,
    `VISION_NAV_FIELD_BUNDLE=${shellQuote(remoteBundle)}`,
    `VISION_NAV_FEATURE_BENCH_BUNDLE=${shellQuote(remoteBundle)}`,
    includeFieldRegistration ? `VISION_NAV_FIELD_CASE_NAME=${shellQuote(fieldCase.caseName)}` : "",
    includeFieldRegistration ? `VISION_NAV_FIELD_EXPECTED=${shellQuote(fieldCase.expected)}` : "",
    includeFieldRegistration ? `VISION_NAV_FIELD_CONDITIONS=${shellQuote(fieldCase.conditions)}` : "",
    includeFieldRegistration ? `VISION_NAV_FIELD_CAPTURE_METADATA=${shellQuote(captureMetadata)}` : "",
    includeFieldRegistration && fieldCase.notes ? `VISION_NAV_FIELD_NOTES=${shellQuote(fieldCase.notes)}` : "",
    includeFieldRegistration && fieldCase.replace ? "VISION_NAV_FIELD_REPLACE=1" : "",
    "VISION_NAV_EVIDENCE_WORKFLOW_ALLOW_FAILED=1",
  ].filter(Boolean).join(" ");
  return `cd ${shellQuote(remoteProject)} && ${env} ./scripts/pi/run_autonomy_evidence_workflow.sh`;
}

function defaultRemotePath(username: string) {
  return `/home/${username || "user"}/Drone`;
}

function defaultForm(): PiForm {
  return {
    name: "Raspberry Pi 5",
    host: "",
    port: 22,
    username: "user",
    authMethod: "password",
    password: "",
    keyPath: "",
    passphrase: "",
    remotePath: "/home/user/Drone",
    mavlinkEndpoint: "serial:/dev/ttyAMA0:921600",
  };
}

function defaultFieldCaseForm(): FieldCaseForm {
  return {
    caseName: "field-good-texture",
    expected: "good_map",
    conditions: "good_texture",
    fieldLog: "",
    captureOutputDir: "",
    runtimeStatusPath: "",
    siteName: "",
    operator: "",
    captureDateUtc: compactUtcNow(),
    locationLabel: "",
    flightAltitudeAglM: "",
    speedMps: "",
    lighting: "",
    weather: "",
    terrainTexture: "",
    mapAgeOrSeasonNotes: "",
    cameraFocusExposureNotes: "",
    imuPx4StateNotes: "",
    safetyNotes: "",
    notes: "",
    replace: false,
    strict: false,
  };
}

function loadFieldCaseForm(): FieldCaseForm {
  const fallback = defaultFieldCaseForm();
  if (typeof localStorage === "undefined") return fallback;
  try {
    const raw = localStorage.getItem(FIELD_CASE_FORM_STORAGE_KEY);
    if (!raw) return fallback;
    const saved = JSON.parse(raw) as Partial<FieldCaseForm>;
    return {
      ...fallback,
      ...saved,
      expected:
        saved.expected === "good_map" || saved.expected === "degraded" || saved.expected === "wrong_map"
          ? saved.expected
          : fallback.expected,
      captureDateUtc:
        typeof saved.captureDateUtc === "string" && saved.captureDateUtc.trim()
          ? saved.captureDateUtc
          : fallback.captureDateUtc,
      replace: Boolean(saved.replace),
      strict: Boolean(saved.strict),
    };
  } catch {
    return fallback;
  }
}

function formFromDevice(device: Device): PiForm {
  const username = device.username ?? "user";
  return {
    name: device.name,
    host: device.host ?? "",
    port: device.port ?? 22,
    username,
    authMethod: device.auth?.type === "Key" ? "key" : "password",
    password: device.auth?.type === "Password" ? device.auth.password : "",
    keyPath: device.auth?.type === "Key" ? device.auth.key_path : "",
    passphrase: device.auth?.type === "Key" ? device.auth.passphrase ?? "" : "",
    remotePath: device.remote_project_path ?? defaultRemotePath(username),
    mavlinkEndpoint: device.mavlink_endpoint ?? "serial:/dev/ttyAMA0:921600",
  };
}

function StatusIcon({ status }: { status: StepStatus }) {
  if (status === "running") return <Loader2 size={14} className="animate-spin text-cyan-400" />;
  if (status === "passed") return <CheckCircle2 size={14} className="text-emerald-400" />;
  if (status === "failed") return <XCircle size={14} className="text-red-400" />;
  return <Terminal size={14} className="text-slate-600" />;
}

function readinessBadgeClass(status?: string) {
  if (status === "passed" || status === "healthy" || status === "covered") return "badge-green";
  if (status === "failed" || status === "missing" || status === "error" || status === "blocked") return "badge-red";
  return "badge-yellow";
}

function readinessIcon(status?: string) {
  if (status === "passed" || status === "healthy" || status === "covered") return <CheckCircle2 size={11} />;
  if (status === "failed" || status === "missing" || status === "error" || status === "blocked") return <XCircle size={11} />;
  return <Terminal size={11} />;
}

function formatReadinessLabel(value?: string | number | null) {
  if (value === undefined || value === null || value === "") return "n/a";
  return String(value).replace(/_/g, " ");
}

function evidenceArtifactLabel(artifact: EvidencePackageArtifact) {
  return formatReadinessLabel((artifact.label ?? artifact.path ?? "artifact").replace(/^proof:/, "proof "));
}

function evidenceArtifactDetail(artifact: EvidencePackageArtifact) {
  if (artifact.missing_conditions.length > 0) {
    const missing = artifact.missing_conditions.slice(0, 3).map(formatReadinessLabel).join(", ");
    const extra = artifact.missing_conditions.length > 3 ? ` +${artifact.missing_conditions.length - 3}` : "";
    return `missing ${missing}${extra}`;
  }
  if (artifact.status) return formatReadinessLabel(artifact.status);
  if (artifact.reason) return formatReadinessLabel(artifact.reason);
  return "";
}

function evidenceArtifactTitle(artifact: EvidencePackageArtifact) {
  return [
    artifact.message,
    artifact.reason && `reason: ${formatReadinessLabel(artifact.reason)}`,
    artifact.status && `status: ${formatReadinessLabel(artifact.status)}`,
    artifact.source && `source: ${formatReadinessLabel(artifact.source)}`,
    artifact.path,
    artifact.missing_conditions.length > 0
      ? `missing: ${artifact.missing_conditions.map(formatReadinessLabel).join(", ")}`
      : "",
  ]
    .filter(Boolean)
    .join("\n");
}

function EvidencePackageArtifactPill({
  artifact,
  kind,
  reportPath,
  index,
}: {
  artifact: EvidencePackageArtifact;
  kind: "included" | "missing" | "skipped";
  reportPath: string;
  index: number;
}) {
  const detail = evidenceArtifactDetail(artifact);
  const style =
    kind === "included"
      ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300"
      : kind === "missing"
        ? "border-amber-500/20 bg-amber-500/10 text-amber-300"
        : "border-slate-600/60 bg-slate-900/60 text-slate-400";

  return (
    <span
      key={`${reportPath}-${kind}-artifact-${artifact.label ?? artifact.path ?? index}`}
      className={cn("rounded border px-1.5 py-0.5 font-mono text-[10px]", style)}
      title={evidenceArtifactTitle(artifact) || artifact.label || artifact.path || `${kind} artifact`}
    >
      {kind} {evidenceArtifactLabel(artifact)}
      {detail && <span className="ml-1 opacity-80">{detail}</span>}
    </span>
  );
}

function validationCheckDetail(check: WorkflowValidationCheck) {
  if (check.missing_steps.length > 0) {
    const missing = check.missing_steps.slice(0, 3).map(formatReadinessLabel).join(", ");
    const extra = check.missing_steps.length > 3 ? ` +${check.missing_steps.length - 3}` : "";
    return `missing steps ${missing}${extra}`;
  }
  if ((check.non_passed_count ?? 0) > 0 || check.non_passed_steps.length > 0) {
    const steps = check.non_passed_steps
      .slice(0, 3)
      .map((step) => `${formatReadinessLabel(step.name)}:${formatReadinessLabel(step.status)}`)
      .join(", ");
    const count = check.non_passed_count ?? check.non_passed_steps.length;
    const extra = count > 3 ? ` +${count - 3}` : "";
    return steps ? `steps ${steps}${extra}` : `steps not passed ${count}`;
  }
  if ((check.blocked_count ?? 0) > 0 || (check.blocked_steps ?? []).length > 0) {
    const steps = (check.blocked_steps ?? [])
      .slice(0, 3)
      .map((step) => formatReadinessLabel(step.name))
      .join(", ");
    const count = check.blocked_count ?? (check.blocked_steps ?? []).length;
    const extra = count > 3 ? ` +${count - 3}` : "";
    return steps ? `blocked ${steps}${extra}` : `blocked steps ${count}`;
  }
  if ((check.superseded_steps ?? []).some((step) => step.current_preflight_allows_capture)) {
    return "current preflight capture-ready";
  }
  if ((check.superseded_steps ?? []).some((step) => step.current_selected_condition)) {
    return "field condition selected";
  }
  if (check.missing_markers.length > 0) {
    const missing = check.missing_markers.slice(0, 3).map(formatReadinessLabel).join(", ");
    const extra = check.missing_markers.length > 3 ? ` +${check.missing_markers.length - 3}` : "";
    return `missing ${missing}${extra}`;
  }
  if (check.message) return check.message;
  if (check.marker_count !== undefined) return `markers ${check.marker_count}`;
  return "";
}

function workflowNextStepTitle(nextStep: WorkflowValidationSummary["next_required_step"]) {
  if (!nextStep) return undefined;
  return [
    nextStep.desktop_action,
    nextStep.command,
    nextStep.capture_command_after_bundle ? `after bundle: ${nextStep.capture_command_after_bundle}` : null,
    nextStep.bundle_path ? `bundle: ${nextStep.bundle_path}` : null,
    nextStep.expected_log ? `expected log: ${nextStep.expected_log}` : null,
    nextStep.output_dir ? `output: ${nextStep.output_dir}` : null,
    nextStep.runtime_status_path ? `runtime status: ${nextStep.runtime_status_path}` : null,
    nextStep.capture_script_path ? `capture script: ${nextStep.capture_script_path}` : null,
    nextStep.capture_script_hint ? `capture script hint: ${nextStep.capture_script_hint}` : null,
    nextStep.metadata_update_command,
    nextStep.notes,
  ]
    .filter(Boolean)
    .join("\n") || undefined;
}

function WorkflowValidationSummaryLine({ summary }: { summary: WorkflowValidationSummary }) {
  const highlightedChecks = summary.checks
    .filter((check) => check.status && check.status !== "passed")
    .slice(0, 3);
  const firstIssue = summary.issues[0];
  const nextStep = summary.next_required_step;

  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
      <span className={cn(readinessBadgeClass(summary.status), "text-[10px]")}>
        validation {formatReadinessLabel(summary.status)}
      </span>
      {summary.workflow_status && (
        <span className="font-mono text-slate-600">workflow {formatReadinessLabel(summary.workflow_status)}</span>
      )}
      <span className="font-mono text-slate-600">issues {summary.issue_count}</span>
      <span className="font-mono text-slate-600">active {summary.active_required_step_count ?? 0}</span>
      <span className="font-mono text-slate-600">downstream {summary.downstream_blocked_step_count ?? 0}</span>
      <span className="font-mono text-slate-600">superseded {summary.superseded_step_count ?? 0}</span>
      {nextStep && (
        <span
          className="inline-flex max-w-full items-center gap-1 rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 text-amber-200"
          title={workflowNextStepTitle(nextStep)}
        >
          <span className={cn(readinessBadgeClass(nextStep.status), "text-[9px] px-1 py-0")}>next</span>
          <span className="truncate font-mono">{formatReadinessLabel(nextStep.name)}</span>
          {nextStep.bundle_path && (
            <span className="max-w-[18rem] truncate font-mono text-amber-100/80">bundle {nextStep.bundle_path}</span>
          )}
          {nextStep.capture_script_path && (
            <span className="max-w-[18rem] truncate font-mono text-emerald-100/80">script {nextStep.capture_script_path}</span>
          )}
          {nextStep.capture_script_hint && (
            <span className="max-w-[18rem] truncate font-mono text-amber-100/80">script hint {nextStep.capture_script_hint}</span>
          )}
          {nextStep.command && (
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(nextStep.command ?? "")}
              className="inline-flex h-4 w-4 items-center justify-center rounded border border-amber-400/20 text-amber-200 hover:border-amber-300"
              title="Copy next workflow command"
            >
              <Copy size={9} />
            </button>
          )}
          {nextStep.capture_command_after_bundle && (
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(nextStep.capture_command_after_bundle ?? "")}
              className="inline-flex h-4 w-4 items-center justify-center rounded border border-cyan-400/20 text-cyan-200 hover:border-cyan-300"
              title="Copy field capture command to run after bundle validation"
            >
              <Copy size={9} />
            </button>
          )}
          {nextStep.capture_script_path && (
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(nextStep.capture_script_path ?? "")}
              className="inline-flex h-4 w-4 items-center justify-center rounded border border-emerald-400/20 text-emerald-200 hover:border-emerald-300"
              title="Copy generated field capture script path"
            >
              <Copy size={9} />
            </button>
          )}
          {nextStep.metadata_update_command && nextStep.metadata_update_command !== nextStep.command && (
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(nextStep.metadata_update_command ?? "")}
              className="inline-flex h-4 w-4 items-center justify-center rounded border border-amber-400/20 text-amber-200 hover:border-amber-300"
              title="Copy field metadata update command"
            >
              <Copy size={9} />
            </button>
          )}
        </span>
      )}
      {highlightedChecks.map((check) => {
        const detail = validationCheckDetail(check);
        const nonPassedStepTitle = check.non_passed_steps
          .map((step) =>
            [
              formatReadinessLabel(step.name),
              formatReadinessLabel(step.status),
              step.notes,
              step.workflow_notes ? `Workflow notes: ${step.workflow_notes}` : undefined,
              step.desktop_action ? `App: ${step.desktop_action}` : undefined,
              step.command ? `Command: ${step.command}` : undefined,
              step.bundle_path ? `Bundle: ${step.bundle_path}` : undefined,
              step.expected_log ? `Expected log: ${step.expected_log}` : undefined,
              step.output_dir ? `Output: ${step.output_dir}` : undefined,
              step.runtime_status_path ? `Runtime status: ${step.runtime_status_path}` : undefined,
              step.capture_script_path ? `Capture script: ${step.capture_script_path}` : undefined,
              step.capture_script_hint ? `Capture script hint: ${step.capture_script_hint}` : undefined,
              step.preflight_report ? `Preflight: ${step.preflight_report}` : undefined,
              step.preflight_status ? `Preflight status: ${step.preflight_status}` : undefined,
              step.ready_for_capture !== undefined ? `Ready for capture: ${step.ready_for_capture ? "yes" : "no"}` : undefined,
              step.ready_for_registration !== undefined
                ? `Ready for registration: ${step.ready_for_registration ? "yes" : "no"}`
                : undefined,
              step.metadata_update_command ? `Metadata update: ${step.metadata_update_command}` : undefined,
              step.blocked_by ? `Blocked by: ${step.blocked_by}` : undefined,
              step.required_log ? `Required log: ${step.required_log}` : undefined,
              step.required_runtime_status ? `Required runtime status: ${step.required_runtime_status}` : undefined,
              step.current_selected_condition ? `Selected condition: ${step.current_selected_condition}` : undefined,
              step.current_selected_case ? `Selected case: ${step.current_selected_case}` : undefined,
              step.current_selected_log ? `Selected log: ${step.current_selected_log}` : undefined,
              step.current_preflight_allows_capture
                ? `Current preflight capture-ready (${formatReadinessLabel(step.current_preflight_status)})`
                : undefined,
              step.current_preflight_report ? `Current preflight: ${step.current_preflight_report}` : undefined,
              step.guidance,
            ]
              .filter(Boolean)
              .join(" - "),
          )
          .join("\n");
        const blockedStepTitle = (check.blocked_steps ?? [])
          .map((step) =>
            [
              `Blocked ${formatReadinessLabel(step.name)}`,
              formatReadinessLabel(step.status),
              step.blocked_by ? `Blocked by: ${step.blocked_by}` : undefined,
              step.required_log ? `Required log: ${step.required_log}` : undefined,
              step.required_runtime_status ? `Required runtime status: ${step.required_runtime_status}` : undefined,
              step.notes,
              step.guidance,
            ]
              .filter(Boolean)
              .join(" - "),
          )
          .join("\n");
        const supersededStepTitle = (check.superseded_steps ?? [])
          .map((step) =>
            [
              `Superseded ${formatReadinessLabel(step.name)}`,
              formatReadinessLabel(step.status),
              step.notes,
              step.current_selected_condition ? `Selected condition: ${step.current_selected_condition}` : undefined,
              step.current_selected_case ? `Selected case: ${step.current_selected_case}` : undefined,
              step.current_selected_log ? `Selected log: ${step.current_selected_log}` : undefined,
              step.current_preflight_allows_capture
                ? `Current preflight capture-ready (${formatReadinessLabel(step.current_preflight_status)})`
                : undefined,
              step.current_preflight_report ? `Current preflight: ${step.current_preflight_report}` : undefined,
              step.guidance,
            ]
              .filter(Boolean)
              .join(" - "),
          )
          .join("\n");
        const title = [
          check.message,
          detail,
          ...check.missing_markers,
          ...check.missing_steps,
          nonPassedStepTitle,
          blockedStepTitle,
          supersededStepTitle,
        ]
          .filter(Boolean)
          .join("\n");
        return (
          <span
            key={`${check.name ?? "check"}-${check.status ?? "status"}`}
            className="flex min-w-0 max-w-full items-center gap-1 rounded border border-slate-800 bg-slate-950/50 px-1.5 py-0.5"
            title={title || undefined}
          >
            <span className={cn(readinessBadgeClass(check.status), "text-[9px] px-1 py-0")}>
              {formatReadinessLabel(check.status)}
            </span>
            <span className="truncate font-mono text-slate-500">{formatReadinessLabel(check.name)}</span>
            {detail && <span className="truncate text-slate-500">{detail}</span>}
          </span>
        );
      })}
      {firstIssue && (
        <span className="truncate text-slate-500" title={firstIssue}>
          {firstIssue}
        </span>
      )}
    </div>
  );
}

function shortSha(value?: string) {
  return value ? value.slice(0, 10) : "n/a";
}

function formatReportSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function formatReportTime(ms?: number) {
  if (!ms) return "unknown time";
  return new Date(ms).toLocaleString();
}

function fieldCollectionCommands(
  conditions: FieldCollectionPlanCondition[],
  key:
    | "preflight_command"
    | "preflight_capture_command"
    | "capture_command"
    | "metadata_update_command"
    | "register_command",
) {
  return uniqueCommands(
    conditions
      .filter(fieldCollectionNeedsAction)
      .map((condition) => condition[key]),
  );
}

function fieldCollectionNeedsAction(condition: FieldCollectionPlanCondition) {
  return (
    condition.status !== "registered" ||
    condition.manifest_log_exists === false ||
    !condition.source_log ||
    !condition.capture_output_dir ||
    !condition.runtime_status_path
  );
}

function uniqueCommands(commands: Array<string | undefined>) {
  return Array.from(new Set(commands.filter((command): command is string => Boolean(command))));
}

function uniqueActionCommands(actions: Array<{ command?: string }>) {
  return uniqueCommands(actions.map((action) => action.command));
}

function commandGroupText(
  commandBundle: AutonomyCommandBundle | undefined,
  group: string,
  commands: string[],
) {
  return commands
    .flatMap((command) => {
      const appHint = commandAppHint(commandBundle, group, command);
      return appHint ? [`# app: ${appHint}`, command] : [command];
    })
    .join("\n");
}

function commandAppHint(commandBundle: AutonomyCommandBundle | undefined, group: string, command: string) {
  const explicitHint = (commandBundle?.command_items ?? []).find(
    (item) => item.group === group && item.command === command && item.desktop_action,
  )?.desktop_action;
  if (explicitHint) return explicitHint;
  return FIELD_COLLECTION_APP_HINTS[group];
}

const FIELD_COLLECTION_APP_HINTS: Record<string, string> = {
  field_collection_preflight: "Module Setup > Field Capture Preflight",
  field_collection_preflight_capture: "Module Setup > Field Capture Preflight, then Field Log Capture",
  field_collection_capture: "Module Setup > Field Log Capture",
  field_collection_metadata_update: "Module Setup > Field Evidence Case > Update Metadata",
  field_collection_registration: "Module Setup > Field Evidence Case > Register",
};

function fieldCollectionWorkflowText(
  conditions: FieldCollectionPlanCondition[],
  commandBundle?: AutonomyCommandBundle,
) {
  return conditions
    .filter(fieldCollectionNeedsAction)
    .flatMap((condition, index) => {
      const label = condition.label ?? formatReadinessLabel(condition.condition) ?? "Field condition";
      const title = condition.condition ? `${label} (${condition.condition})` : label;
      const lines = [`# ${index + 1}. ${title}`];
      if (condition.status) lines.push(`# status: ${formatReadinessLabel(condition.status)}`);
      if (condition.expected) lines.push(`# expected: ${formatReadinessLabel(condition.expected)}`);
      if (condition.capture_output_dir) lines.push(`# capture output: ${condition.capture_output_dir}`);
      if (condition.source_log) lines.push(`# terrain log: ${condition.source_log}`);
      if (condition.runtime_status_path) lines.push(`# runtime status: ${condition.runtime_status_path}`);
      for (const [group, command] of [
        ["field_collection_preflight", condition.preflight_command],
        ["field_collection_capture", condition.capture_command],
        ["field_collection_metadata_update", condition.metadata_update_command],
        ["field_collection_registration", condition.register_command],
      ] as const) {
        if (!command) continue;
        const appHint = commandAppHint(commandBundle, group, command);
        if (appHint) lines.push(`# app: ${appHint}`);
        lines.push(command);
      }
      return lines;
    })
    .join("\n\n");
}

function benchEvidenceOrderText(
  actions: Array<{
    label?: string;
    desktop_action?: string;
    command?: string;
    blocked_by?: string;
    notes?: string;
  }>,
) {
  return actions
    .flatMap((action, index) => {
      const lines = [`# ${index + 1}. ${action.label ?? action.desktop_action ?? "Bench evidence step"}`];
      if (action.desktop_action) lines.push(`# app: ${action.desktop_action}`);
      if (action.blocked_by) lines.push(`# waits on: ${formatReadinessLabel(action.blocked_by)}`);
      if (action.notes) lines.push(`# notes: ${action.notes}`);
      if (action.command) lines.push(action.command);
      return lines;
    })
    .join("\n\n");
}

function workflowMarkerArtifacts(report: AutonomyEvidenceWorkflowReportFile) {
  return [
    { label: "logs", path: report.workflow_logs_local_path ?? report.workflow_logs_path },
    { label: "validation", path: report.workflow_validation_local_path ?? report.workflow_validation_path },
    { label: "support", path: report.support_bundle_local_path ?? report.support_bundle_path },
    { label: "field", path: report.field_evidence_report_local_path ?? report.field_evidence_report_path },
    { label: "feature", path: report.feature_method_report_local_path ?? report.feature_method_report_path },
    { label: "thresholds", path: report.threshold_report_local_path ?? report.threshold_report_path },
    { label: "rosbag", path: report.rosbag_validation_local_path ?? report.rosbag_validation_path },
    { label: "readiness", path: report.readiness_report_local_path ?? report.readiness_report_path },
    { label: "handoff", path: report.handoff_local_path ?? report.handoff_path },
    { label: "package", path: report.evidence_package_local_path ?? report.evidence_package_path },
    { label: "plan", path: report.field_collection_plan_local_path ?? report.field_collection_plan_path },
    {
      label: "checklist",
      path: report.field_collection_plan_markdown_local_path ?? report.field_collection_plan_markdown_path,
    },
    { label: "px4", path: report.px4_receiver_report_local_path ?? report.px4_receiver_report_path },
    { label: "px4 prereqs", path: report.px4_prereq_report_local_path ?? report.px4_prereq_report_path },
  ].filter((artifact): artifact is { label: string; path: string } => Boolean(artifact.path));
}

function workflowMarkerArtifactText(artifacts: Array<{ label: string; path: string }>) {
  return artifacts.map((artifact) => `${artifact.label}: ${artifact.path}`).join("\n");
}

function FieldCollectionConditionBadge({
  condition,
  idPrefix,
}: {
  condition: {
    condition?: string;
    label?: string;
    status?: string;
    case_name?: string;
    capture_command?: string;
    metadata_update_command?: string;
    register_command?: string;
    preflight_command?: string;
  };
  idPrefix: string;
}) {
  const label = formatReadinessLabel(condition.condition ?? condition.label);
  const status = formatReadinessLabel(condition.status);
  const key = `${idPrefix}-${condition.condition ?? condition.label ?? condition.case_name}`;
  return (
    <span
      key={key}
      className={cn(readinessBadgeClass(condition.status), "text-[10px] gap-1")}
      title={condition.case_name ?? condition.condition ?? "pending condition"}
    >
      <span>{label}</span>
      <span>{status}</span>
      {condition.preflight_command && (
        <button
          type="button"
          onClick={() => navigator.clipboard.writeText(condition.preflight_command ?? "")}
          className="text-cyan-300 hover:text-cyan-100"
          title={`Copy preflight command: ${condition.preflight_command}`}
        >
          pre
        </button>
      )}
      {condition.capture_command && (
        <button
          type="button"
          onClick={() => navigator.clipboard.writeText(condition.capture_command ?? "")}
          className="text-cyan-300 hover:text-cyan-100"
          title={`Copy capture command: ${condition.capture_command}`}
        >
          cap
        </button>
      )}
      {condition.metadata_update_command && (
        <button
          type="button"
          onClick={() => navigator.clipboard.writeText(condition.metadata_update_command ?? "")}
          className="text-cyan-300 hover:text-cyan-100"
          title={`Copy metadata update command: ${condition.metadata_update_command}`}
        >
          <FileText size={9} />
        </button>
      )}
      {condition.register_command && (
        <button
          type="button"
          onClick={() => navigator.clipboard.writeText(condition.register_command ?? "")}
          className="text-cyan-300 hover:text-cyan-100"
          title={`Copy register command: ${condition.register_command}`}
        >
          <Copy size={9} />
        </button>
      )}
    </span>
  );
}

function RuntimeStatusCard({
  status,
  remotePath,
  localPath,
  onRefresh,
  busy,
  disabled,
}: {
  status: Record<string, unknown> | null;
  remotePath: string | null;
  localPath: string | null;
  onRefresh: () => void;
  busy: boolean;
  disabled: boolean;
}) {
  const activeMap = asRecord(status?.["active_map"]);
  const lastMatch = asRecord(status?.["last_match"]);
  const estimator = asRecord(status?.["estimator"]);
  const external = asRecord(status?.["external_position_health"]);
  const latestFrame = asRecord(status?.["latest_frame"]);
  const statusCounts = asRecord(status?.["status_counts"]);
  const mapLabel = stringField(activeMap?.["bundle_id"]) || stringField(activeMap?.["map_id"]) || "n/a";
  const matchStatus = stringField(lastMatch?.["status"]);
  const matchReason = stringField(lastMatch?.["reason"]);
  const confidence = numberField(lastMatch?.["confidence"]);
  const tileId = stringField(lastMatch?.["tile_id"]);
  const estimatorHealth = stringField(estimator?.["health"]) || stringField(estimator?.["status"]);
  const externalStatus = stringField(external?.["status"]);
  const messageType = stringField(external?.["message_type"]);
  const sequence = numberField(latestFrame?.["sequence"]);
  const acceptedCount = numberField(statusCounts?.["accepted"]);
  const rejectedCount = numberField(statusCounts?.["rejected"]);

  return (
    <div className="rounded-lg border border-border bg-bg-card p-3 space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-slate-200 flex items-center gap-2">
            <Server size={14} className="text-cyan-400" /> Runtime Status
          </div>
          <p className="text-[11px] text-slate-500 mt-0.5">Fetch the current terrain runtime snapshot from the module.</p>
        </div>
        <button onClick={onRefresh} disabled={disabled || busy} className="btn-secondary text-xs py-1 px-3">
          {busy ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
          Refresh
        </button>
      </div>
      {status ? (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-border bg-bg-base px-3 py-2">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">Last match</div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                <span className={readinessBadgeClass(matchStatus)}>
                  {readinessIcon(matchStatus)}
                  {formatReadinessLabel(matchStatus)}
                </span>
                {confidence !== undefined && <span className="font-mono text-[10px] text-slate-500">conf {confidence.toFixed(2)}</span>}
              </div>
              <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">{matchReason || tileId || "no match detail"}</div>
            </div>
            <div className="rounded-lg border border-border bg-bg-base px-3 py-2">
              <div className="text-[10px] uppercase tracking-wide text-slate-500">Health</div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                <span className={readinessBadgeClass(estimatorHealth)}>{formatReadinessLabel(estimatorHealth)}</span>
                <span className={readinessBadgeClass(externalStatus)}>{formatReadinessLabel(externalStatus)}</span>
              </div>
              <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">{messageType || "external output n/a"}</div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">Active map</span>
              <span className="text-slate-300 truncate">{mapLabel}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">Frame</span>
              <span className="font-mono text-slate-300">{sequence ?? "n/a"}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">Accepted</span>
              <span className="font-mono text-slate-300">{acceptedCount ?? 0}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">Rejected</span>
              <span className="font-mono text-slate-300">{rejectedCount ?? 0}</span>
            </div>
          </div>
          {(remotePath || localPath) && (
            <div className="space-y-1 border-t border-border pt-2">
              {remotePath && <div className="font-mono text-[10px] text-slate-500 truncate">remote {remotePath}</div>}
              {localPath && <div className="font-mono text-[10px] text-slate-500 truncate">saved {localPath}</div>}
            </div>
          )}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No runtime status fetched yet.
        </div>
      )}
    </div>
  );
}

function AutonomyReadinessReportList({
  reports,
  downloadDir,
  onRefresh,
}: {
  reports: AutonomyReadinessReportFile[];
  downloadDir: string;
  onRefresh: () => void;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reveal = async (path: string) => {
    setBusyPath(path);
    setActionError(null);
    try {
      await cmd.revealSupportBundle(path);
    } catch (err) {
      setActionError(String(err));
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-slate-300 flex items-center gap-2">
            <ShieldCheck size={13} className="text-cyan-400" /> Autonomy Readiness Reports
          </h4>
          <p className="text-[10px] text-slate-500 font-mono truncate">{downloadDir}</p>
        </div>
        <button onClick={onRefresh} className="btn-secondary text-xs py-1 px-2">
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>
      {actionError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}
      {reports.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No downloaded autonomy readiness reports yet.
        </div>
      ) : (
        <div className="space-y-2">
          {reports.slice(0, 4).map((report) => {
            const commandBundle = report.command_bundle ?? report.evidence_package_summary?.command_bundle;
            const fieldPlanPreflightCommands = uniqueCommands([
              ...fieldCollectionCommands(report.field_collection_plan?.pending_conditions ?? [], "preflight_command"),
              ...(commandBundle?.field_collection_preflight_commands ?? []),
            ]);
            const fieldPlanPreflightCaptureCommands = uniqueCommands([
              ...fieldCollectionCommands(
                report.field_collection_plan?.pending_conditions ?? [],
                "preflight_capture_command",
              ),
              ...(commandBundle?.field_collection_preflight_capture_commands ?? []),
            ]);
            const fieldPlanCaptureCommands = uniqueCommands([
              ...fieldCollectionCommands(report.field_collection_plan?.pending_conditions ?? [], "capture_command"),
              ...(commandBundle?.field_collection_capture_commands ?? []),
            ]);
            const fieldPlanMetadataUpdateCommands = uniqueCommands([
              ...fieldCollectionCommands(report.field_collection_plan?.pending_conditions ?? [], "metadata_update_command"),
              ...(commandBundle?.field_collection_metadata_update_commands ?? []),
            ]);
            const fieldPlanRegisterCommands = uniqueCommands([
              ...fieldCollectionCommands(report.field_collection_plan?.pending_conditions ?? [], "register_command"),
              ...(commandBundle?.field_collection_registration_commands ?? []),
            ]);
            const nextActionCommands = uniqueCommands([
              ...uniqueActionCommands(report.next_actions),
              ...(commandBundle?.next_action_commands ?? []),
            ]);
            const immediateNextActionCommands = uniqueCommands(
              commandBundle?.immediate_next_action_commands ?? [],
            );
            const prerequisiteFixCommands = uniqueCommands(commandBundle?.prerequisite_fix_commands ?? []);
            const blockedFollowUpCommands = uniqueCommands(commandBundle?.blocked_follow_up_commands ?? []);
            const primaryNextActionCommands =
              immediateNextActionCommands.length > 0 ? immediateNextActionCommands : nextActionCommands;
            const primaryNextActionGroup =
              immediateNextActionCommands.length > 0 ? "immediate_next_action" : "next_action";
            const guidedWorkflowCommands = uniqueCommands(commandBundle?.guided_workflow_commands ?? []);
            const supportBenchBlocker = [
              ...(report.evidence_manifest?.external_blockers ?? []),
              ...(report.evidence_manifest?.completion_blockers ?? []),
              ...(report.evidence_manifest?.proof_items ?? []),
              ...(report.evidence_package_summary?.proof_items ?? []),
            ].find(
              (blocker) =>
                blocker.name === "support_bundle_bench_readiness" &&
                ((blocker.expected_bench_inputs?.length ?? 0) > 0 ||
                  Boolean(blocker.support_bundle_command) ||
                  (blocker.bench_evidence_actions?.length ?? 0) > 0)
            );
            const benchExpectedInputs = supportBenchBlocker?.expected_bench_inputs ?? [];
            const benchEvidenceActions = supportBenchBlocker?.bench_evidence_actions ?? [];
            const readinessWorkflowArtifacts = [
              {
                label: "workflow",
                path: report.workflow_report_local_path ?? report.workflow_report_path,
                localPath: report.workflow_report_local_path,
              },
              {
                label: "validation",
                path: report.workflow_validation_local_path ?? report.workflow_validation_path,
                localPath: report.workflow_validation_local_path,
              },
              {
                label: "logs",
                path: report.workflow_log_archive_local_path ?? report.workflow_log_archive_path,
                localPath: report.workflow_log_archive_local_path,
              },
            ].flatMap((artifact) =>
              artifact.path
                ? [{ label: artifact.label, path: artifact.path, localPath: artifact.localPath }]
                : []
            );
            const planSnapshot = report.plan_snapshot ?? report.evidence_package_summary?.plan_snapshot;
            const researchSnapshot = planSnapshot?.research_doc;
            const implementationSnapshot = planSnapshot?.implementation_plan;
            const proofRunbook = report.proof_runbook ?? report.evidence_package_summary?.proof_runbook_summary;
            const auditMetadata = report.metadata ?? report.evidence_package_summary?.readiness_report_metadata;
            const auditRepo = auditMetadata?.repo;
            return (
            <div key={report.path} className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={readinessBadgeClass(report.summary.status)}>
                      {readinessIcon(report.summary.status)}
                      {formatReadinessLabel(report.summary.status)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      pass {report.summary.passed_count ?? 0}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      degrade {report.summary.degraded_count ?? 0}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      fail {report.summary.failed_count ?? 0}
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                    {report.name} / {formatReportSize(report.size_bytes)} / {formatReportTime(report.modified_unix_ms)}
                  </div>
                  {report.handoff_path && (
                    <div className="mt-1 font-mono text-[10px] text-slate-600 truncate">
                      handoff {formatReportSize(report.handoff_size_bytes ?? 0)} / {formatReportTime(report.handoff_modified_unix_ms)}
                    </div>
                  )}
                  {report.evidence_package_path && (
                    <div className="mt-1 font-mono text-[10px] text-slate-600 truncate">
                      evidence package {formatReportSize(report.evidence_package_size_bytes ?? 0)} /{" "}
                      {formatReportTime(report.evidence_package_modified_unix_ms)}
                    </div>
                  )}
                  {auditMetadata && (
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-slate-500">
                      <span>audit {auditMetadata.generated_at_utc ?? "n/a"}</span>
                      {auditRepo?.branch && <span>branch {auditRepo.branch}</span>}
                      {auditRepo?.commit && <span>commit {shortSha(auditRepo.commit)}</span>}
                      {typeof auditRepo?.dirty === "boolean" && (
                        <span className={auditRepo.dirty ? "text-amber-300" : "text-emerald-300"}>
                          {auditRepo.dirty ? "dirty" : "clean"}
                        </span>
                      )}
                    </div>
                  )}
                  {report.evidence_package_summary && (
                    <div className="mt-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-1.5 text-[10px] font-mono text-slate-500">
                        <span className={cn(readinessBadgeClass(report.evidence_package_summary.readiness_status), "text-[10px]")}>
                          {formatReadinessLabel(report.evidence_package_summary.readiness_status)}
                        </span>
                        <span>included {report.evidence_package_summary.included_count ?? 0}</span>
                        <span>missing {report.evidence_package_summary.missing_count ?? 0}</span>
                        <span>skipped {report.evidence_package_summary.skipped_count ?? 0}</span>
                        {typeof report.evidence_package_summary.proof_item_count === "number" && (
                          <span>
                            proof {report.evidence_package_summary.proof_item_passed_count ?? 0}/
                            {report.evidence_package_summary.proof_item_count}
                          </span>
                        )}
                        {typeof report.evidence_package_summary.external_blocker_count === "number" && (
                          <span>external blockers {report.evidence_package_summary.external_blocker_count}</span>
                        )}
                      </div>
                      {report.evidence_package_summary.workflow_validation_summary && (
                        <WorkflowValidationSummaryLine summary={report.evidence_package_summary.workflow_validation_summary} />
                      )}
                      {report.evidence_package_summary.proof_items.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {report.evidence_package_summary.proof_items.slice(0, 4).map((item, index) => (
                            <span
                              key={`${report.path}-package-proof-${item.name ?? index}`}
                              className="rounded border border-sky-500/20 bg-sky-500/10 px-1.5 py-0.5 font-mono text-[10px] text-sky-200"
                              title={item.message ?? item.name ?? "proof item"}
                            >
                              {formatReadinessLabel(item.name ?? "proof")} {formatReadinessLabel(item.status)}
                            </span>
                          ))}
                        </div>
                      )}
                      {report.evidence_package_summary.included_artifacts.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {report.evidence_package_summary.included_artifacts.slice(0, 4).map((artifact, index) => (
                            <EvidencePackageArtifactPill
                              key={`${report.path}-included-artifact-${artifact.label ?? artifact.path ?? index}`}
                              artifact={artifact}
                              kind="included"
                              reportPath={report.path}
                              index={index}
                            />
                          ))}
                        </div>
                      )}
                      {report.evidence_package_summary.missing_artifacts.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {report.evidence_package_summary.missing_artifacts.slice(0, 4).map((artifact, index) => (
                            <EvidencePackageArtifactPill
                              key={`${report.path}-missing-artifact-${artifact.label ?? artifact.path ?? index}`}
                              artifact={artifact}
                              kind="missing"
                              reportPath={report.path}
                              index={index}
                            />
                          ))}
                        </div>
                      )}
                      {report.evidence_package_summary.skipped_artifacts.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {report.evidence_package_summary.skipped_artifacts.slice(0, 4).map((artifact, index) => (
                            <EvidencePackageArtifactPill
                              key={`${report.path}-skipped-artifact-${artifact.label ?? artifact.path ?? index}`}
                              artifact={artifact}
                              kind="skipped"
                              reportPath={report.path}
                              index={index}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {readinessWorkflowArtifacts.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {readinessWorkflowArtifacts.map((artifact) => (
                        <span
                          key={`${report.path}-workflow-artifact-${artifact.label}`}
                          className="inline-flex items-center gap-1 rounded border border-cyan-500/20 bg-cyan-500/10 px-1.5 py-0.5 font-mono text-[10px] text-cyan-200"
                          title={artifact.path}
                        >
                          {artifact.label}
                          <button
                            type="button"
                            onClick={() => navigator.clipboard.writeText(artifact.path)}
                            className="text-cyan-300 hover:text-cyan-100"
                            title={`Copy ${artifact.label} path`}
                          >
                            <Copy size={9} />
                          </button>
                          {artifact.localPath && (
                            <button
                              type="button"
                              onClick={() => reveal(artifact.localPath ?? artifact.path)}
                              disabled={busyPath === artifact.localPath}
                              className="text-cyan-300 hover:text-cyan-100 disabled:text-slate-600"
                              title={`Show ${artifact.label} artifact`}
                            >
                              {busyPath === artifact.localPath ? (
                                <Loader2 size={9} className="animate-spin" />
                              ) : (
                                <FolderOpen size={9} />
                              )}
                            </button>
                          )}
                        </span>
                      ))}
                    </div>
                  )}
                  {report.field_collection_plan && (
                    <div className="mt-1 rounded-md border border-border bg-slate-950/30 px-2 py-1.5 space-y-1">
                      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                        <span className={cn(readinessBadgeClass(report.field_collection_plan.status), "text-[10px]")}>
                          {readinessIcon(report.field_collection_plan.status)}
                          {formatReadinessLabel(report.field_collection_plan.status)}
                        </span>
                        <span className="font-mono text-slate-500">field collection plan</span>
                        <span className="font-mono text-slate-500">
                          registered {report.field_collection_plan.summary.registered_count ?? 0}/
                          {report.field_collection_plan.summary.required_count ?? 0}
                        </span>
                        <span className="font-mono text-slate-500">
                          pending {report.field_collection_plan.pending_conditions.length}
                        </span>
                        {report.field_collection_plan.pending_conditions.length > 0 && (
                          <button
                            type="button"
                            onClick={() =>
                              navigator.clipboard.writeText(
                                fieldCollectionWorkflowText(
                                  report.field_collection_plan?.pending_conditions ?? [],
                                  commandBundle,
                                ),
                              )
                            }
                            className="btn-secondary px-1.5 py-0.5 text-[10px]"
                            title="Copy pending field conditions in capture, metadata, register order"
                          >
                            <Copy size={9} />
                            workflow
                          </button>
                        )}
                        {fieldPlanPreflightCommands.length > 0 && (
                          <button
                            type="button"
                            onClick={() =>
                              navigator.clipboard.writeText(
                                commandGroupText(
                                  commandBundle,
                                  "field_collection_preflight",
                                  fieldPlanPreflightCommands,
                                ),
                              )
                            }
                            className="btn-secondary px-1.5 py-0.5 text-[10px]"
                            title="Copy pending field capture preflight commands"
                          >
                            <Copy size={9} />
                            preflight
                          </button>
                        )}
                        {fieldPlanPreflightCaptureCommands.length > 0 && (
                          <button
                            type="button"
                            onClick={() =>
                              navigator.clipboard.writeText(
                                commandGroupText(
                                  commandBundle,
                                  "field_collection_preflight_capture",
                                  fieldPlanPreflightCaptureCommands,
                                ),
                              )
                            }
                            className="btn-secondary px-1.5 py-0.5 text-[10px]"
                            title="Copy pending preflight plus capture commands"
                          >
                            <Copy size={9} />
                            preflight + capture
                          </button>
                        )}
                        {fieldPlanCaptureCommands.length > 0 && (
                          <button
                            type="button"
                            onClick={() =>
                              navigator.clipboard.writeText(
                                commandGroupText(commandBundle, "field_collection_capture", fieldPlanCaptureCommands),
                              )
                            }
                            className="btn-secondary px-1.5 py-0.5 text-[10px]"
                            title="Copy pending capture commands"
                          >
                            <Copy size={9} />
                            capture
                          </button>
                        )}
                        {fieldPlanMetadataUpdateCommands.length > 0 && (
                          <button
                            type="button"
                            onClick={() =>
                              navigator.clipboard.writeText(
                                commandGroupText(
                                  commandBundle,
                                  "field_collection_metadata_update",
                                  fieldPlanMetadataUpdateCommands,
                                ),
                              )
                            }
                            className="btn-secondary px-1.5 py-0.5 text-[10px]"
                            title="Copy pending metadata update commands"
                          >
                            <Copy size={9} />
                            metadata
                          </button>
                        )}
                        {fieldPlanRegisterCommands.length > 0 && (
                          <button
                            type="button"
                            onClick={() =>
                              navigator.clipboard.writeText(
                                commandGroupText(
                                  commandBundle,
                                  "field_collection_registration",
                                  fieldPlanRegisterCommands,
                                ),
                              )
                            }
                            className="btn-secondary px-1.5 py-0.5 text-[10px]"
                            title="Copy pending registration commands"
                          >
                            <Copy size={9} />
                            register
                          </button>
                        )}
                      </div>
                          <div className="font-mono text-[10px] text-slate-600 truncate" title={report.field_collection_plan.path}>
                            {formatReadinessLabel(report.field_collection_plan.site_name)} / {report.field_collection_plan.path}
                          </div>
                          {report.field_collection_plan.next_condition && (
                            <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                              <span className="font-mono text-slate-500">next</span>
                              <FieldCollectionConditionBadge
                                condition={report.field_collection_plan.next_condition}
                                idPrefix={`${report.path}-field-plan-next`}
                              />
                            </div>
                          )}
                          {report.field_collection_plan.pending_conditions.length > 0 && (
                            <div className="flex flex-wrap gap-1">
                          {report.field_collection_plan.pending_conditions.slice(0, 8).map((condition, index) => (
                            <FieldCollectionConditionBadge
                              key={`${report.path}-field-plan-${condition.condition ?? condition.label ?? index}`}
                              condition={condition}
                              idPrefix={`${report.path}-field-plan`}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => navigator.clipboard.writeText(report.path)}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Copy report path"
                  >
                    <Copy size={11} />
                  </button>
                  <button
                    onClick={() => reveal(report.path)}
                    disabled={busyPath === report.path}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Show report file"
                  >
                    {busyPath === report.path ? <Loader2 size={11} className="animate-spin" /> : <FolderOpen size={11} />}
                  </button>
                  {report.handoff_path && (
                    <>
                      <button
                        onClick={() => navigator.clipboard.writeText(report.handoff_path ?? "")}
                        className="btn-secondary text-xs py-1 px-2"
                        title="Copy handoff path"
                      >
                        <Copy size={11} />
                      </button>
                      <button
                        onClick={() => reveal(report.handoff_path ?? "")}
                        disabled={busyPath === report.handoff_path}
                        className="btn-secondary text-xs py-1 px-2"
                        title="Show handoff file"
                      >
                        {busyPath === report.handoff_path ? <Loader2 size={11} className="animate-spin" /> : <FileText size={11} />}
                      </button>
                    </>
                  )}
                  {report.evidence_package_path && (
                    <>
                      <button
                        onClick={() => navigator.clipboard.writeText(report.evidence_package_path ?? "")}
                        className="btn-secondary text-xs py-1 px-2"
                        title="Copy evidence package path"
                      >
                        <Copy size={11} />
                      </button>
                      <button
                        onClick={() => reveal(report.evidence_package_path ?? "")}
                        disabled={busyPath === report.evidence_package_path}
                        className="btn-secondary text-xs py-1 px-2"
                        title="Show evidence package"
                      >
                        {busyPath === report.evidence_package_path ? (
                          <Loader2 size={11} className="animate-spin" />
                        ) : (
                          <Archive size={11} />
                        )}
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                {[
                  ["support", report.summary.support_bundle_bench_readiness_status],
                  ["px4", report.summary.px4_receiver_proof_status],
                  ["plan", report.summary.field_collection_plan_status],
                  ["field", report.summary.field_evidence_proof_status],
                  ["features", report.summary.feature_method_benchmark_status],
                  ["thresholds", report.summary.threshold_tuning_status],
                  ["rosbag", report.summary.rosbag_export_validation_status],
                  ["rosbag2", report.summary.rosbag2_cli_review_status],
                ].map(([label, status]) => (
                  <div key={label} className="flex items-center gap-1.5 font-mono text-[10px] text-slate-500">
                    <span className={cn(readinessBadgeClass(status), "text-[10px]")}>
                      {formatReadinessLabel(status)}
                    </span>
                    <span>{label}</span>
                  </div>
                ))}
              </div>
              {planSnapshot && (
                <div className="rounded-md border border-border bg-slate-950/30 px-2 py-1.5 space-y-1">
                  <div className="flex flex-wrap items-center gap-1.5 text-[10px] font-mono text-slate-500">
                    <span className={readinessBadgeClass(
                      (researchSnapshot?.missing_markers.length ?? 0) === 0 &&
                        (implementationSnapshot?.missing_markers.length ?? 0) === 0
                        ? "passed"
                        : "failed"
                    )}>
                      plan snapshot
                    </span>
                    {researchSnapshot && (
                      <>
                        <span>research markers {(researchSnapshot.required_marker_count ?? 0) - researchSnapshot.missing_markers.length}/{researchSnapshot.required_marker_count ?? 0}</span>
                        <span>refs {researchSnapshot.highest_value_reference_count ?? 0}</span>
                        <span>near-term {researchSnapshot.near_term_item_count ?? 0}</span>
                        <span>research sha {shortSha(researchSnapshot.source_sha256)}</span>
                      </>
                    )}
                    {implementationSnapshot && (
                      <>
                        <span>tracks {implementationSnapshot.track_count ?? 0}</span>
                        <span>done {implementationSnapshot.done_count ?? 0}</span>
                        <span>tasks {implementationSnapshot.task_count ?? 0}</span>
                        <span>plan sha {shortSha(implementationSnapshot.source_sha256)}</span>
                      </>
                    )}
                  </div>
                  {[
                    ...(researchSnapshot?.missing_markers ?? []),
                    ...(implementationSnapshot?.missing_markers ?? []),
                  ].length > 0 && (
                    <div className="font-mono text-[10px] text-amber-300">
                      missing markers{" "}
                      {[
                        ...(researchSnapshot?.missing_markers ?? []),
                        ...(implementationSnapshot?.missing_markers ?? []),
                      ].slice(0, 4).join(", ")}
                    </div>
                  )}
                </div>
              )}
              {proofRunbook && <ProofRunbookPanel runbook={proofRunbook} idPrefix={report.path} />}
              {report.evidence_manifest && (
                <div className="rounded-md border border-border bg-slate-950/30 px-2 py-1.5 space-y-1">
                  <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                    <span
                      className={readinessBadgeClass(
                        report.evidence_manifest.ready_for_goal_completion ? "passed" : report.summary.status
                      )}
                    >
                      {report.evidence_manifest.ready_for_goal_completion ? <CheckCircle2 size={10} /> : <XCircle size={10} />}
                      {report.evidence_manifest.ready_for_goal_completion ? "ready" : "waiting"}
                    </span>
                    <span className="font-mono text-slate-500">goal completion proof</span>
                    <span className="font-mono text-slate-500">
                      external blockers {report.evidence_manifest.external_blockers.length}
                    </span>
                    <span className="font-mono text-slate-500">
                      proof {report.evidence_manifest.proof_items.filter((item) => item.status === "passed").length}/
                      {report.evidence_manifest.proof_items.length}
                    </span>
                  </div>
                  {report.evidence_manifest.proof_items.length > 0 && (
                    <div className="grid grid-cols-2 gap-1">
                      {report.evidence_manifest.proof_items.slice(0, 6).map((item) => (
                        <div key={`${report.path}-proof-${item.name}`} className="flex items-center gap-1.5 text-[10px] min-w-0">
                          <span className={cn(readinessBadgeClass(item.status), "text-[10px] shrink-0")}>
                            {formatReadinessLabel(item.status)}
                          </span>
                          <span className="font-mono text-slate-500 truncate">{formatReadinessLabel(item.name)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {report.evidence_manifest.external_blockers.length > 0 && (
                    <div className="space-y-1">
                      {report.evidence_manifest.external_blockers.slice(0, 3).map((blocker) => (
                        <div key={`${report.path}-external-${blocker.name}`} className="flex flex-wrap items-center gap-1.5 text-[10px]">
                          <span className={cn(readinessBadgeClass(blocker.status), "text-[10px]")}>
                            {formatReadinessLabel(blocker.status)}
                          </span>
                          <span className="font-mono text-slate-500">{formatReadinessLabel(blocker.name)}</span>
                          {blocker.missing_conditions.length > 0 && (
                            <span className="text-slate-500">
                              missing {blocker.missing_conditions.slice(0, 3).map(formatReadinessLabel).join(", ")}
                            </span>
                          )}
                          {blocker.bench_subchecks.length > 0 && (
                            <span className="text-slate-500">
                              bench {blocker.bench_subchecks.slice(0, 3).map((item) => formatReadinessLabel(item.name)).join(", ")}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {(benchExpectedInputs.length > 0 ||
                Boolean(supportBenchBlocker?.support_bundle_command) ||
                benchEvidenceActions.length > 0) && (
                <div className="rounded-md border border-cyan-500/20 bg-cyan-500/5 px-2 py-1.5 space-y-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[10px] font-medium uppercase tracking-wide text-cyan-300">
                      Bench evidence order
                    </span>
                    {benchEvidenceActions.length > 0 && (
                      <button
                        type="button"
                        onClick={() => navigator.clipboard.writeText(benchEvidenceOrderText(benchEvidenceActions))}
                        className="btn-secondary px-1.5 py-0.5 text-[10px]"
                        title="Copy the full bench evidence order with app routes and commands"
                      >
                        <Copy size={9} />
                        order
                      </button>
                    )}
                    {supportBenchBlocker?.support_bundle_command && (
                      <button
                        type="button"
                        onClick={() => navigator.clipboard.writeText(supportBenchBlocker.support_bundle_command ?? "")}
                        className="btn-secondary px-1.5 py-0.5 text-[10px]"
                        title="Copy support bundle command"
                      >
                        <Copy size={9} />
                        bundle
                      </button>
                    )}
                  </div>
                  {benchExpectedInputs.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {benchExpectedInputs.slice(0, 12).map((input) => (
                        <span key={`${report.path}-bench-input-${input}`} className="font-mono text-[10px] text-slate-500">
                          {input}
                        </span>
                      ))}
                    </div>
                  )}
                  {benchEvidenceActions.slice(0, 14).map((action, index) => (
                    <div key={`${report.path}-bench-action-${action.label ?? action.command ?? index}`} className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-1 text-[10px]">
                      <span className="font-mono text-cyan-400">{index + 1}</span>
                      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                        <span className="text-slate-300">{action.label ?? action.desktop_action ?? "Bench evidence step"}</span>
                        {action.desktop_action && (
                          <span className="font-mono text-slate-500">{action.desktop_action}</span>
                        )}
                        {action.blocked_by && (
                          <span className="text-slate-500">waits on {formatReadinessLabel(action.blocked_by)}</span>
                        )}
                        {action.command && (
                          <button
                            type="button"
                            onClick={() => navigator.clipboard.writeText(action.command ?? "")}
                            className="font-mono text-[10px] text-cyan-400 hover:text-cyan-300 truncate max-w-full"
                            title="Copy bench evidence command"
                          >
                            {action.command}
                          </button>
                        )}
                        {action.notes && <span className="text-slate-500">{action.notes}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {report.checks.slice(0, 5).map((check) => (
                <div key={`${report.path}-${check.name}`} className="flex flex-wrap items-center gap-1.5 text-[10px]">
                  <span className={cn(readinessBadgeClass(check.status), "text-[10px]")}>
                    {readinessIcon(check.status)}
                    {formatReadinessLabel(check.status)}
                  </span>
                  <span className="font-mono text-slate-500">{formatReadinessLabel(check.name)}</span>
                  {check.message && <span className="text-slate-400 truncate">{check.message}</span>}
                </div>
              ))}
              {(
                guidedWorkflowCommands.length > 0 ||
                prerequisiteFixCommands.length > 0 ||
                report.next_actions.length > 0 ||
                nextActionCommands.length > 0 ||
                blockedFollowUpCommands.length > 0
              ) && (
                <div className="space-y-1 border-t border-border pt-2">
                  {guidedWorkflowCommands.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-cyan-500/20 bg-cyan-500/5 px-2 py-1">
                      <span className="text-[10px] font-medium uppercase tracking-wide text-cyan-300">
                        Guided workflow
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          navigator.clipboard.writeText(
                            commandGroupText(commandBundle, "guided_workflow", guidedWorkflowCommands),
                          )
                        }
                        className="font-mono text-[10px] text-cyan-400 hover:text-cyan-300 truncate max-w-full"
                        title="Copy guided evidence workflow command"
                      >
                        {guidedWorkflowCommands[0]}
                      </button>
                    </div>
                  )}
                  <div className="flex flex-wrap items-center gap-1.5">
                    <div className="text-[10px] font-medium uppercase tracking-wide text-slate-500">
                      Next actions
                    </div>
                    {primaryNextActionCommands.length > 0 && (
                      <button
                        type="button"
                        onClick={() =>
                          navigator.clipboard.writeText(
                            commandGroupText(commandBundle, primaryNextActionGroup, primaryNextActionCommands),
                          )
                        }
                        className="btn-secondary px-1.5 py-0.5 text-[10px]"
                        title={
                          immediateNextActionCommands.length > 0
                            ? "Copy immediately runnable next-action commands"
                            : "Copy next-action commands"
                        }
                      >
                        <Copy size={9} />
                        {immediateNextActionCommands.length > 0 ? "immediate" : "commands"}
                      </button>
                    )}
                    {prerequisiteFixCommands.length > 0 && (
                      <button
                        type="button"
                        onClick={() =>
                          navigator.clipboard.writeText(
                            commandGroupText(commandBundle, "prerequisite_fix", prerequisiteFixCommands),
                          )
                        }
                        className="btn-secondary px-1.5 py-0.5 text-[10px]"
                        title="Copy setup prerequisite fix commands"
                      >
                        <Copy size={9} />
                        prereq fixes
                      </button>
                    )}
                    {blockedFollowUpCommands.length > 0 && (
                      <button
                        type="button"
                        onClick={() =>
                          navigator.clipboard.writeText(
                            commandGroupText(commandBundle, "blocked_follow_up", blockedFollowUpCommands),
                          )
                        }
                        className="btn-secondary px-1.5 py-0.5 text-[10px]"
                        title="Copy commands that are blocked until upstream proof is captured"
                      >
                        <Copy size={9} />
                        blocked
                      </button>
                    )}
                  </div>
                  {report.next_actions.slice(0, 4).map((action) => (
                    <div key={`${report.path}-${action.check}`} className="space-y-1">
                      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                        <span className={cn(readinessBadgeClass(action.status), "text-[10px]")}>
                          {formatReadinessLabel(action.status)}
                        </span>
                        <span className="text-slate-300">{action.title}</span>
                        {action.desktop_action && (
                          <span className="font-mono text-slate-500">{action.desktop_action}</span>
                        )}
                      </div>
                      {action.command && (
                        <button
                          onClick={() => navigator.clipboard.writeText(action.command ?? "")}
                          className="font-mono text-[10px] text-cyan-400 hover:text-cyan-300 truncate max-w-full"
                          title="Copy command"
                        >
                          {action.command}
                        </button>
                      )}
                      {action.missing_conditions.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {action.missing_conditions.slice(0, 8).map((condition) => (
                            <span key={`${report.path}-${action.check}-${condition}`} className="font-mono text-[10px] text-slate-500">
                              {formatReadinessLabel(condition)}
                            </span>
                          ))}
                        </div>
                      )}
                      {action.bench_subchecks && action.bench_subchecks.length > 0 && (
                        <div className="space-y-1">
                          {action.bench_subchecks.slice(0, 6).map((subcheck) => (
                            <div key={`${report.path}-${action.check}-${subcheck.name}`} className="flex flex-wrap items-center gap-1.5 text-[10px]">
                              <span className={cn(readinessBadgeClass(subcheck.status), "text-[10px]")}>
                                {formatReadinessLabel(subcheck.status)}
                              </span>
                              <span className="font-mono text-slate-500">{formatReadinessLabel(subcheck.name)}</span>
                              {subcheck.message && <span className="text-slate-500 truncate">{subcheck.message}</span>}
                            </div>
                          ))}
                        </div>
                      )}
                      {action.bench_subcheck && (
                        <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                          <span className="font-mono text-slate-500">{formatReadinessLabel(action.bench_subcheck)}</span>
                          {action.bench_message && <span className="text-slate-500 truncate">{action.bench_message}</span>}
                        </div>
                      )}
                      {action.notes && <div className="text-[10px] text-slate-500">{action.notes}</div>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
          })}
        </div>
      )}
    </div>
  );
}

function ProofRunbookPanel({
  runbook,
  idPrefix,
}: {
  runbook: NonNullable<AutonomyReadinessReportFile["proof_runbook"]>;
  idPrefix: string;
}) {
  const phaseCommands = uniqueCommands(runbook.phases.flatMap((phase) => phase.commands));
  return (
    <div className="rounded-md border border-border bg-slate-950/30 px-2 py-1.5 space-y-1">
      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
        <span className={readinessBadgeClass(runbook.ready_for_goal_completion ? "passed" : "degraded")}>
          proof runbook
        </span>
        <span className="font-mono text-slate-500">passed {runbook.summary.passed ?? 0}</span>
        <span className="font-mono text-slate-500">action {runbook.summary.action_required ?? 0}</span>
        <span className="font-mono text-slate-500">blocked {runbook.summary.blocked ?? 0}</span>
        {runbook.phases_truncated && <span className="font-mono text-amber-300">truncated</span>}
        {phaseCommands.length > 0 && (
          <button
            type="button"
            onClick={() => navigator.clipboard.writeText(phaseCommands.join("\n"))}
            className="btn-secondary px-1.5 py-0.5 text-[10px]"
            title="Copy proof-runbook commands"
          >
            <Copy size={9} />
            commands
          </button>
        )}
      </div>
      <div className="space-y-1">
        {runbook.phases.slice(0, 6).map((phase, index) => (
          <div key={`${idPrefix}-runbook-${phase.id ?? index}`} className="space-y-1">
            <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
              <span className={cn(readinessBadgeClass(phase.status), "text-[10px]")}>
                {formatReadinessLabel(phase.status)}
              </span>
              <span className="font-mono text-slate-300">{formatReadinessLabel(phase.title ?? phase.id)}</span>
              {phase.depends_on.length > 0 && (
                <span className="font-mono text-slate-500">
                  after{" "}
                  {phase.depends_on
                    .slice(0, 3)
                    .map((dependency) => {
                      const status = phase.dependency_status[dependency];
                      return status ? `${formatReadinessLabel(dependency)}:${formatReadinessLabel(status)}` : formatReadinessLabel(dependency);
                    })
                    .join(", ")}
                </span>
              )}
            </div>
            <div className="flex flex-wrap gap-1">
              {phase.checks.slice(0, 5).map((check, checkIndex) => (
                <span
                  key={`${idPrefix}-runbook-${phase.id ?? index}-check-${check.name ?? checkIndex}`}
                  className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
                  title={check.message ?? check.name ?? "proof check"}
                >
                  <span className={cn(readinessBadgeClass(check.status), "text-[9px] px-1 py-0")}>
                    {formatReadinessLabel(check.status)}
                  </span>
                  {formatReadinessLabel(check.name)}
                </span>
              ))}
            </div>
            {phase.commands.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {phase.commands.slice(0, 2).map((command) => (
                  <button
                    key={`${idPrefix}-runbook-${phase.id ?? index}-${command}`}
                    type="button"
                    onClick={() => navigator.clipboard.writeText(command)}
                    className="font-mono text-[10px] text-cyan-400 hover:text-cyan-300 truncate max-w-full"
                    title="Copy phase command"
                  >
                    {command}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function AutonomyEvidenceWorkflowReportList({
  reports,
  downloadDir,
  onRefresh,
}: {
  reports: AutonomyEvidenceWorkflowReportFile[];
  downloadDir: string;
  onRefresh: () => void;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reveal = async (path: string) => {
    setBusyPath(path);
    setActionError(null);
    try {
      await cmd.revealSupportBundle(path);
    } catch (err) {
      setActionError(String(err));
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-slate-300 flex items-center gap-2">
            <Archive size={13} className="text-cyan-400" /> Evidence Workflow Reports
          </h4>
          <p className="text-[10px] text-slate-500 font-mono truncate">{downloadDir}</p>
        </div>
        <button onClick={onRefresh} className="btn-secondary text-xs py-1 px-2">
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>
      {actionError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}
      {reports.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No downloaded evidence workflow reports yet.
        </div>
      ) : (
        <div className="space-y-2">
          {reports.slice(0, 4).map((report) => {
            const markerArtifacts = workflowMarkerArtifacts(report);
            return (
            <div key={report.path} className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={readinessBadgeClass(report.status)}>
                      {readinessIcon(report.status)}
                      {formatReadinessLabel(report.status)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      pass {report.summary.passed ?? 0}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      fail {report.summary.failed ?? 0}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      skip {report.summary.skipped ?? 0}
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                    {report.name} / {formatReportSize(report.size_bytes)} / {formatReportTime(report.modified_unix_ms)}
                  </div>
                  {report.generated_at && (
                    <div className="mt-1 font-mono text-[10px] text-slate-600 truncate">
                      generated {report.generated_at}
                    </div>
                  )}
                  {report.workflow_validation_summary && (
                    <WorkflowValidationSummaryLine summary={report.workflow_validation_summary} />
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => navigator.clipboard.writeText(report.path)}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Copy workflow report path"
                  >
                    <Copy size={11} />
                  </button>
                  <button
                    onClick={() => reveal(report.path)}
                    disabled={busyPath === report.path}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Show workflow report"
                  >
                    {busyPath === report.path ? <Loader2 size={11} className="animate-spin" /> : <FolderOpen size={11} />}
                  </button>
                </div>
              </div>
              <div className="space-y-1">
                {report.steps.slice(0, 6).map((step) => (
                  <div key={`${report.path}-${step.name}`} className="flex flex-wrap items-center gap-1.5 text-[10px]">
                    <span className={cn(readinessBadgeClass(step.status), "text-[10px]")}>
                      {formatReadinessLabel(step.status)}
                    </span>
                    <span className="font-mono text-slate-500">{formatReadinessLabel(step.name)}</span>
                    {typeof step.exit_code === "number" && (
                      <span className="font-mono text-slate-600">exit {step.exit_code}</span>
                    )}
                    {step.notes && <span className="text-slate-500 truncate">{step.notes}</span>}
                  </div>
                ))}
              </div>
              <div className="flex flex-wrap gap-1.5 text-[10px] font-mono text-slate-500">
                <span>markers {report.marker_count}</span>
                {report.field_metadata_update_command && (
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(report.field_metadata_update_command ?? "")}
                    className="inline-flex items-center gap-1 rounded border border-cyan-500/30 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] text-cyan-300 hover:border-cyan-400/60 hover:text-cyan-100"
                    title={`Copy field metadata update command: ${report.field_metadata_update_command}`}
                  >
                    <Copy size={9} />
                    metadata
                  </button>
                )}
                {markerArtifacts.length > 1 && (
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(workflowMarkerArtifactText(markerArtifacts))}
                    className="inline-flex items-center gap-1 rounded border border-border bg-slate-950/30 px-1.5 py-0.5 text-[10px] text-slate-400 hover:border-cyan-500/50 hover:text-cyan-300"
                    title="Copy all emitted artifact paths"
                  >
                    <Copy size={9} />
                    all
                  </button>
                )}
                {markerArtifacts.map((artifact) => (
                  <button
                    key={`${report.path}-${artifact.label}`}
                    type="button"
                    onClick={() => navigator.clipboard.writeText(artifact.path)}
                    className="inline-flex items-center gap-1 rounded border border-border bg-slate-950/30 px-1.5 py-0.5 text-[10px] text-slate-400 hover:border-cyan-500/50 hover:text-cyan-300"
                    title={`Copy emitted ${artifact.label} artifact path`}
                  >
                    <Copy size={9} />
                    {artifact.label}
                  </button>
                ))}
              </div>
            </div>
          );
          })}
        </div>
      )}
    </div>
  );
}

function FieldEvidenceReportList({
  reports,
  downloadDir,
  onRefresh,
}: {
  reports: FieldEvidenceReportFile[];
  downloadDir: string;
  onRefresh: () => void;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reveal = async (path: string) => {
    setBusyPath(path);
    setActionError(null);
    try {
      await cmd.revealSupportBundle(path);
    } catch (err) {
      setActionError(String(err));
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-slate-300 flex items-center gap-2">
            <TestTube2 size={13} className="text-cyan-400" /> Field Evidence Coverage
          </h4>
          <p className="text-[10px] text-slate-500 font-mono truncate">{downloadDir}</p>
        </div>
        <button onClick={onRefresh} className="btn-secondary text-xs py-1 px-2">
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>
      {actionError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}
      {reports.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No downloaded field evidence report yet.
        </div>
      ) : (
        <div className="space-y-2">
          {reports.slice(0, 3).map((file) => {
            const covered = Array.isArray(file.report.covered_conditions) ? file.report.covered_conditions.length : 0;
            const required = Array.isArray(file.report.required_conditions) ? file.report.required_conditions.length : file.report.requirements.length;
            return (
              <div key={file.path} className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className={readinessBadgeClass(file.report.status)}>
                        {readinessIcon(file.report.status)}
                        {formatReadinessLabel(file.report.status)}
                      </span>
                      <span className="font-mono text-[10px] text-slate-500">
                        coverage {formatReadinessLabel(file.report.coverage_status)}
                      </span>
                      <span className="font-mono text-[10px] text-slate-500">
                        replay {formatReadinessLabel(file.report.replay_status)}
                      </span>
                      <span className="font-mono text-[10px] text-slate-500">
                        {covered}/{required} covered
                      </span>
                      {(file.report.capture_metadata_issue_count ?? 0) > 0 && (
                        <span className="font-mono text-[10px] text-red-300">
                          metadata issues {file.report.capture_metadata_issue_count}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                      {file.name} / {formatReportSize(file.size_bytes)} / {formatReportTime(file.modified_unix_ms)}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => navigator.clipboard.writeText(file.path)}
                      className="btn-secondary text-xs py-1 px-2"
                      title="Copy report path"
                    >
                      <Copy size={11} />
                    </button>
                    <button
                      onClick={() => reveal(file.path)}
                      disabled={busyPath === file.path}
                      className="btn-secondary text-xs py-1 px-2"
                      title="Show report file"
                    >
                      {busyPath === file.path ? <Loader2 size={11} className="animate-spin" /> : <FolderOpen size={11} />}
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-1.5">
                  {file.report.requirements.map((requirement) => (
                    <div key={`${file.path}-${requirement.key}`} className="flex items-center gap-1.5 font-mono text-[10px] text-slate-500">
                      <span className={cn(readinessBadgeClass(requirement.status), "text-[10px]")}>
                        {formatReadinessLabel(requirement.status)}
                      </span>
                      <span className="truncate">{formatReadinessLabel(requirement.key)}</span>
                      <span className="text-slate-600">
                        {requirement.field_case_count ?? 0}/{requirement.case_count ?? 0}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function FieldEvidenceTemplateList({
  templates,
  downloadDir,
  onRefresh,
}: {
  templates: FieldEvidenceTemplateFile[];
  downloadDir: string;
  onRefresh: () => void;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reveal = async (path: string) => {
    setBusyPath(path);
    setActionError(null);
    try {
      await cmd.revealSupportBundle(path);
    } catch (err) {
      setActionError(String(err));
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-slate-300 flex items-center gap-2">
            <FileText size={13} className="text-cyan-400" /> Field Evidence Templates
          </h4>
          <p className="text-[10px] text-slate-500 font-mono truncate">{downloadDir}</p>
        </div>
        <button onClick={onRefresh} className="btn-secondary text-xs py-1 px-2">
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>
      {actionError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}
      {templates.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No downloaded field evidence template yet.
        </div>
      ) : (
        <div className="space-y-2">
          {templates.slice(0, 3).map((file) => {
            const required = file.required_conditions.length || file.conditions.length;
            return (
              <div key={file.path} className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-[10px] text-cyan-300">{file.case_count} cases</span>
                      <span className="font-mono text-[10px] text-slate-500">{file.placeholder_count} placeholders</span>
                      <span className="font-mono text-[10px] text-slate-500">{file.conditions.length}/{required} conditions</span>
                      {file.site_name && <span className="font-mono text-[10px] text-slate-500">{file.site_name}</span>}
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                      {file.name} / {formatReportSize(file.size_bytes)} / {formatReportTime(file.modified_unix_ms)}
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500 truncate">{file.path}</div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => navigator.clipboard.writeText(file.path)}
                      className="btn-secondary text-xs py-1 px-2"
                      title="Copy template path"
                    >
                      <Copy size={11} />
                    </button>
                    <button
                      onClick={() => reveal(file.path)}
                      disabled={busyPath === file.path}
                      className="btn-secondary text-xs py-1 px-2"
                      title="Show template file"
                    >
                      {busyPath === file.path ? <Loader2 size={11} className="animate-spin" /> : <FolderOpen size={11} />}
                    </button>
                  </div>
                </div>
                {file.required_conditions.length > 0 && (
                  <div className="space-y-1.5">
                    {file.placeholder_conditions.length > 0 && (
                      <div>
                        <div className="text-[10px] uppercase tracking-wide text-slate-600 mb-1">Remaining placeholders</div>
                        <div className="flex flex-wrap gap-1">
                          {file.placeholder_conditions.map((condition) => (
                            <span key={`${file.path}-placeholder-${condition}`} className="rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-mono text-amber-300">
                              {condition}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {file.registered_conditions.length > 0 && (
                      <div>
                        <div className="text-[10px] uppercase tracking-wide text-slate-600 mb-1">Registered logs</div>
                        <div className="flex flex-wrap gap-1">
                          {file.registered_conditions.map((condition) => (
                            <span key={`${file.path}-registered-${condition}`} className="rounded border border-emerald-500/20 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-mono text-emerald-300">
                              {condition}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {file.placeholder_conditions.length === 0 && file.registered_conditions.length === 0 && (
                      <div className="flex flex-wrap gap-1">
                        {file.required_conditions.map((condition) => (
                          <span key={`${file.path}-${condition}`} className="rounded border border-border bg-bg-base px-1.5 py-0.5 text-[10px] font-mono text-slate-500">
                            {condition}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function formatAcceptedRate(value?: number) {
  return value == null ? "n/a" : `${Math.round(value * 100)}%`;
}

function FieldCollectionPlanList({
  plans,
  downloadDir,
  onRefresh,
  onLoadCondition,
}: {
  plans: FieldCollectionPlanFile[];
  downloadDir: string;
  onRefresh: () => void;
  onLoadCondition: (plan: FieldCollectionPlanFile, condition: FieldCollectionPlanCondition) => void;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reveal = async (path: string) => {
    setBusyPath(path);
    setActionError(null);
    try {
      await cmd.revealSupportBundle(path);
    } catch (err) {
      setActionError(String(err));
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-slate-300 flex items-center gap-2">
            <FileText size={13} className="text-cyan-400" /> Field Collection Plans
          </h4>
          <p className="text-[10px] text-slate-500 font-mono truncate">{downloadDir}</p>
        </div>
        <button onClick={onRefresh} className="btn-secondary text-xs py-1 px-2">
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>
      {actionError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}
      {plans.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No downloaded field collection plan yet.
        </div>
      ) : (
        <div className="space-y-2">
          {plans.slice(0, 3).map((file) => {
            const registered = file.summary.registered_count ?? 0;
            const required = file.summary.required_count ?? file.conditions.length;
            const remaining =
              (file.summary.placeholder_count ?? 0) +
              (file.summary.missing_count ?? 0) +
              (file.summary.registered_missing_log_count ?? 0);
            const revealPath = file.markdown_path ?? file.path;
            const preflightCommands = fieldCollectionCommands(file.conditions, "preflight_command");
            const captureCommands = fieldCollectionCommands(file.conditions, "capture_command");
            const metadataUpdateCommands = fieldCollectionCommands(file.conditions, "metadata_update_command");
            const registerCommands = fieldCollectionCommands(file.conditions, "register_command");
            return (
              <div key={file.path} className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className={readinessBadgeClass(file.status)}>
                        {readinessIcon(file.status)}
                        {formatReadinessLabel(file.status)}
                      </span>
                      <span className="font-mono text-[10px] text-slate-500">
                        registered {registered}/{required}
                      </span>
                      <span className="font-mono text-[10px] text-slate-500">
                        remaining {remaining}
                      </span>
                      {file.site_name && <span className="font-mono text-[10px] text-slate-500">{file.site_name}</span>}
                      {file.markdown_path && <span className="font-mono text-[10px] text-cyan-400">markdown</span>}
                      {file.conditions.some(fieldCollectionNeedsAction) && (
                        <button
                          type="button"
                          onClick={() => navigator.clipboard.writeText(fieldCollectionWorkflowText(file.conditions))}
                          className="btn-secondary px-1.5 py-0.5 text-[10px]"
                          title="Copy remaining field conditions in capture, metadata, register order"
                        >
                          <Copy size={9} />
                          workflow
                        </button>
                      )}
                      {preflightCommands.length > 0 && (
                        <button
                          type="button"
                          onClick={() => navigator.clipboard.writeText(preflightCommands.join("\n"))}
                          className="btn-secondary px-1.5 py-0.5 text-[10px]"
                          title="Copy pending field capture preflight commands"
                        >
                          <Copy size={9} />
                          preflight
                        </button>
                      )}
                      {captureCommands.length > 0 && (
                        <button
                          type="button"
                          onClick={() => navigator.clipboard.writeText(captureCommands.join("\n"))}
                          className="btn-secondary px-1.5 py-0.5 text-[10px]"
                          title="Copy pending capture commands"
                        >
                          <Copy size={9} />
                          capture
                        </button>
                      )}
                      {metadataUpdateCommands.length > 0 && (
                        <button
                          type="button"
                          onClick={() => navigator.clipboard.writeText(metadataUpdateCommands.join("\n"))}
                          className="btn-secondary px-1.5 py-0.5 text-[10px]"
                          title="Copy pending metadata update commands"
                        >
                          <Copy size={9} />
                          metadata
                        </button>
                      )}
                      {registerCommands.length > 0 && (
                        <button
                          type="button"
                          onClick={() => navigator.clipboard.writeText(registerCommands.join("\n"))}
                          className="btn-secondary px-1.5 py-0.5 text-[10px]"
                          title="Copy pending registration commands"
                        >
                          <Copy size={9} />
                          register
                        </button>
                      )}
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                      {file.name} / {formatReportSize(file.size_bytes)} / {formatReportTime(file.modified_unix_ms)}
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500 truncate">{file.path}</div>
                    {file.next_condition && (
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px]">
                        <span className="font-mono text-slate-500">next</span>
                        <FieldCollectionConditionBadge condition={file.next_condition} idPrefix={`${file.path}-next`} />
                        <button
                          type="button"
                          onClick={() => file.next_condition && onLoadCondition(file, file.next_condition)}
                          className="btn-secondary px-1.5 py-0.5 text-[10px]"
                          title="Load the next pending condition into Field Evidence Case"
                        >
                          Load
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => navigator.clipboard.writeText(file.path)}
                      className="btn-secondary text-xs py-1 px-2"
                      title="Copy plan path"
                    >
                      <Copy size={11} />
                    </button>
                    {file.markdown_path && (
                      <button
                        onClick={() => navigator.clipboard.writeText(file.markdown_path ?? "")}
                        className="btn-secondary text-xs py-1 px-2"
                        title="Copy Markdown checklist path"
                      >
                        MD
                      </button>
                    )}
                    <button
                      onClick={() => reveal(revealPath)}
                      disabled={busyPath === revealPath}
                      className="btn-secondary text-xs py-1 px-2"
                      title="Show collection plan"
                    >
                      {busyPath === revealPath ? <Loader2 size={11} className="animate-spin" /> : <FolderOpen size={11} />}
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-1.5">
                  {file.conditions.slice(0, 8).map((condition) => (
                    <div key={`${file.path}-${condition.condition}`} className="flex min-w-0 items-center gap-1">
                      <FieldCollectionConditionBadge condition={condition} idPrefix={file.path} />
                      {fieldCollectionNeedsAction(condition) && (
                        <button
                          type="button"
                          onClick={() => onLoadCondition(file, condition)}
                          className="btn-secondary px-1.5 py-0.5 text-[10px] shrink-0"
                          title="Load this condition into Field Evidence Case"
                        >
                          Load
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function formatPosition(value: unknown) {
  if (!Array.isArray(value)) return "pos n/a";
  return value
    .slice(0, 3)
    .map((item) => (typeof item === "number" ? item.toFixed(1) : String(item)))
    .join(", ");
}

function Px4PrereqReportList({
  reports,
  downloadDir,
  onRefresh,
}: {
  reports: Px4PrereqReportFile[];
  downloadDir: string;
  onRefresh: () => void;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reveal = async (path: string) => {
    setBusyPath(path);
    setActionError(null);
    try {
      await cmd.revealSupportBundle(path);
    } catch (err) {
      setActionError(String(err));
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-slate-300 flex items-center gap-2">
            <AlertTriangle size={13} className="text-amber-400" /> PX4 Capture Prerequisites
          </h4>
          <p className="text-[10px] text-slate-500 font-mono truncate">{downloadDir}</p>
        </div>
        <button onClick={onRefresh} className="btn-secondary text-xs py-1 px-2">
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>
      {actionError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}
      {reports.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No PX4 prerequisite diagnostic report yet.
        </div>
      ) : (
        <div className="space-y-2">
          {reports.slice(0, 3).map((file) => (
            <div key={file.path} className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={readinessBadgeClass(file.report.status)}>
                      {readinessIcon(file.report.status)}
                      {formatReadinessLabel(file.report.status)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      checks {file.report.checks.length}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500 truncate">
                      {formatReadinessLabel(file.report.px4_target)}
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                    {file.name} / {formatReportSize(file.size_bytes)} / {formatReportTime(file.modified_unix_ms)}
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-600 truncate">
                    session {file.report.session_dir ?? "n/a"}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => navigator.clipboard.writeText(file.path)}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Copy prerequisite report path"
                  >
                    <Copy size={11} />
                  </button>
                  <button
                    onClick={() => reveal(file.path)}
                    disabled={busyPath === file.path}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Show prerequisite report file"
                  >
                    {busyPath === file.path ? <Loader2 size={11} className="animate-spin" /> : <FolderOpen size={11} />}
                  </button>
                </div>
              </div>
              <div className="space-y-1">
                {file.report.checks.slice(0, 5).map((check, index) => (
                  <div key={`${file.path}-${check.name ?? index}`} className="flex flex-wrap items-center gap-1.5 text-[10px]">
                    <span className={cn(readinessBadgeClass(check.status), "text-[10px]")}>
                      {formatReadinessLabel(check.status)}
                    </span>
                    <span className="font-mono text-slate-500">{formatReadinessLabel(check.name)}</span>
                    {check.message && <span className="text-slate-500 truncate">{check.message}</span>}
                  </div>
                ))}
              </div>
              {file.report.next_actions.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(file.report.next_actions.join("\n"))}
                    className="btn-secondary px-1.5 py-0.5 text-[10px]"
                    title="Copy PX4 capture prerequisite next actions"
                  >
                    <Copy size={9} />
                    next actions
                  </button>
                </div>
              )}
              {(file.report.fix_commands ?? []).length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    onClick={() =>
                      navigator.clipboard.writeText(
                        (file.report.fix_commands ?? [])
                          .map((fix) => fix.command)
                          .filter((command): command is string => Boolean(command))
                          .join("\n"),
                      )
                    }
                    className="btn-secondary px-1.5 py-0.5 text-[10px]"
                    title="Copy PX4 prerequisite fix commands"
                  >
                    <Terminal size={9} />
                    fix commands
                  </button>
                  {(file.report.fix_commands ?? []).slice(0, 4).map((fix, index) => (
                    <button
                      key={`${file.path}-fix-${fix.condition ?? index}`}
                      type="button"
                      onClick={() => fix.command && navigator.clipboard.writeText(fix.command)}
                      className="btn-secondary px-1.5 py-0.5 text-[10px] max-w-full"
                      title={fix.command ?? "PX4 prerequisite command"}
                    >
                      <Copy size={9} />
                      <span className="truncate">{formatReadinessLabel(fix.label ?? fix.condition ?? "command")}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Px4ReceiverReportList({
  reports,
  downloadDir,
  onRefresh,
}: {
  reports: Px4ReceiverReportFile[];
  downloadDir: string;
  onRefresh: () => void;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reveal = async (path: string) => {
    setBusyPath(path);
    setActionError(null);
    try {
      await cmd.revealSupportBundle(path);
    } catch (err) {
      setActionError(String(err));
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-slate-300 flex items-center gap-2">
            <Terminal size={13} className="text-cyan-400" /> PX4 Receiver Evidence
          </h4>
          <p className="text-[10px] text-slate-500 font-mono truncate">{downloadDir}</p>
        </div>
        <button onClick={onRefresh} className="btn-secondary text-xs py-1 px-2">
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>
      {actionError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}
      {reports.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No downloaded PX4 receiver report yet.
        </div>
      ) : (
        <div className="space-y-2">
          {reports.slice(0, 3).map((file) => (
            <div key={file.path} className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={readinessBadgeClass(file.report.status)}>
                      {readinessIcon(file.report.status)}
                      {formatReadinessLabel(file.report.status)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {formatReadinessLabel(file.report.expected_message)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      samples {file.report.sample_count ?? 0}
                    </span>
                    {file.report.observed_rate_hz != null && (
                      <span className="font-mono text-[10px] text-slate-500">
                        {file.report.observed_rate_hz.toFixed(2)}hz
                      </span>
                    )}
                    <span className="font-mono text-[10px] text-slate-500">
                      age {file.report.latest_sample_age_s == null ? "n/a" : `${file.report.latest_sample_age_s.toFixed(2)}s`}
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                    {file.name} / {formatReportSize(file.size_bytes)} / {formatReportTime(file.modified_unix_ms)}
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-600 truncate">
                    position {formatPosition(file.report.last_position)} / mavlink {file.report.mavlink_version ?? "n/a"} / udp{" "}
                    {file.report.has_udp_14550 ? "14550" : "n/a"}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => navigator.clipboard.writeText(file.path)}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Copy report path"
                  >
                    <Copy size={11} />
                  </button>
                  <button
                    onClick={() => reveal(file.path)}
                    disabled={busyPath === file.path}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Show report file"
                  >
                    {busyPath === file.path ? <Loader2 size={11} className="animate-spin" /> : <FolderOpen size={11} />}
                  </button>
                </div>
              </div>
              {file.report.issues.length > 0 && (
                <div className="space-y-1">
                  {file.report.issues.slice(0, 3).map((issue) => (
                    <div key={`${file.path}-${issue}`} className="text-[10px] text-slate-500 truncate">
                      {issue}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function FeatureMethodBenchmarkReportList({
  reports,
  downloadDir,
  onRefresh,
}: {
  reports: FeatureMethodBenchmarkReportFile[];
  downloadDir: string;
  onRefresh: () => void;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reveal = async (path: string) => {
    setBusyPath(path);
    setActionError(null);
    try {
      await cmd.revealSupportBundle(path);
    } catch (err) {
      setActionError(String(err));
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-slate-300 flex items-center gap-2">
            <TestTube2 size={13} className="text-cyan-400" /> Feature Method Benchmarks
          </h4>
          <p className="text-[10px] text-slate-500 font-mono truncate">{downloadDir}</p>
        </div>
        <button onClick={onRefresh} className="btn-secondary text-xs py-1 px-2">
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>
      {actionError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}
      {reports.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No downloaded feature-method benchmark report yet.
        </div>
      ) : (
        <div className="space-y-2">
          {reports.slice(0, 3).map((file) => (
            <div key={file.path} className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={readinessBadgeClass(file.report.status)}>
                      {readinessIcon(file.report.status)}
                      {formatReadinessLabel(file.report.status)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      expected {formatReadinessLabel(file.report.expected)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      recommend {formatReadinessLabel(file.report.recommended_method)}
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                    {file.name} / {formatReportSize(file.size_bytes)} / {formatReportTime(file.modified_unix_ms)}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => navigator.clipboard.writeText(file.path)}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Copy report path"
                  >
                    <Copy size={11} />
                  </button>
                  <button
                    onClick={() => reveal(file.path)}
                    disabled={busyPath === file.path}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Show report file"
                  >
                    {busyPath === file.path ? <Loader2 size={11} className="animate-spin" /> : <FolderOpen size={11} />}
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                {file.report.methods.map((method) => (
                  <div key={`${file.path}-${method.method}`} className="flex items-center gap-1.5 font-mono text-[10px] text-slate-500">
                    <span className={cn(readinessBadgeClass(method.status), "text-[10px]")}>
                      {formatReadinessLabel(method.status)}
                    </span>
                    <span>{formatReadinessLabel(method.method)}</span>
                    <span className="text-slate-600">
                      {formatAcceptedRate(method.accepted_rate)} / {method.total_records ?? 0} rec
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function thresholdMarginLabels(margins: unknown) {
  if (!margins || typeof margins !== "object" || Array.isArray(margins)) return [];
  return Object.entries(margins as Record<string, unknown>)
    .slice(0, 3)
    .map(([key, value]) => {
      const numeric = typeof value === "number" ? value.toFixed(2) : String(value ?? "n/a");
      return `${formatReadinessLabel(key)} ${numeric}`;
    });
}

function ThresholdTuningReportList({
  reports,
  downloadDir,
  onRefresh,
}: {
  reports: ThresholdTuningReportFile[];
  downloadDir: string;
  onRefresh: () => void;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reveal = async (path: string) => {
    setBusyPath(path);
    setActionError(null);
    try {
      await cmd.revealSupportBundle(path);
    } catch (err) {
      setActionError(String(err));
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-slate-300 flex items-center gap-2">
            <ShieldCheck size={13} className="text-cyan-400" /> Threshold Tuning Reports
          </h4>
          <p className="text-[10px] text-slate-500 font-mono truncate">{downloadDir}</p>
        </div>
        <button onClick={onRefresh} className="btn-secondary text-xs py-1 px-2">
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>
      {actionError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}
      {reports.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No downloaded threshold-tuning report yet.
        </div>
      ) : (
        <div className="space-y-2">
          {reports.slice(0, 3).map((file) => (
            <div key={file.path} className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={readinessBadgeClass(file.report.status)}>
                      {readinessIcon(file.report.status)}
                      {formatReadinessLabel(file.report.status)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      coverage {formatReadinessLabel(file.report.coverage_status)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      replay {formatReadinessLabel(file.report.replay_status)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      field cases {file.report.field_case_count ?? 0}
                    </span>
                    {(file.report.capture_metadata_issue_count ?? 0) > 0 && (
                      <span className="font-mono text-[10px] text-red-300">
                        metadata issues {file.report.capture_metadata_issue_count}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                    {file.name} / {formatReportSize(file.size_bytes)} / {formatReportTime(file.modified_unix_ms)}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {thresholdMarginLabels(file.report.margins).map((label) => (
                      <span key={`${file.path}-${label}`} className="font-mono text-[10px] text-slate-600">
                        {label}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => navigator.clipboard.writeText(file.path)}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Copy report path"
                  >
                    <Copy size={11} />
                  </button>
                  <button
                    onClick={() => reveal(file.path)}
                    disabled={busyPath === file.path}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Show report file"
                  >
                    {busyPath === file.path ? <Loader2 size={11} className="animate-spin" /> : <FolderOpen size={11} />}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RosbagExportValidationReportList({
  reports,
  downloadDir,
  onRefresh,
}: {
  reports: RosbagExportValidationReportFile[];
  downloadDir: string;
  onRefresh: () => void;
}) {
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const reveal = async (path: string) => {
    setBusyPath(path);
    setActionError(null);
    try {
      await cmd.revealSupportBundle(path);
    } catch (err) {
      setActionError(String(err));
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-medium text-slate-300 flex items-center gap-2">
            <Archive size={13} className="text-cyan-400" /> ROS Bag Validation
          </h4>
          <p className="text-[10px] text-slate-500 font-mono truncate">{downloadDir}</p>
        </div>
        <button onClick={onRefresh} className="btn-secondary text-xs py-1 px-2">
          <RefreshCw size={11} />
          Refresh
        </button>
      </div>
      {actionError && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {actionError}
        </div>
      )}
      {reports.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-slate-500">
          No downloaded ROS bag validation report yet.
        </div>
      ) : (
        <div className="space-y-2">
          {reports.slice(0, 3).map((file) => (
            <div key={file.path} className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={readinessBadgeClass(file.report.status)}>
                      {readinessIcon(file.report.status)}
                      {formatReadinessLabel(file.report.status)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {formatReadinessLabel(file.report.format)}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {file.report.message_count ?? 0} msg
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {file.report.topic_count ?? 0} topics
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-500 truncate">
                    {file.name} / {formatReportSize(file.size_bytes)} / {formatReportTime(file.modified_unix_ms)}
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-600 truncate">
                    artifact {formatReadinessLabel(file.report.artifact_path)}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => navigator.clipboard.writeText(file.path)}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Copy report path"
                  >
                    <Copy size={11} />
                  </button>
                  <button
                    onClick={() => reveal(file.path)}
                    disabled={busyPath === file.path}
                    className="btn-secondary text-xs py-1 px-2"
                    title="Show report file"
                  >
                    {busyPath === file.path ? <Loader2 size={11} className="animate-spin" /> : <FolderOpen size={11} />}
                  </button>
                </div>
              </div>
              {file.report.topics.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {file.report.topics.slice(0, 4).map((topic) => (
                    <span key={`${file.path}-${topic}`} className="rounded border border-border bg-bg-base px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
                      {topic}
                    </span>
                  ))}
                </div>
              )}
              {file.report.issues.length > 0 && (
                <div className="space-y-1">
                  {file.report.issues.slice(0, 3).map((issue) => (
                    <div key={`${file.path}-${issue}`} className="text-[10px] text-amber-300 truncate">
                      {issue}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface ModuleSetupProps {
  initialDeviceId?: string;
  embedded?: boolean;
}

export function ModuleSetup({ initialDeviceId, embedded = false }: ModuleSetupProps) {
  const { devices, addDevice, updateDevice, setActiveDevice, activeDeviceId } = useAppStore();
  const piDevices = devices.filter((device) => device.kind === "pi5");
  const activePi = piDevices.find((device) => device.id === (initialDeviceId ?? activeDeviceId)) ?? piDevices[0];
  const [selectedDeviceId, setSelectedDeviceId] = useState(activePi?.id ?? "new");
  const [form, setForm] = useState<PiForm>(() => (activePi ? formFromDevice(activePi) : defaultForm()));
  const [showPassword, setShowPassword] = useState(false);
  const [showPassphrase, setShowPassphrase] = useState(false);
  const [showSudoPassword, setShowSudoPassword] = useState(false);
  const [sudoPassword, setSudoPassword] = useState("");
  const [repoPath, setRepoPath] = useState(() => localStorage.getItem("drone_repo_path") || DEFAULT_LOCAL_REPO);
  const [testing, setTesting] = useState(false);
  const [connectionResult, setConnectionResult] = useState<{ ok: boolean; message: string; fingerprint?: string } | null>(null);
  const [runningStep, setRunningStep] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, SetupResult>>({});
  const [selectedOutputId, setSelectedOutputId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [cameraPreview, setCameraPreview] = useState<string | null>(null);
  const [cameraPreviewPath, setCameraPreviewPath] = useState<string | null>(null);
  const [cameraAutoRefresh, setCameraAutoRefresh] = useState(false);
  const [capturingCamera, setCapturingCamera] = useState(false);
  const [supportBundles, setSupportBundles] = useState<SupportBundleFile[]>([]);
  const [autonomyReports, setAutonomyReports] = useState<AutonomyReadinessReportFile[]>([]);
  const [autonomyWorkflowReports, setAutonomyWorkflowReports] = useState<AutonomyEvidenceWorkflowReportFile[]>([]);
  const [px4PrereqReports, setPx4PrereqReports] = useState<Px4PrereqReportFile[]>([]);
  const [px4ReceiverReports, setPx4ReceiverReports] = useState<Px4ReceiverReportFile[]>([]);
  const [fieldEvidenceTemplates, setFieldEvidenceTemplates] = useState<FieldEvidenceTemplateFile[]>([]);
  const [fieldCollectionPlans, setFieldCollectionPlans] = useState<FieldCollectionPlanFile[]>([]);
  const [fieldEvidenceReports, setFieldEvidenceReports] = useState<FieldEvidenceReportFile[]>([]);
  const [featureBenchmarkReports, setFeatureBenchmarkReports] = useState<FeatureMethodBenchmarkReportFile[]>([]);
  const [thresholdTuningReports, setThresholdTuningReports] = useState<ThresholdTuningReportFile[]>([]);
  const [rosbagValidationReports, setRosbagValidationReports] = useState<RosbagExportValidationReportFile[]>([]);
  const [runtimeStatus, setRuntimeStatus] = useState<Record<string, unknown> | null>(null);
  const [runtimeStatusRemotePath, setRuntimeStatusRemotePath] = useState<string | null>(null);
  const [runtimeStatusLocalPath, setRuntimeStatusLocalPath] = useState<string | null>(null);
  const [autonomyHandoffLocalPath, setAutonomyHandoffLocalPath] = useState<string | null>(null);
  const [autonomyEvidencePackageLocalPath, setAutonomyEvidencePackageLocalPath] = useState<string | null>(null);
  const [autonomyWorkflowLocalPath, setAutonomyWorkflowLocalPath] = useState<string | null>(null);
  const [fieldTemplateLocalPath, setFieldTemplateLocalPath] = useState<string | null>(null);
  const [fieldManifestLocalPath, setFieldManifestLocalPath] = useState<string | null>(null);
  const [fieldCollectionPlanLocalPath, setFieldCollectionPlanLocalPath] = useState<string | null>(null);
  const [fieldCollectionPlanMarkdownLocalPath, setFieldCollectionPlanMarkdownLocalPath] = useState<string | null>(null);
  const [fieldCapturePreflightLocalPath, setFieldCapturePreflightLocalPath] = useState<string | null>(null);
  const [fieldCapturePreflightReport, setFieldCapturePreflightReport] = useState<FieldCapturePreflightReport | null>(null);
  const [setupReportPath, setSetupReportPath] = useState<string | null>(null);
  const [setupHandoff, setSetupHandoff] = useState<ModuleSetupHandoff | null>(() => readModuleSetupHandoff());
  const [fieldCase, setFieldCase] = useState<FieldCaseForm>(() => loadFieldCaseForm());
  const [discovering, setDiscovering] = useState(false);
  const [discoveryCandidates, setDiscoveryCandidates] = useState<PiDiscoveryCandidate[]>(() => loadDiscoveryHistory());
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [networkHints, setNetworkHints] = useState<LocalNetworkHint[]>([]);
  const [selectedAdapterKey, setSelectedAdapterKey] = useState("");
  const [discoveryCopied, setDiscoveryCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedDevice = useMemo(
    () => piDevices.find((device) => device.id === selectedDeviceId),
    [piDevices, selectedDeviceId],
  );

  useEffect(() => {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(FIELD_CASE_FORM_STORAGE_KEY, JSON.stringify(fieldCase));
  }, [fieldCase]);

  useEffect(() => {
    if (selectedDevice) {
      setForm(formFromDevice(selectedDevice));
      setConnectionResult(null);
      setRuntimeStatus(null);
      setRuntimeStatusRemotePath(null);
      setRuntimeStatusLocalPath(null);
      setAutonomyHandoffLocalPath(null);
      setAutonomyEvidencePackageLocalPath(null);
      setAutonomyWorkflowLocalPath(null);
      setFieldTemplateLocalPath(null);
      setFieldManifestLocalPath(null);
      setFieldCollectionPlanLocalPath(null);
      setFieldCollectionPlanMarkdownLocalPath(null);
      setFieldCapturePreflightLocalPath(null);
      setFieldCapturePreflightReport(null);
      setError(null);
    }
  }, [selectedDeviceId]);

  const refreshSupportBundles = async () => {
    try {
      setSupportBundles(await cmd.listSupportBundles(SUPPORT_DOWNLOAD_DIR));
    } catch {
      setSupportBundles([]);
    }
  };

  const refreshAutonomyReports = async () => {
    try {
      setAutonomyReports(await cmd.listAutonomyReadinessReports(AUTONOMY_REPORT_DOWNLOAD_DIR));
    } catch {
      setAutonomyReports([]);
    }
  };

  const refreshAutonomyWorkflowReports = async () => {
    try {
      setAutonomyWorkflowReports(await cmd.listAutonomyEvidenceWorkflowReports(AUTONOMY_REPORT_DOWNLOAD_DIR));
    } catch {
      setAutonomyWorkflowReports([]);
    }
  };

  const refreshPx4ReceiverReports = async () => {
    try {
      setPx4ReceiverReports(await cmd.listPx4ReceiverReports(PX4_RECEIVER_DOWNLOAD_DIR));
    } catch {
      setPx4ReceiverReports([]);
    }
  };

  const refreshPx4PrereqReports = async () => {
    try {
      setPx4PrereqReports(await cmd.listPx4PrereqReports(PX4_RECEIVER_DOWNLOAD_DIR));
    } catch {
      setPx4PrereqReports([]);
    }
  };

  const refreshFieldEvidenceReports = async () => {
    try {
      setFieldEvidenceReports(await cmd.listFieldEvidenceReports(AUTONOMY_REPORT_DOWNLOAD_DIR));
    } catch {
      setFieldEvidenceReports([]);
    }
  };

  const refreshFieldEvidenceTemplates = async () => {
    try {
      setFieldEvidenceTemplates(await cmd.listFieldEvidenceTemplates(AUTONOMY_REPORT_DOWNLOAD_DIR));
    } catch {
      setFieldEvidenceTemplates([]);
    }
  };

  const refreshFieldCollectionPlans = async () => {
    try {
      setFieldCollectionPlans(await cmd.listFieldCollectionPlans(AUTONOMY_REPORT_DOWNLOAD_DIR));
    } catch {
      setFieldCollectionPlans([]);
    }
  };

  const refreshFeatureBenchmarkReports = async () => {
    try {
      setFeatureBenchmarkReports(await cmd.listFeatureMethodBenchmarkReports(FEATURE_BENCH_DOWNLOAD_DIR));
    } catch {
      setFeatureBenchmarkReports([]);
    }
  };

  const refreshThresholdTuningReports = async () => {
    try {
      setThresholdTuningReports(await cmd.listThresholdTuningReports(AUTONOMY_REPORT_DOWNLOAD_DIR));
    } catch {
      setThresholdTuningReports([]);
    }
  };

  const refreshRosbagValidationReports = async () => {
    try {
      setRosbagValidationReports(await cmd.listRosbagExportValidationReports(ROSBAG_VALIDATION_DOWNLOAD_DIR));
    } catch {
      setRosbagValidationReports([]);
    }
  };

  useEffect(() => {
    refreshSupportBundles();
    refreshAutonomyReports();
    refreshAutonomyWorkflowReports();
    refreshPx4PrereqReports();
    refreshPx4ReceiverReports();
    refreshFieldEvidenceTemplates();
    refreshFieldCollectionPlans();
    refreshFieldEvidenceReports();
    refreshFeatureBenchmarkReports();
    refreshThresholdTuningReports();
    refreshRosbagValidationReports();
    cmd.localNetworkHints().then(setNetworkHints).catch(() => setNetworkHints([]));
  }, []);

  const clearSetupHandoff = () => {
    sessionStorage.removeItem(MODULE_SETUP_HANDOFF_KEY);
    setSetupHandoff(null);
  };

  const auth = (): NonNullable<Device["auth"]> | null => {
    if (form.authMethod === "password") {
      if (!form.password) return null;
      return { type: "Password", password: form.password };
    }
    if (!form.keyPath) return null;
    return {
      type: "Key",
      key_path: form.keyPath,
      passphrase: form.passphrase || undefined,
    };
  };

  const connectionReady = !!form.host && !!form.username && !!auth();
  const remoteProject = form.remotePath || defaultRemotePath(form.username);
  const defaultRemoteBundle = `/home/${form.username || "user"}/drone-data/map_bundles/mission_bundle`;
  const activeHandoff = setupHandoff?.device_id === selectedDeviceId ? setupHandoff : null;
  const remoteBundle = activeHandoff?.remote_bundle_dir || defaultRemoteBundle;
  const fieldMetadataReady = fieldCaptureMetadataReady(fieldCase);
  const fieldMetadataIssues = fieldCaptureMetadataIssues(fieldCase);

  const setResult = (id: string, result: SetupResult) => {
    setResults((prev) => ({ ...prev, [id]: result }));
    setSelectedOutputId(id);
  };

  const loadFieldCollectionCondition = (
    plan: FieldCollectionPlanFile,
    condition: FieldCollectionPlanFile["conditions"][number],
  ) => {
    setFieldCase((value) => fieldCaseFromCollectionPlanCondition(value, plan, condition));
    const commandLines = [
      condition.capture_command ? `capture: ${condition.capture_command}` : null,
      condition.metadata_update_command ? `metadata: ${condition.metadata_update_command}` : null,
      condition.register_command ? `register: ${condition.register_command}` : null,
    ].filter((line): line is string => Boolean(line));
    setResult("field-evidence", {
      status: "idle",
      output: [
        "$ Load Field Collection Condition",
        `case: ${condition.case_name ?? "n/a"}`,
        `condition: ${condition.condition ?? "n/a"}`,
        `expected: ${condition.expected ?? "n/a"}`,
        `log: ${condition.source_log ?? "latest default"}`,
        `plan: ${plan.path}`,
        ...commandLines,
        "",
        "Review capture metadata, fill any missing values, then register the field evidence case.",
      ].join("\n"),
    });
  };

  const loadNextFieldCollectionCondition = () => {
    const plan = [...fieldCollectionPlans]
      .sort((a, b) => (b.modified_unix_ms ?? 0) - (a.modified_unix_ms ?? 0))
      .find((candidate) => candidate.next_condition);
    if (!plan?.next_condition) {
      setResult("field-next-condition", {
        status: "failed",
        output:
          "$ Load Next Field Condition\nNo downloaded field collection plan has a pending next condition yet.\n\nRun Create Plan, then load the next condition after the plan downloads.",
      });
      return;
    }
    loadFieldCollectionCondition(plan, plan.next_condition);
    setSelectedOutputId("field-evidence");
  };

  const browseForKey = async () => {
    const path = await openDialog({
      title: "Select SSH private key",
      multiple: false,
      filters: [{ name: "Private key", extensions: ["pem", "ppk", "key"] }],
    });
    if (path && typeof path === "string") setForm((value) => ({ ...value, keyPath: path }));
  };

  const browseForRepo = async () => {
    const path = await openDialog({ title: "Select local Drone repo", directory: true, multiple: false });
    if (path && typeof path === "string") {
      setRepoPath(path);
      localStorage.setItem("drone_repo_path", path);
    }
  };

  const useDiscoveredDevice = (candidate: PiDiscoveryCandidate) => {
    const username = form.username || "user";
    setSelectedDeviceId("new");
    setForm((value) => ({
      ...value,
      name: candidateName(candidate),
      host: candidateHost(candidate),
      port: candidate.port,
      username,
      remotePath: defaultRemotePath(username),
    }));
    setConnectionResult(null);
    setError(null);
  };

  const runDiscovery = async () => {
    setDiscovering(true);
    setDiscoveryError(null);
    try {
      const seedHosts = piDevices
        .filter((device) => device.host)
        .map((device) => device.host!)
        .concat(form.host ? [form.host] : []);
      const candidates = await cmd.discoverPiDevices(seedHosts, 22);
      const hints = await cmd.localNetworkHints().catch(() => networkHints);
      const next = mergeDiscoveryHistory(discoveryCandidates, candidates);
      setDiscoveryCandidates(next);
      setNetworkHints(hints);
      saveDiscoveryHistory(next);
    } catch (err) {
      setDiscoveryError(String(err));
    } finally {
      setDiscovering(false);
    }
  };

  const selectedDiscoveryHint = selectedNetworkHint(networkHints, selectedAdapterKey);
  const discoverySummary = discoveryStatusSummary(discoveryCandidates, networkHints, selectedDiscoveryHint);
  const discoveryTips = discoveryTroubleshooting(discoveryCandidates, networkHints, selectedDiscoveryHint);

  const discoveryChecklist = () => discoveryChecklistText({
    candidates: discoveryCandidates,
    networkHints,
    selectedHint: selectedDiscoveryHint,
    targetHost: form.host,
    username: form.username,
  });

  const copyDiscoveryChecklist = async () => {
    try {
      await navigator.clipboard.writeText(discoveryChecklist());
      setDiscoveryCopied(true);
      window.setTimeout(() => setDiscoveryCopied(false), 1800);
    } catch (err) {
      setDiscoveryError(`Could not copy checklist: ${err}`);
    }
  };

  const testConnection = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Enter the module host, username, and SSH authentication first.");
      return;
    }
    setTesting(true);
    setError(null);
    setConnectionResult(null);
    try {
      const result = await cmd.testSshConnection(form.host, form.port, form.username, resolvedAuth);
      setConnectionResult({
        ok: result.ok,
        message: result.message,
        fingerprint: result.fingerprint,
      });
    } catch (err) {
      setConnectionResult({ ok: false, message: String(err) });
    } finally {
      setTesting(false);
    }
  };

  const saveConnection = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Testable SSH details are required before saving the module.");
      return;
    }
    const id = selectedDevice?.id ?? generateId();
    const device: Device = {
      id,
      name: form.name || "Raspberry Pi 5",
      kind: "pi5",
      host: form.host,
      port: form.port,
      username: form.username,
      auth: resolvedAuth,
      remote_project_path: remoteProject,
      mavlink_endpoint: form.mavlinkEndpoint,
      autopilot: selectedDevice?.autopilot ?? "px4",
      known_fingerprint: connectionResult?.fingerprint ?? selectedDevice?.known_fingerprint,
    };
    const next = selectedDevice
      ? devices.map((candidate) => (candidate.id === id ? device : candidate))
      : [...devices, device];
    selectedDevice ? updateDevice(device) : addDevice(device);
    setActiveDevice(id);
    setSelectedDeviceId(id);
    await cmd.saveDevices(next);
  };

  const runRemote = async (id: string, label: string, command: string) => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before running setup checks.");
      return;
    }
    setRunningStep(id);
    setError(null);
    setResult(id, { status: "running", output: `$ ${label}\n` });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        command,
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      setResult(id, {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ ${label}\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
    } catch (err) {
      setResult(id, { status: "failed", output: `$ ${label}\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runBundleDiagnostic = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before diagnosing the mission bundle.");
      return;
    }
    setRunningStep("bundle-diagnostic");
    setError(null);
    setResult("bundle-diagnostic", { status: "running", output: "$ bundle diagnostic\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        bundleDiagnosticCommand(remoteProject, remoteBundle),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteReport = parseBundleDiagnosticReport(output);
      if (!remoteReport) {
        setResult("bundle-diagnostic", {
          status: result.exit_code === 0 ? "passed" : "failed",
          output: `$ bundle diagnostic\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("bundle-diagnostic", {
        status: "running",
        output: `$ bundle diagnostic\n${output}\n\n$ download bundle diagnostic\nDownloading ${remoteReport}...`,
      });
      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteReport,
        AUTONOMY_REPORT_DOWNLOAD_DIR,
      );
      setResult("bundle-diagnostic", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ bundle diagnostic\n${output}\n\n$ download bundle diagnostic\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshAutonomyReports();
    } catch (err) {
      setResult("bundle-diagnostic", { status: "failed", output: `$ bundle diagnostic\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const createBenchReport = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before creating a bench report.");
      return;
    }
    setRunningStep("bench-report");
    setError(null);
    setResult("bench-report", { status: "running", output: "$ create bench report\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        benchReportCommand(remoteProject, remoteBundle, form.mavlinkEndpoint),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteZip = parseSupportBundleZip(output);
      if (!remoteZip) {
        setResult("bench-report", {
          status: "failed",
          output: `$ create bench report\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("bench-report", {
        status: "running",
        output: `$ create bench report\n${output}\n\n$ download bench report\nDownloading ${remoteZip}...`,
      });
      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteZip,
        SUPPORT_DOWNLOAD_DIR,
      );
      setResult("bench-report", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ create bench report\n${output}\n\n$ download bench report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshSupportBundles();
    } catch (err) {
      setResult("bench-report", { status: "failed", output: `$ create bench report\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const fetchRuntimeStatus = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before fetching runtime status.");
      return;
    }
    setRunningStep("runtime-status");
    setError(null);
    setResult("runtime-status", { status: "running", output: "$ runtime status\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        runtimeStatusCommand(remoteProject),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteStatus = parseRuntimeStatusPath(output);
      const parsedStatus = parseRuntimeStatusJson(output);
      if (parsedStatus) {
        setRuntimeStatus(parsedStatus);
        setRuntimeStatusRemotePath(remoteStatus ?? null);
      }

      if (!remoteStatus) {
        setResult("runtime-status", {
          status: result.exit_code === 0 ? "passed" : "failed",
          output: `$ runtime status\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteStatus,
        RUNTIME_STATUS_DOWNLOAD_DIR,
      );
      setRuntimeStatusLocalPath(downloaded.local_path);
      setResult("runtime-status", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ runtime status\n${output || "(no output)"}\n\n$ download runtime status\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
    } catch (err) {
      setResult("runtime-status", { status: "failed", output: `$ runtime status\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runFieldLogCapture = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before capturing a field replay log.");
      return;
    }
    setRunningStep("field-log-capture");
    setError(null);
    setResult("field-log-capture", { status: "running", output: "$ field log capture\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        fieldLogCaptureCommand(remoteProject, remoteBundle, form.mavlinkEndpoint, fieldCase),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteLog = parseTerrainRuntimeLog(output);
      const remoteStatus = parseRuntimeStatusPath(output);
      const parsedStatus = parseRuntimeStatusJson(output);
      let downloadText = "";
      if (parsedStatus) {
        setRuntimeStatus(parsedStatus);
        setRuntimeStatusRemotePath(remoteStatus ?? null);
      }
      if (remoteLog) {
        setResult("field-log-capture", {
          status: "running",
          output: `$ field log capture\n${output}\n\n$ download terrain match log\nDownloading ${remoteLog}...`,
        });
        const downloadedLog = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteLog,
          ROSBAG_VALIDATION_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download terrain match log\nSaved to ${downloadedLog.local_path}\n[${downloadedLog.bytes_received} bytes]`;
      }
      if (remoteStatus) {
        setResult("field-log-capture", {
          status: "running",
          output: `$ field log capture\n${output}${downloadText}\n\n$ download runtime status\nDownloading ${remoteStatus}...`,
        });
        const downloadedStatus = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteStatus,
          RUNTIME_STATUS_DOWNLOAD_DIR,
        );
        setRuntimeStatusRemotePath(remoteStatus);
        setRuntimeStatusLocalPath(downloadedStatus.local_path);
        downloadText += `\n\n$ download runtime status\nSaved to ${downloadedStatus.local_path}\n[${downloadedStatus.bytes_received} bytes]`;
      }
      setResult("field-log-capture", {
        status: result.exit_code === 0 && remoteLog && remoteStatus ? "passed" : "failed",
        output: `$ field log capture\n${output || "(no output)"}${downloadText}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
    } catch (err) {
      setResult("field-log-capture", { status: "failed", output: `$ field log capture\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runFieldCapturePreflight = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before running field capture preflight.");
      return;
    }
    setRunningStep("field-capture-preflight");
    setError(null);
    setFieldCapturePreflightLocalPath(null);
    setFieldCapturePreflightReport(null);
    setResult("field-capture-preflight", { status: "running", output: "$ field capture preflight\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        fieldCapturePreflightCommand(remoteProject, fieldCase),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteReport = parseFieldCapturePreflightReport(output);
      const readyForCapture = parseFieldCaptureReady(output);
      if (!remoteReport) {
        setResult("field-capture-preflight", {
          status: result.exit_code === 0 && readyForCapture ? "passed" : "failed",
          output: `$ field capture preflight\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("field-capture-preflight", {
        status: "running",
        output: `$ field capture preflight\n${output}\n\n$ download field capture preflight\nDownloading ${remoteReport}...`,
      });
      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteReport,
        AUTONOMY_REPORT_DOWNLOAD_DIR,
      );
      setFieldCapturePreflightLocalPath(downloaded.local_path);
      setFieldCapturePreflightReport(await readFieldCapturePreflightReport(downloaded.local_path));
      setResult("field-capture-preflight", {
        status: result.exit_code === 0 && readyForCapture ? "passed" : "failed",
        output: `$ field capture preflight\n${output}\n\n$ download field capture preflight\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
    } catch (err) {
      setResult("field-capture-preflight", { status: "failed", output: `$ field capture preflight\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runAutonomyReadiness = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before running the autonomy readiness audit.");
      return;
    }
    setRunningStep("autonomy-readiness");
    setError(null);
    setResult("autonomy-readiness", { status: "running", output: "$ autonomy readiness\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        `cd ${shellQuote(remoteProject)} && ./scripts/pi/run_autonomy_readiness_audit.sh`,
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteReport = parseAutonomyReadinessReport(output);
      const remoteHandoff = parseAutonomyReadinessHandoff(output);
      const remoteEvidencePackage = parseAutonomyEvidencePackage(output);
      const remotePx4Report = parsePx4SitlReport(output);
      const remotePx4Prereqs = parsePx4SitlPrereqs(output);
      const remoteWorkflow = parseAutonomyEvidenceWorkflowReport(output);
      const remoteWorkflowLogs = parseAutonomyEvidenceWorkflowLogs(output);
      const remoteWorkflowValidation = parseAutonomyEvidenceWorkflowValidation(output);
      const remoteFieldEvidenceReport = parseFieldEvidenceReport(output);
      const remoteFieldCollectionPlan = parseFieldCollectionPlan(output);
      const remoteFieldCollectionPlanMarkdown = parseFieldCollectionPlanMarkdown(output);
      const remoteFeatureMethodReport = parseFeatureMethodReport(output);
      const remoteThresholdReport = parseThresholdTuningReport(output);
      const remoteRosbagValidation = parseRosbagExportValidationReport(output);
      const remoteRosbag2CliReview = parseRosbag2CliReviewReport(output);
      if (!remoteReport) {
        setResult("autonomy-readiness", {
          status: "failed",
          output: `$ autonomy readiness\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("autonomy-readiness", {
        status: "running",
        output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nDownloading ${remoteReport}...`,
      });
      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteReport,
        AUTONOMY_REPORT_DOWNLOAD_DIR,
      );
      let handoffDownloadText = "";
      if (remoteHandoff) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n\n$ download readiness handoff\nDownloading ${remoteHandoff}...`,
        });
        const downloadedHandoff = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteHandoff,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setAutonomyHandoffLocalPath(downloadedHandoff.local_path);
        handoffDownloadText = `\n\n$ download readiness handoff\nSaved to ${downloadedHandoff.local_path}\n[${downloadedHandoff.bytes_received} bytes]`;
      } else {
        setAutonomyHandoffLocalPath(null);
      }
      let evidencePackageDownloadText = "";
      if (remoteEvidencePackage) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}\n\n$ download evidence package\nDownloading ${remoteEvidencePackage}...`,
        });
        const downloadedPackage = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteEvidencePackage,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setAutonomyEvidencePackageLocalPath(downloadedPackage.local_path);
        evidencePackageDownloadText = `\n\n$ download evidence package\nSaved to ${downloadedPackage.local_path}\n[${downloadedPackage.bytes_received} bytes]`;
      } else {
        setAutonomyEvidencePackageLocalPath(null);
      }
      let workflowDownloadText = "";
      if (remoteWorkflow) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}\n\n$ download workflow report\nDownloading ${remoteWorkflow}...`,
        });
        const downloadedWorkflow = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteWorkflow,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setAutonomyWorkflowLocalPath(downloadedWorkflow.local_path);
        workflowDownloadText = `\n\n$ download workflow report\nSaved to ${downloadedWorkflow.local_path}\n[${downloadedWorkflow.bytes_received} bytes]`;
      }
      if (remoteWorkflowLogs) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}\n\n$ download workflow logs\nDownloading ${remoteWorkflowLogs}...`,
        });
        const downloadedWorkflowLogs = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteWorkflowLogs,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        workflowDownloadText += `\n\n$ download workflow logs\nSaved to ${downloadedWorkflowLogs.local_path}\n[${downloadedWorkflowLogs.bytes_received} bytes]`;
      }
      if (remoteWorkflowValidation) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}\n\n$ download workflow validation\nDownloading ${remoteWorkflowValidation}...`,
        });
        const downloadedWorkflowValidation = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteWorkflowValidation,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        workflowDownloadText += `\n\n$ download workflow validation\nSaved to ${downloadedWorkflowValidation.local_path}\n[${downloadedWorkflowValidation.bytes_received} bytes]`;
      }
      let proofDownloadText = "";
      if (remoteFieldEvidenceReport) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}\n\n$ download field evidence report\nDownloading ${remoteFieldEvidenceReport}...`,
        });
        const downloadedFieldEvidence = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteFieldEvidenceReport,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        proofDownloadText += `\n\n$ download field evidence report\nSaved to ${downloadedFieldEvidence.local_path}\n[${downloadedFieldEvidence.bytes_received} bytes]`;
      }
      if (remoteFeatureMethodReport) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}${proofDownloadText}\n\n$ download feature benchmark\nDownloading ${remoteFeatureMethodReport}...`,
        });
        const downloadedFeatureBenchmark = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteFeatureMethodReport,
          FEATURE_BENCH_DOWNLOAD_DIR,
        );
        proofDownloadText += `\n\n$ download feature benchmark\nSaved to ${downloadedFeatureBenchmark.local_path}\n[${downloadedFeatureBenchmark.bytes_received} bytes]`;
      }
      if (remoteThresholdReport) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}${proofDownloadText}\n\n$ download threshold report\nDownloading ${remoteThresholdReport}...`,
        });
        const downloadedThreshold = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteThresholdReport,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        proofDownloadText += `\n\n$ download threshold report\nSaved to ${downloadedThreshold.local_path}\n[${downloadedThreshold.bytes_received} bytes]`;
      }
      if (remoteRosbagValidation) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}${proofDownloadText}\n\n$ download ROS bag validation\nDownloading ${remoteRosbagValidation}...`,
        });
        const downloadedRosbagValidation = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteRosbagValidation,
          ROSBAG_VALIDATION_DOWNLOAD_DIR,
        );
        proofDownloadText += `\n\n$ download ROS bag validation\nSaved to ${downloadedRosbagValidation.local_path}\n[${downloadedRosbagValidation.bytes_received} bytes]`;
      }
      if (remoteRosbag2CliReview) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}${proofDownloadText}\n\n$ download rosbag2 CLI review\nDownloading ${remoteRosbag2CliReview}...`,
        });
        const downloadedRosbag2Review = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteRosbag2CliReview,
          ROSBAG_VALIDATION_DOWNLOAD_DIR,
        );
        proofDownloadText += `\n\n$ download rosbag2 CLI review\nSaved to ${downloadedRosbag2Review.local_path}\n[${downloadedRosbag2Review.bytes_received} bytes]`;
      }
      if (remoteFieldCollectionPlan) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}${proofDownloadText}\n\n$ download field collection plan\nDownloading ${remoteFieldCollectionPlan}...`,
        });
        const downloadedPlan = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteFieldCollectionPlan,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setFieldCollectionPlanLocalPath(downloadedPlan.local_path);
        proofDownloadText += `\n\n$ download field collection plan\nSaved to ${downloadedPlan.local_path}\n[${downloadedPlan.bytes_received} bytes]`;
      }
      if (remoteFieldCollectionPlanMarkdown) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}${proofDownloadText}\n\n$ download field collection checklist\nDownloading ${remoteFieldCollectionPlanMarkdown}...`,
        });
        const downloadedMarkdown = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteFieldCollectionPlanMarkdown,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setFieldCollectionPlanMarkdownLocalPath(downloadedMarkdown.local_path);
        proofDownloadText += `\n\n$ download field collection checklist\nSaved to ${downloadedMarkdown.local_path}\n[${downloadedMarkdown.bytes_received} bytes]`;
      }
      let px4DownloadText = "";
      if (remotePx4Report) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}${proofDownloadText}\n\n$ download PX4 receiver report\nDownloading ${remotePx4Report}...`,
        });
        const downloadedPx4 = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remotePx4Report,
          PX4_RECEIVER_DOWNLOAD_DIR,
        );
        px4DownloadText = `\n\n$ download PX4 receiver report\nSaved to ${downloadedPx4.local_path}\n[${downloadedPx4.bytes_received} bytes]`;
      }
      if (remotePx4Prereqs) {
        setResult("autonomy-readiness", {
          status: "running",
          output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}${proofDownloadText}${px4DownloadText}\n\n$ download PX4 prereq report\nDownloading ${remotePx4Prereqs}...`,
        });
        const downloadedPx4Prereqs = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remotePx4Prereqs,
          PX4_RECEIVER_DOWNLOAD_DIR,
        );
        px4DownloadText += `\n\n$ download PX4 prereq report\nSaved to ${downloadedPx4Prereqs.local_path}\n[${downloadedPx4Prereqs.bytes_received} bytes]`;
      }
      setResult("autonomy-readiness", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${handoffDownloadText}${evidencePackageDownloadText}${workflowDownloadText}${proofDownloadText}${px4DownloadText}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshAutonomyReports();
      await refreshAutonomyWorkflowReports();
      await refreshFieldEvidenceReports();
      await refreshFeatureBenchmarkReports();
      await refreshThresholdTuningReports();
      await refreshRosbagValidationReports();
      await refreshFieldCollectionPlans();
      await refreshPx4PrereqReports();
      await refreshPx4ReceiverReports();
    } catch (err) {
      setResult("autonomy-readiness", { status: "failed", output: `$ autonomy readiness\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runLocalAutonomyReadiness = async () => {
    setRunningStep("local-autonomy-readiness");
    setError(null);
    setResult("local-autonomy-readiness", { status: "running", output: "$ local autonomy readiness\n" });
    try {
      const result = await cmd.runLocalAutonomyReadinessAudit(repoPath, DESKTOP_TRANSFER_FROM_PI_DIR);
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const localReport = parseAutonomyReadinessReport(output);
      const localHandoff = parseAutonomyReadinessHandoff(output);
      const localEvidencePackage = parseAutonomyEvidencePackage(output);
      const localWorkflow = parseAutonomyEvidenceWorkflowReport(output);
      const localRosbag2CliReview = parseRosbag2CliReviewReport(output);
      const localPx4Report = parsePx4SitlReport(output);
      const localFieldCollectionPlan = parseFieldCollectionPlan(output);
      const localFieldCollectionPlanMarkdown = parseFieldCollectionPlanMarkdown(output);
      if (localHandoff) {
        setAutonomyHandoffLocalPath(localHandoff);
      }
      if (localEvidencePackage) {
        setAutonomyEvidencePackageLocalPath(localEvidencePackage);
      }
      if (localWorkflow) {
        setAutonomyWorkflowLocalPath(localWorkflow);
      }
      if (localFieldCollectionPlan) {
        setFieldCollectionPlanLocalPath(localFieldCollectionPlan);
      }
      if (localFieldCollectionPlanMarkdown) {
        setFieldCollectionPlanMarkdownLocalPath(localFieldCollectionPlanMarkdown);
      }
      const localEvidenceNotes = [
        localPx4Report ? `PX4 receiver report: ${localPx4Report}` : "",
        localRosbag2CliReview ? `rosbag2 CLI review: ${localRosbag2CliReview}` : "",
      ]
        .filter(Boolean)
        .join("\n");
      const localEvidenceSummary = localEvidenceNotes ? `\n${localEvidenceNotes}` : "";

      setResult("local-autonomy-readiness", {
        status: result.exit_code === 0 && localReport ? "passed" : "failed",
        output: `$ local autonomy readiness\n${output || "(no output)"}${localEvidenceSummary}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshAutonomyReports();
      await refreshAutonomyWorkflowReports();
      await refreshFieldEvidenceReports();
      await refreshFeatureBenchmarkReports();
      await refreshThresholdTuningReports();
      await refreshRosbagValidationReports();
      await refreshFieldCollectionPlans();
      await refreshPx4PrereqReports();
      await refreshPx4ReceiverReports();
      await refreshSupportBundles();
    } catch (err) {
      setResult("local-autonomy-readiness", { status: "failed", output: `$ local autonomy readiness\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runAutonomyEvidenceWorkflow = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before running the autonomy evidence workflow.");
      return;
    }
    setRunningStep("autonomy-evidence-workflow");
    setError(null);
    setResult("autonomy-evidence-workflow", { status: "running", output: "$ autonomy evidence workflow\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        autonomyEvidenceWorkflowCommand(remoteProject, remoteBundle, fieldCase),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteWorkflow = parseAutonomyEvidenceWorkflowReport(output);
      const remoteWorkflowLogs = parseAutonomyEvidenceWorkflowLogs(output);
      const remoteWorkflowValidation = parseAutonomyEvidenceWorkflowValidation(output);
      const remoteSupportZip = parseSupportBundleZip(output);
      const remoteReport = parseAutonomyReadinessReport(output);
      const remoteHandoff = parseAutonomyReadinessHandoff(output);
      const remoteEvidencePackage = parseAutonomyEvidencePackage(output);
      const remoteFieldEvidenceReport = parseFieldEvidenceReport(output);
      const remoteFieldCollectionPlan = parseFieldCollectionPlan(output);
      const remoteFieldCollectionPlanMarkdown = parseFieldCollectionPlanMarkdown(output);
      const remoteFeatureMethodReport = parseFeatureMethodReport(output);
      const remoteThresholdReport = parseThresholdTuningReport(output);
      const remoteRosbagValidation = parseRosbagExportValidationReport(output);
      const remoteRosbag2CliReview = parseRosbag2CliReviewReport(output);
      const remotePx4Report = parsePx4SitlReport(output);
      const remotePx4Prereqs = parsePx4SitlPrereqs(output);
      if (!remoteWorkflow) {
        setResult("autonomy-evidence-workflow", {
          status: "failed",
          output: `$ autonomy evidence workflow\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("autonomy-evidence-workflow", {
        status: "running",
        output: `$ autonomy evidence workflow\n${output}\n\n$ download workflow report\nDownloading ${remoteWorkflow}...`,
      });
      const downloadedWorkflow = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteWorkflow,
        AUTONOMY_REPORT_DOWNLOAD_DIR,
      );
      setAutonomyWorkflowLocalPath(downloadedWorkflow.local_path);
      let downloadText = `\n\n$ download workflow report\nSaved to ${downloadedWorkflow.local_path}\n[${downloadedWorkflow.bytes_received} bytes]`;

      if (remoteWorkflowLogs) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download workflow logs\nDownloading ${remoteWorkflowLogs}...`,
        });
        const downloadedWorkflowLogs = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteWorkflowLogs,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download workflow logs\nSaved to ${downloadedWorkflowLogs.local_path}\n[${downloadedWorkflowLogs.bytes_received} bytes]`;
      }
      if (remoteWorkflowValidation) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download workflow validation\nDownloading ${remoteWorkflowValidation}...`,
        });
        const downloadedWorkflowValidation = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteWorkflowValidation,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download workflow validation\nSaved to ${downloadedWorkflowValidation.local_path}\n[${downloadedWorkflowValidation.bytes_received} bytes]`;
      }
      if (remoteSupportZip) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download support bundle\nDownloading ${remoteSupportZip}...`,
        });
        const downloadedSupport = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteSupportZip,
          SUPPORT_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download support bundle\nSaved to ${downloadedSupport.local_path}\n[${downloadedSupport.bytes_received} bytes]`;
      }
      if (remoteFieldEvidenceReport) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download field evidence report\nDownloading ${remoteFieldEvidenceReport}...`,
        });
        const downloadedFieldEvidence = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteFieldEvidenceReport,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download field evidence report\nSaved to ${downloadedFieldEvidence.local_path}\n[${downloadedFieldEvidence.bytes_received} bytes]`;
      }
      if (remoteFeatureMethodReport) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download feature benchmark\nDownloading ${remoteFeatureMethodReport}...`,
        });
        const downloadedFeatureBenchmark = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteFeatureMethodReport,
          FEATURE_BENCH_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download feature benchmark\nSaved to ${downloadedFeatureBenchmark.local_path}\n[${downloadedFeatureBenchmark.bytes_received} bytes]`;
      }
      if (remoteThresholdReport) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download threshold report\nDownloading ${remoteThresholdReport}...`,
        });
        const downloadedThreshold = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteThresholdReport,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download threshold report\nSaved to ${downloadedThreshold.local_path}\n[${downloadedThreshold.bytes_received} bytes]`;
      }
      if (remoteRosbagValidation) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download ROS bag validation\nDownloading ${remoteRosbagValidation}...`,
        });
        const downloadedRosbagValidation = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteRosbagValidation,
          ROSBAG_VALIDATION_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download ROS bag validation\nSaved to ${downloadedRosbagValidation.local_path}\n[${downloadedRosbagValidation.bytes_received} bytes]`;
      }
      if (remoteRosbag2CliReview) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download rosbag2 CLI review\nDownloading ${remoteRosbag2CliReview}...`,
        });
        const downloadedRosbag2Review = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteRosbag2CliReview,
          ROSBAG_VALIDATION_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download rosbag2 CLI review\nSaved to ${downloadedRosbag2Review.local_path}\n[${downloadedRosbag2Review.bytes_received} bytes]`;
      }
      if (remoteReport) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download readiness report\nDownloading ${remoteReport}...`,
        });
        const downloadedReport = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteReport,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download readiness report\nSaved to ${downloadedReport.local_path}\n[${downloadedReport.bytes_received} bytes]`;
      }
      if (remoteHandoff) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download readiness handoff\nDownloading ${remoteHandoff}...`,
        });
        const downloadedHandoff = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteHandoff,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setAutonomyHandoffLocalPath(downloadedHandoff.local_path);
        downloadText += `\n\n$ download readiness handoff\nSaved to ${downloadedHandoff.local_path}\n[${downloadedHandoff.bytes_received} bytes]`;
      }
      if (remoteEvidencePackage) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download evidence package\nDownloading ${remoteEvidencePackage}...`,
        });
        const downloadedPackage = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteEvidencePackage,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setAutonomyEvidencePackageLocalPath(downloadedPackage.local_path);
        downloadText += `\n\n$ download evidence package\nSaved to ${downloadedPackage.local_path}\n[${downloadedPackage.bytes_received} bytes]`;
      }
      if (remoteFieldCollectionPlan) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download field collection plan\nDownloading ${remoteFieldCollectionPlan}...`,
        });
        const downloadedPlan = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteFieldCollectionPlan,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setFieldCollectionPlanLocalPath(downloadedPlan.local_path);
        downloadText += `\n\n$ download field collection plan\nSaved to ${downloadedPlan.local_path}\n[${downloadedPlan.bytes_received} bytes]`;
      }
      if (remoteFieldCollectionPlanMarkdown) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download field collection checklist\nDownloading ${remoteFieldCollectionPlanMarkdown}...`,
        });
        const downloadedMarkdown = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteFieldCollectionPlanMarkdown,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setFieldCollectionPlanMarkdownLocalPath(downloadedMarkdown.local_path);
        downloadText += `\n\n$ download field collection checklist\nSaved to ${downloadedMarkdown.local_path}\n[${downloadedMarkdown.bytes_received} bytes]`;
      }
      if (remotePx4Report) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download PX4 receiver report\nDownloading ${remotePx4Report}...`,
        });
        const downloadedPx4 = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remotePx4Report,
          PX4_RECEIVER_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download PX4 receiver report\nSaved to ${downloadedPx4.local_path}\n[${downloadedPx4.bytes_received} bytes]`;
      }
      if (remotePx4Prereqs) {
        setResult("autonomy-evidence-workflow", {
          status: "running",
          output: `$ autonomy evidence workflow\n${output}${downloadText}\n\n$ download PX4 prereq report\nDownloading ${remotePx4Prereqs}...`,
        });
        const downloadedPx4Prereqs = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remotePx4Prereqs,
          PX4_RECEIVER_DOWNLOAD_DIR,
        );
        downloadText += `\n\n$ download PX4 prereq report\nSaved to ${downloadedPx4Prereqs.local_path}\n[${downloadedPx4Prereqs.bytes_received} bytes]`;
      }

      setResult("autonomy-evidence-workflow", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ autonomy evidence workflow\n${output}${downloadText}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshAutonomyWorkflowReports();
      await refreshSupportBundles();
      await refreshFieldEvidenceReports();
      await refreshFeatureBenchmarkReports();
      await refreshThresholdTuningReports();
      await refreshRosbagValidationReports();
      await refreshFieldCollectionPlans();
      await refreshAutonomyReports();
      await refreshPx4PrereqReports();
      await refreshPx4ReceiverReports();
    } catch (err) {
      setResult("autonomy-evidence-workflow", { status: "failed", output: `$ autonomy evidence workflow\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runThresholdTuning = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before running threshold tuning.");
      return;
    }
    setRunningStep("threshold-tuning");
    setError(null);
    setResult("threshold-tuning", { status: "running", output: "$ threshold tuning\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        `cd ${shellQuote(remoteProject)} && ./scripts/pi/run_threshold_tuning_report.sh`,
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteReport = parseThresholdTuningReport(output);
      if (!remoteReport) {
        setResult("threshold-tuning", {
          status: "failed",
          output: `$ threshold tuning\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("threshold-tuning", {
        status: "running",
        output: `$ threshold tuning\n${output}\n\n$ download threshold report\nDownloading ${remoteReport}...`,
      });
      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteReport,
        AUTONOMY_REPORT_DOWNLOAD_DIR,
      );
      setResult("threshold-tuning", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ threshold tuning\n${output}\n\n$ download threshold report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshThresholdTuningReports();
    } catch (err) {
      setResult("threshold-tuning", { status: "failed", output: `$ threshold tuning\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runRosbagExportValidation = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before validating the ROS bag export.");
      return;
    }
    setRunningStep("rosbag-validation");
    setError(null);
    setResult("rosbag-validation", { status: "running", output: "$ ROS bag validation\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        `cd ${shellQuote(remoteProject)} && ./scripts/pi/run_rosbag_export_validation.sh`,
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteReport = parseRosbagExportValidationReport(output);
      const remoteSourceLog = parseRosbagSourceLog(output);
      if (!remoteReport) {
        setResult("rosbag-validation", {
          status: "failed",
          output: `$ ROS bag validation\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("rosbag-validation", {
        status: "running",
        output: `$ ROS bag validation\n${output}\n\n$ download ROS bag validation\nDownloading ${remoteReport}...`,
      });
      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteReport,
        ROSBAG_VALIDATION_DOWNLOAD_DIR,
      );
      let sourceLogDownloadText = "";
      if (remoteSourceLog) {
        setResult("rosbag-validation", {
          status: "running",
          output: `$ ROS bag validation\n${output}\n\n$ download ROS bag validation\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n\n$ download terrain match log\nDownloading ${remoteSourceLog}...`,
        });
        try {
          const downloadedLog = await cmd.sshDownloadFile(
            form.host,
            form.port,
            form.username,
            resolvedAuth,
            remoteSourceLog,
            ROSBAG_VALIDATION_DOWNLOAD_DIR,
          );
          sourceLogDownloadText = `\n\n$ download terrain match log\nSaved to ${downloadedLog.local_path}\n[${downloadedLog.bytes_received} bytes]`;
        } catch (err) {
          sourceLogDownloadText = `\n\n$ download terrain match log\nFailed to download ${remoteSourceLog}: ${err}`;
        }
      }
      setResult("rosbag-validation", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ ROS bag validation\n${output}\n\n$ download ROS bag validation\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${sourceLogDownloadText}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshRosbagValidationReports();
    } catch (err) {
      setResult("rosbag-validation", { status: "failed", output: `$ ROS bag validation\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runLocalRosbag2CliReview = async () => {
    setRunningStep("local-rosbag2-cli-review");
    setError(null);
    setResult("local-rosbag2-cli-review", { status: "running", output: "$ native rosbag2 review\n" });
    try {
      const result = await cmd.runLocalRosbag2CliReview(repoPath, DESKTOP_TRANSFER_FROM_PI_DIR);
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const localReview = parseRosbag2CliReviewReport(output);
      const localReviewNotes = localReview ? `\nrosbag2 CLI review: ${localReview}` : "";
      setResult("local-rosbag2-cli-review", {
        status: result.exit_code === 0 && localReview ? "passed" : "failed",
        output: `$ native rosbag2 review\n${output || "(no output)"}${localReviewNotes}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
    } catch (err) {
      setResult("local-rosbag2-cli-review", { status: "failed", output: `$ native rosbag2 review\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runLocalPx4SitlPrereqSetup = async () => {
    setRunningStep("local-px4-sitl-prereqs");
    setError(null);
    setResult("local-px4-sitl-prereqs", { status: "running", output: "$ PX4 SITL prerequisite setup\n" });
    try {
      const result = await cmd.runLocalPx4SitlPrereqSetup(repoPath, DESKTOP_TRANSFER_FROM_PI_DIR);
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      setResult("local-px4-sitl-prereqs", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ PX4 SITL prerequisite setup\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
    } catch (err) {
      setResult("local-px4-sitl-prereqs", { status: "failed", output: `$ PX4 SITL prerequisite setup\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runLocalPx4SitlReceiverCapture = async () => {
    setRunningStep("local-px4-sitl-receiver");
    setError(null);
    setResult("local-px4-sitl-receiver", { status: "running", output: "$ PX4 SITL receiver capture\n" });
    try {
      const result = await cmd.runLocalPx4SitlReceiverCapture(repoPath, DESKTOP_TRANSFER_FROM_PI_DIR);
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const localPx4Report = parsePx4SitlReport(output);
      const localPx4Prereqs = parsePx4SitlPrereqs(output);
      const localPx4Notes = [
        localPx4Report ? `PX4 receiver report: ${localPx4Report}` : "",
        localPx4Prereqs ? `PX4 prerequisite report: ${localPx4Prereqs}` : "",
      ]
        .filter(Boolean)
        .join("\n");
      const localPx4Summary = localPx4Notes ? `\n${localPx4Notes}` : "";
      setResult("local-px4-sitl-receiver", {
        status: result.exit_code === 0 && localPx4Report ? "passed" : "failed",
        output: `$ PX4 SITL receiver capture\n${output || "(no output)"}${localPx4Summary}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshPx4PrereqReports();
      await refreshPx4ReceiverReports();
    } catch (err) {
      setResult("local-px4-sitl-receiver", { status: "failed", output: `$ PX4 SITL receiver capture\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const runFeatureMethodBenchmark = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before running feature-method benchmarks.");
      return;
    }
    setRunningStep("feature-benchmark");
    setError(null);
    setResult("feature-benchmark", { status: "running", output: "$ feature method benchmark\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        featureMethodBenchmarkCommand(remoteProject, remoteBundle, fieldCase),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteReport = parseFeatureMethodReport(output);
      if (!remoteReport) {
        setResult("feature-benchmark", {
          status: "failed",
          output: `$ feature method benchmark\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("feature-benchmark", {
        status: "running",
        output: `$ feature method benchmark\n${output}\n\n$ download feature benchmark report\nDownloading ${remoteReport}...`,
      });
      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteReport,
        FEATURE_BENCH_DOWNLOAD_DIR,
      );
      setResult("feature-benchmark", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ feature method benchmark\n${output}\n\n$ download feature benchmark report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshFeatureBenchmarkReports();
    } catch (err) {
      setResult("feature-benchmark", { status: "failed", output: `$ feature method benchmark\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const updateFieldCaptureMetadata = async () => {
    if (!firstFieldCondition(fieldCase.conditions)) {
      setError("Select a field condition before updating capture metadata.");
      return;
    }
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before updating field capture metadata.");
      return;
    }
    setRunningStep("field-metadata-update");
    setError(null);
    setResult("field-metadata-update", { status: "running", output: "$ Update Field Capture Metadata\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        fieldMetadataUpdateCommand(remoteProject, remoteBundle, fieldCase),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteManifest = parseFieldEvidenceManifest(output);
      const remotePlan = parseFieldCollectionPlan(output);
      const remoteMarkdown = parseFieldCollectionPlanMarkdown(output);
      let downloadText = "";
      if (remoteManifest) {
        setResult("field-metadata-update", {
          status: "running",
          output: `$ Update Field Capture Metadata\n${output}\n\n$ download active field manifest\nDownloading ${remoteManifest}...`,
        });
        const downloadedManifest = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteManifest,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setFieldManifestLocalPath(downloadedManifest.local_path);
        downloadText += `\n\n$ download active field manifest\nSaved to ${downloadedManifest.local_path}\n[${downloadedManifest.bytes_received} bytes]`;
      }
      if (remotePlan) {
        setResult("field-metadata-update", {
          status: "running",
          output: `$ Update Field Capture Metadata\n${output}${downloadText}\n\n$ download field collection plan\nDownloading ${remotePlan}...`,
        });
        const downloadedPlan = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remotePlan,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setFieldCollectionPlanLocalPath(downloadedPlan.local_path);
        downloadText += `\n\n$ download field collection plan\nSaved to ${downloadedPlan.local_path}\n[${downloadedPlan.bytes_received} bytes]`;
      }
      if (remoteMarkdown) {
        setResult("field-metadata-update", {
          status: "running",
          output: `$ Update Field Capture Metadata\n${output}${downloadText}\n\n$ download field checklist\nDownloading ${remoteMarkdown}...`,
        });
        const downloadedMarkdown = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteMarkdown,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setFieldCollectionPlanMarkdownLocalPath(downloadedMarkdown.local_path);
        downloadText += `\n\n$ download field checklist\nSaved to ${downloadedMarkdown.local_path}\n[${downloadedMarkdown.bytes_received} bytes]`;
      }
      setResult("field-metadata-update", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ Update Field Capture Metadata\n${output || "(no output)"}${downloadText}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshFieldEvidenceTemplates();
      await refreshFieldCollectionPlans();
    } catch (err) {
      setResult("field-metadata-update", { status: "failed", output: `$ Update Field Capture Metadata\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const registerFieldEvidenceCase = async () => {
    if (!fieldCase.caseName.trim() || !fieldCase.conditions.trim()) {
      setError("Field case name and condition tags are required.");
      return;
    }
    if (!fieldCaptureMetadataReady(fieldCase)) {
      setError(`Complete field capture metadata before registering evidence: ${fieldCaptureMetadataIssues(fieldCase).join(", ")}.`);
      return;
    }
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before registering field evidence.");
      return;
    }
    setRunningStep("field-evidence");
    setError(null);
    setResult("field-evidence", { status: "running", output: "$ Register Field Evidence Case\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        fieldEvidenceCommand(remoteProject, remoteBundle, fieldCase),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteReport = parseFieldEvidenceReport(output);
      if (!remoteReport) {
        setResult("field-evidence", {
          status: result.exit_code === 0 ? "passed" : "failed",
          output: `$ Register Field Evidence Case\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("field-evidence", {
        status: "running",
        output: `$ Register Field Evidence Case\n${output}\n\n$ download field evidence report\nDownloading ${remoteReport}...`,
      });
      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteReport,
        AUTONOMY_REPORT_DOWNLOAD_DIR,
      );
      setResult("field-evidence", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ Register Field Evidence Case\n${output}\n\n$ download field evidence report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshFieldEvidenceReports();
    } catch (err) {
      setResult("field-evidence", { status: "failed", output: `$ Register Field Evidence Case\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const createFieldEvidenceTemplate = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before creating a field evidence template.");
      return;
    }
    const siteName = safeReportName(form.name || form.host || "field-site");
    setRunningStep("field-template");
    setError(null);
    setResult("field-template", { status: "running", output: "$ Create Field Evidence Template\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        fieldEvidenceTemplateCommand(remoteProject, remoteBundle, siteName),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteTemplate = parseFieldEvidenceTemplate(output);
      const remoteManifest = parseFieldEvidenceManifest(output);
      if (!remoteTemplate) {
        setResult("field-template", {
          status: result.exit_code === 0 ? "passed" : "failed",
          output: `$ Create Field Evidence Template\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("field-template", {
        status: "running",
        output: `$ Create Field Evidence Template\n${output}\n\n$ download field evidence template\nDownloading ${remoteTemplate}...`,
      });
      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteTemplate,
        AUTONOMY_REPORT_DOWNLOAD_DIR,
      );
      setFieldTemplateLocalPath(downloaded.local_path);
      let manifestDownloadText = "";
      if (remoteManifest) {
        setResult("field-template", {
          status: "running",
          output: `$ Create Field Evidence Template\n${output}\n\n$ download field evidence template\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n\n$ download active field manifest\nDownloading ${remoteManifest}...`,
        });
        const downloadedManifest = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteManifest,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setFieldManifestLocalPath(downloadedManifest.local_path);
        manifestDownloadText = `\n\n$ download active field manifest\nSaved to ${downloadedManifest.local_path}\n[${downloadedManifest.bytes_received} bytes]`;
      } else {
        setFieldManifestLocalPath(null);
      }
      setResult("field-template", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ Create Field Evidence Template\n${output}\n\n$ download field evidence template\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]${manifestDownloadText}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshFieldEvidenceTemplates();
    } catch (err) {
      setResult("field-template", { status: "failed", output: `$ Create Field Evidence Template\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const createFieldCollectionPlan = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before creating a field collection plan.");
      return;
    }
    const siteName = safeReportName(form.name || form.host || "field-site");
    setRunningStep("field-collection-plan");
    setError(null);
    setResult("field-collection-plan", { status: "running", output: "$ Create Field Collection Plan\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        fieldCollectionPlanCommand(remoteProject, remoteBundle, siteName),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remotePlan = parseFieldCollectionPlan(output);
      const remoteMarkdown = parseFieldCollectionPlanMarkdown(output);
      if (!remotePlan) {
        setResult("field-collection-plan", {
          status: result.exit_code === 0 ? "passed" : "failed",
          output: `$ Create Field Collection Plan\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("field-collection-plan", {
        status: "running",
        output: `$ Create Field Collection Plan\n${output}\n\n$ download field collection plan\nDownloading ${remotePlan}...`,
      });
      const downloadedPlan = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remotePlan,
        AUTONOMY_REPORT_DOWNLOAD_DIR,
      );
      setFieldCollectionPlanLocalPath(downloadedPlan.local_path);
      let markdownDownloadText = "";
      if (remoteMarkdown) {
        setResult("field-collection-plan", {
          status: "running",
          output: `$ Create Field Collection Plan\n${output}\n\n$ download field collection plan\nSaved to ${downloadedPlan.local_path}\n[${downloadedPlan.bytes_received} bytes]\n\n$ download field checklist\nDownloading ${remoteMarkdown}...`,
        });
        const downloadedMarkdown = await cmd.sshDownloadFile(
          form.host,
          form.port,
          form.username,
          resolvedAuth,
          remoteMarkdown,
          AUTONOMY_REPORT_DOWNLOAD_DIR,
        );
        setFieldCollectionPlanMarkdownLocalPath(downloadedMarkdown.local_path);
        markdownDownloadText = `\n\n$ download field checklist\nSaved to ${downloadedMarkdown.local_path}\n[${downloadedMarkdown.bytes_received} bytes]`;
      } else {
        setFieldCollectionPlanMarkdownLocalPath(null);
      }

      setResult("field-collection-plan", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ Create Field Collection Plan\n${output}\n\n$ download field collection plan\nSaved to ${downloadedPlan.local_path}\n[${downloadedPlan.bytes_received} bytes]${markdownDownloadText}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshFieldCollectionPlans();
    } catch (err) {
      setResult("field-collection-plan", { status: "failed", output: `$ Create Field Collection Plan\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const syncProject = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before installing project files.");
      return false;
    }
    setRunningStep("sync");
    setError(null);
    setUploadProgress(null);
    setResult("sync", { status: "running", output: `$ module file sync\n${repoPath} -> ${remoteProject}` });
    const unlisten = await listen<UploadProgress>("upload-progress", (event) => {
      setUploadProgress(event.payload);
    });
    try {
      await cmd.sshUploadProject(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        repoPath,
        remoteProject,
      );
      setResult("sync", {
        status: "passed",
        output: `$ module file sync\nUploaded runtime module files to ${remoteProject}\n[exit 0]`,
        exitCode: 0,
      });
      return true;
    } catch (err) {
      setResult("sync", { status: "failed", output: `$ module file sync\nERROR: ${err}` });
      return false;
    } finally {
      unlisten();
      setRunningStep(null);
    }
  };

  const installModule = async () => {
    const synced = await syncProject();
    if (!synced) return;
    if (!auth() || !form.host) return;
    await runRemote(
      "bootstrap",
      "Module Installation",
      `cd ${shellQuote(remoteProject)} && chmod +x scripts/pi/*.sh && ${sudoPrefix()}./scripts/pi/bootstrap_pi5.sh`,
    );
  };

  const captureCameraFrame = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before opening the camera view.");
      return;
    }
    setCapturingCamera(true);
    setError(null);
    try {
      const frame = await cmd.sshCaptureCameraFrame(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteProject,
        960,
        720,
        1000,
      );
      setCameraPreview(`data:${frame.mime_type};base64,${frame.base64_data}`);
      setCameraPreviewPath(frame.remote_path);
      setResult("camera-preview", {
        status: "passed",
        output: `$ camera view test\nCaptured preview frame from ${frame.remote_path}\n${[frame.stdout, frame.stderr].filter(Boolean).join("\n").trim()}\n[exit 0]`,
        exitCode: 0,
      });
    } catch (err) {
      setResult("camera-preview", { status: "failed", output: `$ camera view test\nERROR: ${err}` });
    } finally {
      setCapturingCamera(false);
    }
  };

  useEffect(() => {
    if (!cameraAutoRefresh) return;
    let cancelled = false;
    let timer: number | undefined;

    const loop = async () => {
      await captureCameraFrame();
      if (!cancelled) timer = window.setTimeout(loop, 2500);
    };

    loop();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [
    cameraAutoRefresh,
    form.host,
    form.port,
    form.username,
    form.authMethod,
    form.password,
    form.keyPath,
    form.passphrase,
    remoteProject,
  ]);

  const sudoPrefix = () => {
    if (!sudoPassword) return "";
    return `printf ${shellQuote(`${sudoPassword}\n`)} | sudo -S -p '' -v && `;
  };

  const steps: SetupStep[] = [
    {
      id: "identity",
      title: "Wi-Fi SSH Identity",
      detail: "Confirms the app is talking to the expected module on the local network.",
      command: () => "whoami && hostname && hostname -I && printf '\\n'; sed -n '1,6p' /etc/os-release",
      recommended: true,
    },
    {
      id: "repo",
      title: "Project Files",
      detail: "Checks that the runtime repo exists at the configured module path.",
      command: () =>
        `cd ${shellQuote(remoteProject)} && test -d src/vision_nav && test -x scripts/pi/bootstrap_pi5.sh && pwd && echo 'Drone repo files ready'`,
      recommended: true,
    },
    {
      id: "bootstrap",
      title: "Module Installation",
      detail: "Installs runtime dependencies and service support. Requires sudo and a reboot afterward.",
      command: () =>
        `cd ${shellQuote(remoteProject)} && chmod +x scripts/pi/*.sh && ${sudoPrefix()}./scripts/pi/bootstrap_pi5.sh`,
      requiresSudo: true,
    },
    {
      id: "verify",
      title: "Verify System Setup",
      detail: "Checks SSH, Docker, transfer folders, and the Python vision environment.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/verify_pi_setup.sh`,
      recommended: true,
    },
    {
      id: "camera",
      title: "Camera Health",
      detail: "Captures from the module camera and measures image quality when camera tools are available.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/check_global_shutter_camera.sh`,
      recommended: true,
    },
    {
      id: "time-sync",
      title: "Time Sync",
      detail: "Checks system clock plausibility and NTP synchronization for timestamped external-vision output.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/check_time_sync.sh`,
      recommended: true,
    },
    {
      id: "mavlink",
      title: "MAVLink Endpoint",
      detail: "Validates MAVLink endpoint syntax and serial-device access. Set probe mode for live telemetry.",
      command: () =>
        `cd ${shellQuote(remoteProject)} && VISION_NAV_MAVLINK_ENDPOINT=${shellQuote(form.mavlinkEndpoint)} ./scripts/pi/check_mavlink_endpoint.sh`,
      recommended: true,
    },
    {
      id: "xrce-dds",
      title: "Micro XRCE-DDS Agent",
      detail: "Checks the optional PX4 uXRCE-DDS Agent used for ROS 2 telemetry and external-vision bench paths.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/check_micro_xrce_dds_agent.sh`,
    },
    {
      id: "local-px4-sitl-prereqs",
      title: "PX4 Prereq Setup",
      detail: "Dry-runs local tmux and PX4 checkout setup commands before receiver proof capture.",
      localOnly: true,
    },
    {
      id: "local-px4-sitl-receiver",
      title: "PX4 SITL Receiver Capture",
      detail: "Runs local PX4 SITL and captures receiver proof for the external-vision ODOMETRY path.",
      localOnly: true,
    },
    {
      id: "calibration-capture",
      title: "Calibration Capture",
      detail: "Captures a short chessboard image set for down-camera calibration when the target is ready.",
      command: () =>
        `cd ${shellQuote(remoteProject)} && CALIBRATION_COUNT=8 CALIBRATION_DELAY_S=1 ./scripts/pi/capture_calibration_set.sh`,
    },
    {
      id: "smoke",
      title: "Vision Smoke Test",
      detail: "Builds and matches a synthetic feature-map pair on the module.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/smoke_test_vision.sh`,
      recommended: true,
    },
    {
      id: "runtime",
      title: "Runtime Bundle Check",
      detail: "Validates the currently deployed mission bundle if one exists.",
      command: () =>
        `cd ${shellQuote(remoteProject)} && VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ./scripts/pi/validate_terrain_bundle.sh`,
    },
    {
      id: "bundle-diagnostic",
      title: "Bundle Diagnostic",
      detail: "Scans the selected mission bundle, bundle candidates, and saved map sources before support capture.",
    },
    {
      id: "runtime-status",
      title: "Runtime Status",
      detail: "Fetches the latest terrain runtime snapshot with active map, match, estimator, and external-position health.",
    },
    {
      id: "field-log-capture",
      title: "Field Log Capture",
      detail: "Runs a bounded 30-frame terrain capture on the module and downloads the replay log.",
    },
    {
      id: "bench-report",
      title: "Bench Report",
      detail: "Validates the deployed terrain bundle, creates a Pi support bundle, and downloads it.",
    },
    {
      id: "field-collection-plan",
      title: "Field Collection Plan",
      detail: "Creates and downloads a JSON/Markdown checklist for the remaining real-world replay cases.",
    },
    {
      id: "field-next-condition",
      title: "Load Next Field Condition",
      detail: "Loads the newest downloaded plan's next pending condition into the Field Evidence Case form.",
      localOnly: true,
    },
    {
      id: "field-capture-preflight",
      title: "Field Capture Preflight",
      detail: "Checks the selected field condition before terrain capture and downloads the preflight report.",
    },
    {
      id: "feature-benchmark",
      title: "Feature Benchmark",
      detail: "Compares ORB, AKAZE, SIFT, and neural placeholders on the latest field replay log.",
    },
    {
      id: "threshold-tuning",
      title: "Threshold Tuning",
      detail: "Generates and downloads the replay-gate threshold report from registered real field cases.",
    },
    {
      id: "rosbag-validation",
      title: "ROS Bag Validation",
      detail: "Exports the latest terrain log as ROS bag JSONL and downloads the validation report plus source log.",
    },
    {
      id: "local-rosbag2-cli-review",
      title: "Native rosbag2 Review",
      detail: "Runs the desktop ROS 2 rosbag2 CLI review against the downloaded terrain log.",
      localOnly: true,
    },
    {
      id: "autonomy-evidence-workflow",
      title: "Evidence Workflow",
      detail: "Attempts the ordered evidence sequence and downloads a per-step workflow report for support review.",
    },
    {
      id: "autonomy-readiness",
      title: "Autonomy Readiness",
      detail: "Runs the strict final audit against the latest Pi support bundle and field evidence artifacts.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/run_autonomy_readiness_audit.sh`,
    },
    {
      id: "local-autonomy-readiness",
      title: "Local Readiness Re-Audit",
      detail: "Re-runs the strict final audit against downloaded desktop evidence without connecting to the module.",
      localOnly: true,
    },
  ];

  const runSetupStep = async (step: SetupStep) => {
    if (step.id === "bench-report") {
      await createBenchReport();
      return;
    }
    if (step.id === "bundle-diagnostic") {
      await runBundleDiagnostic();
      return;
    }
    if (step.id === "runtime-status") {
      await fetchRuntimeStatus();
      return;
    }
    if (step.id === "field-log-capture") {
      await runFieldLogCapture();
      return;
    }
    if (step.id === "feature-benchmark") {
      await runFeatureMethodBenchmark();
      return;
    }
    if (step.id === "threshold-tuning") {
      await runThresholdTuning();
      return;
    }
    if (step.id === "rosbag-validation") {
      await runRosbagExportValidation();
      return;
    }
    if (step.id === "local-rosbag2-cli-review") {
      await runLocalRosbag2CliReview();
      return;
    }
    if (step.id === "local-px4-sitl-prereqs") {
      await runLocalPx4SitlPrereqSetup();
      return;
    }
    if (step.id === "local-px4-sitl-receiver") {
      await runLocalPx4SitlReceiverCapture();
      return;
    }
    if (step.id === "field-collection-plan") {
      await createFieldCollectionPlan();
      return;
    }
    if (step.id === "field-next-condition") {
      loadNextFieldCollectionCondition();
      return;
    }
    if (step.id === "field-capture-preflight") {
      await runFieldCapturePreflight();
      return;
    }
    if (step.id === "autonomy-evidence-workflow") {
      await runAutonomyEvidenceWorkflow();
      return;
    }
    if (step.id === "autonomy-readiness") {
      await runAutonomyReadiness();
      return;
    }
    if (step.id === "local-autonomy-readiness") {
      await runLocalAutonomyReadiness();
      return;
    }
    if (!step.command) return;
    await runRemote(step.id, step.title, step.command());
  };

  const runRecommendedChecks = async () => {
    for (const step of steps.filter((candidate) => candidate.recommended)) {
      await runSetupStep(step);
    }
  };

  const saveSetupReport = async () => {
    const stepResults = steps.map((step) => {
      const result = results[step.id];
      return {
        id: step.id,
        title: step.title,
        status: result?.status ?? "idle",
        exit_code: result?.exitCode ?? null,
        output: result?.output ?? null,
      };
    });
    const statusCounts = stepResults.reduce<Record<string, number>>((acc, result) => {
      acc[result.status] = (acc[result.status] ?? 0) + 1;
      return acc;
    }, {});
    const stepIds = new Set(steps.map((step) => step.id));
    const extraResults = Object.entries(results)
      .filter(([id]) => !stepIds.has(id))
      .map(([id, result]) => ({
        id,
        status: result.status,
        exit_code: result.exitCode ?? null,
        output: result.output ?? null,
      }));
    const supportBundleDiagnostics = await Promise.all(
      supportBundles.slice(0, 3).map(async (bundle) => {
        try {
          const details = await cmd.readSupportBundleDetails(bundle.path);
          return supportBundleDiagnosticsSnapshot(bundle, details);
        } catch (error) {
          return {
            name: bundle.name,
            path: bundle.path,
            size_bytes: bundle.size_bytes,
            error: String(error),
          };
        }
      }),
    );
    const latestAutonomyReport = autonomyReports[0] ?? null;
    const latestWorkflowReport = autonomyWorkflowReports[0] ?? null;
    const latestExternalBlockers = latestAutonomyReport?.evidence_manifest?.external_blockers ?? [];
    const latestProofItems = latestAutonomyReport?.evidence_manifest?.proof_items ?? [];
    const latestCompletionBlockers = latestAutonomyReport?.evidence_manifest?.completion_blockers ?? [];
    const latestProofRunbook =
      latestAutonomyReport?.proof_runbook ?? latestAutonomyReport?.evidence_package_summary?.proof_runbook_summary ?? null;
    const latestAuditMetadata =
      latestAutonomyReport?.metadata ?? latestAutonomyReport?.evidence_package_summary?.readiness_report_metadata ?? null;
    const report = {
      version: "0.1.0",
      generated_at: new Date().toISOString(),
      device: {
        name: form.name,
        host: form.host,
        port: form.port,
        username: form.username,
        auth_method: form.authMethod,
        remote_project_path: remoteProject,
        remote_bundle_path: remoteBundle,
        mavlink_endpoint: form.mavlinkEndpoint,
        ssh_fingerprint: connectionResult?.fingerprint ?? selectedDevice?.known_fingerprint ?? null,
      },
      field_evidence_case: fieldCase,
      mission_planner_handoff: activeHandoff,
      local: {
        repo_path: repoPath,
        support_bundle_download_dir: SUPPORT_DOWNLOAD_DIR,
        autonomy_report_download_dir: AUTONOMY_REPORT_DOWNLOAD_DIR,
        autonomy_workflow_local_path: autonomyWorkflowLocalPath,
        autonomy_handoff_local_path: autonomyHandoffLocalPath,
        autonomy_evidence_package_local_path: autonomyEvidencePackageLocalPath,
        field_template_local_path: fieldTemplateLocalPath,
        field_manifest_local_path: fieldManifestLocalPath,
        field_collection_plan_local_path: fieldCollectionPlanLocalPath,
        field_collection_plan_markdown_local_path: fieldCollectionPlanMarkdownLocalPath,
        field_capture_preflight_local_path: fieldCapturePreflightLocalPath,
        feature_benchmark_download_dir: FEATURE_BENCH_DOWNLOAD_DIR,
        px4_receiver_download_dir: PX4_RECEIVER_DOWNLOAD_DIR,
        rosbag_validation_download_dir: ROSBAG_VALIDATION_DOWNLOAD_DIR,
        runtime_status_download_dir: RUNTIME_STATUS_DOWNLOAD_DIR,
      },
      runtime_status: runtimeStatus
        ? {
            remote_path: runtimeStatusRemotePath,
            local_path: runtimeStatusLocalPath,
            snapshot: runtimeStatus,
          }
        : null,
      discovery: {
        candidates: discoveryCandidates.slice(0, 8),
        network_hints: networkHints,
        selected_network_hint: selectedDiscoveryHint,
        checklist: discoveryChecklist(),
      },
      connection_result: connectionResult
        ? {
            ok: connectionResult.ok,
            message: connectionResult.message,
            fingerprint: connectionResult.fingerprint ?? null,
          }
        : null,
      camera_preview_remote_path: cameraPreviewPath,
      status_counts: statusCounts,
      steps: stepResults,
      extra_results: extraResults,
      downloaded_support_bundles: supportBundles.slice(0, 5),
      support_bundle_diagnostics: supportBundleDiagnostics,
      autonomy_evidence_workflow_summary: latestWorkflowReport
        ? {
            name: latestWorkflowReport.name,
            path: latestWorkflowReport.path,
            size_bytes: latestWorkflowReport.size_bytes,
            modified_unix_ms: latestWorkflowReport.modified_unix_ms ?? null,
            generated_at: latestWorkflowReport.generated_at ?? null,
            status: latestWorkflowReport.status ?? null,
            summary: latestWorkflowReport.summary,
            marker_count: latestWorkflowReport.marker_count,
            field_metadata_update_command: latestWorkflowReport.field_metadata_update_command ?? null,
            workflow_logs_path: latestWorkflowReport.workflow_logs_local_path ?? latestWorkflowReport.workflow_logs_path ?? null,
            workflow_validation_path:
              latestWorkflowReport.workflow_validation_local_path ?? latestWorkflowReport.workflow_validation_path ?? null,
            rosbag_validation_path:
              latestWorkflowReport.rosbag_validation_local_path ?? latestWorkflowReport.rosbag_validation_path ?? null,
            validation: latestWorkflowReport.workflow_validation_summary
              ? {
                      status: latestWorkflowReport.workflow_validation_summary.status ?? null,
                      workflow_status: latestWorkflowReport.workflow_validation_summary.workflow_status ?? null,
                      next_required_step: latestWorkflowReport.workflow_validation_summary.next_required_step ?? null,
                      issue_count: latestWorkflowReport.workflow_validation_summary.issue_count,
                      missing_required_step_count:
                        latestWorkflowReport.workflow_validation_summary.missing_required_step_count ?? null,
                      active_required_step_count:
                        latestWorkflowReport.workflow_validation_summary.active_required_step_count ?? null,
                      downstream_blocked_step_count:
                        latestWorkflowReport.workflow_validation_summary.downstream_blocked_step_count ?? null,
                      superseded_step_count:
                        latestWorkflowReport.workflow_validation_summary.superseded_step_count ?? null,
                  issues: latestWorkflowReport.workflow_validation_summary.issues.slice(0, 8),
                  checks: latestWorkflowReport.workflow_validation_summary.checks.slice(0, 8).map((check) => ({
                    name: check.name ?? null,
                    status: check.status ?? null,
                    message: check.message ?? null,
                    marker_count: check.marker_count ?? null,
                    missing_markers: check.missing_markers.slice(0, 16),
                    present_markers: check.present_markers.slice(0, 16),
                  })),
                  log_archive: latestWorkflowReport.workflow_validation_summary.log_archive ?? null,
                }
              : null,
          }
        : null,
      autonomy_readiness_summary: latestAutonomyReport
        ? {
            name: latestAutonomyReport.name,
            path: latestAutonomyReport.path,
            size_bytes: latestAutonomyReport.size_bytes,
            modified_unix_ms: latestAutonomyReport.modified_unix_ms ?? null,
            handoff_path: latestAutonomyReport.handoff_path ?? null,
            handoff_size_bytes: latestAutonomyReport.handoff_size_bytes ?? null,
            evidence_package_path: latestAutonomyReport.evidence_package_path ?? null,
            evidence_package_size_bytes: latestAutonomyReport.evidence_package_size_bytes ?? null,
            evidence_package_summary: latestAutonomyReport.evidence_package_summary ?? null,
            audit_metadata: latestAuditMetadata,
            plan_snapshot:
              latestAutonomyReport.plan_snapshot ?? latestAutonomyReport.evidence_package_summary?.plan_snapshot ?? null,
            workflow_report_path:
              latestAutonomyReport.workflow_report_local_path ?? latestAutonomyReport.workflow_report_path ?? null,
            workflow_validation_path:
              latestAutonomyReport.workflow_validation_local_path ?? latestAutonomyReport.workflow_validation_path ?? null,
            workflow_log_archive_path:
              latestAutonomyReport.workflow_log_archive_local_path ?? latestAutonomyReport.workflow_log_archive_path ?? null,
            status: latestAutonomyReport.summary.status ?? null,
            failed_count: latestAutonomyReport.summary.failed_count ?? 0,
            degraded_count: latestAutonomyReport.summary.degraded_count ?? 0,
            passed_count: latestAutonomyReport.summary.passed_count ?? 0,
            ready_for_goal_completion:
              latestAutonomyReport.evidence_manifest?.ready_for_goal_completion ?? null,
            proof_item_count: latestProofItems.length,
            proof_item_passed_count: latestProofItems.filter((item) => item.status === "passed").length,
            proof_items: latestProofItems.slice(0, 12),
            proof_runbook: latestProofRunbook
              ? {
                  schema_version: latestProofRunbook.schema_version ?? null,
                  ready_for_goal_completion: latestProofRunbook.ready_for_goal_completion ?? null,
                  phases_truncated: latestProofRunbook.phases_truncated ?? null,
                  summary: latestProofRunbook.summary,
                  phases: latestProofRunbook.phases.slice(0, 8),
                }
              : null,
            completion_blocker_count: latestCompletionBlockers.length,
            completion_blockers: latestCompletionBlockers.slice(0, 8),
            external_blocker_count: latestExternalBlockers.length,
            external_blockers: latestExternalBlockers.slice(0, 8),
            next_action_count: latestAutonomyReport.next_actions.length,
            next_actions: latestAutonomyReport.next_actions.slice(0, 8),
            command_bundle:
              latestAutonomyReport.command_bundle ?? latestAutonomyReport.evidence_package_summary?.command_bundle ?? null,
            field_collection_plan: latestAutonomyReport.field_collection_plan
              ? {
                  path: latestAutonomyReport.field_collection_plan.path,
                  status: latestAutonomyReport.field_collection_plan.status ?? null,
                  site_name: latestAutonomyReport.field_collection_plan.site_name ?? null,
                  manifest_path: latestAutonomyReport.field_collection_plan.manifest_path ?? null,
                  bundle: latestAutonomyReport.field_collection_plan.bundle ?? null,
                      summary: latestAutonomyReport.field_collection_plan.summary,
                      next_condition: latestAutonomyReport.field_collection_plan.next_condition ?? null,
                      pending_condition_count: latestAutonomyReport.field_collection_plan.pending_conditions.length,
                      pending_conditions: latestAutonomyReport.field_collection_plan.pending_conditions.slice(0, 8),
                    }
              : null,
          }
        : null,
      downloaded_autonomy_workflow_reports: autonomyWorkflowReports.slice(0, 5),
      downloaded_autonomy_reports: autonomyReports.slice(0, 5),
      downloaded_px4_prereq_reports: px4PrereqReports.slice(0, 5),
      downloaded_px4_receiver_reports: px4ReceiverReports.slice(0, 5),
      downloaded_field_evidence_templates: fieldEvidenceTemplates.slice(0, 5),
      downloaded_field_collection_plans: fieldCollectionPlans.slice(0, 5),
      downloaded_field_evidence_reports: fieldEvidenceReports.slice(0, 5),
      downloaded_feature_benchmark_reports: featureBenchmarkReports.slice(0, 5),
      downloaded_threshold_tuning_reports: thresholdTuningReports.slice(0, 5),
      downloaded_rosbag_validation_reports: rosbagValidationReports.slice(0, 5),
    };
    const defaultPath = `drone-module-setup-${safeReportName(form.host || form.name)}-${new Date().toISOString().slice(0, 10)}.json`;
    const path = await saveDialog({
      title: "Save module setup report",
      defaultPath,
      filters: [{ name: "JSON", extensions: ["json"] }],
    });
    if (path && typeof path === "string") {
      await writeTextFile(path, JSON.stringify(report, null, 2) + "\n");
      setSetupReportPath(path);
    }
  };

  const output = selectedOutputId ? results[selectedOutputId]?.output : undefined;
  const fieldPreflightFailedChecks = fieldCapturePreflightReport?.checks.filter((check) => check.status === "failed") ?? [];
  const fieldPreflightDegradedChecks = fieldCapturePreflightReport?.checks.filter((check) => check.status === "degraded") ?? [];
  const fieldPreflightVisibleChecks = [...fieldPreflightFailedChecks, ...fieldPreflightDegradedChecks].slice(0, 4);
  const fieldPreflightNextActions = fieldCapturePreflightReport?.next_actions.slice(0, 4) ?? [];

  return (
    <div className={cn(embedded ? "space-y-5" : "p-6 space-y-6 animate-fade-in")}>
      {!embedded && (
        <div>
          <h1 className="section-title">Module Setup</h1>
          <p className="text-slate-400 text-sm mt-1">
            Connect to the runtime module over local Wi-Fi, install it, and run vision-system checks.
          </p>
        </div>
      )}

      <div className="grid grid-cols-[0.95fr_1.05fr] gap-6">
        <div className="space-y-4">
          {embedded ? (
            <div className="card space-y-3">
              <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
                <Wifi size={15} className="text-cyan-400" /> Saved SSH Connection
              </h3>
              {connectionReady ? (
                <div className="rounded-lg border border-border bg-bg-card px-3 py-2">
                  <div className="text-xs text-slate-400">Using saved device connection</div>
                  <div className="font-mono text-xs text-slate-200 mt-1">{form.username}@{form.host}:{form.port}</div>
                </div>
              ) : (
                <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  Edit this device and add SSH connection details before installing the module.
                </div>
              )}
              {error && (
                <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                  {error}
                </div>
              )}
            </div>
          ) : (
          <div className="card space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
                <Wifi size={15} className="text-cyan-400" /> SSH Connection
              </h3>
              {!embedded && (
                <select
                  className="input-field max-w-52 text-xs"
                  value={selectedDeviceId}
                  onChange={(event) => setSelectedDeviceId(event.target.value)}
                >
                  <option value="new">New module</option>
                  {piDevices.map((device) => (
                    <option key={device.id} value={device.id}>{device.name}</option>
                  ))}
                </select>
              )}
            </div>

            <div className="grid grid-cols-3 gap-2">
              {HOST_SUGGESTIONS.map((host) => (
                <button
                  key={host}
                  onClick={() => setForm((value) => ({ ...value, host }))}
                  className={cn(
                    "rounded-lg border px-2 py-2 text-[11px] font-mono transition-colors",
                    form.host === host ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-300" : "border-border text-slate-500 hover:text-slate-300",
                  )}
                >
                  {host}
                </button>
              ))}
            </div>

            <div className="rounded-lg border border-border bg-bg-card p-3 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-200 flex items-center gap-2">
                    <Wifi size={14} className="text-cyan-400" /> Discovery
                  </div>
                  <p className="text-[11px] text-slate-500 mt-0.5">Scan saved hosts, mDNS names, and local SSH neighbors.</p>
                </div>
                <button onClick={runDiscovery} disabled={discovering} className="btn-secondary text-xs py-1 px-3">
                  {discovering ? <Loader2 size={11} className="animate-spin" /> : <Wifi size={11} />}
                  Scan
                </button>
              </div>
              {discoveryError && (
                <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                  {discoveryError}
                </div>
              )}
              <div className="rounded-lg border border-border bg-bg-base px-3 py-2 space-y-2">
                <div className="flex items-center justify-between gap-3">
                  <label className="text-[11px] uppercase tracking-wide text-slate-500">Network adapter</label>
                  <span className={discoverySummary.status === "ready" ? "badge-green text-[10px]" : discoverySummary.status === "blocked" ? "badge-red text-[10px]" : "badge-yellow text-[10px]"}>
                    {discoverySummary.status === "ready" ? <CheckCircle2 size={11} /> : <XCircle size={11} />}
                    {discoverySummary.label}
                  </span>
                </div>
                <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
                  {networkHints.length > 0 ? (
                    <select
                      className="input-field text-xs"
                      value={selectedDiscoveryHint ? networkHintKey(selectedDiscoveryHint) : ""}
                      onChange={(event) => setSelectedAdapterKey(event.target.value)}
                    >
                      {networkHints.map((hint) => (
                        <option key={networkHintKey(hint)} value={networkHintKey(hint)}>
                          {networkHintLabel(hint)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <div className="rounded-md border border-border bg-bg-card px-2 py-1.5 text-[11px] text-slate-500">
                      No private Wi-Fi/Ethernet adapter detected yet.
                    </div>
                  )}
                  <button onClick={copyDiscoveryChecklist} className="btn-secondary text-xs py-1 px-2">
                    <Copy size={11} />
                    {discoveryCopied ? "Copied" : "Checklist"}
                  </button>
                </div>
                <p className="text-[11px] text-slate-500">{discoverySummary.detail}</p>
              </div>
              {discoveryCandidates.length > 0 && (
                <div className="space-y-2">
                  {discoveryCandidates.slice(0, 4).map((candidate) => (
                    <div key={`${candidate.host}:${candidate.port}`} className="flex items-center gap-2 rounded-lg border border-border/70 bg-bg-base px-2 py-1.5">
                      <span className={candidate.ssh_open ? "badge-green text-[10px]" : "badge-red text-[10px]"}>
                        {candidate.ssh_open ? "SSH" : "offline"}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="font-mono text-[11px] text-slate-300 truncate">{candidateHost(candidate)}</div>
                        <div className="text-[10px] text-slate-500 truncate">{candidate.source} · {candidate.message}</div>
                      </div>
                      <button onClick={() => useDiscoveredDevice(candidate)} className="btn-secondary text-xs py-1 px-2">
                        Use
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {discoveryTips.length > 0 && (
                <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 space-y-1">
                  {discoveryTips.map((item) => (
                    <div key={item} className="text-[11px] text-amber-200">{item}</div>
                  ))}
                </div>
              )}
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <label className="label">Module hostname or IP</label>
                <input
                  className="input-field"
                  value={form.host}
                  onChange={(event) => setForm({ ...form, host: event.target.value })}
                  placeholder="dronecompute.local or 192.168.1.x"
                />
              </div>
              <div>
                <label className="label">SSH port</label>
                <input
                  className="input-field"
                  type="number"
                  value={form.port}
                  onChange={(event) => setForm({ ...form, port: Number(event.target.value) })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Device name</label>
                <input
                  className="input-field"
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </div>
              <div>
                <label className="label">Username</label>
                <input
                  className="input-field"
                  value={form.username}
                  onChange={(event) => {
                    const username = event.target.value;
                    setForm({ ...form, username, remotePath: defaultRemotePath(username) });
                  }}
                />
              </div>
            </div>

            <div>
              <label className="label">Auth method</label>
              <div className="grid grid-cols-2 gap-2">
                {(["password", "key"] as const).map((method) => (
                  <button
                    key={method}
                    onClick={() => setForm({ ...form, authMethod: method })}
                    className={cn(
                      "py-2 rounded-lg border text-sm font-medium transition-colors flex items-center justify-center gap-1.5",
                      form.authMethod === method
                        ? "bg-cyan-500/10 border-cyan-500/40 text-cyan-400"
                        : "border-border text-slate-400",
                    )}
                  >
                    {method === "password" ? <Lock size={13} /> : <KeyRound size={13} />}
                    {method === "password" ? "Password" : "SSH Key"}
                  </button>
                ))}
              </div>
            </div>

            {form.authMethod === "password" ? (
              <div>
                <label className="label">SSH password</label>
                <div className="relative">
                  <input
                    className="input-field pr-10"
                    type={showPassword ? "text" : "password"}
                    value={form.password}
                    onChange={(event) => setForm({ ...form, password: event.target.value })}
                  />
                  <button onClick={() => setShowPassword(!showPassword)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                    {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="label">SSH private key</label>
                  <div className="flex gap-2">
                    <input
                      className="input-field flex-1 font-mono text-xs"
                      value={form.keyPath}
                      onChange={(event) => setForm({ ...form, keyPath: event.target.value })}
                      placeholder="~/.ssh/id_ed25519"
                    />
                    <button onClick={browseForKey} className="btn-secondary px-3">
                      <FolderOpen size={14} />
                    </button>
                  </div>
                </div>
                <div>
                  <label className="label">Key passphrase</label>
                  <div className="relative">
                    <input
                      className="input-field pr-10"
                      type={showPassphrase ? "text" : "password"}
                      value={form.passphrase}
                      onChange={(event) => setForm({ ...form, passphrase: event.target.value })}
                    />
                    <button onClick={() => setShowPassphrase(!showPassphrase)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                      {showPassphrase ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>
              </div>
            )}

            <div className="grid grid-cols-[1fr_auto_auto] gap-2">
              <button onClick={testConnection} disabled={!connectionReady || testing} className="btn-primary justify-center">
                {testing ? <Loader2 size={15} className="animate-spin" /> : <Wifi size={15} />}
                Test Wi-Fi SSH
              </button>
              <button onClick={saveConnection} disabled={!connectionReady} className="btn-secondary px-3">
                <Save size={14} /> Save
              </button>
              <button onClick={() => navigator.clipboard.writeText(`${form.username}@${form.host}`)} className="btn-secondary px-3">
                <Copy size={14} />
              </button>
            </div>

            {connectionResult && (
              <div className={cn(
                "rounded-lg border px-3 py-2 text-xs space-y-1",
                connectionResult.ok ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300" : "border-red-500/20 bg-red-500/10 text-red-300",
              )}>
                <div className="flex items-center gap-2">
                  {connectionResult.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                  <span>{connectionResult.message}</span>
                </div>
                {connectionResult.fingerprint && (
                  <div className="font-mono text-[10px] text-slate-400 break-all">{connectionResult.fingerprint}</div>
                )}
              </div>
            )}

            {error && (
              <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {error}
              </div>
            )}
          </div>
          )}

          {activeHandoff && (
            <div className="card space-y-3 border-cyan-500/30 bg-cyan-500/5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
                    <Archive size={15} className="text-cyan-400" /> Mission Bundle Handoff
                  </h3>
                  <p className="text-[11px] text-slate-500 mt-1">
                    Mission Planner uploaded this bundle for field preflight and bench validation.
                  </p>
                </div>
                <button onClick={clearSetupHandoff} className="btn-ghost text-xs py-1 px-2">
                  Clear
                </button>
              </div>
              <div className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-1">
                <div className="flex justify-between gap-3 text-[11px]">
                  <span className="text-slate-500">Region</span>
                  <span className="text-slate-300 truncate">{activeHandoff.region_name || "n/a"}</span>
                </div>
                <div className="flex justify-between gap-3 text-[11px]">
                  <span className="text-slate-500">Uploaded</span>
                  <span className="text-slate-300">{activeHandoff.uploaded_at ? new Date(activeHandoff.uploaded_at).toLocaleString() : "n/a"}</span>
                </div>
                <div className="text-[10px] font-mono text-slate-500 truncate">{remoteBundle}</div>
              </div>
              <div className="grid grid-cols-4 gap-2">
                <button onClick={runBundleDiagnostic} disabled={!connectionReady || !!runningStep} className="btn-secondary justify-center text-xs px-2">
                  {runningStep === "bundle-diagnostic" ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />}
                  Diagnose
                </button>
                <button onClick={createFieldCollectionPlan} disabled={!connectionReady || !!runningStep} className="btn-secondary justify-center text-xs px-2">
                  {runningStep === "field-collection-plan" ? <Loader2 size={14} className="animate-spin" /> : <ClipboardCheck size={14} />}
                  Plan
                </button>
                <button onClick={runFieldCapturePreflight} disabled={!connectionReady || !!runningStep} className="btn-secondary justify-center text-xs px-2">
                  {runningStep === "field-capture-preflight" ? <Loader2 size={14} className="animate-spin" /> : <TestTube2 size={14} />}
                  Preflight
                </button>
                <button onClick={createBenchReport} disabled={!connectionReady || !!runningStep} className="btn-primary justify-center text-xs px-2">
                  {runningStep === "bench-report" ? <Loader2 size={14} className="animate-spin" /> : <Archive size={14} />}
                  Bench
                </button>
              </div>
              <button
                onClick={runAutonomyEvidenceWorkflow}
                disabled={!connectionReady || !!runningStep}
                className="btn-secondary w-full justify-center text-xs"
              >
                {runningStep === "autonomy-evidence-workflow" ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
                Run Guided Evidence Workflow
              </button>
            </div>
          )}

          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <HardDriveUpload size={15} className="text-cyan-400" /> Module Installation
            </h3>
            <div>
              <label className="label">Local Drone repo</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 font-mono text-xs" value={repoPath} onChange={(event) => setRepoPath(event.target.value)} />
                <button onClick={browseForRepo} className="btn-secondary px-3"><FolderOpen size={14} /></button>
              </div>
            </div>
            <div>
              <label className="label">Module project path</label>
              <input
                className="input-field font-mono text-xs"
                value={remoteProject}
                onChange={(event) => setForm({ ...form, remotePath: event.target.value })}
              />
            </div>
            <button onClick={installModule} disabled={!connectionReady || !!runningStep || !sudoPassword} className="btn-primary w-full justify-center">
              {runningStep === "sync" || runningStep === "bootstrap" ? <Loader2 size={14} className="animate-spin" /> : <HardDriveUpload size={14} />}
              Install Module
            </button>
            {uploadProgress && runningStep === "sync" && (
              <div className="space-y-1">
                <div className="flex justify-between text-[10px] text-slate-500">
                  <span className="truncate">{uploadProgress.file}</span>
                  <span>{uploadProgress.percent.toFixed(0)}%</span>
                </div>
                <div className="h-2 bg-bg-elevated rounded-full overflow-hidden">
                  <div className="h-full bg-cyan-500 rounded-full" style={{ width: `${uploadProgress.percent}%` }} />
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-4">
          <div className="card space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
                <ShieldCheck size={15} className="text-cyan-400" /> Checks And Tests
              </h3>
              <div className="flex items-center gap-2">
                <button onClick={saveSetupReport} disabled={Object.keys(results).length === 0} className="btn-secondary text-xs py-1.5 px-3">
                  <Save size={12} /> Save Report
                </button>
                <button onClick={runRecommendedChecks} disabled={!connectionReady || !!runningStep} className="btn-secondary text-xs py-1.5 px-3">
                  <TestTube2 size={12} /> Run Checks/Tests
                </button>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Sudo password</label>
                <div className="relative">
                  <input
                    className="input-field pr-10"
                    type={showSudoPassword ? "text" : "password"}
                    value={sudoPassword}
                    onChange={(event) => setSudoPassword(event.target.value)}
                    placeholder="only needed for bootstrap"
                  />
                  <button onClick={() => setShowSudoPassword(!showSudoPassword)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                    {showSudoPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
              <div>
                <label className="label">Runtime bundle path</label>
                <input className="input-field font-mono text-xs" value={remoteBundle} readOnly />
              </div>
            </div>

            <RuntimeStatusCard
              status={runtimeStatus}
              remotePath={runtimeStatusRemotePath}
              localPath={runtimeStatusLocalPath}
              onRefresh={fetchRuntimeStatus}
              busy={runningStep === "runtime-status"}
              disabled={!connectionReady || !!runningStep}
            />

            <div className="rounded-lg border border-border bg-bg-card p-3 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-200 flex items-center gap-2">
                    <ShieldCheck size={14} className="text-cyan-400" /> Field Evidence Case
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={createFieldEvidenceTemplate}
                    disabled={!connectionReady || !!runningStep}
                    className="btn-secondary text-xs py-1 px-3"
                  >
                    {runningStep === "field-template" ? <Loader2 size={11} className="animate-spin" /> : <FileText size={11} />}
                    Create Template
                  </button>
                  <button
                    onClick={createFieldCollectionPlan}
                    disabled={!connectionReady || !!runningStep}
                    className="btn-secondary text-xs py-1 px-3"
                  >
                    {runningStep === "field-collection-plan" ? <Loader2 size={11} className="animate-spin" /> : <FileText size={11} />}
                    Create Plan
                  </button>
                  <button
                    onClick={runFieldCapturePreflight}
                    disabled={!connectionReady || !!runningStep}
                    className="btn-secondary text-xs py-1 px-3"
                  >
                    {runningStep === "field-capture-preflight" ? <Loader2 size={11} className="animate-spin" /> : <TestTube2 size={11} />}
                    Preflight
                  </button>
                  <button
                    onClick={updateFieldCaptureMetadata}
                    disabled={!connectionReady || !!runningStep || !firstFieldCondition(fieldCase.conditions)}
                    className="btn-secondary text-xs py-1 px-3"
                  >
                    {runningStep === "field-metadata-update" ? <Loader2 size={11} className="animate-spin" /> : <FileText size={11} />}
                    Update Metadata
                  </button>
                  <button
                    onClick={registerFieldEvidenceCase}
                    disabled={!connectionReady || !!runningStep || !fieldCase.caseName.trim() || !fieldCase.conditions.trim() || !fieldMetadataReady}
                    className="btn-secondary text-xs py-1 px-3"
                  >
                    {runningStep === "field-evidence" ? <Loader2 size={11} className="animate-spin" /> : <ShieldCheck size={11} />}
                    Register
                  </button>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-[10px] font-mono">
                <span className={fieldMetadataReady ? "badge-green" : "badge-yellow"}>
                  {fieldMetadataReady ? "metadata ready" : "metadata incomplete"}
                </span>
                <span className="text-slate-500">condition {firstFieldCondition(fieldCase.conditions) || "n/a"}</span>
              </div>
              {!fieldMetadataReady && (
                <div className="flex flex-wrap gap-1.5 text-[10px]">
                  {fieldMetadataIssues.map((issue) => (
                    <span key={issue} className="rounded border border-amber-500/20 bg-amber-500/10 px-2 py-0.5 text-amber-300">
                      {issue}
                    </span>
                  ))}
                </div>
              )}
              {fieldCapturePreflightReport && (
                <div className="space-y-3 border-t border-border pt-3">
                  <div className="flex flex-wrap items-center gap-2 text-[10px] font-mono">
                    <span className={cn(readinessBadgeClass(fieldCapturePreflightReport.status), "text-[10px]")}>
                      preflight {formatReadinessLabel(fieldCapturePreflightReport.status)}
                    </span>
                    <span className={fieldCapturePreflightReport.ready_for_capture ? "badge-green text-[10px]" : "badge-red text-[10px]"}>
                      capture {fieldCapturePreflightReport.ready_for_capture ? "ready" : "blocked"}
                    </span>
                    <span className={fieldCapturePreflightReport.ready_for_registration ? "badge-green text-[10px]" : "badge-yellow text-[10px]"}>
                      registration {fieldCapturePreflightReport.ready_for_registration ? "ready" : "waiting"}
                    </span>
                    <span className="text-slate-500">condition {fieldCapturePreflightReport.condition ?? "n/a"}</span>
                  </div>
                  <div className="grid grid-cols-1 xl:grid-cols-2 gap-2 text-[11px]">
                    <div className="min-w-0 text-slate-400">
                      Bundle{" "}
                      <span className="font-mono text-slate-300 break-all">
                        {fieldCapturePreflightReport.bundle_path ?? "n/a"}
                      </span>
                    </div>
                    <div className="min-w-0 text-slate-400">
                      Capture output{" "}
                      <span className="font-mono text-slate-300 break-all">
                        {fieldCapturePreflightReport.capture_output_dir ?? "n/a"}
                      </span>
                    </div>
                    <div className="min-w-0 text-slate-400">
                      Source log{" "}
                      <span className="font-mono text-slate-300 break-all">
                        {fieldCapturePreflightReport.source_log ?? "n/a"}
                      </span>
                    </div>
                    <div className="min-w-0 text-slate-400">
                      Runtime status{" "}
                      <span className="font-mono text-slate-300 break-all">
                        {fieldCapturePreflightReport.runtime_status_path ?? "n/a"}
                      </span>
                    </div>
                    <div className="min-w-0 text-slate-400">
                      Capture script{" "}
                      <span className="font-mono text-slate-300 break-all">
                        {fieldCapturePreflightReport.capture_script_path ?? "n/a"}
                      </span>
                    </div>
                    {fieldCapturePreflightReport.capture_script_hint && (
                      <div className="min-w-0 text-amber-200/80">
                        Capture script hint{" "}
                        <span className="font-mono break-all">
                          {fieldCapturePreflightReport.capture_script_hint}
                        </span>
                      </div>
                    )}
                  </div>
                  <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                    <div className="space-y-2">
                      <div className="text-[10px] uppercase tracking-wide text-slate-500">Blocking checks</div>
                      {fieldPreflightVisibleChecks.length ? (
                        fieldPreflightVisibleChecks.map((check) => (
                          <div key={`${check.name ?? "check"}-${check.status ?? "status"}`} className="rounded-md border border-border/70 px-2 py-2 space-y-1">
                            <div className="flex items-center justify-between gap-2">
                              <span className="min-w-0 truncate text-xs text-slate-200">{formatReadinessLabel(check.name ?? "check")}</span>
                              <span className={cn(readinessBadgeClass(check.status), "text-[10px] shrink-0")}>
                                {formatReadinessLabel(check.status)}
                              </span>
                            </div>
                            {check.message && <div className="text-[11px] text-slate-400">{check.message}</div>}
                            {check.validation_command && (
                              <button
                                type="button"
                                onClick={() => navigator.clipboard.writeText(check.validation_command ?? "")}
                                className="btn-secondary text-[10px] px-2 py-0.5"
                                title="Copy validation command"
                              >
                                <Copy size={10} /> Copy command
                              </button>
                            )}
                          </div>
                        ))
                      ) : (
                        <div className="rounded-md border border-emerald-500/20 px-2 py-2 text-[11px] text-emerald-300">
                          No failed or degraded checks in the latest preflight.
                        </div>
                      )}
                    </div>
                    <div className="space-y-2">
                      <div className="text-[10px] uppercase tracking-wide text-slate-500">Next actions</div>
                      {fieldPreflightNextActions.length ? (
                        fieldPreflightNextActions.map((action) => (
                          <div key={action.id ?? action.title ?? "next-action"} className="rounded-md border border-border/70 px-2 py-2 space-y-1">
                            <div className="flex items-center justify-between gap-2">
                              <span className="min-w-0 truncate text-xs text-slate-200">{action.title ?? formatReadinessLabel(action.id ?? "next action")}</span>
                              <span className={cn(readinessBadgeClass(action.status), "text-[10px] shrink-0")}>
                                {formatReadinessLabel(action.status)}
                              </span>
                            </div>
                            {action.desktop_action && <div className="text-[11px] text-cyan-300">{action.desktop_action}</div>}
                            {action.notes && <div className="text-[11px] text-slate-400">{action.notes}</div>}
                            {(action.waits_on ?? []).length > 0 && (
                              <div className="text-[10px] text-amber-300">
                                waits on {(action.waits_on ?? []).map(formatReadinessLabel).join(", ")}
                              </div>
                            )}
                            {action.bundle_diagnostic && (
                              <div className="space-y-1 rounded border border-cyan-500/20 bg-cyan-500/5 px-2 py-1 text-[10px] text-slate-400">
                                {action.bundle_diagnostic.missing_required_files.length > 0 && (
                                  <div className="flex flex-wrap gap-1">
                                    <span className="text-slate-500">missing</span>
                                    {action.bundle_diagnostic.missing_required_files.slice(0, 5).map((file) => (
                                      <span key={`${action.id ?? "action"}-missing-${file}`} className="rounded bg-bg-base/70 px-1 font-mono text-slate-300">
                                        {file}
                                      </span>
                                    ))}
                                    {action.bundle_diagnostic.missing_required_files.length > 5 && (
                                      <span className="text-slate-500">+{action.bundle_diagnostic.missing_required_files.length - 5}</span>
                                    )}
                                  </div>
                                )}
                                {action.bundle_diagnostic.bundle_candidates.length > 0 && (
                                  <div className="space-y-0.5">
                                    <div className="text-slate-500">
                                      bundle candidates {action.bundle_diagnostic.bundle_candidate_count ?? action.bundle_diagnostic.bundle_candidates.length}
                                    </div>
                                    {action.bundle_diagnostic.bundle_candidates.slice(0, 2).map((candidate) => (
                                      <div key={`${action.id ?? "action"}-candidate-${candidate.path}`} className="flex min-w-0 flex-wrap items-center gap-1">
                                        <span className="font-mono text-slate-300 break-all">{candidate.path ?? "n/a"}</span>
                                        {candidate.bundle_id && <span className="badge-yellow text-[10px]">{candidate.bundle_id}</span>}
                                        {candidate.field_proof_warning && (
                                          <span className="badge-yellow text-[10px]" title={candidate.field_proof_warning}>smoke only</span>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                )}
                                {action.bundle_diagnostic.map_source_candidates.length > 0 && (
                                  <div className="space-y-0.5">
                                    <div className="text-slate-500">
                                      map sources {action.bundle_diagnostic.map_source_candidate_count ?? action.bundle_diagnostic.map_source_candidates.length}
                                    </div>
                                    {action.bundle_diagnostic.map_source_candidates.slice(0, 2).map((source) => (
                                      <div key={`${action.id ?? "action"}-map-source-${source.path}`} className="flex min-w-0 flex-wrap items-center gap-1">
                                        <span className="font-mono text-slate-300 break-all">{source.path ?? "n/a"}</span>
                                        {source.name && <span className="badge-green text-[10px]">{formatReadinessLabel(source.name)}</span>}
                                        {source.source_format && <span className="badge-cyan text-[10px]">{formatReadinessLabel(source.source_format)}</span>}
                                        {source.georef_source && <span className="badge-yellow text-[10px]">{formatReadinessLabel(source.georef_source)}</span>}
                                        {source.requires_import && <span className="badge-yellow text-[10px]">import</span>}
                                      </div>
                                    ))}
                                  </div>
                                )}
                                {action.bundle_diagnostic.search_roots.length > 0 && (
                                  <div className="space-y-0.5">
                                    <div className="text-slate-500">
                                      searched roots {action.bundle_diagnostic.search_root_count ?? action.bundle_diagnostic.search_roots.length}
                                    </div>
                                    {action.bundle_diagnostic.search_roots.slice(0, 4).map((root) => (
                                      <div key={`${action.id ?? "action"}-search-root-${root}`} className="font-mono text-slate-300 break-all">
                                        {root}
                                      </div>
                                    ))}
                                  </div>
                                )}
                                {action.bundle_diagnostic.recommended_actions.length > 0 && (
                                  <div className="space-y-0.5">
                                    <div className="text-slate-500">recommended actions</div>
                                    {action.bundle_diagnostic.recommended_actions.slice(0, 3).map((recommendation, recommendationIndex) => (
                                      recommendation.command ? (
                                        <button
                                          key={`${action.id ?? "action"}-diagnostic-recommendation-${recommendation.id}-${recommendationIndex}`}
                                          type="button"
                                          onClick={() => recommendation.command && navigator.clipboard.writeText(recommendation.command)}
                                          className="flex w-full min-w-0 items-center gap-1.5 rounded border border-border/50 bg-bg-base/60 px-2 py-1 text-left font-mono text-[10px] text-slate-400 hover:border-cyan-500/40 hover:text-cyan-200"
                                          title={[
                                            recommendation.command,
                                            recommendation.mission_plan_path ? `Mission plan: ${recommendation.mission_plan_path}` : undefined,
                                            recommendation.qgc_plan_path ? `QGC plan: ${recommendation.qgc_plan_path}` : undefined,
                                            recommendation.notes,
                                          ].filter(Boolean).join("\n") || undefined}
                                        >
                                          <Copy size={9} className="shrink-0" />
                                          <span className={cn(readinessBadgeClass(recommendation.status), "text-[10px] shrink-0")}>
                                            {formatReadinessLabel(recommendation.status)}
                                          </span>
                                          <span className="truncate whitespace-pre">{recommendation.command}</span>
                                        </button>
                                      ) : (
                                        <div
                                          key={`${action.id ?? "action"}-diagnostic-recommendation-${recommendation.id}-${recommendationIndex}`}
                                          className="flex min-w-0 flex-wrap items-center gap-1 rounded border border-border/40 bg-bg-base/40 px-2 py-1"
                                          title={[
                                            recommendation.mission_plan_path ? `Mission plan: ${recommendation.mission_plan_path}` : undefined,
                                            recommendation.qgc_plan_path ? `QGC plan: ${recommendation.qgc_plan_path}` : undefined,
                                            recommendation.notes,
                                          ].filter(Boolean).join("\n") || undefined}
                                        >
                                          <span className={cn(readinessBadgeClass(recommendation.status), "text-[10px] shrink-0")}>
                                            {formatReadinessLabel(recommendation.status)}
                                          </span>
                                          <span className="text-slate-300">{formatReadinessLabel(recommendation.title ?? recommendation.id)}</span>
                                          {recommendation.desktop_action && <span className="font-mono text-slate-500">{recommendation.desktop_action}</span>}
                                          {recommendation.map_source_path && <span className="font-mono text-slate-500 break-all">{recommendation.map_source_path}</span>}
                                        </div>
                                      )
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}
                            {action.command && (
                              <button
                                type="button"
                                onClick={() => navigator.clipboard.writeText(action.command ?? "")}
                                className="btn-secondary text-[10px] px-2 py-0.5"
                                title="Copy next action command"
                              >
                                <Copy size={10} /> Copy command
                              </button>
                            )}
                          </div>
                        ))
                      ) : (
                        <div className="rounded-md border border-border/70 px-2 py-2 text-[11px] text-slate-400">
                          No next actions were reported.
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <div>
                  <label className="label">Case name</label>
                  <input
                    className="input-field font-mono text-xs"
                    value={fieldCase.caseName}
                    onChange={(event) => setFieldCase((value) => ({ ...value, caseName: event.target.value }))}
                  />
                </div>
                <div>
                  <label className="label">Expected behavior</label>
                  <select
                    className="input-field text-xs"
                    value={fieldCase.expected}
                    onChange={(event) => setFieldCase((value) => ({ ...value, expected: event.target.value as FieldExpected }))}
                  >
                    {FIELD_EXPECTED_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="label">Condition tags</label>
                  <input
                    className="input-field font-mono text-xs"
                    list="field-condition-options"
                    value={fieldCase.conditions}
                    onChange={(event) => setFieldCase((value) => ({ ...value, conditions: event.target.value }))}
                  />
                  <datalist id="field-condition-options">
                    {FIELD_CONDITION_OPTIONS.map((condition) => (
                      <option key={condition} value={condition} />
                    ))}
                  </datalist>
                </div>
                <div>
                  <label className="label">Field log</label>
                  <input
                    className="input-field font-mono text-xs"
                    value={fieldCase.fieldLog}
                    placeholder="$HOME/DroneTransfer/outgoing/field-captures/.../terrain_matches.jsonl"
                    onChange={(event) => setFieldCase((value) => ({ ...value, fieldLog: event.target.value }))}
                  />
                </div>
                <div>
                  <label className="label">Capture output</label>
                  <input
                    className="input-field font-mono text-xs"
                    value={fieldCase.captureOutputDir}
                    placeholder="$HOME/DroneTransfer/outgoing/field-captures/.../"
                    onChange={(event) => setFieldCase((value) => ({ ...value, captureOutputDir: event.target.value }))}
                  />
                </div>
                <div>
                  <label className="label">Runtime status</label>
                  <input
                    className="input-field font-mono text-xs"
                    value={fieldCase.runtimeStatusPath}
                    placeholder="$HOME/DroneTransfer/outgoing/field-captures/.../runtime_status.json"
                    onChange={(event) => setFieldCase((value) => ({ ...value, runtimeStatusPath: event.target.value }))}
                  />
                </div>
                <div>
                  <label className="label">Notes</label>
                  <input
                    className="input-field text-xs"
                    value={fieldCase.notes}
                    onChange={(event) => setFieldCase((value) => ({ ...value, notes: event.target.value }))}
                  />
                </div>
              </div>
              <div className="space-y-2">
                <div className="text-[10px] uppercase tracking-wide text-slate-500">Capture Metadata</div>
                <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
                  <div>
                    <label className="label">Site name</label>
                    <input
                      className="input-field text-xs"
                      value={fieldCase.siteName}
                      onChange={(event) => setFieldCase((value) => ({ ...value, siteName: event.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="label">Operator</label>
                    <input
                      className="input-field text-xs"
                      value={fieldCase.operator}
                      onChange={(event) => setFieldCase((value) => ({ ...value, operator: event.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="label">Captured UTC</label>
                    <div className="flex gap-1">
                      <input
                        className="input-field font-mono text-xs"
                        value={fieldCase.captureDateUtc}
                        onChange={(event) => setFieldCase((value) => ({ ...value, captureDateUtc: event.target.value }))}
                      />
                      <button
                        type="button"
                        onClick={() => setFieldCase((value) => ({ ...value, captureDateUtc: compactUtcNow() }))}
                        className="btn-secondary px-2"
                        title="Set current UTC time"
                      >
                        <RefreshCw size={11} />
                      </button>
                    </div>
                  </div>
                  <div>
                    <label className="label">Location label</label>
                    <input
                      className="input-field text-xs"
                      value={fieldCase.locationLabel}
                      onChange={(event) => setFieldCase((value) => ({ ...value, locationLabel: event.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="label">Altitude AGL m</label>
                    <input
                      className="input-field font-mono text-xs"
                      type="number"
                      min="0"
                      step="0.1"
                      value={fieldCase.flightAltitudeAglM}
                      onChange={(event) => setFieldCase((value) => ({ ...value, flightAltitudeAglM: event.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="label">Speed m/s</label>
                    <input
                      className="input-field font-mono text-xs"
                      type="number"
                      min="0"
                      step="0.1"
                      value={fieldCase.speedMps}
                      onChange={(event) => setFieldCase((value) => ({ ...value, speedMps: event.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="label">Lighting</label>
                    <input
                      className="input-field text-xs"
                      value={fieldCase.lighting}
                      onChange={(event) => setFieldCase((value) => ({ ...value, lighting: event.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="label">Weather</label>
                    <input
                      className="input-field text-xs"
                      value={fieldCase.weather}
                      onChange={(event) => setFieldCase((value) => ({ ...value, weather: event.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="label">Terrain texture</label>
                    <input
                      className="input-field text-xs"
                      value={fieldCase.terrainTexture}
                      onChange={(event) => setFieldCase((value) => ({ ...value, terrainTexture: event.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="label">Map age notes</label>
                    <input
                      className="input-field text-xs"
                      value={fieldCase.mapAgeOrSeasonNotes}
                      onChange={(event) => setFieldCase((value) => ({ ...value, mapAgeOrSeasonNotes: event.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="label">Focus/exposure</label>
                    <input
                      className="input-field text-xs"
                      value={fieldCase.cameraFocusExposureNotes}
                      onChange={(event) => setFieldCase((value) => ({ ...value, cameraFocusExposureNotes: event.target.value }))}
                    />
                  </div>
                  <div>
                    <label className="label">IMU/PX4 state</label>
                    <input
                      className="input-field text-xs"
                      value={fieldCase.imuPx4StateNotes}
                      onChange={(event) => setFieldCase((value) => ({ ...value, imuPx4StateNotes: event.target.value }))}
                    />
                  </div>
                  <div className="xl:col-span-3">
                    <label className="label">Safety notes</label>
                    <input
                      className="input-field text-xs"
                      value={fieldCase.safetyNotes}
                      onChange={(event) => setFieldCase((value) => ({ ...value, safetyNotes: event.target.value }))}
                    />
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap gap-3 text-[11px] text-slate-400">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={fieldCase.replace}
                    onChange={(event) => setFieldCase((value) => ({ ...value, replace: event.target.checked }))}
                  />
                  Replace existing case
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={fieldCase.strict}
                    onChange={(event) => setFieldCase((value) => ({ ...value, strict: event.target.checked }))}
                  />
                  Strict full gate
                </label>
              </div>
              {results["field-evidence"]?.output && selectedOutputId !== "field-evidence" && (
                <button onClick={() => setSelectedOutputId("field-evidence")} className="text-[10px] text-cyan-400 hover:text-cyan-300">
                  Show field evidence output
                </button>
              )}
              {results["field-metadata-update"]?.output && selectedOutputId !== "field-metadata-update" && (
                <button onClick={() => setSelectedOutputId("field-metadata-update")} className="text-[10px] text-cyan-400 hover:text-cyan-300">
                  Show metadata update output
                </button>
              )}
              {results["field-template"]?.output && selectedOutputId !== "field-template" && (
                <button onClick={() => setSelectedOutputId("field-template")} className="text-[10px] text-cyan-400 hover:text-cyan-300">
                  Show field template output
                </button>
              )}
              {results["field-collection-plan"]?.output && selectedOutputId !== "field-collection-plan" && (
                <button onClick={() => setSelectedOutputId("field-collection-plan")} className="text-[10px] text-cyan-400 hover:text-cyan-300">
                  Show field plan output
                </button>
              )}
            </div>

            <div className="rounded-lg border border-border bg-bg-card p-3 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-200 flex items-center gap-2">
                    <Camera size={14} className="text-cyan-400" /> Camera View Test
                  </div>
                  <p className="text-[11px] text-slate-500 mt-0.5">Capture a live preview frame from the module camera over SSH.</p>
                </div>
                <div className="flex gap-2">
                  <button onClick={captureCameraFrame} disabled={!connectionReady || capturingCamera} className="btn-secondary text-xs py-1 px-3">
                    {capturingCamera ? <Loader2 size={11} className="animate-spin" /> : <Camera size={11} />}
                    Refresh View
                  </button>
                  <button
                    onClick={() => setCameraAutoRefresh((value) => !value)}
                    disabled={!connectionReady}
                    className={cn(
                      "btn-secondary text-xs py-1 px-3",
                      cameraAutoRefresh && "border-emerald-500/30 text-emerald-400",
                    )}
                  >
                    {cameraAutoRefresh ? <EyeOff size={11} /> : <Eye size={11} />}
                    {cameraAutoRefresh ? "Stop Live View" : "Start Live View"}
                  </button>
                </div>
              </div>
              {cameraPreview ? (
                <div className="space-y-2">
                  <img src={cameraPreview} alt="Module camera preview" className="w-full aspect-video object-contain bg-black rounded border border-border" />
                  {cameraPreviewPath && <div className="text-[10px] font-mono text-slate-500">{cameraPreviewPath}</div>}
                </div>
              ) : (
                <div className="aspect-video rounded border border-dashed border-border bg-bg-base flex items-center justify-center text-xs text-slate-600">
                  No camera frame captured yet
                </div>
              )}
            </div>

            <div className="space-y-2">
              {steps.map((step) => {
                const result = results[step.id];
                const status = result?.status ?? "idle";
                return (
                  <div key={step.id} className="rounded-lg border border-border bg-bg-card px-3 py-2.5">
                    <div className="flex items-start gap-3">
                      <StatusIcon status={status} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-slate-200">{step.title}</span>
                          {step.requiresSudo && (
                            <span className="text-[10px] border border-amber-500/20 bg-amber-500/10 text-amber-400 rounded px-1.5 py-0.5">sudo</span>
                          )}
                          {step.localOnly && (
                            <span className="text-[10px] border border-cyan-500/20 bg-cyan-500/10 text-cyan-300 rounded px-1.5 py-0.5">local</span>
                          )}
                        </div>
                        <p className="text-[11px] text-slate-500 mt-0.5">{step.detail}</p>
                      </div>
                      <button
                        onClick={() => runSetupStep(step)}
                        disabled={(!step.localOnly && !connectionReady) || !!runningStep || (step.requiresSudo && !sudoPassword)}
                        className="btn-secondary text-xs py-1 px-2 shrink-0"
                      >
                        {runningStep === step.id ? <Loader2 size={11} className="animate-spin" /> : step.id === "bench-report" ? <Archive size={11} /> : step.localOnly ? <RefreshCw size={11} /> : <Terminal size={11} />}
                        Run
                      </button>
                      {runningStep === step.id && <Loader2 size={12} className="animate-spin text-cyan-400 shrink-0 mt-0.5" />}
                    </div>
                    {result?.output && selectedOutputId !== step.id && (
                      <button onClick={() => setSelectedOutputId(step.id)} className="mt-2 text-[10px] text-cyan-400 hover:text-cyan-300">
                        Show output
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <Server size={15} className="text-cyan-400" /> Latest Output
            </h3>
            {setupReportPath && (
              <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
                Setup report saved to <span className="font-mono">{setupReportPath}</span>
              </div>
            )}
            {autonomyWorkflowLocalPath && (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-300">
                <span className="min-w-0">
                  Autonomy evidence workflow saved to{" "}
                  <span className="font-mono break-all">{autonomyWorkflowLocalPath}</span>
                </span>
                <button
                  onClick={() => navigator.clipboard.writeText(autonomyWorkflowLocalPath)}
                  className="btn-secondary text-xs py-1 px-2 shrink-0"
                  title="Copy autonomy evidence workflow path"
                >
                  <Copy size={11} />
                </button>
              </div>
            )}
            {autonomyHandoffLocalPath && (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-300">
                <span className="min-w-0">
                  Autonomy handoff saved to <span className="font-mono break-all">{autonomyHandoffLocalPath}</span>
                </span>
                <button
                  onClick={() => navigator.clipboard.writeText(autonomyHandoffLocalPath)}
                  className="btn-secondary text-xs py-1 px-2 shrink-0"
                  title="Copy autonomy handoff path"
                >
                  <Copy size={11} />
                </button>
              </div>
            )}
            {autonomyEvidencePackageLocalPath && (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-300">
                <span className="min-w-0">
                  Autonomy evidence package saved to{" "}
                  <span className="font-mono break-all">{autonomyEvidencePackageLocalPath}</span>
                </span>
                <button
                  onClick={() => navigator.clipboard.writeText(autonomyEvidencePackageLocalPath)}
                  className="btn-secondary text-xs py-1 px-2 shrink-0"
                  title="Copy autonomy evidence package path"
                >
                  <Copy size={11} />
                </button>
              </div>
            )}
            {fieldTemplateLocalPath && (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-300">
                <span className="min-w-0">
                  Field evidence template saved to <span className="font-mono break-all">{fieldTemplateLocalPath}</span>
                </span>
                <button
                  onClick={() => navigator.clipboard.writeText(fieldTemplateLocalPath)}
                  className="btn-secondary text-xs py-1 px-2 shrink-0"
                  title="Copy field evidence template path"
                >
                  <Copy size={11} />
                </button>
              </div>
            )}
            {fieldManifestLocalPath && (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-300">
                <span className="min-w-0">
                  Active field manifest saved to <span className="font-mono break-all">{fieldManifestLocalPath}</span>
                </span>
                <button
                  onClick={() => navigator.clipboard.writeText(fieldManifestLocalPath)}
                  className="btn-secondary text-xs py-1 px-2 shrink-0"
                  title="Copy active field manifest path"
                >
                  <Copy size={11} />
                </button>
              </div>
            )}
            {fieldCollectionPlanLocalPath && (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-300">
                <span className="min-w-0">
                  Field collection plan saved to <span className="font-mono break-all">{fieldCollectionPlanLocalPath}</span>
                </span>
                <button
                  onClick={() => navigator.clipboard.writeText(fieldCollectionPlanLocalPath)}
                  className="btn-secondary text-xs py-1 px-2 shrink-0"
                  title="Copy field collection plan path"
                >
                  <Copy size={11} />
                </button>
              </div>
            )}
            {fieldCollectionPlanMarkdownLocalPath && (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-300">
                <span className="min-w-0">
                  Field collection checklist saved to{" "}
                  <span className="font-mono break-all">{fieldCollectionPlanMarkdownLocalPath}</span>
                </span>
                <button
                  onClick={() => navigator.clipboard.writeText(fieldCollectionPlanMarkdownLocalPath)}
                  className="btn-secondary text-xs py-1 px-2 shrink-0"
                  title="Copy field collection checklist path"
                >
                  <Copy size={11} />
                </button>
              </div>
            )}
            {fieldCapturePreflightLocalPath && (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2 text-xs text-cyan-300">
                <span className="min-w-0">
                  Field capture preflight saved to{" "}
                  <span className="font-mono break-all">{fieldCapturePreflightLocalPath}</span>
                </span>
                <button
                  onClick={() => navigator.clipboard.writeText(fieldCapturePreflightLocalPath)}
                  className="btn-secondary text-xs py-1 px-2 shrink-0"
                  title="Copy field capture preflight path"
                >
                  <Copy size={11} />
                </button>
              </div>
            )}
            {output ? (
              <pre className="bg-bg-base border border-border rounded-lg px-3 py-2.5 text-[11px] font-mono text-slate-300 whitespace-pre-wrap max-h-80 overflow-y-auto leading-relaxed">
                {runningStep && results[selectedOutputId ?? ""]?.status === "running" ? `${output}▋` : output}
              </pre>
            ) : (
              <div className="rounded-lg border border-dashed border-border px-3 py-8 text-center">
                <Terminal size={20} className="text-slate-600 mx-auto mb-2" />
                <p className="text-xs text-slate-500">Run a setup check to see command output.</p>
              </div>
            )}
            <SupportBundleList bundles={supportBundles} downloadDir={SUPPORT_DOWNLOAD_DIR} onChanged={refreshSupportBundles} />
            <Px4PrereqReportList
              reports={px4PrereqReports}
              downloadDir={PX4_RECEIVER_DOWNLOAD_DIR}
              onRefresh={refreshPx4PrereqReports}
            />
            <Px4ReceiverReportList
              reports={px4ReceiverReports}
              downloadDir={PX4_RECEIVER_DOWNLOAD_DIR}
              onRefresh={refreshPx4ReceiverReports}
            />
            <FeatureMethodBenchmarkReportList
              reports={featureBenchmarkReports}
              downloadDir={FEATURE_BENCH_DOWNLOAD_DIR}
              onRefresh={refreshFeatureBenchmarkReports}
            />
            <FieldEvidenceTemplateList
              templates={fieldEvidenceTemplates}
              downloadDir={AUTONOMY_REPORT_DOWNLOAD_DIR}
              onRefresh={refreshFieldEvidenceTemplates}
            />
            <FieldCollectionPlanList
              plans={fieldCollectionPlans}
              downloadDir={AUTONOMY_REPORT_DOWNLOAD_DIR}
              onRefresh={refreshFieldCollectionPlans}
              onLoadCondition={loadFieldCollectionCondition}
            />
            <AutonomyEvidenceWorkflowReportList
              reports={autonomyWorkflowReports}
              downloadDir={AUTONOMY_REPORT_DOWNLOAD_DIR}
              onRefresh={refreshAutonomyWorkflowReports}
            />
            <FieldEvidenceReportList
              reports={fieldEvidenceReports}
              downloadDir={AUTONOMY_REPORT_DOWNLOAD_DIR}
              onRefresh={refreshFieldEvidenceReports}
            />
            <ThresholdTuningReportList
              reports={thresholdTuningReports}
              downloadDir={AUTONOMY_REPORT_DOWNLOAD_DIR}
              onRefresh={refreshThresholdTuningReports}
            />
            <RosbagExportValidationReportList
              reports={rosbagValidationReports}
              downloadDir={ROSBAG_VALIDATION_DOWNLOAD_DIR}
              onRefresh={refreshRosbagValidationReports}
            />
            <AutonomyReadinessReportList
              reports={autonomyReports}
              downloadDir={AUTONOMY_REPORT_DOWNLOAD_DIR}
              onRefresh={refreshAutonomyReports}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
