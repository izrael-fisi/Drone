import { useEffect, useRef, useState } from "react";
import { siteRequest, SiteRequestError, type SiteSession } from "./SiteOperator";
import type { Alert, HandoffEvidence, IncomingObservation, Recording, SiteEvent, Workspace } from "./monitoringModel";

type Placement = "bedrock" | "on-premises";
type Capabilities = { handoff: boolean; clipReview: boolean; placement?: Placement; clipBoundaries?: string[] };
type Claim = { text: string; evidenceRefs: string[] };
type Draft = { summary: Claim[]; nextAction: Claim; sources: HandoffEvidence[]; template?: boolean };
type Frame = { ref: string; offsetSeconds: number; occurredAt: string; eventFrame: boolean };
type ClipResult = { outcome: "observed" | "not-observed" | "unable-to-verify"; explanation: string; sources: Frame[];
  recording?: { path: string; cameraId: string; durationSeconds: number; eventOffsetSeconds: number } };
type Job = { id: string; state: "queued" | "running" | "ready" | "failed" | "approved"; message?: string };
type JobResult<T> = Job & T;

const time = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "medium", timeZone: "UTC" });
const clock = (at: string | number) => time.format(new Date(typeof at === "string" ? at : at * 1000)) + " UTC";
const placementName = (value?: Placement) => value === "bedrock" ? "Approved cloud model" : "On-premises model";

function useRead<T>(session: SiteSession | undefined, path: string, interval?: number) {
  const [snapshot, setSnapshot] = useState<{ session: SiteSession; path: string; data: T }>();
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    const active = session;
    let timer: number | undefined;
    async function read() {
      try {
        const data = await siteRequest(active, path, undefined, AbortSignal.any([controller.signal, AbortSignal.timeout(6000)])) as T;
        if (!controller.signal.aborted) setSnapshot({ session: active, path, data });
      } catch {
        if (!controller.signal.aborted) setSnapshot(undefined);
      } finally {
        if (interval && !controller.signal.aborted) timer = window.setTimeout(read, interval);
      }
    }
    void read();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [session, path, interval]);
  return snapshot && snapshot.session === session && snapshot.path === path ? snapshot.data : undefined;
}

function useJob<T>(session: SiteSession) {
  const [job, setJob] = useState<JobResult<T>>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ongoing = useRef<AbortController>();
  useEffect(() => () => ongoing.current?.abort(), [session]);

  async function run(body: object) {
    ongoing.current?.abort();
    const controller = new AbortController();
    ongoing.current = controller;
    setBusy(true);
    setError("");
    setJob(undefined);
    try {
      const request = (path: string, payload?: object) => siteRequest(session, path, payload,
        AbortSignal.any([controller.signal, AbortSignal.timeout(6000)])) as Promise<JobResult<T>>;
      let result = await request("ai-jobs", body);
      const limit = Date.now() + 150_000;
      while (!controller.signal.aborted && ["queued", "running"].includes(result.state)) {
        if (Date.now() >= limit) throw new Error("The site AI queue is still busy. Try again shortly.");
        await new Promise((resolve) => window.setTimeout(resolve, 750));
        result = await request("ai-jobs/" + result.id);
      }
      if (!controller.signal.aborted) {
        if (result.state !== "ready") throw new Error(result.message ?? "This review is unavailable.");
        setJob(result);
      }
    } catch (issue) {
      if (!controller.signal.aborted) setError(issue instanceof Error ? issue.message : "Site AI review is unavailable.");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return { job, busy, error, run };
}

export type IntelligenceEvent = { sensorId: string; eventId: string; occurredAt?: string; text?: string; type?: IncomingObservation["eventType"] };

export function HandoffEvidenceLinks({ sources, onEvidence }: { sources: HandoffEvidence[]; onEvidence: (ref: IntelligenceEvent) => void }) {
  return <ul>{sources.map((source) => {
    const label = source.label ?? [source.type.replace(/-/g, " "), source.occurredAt && clock(source.occurredAt)].filter(Boolean).join(" · ");
    const key = source.sensorId && source.eventId && { sensorId: source.sensorId, eventId: source.eventId };
    const anchor = source.actionSequence ? "gv-source-action-" + source.actionSequence : source.procedureStep ? "gv-source-procedure" : null;
    return <li key={source.ref}>{key ? <button type="button" className="gv-button" onClick={() => onEvidence(key)}>{label}</button> :
      anchor ? <button type="button" className="gv-button" onClick={() => {
        const target = document.getElementById(anchor);
        target?.focus();
        target?.scrollIntoView({ block: "nearest" });
      }}>{label}</button> : label}</li>;
  })}</ul>;
}

export function DraftHandoff({ session, incident, onApply, onEvidence }: {
  session: SiteSession; incident: Alert;
  onApply: (job: string, summary: string, next: string) => void;
  onEvidence: (ref: IntelligenceEvent) => void;
}) {
  const features = useRead<Capabilities>(session, "ai-jobs");
  const [chooseNotes, setChooseNotes] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const history = useRead<{ actions: { sequence: number; at: string; action: { kind: string; text?: string; note?: string } }[] }>(
    chooseNotes && features?.handoff ? session : undefined, "incident-history?" + new URLSearchParams({ incidentId: incident.id }));
  const { job, busy, error, run } = useJob<Draft>(session);
  if (!features?.handoff) return null;
  const notes = history?.actions.filter(({ action }) => action.kind === "note" && action.text || action.kind === "handoff" && action.note).slice(-10) ?? [];

  return <div className="gv-triage-content gv-intelligence">
    <p>{placementName(features.placement)} · Event metadata</p>
    <label className="gv-checkbox">
      <input type="checkbox" checked={chooseNotes} disabled={busy} onChange={(event) => { setChooseNotes(event.target.checked); setSelected([]); }} />
      Include selected notes
    </label>
    {chooseNotes && (notes.length ? notes.map(({ sequence, at, action }) => <label className="gv-checkbox" key={sequence}>
      <input type="checkbox" checked={selected.includes(sequence)} disabled={busy} onChange={(event) => {
        setSelected((current) => event.target.checked ? [...current, sequence] : current.filter((value) => value !== sequence));
      }} />
      <span>{clock(at)} · {action.text ?? action.note}</span>
    </label>) : <p>{history ? "No recent incident notes" : "Loading incident notes…"}</p>)}
    <button type="button" className="gv-button" disabled={busy} onClick={() => void run({
      type: "handoff", incidentId: incident.id, noteSequences: selected,
    })}>{busy ? "Drafting handoff…" : "Draft handoff"}</button>
    {error && <p role="alert">{error}</p>}
    {job && <div aria-label="Handoff draft">
      <h4>{job.template ? "Timeline template" : "AI handoff draft"}</h4>
      <p>Review the sources before using the draft.</p>
      {job.summary.map((entry, index) => <div key={index}><p>{entry.text}</p><HandoffEvidenceLinks sources={job.sources.filter(({ ref }) => entry.evidenceRefs.includes(ref))} onEvidence={onEvidence} /></div>)}
      <p>Next action · {job.nextAction.text}</p>
      <HandoffEvidenceLinks sources={job.sources.filter(({ ref }) => job.nextAction.evidenceRefs.includes(ref))} onEvidence={onEvidence} />
      <button type="button" className="gv-button" onClick={() => onApply(job.id, job.summary.map((entry) => entry.text).join("\n"), job.nextAction.text)}>Use draft in handoff</button>
    </div>}
  </div>;
}

const observations = { observed: "Occupancy observed", "not-observed": "Occupancy not observed", "unable-to-verify": "Unable to verify" };
const clipQuestion = "Was the gate area occupied by a person or vehicle at the event time?";

export function ReviewClip({ session, incident, event, onSeek }: {
  session: SiteSession; incident: Alert; event: SiteEvent; onSeek: (recording: Recording) => void;
}) {
  const features = useRead<Capabilities>(session, "ai-jobs");
  const { job, busy, error, run } = useJob<ClipResult>(session);
  const [confirm, setConfirm] = useState(false);
  const [seekError, setSeekError] = useState("");
  const blob = useRef<{ path: string; url: string }>();
  const seekRequest = useRef<AbortController>();
  useEffect(() => () => {
    seekRequest.current?.abort();
    if (blob.current) URL.revokeObjectURL(blob.current.url);
  }, []);
  if (!features?.clipReview || !features.clipBoundaries?.includes(incident.zoneId)) return null;

  async function seek(offset: number) {
    const target = job?.recording;
    if (!target || !/^streams\/[a-f0-9]{48}\/clips\/[a-f0-9]{32}\.mp4$/.test(target.path)) return;
    const controller = new AbortController();
    seekRequest.current?.abort();
    seekRequest.current = controller;
    setSeekError("");
    try {
      let url = new URL(target.path, session.base).href;
      if (!session.enterprise && session.token) {
        if (blob.current?.path !== target.path) {
          const response = await fetch(url, { headers: { Authorization: "Bearer " + session.token },
            signal: AbortSignal.any([AbortSignal.timeout(15000), controller.signal]), cache: "no-store", redirect: "error" });
          if (!response.ok) throw new Error("The recording has expired or is unavailable.");
          const content = await response.blob();
          if (controller.signal.aborted) return;
          if (blob.current) URL.revokeObjectURL(blob.current.url);
          blob.current = { path: target.path, url: URL.createObjectURL(content) };
        }
        url = blob.current.url;
      }
      onSeek({ format: "mp4", url, cameraId: target.cameraId, durationSeconds: target.durationSeconds, eventOffsetSeconds: offset });
    } catch (issue) {
      if (!controller.signal.aborted) setSeekError(issue instanceof Error ? issue.message : "The recording is unavailable.");
    }
  }

  return <div className="gv-triage-content gv-intelligence">
    <button type="button" className="gv-button" aria-expanded={confirm} onClick={() => setConfirm(!confirm)}>Review with AI</button>
    {confirm && <div className="gv-triage-content">
      <p>{clipQuestion}</p>
      <p>{placementName(features.placement)} · Selected frames, up to 12 seconds of recording</p>
      <button type="button" className="gv-button" disabled={busy} onClick={() => {
        seekRequest.current?.abort();
        if (blob.current) URL.revokeObjectURL(blob.current.url);
        blob.current = undefined;
        setSeekError("");
        void run({ type: "clip-review", incidentId: incident.id, event: { sensorId: event.observation.sensorId, id: event.observation.id } });
      }}>{busy ? "Reviewing recording…" : "Analyze event frames"}</button>
      {error && <p role="alert">{error}</p>}
      {job && <div aria-label="AI draft observation">
        <h4>Draft observation · {observations[job.outcome]}</h4>
        <p>{job.explanation}</p>
        {job.sources.map((frame) => <button key={frame.ref} type="button" className="gv-button" onClick={() => void seek(frame.offsetSeconds)}>
          Seek to {clock(frame.occurredAt)}{frame.eventFrame ? " · Event frame" : ""}
        </button>)}
        {seekError && <p role="alert">{seekError}</p>}
      </div>}
    </div>}
  </div>;
}

type Metric = { id: string; label: string; state: "abnormal" | "normal" | "learning" | "insufficient-data"; checkedAt: number;
  value: number | null; expected: number | null; openedAt: number | null; history: { at: number; value: number; samples: number }[] };
type Degradation = { enabled: boolean; cameras: { cameraId: string; boundaries: { id: string; name: string }[]; metrics: Metric[];
  recentRecoveries: { metric: string; at: number; reason: string }[] }[] };
type Gate = { sensorId: string; sensorName: string; boundaryId: string; cameraId: string; state: string };
type FusionRead = { enabled: boolean; gates?: Gate[] };
type FusionEvidence = IntelligenceEvent & { occurredAt: string; source: string; summary: string; trustedClock: boolean };
type FusionEpisode = { state: "collecting" | "corroborated" | "single-source" | "ambiguous" | "insufficient-evidence"; reason?: string;
  maintenance?: boolean; cameraAvailable?: boolean; radarAvailable?: boolean; evidence: FusionEvidence[]; review?: { reviewer: string; outcome: FusionOutcome } };
type FusionOutcome = "confirmed" | "false-corroboration" | "unverifiable";
const outcomes: Record<FusionOutcome, string> = { confirmed: "Confirmed", "false-corroboration": "Incorrect correlation", unverifiable: "Unable to verify" };
const fusionLabels = { collecting: "Collecting gate evidence", corroborated: "Corroborated", "single-source": "Single source",
  ambiguous: "Ambiguous", "insufficient-evidence": "Insufficient evidence" };
const metricLabels = { abnormal: "Above baseline", normal: "Within baseline", learning: "Learning baseline", "insufficient-data": "Insufficient data" };
const radarLabels: Record<string, string> = { ready: "Reporting", unmapped: "Check gate mapping", unsupported: "Profile unavailable",
  "clock-unverified": "Clock unverified", disconnected: "Disconnected" };

function units(metric: string, value: number) {
  if (metric.endsWith("-failures")) return new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 }).format(value);
  return value.toFixed(1) + " s";
}

type TriageSetting = { available: boolean; enabled: boolean; revision: number; canManage: boolean; placement?: Placement; dailyLimit: number };
type TriageEvent = IntelligenceEvent & { ref: string; text: string; occurredAt: string };
type TriageReview = { state: "collecting" } | {
  state: "processing" | "ready" | "timeline"; reason?: "limit" | "provider" | "restart" | "interrupted" | "disabled" | "expired";
  summary: Claim[]; events: TriageEvent[];
};
const triageReasons = {
  limit: "Daily AI limit reached.",
  provider: "AI review was unavailable.",
  restart: "The gateway restarted during review.",
  interrupted: "AI review was interrupted.",
  disabled: "Automatic triage was turned off during review.",
  expired: "This incident arrived outside the automatic review window.",
};

function AutomaticTriageSetting({ session }: { session: SiteSession }) {
  const fetched = useRead<TriageSetting>(session.operator.automaticTriageAvailable ? session : undefined, "automatic-triage", 10000);
  const [saved, setSaved] = useState<{ session: SiteSession; value: TriageSetting }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const local = saved?.session === session ? saved.value : undefined;
  const settings = fetched && local && local.revision >= fetched.revision ? local : fetched;
  if (!settings?.available || !settings.canManage) return null;

  async function save(selected: TriageSetting) {
    setBusy(true);
    setError("");
    try {
      const value = await siteRequest(session, "automatic-triage", { enabled: !selected.enabled, revision: selected.revision }) as TriageSetting;
      setSaved({ session, value });
    } catch (issue) {
      if (issue instanceof SiteRequestError && issue.status === 409) setSaved(undefined);
      setError(issue instanceof Error ? issue.message : "Unable to update automatic triage.");
    } finally {
      setBusy(false);
    }
  }

  return <details className="gv-triage-section">
    <summary>Automatic AI triage <span>{settings.enabled ? "On" : "Off"}</span></summary>
    <div className="gv-triage-content">
      <p>Summarize new camera-linked incidents from recorded timeline events. Each review includes timestamps that open in replay.</p>
      <p>{placementName(settings.placement)} · Event metadata only · Up to {settings.dailyLimit} AI reviews per day</p>
      <label className="gv-checkbox">
        <input type="checkbox" checked={settings.enabled} disabled={busy} onChange={() => void save(settings)} />
        Enable automatic AI triage
      </label>
      {error && <p role="alert">{error}</p>}
    </div>
  </details>;
}

export function AutomaticTriageReview({ session, incident, onEvidence }: {
  session: SiteSession; incident: Alert; onEvidence: (ref: IntelligenceEvent) => void;
}) {
  const path = "automatic-triage?" + new URLSearchParams({ incidentId: incident.id });
  const supported = session.operator.automaticTriageAvailable && incident.cameraId && incident.observation.eventType !== "coverage-loss";
  const review = useRead<{ review?: TriageReview }>(supported ? session : undefined, path, 4500)?.review;
  if (!review) return null;

  return <section className="gv-details gv-intelligence gv-automatic-review" aria-label="Automatic incident review">
    <h4>Automatic incident review</h4>
    {review.state === "collecting" ? <p role="status">Collecting timeline events…</p> : <>
      {review.state === "processing" && <p role="status">Summarizing timeline events…</p>}
      {review.state === "ready" && <div aria-label="Recorded timeline summary">
        <p>Recorded facts from AI-selected events</p>
        {review.summary.map((claim, index) => <p key={index}>{claim.text} {claim.evidenceRefs.map((ref) => {
          const item = review.events.find((event) => event.ref === ref);
          return item && <button key={ref} type="button" className="gv-button"
            aria-label={`Open ${item.text} at ${clock(item.occurredAt)}`} onClick={() => onEvidence(item)}>
            {clipTime(item.occurredAt)}
          </button>;
        })}</p>)}
      </div>}
      {review.state === "timeline" && <p>{review.reason && triageReasons[review.reason]} Recorded timeline</p>}
      <h5>Key timestamps</h5>
      <ol className="gv-triage-records">{review.events.map((event) => <li key={event.ref}>
        <button type="button" className="gv-button gv-automatic-event" onClick={() => onEvidence(event)}>
          <time dateTime={event.occurredAt}>{clipTime(event.occurredAt)} UTC</time>
          <span>{event.text}</span>
        </button>
      </li>)}</ol>
    </>}
  </section>;
}

const timestamp = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC", hourCycle: "h23" });
function clipTime(value: string) {
  return timestamp.format(new Date(value));
}

export function IntelligencePosture({ session, workspace, onCamera, onBoundary }: {
  session: SiteSession; workspace: Workspace; onCamera: (id: string) => void; onBoundary: (id: string) => void;
}) {
  const device = useRead<Degradation>(session, "device-degradation", 30000);
  const fusion = useRead<FusionRead>(session, "sensor-fusion", 10000);
  return <>
    <AutomaticTriageSetting session={session} />
    {device?.enabled && <details className="gv-triage-section">
      <summary>Device degradation warnings</summary>
      <div className="gv-triage-content">
        <p>{device.cameras.flatMap(({ metrics }) => metrics).filter((metric) => metric.openedAt).length} active warnings</p>
        {!device.cameras.length && <p>Camera measurements are collecting.</p>}
        {device.cameras.map(({ cameraId, boundaries, metrics, recentRecoveries }) => <details key={cameraId}>
          <summary>{workspace.sensors.find(({ id }) => id === cameraId)?.name ?? cameraId} · {metrics.some((m) => m.openedAt) ? "Warning" : "Measurements"}</summary>
          <div className="gv-triage-content">
            <div className="gv-detail-actions">
              <button type="button" className="gv-button" onClick={() => onCamera(cameraId)}>Show camera</button>
              {boundaries.map((boundary) => <button type="button" key={boundary.id} className="gv-button" onClick={() => onBoundary(boundary.id)}>{boundary.name}</button>)}
            </div>
            {metrics.map((metric) => <details key={metric.id}>
              <summary>{metric.label} · {metric.openedAt ? "Warning active" : metricLabels[metric.state]}</summary>
              <p>{metricLabels[metric.state]}{metric.value !== null && ` · Latest ${units(metric.id, metric.value)}`}
                {metric.expected !== null && ` · Baseline ${units(metric.id, metric.expected)}`}</p>
              {metric.openedAt && <p>Opened {clock(metric.openedAt)}</p>}
              <ul>{metric.history.map((sample) => <li key={sample.at}>
                {clock(sample.at)} · {units(metric.id, sample.value)} · {sample.samples} samples
              </li>)}</ul>
            </details>)}
            {recentRecoveries.map((entry) => <p key={entry.metric + entry.at}>
              {entry.metric} · {entry.reason === "normal" ? "Recovered" : "New baseline"} · {clock(entry.at)}
            </p>)}
          </div>
        </details>)}
      </div>
    </details>}
    {fusion?.enabled && <details className="gv-triage-section">
      <summary>Gate radar status</summary>
      <div className="gv-triage-content">{fusion.gates?.map((gate) => <div key={gate.sensorId}>
        <p>{gate.sensorName} · {radarLabels[gate.state]}</p>
        <button type="button" className="gv-button" onClick={() => onBoundary(gate.boundaryId)}>
          {workspace.boundaries.find(({ id }) => id === gate.boundaryId)?.name ?? "Show boundary"}
        </button>
      </div>)}</div>
    </details>}
  </>;
}

export function GateEvidence({ session, incident, onEvidence }: {
  session: SiteSession; incident: Alert; onEvidence: (ref: IntelligenceEvent) => void;
}) {
  const path = "sensor-fusion?" + new URLSearchParams({ incidentId: incident.id });
  const snapshot = useRead<{ episode?: FusionEpisode }>(session, path, 5000);
  const [review, setReview] = useState<FusionEpisode["review"]>();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const episode = snapshot?.episode;
  if (!episode) return null;
  const decision = review ?? episode.review;

  async function save(outcome: FusionOutcome) {
    setError("");
    setSaving(true);
    try {
      const result = await siteRequest(session, "sensor-fusion", { incidentId: incident.id, outcome }) as { episode: FusionEpisode };
      setReview(result.episode.review);
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : "Gate evidence review could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  return <details className="gv-triage-section">
    <summary>Gate corroboration · {fusionLabels[episode.state]}</summary>
    <div className="gv-triage-content">
      <p>Observe mode{episode.reason && ` · ${episode.reason}`}</p>
      {episode.maintenance && <p>Maintenance was active at the event time.</p>}
      {episode.cameraAvailable === false && <p>Camera analytics availability was unverified.</p>}
      {episode.radarAvailable === false && <p>Radar availability was unverified.</p>}
      <ul>{episode.evidence.map((source) => <li key={source.sensorId + "/" + source.eventId}>
        <button type="button" className="gv-button" onClick={() => onEvidence(source)}>
          {source.summary} · {clock(source.occurredAt)}{source.trustedClock ? "" : " · Clock unverified"}
        </button>
      </li>)}</ul>
      {decision && <p role="status">{outcomes[decision.outcome]} · {decision.reviewer}</p>}
      {session.operator.role !== "viewer" && episode.state !== "collecting" && <div className="gv-detail-actions">
        {Object.entries(outcomes).map(([key, label]) => <button key={key} type="button" className="gv-button" disabled={saving}
          onClick={() => void save(key as FusionOutcome)}>{label}</button>)}
      </div>}
      {error && <p role="alert">{error}</p>}
    </div>
  </details>;
}
