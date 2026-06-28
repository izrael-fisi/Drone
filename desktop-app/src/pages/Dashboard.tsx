import { useEffect, useState } from "react";
import { Activity, Check, Download, Layers, LocateFixed, Map as MapIcon, Minus, Navigation, Plus, Route, Scissors, X } from "lucide-react";
import { CircleMarker, MapContainer, Pane, Polygon, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import { useShellStore } from "../lib/shellStore";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import type { DronePositionUpdate, Region } from "../lib/types";
import { cn, formatDate } from "../lib/utils";

function regionCenter(region?: Region): [number, number] {
  if (!region) return [37.775, -122.418];
  return [(region.lat_min + region.lat_max) / 2, (region.lon_min + region.lon_max) / 2];
}

function regionPolygon(region?: Region): [number, number][] {
  if (!region) return [];
  return [
    [region.lat_min, region.lon_min],
    [region.lat_min, region.lon_max],
    [region.lat_max, region.lon_max],
    [region.lat_max, region.lon_min],
  ];
}

function pointsBounds(points: [number, number][]) {
  const lats = points.map(([lat]) => lat);
  const lons = points.map(([, lon]) => lon);
  return {
    lat_min: Math.min(...lats),
    lat_max: Math.max(...lats),
    lon_min: Math.min(...lons),
    lon_max: Math.max(...lons),
  };
}

function positionLatLon(position: DronePositionUpdate | null): [number, number] | null {
  const lat = position?.lat_lon?.lat;
  const lon = position?.lat_lon?.lon;
  return typeof lat === "number" && Number.isFinite(lat) && typeof lon === "number" && Number.isFinite(lon)
    ? [lat, lon]
    : null;
}

function positionLabel(position: DronePositionUpdate | null) {
  if (!position) return "No packet";
  if (position.source_state === "gps_primary") return "GPS primary";
  if (position.source_state === "vision_correction") return "Vision fix";
  if (position.source_state === "dead_reckoning_between_fixes") return "Dead reckoning";
  if (position.source_state === "gps_degraded") return "GPS degraded";
  if (position.source_state === "no_position") return "No position";
  if (position.source === "gps") return "GPS primary";
  if (position.source === "vision") return "Vision fallback";
  return String(position.source_state ?? position.source ?? "Unknown").replace(/_/g, " ");
}

function positionTone(position: DronePositionUpdate | null) {
  if (!position) return "offline";
  if (position.source_state === "gps_primary" || position.source === "gps") return "ready";
  if (position.source_state === "vision_correction" || position.source === "vision") return "active";
  if (position.source_state === "dead_reckoning_between_fixes" || position.source_state === "gps_degraded") return "warning";
  return "critical";
}

function toneClass(tone: string) {
  if (tone === "ready") return "text-status-ready";
  if (tone === "active") return "text-status-active";
  if (tone === "warning") return "text-status-warning";
  if (tone === "critical") return "text-status-critical";
  return "text-slate-500";
}

function ledClass(tone: string) {
  if (tone === "ready") return "ops-led-ready";
  if (tone === "active") return "ops-led-active";
  if (tone === "warning") return "ops-led-warning";
  if (tone === "critical") return "ops-led-critical";
  return "ops-led-offline";
}

type MapApi = {
  zoomIn: () => void;
  zoomOut: () => void;
  recenter: () => void;
};

type MapToolMode = "idle" | "border" | "waypoint";
type MapSelectionValue = "world" | string;

const MAP_MIN_ZOOM = 3;
const WEB_MERCATOR_LAT_LIMIT = 85.05112878;

function wrapMapLongitude(lon: number) {
  return ((((lon + 180) % 360) + 360) % 360) - 180;
}

function wrapMapLatitude(lat: number) {
  const span = WEB_MERCATOR_LAT_LIMIT * 2;
  let next = lat;
  while (next > WEB_MERCATOR_LAT_LIMIT) next -= span;
  while (next < -WEB_MERCATOR_LAT_LIMIT) next += span;
  return Math.max(-WEB_MERCATOR_LAT_LIMIT, Math.min(WEB_MERCATOR_LAT_LIMIT, next));
}

function normalizeMapCenter(center: [number, number]): [number, number] {
  return [wrapMapLatitude(center[0]), wrapMapLongitude(center[1])];
}

export function Dashboard() {
  const { devices, regions, activeDeviceId, addRegion } = useAppStore();
  const { rightDockOpen, resetRightDock, pushRightDock, mapSearchTarget } = useShellStore();
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const readyMaps = regions.filter((region) => region.last_downloaded);
  const [selectedMap, setSelectedMap] = useState<MapSelectionValue>("world");
  const selectedRegion = selectedMap !== "world" ? regions.find((region) => region.id === selectedMap) : undefined;
  const activeMap = selectedRegion;
  const [position, setPosition] = useState<DronePositionUpdate | null>(null);
  const [telemetryMessage, setTelemetryMessage] = useState("listening");
  const [mapApi, setMapApi] = useState<MapApi | null>(null);
  const [toolMode, setToolMode] = useState<MapToolMode>("idle");
  const [borderPoints, setBorderPoints] = useState<[number, number][]>([]);
  const [waypoints, setWaypoints] = useState<[number, number][]>([]);
  const positionPort = Number(localStorage.getItem("vision_nav_position_udp_port") || 17660);
  const currentPosition = positionLatLon(position);
  const searchedPosition: [number, number] | null = mapSearchTarget ? [mapSearchTarget.lat, mapSearchTarget.lon] : null;
  const mapCenter = searchedPosition ?? currentPosition ?? (activeMap ? regionCenter(activeMap) : [20, 0] as [number, number]);
  const mapPolygon = regionPolygon(activeMap);
  const positionState = positionTone(position);
  const mapState = selectedMap === "world" ? "ready" : activeMap?.active_bundle_path || activeMap?.lifecycle_state === "active" ? "active" : readyMaps.length ? "ready" : "warning";
  const mapZoom = Math.max(MAP_MIN_ZOOM, mapSearchTarget?.zoom ?? (activeMap ? 14 : MAP_MIN_ZOOM));
  const hudLeftClass = rightDockOpen ? "left-[444px]" : "left-20";

  useEffect(() => {
    if (selectedMap !== "world" && !regions.some((region) => region.id === selectedMap)) {
      setSelectedMap("world");
    }
  }, [regions, selectedMap]);

  const openMapsPanel = () => {
    resetRightDock();
    pushRightDock("maps");
  };

  const saveBorderCut = async () => {
    if (borderPoints.length < 3) return;
    const id = `manual-border-${Date.now()}`;
    const bounds = pointsBounds(borderPoints);
    const nextRegion: Region = {
      id,
      name: `Map cut ${new Date().toLocaleString()}`,
      ...bounds,
      zoom: 16,
      source: "folder",
      output_path: `manual-border://${id}`,
      lifecycle_state: "local",
      tile_count: borderPoints.length,
      location_label: "Manual border cut",
    };
    const nextRegions = [...regions, nextRegion];
    addRegion(nextRegion);
    setSelectedMap(id);
    setBorderPoints([]);
    setToolMode("idle");
    try {
      await cmd.saveRegions(nextRegions);
    } catch (error) {
      console.warn("Failed to persist manual border cut", error);
    }
  };

  const clearMapTool = () => {
    setToolMode("idle");
    setBorderPoints([]);
    setWaypoints([]);
  };

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      try {
        const packet = await cmd.receivePositionUpdate(positionPort, 300);
        if (cancelled) return;
        if (packet) {
          setPosition(packet);
          setTelemetryMessage(`packet ${packet.sequence ?? "n/a"}`);
        } else {
          setTelemetryMessage("waiting");
        }
      } catch (error) {
        if (!cancelled) setTelemetryMessage(`unavailable: ${String(error)}`);
      } finally {
        if (!cancelled) timer = setTimeout(poll, 1600);
      }
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [positionPort]);

  return (
    <div className="ops-screen-bg relative h-full min-h-[calc(100vh-127px)] overflow-hidden animate-fade-in">
      <div className="absolute inset-0">
        <MapContainer
          center={normalizeMapCenter(mapCenter)}
          zoom={mapZoom}
          minZoom={MAP_MIN_ZOOM}
          worldCopyJump
          className="mission-map h-full w-full"
          scrollWheelZoom
          attributionControl={false}
          zoomControl={false}
        >
          <MapViewportController center={mapCenter} zoom={mapZoom} setMapApi={setMapApi} />
          <MapDrawController
            mode={toolMode}
            onPoint={(point) => {
              if (toolMode === "border") setBorderPoints((current) => [...current, point]);
              if (toolMode === "waypoint") setWaypoints((current) => [...current, point]);
            }}
          />
          <TileLayer
            url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
            maxZoom={19}
          />
          <Pane name="mission-labels" className="mission-map-label-pane" style={{ zIndex: 420 }}>
            <TileLayer
              url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
              maxZoom={19}
              opacity={0.92}
            />
          </Pane>
          {mapPolygon.length > 0 && (
            <Polygon
              positions={mapPolygon}
              pathOptions={{ color: "#FF6600", fillColor: "#FF6600", fillOpacity: 0.08, weight: 2 }}
            />
          )}
          {borderPoints.length > 0 && (
            <>
              {borderPoints.length >= 3 ? (
                <Polygon
                  positions={borderPoints}
                  pathOptions={{ color: "#FF6600", fillColor: "#FF6600", fillOpacity: 0.1, dashArray: "8 8", weight: 2 }}
                />
              ) : (
                <Polyline positions={borderPoints} pathOptions={{ color: "#FF6600", dashArray: "8 8", weight: 2 }} />
              )}
              {borderPoints.map((point, index) => (
                <CircleMarker
                  key={`border-${index}-${point[0]}-${point[1]}`}
                  center={point}
                  radius={5}
                  pathOptions={{ color: "#FF6600", fillColor: "#0B0C0D", fillOpacity: 0.95, weight: 2 }}
                >
                  <Tooltip direction="top" offset={[0, -5]} opacity={0.9}>
                    Cut {index + 1}
                  </Tooltip>
                </CircleMarker>
              ))}
            </>
          )}
          {waypoints.length > 0 && (
            <>
              {waypoints.length > 1 && (
                <Polyline positions={waypoints} pathOptions={{ color: "#FF6600", weight: 3, opacity: 0.9 }} />
              )}
              {waypoints.map((point, index) => {
                const isStart = index === 0;
                const isLand = waypoints.length > 1 && index === waypoints.length - 1;
                const color = isStart ? "#63D706" : isLand ? "#EF4444" : "#FF6600";
                const label = isStart ? "Start" : isLand ? "Land" : `WP ${index + 1}`;
                return (
                  <CircleMarker
                    key={`waypoint-${index}-${point[0]}-${point[1]}`}
                    center={point}
                    radius={7}
                    pathOptions={{ color, fillColor: color, fillOpacity: 0.9, weight: 3 }}
                  >
                    <Tooltip direction="top" offset={[0, -7]} opacity={0.95} permanent>
                      {label}
                    </Tooltip>
                  </CircleMarker>
                );
              })}
            </>
          )}
          {currentPosition && (
            <CircleMarker
              center={currentPosition}
              radius={9}
              pathOptions={{
                color: positionState === "ready" ? "#63D706" : positionState === "active" ? "#FF6600" : "#F59E0B",
                fillColor: positionState === "ready" ? "#2E8F49" : positionState === "active" ? "#B84A00" : "#9B6B16",
                fillOpacity: 0.95,
                weight: 3,
              }}
            />
          )}
        </MapContainer>
      </div>

      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-bg-base/45 via-transparent to-bg-base/50" />

      <MapRightControls
        regions={regions}
        activeMap={activeMap}
        selectedMap={selectedMap}
        onMapChange={(id) => setSelectedMap(id)}
        onOpenMapsPanel={openMapsPanel}
        mode={toolMode}
        borderPointCount={borderPoints.length}
        waypointCount={waypoints.length}
        onModeChange={setToolMode}
        onSaveBorder={saveBorderCut}
        onClearDrawing={clearMapTool}
        onZoomIn={() => mapApi?.zoomIn()}
        onZoomOut={() => mapApi?.zoomOut()}
        onRecenter={() => mapApi?.recenter()}
        zoomReady={Boolean(mapApi)}
      />

      <section className={cn("pointer-events-none absolute top-3 z-[1100] w-[320px] rounded-lg border border-border bg-bg-base/92 p-3 shadow-2xl backdrop-blur-sm transition-[left] duration-200", hudLeftClass)}>
        <div className="flex items-center gap-2">
          <span className={cn("ops-led", activeDevice ? "ops-led-ready" : "ops-led-offline")} />
          <Activity size={13} className={activeDevice ? "text-status-ready" : "text-slate-500"} />
          <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">Connection</span>
          <span className="ml-auto truncate font-data-mono text-[10px] text-slate-400">{activeDevice?.name ?? "Offline"}</span>
        </div>
        <div className="mt-3 grid gap-2">
          <HudRow
            Icon={MapIcon}
            label="Active Map"
            value={selectedMap === "world" ? "World Map" : activeMap?.name ?? "No map selected"}
            detail={selectedMap === "world" ? "Global imagery base layer" : activeMap?.last_downloaded ? formatDate(activeMap.last_downloaded) : "Use Maps panel"}
            tone={mapState}
          />
          <HudRow
            Icon={Navigation}
            label="Position"
            value={positionLabel(position)}
            detail={currentPosition ? `${currentPosition[0].toFixed(6)}, ${currentPosition[1].toFixed(6)}` : `UDP ${positionPort} ${telemetryMessage}`}
            tone={positionState}
          />
        </div>
      </section>
    </div>
  );
}

function MapViewportController({
  center,
  zoom,
  setMapApi,
}: {
  center: [number, number];
  zoom: number;
  setMapApi: (api: MapApi) => void;
}) {
  const map = useMap();
  const centerLat = center[0];
  const centerLon = center[1];

  useEffect(() => {
    setMapApi({
      zoomIn: () => map.zoomIn(),
      zoomOut: () => {
        if (map.getZoom() > MAP_MIN_ZOOM) map.zoomOut();
      },
      recenter: () => map.setView(normalizeMapCenter(center), Math.max(MAP_MIN_ZOOM, zoom), { animate: true }),
    });
  }, [centerLat, centerLon, map, setMapApi, zoom]);

  useEffect(() => {
    map.setMinZoom(MAP_MIN_ZOOM);
    if (map.getZoom() < MAP_MIN_ZOOM) map.setZoom(MAP_MIN_ZOOM, { animate: false });
  }, [map]);

  useEffect(() => {
    let wrapping = false;
    const wrapWorldCenter = () => {
      if (wrapping) return;
      const current = map.getCenter();
      const nextLat = wrapMapLatitude(current.lat);
      const nextLng = wrapMapLongitude(current.lng);
      const needsWrap = Math.abs(nextLat - current.lat) > 0.000001 || Math.abs(nextLng - current.lng) > 0.000001;
      if (!needsWrap) return;
      wrapping = true;
      map.setView([nextLat, nextLng], Math.max(MAP_MIN_ZOOM, map.getZoom()), { animate: false });
      wrapping = false;
    };

    map.on("moveend", wrapWorldCenter);
    map.on("zoomend", wrapWorldCenter);
    wrapWorldCenter();
    return () => {
      map.off("moveend", wrapWorldCenter);
      map.off("zoomend", wrapWorldCenter);
    };
  }, [map]);

  useEffect(() => {
    map.setView(normalizeMapCenter(center), Math.max(MAP_MIN_ZOOM, zoom), { animate: true });
  }, [centerLat, centerLon, map, zoom]);

  return null;
}

function MapDrawController({
  mode,
  onPoint,
}: {
  mode: MapToolMode;
  onPoint: (point: [number, number]) => void;
}) {
  useMapEvents({
    click(event) {
      if (mode === "idle") return;
      onPoint([event.latlng.lat, event.latlng.lng]);
    },
  });
  return null;
}

function MapRightControls({
  regions,
  activeMap,
  selectedMap,
  onMapChange,
  onOpenMapsPanel,
  mode,
  borderPointCount,
  waypointCount,
  onModeChange,
  onSaveBorder,
  onClearDrawing,
  onZoomIn,
  onZoomOut,
  onRecenter,
  zoomReady,
}: {
  regions: Region[];
  activeMap?: Region;
  selectedMap: MapSelectionValue;
  onMapChange: (id: MapSelectionValue) => void;
  onOpenMapsPanel: () => void;
  mode: MapToolMode;
  borderPointCount: number;
  waypointCount: number;
  onModeChange: (mode: MapToolMode) => void;
  onSaveBorder: () => void;
  onClearDrawing: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onRecenter: () => void;
  zoomReady: boolean;
}) {
  const [mapMenuOpen, setMapMenuOpen] = useState(false);
  const selectedLabel = selectedMap === "world" ? "World Map" : activeMap?.name ?? "Uploaded map missing";
  const drawingActive = mode !== "idle";

  return (
    <section className="absolute right-4 top-4 z-[1150] flex w-[264px] flex-col gap-2">
      <div className="rounded-lg border border-white/10 bg-bg-base/60 p-2 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl">
        <div className="mb-2 flex items-center gap-2 px-1">
          <Layers size={14} className="text-cyan-400" />
          <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">
            Map
          </span>
          <span className="ml-auto truncate text-[11px] text-slate-500">
            {regions.length ? `${regions.length} available` : "none"}
          </span>
        </div>
        <div className="relative">
          <button
            type="button"
            onClick={() => setMapMenuOpen((current) => !current)}
            className="flex h-9 w-full items-center gap-2 rounded-md border border-white/10 bg-black/55 px-2 text-left text-xs text-slate-200 outline-none transition-colors hover:border-orange-500/55"
            title="Map selection"
          >
            <MapIcon size={13} className="text-orange-300" />
            <span className="min-w-0 flex-1 truncate">{selectedLabel}</span>
            <span className="text-[10px] uppercase tracking-[0.08em] text-slate-600">
              {selectedMap === "world" ? "Base" : "Upload"}
            </span>
          </button>
          {mapMenuOpen && (
            <div className="absolute right-0 top-10 z-[1250] w-full overflow-hidden rounded-lg border border-white/10 bg-black/90 shadow-2xl">
              <button
                type="button"
                onClick={() => {
                  onMapChange("world");
                  setMapMenuOpen(false);
                }}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-white/[0.06]",
                  selectedMap === "world" ? "text-orange-300" : "text-slate-300",
                )}
              >
                <MapIcon size={13} />
                World Map
              </button>
              <div className="border-t border-white/10 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-600">
                Uploaded Maps
              </div>
              {regions.length === 0 ? (
                <div className="px-3 py-2 text-xs text-slate-600">No uploaded maps</div>
              ) : (
                regions.map((region) => (
                  <button
                    key={region.id}
                    type="button"
                    onClick={() => {
                      onMapChange(region.id);
                      setMapMenuOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-white/[0.06]",
                      selectedMap === region.id ? "text-orange-300" : "text-slate-300",
                    )}
                  >
                    <Layers size={13} />
                    <span className="min-w-0 flex-1 truncate">{region.name}</span>
                  </button>
                ))
              )}
              <button
                type="button"
                onClick={() => {
                  setMapMenuOpen(false);
                  onOpenMapsPanel();
                }}
                className="flex w-full items-center justify-center gap-2 border-t border-white/10 px-3 py-2 text-xs text-slate-300 transition-colors hover:bg-orange-500/10 hover:text-orange-200"
              >
                <Download size={13} />
                Import Map
              </button>
            </div>
          )}
        </div>

        <div className="mt-2 grid grid-cols-2 gap-2">
          <ToolButton
            Icon={Scissors}
            label="Map Border"
            active={mode === "border"}
            onClick={() => {
              setMapMenuOpen(false);
              onModeChange(mode === "border" ? "idle" : "border");
            }}
          />
          <ToolButton
            Icon={Route}
            label="Waypoint"
            active={mode === "waypoint"}
            onClick={() => {
              setMapMenuOpen(false);
              onModeChange(mode === "waypoint" ? "idle" : "waypoint");
            }}
          />
        </div>

        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setMapMenuOpen(false);
              onOpenMapsPanel();
            }}
            className="flex h-8 flex-1 items-center justify-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-2 text-xs text-slate-300 transition-colors hover:border-orange-500/55 hover:text-slate-100"
          >
            <MapIcon size={13} />
            Manage
          </button>
          <span className={cn("ops-led rounded-full", selectedMap === "world" || activeMap ? "ops-led-active" : "ops-led-warning")} />
        </div>
        {drawingActive && (
          <div className="mt-2 rounded-md border border-white/10 bg-black/45 p-2">
            <div className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
              <span>{mode === "border" ? "Click map to cut border" : "Click map to plot path"}</span>
              <span className="font-data-mono text-orange-300">
                {mode === "border" ? borderPointCount : waypointCount} pts
              </span>
            </div>
            <div className="mt-2 flex gap-2">
              {mode === "border" && (
                <button
                  type="button"
                  onClick={onSaveBorder}
                  disabled={borderPointCount < 3}
                  className="flex h-7 flex-1 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.035] text-[11px] text-slate-300 transition-colors hover:border-orange-500/55 hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Check size={12} />
                  Save Cut
                </button>
              )}
              <button
                type="button"
                onClick={onClearDrawing}
                className="flex h-7 flex-1 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.035] text-[11px] text-slate-400 transition-colors hover:border-red-500/45 hover:text-red-200"
              >
                <X size={12} />
                Clear
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="ml-auto flex w-11 flex-col overflow-hidden rounded-lg border border-white/10 bg-bg-base/60 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl">
        <MapControlButton Icon={Plus} label="Zoom in" disabled={!zoomReady} onClick={onZoomIn} />
        <MapControlButton Icon={Minus} label="Zoom out" disabled={!zoomReady} onClick={onZoomOut} />
        <MapControlButton Icon={LocateFixed} label="Recenter" disabled={!zoomReady} onClick={onRecenter} />
      </div>
    </section>
  );
}

function MapControlButton({
  Icon,
  label,
  disabled,
  onClick,
}: {
  Icon: typeof Plus;
  label: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex h-11 w-11 items-center justify-center border-b border-white/10 text-slate-300 transition-colors last:border-b-0 hover:bg-cyan-500/10 hover:text-cyan-300 disabled:cursor-not-allowed disabled:opacity-40"
      title={label}
    >
      <Icon size={17} />
    </button>
  );
}

function ToolButton({
  Icon,
  label,
  active,
  onClick,
}: {
  Icon: typeof Scissors;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-8 items-center justify-center gap-1 rounded-md border px-2 text-[11px] transition-colors",
        active
          ? "border-orange-500/75 bg-orange-500/18 text-orange-200"
          : "border-white/10 bg-white/[0.035] text-slate-400 hover:border-orange-500/55 hover:text-slate-100",
      )}
    >
      <Icon size={12} />
      <span className="truncate">{label}</span>
    </button>
  );
}

function HudRow({
  Icon,
  label,
  value,
  detail,
  tone,
}: {
  Icon: typeof MapIcon;
  label: string;
  value: string;
  detail: string;
  tone: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-bg-card/80 px-3 py-2">
      <div className="flex items-center gap-2">
        <span className={cn("ops-led", ledClass(tone))} />
        <Icon size={13} className={toneClass(tone)} />
        <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">{label}</span>
      </div>
      <div className={cn("mt-1 truncate font-data-mono text-sm font-semibold", toneClass(tone))}>{value}</div>
      <div className="truncate font-data-mono text-[10px] text-slate-500">{detail}</div>
    </div>
  );
}
