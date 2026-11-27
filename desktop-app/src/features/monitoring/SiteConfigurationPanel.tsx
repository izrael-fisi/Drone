import { useEffect, useState } from "react";
import { canManageSite, siteInventory, siteRequest, type SiteInventory, type SiteSession } from "./SiteOperator";
import { ProcedureEditor } from "./ResponseProcedures";
import { InventoryImport } from "./InventoryImport";

export type SiteDraft = { configuration: SiteInventory; publishedVersion: number; draftRevision: number; importPreview?: boolean };
type ConfigurationState = { publishedVersion: number; draftRevision: number; draft: SiteInventory | null; published: SiteInventory | null;
  diff: { collection: string; id: string; name: string; change: "added" | "removed" | "changed";
    fields: { name: string; before: unknown; after: unknown }[] }[];
  history: { version: number; publishedAt: number; actor: string; message: string; restoredFrom?: number }[] };

export function SiteConfigurationPanel({ session, local, editing, onEdit, onExit, publishedVersion, importRequest = 0 }: {
  session: SiteSession; local: SiteInventory; editing?: SiteDraft; onEdit: (draft: SiteDraft) => void; onExit: () => void; publishedVersion: number;
  importRequest?: number;
}) {
  const [state, setState] = useState<ConfigurationState>();
  const [expanded, setExpanded] = useState(!!editing);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState("");
  const [drill, setDrill] = useState("");
  const [result, setResult] = useState<{ events: number; matchCount: number; incidentCount?: number;
    matches: { source: string; boundary: string; summary: string; severity: string }[] }>();
  useEffect(() => { if (editing) setExpanded(true); }, [!!editing]);
  useEffect(() => {
    if (importRequest && canManageSite(session)) {
      setExpanded(true);
      if (!state && !busy) void load();
    }
  }, [importRequest]);
  if (!canManageSite(session)) return null;

  async function load() {
    setBusy(true); setError("");
    try { setState(await siteRequest(session, "configuration")); }
    catch (issue) { setError(issue instanceof Error ? issue.message : "Could not load site configuration."); }
    finally { setBusy(false); }
  }

  function edit(source: SiteInventory) {
    if (!state) return;
    onEdit({ configuration: siteInventory(source), publishedVersion: state.publishedVersion, draftRevision: state.draftRevision });
  }

  async function change(operation: "save" | "publish" | "restore" | "discard") {
    if (!state) return;
    setBusy(true); setError(""); setNotice("");
    const basis = operation === "save" && editing ? editing : state;
    try {
      const next = await siteRequest(session, "configuration", { operation, draftRevision: basis.draftRevision, publishedVersion: basis.publishedVersion,
        configuration: operation === "save" && editing ? siteInventory(editing.configuration) : undefined,
        message: message.trim(), restoreVersion: Number(history) });
      setState(next);
      if (operation === "save") onExit();
      if (operation === "publish" || operation === "restore") { setMessage(""); setHistory(""); }
      setNotice(operation === "save" ? "Shared site draft saved." : operation === "discard" ? "Shared site draft discarded."
        : `Configuration version ${next.publishedVersion} published.`);
    } catch (issue) { setError(issue instanceof Error ? issue.message : "Could not update site configuration."); }
    finally { setBusy(false); }
  }

  async function testDraft() {
    const configuration = editing?.configuration ?? state?.draft;
    if (!configuration) return;
    setBusy(true); setError(""); setResult(undefined);
    try {
      const observations = JSON.parse(drill);
      setResult(await siteRequest(session, "configuration/preview", { configuration: siteInventory(configuration), observations }));
    } catch (issue) { setError(issue instanceof Error ? issue.message : "Could not preview the drill."); }
    finally { setBusy(false); }
  }

  return <details className="gv-triage-section" open={expanded} onToggle={(event) => {
    setExpanded(event.currentTarget.open);
    if (event.currentTarget.open && !state && !busy) void load();
  }}>
    <summary>Site configuration and versions</summary>
    <div className="gv-triage-content gv-incident-form">
      <p>Gateway configuration {publishedVersion ? `version ${publishedVersion} is live.` : "has no published version."}</p>
      <button className="gv-button" type="button" disabled={busy} onClick={() => void load()}>Refresh configuration</button>
      {state && <>
        {editing ? <div className="gv-detail-actions">
          {!editing.importPreview && <button className="gv-button gv-primary" type="button" disabled={busy} onClick={() => void change("save")}>Save site draft</button>}
          <button className="gv-button" type="button" disabled={busy} onClick={onExit}>{editing.importPreview ? "Return to inventory import" : "Exit draft without saving"}</button>
        </div> : <div className="gv-detail-actions">
          <button className="gv-button" type="button" disabled={busy} onClick={() => edit(state.draft ?? state.published ?? local)}>
            {state.draft ? "Edit saved site draft" : state.published ? "Edit published configuration as a draft" : "Start draft from preview inventory"}
          </button>
          {state.draft && <button className="gv-button" type="button" disabled={busy} onClick={() => void change("discard")}>Discard saved site draft</button>}
        </div>}
        {(!editing || editing.importPreview) && <div hidden={!!editing?.importPreview}><InventoryImport session={session} publishedVersion={state.publishedVersion}
          openRequest={importRequest}
          draftRevision={state.draftRevision} onSaved={() => void load()} onPreview={(configuration) => onEdit({
            publishedVersion: state.publishedVersion, draftRevision: state.draftRevision, configuration, importPreview: true,
          })} /></div>}
        {editing && !editing.importPreview && <ProcedureEditor definitions={editing.configuration.procedures ?? []} boundaries={editing.configuration.boundaries}
          onChange={(procedures) => onEdit({ ...editing, configuration: { ...editing.configuration, procedures } })} />}
        {state.draft && <section aria-label="Draft changes">
          <h4>Draft changes</h4>
          {state.diff.length ? <ul className="gv-triage-records">{state.diff.map((item) => <li key={item.collection + item.id}>
            <strong>{item.name}</strong><small>{item.collection} · {item.change}</small>
            {item.change === "changed" && !!item.fields.length && <details><summary>Compare values</summary><dl className="gv-draft-diff">
              {item.fields.map((field) => <div key={field.name}><dt>{field.name.replace(/([A-Z])/g, " $1")}</dt>
                <dd>Before <code>{JSON.stringify(field.before)}</code></dd><dd>After <code>{JSON.stringify(field.after)}</code></dd>
              </div>)}
            </dl></details>}
          </li>)}</ul> : <p>The draft matches the published version.</p>}
        </section>}
        {(editing || state.draft) && <details className="gv-triage-section">
          <summary>Run a configuration drill</summary>
          <div className="gv-triage-content gv-incident-form">
            <label>Drill observations (JSON array)<textarea rows={4} value={drill} maxLength={65_536} placeholder='[{"id":"door-test", "sensorId":"gate-01", "eventType":"door-forced-open", "occurredAt":"…", "summary":"Test"}]'
              onChange={(event) => { setDrill(event.target.value); setResult(undefined); }} /></label>
            <button className="gv-button" type="button" disabled={busy || !drill.trim()} onClick={() => void testDraft()}>Preview draft with test events</button>
            {result && <><p role="status">{result.events} test events · {result.matchCount} boundary matches · {result.incidentCount ?? result.matchCount} {(result.incidentCount ?? result.matchCount) === 1 ? "incident" : "incidents"} in the drill</p>
              <ul className="gv-triage-records">{result.matches.map((item, index) => <li key={index}><strong>{item.summary}</strong><small>{item.source} · {item.boundary} · {item.severity}</small></li>)}</ul></>}
          </div>
        </details>}
        {(state.draft || state.history.length > 1) && !editing && <label>Version note<input value={message} maxLength={180} onChange={(event) => setMessage(event.target.value)} /></label>}
        {state.draft && !editing && <button className="gv-button gv-primary" type="button" disabled={busy || !message.trim()} onClick={() => void change("publish")}>Publish saved site draft</button>}
        {!!state.history.length && <details className="gv-triage-section">
          <summary>Published version history</summary>
          <div className="gv-triage-content gv-incident-form">
            <ul className="gv-triage-records">{state.history.map((entry) => <li key={entry.version}>
              <strong>Version {entry.version} · {entry.message}</strong><small>{entry.actor} · {new Date(entry.publishedAt * 1000).toLocaleString()}{entry.restoredFrom ? ` · Restored from ${entry.restoredFrom}` : ""}</small>
            </li>)}</ul>
            {state.history.length > 1 && !editing && <><label>Version to restore<select value={history} onChange={(event) => setHistory(event.target.value)}>
              <option value="">Select a prior version</option>{state.history.filter(({ version }) => version !== state.publishedVersion).map(({ version }) => <option key={version} value={version}>Version {version}</option>)}
            </select></label><button className="gv-button" type="button" disabled={busy || !history || !message.trim() || !!state.draft} onClick={() => void change("restore")}>Restore as a new version</button></>}
          </div>
        </details>}
      </>}
      {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
    </div>
  </details>;
}
