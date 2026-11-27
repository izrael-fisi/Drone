import { useEffect, useState } from "react";
import type { Alert } from "./monitoringModel";
import { connectSite, siteRequest, SiteRequestError, type SiteSession } from "./SiteOperator";
import { readSiteEndpoint } from "./SiteTelemetry";

type DispatchAlert = Alert & { nextAction?: string; recordingState?: string };
type DispatchSite = { session: SiteSession; connected: boolean; seen?: number; alerts: DispatchAlert[]; version?: number };

export function useOrganizationDispatch(sites: SiteSession[]) {
  const [status, setStatus] = useState<DispatchSite[]>([]);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (sites.length <= 1) { setStatus([]); return; }
    const lifetime = new AbortController();
    let timer: number;
    async function poll() {
      const next = await Promise.all(sites.map(async (session): Promise<DispatchSite | undefined> => {
        try {
          const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(4500)]);
          const snapshot = await siteRequest(session, "dispatch", undefined, signal);
          if (snapshot.account?.organization?.id !== session.operator.organization.id || snapshot.account?.siteId !== session.operator.siteId) return;
          const alerts = ((snapshot.alerts ?? []) as DispatchAlert[]).filter((alert) => !alert.correlation && alert.status !== "resolved");
          return { session, alerts, connected: true, seen: Date.now(), version: snapshot.publishedVersion };
        } catch (error) {
          return error instanceof SiteRequestError && ([401, 403].includes(error.status) || session.enterprise && error.status === 404)
            ? undefined : { session, alerts: [], connected: false };
        }
      }));
      if (lifetime.signal.aborted) return;
      setStatus((previous) => next.flatMap((entry) => {
        if (!entry) return [];
        const cached = previous.find((value) => value.session === entry.session);
        return [!entry.connected && cached ? { ...cached, connected: false } : entry];
      }));
      timer = window.setTimeout(poll, 10_000);
    }
    void poll();
    return () => { lifetime.abort(); window.clearTimeout(timer); };
  }, [sites, revision]);
  return { status, refresh: () => setRevision((value) => value + 1) };
}

function siteTime(at: number, timeZone: string) {
  try { return new Date(at).toLocaleString(undefined, { timeZone, dateStyle: "short", timeStyle: "short" }); }
  catch { return new Date(at).toISOString(); }
}

export function OrganizationDispatch({ active, sites, status, onAdd, onVisit, onRefresh, allowConnect }: {
  active: SiteSession; sites: SiteSession[]; status: DispatchSite[]; onAdd: (session: SiteSession) => void;
  onVisit: (session: SiteSession, incidentId?: string) => void; allowConnect?: boolean;
  onRefresh: () => void;
}) {
  const [gateway, setGateway] = useState("");
  const [credential, setCredential] = useState("");
  const [selected, setSelected] = useState("");
  const [queue, setQueue] = useState("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const base = readSiteEndpoint(gateway);
  const visible = status.filter(({ session }) => sites.includes(session) && (!selected || session.operator.siteId === selected));
  const alerts = visible.flatMap((site) => site.alerts.filter((alert) => queue === "all" || queue === alert.status).map((alert) => ({ site, alert })))
    .sort((a, b) => Number(b.alert.severity === "High") - Number(a.alert.severity === "High")
      || Date.parse(b.alert.observation.occurredAt) - Date.parse(a.alert.observation.occurredAt)).slice(0, 30);
  if (!allowConnect && sites.length < 2) return null;
  return <details className="gv-triage-section">
    <summary>Organization dispatch</summary>
    <div className="gv-triage-content gv-incident-form">
      {allowConnect && <form className="gv-incident-form" onSubmit={async (event) => {
        event.preventDefault(); if (!base || sites.length >= 12) return;
        setBusy(true); setError("");
        try {
          const site = await connectSite(base, credential);
          if (site.operator.organization.id !== active.operator.organization.id) throw new Error("This gateway belongs to a different organization.");
          if (sites.some((existing) => existing.operator.siteId === site.operator.siteId && existing.base !== site.base)) {
            throw new Error("That site ID is already connected through a different gateway.");
          }
          onAdd(site); setGateway(""); setCredential("");
        } catch (issue) { setError(issue instanceof Error ? issue.message : "Could not connect the site."); }
        finally { setBusy(false); }
      }}>
        <label>Additional site gateway URL<input type="url" required value={gateway} onChange={(event) => setGateway(event.target.value)} /></label>
        <label>Additional site operator token<input type="password" autoComplete="off" value={credential} onChange={(event) => setCredential(event.target.value)} /></label>
        <button type="submit" className="gv-button" disabled={!base || busy || sites.length >= 12}>Connect site to dispatch</button>
        {error && <p role="alert">{error}</p>}
      </form>}
      {sites.length > 1 && <>
        <button type="button" className="gv-button" onClick={onRefresh}>Refresh dispatch sites</button>
        <label>Dispatch queue<select value={queue} onChange={(event) => setQueue(event.target.value)}>
          <option value="all">All open incidents</option><option value="new">Unassigned</option><option value="acknowledged">Investigating</option>
        </select></label>
        <label>Dispatch site<select value={selected} onChange={(event) => setSelected(event.target.value)}>
          <option value="">All connected sites</option>{sites.map((session) => <option key={session.operator.siteId} value={session.operator.siteId}>{session.operator.site}</option>)}
        </select></label>
        {visible.map((site) => <button type="button" key={site.session.operator.siteId} className="gv-posture-row"
          disabled={!site.connected} aria-current={site.session.operator.siteId === active.operator.siteId || undefined}
          onClick={() => onVisit(site.session)}><span><strong>{site.session.operator.site}</strong><small>{site.connected ? "Gateway responding" : "Gateway unreachable"}
            {site.seen ? ` · ${siteTime(site.seen, site.session.operator.timeZone)} local` : ""}{site.version ? ` · Version ${site.version}` : ""}</small></span></button>)}
        {alerts.map(({ site, alert }) => <button type="button" key={`${site.session.operator.siteId}:${alert.id}`} className="gv-posture-row" disabled={!site.connected}
          onClick={() => onVisit(site.session, alert.id)}><span><strong>{alert.observation.summary}</strong>
            <small>{site.session.operator.site} · {alert.zoneName} · {siteTime(Date.parse(alert.observation.occurredAt), site.session.operator.timeZone)} local</small>
            <small>{alert.severity} priority · {alert.responseTeam ?? "No response team"} · {alert.owner ?? "Unassigned"} · {Math.max(0, Math.floor(((site.seen ?? Date.now()) - Date.parse(alert.observation.occurredAt)) / 60000))} min old</small>
            <small>{site.connected ? "" : "Stale · "}{alert.recordingState === "available" ? "Recording available"
              : alert.recordingState === "gap" ? "Recording gap" : "Recording " + (alert.recordingState ?? "unverified")}</small>
            {alert.nextAction && <small>Next action · {alert.nextAction}</small>}</span></button>)}
        {!alerts.length && <p>No open incidents from the selected sites.</p>}
      </>}
    </div>
  </details>;
}
