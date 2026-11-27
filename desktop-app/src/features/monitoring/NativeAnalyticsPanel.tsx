import { useEffect, useState } from "react";
import { ObservationRecording } from "./CctvReview";
import { siteRequest, type SiteSession } from "./SiteOperator";
import type { Sensor, SiteEvent } from "./monitoringModel";

type Capture = { cameraId: string; state: "supported" | "unsupported" | "disconnected"; checkedAt: number; lastEventAt: number | null; issue?: string };
type NativeState = { cameras: Capture[]; events: SiteEvent[] };
const LABELS = { supported: "Supported", unsupported: "Unsupported", disconnected: "Disconnected" } as const;
const TIMES = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" });

export function NativeAnalyticsPanel({ session, cameras, onCamera }: { session: SiteSession; cameras: Sensor[]; onCamera: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<NativeState>();
  const [error, setError] = useState("");
  const [recording, setRecording] = useState("");
  const enabled = cameras.filter(({ analyticsRules }) => analyticsRules?.some(({ cameraNative }) => cameraNative));

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let timer: number;
    async function refresh() {
      try {
        const response = await siteRequest(session, "camera-native", undefined,
          AbortSignal.any([controller.signal, AbortSignal.timeout(6000)]));
        if (!controller.signal.aborted) { setData(response); setError(""); }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not read native camera events.");
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(refresh, 3000);
      }
    }
    void refresh();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [open, session]);

  return <details className="gv-triage-section" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Camera-native analytics</summary>
    {open && <section className="gv-triage-content" aria-label="Camera-native analytics">
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">Checking native camera sources…</p>}
      {data && <ul className="gv-triage-records">{enabled.map((camera) => {
        const item = data.cameras.find(({ cameraId }) => cameraId === camera.id);
        return <li key={camera.id}><strong>{camera.name} · {item ? LABELS[item.state] : "Waiting for enrollment"}</strong>
          <small>ONVIF Profile M · Observe</small>
          {item?.lastEventAt && <small>Last valid event {TIMES.format(item.lastEventAt * 1000)} UTC</small>}
          {item?.issue && <small>{item.issue}</small>}
          <button type="button" className="gv-button" onClick={() => onCamera(camera.id)}>Open camera</button>
        </li>;
      })}</ul>}
      {!!data?.events.length && <ol aria-label="Native camera events" className="gv-triage-records">{data.events.map((event) => {
        const id = event.observation.id, camera = cameras.find(({ id }) => event.cameraId === id);
        return <li key={event.cameraId + "/" + id}>
          <strong>{event.observation.summary}</strong><small>{event.sensorName} · ONVIF Profile M · {TIMES.format(new Date(event.observation.occurredAt))} UTC</small>
          {camera && <button type="button" className="gv-button" aria-expanded={recording === id}
            onClick={() => setRecording(recording === id ? "" : id)}>{recording === id ? "Close recording" : "Find recording"}</button>}
          {recording === id && <ObservationRecording event={event} camera={camera} />}
        </li>;
      })}</ol>}
    </section>}
  </details>;
}
