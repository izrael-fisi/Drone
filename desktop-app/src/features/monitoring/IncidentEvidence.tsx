import { useState } from "react";
import { preserveClip, type ClipHold, type IncidentArchive } from "./incidentArchive";
import type { Alert, IncidentAction, SiteEvent } from "./monitoringModel";
import type { SiteSession } from "./SiteOperator";

export function IncidentEvidence({ alert, archive, timeline, siteSession, onAction }: {
  alert: Alert; archive?: IncidentArchive; timeline: SiteEvent[]; siteSession?: SiteSession; onAction: (action: IncidentAction) => void;
}) {
  const [start, setStart] = useState("-5");
  const [end, setEnd] = useState("5");
  const [label, setLabel] = useState("");
  const [hold, setHold] = useState<{ clipId: string; receipt: ClipHold }>();
  const [token, setToken] = useState("");
  const [preserving, setPreserving] = useState(false);
  const [error, setError] = useState("");
  const [exportToken, setExportToken] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportStatus, setExportStatus] = useState("");
  const [exportError, setExportError] = useState("");
  const [selected, setSelected] = useState<string[]>();
  const [redact, setRedact] = useState(false);
  const [mask, setMask] = useState(["0", "80", "100", "20"]);
  const enterprise = siteSession?.enterprise === true;
  const canManageEvidence = !enterprise || siteSession.operator.role === "supervisor" || siteSession.operator.role === "org-admin";
  const clip = archive?.state === "available" ? archive : undefined;
  const receipt = clip && (hold?.clipId === clip.clipId ? hold.receipt : clip.hold);
  const bookmarks = alert.evidenceBookmarks ?? [];
  const time = Date.parse(alert.observation.occurredAt);
  const valid = !!label.trim() && start !== "" && end !== "" && Number(start) < Number(end);
  const references = new Map<string, { cameraKey: string; clipId: string; label: string }>();
  if (clip && receipt) {
    references.set(clip.clipId, { cameraKey: clip.cameraKey, clipId: clip.clipId, label: "Held recorder clip" });
    const base = new URL(clip.gateway);
    for (const event of timeline) {
      if (!event.observation.recording) continue;
      const url = new URL(event.observation.recording.url);
      const match = url.origin === base.origin && url.pathname.startsWith(base.pathname)
        && url.pathname.slice(base.pathname.length).match(/^streams\/([a-f0-9]{48})\/clips\/([a-f0-9]{32})\.mp4$/);
      if (match && !references.has(match[2])) references.set(match[2], { cameraKey: match[1], clipId: match[2], label: event.observation.summary });
    }
  }
  const choices = selected ?? (clip ? [clip.clipId] : []);
  const selectedRefs = [...references.values()].filter(({ clipId }) => choices.includes(clipId));
  const numericMask = mask.map(Number);
  const validMask = !redact || mask.every((value) => value.trim() && /^\d{1,3}$/.test(value)) && numericMask[2] > 0 && numericMask[3] > 0
    && numericMask[0] + numericMask[2] <= 100 && numericMask[1] + numericMask[3] <= 100;

  async function exportPackage() {
    if (!clip || !receipt || !alert.owner || !canManageEvidence || (!enterprise && !exportToken.trim()) || !selectedRefs.length || selectedRefs.length > 4 || !validMask) return;
    setExporting(true); setExportError(""); setExportStatus("");
    try {
      const events = [...timeline].sort((a, b) => Math.abs(Date.parse(a.observation.occurredAt) - time) - Math.abs(Date.parse(b.observation.occurredAt) - time))
        .slice(0, 35).sort((a, b) => a.observation.occurredAt.localeCompare(b.observation.occurredAt))
        .map((event) => ({ at: event.observation.occurredAt, device: event.observation.sensorId, event: event.observation.summary }));
      const response = await fetch(new URL("api/clips/package", clip.gateway), {
        method: "POST", credentials: enterprise ? "same-origin" : "omit", redirect: "error", signal: AbortSignal.timeout(125_000),
        headers: { "Content-Type": "application/json", ...(!enterprise && { Authorization: "Bearer " + exportToken.trim() }) },
        body: JSON.stringify({ incidentId: alert.id, operator: alert.owner, clips: selectedRefs.map(({ cameraKey, clipId }) => ({ cameraKey, clipId })),
          timeline: events, mask: redact ? numericMask : undefined }),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.error ?? "Could not export the evidence package.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "proxigo-incident-evidence.zip"; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setExportToken(""); setExportStatus("Signed evidence package downloaded.");
    } catch (error) { setExportError(error instanceof Error ? error.message : "Could not export the evidence package."); }
    finally { setExporting(false); }
  }

  function downloadIndex() {
    const payload = {
      incidentId: alert.id, observationId: alert.observation.id, source: alert.sensorName, boundary: alert.zoneName,
      observedAt: alert.observation.occurredAt, status: alert.status, owner: alert.owner, disposition: alert.disposition,
      bookmarks, activity: alert.activity ?? [], clip: clip && receipt ? {
        id: clip.clipId, cameraId: clip.recording.cameraId, eventOffsetSeconds: clip.recording.eventOffsetSeconds,
        durationSeconds: clip.recording.durationSeconds, receipt,
      } : undefined,
      exportedAt: new Date().toISOString(),
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "proxigo-incident-evidence.json";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <details className="gv-triage-section">
    <summary>Evidence bookmarks and retention</summary>
    <div className="gv-triage-content">
      {alert.owner && bookmarks.length < 30 && <form className="gv-incident-form" onSubmit={(event) => {
        event.preventDefault();
        if (!valid) return;
        onAction({ kind: "bookmark", label, startAt: new Date(time + Number(start) * 1000).toISOString(),
          endAt: new Date(time + Number(end) * 1000).toISOString() });
        setLabel("");
      }}>
        <p>Set the evidence range in seconds relative to the incident time.</p>
        <label>Bookmark name<input required maxLength={80} value={label} onChange={(event) => setLabel(event.target.value)} /></label>
        <label>Start offset (seconds)<input type="number" required min={-900} max={900} value={start} onChange={(event) => setStart(event.target.value)} /></label>
        <label>End offset (seconds)<input type="number" required min={-900} max={900} value={end} onChange={(event) => setEnd(event.target.value)} /></label>
        <button type="submit" className="gv-button" disabled={!valid}>Save evidence bookmark</button>
      </form>}
      {!!bookmarks.length && <ul className="gv-triage-records">{bookmarks.map((bookmark) => <li key={bookmark.id}>
        <strong>{bookmark.label}</strong><small>{bookmark.startAt} to {bookmark.endAt}</small>
      </li>)}</ul>}
      {clip && canManageEvidence && alert.owner && !receipt && <form className="gv-incident-form" onSubmit={async (event) => {
        event.preventDefault();
        if (!enterprise && !token.trim()) return;
        setPreserving(true); setError("");
        try {
          const receipt = await preserveClip(clip, token, alert.owner!, enterprise);
          setHold({ clipId: clip.clipId, receipt });
          setToken("");
        } catch (error) { setError(error instanceof Error ? error.message : "Could not preserve the clip."); }
        finally { setPreserving(false); }
      }}>
        <p>The hold preserves the retrieved clip. Bookmarks retain the selected time range in the incident index.</p>
        {!enterprise && <label>Gateway management token<input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} /></label>}
        <button className="gv-button" type="submit" disabled={(!enterprise && !token.trim()) || preserving}>Preserve recorder clip for 7 days</button>
      </form>}
      {error && <p role="alert">{error}</p>}
      {clip && receipt && <div>
        <p role="status">Clip held until {new Date(receipt.heldUntil * 1000).toISOString()}</p>
        <a href={clip.recording.url} className="gv-button" target="_blank" rel="noreferrer">Open preserved MP4</a>
      </div>}
      {clip && receipt && alert.owner && canManageEvidence && <form className="gv-incident-form" onSubmit={(event) => { event.preventDefault(); void exportPackage(); }}>
        <h5>Signed evidence package</h5>
        <p>Choose up to four clips with active holds. The package includes the incident timeline and signed file checksums.</p>
        {[...references.values()].map((entry) => <label key={entry.clipId} className="gv-checkbox"><input type="checkbox" checked={choices.includes(entry.clipId)}
          onChange={(event) => setSelected(event.target.checked ? [...choices, entry.clipId] : choices.filter((id) => id !== entry.clipId))} />{entry.label}</label>)}
        <label className="gv-checkbox"><input type="checkbox" checked={redact} onChange={(event) => setRedact(event.target.checked)} />Apply a redaction rectangle to exported clips</label>
        {redact && <div className="gv-queue-filters">{["Left", "Top", "Width", "Height"].map((field, index) => <label key={field}>{field} (% of frame)<input
          type="number" min={0} max={100} required value={mask[index]} onChange={(event) => setMask(mask.map((value, i) => i === index ? event.target.value : value))} /></label>)}</div>}
        {!enterprise && <label>Evidence export token<input type="password" autoComplete="off" value={exportToken} onChange={(event) => setExportToken(event.target.value)} /></label>}
        <button type="submit" className="gv-button" disabled={exporting || (!enterprise && !exportToken.trim()) || !selectedRefs.length || selectedRefs.length > 4 || !validMask}>
          {exporting ? "Preparing evidence package…" : "Download signed evidence package"}</button>
        {exportStatus && <p role="status">{exportStatus}</p>}{exportError && <p role="alert">{exportError}</p>}
      </form>}
      <button type="button" className="gv-button" onClick={downloadIndex}>Export evidence index</button>
    </div>
  </details>;
}
