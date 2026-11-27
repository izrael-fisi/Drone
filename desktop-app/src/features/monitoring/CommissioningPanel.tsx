import { useEffect, useRef, useState } from "react";
import { RecordedFeed } from "./CameraFeed";
import { gatewayStream } from "./gatewayStream";
import { canManageSite, siteRequest, type SiteSession } from "./SiteOperator";
import type { Sensor } from "./monitoringModel";

type Clip = { clipId: string; durationSeconds: number; eventOffsetSeconds: number; occurredAt: number };
type Scores = { matches: { expectedMs: number; detectedMs: number }[]; misses: number[]; extras: number[] };
type Run = { id: string; cameraId: string; publishedVersion: number; draftRevision: number; revision: number; state: string; clip: Clip;
  modelSha256: string; annotations: { startMs: number; endMs: number; expectedMs: number[] } | null;
  result?: { published: Scores; candidate: Scores }; error?: string };

export function CommissioningPanel({ session, cameras }: { session: SiteSession; cameras: Sensor[] }) {
  const [open, setOpen] = useState(false);
  const [runs, setRuns] = useState<Run[]>();
  const [enabled, setEnabled] = useState(false);
  const [draft, setDraft] = useState<{ draftRevision: number; draft: unknown }>();
  const [selected, setSelected] = useState("");
  const [camera, setCamera] = useState("");
  const [rule, setRule] = useState("");
  const [when, setWhen] = useState("");
  const [clip, setClip] = useState<Clip>();
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("");
  const [labels, setLabels] = useState<number[]>([]);
  const [labelTime, setLabelTime] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const player = useRef<HTMLVideoElement>(null);
  const supervisor = canManageSite(session);
  const active = runs?.find(({ id }) => id === selected);
  const currentCamera = cameras.find(({ id }) => id === (active?.cameraId ?? camera));
  const media = gatewayStream(currentCamera);
  const selectedClip = active?.clip ?? clip;
  const isWorkerTripwire = (item: NonNullable<Sensor["analyticsRules"]>[number]) =>
    item.kind === "line-crossing" && item.direction !== "either" && !item.cameraNative;
  const tripwires = currentCamera?.analyticsRules?.filter(isWorkerTripwire);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let timer: number;
    async function refresh() {
      try {
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(6000)]);
        const [panel, siteDraft] = await Promise.all([siteRequest(session, "commissioning-runs", undefined, signal),
          supervisor ? siteRequest(session, "configuration", undefined, signal) : undefined]);
        if (!controller.signal.aborted) {
          setRuns(panel.runs);
          setEnabled(panel.enabled);
          setDraft(siteDraft);
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Failed to load commissioning runs.");
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(refresh, 2500);
      }
    }
    void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [open, session, supervisor]);

  function select(run?: Run) {
    setSelected(run?.id ?? "");
    setStart(String((run?.annotations?.startMs ?? 0) / 1000));
    setEnd(run ? String((run.annotations?.endMs ?? Math.floor(run.clip.durationSeconds * 1000)) / 1000) : "");
    setLabels(run?.annotations?.expectedMs ?? []);
    setClip(undefined);
    setError("");
    setMessage("");
  }

  async function update(body: object, apply: (value: Run & Clip & { state: string }) => void) {
    setPending(true); setError(""); setMessage("");
    try {
      apply(await siteRequest(session, "commissioning-runs", body, AbortSignal.timeout(25_000)));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Commissioning request failed.");
    } finally {
      setPending(false);
    }
  }

  function saveRun(value: Run) {
    setRuns((previous) => [value, ...(previous ?? []).filter(({ id }) => id !== value.id)]);
  }

  function addLabel(seconds: number) {
    if (!selectedClip || !Number.isFinite(seconds) || seconds < 0 || seconds >= selectedClip.durationSeconds || labels.length >= 100) return;
    setLabels((previous) => [...new Set([...previous, Math.round(seconds * 1000)])].sort((a, b) => a - b));
    setLabelTime("");
  }

  function seek(milliseconds: number) {
    if (player.current) player.current.currentTime = milliseconds / 1000;
  }

  const frozen = active?.state === "queued" || active?.state === "running";
  const reviewSeconds = active?.annotations ? (active.annotations.endMs - active.annotations.startMs) / 1000 : 0;
  const saveAllowed = start.trim() && end.trim() && Number(end) > Number(start);
  return <details className="gv-triage-section" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Commission a tripwire</summary>
    {open && <section className="gv-triage-content gv-incident-form" aria-label="Tripwire commissioning">
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {runs === undefined && !error && <p role="status">Loading commissioning runs…</p>}
      {runs !== undefined && !enabled && <p>The gateway commissioning worker is unavailable.</p>}
      {!!runs?.length && <label>Commissioning run<select value={selected} onChange={(event) => select(runs.find(({ id }) => id === event.target.value))}>
        <option value="">New comparison</option>
        {runs.map((run) => <option key={run.id} value={run.id}>{cameras.find(({ id }) => id === run.cameraId)?.name ?? run.cameraId}
          {" · "}{new Date(run.clip.occurredAt).toISOString().slice(0, 19)} UTC · {run.state} · {run.id.slice(0, 6)}</option>)}
      </select></label>}
      {!active && supervisor && enabled && <>
        {!draft?.draft && <p>Save a candidate site draft with the same tripwire to start a comparison.</p>}
        <label>Commissioning camera<select value={camera} onChange={(event) => { setCamera(event.target.value); setRule(""); setClip(undefined); }}>
          <option value="">Choose camera</option>
          {cameras.filter((sensor) => gatewayStream(sensor) && sensor.analyticsRules?.some(isWorkerTripwire))
            .map((sensor) => <option key={sensor.id} value={sensor.id}>{sensor.name}</option>)}
        </select></label>
        <label>Directional tripwire<select value={rule} onChange={(event) => setRule(event.target.value)}>
          <option value="">Choose tripwire</option>{tripwires?.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
        </select></label>
        <label>Recording time (UTC)<input value={when} onChange={(event) => { setWhen(event.target.value); setClip(undefined); }} placeholder="2026-11-25T12:30:00Z" /></label>
        <div className="gv-detail-actions">
          <button type="button" className="gv-button" disabled={!camera || !when.trim() || pending} onClick={() => void update(
            { operation: "clip", cameraId: camera, occurredAt: when.trim() }, (value) => {
              if (value.state === "available") setClip(value);
              else setMessage(value.state === "pending" ? "The recorder is preparing the clip. Try again shortly." : "No recording is available at that time.");
            })}>Find recording</button>
          <button type="button" className="gv-button" disabled={!clip || !rule || !draft?.draft || pending} onClick={() => void update(
            { operation: "create", cameraId: camera, ruleId: rule, clipId: clip?.clipId, draftRevision: draft?.draftRevision },
            (value) => { saveRun(value); select(value); })}>Create comparison</button>
        </div>
      </>}
      {selectedClip && media && currentCamera && <RecordedFeed synchronized={player} name={currentCamera.name + " commissioning recording"}
        recording={{ cameraId: currentCamera.id, format: "mp4", eventOffsetSeconds: selectedClip.eventOffsetSeconds, durationSeconds: selectedClip.durationSeconds,
          url: new URL(`streams/${media.key}/clips/${selectedClip.clipId}.mp4`, media.base).href }} />}
      {active && <>
        <p>Published version {active.publishedVersion} · Draft revision {active.draftRevision} · Model {active.modelSha256.slice(0, 12)}</p>
        <p role="status">Run {active.state}</p>
        {active.error && <p role="alert">{active.error}</p>}
        {supervisor && <fieldset disabled={pending || frozen}>
          <legend>Reviewed interval and expected crossings</legend>
          <label>Start (seconds)<input type="number" min={0} step=".001" value={start} onChange={(event) => setStart(event.target.value)} /></label>
          <label>End (seconds)<input type="number" min={0} step=".001" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
          <label>Crossing time (seconds)<input type="number" min={0} step=".001" value={labelTime} onChange={(event) => setLabelTime(event.target.value)} /></label>
          <div className="gv-detail-actions">
            <button type="button" className="gv-button" disabled={!labelTime.trim()} onClick={() => addLabel(Number(labelTime))}>Add expected crossing</button>
            <button type="button" className="gv-button" onClick={() => addLabel(player.current?.currentTime ?? NaN)}>Mark playback position</button>
          </div>
          {!!labels.length && <ul className="gv-triage-records">{labels.map((time) => <li key={time}>
            <button type="button" className="gv-button" onClick={() => seek(time)}>{(time / 1000).toFixed(3)} s</button>
            <button type="button" className="gv-button" onClick={() => setLabels(labels.filter((label) => label !== time))}>Remove crossing at {(time / 1000).toFixed(3)} s</button>
          </li>)}</ul>}
          <div className="gv-detail-actions">
            <button type="button" className="gv-button" disabled={!saveAllowed} onClick={() => void update({
              operation: "annotate", id: active.id, revision: active.revision, startMs: Math.round(Number(start) * 1000),
              endMs: Math.round(Number(end) * 1000), expectedMs: labels,
            }, (value) => { saveRun(value); setMessage("Reviewed interval saved."); })}>Save reviewed interval</button>
            <button type="button" className="gv-button" disabled={!active.annotations || !enabled} onClick={() => void update(
              { operation: "queue", id: active.id, revision: active.revision }, saveRun)}>Run comparison</button>
          </div>
        </fieldset>}
        {active.result && Object.entries(active.result).map(([name, result]) => <section key={name} aria-label={name + " results"} className="gv-details">
          <h5>{name === "published" ? "Published rule" : "Candidate rule"}</h5>
          <p>{result.matches.length} matched · {result.misses.length} missed · {result.extras.length} extra</p>
          <p>{reviewSeconds.toFixed(3)} reviewed seconds · {(result.extras.length * 3600 / reviewSeconds).toFixed(1)} extra per reviewed hour</p>
          <div className="gv-detail-actions">
            {result.matches.map(({ expectedMs, detectedMs }, index) => <button className="gv-button" type="button" key={"m" + index}
              onClick={() => seek(detectedMs)}>Match at {(detectedMs / 1000).toFixed(3)} s · label {(expectedMs / 1000).toFixed(3)} s</button>)}
            {result.misses.map((time) => <button className="gv-button" type="button" key={"x" + time} onClick={() => seek(time)}>Miss at {(time / 1000).toFixed(3)} s</button>)}
            {result.extras.map((time, index) => <button className="gv-button" type="button" key={"e" + index} onClick={() => seek(time)}>Extra at {(time / 1000).toFixed(3)} s</button>)}
          </div>
        </section>)}
      </>}
    </section>}
  </details>;
}
