import { useState } from "react";
import { AlertTriangle, Archive, CheckCircle2, Clipboard, Eye, FolderOpen, Trash2 } from "lucide-react";
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
  if (status === "passed" || status === "healthy") return "badge-green";
  if (status === "failed" || status === "error") return "badge-red";
  return "badge-yellow";
}

function statusIcon(status?: string) {
  if (status === "passed" || status === "healthy") return <CheckCircle2 size={11} />;
  return <AlertTriangle size={11} />;
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

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function nestedString(root: unknown, path: string[]) {
  let current: unknown = root;
  for (const key of path) {
    current = asRecord(current)?.[key];
  }
  return typeof current === "string" && current ? current : null;
}

function SupportBundleDetailPanel({ details }: { details: SupportBundleDetails }) {
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
        <span>health {formatLabel(healthStatus)}</span>
        <span>version {formatLabel(projectVersion)}</span>
        <span>checksums {formatLabel(checksumStatus)}</span>
        <span className="truncate">branch {formatLabel(gitBranch)}</span>
        <span className="truncate">commit {gitCommit ? gitCommit.slice(0, 8) : "n/a"}</span>
        <span className="col-span-2 truncate">host {formatLabel(platform)}</span>
      </div>

      {details.logs.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-slate-500">Log summaries</div>
          {details.logs.slice(0, 3).map((log) => (
            <div key={log.name} className="rounded border border-border/60 bg-bg-surface/40 px-2 py-1 space-y-0.5">
              <div className="flex items-center justify-between gap-2 font-mono text-slate-400">
                <span className="truncate">{log.name}</span>
                <span>{log.total_records ?? 0} rec, {formatPercent(log.accepted_rate)} accepted</span>
              </div>
              <div className="font-mono text-slate-500">status {formatCounts(log.status_counts)}</div>
              <div className="font-mono text-slate-500">reasons {formatCounts(log.reason_counts)}</div>
            </div>
          ))}
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
                  </div>
                </div>
              ))}
            </div>
          ))}
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
                </div>
              )}
              {detailLoadingPath === bundle.path && (
                <div className="text-slate-400">Reading bundle details...</div>
              )}
              {detailsByPath[bundle.path] && (
                <SupportBundleDetailPanel details={detailsByPath[bundle.path]} />
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
