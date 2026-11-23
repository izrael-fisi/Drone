import { useEffect, useId, useRef, useState, type FormEvent, type Ref } from "react";
import { Cctv, ChevronLeft, ChevronRight, MapPin, Maximize2, Pause, Play, Plus, Radar, Search, SlidersHorizontal, Star, Video } from "lucide-react";
import { CameraFeed, RecordedFeed } from "./CameraFeed";
import { HealthIndicator } from "./DeviceHealth";
import { CameraInspection, VideoHealthLabel, type VideoCheck } from "./VideoPosture";
import { SiteCameraViewer, type SiteSession } from "./SiteOperator";
import { CameraGatewayAccess } from "./CameraGatewayAccess";
import { activeMaintenance, type ApplianceHealth, type HealthStatus } from "./applianceHealth";
import { coveragePercent, type CameraCoverage } from "./cameraCoverage";
import { labelKey, observationKey, type Alert, type Boundary, type Recording, type Sensor, type Workspace } from "./monitoringModel";
import { readCameraWall, saveCameraWall, WALL_LAYOUTS, wallKey, wallLayout } from "./cameraWallModel";
import "./cameraWorkspace.css";

const SECTIONS = { wall: "Live wall", directory: "Camera directory", coverage: "Coverage" } as const;
type Section = keyof typeof SECTIONS;
const DATE_UTC = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });
const HEALTH_ISSUES = new Set<HealthStatus>(["offline", "stale", "unknown"]);
const hasFeed = (camera: Sensor) => !!(camera.cameraFeedUrl || camera.cameraGridUrl);

interface CameraWorkspaceProps {
  workspace: Workspace;
  healthById: ReadonlyMap<string, ApplianceHealth>;
  videoChecks?: ReadonlyMap<string, VideoCheck>;
  now: number;
  compact: boolean;
  coverage: CameraCoverage | null;
  initialCamera?: string;
  onEdit: (camera?: Sensor) => void;
  onMapCamera: (camera: Sensor) => void;
  onMapCoverage: (boundary?: Boundary) => void;
  onIncident: (alert: Alert) => void;
  onVerify: (id: string) => void;
  onInspect: (id: string, inspection?: Sensor["cameraInspection"]) => void;
  siteSession?: SiteSession;
  enterpriseWall?: { preferences?: ReturnType<typeof readCameraWall>; save: (value: ReturnType<typeof readCameraWall>) => void };
  inspectionsReadOnly?: boolean;
}

export function CameraWorkspace({ workspace, healthById, videoChecks, now, compact, coverage, initialCamera, onEdit, onMapCamera, onMapCoverage, onIncident, onVerify, onInspect, siteSession, inspectionsReadOnly, enterpriseWall }: CameraWorkspaceProps) {
  const destination = useRef<HTMLHeadingElement>(null);
  const origin = useRef<string>();
  const pendingFocus = useRef(false);
  const title = useId();
  const [section, setSection] = useState<Section>("wall");
  const [cameraId, setCameraId] = useState(initialCamera);
  const [preferences, setPreferences] = useState(() => enterpriseWall?.preferences ?? readCameraWall(!enterpriseWall));
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("");
  const [boundary, setBoundary] = useState("");
  const [status, setStatus] = useState("all");
  const [showControls, setShowControls] = useState<boolean>();
  const [paused, setPaused] = useState(false);
  const [saving, setSaving] = useState(false);
  const [viewName, setViewName] = useState("");
  const [message, setMessage] = useState("");
  const cameras = workspace.sensors.filter((device) => device.modality === "vision").sort((a, b) => a.name.localeCompare(b.name));
  const focused = cameras.find((camera) => camera.id === cameraId);
  const savedView = preferences.walls.find(({ name }) => wallKey(name) === preferences.view);
  const selectedBoundary = workspace.boundaries.find(({ id }) => id === boundary);
  const groupNames = new Map(workspace.groups.map(({ id, name }) => [id, name]));
  const words = labelKey(query.trim()).split(/\s+/).filter(Boolean);
  const matching = cameras.filter((camera) => {
    if (group && (camera.groupId ?? "ungrouped") !== group) return false;
    if (selectedBoundary && !selectedBoundary.sensorIds.includes(camera.id)) return false;
    const fields = labelKey([camera.name, camera.id, camera.mapLabel, camera.cameraProvider, camera.cameraChannel, ...camera.labels].join(" "));
    if (!words.every((word) => fields.includes(word))) return false;
    if (section === "directory") {
      if (status === "needs-feed") return !hasFeed(camera) && !camera.demo;
      if (status === "needs-coverage") return !camera.cameraView;
      if (status === "health") return HEALTH_ISSUES.has(healthById.get(camera.id)?.status ?? "unknown");
    }
    return true;
  });
  const wallCameras = matching.filter((camera) => preferences.view === "pinned" ? preferences.pinned.includes(camera.id)
    : savedView ? savedView.cameras.includes(camera.id) : true);
  const pageCount = Math.max(1, Math.ceil(wallCameras.length / preferences.layout));
  const currentPage = Math.min(page, pageCount - 1);
  const currentCameras = wallCameras.slice(currentPage * preferences.layout, (currentPage + 1) * preferences.layout);
  const name = viewName.trim();
  const duplicate = !!name && preferences.walls.some((view) => wallKey(view.name) === wallKey(name));
  const controlsExpanded = showControls ?? !compact;

  useEffect(() => { destination.current?.focus(); }, []);

  useEffect(() => {
    if (enterpriseWall) enterpriseWall.save(preferences);
    else saveCameraWall(preferences);
  }, [preferences, enterpriseWall]);

  useEffect(() => {
    if (!pendingFocus.current) return;
    pendingFocus.current = false;
    const previous = !focused && origin.current ? document.getElementById(origin.current) : null;
    (previous ?? destination.current)?.focus();
  }, [focused?.id, section]);

  function changeSection(next: Section) {
    origin.current = undefined;
    setCameraId(undefined);
    setSection(next);
    setSaving(false);
  }

  function chooseCamera(camera: Sensor, button: HTMLButtonElement) {
    origin.current = button.id;
    pendingFocus.current = true;
    setCameraId(camera.id);
  }

  function chooseView(view: string) {
    const layout = preferences.walls.find(({ name }) => wallKey(name) === view)?.layout;
    setPreferences((current) => ({ ...current, view, layout: layout ?? current.layout }));
    setPage(0);
    setQuery("");
    setGroup("");
    setBoundary("");
  }

  function pinCamera(id: string) {
    setPreferences((current) => ({ ...current, pinned: current.pinned.includes(id)
      ? current.pinned.filter((value) => value !== id) : [...current.pinned, id] }));
  }

  function saveView(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name || duplicate || !wallCameras.length) return;
    const view = { name, cameras: wallCameras.map((camera) => camera.id), layout: preferences.layout };
    setPreferences((current) => ({ ...current, walls: [...current.walls, view], view: wallKey(name) }));
    setSaving(false);
    setViewName("");
    setMessage("Saved wall " + name + ".");
    document.getElementById(title + "-view")?.focus();
  }

  const filters = <div className="gv-camera-filters">
    <label>Find cameras <span className="gv-camera-search"><Search aria-hidden="true" size={15} />
      <input type="search" value={query} placeholder="Name, label, channel…" onChange={(event) => { setQuery(event.target.value); setPage(0); }} />
    </span></label>
    <label>Group<select value={group} onChange={(event) => { setGroup(event.target.value); setPage(0); }}>
      <option value="">All groups</option>{workspace.groups.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
      <option value="ungrouped">Ungrouped</option>
    </select></label>
    <label>Boundary<select value={boundary} onChange={(event) => { setBoundary(event.target.value); setPage(0); }}>
      <option value="">All boundaries</option>{workspace.boundaries.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
    </select></label>
    {section === "directory" && <label>Camera status<select value={status} onChange={(event) => setStatus(event.target.value)}>
      <option value="all">Any status</option><option value="needs-feed">Needs feed</option>
      <option value="needs-coverage">Missing field of view</option><option value="health">Reporting issues</option>
    </select></label>}
  </div>;

  return <section id="gv-cameras" aria-labelledby={title} className="gv-camera-workspace">
    <header className="gv-camera-workspace-header">
      <div className="gv-camera-workspace-title"><span><Cctv aria-hidden="true" size={22} /></span>
        <div><h2 id={title}>Cameras</h2><p>{cameras.length} {cameras.length === 1 ? "camera" : "cameras"} at this site</p></div>
      </div>
      <div className="gv-camera-header-actions">
        <label className="gv-camera-view-picker">Camera view
          <select value={focused ? "" : section} onChange={(event) => changeSection(event.target.value as Section)}>
            {focused && <option value="" disabled>{focused.name}</option>}
            {(Object.keys(SECTIONS) as Section[]).map((key) => <option key={key} value={key}>{SECTIONS[key]}</option>)}
          </select>
        </label>
        <button type="button" className="gv-button gv-primary" onClick={() => onEdit()}><Plus size={16} aria-hidden="true" /> Add camera</button>
      </div>
    </header>
    <div className="gv-camera-workspace-body">
      <p className="gv-sr-only" role="status">{message}</p>
      {focused ? <CameraFocus key={focused.id} camera={focused} workspace={workspace} health={healthById.get(focused.id)} now={now} onInspect={(inspection) => onInspect(focused.id, inspection)} siteSession={siteSession} inspectionsReadOnly={inspectionsReadOnly}
        videoCheck={videoChecks?.get(focused.id)} onVerify={() => onVerify(focused.id)}
        heading={destination} onBack={() => { pendingFocus.current = true; setCameraId(undefined); }}
        onEdit={() => onEdit(focused)} onMap={() => onMapCamera(focused)} onIncident={onIncident} />
      : section === "coverage" ? <CameraCoveragePane cameras={cameras} boundaries={workspace.boundaries} coverage={coverage} heading={destination}
        onMap={onMapCoverage} />
      : <>
        <div className="gv-camera-content-title">
          <div><h3 tabIndex={-1} ref={destination}>{SECTIONS[section]}</h3>
            <p>{section === "wall" ? `${wallCameras.length} cameras in this wall` : `${matching.length} of ${cameras.length} cameras`}</p></div>
          <div className="gv-camera-header-actions">
            <button className="gv-button" type="button" aria-expanded={controlsExpanded} aria-controls={title + "-filters"}
              onClick={() => setShowControls(!controlsExpanded)}><SlidersHorizontal size={15} aria-hidden="true" /> {section === "wall" ? "Filters & layout" : "Filters"}</button>
            {section === "wall" && <button className="gv-button" type="button" onClick={() => setPaused((current) => !current)}>
              {paused ? <Play size={15} aria-hidden="true" /> : <Pause size={15} aria-hidden="true" />}
              {paused ? "Resume wall" : "Pause wall"}</button>}
          </div>
        </div>
        <div id={title + "-filters"} className="gv-camera-toolbar" hidden={!controlsExpanded}>
          {filters}
          {section === "wall" && <div className="gv-wall-options">
            <label>Wall view<select id={title + "-view"} value={preferences.view} onChange={(event) => chooseView(event.target.value)}>
              <option value="all">All cameras</option><option value="pinned">Pinned cameras</option>
              {preferences.walls.map((view) => <option key={wallKey(view.name)} value={wallKey(view.name)}>{view.name}</option>)}
            </select></label>
            <label>Cameras per page<select value={preferences.layout} onChange={(event) => {
              const layout = wallLayout(Number(event.target.value));
              setPreferences((current) => ({ ...current, layout })); setPage(0);
            }}>{WALL_LAYOUTS.map((size) => <option key={size} value={size}>{size}</option>)}</select></label>
            <button type="button" className="gv-button" aria-expanded={saving} disabled={!wallCameras.length} onClick={() => setSaving(!saving)}>Save wall as</button>
            {savedView && <button type="button" className="gv-button" onClick={() => {
              chooseView("all");
              setPreferences((current) => ({ ...current, walls: current.walls.filter((view) => view.name !== savedView.name) }));
              setMessage("Removed saved wall " + savedView.name + ".");
              document.getElementById(title + "-view")?.focus();
            }}>Remove saved wall</button>}
          </div>}
          {section === "wall" && saving && <form className="gv-wall-options" onSubmit={saveView}>
            <label>Wall name<input autoFocus required maxLength={48} value={viewName} onChange={(event) => setViewName(event.target.value)} /></label>
            <button type="submit" className="gv-button gv-primary" disabled={!name || duplicate || !wallCameras.length}>Save wall</button>
            {duplicate && <p role="status">A wall already uses that name.</p>}
          </form>}
        </div>
        {section === "wall" ? <>
          {!currentCameras.length && <p className="gv-camera-empty">{preferences.view === "pinned" && !preferences.pinned.length
            ? "Pin cameras from the wall or directory to build a watchlist." : "No cameras match this view."}</p>}
          {currentCameras.length > 0 && <div className="gv-wall-grid" data-layout={preferences.layout} aria-label="Live camera tiles" role="group">
            {currentCameras.map((camera) => <article key={camera.id} className="gv-wall-tile" aria-label={camera.name}>
              <div className="gv-wall-tile-header"><h4>{camera.name}</h4>
                <button type="button" className="gv-icon-button" id={title + "-live-" + camera.id}
                  aria-label={`Focus ${camera.name}`} title="Focus camera" onClick={(event) => chooseCamera(camera, event.currentTarget)}>
                  <Maximize2 size={16} aria-hidden="true" /></button>
                <CameraPin camera={camera} pinned={preferences.pinned.includes(camera.id)} onPin={() => pinCamera(camera.id)} />
              </div>
              {paused ? <div className="gv-wall-paused"><Pause size={24} aria-hidden="true" /><span>Wall paused</span></div>
                : <CameraFeed camera={camera} preferGrid announce={false} />}
              <div className="gv-wall-tile-footer">
                <span className="gv-wall-health"><small>Device</small><HealthIndicator health={healthById.get(camera.id) ?? { status: "unknown" }} /></span>
                <VideoHealthLabel camera={camera} check={videoChecks?.get(camera.id)} />
                <small>{camera.cameraGridUrl ? "Lower-bandwidth feed" : camera.cameraGatewayKind ? `${camera.cameraGatewayKind.toUpperCase()} via gateway`
                  : camera.cameraFeedUrl ? "Primary feed" : camera.demo ? "Simulation" : "Needs feed"}</small>
              </div>
            </article>)}
          </div>}
          {pageCount > 1 && <div className="gv-wall-pagination" aria-label="Camera wall pages" role="group">
            <button type="button" className="gv-button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={16} aria-hidden="true" /> Previous</button>
            <span role="status">Page {currentPage + 1} of {pageCount}</span>
            <button type="button" className="gv-button" disabled={currentPage === pageCount - 1} onClick={() => setPage(currentPage + 1)}>Next <ChevronRight size={16} aria-hidden="true" /></button>
          </div>}
        </> : <div className="gv-camera-directory">
          {!matching.length && <p className="gv-camera-empty">No cameras match these filters.</p>}
          {matching.map((camera) => <article className="gv-directory-camera" key={camera.id} aria-label={camera.name}>
            <div className="gv-directory-camera-name"><Cctv aria-hidden="true" size={20} /><div><h4>{camera.name}</h4>
              <p>{groupNames.get(camera.groupId ?? "") ?? "Ungrouped"}{camera.cameraProvider && ` · ${camera.cameraProvider}`}</p></div></div>
            <div className="gv-directory-camera-state">
              <HealthIndicator health={healthById.get(camera.id) ?? { status: "unknown" }} />
              {activeMaintenance(camera, now) && <small>Maintenance active</small>}
              <small>{hasFeed(camera) ? "Feed configured" : camera.demo ? "Demo feed" : "Needs feed"}</small>
              <VideoHealthLabel camera={camera} check={videoChecks?.get(camera.id)} />
              <small>{camera.cameraView ? "Field of view set" : "Missing field of view"}</small>
            </div>
            <div className="gv-directory-actions">
              <button type="button" className="gv-button" id={title + "-directory-" + camera.id}
                aria-label={`View ${camera.name}`} onClick={(event) => chooseCamera(camera, event.currentTarget)}>View</button>
              <button type="button" className="gv-button" aria-label={`Edit ${camera.name}`} onClick={() => onEdit(camera)}>Edit</button>
              <CameraPin camera={camera} pinned={preferences.pinned.includes(camera.id)} onPin={() => pinCamera(camera.id)} />
            </div>
          </article>)}
        </div>}
      </>}
    </div>
  </section>;
}

function CameraPin({ camera, pinned, onPin }: { camera: Sensor; pinned: boolean; onPin: () => void }) {
  return <button type="button" className="gv-icon-button gv-camera-pin" aria-pressed={pinned} aria-label={`Pin ${camera.name}`}
    title={pinned ? "Remove from pinned cameras" : "Pin camera"} onClick={onPin}><Star size={17} aria-hidden="true" /></button>;
}

function CameraFocus({ camera, workspace, health, videoCheck, now, heading, onBack, onEdit, onMap, onIncident, onVerify, onInspect, siteSession, inspectionsReadOnly }: {
  camera: Sensor; workspace: Workspace; health?: ApplianceHealth; now: number;
  heading: Ref<HTMLHeadingElement>;
  onBack: () => void; onEdit: () => void; onMap: () => void; onIncident: (alert: Alert) => void;
  videoCheck?: VideoCheck; onVerify: () => void; onInspect: (inspection?: Sensor["cameraInspection"]) => void;
  siteSession?: SiteSession;
  inspectionsReadOnly?: boolean;
}) {
  const [clip, setClip] = useState<{ key: string; summary: string; recording: Recording }>();
  const [preferGrid, setPreferGrid] = useState(false);
  const recordingTitle = useRef<HTMLHeadingElement>(null);
  const recordingOrigin = useRef<HTMLButtonElement>();
  const boundaries = workspace.boundaries.filter(({ sensorIds }) => sensorIds.includes(camera.id));
  const doors = workspace.sensors.filter((sensor) => sensor.modality === "access" && sensor.linkedCameraId === camera.id);
  const incidents = workspace.alerts.filter((alert) => alert.cameraId === camera.id || alert.observation.sensorId === camera.id || alert.observation.recording?.cameraId === camera.id).slice(0, 6);
  const clips = [...new Map([
    ...(workspace.siteEvents ?? []).map((event) => event.observation),
    ...workspace.alerts.map((alert) => alert.observation),
  ].filter((event) => event.recording?.cameraId === camera.id).map((event) => [observationKey(event), event])).values()]
    .sort((a, b) => b.occurredAt.localeCompare(a.occurredAt)).slice(0, 6);
  useEffect(() => { if (clip) recordingTitle.current?.focus(); }, [clip]);

  return <>
    <div className="gv-camera-content-title gv-camera-focus-title">
      <div><h3 ref={heading} tabIndex={-1}>{camera.name}</h3>
        <p>{workspace.groups.find((group) => group.id === camera.groupId)?.name ?? "Ungrouped"}</p></div>
      <div className="gv-camera-header-actions">
        <button type="button" className="gv-button" onClick={onBack}><ChevronLeft size={16} aria-hidden="true" /> Back to cameras</button>
        <button type="button" className="gv-button" onClick={onMap}><MapPin size={16} aria-hidden="true" /> Show on map</button>
        <button type="button" className="gv-button" onClick={onEdit}>Edit camera</button>
      </div>
    </div>
    <div className="gv-camera-focus-layout">
      <section className="gv-camera-feature" aria-label={clip ? "Recorded video" : "Live video"}>
        {clip ? <>
          <header className="gv-camera-feature-header"><div><span>Recording</span><h4 tabIndex={-1} ref={recordingTitle}>{clip.summary}</h4></div>
            <button type="button" className="gv-button" onClick={() => { setClip(undefined); recordingOrigin.current?.focus(); }}>Return to live</button></header>
          <RecordedFeed key={clip.key} recording={clip.recording} name={camera.name + " recording"} />
        </> : <>
          <header className="gv-camera-feature-header"><h4>{camera.demo && !hasFeed(camera) ? "Demo video" : "Live video"}</h4>
            {camera.cameraFeedUrl && camera.cameraGridUrl
              ? <label>Stream quality<select value={preferGrid ? "grid" : "primary"} onChange={(event) => setPreferGrid(event.target.value === "grid")}>
                <option value="primary">Primary feed</option><option value="grid">Lower-bandwidth feed</option>
              </select></label>
              : hasFeed(camera) && <span>{camera.cameraFeedUrl ? "Primary feed" : "Lower-bandwidth feed"}</span>}
          </header>
          <CameraFeed camera={camera} preferGrid={preferGrid} />
        </>}
      </section>
      <section className="gv-camera-feature gv-camera-profile" aria-label="Camera configuration">
        <h4>Camera details</h4>
        <dl>
          <div><dt>Appliance health</dt><dd><HealthIndicator health={health ?? { status: "unknown" }} /></dd></div>
          {camera.cameraGatewayKind && <div><dt>Stream health</dt><dd><VideoHealthLabel camera={camera} check={videoCheck} /></dd></div>}
          {activeMaintenance(camera, now) && <div><dt>Maintenance</dt><dd>Active</dd></div>}
          {camera.cameraGatewayKind && <div><dt>Connection</dt><dd>{camera.cameraGatewayKind.toUpperCase()} via camera gateway</dd></div>}
          <div><dt>Video system</dt><dd>{camera.cameraProvider || "Unspecified"}</dd></div>
          <div><dt>Channel</dt><dd>{camera.cameraChannel || "Unspecified"}</dd></div>
          <div><dt>Field of view</dt><dd>{camera.cameraView ? `${camera.cameraView.bearingDeg}° true · ${camera.cameraView.fovDeg}° wide · ${camera.cameraView.rangeM} m` : "Unconfigured"}</dd></div>
          {camera.cameraView && <div><dt>Field check</dt><dd>{camera.cameraVerifiedAt
            ? <time dateTime={camera.cameraVerifiedAt}>{DATE_UTC.format(new Date(camera.cameraVerifiedAt))} UTC</time> : "Pending"}</dd></div>}
          <div><dt>Assigned boundaries</dt><dd>{boundaries.map(({ name }) => name).join(", ") || "None"}</dd></div>
          <div><dt>Linked access devices</dt><dd>{doors.map(({ name }) => name).join(", ") || "None"}</dd></div>
        </dl>
        {camera.cameraView && !inspectionsReadOnly && <button type="button" className="gv-button" onClick={onVerify}>Mark view checked on site</button>}
        <CameraInspection camera={camera} check={videoCheck} onChange={onInspect} readOnly={inspectionsReadOnly} />
        <SiteCameraViewer session={siteSession} camera={camera} />
        <CameraGatewayAccess camera={camera} />
      </section>
      <section className="gv-camera-feature gv-camera-evidence" aria-label="Recent recordings">
        <h4>Recent recordings</h4>
        {!clips.length && <p>No clips received for this camera.</p>}
        {clips.map((event) => {
          const recording = event.recording;
          if (!recording) return null;
          const key = observationKey(event);
          return <button type="button" key={key} className="gv-camera-event" aria-pressed={clip?.key === key} onClick={(click) => {
            recordingOrigin.current = click.currentTarget; setClip({ key, summary: event.summary, recording });
          }}>
            <Video size={16} aria-hidden="true" /><span><strong>{event.summary}</strong>
              <small><time dateTime={event.occurredAt}>{DATE_UTC.format(new Date(event.occurredAt))} UTC</time></small></span><Play size={15} aria-hidden="true" />
          </button>;
        })}
      </section>
      <section className="gv-camera-feature gv-camera-evidence" aria-label="Recent incidents">
        <h4>Recent incidents</h4>
        {!incidents.length && <p>No incidents linked to this camera.</p>}
        {incidents.map((alert) => <button type="button" key={alert.id} className="gv-camera-event" onClick={() => onIncident(alert)}>
          <Radar size={16} aria-hidden="true" /><span><strong>{alert.observation.summary}</strong><small>{alert.zoneName} · {alert.severity} priority</small></span><ChevronRight size={15} aria-hidden="true" />
        </button>)}
      </section>
    </div>
  </>;
}

function CameraCoveragePane({ cameras, boundaries, coverage, heading, onMap }: {
  cameras: Sensor[]; boundaries: Boundary[]; coverage: CameraCoverage | null;
  heading: Ref<HTMLHeadingElement>; onMap: (area?: Boundary) => void;
}) {
  const areas = [...boundaries].sort((a, b) => (coverage?.gapFractionByBoundary.get(b.id) ?? -1) - (coverage?.gapFractionByBoundary.get(a.id) ?? -1));
  const modeled = coverage?.sectors.length ?? 0;
  return <>
    <div className="gv-camera-content-title">
      <div><h3 ref={heading} tabIndex={-1}>Coverage</h3><p>{coverage
        ? `${modeled} of ${cameras.length} cameras modeled${coverage.overlaps.length ? " · Camera overlap modeled" : ""}`
        : "Coverage unavailable"}</p></div>
      <button type="button" className="gv-button" onClick={() => onMap()}><MapPin size={16} aria-hidden="true" /> Open coverage map</button>
    </div>
    <p className="gv-camera-coverage-note">Estimated from camera direction, angle, range, and marked obstructions. Check performance on site.</p>
    <section className="gv-camera-coverage-list" aria-label="Modeled boundary coverage">
      {!areas.length && <p className="gv-camera-empty">No boundaries configured.</p>}
      {areas.map((area) => {
        const gap = coverage?.gapFractionByBoundary.get(area.id);
        return <article className="gv-camera-coverage-row" key={area.id} aria-label={area.name}>
          <div><h4>{area.name}</h4><p>{!area.enabled ? "Monitoring paused" : gap === undefined ? "Coverage unavailable"
            : `${coveragePercent(1 - gap)} modeled coverage · ${coveragePercent(gap)} modeled gap`}</p>
            {gap !== undefined && <meter min={0} max={100} value={(1 - gap) * 100} aria-label={`Modeled coverage of ${area.name}`} />}
          </div>
          <button type="button" className="gv-button" aria-label={`Show coverage for ${area.name}`} onClick={() => onMap(area)}>
            <MapPin size={16} aria-hidden="true" /> Show on map
          </button>
        </article>;
      })}
    </section>
  </>;
}
