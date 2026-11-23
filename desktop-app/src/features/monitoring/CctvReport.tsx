import { useEffect, useState } from "react";
import type { AnalyticsProfile, AnalyticsRule } from "./monitoringModel";
import { siteRequest, type SiteSession } from "./SiteOperator";

const PROFILES: Record<AnalyticsProfile, string> = { "all-day": "All day", day: "Day", night: "Night" };
const DECISIONS = { pending: "Pending", confirmed: "Confirmed", "false-detection": "False detections", unverifiable: "Unable to verify" } as const;

type Report = { version: number; versions: number[]; rules: {
  cameraId: string; cameraName: string; ruleId: string; ruleName: string; mode: AnalyticsRule["mode"];
  profiles: { profile: AnalyticsProfile; counts: Record<keyof typeof DECISIONS, number>; latencySamples: number; meanGatewaySeconds: number | null }[];
}[] };

export function CctvReport({ session, publishedVersion }: { session: SiteSession; publishedVersion: number }) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState("live");
  const [historicalVersion, setHistoricalVersion] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [snapshot, setSnapshot] = useState<{ query: string; report?: Report; error?: string }>();
  const requested = historicalVersion || publishedVersion;
  const query = new URLSearchParams({ source, version: String(requested) }).toString();
  const current = snapshot?.query === query ? snapshot : undefined;
  const report = current?.report;

  useEffect(() => {
    if (!open) return;
    const lifetime = new AbortController();
    async function load() {
      try {
        const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(6000)]);
        const report: Report = await siteRequest(session, "analytics-report?" + query, undefined, signal);
        if (!lifetime.signal.aborted) setSnapshot({ query, report });
      } catch (cause) {
        if (!lifetime.signal.aborted) setSnapshot({ query, error: cause instanceof Error ? cause.message : "Could not load the Observe report." });
      }
    }
    void load();
    return () => lifetime.abort();
  }, [session, open, query, refresh]);

  return <details className="gv-triage-section" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Observe rule report</summary>
    {open && <section className="gv-triage-content gv-cctv-review" aria-label="Observe rule report">
      <p>Counts cover collected Observe detections. Measure missed events with independently sampled footage.</p>
      <div className="gv-queue-filters gv-incident-form">
        <label>Report source<select value={source} onChange={(event) => setSource(event.currentTarget.value)}>
          <option value="live">Live CCTV</option><option value="replay">Replay</option>
        </select></label>
        <label>Report version<select value={historicalVersion} onChange={(event) => setHistoricalVersion(Number(event.currentTarget.value))}>
          <option value={0}>Current published version</option>
          {Array.from(new Set([...(snapshot?.report?.versions ?? []), ...(historicalVersion ? [historicalVersion] : [])]))
            .filter((version) => version !== publishedVersion || version === historicalVersion)
            .map((version) => <option key={version} value={version}>Version {version}</option>)}
        </select></label>
        <button type="button" className="gv-button" onClick={() => setRefresh((value) => value + 1)}>Refresh report</button>
      </div>
      {current?.error && <p role="alert">{current.error}</p>}
      {!current && <p role="status">Loading rule report…</p>}
      {report && <p role="status">{report.version ? `Published version ${report.version}` : "Publish a site version to start a report."}</p>}
      {report?.version !== 0 && report?.rules.length === 0 && <p>No published CCTV rules for the cameras you can access.</p>}
      {report?.rules.map((rule) => <section key={rule.cameraId + ":" + rule.ruleId} className="gv-details" aria-label={`${rule.cameraName} · ${rule.ruleName}`}>
        <h5>{rule.ruleName}</h5><p>{rule.cameraName} · {rule.mode === "observe" ? "Observe mode" : "Alert mode"}</p>
        {rule.profiles.map(({ profile, counts, latencySamples, meanGatewaySeconds }) => <section key={profile} className="gv-cctv-report-profile" aria-label={PROFILES[profile]}>
          <h6>{PROFILES[profile]}</h6>
          <dl>
            {Object.entries(DECISIONS).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{counts[key as keyof typeof DECISIONS]}</dd></div>)}
          </dl>
          {latencySamples > 0 && meanGatewaySeconds !== null && <small>Mean gateway delivery {meanGatewaySeconds.toFixed(2)} seconds · {latencySamples} samples</small>}
        </section>)}
      </section>)}
    </section>}
  </details>;
}
