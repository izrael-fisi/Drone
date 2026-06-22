import { useState } from "react";
import { AlertTriangle, Archive, CheckCircle2, Clipboard, Eye, FileDown, FolderOpen, Trash2 } from "lucide-react";
import { cmd } from "../lib/tauri";
import type { SupportBundleDetails, SupportBundleFile } from "../lib/types";

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
          {details.field_collection_plan_reports.slice(0, 2).map((report, index) => (
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
                <span className="font-mono text-slate-500">source logs {report.condition_source_log_count ?? 0}</span>
                <span className="font-mono text-slate-500">runtime paths {report.runtime_status_path_count ?? 0}</span>
              </div>
              <div className="font-mono text-slate-500 truncate">
                manifest {formatLabel(report.manifest_path)} / log {formatLabel(report.source_log)}
              </div>
              {report.capture_root && (
                <div className="font-mono text-slate-500 truncate">
                  capture root {formatLabel(report.capture_root)}
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
                      {condition.runtime_status_path ? " status" : ""}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
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
                  <span>plan source logs {bundle.summary.field_collection_plan_condition_source_log_count ?? 0}</span>
                  <span>plan runtime paths {bundle.summary.field_collection_plan_runtime_status_path_count ?? 0}</span>
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
