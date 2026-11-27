import { useEffect, useRef, useState } from "react";
import { ANALYTICS_PLACEMENTS, analyticsWorkerState, EVENT_TYPES, FACTORY_SAFETY_RULES, validateBoundary, type AnalyticsKind, type AnalyticsPlacement, type AnalyticsRule, type AnalyticsThresholds, type AnalyticsWorker, type Boundary, type FactorySafetyRule, type Position, type Workspace } from "./monitoringModel";
import { CctvReview } from "./CctvReview";
import { CctvReport } from "./CctvReport";
import { CommissioningPanel } from "./CommissioningPanel";
import { NativeAnalyticsPanel } from "./NativeAnalyticsPanel";
import type { SiteSession } from "./SiteOperator";

const KINDS: [AnalyticsKind, string][] = [["line-crossing", "Tripwire"], ["zone-entry", "Zone entry"], ["zone-dwell", "Time in zone"],
  ["restricted-stop", "Vehicle stop"], ["drone-sighting", "Possible drone"]];
const OBJECTS = { person: "Person", vehicle: "Vehicle", drone: "Possible drone" } as const;
const STATES: Record<AnalyticsWorker["state"], string> = { starting: "Starting", running: "Analyzing video", idle: "Idle", "no-video": "Waiting for video",
  "model-error": "Model needs attention", "delivery-delayed": "Delivery delayed" };

function options(kind: AnalyticsKind): AnalyticsRule["object"][] {
  return kind === "drone-sighting" ? ["drone"] : kind === "restricted-stop" ? ["vehicle"] : ["person", "vehicle"];
}

function defaultPoints(kind: AnalyticsKind): Position[] {
  return kind === "line-crossing" ? [[.2, .5], [.8, .5]] : [[.15, .15], [.85, .15], [.85, .85], [.15, .85]];
}

const CLOCK = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/;

function validThresholds({ minimumScore, minimumPixels, seconds }: AnalyticsThresholds) {
  return minimumScore >= .05 && minimumScore <= 1 && seconds >= .25 && seconds <= 600
    && Number.isInteger(minimumPixels) && minimumPixels >= 4 && minimumPixels <= 2000;
}

function imagePoints(points: Position[]) {
  return points.every((point) => point.every((axis) => axis >= 0 && axis <= 1));
}

function imageRegion(points: Position[]) {
  return points.length <= 12 && imagePoints(points) && !validateBoundary(points);
}

export function analyticsRuleError(rules: AnalyticsRule[], boundaryIds: string[]) {
  return rules.some((rule) => !boundaryIds.includes(rule.boundaryId) || !rule.name.trim()) ? "Give each rule a name and an assigned boundary."
    : rules.some(({ trackOccupancy, kind, object, mode }) => trackOccupancy && (kind !== "zone-entry" || object !== "vehicle" || mode !== "observe"))
      ? "Dock occupancy uses an Observe-only vehicle zone-entry rule."
    : rules.some(({ cameraNative, kind, mode }) => cameraNative && (kind !== "line-crossing" || mode !== "observe"))
      ? "Camera-native analytics uses an Observe-only line-crossing rule."
    : rules.some(({ safetyRule, kind, object, mode, requireMovement }) => safetyRule && (
      FACTORY_SAFETY_RULES[safetyRule]?.kind !== kind || FACTORY_SAFETY_RULES[safetyRule]?.object !== object || mode !== "observe" || requireMovement))
      ? "Choose the matching Observe-only behavior for each factory safety rule."
    : rules.some((rule) => !validThresholds(rule) || rule.schedule && !validThresholds(rule.schedule.night)) ? "Check confidence, confirmation time and minimum object size."
      : rules.some(({ kind, dwellGapSeconds: gap = 0 }) => !Number.isFinite(gap) || gap < 0 || gap > 1 || gap > 0 && kind !== "zone-dwell")
        ? "Set a detection gap between zero and one second for time in zone."
      : rules.some(({ schedule }) => schedule && (!CLOCK.test(schedule.dayStartsAt) || !CLOCK.test(schedule.nightStartsAt) || schedule.dayStartsAt === schedule.nightStartsAt))
        ? "Choose distinct day and night start times."
        : rules.some(({ kind, points, exclusions }) => !(kind === "line-crossing"
          ? imagePoints(points) && points.length === 2 && points[0].some((axis, index) => axis !== points[1][index]) : imageRegion(points))
          || exclusions && (exclusions.length > 4 || !exclusions.every(imageRegion))) ? "Choose distinct points for a valid line, region, or exclusion zone." : "";
}

function RegionControl({ rule, onChange }: { rule: AnalyticsRule; onChange: (fields: Partial<AnalyticsRule>) => void }) {
  const [reference, setReference] = useState<File>();
  const [image, setImage] = useState("");
  const [selected, setSelected] = useState(0);
  const [active, setActive] = useState(-1);
  const canvas = useRef<HTMLCanvasElement>(null);
  const exclusions = rule.exclusions ?? [];
  const mask = active >= 0 && exclusions[active] !== undefined;
  const line = !mask && rule.kind === "line-crossing";
  const points = mask ? exclusions[active] : rule.points;
  useEffect(() => {
    if (!reference) { setImage(""); return; }
    const url = URL.createObjectURL(reference);
    setImage(url);
    return () => URL.revokeObjectURL(url);
  }, [reference]);
  useEffect(() => {
    const node = canvas.current;
    const context = node?.getContext("2d");
    if (!node || !context) return;
    const { width, height } = node;
    context.clearRect(0, 0, width, height);
    const drawPoint = ([x, y]: Position) => [Math.max(8, Math.min(width - 8, x * width)), Math.max(8, Math.min(height - 8, y * height))] as const;
    function shape(vertices: Position[], open: boolean, muted: boolean) {
      if (!context) return;
      context.beginPath();
      context.setLineDash(muted ? [5, 4] : []);
      vertices.forEach((point, index) => index ? context.lineTo(...drawPoint(point)) : context.moveTo(...drawPoint(point)));
      if (!open) {
        context.closePath();
        context.fillStyle = muted ? "rgba(215, 215, 225, .18)" : "rgba(255, 108, 43, .18)";
        context.fill();
      }
      context.lineWidth = 2;
      context.strokeStyle = muted ? "#dedee5" : "#ff7a41";
      context.stroke();
    }
    if (mask) shape(rule.points, rule.kind === "line-crossing", false);
    exclusions.forEach((shapePoints, index) => { if (index !== active) shape(shapePoints, false, true); });
    shape(points, line, mask);
    context.font = "600 12px system-ui";
    points.forEach((point, index) => {
      const [x, y] = drawPoint(point);
      context.beginPath();
      context.arc(x, y, 7, 0, 2 * Math.PI);
      context.fillStyle = index === selected ? "#ff7a41" : "#e5e5e5";
      context.fill();
      context.fillStyle = "#171719";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(String(index + 1), x, y);
    });
  }, [rule.points, rule.kind, rule.exclusions, points, line, selected, active, mask]);

  function setPoints(next: Position[]) {
    if (mask) onChange({ exclusions: exclusions.map((shape, index) => index === active ? next : shape) });
    else onChange({ points: next });
  }

  function update(index: number, point: Position) {
    setPoints(points.map((old, position) => position === index ? point : old));
  }
  return <div className="gv-cctv-region">
    <label>Local reference image<input type="file" accept="image/png,image/jpeg,image/webp"
      onChange={(event) => setReference(event.target.files?.[0])} /></label>
    <div className="gv-detail-actions">
      <label>Image shape<select value={mask ? active : -1} onChange={(event) => { setActive(Number(event.target.value)); setSelected(0); }}>
        <option value={-1}>{rule.kind === "line-crossing" ? "Tripwire" : "Detection region"}</option>
        {exclusions.map((_, index) => <option key={index} value={index}>Exclusion zone {index + 1}</option>)}
      </select></label>
      <button className="gv-button" type="button" disabled={exclusions.length >= 4} onClick={() => {
        const offset = exclusions.length * .06;
        const shape: Position[] = [[.3 + offset, .3], [.46 + offset, .3], [.46 + offset, .46], [.3 + offset, .46]];
        setActive(exclusions.length); setSelected(0); onChange({ exclusions: [...exclusions, shape] });
      }}>Add exclusion zone</button>
      {mask && <button className="gv-button" type="button" onClick={() => {
        setActive(-1); setSelected(0); onChange({ exclusions: exclusions.filter((_, index) => index !== active) });
      }}>Remove exclusion zone</button>}
    </div>
    <div className="gv-cctv-image">
      {image && <img src={image} alt="Camera reference" />}
      <canvas ref={canvas} width={500} height={280} aria-hidden="true" onClick={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        const unit = (n: number) => Math.round(Math.max(0, Math.min(1, n)) * 1000) / 1000;
        const point: Position = [unit((event.clientX - bounds.left) / bounds.width), unit((event.clientY - bounds.top) / bounds.height)];
        if (!line && points.length < 3) {
          setPoints([...points, point]);
          setSelected(points.length);
        } else if (points[selected]) update(selected, point);
      }} />
    </div>
    <small>{!line && points.length < 3 ? "Click the image to place three region points, or start with an editable rectangle."
      : "Choose a point, then click the image or edit its coordinates. Dashed regions are excluded from this rule."}</small>
    <div className="gv-cctv-points">
      {points.map((point, index) => <div key={index}>
        <button type="button" className="gv-button" onClick={() => setSelected(index)} aria-pressed={index === selected}>Point {index + 1}</button>
        {(["X", "Y"] as const).map((axis, coordinate) => <label key={axis}>{axis}<input type="number" min={0} max={1} step={.001}
          aria-label={`Point ${index + 1} ${axis}`} value={point[coordinate]} onFocus={() => setSelected(index)}
          onChange={(event) => { const next: Position = [...point]; next[coordinate] = event.target.valueAsNumber || 0; update(index, next); }} /></label>)}
        {!line && points.length > 3 && <button className="gv-button" type="button" aria-label={`Remove point ${index + 1}`} onClick={() => {
          setSelected(0); setPoints(points.filter((_, n) => n !== index));
        }}>Remove</button>}
      </div>)}
    </div>
    {!line && points.length < 12 && <button className="gv-button" type="button" onClick={() => {
      if (points.length < 3) {
        setPoints(defaultPoints("zone-entry")); setSelected(0); return;
      }
      const next = [...points];
      const nextPoint = points[(selected + 1) % points.length] ?? points[0];
      const current = points[selected] ?? points[0];
      next.splice(selected + 1, 0, [(current[0] + nextPoint[0]) / 2, (current[1] + nextPoint[1]) / 2]);
      setPoints(next); setSelected(selected + 1);
    }}>{points.length < 3 ? "Start with a rectangle" : "Add region point"}</button>}
  </div>;
}

function ThresholdFields({ prefix = "", durationLabel, value, onChange }: {
  prefix?: string; durationLabel: string; value: AnalyticsThresholds; onChange: (fields: Partial<AnalyticsThresholds>) => void;
}) {
  return <>
    <label>{prefix}Minimum confidence (%)<input type="number" min={5} max={100} step={1} value={Math.round(value.minimumScore * 100)}
      onChange={(event) => onChange({ minimumScore: (event.target.valueAsNumber || 0) / 100 })} /></label>
    <label>{prefix}{durationLabel}<input type="number" min={.25} max={600} step={.25} value={value.seconds}
      onChange={(event) => onChange({ seconds: event.target.valueAsNumber || 0 })} /></label>
    <label>{prefix}Minimum object size (pixels)<input type="number" min={4} max={2000} value={value.minimumPixels}
      onChange={(event) => onChange({ minimumPixels: event.target.valueAsNumber || 0 })} /></label>
  </>;
}

function RuleProfiles({ rule, timeZone, onChange }: { rule: AnalyticsRule; timeZone: string; onChange: (fields: Partial<AnalyticsRule>) => void }) {
  const { schedule, minimumScore, minimumPixels, seconds } = rule;
  const durationLabel = rule.kind === "zone-dwell" ? "Time in zone (seconds)" : "Confirmation time (seconds)";
  return <>
    <label className="gv-checkbox"><input type="checkbox" checked={!!schedule} onChange={(event) => onChange({
      schedule: event.target.checked ? { dayStartsAt: "06:00", nightStartsAt: "18:00", night: { minimumScore, minimumPixels, seconds } } : undefined,
    })} />Use day and night profiles</label>
    {schedule && <>
      <small>Profile times use {timeZone}. Each profile stays active until the other starts.</small>
      <label>Day starts<input type="time" value={schedule.dayStartsAt} onChange={(event) => onChange({ schedule: { ...schedule, dayStartsAt: event.target.value } })} /></label>
      <label>Night starts<input type="time" value={schedule.nightStartsAt} onChange={(event) => onChange({ schedule: { ...schedule, nightStartsAt: event.target.value } })} /></label>
    </>}
    <ThresholdFields prefix={schedule ? "Day · " : ""} durationLabel={durationLabel} value={rule} onChange={onChange} />
    {schedule && <ThresholdFields prefix="Night · " durationLabel={durationLabel} value={schedule.night} onChange={(fields) => onChange({ schedule: { ...schedule, night: { ...schedule.night, ...fields } } })} />}
  </>;
}

export function CctvRulesEditor({ rules, boundaries, timeZone, placement, legacy, onChange, onPlacement }: {
  rules: AnalyticsRule[]; boundaries: Boundary[]; timeZone: string; placement?: AnalyticsPlacement; legacy: boolean;
  onChange: (rules: AnalyticsRule[]) => void; onPlacement: (placement: AnalyticsPlacement | undefined) => void;
}) {
  const compute = placement ?? (legacy ? "existing" : "cloud");
  function patch(id: string, fields: Partial<AnalyticsRule>) {
    onChange(rules.map((rule) => rule.id === id ? { ...rule, ...fields } : rule));
  }
  return <details className="gv-triage-section gv-cctv-editor">
    <summary>CCTV analytics rules{rules.length ? ` · ${rules.length}` : ""}</summary>
    <div className="gv-triage-content">
      <p>Use image-space rules on a fixed camera. Assign each rule to a boundary for map alerts.</p>
      <label>Analytics compute<select value={compute} onChange={(event) => {
        const mode = event.currentTarget.value;
        onPlacement(mode === "cloud" || mode === "on-premises" ? mode : undefined);
      }}>
        <option value="cloud">Cloud (default)</option>
        <option value="on-premises">On-premises</option>
        {legacy && <option value="existing">Existing on-premises worker</option>}
      </select></label>
      <small>{compute === "cloud"
        ? "An enrolled cloud worker processes an approved camera stream over a private network."
        : "A site worker processes the stream locally and sends detection metadata to the site gateway."} Applies when the site draft is published.</small>
      {rules.map((rule) => <details key={rule.id} className="gv-triage-section">
        <summary>{rule.name || "New rule"} · {rule.mode === "alert" ? "Alerts on" : "Observe"}</summary>
        <div className="gv-triage-content gv-incident-form">
          <label>Rule name<input maxLength={48} value={rule.name} onChange={(event) => patch(rule.id, { name: event.target.value })} /></label>
          <label>Behavior<select value={rule.kind} disabled={!!rule.safetyRule} onChange={(event) => {
            const kind = event.target.value as AnalyticsKind;
            patch(rule.id, { kind, object: options(kind).includes(rule.object) ? rule.object : options(kind)[0],
              points: defaultPoints(kind), direction: "either", seconds: kind === "zone-dwell" || kind === "restricted-stop" ? 12 : 1,
              dwellGapSeconds: kind === "zone-dwell" ? .75 : undefined,
              trackOccupancy: kind === "zone-entry" ? rule.trackOccupancy : undefined,
              cameraNative: kind === "line-crossing" ? rule.cameraNative : undefined,
              requireMovement: kind === "restricted-stop" ? rule.requireMovement : undefined,
              scaleCrossingBuffer: kind === "line-crossing" && rule.object === "person" ? rule.scaleCrossingBuffer : undefined,
              minimumScore: kind === "drone-sighting" ? .15 : .35, minimumPixels: kind === "drone-sighting" ? 12 : 20 });
          }}>{KINDS.map(([kind, text]) => <option key={kind} value={kind}>{text}</option>)}</select></label>
          <label>Object<select value={rule.object} disabled={!!rule.safetyRule} onChange={(event) => {
            const object = event.target.value as AnalyticsRule["object"];
            patch(rule.id, { object, scaleCrossingBuffer: object === "person" ? rule.scaleCrossingBuffer : undefined,
              trackOccupancy: object === "vehicle" ? rule.trackOccupancy : undefined });
          }}>
            {options(rule.kind).map((value) => <option key={value} value={value}>{OBJECTS[value]}</option>)}</select></label>
          {rule.kind === "restricted-stop" && !rule.safetyRule && <label className="gv-checkbox">
            <input type="checkbox" checked={!!rule.requireMovement}
              onChange={(event) => patch(rule.id, { requireMovement: event.target.checked })} />
            Require movement before the stop
          </label>}
          <label>Alert boundary<select value={rule.boundaryId} onChange={(event) => patch(rule.id, { boundaryId: event.target.value })}>
            {!boundaries.some(({ id }) => id === rule.boundaryId) && <option value={rule.boundaryId}>Choose an assigned boundary</option>}
            {boundaries.map((area) => <option key={area.id} value={area.id}>{area.name}{area.enabled ? "" : " (paused)"}</option>)}</select></label>
          <label>Rule mode<select value={rule.mode} disabled={!!rule.trackOccupancy || !!rule.safetyRule || !!rule.cameraNative} onChange={(event) => patch(rule.id, { mode: event.target.value as AnalyticsRule["mode"] })}>
            <option value="observe">Observe only</option><option value="alert">Create site alerts</option></select></label>
          {rule.kind === "line-crossing" && <label className="gv-checkbox">
            <input type="checkbox" checked={!!rule.cameraNative} onChange={(event) => patch(rule.id, {
              cameraNative: event.target.checked || undefined, ...(event.target.checked ? { mode: "observe" } : {}),
            })} />Use camera-native ONVIF events
          </label>}
          {rule.cameraNative && <small>The enrolled camera supplies crossings from its configured line. Verify the line on that camera.</small>}
          {rule.kind === "zone-entry" && rule.object === "vehicle" && <label className="gv-checkbox">
            <input type="checkbox" checked={!!rule.trackOccupancy} onChange={(event) => patch(rule.id, {
              trackOccupancy: event.target.checked || undefined, ...(event.target.checked ? { mode: "observe" } : {}),
            })} />Track dock occupancy
          </label>}
          {rule.kind === "drone-sighting" && <p>Review possible drone sightings in Observe mode during commissioning.</p>}
          {rule.kind === "line-crossing" && <label>Crossing direction<select value={rule.direction}
            onChange={(event) => patch(rule.id, { direction: event.target.value as AnalyticsRule["direction"] })}>
            <option value="either">Both directions</option><option value="positive">Toward the right of point 1 → 2</option>
            <option value="negative">Toward the left of point 1 → 2</option></select></label>}
          {rule.kind === "line-crossing" && rule.object === "person" && <>
            <label className="gv-checkbox">
              <input type="checkbox" checked={!!rule.scaleCrossingBuffer}
                onChange={(event) => patch(rule.id, { scaleCrossingBuffer: event.target.checked || undefined })} />
              Scale crossing buffer to person size
            </label>
            <small>Use a narrower buffer for people who appear small in the camera image.</small>
          </>}
          <RuleProfiles rule={rule} timeZone={timeZone} onChange={(fields) => patch(rule.id, fields)} />
          {rule.kind === "zone-dwell" && <>
            <label>Maximum detection gap (seconds)<input type="number" min={0} max={1} step={.05} value={rule.dwellGapSeconds ?? 0}
              onChange={(event) => patch(rule.id, { dwellGapSeconds: event.target.valueAsNumber || 0 })} /></label>
            <small>The timer pauses when the object drops out of view. Set zero to restart the timer after any gap.</small>
          </>}
          <RegionControl rule={rule} onChange={(fields) => patch(rule.id, fields)} />
          <button type="button" className="gv-button" onClick={() => onChange(rules.filter(({ id }) => id !== rule.id))}>Remove rule</button>
        </div>
      </details>)}
      <button type="button" className="gv-button" disabled={!boundaries.length || rules.length >= 12} onClick={() => onChange([...rules, {
        id: crypto.randomUUID(), name: "Zone entry", kind: "zone-entry", object: "person", mode: "observe", boundaryId: boundaries[0].id,
        points: defaultPoints("zone-entry"), direction: "either", minimumScore: .35, minimumPixels: 20, seconds: 1,
      }])}>Add CCTV rule</button>
      <button type="button" className="gv-button" disabled={!boundaries.length || rules.length >= 12} onClick={() => onChange([...rules, {
        id: crypto.randomUUID(), name: "Dock monitoring", kind: "zone-entry", object: "vehicle", mode: "observe", boundaryId: boundaries[0].id,
        points: [], direction: "either", minimumScore: .35, minimumPixels: 20, seconds: 2, trackOccupancy: true,
      }])}>Add dock monitoring</button>
      {Object.entries(FACTORY_SAFETY_RULES).map(([safetyRule, preset]) =>
        <button key={safetyRule} type="button" className="gv-button" disabled={!boundaries.length || rules.length >= 12}
          onClick={() => onChange([...rules, { id: crypto.randomUUID(), ...preset, safetyRule: safetyRule as FactorySafetyRule,
            mode: "observe", boundaryId: boundaries[0].id, points: [], direction: "either", minimumScore: .35, minimumPixels: 20,
          }])}>Add {preset.name.toLowerCase()}</button>)}
      {!boundaries.length && <small>Assign this camera to a boundary to add rules.</small>}
    </div>
  </details>;
}

export function CctvAnalyticsPosture({ workspace, session, version, onCamera }: { workspace: Workspace; session: SiteSession; version: number; onCamera: (id: string) => void }) {
  const [clock, setClock] = useState(Date.now());
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, []);
  const cameras = workspace.sensors.filter((camera) => camera.modality === "vision" && camera.analyticsRules?.length);
  const accessKey = JSON.stringify([session.base, session.operator.subject, session.operator.role, session.operator.cameras]);
  return <details className="gv-triage-section" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>CCTV analytics</summary>
    <div className="gv-triage-content">
      {!cameras.length && <p>Assign camera analytics rules in a site configuration draft.</p>}
      {cameras.map((camera) => {
        const placement = camera.analyticsPlacement;
        const compute = placement ? ANALYTICS_PLACEMENTS[placement] : "Existing on-premises worker";
        const reported = workspace.analyticsWorkers?.[camera.id];
        const worker = reported?.placement === placement ? reported : undefined;
        const waiting = placement === "cloud" ? "Waiting for cloud worker" : placement === "on-premises" ? "Waiting for on-premises worker" : "Waiting for worker";
        const state = analyticsWorkerState(worker, placement, version, clock);
        const nativeOnly = camera.analyticsRules?.every(({ cameraNative }) => cameraNative);
        const status = nativeOnly ? "Camera-native events" : state === "waiting" ? waiting : state === "stale" ? "Worker stopped reporting"
          : state === "old-policy" ? "Waiting for published rules" : STATES[state];
        return <button className="gv-posture-row" type="button" key={camera.id} onClick={() => onCamera(camera.id)}>
          <span><strong>{camera.name}</strong><small>{nativeOnly ? status : compute + " · " + status} · {camera.analyticsRules?.length} rules</small>
            {!nativeOnly && worker?.models.length ? <small>{worker.models.join(" · ")} · {worker.fps} fps{worker.pending ? ` · ${worker.pending} queued` : ""}</small> : null}</span>
        </button>;
      })}
      {open && <div key={accessKey} className="gv-triage-content">
        <CctvReview session={session} version={version} cameras={workspace.sensors.filter((camera) => camera.modality === "vision")} />
        <CctvReport session={session} publishedVersion={version} />
        <CommissioningPanel session={session} cameras={cameras} />
        {cameras.some(({ analyticsRules }) => analyticsRules?.some(({ cameraNative }) => cameraNative)) &&
          <NativeAnalyticsPanel session={session} cameras={cameras} onCamera={onCamera} />}
      </div>}
    </div>
  </details>;
}

export function AnalyticsEvidenceDetails({ evidence, event }: { evidence: NonNullable<Workspace["alerts"][number]["observation"]["analytics"]>; event?: keyof typeof EVENT_TYPES }) {
  return <dl className="gv-cctv-evidence" aria-label="CCTV detection details">
    <div><dt>Rule</dt><dd>{evidence.ruleName ?? (event ? EVENT_TYPES[event] : evidence.ruleId)}</dd></div>
    <div><dt>Detected object</dt><dd>{OBJECTS[evidence.object]} · {Math.round(evidence.score * 100)}% model score</dd></div>
    {evidence.dwellSeconds !== undefined && <div><dt>Observed time in zone</dt><dd>{Number(evidence.dwellSeconds.toFixed(2))} s</dd></div>}
    {evidence.movementObserved && <div><dt>Vehicle movement</dt><dd>Observed before the stop</dd></div>}
    <div><dt>Source</dt><dd>Camera image · {evidence.model}</dd></div>
    <div><dt>Published version</dt><dd>{evidence.policyVersion}</dd></div>
    {evidence.placement && <div><dt>Analytics compute</dt><dd>{ANALYTICS_PLACEMENTS[evidence.placement]}</dd></div>}
    {evidence.profile && evidence.profile !== "all-day" && <div><dt>Rule profile</dt><dd>{evidence.profile === "day" ? "Day" : "Night"}</dd></div>}
  </dl>;
}
