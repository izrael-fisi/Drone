import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, MapPin, Play, Video, X } from "lucide-react";
import { RecordedFeed } from "./CameraFeed";
import { deviceIcon } from "./DeviceInventory";
import { observationKey, MODALITY_LABEL, type Alert, type Recording, type SiteEvent, type Workspace } from "./monitoringModel";
import { incidentTimeline, recordingsForEvent, REPLAY_WINDOW_MINUTES } from "./replayTimeline";
import { RecordingComparison } from "./RecordingComparison";
import { RECORDING_COVERAGE } from "./IncidentPanel";
import type { VideoCheck } from "./VideoPosture";
import { AnalyticsEvidenceDetails } from "./CctvAnalytics";

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" });
const eventLabel = { access: "Door event", vision: "Camera event", rf: "RF detection" };

export function IncidentReplay({ alert, workspace, mapView, videoChecks, initialEvent, onJump, onBack, onClose }: {
  alert: Alert; workspace: Workspace; mapView: boolean; onJump: (event: SiteEvent) => void; onBack: () => void; onClose: () => void;
  videoChecks?: ReadonlyMap<string, VideoCheck>; initialEvent?: SiteEvent;
}) {
  const anchorKey = observationKey(alert.observation);
  const [active, setActive] = useState<{ key: string; request: number; recording?: Recording; focusEvent?: boolean }>(() => ({
    key: initialEvent ? observationKey(initialEvent.observation) : anchorKey, request: 0, recording: initialEvent?.observation.recording,
  }));
  const [comparing, setComparing] = useState(false);
  const recordingCoverage = workspace.recordingRecoveryEnabled ? workspace.recordingCoverage?.find((entry) => entry.incidentId === alert.id) : undefined;
  const player = useRef<HTMLDivElement>(null);
  const eventHeading = useRef<HTMLHeadingElement>(null);
  const entries = useMemo(() => {
    const timeline = incidentTimeline({ alerts: workspace.alerts, siteEvents: workspace.siteEvents }, alert);
    const clips = timeline.filter((event) => event.observation.recording);
    return timeline.map((event) => {
      const recordings = recordingsForEvent(event, clips);
      return { event, key: observationKey(event.observation), recording: recordings[0], recordings };
    });
  }, [workspace.alerts, workspace.siteEvents, alert]);
  const selected = entries.find((entry) => entry.key === active.key) ?? entries.find((entry) => entry.key === anchorKey);
  const selectedIndex = selected ? entries.indexOf(selected) : -1;
  const previous = entries[selectedIndex - 1];
  const next = entries[selectedIndex + 1];
  const recording = selected?.key === active.key ? active.recording : undefined;
  const camera = recording && workspace.sensors.find((sensor) => sensor.id === recording.cameraId);
  const angles = selected?.recordings.filter((entry) => (entry.durationSeconds ?? 0) - entry.eventOffsetSeconds > .1);
  const first = angles?.[0], second = angles?.[1];

  useEffect(() => {
    if (active.focusEvent) eventHeading.current?.focus();
    else if (mapView && recording && !document.activeElement?.matches(".gv-replay-navigation button")) {
      player.current?.scrollIntoView({ block: "nearest" });
    }
  }, [active, mapView, recording]);

  function jump({ event, key, recording }: typeof entries[number], focusEvent: boolean) {
    setComparing(false);
    setActive((current) => ({ key, recording, focusEvent, request: current.request + 1 }));
    onJump(event);
  }

  const position = selected?.event.observation.position;

  return <div className="gv-incident gv-replay">
    <header className="gv-panel-heading">
      <button type="button" className="gv-incident-back" onClick={onBack}><ChevronLeft size={16} /> Back to incident</button>
      <button type="button" className="gv-icon-button" aria-label="Close replay" onClick={onClose}><X size={17} /></button>
    </header>
    <section className="gv-details gv-incident-summary">
      <h3>Incident replay</h3>
      <p className="gv-replay-anchor">{alert.observation.summary}</p>
      <p className="gv-replay-window">Site activity within {REPLAY_WINDOW_MINUTES} minutes of the incident · UTC</p>
      {recordingCoverage && <p className="gv-recording-continuity" role="status"><Video size={15} aria-hidden="true" />{RECORDING_COVERAGE[recordingCoverage.state]}</p>}
    </section>
    {selected && <section className="gv-details gv-replay-selection" aria-label="Selected replay event">
      <h4 ref={eventHeading} tabIndex={-1}>{selected.event.observation.summary}</h4>
      {selected.event.observation.analytics && <AnalyticsEvidenceDetails evidence={selected.event.observation.analytics} event={selected.event.observation.eventType} />}
      <p>{position ? selected.event.boundaries.map((boundary) => boundary.name).join(" · ") || "Reported location" : "Location unavailable"}</p>
      {!mapView && position && <p>Latitude {position[1].toFixed(5)}° · Longitude {position[0].toFixed(5)}°</p>}
      <p className="gv-sr-only" role="status">{active.request > 0 && !active.focusEvent ? selected.event.observation.summary : ""}</p>
      <div className="gv-detail-actions gv-replay-navigation">
        <button type="button" className="gv-button" disabled={!previous} onClick={() => jump(previous, selectedIndex === 1)}>
          <ChevronLeft size={14} /> Previous event
        </button>
        <button type="button" className="gv-button" disabled={!next} onClick={() => jump(next, selectedIndex === entries.length - 2)}>
          Next event <ChevronRight size={14} />
        </button>
      </div>
      {first && second && <button type="button" className="gv-button" aria-pressed={comparing} onClick={() => setComparing(!comparing)}>
        {comparing ? "Return to one camera" : "Compare camera angles"}
      </button>}
      {comparing && first && second ? <RecordingComparison key={selected.key} recordings={[first, second]} sensors={workspace.sensors}
        eventTime={selected.event.observation.occurredAt} videoChecks={videoChecks} /> : recording ? <div ref={player} className="gv-replay-player">
        <p className="gv-replay-clip"><Video size={15} /> {selected.event.observation.recording ? "Event clip" : "Matched clip"} · {camera?.name ?? recording.cameraId}</p>
        <RecordedFeed key={active.request} recording={recording} name={(camera?.name ?? "Replay") + " clip"} />
      </div> : selected.recording
        ? <button type="button" className="gv-button" onClick={() => jump(selected, true)}><Play size={14} /> Play from event</button>
        : <p className="gv-replay-gap"><Video size={14} /> No recording for this event</p>}
    </section>}
    <ol className="gv-replay-timeline" aria-label="Replay timeline">
      {entries.map((entry) => {
        const { event, key, recording: clip } = entry;
        const Icon = deviceIcon[event.modality];
        return <li key={key}>
          <button type="button" aria-pressed={selected?.key === key} onClick={(event) => jump(entry, !mapView || event.detail === 0)}>
            <span className="gv-replay-event-icon"><Icon aria-hidden="true" size={16} /></span>
            <span className="gv-replay-event-body">
              <time dateTime={event.observation.occurredAt}>{dateTime.format(new Date(event.observation.occurredAt))} UTC</time>
              <strong>{event.observation.summary}</strong>
              <small>{eventLabel[event.modality]} · {event.sensorName || MODALITY_LABEL[event.modality]}</small>
              {event.observation.gatewayReceivedAt && <small>Gateway received {dateTime.format(new Date(event.observation.gatewayReceivedAt * 1000))} UTC</small>}
              <span className="gv-replay-event-badges">
                {key === anchorKey && <small>Incident alert</small>}
                {event.observation.position && <small><MapPin size={12} /> {mapView ? "Map" : "Location recorded"}</small>}
                {clip ? <small><Video size={12} /> Clip</small> : !mapView && <small>No recording</small>}
              </span>
            </span>
          </button>
        </li>;
      })}
    </ol>
  </div>;
}
