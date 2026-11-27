import { useEffect, useState } from "react";
import type { Boundary, Workspace } from "./monitoringModel";
import { canManageSite, siteRequest, type SiteSession } from "./SiteOperator";

type Coverage = { boundaryId: string; state: "available" | "unavailable" | "unverified"; cameras: string[]; updatedAt: number; incidentId?: string };
type Operations = { coverage: Coverage[]; lastBackupAt?: number; lastTelemetryAt?: number; notificationBacklog?: number;
  oldestNotificationAt?: string; recorderJobs?: Record<string, number>; indexCurrent?: boolean };
type Connector = { id: string; provider: string; playback: boolean; lastSuccessfulPoll?: number; lastAcceptedEvent?: number;
  rejected: number; backlog: number; problem?: string };
const COVERAGE = { available: "Verified camera video available", unavailable: "Verified views unavailable", unverified: "Coverage unverified" };
const clock = (value?: number) => value ? new Date(value * 1000).toLocaleString() : "No record";

export function SiteOperationsPanel({ session, workspace, version, onBoundary, onIncident, onValidation }: {
  session: SiteSession; workspace: Workspace; version: number; onBoundary: (boundary: Boundary) => void;
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
    if (!expanded) return;
    const controller = new AbortController();
    let timer: number | undefined;
    async function load() {
      try {
        const signal = AbortSignal.any([AbortSignal.timeout(7000), controller.signal]);
        const [operations, status] = await Promise.all([siteRequest(session, "operations", undefined, signal),
          supervisor ? siteRequest(session, "vms-status", undefined, signal) : { connectors: [] }]);
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

  return <details className="gv-triage-section" onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary>Site operations and connectors</summary>
    <div className="gv-triage-content gv-incident-form">
      <button type="button" className="gv-button" onClick={() => setRevision((value) => value + 1)}>Refresh site operations</button>
      {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
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
        setBusy(true); setError(""); setNotice("");
        try {
          await siteRequest(session, "assets", { operation: "verify-rule", version, cameraId: selected.camera.id, ruleId: selected.rule.id, note });
          setNote(""); setNotice("Rule validation recorded for the current camera and rule."); onValidation();
        } catch (issue) { setError(issue instanceof Error ? issue.message : "Could not record rule validation."); }
        finally { setBusy(false); }
      }}>
        <h4>Record rule validation</h4>
        <label>Rule to validate<select value={selection} onChange={(event) => setSelection(event.target.value)}>
          <option value="">Choose a camera rule</option>
          {options.map(({ camera, rule }) => <option key={camera.id + ":" + rule.id} value={camera.id + ":" + rule.id}>{camera.name} · {rule.name}</option>)}
        </select></label>
        <label>Validation evidence<textarea required value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} /></label>
        <button type="submit" className="gv-button" disabled={busy || !selected || !note.trim()}>Record rule validation</button>
      </form>}
    </div>
  </details>;
}
