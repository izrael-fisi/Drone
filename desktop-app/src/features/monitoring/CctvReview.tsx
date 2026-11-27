import { useEffect, useState, type FormEvent } from "react";
import { RecordedFeed } from "./CameraFeed";
import { gatewayStream } from "./gatewayStream";
import { useIncidentArchive } from "./incidentArchive";
import { siteRequest, SiteRequestError, type SiteSession } from "./SiteOperator";
import { FACTORY_SAFETY_RULES, type IncomingObservation, type Sensor, type SiteEvent } from "./monitoringModel";

const OUTCOMES = { confirmed: "Confirmed", "false-detection": "False detection", unverifiable: "Unable to verify" } as const;
type Outcome = keyof typeof OUTCOMES;
type Filter = "all" | "pending" | Outcome;
type Reviewed = { revision: number; outcome: Outcome; reviewer: string; reviewedAt: string; note: string };
type Entry = { sequence: number; event: SiteEvent; review: Reviewed | null };
type ReviewPage = { entries: Entry[]; counts: Record<Exclude<Filter, "all">, number>; before: number | null };
const DATES = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" });

export function ObservationRecording({ event, camera }: {
  event: { sensorName: string; observation: Pick<IncomingObservation, "recording" | "occurredAt"> }; camera?: Sensor;
}) {
  const archive = useIncidentArchive(event, camera);
  const recording = event.observation.recording ?? (archive?.state === "available" ? archive.recording : undefined);
  if (recording) return <RecordedFeed recording={recording} name={event.sensorName + " observation recording"} />;
  const state = archive?.state;
  return <p role="status">{state === "loading" || state === "pending" ? "Looking up the recording at the observation time…"
    : state === "missing" ? "The recorder has no footage at this time."
      : state === "error" ? "The recorder could not be reached." : "Recorder lookup is unavailable for this camera."}</p>;
}

function ReviewForm({ entry, session, onSaved, onConflict }: {
  entry: Entry; session: SiteSession; onSaved: () => void; onConflict: () => void;
}) {
  const [outcome, setOutcome] = useState<Outcome | "">(entry.review?.outcome ?? "");
  const [note, setNote] = useState(entry.review?.note ?? "");
  const [revision, setRevision] = useState(entry.review?.revision ?? 0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!outcome || busy || stale) return;
    setBusy(true);
    setError("");
    try {
      await siteRequest(session, "analytics-review", { sequence: entry.sequence, revision, outcome, note });
      onSaved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save the detection review.");
      if (cause instanceof SiteRequestError && cause.status === 409) onConflict();
    } finally {
      setBusy(false);
    }
  }
  const stale = revision !== (entry.review?.revision ?? 0);
  return <form className="gv-incident-form" onSubmit={save}>
    <label>Review decision<select value={outcome} onChange={(event) => setOutcome(event.currentTarget.value as Outcome | "")}>
      <option value="">Select a decision</option>
      {Object.entries(OUTCOMES).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
    </select></label>
    <label>Review note<textarea rows={2} maxLength={350} value={note} onChange={(event) => setNote(event.currentTarget.value)} /></label>
    <button type="submit" className="gv-button" disabled={!outcome || busy || stale}>Save detection review</button>
    {stale && <button type="button" className="gv-button" onClick={() => {
      setRevision(entry.review?.revision ?? 0); setOutcome(entry.review?.outcome ?? ""); setNote(entry.review?.note ?? ""); setError("");
    }}>Load current decision</button>}
    {error && <p role="alert">{error}</p>}
  </form>;
}

export function CctvReview({ session, version, cameras }: { session: SiteSession; version: number; cameras: Sensor[] }) {
  const [camera, setCamera] = useState("");
  const [source, setSource] = useState("live");
  const [status, setStatus] = useState<Filter>("pending");
  const [safety, setSafety] = useState("");
  const [currentVersion, setCurrentVersion] = useState(false);
  const [before, setBefore] = useState<number>();
  const [selected, setSelected] = useState<number>();
  const [video, setVideo] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [saved, setSaved] = useState("");
  const [response, setResponse] = useState<{ query: string; page?: ReviewPage; error?: string }>();

  const filters = new URLSearchParams({ source, status });
  if (camera) filters.set("camera", camera);
  if (safety) filters.set("safety", safety);
  if (before) filters.set("before", String(before));
  if (currentVersion && version) filters.set("version", String(version));
  const query = filters.toString();
  const current = response?.query === query ? response : undefined;
  const page = current?.page;
  const entry = page?.entries.find(({ sequence }) => sequence === selected);
  const event = entry?.event;
  const evidence = event?.observation.analytics;
  const selectedCamera = event && cameras.find(({ id }) => id === event.cameraId);

  useEffect(() => {
    const lifetime = new AbortController();
    let timer: number;
    async function load() {
      try {
        const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(6000)]);
        const page: ReviewPage = await siteRequest(session, `analytics-review?${query}`, undefined, signal);
        if (!lifetime.signal.aborted) setResponse({ query, page });
      } catch (cause) {
        if (!lifetime.signal.aborted) setResponse({ query, error: cause instanceof Error ? cause.message : "Could not load CCTV observations." });
      } finally {
        if (!lifetime.signal.aborted && !before) timer = window.setTimeout(load, 5000);
      }
    }
    void load();
    return () => { lifetime.abort(); window.clearTimeout(timer); };
  }, [session, query, before, refresh]);

  function reset() {
    setBefore(undefined);
    setSelected(undefined);
    setVideo(false);
    setSaved("");
  }

  return <section aria-label="Observed CCTV detections" className="gv-cctv-review">
    <h4>Observe mode review</h4>
    <div className="gv-queue-filters gv-incident-form">
      <label>Review camera<select value={camera} onChange={(event) => { setCamera(event.currentTarget.value); reset(); }}>
        <option value="">All cameras</option>{cameras.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
      </select></label>
      <label>Observation source<select value={source} onChange={(event) => { setSource(event.currentTarget.value); reset(); }}>
        <option value="live">Live CCTV</option><option value="replay">Replay</option>
      </select></label>
      <label>Factory safety<select value={safety} onChange={(event) => { setSafety(event.currentTarget.value); reset(); }}>
        <option value="">All CCTV rules</option><option value="all">All factory safety rules</option>
        {Object.entries(FACTORY_SAFETY_RULES).map(([id, rule]) => <option key={id} value={id}>{rule.name}</option>)}
      </select></label>
      <label>Review status<select value={status} onChange={(event) => { setStatus(event.currentTarget.value as Filter); reset(); }}>
        <option value="pending">Pending{page ? ` (${page.counts.pending})` : ""}</option><option value="all">All decisions</option>
        {Object.entries(OUTCOMES).map(([key, name]) => <option key={key} value={key}>{name}{page ? ` (${page.counts[key as Outcome]})` : ""}</option>)}
      </select></label>
      <label className="gv-checkbox"><input type="checkbox" checked={currentVersion} disabled={!version} onChange={(event) => {
        setCurrentVersion(event.currentTarget.checked); reset();
      }} />Current published version</label>
    </div>
    {saved && <p role="status">{saved}</p>}
    {current?.error && <p role="alert">{current.error}</p>}
    {!page && !current?.error && <p role="status">Loading observations…</p>}
    {page && !page.entries.length && <p>No observations match these filters.</p>}
    {page?.entries.map(({ sequence, event, review }) => {
      const detected = event.observation.analytics;
      return <button type="button" key={sequence} className="gv-posture-row"
        aria-pressed={sequence === selected} onClick={() => { setSelected(sequence); setVideo(false); setSaved(""); }}>
        <span><strong>{event.observation.summary}</strong><small>{event.sensorName} · {review ? OUTCOMES[review.outcome] : "Pending"}</small>
          <small>{DATES.format(new Date(event.observation.occurredAt))} UTC{detected && ` · ${Math.round(detected.score * 100)}% model score`}</small></span>
      </button>;
    })}
    {entry && event && evidence && <section className="gv-details" aria-label="Selected CCTV observation">
      <h5>{event.observation.summary}</h5>
      <p>{DATES.format(new Date(event.observation.occurredAt))} UTC · {event.sensorName}</p>
      <p>Version {evidence.policyVersion} · {evidence.model} · {Math.round(evidence.score * 100)}% model score</p>
      {entry.review && <p>{OUTCOMES[entry.review.outcome]} · {entry.review.reviewer} · {DATES.format(new Date(entry.review.reviewedAt))} UTC
        {entry.review.note && <><br />{entry.review.note}</>}</p>}
      {source === "live" && (event.observation.recording || gatewayStream(selectedCamera)) && <button type="button" className="gv-button" aria-expanded={video}
        onClick={() => setVideo(!video)}>{video ? "Close recording" : "Review recording"}</button>}
      {video && <ObservationRecording event={event} camera={selectedCamera} />}
      {session.operator.role !== "viewer" && <ReviewForm key={entry.sequence} entry={entry} session={session} onSaved={() => {
        setSaved("Detection review saved."); setSelected(undefined); setVideo(false); setRefresh((value) => value + 1);
      }} onConflict={() => setRefresh((value) => value + 1)} />}
    </section>}
    {page && (before || page.before) && <div className="gv-detail-actions">
      {before && <button type="button" className="gv-button" onClick={reset}>Newest observations</button>}
      {page.before && <button type="button" className="gv-button" onClick={() => { reset(); setBefore(page.before ?? undefined); }}>Older observations</button>}
    </div>}
  </section>;
}
