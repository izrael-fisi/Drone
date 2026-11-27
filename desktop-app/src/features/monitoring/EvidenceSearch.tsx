import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { RecordedFeed } from "./CameraFeed";
import type { Recording } from "./monitoringModel";
import { canManageSite, siteRequest, type SiteSession } from "./SiteOperator";

type Evidence = { id: string; source: string; label: string; occurredAt: string; incidentId?: string; cameraId?: string;
  device?: string; boundaryId?: string; configurationVersion?: number };
type Clip = Omit<Recording, "url" | "format"> & { path: string };
type Attachment = Evidence & { attachmentId: string; startAt: string; endAt: string; state: string; recording?: Clip; heldUntil?: number };
type CaseHeader = { id: string; revision: number; title: string; finding: string; incidentId?: string; closed: boolean };
type Case = CaseHeader & { attachments: Attachment[]; activity: { at: string; actor: string; operation: string; detail?: string }[] };
type Page = { session: SiteSession; results: Evidence[]; cursor?: string | null; loaded: boolean; failed?: boolean;
  searchableFrom?: string; indexCurrent?: boolean };
const MEDIA: Record<string, string> = { awaiting: "Recording not retrieved", pending: "Retrieving", missing: "Recording gap",
  error: "Recorder unavailable", unsupported: "Recorder unavailable", expired: "Expired", available: "Available" };
const LABELS = { q: "Evidence keywords", startAt: "From (UTC)", endAt: "To (UTC)", cameraId: "Camera ID",
  floorId: "Floor ID", boundaryId: "Boundary ID", eventType: "Event type", object: "Object class" };
const BLANK = { q: "", startAt: "", endAt: "", cameraId: "", floorId: "", boundaryId: "", eventType: "", object: "" };
const PAGE_SIZE = 25;
const waitForRecorder = () => new Promise<void>((resolve) => window.setTimeout(resolve, 2000));

function describe(issue: unknown) {
  return issue instanceof Error ? issue.message : "The site could not complete this request.";
}

function ClipPlayer({ session, clip, label }: { session: SiteSession; clip: Clip; label: string }) {
  const [src, setSrc] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const path = new URL(clip.path, session.base);
    let blobUrl: string | undefined;
    if (session.enterprise) setSrc(path.href);
    else {
      fetch(path, { headers: { Authorization: "Bearer " + session.token }, cache: "no-store", redirect: "error",
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(60000)]) })
        .then((response) => { if (!response.ok) throw new Error("This recording is unavailable."); return response.blob(); })
        .then((blob) => { if (!controller.signal.aborted) { blobUrl = URL.createObjectURL(blob); setSrc(blobUrl); } })
        .catch((issue) => { if (!controller.signal.aborted) setError(describe(issue)); });
    }
    return () => { controller.abort(); if (blobUrl) URL.revokeObjectURL(blobUrl); };
  }, [session, clip.path]);
  return error ? <p role="alert">{error}</p> : src ? <RecordedFeed recording={{ ...clip, format: "mp4", url: src }} name={label} />
    : <p role="status">Opening recording…</p>;
}

export function EvidenceSearch({ active, sites, incident, onClose, onIncident }: {
  active: SiteSession; sites: SiteSession[]; incident?: { id: string; title: string };
  onClose: () => void; onIncident: (site: SiteSession, id: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const lifetime = useRef<AbortController | null>(null);
  const first = useRef<HTMLInputElement>(null);
  const pages = useRef<Page[]>([]);
  const query = useRef("");
  const [scope, setScope] = useState(active.operator.siteId);
  const [filters, setFilters] = useState(BLANK);
  const [rows, setRows] = useState<{ site: SiteSession; evidence: Evidence }[]>([]);
  const [state, setState] = useState<{ partial: string[]; index: string[]; more: boolean; earliest?: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [caseSite, setCaseSite] = useState(active);
  const [cases, setCases] = useState<CaseHeader[]>();
  const [selected, setSelected] = useState<Case>();
  const [title, setTitle] = useState(incident?.title ?? "");
  const [finding, setFinding] = useState("");
  const [clip, setClip] = useState<{ session: SiteSession; recording: Clip; label: string }>();
  const [exportSelection, setExportSelection] = useState<string[]>([]);
  const available = Array.from(new Map([...sites, active].filter((item) => item.operator.organization.id === active.operator.organization.id)
    .map((item) => [item.operator.siteId, item] as const)).values());

  useEffect(() => {
    const node = dialog.current;
    const previous = document.activeElement;
    lifetime.current = new AbortController();
    node?.showModal();
    first.current?.focus();
    return () => { lifetime.current?.abort(); node?.close(); if (previous instanceof HTMLElement) previous.focus(); };
  }, []);

  function updateCase(session: SiteSession, value: Case) {
    setCaseSite(session); setSelected(value); setTitle(value.title); setFinding(value.finding);
    setExportSelection(value.attachments.filter((item) => (item.heldUntil ?? 0) > Date.now() / 1000).slice(0, 4).map((item) => item.attachmentId));
  }

  async function act(fn: (signal: AbortSignal) => Promise<void>, timeout = 15000) {
    if (!lifetime.current || busy) return;
    const signal = AbortSignal.any([lifetime.current.signal, AbortSignal.timeout(timeout)]);
    setBusy(true); setError(""); setNotice("");
    try { await fn(signal); }
    catch (issue) { if (!lifetime.current.signal.aborted) setError(describe(issue)); }
    finally { setBusy(false); }
  }

  async function showPage(reset: boolean) {
    await act(async (signal) => {
      if (reset) {
        setRows([]); setState(undefined);
        const parameters = new URLSearchParams({ limit: String(PAGE_SIZE) });
        for (const [key, value] of Object.entries(filters)) if (value.trim()) {
          parameters.set(key, key === "startAt" || key === "endAt" ? new Date(value + "Z").toISOString() : value.trim());
        }
        query.current = parameters.toString();
        pages.current = available.filter((site) => !scope || site.operator.siteId === scope)
          .map((session) => ({ session, loaded: false, results: [] }));
      }
      const batch: typeof rows = [];
      while (batch.length < PAGE_SIZE && !signal.aborted) {
        await Promise.all(pages.current.filter((page) => !page.failed && !page.results.length && (!page.loaded || page.cursor)).map(async (page) => {
          try {
            const request = new URLSearchParams(query.current);
            if (page.cursor) request.set("cursor", page.cursor);
            const result = await siteRequest(page.session, "evidence-search?" + request, undefined,
              AbortSignal.any([signal, AbortSignal.timeout(3500)]));
            Object.assign(page, result, { loaded: true });
          } catch {
            page.failed = true;
          }
        }));
        const next = pages.current.filter((page) => page.results.length).sort((a, b) =>
          b.results[0].occurredAt.localeCompare(a.results[0].occurredAt) || a.session.operator.siteId.localeCompare(b.session.operator.siteId))[0];
        if (!next) break;
        batch.push({ site: next.session, evidence: next.results.shift()! });
      }
      if (signal.aborted) return;
      const partial = pages.current.filter((page) => page.failed).map(({ session }) => session.operator.site);
      setRows((previous) => [...(reset ? [] : previous.filter(({ site }) => !pages.current.some((page) => page.failed && page.session === site))), ...batch]);
      setState({ partial, more: pages.current.some((page) => !page.failed && !!(page.results.length || page.cursor)),
        index: pages.current.filter((page) => page.indexCurrent === false).map(({ session }) => session.operator.site),
        earliest: pages.current.flatMap((page) => page.searchableFrom ? [page.searchableFrom] : []).sort()[0] });
    });
  }

  function caseAction(operation: string, extra: Record<string, unknown> = {}, session = caseSite) {
    return act(async (signal) => {
      let revision = selected?.revision;
      for (let attempt = 0; attempt < 5; attempt++) {
        signal.throwIfAborted();
        const result = await siteRequest(session, "cases", { operation, caseId: selected?.id, revision, ...extra }, signal);
        updateCase(session, result.case);
        if (operation !== "retrieve" || !result.case.attachments.some((item: Attachment) => item.attachmentId === extra.attachmentId && item.state === "pending")) break;
        revision = result.case.revision;
        if (attempt < 4) await waitForRecorder();
      }
      if (operation === "request-export") setNotice("Requested supervisor export.");
    }, operation === "retrieve" ? 95000 : 15000);
  }

  function download() {
    void act(async (signal) => {
      const response = await fetch(new URL("api/site/case-export", caseSite.base), { method: "POST", credentials: "same-origin",
        redirect: "error", signal, headers: { "Content-Type": "application/json", ...(caseSite.token && { Authorization: "Bearer " + caseSite.token }) },
        body: JSON.stringify({ caseId: selected?.id, revision: selected?.revision, attachmentIds: exportSelection }),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.error ?? "Could not export the case.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url; link.download = "proxigo-case-evidence.zip"; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("Signed case evidence downloaded.");
    }, 125000);
  }

  return <dialog className="gv-search gv-evidence-dialog" ref={dialog} aria-label="Evidence search and cases"
    onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <div className="gv-search-content gv-incident-form">
      <header className="gv-panel-heading"><h3>Evidence search and cases</h3>
        <button type="button" aria-label="Close evidence search" className="gv-icon-button" onClick={onClose}><X size={18} /></button></header>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <form className="gv-incident-form" onSubmit={(event) => { event.preventDefault(); void showPage(true); }}>
        <div className="gv-evidence-filters">
          <label>Search sites<select value={scope} onChange={(event) => setScope(event.target.value)}>
            <option value="">All authorized sites</option>
            {available.map(({ operator }) => <option key={operator.siteId} value={operator.siteId}>{operator.site}</option>)}
          </select></label>
          {(Object.entries(LABELS) as [keyof typeof LABELS, string][]).map(([key, label]) =>
            <label key={key}>{label}<input ref={key === "q" ? first : undefined} type={key.endsWith("At") ? "datetime-local" : "text"}
              value={filters[key]} maxLength={160} onChange={(event) => setFilters({ ...filters, [key]: event.target.value })} /></label>)}
        </div>
        <button type="submit" className="gv-button gv-primary" disabled={busy}>Search evidence</button>
      </form>
      {state && <section aria-label="Evidence search results">
        <p role="status">{rows.length} results{state.earliest ? " · Indexed from " + new Date(state.earliest).toLocaleString() : ""}</p>
        {!!state.partial.length && <p role="status">Search incomplete · {state.partial.join(", ")} unavailable. Search again to retry.</p>}
        {!!state.index.length && <p role="status">Still indexing · {state.index.join(", ")}</p>}
        <ul className="gv-triage-records">{rows.map(({ site, evidence }) =>
          <li key={site.operator.siteId + ":" + evidence.id}><strong>{evidence.label}</strong>
            <small>{site.operator.site} · {evidence.device ?? evidence.source} · {new Date(evidence.occurredAt).toLocaleString()}</small>
            <div className="gv-detail-actions">
              {evidence.incidentId && <button className="gv-button" type="button" onClick={() => onIncident(site, evidence.incidentId!)}>Open incident replay</button>}
              {evidence.cameraId && <button className="gv-button" type="button" disabled={busy} onClick={() => void act(async (signal) => {
                let value;
                for (let attempt = 0; attempt < 5; attempt++) {
                  signal.throwIfAborted();
                  value = await siteRequest(site, "evidence-recording", { sourceId: evidence.id }, signal);
                  if (value.state !== "pending") break;
                  if (attempt < 4) await waitForRecorder();
                }
                if (value.recording) setClip({ session: site, recording: value.recording, label: evidence.label });
                else { setClip(undefined); setNotice(MEDIA[value.state] ?? "Recording unavailable"); }
              }, 95000)}>Open evidence recording</button>}
              {site.operator.role !== "viewer" && <button className="gv-button" type="button" disabled={busy} onClick={() =>
                void caseAction("create", { title: evidence.label.slice(0, 120), sourceId: evidence.id, incidentId: evidence.incidentId }, site)}>Start case from result</button>}
              {selected && !selected.closed && caseSite.operator.siteId === site.operator.siteId && site.operator.role !== "viewer" &&
                <button className="gv-button" type="button" disabled={busy} onClick={() => void caseAction("attach", { sourceId: evidence.id })}>Attach to open case</button>}
            </div></li>)}</ul>
        {state.more && <button className="gv-button" disabled={busy} type="button" onClick={() => void showPage(false)}>More evidence results</button>}
      </section>}
      {clip && <section aria-label="Evidence recording">
        <button type="button" className="gv-button" onClick={() => setClip(undefined)}>Close recording</button>
        <ClipPlayer key={clip.recording.path} session={clip.session} clip={clip.recording} label={clip.label} />
      </section>}
      <section className="gv-incident-form" aria-label="Cases">
        <h4>Cases</h4>
        <label>Case site<select disabled={busy} value={caseSite.operator.siteId} onChange={(event) => {
          setCaseSite(available.find(({ operator }) => operator.siteId === event.target.value) ?? active);
          setCases(undefined); setSelected(undefined); setTitle(""); setFinding(""); setClip(undefined);
        }}>{available.map(({ operator }) => <option key={operator.siteId} value={operator.siteId}>{operator.site}</option>)}</select></label>
        <button className="gv-button" disabled={busy} type="button" onClick={() => void act(async (signal) => {
          const value = await siteRequest(caseSite, "cases", undefined, signal);
          setCases(value.cases);
        })}>Open cases list</button>
        {cases && <ul className="gv-triage-records">{cases.map((item) => <li key={item.id}>
          <button className="gv-button" disabled={busy} type="button" onClick={() => void act(async (signal) => {
            const result = await siteRequest(caseSite, "cases?" + new URLSearchParams({ id: item.id }), undefined, signal);
            updateCase(caseSite, result.case);
          })}>{item.title}</button>{item.closed && <small>Closed</small>}</li>)}</ul>}
        {selected && <div className="gv-detail-actions">
          <button type="button" className="gv-button" disabled={busy} onClick={() => void act(async (signal) => {
            const result = await siteRequest(caseSite, "cases?" + new URLSearchParams({ id: selected.id }), undefined, signal);
            updateCase(caseSite, result.case);
          })}>Refresh open case</button>
          <button type="button" className="gv-button" onClick={() => { setSelected(undefined); setTitle(""); setFinding(""); }}>Leave case</button>
        </div>}
        {caseSite.operator.role !== "viewer" && !selected?.closed && <>
          <label>Case title<input value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} /></label>
          {selected ? <>
            <label>Case finding<textarea value={finding} maxLength={1000} onChange={(event) => setFinding(event.target.value)} /></label>
            <button className="gv-button" type="button" disabled={busy || !title.trim()} onClick={() => void caseAction("update", { title, finding })}>Save case finding</button>
          </> : <button className="gv-button" type="button" disabled={busy || !title.trim()}
            onClick={() => void caseAction("create", { title, ...(caseSite.operator.siteId === active.operator.siteId && incident ? { incidentId: incident.id } : {}) })}>Create case</button>}
        </>}
        {selected && <section aria-label="Open case evidence">
          <h4>{selected.title}</h4><p>{selected.closed ? "Closed" : "Open"} · Revision {selected.revision}</p>
          {selected.finding && <p>{selected.finding}</p>}
          {selected.incidentId && <button className="gv-button" type="button" onClick={() => onIncident(caseSite, selected.incidentId!)}>Open linked incident</button>}
          <ul className="gv-triage-records">{selected.attachments.map((item) => <li key={item.attachmentId}>
            <strong>{item.label}</strong><small>{MEDIA[item.state] ?? item.state} · {new Date(item.occurredAt).toLocaleString()}</small>
            <div className="gv-detail-actions">
              {item.cameraId && caseSite.operator.role !== "viewer" && <button className="gv-button" type="button" disabled={busy}
                onClick={() => void caseAction("retrieve", { attachmentId: item.attachmentId })}>Retrieve case recording</button>}
              {item.recording && <button className="gv-button" type="button"
                onClick={() => setClip({ session: caseSite, recording: item.recording!, label: item.label })}>Play case recording</button>}
              {item.recording && canManageSite(caseSite) && <button className="gv-button" type="button" disabled={busy}
                onClick={() => void caseAction("hold", { attachmentId: item.attachmentId })}>Hold recording</button>}
              {!selected.closed && caseSite.operator.role !== "viewer" && <button className="gv-button" type="button" disabled={busy}
                onClick={() => void caseAction("detach", { attachmentId: item.attachmentId })}>Remove attachment</button>}
              {canManageSite(caseSite) && !!item.heldUntil && <label className="gv-checkbox"><input type="checkbox"
                checked={exportSelection.includes(item.attachmentId)} disabled={!exportSelection.includes(item.attachmentId) && exportSelection.length >= 4}
                onChange={(event) => setExportSelection(event.target.checked ? [...exportSelection, item.attachmentId]
                  : exportSelection.filter((id) => id !== item.attachmentId))} /> Include in signed export</label>}
            </div>
          </li>)}</ul>
          {canManageSite(caseSite) ? <button className="gv-button" type="button" disabled={busy || !exportSelection.length} onClick={download}>Export signed case</button>
            : caseSite.operator.role !== "viewer" && !selected.closed && <button className="gv-button" type="button" disabled={busy}
              onClick={() => void caseAction("request-export")}>Request supervisor export</button>}
          {!selected.closed && caseSite.operator.role !== "viewer" && <button className="gv-button" type="button" disabled={busy}
            onClick={() => void caseAction("close")}>Close case</button>}
          <ul className="gv-triage-records">{selected.activity.map((item, index) => <li key={index}><strong>{item.actor} · {item.operation}</strong>
            <small>{new Date(item.at).toLocaleString()}{item.detail && " · " + item.detail}</small></li>)}</ul>
        </section>}
      </section>
    </div>
  </dialog>;
}
