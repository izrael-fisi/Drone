import { IntelligencePosture } from "./SiteIntelligence";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import {
  Activity, Bell, Camera, Cctv, ChevronLeft, ChevronRight, CircleAlert, Layers3,
  List, Map as MapIcon, MapPin, Minus, Pentagon, Plus, Radio, Search, ShieldCheck, Square, Tag, Upload, X,
} from "lucide-react";
import { MonitoringMap } from "./MonitoringMap";
import { CameraFeed } from "./CameraFeed";
import { CameraWorkspace } from "./CameraWorkspace";
import { IncidentPanel } from "./IncidentPanel";
import { IncidentReplay } from "./IncidentReplay";
import { IncidentQueue } from "./IncidentQueuePanel";
import { DEFAULT_INCIDENT_FILTERS } from "./incidentQueue";
import { eventFromAlert, incidentTimeline } from "./replayTimeline";
import { mapAlertScope } from "./mapAlerts";
import { GatewayIncidentPanel, GatewayIncidentRows } from "./GatewayIncidentPanel";
import { gatewayBoundaries, normalizeGatewayId, NO_GATEWAY_INCIDENTS, reconcileGatewayIncidents,
  RECOVERED_BOUNDARY_COLOR, type GatewayIncident } from "./gatewayIncidents";
import { ApplianceHealthMonitor } from "./ApplianceHealthMonitor";
import { activeMaintenance, getApplianceHealth, getHealthScope, getOutageHistory, isNewerReport, parseHealthReport, recordHealthReport,
  REPORT_INTERVAL, scheduleMaintenance, stopMaintenance, validReportInterval, type ApplianceHealth, type HealthReport, type HealthStatus, type MaintenanceWindow } from "./applianceHealth";
import { HealthIndicator, HealthLegend, healthText } from "./DeviceHealth";
import type { TriageFocus } from "./HealthTriage";
import { applyIncidentAction, incidentCamera, incidentCameras } from "./incidentModel";
import { CameraInspection, useVideoHealth, VideoPosture, type VideoCheck } from "./VideoPosture";
import { useIncidentArchive } from "./incidentArchive";
import { useGatewayEvents } from "./gatewayEvents";
import { WorkspaceSearch, type SearchTarget } from "./WorkspaceSearch";
import { EvidenceSearch } from "./EvidenceSearch";
import { BoundaryForm } from "./BoundaryEditor";
import { bearingTo, insertVertex, MAX_VERTICES, moveVertex, placeBoundaryPoint, readCoordinates, toCoordinateDraft, type BoundaryDraft } from "./configurationModel";
import { ALL_GROUPS, UNGROUPED, DeviceForm, DeviceInventory, GroupForm, readCameraView, toCameraViewDraft, type DeviceDraft, type DevicePlacement } from "./DeviceInventory";
import { CAMERA_COVERAGE_COLORS, calculateCameraCoverage } from "./cameraCoverage";
import type { WallPreferences } from "./cameraWallModel";
import { SitePosture } from "./SitePosture";
import { CctvAnalyticsPosture } from "./CctvAnalytics";
import { DockOccupancyPanel } from "./DockOccupancyPanel";
import { InspectionResultsPanel } from "./InspectionResultsPanel";
import { DoorPassageReview } from "./DoorPassageReview";
import { FloorSelect, SiteFloors } from "./SiteFloors";
import { SiteTelemetryPanel, useSiteTelemetry, type TelemetryConnection } from "./SiteTelemetry";
import { canManageSite, enterpriseWorkspace, SiteCameraViewer, SiteOperatorPanel, siteInventory, siteRequest, useSiteOperator, type SiteSession } from "./SiteOperator";
import { SiteConfigurationPanel, type SiteDraft } from "./SiteConfigurationPanel";
import { SiteOperationsPanel } from "./SiteOperationsPanel";
import { OrganizationAccounts } from "./OrganizationAccounts";
import { SiteReadiness } from "./SiteReadiness";
import { DEFAULT_INSIGHTS_VIEW, SiteInsights, type SiteInsightsView } from "./SiteInsights";
import { useSiteAssets } from "./useSiteAssets";
import { XProtectCatalog } from "./XProtectCamera";
import { OrganizationDispatch, useOrganizationDispatch } from "./OrganizationDispatch";
import { hasTauriWindowRuntime, runWindowAction } from "../../lib/windowControls";
import {
  BOUNDARY_VISIBILITY, MODALITY_LABEL, SAMPLE_SUMMARIES,
  associatedCamera, boundaryColor, boundaryVisibility, cameraSourceLabel, httpEndpointUrl, evaluateObservation, labelKey, loadWorkspace, parseLabels, parseObservation, readMonitoringFile, samplePosition, saveDeviceAssignments, saveWorkspace, validInspectionStationId, validLabels, validateBoundary,
  observationKey, type Alert, type Boundary, type DeviceGroup, type IncomingObservation, type IncidentAction, type Position, type Sensor, type SiteEvent, type SiteFloor, type Workspace,
} from "./monitoringModel";
import "./globalView.css";

const NO_VERTICES: Position[] = [];
const NO_FLOORS: SiteFloor[] = [];
const COMPACT_WORKSPACE = "(max-width: 920px)";
const VIEW_PREFERENCE = "proxigo.global-view.view.v1";
const LIST_SECTIONS = { alerts: "Alerts", devices: "Devices", boundaries: "Boundaries", posture: "Posture" } as const;
const LIST_SECTION_ICONS = { alerts: Bell, devices: Radio, boundaries: Pentagon, posture: ShieldCheck };
type ListSection = keyof typeof LIST_SECTIONS;
type FocusOrigin = { element?: HTMLElement; key?: string; returnToPosture?: boolean };
type FocusDestination = FocusOrigin | "review" | "inventory" | "list" | "view" | "replay-launcher";

function currentFocus(): FocusOrigin {
  const element = document.activeElement;
  return element instanceof HTMLElement && element !== document.body
    ? { element, key: element.closest<HTMLElement>("[data-focus-key]")?.dataset.focusKey } : {};
}

function readViewPreference() {
  try { return localStorage.getItem(VIEW_PREFERENCE) === "list" ? "list" : "map"; }
  catch { return "map"; }
}

type Selection = { kind: SearchTarget["kind"]; id: string };
type Editor = BoundaryDraft
  | { kind: "sensor"; value: DeviceDraft; placement: DevicePlacement }
  | { kind: "group"; value: DeviceGroup };
const LAYER_ICONS = { boundaries: Pentagon, sensors: Radio, deviceLabels: Tag, deviceHealth: Activity, cameraViews: Camera, alerts: Bell };
const LAYER_TITLES = { boundaries: "Boundaries", sensors: "Devices", deviceLabels: "Device labels", deviceHealth: "Device health", cameraViews: "Camera coverage", alerts: "Alerts" };

function newId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function locationText([lng, lat]: Position) {
  return `${Math.abs(lat).toFixed(5)}° ${lat < 0 ? "S" : "N"} · ${Math.abs(lng).toFixed(5)}° ${lng < 0 ? "W" : "E"}`;
}

function alertMapPoints(alert: Alert, boundaries: Boundary[]) {
  const boundary = boundaries.find((zone) => zone.id === alert.zoneId);
  return boundary ? [...boundary.vertices, alert.observation.position] : [alert.observation.position];
}

function gatewayMapPoints(incident: GatewayIncident, sensors: Sensor[], boundaries: Boundary[]) {
  const sensorIds = new Set(incident.devices.map((device) => device.sensorId));
  const boundaryIds = new Set(gatewayBoundaries(incident).map((boundary) => boundary.id));
  return [...sensors.filter((sensor) => sensorIds.has(sensor.id)).map((sensor) => sensor.position),
    ...boundaries.filter((boundary) => boundaryIds.has(boundary.id)).flatMap((boundary) => boundary.vertices)];
}

const EMPTY_SITE: Workspace = { sensors: [], boundaries: [], groups: [], alerts: [], received: [] };
type EnterpriseWorkspaceAccess = { current: SiteSession; sites: SiteSession[]; incidentId?: string; incidentReplay?: boolean;
  visit: (session: SiteSession, incidentId?: string, replay?: boolean) => void;
  onAccessEnded: () => void; controls: ReactNode };

export function GlobalView({ enterprise }: { enterprise?: EnterpriseWorkspaceAccess }) {
  const [{ workspace: localWorkspace, receipt }, setSession] = useState<{ workspace: Workspace; receipt?: { message: string; alert?: Alert; points?: Position[] } }>(
    () => ({ workspace: enterprise ? EMPTY_SITE : loadWorkspace() }),
  );
  const [selection, setSelection] = useState<Selection | null>(null);
  const [view, setView] = useState<"map" | "list">(readViewPreference);
  const [mapFailed, setMapFailed] = useState(false);
  const [listSection, setListSection] = useState<ListSection>("alerts");
  const listView = view === "list" || mapFailed;
  const [replayEvent, setReplayEvent] = useState<SiteEvent | null>(null);
  const [inventoryOpen, setInventoryOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [compact, setCompact] = useState(() => window.matchMedia(COMPACT_WORKSPACE).matches);
  const [focus, setFocus] = useState<{ points: Position[] } | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [incidentFilters, setIncidentFilters] = useState(DEFAULT_INCIDENT_FILTERS);
  const [floorId, setFloorId] = useState("");
  const [telemetry, setTelemetry] = useState<TelemetryConnection>();
  const [manualConnection, setSiteSession] = useState<SiteSession>();
  const [insights, setInsights] = useState<{ scope: string; view: SiteInsightsView }>();
  const siteConnection = enterprise?.current ?? manualConnection;
  const [connectedSites, setConnectedSites] = useState<SiteSession[]>([]);
  const [dispatchAlert, setDispatchAlert] = useState<{ siteId: string; incidentId: string; replay?: boolean } | undefined>(() => enterprise?.incidentId ? {
    siteId: enterprise.current.operator.siteId, incidentId: enterprise.incidentId, replay: enterprise.incidentReplay,
  } : undefined);
  const dispatchSites = enterprise?.sites ?? connectedSites;
  const dispatchState = useOrganizationDispatch(dispatchSites);
  const [siteDraft, setSiteDraft] = useState<SiteDraft>();
  const [importRequest, setImportRequest] = useState(0);
  const siteOperator = useSiteOperator(siteConnection, localWorkspace);
  const siteSession = siteOperator.session;
  const insightsScope = siteSession ? JSON.stringify([siteSession.base, siteSession.operator.subject, siteSession.operator.cameras]) : "local";
  const editingSite = siteDraft && canManageSite(siteSession) && siteOperator.status !== "revoked" ? siteDraft : undefined;
  const authoritativeSite = siteOperator.publishedVersion > 0;
  const enterpriseBase = enterprise?.current.base;
  const workspace = useMemo(() => {
    const current = editingSite ? { ...siteOperator.workspace, ...editingSite.configuration } : siteOperator.workspace;
    return enterpriseBase ? enterpriseWorkspace(current, enterpriseBase) : current;
  }, [siteOperator.workspace, editingSite, enterpriseBase]);

  useEffect(() => {
    if (siteOperator.status === "revoked") enterprise?.onAccessEnded();
  }, [siteOperator.status]);
  const [initialReplayEvent, setInitialReplayEvent] = useState<SiteEvent>();
  const [searchOpen, setSearchOpen] = useState(false);
  const [evidenceSearch, setEvidenceSearch] = useState<{ incident?: Alert }>();
  const [cameraWorkspace, setCameraWorkspace] = useState<{ cameraId?: string; revision: number } | null>(null);
  const enterpriseWall = useRef<{ preferences?: WallPreferences; save: (value: WallPreferences) => void }>({
    save: (preferences) => { enterpriseWall.current.preferences = preferences; },
  });
  const [layers, setLayers] = useState({ boundaries: true, sensors: true, deviceLabels: true, deviceHealth: true, cameraViews: true, alerts: true });
  const [healthClock, setHealthClock] = useState(0);
  const [healthFilter, setHealthFilter] = useState<HealthStatus | null>(null);
  const [triageFocus, setTriageFocus] = useState<TriageFocus | null>(null);
  const [inventoryTab, setInventoryTab] = useState<"site" | "devices" | "boundaries">("devices");
  const [reviewTab, setReviewTab] = useState<"alerts" | "posture">("alerts");
  const [groupFilter, setGroupFilter] = useState(ALL_GROUPS);
  const [labelFilter, setLabelFilter] = useState("");
  const [notice, setNotice] = useState("");
  const [storageError, setStorageError] = useState(false);
  const importInput = useRef<HTMLInputElement>(null);
  const inspector = useRef<HTMLElement>(null);
  const inventory = useRef<HTMLElement>(null);
  const siteList = useRef<HTMLElement>(null);
  const mapButton = useRef<HTMLButtonElement>(null);
  const searchOrigin = useRef<FocusOrigin>({});
  const cameraOrigin = useRef<FocusOrigin>({});
  const reviewOrigin = useRef<FocusOrigin>({});
  const inventoryOrigin = useRef<FocusOrigin>({});
  const editorOrigin = useRef<FocusOrigin>({});
  const focusDestination = useRef<FocusDestination | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const replaying = useRef(false);
  const { boundaries, sensors, groups, alerts } = workspace;
  const floors = workspace.floors ?? NO_FLOORS;
  const activeFloor = floors.find((floor) => floor.id === (editor?.kind === "sensor" || editor?.kind === "boundary" ? editor.value.floorId : floorId));
  const levelSensors = useMemo(() => sensors.filter((device) => device.floorId === activeFloor?.id), [sensors, activeFloor?.id]);
  const levelBoundaries = useMemo(() => boundaries.filter((zone) => zone.floorId === activeFloor?.id), [boundaries, activeFloor?.id]);
  const gatewayIncidents = workspace.gatewayIncidents ?? NO_GATEWAY_INCIDENTS;
  const activeGatewayIncidents = gatewayIncidents.filter((incident) => !incident.recoveredAt);
  const healthNow = useMemo(() => Math.max(Date.now(), healthClock), [sensors, healthClock]);
  const healthById = useMemo(() => new Map(sensors.map((sensor) => [sensor.id, getApplianceHealth(sensor, healthNow)])), [sensors, healthNow]);
  const maintenanceById = useMemo(() => {
    const active = new Map<string, MaintenanceWindow>();
    for (const sensor of sensors) {
      const window = activeMaintenance(sensor, healthNow);
      if (window) active.set(sensor.id, window);
    }
    return active;
  }, [sensors, healthNow]);
  const cameras = useMemo(() => sensors.filter((device) => device.modality === "vision"), [sensors]);
  const videoChecks = useVideoHealth(cameras);
  const legacyAnalytics = useMemo(() => authoritativeSite || editingSite ? [] : cameras, [authoritativeSite, cameras, editingSite]);
  const telemetryStatus = useSiteTelemetry(telemetry, (events) => {
    if (authoritativeSite) return;
    setSession((current) => {
      let workspace = current.workspace;
      const now = Date.now();
      let created = 0;
      for (const event of events) {
        if (!event || typeof event !== "object" || !("type" in event)) continue;
        if ("nativeAnalytics" in event) continue;
        try {
          if (event.type === "health") workspace = recordHealthReport(workspace, parseHealthReport(event, now));
          else if (event.type === "observation") {
            const result = evaluateObservation(workspace, parseObservation(event), "Site gateway", true);
            workspace = result.workspace;
            created += result.created.filter((alert) => !alert.correlation).length;
          }
        } catch { /* Adapters may report devices that this workspace has not enrolled. */ }
      }
      if (workspace === current.workspace) return current;
      return { workspace: reconcileGatewayIncidents(workspace, now), receipt: created ? { message: `${created} new site ${created === 1 ? "incident" : "incidents"}.` } : current.receipt };
    });
  });
  useGatewayEvents(legacyAnalytics, (events) => {
    setSession((current) => {
      let workspace = current.workspace;
      let created = 0;
      for (const event of events) {
        if (!workspace.sensors.some((camera) => camera.id === event.sensorId && camera.modality === "vision" && camera.cameraGatewayKind)) continue;
        const result = evaluateObservation(workspace, event, "Camera gateway");
        workspace = result.workspace;
        created += result.created.filter((alert) => !alert.correlation).length;
      }
      return workspace === current.workspace ? current : {
        workspace, receipt: created ? { message: `${created} new camera ${created === 1 ? "alert" : "alerts"}.` } : undefined,
      };
    });
  });
  const coverage = useMemo(() => {
    try { return calculateCameraCoverage(levelSensors, levelBoundaries); }
    catch (error) { console.warn("Unable to calculate camera coverage", error); return null; }
  }, [levelBoundaries, levelSensors]);
  const effectiveLabelFilter = sensors.some((sensor) => sensor.labels.some((label) => labelKey(label) === labelFilter)) ? labelFilter : "";
  const visibleDevices = sensors.filter((sensor) => (groupFilter === ALL_GROUPS || (sensor.groupId ?? UNGROUPED) === groupFilter)
    && (!effectiveLabelFilter || sensor.labels.some((label) => labelKey(label) === effectiveLabelFilter)) && (!floorId || sensor.floorId === floorId));
  const selectedAlert = selection?.kind === "alert" ? alerts.find((alert) => alert.id === selection.id) : undefined;
  const correlatedAlert = selectedAlert && alerts.find((alert) => alert.correlation?.incidentId === selectedAlert.id);
  const showReplay = !!(selectedAlert && replayEvent && !editor && reviewOpen);
  replaying.current = showReplay;
  const selectedGateway = selection?.kind === "gateway" ? gatewayIncidents.find((incident) => incident.id === selection.id) : undefined;
  const selectedBoundary = selection?.kind === "boundary" ? boundaries.find((zone) => zone.id === selection.id) : undefined;
  const selectedSensor = selection?.kind === "sensor" ? sensors.find((sensor) => sensor.id === selection.id) : undefined;
  const selectedCamera = selectedSensor?.modality === "vision" ? selectedSensor : undefined;
  const linkedCamera = selectedSensor?.modality === "access" ? associatedCamera(selectedSensor, cameras) : undefined;
  const evidenceCamera = selectedAlert ? incidentCamera(selectedAlert, sensors) : undefined;
  const archive = useIncidentArchive(selectedAlert, evidenceCamera);
  const replayAlert = useMemo(() => selectedAlert && archive?.state === "available"
    ? { ...selectedAlert, observation: { ...selectedAlert.observation, recording: archive.recording } } : selectedAlert, [selectedAlert, archive]);
  const hasReview = !!editor || (reviewOpen && (!listView || !!selection));
  const hasInventory = !listView && inventoryOpen && !(compact && hasReview);
  const showReview = !cameraWorkspace && hasReview;
  const showInventory = !cameraWorkspace && hasInventory;
  const surface = cameraWorkspace ? "cameras" : listView ? "list" : "map";
  const viewingCameraId = showReview && !editor ? selectedCamera?.id : undefined;
  const sensorIds = new Set(sensors.map((sensor) => sensor.id));
  const posture = boundaries.map((boundary) => {
    const assignedCount = boundary.sensorIds.filter((id) => sensorIds.has(id)).length;
    return { boundary, assignedCount, visibility: boundaryVisibility(boundary, assignedCount) };
  });
  const selectedVisibility = posture.find(({ boundary }) => boundary.id === selectedBoundary?.id)?.visibility;
  const editingSensor = editor?.kind === "sensor" ? editor : null;
  const savedDevice = editingSensor ? sensors.find((sensor) => sensor.id === editingSensor.value.id) : undefined;
  const devicePosition = editingSensor ? readCoordinates(editingSensor.value.coordinates) : null;
  const cameraDraft = editingSensor && (editingSensor.value.modality === "vision" || savedDevice?.modality === "vision")
    ? { id: editingSensor.value.id, position: devicePosition,
        blockers: levelBoundaries.filter((boundary) => boundary.blocksCameraView).map((boundary) => boundary.vertices),
        view: editingSensor.value.modality === "vision" ? readCameraView(editingSensor.value.cameraView) : null }
    : undefined;
  const linkedDeviceCount = savedDevice?.modality === "vision"
    ? sensors.filter((sensor) => sensor.modality === "access" && sensor.linkedCameraId === savedDevice.id).length : 0;
  const showPosture = listView ? listSection === "posture" : reviewOpen && reviewTab === "posture";
  const activeFilter = showPosture && (listView || (!editor && !selectedSensor && !selectedAlert && !selectedGateway)) ? healthFilter : null;
  const siteAssets = useSiteAssets(siteSession, siteOperator.publishedVersion,
    showPosture && !activeFilter && !editingSite && siteOperator.status !== "revoked");
  const triageScope = activeFilter ? getHealthScope(sensors, boundaries, healthById, activeFilter) : null;
  const gatewayScope = selectedGateway && !editor ? {
    sensors: new Set(selectedGateway.devices.map((device) => device.sensorId)),
    boundaries: gatewayBoundaries(selectedGateway),
  } : null;
  const scopedBoundaryIds = gatewayScope || triageScope
    ? new Set((gatewayScope?.boundaries ?? triageScope?.boundaries ?? []).map((boundary) => boundary.id)) : undefined;
  const recoveredBoundaryIds = gatewayScope
    ? new Set(gatewayScope.boundaries.filter((boundary) => !boundary.affected).map((boundary) => boundary.id)) : undefined;
  const scopedDevices = gatewayScope ? sensors.filter((sensor) => gatewayScope.sensors.has(sensor.id)) : triageScope?.devices;
  const replayBoundaryIds = showReplay ? new Set(replayEvent?.boundaries.map((boundary) => boundary.id)) : undefined;
  const showCameraGaps = showPosture && layers.boundaries && !scopedDevices;
  const gatewayLegend = gatewayScope ? [
    ...(gatewayScope.boundaries.some((boundary) => boundary.affected) ? [{ color: "#ff6600", label: "Assigned to affected devices" }] : []),
    ...(recoveredBoundaryIds?.size ? [{ color: RECOVERED_BOUNDARY_COLOR, label: "Assigned devices recovered" }] : []),
  ] : undefined;
  const mapLegend = gatewayLegend ?? (triageScope ? (triageScope.boundaries.length
    ? [{ color: "#ff6600", label: "Assigned to filtered devices" }] : []) : [
    ...(layers.cameraViews && coverage?.sectors.length ? [CAMERA_COVERAGE_COLORS.view] : []),
    ...(layers.cameraViews && coverage?.overlaps.length ? [CAMERA_COVERAGE_COLORS.overlap] : []),
    ...(layers.cameraViews && showCameraGaps && coverage?.gaps.length ? [CAMERA_COVERAGE_COLORS.gap] : []),
    ...(showPosture && layers.boundaries ? Object.entries(BOUNDARY_VISIBILITY)
      .filter(([status]) => posture.some((area) => area.visibility === status)).map(([, entry]) => entry) : []),
  ]);
  const newAlerts = alerts.filter((alert) => alert.status === "new" && !alert.correlation).length;
  const activeAlerts = mapAlertScope(alerts.filter((alert) => (listView || alert.floorId === activeFloor?.id) && (!alert.correlation || alert.id === selectedAlert?.id)), selectedAlert, scopedDevices && scopedBoundaryIds
    ? { devices: new Set(scopedDevices.map((device) => device.id)), boundaries: scopedBoundaryIds } : undefined);
  const editingBoundary = editor?.kind === "boundary" ? editor : null;
  const draft = editingBoundary ? (editingBoundary.corner ? [editingBoundary.corner] : editingBoundary.value.vertices) : NO_VERTICES;
  const placingDevice = editingSensor?.placement === "location";
  const aimingCamera = editingSensor?.placement === "aim";
  const searchTargets = useMemo<SearchTarget[]>(() => [
    ...sensors.map((sensor): SearchTarget => ({
      kind: "sensor", id: sensor.id, name: sensor.name, detail: MODALITY_LABEL[sensor.modality],
      terms: [sensor.id, sensor.mapLabel, groups.find((group) => group.id === sensor.groupId)?.name ?? "",
        healthText(healthById.get(sensor.id) ?? { status: "unknown" }), sensor.gatewayId ?? "", ...sensor.labels],
    })),
    ...boundaries.map((zone): SearchTarget => ({
      kind: "boundary", id: zone.id, name: zone.name, detail: "Boundary · " + zone.kind,
      terms: [zone.mapLabel ?? "", ...(zone.labels ?? [])],
    })),
    ...alerts.map((alert): SearchTarget => ({
      kind: "alert", id: alert.id, name: alert.observation.summary, detail: (alert.correlation ? "Correlated event · " : "Alert · ") + alert.zoneName,
      terms: [alert.sensorName, alert.severity, alert.status],
    })),
    ...gatewayIncidents.map((incident): SearchTarget => ({
      kind: "gateway", id: incident.id, name: "Gateway outage · " + incident.gatewayId,
      detail: incident.recoveredAt ? "Recovered" : "Active", terms: incident.devices.map((device) => device.name),
    })),
  ], [alerts, boundaries, gatewayIncidents, groups, healthById, sensors]);

  function setWorkspace(update: (workspace: Workspace) => Workspace) {
    setSession((current) => ({ ...current, workspace: update(current.workspace) }));
  }

  function canEditConfiguration() {
    if (!siteSession || editingSite) return true;
    setNotice(canManageSite(siteSession) ? "Open the site configuration draft in Posture to edit the map." : "Site configuration requires a site supervisor.");
    return false;
  }

  function setConfiguration(update: (workspace: Workspace) => Workspace) {
    if (editingSite) {
      setSiteDraft((current) => current && { ...current, configuration: siteInventory(update({ ...siteOperator.workspace, ...current.configuration })) });
    } else if (!siteSession) setWorkspace(update);
  }

  const requestFocus = useCallback((destination: FocusDestination) => {
    focusDestination.current = destination;
    setFocusRequest((version) => version + 1);
  }, []);

  useLayoutEffect(() => {
    if (searchOpen || !focusDestination.current) return;
    const destination = focusDestination.current;
    focusDestination.current = null;
    const visible = (element?: HTMLElement | null): element is HTMLElement => !!element?.isConnected && element.getClientRects().length > 0 && !element.matches(":disabled");
    const heading = (panel: HTMLElement | null) => panel?.querySelector<HTMLElement>("h2, h3") ?? panel;
    let target: HTMLElement | null | undefined;
    if (destination === "review") target = heading(inspector.current);
    else if (destination === "inventory") target = heading(inventory.current);
    else if (destination === "list") target = heading(siteList.current);
    else if (destination === "replay-launcher") target = inspector.current?.querySelector<HTMLElement>(".gv-open-replay");
    else if (destination === "view") target = mapButton.current;
    else {
      target = destination.element;
      if (!visible(target) && destination.key) {
        target = [...document.querySelectorAll<HTMLElement>("[data-focus-key]")]
          .find((element) => element.dataset.focusKey === destination.key && visible(element));
      }
    }
    if (!visible(target)) target = heading(showReview ? inspector.current : siteList.current) ?? mapButton.current;
    if (!visible(target)) target = mapButton.current;
    if (!visible(target)) return;
    if (!target.matches("button, a, input, select, textarea, summary")) target.tabIndex = -1;
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [cameraWorkspace, focusRequest, listView, searchOpen, showReview]);

  function openCameras(element: HTMLButtonElement, cameraId?: string) {
    cameraOrigin.current = { element };
    setCameraWorkspace((current) => ({ cameraId, revision: (current?.revision ?? 0) + 1 }));
  }

  function editFromCameras(camera?: Sensor) {
    setCameraWorkspace(null);
    if (listView) setListSection("devices");
    if (camera) select("sensor", camera.id, true, cameraOrigin.current);
    else closeReview(false);
    startSensor(camera);
  }

  function showCameraOnMap(camera: Sensor) {
    setCameraWorkspace(null);
    if (listView) switchWorkspace("map", "review");
    setLayers((current) => ({ ...current, sensors: true, cameraViews: true }));
    if (!visibleDevices.some((device) => device.id === camera.id)) {
      setGroupFilter(ALL_GROUPS);
      setLabelFilter("");
    }
    setReviewTab("alerts");
    select("sensor", camera.id, true, cameraOrigin.current);
  }

  function showCoverageOnMap(boundary?: Boundary) {
    setCameraWorkspace(null);
    if (listView) switchWorkspace("map", "review");
    setLayers((current) => ({ ...current, boundaries: true, cameraViews: true }));
    setReviewTab("posture");
    setReviewOpen(true);
    setHealthFilter(null);
    setTriageFocus(null);
    setReplayEvent(null);
    setSelection(boundary ? { kind: "boundary", id: boundary.id } : null);
    if (boundary) setFloorId(boundary.floorId ?? "");
    const points = boundary?.vertices ?? boundaries.flatMap((area) => area.vertices);
    if (points.length) setFocus({ points });
    reviewOrigin.current = cameraOrigin.current;
    requestFocus("review");
  }

  function openSearch(origin = currentFocus()) {
    searchOrigin.current = origin;
    setSearchOpen(true);
  }

  function cancelSearch() {
    setSearchOpen(false);
    requestFocus(searchOrigin.current);
  }

  function openEvidenceSearch(incident?: Alert) {
    if (!searchOpen) searchOrigin.current = currentFocus();
    setSearchOpen(false);
    setEvidenceSearch({ incident });
  }

  function currentListSection(): ListSection {
    if (selection?.kind === "alert" || selection?.kind === "gateway") return "alerts";
    if (reviewOpen && reviewTab === "posture") return "posture";
    if (selection?.kind === "sensor") return "devices";
    if (selection?.kind === "boundary") return "boundaries";
    if (showInventory) return inventoryTab === "devices" ? "devices" : "boundaries";
    return "alerts";
  }

  function switchWorkspace(next: "map" | "list", destination: FocusDestination) {
    setCameraWorkspace(null);
    if (next === "list") {
      if (!listView) setListSection(currentListSection());
    }
    else {
      setMapFailed(false);
      if (listView && listSection === "posture") setReviewTab("posture");
    }
    setView(next);
    try { localStorage.setItem(VIEW_PREFERENCE, next); } catch { /* The view remains available for this session. */ }
    requestFocus(destination);
  }

  function navigateSection(section: ListSection) {
    setCameraWorkspace(null);
    if (listView) openListSection(section);
    else if (section === "devices" || section === "boundaries") toggleInventory(section);
    else toggleReview(section);
  }

  function fallBackToList() {
    setListSection(currentListSection());
    setMapFailed(true);
    const active = document.activeElement;
    if (!searchOpen && !cameraWorkspace && !inspector.current?.contains(active)) requestFocus(selection || editor ? "review" : "list");
  }

  const receivePolledReport = useCallback((report: HealthReport, url: string) => {
    const now = Date.now();
    setSession((current) => {
      if (!current.workspace.sensors.some((sensor) => sensor.id === report.sensorId && sensor.healthUrl === url)) return current;
      const workspace = recordHealthReport(current.workspace, report);
      return workspace === current.workspace ? current : { ...current, workspace: reconcileGatewayIncidents(workspace, now) };
    });
  }, []);

  useEffect(() => {
    setSession((current) => {
      const workspace = reconcileGatewayIncidents(current.workspace, healthNow);
      return workspace === current.workspace ? current : { ...current, workspace };
    });
  }, [healthNow, boundaries, sensors]);

  useEffect(() => {
    const deadlines = Array.from(healthById.values()).flatMap((health) => health.status === "online" && health.staleAt ? [health.staleAt] : []);
    for (const report of Object.values(workspace.dependencyHealth ?? {})) {
      const staleAt = Date.parse(report.occurredAt) + REPORT_INTERVAL.default * 3000;
      if (report.state === "online" && staleAt > healthNow) deadlines.push(staleAt);
    }
    for (const sensor of sensors) for (const window of sensor.maintenanceWindows ?? []) {
      for (const time of [window.startsAt, window.endsAt].map(Date.parse)) if (time > healthNow) deadlines.push(time);
    }
    if (!deadlines.length) return;
    const next = Math.min(...deadlines);
    const timer = window.setTimeout(() => setHealthClock(Date.now()), Math.min(2_147_483_647, Math.max(1, next - Date.now())));
    return () => window.clearTimeout(timer);
  }, [healthById, healthNow, sensors, workspace.dependencyHealth]);

  useEffect(() => {
    const media = window.matchMedia(COMPACT_WORKSPACE);
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    inspector.current?.scrollTo({ top: 0 });
  }, [viewingCameraId, selectedAlert?.id, selectedGateway?.id, showReplay]);

  useEffect(() => {
    if (!receipt) return;
    setNotice(receipt.message);
    if (receipt.alert && !listView && !editor && !searchOpen && !cameraWorkspace && !replaying.current) {
      setReplayEvent(null);
      setHealthFilter(null);
      setTriageFocus(null);
      setReviewOpen(true);
      setReviewTab("alerts");
      setSelection({ kind: "alert", id: receipt.alert.id });
      setFloorId(receipt.alert.floorId ?? "");
      setFocus({ points: receipt.points ?? [receipt.alert.observation.position] });
      setLayers((current) => ({ ...current, alerts: true, boundaries: true }));
    }
  }, [receipt]);

  useEffect(() => {
    if (enterpriseBase) return;
    try { saveWorkspace(localWorkspace); setStorageError(false); }
    catch { setStorageError(true); }
  }, [localWorkspace, enterpriseBase]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 6500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    function onEscape(event: KeyboardEvent) {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey) && !editor) {
        event.preventDefault();
        if (!searchOpen) openSearch();
        return;
      }
      if (event.key !== "Escape" || searchOpen || cameraWorkspace || document.fullscreenElement || event.defaultPrevented) return;
      if (editor) {
        if (!inspector.current?.querySelector("[data-camera-connecting='true']")) cancelEditor();
      }
      else if (showReplay && selectedAlert) leaveReplay(selectedAlert);
      else if (showReview) closeReview();
      else if (showInventory) closeInventory();
    }
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [boundaries, cameraWorkspace, editor, listSection, listView, searchOpen, selectedAlert, selection, showInventory, showReplay, showReview]);

  function closeReview(restoreFocus = true) {
    const returnToPosture = restoreFocus && !!selection && reviewOrigin.current.returnToPosture && !listView;
    setReviewOpen(!!returnToPosture);
    if (returnToPosture) setReviewTab("posture");
    setSelection(null);
    setReplayEvent(null);
    if (!(listView && listSection === "posture")) {
      setHealthFilter(null);
      setTriageFocus(null);
    }
    if (restoreFocus) {
      const origin = reviewOrigin.current;
      requestFocus(listView && selection ? { ...origin, key: origin.key ?? selection.kind + selection.id } : origin);
    }
  }

  function closeInventory() {
    setInventoryOpen(false);
    requestFocus(inventoryOrigin.current);
  }

  function openListSection(section: ListSection) {
    closeReview(false);
    setHealthFilter(null);
    setTriageFocus(null);
    setListSection(section);
    requestFocus("list");
  }

  function toggleInventory(tab: typeof inventoryTab) {
    if (showInventory && inventoryTab === tab) {
      closeInventory();
      return;
    }
    inventoryOrigin.current = currentFocus();
    if (compact && showReview) closeReview(false);
    setInventoryOpen(!showInventory || inventoryTab !== tab);
    setInventoryTab(tab);
    requestFocus("inventory");
  }

  function toggleReview(tab: typeof reviewTab) {
    if (showReview && tab === "posture" && healthFilter) {
      filterHealth(null);
      return;
    }
    if (showReview && reviewTab === tab && !selection) {
      closeReview();
      return;
    }
    setSelection(null);
    setReplayEvent(null);
    setHealthFilter(null);
    setTriageFocus(null);
    setReviewTab(tab);
    setReviewOpen(true);
    reviewOrigin.current = currentFocus();
    requestFocus("review");
    if (tab === "posture") setLayers((current) => ({ ...current, boundaries: true }));
  }

  function select(kind: Selection["kind"], id: string, move = false, origin = currentFocus()) {
    if (kind === "alert") {
      const candidate = alerts.find((entry) => entry.id === id);
      const incidentId = candidate?.correlation?.incidentId;
      if (incidentId && alerts.some((entry) => entry.id === incidentId)) id = incidentId;
    }
    if (kind !== "gateway") {
      const source = kind === "alert" ? alerts : kind === "sensor" ? sensors : boundaries;
      setFloorId(source.find((entry) => entry.id === id)?.floorId ?? "");
    }
    reviewOrigin.current = origin;
    setReviewOpen(true);
    setSelection({ kind, id });
    setReplayEvent(null);
    setInitialReplayEvent(undefined);
    if (!(listView && listSection === "posture")) {
      setHealthFilter(null);
      setTriageFocus(null);
    }
    requestFocus("review");
    if (kind === "alert" || kind === "gateway") {
      setReviewTab("alerts");
      setLayers((current) => ({ ...current, alerts: true, boundaries: true }));
    }
    if (!move) return;
    if (kind === "gateway") {
      const incident = gatewayIncidents.find((entry) => entry.id === id);
      if (incident) fitGateway(incident);
    } else if (kind === "alert") {
      const alert = alerts.find((entry) => entry.id === id);
      if (alert) setFocus({ points: alertMapPoints(alert, boundaries) });
    } else if (kind === "sensor") {
      const sensor = sensors.find((entry) => entry.id === id);
      if (sensor) setFocus({ points: [sensor.position] });
    } else {
      const boundary = boundaries.find((entry) => entry.id === id);
      if (boundary) setFocus({ points: boundary.vertices });
    }
  }

  function jumpToReplayEvent(event: SiteEvent) {
    setReplayEvent(event);
    setFloorId(event.floorId ?? "");
    if (event.observation.position) setFocus({ points: [event.observation.position] });
  }

  function openReplay(alert: Alert) {
    const key = observationKey(alert.observation);
    const anchor = incidentTimeline(workspace, alert).find((event) => observationKey(event.observation) === key);
    jumpToReplayEvent(anchor ?? eventFromAlert(alert));
    setInitialReplayEvent(undefined);
    requestFocus("review");
  }

  function leaveReplay(alert: Alert) {
    setReplayEvent(null);
    setFloorId(alert.floorId ?? "");
    setInitialReplayEvent(undefined);
    setFocus({ points: alertMapPoints(alert, boundaries) });
    requestFocus("replay-launcher");
  }

  function backToAlerts(id: string, kind: "alert" | "gateway") {
    if (listView) {
      closeReview();
      return;
    }
    setSelection(null);
    setTriageFocus(null);
    requestFocus({ key: kind + id });
  }

  function fitGateway(incident: GatewayIncident) {
    const points = gatewayMapPoints(incident, sensors, boundaries);
    setTriageFocus(null);
    if (points.length) setFocus({ points });
  }

  function filterHealth(status: HealthStatus | null) {
    setHealthFilter(status);
    setTriageFocus(null);
    setSelection(null);
    if (!status) return;
    const scope = getHealthScope(sensors, boundaries, healthById, status);
    const points = [...scope.devices.map((sensor) => sensor.position), ...scope.boundaries.flatMap((boundary) => boundary.vertices)];
    if (points.length) setFocus({ points });
  }

  function focusTriage(target: TriageFocus) {
    const sensor = target.kind === "sensor" ? sensors.find((entry) => entry.id === target.id) : undefined;
    const boundary = target.kind === "boundary" ? boundaries.find((entry) => entry.id === target.id) : undefined;
    const points = sensor ? [sensor.position] : boundary?.vertices;
    if (!points?.length) return;
    setTriageFocus(target);
    setFloorId(sensor?.floorId ?? boundary?.floorId ?? "");
    setFocus({ points });
  }

  async function planMaintenance(sensorId: string, start: number, end: number, reason: string) {
    const now = Date.now();
    try {
      const result = scheduleMaintenance(workspace, sensorId, start, end, reason, now);
      if (siteSession) {
        if (!siteOperator.ready) return "Wait for the shared site connection before scheduling maintenance.";
        await siteOperator.maintenance({ sensorId, mode: "schedule", startsAt: new Date(start).toISOString(), endsAt: new Date(end).toISOString(), reason });
        return;
      }
      const windows = result.sensors.find((sensor) => sensor.id === sensorId)?.maintenanceWindows;
      setWorkspace((current) => ({ ...current, sensors: current.sensors.map((sensor) => sensor.id === sensorId
        ? { ...sensor, maintenanceWindows: windows } : sensor) }));
    } catch (error) { return error instanceof Error ? error.message : "Unable to schedule maintenance."; }
  }

  function jumpTo(target: SearchTarget) {
    setSearchOpen(false);
    setCameraWorkspace(null);
    if (listView) setListSection(target.kind === "sensor" ? "devices" : target.kind === "boundary" ? "boundaries" : "alerts");
    if (target.kind === "sensor") {
      setLayers((current) => ({ ...current, sensors: true }));
      if (!visibleDevices.some((device) => device.id === target.id)) {
        setGroupFilter(ALL_GROUPS);
        setLabelFilter("");
      }
    }
    if (target.kind === "boundary") setLayers((current) => ({ ...current, boundaries: true }));
    select(target.kind, target.id, true, {
      ...searchOrigin.current,
      key: listView ? target.kind + target.id : searchOrigin.current.key,
      element: listView && compact ? undefined : searchOrigin.current.element,
    });
  }

  function addPoint(position: Position) {
    setEditor((current) => {
      if (current?.kind === "boundary") return placeBoundaryPoint(current, position);
      if (current?.kind !== "sensor" || current.placement === "idle") return current;
      if (current.placement === "location") return { ...current, value: { ...current.value, coordinates: toCoordinateDraft(position) } };

      const origin = readCoordinates(current.value.coordinates);
      const bearing = origin && bearingTo(origin, position);
      if (bearing === null || !current.value.cameraView) return current;
      return { ...current, placement: "idle", value: { ...current.value, cameraView: { ...current.value.cameraView, bearingDeg: String(bearing) } } };
    });
  }

  function editBoundaryDraft(update: (draft: BoundaryDraft) => BoundaryDraft) {
    setEditor((current) => current?.kind === "boundary" ? update(current) : current);
  }

  function startBoundary(boundary?: Boundary) {
    if (!canEditConfiguration()) return;
    editorOrigin.current = currentFocus();
    requestFocus("review");
    setReplayEvent(null);
    setEditor({ kind: "boundary", tool: boundary ? "vertices" : "polygon", corner: null, history: [], selectedVertex: null,
      labelsText: boundary?.labels?.join(", ") ?? "", value: boundary ?? {
        id: newId(), name: "", kind: "Perimeter", vertices: [], sensorIds: [], enabled: true, floorId: activeFloor?.id,
      } });
    if (boundary) setFocus({ points: boundary.vertices });
    setLayers((current) => ({ ...current, boundaries: true }));
  }

  function saveBoundary(event: FormEvent, { value, labelsText }: BoundaryDraft) {
    event.preventDefault();
    const labels = parseLabels(labelsText);
    if (!value.name.trim() || validateBoundary(value.vertices) || !validLabels(labels)) return;
    const boundary = { ...value, name: value.name.trim(), mapLabel: value.mapLabel?.trim(), notes: value.notes?.trim(), labels };
    setConfiguration((current) => ({ ...current, boundaries: current.boundaries.some((zone) => zone.id === boundary.id)
      ? current.boundaries.map((zone) => zone.id === boundary.id ? boundary : zone)
      : [...current.boundaries, boundary] }));
    setEditor(null);
    setReviewOpen(true);
    setSelection({ kind: "boundary", id: boundary.id });
    setFloorId(boundary.floorId ?? "");
    requestFocus("review");
    setNotice("Boundary saved.");
  }

  function startSensor(sensor?: Sensor, boundary?: Boundary) {
    if (!canEditConfiguration()) return;
    editorOrigin.current = currentFocus();
    requestFocus("review");
    setReplayEvent(null);
    const boundaryIds = sensor ? boundaries.filter((zone) => zone.sensorIds.includes(sensor.id)).map((zone) => zone.id)
      : boundary ? [boundary.id] : [];
    const reportInterval = String(sensor?.reportIntervalSeconds ?? REPORT_INTERVAL.default);
    setEditor({ kind: "sensor", placement: sensor || listView ? "idle" : "location", value: sensor ? {
      id: sensor.id, name: sensor.name, modality: sensor.modality, demo: sensor.demo, groupId: sensor.groupId, floorId: sensor.floorId, dependencyIds: sensor.dependencyIds,
      mapLabel: sensor.mapLabel, linkedCameraId: sensor.linkedCameraId, passageRuleId: sensor.passageRuleId,
      reportInterval, healthUrl: sensor.healthUrl ?? "", gatewayId: sensor.gatewayId ?? "",
      boundaryIds, coordinates: toCoordinateDraft(sensor.position), labelsText: sensor.labels.join(", "),
      cameraView: toCameraViewDraft(sensor.cameraView), cameraFeedUrl: sensor.cameraFeedUrl ?? "", cameraGridUrl: sensor.cameraGridUrl ?? "",
      cameraProvider: sensor.cameraProvider, cameraChannel: sensor.cameraChannel, cameraGatewayKind: sensor.cameraGatewayKind, analyticsRules: sensor.analyticsRules,
      analyticsPlacement: sensor.analyticsPlacement, inspectionStationId: sensor.inspectionStationId,
    } : {
      id: newId(), name: "", modality: "vision", coordinates: toCoordinateDraft(null), boundaryIds, demo: false, reportInterval, healthUrl: "", gatewayId: "", floorId: boundary?.floorId ?? activeFloor?.id,
      groupId: groups.find((group) => group.id === groupFilter)?.id ?? null, labelsText: "", mapLabel: "", cameraView: null, cameraFeedUrl: "", cameraGridUrl: "",
    } });
    if (boundary) setFocus({ points: boundary.vertices });
    setLayers((current) => ({ ...current, sensors: true }));
  }

  function saveSensor(value: DeviceDraft) {
    const { labelsText, coordinates, boundaryIds, cameraView: draftView, cameraFeedUrl: draftFeed, cameraGridUrl: draftGrid,
      cameraProvider, cameraChannel, cameraGatewayKind, reportInterval, pendingReport,
      healthUrl: draftHealthUrl, gatewayId: draftGatewayId, ...fields } = value;
    const position = readCoordinates(coordinates);
    const labels = parseLabels(labelsText);
    const cameraView = value.modality === "vision" ? readCameraView(draftView) : null;
    const reportIntervalSeconds = Number(reportInterval);
    if (!position || !value.name.trim() || !validLabels(labels) || !validReportInterval(reportIntervalSeconds)) return;
    const feed = value.modality === "vision" ? httpEndpointUrl(draftFeed) : null;
    const gridFeed = value.modality === "vision" ? httpEndpointUrl(draftGrid) : null;
    if (value.modality === "vision" && ((draftView && !cameraView) || (draftFeed.trim() && !feed) || (draftGrid.trim() && !gridFeed))) return;
    const healthUrl = httpEndpointUrl(draftHealthUrl);
    const gatewayId = normalizeGatewayId(draftGatewayId);
    const station = value.modality === "vision" ? fields.inspectionStationId?.trim() : undefined;
    if ((draftHealthUrl.trim() && !healthUrl) || (draftGatewayId.trim() && !gatewayId)) return;
    if (station && !validInspectionStationId(station)) return;
    const linkedCameraId = value.modality === "access" && cameras.some((camera) => camera.id !== value.id && camera.id === fields.linkedCameraId)
      ? fields.linkedCameraId : null;
    const sensor: Sensor = { ...fields, name: fields.name.trim(), mapLabel: fields.mapLabel.trim(), labels, position, cameraView,
      cameraFeedUrl: feed, cameraGridUrl: gridFeed,
      cameraProvider: value.modality === "vision" ? cameraSourceLabel(cameraProvider) || undefined : undefined,
      cameraChannel: value.modality === "vision" ? cameraSourceLabel(cameraChannel) || undefined : undefined,
      cameraGatewayKind: value.modality === "vision" && feed ? cameraGatewayKind : undefined,
      analyticsRules: value.modality === "vision" && fields.analyticsRules?.length ? fields.analyticsRules : undefined,
      analyticsPlacement: value.modality === "vision" ? fields.analyticsPlacement : undefined,
      inspectionStationId: station || undefined,
      linkedCameraId, passageRuleId: linkedCameraId ? fields.passageRuleId : undefined,
      reportIntervalSeconds, healthUrl, gatewayId: gatewayId ?? undefined };
    const existing = sensors.some((entry) => entry.id === value.id);
    const savedAt = Date.now();
    setConfiguration((current) => {
      const previous = current.sensors.find((device) => device.id === sensor.id);
      const historical = previous && getOutageHistory(previous, savedAt);
      const cameraVerifiedAt = previous && sensor.cameraView && previous.position.every((axis, index) => axis === sensor.position[index])
        && JSON.stringify(previous.cameraView) === JSON.stringify(sensor.cameraView) && previous.cameraFeedUrl === sensor.cameraFeedUrl
        ? previous.cameraVerifiedAt : undefined;
      const saved = saveDeviceAssignments(current, { ...sensor, cameraVerifiedAt,
        cameraInspection: sensor.modality === "vision" && previous?.cameraFeedUrl === sensor.cameraFeedUrl ? previous?.cameraInspection : undefined,
        healthReport: previous?.healthReport,
        healthOutages: historical?.length ? historical : undefined, maintenanceWindows: previous?.maintenanceWindows }, boundaryIds);
      return reconcileGatewayIncidents(pendingReport ? recordHealthReport(saved, pendingReport) : saved, savedAt);
    });
    if (groupFilter !== ALL_GROUPS && (sensor.groupId ?? UNGROUPED) !== groupFilter) setGroupFilter(sensor.groupId ?? UNGROUPED);
    if (effectiveLabelFilter && !labels.some((label) => labelKey(label) === effectiveLabelFilter)) setLabelFilter("");
    setEditor(null);
    setReviewOpen(true);
    setSelection({ kind: "sensor", id: sensor.id });
    setFloorId(sensor.floorId ?? "");
    requestFocus("review");
    setNotice(existing ? "Device updated." : "Device registered.");
  }

  function testDeviceRoute(value: DeviceDraft) {
    const position = readCoordinates(value.coordinates);
    const targets = boundaries.filter((boundary) => boundary.enabled && value.boundaryIds.includes(boundary.id));
    if (!position || !targets.length) return "Place the device and assign an enabled boundary first.";
    const device: Sensor = {
      id: value.id, name: value.name.trim() || "Draft device", modality: value.modality, position, demo: value.demo,
      groupId: value.groupId, labels: [], mapLabel: "", cameraView: null, cameraFeedUrl: null,
    };
    const trial = saveDeviceAssignments({ ...workspace, alerts: [], received: [] }, device, value.boundaryIds);
    const passed = targets.filter((boundary) => evaluateObservation(trial, {
      id: "setup-test", sensorId: value.id, summary: SAMPLE_SUMMARIES[value.modality], occurredAt: new Date().toISOString(),
      position: value.modality === "access" ? undefined : samplePosition(boundary.vertices),
    }, "Simulation").created.some((alert) => alert.zoneId === boundary.id));
    return passed.length === targets.length
      ? `Simulated route passed for ${passed.length} enabled ${passed.length === 1 ? "boundary" : "boundaries"}.`
      : `Simulated route passed for ${passed.length} of ${targets.length} enabled boundaries.`;
  }

  function attachTestReport(device: DeviceDraft, report: HealthReport) {
    setEditor((current) => {
      if (current?.kind !== "sensor" || current.value.id !== device.id || current.value.modality !== device.modality
        || !isNewerReport(report, current.value.pendingReport)) return current;
      return { ...current, value: { ...current.value, pendingReport: report } };
    });
  }

  function startGroup(group?: DeviceGroup) {
    if (!canEditConfiguration()) return;
    editorOrigin.current = currentFocus();
    requestFocus("review");
    setReplayEvent(null);
    setEditor({ kind: "group", value: group ?? { id: newId(), name: "" } });
  }

  function cancelEditor() {
    setEditor(null);
    requestFocus(editorOrigin.current);
  }

  function saveGroup(group: DeviceGroup) {
    const existing = groups.some((entry) => entry.id === group.id);
    setConfiguration((current) => ({ ...current, groups: existing
      ? current.groups.map((entry) => entry.id === group.id ? group : entry)
      : [...current.groups, group] }));
    if (!existing) { setGroupFilter(group.id); setLabelFilter(""); }
    setEditor(null);
    requestFocus(editorOrigin.current);
    setNotice(existing ? "Group renamed." : "Group created.");
  }

  function ingest(input: IncomingObservation | IncomingObservation[], origin: Alert["origin"]) {
    const observations = Array.isArray(input) ? input : [input];
    setSession((current) => {
      try {
        let workspace = current.workspace;
        const alerts: Alert[] = [];
        let accepted = 0;
        for (const observation of observations) {
          const result = evaluateObservation(workspace, observation, origin, origin === "Imported sample");
          workspace = result.workspace;
          if (!result.duplicate) accepted++;
          alerts.push(...result.created);
        }
        const created = alerts.filter((entry) => !entry.correlation);
        const grouped = alerts.length - created.length;
        const alert = created.sort((a, b) => Date.parse(b.observation.occurredAt) - Date.parse(a.observation.occurredAt))[0];
        const message = grouped ? `${accepted} events recorded · ${created.length} new alerts · ${grouped} correlated events.`
          : observations.length > 1 ? `${accepted} events recorded · ${alerts.length} alerts created.`
          : !accepted ? "This observation was already processed."
          : !alert ? "Event saved for replay."
          : alerts.length === 1 ? "A new alert was created." : `${alerts.length} new alerts were created.`;
        return { workspace, receipt: { message, alert, points: alert ? alertMapPoints(alert, workspace.boundaries) : undefined } };
      } catch (error) {
        return { ...current, receipt: { message: error instanceof Error ? error.message : "Unable to read observation." } };
      }
    });
  }

  async function importObservation(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const payload = await readMonitoringFile(file);
      if (payload && typeof payload === "object" && "type" in payload && payload.type === "health") {
        const now = Date.now();
        const report = parseHealthReport(payload, now);
        setSession((current) => {
          try {
            const next = recordHealthReport(current.workspace, report);
            return { workspace: reconcileGatewayIncidents(next, now), receipt: { message: next === current.workspace
              ? "A newer or equal health report is already available." : "Device health updated." } };
          } catch (error) {
            return { ...current, receipt: { message: error instanceof Error ? error.message : "Unable to record device health." } };
          }
        });
      } else if (payload && typeof payload === "object" && "type" in payload && payload.type === "telemetry") {
        if (!("observations" in payload) || !Array.isArray(payload.observations) || !payload.observations.length) {
          throw new Error("Provide a nonempty observations array in the telemetry file.");
        }
        ingest(payload.observations.map(parseObservation), "Imported sample");
      } else ingest(parseObservation(payload), "Imported sample");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Unable to read the file."); }
  }

  function simulate(zone?: Boundary, sensor?: Sensor) {
    if (siteSession) { setNotice("Use a configuration drill in Posture to test site alerts."); return; }
    const eligible = (boundary: Boundary) => boundary.enabled && (sensor ? boundary.sensorIds.includes(sensor.id) : boundary.sensorIds.length > 0);
    const target = zone ?? (boundaries.find((boundary) => boundary.kind === "Restricted" && eligible(boundary)) ?? boundaries.find(eligible));
    const source = sensor ?? sensors.find((entry) => target?.sensorIds.includes(entry.id));
    if (!target || !source || !eligible(target)) { setNotice("Enable a boundary and assign a sensor to run a detection test."); return; }
    ingest({ id: newId(), sensorId: source.id, position: source.modality === "access" ? undefined : samplePosition(target.vertices),
      occurredAt: new Date().toISOString(), summary: SAMPLE_SUMMARIES[source.modality] }, "Simulation");
  }

  function updateIncident(id: string, action: IncidentAction) {
    const alert = alerts.find((entry) => entry.id === id);
    if (siteSession && alert) {
      if (!siteOperator.ready) { setNotice("Wait for the shared site connection before changing an incident."); return; }
      void siteOperator.incident(alert, action).catch((issue) => setNotice(issue instanceof Error ? issue.message : "Unable to update the shared incident."));
      return;
    }
    const occurredAt = new Date().toISOString();
    setWorkspace((current) => ({ ...current, alerts: current.alerts.map((alert) => alert.id === id
      ? applyIncidentAction(alert, action, occurredAt) : alert) }));
  }

  function inspectCamera(id: string, cameraInspection?: Sensor["cameraInspection"]) {
    if (authoritativeSite && siteSession) {
      updateSiteCameraCheck(id, cameraInspection ? "inspect" : "clear", cameraInspection);
      return;
    }
    setWorkspace((current) => ({ ...current, sensors: current.sensors.map((sensor) => sensor.id === id && sensor.modality === "vision"
      ? { ...sensor, cameraInspection } : sensor) }));
  }

  function updateSiteCameraCheck(id: string, mode: "verify" | "inspect" | "clear", inspection?: Sensor["cameraInspection"]) {
    if (!siteSession || !siteOperator.ready || siteSession.operator.role === "viewer") return;
    void siteRequest(siteSession, "assets", { operation: "check-camera", version: siteOperator.publishedVersion, cameraId: id, mode,
      issue: inspection?.issue, note: inspection?.note }).then(() => setNotice("Site camera check saved."))
      .catch((issue) => setNotice(issue instanceof Error ? issue.message : "Could not save the site camera check."));
  }

  function selectFloor(id: string) {
    setFloorId(id);
    setSelection(null);
    setReplayEvent(null);
    const floor = floors.find((entry) => entry.id === id);
    if (floor) setFocus({ points: floor.corners });
  }

  const deviceList = <DeviceInventory devices={sensors} visible={visibleDevices} groups={groups} healthById={healthById} maintenanceById={maintenanceById}
    groupFilter={groupFilter} labelFilter={effectiveLabelFilter} disabled={!!editor} selected={selectedSensor?.id}
    onGroupFilter={setGroupFilter} onLabelFilter={setLabelFilter} onSelect={(id) => select("sensor", id, true)}
    onRegister={() => startSensor()} onEditGroup={startGroup}
    onImport={canManageSite(siteSession) && !editingSite ? () => {
      setImportRequest((value) => value + 1);
      if (listView) openListSection("posture"); else { setReviewTab("posture"); setReviewOpen(true); requestFocus("review"); }
    } : undefined} />;

  const boundaryList = <div className="gv-sidebar-section">
    {!listView && <div className="gv-section-heading"><h3>Boundaries <span>{boundaries.length}</span></h3>
      <button className="gv-icon-button" type="button" title="Add boundary" aria-label="Add boundary" disabled={!!editor}
        onClick={() => startBoundary()}><Plus aria-hidden="true" size={16} /></button>
    </div>}
    {(floorId ? levelBoundaries : boundaries).map((zone) => <button key={zone.id} type="button" className="gv-inventory-row" data-focus-key={"boundary" + zone.id}
      disabled={!!editor} aria-pressed={selectedBoundary?.id === zone.id} onClick={() => select("boundary", zone.id, true)}>
      <Pentagon aria-hidden="true" size={17} style={{ color: boundaryColor(zone) }} />
      <span><strong>{zone.name}</strong><small>{!zone.enabled ? "Paused" : zone.kind} · {zone.sensorIds.length} {zone.sensorIds.length === 1 ? "device" : "devices"}</small></span>
      <ChevronRight aria-hidden="true" size={14} />
    </button>)}
    {!boundaries.length && <p className="gv-empty">No boundaries configured.</p>}
  </div>;

  function openQueuedIncident(alert: Alert, event?: Alert) {
    select("alert", alert.id, true);
    if (event) {
      const selected = eventFromAlert(event);
      setInitialReplayEvent(selected);
      jumpToReplayEvent(selected);
    }
  }

  function connectOperator(session?: SiteSession) {
    setSiteSession(session); setConnectedSites(session ? [session] : []); setSiteDraft(undefined);
    setDispatchAlert(undefined); setTelemetry(undefined); setSelection(null); setReplayEvent(null); setFloorId("");
  }

  function visitSite(session: SiteSession, incidentId?: string, replay = false) {
    if (incidentId && listView) setListSection("alerts");
    if (siteConnection?.base === session.base && incidentId) {
      const alert = alerts.find((entry) => entry.id === incidentId);
      if (alert) { select("alert", incidentId, true); if (replay) openReplay(alert); }
      else setDispatchAlert({ siteId: session.operator.siteId, incidentId, replay });
      return;
    }
    if (enterprise) { enterprise.visit(session, incidentId, replay); return; }
    setSiteSession(session); setSiteDraft(undefined); setTelemetry(undefined); setSelection(null); setReplayEvent(null); setFloorId("");
    setDispatchAlert(incidentId ? { siteId: session.operator.siteId, incidentId, replay } : undefined);
  }

  useEffect(() => {
    if (!dispatchAlert || !siteOperator.ready || siteSession?.operator.siteId !== dispatchAlert.siteId) return;
    const alert = alerts.find((item) => item.id === dispatchAlert.incidentId);
    if (alert) {
      select("alert", dispatchAlert.incidentId, true);
      if (dispatchAlert.replay) openReplay(alert);
      setDispatchAlert(undefined);
      return;
    }
    const controller = new AbortController();
    void siteOperator.history(dispatchAlert.incidentId, AbortSignal.any([controller.signal, AbortSignal.timeout(8000)])).catch((issue) => {
      if (!controller.signal.aborted) {
        setNotice(issue instanceof Error ? issue.message : "Could not open the recorded incident.");
        setDispatchAlert(undefined);
      }
    });
    return () => controller.abort();
  }, [dispatchAlert, siteOperator.ready, siteSession?.operator.siteId, alerts]);

  const dispatch = siteSession && !editingSite && siteOperator.status !== "revoked" ? {
    active: siteSession, sites: dispatchSites, status: dispatchState.status, onVisit: visitSite, onRefresh: dispatchState.refresh,
    onAdd: (session: SiteSession) => setConnectedSites((current) => [...current.filter((site) => site.operator.siteId !== session.operator.siteId), session]),
  } : undefined;

  const alertList = <IncidentQueue workspace={workspace} filters={incidentFilters} onFilter={setIncidentFilters}
    selected={selectedAlert?.id} onOpen={openQueuedIncident}
    gatewayRows={<>{siteSession && siteOperator.ready && <button type="button" className="gv-button"
      onClick={() => openEvidenceSearch()}>Evidence search and cases</button>}
      {dispatch && <OrganizationDispatch {...dispatch} />}
      {siteSession && siteOperator.ready && !editingSite && workspace.sensors.some(({ passageRuleId }) => passageRuleId) &&
        <DoorPassageReview key={insightsScope} session={siteSession} workspace={workspace} mapView={!listView}
          onJump={jumpToReplayEvent} onClose={() => setReplayEvent(null)} />}
      {(incidentFilters.status === "all" || incidentFilters.status === "new") && <GatewayIncidentRows
      incidents={incidentFilters.status === "new" ? activeGatewayIncidents : gatewayIncidents} onOpen={(id) => select("gateway", id, true)} />}</>}
    onSaveSearch={(name, filters) => setWorkspace((current) => ({ ...current, savedIncidentSearches: [
      ...(current.savedIncidentSearches ?? []).filter((entry) => entry.name !== name), { name, filters },
    ].slice(-20) }))}
    onRemoveSearch={(name) => setWorkspace((current) => ({ ...current, savedIncidentSearches: current.savedIncidentSearches?.filter((entry) => entry.name !== name) }))}
    onRule={!siteSession || editingSite ? (correlationRule) => setConfiguration((current) => ({ ...current, correlationRule })) : undefined} />;

  const postureList = <SitePosture areas={posture} insights={!editingSite && siteOperator.status !== "revoked" &&
    <SiteInsights workspace={workspace} healthById={healthById} videoChecks={videoChecks} version={siteOperator.publishedVersion} now={healthNow}
      view={insights?.scope === insightsScope ? insights.view : DEFAULT_INSIGHTS_VIEW} onView={(view) => setInsights({ scope: insightsScope, view })}
      assets={siteAssets.assets} assetsError={siteAssets.error} onRefresh={siteAssets.refresh}
      scoped={!!siteSession && siteSession.operator.cameras !== "*"} synchronizedAt={siteOperator.synchronizedAt}
      connection={!siteSession ? "local" : siteOperator.status === "offline" ? "offline" : siteOperator.ready ? "ready" : "syncing"}
      onBoundary={(id) => select("boundary", id, true, { ...currentFocus(), returnToPosture: true })}
      onCamera={(id) => select("sensor", id, true, { ...currentFocus(), returnToPosture: true })}
      onIncident={(id) => select("alert", id, true, { ...currentFocus(), returnToPosture: true })} />}
    triage={{
    sensors, healthById, now: healthNow, filter: activeFilter, focus: triageFocus, scope: triageScope,
    onFilter: filterHealth, onFocus: listView ? (target) => {
      select(target.kind, target.id, true);
      setTriageFocus(target);
    } : focusTriage, onSchedule: planMaintenance,
    onStop: async (sensorId, startsAt) => {
      if (siteSession) {
        if (!siteOperator.ready) throw new Error("Wait for the shared site connection before changing maintenance.");
        await siteOperator.maintenance({ sensorId, mode: "stop", startsAt });
        return;
      }
      const now = Date.now();
      setWorkspace((current) => stopMaintenance(current, sensorId, startsAt, now));
    },
  }} video={<>{enterprise && siteSession && <p role="status">{siteSession.operator.name} · {siteSession.operator.organization.name} · {siteSession.operator.site}</p>}
    {!enterprise && <SiteOperatorPanel session={siteSession} status={siteOperator.status} onChange={connectOperator} />}
    {dispatch && <OrganizationDispatch {...dispatch} allowConnect={!enterprise} />}
    {!enterprise && <SiteTelemetryPanel connection={telemetry} status={telemetryStatus} onConnect={setTelemetry} useOperator={siteSession ? () => setTelemetry({
    endpoint: new URL("api/site/telemetry", siteSession.base).href, token: siteSession.token,
  }) : undefined} />}
    {siteSession && siteOperator.status !== "revoked" && <>
      <SiteConfigurationPanel session={siteSession} local={localWorkspace} editing={editingSite} publishedVersion={siteOperator.publishedVersion} importRequest={importRequest}
        onEdit={(draft) => { setSiteDraft(draft); setSelection(null); setReplayEvent(null); }} onExit={() => { setSiteDraft(undefined); setEditor(null); }} />
      <OrganizationAccounts session={siteSession} />
      {!editingSite && <SiteOperationsPanel session={siteSession} workspace={workspace} version={siteOperator.publishedVersion}
        onBoundary={(boundary) => select("boundary", boundary.id, true)} onIncident={(id) => visitSite(siteSession, id)}
        onValidation={siteAssets.refresh} />}
      {!editingSite && <IntelligencePosture session={siteSession} workspace={workspace}
        onCamera={(id) => select("sensor", id, true)} onBoundary={(id) => select("boundary", id, true)} />}
      <CctvAnalyticsPosture workspace={workspace} session={siteSession} version={siteOperator.publishedVersion} onCamera={(id) => select("sensor", id, true)} />
      {!editingSite && <DockOccupancyPanel key={insightsScope}
        workspace={workspace} version={siteOperator.publishedVersion} session={siteSession}
        ready={siteOperator.ready && siteOperator.status !== "offline"} onCamera={(id) => select("sensor", id, true)} />}
      {!editingSite && siteOperator.ready && <InspectionResultsPanel key={insightsScope}
        session={siteSession} cameras={cameras} onCamera={(id) => select("sensor", id, true)} />}
      {editingSite && <XProtectCatalog session={siteSession} onAdd={(camera) => {
        setConfiguration((current) => current.sensors.some(({ id }) => id === camera.id) ? current : { ...current, sensors: [...current.sensors, camera] });
      }} />}
      {editingSite && <XProtectCatalog session={siteSession} provider="genetec" onAdd={(device) => {
        setConfiguration((current) => current.sensors.some(({ id }) => id === device.id) ? current : { ...current, sensors: [...current.sensors, device] });
      }} />}
      <SiteReadiness session={siteSession} workspace={workspace} health={healthById} version={siteOperator.publishedVersion} now={healthNow} editing={!!editingSite}
        assets={siteAssets.assets} refreshAssets={siteAssets.refresh}
        onDependency={(dependency) => setConfiguration((current) => ({ ...current, dependencies: [
          ...(current.dependencies ?? []).filter((entry) => entry.id !== dependency.id), dependency,
        ] }))} onBoundary={(area) => select("boundary", area.id, true)} onIncident={(alert) => openQueuedIncident(alert)} />
    </>}
    <VideoPosture cameras={cameras} boundaries={levelBoundaries} coverage={coverage} checks={videoChecks}
      onCamera={(id) => select("sensor", id, true)} onBoundary={(id) => select("boundary", id, true)} /></>}
    cameraGaps={coverage?.gapFractionByBoundary} selected={selectedBoundary?.id} onSelect={(id) => select("boundary", id, true)} />;

  const statusMessage = storageError ? "Local storage is unavailable. Changes will be lost when you close this session." : notice;
  const toast = statusMessage && <div className="gv-toast"><CircleAlert aria-hidden="true" size={17} /><span aria-hidden="true">{statusMessage}</span>
    {!storageError && <button className="gv-icon-button" type="button" aria-label="Dismiss message" onClick={() => {
      setNotice("");
      requestFocus(listView && !showReview ? "list" : showReview ? "review" : "view");
    }}><X aria-hidden="true" size={15} /></button>}
  </div>;

  return (
    <div className="gv-app">
      <main className="gv-workspace" data-view={surface} data-inventory-open={showInventory}
        data-review-open={showReview} data-replay-open={showReplay}>
        <a href={cameraWorkspace ? "#gv-cameras" : "#gv-site-list"} className="gv-skip-link" onClick={(event) => {
          event.preventDefault();
          if (cameraWorkspace) {
            requestFocus({ element: document.querySelector<HTMLElement>(".gv-camera-workspace h3") ?? undefined });
            return;
          }
          const destination = selection || editor ? "review" : "list";
          if (listView) requestFocus(destination);
          else switchWorkspace("list", destination);
        }}>Skip to site content</a>
        <div className="gv-sr-only" role="status" aria-atomic="true">
          {mapFailed ? "Map unavailable. Site list is open." : ""}
        </div>
        <div className="gv-sr-only" role="status" aria-atomic="true">{statusMessage}</div>
        {!authoritativeSite && !editingSite && sensors.map((sensor) => sensor.healthUrl && <ApplianceHealthMonitor key={sensor.id} deviceId={sensor.id} url={sensor.healthUrl}
          interval={sensor.reportIntervalSeconds ?? REPORT_INTERVAL.default} onReport={receivePolledReport} />)}
        <div className="gv-workspace-header">
        <header className="gv-brandbar" data-tauri-drag-region={hasTauriWindowRuntime() || undefined}>
          <span className="gv-brand-mark"><img src="/proxigo-mark.png" alt="" width={19} height={19} /></span>
          <div className="gv-brand">
            <h1>Proxigo <span>Global View</span></h1>
            <p>{siteSession ? `${siteSession.operator.organization.name} · ${siteSession.operator.site}` : <>Harbor Logistics Campus <span className="gv-brand-city">· Alameda, CA</span></>}</p>
          </div>
          <span className="gv-preview-dot" role="img" aria-label={authoritativeSite ? "Published site" : enterprise ? "Enterprise site" : "Demo workspace"} />
        </header>
        <div className="gv-actions" role="group" aria-label="Site actions">
          <input ref={importInput} type="file" accept=".json,application/json" onChange={importObservation} hidden aria-label="Observation JSON file" />
          {!enterprise && !authoritativeSite && !editingSite && <><button type="button" className="gv-button" aria-label="Import event or health report" title="Import events or a health report" disabled={!!editor} onClick={() => importInput.current?.click()}><Upload size={15} /><span>Import JSON</span></button>
          <button type="button" className="gv-button" aria-label="Test detection" title="Test detection" disabled={!!editor} onClick={() => simulate()}><Activity size={16} /><span>Test detection</span></button></>}
          {editingSite && <button type="button" className="gv-button" disabled={!!editor} onClick={() => {
            setCameraWorkspace(null); setSelection(null); setReplayEvent(null);
            if (listView) openListSection("posture"); else { setReviewTab("posture"); setReviewOpen(true); requestFocus("review"); }
          }}>Draft mode · Review changes</button>}
          {!editingSite && authoritativeSite && <span className="gv-published-version">Version {siteOperator.publishedVersion}</span>}
          {surface === "map" && (!enterprise || editingSite) && <button type="button" className="gv-button gv-primary" aria-label="Draw boundary" title="Draw boundary" disabled={!!editor} onClick={() => startBoundary()}><Pentagon size={17} /><span>Draw boundary</span></button>}
          {enterprise?.controls}
          {hasTauriWindowRuntime() && <div className="gv-window-controls">
            <button type="button" className="gv-icon-button" title="Minimize window" aria-label="Minimize window" onClick={() => void runWindowAction("minimize")}><Minus size={14} /></button>
            <button type="button" className="gv-icon-button" title="Toggle maximized window" aria-label="Toggle maximized window" onClick={() => void runWindowAction("toggleMaximize")}><Square size={13} /></button>
            <button type="button" className="gv-icon-button" title="Close window" aria-label="Close window" onClick={() => void runWindowAction("close")}><X size={16} /></button>
          </div>}
        </div>
        </div>
        <nav className="gv-map-tools" aria-label="Workspace panels">
          <button ref={mapButton} type="button" aria-label="Map" title={mapFailed ? "Retry map" : "Map"} aria-pressed={surface === "map"} onClick={(event) => {
            if (surface === "map" && !editor) { closeReview(false); setInventoryOpen(false); }
            switchWorkspace("map", { element: event.currentTarget });
          }}><MapIcon aria-hidden="true" size={18} /><span>Map</span></button>
          <button type="button" aria-label="Site list" title="Site list" aria-pressed={surface === "list"} onClick={(event) => {
            if (surface === "list" && !editor) closeReview(false);
            switchWorkspace("list", { element: event.currentTarget });
          }}><List aria-hidden="true" size={18} /><span>Site list</span></button>
          <button type="button" aria-label="Cameras" aria-pressed={surface === "cameras"} title="Cameras" disabled={!!editor}
            onClick={(event) => openCameras(event.currentTarget)}><Cctv aria-hidden="true" size={18} /><span>Cameras</span></button>
          {(Object.keys(LIST_SECTIONS) as ListSection[]).map((section) => {
            const Icon = LIST_SECTION_ICONS[section];
            const open = section === "devices" || section === "boundaries"
              ? showInventory && inventoryTab === section
              : showReview && reviewTab === section && !selectedSensor && !editor;
            return <button key={section} type="button" title={LIST_SECTIONS[section]}
              aria-pressed={surface === "list" && listSection === section || surface === "map" && open}
              disabled={!!editor} onClick={() => navigateSection(section)}>
              <Icon aria-hidden="true" size={18} /><span>{LIST_SECTIONS[section]}</span>
              {section === "alerts" && (newAlerts > 0 || activeGatewayIncidents.length > 0) && <i className="gv-unread-dot" aria-hidden="true" />}
            </button>;
          })}
          <button type="button" aria-label="Map layers" title="Map layers" aria-pressed={showInventory && inventoryTab === "site"}
            disabled={!!editor} onClick={() => {
              if (surface !== "map") switchWorkspace("map", "inventory");
              toggleInventory("site");
            }}><Layers3 aria-hidden="true" size={18} /><span>Layers</span></button>
          <button type="button" aria-label="Search workspace" aria-haspopup="dialog" title="Search workspace (Ctrl or Command + K)" disabled={!!editor}
            onClick={(event) => openSearch({ element: event.currentTarget })}><Search aria-hidden="true" size={18} /><span>Search</span></button>
        </nav>
        {listView &&
          <section id="gv-site-list" ref={siteList} className="gv-site-list" aria-labelledby="gv-site-list-heading" hidden={!!cameraWorkspace || compact && showReview}>
            <h2 id="gv-site-list-heading" className="gv-site-list-heading" tabIndex={-1}>{LIST_SECTIONS[listSection]}</h2>
            {mapFailed && <p className="gv-list-fallback">Map unavailable. Choose Map to retry.</p>}
            {(listSection === "devices" || listSection === "boundaries") && floors.length > 0 && <div className="gv-list-floor"><FloorSelect floors={floors} value={floorId} onChange={selectFloor} /></div>}
            {listSection === "devices" ? deviceList : listSection === "boundaries" ? boundaryList : listSection === "posture" ? postureList : alertList}
          </section>}
        {hasInventory && <aside id="gv-inventory" className="gv-sidebar" ref={inventory} aria-label="Map layers and site inventory" hidden={!!cameraWorkspace}>
          <header className="gv-panel-heading">
            <div>{inventoryTab === "devices" ? <Radio size={17} /> : inventoryTab === "boundaries" ? <Pentagon size={17} /> : <Layers3 size={17} />}
              <h2>{inventoryTab === "site" ? "Map layers" : LIST_SECTIONS[inventoryTab]}</h2></div>
            <button type="button" className="gv-icon-button" aria-label="Close inventory" onClick={closeInventory}><X aria-hidden="true" size={17} /></button>
          </header>
          <div className="gv-sidebar-scroll">
            {inventoryTab === "devices" ? deviceList : inventoryTab === "boundaries" ? boundaryList : <>
              <div className="gv-sidebar-section gv-layer-toggles">
                {(Object.keys(LAYER_ICONS) as (keyof typeof LAYER_ICONS)[]).map((layer) => {
                  const Icon = LAYER_ICONS[layer];
                  return <label key={layer}><span><Icon size={15} />{LAYER_TITLES[layer]}</span>
                    <input type="checkbox" checked={layers[layer]} onChange={(event) => setLayers({ ...layers, [layer]: event.target.checked })} /></label>;
                })}
                {layers.deviceHealth && <HealthLegend />}
              </div>
              <SiteFloors floors={floors} active={floorId} onSelect={selectFloor} newId={newId} onSave={(floor) => {
                if (!canEditConfiguration()) return;
                setConfiguration((current) => ({ ...current, floors: [...(current.floors ?? []).filter((entry) => entry.id !== floor.id), floor] }));
                setFloorId(floor.id); setSelection(null); setFocus({ points: floor.corners }); setNotice("Floor plan saved.");
              }} />
              {boundaryList}
            </>}
          </div>
          <div className="gv-sidebar-footer"><span className="gv-preview-dot" /> Local preview</div>
        </aside>}
        {!listView && <section className="gv-map" aria-label="Site map" data-background={!!cameraWorkspace}>
          <MonitoringMap floor={activeFloor} boundaries={layers.boundaries || scopedDevices || showReplay ? levelBoundaries.filter((zone) => zone.id !== editingBoundary?.value.id) : []}
            sensors={scopedDevices ? scopedDevices.filter((device) => device.floorId === activeFloor?.id) : (layers.sensors ? visibleDevices.filter((device) => device.floorId === activeFloor?.id && device.id !== editingSensor?.value.id) : [])} deviceLabels={layers.deviceLabels}
            healthById={layers.deviceHealth || scopedDevices ? healthById : undefined} maintenanceById={maintenanceById}
            posture={showPosture ? posture : undefined}
            cameraCoverage={layers.cameraViews && !scopedDevices && !showReplay ? coverage : undefined} showCameraGaps={showCameraGaps && !editor} cameraDraft={cameraDraft}
            alerts={layers.alerts && !editor && !showReplay ? activeAlerts : []}
            replayEvent={showReplay ? replayEvent ?? undefined : undefined} legend={editor || showReplay ? [] : mapLegend}
            selected={scopedDevices ? triageFocus?.id : selection?.id}
            highlightedBoundaryId={showReplay ? undefined : selectedAlert?.zoneId} scopedBoundaryIds={replayBoundaryIds ?? scopedBoundaryIds}
            recoveredBoundaryIds={recoveredBoundaryIds} focus={focus}
            draft={draft}
            vertexEditing={editingBoundary?.tool === "vertices" ? {
              selected: editingBoundary.selectedVertex,
              onSelect: (index) => editBoundaryDraft((current) => ({ ...current, selectedVertex: index })),
              onInsert: (index) => editBoundaryDraft((current) => insertVertex(current, index)),
              onMove: (index, position) => editBoundaryDraft((current) => moveVertex(current, index, position)),
            } : undefined}
            devicePlacement={editingSensor && devicePosition ? {
              position: devicePosition, modality: editingSensor.value.modality,
              label: editingSensor.value.mapLabel.trim() || editingSensor.value.name.trim() || "Draft " + MODALITY_LABEL[editingSensor.value.modality].toLowerCase() + " device",
              onMove: aimingCamera ? undefined : (position) => setEditor((current) => current?.kind === "sensor"
                ? { ...current, value: { ...current.value, coordinates: toCoordinateDraft(position) } } : current),
            } : undefined}
            onUnavailable={fallBackToList}
            drawing={!!(editingBoundary && editingBoundary.tool !== "vertices" || placingDevice || aimingCamera)} onPoint={addPoint}
            onSelect={(kind, id) => {
              if (editor) return;
              if (scopedDevices && (kind === "sensor" || (kind === "boundary" && scopedBoundaryIds?.has(id)))) focusTriage({ kind, id });
              else select(kind, id, compact);
            }} />
          {editor && (editingBoundary || placingDevice || aimingCamera || devicePosition) && <div className="gv-drawing-hint">
            <MapPin size={15} />{editingBoundary
              ? editingBoundary.tool === "vertices" ? "Drag a corner or click an edge midpoint."
                : editingBoundary.tool === "rectangle" ? (editingBoundary.corner ? "Click the opposite corner." : "Click the first corner.")
                : `Click to add corners · ${draft.length}/${MAX_VERTICES}`
              : aimingCamera ? "Click where the camera should point."
              : placingDevice ? "Click to place or drag to move the device." : "Drag the draft marker to move the device."}
          </div>}
          {surface === "map" && toast}
        </section>}
        {surface !== "map" && toast}
        {hasReview && <aside id="gv-review" className="gv-inspector" ref={inspector} aria-label="Monitor and review" hidden={!!cameraWorkspace}>
          {editor?.kind === "boundary" ? <BoundaryForm editor={editor} sensors={sensors} floors={floors} mapView={!listView} existing={boundaries.some((zone) => zone.id === editor.value.id)}
            onChange={setEditor} onSave={saveBoundary} onCancel={cancelEditor} />
            : editingSensor ? <DeviceForm key={editingSensor.value.id} device={editingSensor.value} groups={groups} boundaries={boundaries} cameras={cameras} floors={floors} dependencies={workspace.dependencies} placement={editingSensor.placement}
              savedDevice={savedDevice} linkedDeviceCount={linkedDeviceCount} mapView={!listView} configurationDraft={!!editingSite} timeZone={siteSession?.operator.timeZone}
              onReport={(report) => attachTestReport(editingSensor.value, report)} onTestRoute={() => testDeviceRoute(editingSensor.value)}
              onChange={(value) => setEditor((current) => current?.kind === "sensor" ? { ...current, value } : current)}
              onPlacement={(placement) => setEditor((current) => current?.kind === "sensor" ? { ...current, placement } : current)}
              onLocate={() => { if (devicePosition) setFocus({ points: [devicePosition] }); }}
              onSave={saveSensor} onCancel={cancelEditor} />
            : editor?.kind === "group" ? <GroupForm draft={editor.value} groups={groups} onChange={(value) => setEditor({ kind: "group", value })} onSave={saveGroup} onCancel={cancelEditor} />
            : selectedSensor ? <DeviceDetails key={selectedSensor.id} device={selectedSensor} groups={groups} boundaries={boundaries}
              health={healthById.get(selectedSensor.id) ?? { status: "unknown" }} maintenance={maintenanceById.get(selectedSensor.id)} listView={listView}
              videoCheck={videoChecks?.get(selectedSensor.id)} onInspect={(inspection) => inspectCamera(selectedSensor.id, inspection)} siteSession={siteSession}
              inspectionsReadOnly={authoritativeSite && (!siteOperator.ready || siteSession?.operator.role === "viewer")}
              linkedCamera={linkedCamera} onViewLinkedCamera={() => { if (linkedCamera) select("sensor", linkedCamera.id, true); }}
              onEdit={() => startSensor(selectedSensor)} onTest={() => simulate(undefined, selectedSensor)} onClose={() => closeReview()}
              onOpenCameras={(button) => openCameras(button, selectedSensor.id)} suspendVideo={!!cameraWorkspace} />
            : selectedAlert && replayAlert ? <IncidentPanel key={selectedAlert.id + (siteSession?.operator.name ?? "")} alert={selectedAlert} camera={evidenceCamera} archive={archive} cameras={incidentCameras(selectedAlert, sensors, coverage)}
              operatorNameOverride={siteSession?.operator.name} readOnly={!!siteSession && (siteSession.operator.role === "viewer" || !siteOperator.ready)}
              siteSession={siteSession}
              onIntelligenceEvidence={({ sensorId, eventId, occurredAt, text, type }) => {
                const match = incidentTimeline(workspace, replayAlert).find(({ observation }) => observation.sensorId === sensorId && observation.id === eventId);
                const device = sensors.find((sensor) => sensor.id === sensorId);
                const recordedEvent: SiteEvent | undefined = occurredAt && text ? {
                  observation: { id: eventId, sensorId, occurredAt, summary: text, eventType: type },
                  sensorName: device?.name ?? sensorId, modality: device?.modality ?? (type === "rf-detection" ? "rf" : "vision"),
                  cameraId: selectedAlert.cameraId ?? null, floorId: selectedAlert.floorId, origin: "Site gateway",
                  boundaries: [{ id: selectedAlert.zoneId, name: selectedAlert.zoneName }],
                } : undefined;
                const entry = match ?? recordedEvent;
                if (!entry) { openReplay(selectedAlert); return; }
                setInitialReplayEvent(entry);
                jumpToReplayEvent(entry);
                requestFocus("review");
              }}
              onCase={siteSession && siteOperator.ready ? () => openEvidenceSearch(selectedAlert) : undefined}
              recordingCoverage={workspace.recordingRecoveryEnabled ? workspace.recordingCoverage?.find((row) => row.incidentId === selectedAlert.id) : undefined}
              timeline={incidentTimeline(workspace, replayAlert)}
              companion={correlatedAlert} onReplayEvent={(entry) => openQueuedIncident(selectedAlert, entry)}
              replay={showReplay ? <IncidentReplay alert={replayAlert} workspace={workspace} siteSession={siteSession}
                videoChecks={videoChecks} initialEvent={initialReplayEvent}
                onJump={jumpToReplayEvent} mapView={!listView} onBack={() => leaveReplay(selectedAlert)} onClose={() => closeReview()} /> : undefined}
              onReplay={() => openReplay(selectedAlert)}
              related={activeAlerts.filter((alert) => alert.id !== selectedAlert.id)} onSelectRelated={(id) => select("alert", id, true)}
              backLabel={reviewOrigin.current.returnToPosture ? "Back to Posture" : undefined}
              onBack={() => reviewOrigin.current.returnToPosture ? closeReview() : backToAlerts(selectedAlert.id, "alert")}
              onClose={() => closeReview()} onAction={(action) => updateIncident(selectedAlert.id, action)} />
            : selectedGateway ? <GatewayIncidentPanel key={selectedGateway.id} incident={selectedGateway} healthById={healthById} focus={triageFocus}
              onBack={() => backToAlerts(selectedGateway.id, "gateway")} onClose={() => closeReview()}
              onFocus={listView ? (target) => select(target.kind, target.id, true) : focusTriage}
              onFit={listView ? undefined : () => fitGateway(selectedGateway)} />
            : <>
              <header className="gv-panel-heading">
                <div>{selectedBoundary ? <Pentagon size={17} /> : reviewTab === "posture" ? <ShieldCheck size={17} /> : <Bell size={17} />}
                  <h2>{selectedBoundary?.name ?? (reviewTab === "posture" ? "Posture" : "Alerts")}</h2>
                  {reviewTab === "alerts" && !selectedBoundary && <span className="gv-alert-count">{newAlerts} new</span>}
                </div>
                {listView ? <button type="button" className="gv-incident-back" onClick={() => closeReview()}><ChevronLeft size={16} /> Back to list</button>
                  : <button type="button" className="gv-icon-button" aria-label="Close review" onClick={() => closeReview()}><X size={17} /></button>}
              </header>
              {selectedBoundary && <div className="gv-details">
                <dl>
                  <div><dt>Type</dt><dd>{selectedBoundary.kind}</dd></div>
                  <div><dt>Visibility</dt><dd>{selectedVisibility && BOUNDARY_VISIBILITY[selectedVisibility].label}</dd></div>
                  {selectedBoundary.mapLabel && <div><dt>Map label</dt><dd>{selectedBoundary.mapLabel}</dd></div>}
                  {!!selectedBoundary.labels?.length && <div><dt>Labels</dt><dd className="gv-tags">{selectedBoundary.labels.map((label) => <span key={labelKey(label)}>{label}</span>)}</dd></div>}
                  {selectedBoundary.notes && <div><dt>Notes</dt><dd>{selectedBoundary.notes}</dd></div>}
                  <div><dt>Devices</dt><dd>{sensors.filter((sensor) => selectedBoundary.sensorIds.includes(sensor.id)).map((sensor) => sensor.name).join(", ") || "None"}</dd></div>
                </dl>
                {authoritativeSite && !editingSite && <DockOccupancyPanel workspace={workspace} version={siteOperator.publishedVersion}
                  ready={siteOperator.ready && siteOperator.status !== "offline"} boundaryId={selectedBoundary.id} onCamera={(id) => select("sensor", id, true)} />}
                <div className="gv-detail-actions">
                  <button type="button" className="gv-button gv-primary" data-focus-key="edit-boundary"
                    onClick={() => startBoundary(selectedBoundary)}>Edit boundary</button>
                  <button type="button" className="gv-button" data-focus-key="add-boundary-device"
                    onClick={() => startSensor(undefined, selectedBoundary)}>Add device to boundary</button>
                  <button type="button" className="gv-button" disabled={!selectedBoundary.enabled || !selectedBoundary.sensorIds.length} onClick={() => simulate(selectedBoundary)}>Test</button>
                </div>
              </div>}
              {!listView && (reviewTab === "posture" ? postureList : !selectedBoundary && alertList)}

            </>}
        </aside>}
        {searchOpen && <WorkspaceSearch targets={searchTargets} onClose={cancelSearch} onSelect={jumpTo}
          onEvidence={siteSession && siteOperator.ready ? () => openEvidenceSearch() : undefined} />}
        {evidenceSearch && siteSession && siteOperator.status !== "revoked" && <EvidenceSearch key={insightsScope}
          active={siteSession} sites={dispatchSites} incident={evidenceSearch.incident ? {
            id: evidenceSearch.incident.id, title: evidenceSearch.incident.observation.summary,
          } : undefined}
          onClose={() => { setEvidenceSearch(undefined); requestFocus(searchOrigin.current); }}
          onIncident={(site, id) => { setEvidenceSearch(undefined); visitSite(site, id, true); }} />}
        {cameraWorkspace && <CameraWorkspace key={cameraWorkspace.revision} workspace={workspace} healthById={healthById} videoChecks={videoChecks} now={healthNow} coverage={coverage} compact={compact} onInspect={inspectCamera} siteSession={siteSession}
          enterpriseWall={enterprise ? enterpriseWall.current : undefined}
          inspectionsReadOnly={authoritativeSite && (!siteOperator.ready || siteSession?.operator.role === "viewer")}
          onVerify={(id) => {
            if (authoritativeSite && siteSession) { updateSiteCameraCheck(id, "verify"); return; }
            const cameraVerifiedAt = new Date().toISOString();
            setWorkspace((current) => ({ ...current, sensors: current.sensors.map((sensor) => sensor.id === id && sensor.cameraView
              ? { ...sensor, cameraVerifiedAt } : sensor) }));
          }}
          initialCamera={cameraWorkspace.cameraId} onEdit={editFromCameras} onMapCamera={showCameraOnMap} onMapCoverage={showCoverageOnMap}
          onIncident={(alert) => {
            setCameraWorkspace(null);
            if (listView) setListSection("alerts");
            select("alert", alert.id, true, cameraOrigin.current);
          }} />}
      </main>
    </div>
  );
}

function DeviceDetails({ device, groups, boundaries, health, maintenance, listView, linkedCamera, onViewLinkedCamera, onEdit, onTest, onClose, onOpenCameras, suspendVideo, videoCheck, onInspect, siteSession, inspectionsReadOnly }: {
  device: Sensor; groups: DeviceGroup[]; boundaries: Boundary[]; health: ApplianceHealth; linkedCamera?: Sensor;
  listView: boolean; maintenance?: MaintenanceWindow;
  onViewLinkedCamera: () => void; onEdit: () => void; onTest: () => void; onClose: () => void;
  onOpenCameras: (button: HTMLButtonElement) => void; suspendVideo: boolean;
  videoCheck?: VideoCheck; onInspect: (inspection?: Sensor["cameraInspection"]) => void;
  siteSession?: SiteSession;
  inspectionsReadOnly?: boolean;
}) {
  const isCamera = device.modality === "vision";
  const actions = <div className="gv-detail-actions">
    <button type="button" className="gv-button gv-primary" data-focus-key="edit-device" onClick={onEdit}>Edit device</button>
    <button type="button" className="gv-button" title="Simulate a detection" onClick={onTest}>Test device</button>
    {isCamera && <button type="button" className="gv-button" onClick={(event) => onOpenCameras(event.currentTarget)}>Open in Cameras</button>}
    {linkedCamera && <button type="button" className="gv-button" onClick={onViewLinkedCamera}>View linked camera</button>}
  </div>;
  return <section className={isCamera ? "gv-details gv-camera-inspector" : "gv-details"}>
    {listView && <button type="button" className="gv-incident-back" onClick={onClose}><ChevronLeft size={16} /> Back to list</button>}
    {isCamera ? <header className="gv-camera-heading">
      <Cctv size={18} /><h3>{device.name}</h3>
      {!listView && <button type="button" className="gv-icon-button" aria-label="Close camera feed" onClick={onClose}><X size={16} /></button>}
    </header> : <><div className="gv-detail-label">Device
      {!listView && <button type="button" className="gv-icon-button" aria-label="Close device details" onClick={onClose}><X size={16} /></button>}
    </div><h3>{device.name}</h3></>}
    {isCamera && <>{!suspendVideo && <CameraFeed camera={device} />}{actions}<SiteCameraViewer session={siteSession} camera={device} /><CameraInspection camera={device} check={videoCheck} onChange={onInspect} readOnly={inspectionsReadOnly} /></>}
    <dl>
      {!isCamera && <div><dt>Type</dt><dd>{MODALITY_LABEL[device.modality]}</dd></div>}
      <div><dt>Health</dt><dd><HealthIndicator health={health} /></dd></div>
      {maintenance && <div><dt>Maintenance</dt><dd>Active · {maintenance.reason}</dd></div>}
      <div><dt>Last report</dt><dd>{health.lastReport ? <time dateTime={health.lastReport}>{new Date(health.lastReport).toLocaleString()}</time> : "—"}</dd></div>
      {device.cameraGatewayKind && <div><dt>Video source</dt><dd>{device.cameraGatewayKind.toUpperCase()} via camera gateway</dd></div>}
      {device.gatewayId && <div><dt>Gateway</dt><dd>{device.gatewayId}</dd></div>}
      <div><dt>Group</dt><dd>{groups.find((group) => group.id === device.groupId)?.name ?? "Ungrouped"}</dd></div>
      <div><dt>Boundaries</dt><dd>{boundaries.filter((boundary) => boundary.sensorIds.includes(device.id)).map((boundary) => boundary.name).join(", ") || "None"}</dd></div>
      {linkedCamera && <div><dt>Linked camera</dt><dd>{linkedCamera.name}</dd></div>}
      <div><dt>Map label</dt><dd>{device.mapLabel || device.name}</dd></div>
      {isCamera && <div><dt>Field of view</dt><dd>{device.cameraView
        ? `${device.cameraView.bearingDeg}° true · ${device.cameraView.fovDeg}° wide · ${device.cameraView.rangeM} m`
        : "Unconfigured"}</dd></div>}
      <div><dt>Labels</dt><dd className="gv-tags">{device.labels.length ? device.labels.map((label) => <span key={labelKey(label)}>{label}</span>) : "None"}</dd></div>
      <div><dt>Device ID</dt><dd className="gv-mono">{device.id}</dd></div>
      <div><dt>Location</dt><dd className="gv-mono">{locationText(device.position)}</dd></div>
    </dl>
    {!isCamera && actions}
  </section>;
}
