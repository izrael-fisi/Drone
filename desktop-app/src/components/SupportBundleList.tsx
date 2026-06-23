import { useState } from "react";
import { AlertTriangle, Archive, CheckCircle2, Clipboard, Eye, FileDown, FolderOpen, Trash2 } from "lucide-react";
import { cmd } from "../lib/tauri";
import type { FieldCollectionPlanCondition, SupportBundleDetails, SupportBundleFile } from "../lib/types";

type WorkflowValidationSummary = NonNullable<SupportBundleDetails["autonomy_evidence_workflow_validation"]>;
type WorkflowValidationStep = WorkflowValidationSummary["checks"][number]["non_passed_steps"][number];
type FieldCapturePreflightCheck = SupportBundleDetails["field_capture_preflight_reports"][number]["checks"][number];
type BundleDiagnostic = NonNullable<FieldCapturePreflightCheck["bundle_diagnostic"]>;
type BenchReadiness = NonNullable<SupportBundleDetails["bench_readiness"]>;

function formatBundleSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function formatLabel(value?: string | number | null) {
  if (value === undefined || value === null || value === "") return "n/a";
  return String(value).replace(/_/g, " ");
}

function statusClass(status?: string) {
  if (status === "passed" || status === "healthy" || status === "covered") return "badge-green";
  if (status === "failed" || status === "error" || status === "missing") return "badge-red";
  return "badge-yellow";
}

function statusIcon(status?: string) {
  if (status === "passed" || status === "healthy") return <CheckCircle2 size={11} />;
  return <AlertTriangle size={11} />;
}

function timelineSegmentClass(status?: string) {
  if (status === "accepted" || status === "passed" || status === "healthy") {
    return "bg-emerald-400/70 border-emerald-300/70";
  }
  if (status === "rejected" || status === "failed" || status === "error" || status === "invalid_json") {
    return "bg-red-400/70 border-red-300/70";
  }
  if (status === "degraded") return "bg-amber-300/70 border-amber-200/70";
  return "bg-slate-500/50 border-slate-400/50";
}

function formatPercent(value?: number) {
  return value == null ? "n/a" : `${Math.round(value * 100)}%`;
}

function formatCounts(counts?: Record<string, number>) {
  if (!counts || Object.keys(counts).length === 0) return "none";
  return Object.entries(counts)
    .map(([key, value]) => `${key} ${value}`)
    .join(", ");
}

function uniqueStrings(values: string[]) {
  const seen = new Set<string>();
  return values.filter((value) => {
    if (!value || seen.has(value)) return false;
    seen.add(value);
    return true;
  });
}

function formatLimitedList(values: string[], limit: number) {
  const shown = values.slice(0, limit).map(formatLabel).join(", ");
  const extra = values.length > limit ? ` +${values.length - limit}` : "";
  return `${shown}${extra}`;
}

function formatWorkflowStep(step: WorkflowValidationStep) {
  const base = [formatLabel(step.name), formatLabel(step.status)]
    .filter((part) => part && part !== "n/a")
    .join(": ");
  if (step.current_preflight_allows_capture) {
    return `${base} (current preflight capture-ready)`;
  }
  return base;
}

function workflowStepTitle(step: WorkflowValidationStep) {
  return [
    step.notes,
    step.current_preflight_allows_capture
      ? `Current preflight capture-ready (${formatLabel(step.current_preflight_status)})`
      : undefined,
    step.current_preflight_report ? `Current preflight: ${step.current_preflight_report}` : undefined,
    step.guidance,
  ]
    .filter(Boolean)
    .join("\n");
}

function countsRecord(value: unknown): Record<string, number> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const entries = Object.entries(value).filter((entry): entry is [string, number] => typeof entry[1] === "number");
  return entries.length > 0 ? Object.fromEntries(entries) : undefined;
}

function stringArray(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

function formatVector(value: unknown) {
  if (!Array.isArray(value)) return "n/a";
  return value
    .map((item) => typeof item === "number" ? item.toFixed(2) : String(item))
    .join(", ");
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function arrayCount(value: unknown) {
  return Array.isArray(value) ? value.length : 0;
}

function formatMargin(value: unknown) {
  return typeof value === "number" ? value.toFixed(2) : "n/a";
}

function nestedString(root: unknown, path: string[]) {
  let current: unknown = root;
  for (const key of path) {
    current = asRecord(current)?.[key];
  }
  return typeof current === "string" && current ? current : null;
}

function nestedNumber(root: unknown, path: string[]) {
  let current: unknown = root;
  for (const key of path) {
    current = asRecord(current)?.[key];
  }
  return typeof current === "number" ? current : null;
}

function commandRecords(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const record = asRecord(item);
    const command = typeof record?.command === "string" && record.command ? record.command : null;
    if (!command) return [];
    return [{
      label: typeof record?.label === "string" ? record.label : undefined,
      condition: typeof record?.condition === "string" ? record.condition : undefined,
      command,
    }];
  });
}

function namedCommandRecords(value: unknown) {
  const record = asRecord(value);
  if (!record) return [];
  return Object.entries(record).flatMap(([key, rawValue]) => {
    if (typeof rawValue === "string" && rawValue) {
      return [{ label: key, command: rawValue }];
    }
    if (Array.isArray(rawValue)) {
      const lines = rawValue.filter((item): item is string => typeof item === "string" && item.length > 0);
      if (lines.length > 0) return [{ label: key, command: lines.join("\n") }];
    }
    return [];
  });
}

function fieldConditionCommandRecords(conditions: FieldCollectionPlanCondition[]) {
  return conditions.flatMap((condition) => {
    const label = condition.label || condition.condition || "condition";
    return [
      condition.preflight_command ? { stage: "preflight", label, command: condition.preflight_command } : null,
      condition.preflight_capture_command
        ? { stage: "preflight + capture", label, command: condition.preflight_capture_command }
        : null,
      condition.capture_command ? { stage: "capture", label, command: condition.capture_command } : null,
      condition.metadata_update_command ? { stage: "metadata", label, command: condition.metadata_update_command } : null,
      condition.register_command ? { stage: "register", label, command: condition.register_command } : null,
    ].filter((item): item is { stage: string; label: string; command: string } => Boolean(item));
  });
}

const BENCH_FOLLOW_UPS: Record<string, { title: string; desktopAction: string; command: string; notes: string }> = {
  bundle_health: {
    title: "Rebuild or validate the terrain bundle",
    desktopAction: "Mission Planner > Build Bundle, then Module Setup > Bench Report",
    command: "./scripts/pi/validate_terrain_bundle.sh",
    notes: "The support bundle must include passing terrain bundle health before bench readiness can pass.",
  },
  gnss_denied_plan: {
    title: "Complete GNSS-denied mission prep",
    desktopAction: "Mission Planner > GNSS-Denied Prep, then Build/Upload Bundle and Bench Report",
    command: "./scripts/pi/check_gnss_denied_plan.sh && ./scripts/pi/validate_terrain_bundle.sh",
    notes: "Rebuild the bundle after satellite source, map reset, home reset, heading, and estimator checks are ready.",
  },
  runtime_logs: {
    title: "Capture a terrain runtime log",
    desktopAction: "Module Setup > Field Log Capture, then Bench Report",
    command: "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh",
    notes: "Create the support bundle after Field Log Capture produces terrain_matches.jsonl.",
  },
  runtime_status: {
    title: "Capture runtime status with the terrain log",
    desktopAction: "Module Setup > Field Log Capture, Runtime Status, then Bench Report",
    command: "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && VISION_NAV_RUNTIME_STATUS_ROOTS=$HOME/DroneTransfer/outgoing/terrain-match ./scripts/pi/read_runtime_status.sh",
    notes: "Runtime status proves active map, output path, estimator health, and latest match state.",
  },
  replay_gates: {
    title: "Run guided field replay evidence",
    desktopAction: "Module Setup > Load Next Field Condition, then Evidence Workflow",
    command: "./scripts/pi/run_autonomy_evidence_workflow.sh",
    notes: "The workflow captures, validates, and registers condition-specific logs.",
  },
  px4_sitl_evidence: {
    title: "Capture PX4 receiver proof",
    desktopAction: "Module Setup > PX4 SITL Receiver Capture, then Bench Report",
    command: "VISION_NAV_SITL_SMOKE_DIR=$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh",
    notes: "Receiver proof must show the MAVLink ODOMETRY path arriving as vehicle_visual_odometry samples.",
  },
  px4_params: {
    title: "Export and check PX4 external-vision parameters",
    desktopAction: "Module Setup > PX4 parameter check, then Bench Report",
    command: "./scripts/pi/check_px4_params.sh",
    notes: "Export PX4 parameters from QGroundControl or the PX4 shell before creating the support bundle.",
  },
  feature_method_benchmarks: {
    title: "Benchmark feature methods on field logs",
    desktopAction: "Module Setup > Feature Benchmark",
    command: "./scripts/pi/run_feature_method_benchmark.sh",
    notes: "Use real field logs to compare ORB, AKAZE, SIFT, and neural descriptor options.",
  },
  field_evidence: {
    title: "Collect and register field replay proof",
    desktopAction: "Module Setup > Evidence Workflow",
    command: "./scripts/pi/run_autonomy_evidence_workflow.sh",
    notes: "Field evidence must cover all required terrain conditions with real captured logs.",
  },
  threshold_tuning: {
    title: "Tune replay gates against field logs",
    desktopAction: "Module Setup > Threshold Tuning",
    command: "./scripts/pi/run_threshold_tuning_report.sh",
    notes: "Threshold tuning should run after the field-evidence manifest passes.",
  },
  rosbag_export_validations: {
    title: "Export and validate the ROS replay artifact",
    desktopAction: "Module Setup > ROS Bag Validation, then Bench Report",
    command: "./scripts/pi/run_rosbag_export_validation.sh && ./scripts/pi/create_support_bundle.sh",
    notes: "Support bundles should include a passed ROS replay export validation summary.",
  },
  rosbag2_cli_reviews: {
    title: "Review the native rosbag2 export",
    desktopAction: "Module Setup > Native rosbag2 Review, then Bench Report",
    command: "./scripts/dev/run_rosbag2_cli_review.sh && ./scripts/pi/create_support_bundle.sh",
    notes: "Run on a sourced ROS 2 workstation when native rosbag2 export is part of the evidence package.",
  },
  ardupilot_params: {
    title: "Review ArduPilot ExternalNav parameters",
    desktopAction: "Module Setup > ArduPilot parameter check",
    command: "./scripts/pi/check_ardupilot_params.sh",
    notes: "ArduPilot remains optional for the PX4-first bench path unless explicitly required.",
  },
};

function benchReadinessFollowUps(readiness?: BenchReadiness) {
  const reportActions = (readiness?.next_actions ?? []).flatMap((action) => {
    if (!action.command) return [];
    return [{
      check: action.check ?? "bench_readiness",
      status: action.status,
      title: action.title ?? "Bench follow-up",
      desktopAction: action.desktop_action ?? "Module Setup",
      command: action.command,
      notes: action.notes,
      message: action.message,
    }];
  });
  if (reportActions.length > 0) return reportActions;
  return (readiness?.checks ?? []).flatMap((check) => {
    if (!check.name || check.status === "passed") return [];
    const spec = BENCH_FOLLOW_UPS[check.name];
    if (!spec) return [];
    return [{
      check: check.name,
      status: check.status,
      message: check.message,
      ...spec,
    }];
  });
}

function SupportBundleDetailPanel({
  details,
  onExtractArtifact,
  extractingEntry,
}: {
  details: SupportBundleDetails;
  onExtractArtifact: (entryPath: string) => void | Promise<void>;
  extractingEntry?: string | null;
}) {
  const projectVersion = nestedString(details.metadata, ["vision_nav", "project_version"]);
  const gitBranch = nestedString(details.metadata, ["vision_nav", "git", "branch"]);
  const gitCommit = nestedString(details.metadata, ["vision_nav", "git", "commit"]);
  const platform = nestedString(details.metadata, ["host", "platform"]);
  const healthStatus = nestedString(details.bundle_health, ["status"]);
  const checksumStatus = nestedString(details.bundle_health, ["checksums", "status"]);
  const px4Evidence = asRecord(details.manifest.px4_sitl_evidence);
  const px4SessionSummary = asRecord(px4Evidence?.session_summary);
  const px4SessionCommands = namedCommandRecords(px4SessionSummary?.operator_commands);
  const px4Prereqs = asRecord(details.manifest.px4_sitl_prereqs);
  const px4PrereqFixCommands = commandRecords(px4Prereqs?.fix_commands);
  const benchFollowUps = benchReadinessFollowUps(details.bench_readiness);
  const evidenceWorkflow = asRecord(details.manifest.autonomy_evidence_workflow);
  const workflowValidation = details.autonomy_evidence_workflow_validation;
  const workflowStatus = typeof evidenceWorkflow?.status === "string" ? evidenceWorkflow.status : undefined;
  const workflowProvenance = asRecord(asRecord(evidenceWorkflow?.validation_summary)?.workflow_provenance);
  const workflowRepoCommit = typeof workflowProvenance?.repo_commit === "string" ? workflowProvenance.repo_commit : undefined;
  const workflowProvenanceCheck = workflowValidation?.checks.find((check) => check.name === "workflow_provenance");
  const workflowNextStep = workflowValidation?.next_required_step;
  const workflowNextCommand = workflowNextStep?.command || workflowNextStep?.metadata_update_command;
  const workflowMetadataUpdateCommand = workflowNextStep?.metadata_update_command;
  const workflowCaptureAfterBundleCommand = workflowNextStep?.capture_command_after_bundle;
  const workflowBlockingChecks = (workflowValidation?.checks ?? []).filter((check) => check.status && check.status !== "passed");
  const workflowMissingSteps = uniqueStrings(workflowBlockingChecks.flatMap((check) => check.missing_steps));
  const workflowMissingMarkers = uniqueStrings(workflowBlockingChecks.flatMap((check) => check.missing_markers));
  const workflowNonPassedSteps = workflowBlockingChecks.flatMap((check) => check.non_passed_steps);
  const hasWorkflowBlockerDetails = workflowMissingSteps.length > 0
    || workflowNonPassedSteps.length > 0
    || workflowMissingMarkers.length > 0
    || workflowBlockingChecks.length > 0;
  const showEvidenceWorkflow = Boolean(workflowValidation || (workflowStatus && workflowStatus !== "not_provided"));

  return (
    <div className="space-y-2 pt-1">
      <div className="grid grid-cols-2 gap-2 text-[10px] font-mono">
        <span>entries {details.entry_count}</span>
        <span>readiness {formatLabel(details.bench_readiness?.status ?? healthStatus)}</span>
        <span>version {formatLabel(projectVersion)}</span>
        <span>checksums {formatLabel(checksumStatus)}</span>
        <span className="truncate">branch {formatLabel(gitBranch)}</span>
        <span className="truncate">commit {gitCommit ? gitCommit.slice(0, 8) : "n/a"}</span>
        <span className="col-span-2 truncate">host {formatLabel(platform)}</span>
      </div>

      {details.bench_readiness && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Bench readiness</div>
          <div className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className={statusClass(details.bench_readiness.status)}>
                {statusIcon(details.bench_readiness.status)}
                {formatLabel(details.bench_readiness.status)}
              </span>
              <span className="font-mono text-slate-500">pass {details.bench_readiness.passed_count ?? 0}</span>
              <span className="font-mono text-slate-500">degrade {details.bench_readiness.degraded_count ?? 0}</span>
              <span className="font-mono text-slate-500">fail {details.bench_readiness.failed_count ?? 0}</span>
            </div>
            {details.bench_readiness.checks.slice(0, 5).map((check) => (
              <div key={`${check.name}-${check.status}`} className="flex flex-wrap items-center gap-1.5 font-mono text-slate-500">
                <span className={statusClass(check.status)}>
                  {statusIcon(check.status)}
                  {formatLabel(check.status)}
                </span>
                <span>{formatLabel(check.name)}</span>
                {check.message && <span className="truncate text-slate-400">{check.message}</span>}
              </div>
            ))}
            {benchFollowUps.length > 0 && (
              <div className="space-y-1 rounded border border-border/50 bg-bg-base/50 px-2 py-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <div className="text-[10px] uppercase tracking-wide text-slate-500">Bench follow-up</div>
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(benchFollowUps.map((item) => `# ${item.title}\n# ${item.desktopAction}\n${item.command}`).join("\n\n"))}
                    className="btn-secondary px-1.5 py-0.5 text-[10px]"
                    title="Copy all bench follow-up commands"
                  >
                    <Clipboard size={9} />
                    copy all
                  </button>
                </div>
                {benchFollowUps.slice(0, 6).map((item) => (
                  <div key={`${item.check}-${item.status ?? "status"}`} className="space-y-1">
                    <div className="flex flex-wrap items-center gap-1.5 font-mono text-slate-500">
                      <span className={statusClass(item.status)}>
                        {statusIcon(item.status)}
                        {formatLabel(item.status)}
                      </span>
                      <span>{item.title}</span>
                      <span className="truncate text-cyan-300">{item.desktopAction}</span>
                    </div>
                    {(item.message || item.notes) && (
                      <div className="text-[10px] text-slate-400">
                        {item.message || item.notes}
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={() => navigator.clipboard.writeText(item.command)}
                      className="flex w-full min-w-0 items-center gap-1.5 rounded border border-border/50 bg-bg-base/60 px-2 py-1 text-left font-mono text-[10px] text-slate-400 hover:border-cyan-500/40 hover:text-cyan-200"
                      title={item.command}
                    >
                      <Clipboard size={9} className="shrink-0" />
                      <span className="shrink-0 text-slate-500">copy command</span>
                      <span className="truncate whitespace-pre">{item.command}</span>
                    </button>
                  </div>
                ))}
                {benchFollowUps.length > 6 && (
                  <div className="font-mono text-slate-500">+{benchFollowUps.length - 6} more bench follow-up actions</div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {showEvidenceWorkflow && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Evidence workflow</div>
          <div className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-1">
            <div className="flex flex-wrap items-center gap-1.5">
              {workflowStatus && workflowStatus !== "not_provided" && (
                <span className={statusClass(workflowStatus)}>
                  {statusIcon(workflowStatus)}
                  package {formatLabel(workflowStatus)}
                </span>
              )}
              {workflowValidation?.status && (
                <span className={statusClass(workflowValidation.status)}>
                  {statusIcon(workflowValidation.status)}
                  validation {formatLabel(workflowValidation.status)}
                </span>
              )}
              {workflowValidation?.workflow_status && (
                <span className={statusClass(workflowValidation.workflow_status)}>
                  {statusIcon(workflowValidation.workflow_status)}
                  runtime {formatLabel(workflowValidation.workflow_status)}
                </span>
              )}
              {workflowProvenanceCheck?.status && (
                <span className={statusClass(workflowProvenanceCheck.status)}>
                  {statusIcon(workflowProvenanceCheck.status)}
                  proof {formatLabel(workflowProvenanceCheck.status)}
                </span>
              )}
              <span className="font-mono text-slate-500">steps {workflowValidation?.step_count ?? 0}</span>
              <span className="font-mono text-slate-500">issues {workflowValidation?.issue_count ?? 0}</span>
              <span className="font-mono text-slate-500">commit {workflowRepoCommit ? workflowRepoCommit.slice(0, 8) : "n/a"}</span>
            </div>
            {workflowNextStep && (
              <div className="rounded border border-border/50 bg-bg-base/50 px-2 py-1 space-y-1">
                <div className="flex flex-wrap items-center gap-1.5 font-mono text-slate-500">
                  <span>next {formatLabel(workflowNextStep.name)}</span>
                  <span className={statusClass(workflowNextStep.status)}>
                    {statusIcon(workflowNextStep.status)}
                    {formatLabel(workflowNextStep.status)}
                  </span>
                  {workflowNextStep.desktop_action && <span className="truncate">app {workflowNextStep.desktop_action}</span>}
                  {workflowNextStep.bundle_path && <span className="truncate">bundle {workflowNextStep.bundle_path}</span>}
                  {workflowNextStep.expected_log && <span className="truncate">log {workflowNextStep.expected_log}</span>}
                  {workflowNextStep.output_dir && <span className="truncate">output {workflowNextStep.output_dir}</span>}
                  {workflowNextStep.runtime_status_path && (
                    <span className="truncate">runtime {workflowNextStep.runtime_status_path}</span>
                  )}
                  {workflowNextStep.metadata_update_command && <span className="truncate">metadata update ready</span>}
                </div>
                {workflowNextStep.notes && (
                  <div className="text-[10px] text-slate-400">
                    {workflowNextStep.notes}
                  </div>
                )}
                {workflowNextCommand && (
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(workflowNextCommand)}
                    className="flex w-full min-w-0 items-center gap-1.5 rounded border border-border/50 bg-bg-base/60 px-2 py-1 text-left font-mono text-[10px] text-slate-400 hover:border-cyan-500/40 hover:text-cyan-200"
                    title={workflowNextCommand}
                  >
                    <Clipboard size={9} className="shrink-0" />
                    <span className="shrink-0 text-slate-500">copy next</span>
                    <span className="truncate whitespace-pre">{workflowNextCommand}</span>
                  </button>
                )}
                {workflowMetadataUpdateCommand && workflowMetadataUpdateCommand !== workflowNextCommand && (
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(workflowMetadataUpdateCommand)}
                    className="flex w-full min-w-0 items-center gap-1.5 rounded border border-amber-500/20 bg-amber-500/10 px-2 py-1 text-left font-mono text-[10px] text-amber-100 hover:border-amber-400/50"
                    title={workflowMetadataUpdateCommand}
                  >
                    <Clipboard size={9} className="shrink-0" />
                    <span className="shrink-0 text-amber-200/80">copy metadata update</span>
                    <span className="truncate whitespace-pre">{workflowMetadataUpdateCommand}</span>
                  </button>
                )}
                {workflowCaptureAfterBundleCommand && (
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(workflowCaptureAfterBundleCommand)}
                    className="flex w-full min-w-0 items-center gap-1.5 rounded border border-cyan-500/20 bg-cyan-500/10 px-2 py-1 text-left font-mono text-[10px] text-cyan-100 hover:border-cyan-400/50"
                    title={workflowCaptureAfterBundleCommand}
                  >
                    <Clipboard size={9} className="shrink-0" />
                    <span className="shrink-0 text-cyan-200/80">copy capture after bundle</span>
                    <span className="truncate whitespace-pre">{workflowCaptureAfterBundleCommand}</span>
                  </button>
                )}
              </div>
            )}
            {(workflowValidation?.issues ?? []).slice(0, 2).map((issue) => (
              <div key={issue} className="text-amber-200/80 truncate">
                {issue}
              </div>
            ))}
            {hasWorkflowBlockerDetails && (
              <div className="space-y-1 rounded border border-border/50 bg-bg-base/50 px-2 py-1">
                <div className="text-[10px] uppercase tracking-wide text-slate-500">Workflow validation blockers</div>
                {workflowMissingSteps.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5 font-mono text-slate-500">
                    <span className="badge-red">missing steps</span>
                    <span className="truncate" title={workflowMissingSteps.map(formatLabel).join(", ")}>
                      {formatLimitedList(workflowMissingSteps, 6)}
                    </span>
                  </div>
                )}
                {workflowNonPassedSteps.slice(0, 4).map((step, index) => (
                  <div
                    key={`${step.name ?? "workflow-step"}-${step.status ?? "status"}-${index}`}
                    className="flex flex-wrap items-center gap-1.5 font-mono text-slate-500"
                    title={workflowStepTitle(step) || undefined}
                  >
                    <span className={statusClass(step.status)}>
                      {statusIcon(step.status)}
                      {formatLabel(step.status)}
                    </span>
                    <span className="truncate">{formatWorkflowStep(step)}</span>
                    {typeof step.exit_code === "number" && <span>exit {step.exit_code}</span>}
                    {step.notes && <span className="truncate text-slate-400">{step.notes}</span>}
                  </div>
                ))}
                {workflowNonPassedSteps.length > 4 && (
                  <div className="font-mono text-slate-500">+{workflowNonPassedSteps.length - 4} more non-passing workflow steps</div>
                )}
                {workflowMissingMarkers.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5 font-mono text-slate-500">
                    <span className="badge-yellow">missing markers</span>
                    <span className="truncate" title={workflowMissingMarkers.map(formatLabel).join(", ")}>
                      {formatLimitedList(workflowMissingMarkers, 6)}
                    </span>
                  </div>
                )}
                {workflowBlockingChecks.slice(0, 4).map((check) => (
                  <div key={`${check.name ?? "check"}-${check.status ?? "status"}`} className="flex flex-wrap items-center gap-1.5 font-mono text-slate-500">
                    <span className={statusClass(check.status)}>
                      {statusIcon(check.status)}
                      {formatLabel(check.status)}
                    </span>
                    <span>{formatLabel(check.name)}</span>
                    {check.message && <span className="truncate text-slate-400">{check.message}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {px4SessionCommands.length > 0 && (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <div className="text-[10px] uppercase tracking-wide text-slate-500">PX4 receiver session commands</div>
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(px4SessionCommands.map((item) => `# ${formatLabel(item.label)}\n${item.command}`).join("\n\n"))}
              className="btn-secondary px-1.5 py-0.5 text-[10px]"
              title="Copy all PX4 receiver-session commands"
            >
              <Clipboard size={9} />
              copy all
            </button>
          </div>
          <div className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-1">
            <div className="flex flex-wrap items-center gap-1.5 font-mono text-slate-500">
              <span>message {formatLabel(px4SessionSummary?.message_type as string | undefined)}</span>
              <span>rate {formatLabel(px4SessionSummary?.rate_hz as string | number | undefined)} hz</span>
              <span className="truncate">endpoint {formatLabel(px4SessionSummary?.endpoint as string | undefined)}</span>
            </div>
            {px4SessionCommands.slice(0, 5).map((item) => (
              <button
                key={item.label}
                type="button"
                onClick={() => navigator.clipboard.writeText(item.command)}
                className="flex w-full min-w-0 items-center gap-1.5 rounded border border-border/50 bg-bg-base/50 px-2 py-1 text-left font-mono text-[10px] text-slate-400 hover:border-cyan-500/40 hover:text-cyan-200"
                title={item.command}
              >
                <Clipboard size={9} className="shrink-0" />
                <span className="shrink-0 text-slate-500">{formatLabel(item.label)}</span>
                <span className="truncate whitespace-pre">{item.command}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {px4PrereqFixCommands.length > 0 && (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <div className="text-[10px] uppercase tracking-wide text-slate-500">PX4 prerequisite fixes</div>
            <button
              type="button"
              onClick={() => navigator.clipboard.writeText(px4PrereqFixCommands.map((item) => item.command).join("\n"))}
              className="btn-secondary px-1.5 py-0.5 text-[10px]"
              title="Copy all PX4 prerequisite fix commands"
            >
              <Clipboard size={9} />
              copy all
            </button>
          </div>
          <div className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-1">
            {px4PrereqFixCommands.slice(0, 5).map((item, index) => (
              <button
                key={`${item.condition ?? item.label ?? "fix"}-${index}`}
                type="button"
                onClick={() => navigator.clipboard.writeText(item.command)}
                className="flex w-full min-w-0 items-center gap-1.5 rounded border border-border/50 bg-bg-base/50 px-2 py-1 text-left font-mono text-[10px] text-slate-400 hover:border-cyan-500/40 hover:text-cyan-200"
                title={item.command}
              >
                <Clipboard size={9} className="shrink-0" />
                <span className="shrink-0 text-slate-500">{formatLabel(item.label ?? item.condition ?? "fix")}</span>
                <span className="truncate">{item.command}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {details.logs.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Log summaries</div>
          {details.logs.slice(0, 3).map((log) => {
            const externalPosition = asRecord(log.external_position);
            const warningCounts = countsRecord(externalPosition?.warning_counts);
            return (
              <div key={log.name} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
                <div className="flex items-center justify-between gap-2 font-mono text-slate-400">
                  <span className="truncate">{log.name}</span>
                  <span>{log.total_records ?? 0} rec, {formatPercent(log.accepted_rate)} accepted</span>
                </div>
                <div className="font-mono text-slate-500">status {formatCounts(log.status_counts)}</div>
                <div className="font-mono text-slate-500">reasons {formatCounts(log.reason_counts)}</div>
                {warningCounts && <div className="font-mono text-amber-200/80">ext warnings {formatCounts(warningCounts)}</div>}
              </div>
            );
          })}
        </div>
      )}

      {details.runtime_statuses.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Runtime status</div>
          {details.runtime_statuses.slice(0, 2).map((status, index) => {
            const activeMap = asRecord(status.active_map);
            const output = asRecord(status.output);
            const lastMatch = asRecord(status.last_match);
            const estimator = asRecord(status.estimator);
            const externalPosition = asRecord(status.external_position);
            const confidence = nestedNumber(lastMatch, ["confidence"]);
            const matchStatus = typeof lastMatch?.status === "string" ? lastMatch.status : undefined;
            const matchReason = typeof lastMatch?.reason === "string" ? lastMatch.reason : null;
            const bundleId = typeof activeMap?.bundle_id === "string" ? activeMap.bundle_id : undefined;
            const estimatorHealth = typeof estimator?.health === "string" ? estimator.health : undefined;
            const externalStatus = typeof externalPosition?.status === "string" ? externalPosition.status : null;
            const externalWarnings = stringArray(externalPosition?.last_warnings);
            const logPath = typeof output?.log_path === "string" ? output.log_path : undefined;
            const sequence = typeof status.sequence === "number" || typeof status.sequence === "string" ? status.sequence : undefined;
            return (
              <div key={`runtime-status-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className={statusClass(matchStatus)}>
                    {statusIcon(matchStatus)}
                    {formatLabel(matchStatus)}
                  </span>
                  <span className="font-mono text-slate-500">seq {formatLabel(sequence)}</span>
                  {matchReason && <span className="font-mono text-amber-200/80 truncate">{formatLabel(matchReason)}</span>}
                  {confidence != null && <span className="font-mono text-slate-500">conf {confidence.toFixed(2)}</span>}
                </div>
                <div className="font-mono text-slate-500 truncate">
                  map {formatLabel(bundleId)} / estimator {formatLabel(estimatorHealth)}
                  {externalStatus ? ` / ext ${formatLabel(externalStatus)}` : ""}
                </div>
                {externalWarnings.length > 0 && (
                  <div className="font-mono text-amber-200/80 truncate">ext warnings {externalWarnings.join(", ")}</div>
                )}
                <div className="font-mono text-slate-500 truncate">log {formatLabel(logPath)}</div>
              </div>
            );
          })}
        </div>
      )}

      {details.log_previews.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Log preview</div>
          {details.log_previews.slice(0, 2).map((preview) => (
            <div key={preview.name} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-1">
              <div className="flex items-center justify-between gap-2 font-mono text-slate-400">
                <span className="truncate">{preview.name}</span>
                <span>{preview.records.length} shown{preview.truncated ? "+" : ""}</span>
              </div>
              {preview.records.map((record) => (
                <div key={`${preview.name}-${record.line_number}`} className="rounded bg-bg-base/60 px-2 py-1 space-y-0.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={statusClass(record.status)}>
                      {statusIcon(record.status)}
                      {formatLabel(record.status)}
                    </span>
                    <span className="font-mono text-slate-500">L{record.line_number}</span>
                    {record.sequence != null && <span className="font-mono text-slate-500">seq {record.sequence}</span>}
                    {record.tile_id && <span className="font-mono text-slate-400 truncate">tile {record.tile_id}</span>}
                    {record.reason && <span className="font-mono text-amber-200/80 truncate">{record.reason}</span>}
                  </div>
                  <div className="font-mono text-slate-500 truncate">
                    conf {record.confidence != null ? record.confidence.toFixed(2) : "n/a"}
                    {record.inliers != null ? `, inliers ${record.inliers}` : ""}
                    {record.reprojection_error_px != null ? `, reproj ${record.reprojection_error_px.toFixed(1)} px` : ""}
                    {record.external_position_status ? `, ext ${record.external_position_status}` : ""}
                    {record.external_position_message_type ? `/${record.external_position_message_type}` : ""}
                    {record.external_position_warnings?.length ? `, warnings ${record.external_position_warnings.join(", ")}` : ""}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {details.log_timelines.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Frame timelines</div>
          {details.log_timelines.slice(0, 3).map((timeline) => (
            <div key={timeline.path} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-1">
              <div className="flex items-center justify-between gap-2 font-mono text-slate-400">
                <span className="truncate" title={timeline.path}>{timeline.name}</span>
                <span>{timeline.total_records ?? "skipped"} rec, {formatPercent(timeline.accepted_rate)} accepted</span>
              </div>
              <div className="flex h-5 gap-0.5">
                {timeline.segments.map((segment) => (
                  <div
                    key={`${timeline.path}-${segment.index}`}
                    className={`min-w-[4px] flex-1 rounded-sm border ${timelineSegmentClass(segment.dominant_status)}`}
                    title={[
                      `lines ${segment.start_line}-${segment.end_line}`,
                      `records ${segment.total_records}`,
                      `status ${formatLabel(segment.dominant_status)}`,
                      `accepted ${formatPercent(segment.accepted_rate)}`,
                      `conf ${segment.average_confidence != null ? segment.average_confidence.toFixed(2) : "n/a"}`,
                    ].join(" / ")}
                  />
                ))}
              </div>
              <div className="grid grid-cols-2 gap-2 font-mono text-slate-500">
                <span>status {formatCounts(timeline.status_counts)}</span>
                <span>ext {formatCounts(timeline.external_position_status_counts)}</span>
                {timeline.external_position_warning_counts && <span>ext warn {formatCounts(timeline.external_position_warning_counts)}</span>}
                <span>conf {timeline.average_confidence != null ? timeline.average_confidence.toFixed(2) : "n/a"}</span>
                <span>inliers {timeline.average_inliers != null ? timeline.average_inliers.toFixed(1) : "n/a"}</span>
                <span>reproj {timeline.average_reprojection_error_px != null ? `${timeline.average_reprojection_error_px.toFixed(1)} px` : "n/a"}</span>
                <span>seq {timeline.first_sequence ?? "n/a"}-{timeline.last_sequence ?? "n/a"}</span>
              </div>
              {timeline.truncated && (
                <div className="text-amber-200/80">
                  Timeline skipped because this log is larger than the desktop detail limit.
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {details.artifacts.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Full artifacts</div>
          {details.artifacts.slice(0, 8).map((artifact) => (
            <div key={artifact.path} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-mono text-slate-400 truncate" title={artifact.path}>
                    {artifact.path}
                  </div>
                  <div className="font-mono text-slate-500">
                    {formatLabel(artifact.kind)} / {formatBundleSize(artifact.size_bytes)}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => onExtractArtifact(artifact.path)}
                  disabled={extractingEntry === artifact.path}
                  className="btn-secondary text-[10px] py-1 px-2 shrink-0"
                >
                  <FileDown size={11} /> Extract
                </button>
              </div>
            </div>
          ))}
          {details.artifacts.length > 8 && (
            <div className="font-mono text-[10px] text-slate-500">
              {details.artifacts.length - 8} more extractable artifacts in this bundle
            </div>
          )}
        </div>
      )}

      {details.image_previews.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Image artifacts</div>
          <div className="grid grid-cols-2 gap-2">
            {details.image_previews.map((image) => (
              <div key={image.path} className="rounded border border-border/60 bg-bg-surface/40 p-1.5 space-y-1">
                <img
                  src={`data:${image.mime_type};base64,${image.base64_data}`}
                  alt={image.name}
                  className="h-24 w-full rounded border border-border/50 bg-bg-base object-contain"
                  loading="lazy"
                />
                <div className="font-mono text-[10px] text-slate-500 truncate" title={image.path}>
                  {image.name} / {formatBundleSize(image.size_bytes)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {details.px4_evidence_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">PX4 receiver evidence</div>
          {details.px4_evidence_reports.slice(0, 2).map((report, index) => (
            <div key={`${report.expected_message}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className={statusClass(report.status)}>
                  {statusIcon(report.status)}
                  {formatLabel(report.status)}
                </span>
                <span className="font-mono text-slate-400">{formatLabel(report.expected_message)}</span>
                <span className="font-mono text-slate-500">{report.sample_count ?? 0} samples</span>
                {report.observed_rate_hz != null && (
                  <span className="font-mono text-slate-500">{report.observed_rate_hz.toFixed(2)}hz</span>
                )}
                {report.latest_sample_age_s != null && (
                  <span className="font-mono text-slate-500">age {report.latest_sample_age_s.toFixed(2)}s</span>
                )}
                {report.mavlink_version != null && (
                  <span className="font-mono text-slate-500">mavlink v{report.mavlink_version}</span>
                )}
                {report.has_udp_14550 && <span className="font-mono text-slate-500">udp 14550</span>}
              </div>
              <div className="font-mono text-slate-500 truncate">pos [{formatVector(report.last_position)}]</div>
              {report.issues.slice(0, 2).map((issue) => (
                <div key={issue} className="text-amber-200/80 truncate">
                  {issue}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {details.px4_param_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">PX4 parameter check</div>
          {details.px4_param_reports.slice(0, 2).map((report, index) => (
            <div key={`${report.ev_ctrl}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className={statusClass(report.status)}>
                  {statusIcon(report.status)}
                  {formatLabel(report.status)}
                </span>
                <span className="font-mono text-slate-500">EV {report.ev_ctrl ?? "n/a"}</span>
                <span className="font-mono text-slate-500">HGT {report.hgt_ref ?? "n/a"}</span>
                <span className="font-mono text-slate-500">GPS {report.gps_ctrl ?? "n/a"}</span>
                <span className="font-mono text-slate-500">NOISE {report.ev_noise_mode ?? "n/a"}</span>
                {report.ev_delay_ms != null && (
                  <span className="font-mono text-slate-500">delay {report.ev_delay_ms.toFixed(0)}ms</span>
                )}
              </div>
              {report.issues.slice(0, 2).map((issue) => (
                <div key={issue} className="text-amber-200/80 truncate">
                  {issue}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {details.ardupilot_param_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">ArduPilot parameter check</div>
          {details.ardupilot_param_reports.slice(0, 2).map((report, index) => (
            <div key={`${report.source_set}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className={statusClass(report.status)}>
                  {statusIcon(report.status)}
                  {formatLabel(report.status)}
                </span>
                <span className="font-mono text-slate-500">SRC {report.source_set ?? "n/a"}</span>
                <span className="font-mono text-slate-500">VISO {report.viso_type ?? "n/a"}</span>
                <span className="font-mono text-slate-500">XY {report.posxy_source ?? "n/a"}</span>
                <span className="font-mono text-slate-500">Z {report.posz_source ?? "n/a"}</span>
                <span className="font-mono text-slate-500">YAW {report.yaw_source ?? "n/a"}</span>
              </div>
              {report.issues.slice(0, 2).map((issue) => (
                <div key={issue} className="text-amber-200/80 truncate">
                  {issue}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {details.feature_method_benchmark_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Feature method benchmarks</div>
          {details.feature_method_benchmark_reports.slice(0, 2).map((report, index) => (
            <div key={`${report.case_name}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className={statusClass(report.status)}>
                  {statusIcon(report.status)}
                  {formatLabel(report.status)}
                </span>
                <span className="font-mono text-slate-400 truncate">{formatLabel(report.case_name)}</span>
                <span className="font-mono text-slate-500">{formatLabel(report.expected)}</span>
                <span className="font-mono text-slate-500">pick {formatLabel(report.recommended_method)}</span>
              </div>
              {report.methods.slice(0, 4).map((method) => (
                <div key={`${report.case_name}-${method.method}`} className="flex flex-wrap items-center gap-1.5 font-mono text-slate-500">
                  <span className={statusClass(method.status)}>
                    {statusIcon(method.status)}
                    {formatLabel(method.status)}
                  </span>
                  <span>{formatLabel(method.method)}</span>
                  <span>{method.total_records ?? 0} rec</span>
                  <span>{formatPercent(method.accepted_rate)} accepted</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {details.field_evidence_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Field evidence</div>
          {details.field_evidence_reports.slice(0, 2).map((report, index) => (
            <div key={`${report.manifest_path}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className={statusClass(report.status)}>
                  {statusIcon(report.status)}
                  {formatLabel(report.status)}
                </span>
                <span className="font-mono text-slate-400 truncate">{formatLabel(report.manifest_path)}</span>
                <span className="font-mono text-slate-500">coverage {formatLabel(report.coverage_status)}</span>
                <span className="font-mono text-slate-500">replay {formatLabel(report.replay_status)}</span>
              </div>
              <div className="font-mono text-slate-500">
                field cases {report.field_case_count ?? 0}/{report.case_count ?? 0}
                {arrayCount(report.covered_conditions) > 0 ? `, conditions ${arrayCount(report.covered_conditions)}/${arrayCount(report.required_conditions)}` : ""}
                {(report.capture_metadata_issue_count ?? 0) > 0 ? `, metadata issues ${report.capture_metadata_issue_count}` : ""}
              </div>
              {report.requirements.length > 0 && (
                <div className="flex flex-wrap gap-1 pt-0.5">
                  {report.requirements.slice(0, 8).map((requirement) => (
                    <span key={`${report.manifest_path}-${requirement.key}`} className={statusClass(requirement.status)}>
                      {statusIcon(requirement.status)}
                      {formatLabel(requirement.key)} {formatLabel(requirement.status)}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {details.field_collection_plan_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Field collection plan</div>
          {details.field_collection_plan_reports.slice(0, 2).map((report, index) => {
            const fieldCommands = fieldConditionCommandRecords(report.conditions);
            return (
              <div key={`${report.manifest_path}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className={statusClass(report.status)}>
                    {statusIcon(report.status)}
                    {formatLabel(report.status)}
                  </span>
                  <span className="font-mono text-slate-400 truncate">{formatLabel(report.site_name)}</span>
                  <span className="font-mono text-slate-500">
                    registered {report.summary.registered_count ?? 0}/{report.summary.required_count ?? 0}
                  </span>
                  <span className="font-mono text-slate-500">placeholder {report.summary.placeholder_count ?? 0}</span>
                  <span className="font-mono text-slate-500">missing {report.summary.missing_count ?? 0}</span>
                  <span className="font-mono text-slate-500">capture cmds {report.pending_capture_command_count ?? 0}</span>
                  <span className="font-mono text-slate-500">metadata cmds {report.pending_metadata_update_command_count ?? 0}</span>
                  <span className="font-mono text-slate-500">source logs {report.condition_source_log_count ?? 0}</span>
                  <span className="font-mono text-slate-500">runtime paths {report.runtime_status_path_count ?? 0}</span>
                  {fieldCommands.length > 0 && (
                    <button
                      type="button"
                      onClick={() => navigator.clipboard.writeText(fieldCommands.map((item) => `# ${formatLabel(item.label)} ${item.stage}\n${item.command}`).join("\n\n"))}
                      className="btn-secondary px-1.5 py-0.5 text-[10px]"
                      title="Copy all field collection commands"
                    >
                      <Clipboard size={9} />
                      copy commands
                    </button>
                  )}
                </div>
                <div className="font-mono text-slate-500 truncate">
                  manifest {formatLabel(report.manifest_path)} / log {formatLabel(report.source_log)}
                </div>
                {report.capture_root && (
                  <div className="font-mono text-slate-500 truncate">
                    capture root {formatLabel(report.capture_root)}
                  </div>
                )}
                {fieldCommands.length > 0 && (
                  <div className="space-y-1 pt-0.5">
                    {fieldCommands.slice(0, 6).map((item, commandIndex) => (
                      <button
                        key={`${report.manifest_path}-${item.label}-${item.stage}-${commandIndex}`}
                        type="button"
                        onClick={() => navigator.clipboard.writeText(item.command)}
                        className="flex w-full min-w-0 items-center gap-1.5 rounded border border-border/50 bg-bg-base/50 px-2 py-1 text-left font-mono text-[10px] text-slate-400 hover:border-cyan-500/40 hover:text-cyan-200"
                        title={item.command}
                      >
                        <Clipboard size={9} className="shrink-0" />
                        <span className="shrink-0 text-slate-500">{formatLabel(item.label)} {item.stage}</span>
                        <span className="truncate whitespace-pre">{item.command}</span>
                      </button>
                    ))}
                  </div>
                )}
                {report.conditions.length > 0 && (
                  <div className="flex flex-wrap gap-1 pt-0.5">
                    {report.conditions.slice(0, 8).map((condition) => (
                      <span
                        key={`${report.manifest_path}-${condition.condition}`}
                        className={statusClass(condition.status)}
                        title={[
                          condition.source_log ? `log ${condition.source_log}` : "",
                          condition.runtime_status_path ? `runtime ${condition.runtime_status_path}` : "",
                          condition.capture_output_dir ? `capture ${condition.capture_output_dir}` : "",
                        ].filter(Boolean).join(" / ") || undefined}
                      >
                        {statusIcon(condition.status)}
                        {formatLabel(condition.condition)} {formatLabel(condition.status)}
                        {condition.has_capture_command ? " cap" : ""}
                        {condition.has_metadata_update_command ? " meta" : ""}
                        {condition.has_register_command ? " reg" : ""}
                        {condition.runtime_status_path ? " status" : ""}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {details.field_capture_preflight_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Field capture preflight</div>
          {details.field_capture_preflight_reports.slice(0, 2).map((report, index) => {
            const commandActions = report.next_actions.filter((action) => action.command);
            const bundleDiagnostics = report.checks
              .map((check) => check.bundle_diagnostic)
              .filter((diagnostic): diagnostic is BundleDiagnostic => Boolean(diagnostic));
            return (
              <div key={`${report.condition}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className={statusClass(report.status)}>
                    {statusIcon(report.status)}
                    {formatLabel(report.status)}
                  </span>
                  <span className="font-mono text-slate-400 truncate">{formatLabel(report.condition)}</span>
                  <span className={report.ready_for_capture ? "badge-green" : "badge-red"}>
                    {statusIcon(report.ready_for_capture ? "passed" : "failed")}
                    capture {report.ready_for_capture ? "ready" : "blocked"}
                  </span>
                  <span className={report.ready_for_registration ? "badge-green" : "badge-yellow"}>
                    {statusIcon(report.ready_for_registration ? "passed" : "degraded")}
                    register {report.ready_for_registration ? "ready" : "waiting"}
                  </span>
                  {commandActions.length > 0 && (
                    <button
                      type="button"
                      onClick={() => navigator.clipboard.writeText(commandActions.map((action) => `# ${formatLabel(action.id)}\n${action.command}`).join("\n\n"))}
                      className="btn-secondary px-1.5 py-0.5 text-[10px]"
                      title="Copy field preflight next-action commands"
                    >
                      <Clipboard size={9} />
                      copy actions
                    </button>
                  )}
                </div>
                <div className="font-mono text-slate-500 truncate">
                  bundle {formatLabel(report.bundle_path)} / log {formatLabel(report.source_log)}
                </div>
                {report.checks.filter((check) => check.status !== "passed").length > 0 && (
                  <div className="flex flex-wrap gap-1 pt-0.5">
                    {report.checks.filter((check) => check.status !== "passed").slice(0, 6).map((check) => (
                      <span key={`${report.condition}-${check.name}`} className={statusClass(check.status)} title={check.message}>
                        {statusIcon(check.status)}
                        {formatLabel(check.name)} {formatLabel(check.status)}
                      </span>
                    ))}
                  </div>
                )}
                {bundleDiagnostics.length > 0 && (
                  <div className="space-y-1 rounded border border-cyan-500/20 bg-cyan-500/5 px-2 py-1 text-[10px] text-slate-400">
                    {bundleDiagnostics.slice(0, 1).map((diagnostic, diagnosticIndex) => (
                      <div key={`${report.condition}-bundle-diagnostic-${diagnosticIndex}`} className="space-y-1">
                        {diagnostic.missing_required_files.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            <span className="text-slate-500">missing</span>
                            {diagnostic.missing_required_files.slice(0, 5).map((file) => (
                              <span key={`${report.condition}-missing-${file}`} className="rounded bg-bg-base/70 px-1 font-mono text-slate-300">
                                {file}
                              </span>
                            ))}
                            {diagnostic.missing_required_files.length > 5 && (
                              <span className="text-slate-500">+{diagnostic.missing_required_files.length - 5}</span>
                            )}
                          </div>
                        )}
                        {diagnostic.bundle_candidates.length > 0 && (
                          <div className="space-y-0.5">
                            <div className="text-slate-500">bundle candidates {diagnostic.bundle_candidate_count ?? diagnostic.bundle_candidates.length}</div>
                            {diagnostic.bundle_candidates.slice(0, 2).map((candidate) => (
                              <div key={`${report.condition}-candidate-${candidate.path}`} className="flex min-w-0 flex-wrap items-center gap-1">
                                <span className="font-mono text-slate-300 truncate">{formatLabel(candidate.path)}</span>
                                {candidate.bundle_id && <span className="badge-yellow">{formatLabel(candidate.bundle_id)}</span>}
                                {candidate.field_proof_warning && (
                                  <span className="badge-yellow" title={candidate.field_proof_warning}>smoke only</span>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                        {diagnostic.map_source_candidates.length > 0 && (
                          <div className="space-y-0.5">
                            <div className="text-slate-500">map sources {diagnostic.map_source_candidate_count ?? diagnostic.map_source_candidates.length}</div>
                            {diagnostic.map_source_candidates.slice(0, 2).map((source) => (
                              <div key={`${report.condition}-map-source-${source.path}`} className="flex min-w-0 flex-wrap items-center gap-1">
                                <span className="font-mono text-slate-300 truncate">{formatLabel(source.path)}</span>
                                <span className="badge-green">{formatLabel(source.name)}</span>
                                {source.source_format && <span className="badge-cyan">{formatLabel(source.source_format)}</span>}
                                {source.georef_source && <span className="badge-yellow">{formatLabel(source.georef_source)}</span>}
                                {source.requires_import && <span className="badge-yellow">import</span>}
                              </div>
                            ))}
                          </div>
                        )}
                        {diagnostic.search_roots.length > 0 && (
                          <div className="space-y-0.5">
                            <div className="text-slate-500">searched roots {diagnostic.search_root_count ?? diagnostic.search_roots.length}</div>
                            {diagnostic.search_roots.slice(0, 3).map((root) => (
                              <div key={`${report.condition}-search-root-${root}`} className="font-mono text-slate-300 truncate">
                                {formatLabel(root)}
                              </div>
                            ))}
                          </div>
                        )}
                        {diagnostic.recommended_actions.length > 0 && (
                          <div className="space-y-0.5">
                            {diagnostic.recommended_actions.slice(0, 2).map((action, actionIndex) => (
                              action.command ? (
                                <button
                                  key={`${report.condition}-diagnostic-action-${action.id}-${actionIndex}`}
                                  type="button"
                                  onClick={() => action.command && navigator.clipboard.writeText(action.command)}
                                  className="flex w-full min-w-0 items-center gap-1.5 rounded border border-border/50 bg-bg-base/60 px-2 py-1 text-left font-mono text-[10px] text-slate-400 hover:border-cyan-500/40 hover:text-cyan-200"
                                  title={[action.command, action.notes].filter(Boolean).join("\n") || undefined}
                                >
                                  <Clipboard size={9} className="shrink-0" />
                                  <span className="shrink-0 text-slate-500">{formatLabel(action.id)}</span>
                                  <span className={statusClass(action.status)}>{formatLabel(action.status)}</span>
                                  <span className="truncate whitespace-pre">{action.command}</span>
                                </button>
                              ) : (
                                <div
                                  key={`${report.condition}-diagnostic-action-${action.id}-${actionIndex}`}
                                  className="flex min-w-0 flex-wrap items-center gap-1 rounded border border-border/40 bg-bg-base/40 px-2 py-1"
                                  title={action.notes || undefined}
                                >
                                  <span className={statusClass(action.status)}>{formatLabel(action.status)}</span>
                                  <span className="truncate">{formatLabel(action.title)}</span>
                                  {action.desktop_action && <span className="font-mono text-slate-500">{action.desktop_action}</span>}
                                </div>
                              )
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {commandActions.length > 0 && (
                  <div className="space-y-1 pt-0.5">
                    {commandActions.slice(0, 4).map((action, actionIndex) => (
                      <button
                        key={`${report.condition}-${action.id}-${actionIndex}`}
                        type="button"
                        onClick={() => action.command && navigator.clipboard.writeText(action.command)}
                        className="flex w-full min-w-0 items-center gap-1.5 rounded border border-border/50 bg-bg-base/50 px-2 py-1 text-left font-mono text-[10px] text-slate-400 hover:border-cyan-500/40 hover:text-cyan-200"
                        title={action.command}
                      >
                        <Clipboard size={9} className="shrink-0" />
                        <span className="shrink-0 text-slate-500">{formatLabel(action.id)}</span>
                        <span className={statusClass(action.status)}>{formatLabel(action.status)}</span>
                        <span className="truncate whitespace-pre">{action.command}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {details.threshold_tuning_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Threshold tuning</div>
          {details.threshold_tuning_reports.slice(0, 2).map((report, index) => {
            const margins = asRecord(report.margins);
            return (
              <div key={`${report.manifest_path}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className={statusClass(report.status)}>
                    {statusIcon(report.status)}
                    {formatLabel(report.status)}
                  </span>
                  <span className="font-mono text-slate-400 truncate">{formatLabel(report.manifest_path)}</span>
                  <span className="font-mono text-slate-500">coverage {formatLabel(report.coverage_status)}</span>
                  <span className="font-mono text-slate-500">replay {formatLabel(report.replay_status)}</span>
                </div>
                <div className="font-mono text-slate-500">
                  field cases {report.field_case_count ?? 0}/{report.case_count ?? 0}
                  {arrayCount(report.covered_conditions) > 0 ? `, conditions ${arrayCount(report.covered_conditions)}` : ""}
                  {(report.capture_metadata_issue_count ?? 0) > 0 ? `, metadata issues ${report.capture_metadata_issue_count}` : ""}
                </div>
                <div className="flex flex-wrap gap-1.5 font-mono text-slate-500">
                  <span>good margin {formatMargin(margins?.good_map_accepted_rate)}</span>
                  <span>degraded margin {formatMargin(margins?.degraded_accepted_rate)}</span>
                  <span>wrong margin {formatMargin(margins?.wrong_map_accepted_rate)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {details.rosbag_export_validation_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">ROS bag export validation</div>
          {details.rosbag_export_validation_reports.slice(0, 2).map((report, index) => (
            <div key={`${report.artifact_path}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className={statusClass(report.status)}>
                  {statusIcon(report.status)}
                  {formatLabel(report.status)}
                </span>
                <span className="font-mono text-slate-400 truncate">{formatLabel(report.format)}</span>
                <span className="font-mono text-slate-500">{report.message_count ?? 0} msg</span>
                <span className="font-mono text-slate-500">{report.topic_count ?? 0} topics</span>
              </div>
              <div className="font-mono text-slate-500 truncate">
                {formatLabel(report.artifact_path)}
              </div>
              {report.topics.length > 0 && (
                <div className="flex flex-wrap gap-1 pt-0.5">
                  {report.topics.slice(0, 4).map((topic) => (
                    <span key={`${report.artifact_path}-${topic}`} className="rounded border border-border/60 bg-bg-base px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                      {topic}
                    </span>
                  ))}
                </div>
              )}
              {report.issues.slice(0, 2).map((issue) => (
                <div key={issue} className="text-amber-200/80 truncate">
                  {issue}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {details.rosbag2_cli_review_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">rosbag2 CLI reviews</div>
          {details.rosbag2_cli_review_reports.slice(0, 2).map((report, index) => (
            <div key={`${report.artifact_path ?? report.path}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className={statusClass(report.status)}>
                  {statusIcon(report.status)}
                  {formatLabel(report.status)}
                </span>
                <span className="font-mono text-slate-400 truncate">{formatLabel(report.validation_format)}</span>
                <span className="font-mono text-slate-500">validation {formatLabel(report.validation_status)}</span>
                <span className="font-mono text-slate-500">cli {formatLabel(report.ros2_cli_status)}</span>
                <span className="font-mono text-slate-500">exit {report.ros2_cli_exit_code ?? "n/a"}</span>
              </div>
              <div className="font-mono text-slate-500 truncate">
                {formatLabel(report.bag_dir ?? report.artifact_path ?? report.path)}
              </div>
              {report.issues.slice(0, 2).map((issue) => (
                <div key={issue} className="text-amber-200/80 truncate">
                  {issue}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {details.replay_reports.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Replay gates</div>
          {details.replay_reports.slice(0, 4).map((report, index) => (
            <div key={`${report.case_name}-${index}`} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className={statusClass(report.status)}>
                  {statusIcon(report.status)}
                  {formatLabel(report.status)}
                </span>
                <span className="font-mono text-slate-400 truncate">{formatLabel(report.case_name)}</span>
                <span className="font-mono text-slate-500">{formatLabel(report.expected)}</span>
                <span className="font-mono text-slate-500">{report.total_records ?? 0} rec</span>
                <span className="font-mono text-slate-500">{formatPercent(report.accepted_rate)} accepted</span>
              </div>
              {report.issues.slice(0, 2).map((issue) => (
                <div key={issue} className="text-amber-200/80 truncate">
                  {issue}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function SupportBundleList({
  bundles,
  downloadDir,
  onChanged,
}: {
  bundles: SupportBundleFile[];
  downloadDir: string;
  onChanged?: () => void | Promise<void>;
}) {
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [detailLoadingPath, setDetailLoadingPath] = useState<string | null>(null);
  const [extractingEntry, setExtractingEntry] = useState<string | null>(null);
  const [detailsByPath, setDetailsByPath] = useState<Record<string, SupportBundleDetails>>({});
  const [message, setMessage] = useState<string | null>(null);
  if (bundles.length === 0) return null;

  const toggleDetails = async (bundle: SupportBundleFile) => {
    if (expandedPath === bundle.path) {
      setExpandedPath(null);
      return;
    }
    setExpandedPath(bundle.path);
    if (detailsByPath[bundle.path]) return;
    setDetailLoadingPath(bundle.path);
    setMessage(null);
    try {
      const details = await cmd.readSupportBundleDetails(bundle.path);
      setDetailsByPath((current) => ({ ...current, [bundle.path]: details }));
    } catch (error) {
      setMessage(`Could not read ${bundle.name}: ${error}`);
    } finally {
      setDetailLoadingPath(null);
    }
  };

  const revealBundle = async (bundle: SupportBundleFile) => {
    setBusyPath(bundle.path);
    setMessage(null);
    try {
      await cmd.revealSupportBundle(bundle.path);
    } catch (error) {
      setMessage(`Could not reveal ${bundle.name}: ${error}`);
    } finally {
      setBusyPath(null);
    }
  };

  const copyPath = async (bundle: SupportBundleFile) => {
    try {
      await navigator.clipboard.writeText(bundle.path);
      setMessage(`Copied ${bundle.name}`);
    } catch (error) {
      setMessage(`Could not copy path: ${error}`);
    }
  };

  const extractArtifact = async (bundle: SupportBundleFile, entryPath: string) => {
    setBusyPath(bundle.path);
    setExtractingEntry(entryPath);
    setMessage(null);
    try {
      const artifact = await cmd.extractSupportBundleArtifact(bundle.path, entryPath);
      setMessage(`Extracted ${artifact.name}`);
      await cmd.revealSupportBundle(artifact.path);
    } catch (error) {
      setMessage(`Could not extract artifact: ${error}`);
    } finally {
      setBusyPath(null);
      setExtractingEntry(null);
    }
  };

  const deleteBundle = async (bundle: SupportBundleFile) => {
    if (!window.confirm(`Delete ${bundle.name}?`)) return;
    setBusyPath(bundle.path);
    setMessage(null);
    try {
      await cmd.deleteSupportBundle(bundle.path);
      if (expandedPath === bundle.path) setExpandedPath(null);
      await onChanged?.();
      setMessage(`Deleted ${bundle.name}`);
    } catch (error) {
      setMessage(`Could not delete ${bundle.name}: ${error}`);
    } finally {
      setBusyPath(null);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-bg-base px-3 py-2 text-[11px] text-slate-400 space-y-2">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium text-slate-300 flex items-center gap-1.5">
          <Archive size={12} className="text-amber-300" /> Downloaded support bundles
        </span>
        <span className="font-mono truncate">{downloadDir}</span>
      </div>
      {message && (
        <div className="rounded-md border border-border/70 bg-bg-surface px-2 py-1 text-[11px] text-slate-300">
          {message}
        </div>
      )}
      {bundles.slice(0, 6).map((bundle) => (
        <div key={bundle.path} className="rounded-md border border-border/70 bg-bg-surface/40 px-2 py-1.5 space-y-1">
          <div className="flex items-center justify-between gap-3 font-mono">
            <span className="truncate text-slate-300">{bundle.name}</span>
            <span className="shrink-0 text-slate-500">{formatBundleSize(bundle.size_bytes)}</span>
          </div>
          {bundle.summary ? (
            <>
              <div className="flex flex-wrap gap-1.5">
                {bundle.summary.bench_readiness_status && (
                  <span className={statusClass(bundle.summary.bench_readiness_status)}>
                    {statusIcon(bundle.summary.bench_readiness_status)}
                    readiness {formatLabel(bundle.summary.bench_readiness_status)}
                  </span>
                )}
                <span className={statusClass(bundle.summary.bundle_health_status)}>
                  {statusIcon(bundle.summary.bundle_health_status)}
                  health {formatLabel(bundle.summary.bundle_health_status)}
                </span>
                <span className={statusClass(bundle.summary.checksum_status)}>
                  {statusIcon(bundle.summary.checksum_status)}
                  checksums {formatLabel(bundle.summary.checksum_status)}
                </span>
                {bundle.summary.replay_gate_status && (
                  <span className={statusClass(bundle.summary.replay_gate_status)}>
                    {statusIcon(bundle.summary.replay_gate_status)}
                    replay {formatLabel(bundle.summary.replay_gate_status)}
                  </span>
                )}
                {bundle.summary.gnss_denied_plan_status && (
                  <span className={statusClass(bundle.summary.gnss_denied_plan_status)}>
                    {statusIcon(bundle.summary.gnss_denied_plan_status)}
                    gnss prep {formatLabel(bundle.summary.gnss_denied_plan_status)}
                  </span>
                )}
                {bundle.summary.px4_sitl_evidence_status && bundle.summary.px4_sitl_evidence_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.px4_sitl_evidence_status)}>
                    {statusIcon(bundle.summary.px4_sitl_evidence_status)}
                    px4 {formatLabel(bundle.summary.px4_sitl_evidence_status)}
                  </span>
                )}
                {bundle.summary.px4_sitl_prereq_status && bundle.summary.px4_sitl_prereq_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.px4_sitl_prereq_status)}>
                    {statusIcon(bundle.summary.px4_sitl_prereq_status)}
                    px4 prereqs {formatLabel(bundle.summary.px4_sitl_prereq_status)}
                  </span>
                )}
                {bundle.summary.px4_params_status && bundle.summary.px4_params_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.px4_params_status)}>
                    {statusIcon(bundle.summary.px4_params_status)}
                    params {formatLabel(bundle.summary.px4_params_status)}
                  </span>
                )}
                {bundle.summary.ardupilot_params_status && bundle.summary.ardupilot_params_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.ardupilot_params_status)}>
                    {statusIcon(bundle.summary.ardupilot_params_status)}
                    ardu {formatLabel(bundle.summary.ardupilot_params_status)}
                  </span>
                )}
                {bundle.summary.feature_method_benchmark_status && bundle.summary.feature_method_benchmark_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.feature_method_benchmark_status)}>
                    {statusIcon(bundle.summary.feature_method_benchmark_status)}
                    methods {formatLabel(bundle.summary.feature_method_benchmark_status)}
                  </span>
                )}
                {bundle.summary.field_evidence_status && bundle.summary.field_evidence_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.field_evidence_status)}>
                    {statusIcon(bundle.summary.field_evidence_status)}
                    field {formatLabel(bundle.summary.field_evidence_status)}
                  </span>
                )}
                {bundle.summary.field_collection_plan_status && bundle.summary.field_collection_plan_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.field_collection_plan_status)}>
                    {statusIcon(bundle.summary.field_collection_plan_status)}
                    plan {formatLabel(bundle.summary.field_collection_plan_status)}
                  </span>
                )}
                {bundle.summary.field_capture_preflight_status && bundle.summary.field_capture_preflight_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.field_capture_preflight_status)}>
                    {statusIcon(bundle.summary.field_capture_preflight_status)}
                    preflight {formatLabel(bundle.summary.field_capture_preflight_status)}
                  </span>
                )}
                {bundle.summary.threshold_tuning_status && bundle.summary.threshold_tuning_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.threshold_tuning_status)}>
                    {statusIcon(bundle.summary.threshold_tuning_status)}
                    thresholds {formatLabel(bundle.summary.threshold_tuning_status)}
                  </span>
                )}
                {bundle.summary.rosbag_export_validation_status && bundle.summary.rosbag_export_validation_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.rosbag_export_validation_status)}>
                    {statusIcon(bundle.summary.rosbag_export_validation_status)}
                    rosbag {formatLabel(bundle.summary.rosbag_export_validation_status)}
                  </span>
                )}
                {bundle.summary.rosbag2_cli_review_status && bundle.summary.rosbag2_cli_review_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.rosbag2_cli_review_status)}>
                    {statusIcon(bundle.summary.rosbag2_cli_review_status)}
                    rosbag2 {formatLabel(bundle.summary.rosbag2_cli_review_status)}
                  </span>
                )}
                {bundle.summary.evidence_workflow_status && bundle.summary.evidence_workflow_status !== "not_provided" && (
                  <span className={statusClass(bundle.summary.evidence_workflow_status)}>
                    {statusIcon(bundle.summary.evidence_workflow_status)}
                    workflow {formatLabel(bundle.summary.evidence_workflow_status)}
                  </span>
                )}
                {bundle.summary.elevation_status && (
                  <span className={bundle.summary.vertical_sanity_ready ? "badge-green" : statusClass(bundle.summary.elevation_status)}>
                    {statusIcon(bundle.summary.vertical_sanity_ready ? "passed" : bundle.summary.elevation_status)}
                    elevation {bundle.summary.vertical_sanity_ready ? "ready" : formatLabel(bundle.summary.elevation_status)}
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 gap-2 text-slate-500 font-mono">
                <span className="truncate">
                  {formatLabel(bundle.summary.map_source)}
                  {bundle.summary.source_name ? ` / ${bundle.summary.source_name}` : ""}
                </span>
                <span className="truncate text-right">
                  {formatLabel(bundle.summary.georef_source)}
                  {bundle.summary.georef_confidence != null
                    ? ` ${Math.round(bundle.summary.georef_confidence * 100)}%`
                    : ""}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-slate-500 font-mono">
                <span className="truncate">bundle {formatLabel(bundle.summary.bundle_id)}</span>
                <span className="truncate text-right">
                  {bundle.summary.covered_file_count ?? 0} files
                  {bundle.summary.replay_case_count ? `, ${bundle.summary.replay_case_count} replay cases` : ""}
                  {bundle.summary.elevation_asset_count ? `, ${bundle.summary.elevation_asset_count} elevation` : ""}
                </span>
              </div>
            </>
          ) : (
            <div className="text-slate-500">No parsed support manifest in this ZIP.</div>
          )}
          {expandedPath === bundle.path && (
            <div className="rounded-md border border-border/70 bg-bg-base px-2 py-1.5 space-y-1 text-slate-500">
              <div className="font-mono break-all">{bundle.path}</div>
              {bundle.summary && (
                <div className="grid grid-cols-2 gap-2 font-mono">
                  <span>health {formatLabel(bundle.summary.bundle_health_status)}</span>
                  <span>checksums {formatLabel(bundle.summary.checksum_status)}</span>
                  <span>elevation {bundle.summary.vertical_sanity_ready ? "ready" : formatLabel(bundle.summary.elevation_status)}</span>
                  <span>replay {formatLabel(bundle.summary.replay_gate_status)}</span>
                  <span>gnss prep {formatLabel(bundle.summary.gnss_denied_plan_status)}</span>
                  <span>px4 {formatLabel(bundle.summary.px4_sitl_evidence_status)}</span>
                  <span>px4 prereqs {formatLabel(bundle.summary.px4_sitl_prereq_status)}</span>
                  <span>px4 samples {bundle.summary.px4_sitl_sample_count ?? 0}</span>
                  <span>params {formatLabel(bundle.summary.px4_params_status)}</span>
                  <span>EV ctrl {bundle.summary.px4_ev_ctrl ?? "n/a"}</span>
                  <span>methods {formatLabel(bundle.summary.feature_method_benchmark_status)}</span>
                  <span>pick {formatLabel(bundle.summary.feature_method_benchmark_recommended)}</span>
                  <span>field {formatLabel(bundle.summary.field_evidence_status)}</span>
                  <span>field cases {bundle.summary.field_evidence_field_case_count ?? 0}</span>
                  <span>field metadata issues {bundle.summary.field_evidence_capture_metadata_issue_count ?? 0}</span>
                  <span>plan {formatLabel(bundle.summary.field_collection_plan_status)}</span>
                  <span>
                    plan registered {bundle.summary.field_collection_plan_registered_count ?? 0}/
                    {bundle.summary.field_collection_plan_required_count ?? 0}
                  </span>
                  <span>plan capture cmds {bundle.summary.field_collection_plan_pending_capture_command_count ?? 0}</span>
                  <span>plan metadata cmds {bundle.summary.field_collection_plan_pending_metadata_update_command_count ?? 0}</span>
                  <span>plan source logs {bundle.summary.field_collection_plan_condition_source_log_count ?? 0}</span>
                  <span>plan runtime paths {bundle.summary.field_collection_plan_runtime_status_path_count ?? 0}</span>
                  <span>preflight {formatLabel(bundle.summary.field_capture_preflight_status)}</span>
                  <span>preflight ready {bundle.summary.field_capture_preflight_ready_for_capture_count ?? 0}</span>
                  <span>preflight failed checks {bundle.summary.field_capture_preflight_failed_check_count ?? 0}</span>
                  <span>preflight blocked actions {bundle.summary.field_capture_preflight_blocked_action_count ?? 0}</span>
                  <span>thresholds {formatLabel(bundle.summary.threshold_tuning_status)}</span>
                  <span>threshold cases {bundle.summary.threshold_tuning_field_case_count ?? 0}</span>
                  <span>threshold metadata issues {bundle.summary.threshold_tuning_capture_metadata_issue_count ?? 0}</span>
                  <span>rosbag {formatLabel(bundle.summary.rosbag_export_validation_status)}</span>
                  <span>
                    rosbag {bundle.summary.rosbag_export_validation_message_count ?? 0} msg /
                    {" "}{bundle.summary.rosbag_export_validation_topic_count ?? 0} topics
                  </span>
                  <span>rosbag2 cli {formatLabel(bundle.summary.rosbag2_cli_review_status)}</span>
                  <span>rosbag2 cli reports {bundle.summary.rosbag2_cli_review_report_count ?? 0}</span>
                  <span>workflow {formatLabel(bundle.summary.evidence_workflow_status)}</span>
                  <span>workflow validation {formatLabel(bundle.summary.evidence_workflow_validation_status)}</span>
                  <span>workflow runtime {formatLabel(bundle.summary.evidence_workflow_runtime_status)}</span>
                  <span>workflow proof {formatLabel(bundle.summary.evidence_workflow_provenance_status)}</span>
                  <span>workflow steps {bundle.summary.evidence_workflow_step_count ?? 0}</span>
                  <span>workflow issues {bundle.summary.evidence_workflow_issue_count ?? 0}</span>
                  <span className="truncate">workflow commit {bundle.summary.evidence_workflow_repo_commit?.slice(0, 8) ?? "n/a"}</span>
                  <span>ready {formatLabel(bundle.summary.bench_readiness_status)}</span>
                  <span>
                    gate {bundle.summary.bench_readiness_failed_count ?? 0} fail / {bundle.summary.bench_readiness_degraded_count ?? 0} degrade
                  </span>
                </div>
              )}
              {detailLoadingPath === bundle.path && (
                <div className="text-slate-400">Reading bundle details...</div>
              )}
              {detailsByPath[bundle.path] && (
                <SupportBundleDetailPanel
                  details={detailsByPath[bundle.path]}
                  extractingEntry={extractingEntry}
                  onExtractArtifact={(entryPath) => extractArtifact(bundle, entryPath)}
                />
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-1.5 pt-0.5">
            <button
              type="button"
              onClick={() => toggleDetails(bundle)}
              className="btn-secondary text-[10px] py-1 px-2"
            >
              <Eye size={11} /> Details
            </button>
            <button
              type="button"
              onClick={() => revealBundle(bundle)}
              disabled={busyPath === bundle.path}
              className="btn-secondary text-[10px] py-1 px-2"
            >
              <FolderOpen size={11} /> Reveal
            </button>
            <button
              type="button"
              onClick={() => copyPath(bundle)}
              className="btn-secondary text-[10px] py-1 px-2"
            >
              <Clipboard size={11} /> Copy path
            </button>
            <button
              type="button"
              onClick={() => deleteBundle(bundle)}
              disabled={busyPath === bundle.path}
              className="btn-secondary text-[10px] py-1 px-2 text-red-400 border-red-500/20"
            >
              <Trash2 size={11} /> Delete
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
