import { useEffect, useRef, useState } from "react";
import { siteRequest, type SiteInventory, type SiteSession } from "./SiteOperator";

type Review = { changes: { collection: string; id: string; name: string; change: string }[];
  unchanged: string[]; errors: { source: string; row: number; message: string }[] };

export function InventoryImport({ session, publishedVersion, draftRevision, onPreview, onSaved, openRequest }: {
  session: SiteSession; publishedVersion: number; draftRevision: number;
  openRequest: number;
  onPreview: (value: SiteInventory) => void; onSaved: () => void;
}) {
  const [csv, setCsv] = useState("");
  const [geo, setGeo] = useState("");
  const [review, setReview] = useState<{ review: Review; inventory?: SiteInventory }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const panel = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (openRequest && panel.current) {
      panel.current.open = true;
      panel.current.querySelector<HTMLInputElement>("input")?.focus();
    }
  }, [openRequest]);

  async function read(file: File | undefined, setValue: (value: string) => void) {
    setReview(undefined); setNotice(""); setError("");
    if (!file) { setValue(""); return; }
    try {
      if (file.size > 450_000) throw new Error("Choose an inventory file smaller than 450 KB.");
      setValue(new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()));
    } catch (issue) { setValue(""); setError(issue instanceof Error ? issue.message : "Could not read the file."); }
  }

  async function run(operation: "preview" | "apply") {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await siteRequest(session, "configuration/import", { operation, publishedVersion, draftRevision,
        devicesCsv: csv, boundariesGeoJson: geo }, AbortSignal.timeout(15000));
      setReview(result);
      if (operation === "apply" && !result.review.errors.length) {
        setNotice(result.review.changes.length ? "Inventory saved to the shared draft. Review and publish it when ready." : "The inventory already matches this site.");
        onSaved();
      }
    } catch (issue) { setReview(undefined); setError(issue instanceof Error ? issue.message : "Could not review the import."); }
    finally { setBusy(false); }
  }

  return <details ref={panel} className="gv-triage-section">
    <summary>Import site inventory</summary>
    <div className="gv-triage-content gv-incident-form">
      <label>Device CSV<input type="file" accept=".csv,text/csv" onChange={(event) => void read(event.target.files?.[0], setCsv)} /></label>
      <label>Boundary GeoJSON<input type="file" accept=".json,.geojson,application/json,application/geo+json" onChange={(event) => void read(event.target.files?.[0], setGeo)} /></label>
      <p>Use site device IDs, longitude and latitude, and current floor and group IDs.</p>
      <button className="gv-button" type="button" disabled={busy || !csv && !geo} onClick={() => void run("preview")}>Preview inventory import</button>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {review && <section aria-label="Inventory import review">
        <p role="status">{review.review.changes.length} changes · {review.review.unchanged.length} unchanged · {review.review.errors.length} errors</p>
        {!!review.review.errors.length && <ul className="gv-triage-records">{review.review.errors.map((item, index) =>
          <li key={index}><strong>{item.source}{item.row ? " · Row " + item.row : ""}</strong><small>{item.message}</small></li>)}</ul>}
        {!!review.review.changes.length && <ul className="gv-triage-records">{review.review.changes.map((item) =>
          <li key={item.collection + item.id}><strong>{item.name}</strong><small>{item.collection} · {item.change}</small></li>)}</ul>}
        {review.inventory && !review.review.errors.length && <div className="gv-detail-actions">
          <button className="gv-button" type="button" disabled={busy} onClick={() => onPreview(review.inventory!)}>Preview inventory on map</button>
          <button className="gv-button gv-primary" type="button" disabled={busy || !review.review.changes.length}
            onClick={() => void run("apply")}>Apply inventory to draft</button>
        </div>}
      </section>}
    </div>
  </details>;
}
