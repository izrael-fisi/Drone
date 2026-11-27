import { useEffect, useState } from "react";
import { IncidentReplay } from "./IncidentReplay";
import { siteRequest, type SiteSession } from "./SiteOperator";
import type { Alert, SiteEvent, Workspace } from "./monitoringModel";

type Episode = { id: number; doorId: string; cameraId: string; boundaryId: string; version: number; startedAt: number;
  state: "possible-extra" | "insufficient" | "normal"; reason: string; grants: number; inboundTracks: number; events: SiteEvent[] };

const LABELS = { "possible-extra": "Possible extra passage", insufficient: "Insufficient evidence", normal: "No extra passage observed" } as const;
const DATES = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" });

export function DoorPassageReview({ session, workspace, mapView, onJump, onClose }: {
  session: SiteSession; workspace: Workspace; mapView: boolean; onJump: (event: SiteEvent) => void; onClose: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState("review");
  const [result, setResult] = useState<{ status: string; episodes: Episode[] }>();
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<number>();
  const episodes = result?.status === status ? result.episodes : undefined;
  const episode = episodes?.find(({ id }) => id === selected);
  const lead = episode?.events.find(({ observation }) => observation.eventType === "door-opened") ?? episode?.events[0];
  const route = (event: SiteEvent) => { onJump(event); };

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let timer: number;
    async function load() {
      try {
        const value = await siteRequest(session, "door-passages?status=" + status, undefined,
          AbortSignal.any([controller.signal, AbortSignal.timeout(6000)]));
        if (!controller.signal.aborted) { setResult({ status, episodes: value.episodes }); setError(""); }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not load passage reviews.");
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(load, 3000);
      }
    }
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [session, open, status]);

  function close() { setSelected(undefined); onClose(); }
  const zone = lead?.boundaries.find(({ id }) => id === episode?.boundaryId);
  const alert: Alert | undefined = episode && lead && zone && lead.observation.position ? {
    id: "passage-review-" + episode.id, observation: { ...lead.observation, position: lead.observation.position },
    zoneId: zone.id, zoneName: zone.name, severity: "Medium", modality: lead.modality, sensorName: lead.sensorName,
    origin: "Site gateway", status: "new", cameraId: episode.cameraId, configurationVersion: episode.version,
  } : undefined;
  return <details className="gv-triage-section" onToggle={(event) => {
    setOpen(event.currentTarget.open);
    if (!event.currentTarget.open) close();
  }}>
    <summary>Door passage review</summary>
    {open && <section className="gv-triage-content gv-incident-form" aria-label="Door passage review">
      <label>Passage outcome<select value={status} onChange={(event) => { setStatus(event.target.value); close(); }}>
        <option value="review">Needs review</option><option value="all">All episodes</option>
      </select></label>
      {error && <p role="alert">{error}</p>}
      {!episodes && !error && <p role="status">Loading passage episodes…</p>}
      {episodes?.length === 0 && <p>No passage episodes match this filter.</p>}
      {episodes?.map((row) => <button key={row.id} type="button" className="gv-posture-row" aria-pressed={selected === row.id} onClick={() => {
        setSelected(row.id);
        if (row.events[0]) route(row.events[0]);
      }}>
        <span><strong>{LABELS[row.state]}</strong><small>{workspace.sensors.find(({ id }) => id === row.doorId)?.name ?? row.doorId} ·
          {" "}{DATES.format(row.startedAt * 1000)} UTC</small></span>
      </button>)}
      {episode && <section className="gv-details" aria-label="Selected door passage">
        <p>Observe · Version {episode.version} · {episode.grants} grants · {episode.inboundTracks} inbound tracks</p>
        {episode.reason && <p>{episode.reason}</p>}
        {alert && <IncidentReplay key={episode.id} alert={alert} workspace={workspace} explicitTimeline={episode.events}
          mapView={mapView} onJump={route} onBack={close} onClose={close} />}
      </section>}
    </section>}
  </details>;
}
