import { useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Activity, AlertTriangle, Cpu, Database, Map, Radio, RefreshCw, Server } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { loadPipelineConfig } from "../lib/pipelineConfig";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import type { DronePositionUpdate } from "../lib/types";
import { cn } from "../lib/utils";

type StatusTab = "diagnostics" | "parameters" | "mavlink";
type LogTone = "normal" | "ready" | "warning" | "critical" | "cyan";

function statusText(value?: string | null) {
  return value ? value.replace(/_/g, " ") : "unknown";
}

function sourceTone(position: DronePositionUpdate | null): LogTone {
  if (position?.source_state === "gps_primary" || (position?.source === "gps" && position.status === "accepted")) return "ready";
  if (position?.source_state === "vision_correction" || (position?.source === "vision" && position.status === "accepted")) return "cyan";
  if (position?.source_state === "dead_reckoning_between_fixes" || position?.source_state === "gps_degraded") return "warning";
  if (position?.status === "degraded") return "warning";
  return "critical";
}

function sourceLabel(position: DronePositionUpdate | null) {
  if (!position) return "NO PACKET";
  if (position.source_state === "gps_primary") return "GPS PRIMARY";
  if (position.source_state === "vision_correction") return "VISION CORRECTION";
  if (position.source_state === "dead_reckoning_between_fixes") return "DEAD RECKONING";
  if (position.source_state === "gps_degraded") return "GPS DEGRADED";
  if (position.source_state === "no_position") return "NO POSITION";
  if (position.source === "gps") return "GPS PRIMARY";
  if (position.source === "vision") return "VISION FALLBACK";
  if (position.source === "gps_degraded") return "GPS DEGRADED";
  return statusText(position.source).toUpperCase();
}

function toneText(tone: LogTone) {
  if (tone === "ready") return "text-status-ready font-bold";
  if (tone === "warning") return "text-status-warning font-bold";
  if (tone === "critical") return "text-status-critical font-bold";
  if (tone === "cyan") return "text-status-active font-bold";
  return "text-on-surface";
}

function serviceBadge(ok: boolean, degraded = false) {
  if (ok && !degraded) return "ONLINE";
  if (degraded) return "DEGRADED";
  return "OFFLINE";
}

export function SystemStatus() {
  const navigate = useNavigate();
  const { activeDeviceId, devices, regions } = useAppStore();
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const downloadedRegions = regions.filter((region) => region.last_downloaded);
  const activeRegion = regions.find((region) => region.lifecycle_state === "active") ?? regions.find((region) => region.active_bundle_path) ?? downloadedRegions[0];
  const pipelineConfig = useMemo(() => loadPipelineConfig(), []);
  const [position, setPosition] = useState<DronePositionUpdate | null>(null);
  const [telemetryMessage, setTelemetryMessage] = useState("Waiting for position packets");
  const [refreshing, setRefreshing] = useState(false);
  const [activeTab, setActiveTab] = useState<StatusTab>("diagnostics");
  const [terminalInput, setTerminalInput] = useState("");
  const [manualLogs, setManualLogs] = useState<Array<{ message: string; tone: LogTone }>>([]);
  const port = Number(localStorage.getItem("vision_nav_position_udp_port") || 17660);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const update = await cmd.receivePositionUpdate(port, 120);
        if (cancelled) return;
        if (update) {
          setPosition(update);
          setTelemetryMessage(`packet ${update.sequence ?? "n/a"} from ${statusText(update.source_state ?? update.source)}`);
        }
      } catch (error) {
        if (!cancelled) setTelemetryMessage(`telemetry unavailable: ${error}`);
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, 1200);
      }
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [port]);

  const refreshTelemetry = async () => {
    setRefreshing(true);
    try {
      const update = await cmd.receivePositionUpdate(port, 400);
      setPosition(update);
      setTelemetryMessage(update ? `packet ${update.sequence ?? "n/a"} received` : "no packet received");
    } catch (error) {
      setTelemetryMessage(`telemetry unavailable: ${error}`);
    } finally {
      setRefreshing(false);
    }
  };

  const submitCommand = (event: FormEvent) => {
    event.preventDefault();
    const command = terminalInput.trim().toLowerCase();
    if (!command) return;
    if (command === "clear") {
      setManualLogs([]);
      setTerminalInput("");
      return;
    }
    if (command.includes("map")) navigate("/maps");
    if (command.includes("mission")) navigate("/mission-planner");
    if (command.includes("device") || command.includes("vehicle")) navigate("/vehicle-manager");
    if (command.includes("vision") || command.includes("pipeline")) navigate("/camera-vision");
    const message =
      command === "status"
        ? `System status: ${activeDevice ? activeDevice.name : "no active device"}, ${downloadedRegions.length} maps, telemetry ${sourceLabel(position)}.`
        : command.includes("telemetry")
          ? `Telemetry listener: UDP ${port}; ${telemetryMessage}.`
          : command.includes("mavlink")
            ? `MAVLink endpoint: ${activeDevice?.mavlink_endpoint || "not configured"}.`
            : `Command accepted: ${terminalInput}`;
    setManualLogs((current) => [...current.slice(-8), { message, tone: command === "status" ? "ready" : "cyan" }]);
    setTerminalInput("");
  };

  const diagnosticsLogs: Array<{ message: string; tone: LogTone }> = [
    { message: `SYS_INIT: Drone Vision GCS diagnostics loaded. Runtime target: ${activeDevice?.name ?? "none selected"}.`, tone: activeDevice ? "ready" : "warning" },
    { message: `ACTIVE_MAP: ${activeRegion?.name ?? "none"} lifecycle=${activeRegion?.lifecycle_state ?? "not_selected"} bundle=${activeRegion?.active_bundle_path ?? "not activated"}.`, tone: activeRegion ? "ready" : "warning" },
    { message: `MAP_CACHE: ${downloadedRegions.length} downloaded map source${downloadedRegions.length === 1 ? "" : "s"} available.`, tone: downloadedRegions.length ? "ready" : "warning" },
    { message: `VISION: ${pipelineConfig.pipeline.toUpperCase()} pipeline, ${pipelineConfig.featureMethod.toUpperCase()} features, ${pipelineConfig.maxFeatures} max features.`, tone: "cyan" as LogTone },
    { message: `POSITION: ${sourceLabel(position)}; ${telemetryMessage}.`, tone: sourceTone(position) as LogTone },
    { message: `FIX_CADENCE: last=${position?.last_vision_fix_utc ?? "none"} age=${position?.seconds_since_vision_fix ?? "n/a"}s distance=${position?.meters_since_vision_fix ?? "n/a"}m.`, tone: position?.last_vision_fix_utc ? "cyan" : "warning" },
    { message: `GPS_HEALTH: ${position?.gps_health?.healthy ? "healthy" : position?.gps_health?.reason ?? "not reported"}.`, tone: position?.gps_health?.healthy ? "ready" : "warning" },
    { message: `VISION_HEALTH: ${position?.vision_health?.status ?? "not reported"}; tile ${position?.vision_health?.tile_id ?? "n/a"}.`, tone: position?.vision_health?.available ? "ready" : "warning" },
    ...manualLogs,
  ];

  const parameterLogs: Array<{ message: string; tone: LogTone }> = [
    { message: `VISION_NAV_POSITION_UDP_PORT=${port}`, tone: "cyan" },
    { message: `VISION_PIPELINE=${pipelineConfig.pipeline}`, tone: "cyan" },
    { message: `FEATURE_METHOD=${pipelineConfig.featureMethod}`, tone: "cyan" },
    { message: `MATCHER_RATIO=${pipelineConfig.matcherRatio.toFixed(2)}`, tone: "cyan" },
    { message: `MIN_MATCHES=${pipelineConfig.minMatches}`, tone: "cyan" },
    { message: `ACTIVE_DEVICE=${activeDevice?.name ?? "none"}`, tone: activeDevice ? "ready" : "warning" },
  ];

  const mavlinkLogs: Array<{ message: string; tone: LogTone }> = [
    { message: `mavlink endpoint: ${activeDevice?.mavlink_endpoint || "not configured"}`, tone: activeDevice?.mavlink_endpoint ? "ready" : "warning" },
    { message: "position source priority: GPS healthy -> terrain vision -> dead reckoning -> degraded GPS", tone: "cyan" },
    { message: "source states: gps_primary -> vision_correction -> dead_reckoning_between_fixes -> gps_degraded -> no_position", tone: "cyan" },
    { message: `last source: ${sourceLabel(position)} reason=${position?.source_transition_reason ?? "n/a"}`, tone: sourceTone(position) as LogTone },
    { message: `vision cadence: last=${position?.last_vision_fix_utc ?? "none"} age=${position?.seconds_since_vision_fix ?? "n/a"}s interval=${position?.vision_fix_interval_m ?? "n/a"}m`, tone: position?.last_vision_fix_utc ? "cyan" : "warning" },
    { message: `local ENU: x=${position?.local_enu_m?.x ?? "n/a"} y=${position?.local_enu_m?.y ?? "n/a"} z=${position?.local_enu_m?.z ?? "n/a"}`, tone: "normal" },
  ];

  const tabLogs = activeTab === "diagnostics" ? diagnosticsLogs : activeTab === "parameters" ? parameterLogs : mavlinkLogs;
  const cpuLoad = activeDevice ? (position ? 42 : 24) : 8;
  const memoryLoad = pipelineConfig.pipeline === "neural" ? 78 : 46;
  const serviceRows = [
    { label: "FLIGHT_CORE", ok: Boolean(activeDevice), degraded: false },
    { label: "TELEMETRY_LINK", ok: Boolean(position), degraded: !position },
    { label: "POSITION_SOURCE", ok: Boolean(position && position.source_state !== "no_position"), degraded: position?.source_state === "gps_degraded" || position?.dead_reckoning_active },
    { label: "VISION_RUNTIME", ok: true, degraded: pipelineConfig.pipeline === "neural" && activeDevice?.kind === "pi5" },
    { label: "MAP_CACHE", ok: downloadedRegions.length > 0, degraded: false },
  ];

  return (
    <div className="ops-screen-bg relative h-full min-h-[calc(100vh-96px)] overflow-hidden animate-fade-in">
      <div className="absolute inset-0 opacity-10 pointer-events-none" style={{
        backgroundImage: "linear-gradient(rgba(104, 199, 230, 0.12) 1px, transparent 1px), linear-gradient(90deg, rgba(104, 199, 230, 0.12) 1px, transparent 1px)",
        backgroundSize: "56px 56px",
      }} />

      <main className="glass-panel panel-3d-center absolute bottom-3 left-3 right-[336px] top-3 flex flex-col overflow-hidden">
        <div className="flex h-10 shrink-0 border-b border-border bg-bg-card px-2 font-label-caps text-label-caps uppercase">
          {[
            ["diagnostics", "DIAGNOSTICS"],
            ["parameters", "PARAMETERS"],
            ["mavlink", "MAVLINK CONSOLE"],
          ].map(([key, label]) => (
            <button
              key={key}
              onClick={() => setActiveTab(key as StatusTab)}
              className={cn(
                "px-6 h-full flex items-center border-b-2 rounded-t-md mt-1 transition-colors",
                activeTab === key
                    ? "border-status-active bg-cyan-500/10 text-cyan-300"
                    : "border-transparent text-on-surface-variant glass-hover",
              )}
            >
              {label}
            </button>
          ))}
          <button onClick={refreshTelemetry} disabled={refreshing} className="ml-auto flex items-center gap-2 px-3 text-status-active disabled:opacity-50">
            <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} /> REFRESH
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6 font-data-mono text-data-mono flex flex-col gap-1 select-text bg-transparent relative z-10">
          {tabLogs.map((line, index) => (
            <div
              key={`${activeTab}-${index}-${line.message}`}
              className={cn(
                "group flex gap-4 px-2 py-0.5 transition-colors hover:bg-bg-elevated",
                line.tone === "critical" && "bg-red-500/10 border-l-4 border-status-critical",
              )}
            >
              <span className="text-status-offline w-[110px] shrink-0 text-slate-500">
                [{new Date(Date.now() - (tabLogs.length - index) * 650).toLocaleTimeString()}]
              </span>
              <span className={toneText(line.tone)}>{line.message}</span>
            </div>
          ))}
        </div>

        <form onSubmit={submitCommand} className="flex h-12 shrink-0 items-center border-t border-border bg-bg-card px-4">
          <span className="mr-3 font-data-mono text-data-mono text-status-active font-bold text-lg">&gt;</span>
          <input
            value={terminalInput}
            onChange={(event) => setTerminalInput(event.target.value)}
            className="bg-transparent border-none outline-none flex-1 font-data-mono text-data-mono text-on-surface placeholder:text-slate-600 focus:ring-0"
            placeholder="Type a command: status, telemetry, mavlink, maps, mission, vision, clear"
            type="text"
          />
        </form>
      </main>

      <aside className="glass-panel panel-3d-right absolute bottom-3 right-3 top-3 z-40 flex w-[320px] flex-col overflow-y-auto border-l border-border">
        <div className="relative z-10 flex flex-col h-full">
          <div className="flex justify-center border-b border-border bg-bg-card px-4 pb-3 pt-4">
            <div className="holo-core" />
          </div>

          <div className="p-6 border-b border-border">
            <h3 className="font-label-caps text-label-caps uppercase text-secondary mb-4 flex items-center gap-2">
              <Database size={14} className="text-status-active" /> Subsystem Services
            </h3>
            <div className="flex flex-col gap-3 font-data-mono text-data-mono">
              {serviceRows.map((row) => {
                const label = serviceBadge(row.ok, row.degraded);
                const tone = row.ok && !row.degraded ? "ready" : row.degraded ? "warning" : "critical";
                return (
                  <div key={row.label} className="flex justify-between items-center group">
                    <span className="text-on-surface-variant group-hover:text-on-surface transition-colors">{row.label}</span>
                    <div className={cn(
                      "glass-panel flex items-center gap-2 border px-2 py-0.5",
                      tone === "ready" ? "border-status-ready/50" : tone === "warning" ? "border-status-warning/50" : "border-status-critical/50",
                    )}>
                      <span className={cn(
                        "h-3 w-1.5",
                        tone === "ready" ? "bg-status-ready" : tone === "warning" ? "bg-status-warning" : "bg-status-critical",
                      )} />
                      <span className={cn(
                        "font-bold text-[10px]",
                        tone === "ready" ? "text-status-ready" : tone === "warning" ? "text-status-warning" : "text-status-critical",
                      )}>
                        {label}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="p-6 flex-1">
            <h3 className="font-label-caps text-label-caps uppercase text-secondary mb-4 flex items-center gap-2">
              <Activity size={14} className="text-status-active" /> Resource Monitor
            </h3>
            <div className="flex flex-col gap-6">
              <ResourceBar label="CPU_LOAD" value={cpuLoad} tone="cyan" />
              <ResourceBar label="MEM_ALLOC" value={memoryLoad} tone={memoryLoad > 70 ? "warning" : "cyan"} />

              <div className="glass-panel relative mt-2 flex flex-col gap-2 overflow-hidden border-status-active/40 p-4">
                <span className="font-label-caps text-label-caps uppercase text-secondary">POSITION_SOURCE</span>
                <span className={cn(
                  "font-mono text-[26px] font-bold leading-none",
                  sourceTone(position) === "ready" ? "text-status-ready" : sourceTone(position) === "cyan" ? "text-status-active" : sourceTone(position) === "warning" ? "text-status-warning" : "text-status-critical",
                )}>
                  {sourceLabel(position)}
                </span>
                <div className="flex justify-between font-data-mono text-data-mono text-xs text-on-surface-variant mt-2 border-t border-border pt-3">
                  <span>PORT: {port}</span>
                  <span className="text-status-ready">{position?.confidence != null ? `CONF: ${Math.round(position.confidence * 100)}%` : "LISTENING"}</span>
                </div>
                <div className="grid grid-cols-2 gap-2 border-t border-border pt-3 font-data-mono text-[10px] text-on-surface-variant">
                  <span>VISION AGE</span>
                  <span className="text-right">{position?.seconds_since_vision_fix != null ? `${position.seconds_since_vision_fix.toFixed(1)}s` : "n/a"}</span>
                  <span>FIX DIST</span>
                  <span className="text-right">{position?.meters_since_vision_fix != null ? `${position.meters_since_vision_fix.toFixed(1)}m` : "n/a"}</span>
                  <span>ACTIVE MAP</span>
                  <span className="truncate text-right">{activeRegion?.name ?? "none"}</span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <Shortcut label="Device" icon={<Server size={13} />} onClick={() => navigate("/vehicle-manager")} />
                <Shortcut label="Vision" icon={<Cpu size={13} />} onClick={() => navigate("/camera-vision")} />
                <Shortcut label="Maps" icon={<Map size={13} />} onClick={() => navigate("/maps")} />
                <Shortcut label="Mission" icon={<Radio size={13} />} onClick={() => navigate("/mission-planner")} />
              </div>

              <div className="rounded border border-amber-500/25 bg-amber-500/5 p-3 text-xs text-amber-100/80">
                <div className="flex items-center gap-2 font-medium text-status-warning">
                  <AlertTriangle size={13} /> Hardware-first mode
                </div>
                <p className="mt-1">
                  Diagnostics are centered on the Raspberry Pi runtime, PX4/MAVLink, map readiness, and GPS-to-vision fallback.
                </p>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </div>
  );
}

function ResourceBar({ label, value, tone }: { label: string; value: number; tone: "cyan" | "warning" }) {
  return (
    <div>
      <div className="flex justify-between font-data-mono text-data-mono mb-2">
        <span className="text-on-surface-variant">{label}</span>
        <span className={tone === "warning" ? "text-status-warning font-bold" : "text-status-active font-bold"}>{value}%</span>
      </div>
      <div className="relative h-2 w-full overflow-hidden border border-border bg-slate-950 shadow-depth-inset">
        <div
          className={cn("absolute top-0 left-0 h-full transition-all duration-500", tone === "warning" ? "bg-status-warning" : "bg-status-active")}
          style={{ width: `${value}%` }}
        />
      </div>
      <div className="h-12 w-full mt-2 flex items-end gap-[2px] overflow-hidden">
        {[0.52, 0.66, 0.45, 0.72, value / 100].map((height, index) => (
          <div
            key={index}
            className={cn("w-full rounded-t-sm", index === 4 ? (tone === "warning" ? "bg-status-warning" : "bg-status-active") : "bg-border")}
            style={{ height: `${Math.round(height * 100)}%` }}
          />
        ))}
      </div>
    </div>
  );
}

function Shortcut({ label, icon, onClick }: { label: string; icon: ReactNode; onClick: () => void }) {
  return (
    <button onClick={onClick} className="glass-hover flex items-center gap-2 border border-border px-3 py-2 text-left text-xs text-slate-300">
      <span className="text-status-active">{icon}</span>
      {label}
    </button>
  );
}
