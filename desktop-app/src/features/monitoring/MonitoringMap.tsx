import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { GeoJSONSource, Map as GlobeMap, Marker as GlobeMarker } from "maplibre-gl";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { Bell, BellRing, Clock3, Crosshair, Globe2, History, Layers3, Minus, Plus, Satellite } from "lucide-react";
import { HEALTH_LABELS, type ApplianceHealth, type MaintenanceWindow } from "./applianceHealth";
import { RECOVERED_BOUNDARY_COLOR } from "./gatewayIncidents";
import { healthIcons, healthText } from "./DeviceHealth";
import { alertLabelPosition, groupAlertPins, sameMapPoint, type AlertPin } from "./mapAlerts";
import "maplibre-gl/dist/maplibre-gl.css";
import { MAP_MAX_ZOOM, SATELLITE_STYLE } from "../../lib/satelliteMapStyle";
import { CAMERA_COVERAGE_COLORS, cameraSector, type CameraCoverage } from "./cameraCoverage";
import { MAX_VERTICES } from "./configurationModel";
import { deviceIcon } from "./DeviceInventory";
import { boundaryColor, isPosition, MODALITY_LABEL, SITE_BOUNDS, SITE_CENTER, SITE_ZOOM, samplePosition, type Alert, type Boundary, type BoundaryPosture, type CameraView, type Modality, type Position, type Sensor, type SiteEvent, type SiteFloor } from "./monitoringModel";

interface Props {
  boundaries: Boundary[];
  floor?: SiteFloor;
  sensors: Sensor[];
  healthById?: ReadonlyMap<string, ApplianceHealth>;
  maintenanceById: ReadonlyMap<string, MaintenanceWindow>;
  deviceLabels: boolean;
  posture?: BoundaryPosture[];
  cameraCoverage?: CameraCoverage | null;
  showCameraGaps: boolean;
  cameraDraft?: { id: string; position: Position | null; view: CameraView | null; blockers: Position[][] };
  alerts: Alert[];
  replayEvent?: SiteEvent;
  legend: { color: string; label: string }[];
  selected?: string;
  highlightedBoundaryId?: string;
  scopedBoundaryIds?: ReadonlySet<string>;
  recoveredBoundaryIds?: ReadonlySet<string>;
  focus: { points: Position[] } | null;
  draft: Position[];
  vertexEditing?: { selected: number | null; onSelect: (index: number) => void; onInsert: (index: number) => void; onMove: (index: number, position: Position) => void };
  devicePlacement?: { position: Position; modality: Modality; label: string; onMove?: (position: Position) => void };
  drawing: boolean;
  onUnavailable: () => void;
  onPoint: (position: Position) => void;
  onSelect: (kind: "boundary" | "sensor" | "alert", id: string) => void;
}

const SOURCE = "monitoring-overlays";
const SITE_DETAIL_ZOOM = 13;
const LAYERS = ["monitor-device-labels", "monitor-zone-names", "monitor-zones"];
const PLAN_LAYERS = ["site-plan-water", "site-plan-roads", "site-plan-buildings"];

function overlay(geometry: Geometry, properties: Feature["properties"]): Feature {
  return { type: "Feature", geometry, properties };
}

function addLayers(map: GlobeMap) {
  const plan = { source: "openmaptiles", layout: { visibility: "none" as const } };
  const before = "operator-country-boundary";
  map.addLayer({ ...plan, id: PLAN_LAYERS[0], type: "fill", "source-layer": "water", paint: { "fill-color": "#242426" } }, before);
  map.addLayer({ ...plan, id: PLAN_LAYERS[1], type: "line", "source-layer": "transportation", minzoom: 10,
    filter: ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary", "secondary", "tertiary", "minor", "service"]]],
    paint: { "line-color": "#48474b", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.6, 15, 1.4, 19, 5] } }, before);
  map.addLayer({ ...plan, id: PLAN_LAYERS[2], type: "fill", "source-layer": "building", minzoom: 13.5,
    paint: { "fill-color": "#333235", "fill-outline-color": "#4f4d50" } }, before);
  map.setPaintProperty("imagery", "raster-saturation", 0);

  map.addSource(SOURCE, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  const style = { source: SOURCE };
  map.addLayer({ ...style, id: "monitor-zones", minzoom: 12, type: "fill", filter: ["==", ["get", "kind"], "boundary"], paint: {
    "fill-color": ["get", "color"], "fill-opacity": ["case", ["get", "selected"], 0.19, ["get", "enabled"], 0.10, 0.04],
  } });
  map.addLayer({ ...style, id: "monitor-camera-gaps", minzoom: SITE_DETAIL_ZOOM, type: "fill", filter: ["==", ["get", "kind"], "camera-gap"], paint: {
    "fill-color": CAMERA_COVERAGE_COLORS.gap.color, "fill-opacity": 0.25,
  } });
  map.addLayer({ ...style, id: "monitor-camera-views", minzoom: SITE_DETAIL_ZOOM, type: "fill", filter: ["==", ["get", "kind"], "camera-view"], paint: {
    "fill-color": CAMERA_COVERAGE_COLORS.view.color, "fill-opacity": ["case", ["get", "selected"], 0.19, 0.065],
  } });
  map.addLayer({ ...style, id: "monitor-camera-overlap", minzoom: SITE_DETAIL_ZOOM, type: "fill", filter: ["==", ["get", "kind"], "camera-overlap"], paint: {
    "fill-color": CAMERA_COVERAGE_COLORS.overlap.color, "fill-opacity": 0.29,
  } });
  map.addLayer({ ...style, id: "monitor-camera-outlines", minzoom: SITE_DETAIL_ZOOM, type: "line", filter: ["==", ["get", "kind"], "camera-view"], paint: {
    "line-color": CAMERA_COVERAGE_COLORS.view.color, "line-opacity": ["case", ["get", "selected"], 1, 0.53],
    "line-width": ["case", ["get", "selected"], 2, 1],
  } });
  map.addLayer({ ...style, id: "monitor-zone-edges", minzoom: 12, type: "line", filter: ["all", ["==", ["get", "kind"], "boundary"], ["get", "enabled"]], paint: {
    "line-color": ["get", "color"], "line-width": ["case", ["get", "selected"], 2.6, 1.5],
    "line-opacity": ["case", ["get", "selected"], 1, 0.80],
  } });
  map.addLayer({ ...style, id: "monitor-zone-paused", minzoom: 12, type: "line", filter: ["all", ["==", ["get", "kind"], "boundary"], ["!", ["get", "enabled"]]], paint: {
    "line-color": ["get", "color"], "line-width": ["case", ["get", "selected"], 2.6, 1.5],
    "line-opacity": ["case", ["get", "selected"], 0.9, 0.5], "line-dasharray": [3, 2],
  } });
  map.addLayer({ ...style, id: "monitor-zone-names", type: "symbol", minzoom: 12, filter: ["==", ["get", "kind"], "zone-label"], layout: {
    "text-field": ["get", "name"], "text-font": ["Noto Sans Bold"],
    "text-size": 12, "text-offset": [0, -0.4], "text-max-width": 18,
    "symbol-sort-key": ["case", ["get", "selected"], 0, 1],
  }, paint: { "text-color": ["get", "color"], "text-halo-color": "#09090b", "text-halo-width": 1.7 } });
  map.addLayer({ ...style, id: "monitor-draft", type: "line", filter: ["==", ["get", "kind"], "draft-line"], paint: {
    "line-color": "#ff6600", "line-width": 2.5,
  } });
  map.addLayer({ ...style, id: "monitor-device-labels", type: "symbol", minzoom: SITE_DETAIL_ZOOM, filter: ["==", ["get", "kind"], "sensor"], layout: {
    "text-field": ["format", ["get", "name"], {}, ["get", "status"], { "font-scale": 0.91, "text-color": "#d4d4d8" }],
    "text-font": ["Noto Sans Bold"], "text-size": 11, "text-offset": [0, 1.65],
    "text-anchor": "top", "text-max-width": 12, "text-padding": 4, "symbol-sort-key": ["case", ["get", "selected"], 0, 1],
  }, paint: { "text-color": "#e4e4e7", "text-halo-color": "#09090b", "text-halo-width": 1.8 } });
}

function mapDuration(duration: number) {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : duration;
}

function focusPoints(map: GlobeMap, points: Position[], duration = mapDuration(420)) {
  const container = map.getContainer();
  const bounds = container.getBoundingClientRect();
  const workspace = container.closest(".gv-workspace");
  const rail = workspace?.querySelector(".gv-map-tools")?.getBoundingClientRect();
  const inventory = workspace?.querySelector(".gv-sidebar")?.getBoundingClientRect();
  const review = workspace?.querySelector(".gv-inspector")?.getBoundingClientRect();
  const compact = getComputedStyle(container).getPropertyValue("--gv-dock-at-bottom").trim() === "1";
  const leftEdge = (compact ? rail?.right : inventory?.right ?? rail?.right) ?? bounds.left;
  const left = Math.max(0, leftEdge - bounds.left);
  const right = !compact && review ? bounds.right - review.left : 0;
  const top = Math.min(80, bounds.height / 5);
  const footer = Math.min(85, bounds.height / 5);
  const sheet = compact ? review ?? inventory : undefined;
  const bottom = sheet ? bounds.bottom - sheet.top + footer : footer;
  if (points.length === 1) {
    map.easeTo({ center: points[0], offset: [(left - right) / 2, (top - bottom) / 2], zoom: Math.max(map.getZoom(), 16.2), duration });
    return;
  }
  const lngs = points.map(([lng]) => lng);
  const lats = points.map(([, lat]) => lat);
  const margin = Math.max(0, Math.min(85, (bounds.width - left - right) / 6, (bounds.height - top - bottom) / 6));
  const padding = { left: left + margin, right: right + margin, top: top + margin, bottom: bottom + margin };
  map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding, duration, maxZoom: 17 });
}

function layoutAlertLabels(container: HTMLElement) {
  const buttons = [...container.querySelectorAll<HTMLElement>(".gv-map-alert, .gv-map-replay")];
  if (!buttons.length) return;
  const viewport = container.getBoundingClientRect();
  const controls = container.closest(".gv-workspace")?.querySelectorAll(
    ".gv-brandbar, .gv-actions, .gv-map-tools, .gv-map-controls, .gv-sidebar, .gv-inspector, .gv-map-meta, .gv-map-legend, .gv-toast",
  ) ?? [];
  const anchors = buttons.map((button) => ({
    button, box: button.getBoundingClientRect(), label: button.querySelector<HTMLElement>(".gv-map-alert-label"),
    priority: button.getAttribute("aria-pressed") === "true" ? 0 : button.dataset.severity === "high" ? 1 : 2,
  })).sort((a, b) => a.priority - b.priority);
  const obstacles = [
    ...controls, ...container.querySelectorAll(".gv-device-marker-button"),
  ].map((element) => element.getBoundingClientRect()).concat(anchors.map((anchor) => anchor.box));
  const placed: ReturnType<typeof alertLabelPosition>[] = [];
  for (const { button, box, label } of anchors) {
    if (!label) continue;
    const visible = box.right > viewport.left && box.left < viewport.right && box.bottom > viewport.top && box.top < viewport.bottom;
    label.style.visibility = visible ? "" : "hidden";
    if (!visible) continue;
    const position = alertLabelPosition(box, { width: label.offsetWidth, height: label.offsetHeight },
      { left: viewport.left + 6, top: viewport.top + 6, width: viewport.width - 12, height: viewport.height - 12 },
      [...obstacles, ...placed]);
    label.style.left = `${position.left - box.left - button.clientLeft}px`;
    label.style.top = `${position.top - box.top - button.clientTop}px`;
    label.style.transform = "none";
    placed.push(position);
  }
}

type MarkerRuntime = { map: GlobeMap; Marker: typeof GlobeMarker };

function MapMarker({ runtime, position, offsetY = 0, onMove, children }: {
  runtime: MarkerRuntime; position: Position; offsetY?: number; onMove?: (position: Position) => void; children: ReactNode;
}) {
  const [element] = useState(() => document.createElement("div"));
  const markerRef = useRef<GlobeMarker | null>(null);
  const latest = useRef({ position, onMove });
  latest.current = { position, onMove };
  const [lng, lat] = position;
  const draggable = !!onMove;

  useEffect(() => {
    element.setAttribute("role", "presentation");
    const marker = new runtime.Marker({ element, opacityWhenCovered: 0, className: "gv-device-marker", draggable, clickTolerance: 4, offset: [0, offsetY] })
      .setLngLat(latest.current.position).addTo(runtime.map);
    // The portal renders and labels the interactive controls inside this wrapper.
    element.removeAttribute("aria-label");
    markerRef.current = marker;
    const onDragEnd = () => {
      const { lng, lat } = marker.getLngLat().wrap();
      const next: Position = [lng, lat];
      if (isPosition(next)) {
        marker.setLngLat(next);
        latest.current.onMove?.(next);
      } else marker.setLngLat(latest.current.position);
    };
    marker.on("dragend", onDragEnd);
    return () => { marker.off("dragend", onDragEnd); marker.remove(); markerRef.current = null; };
  }, [draggable, element, offsetY, runtime]);

  useEffect(() => { markerRef.current?.setLngLat([lng, lat]); }, [lng, lat]);
  return createPortal(children, element);
}

function AlertMarkerButton({ pin, selected, deviceName, onSelect }: {
  pin: AlertPin; selected: boolean; deviceName?: string; onSelect: () => void;
}) {
  const { alert, count } = pin;
  const summary = alert.observation.summary;
  const Icon = alert.severity === "High" ? BellRing : Bell;
  const context = [alert.severity + " priority", alert.zoneName, alert.sensorName, count > 1 ? `${count} alerts here` : ""].filter(Boolean).join(" · ");
  return <button type="button" className="gv-map-alert" data-attached={!!deviceName} data-severity={alert.severity.toLowerCase()}
    aria-label={deviceName ? "Open alert at " + deviceName : "Open alert " + summary}
    aria-description={deviceName ? summary + " · " + context : context} aria-pressed={selected}
    title={summary + " · " + context} onClick={(event) => { event.stopPropagation(); onSelect(); }}>
    <Icon className="gv-map-alert-icon" size={15} aria-hidden="true" />
    <span className="gv-map-alert-label"><strong>{summary}</strong>{count > 1 && <small>{count} alerts</small>}</span>
  </button>;
}

function DeviceMarker({ runtime, sensor, health, maintenance, selected, drawing, onSelect, pin, alertSelected, onSelectAlert }: {
  runtime: MarkerRuntime; sensor: Sensor; selected: boolean; drawing: boolean; onSelect: () => void;
  health?: ApplianceHealth;
  maintenance?: MaintenanceWindow;
  pin?: AlertPin; alertSelected: boolean; onSelectAlert: (id: string) => void;
}) {
  const Icon = deviceIcon[sensor.modality];
  const HealthIcon = health && healthIcons[health.status];
  const status = [health && healthText(health), maintenance && "Maintenance active"].filter(Boolean).join(" · ");
  return <MapMarker runtime={runtime} position={sensor.position}>
    <button type="button" className="gv-device-marker-button" data-modality={sensor.modality}
      aria-label={sensor.name} aria-description={[MODALITY_LABEL[sensor.modality], status].filter(Boolean).join(" · ")} aria-pressed={selected} disabled={drawing}
      title={status ? sensor.name + " · " + status : sensor.name} onClick={(event) => { event.stopPropagation(); onSelect(); }}>
      <Icon aria-hidden="true" size={16} />
      {health && HealthIcon && <span className="gv-marker-health" data-health={health.status} aria-hidden="true"><HealthIcon size={10} /></span>}
      {maintenance && <span className="gv-marker-maintenance" aria-hidden="true"><Clock3 size={11} /></span>}
    </button>
    {pin && !drawing && <AlertMarkerButton pin={pin} selected={alertSelected} deviceName={sensor.name} onSelect={() => onSelectAlert(pin.alert.id)} />}
  </MapMarker>;
}

function BoundaryHandles({ runtime, vertices, editing }: { runtime: MarkerRuntime; vertices: Position[]; editing: NonNullable<Props["vertexEditing"]> }) {
  return <>{vertices.map((vertex, index) => <MapMarker key={"vertex-" + index} runtime={runtime} position={vertex} onMove={(position) => editing.onMove(index, position)}>
    <button type="button" className="gv-vertex-handle" aria-label={"Select vertex " + (index + 1)}
      title={"Drag vertex " + (index + 1)} aria-pressed={editing.selected === index}
      onClick={(event) => { event.stopPropagation(); editing.onSelect(index); }}>{index + 1}</button>
  </MapMarker>)}
    {vertices.length < MAX_VERTICES && vertices.map((vertex, index) => {
      const next = vertices[(index + 1) % vertices.length];
      const midpoint: Position = [(vertex[0] + next[0]) / 2, (vertex[1] + next[1]) / 2];
      return <MapMarker key={"midpoint-" + index} runtime={runtime} position={midpoint}>
        <button type="button" className="gv-midpoint-handle" aria-label={"Insert vertex on edge " + (index + 1)}
          title={"Insert vertex on edge " + (index + 1)} onClick={(event) => { event.stopPropagation(); editing.onInsert(index); }}><Plus size={10} /></button>
      </MapMarker>;
    })}
  </>;
}

function PlacementMarker({ runtime, draft }: { runtime: MarkerRuntime; draft: NonNullable<Props["devicePlacement"]> }) {
  const Icon = deviceIcon[draft.modality];
  return <MapMarker runtime={runtime} position={draft.position} onMove={draft.onMove}>
    <button type="button" className="gv-device-marker-button gv-placement-marker" data-draggable={!!draft.onMove} data-modality={draft.modality}
      aria-label="Draft device location" aria-description={draft.label} title={draft.onMove ? "Drag to move " + draft.label : draft.label}
      onClick={(event) => event.stopPropagation()}><Icon aria-hidden="true" size={17} /></button>
    <span className="gv-draft-label" aria-hidden="true">{draft.label}</span>
  </MapMarker>;
}

export function MonitoringMap(props: Props) {
  const { boundaries, sensors, alerts, replayEvent, deviceLabels, healthById, maintenanceById, posture, selected, highlightedBoundaryId,
    scopedBoundaryIds, recoveredBoundaryIds, focus, draft, drawing, cameraCoverage, showCameraGaps, cameraDraft } = props;
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<GlobeMap | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const [markerRuntime, setMarkerRuntime] = useState<MarkerRuntime | null>(null);
  const ready = !!markerRuntime;
  const [satellite, setSatellite] = useState(true);
  const [siteDetail, setSiteDetail] = useState(false);
  const pins = useMemo(() => groupAlertPins(alerts, sensors, selected), [alerts, sensors, selected]);
  const replayPosition = replayEvent?.observation.position;

  const data = useMemo<FeatureCollection>(() => {
    const visibilityById = new Map(posture?.map(({ boundary, visibility }) => [boundary.id, visibility]));
    const features = boundaries.flatMap((zone) => {
      const visibility = visibilityById.get(zone.id);
      const incidentBoundary = highlightedBoundaryId === zone.id;
      const highlighted = incidentBoundary || scopedBoundaryIds?.has(zone.id);
      const recovered = !incidentBoundary && recoveredBoundaryIds?.has(zone.id);
      const color = recovered ? RECOVERED_BOUNDARY_COLOR : highlighted ? "#ff6600" : boundaryColor(zone, visibility);
      const name = (zone.mapLabel || zone.name) + (zone.enabled ? "" : " · Paused");
      const props = { id: zone.id, name, color, selected: highlighted || selected === zone.id, enabled: zone.enabled };
      return [
        overlay({ type: "Polygon", coordinates: [[...zone.vertices, zone.vertices[0]]] }, { ...props, kind: "boundary" }),
        overlay({ type: "Point", coordinates: samplePosition(zone.vertices) }, { ...props, kind: "zone-label" }),
      ];
    });
    for (const sensor of sensors) {
      const health = healthById?.get(sensor.id);
      const status = [health && (health.status !== "online" || selected === sensor.id || scopedBoundaryIds) && HEALTH_LABELS[health.status],
        maintenanceById.has(sensor.id) && "Maintenance"].filter(Boolean).join(" · ");
      features.push(overlay({ type: "Point", coordinates: sensor.position }, {
        kind: "sensor", id: sensor.id, name: sensor.mapLabel || sensor.name, status: status ? "\n" + status : "", selected: selected === sensor.id,
      }));
    }
    for (const sector of cameraCoverage?.sectors ?? []) {
      if (sector.id !== cameraDraft?.id) features.push(overlay({ type: "MultiPolygon", coordinates: sector.geometry }, {
        kind: "camera-view", selected: selected === sector.id,
      }));
    }
    if (cameraDraft?.position && cameraDraft.view) features.push(overlay({
      type: "MultiPolygon", coordinates: cameraSector(cameraDraft.position, cameraDraft.view, cameraDraft.blockers),
    }, { kind: "camera-view", selected: true }));
    if (!cameraDraft && cameraCoverage?.overlaps.length) features.push(overlay({
      type: "MultiPolygon", coordinates: cameraCoverage.overlaps,
    }, { kind: "camera-overlap" }));
    if (!cameraDraft && showCameraGaps && cameraCoverage?.gaps.length) features.push(overlay({
      type: "MultiPolygon", coordinates: cameraCoverage.gaps,
    }, { kind: "camera-gap" }));
    if (draft.length >= 2) {
      const closed = draft.length >= 3;
      features.push(overlay({ type: "LineString", coordinates: closed ? [...draft, draft[0]] : draft }, { kind: "draft-line" }));
    }
    return { type: "FeatureCollection", features };
  }, [boundaries, sensors, healthById, maintenanceById, selected, highlightedBoundaryId, scopedBoundaryIds, recoveredBoundaryIds, draft, posture, cameraCoverage, showCameraGaps, cameraDraft]);

  useEffect(() => {
    let disposed = false;
    let map: GlobeMap | null = null;
    let observer: ResizeObserver | undefined;
    void (async () => {
      try {
        const { Map, Marker, ScaleControl } = await import("maplibre-gl");
        if (disposed || !container.current) return;
        const globe = new Map({
          container: container.current, style: SATELLITE_STYLE, center: SITE_CENTER, zoom: SITE_ZOOM,
          maxZoom: MAP_MAX_ZOOM, minZoom: 1, maxPitch: 0, renderWorldCopies: false,
          attributionControl: false, validateStyle: false, pixelRatio: Math.min(window.devicePixelRatio || 1, 1.5),
        });
        map = globe;
        mapRef.current = globe;
        globe.dragRotate.disable();
        globe.touchZoomRotate.disableRotation();
        globe.addControl(new ScaleControl(), "bottom-left");
        globe.on("load", () => {
          addLayers(globe);
          focusPoints(globe, SITE_BOUNDS, 0);
          setSiteDetail(globe.getZoom() >= SITE_DETAIL_ZOOM);
          setMarkerRuntime({ map: globe, Marker });
        });
        globe.on("webglcontextlost", () => { if (!disposed) latest.current.onUnavailable(); });
        globe.on("zoom", () => setSiteDetail(globe.getZoom() >= SITE_DETAIL_ZOOM));
        globe.on("click", (event) => {
          if (latest.current.drawing) {
            const { lng, lat } = event.lngLat.wrap();
            if (Math.abs(lat) <= 85) latest.current.onPoint([lng, lat]);
            return;
          }
          const available = LAYERS.filter((layer) => globe.getLayer(layer));
          if (!available.length) return;
          const hits = globe.queryRenderedFeatures(event.point, { layers: available });
          const hit = available.map((layer) => hits.find((feature) => feature.layer.id === layer)).find(Boolean);
          if (hit) {
            const kind = hit.properties.kind === "zone-label" ? "boundary" : hit.properties.kind;
            latest.current.onSelect(kind, hit.properties.id);
          }
        });
        observer = new ResizeObserver(() => globe.resize());
        observer.observe(container.current);
      } catch (error) {
        console.warn("Global View map initialization failed", error);
        if (!disposed) latest.current.onUnavailable();
      }
    })();
    return () => { disposed = true; observer?.disconnect(); map?.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => {
    if (!ready) return;
    (mapRef.current?.getSource(SOURCE) as GeoJSONSource | undefined)?.setData(data);
  }, [data, ready]);

  useEffect(() => {
    const map = markerRuntime?.map;
    const floor = props.floor;
    if (!map || !floor) return;
    const id = "site-floor-plan";
    map.addSource(id, { type: "image", url: floor.imageUrl, coordinates: floor.corners });
    map.addLayer({ id, type: "raster", source: id, paint: { "raster-opacity": .9, "raster-fade-duration": 0 } }, "monitor-zones");
    return () => {
      if (mapRef.current !== map) return;
      if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(id)) map.removeSource(id);
    };
  }, [markerRuntime, props.floor]);

  useEffect(() => {
    if (ready && focus?.points.length && mapRef.current) focusPoints(mapRef.current, focus.points);
  }, [focus, ready]);

  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current;
    if (!map) return;
    map.setLayoutProperty("imagery", "visibility", satellite ? "visible" : "none");
    for (const id of PLAN_LAYERS) map.setLayoutProperty(id, "visibility", satellite ? "none" : "visible");
    map.setPaintProperty("background", "background-color", satellite ? "#09090b" : "#171719");
  }, [ready, satellite]);

  useEffect(() => {
    if (ready) mapRef.current?.setLayoutProperty("monitor-device-labels", "visibility", deviceLabels ? "visible" : "none");
  }, [ready, deviceLabels]);

  useEffect(() => {
    const map = markerRuntime?.map;
    const node = container.current;
    if (!map || !node || drawing) return;
    let frame = 0;
    const scheduleLayout = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => { frame = 0; layoutAlertLabels(node); });
    };
    scheduleLayout();
    map.on("move", scheduleLayout);
    map.on("resize", scheduleLayout);
    return () => {
      window.cancelAnimationFrame(frame);
      map.off("move", scheduleLayout);
      map.off("resize", scheduleLayout);
    };
  }, [markerRuntime, pins, replayEvent, siteDetail, drawing]);

  useEffect(() => {
    if (!ready || !mapRef.current) return;
    mapRef.current.getCanvas().style.cursor = drawing ? "crosshair" : "grab";
    if (drawing) mapRef.current.doubleClickZoom.disable();
    else mapRef.current.doubleClickZoom.enable();
  }, [drawing, ready]);


  return <>
    <div className="gv-map-canvas" ref={container} />
    {markerRuntime && sensors.filter((sensor) => siteDetail || selected === sensor.id || (selected && pins.atDevice.get(sensor.id)?.alert.id === selected))
      .map((sensor) => <DeviceMarker key={sensor.id} runtime={markerRuntime} sensor={sensor}
      health={healthById?.get(sensor.id)} maintenance={maintenanceById.get(sensor.id)}
      selected={selected === sensor.id} drawing={drawing} onSelect={() => props.onSelect("sensor", sensor.id)}
      pin={siteDetail || pins.atDevice.get(sensor.id)?.alert.id === selected ? pins.atDevice.get(sensor.id) : undefined}
      alertSelected={pins.atDevice.get(sensor.id)?.alert.id === selected && !!selected}
      onSelectAlert={(id) => props.onSelect("alert", id)} />)}
    {markerRuntime && !drawing && pins.standalone.filter(({ alert }) => siteDetail || alert.id === selected).map((pin) =>
      <MapMarker key={pin.alert.observation.position.join(",")} runtime={markerRuntime} position={pin.alert.observation.position}>
        <AlertMarkerButton pin={pin} selected={selected === pin.alert.id} onSelect={() => props.onSelect("alert", pin.alert.id)} />
      </MapMarker>)}
    {markerRuntime && replayEvent && replayPosition && <MapMarker runtime={markerRuntime} position={replayPosition}
      offsetY={sensors.some((sensor) => sameMapPoint(sensor.position, replayPosition)) ? -33 : 0}>
      <span className="gv-map-replay" role="img" aria-label={"Replay event " + replayEvent.observation.summary}
        title={replayEvent.observation.summary + " · " + replayEvent.sensorName}>
        <History size={15} aria-hidden="true" />
        <span className="gv-map-alert-label" aria-hidden="true"><strong>{replayEvent.observation.summary}</strong><small>Replay event</small></span>
      </span>
    </MapMarker>}
    {markerRuntime && props.vertexEditing && <BoundaryHandles runtime={markerRuntime} vertices={draft} editing={props.vertexEditing} />}
    {markerRuntime && !props.vertexEditing && draft.map((position, index) => <MapMarker key={index} runtime={markerRuntime} position={position}>
      <span className="gv-draft-vertex" aria-hidden="true">{index + 1}</span>
    </MapMarker>)}
    {markerRuntime && props.devicePlacement && <PlacementMarker runtime={markerRuntime} draft={props.devicePlacement} />}
    {!ready && <p className="gv-map-message">Loading map…</p>}
    {siteDetail && props.legend.length > 0 && <div className="gv-map-legend" role="group" aria-label="Map overlay legend">
      {props.legend.map(({ color, label }) => <span key={label}><i style={{ background: color }} />{label}</span>)}
    </div>}
    <div className="gv-map-controls" role="group" aria-label="Map controls">
      <button type="button" aria-label="Zoom in" title="Zoom in" disabled={!ready} onClick={() => mapRef.current?.zoomIn({ duration: mapDuration(250) })}><Plus size={18} /></button>
      <button type="button" aria-label="Zoom out" title="Zoom out" disabled={!ready} onClick={() => mapRef.current?.zoomOut({ duration: mapDuration(250) })}><Minus size={18} /></button>
      <button type="button" aria-label="Fit demo site" title="Fit demo site" disabled={!ready} onClick={() => { if (mapRef.current) focusPoints(mapRef.current, SITE_BOUNDS); }}><Crosshair size={17} /></button>
      <button type="button" aria-label="Globe overview" title="Globe overview" disabled={!ready} onClick={() => mapRef.current?.easeTo({ center: SITE_CENTER, zoom: 1.9, duration: mapDuration(600) })}><Globe2 size={17} /></button>
    </div>
    <div className="gv-map-meta">
      <div className="gv-basemaps" role="group" aria-label="Basemap">
        <button type="button" disabled={!ready} aria-pressed={satellite} onClick={() => setSatellite(true)}><Satellite size={13} /> Satellite</button>
        <button type="button" disabled={!ready} aria-pressed={!satellite} onClick={() => setSatellite(false)}><Layers3 size={13} /> Site plan</button>
      </div>
      {satellite && <a href="https://www.esri.com/en-us/legal/terms/data-attributions" target="_blank" rel="noreferrer">© Esri</a>}
      <a href="https://openfreemap.org/" target="_blank" rel="noreferrer">OpenFreeMap</a>
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OSM</a>
    </div>
  </>;
}
