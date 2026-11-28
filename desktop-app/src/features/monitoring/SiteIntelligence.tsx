import { useEffect, useRef, useState } from "react";
import { siteRequest, SiteRequestError, type SiteSession } from "./SiteOperator";
import type { Alert, HandoffEvidence, IncomingObservation, Recording, SiteEvent, Workspace } from "./monitoringModel";

type Placement = "bedrock" | "on-premises";
type Capabilities = { handoff: boolean; clipReview: boolean; videoReview?: boolean; model?: string;
  placement?: Placement; clipBoundaries?: string[] };
type Claim = { text: string; evidenceRefs: string[] };
type Draft = { summary: Claim[]; nextAction: Claim; sources: HandoffEvidence[]; template?: boolean };
type Frame = { ref: string; offsetSeconds: number; occurredAt: string; eventFrame: boolean };
type ReviewRecording = { path: string; cameraId: string; durationSeconds: number; eventOffsetSeconds: number; eventAt?: number };
type ClipResult = { outcome: "observed" | "not-observed" | "unable-to-verify"; explanation: string; sources: Frame[];
  recording?: ReviewRecording; videoFallback?: VideoResult };
type VideoSegment = { startSeconds: number; endSeconds: number; startAt: string; endAt: string;
  outcome: ClipResult["outcome"]; summary: string };
type VideoResult = { segments: VideoSegment[]; unavailable?: string; pending?: boolean; recording?: ReviewRecording };
type Job = { id: string; state: "queued" | "running" | "ready" | "failed" | "approved"; message?: string };
type JobResult<T> = Job & T;
type AutomaticFrameResult = { state: "collecting" | "running" | "failed" | "ready"; message?: string } & Partial<ClipResult>;

const time = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "medium", timeZone: "UTC" });
const clock = (at: string | number) => time.format(new Date(typeof at === "string" ? at : at * 1000)) + " UTC";
const placementName = (value?: Placement) => value === "bedrock" ? "Approved cloud model" : "On-premises model";
const reviewModel = (model?: string) => model === "us.amazon.nova-2-lite-v1:0" ? "Nova 2 Lite" : undefined;

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
        if (!controller.signal.aborted && result.state === "running" && "videoFallback" in result) setJob(result);
      }
      if (!controller.signal.aborted) {
        if (result.state !== "ready") throw new Error(result.message ?? "This review is unavailable.");
        setJob(result);
      }
    } catch (issue) {
      if (!controller.signal.aborted) {
        setJob(undefined);
        setError(issue instanceof Error ? issue.message : "Site AI review is unavailable.");
      }
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
const clipQuestion = "Was a person or vehicle visible in the monitored area at the event time?";

function useAutomaticFrameReview(session: SiteSession, incident: Alert, anchor = true) {
  const eligible = anchor && session.operator.role !== "viewer" && incident.modality === "vision"
    && incident.cameraId === incident.observation.sensorId && incident.observation.analytics?.source !== "replay"
    && !["coverage-loss", "coverage-recovered"].includes(incident.observation.eventType ?? "");
  const path = "automatic-frame-review?" + new URLSearchParams({ incidentId: incident.id });
  return useRead<{ review?: AutomaticFrameResult }>(eligible ? session : undefined, path, 2000)?.review;
}

function frameStatus(review: AutomaticFrameResult) {
  if (review.state === "collecting") return "Waiting for the camera recording…";
  if (review.state === "failed") return review.message ?? "Automatic frame review was interrupted. Check the recording in replay.";
  return review.outcome ? null : "Nova is reviewing the event frame…";
}

export function AutomaticFrameSummary({ session, incident, onReplay }: { session: SiteSession; incident: Alert; onReplay: () => void }) {
  const review = useAutomaticFrameReview(session, incident);
  if (!review) return null;
  return <section className="gv-details" aria-label="Automatic camera review">
    <h4>Automatic camera review</h4>
    <div role="status">
      {review.outcome ? <p><strong>Nova · {observations[review.outcome]}</strong></p> : <p>{frameStatus(review)}</p>}
      {review.videoFallback && <p>{review.videoFallback.pending ? "Pegasus is reviewing the event clip…" :
        review.videoFallback.unavailable ?? "Pegasus clip review ready"}</p>}
    </div>
    {review.outcome && <button type="button" className="gv-button" onClick={onReplay}>Open automatic review in replay</button>}
  </section>;
}

function FrameObservations({ result, seek, automatic = false }: {
  result: Pick<ClipResult, "outcome" | "explanation" | "sources">; seek: (offset: number) => Promise<void>; automatic?: boolean;
}) {
  return <div aria-label={automatic ? "Automatic Nova frame review" : "AI draft observation"}>
    <h4>{automatic ? "Nova draft observation" : "Draft observation"} · {observations[result.outcome]}</h4>
    <p>{result.explanation}</p>
    {result.sources.map((frame) => <button key={frame.ref} type="button" className="gv-button" onClick={() => void seek(frame.offsetSeconds)}>
      Seek to {clock(frame.occurredAt)}{frame.eventFrame ? " · Event frame" : ""}
    </button>)}
  </div>;
}

function VideoObservations({ result, seek, automatic }: {
  result: VideoResult; seek: (offset: number) => Promise<void>; automatic: boolean;
}) {
  return <div aria-label={automatic ? "Automatic Pegasus clip review" : "AI video review"}>
    <h4>{automatic ? "Pegasus fallback" : "Draft video observations"}</h4>
    {result.pending ? <p role="status">Pegasus is reviewing the event clip…</p> :
      result.unavailable ? <p role="status">{result.unavailable}</p> : result.segments.length ? <ol className="gv-triage-records">
        {result.segments.map((segment, index) => <li key={index}>
          <strong>{observations[segment.outcome]}</strong>
          <p>{segment.summary}</p>
          <div className="gv-detail-actions">
            <button type="button" className="gv-button" onClick={() => void seek(segment.startSeconds)}>
              Start · <time dateTime={segment.startAt}>{clipTime(segment.startAt)} UTC</time>
            </button>
            <button type="button" className="gv-button" onClick={() => void seek(segment.endSeconds)}>
              End · <time dateTime={segment.endAt}>{clipTime(segment.endAt)} UTC</time>
            </button>
          </div>
        </li>)}
      </ol> : <p>No matching segments found. Review the original recording if needed.</p>}
  </div>;
}

export function ReviewClip({ session, incident, event, onSeek }: {
  session: SiteSession; incident: Alert; event: SiteEvent; onSeek: (recording: Recording) => void;
}) {
  const features = useRead<Capabilities>(session, "ai-jobs");
  const fallbackSetting = useRead<ReviewSetting>(features?.videoReview && features.clipReview ? session : undefined,
    "ai-review-settings", 10000);
  const automatic = useAutomaticFrameReview(session, incident, incident.observation.id === event.observation.id
    && incident.observation.sensorId === event.observation.sensorId);
  const { job, busy, error, run } = useJob<ClipResult | VideoResult>(session);
  const [confirm, setConfirm] = useState(false);
  const [seekError, setSeekError] = useState("");
  const blob = useRef<{ path: string; url: string }>();
  const seekRequest = useRef<AbortController>();
  useEffect(() => () => {
    seekRequest.current?.abort();
    if (blob.current) URL.revokeObjectURL(blob.current.url);
  }, []);
  if ((!features?.clipReview && !features?.videoReview) || !features.clipBoundaries?.includes(incident.zoneId)) return null;
  const frameModel = reviewModel(features.model);
  const fallback = job && "outcome" in job ? job.videoFallback : undefined;

  function analyze(type: "clip-review" | "video-review") {
    seekRequest.current?.abort();
    setSeekError("");
    void run({ type, incidentId: incident.id, event: { sensorId: event.observation.sensorId, id: event.observation.id } });
  }

  async function seek(offset: number, target = job?.recording ?? fallback?.recording) {
    if (!target || !/^streams\/[a-f0-9]{48}\/clips\/[a-f0-9]{32}\.mp4$/.test(target.path)) return;
    const controller = new AbortController();
    seekRequest.current?.abort();
    seekRequest.current = controller;
    setSeekError("");
    try {
      const cameraKey = target.path.split("/")[1];
      const stamp = target.eventAt ?? Date.parse(event.observation.occurredAt);
      const lookup = new URL(`streams/${cameraKey}/recording.json`, session.base);
      lookup.searchParams.set("at", String(stamp));
      const headers = !session.enterprise && session.token ? { Authorization: "Bearer " + session.token } : undefined;
      const signal = AbortSignal.any([AbortSignal.timeout(60000), controller.signal]);
      let clip;
      for (let attempt = 0; attempt < 4; attempt++) {
        const response = await fetch(lookup, { headers, signal, cache: "no-store", redirect: "error" });
        clip = response.ok ? await response.json() : undefined;
        if (clip?.state !== "pending" || attempt === 3) break;
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
      }
      if (clip?.state !== "available" || typeof clip.clipId !== "string" || !/^[a-f0-9]{32}$/.test(clip.clipId)
        || !Number.isFinite(clip.eventOffsetSeconds) || !Number.isFinite(clip.durationSeconds)) {
        throw new Error("The recorder could not supply footage for this timestamp.");
      }
      const citedOffset = offset - target.eventOffsetSeconds + clip.eventOffsetSeconds;
      if (!Number.isFinite(citedOffset) || citedOffset < -.001 || citedOffset > clip.durationSeconds + .001) {
        throw new Error("The available recording does not include this timestamp.");
      }
      const path = `streams/${cameraKey}/clips/${clip.clipId}.mp4`;
      const cacheKey = path + "|" + clip.expiresAt + "|" + clip.eventOffsetSeconds;
      let url = new URL(path, session.base).href;
      if (!session.enterprise && session.token) {
        if (blob.current?.path !== cacheKey) {
          const response = await fetch(url, { headers,
            signal: AbortSignal.any([AbortSignal.timeout(15000), controller.signal]), cache: "no-store", redirect: "error" });
          if (!response.ok) throw new Error("The recording has expired or is unavailable.");
          const content = await response.blob();
          if (controller.signal.aborted) return;
          if (blob.current) URL.revokeObjectURL(blob.current.url);
          blob.current = { path: cacheKey, url: URL.createObjectURL(content) };
        }
        url = blob.current.url;
      }
      // An end marker can coincide with the exclusive media end.
      if (controller.signal.aborted) return;
      const playableOffset = citedOffset >= clip.durationSeconds ? Math.max(0, clip.durationSeconds - .05) : Math.max(0, citedOffset);
      onSeek({ format: "mp4", url, cameraId: target.cameraId, durationSeconds: clip.durationSeconds, eventOffsetSeconds: playableOffset });
    } catch (issue) {
      if (!controller.signal.aborted) setSeekError(issue instanceof Error ? issue.message : "The recording is unavailable.");
    }
  }

  return <div className="gv-triage-content gv-intelligence">
    {automatic && <section className="gv-triage-content" aria-label="Automatic camera review in replay">
      {automatic.outcome ? <FrameObservations automatic result={{ outcome: automatic.outcome,
        explanation: automatic.explanation ?? "", sources: automatic.sources ?? [] }}
        seek={(offset) => seek(offset, automatic.recording)} /> : <p role="status">{frameStatus(automatic)}</p>}
      {automatic.videoFallback && <VideoObservations result={automatic.videoFallback}
        seek={(offset) => seek(offset, automatic.videoFallback?.recording)} automatic />}
    </section>}
    <button type="button" className="gv-button" aria-expanded={confirm} onClick={() => setConfirm(!confirm)}>Review with AI</button>
    {confirm && <div className="gv-triage-content">
      <p>{clipQuestion}</p>
      {features.clipReview && <div className="gv-triage-content">
        <p>{frameModel ? `${frameModel} · Event-time frame` : `${placementName(features.placement)} · Selected frames around the event`}</p>
        {fallbackSetting?.enabled && <p>Pegasus will review the event clip if Nova is unable to verify the frame.</p>}
        <button type="button" className="gv-button" disabled={busy} onClick={() => analyze("clip-review")}>Analyze event frames</button>
      </div>}
      {features.videoReview && <div className="gv-triage-content">
        <p>Pegasus 1.5 · Up to 12 seconds of video sent to Bedrock</p>
        <button type="button" className="gv-button" disabled={busy} onClick={() => analyze("video-review")}>Analyze video clip</button>
      </div>}
      {busy && !fallback?.pending && <p role="status">Reviewing recording…</p>}
      {error && <p role="alert">{error}</p>}
      {job && "outcome" in job && <FrameObservations result={job} seek={seek} />}
      {job && "segments" in job && <VideoObservations result={job} seek={seek} automatic={false} />}
      {fallback && <VideoObservations result={fallback} seek={seek} automatic />}
    </div>}
    {seekError && <p role="alert">{seekError}</p>}
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

type ReviewSetting = { available: boolean; enabled: boolean; revision: number; canManage: boolean };
type AIQuota = {
  available: true; canManage: boolean; dailyLimit: number; operatorReserve: number; revision: number;
  usage: { operator: number; frame: number; triage: number; fallback: number };
  operatorRemaining: number; automaticRemaining: number; protectedRemaining: number; resetsAt: string;
};
type QuotaDraft = { dailyLimit: string; operatorReserve: string; revision: number };
type TriageSetting = ReviewSetting & { placement?: Placement; model?: string; dailyLimit: number };
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

function useReviewSetting<T extends ReviewSetting>(session: SiteSession, path: string, available = true) {
  const fetched = useRead<T>(available ? session : undefined, path, 10000);
  const [saved, setSaved] = useState<{ session: SiteSession; value: T }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const local = saved?.session === session ? saved.value : undefined;
  const settings = fetched && local && local.revision > fetched.revision ? local : fetched;
  async function save(selected: T) {
    setBusy(true);
    setError("");
    try {
      const value = await siteRequest(session, path, { enabled: !selected.enabled, revision: selected.revision }) as T;
      setSaved({ session, value });
    } catch (issue) {
      if (issue instanceof SiteRequestError && issue.status === 409) setSaved(undefined);
      setError(issue instanceof Error ? issue.message : "Unable to update the site review setting.");
    } finally {
      setBusy(false);
    }
  }
  return { settings, busy, error, save };
}

function AIUsageAndQuota({ session }: { session: SiteSession }) {
  const fetched = useRead<AIQuota | { available: false }>(session, "ai-quota", 3000);
  const [saved, setSaved] = useState<AIQuota>();
  const [draft, setDraft] = useState<QuotaDraft>();
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  if (!fetched?.available) return null;
  const quota = saved && saved.revision > fetched.revision ? saved : fetched;
  const values = draft ?? { dailyLimit: String(quota.dailyLimit), operatorReserve: String(quota.operatorReserve), revision: quota.revision };
  const spent = quota.usage.operator + quota.usage.triage + quota.usage.frame + quota.usage.fallback;

  function edit(key: "dailyLimit" | "operatorReserve", value: string) {
    setDraft({ ...values, [key]: value });
    setMessage("");
    setError("");
  }

  async function save() {
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const next = await siteRequest(session, "ai-quota", {
        dailyLimit: Number(values.dailyLimit), operatorReserve: Number(values.operatorReserve), revision: values.revision,
      }) as AIQuota;
      setSaved(next);
      setDraft(undefined);
      setMessage("AI quota saved.");
    } catch (issue) {
      if (issue instanceof SiteRequestError && issue.status === 409) setDraft(undefined);
      setError(issue instanceof Error ? issue.message : "Unable to save the site AI quota.");
    } finally {
      setSaving(false);
    }
  }

  return <details className="gv-triage-section">
    <summary>AI usage and quota <span>{spent} / {quota.dailyLimit} calls today</span></summary>
    <div className="gv-triage-content">
      <dl className="gv-ai-usage" aria-label="Today's AI usage">
        <div><dt>Operator requests</dt><dd>{quota.usage.operator}</dd></div>
        <div><dt>Automatic triage</dt><dd>{quota.usage.triage}</dd></div>
        <div><dt>Automatic frame reviews</dt><dd>{quota.usage.frame}</dd></div>
        <div><dt>Automatic Pegasus fallbacks</dt><dd>{quota.usage.fallback}</dd></div>
      </dl>
      <dl className="gv-ai-usage" aria-label="Remaining AI calls">
        <div><dt>Available to operators</dt><dd>{quota.operatorRemaining}</dd></div>
        <div><dt>Reserved for operators</dt><dd>{quota.protectedRemaining}</dd></div>
        <div><dt>Available to automation</dt><dd>{quota.automaticRemaining}</dd></div>
      </dl>
      <p>Resets <time dateTime={quota.resetsAt}>{clock(quota.resetsAt)}</time>.</p>
      {quota.canManage && <form className="gv-form gv-ai-quota-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <label>Daily AI call limit
          <input type="number" required min={1} max={500} value={values.dailyLimit} disabled={saving}
            onChange={(event) => edit("dailyLimit", event.target.value)} />
        </label>
        <label>Operator reserve
          <input type="number" required min={1} max={Number(values.dailyLimit) || 1} value={values.operatorReserve} disabled={saving}
            onChange={(event) => edit("operatorReserve", event.target.value)} />
        </label>
        <p>The reserve is available only to operator requests. Changes apply to future calls and usage already recorded today still counts.</p>
        <button className="gv-button" type="submit" disabled={!draft || saving}>{saving ? "Saving…" : "Save AI quota"}</button>
        {message && <p role="status">{message}</p>}
        {error && <p role="alert">{error}</p>}
      </form>}
    </div>
  </details>;
}

function AutomaticTriageSetting({ session }: { session: SiteSession }) {
  const { settings, busy, error, save } = useReviewSetting<TriageSetting>(session, "automatic-triage", session.operator.automaticTriageAvailable === true);
  if (!settings?.available || !settings.canManage) return null;

  return <details className="gv-triage-section">
    <summary>Automatic AI triage <span>{settings.enabled ? "On" : "Off"}</span></summary>
    <div className="gv-triage-content">
      <p>Summarize new camera-linked incidents from recorded timeline events. Each review includes timestamps that open in replay.</p>
      <p>{reviewModel(settings.model) ?? placementName(settings.placement)} · Event metadata only · Up to {settings.dailyLimit} AI reviews per day</p>
      <label className="gv-checkbox">
        <input type="checkbox" checked={settings.enabled} disabled={busy} onChange={() => void save(settings)} />
        Enable automatic AI triage
      </label>
      {error && <p role="alert">{error}</p>}
    </div>
  </details>;
}

function AutomaticFrameReviewSetting({ session }: { session: SiteSession }) {
  const { settings, busy, error, save } = useReviewSetting<ReviewSetting>(session, "automatic-frame-review");
  if (!settings?.available || !settings.canManage) return null;
  return <details className="gv-triage-section">
    <summary>Automatic frame review <span>{settings.enabled ? "On" : "Off"}</span></summary>
    <div className="gv-triage-content">
      <p>Send one privacy-masked event frame to Nova for each new camera incident in an approved boundary. Operators can review the result in incident replay.</p>
      <p>Uses the site’s automatic quota. Enable automatic video fallback to use Pegasus when Nova is unable to verify the frame.</p>
      <label className="gv-checkbox">
        <input type="checkbox" checked={settings.enabled} disabled={busy} onChange={() => void save(settings)} />
        Enable automatic frame review
      </label>
      {error && <p role="alert">{error}</p>}
    </div>
  </details>;
}

function AutomaticVideoFallbackSetting({ session }: { session: SiteSession }) {
  const { settings, busy, error, save } = useReviewSetting<ReviewSetting>(session, "ai-review-settings");
  if (!settings?.available || !settings.canManage) return null;
  return <details className="gv-triage-section">
    <summary>Automatic video fallback <span>{settings.enabled ? "On" : "Off"}</span></summary>
    <div className="gv-triage-content">
      <p>When Nova is unable to verify a frame during an automatic or operator-requested review, send up to 12 seconds of privacy-masked, silent video to Pegasus.</p>
      <label className="gv-checkbox">
        <input type="checkbox" checked={settings.enabled} disabled={busy} onChange={() => void save(settings)} />
        Enable automatic video fallback
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
    <AIUsageAndQuota key={session.base + "/" + session.operator.subject} session={session} />
    <AutomaticTriageSetting session={session} />
    <AutomaticFrameReviewSetting session={session} />
    <AutomaticVideoFallbackSetting session={session} />
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
