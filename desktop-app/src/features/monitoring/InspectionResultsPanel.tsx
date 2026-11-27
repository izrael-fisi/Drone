import { useEffect, useState } from "react";
import { ObservationRecording } from "./CctvReview";
import { siteRequest, type SiteSession } from "./SiteOperator";
import type { InspectionEvent, Sensor } from "./monitoringModel";

type InspectionPage = { entries: { sequence: number; event: InspectionEvent }[]; before: number | null };
const TIMES = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" });

function provenance(value: InspectionEvent["observation"]["inspection"]) {
  if (value.source === "simulated") return "Simulated station";
  return value.simulated ? "OPC UA · Simulated station" : "OPC UA station";
}

export function InspectionResultsPanel({ session, cameras, onCamera }: {
  session: SiteSession; cameras: Sensor[]; onCamera: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [camera, setCamera] = useState("");
  const [outcome, setOutcome] = useState("all");
  const [recipe, setRecipe] = useState("");
  const [lot, setLot] = useState("");
  const [filters, setFilters] = useState({ recipe: "", lot: "" });
  const [exporting, setExporting] = useState(false);
  const [exportNotice, setExportNotice] = useState("");
  const [before, setBefore] = useState<number>();
  const [selected, setSelected] = useState<number>();
  const [recording, setRecording] = useState(false);
  const [response, setResponse] = useState<{ query: string; page?: InspectionPage; error?: string }>();
  const stations = cameras.filter(({ inspectionStationId }) => inspectionStationId);
  const cameraFilter = stations.some(({ id }) => id === camera) ? camera : "";
  const params = new URLSearchParams({ outcome });
  if (before) params.set("before", String(before));
  if (cameraFilter) params.set("camera", cameraFilter);
  if (filters.recipe) params.set("recipe", filters.recipe);
  if (filters.lot) params.set("lot", filters.lot);
  const query = params.toString();
  const active = open && stations.length > 0;
  const current = response?.query === query ? response : undefined;
  const event = current?.page?.entries.find(({ sequence }) => selected === sequence)?.event;
  const inspection = event?.observation.inspection;
  const linked = event && cameras.find(({ id }) => id === event.cameraId);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let timer: number;
    async function load() {
      try {
        const page: InspectionPage = await siteRequest(session, `inspection-results?${query}`, undefined,
          AbortSignal.any([controller.signal, AbortSignal.timeout(6000)]));
        if (!controller.signal.aborted) setResponse({ query, page });
      } catch (cause) {
        if (!controller.signal.aborted) setResponse({ query, error: cause instanceof Error ? cause.message : "Could not load inspection results." });
      } finally {
        if (!controller.signal.aborted && !before) timer = window.setTimeout(load, 5000);
      }
    }
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [active, session, query, before]);

  function pageTo(value?: number) {
    setBefore(value);
    setSelected(undefined);
    setRecording(false);
  }

  async function exportResults() {
    setExporting(true);
    setExportNotice("");
    try {
      const exported = new URLSearchParams(query);
      exported.delete("before");
      exported.set("export", "csv");
      const data = await siteRequest(session, "inspection-results?" + exported.toString());
      const url = URL.createObjectURL(new Blob([data.csv], { type: "text/csv;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "proxigo-inspection-results.csv";
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setExportNotice(data.truncated ? "Exported the latest 1,000 matching results." : `Exported ${data.count} matching results.`);
    } catch (cause) {
      setExportNotice(cause instanceof Error ? cause.message : "Could not export inspection results.");
    } finally {
      setExporting(false);
    }
  }

  if (!stations.length) return null;
  return <details className="gv-triage-section" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Inspection results</summary>
    {active && <section className="gv-triage-content" aria-label="Inspection results">
      <div className="gv-queue-filters gv-incident-form">
        <label>Inspection camera<select value={cameraFilter} onChange={(event) => { setCamera(event.currentTarget.value); pageTo(); }}>
          <option value="">All assigned stations</option>
          {stations.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
        </select></label>
        <label>Inspection outcome<select value={outcome} onChange={(event) => { setOutcome(event.currentTarget.value); pageTo(); }}>
          <option value="all">All results</option><option value="pass">Pass</option><option value="fail">Fail</option><option value="incomplete">Incomplete</option>
        </select></label>
      </div>
      <form className="gv-queue-filters gv-incident-form" onSubmit={(event) => {
        event.preventDefault(); setFilters({ recipe: recipe.trim(), lot: lot.trim() }); setExportNotice(""); pageTo();
      }}>
        <label>Recipe ID<input value={recipe} maxLength={96} pattern="[A-Za-z0-9_\\-]+" onChange={(event) => setRecipe(event.target.value)} /></label>
        <label>Lot ID<input value={lot} maxLength={96} pattern="[A-Za-z0-9_\\-]+" onChange={(event) => setLot(event.target.value)} /></label>
        <div className="gv-detail-actions">
          <button type="submit" className="gv-button">Apply inspection filters</button>
          <button type="button" className="gv-button" disabled={exporting || !current?.page}
            onClick={() => void exportResults()}>Export filtered results</button>
        </div>
      </form>
      {exportNotice && <p role="status">{exportNotice}</p>}
      {current?.error && <p role="alert">{current.error}</p>}
      {!current && <p role="status">Loading inspection results…</p>}
      {current?.page && !current.page.entries.length && <p>No inspection results match these filters.</p>}
      {current?.page?.entries.map(({ sequence, event: item }) => <button key={sequence} type="button" className="gv-posture-row"
        aria-pressed={selected === sequence} onClick={() => { setSelected(sequence); setRecording(false); }}>
        <span><strong>{item.observation.summary}</strong><small>{item.sensorName} · {item.observation.inspection.componentId}</small>
          <small>{TIMES.format(new Date(item.observation.occurredAt))} UTC · {provenance(item.observation.inspection)}</small></span>
      </button>)}
      {event && inspection && <section className="gv-details" aria-label="Selected inspection result">
        <h4>{event.observation.summary}</h4>
        <p>{event.sensorName} · {inspection.stationId} · Version {event.configurationVersion}</p>
        <p>{provenance(inspection)}{inspection.resultId && " · Result " + inspection.resultId}</p>
        <p>Recipe {inspection.recipeId} · Component {inspection.componentId}</p>
        <p>{[inspection.partId && `Part ${inspection.partId}`, inspection.lotId && `Lot ${inspection.lotId}`].filter(Boolean).join(" · ")}</p>
        {!!event.boundaries.length && <p>{event.boundaries.map(({ name }) => name).join(", ")}</p>}
        <div className="gv-detail-actions">
          {linked && <button type="button" className="gv-button" onClick={() => onCamera(linked.id)}>Open station camera</button>}
          {linked && inspection.outcome === "fail" && <button type="button" className="gv-button" aria-expanded={recording}
            onClick={() => setRecording(!recording)}>{recording ? "Close station recording" : "Find station recording"}</button>}
        </div>
        {recording && <ObservationRecording event={event} camera={linked} />}
      </section>}
      {current?.page && (before || current.page.before) && <div className="gv-detail-actions">
        {before && <button type="button" className="gv-button" onClick={() => pageTo()}>Newest inspection results</button>}
        {current.page.before && <button type="button" className="gv-button" onClick={() => pageTo(current.page?.before ?? undefined)}>Older inspection results</button>}
      </div>}
    </section>}
  </details>;
}
