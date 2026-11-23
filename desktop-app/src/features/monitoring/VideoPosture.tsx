import { useEffect, useMemo, useState } from "react";
import { Cctv, ChevronRight, Pentagon } from "lucide-react";
import { calculateCameraCoverage, coveragePercent, type CameraCoverage } from "./cameraCoverage";
import { VIDEO_ISSUES, type Boundary, type Sensor, type VideoIssue } from "./monitoringModel";
import { gatewayStream } from "./gatewayStream";

const VIDEO_LABELS = { receiving: "Video received", unavailable: "Video unavailable", unknown: "Video unverified" } as const;
export type VideoCheck = { state: keyof typeof VIDEO_LABELS; checkedAt?: number; lastVideoAt?: number; clockOffsetSeconds?: number; clockCheckedAt?: number;
  integrity?: VideoIssue | "clear"; integritySince?: number; integrityCheckedAt?: number };
const UNKNOWN: VideoCheck = { state: "unknown" };

function videoStatusUrl(camera: Sensor) {
  const stream = gatewayStream(camera);
  return stream && new URL(`streams/${stream.key}/status.json`, stream.base).href;
}

export function useVideoHealth(cameras: Sensor[]) {
  const endpoints = JSON.stringify(cameras.flatMap((camera) => {
    const url = videoStatusUrl(camera);
    return url ? [[camera.id, url]] : [];
  }));
  const [snapshot, setSnapshot] = useState<{ endpoints: string; checks: Map<string, VideoCheck> }>();
  useEffect(() => {
    const sources = JSON.parse(endpoints) as [string, string][];
    if (!sources.length) return;
    const lifetime = new AbortController();
    let timer: number;
    async function poll() {
      const reads = await Promise.all(sources.map(async ([id, url]): Promise<[string, VideoCheck]> => {
        try {
          const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(4000)]);
          const response = await fetch(url, { signal, redirect: "error", credentials: "same-origin", cache: "no-store" });
          if (!response.ok) throw new Error("Video check unavailable");
          const report = await response.json();
          return [id, {
            state: report?.state === "receiving" || report?.state === "unavailable" ? report.state : "unknown",
            checkedAt: Number.isFinite(report?.checkedAt) ? report.checkedAt : undefined,
            lastVideoAt: Number.isFinite(report?.lastVideoAt) ? report.lastVideoAt : undefined,
            clockOffsetSeconds: Number.isFinite(report?.clockOffsetSeconds) ? report.clockOffsetSeconds : undefined,
            clockCheckedAt: Number.isFinite(report?.clockCheckedAt) ? report.clockCheckedAt : undefined,
            integrity: report?.integrity === "clear" || Object.prototype.hasOwnProperty.call(VIDEO_ISSUES, report?.integrity) ? report.integrity : undefined,
            integritySince: Number.isFinite(report?.integritySince) ? report.integritySince : undefined,
            integrityCheckedAt: Number.isFinite(report?.integrityCheckedAt) ? report.integrityCheckedAt : undefined,
          }];
        } catch { return [id, UNKNOWN]; }
      }));
      if (lifetime.signal.aborted) return;
      setSnapshot({ endpoints, checks: new Map(reads) });
      timer = window.setTimeout(poll, 5_000);
    }
    void poll();
    return () => { lifetime.abort(); window.clearTimeout(timer); };
  }, [endpoints]);
  return snapshot?.endpoints === endpoints ? snapshot.checks : undefined;
}

export function VideoHealthLabel({ camera, check }: { camera: Sensor; check?: VideoCheck }) {
  if (!camera.cameraGatewayKind) return null;
  const status = check?.state ?? "unknown";
  return <small className={status === "unavailable" ? "gv-camera-gap-text" : undefined}>{VIDEO_LABELS[status]}</small>;
}

function suspected(check?: VideoCheck) {
  return check?.state === "receiving" && check.integrity && check.integrity !== "clear" ? check.integrity : undefined;
}

export function CameraInspection({ camera, check, onChange, readOnly }: { camera: Sensor; check?: VideoCheck; onChange: (inspection?: Sensor["cameraInspection"]) => void; readOnly?: boolean }) {
  const [issue, setIssue] = useState<VideoIssue | "">(camera.cameraInspection?.issue ?? "");
  const [note, setNote] = useState(camera.cameraInspection?.note ?? "");
  const flag = suspected(check);
  const selected = issue || flag || "";
  return <details className="gv-triage-section">
    <summary>Video quality inspection</summary>
    <div className="gv-triage-content gv-incident-form">
      <p>{flag ? `Suspected ${VIDEO_ISSUES[flag].toLowerCase()}${check?.integritySince ? ` since ${new Date(check.integritySince).toLocaleString()}` : ""}.`
        : check?.integrity === "clear" ? "No issue detected in the latest sample." : "Video quality is unverified."}</p>
      {camera.cameraInspection ? <><p role="status">Confirmed {VIDEO_ISSUES[camera.cameraInspection.issue].toLowerCase()} · {new Date(camera.cameraInspection.confirmedAt).toLocaleString()}
        {camera.cameraInspection.note ? " · " + camera.cameraInspection.note : ""}</p>
        {!readOnly && <button className="gv-button" type="button" onClick={() => { onChange(); setIssue(""); setNote(""); }}>Clear confirmed issue</button>}</>
        : !readOnly && <form className="gv-incident-form" onSubmit={(event) => {
          event.preventDefault(); if (selected) onChange({ issue: selected, note: note.trim(), confirmedAt: new Date().toISOString() });
        }}>
          <label>Observed issue<select value={selected} onChange={(event) => setIssue(event.target.value as VideoIssue | "")}>
            <option value="">Choose issue</option>{Object.entries(VIDEO_ISSUES).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select></label>
          <label>Inspection note<textarea maxLength={250} rows={2} value={note} onChange={(event) => setNote(event.target.value)} /></label>
          <button className="gv-button" type="submit" disabled={!selected}>Confirm video issue</button>
        </form>}
    </div>
  </details>;
}

export function VideoPosture({ cameras, boundaries, coverage, checks, onCamera, onBoundary }: {
  cameras: Sensor[]; boundaries: Boundary[]; coverage: CameraCoverage | null; checks?: ReadonlyMap<string, VideoCheck>;
  onCamera: (id: string) => void; onBoundary: (id: string) => void;
}) {
  const [filter, setFilter] = useState<VideoCheck["state"]>("unavailable");
  const monitored = cameras.filter((camera) => camera.cameraGatewayKind);
  const issues = cameras.filter((camera) => camera.cameraInspection || suspected(checks?.get(camera.id)));
  const state = (camera: Sensor) => checks?.get(camera.id)?.state ?? "unknown";
  const degraded = useMemo(() => {
    const impaired = new Set(cameras.filter((camera) => camera.cameraInspection || checks?.get(camera.id)?.state === "unavailable").map(({ id }) => id));
    if (!coverage || !impaired.size) return [];
    try {
      const remaining = calculateCameraCoverage(cameras.filter((camera) => camera.floorId === boundaries[0]?.floorId && !impaired.has(camera.id)), boundaries);
      return boundaries.flatMap((boundary) => {
        const before = coverage.gapFractionByBoundary.get(boundary.id);
        const after = remaining.gapFractionByBoundary.get(boundary.id);
        const assignedFailure = boundary.sensorIds.some((id) => impaired.has(id));
        return before !== undefined && after !== undefined && (after - before > 0.00001 || assignedFailure)
          ? [{ boundary, before, after }] : [];
      });
    } catch { return []; }
  }, [cameras, boundaries, checks, coverage]);
  if (!monitored.length && !issues.length) return null;
  const matching = monitored.filter((camera) => state(camera) === filter);
  const failed = monitored.filter((camera) => state(camera) === "unavailable").length;
  return <section className="gv-posture-health" aria-label="Camera video health">
    <h3>Camera video health</h3>
    <p role="status">{failed} of {monitored.length} gateway cameras unavailable</p>
    <div className="gv-detail-actions">
      {(Object.keys(VIDEO_LABELS) as VideoCheck["state"][]).map((status) => <button className="gv-button" type="button" key={status}
        aria-pressed={filter === status} onClick={() => setFilter(status)}>
        {VIDEO_LABELS[status]} · {monitored.filter((camera) => state(camera) === status).length}
      </button>)}
    </div>
    {matching.map((camera) => {
      const check = checks?.get(camera.id);
      return <button key={camera.id} type="button" className="gv-posture-row" data-focus-key={"sensor" + camera.id} onClick={() => onCamera(camera.id)}>
        <Cctv aria-hidden="true" size={16} /><span><strong>{camera.name}</strong><small>{check?.lastVideoAt
          ? "Last video " + new Date(check.lastVideoAt).toLocaleString() : "No video observed yet"}</small></span><ChevronRight aria-hidden="true" size={14} />
      </button>;
    })}
    {!!issues.length && <section aria-label="Video quality issues"><h4>Video quality issues</h4>
      {issues.map((camera) => <button key={camera.id} type="button" className="gv-posture-row" onClick={() => onCamera(camera.id)}>
        <Cctv size={16} aria-hidden="true" /><span><strong>{camera.name}</strong><small>{camera.cameraInspection ? "Confirmed · " + VIDEO_ISSUES[camera.cameraInspection.issue]
          : "Suspected · " + VIDEO_ISSUES[suspected(checks?.get(camera.id))!]}</small></span><ChevronRight size={14} aria-hidden="true" />
      </button>)}
    </section>}
    {!!degraded.length && <section aria-label={failed ? "Coverage affected by video outages" : "Coverage affected by video quality"}>
      <h4>{failed ? "Coverage affected by video outages" : "Coverage affected by video quality"}</h4>
      <p>Remaining modeled coverage excludes unavailable cameras and cameras with confirmed video issues.</p>
      {degraded.map(({ boundary, before, after }) => <button key={boundary.id} type="button" className="gv-posture-row" data-focus-key={"boundary" + boundary.id}
        onClick={() => onBoundary(boundary.id)}>
        <Pentagon aria-hidden="true" size={16} /><span><strong>{boundary.name}</strong>
          <small>{coveragePercent(1 - before)} planned · {coveragePercent(1 - after)} remaining modeled coverage</small></span><ChevronRight aria-hidden="true" size={14} />
      </button>)}
    </section>}
  </section>;
}
