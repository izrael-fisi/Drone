import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Archive,
  ArrowRight,
  Camera,
  CheckCircle2,
  Circle,
  Map as MapIcon,
  Navigation,
  Radio,
  Route,
  Server,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { CircleMarker, MapContainer, Polygon, TileLayer } from "react-leaflet";
import { useNavigate } from "react-router-dom";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import type { DronePositionUpdate, Region } from "../lib/types";
import { cn, formatDate } from "../lib/utils";

type OperatorStep = {
  label: string;
  detail: string;
  to: string;
  Icon: LucideIcon;
  done: boolean;
};

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

export function Dashboard() {
  const { profile, devices, regions, activeDeviceId } = useAppStore();
  const navigate = useNavigate();
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const readyMaps = regions.filter((region) => region.last_downloaded);
  const activeMap =
    regions.find((region) => region.lifecycle_state === "active") ??
    regions.find((region) => region.active_bundle_path) ??
    readyMaps[0] ??
    regions[0];
  const [position, setPosition] = useState<DronePositionUpdate | null>(null);
  const [telemetryMessage, setTelemetryMessage] = useState("listening");
  const positionPort = Number(localStorage.getItem("vision_nav_position_udp_port") || 17660);
  const currentPosition = positionLatLon(position);
  const mapCenter = currentPosition ?? regionCenter(activeMap);
  const mapPolygon = regionPolygon(activeMap);
  const positionState = positionTone(position);

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

  const readiness = useMemo(() => {
    const hasMap = readyMaps.length > 0;
    const hasDrone = Boolean(activeDevice);
    const hasBundle = Boolean(activeMap?.active_bundle_path || activeMap?.lifecycle_state === "active");
    return [
      { label: "Map", detail: hasMap ? activeMap?.name ?? `${readyMaps.length} ready` : "Choose area", to: "/maps", Icon: MapIcon, done: hasMap },
      { label: "Mission", detail: hasMap ? "Plan route" : "Needs map", to: "/mission-planner", Icon: Route, done: hasMap },
      { label: "Drone", detail: activeDevice?.name ?? "Select vehicle", to: "/devices", Icon: Server, done: hasDrone },
      { label: "Bundle", detail: hasBundle ? "Active" : "Build/upload", to: "/mission-bundle-builder", Icon: Upload, done: hasBundle },
      { label: "Fly", detail: hasDrone && hasMap ? "Monitor" : "Not ready", to: "/system-status", Icon: Radio, done: hasDrone && hasMap },
      { label: "Review", detail: "Evidence", to: "/flight-review", Icon: Archive, done: false },
    ] satisfies OperatorStep[];
  }, [activeDevice, activeMap, readyMaps.length]);

  const nextStep = readiness.find((step) => !step.done && step.label !== "Review") ?? readiness.find((step) => step.label === "Fly") ?? readiness[0];

  return (
    <div className="ops-screen-bg relative h-full min-h-[calc(100vh-96px)] overflow-hidden animate-fade-in">
      <div className="absolute inset-0">
        <MapContainer center={mapCenter} zoom={activeMap ? 14 : 11} className="mission-map h-full w-full" scrollWheelZoom attributionControl={false}>
          <TileLayer
            url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
            maxZoom={19}
          />
          {mapPolygon.length > 0 && (
            <Polygon
              positions={mapPolygon}
              pathOptions={{ color: "#68C7E6", fillColor: "#68C7E6", fillOpacity: 0.08, weight: 2 }}
            />
          )}
          {currentPosition && (
            <CircleMarker
              center={currentPosition}
              radius={9}
              pathOptions={{
                color: positionState === "ready" ? "#7CCB8A" : positionState === "active" ? "#68C7E6" : "#D6A84C",
                fillColor: positionState === "ready" ? "#2E8F49" : positionState === "active" ? "#0B7FA6" : "#9B6B16",
                fillOpacity: 0.95,
                weight: 3,
              }}
            />
          )}
        </MapContainer>
      </div>

      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-bg-base/55 via-transparent to-bg-base/65" />

      <section className="pointer-events-auto absolute left-3 top-3 z-[520] w-[330px] border border-border bg-bg-card/95 p-4">
        <div className="font-data-mono text-[10px] uppercase tracking-[0.18em] text-slate-500">
          {profile?.org || "Drone Vision"}
        </div>
        <h1 className="mt-1 font-data-mono text-xl font-bold text-slate-100">Operations Map</h1>
        <p className="mt-2 text-xs leading-relaxed text-slate-400">
          Plan from the map, connect the runtime module, then monitor GPS, vision, and dead-reckoning position from one surface.
        </p>

        <button
          type="button"
          onClick={() => navigate(nextStep.to)}
          className="mt-4 flex h-9 w-full items-center justify-center gap-2 border border-cyan-500/50 bg-cyan-500/10 px-3 font-data-mono text-xs font-bold uppercase tracking-[0.1em] text-cyan-200 hover:bg-cyan-500/20"
        >
          Continue: {nextStep.label}
          <ArrowRight size={14} />
        </button>

        <div className="mt-4 grid grid-cols-2 gap-2">
          {readiness.slice(0, 4).map((step) => {
            const Icon = step.Icon;
            return (
              <button
                key={step.label}
                type="button"
                onClick={() => navigate(step.to)}
                className="border border-border bg-bg-base/80 p-3 text-left hover:border-cyan-500/40"
              >
                <div className="flex items-center justify-between">
                  <Icon size={15} className={step.done ? "text-status-ready" : "text-slate-500"} />
                  {step.done ? <CheckCircle2 size={13} className="text-status-ready" /> : <Circle size={12} className="text-slate-600" />}
                </div>
                <div className="mt-2 text-xs font-semibold text-slate-200">{step.label}</div>
                <div className="mt-0.5 truncate font-data-mono text-[10px] text-slate-500">{step.detail}</div>
              </button>
            );
          })}
        </div>
      </section>

      <section className="pointer-events-auto absolute right-3 top-3 z-[520] w-[316px] space-y-3 border border-border bg-bg-card/95 p-3">
        <StatusRow
          icon={<Activity size={14} />}
          label="Connection"
          value={activeDevice?.name ?? "No device selected"}
          detail={activeDevice?.host ?? "Planning mode"}
          tone={activeDevice ? "ready" : "warning"}
        />
        <StatusRow
          icon={<Navigation size={14} />}
          label="Position Source"
          value={positionLabel(position)}
          detail={currentPosition ? `${currentPosition[0].toFixed(6)}, ${currentPosition[1].toFixed(6)}` : `UDP ${positionPort} ${telemetryMessage}`}
          tone={positionState}
        />
        <StatusRow
          icon={<MapIcon size={14} />}
          label="Active Map"
          value={activeMap?.name ?? "No map selected"}
          detail={activeMap?.last_downloaded ? formatDate(activeMap.last_downloaded) : "download/import map"}
          tone={readyMaps.length ? "active" : "warning"}
        />
        <StatusRow
          icon={<ShieldCheck size={14} />}
          label="GNSS-Denied"
          value={activeMap?.active_bundle_path ? "Bundle active" : "Bundle not active"}
          detail={activeMap?.gsd_m_per_px != null ? `${activeMap.gsd_m_per_px.toFixed(2)} m/px source` : "build terrain bundle"}
          tone={activeMap?.active_bundle_path ? "ready" : "warning"}
        />
      </section>

      <section className="pointer-events-auto absolute bottom-3 left-3 right-3 z-[520] grid gap-3 lg:grid-cols-[1fr_1fr_1fr]">
        <ActionPanel
          title="Plan"
          Icon={Route}
          detail="Choose a map source and draw takeoff, waypoints, landing, fence, rally, and vision checkpoints."
          action="Open mission planner"
          onClick={() => navigate("/mission-planner")}
        />
        <ActionPanel
          title="Calibrate"
          Icon={Camera}
          detail="Configure camera/vision defaults and prepare calibration data before flight capture."
          action="Open camera settings"
          onClick={() => navigate("/camera-vision")}
        />
        <ActionPanel
          title="Monitor"
          Icon={Radio}
          detail="Watch MAVLink, GPS health, vision cadence, dead-reckoning state, and runtime diagnostics."
          action="Open live status"
          onClick={() => navigate("/system-status")}
        />
      </section>
    </div>
  );
}

function StatusRow({
  icon,
  label,
  value,
  detail,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  detail: string;
  tone: string;
}) {
  return (
    <div className="border border-border bg-bg-base/80 p-3">
      <div className="flex items-center gap-2">
        <span className={cn("ops-led", ledClass(tone))} />
        <span className={cn("text-slate-500", toneClass(tone))}>{icon}</span>
        <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">{label}</span>
      </div>
      <div className={cn("mt-2 truncate font-data-mono text-sm font-semibold", toneClass(tone))}>{value}</div>
      <div className="mt-0.5 truncate font-data-mono text-[10px] text-slate-500">{detail}</div>
    </div>
  );
}

function ActionPanel({
  title,
  detail,
  action,
  Icon,
  onClick,
}: {
  title: string;
  detail: string;
  action: string;
  Icon: LucideIcon;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group border border-border bg-bg-card/95 p-4 text-left hover:border-cyan-500/40"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
          <Icon size={16} className="text-status-active" />
          {title}
        </div>
        <ArrowRight size={14} className="text-slate-600 group-hover:text-status-active" />
      </div>
      <p className="mt-2 min-h-10 text-xs leading-relaxed text-slate-500">{detail}</p>
      <div className="mt-3 font-data-mono text-[10px] font-bold uppercase tracking-[0.1em] text-status-active">{action}</div>
    </button>
  );
}
