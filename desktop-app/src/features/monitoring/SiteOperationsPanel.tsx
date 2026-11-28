import { useEffect, useState, type SyntheticEvent } from "react";
import { analyticsWorkerState, type Boundary, type Workspace } from "./monitoringModel";
import { canManageSite, siteRequest, type SiteSession } from "./SiteOperator";
import type { ApplianceHealth } from "./applianceHealth";
import type { SiteAssets } from "./useSiteAssets";

type Coverage = { boundaryId: string; state: "available" | "unavailable" | "unverified"; cameras: string[]; updatedAt: number; incidentId?: string };
type NotificationFailure = { sequence: number; attempts: number; reason: string; blocked: boolean; retryAt?: number; incidentId?: string; boundaryId?: string };
type AIException = { incidentId: string; reason?: string; label?: string; workflow: string };
type Operations = { coverage: Coverage[]; lastBackupAt?: number; lastTelemetryAt?: number; notificationBacklog?: number;
  failedNotificationCount?: number; failedNotifications?: NotificationFailure[];
  aiAttention?: { count: number; incidents: AIException[] };
  oldestNotificationAt?: string; recorderJobs?: Record<string, number>; indexCurrent?: boolean };
type Connector = { id: string; provider: string; playback: boolean; lastSuccessfulPoll?: number; lastAcceptedEvent?: number;
  rejected: number; backlog: number; problem?: string };
const COVERAGE = { available: "Verified camera video available", unavailable: "Verified views unavailable", unverified: "Coverage unverified" };
const clock = (value?: number) => value ? new Date(value * 1000).toLocaleString() : "No record";
const REVIEW_REASONS: Record<string, string> = {
  limit: "Automatic quota exhausted", footage: "Event footage unavailable", provider: "AI provider unavailable",
  expired: "Review window expired", restart: "Gateway restarted", interrupted: "Review interrupted",
  disabled: "Automatic review disabled", fallback: "Video fallback unavailable",
};
const MEASUREMENTS = [
  ["truePositives", "True positives", 0, 1000000, 1],
  ["falsePositives", "False positives", 0, 1000000, 1],
  ["missedEvents", "Missed events", 0, 1000000, 1],
  ["cameraHours", "Reviewed camera-hours", .01, 100000, .01],
  ["p95LatencySeconds", "Measured p95 alert latency (seconds)", 0, 3600, .01],
  ["minimumRecall", "Minimum recall (0 to 1)", .01, 1, .01],
  ["maximumFalseAlarmsPerHour", "Maximum false alarms per camera-hour", 0, 10000, .01],
  ["maximumP95Seconds", "Maximum p95 alert latency (seconds)", .01, 3600, .01],
] as const;

export function SiteOperationsPanel({ session, workspace, version, health, assets, onCamera, onBoundary, onIncident, onValidation }: {
  session: SiteSession; workspace: Workspace; version: number; onBoundary: (boundary: Boundary) => void;
  health: ReadonlyMap<string, ApplianceHealth>; assets?: SiteAssets; onCamera: (id: string) => void;
  onIncident: (id: string) => void; onValidation: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ operations: Operations; connectors: Connector[] }>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selection, setSelection] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const supervisor = canManageSite(session);
  const options = workspace.sensors.flatMap((camera) => (camera.analyticsRules ?? []).map((rule) => ({ camera, rule })))
    .filter(({ rule }) => workspace.boundaries.some(({ enabled, id }) => enabled && id === rule.boundaryId));
  const selected = options.find(({ camera, rule }) => selection === camera.id + ":" + rule.id);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    async function load() {
      try {
        const signal = AbortSignal.any([AbortSignal.timeout(7000), controller.signal]);
        const [operations, status] = await Promise.all([siteRequest(session, "operations", undefined, signal),
          supervisor && expanded ? siteRequest(session, "vms-status", undefined, signal) : { connectors: [] }]);
        if (!controller.signal.aborted) { setResult({ operations, connectors: status.connectors }); setError(""); }
      } catch (issue) {
        if (!controller.signal.aborted) { setResult(undefined); setError(issue instanceof Error ? issue.message : "Site diagnostics unavailable."); }
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(load, 10000);
      }
    }
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [session, expanded, revision, supervisor]);

  const coverageLoss = result?.operations.coverage.filter(({ state }) => state === "unavailable") ?? [];
  const stale = workspace.sensors.filter(({ id, modality, analyticsPlacement }) => {
    const worker = analyticsWorkerState(workspace.analyticsWorkers?.[id], analyticsPlacement, version, Date.now());
    return modality === "vision" && (["stale", "offline"].includes(health.get(id)?.status ?? "") ||
      ["stale", "no-video", "model-error", "delivery-delayed"].includes(worker));
  });
  const unverified = assets ? options.filter(({ camera, rule }) =>
    !assets.ruleAcceptances?.some((entry) => entry.cameraId === camera.id && entry.ruleId === rule.id && entry.passed)) : [];
  const failed = result?.operations.failedNotifications ?? [];
  const skipped = result?.operations.aiAttention;
  const selectedResult = assets?.ruleAcceptances?.find(({ cameraId, ruleId }) =>
    cameraId === selected?.camera.id && ruleId === selected?.rule.id);

  function refreshOnOpen(event: SyntheticEvent<HTMLDetailsElement>) {
    if (event.target === event.currentTarget && event.currentTarget.open) setRevision((value) => value + 1);
  }

  async function retry(sequence: number) {
    setBusy(true); setError(""); setNotice("");
    try {
      await siteRequest(session, "retry-notification", { sequence });
      setRevision((prior) => prior + 1);
      setNotice("Notification queued for retry.");
    } catch (issue) { setError(issue instanceof Error ? issue.message : "Could not queue a retry."); }
    finally { setBusy(false); }
  }

  return <>
  <section aria-label="Needs attention" className="gv-details">
    <h4>Needs attention</h4>
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
    <details className="gv-triage-section" onToggle={refreshOnOpen}><summary>Critical coverage loss <span>{coverageLoss.length}</span></summary>
      {coverageLoss.map(({ boundaryId, incidentId }) => {
        const boundary = workspace.boundaries.find(({ id }) => id === boundaryId);
        return boundary && <button key={boundaryId} className="gv-posture-row" type="button"
          onClick={() => incidentId ? onIncident(incidentId) : onBoundary(boundary)}>{boundary.name}</button>;
      })}
      {!coverageLoss.length && <p>{result ? "No critical coverage loss." : "Waiting for coverage status…"}</p>}
    </details>
    <details className="gv-triage-section"><summary>Stale or interrupted feeds <span>{stale.length}</span></summary>
      {stale.map(({ id, name }) => <button key={id} className="gv-posture-row" type="button" onClick={() => onCamera(id)}>{name}</button>)}
      {!stale.length && <p>No stale or interrupted feeds.</p>}
    </details>
    {supervisor && <details className="gv-triage-section" onToggle={refreshOnOpen}><summary>Delivery failures <span>{result?.operations.failedNotificationCount ?? "—"}</span></summary>
      {failed.map((item) => <div key={item.sequence} className="gv-triage-content">
        <p>{item.reason} · {item.attempts} {item.attempts === 1 ? "attempt" : "attempts"} · {item.blocked ? "Manual retry required" : "Retry scheduled " + clock(item.retryAt)}</p>
        <div className="gv-detail-actions">
          {item.incidentId && <button type="button" className="gv-button" onClick={() => onIncident(item.incidentId!)}>Open notification incident</button>}
          {item.blocked && <button type="button" className="gv-button" disabled={busy} onClick={() => void retry(item.sequence)}>Retry notification</button>}
        </div>
      </div>)}
      {!failed.length && <p>{result ? "No failed notifications." : "Waiting for delivery status…"}</p>}
    </details>}
    <details className="gv-triage-section"><summary>Unverified camera rules <span>{assets ? unverified.length : "—"}</span></summary>
      {unverified.map(({ camera, rule }) => <button key={camera.id + ":" + rule.id} className="gv-posture-row" type="button"
        onClick={() => onCamera(camera.id)}>{camera.name} · {rule.name}</button>)}
      {!unverified.length && <p>{assets ? "All active camera rules have passing acceptance." : "Waiting for rule checks…"}</p>}
    </details>
    {skipped && <details className="gv-triage-section" onToggle={refreshOnOpen}><summary>Skipped or interrupted AI reviews · 24h <span>{skipped.count}</span></summary>
      {skipped.incidents.map(({ incidentId, label, reason, workflow }) => <button key={incidentId} className="gv-posture-row" type="button"
        onClick={() => onIncident(incidentId)}><span><strong>{label ?? "Incident"}</strong>
        <small>{workflow === "automatic-triage" ? "Automatic triage" : "Automatic frame review"} · {REVIEW_REASONS[reason ?? ""] ?? "Review unavailable"}</small></span></button>)}
      {!skipped.count && <p>No AI reviews need attention.</p>}
    </details>}
  </section>
  <details className="gv-triage-section" onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary>Site operations and connectors</summary>
    <div className="gv-triage-content gv-incident-form">
      <button type="button" className="gv-button" onClick={() => setRevision((value) => value + 1)}>Refresh site operations</button>
      {result && <>
        {supervisor && <>
          <dl>
            <div><dt>Last device telemetry</dt><dd>{clock(result.operations.lastTelemetryAt)}</dd></div>
            <div><dt>Pending security notifications</dt><dd>{result.operations.notificationBacklog ?? 0}
              {result.operations.oldestNotificationAt ? " · Oldest " + new Date(result.operations.oldestNotificationAt).toLocaleString() : ""}</dd></div>
            <div><dt>Evidence indexing</dt><dd>{result.operations.indexCurrent ? "Current" : "Catching up"}</dd></div>
            <div><dt>Last verified backup snapshot</dt><dd>{clock(result.operations.lastBackupAt)}</dd></div>
            <div><dt>Recorder retrieval jobs</dt><dd>{Object.entries(result.operations.recorderJobs ?? {}).map(([key, value]) => key + " " + value).join(" · ") || "No jobs"}</dd></div>
          </dl>
          {result.connectors.map((connector) => <section className="gv-details" key={connector.id} aria-label={connector.provider + " connector"}>
            <h4>{connector.provider === "xprotect" ? "Milestone XProtect" : "Genetec Security Center"}</h4>
            <p>Vendor validation required · {connector.playback ? "Recorder playback configured" : "Event evidence"}</p>
            <small>Last successful poll · {clock(connector.lastSuccessfulPoll)}</small>
            <small>Last accepted event · {clock(connector.lastAcceptedEvent)} · {connector.rejected} rejected</small>
            {connector.backlog > 0 && <p role="status">Connector catching up on queued events.</p>}
            {connector.problem && <p role="status">{connector.problem}</p>}
          </section>)}
        </>}
        {result.operations.coverage.map((coverage) => {
          const boundary = workspace.boundaries.find(({ id }) => id === coverage.boundaryId);
          if (!boundary) return null;
          return <section className="gv-details" key={boundary.id}>
            <button type="button" className="gv-button" onClick={() => onBoundary(boundary)}>{boundary.name}</button>
            <p>{COVERAGE[coverage.state]} · {coverage.cameras.length} field-checked views</p>
            {coverage.incidentId && <button type="button" className="gv-button"
              onClick={() => onIncident(coverage.incidentId!)}>Open critical coverage incident</button>}
          </section>;
        })}
      </>}
      {supervisor && !!version && !!options.length && <form className="gv-incident-form" onSubmit={async (event) => {
        event.preventDefault(); if (!selected) return;
        const form = new FormData(event.currentTarget);
        const accepting = selected.rule.mode === "observe";
        const measurements = accepting ? Object.fromEntries(MEASUREMENTS.map(([key]) => [key, Number(form.get(key))])) : undefined;
        setBusy(true); setError(""); setNotice("");
        try {
          const saved = await siteRequest(session, "assets", { operation: accepting ? "accept-rule" : "verify-rule",
            version, cameraId: selected.camera.id, ruleId: selected.rule.id, note, measurements }) as SiteAssets;
          const result = saved.ruleAcceptances?.find(({ cameraId, ruleId }) => cameraId === selected.camera.id && ruleId === selected.rule.id);
          setNote("");
          setNotice(accepting ? result?.passed ? "Acceptance passed. This rule can now be promoted to Alert." :
            "Measurements saved. This rule stays in Observe because it did not meet the customer thresholds." : "Rule validation recorded.");
          onValidation();
        } catch (issue) { setError(issue instanceof Error ? issue.message : "Could not record rule validation."); }
        finally { setBusy(false); }
      }}>
        <h4>Camera rule acceptance</h4>
        <label>Rule to validate<select value={selection} onChange={(event) => setSelection(event.target.value)}>
          <option value="">Choose a camera rule</option>
          {options.map(({ camera, rule }) => <option key={camera.id + ":" + rule.id} value={camera.id + ":" + rule.id}>{camera.name} · {rule.name}</option>)}
        </select></label>
        {selectedResult && <p>Latest acceptance · {selectedResult.passed ? "Passed" : "Thresholds missed"}</p>}
        {selected?.rule.mode === "observe" && <fieldset key={selection}>
          <legend>Field measurements and customer thresholds</legend>
          {MEASUREMENTS.map(([key, label, min, max, step]) => <label key={key}>{label}
            <input type="number" name={key} required min={min} max={max} step={step} /></label>)}
        </fieldset>}
        <label>Validation evidence<textarea required value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} /></label>
        <button type="submit" className="gv-button" disabled={busy || !selected || !note.trim()}>
          {selected?.rule.mode === "observe" ? "Record rule acceptance" : "Record rule validation"}</button>
      </form>}
    </div>
  </details></>;
}
