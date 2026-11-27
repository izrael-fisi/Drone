import { useEffect, useRef, useState, type FormEvent } from "react";
import { Cctv, ChevronRight, DoorOpen, Folder, FolderPlus, Pencil, Plus, Radio, X } from "lucide-react";
import { readCoordinates, type CoordinateDraft } from "./configurationModel";
import { CAMERA_SOURCE_MAX_LENGTH, CAMERA_VIEW_LIMITS, httpEndpointUrl, isCameraView, labelKey, MODALITIES, MODALITY_LABEL, parseLabels, validInspectionStationId, validLabels, type Boundary, type CameraGatewayKind, type CameraView, type DeviceGroup, type Modality, type Sensor } from "./monitoringModel";
import { validReportInterval, type ApplianceHealth, type HealthReport, type MaintenanceWindow } from "./applianceHealth";
import { ApplianceSetup } from "./ApplianceSetup";
import { HealthIndicator } from "./DeviceHealth";
import { normalizeGatewayId } from "./gatewayIncidents";
import { CAMERA_GATEWAY_DEFAULTS, connectGatewayCamera, discoverCameraProfiles, gatewayDraftReady, type CameraGatewayDraft, type OnvifProfile } from "./cameraGateway";
import { FloorSelect } from "./SiteFloors";
import type { SiteDependency, SiteFloor } from "./monitoringModel";
import { analyticsRuleError, CctvRulesEditor } from "./CctvAnalytics";

type CameraViewDraft = Record<keyof CameraView, string>;
export type DeviceDraft = Omit<Sensor, "position" | "labels" | "cameraView" | "cameraFeedUrl" | "cameraGridUrl" | "reportIntervalSeconds" | "healthReport" | "healthUrl" | "healthOutages" | "maintenanceWindows" | "gatewayId"> & {
  coordinates: CoordinateDraft; boundaryIds: string[]; labelsText: string; cameraView: CameraViewDraft | null;
  cameraFeedUrl: string; cameraGridUrl: string;
  reportInterval: string; healthUrl: string; gatewayId: string; pendingReport?: HealthReport;
};
export type DevicePlacement = "idle" | "location" | "aim";
export const ALL_GROUPS = "all";
export const UNGROUPED = "ungrouped";

export function toCameraViewDraft(view: CameraView | null): CameraViewDraft | null {
  return view ? { bearingDeg: String(view.bearingDeg), fovDeg: String(view.fovDeg), rangeM: String(view.rangeM) } : null;
}

export function readCameraView(draft: CameraViewDraft | null) {
  if (!draft || Object.values(draft).some((value) => value.trim() === "")) return null;
  const view = { bearingDeg: Number(draft.bearingDeg), fovDeg: Number(draft.fovDeg), rangeM: Number(draft.rangeM) };
  return isCameraView(view) ? view : null;
}

interface InventoryProps {
  devices: Sensor[];
  visible: Sensor[];
  groups: DeviceGroup[];
  healthById: ReadonlyMap<string, ApplianceHealth>;
  maintenanceById: ReadonlyMap<string, MaintenanceWindow>;
  groupFilter: string;
  labelFilter: string;
  selected?: string;
  disabled: boolean;
  onGroupFilter: (value: string) => void;
  onLabelFilter: (value: string) => void;
  onSelect: (id: string) => void;
  onRegister: () => void;
  onImport?: () => void;
  onEditGroup: (group?: DeviceGroup) => void;
}

export const deviceIcon = { vision: Cctv, rf: Radio, access: DoorOpen };

export function DeviceInventory({ devices, visible, groups, healthById, maintenanceById, groupFilter, labelFilter, selected, disabled, onGroupFilter, onLabelFilter, onSelect, onRegister, onImport, onEditGroup }: InventoryProps) {
  const labelOptions = [...new Map(devices.flatMap((device) => device.labels.map((label) => [labelKey(label), label] as const))).entries()]
    .sort((a, b) => a[1].localeCompare(b[1]));
  const displayedGroups = [...groups, { id: UNGROUPED, name: "Ungrouped" }].filter((group) => groupFilter === ALL_GROUPS || group.id === groupFilter);
  const filtered = groupFilter !== ALL_GROUPS || !!labelFilter;

  return <div className="gv-device-inventory">
    <div className="gv-device-tools">
      <div className="gv-device-actions">
        <button type="button" className="gv-button" data-focus-key="new-group" disabled={disabled}
          onClick={() => onEditGroup()}><FolderPlus size={14} /> New group</button>
        <button type="button" className="gv-button" data-focus-key="add-device" disabled={disabled}
          onClick={onRegister}><Plus size={15} /> Add device</button>
        {onImport && <button type="button" className="gv-button" disabled={disabled} onClick={onImport}>Import inventory</button>}
      </div>
      <div className="gv-device-filters">
        <label><span id="gv-group-filter-label">Group</span><select aria-labelledby="gv-group-filter-label" value={groupFilter} disabled={disabled} onChange={(event) => onGroupFilter(event.target.value)}>
          <option value={ALL_GROUPS}>All groups</option>
          {groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
          <option value={UNGROUPED}>Ungrouped</option>
        </select></label>
        <label><span id="gv-label-filter-label">Label</span><select aria-labelledby="gv-label-filter-label" value={labelFilter} disabled={disabled} onChange={(event) => onLabelFilter(event.target.value)}>
          <option value="">All labels</option>
          {labelOptions.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
      </div>
      {filtered && <div className="gv-device-results" role="status"><span>{visible.length} of {devices.length} devices</span><button type="button" disabled={disabled} onClick={() => { onGroupFilter(ALL_GROUPS); onLabelFilter(""); }}>Clear filters</button></div>}
    </div>
    {displayedGroups.map((group) => {
      const members = visible.filter((device) => (device.groupId ?? UNGROUPED) === group.id);
      if (!members.length && (group.id === UNGROUPED || labelFilter) && groupFilter === ALL_GROUPS) return null;
      return <section className="gv-device-group" key={group.id} aria-label={group.name}>
        <header className="gv-section-heading gv-group-heading">
          <Folder size={15} /><h3>{group.name}</h3><span className="gv-group-count">{members.length}</span>
          {group.id !== UNGROUPED && <button type="button" className="gv-icon-button" data-focus-key={"edit-group-" + group.id}
            aria-label={`Edit group ${group.name}`} title={`Edit group ${group.name}`} disabled={disabled} onClick={() => onEditGroup(group)}><Pencil size={13} /></button>}
        </header>
        {members.map((device) => {
          const Icon = deviceIcon[device.modality];
          return <button key={device.id} type="button" className="gv-inventory-row" data-focus-key={"sensor" + device.id}
            disabled={disabled} aria-pressed={selected === device.id} onClick={() => onSelect(device.id)}>
            <Icon size={16} /><span><strong title={device.name}>{device.name}</strong><small>{MODALITY_LABEL[device.modality]}</small>
              <HealthIndicator health={healthById.get(device.id) ?? { status: "unknown" }} />
              {maintenanceById.has(device.id) && <small>Maintenance active</small>}
            </span><ChevronRight size={14} />
          </button>;
        })}
        {!members.length && <p className="gv-empty-group">{labelFilter ? "No matching devices" : "No devices assigned"}</p>}
      </section>;
    })}
    {!visible.length && labelFilter && groupFilter === ALL_GROUPS && <p className="gv-empty">No matching devices.</p>}
  </div>;
}

export function DeviceForm({ device, groups, boundaries, cameras, floors, dependencies = [], placement, mapView, savedDevice, linkedDeviceCount, configurationDraft, timeZone = "UTC", onChange, onPlacement, onLocate, onSave, onCancel, onReport, onTestRoute }: {
  device: DeviceDraft; groups: DeviceGroup[]; boundaries: Boundary[]; cameras: Sensor[]; floors: SiteFloor[]; placement: DevicePlacement;
  dependencies?: SiteDependency[];
  savedDevice?: Sensor; mapView: boolean; linkedDeviceCount: number; configurationDraft?: boolean; timeZone?: string; onReport: (report: HealthReport) => void; onTestRoute: () => string;
  onChange: (device: DeviceDraft) => void; onPlacement: (placement: DevicePlacement) => void; onLocate: () => void;
  onSave: (device: DeviceDraft) => void; onCancel: () => void;
}) {
  const [connection, setConnection] = useState<"hls" | CameraGatewayKind>("hls");
  const [gateway, setGateway] = useState(CAMERA_GATEWAY_DEFAULTS);
  const [profiles, setProfiles] = useState<OnvifProfile[]>([]);
  const [checking, setChecking] = useState<"profiles" | "connect">();
  const [gatewayError, setGatewayError] = useState("");
  const gatewayRequest = useRef<AbortController>();
  const sourceType = device.modality === "vision" && connection !== "hls" ? connection : null;
  const gatewayReady = !!sourceType && gatewayDraftReady(gateway, sourceType);
  useEffect(() => () => gatewayRequest.current?.abort(), []);

  const invalidLabels = !validLabels(parseLabels(device.labelsText));
  const position = readCoordinates(device.coordinates);
  const hasCoordinates = !!(device.coordinates.latitude || device.coordinates.longitude);
  const invalidView = device.modality === "vision" && !!device.cameraView && !readCameraView(device.cameraView);
  const feed = device.modality === "vision" ? httpEndpointUrl(device.cameraFeedUrl) : null;
  const gridFeed = device.modality === "vision" ? httpEndpointUrl(device.cameraGridUrl) : null;
  const invalidFeed = device.modality === "vision" && !!device.cameraFeedUrl.trim() && !feed;
  const invalidGridFeed = device.modality === "vision" && !!device.cameraGridUrl.trim() && !gridFeed;
  const healthSource = httpEndpointUrl(device.healthUrl);
  const invalidHealthSource = !!device.healthUrl.trim() && !healthSource;
  const invalidGateway = !!device.gatewayId.trim() && !normalizeGatewayId(device.gatewayId);
  const station = device.inspectionStationId?.trim();
  const invalidStation = device.modality === "vision" && !!station && !validInspectionStationId(station);
  const assignedBoundaries = boundaries.filter((boundary) => boundary.floorId === device.floorId && device.boundaryIds.includes(boundary.id));
  const legacyAnalytics = !savedDevice?.analyticsPlacement && !!savedDevice?.analyticsRules?.length;
  const analyticsError = configurationDraft && device.modality === "vision" ? analyticsRuleError(device.analyticsRules ?? [], assignedBoundaries.map(({ id }) => id)) : "";
  const canSave = !!position && !!device.name.trim() && !checking && !invalidLabels && !invalidView
    && (sourceType ? gatewayReady : !invalidFeed && !invalidGridFeed) && !invalidHealthSource
    && !invalidGateway && !invalidStation && !analyticsError && validReportInterval(Number(device.reportInterval));
  const enabledIds = boundaries.filter((boundary) => boundary.enabled && device.boundaryIds.includes(boundary.id)).map((boundary) => boundary.id);
  const removingCamera = savedDevice?.modality === "vision" && device.modality !== "vision";
  const removingAccessLink = savedDevice?.modality === "access" && !!savedDevice.linkedCameraId && device.modality !== "access";
  const patch = (field: Partial<DeviceDraft>) => onChange({ ...device, ...field });

  function patchGateway(field: Partial<CameraGatewayDraft>) {
    setGateway((current) => ({ ...current, profile: "", gridProfile: "", ...field }));
    setProfiles([]);
    setGatewayError("");
  }

  async function runGateway(operation: "profiles" | "connect", action: (signal: AbortSignal) => Promise<void>) {
    const controller = new AbortController();
    gatewayRequest.current = controller;
    setChecking(operation);
    setGatewayError("");
    const timeout = window.setTimeout(() => controller.abort("timeout"), operation === "connect" && (gateway.gridAddress || gateway.gridProfile) ? 65_000 : 40_000);
    try {
      await action(controller.signal);
    } catch (error) {
      if (!controller.signal.aborted || controller.signal.reason === "timeout") {
        setGatewayError(error instanceof Error ? error.message : "Camera gateway request failed.");
      }
    } finally {
      window.clearTimeout(timeout);
      if (gatewayRequest.current === controller && (!controller.signal.aborted || controller.signal.reason === "timeout")) setChecking(undefined);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    if (!sourceType) {
      const cameraGatewayKind = savedDevice?.cameraFeedUrl === feed ? savedDevice?.cameraGatewayKind : undefined;
      onSave({ ...device, cameraGatewayKind });
      return;
    }
    void runGateway("connect", async (signal) => {
      const streams = await connectGatewayCamera(gateway, sourceType, device.id, signal);
      if (!signal.aborted) onSave({ ...device, ...streams, cameraGatewayKind: sourceType });
    });
  }

  return <>
    <header className="gv-panel-heading"><div><Radio size={17} /><h2>{savedDevice ? "Edit device" : "Register device"}</h2></div><button className="gv-icon-button" type="button" disabled={checking === "connect"} aria-label="Cancel device editing" onClick={onCancel}><X size={17} /></button></header>
    <form className="gv-form" onSubmit={submit} data-camera-connecting={checking === "connect" || undefined}>
      <fieldset className="gv-device-editor-fields" disabled={!!checking}>
      <label>Device name<input required maxLength={60} value={device.name} onChange={(event) => patch({ name: event.target.value })} placeholder="e.g. West gate camera" /></label>
      <FloorSelect floors={floors} value={device.floorId} onChange={(id) => patch({ floorId: id || undefined,
        boundaryIds: device.boundaryIds.filter((boundaryId) => boundaries.find((boundary) => boundary.id === boundaryId)?.floorId === (id || undefined)) })} empty="Outdoors" />
      <label><span id="gv-device-type-label">Device type</span><select aria-labelledby="gv-device-type-label" aria-describedby={removingCamera || removingAccessLink ? "gv-device-type-impact" : undefined} value={device.modality} onChange={(event) => {
        const modality = event.target.value as Modality;
        if (modality !== "vision" && placement === "aim") onPlacement("idle");
        patch({ modality });
      }}>
        {MODALITIES.map((modality) => <option key={modality} value={modality}>{MODALITY_LABEL[modality]}</option>)}
      </select></label>
      {removingCamera && <p id="gv-device-type-impact" className="gv-setup-note" role="status">
        Saving clears the camera feed and field of view settings.{linkedDeviceCount > 0 && ` It also removes the camera link from ${linkedDeviceCount} access ${linkedDeviceCount === 1 ? "device" : "devices"}.`}
      </p>}
      {removingAccessLink && <p id="gv-device-type-impact" className="gv-setup-note" role="status">Saving clears this device’s linked camera.</p>}
      <fieldset>
        <legend>Location</legend>
        <div className="gv-coordinate-inputs">
          <label>Latitude<input type="number" min={-85} max={85} step="any" value={device.coordinates.latitude}
            onChange={(event) => patch({ coordinates: { ...device.coordinates, latitude: event.target.value } })} /></label>
          <label>Longitude<input type="number" min={-180} max={180} step="any" value={device.coordinates.longitude}
            onChange={(event) => patch({ coordinates: { ...device.coordinates, longitude: event.target.value } })} /></label>
        </div>
        {!position && <p className="gv-form-geometry">{!hasCoordinates && mapView ? "Click the map or enter coordinates." : "Enter valid latitude and longitude."}</p>}
        {mapView && <div className="gv-editor-tools">
          <button className="gv-button" type="button" aria-pressed={placement === "location"} onClick={() => onPlacement(placement === "location" ? "idle" : "location")}>
            {placement === "location" ? "Finish placement" : position ? "Move on map" : "Place on map"}
          </button>
          <button className="gv-button" type="button" disabled={!position} onClick={onLocate}>Locate on map</button>
        </div>}
      </fieldset>
      {device.modality === "vision" && <fieldset className="gv-appliance-setup">
        <legend>Camera connection</legend>
        <label>Connection type<select value={connection} onChange={(event) => {
          setConnection(event.target.value as typeof connection);
          patchGateway({ address: "", gridAddress: "" });
        }}>
          <option value="hls">HLS playback URL</option><option value="onvif" disabled={configurationDraft}>ONVIF camera</option><option value="rtsp" disabled={configurationDraft}>RTSP camera</option>
        </select></label>
        {sourceType ? <>
          <label>Camera gateway URL<input type="url" value={gateway.gateway} onChange={(event) => patchGateway({ gateway: event.target.value })}
            autoComplete="off" spellCheck={false} /></label>
          <label>Gateway management token<input type="password" value={gateway.token} onChange={(event) => patchGateway({ token: event.target.value })}
            autoComplete="off" spellCheck={false} /></label>
          <label>{sourceType === "onvif" ? "ONVIF device address" : "RTSP stream URL"}<input type="url" value={gateway.address}
            onChange={(event) => patchGateway({ address: event.target.value })} autoComplete="off" spellCheck={false}
            placeholder={sourceType === "onvif" ? "http://192.168.1.20/onvif/device_service" : "rtsp://192.168.1.20:554/stream1"} /></label>
          <label>Camera username<input value={gateway.username} onChange={(event) => patchGateway({ username: event.target.value })} autoComplete="off" /></label>
          <label>Camera password<input type="password" value={gateway.password} onChange={(event) => patchGateway({ password: event.target.value })} autoComplete="off" /></label>
          <p className="gv-setup-note">Use the camera’s IP address. The gateway verifies video before saving the device.</p>
          {sourceType === "onvif" && <>
            <button type="button" className="gv-button gv-inline-action" disabled={!gatewayReady || !!checking} onClick={() => {
              void runGateway("profiles", async (signal) => {
                const found = await discoverCameraProfiles(gateway, signal);
                if (!signal.aborted) setProfiles(found);
              });
            }}>Find ONVIF profiles</button>
            {!!profiles.length && <label>ONVIF profile<select value={gateway.profile} onChange={(event) => setGateway({ ...gateway, profile: event.target.value, gridProfile: "" })}>
              <option value="">First available profile</option>
              {profiles.map((profile) => <option key={profile.token} value={profile.token}>{profile.name}</option>)}
            </select></label>}
            {profiles.length > 1 && <label>Live wall profile<select value={gateway.gridProfile} onChange={(event) => setGateway({ ...gateway, gridProfile: event.target.value })}>
              <option value="">Use primary profile</option>
              {profiles.filter((profile) => profile.token !== (gateway.profile || profiles[0].token)).map((profile) => <option key={profile.token} value={profile.token}>{profile.name}</option>)}
            </select></label>}
          </>}
          {sourceType === "rtsp" && <label>Secondary RTSP URL (live wall)<input type="url" value={gateway.gridAddress} onChange={(event) =>
            setGateway({ ...gateway, gridAddress: event.target.value })} placeholder="rtsp://192.168.1.20:554/stream2" autoComplete="off" spellCheck={false} /></label>}
          {savedDevice?.cameraGridUrl && <p className="gv-setup-note">Connecting replaces the primary and lower-bandwidth playback URLs.</p>}
        </> : <>
        {savedDevice?.cameraGatewayKind && <p className="gv-setup-note">Connected by {savedDevice.cameraGatewayKind.toUpperCase()} through the camera gateway.</p>}
        <label>HLS feed URL<input type="url" value={device.cameraFeedUrl} aria-invalid={invalidFeed} aria-describedby="gv-camera-feed-help"
          onChange={(event) => patch({ cameraFeedUrl: event.target.value })} placeholder="https://gateway.example/camera/live.m3u8" autoComplete="off" spellCheck={false} /></label>
        <p className="gv-setup-note" id="gv-camera-feed-help">{invalidFeed ? "Enter an HTTP or HTTPS playback URL without embedded credentials." : "Use the playback URL from your camera gateway or video management system."}</p>
        <label>Lower-bandwidth HLS URL<input type="url" value={device.cameraGridUrl} aria-invalid={invalidGridFeed} aria-describedby="gv-camera-grid-help"
          onChange={(event) => patch({ cameraGridUrl: event.target.value })} placeholder="https://gateway.example/camera/substream.m3u8" autoComplete="off" spellCheck={false} /></label>
        <p className="gv-setup-note" id="gv-camera-grid-help">{invalidGridFeed ? "Enter an HTTP or HTTPS playback URL without embedded credentials." : "Optional. The Live wall prefers this stream. A focused camera uses the primary feed when available."}</p>
        </>}
        <label>Video system<input value={device.cameraProvider ?? ""} maxLength={CAMERA_SOURCE_MAX_LENGTH}
          onChange={(event) => patch({ cameraProvider: event.target.value })} placeholder="e.g. East site VMS" /></label>
        <label>Channel reference<input value={device.cameraChannel ?? ""} maxLength={CAMERA_SOURCE_MAX_LENGTH}
          onChange={(event) => patch({ cameraChannel: event.target.value })} placeholder="e.g. Recorder 1 / Channel 07" /></label>
      </fieldset>}
      {device.modality === "access" && <div className="gv-form-field">
        <label htmlFor="gv-linked-camera">Linked camera</label>
        <select id="gv-linked-camera" value={device.linkedCameraId ?? ""} onChange={(event) => patch({ linkedCameraId: event.target.value || null, passageRuleId: undefined })}>
          <option value="">No linked camera</option>
          {cameras.filter((camera) => camera.id !== device.id).map((camera) => <option key={camera.id} value={camera.id}>{camera.name}</option>)}
        </select>
        {configurationDraft && device.linkedCameraId && <label>Inbound passage tripwire
          <select value={device.passageRuleId ?? ""} onChange={(event) => patch({ passageRuleId: event.target.value || undefined })}>
            <option value="">Passage review disabled</option>
            {cameras.find(({ id }) => id === device.linkedCameraId)?.analyticsRules
              ?.filter(({ kind, object, direction, boundaryId, cameraNative }) => kind === "line-crossing" && object === "person"
                && direction !== "either" && !cameraNative && device.boundaryIds.includes(boundaryId))
              .map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
          </select>
        </label>}
      </div>}
      <label><span id="gv-device-group-label">Device group</span><select aria-labelledby="gv-device-group-label" value={device.groupId ?? ""} onChange={(event) => patch({ groupId: event.target.value || null })}>
        <option value="">Ungrouped</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
      </select></label>
      <div className="gv-form-field">
        <label htmlFor="gv-device-labels">Labels</label>
        <input id="gv-device-labels" maxLength={272} value={device.labelsText} aria-invalid={invalidLabels} aria-describedby="gv-label-help" onChange={(event) => patch({ labelsText: event.target.value })} placeholder="Outdoor, Loading dock" />
        <small id="gv-label-help">{invalidLabels ? "Use up to eight labels with 32 characters each." : "Separate labels with commas."}</small>
      </div>
      <label>Map label<input maxLength={24} value={device.mapLabel} onChange={(event) => patch({ mapLabel: event.target.value })} placeholder="Defaults to device name" /></label>
      {!!dependencies.length && <fieldset><legend>Infrastructure dependencies</legend>
        {dependencies.map((dependency) => <label key={dependency.id} className="gv-checkbox">
          <input type="checkbox" checked={!!device.dependencyIds?.includes(dependency.id)} onChange={(event) => patch({
            dependencyIds: event.target.checked ? [...(device.dependencyIds ?? []), dependency.id] : (device.dependencyIds ?? []).filter((id) => id !== dependency.id),
          })} />{dependency.name}
        </label>)}
      </fieldset>}
      {!!boundaries.length && <fieldset><legend>Assigned boundaries</legend>
        {boundaries.filter((boundary) => boundary.floorId === device.floorId).map((boundary) => <label key={boundary.id} className="gv-checkbox">
          <input type="checkbox" checked={device.boundaryIds.includes(boundary.id)} onChange={(event) => patch({
            boundaryIds: event.target.checked ? [...device.boundaryIds, boundary.id] : device.boundaryIds.filter((id) => id !== boundary.id),
          })} />{boundary.name}
        </label>)}
      </fieldset>}
      {device.modality === "vision" && <CameraViewFields value={device.cameraView} mapView={mapView} canAim={!!position} aiming={placement === "aim"}
        onAim={() => onPlacement(placement === "aim" ? "idle" : "aim")}
        onChange={(cameraView) => { if (!cameraView && placement === "aim") onPlacement("idle"); patch({ cameraView }); }} />}
      {configurationDraft && device.modality === "vision" && <CctvRulesEditor rules={device.analyticsRules ?? []} boundaries={assignedBoundaries} timeZone={timeZone}
        placement={device.analyticsPlacement} legacy={legacyAnalytics}
        onPlacement={(analyticsPlacement) => patch({ analyticsPlacement })}
        onChange={(analyticsRules) => patch({ analyticsRules,
          analyticsPlacement: device.analyticsPlacement ?? (legacyAnalytics ? undefined : "cloud") })} />}
      {analyticsError && <p role="alert">{analyticsError}</p>}
      {configurationDraft && device.modality === "vision" && <label>Inspection station ID (pilot)
        <input value={device.inspectionStationId ?? ""} maxLength={96} aria-invalid={invalidStation}
          aria-describedby="gv-inspection-station-help" onChange={(event) => patch({ inspectionStationId: event.target.value })} />
        <small id="gv-inspection-station-help">Optional. Use the ID from the simulated station adapter. Letters, numbers, hyphens and underscores are supported.</small>
      </label>}
      <ApplianceSetup key={device.modality} deviceId={device.id} name={device.name} feed={sourceType ? null : feed} gridFeed={sourceType ? null : gridFeed} interval={device.reportInterval}
        healthUrl={device.healthUrl} source={healthSource} onSource={(healthUrl) => patch({ healthUrl })}
        gatewayId={device.gatewayId} invalidGateway={invalidGateway} onGateway={(gatewayId) => patch({ gatewayId })}
        existingReport={savedDevice?.healthReport} pendingReport={device.pendingReport} routeKey={JSON.stringify([position, enabledIds])}
        canTestRoute={!!position && enabledIds.length > 0} onInterval={(reportInterval) => patch({ reportInterval })}
        onReport={onReport} onTestRoute={onTestRoute} />
      </fieldset>
      <div className="gv-detail-actions gv-editor-actions">
        {checking && <p className="gv-setup-note" role="status">{checking === "profiles" ? "Reading ONVIF profiles…" : "Connecting and checking video…"}</p>}
        {gatewayError && <p className="gv-setup-note gv-gateway-form-error" role="alert">{gatewayError}</p>}
        <button className="gv-button gv-primary" type="submit" disabled={!canSave}>{sourceType ? "Connect and save" : "Save device"}</button>
        <button className="gv-button" type="button" disabled={checking === "connect"} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  </>;
}

const cameraFields = [
  { key: "bearingDeg", label: "Bearing (°)" },
  { key: "fovDeg", label: "Horizontal FOV (°)" },
  { key: "rangeM", label: "Range (m)" },
] as const;

function CameraViewFields({ value, mapView, canAim, aiming, onAim, onChange }: {
  value: CameraViewDraft | null; mapView: boolean; canAim: boolean; aiming: boolean; onAim: () => void; onChange: (view: CameraViewDraft | null) => void;
}) {
  return <fieldset className="gv-camera-fields">
    <legend>Camera field of view</legend>
    <label className="gv-checkbox"><input type="checkbox" checked={!!value} onChange={(event) => onChange(event.target.checked ? { bearingDeg: "0", fovDeg: "90", rangeM: "100" } : null)} /> Configure field of view</label>
    {value && <>
      <div className="gv-camera-inputs">
        {cameraFields.map(({ key, label }) => <label key={key}>{label}
          <input type="number" step="any" required {...CAMERA_VIEW_LIMITS[key]} value={value[key]} onChange={(event) => onChange({ ...value, [key]: event.target.value })} />
        </label>)}
      </div>
      {mapView && <button type="button" className="gv-button gv-inline-action" aria-pressed={aiming} disabled={!canAim} onClick={onAim}>{aiming ? "Cancel aiming" : "Aim on map"}</button>}
      <p>Bearings are clockwise from true north. North is 0° and east is 90°.</p>
    </>}
  </fieldset>;
}

export function GroupForm({ draft, groups, onChange, onSave, onCancel }: {
  draft: DeviceGroup; groups: DeviceGroup[]; onChange: (draft: DeviceGroup) => void; onSave: (group: DeviceGroup) => void; onCancel: () => void;
}) {
  const name = draft.name.trim();
  const duplicate = groups.some((group) => group.id !== draft.id && labelKey(group.name) === labelKey(name));
  const reserved = labelKey(name) === UNGROUPED;
  const existing = groups.some((group) => group.id === draft.id);
  return <>
    <header className="gv-panel-heading"><div><Folder size={17} /><h2>{existing ? "Edit device group" : "New device group"}</h2></div><button className="gv-icon-button" type="button" aria-label="Cancel group editing" onClick={onCancel}><X size={17} /></button></header>
    <form className="gv-form" onSubmit={(event) => { event.preventDefault(); if (name && !duplicate && !reserved) onSave({ ...draft, name }); }}>
      <label>Group name<input required maxLength={48} value={draft.name} onChange={(event) => onChange({ ...draft, name: event.target.value })} placeholder="e.g. West campus" /></label>
      {(duplicate || reserved) && <p className="gv-form-geometry" role="status">Choose a different group name.</p>}
      <div className="gv-detail-actions"><button type="submit" className="gv-button gv-primary" disabled={!name || duplicate || reserved}>Save group</button><button className="gv-button" type="button" onClick={onCancel}>Cancel</button></div>
    </form>
  </>;
}
