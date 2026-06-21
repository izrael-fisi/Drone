import { useEffect, useMemo, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { open as openDialog, save as saveDialog } from "@tauri-apps/plugin-dialog";
import { writeTextFile } from "@tauri-apps/plugin-fs";
import {
  Archive,
  CheckCircle2,
  Copy,
  Camera,
  Eye,
  EyeOff,
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
  AutonomyReadinessReportFile,
  Device,
  FieldEvidenceReportFile,
  FeatureMethodBenchmarkReportFile,
  LocalNetworkHint,
  PiDiscoveryCandidate,
  SupportBundleFile,
  UploadProgress,
} from "../lib/types";

const DEFAULT_LOCAL_REPO = "/Users/izzyfisi/Documents/DRONE";
const HOST_SUGGESTIONS = ["dronecompute.local", "raspberrypi.local", "192.168.1.158"];
const SUPPORT_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/support-bundles";
const AUTONOMY_REPORT_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/replay-cases";
const FEATURE_BENCH_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/feature-method-bench";
const MODULE_SETUP_HANDOFF_KEY = "drone_module_setup_handoff";
const SUPPORT_EVIDENCE_ENV =
  'VISION_NAV_PX4_SITL_SESSION="$HOME/px4-sitl-evidence" VISION_NAV_PX4_PARAMS="$HOME/px4.params" VISION_NAV_ARDUPILOT_PARAMS="$HOME/ardupilot.params" ';

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
}

interface ModuleSetupHandoff {
  version: number;
  source: "mission-planner";
  action: "bench-report";
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

function safeReportName(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "module";
}

function parseSupportBundleZip(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_SUPPORT_ZIP__="))
    ?.replace("__VISION_NAV_SUPPORT_ZIP__=", "");
}

function parseAutonomyReadinessReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_AUTONOMY_REPORT__="))
    ?.replace("__VISION_NAV_AUTONOMY_REPORT__=", "");
}

function parseThresholdTuningReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_THRESHOLD_REPORT__="))
    ?.replace("__VISION_NAV_THRESHOLD_REPORT__=", "");
}

function parseFieldEvidenceReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_FIELD_EVIDENCE_REPORT__="))
    ?.replace("__VISION_NAV_FIELD_EVIDENCE_REPORT__=", "");
}

function parseFeatureMethodReport(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_FEATURE_METHOD_REPORT__="))
    ?.replace("__VISION_NAV_FEATURE_METHOD_REPORT__=", "");
}

function readModuleSetupHandoff(): ModuleSetupHandoff | null {
  try {
    const raw = sessionStorage.getItem(MODULE_SETUP_HANDOFF_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ModuleSetupHandoff;
    if (parsed.source !== "mission-planner" || parsed.action !== "bench-report") return null;
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
  const env = [
    `VISION_NAV_FIELD_CASE_NAME=${shellQuote(fieldCase.caseName)}`,
    `VISION_NAV_FIELD_EXPECTED=${shellQuote(fieldCase.expected)}`,
    `VISION_NAV_FIELD_CONDITIONS=${shellQuote(fieldCase.conditions)}`,
    `VISION_NAV_FIELD_BUNDLE=${shellQuote(remoteBundle)}`,
    fieldCase.notes ? `VISION_NAV_FIELD_NOTES=${shellQuote(fieldCase.notes)}` : "",
    fieldCase.replace ? "VISION_NAV_FIELD_REPLACE=1" : "",
    fieldCase.strict ? "VISION_NAV_FIELD_GATE_STRICT=1" : "",
  ].filter(Boolean).join(" ");
  return `cd ${shellQuote(remoteProject)} && ${env} ./scripts/pi/register_field_replay_case.sh`;
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
    notes: "",
    replace: false,
    strict: false,
  };
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
  if (status === "failed" || status === "missing" || status === "error") return "badge-red";
  return "badge-yellow";
}

function readinessIcon(status?: string) {
  if (status === "passed" || status === "healthy" || status === "covered") return <CheckCircle2 size={11} />;
  if (status === "failed" || status === "missing" || status === "error") return <XCircle size={11} />;
  return <Terminal size={11} />;
}

function formatReadinessLabel(value?: string | number | null) {
  if (value === undefined || value === null || value === "") return "n/a";
  return String(value).replace(/_/g, " ");
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
          {reports.slice(0, 4).map((report) => (
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
                </div>
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                {[
                  ["support", report.summary.support_bundle_bench_readiness_status],
                  ["px4", report.summary.px4_receiver_proof_status],
                  ["field", report.summary.field_evidence_proof_status],
                  ["features", report.summary.feature_method_benchmark_status],
                  ["thresholds", report.summary.threshold_tuning_status],
                ].map(([label, status]) => (
                  <div key={label} className="flex items-center gap-1.5 font-mono text-[10px] text-slate-500">
                    <span className={cn(readinessBadgeClass(status), "text-[10px]")}>
                      {formatReadinessLabel(status)}
                    </span>
                    <span>{label}</span>
                  </div>
                ))}
              </div>
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
            </div>
          ))}
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

function formatAcceptedRate(value?: number) {
  return value == null ? "n/a" : `${Math.round(value * 100)}%`;
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
  const [fieldEvidenceReports, setFieldEvidenceReports] = useState<FieldEvidenceReportFile[]>([]);
  const [featureBenchmarkReports, setFeatureBenchmarkReports] = useState<FeatureMethodBenchmarkReportFile[]>([]);
  const [setupReportPath, setSetupReportPath] = useState<string | null>(null);
  const [setupHandoff, setSetupHandoff] = useState<ModuleSetupHandoff | null>(() => readModuleSetupHandoff());
  const [fieldCase, setFieldCase] = useState<FieldCaseForm>(() => defaultFieldCaseForm());
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
    if (selectedDevice) {
      setForm(formFromDevice(selectedDevice));
      setConnectionResult(null);
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

  const refreshFieldEvidenceReports = async () => {
    try {
      setFieldEvidenceReports(await cmd.listFieldEvidenceReports(AUTONOMY_REPORT_DOWNLOAD_DIR));
    } catch {
      setFieldEvidenceReports([]);
    }
  };

  const refreshFeatureBenchmarkReports = async () => {
    try {
      setFeatureBenchmarkReports(await cmd.listFeatureMethodBenchmarkReports(FEATURE_BENCH_DOWNLOAD_DIR));
    } catch {
      setFeatureBenchmarkReports([]);
    }
  };

  useEffect(() => {
    refreshSupportBundles();
    refreshAutonomyReports();
    refreshFieldEvidenceReports();
    refreshFeatureBenchmarkReports();
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

  const setResult = (id: string, result: SetupResult) => {
    setResults((prev) => ({ ...prev, [id]: result }));
    setSelectedOutputId(id);
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
      setResult("autonomy-readiness", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ autonomy readiness\n${output}\n\n$ download readiness report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshAutonomyReports();
    } catch (err) {
      setResult("autonomy-readiness", { status: "failed", output: `$ autonomy readiness\nERROR: ${err}` });
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
      await refreshAutonomyReports();
    } catch (err) {
      setResult("threshold-tuning", { status: "failed", output: `$ threshold tuning\nERROR: ${err}` });
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

  const registerFieldEvidenceCase = async () => {
    if (!fieldCase.caseName.trim() || !fieldCase.conditions.trim()) {
      setError("Field case name and condition tags are required.");
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
      id: "bench-report",
      title: "Bench Report",
      detail: "Validates the deployed terrain bundle, creates a Pi support bundle, and downloads it.",
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
      id: "autonomy-readiness",
      title: "Autonomy Readiness",
      detail: "Runs the strict final audit against the latest Pi support bundle and field evidence artifacts.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/run_autonomy_readiness_audit.sh`,
    },
  ];

  const runSetupStep = async (step: SetupStep) => {
    if (step.id === "bench-report") {
      await createBenchReport();
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
    if (step.id === "autonomy-readiness") {
      await runAutonomyReadiness();
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
        feature_benchmark_download_dir: FEATURE_BENCH_DOWNLOAD_DIR,
      },
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
      downloaded_autonomy_reports: autonomyReports.slice(0, 5),
      downloaded_field_evidence_reports: fieldEvidenceReports.slice(0, 5),
      downloaded_feature_benchmark_reports: featureBenchmarkReports.slice(0, 5),
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
                    Mission Planner uploaded this bundle for bench validation.
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
              <button onClick={createBenchReport} disabled={!connectionReady || !!runningStep} className="btn-primary w-full justify-center">
                {runningStep === "bench-report" ? <Loader2 size={14} className="animate-spin" /> : <Archive size={14} />}
                Create Bench Report
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

            <div className="rounded-lg border border-border bg-bg-card p-3 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-200 flex items-center gap-2">
                    <ShieldCheck size={14} className="text-cyan-400" /> Field Evidence Case
                  </div>
                </div>
                <button
                  onClick={registerFieldEvidenceCase}
                  disabled={!connectionReady || !!runningStep || !fieldCase.caseName.trim() || !fieldCase.conditions.trim()}
                  className="btn-secondary text-xs py-1 px-3"
                >
                  {runningStep === "field-evidence" ? <Loader2 size={11} className="animate-spin" /> : <ShieldCheck size={11} />}
                  Register
                </button>
              </div>
              <div className="grid grid-cols-2 gap-3">
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
                  <label className="label">Notes</label>
                  <input
                    className="input-field text-xs"
                    value={fieldCase.notes}
                    onChange={(event) => setFieldCase((value) => ({ ...value, notes: event.target.value }))}
                  />
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
                        </div>
                        <p className="text-[11px] text-slate-500 mt-0.5">{step.detail}</p>
                      </div>
                      <button
                        onClick={() => runSetupStep(step)}
                        disabled={!connectionReady || !!runningStep || (step.requiresSudo && !sudoPassword)}
                        className="btn-secondary text-xs py-1 px-2 shrink-0"
                      >
                        {runningStep === step.id ? <Loader2 size={11} className="animate-spin" /> : step.id === "bench-report" ? <Archive size={11} /> : <Terminal size={11} />}
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
            <FeatureMethodBenchmarkReportList
              reports={featureBenchmarkReports}
              downloadDir={FEATURE_BENCH_DOWNLOAD_DIR}
              onRefresh={refreshFeatureBenchmarkReports}
            />
            <FieldEvidenceReportList
              reports={fieldEvidenceReports}
              downloadDir={AUTONOMY_REPORT_DOWNLOAD_DIR}
              onRefresh={refreshFieldEvidenceReports}
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
