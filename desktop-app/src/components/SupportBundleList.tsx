import { AlertTriangle, Archive, CheckCircle2 } from "lucide-react";
import type { SupportBundleFile } from "../lib/types";

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

export function SupportBundleList({ bundles, downloadDir }: { bundles: SupportBundleFile[]; downloadDir: string }) {
  if (bundles.length === 0) return null;

  return (
    <div className="rounded-lg border border-border bg-bg-base px-3 py-2 text-[11px] text-slate-400 space-y-2">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium text-slate-300 flex items-center gap-1.5">
          <Archive size={12} className="text-amber-300" /> Downloaded support bundles
        </span>
        <span className="font-mono truncate">{downloadDir}</span>
      </div>
      {bundles.slice(0, 3).map((bundle) => (
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
        </div>
      ))}
    </div>
  );
}
