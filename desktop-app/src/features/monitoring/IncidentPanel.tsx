import { AutomaticFrameSummary, AutomaticTriageReview, DraftHandoff, GateEvidence, HandoffEvidenceLinks, type IntelligenceEvent } from "./SiteIntelligence";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Bell, Check, ChevronLeft, ChevronRight, CircleAlert, History, Video, X } from "lucide-react";
import { CameraFeed, RecordedFeed } from "./CameraFeed";
import { INCIDENT_DISPOSITIONS, type Alert, type IncidentAction, type IncidentActivity, type IncidentDisposition, type RecordingCoverage, type ResponseStep, type Sensor, type SiteEvent } from "./monitoringModel";
import { RESPONSE_STEPS, type incidentCameras } from "./incidentModel";
import { IncidentEvidence } from "./IncidentEvidence";
import type { IncidentArchive } from "./incidentArchive";
import type { SiteSession } from "./SiteOperator";
import { XProtectPlayback } from "./XProtectCamera";
import { AnalyticsEvidenceDetails } from "./CctvAnalytics";
import { pairKind } from "./incidentQueue";
import { IncidentProcedure } from "./ResponseProcedures";

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" });
const statuses = { new: "New", acknowledged: "Investigating", resolved: "Resolved" };
export const RECORDING_COVERAGE: Record<RecordingCoverage["state"], string> = { awaiting: "Awaiting recorder retrieval", retrieving: "Checking the recorder",
  available: "Recorder footage verified", gap: "Recording gap at event time", unavailable: "Recorder check unavailable", unsupported: "Recorder retrieval unsupported" };

function stepsFor(alert: Alert, companion?: Alert) {
  if (alert.observation.eventType === "drone-sighting") return {
    verify: "Review the camera view and rule evidence for a possible drone",
    contact: "Notify the designated site contact if the sighting is corroborated",
    document: "Record the sighting and any corroborating observations",
  };
  if ([alert, companion].some((entry) => entry && (pairKind(entry) === "door" || pairKind(entry) === "held"))) return {
    verify: "Compare the door event with the linked camera", contact: "Contact the site lead for the affected access point",
    document: "Record the door status and disposition",
  };
  if (alert.observation.eventType === "tamper") return {
    verify: "Inspect live video for an obstructed or changed view", contact: "Send the camera location to the site technician",
    document: "Record the coverage impact and follow-up",
  };
  return RESPONSE_STEPS;
}

function activityText(entry: IncidentActivity, steps: Record<ResponseStep, string>) {
  switch (entry.kind) {
    case "acknowledge": return "Acknowledged alert";
    case "claim": return "Took ownership";
    case "note": return entry.text;
    case "resolve": return INCIDENT_DISPOSITIONS[entry.disposition];
    case "checklist": return `${entry.done ? "Completed" : "Reopened"} · ${steps[entry.step]}`;
    case "handoff": return `Handoff · ${entry.nextAction}${entry.note ? "\n" + entry.note : ""}`;
    case "accept-handoff": return "Accepted handoff";
    case "bookmark": return `Bookmarked evidence · ${entry.label}`;
    case "procedure-step": return `${entry.label ?? entry.step} · ${entry.answer}${entry.text ? "\n" + entry.text : ""}`;
    case "procedure-override": return `Supervisor override · ${entry.reason}`;
  }
}

export function IncidentPanel({ alert, camera, cameras, archive, companion, related, replay, timeline, operatorNameOverride, readOnly, recordingCoverage, siteSession, onSelectRelated, onReplayEvent, onReplay, onBack, backLabel, onClose, onAction, onCase, onIntelligenceEvidence }: {
  related: Alert[]; onSelectRelated: (id: string) => void;
  onReplay: () => void; replay?: ReactNode;
  onCase?: () => void;
  onIntelligenceEvidence?: (ref: IntelligenceEvent) => void;
  alert: Alert; camera?: Sensor; onBack: () => void; backLabel?: string; onClose: () => void; onAction: (action: IncidentAction) => void;
  cameras: ReturnType<typeof incidentCameras>;
  archive?: IncidentArchive;
  timeline: SiteEvent[];
  operatorNameOverride?: string; readOnly?: boolean;
  recordingCoverage?: RecordingCoverage;
  siteSession?: SiteSession;
  companion?: Alert; onReplayEvent: (event: Alert) => void;
}) {
  const [operatorName, setOperatorName] = useState(alert.owner ?? "");
  const [changingOwner, setChangingOwner] = useState(false);
  const [note, setNote] = useState("");
  const [disposition, setDisposition] = useState<IncidentDisposition | "">("");
  const [playback, setPlayback] = useState<"live" | "recording" | null>(null);
  const [viewingCameras, setViewingCameras] = useState(false);
  const [cameraId, setCameraId] = useState("");
  const [nextAction, setNextAction] = useState(alert.handoff?.nextAction ?? "");
  const [handoffNote, setHandoffNote] = useState(alert.handoff?.note ?? "");
  const [aiJobId, setAiJobId] = useState<string>();
  useEffect(() => setAiJobId(undefined), [alert.handoff?.occurredAt]);
  const [handoffDue, setHandoffDue] = useState(alert.handoff?.dueAt?.slice(0, 16) ?? "");
  const [recipient, setRecipient] = useState("");
  const investigation = useRef<HTMLHeadingElement>(null);
  const recording = alert.observation.recording ?? (archive?.state === "available" ? archive.recording : undefined);
  const hasLive = !!(camera?.cameraFeedUrl || camera?.cameraGridUrl || camera?.demo);
  const resolved = alert.status === "resolved";
  const canEdit = !resolved && !readOnly && (!operatorNameOverride || alert.owner === operatorNameOverride);
  const actor = operatorNameOverride ?? operatorName;
  const receivingOperator = operatorNameOverride ?? recipient;
  const [lng, lat] = alert.observation.position;
  const incidentFeed = cameras.find(({ camera }) => camera.id === cameraId) ?? cameras[0];
  const steps = stepsFor(alert, companion);
  const dueAt = handoffDue && Number.isFinite(Date.parse(handoffDue + "Z")) ? new Date(handoffDue + "Z").toISOString() : undefined;

  function claim(event: FormEvent) {
    event.preventDefault();
    if (!actor.trim()) return;
    investigation.current?.focus();
    onAction({ kind: "claim", name: actor });
    setChangingOwner(false);
  }

  if (replay) return <>{replay}</>;
  return <div className="gv-incident">
    <header className="gv-panel-heading">
      <button type="button" className="gv-incident-back" onClick={onBack}><ChevronLeft size={16} /> {backLabel ?? "Back to alerts"}</button>
      <button type="button" className="gv-icon-button" aria-label="Close incident" onClick={onClose}><X size={17} /></button>
    </header>
    <section className="gv-details gv-incident-summary">
      <div className="gv-detail-label">
        <span className={"gv-severity-label is-" + alert.severity.toLowerCase()}>{alert.severity} priority</span>
        <span>{statuses[alert.status]}</span>
      </div>
      <h3>{alert.observation.summary}</h3>
      <dl>
        <div><dt>Boundary</dt><dd>{alert.zoneName}</dd></div>
        {alert.responseTeam && <div><dt>Response team</dt><dd>{alert.responseTeam}</dd></div>}
        <div><dt>Source</dt><dd>{alert.sensorName}</dd></div>
        <div><dt>Observed</dt><dd>{dateTime.format(new Date(alert.observation.occurredAt))} UTC</dd></div>
        <div><dt>{alert.locationBasis === "device" ? alert.modality === "vision" ? "Camera location" : "Door location" : "Location"}</dt>
          <dd className="gv-mono">{lat.toFixed(5)}°, {lng.toFixed(5)}°</dd></div>
        <div><dt>Event origin</dt><dd>{alert.origin}</dd></div>
        {alert.observation.vendor && <div><dt>Vendor source</dt><dd>{alert.observation.vendor.provider === "genetec" ? "Genetec Security Center" : "Milestone XProtect"}
          {" · "}{alert.observation.vendor.eventType}</dd></div>}
        {alert.affectedCameras && <div><dt>Affected cameras</dt><dd>{alert.affectedCameras.join(", ")}</dd></div>}
        {!!alert.affectedDependencies?.length && <div><dt>Associated infrastructure</dt><dd>{alert.affectedDependencies.join(", ")}</dd></div>}
        {!!alert.maintenanceCameras?.length && <div><dt>Under maintenance</dt><dd>{alert.maintenanceCameras.join(", ")}</dd></div>}
        {alert.coverageRecoveredAt && <div><dt>Coverage restored</dt><dd>{dateTime.format(new Date(alert.coverageRecoveredAt))} UTC</dd></div>}
        {alert.configurationVersion !== undefined && <div><dt>Configuration version</dt><dd>Version {alert.configurationVersion}</dd></div>}
      </dl>
      <button type="button" className="gv-button gv-open-replay" onClick={() => { setPlayback(null); onReplay(); }}><History size={15} /> Replay timeline</button>
      {onCase && <button type="button" className="gv-button" onClick={onCase}>Open incident case workspace</button>}
    </section>
    {siteSession && !!alert.configurationVersion &&
      <AutomaticFrameSummary session={siteSession} incident={alert} onReplay={() => { setPlayback(null); onReplay(); }} />}
      {siteSession && onIntelligenceEvidence && !!alert.configurationVersion &&
        <AutomaticTriageReview session={siteSession} incident={alert} onEvidence={onIntelligenceEvidence} />}

    {alert.observation.analytics && <section className="gv-details" aria-label="CCTV analytics evidence">
      <h4>CCTV analytics evidence</h4><AnalyticsEvidenceDetails evidence={alert.observation.analytics} event={alert.observation.eventType} />
    </section>}
    {companion?.correlation && <section className="gv-details gv-incident-related" aria-label="Correlated source events">
      <h4>Why these events were grouped</h4>
      <p>The forced door and linked camera line crossing share {alert.zoneName}. They occurred {Math.abs(companion.correlation.seconds).toFixed(1)} seconds apart.</p>
      {[alert, companion].map((entry) => <button type="button" className="gv-posture-row" key={entry.id} onClick={() => onReplayEvent(entry)}>
        <History size={15} aria-hidden="true" /><span><strong>{entry.observation.summary}</strong><small>{entry.sensorName} · {dateTime.format(new Date(entry.observation.occurredAt))} UTC</small></span><ChevronRight size={14} aria-hidden="true" />
      </button>)}
    </section>}
    {!!related.length && <section className="gv-details gv-incident-related" aria-label="Other alerts at this location">
      <h4><Bell size={15} /> Other alerts at this location</h4>
      {related.map((other) => <button type="button" className="gv-posture-row" key={other.id} onClick={() => onSelectRelated(other.id)}>
        <CircleAlert size={15} /><span><strong>{other.observation.summary}</strong>
          <small>{other.zoneName} · {other.severity} priority</small></span><ChevronRight size={14} />
      </button>)}
    </section>}
    <section className="gv-details gv-incident-evidence" aria-label="Camera evidence">
      <h4><Video size={15} /> Camera evidence</h4>
      {recordingCoverage && <p role="status">{RECORDING_COVERAGE[recordingCoverage.state]} · {dateTime.format(new Date(recordingCoverage.checkedAt * 1000))} UTC</p>}
      {siteSession && alert.configurationVersion && camera?.cameraProvider === "Milestone XProtect" && <XProtectPlayback session={siteSession} alert={alert} />}
      <p>{camera?.name ?? (recording ? "Recording source" : "No camera linked to this source.")}</p>
      {(recording || camera) && <div className="gv-detail-actions">
        {recording && <button type="button" className="gv-button" aria-pressed={playback === "recording"}
          onClick={() => setPlayback(playback === "recording" ? null : "recording")}>Event recording</button>}
        {hasLive && <button type="button" className="gv-button" aria-pressed={playback === "live"}
          onClick={() => setPlayback(playback === "live" ? null : "live")}>Live camera</button>}
      </div>}
      {!recording && camera && (!archive || archive.state === "unsupported" ? <small>No event recording supplied.</small>
        : <small role="status">{archive.state === "missing" ? "Recorder has no matching footage." : archive.state === "error"
          ? "Recorder footage unavailable." : "Retrieving footage from recorder…"}</small>)}
      {camera && !hasLive && <small>Live feed unavailable.</small>}
      {playback === "recording" && recording && <RecordedFeed recording={recording} name={(camera?.name ?? "Incident") + " recording"} />}
      {playback === "live" && camera && <CameraFeed camera={camera} />}
      {!!cameras.length && <button type="button" className="gv-button" aria-expanded={viewingCameras} onClick={() => {
        setPlayback(null); setViewingCameras(!viewingCameras);
      }}>{viewingCameras ? "Hide incident cameras" : "Open incident cameras"}</button>}
      {viewingCameras && incidentFeed && <>
        <label className="gv-incident-field">Incident camera<select value={incidentFeed.camera.id} onChange={(event) => setCameraId(event.target.value)}>
          {cameras.map(({ camera, reason }) => <option value={camera.id} key={camera.id}>{camera.name} · {reason}</option>)}
        </select></label>
        {!playback && <CameraFeed camera={incidentFeed.camera} />}
      </>}
      <IncidentEvidence alert={alert} archive={archive} timeline={timeline} siteSession={siteSession} onAction={onAction} />
    </section>
    <section className="gv-details gv-incident-case" aria-label="Operator decisions">
      <h4 ref={investigation} tabIndex={-1}><CircleAlert size={15} /> Investigation</h4>
      <p>{alert.owner ? "Owner · " + alert.owner : "Unassigned"}
        {!!alert.owner && canEdit && !changingOwner && !operatorNameOverride && <button className="gv-incident-text-button" type="button" onClick={() => setChangingOwner(true)}>Change owner</button>}
      </p>
      {!resolved && !readOnly && (!alert.owner || changingOwner) && <form className="gv-incident-form" onSubmit={claim}>
        {!operatorNameOverride && <label>Operator name<input value={operatorName} maxLength={80} required onChange={(event) => setOperatorName(event.target.value)} /></label>}
        <button className="gv-button gv-primary" type="submit" disabled={!actor.trim() || actor.trim() === alert.owner}>Take ownership</button>
      </form>}
      {alert.status === "new" && !readOnly && <button className="gv-button" type="button" onClick={() => {
        investigation.current?.focus();
        onAction({ kind: "acknowledge" });
      }}><Check size={15} /> Acknowledge alert</button>}
      {canEdit && alert.owner && <>
        <form className="gv-incident-form" onSubmit={(event) => { event.preventDefault(); if (note.trim()) { onAction({ kind: "note", text: note }); setNote(""); } }}>
          <label>Investigation note<textarea rows={3} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} /></label>
          <button type="submit" className="gv-button" disabled={!note.trim()}>Add note</button>
        </form>
        <form className="gv-incident-form" onSubmit={(event) => {
          event.preventDefault();
          if (disposition) {
            investigation.current?.focus();
            onAction({ kind: "resolve", disposition });
          }
        }}>
          <div className="gv-incident-field">
            <label htmlFor="gv-incident-disposition">Disposition</label>
            <select id="gv-incident-disposition" value={disposition} onChange={(event) => setDisposition(event.target.value as IncidentDisposition | "")}>
              <option value="">Select disposition</option>
              {Object.entries(INCIDENT_DISPOSITIONS).map(([key, value]) => <option value={key} key={key}>{value}</option>)}
            </select>
          </div>
          <button className="gv-button gv-primary" type="submit" disabled={!disposition}>Resolve alert</button>
        </form>
      </>}
      {alert.disposition && <p>Disposition · {INCIDENT_DISPOSITIONS[alert.disposition]}</p>}
      {siteSession && !!alert.configurationVersion && <div id="gv-source-procedure" tabIndex={-1}>
        <IncidentProcedure session={siteSession} incidentId={alert.id} activityCount={alert.activity?.length ?? 0}
          canEdit={canEdit && !!alert.owner} readOnly={!!readOnly || resolved} onAction={onAction} onReplay={onReplay} />
      </div>}
      {siteSession && onIntelligenceEvidence && !!alert.configurationVersion &&
        <GateEvidence session={siteSession} incident={alert} onEvidence={onIntelligenceEvidence} />}
      {alert.owner && <details className="gv-triage-section">
        <summary>Response checklist and handoff</summary>
        <div className="gv-triage-content">
          {(Object.entries(steps) as [ResponseStep, string][]).map(([step, text]) =>
            <label className="gv-checkbox" key={step}><input type="checkbox" checked={!!alert.responseSteps?.[step]} disabled={!canEdit}
              onChange={(event) => onAction({ kind: "checklist", step, done: event.target.checked })} />{text}</label>)}
          {alert.handoff && <p role="status">Next action · {alert.handoff.nextAction} · {alert.handoff.operator}
            {alert.handoff.dueAt && ` · Due ${dateTime.format(new Date(alert.handoff.dueAt))} UTC`}
            {alert.handoff.acceptedBy && ` · Accepted by ${alert.handoff.acceptedBy}`}</p>}
          {!resolved && !readOnly && alert.handoff && !alert.handoff.acceptedBy && operatorNameOverride !== alert.owner && <form className="gv-incident-form" onSubmit={(event) => {
            event.preventDefault(); onAction({ kind: "accept-handoff", name: receivingOperator });
          }}>
            {!operatorNameOverride && <label>Receiving operator<input required maxLength={80} value={recipient} onChange={(event) => setRecipient(event.target.value)} /></label>}
            <button type="submit" className="gv-button" disabled={!receivingOperator.trim() || receivingOperator.trim() === alert.owner}>Accept handoff</button>
          </form>}
          {canEdit && siteSession && onIntelligenceEvidence && !!alert.configurationVersion &&
            <DraftHandoff session={siteSession} incident={alert} onEvidence={onIntelligenceEvidence}
              onApply={(id, summary, next) => { setAiJobId(id); setHandoffNote(summary); setNextAction(next); }} />}
          {canEdit && <form className="gv-incident-form" onSubmit={(event) => {
            event.preventDefault();
            onAction({ kind: "handoff", nextAction, note: handoffNote, dueAt, aiJobId });
          }}>
            <label>Next action<input required maxLength={180} value={nextAction} onChange={(event) => setNextAction(event.target.value)} /></label>
            <label>Handoff note<textarea rows={2} maxLength={500} value={handoffNote} onChange={(event) => setHandoffNote(event.target.value)} /></label>
            <label>Due (UTC)<input type="datetime-local" value={handoffDue} onChange={(event) => setHandoffDue(event.target.value)} /></label>
            {aiJobId && <div>
              <p role="status">Draft applied. Review and save the handoff to share it.</p>
              <button type="button" className="gv-button" onClick={() => {
                setAiJobId(undefined); setHandoffNote(alert.handoff?.note ?? ""); setNextAction(alert.handoff?.nextAction ?? "");
              }}>Discard applied draft</button>
            </div>}
            <button type="submit" className="gv-button" disabled={!nextAction.trim() || nextAction.trim() === alert.handoff?.nextAction && handoffNote.trim() === alert.handoff?.note && dueAt === alert.handoff?.dueAt}>Save handoff</button>
          </form>}
        </div>
      </details>}
      {!!alert.activity?.length && <ol className="gv-incident-activity" aria-label="Incident activity">
        {[...alert.activity].reverse().map((entry, index) => <li key={entry.occurredAt + index}
          id={entry.sourceSequence ? "gv-source-action-" + entry.sourceSequence : undefined} tabIndex={-1}>
          <small>{entry.operator ?? "Local operator"} · {dateTime.format(new Date(entry.occurredAt))} UTC</small>
          <span>{activityText(entry, steps)}</span>
          {entry.kind === "handoff" && entry.aiDraft && onIntelligenceEvidence &&
            <HandoffEvidenceLinks sources={entry.aiDraft.sources} onEvidence={onIntelligenceEvidence} />}
        </li>)}
      </ol>}
    </section>
  </div>;
}
