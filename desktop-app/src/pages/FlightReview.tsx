import { useEffect, useMemo, useState } from "react";
import { Archive, Clock, FileDown, Map, Navigation, RefreshCw, ShieldCheck } from "lucide-react";
import { DefenseHeader, DefenseMetric, DefensePane, DefenseRightPanel, DefenseSection, DefenseListItem } from "../components/DefensePane";
import { SupportBundleList } from "../components/SupportBundleList";
import { cmd } from "../lib/tauri";
import type { SupportBundleFile } from "../lib/types";
import { formatDate } from "../lib/utils";

const SUPPORT_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/support-bundles";

function formatSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function healthTone(status?: string) {
  if (status === "passed" || status === "healthy") return "ready";
  if (status === "failed") return "critical";
  if (status === "degraded") return "warning";
  return "offline";
}

function formatMeters(value?: number) {
  if (value == null) return "n/a";
  if (value >= 1000) return `${(value / 1000).toFixed(2)} km`;
  return `${value.toFixed(1)} m`;
}

function formatDuration(seconds?: number) {
  if (seconds == null) return "n/a";
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(1)} hr`;
  if (seconds >= 60) return `${(seconds / 60).toFixed(1)} min`;
  return `${seconds.toFixed(0)} s`;
}

export function FlightReview() {
  const [bundles, setBundles] = useState<SupportBundleFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refreshBundles = async () => {
    setLoading(true);
    setMessage(null);
    try {
      setBundles(await cmd.listSupportBundles(SUPPORT_DOWNLOAD_DIR));
    } catch (error) {
      setMessage(`Could not list support bundles: ${error}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshBundles();
  }, []);

  const totals = useMemo(() => {
    const bytes = bundles.reduce((sum, bundle) => sum + bundle.size_bytes, 0);
    const passed = bundles.filter((bundle) => bundle.summary?.bundle_health_status === "passed").length;
    const failed = bundles.filter((bundle) => bundle.summary?.bundle_health_status === "failed").length;
    const degraded = bundles.filter((bundle) => bundle.summary?.bundle_health_status === "degraded").length;
    return { bytes, degraded, failed, passed };
  }, [bundles]);

  const latest = bundles[0];

  return (
    <DefensePane
      right={
        <DefenseRightPanel title="EVIDENCE INDEX">
          <div className="flex justify-center border-b border-white/10 pb-4">
            <div className="holo-core" />
          </div>
          <DefenseListItem label="Support bundle path" detail={SUPPORT_DOWNLOAD_DIR} tone="active" />
          <DefenseListItem label="Latest bundle" detail={latest?.name ?? "none downloaded"} tone={latest ? healthTone(latest.summary?.bundle_health_status) : "warning"} />
          <DefenseListItem label="Replay gate" detail={latest?.summary?.replay_gate_status ?? "not available"} tone={healthTone(latest?.summary?.replay_gate_status)} />
          <DefenseListItem label="GNSS-denied plan" detail={latest?.summary?.gnss_denied_plan_status ?? latest?.summary?.gnss_denied_plan_check_status ?? "not available"} tone={healthTone(latest?.summary?.gnss_denied_plan_status ?? latest?.summary?.gnss_denied_plan_check_status)} />
          <DefenseListItem label="Vision fixes" detail={`${latest?.summary?.accepted_vision_fix_count ?? 0} accepted / ${latest?.summary?.rejected_vision_fix_count ?? 0} rejected`} tone={(latest?.summary?.accepted_vision_fix_count ?? 0) > 0 ? "ready" : "warning"} />
          <DefenseListItem label="GPS vs vision" detail={formatMeters(latest?.summary?.gps_vs_vision_median_distance_m)} tone={latest?.summary?.gps_vs_vision_median_distance_m != null ? "active" : "offline"} />
        </DefenseRightPanel>
      }
    >
      <DefenseHeader
        eyebrow="FLIGHT REVIEW"
        title="EVIDENCE, HISTORY, SUPPORT BUNDLES"
        subtitle="bench reports // field logs // replay gates // runtime evidence"
        action={
          <button className="border border-status-active bg-status-active/10 px-4 py-2 font-label-caps text-label-caps text-status-active transition-colors hover:bg-status-active hover:text-[#05070A]" onClick={refreshBundles} disabled={loading}>
            <RefreshCw size={14} className={loading ? "inline mr-2 animate-spin" : "inline mr-2"} /> REFRESH
          </button>
        }
      />

      <div className="grid grid-cols-4 gap-3">
        <DefenseMetric label="BUNDLES" value={bundles.length} detail="downloaded" tone={bundles.length ? "active" : "warning"} />
        <DefenseMetric label="PASSED" value={totals.passed} detail="health gates" tone="ready" />
        <DefenseMetric label="DEGRADED" value={totals.degraded} detail="needs review" tone={totals.degraded ? "warning" : "offline"} />
        <DefenseMetric label="STORAGE" value={formatSize(totals.bytes)} detail="support bundle cache" tone="active" />
      </div>

      {message && (
        <div className="border border-status-warning/25 bg-yellow-500/5 px-3 py-2 font-data-mono text-xs text-status-warning">
          {message}
        </div>
      )}

      <DefenseSection title="TRACK REPLAY" icon={<Map size={14} />}>
        <TrackReplayPreview bundle={latest} />
      </DefenseSection>

      {bundles.length === 0 ? (
        <DefenseSection title="NO EVIDENCE DOWNLOADED" icon={<Archive size={14} />}>
          <div className="py-12 text-center">
            <Archive size={34} className="mx-auto mb-3 text-white/25" />
            <div className="font-data-mono text-sm text-white">No support bundles downloaded yet</div>
            <p className="mt-1 font-data-mono text-xs text-white/40">
              Create or download support bundles from Mission Planner or Vehicle Manager after bench and field runs.
            </p>
            <div className="mt-4 flex justify-center gap-2 font-data-mono text-xs text-white/40">
              <FileDown size={12} /> {SUPPORT_DOWNLOAD_DIR}
            </div>
          </div>
        </DefenseSection>
      ) : (
        <>
          <DefenseSection title="LATEST BUNDLE" icon={<Clock size={14} />}>
            <div className="grid grid-cols-3 gap-3">
              <DefenseMetric label="MODIFIED" value={formatDate(latest?.modified_unix_ms ? new Date(latest.modified_unix_ms).toISOString() : undefined)} detail={latest?.name ?? "n/a"} tone="active" />
              <DefenseMetric label="HEALTH" value={latest?.summary?.bundle_health_status?.toUpperCase() ?? "UNKNOWN"} detail="bundle summary" tone={healthTone(latest?.summary?.bundle_health_status)} />
              <DefenseMetric label="SIZE" value={latest ? formatSize(latest.size_bytes) : "n/a"} detail="archive" tone="active" />
            </div>
          </DefenseSection>
          <DefenseSection title="FLIGHT EVIDENCE METRICS" icon={<ShieldCheck size={14} />}>
            <div className="grid grid-cols-4 gap-3">
              <DefenseMetric label="DISTANCE" value={formatMeters(latest?.summary?.flight_evidence_total_distance_m)} detail="total track" tone="active" />
              <DefenseMetric label="DURATION" value={formatDuration(latest?.summary?.flight_evidence_duration_s)} detail="log span" tone="active" />
              <DefenseMetric label="VISION FIXES" value={latest?.summary?.accepted_vision_fix_count ?? 0} detail={`${latest?.summary?.rejected_vision_fix_count ?? 0} rejected`} tone={(latest?.summary?.accepted_vision_fix_count ?? 0) > 0 ? "ready" : "warning"} />
              <DefenseMetric label="DR TIME" value={formatDuration(latest?.summary?.dead_reckoning_duration_s)} detail={`${latest?.summary?.source_transition_count ?? 0} transitions`} tone={(latest?.summary?.dead_reckoning_duration_s ?? 0) > 0 ? "warning" : "active"} />
            </div>
          </DefenseSection>
          <DefenseSection title="SUPPORT BUNDLE MANAGER" icon={<ShieldCheck size={14} />}>
            <SupportBundleList bundles={bundles} downloadDir={SUPPORT_DOWNLOAD_DIR} onChanged={refreshBundles} />
          </DefenseSection>
        </>
      )}
    </DefensePane>
  );
}

function TrackReplayPreview({ bundle }: { bundle?: SupportBundleFile }) {
  const summary = bundle?.summary;
  const accepted = summary?.accepted_vision_fix_count ?? 0;
  const rejected = summary?.rejected_vision_fix_count ?? 0;
  const gpsDelta = summary?.gps_vs_vision_median_distance_m;
  const hasEvidence = accepted > 0 || gpsDelta != null || (summary?.flight_evidence_total_distance_m ?? 0) > 0;

  return (
    <div className="grid gap-3 lg:grid-cols-[1.2fr_0.8fr]">
      <div className="relative h-64 overflow-hidden border border-border bg-[#03070B]">
        <div
          className="absolute inset-0 opacity-25"
          style={{
            backgroundImage:
              "linear-gradient(rgba(104,199,230,0.12) 1px, transparent 1px), linear-gradient(90deg, rgba(104,199,230,0.12) 1px, transparent 1px)",
            backgroundSize: "28px 28px",
          }}
        />
        <svg className="absolute inset-0 h-full w-full" viewBox="0 0 640 260" role="img" aria-label="GPS and vision track preview">
          <polyline
            points="46,210 120,178 194,184 262,138 340,124 426,86 586,62"
            fill="none"
            stroke={hasEvidence ? "#7CCB8A" : "#314458"}
            strokeWidth="4"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={hasEvidence ? "0" : "10 10"}
          />
          <polyline
            points="46,216 122,184 198,178 266,146 338,132 430,94 586,70"
            fill="none"
            stroke={hasEvidence ? "#68C7E6" : "#596675"}
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={hasEvidence ? "8 8" : "4 10"}
          />
          {[46, 194, 340, 586].map((x, index) => (
            <circle key={x} cx={x} cy={[216, 178, 132, 70][index]} r="5" fill={hasEvidence ? "#68C7E6" : "#596675"} />
          ))}
        </svg>
        <div className="absolute left-3 top-3 border border-border bg-bg-card/90 px-3 py-2 font-data-mono text-xs">
          <div className="flex items-center gap-2 text-status-ready">
            <span className="h-2 w-4 bg-status-ready" /> GPS track
          </div>
          <div className="mt-1 flex items-center gap-2 text-status-active">
            <span className="h-2 w-4 border-t-2 border-dashed border-status-active" /> Vision estimate
          </div>
        </div>
        {!hasEvidence && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="border border-border bg-bg-card/90 px-4 py-3 text-center">
              <Navigation size={18} className="mx-auto mb-2 text-status-active" />
              <div className="font-data-mono text-sm text-slate-200">No replay track yet</div>
              <div className="mt-1 font-data-mono text-xs text-slate-500">Download a support bundle after bench or flight capture.</div>
            </div>
          </div>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
        <DefenseMetric label="GPS VS VISION" value={formatMeters(gpsDelta)} detail="median delta" tone={gpsDelta != null ? "active" : "offline"} />
        <DefenseMetric label="FIXES" value={accepted} detail={`${rejected} rejected`} tone={accepted ? "ready" : "warning"} />
        <DefenseMetric label="TRANSITIONS" value={summary?.source_transition_count ?? 0} detail="source changes" tone={(summary?.source_transition_count ?? 0) > 0 ? "warning" : "offline"} />
      </div>
    </div>
  );
}
