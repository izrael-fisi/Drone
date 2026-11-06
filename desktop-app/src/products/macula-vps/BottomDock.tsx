import { Activity, MessageSquare, Radio, SlidersHorizontal, Terminal, type LucideIcon } from "lucide-react";
import type { BottomDockTabId } from "../../lib/shellStore";
import { cn } from "../../lib/utils";
import type { OperatorRuntimeModel } from "../../lib/operatorRuntimeAdapter";
import { DEFAULT_PIPELINE_CONFIG } from "../../lib/pipelineConfig";

export const BOTTOM_TABS: Array<{ id: BottomDockTabId; label: string; Icon: LucideIcon; requiresDevice?: boolean }> = [
  { id: "system-status", label: "System Status", Icon: Activity },
  { id: "diagnostics", label: "Diagnostics", Icon: Activity },
  { id: "parameters", label: "Parameters", Icon: SlidersHorizontal },
  { id: "messages", label: "Messages", Icon: MessageSquare, requiresDevice: true },
  { id: "ekf-init", label: "EKF Init", Icon: Radio, requiresDevice: true },
  { id: "console", label: "Console", Icon: Terminal, requiresDevice: true },
];

export function BottomDock({
  open,
  tab,
  model,
  onOpenChange,
  onTabChange,
}: {
  open: boolean;
  tab: BottomDockTabId;
  model: OperatorRuntimeModel;
  onOpenChange: (open: boolean) => void;
  onTabChange: (tab: BottomDockTabId) => void;
}) {
  const visibleTabs = BOTTOM_TABS.filter((item) => !item.requiresDevice || model.activeDevice);
  const effectiveTab = visibleTabs.some((item) => item.id === tab) ? tab : "system-status";
  const activeTab = visibleTabs.find((item) => item.id === effectiveTab) ?? visibleTabs[0];
  const ActiveTabIcon = activeTab?.Icon;
  const rows = dockRows(effectiveTab, model);

  return (
    <section
      className={cn(
        "absolute bottom-3 left-20 z-[1200] flex items-stretch justify-start transition-[width,height] duration-200",
        open ? "w-[calc(100%-92px)]" : "w-14",
      )}
      style={{ height: open ? 260 : Math.min(360, visibleTabs.length * 44 + 8) }}
    >
      <div
        className={cn(
          "flex h-full w-14 shrink-0 flex-col items-center gap-1 border border-white/10 bg-bg-base/60 p-1 shadow-[0_18px_70px_rgba(0,0,0,0.48)] ring-1 ring-white/5 backdrop-blur-xl",
          open ? "rounded-l-lg" : "rounded-lg",
        )}
      >
        {visibleTabs.map(({ id, label, Icon }) => {
          return (
            <button
              key={id}
              type="button"
              onClick={() => {
                if (open && effectiveTab === id) {
                  onOpenChange(false);
                  return;
                }
                onTabChange(id);
                onOpenChange(true);
              }}
              className={cn(
                "flex h-10 w-10 items-center justify-center rounded-md border text-xs transition-colors",
                open && effectiveTab === id
                  ? "border-orange-500/75 bg-orange-500/18 text-orange-300"
                  : "border-transparent text-slate-500 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-300",
              )}
              title={label}
            >
              <Icon size={14} />
            </button>
          );
        })}
      </div>

      {open && (
        <div className="h-full min-w-0 flex-1 overflow-hidden rounded-r-lg border border-l-0 border-white/10 bg-black/80 shadow-[0_18px_70px_rgba(0,0,0,0.48)] ring-1 ring-white/5 backdrop-blur-2xl">
          <div className="flex h-12 items-center gap-2 border-b border-white/10 px-4">
            {ActiveTabIcon && <ActiveTabIcon size={15} className="text-orange-300" />}
            <span className="text-sm font-semibold text-slate-100">{activeTab?.label ?? "Status"}</span>
            <span className="ml-auto font-data-mono text-[10px] uppercase tracking-[0.12em] text-slate-600">
              {effectiveTab}
            </span>
          </div>
          <div className="h-[calc(100%-48px)] overflow-y-auto p-4 font-mono text-xs">
            {rows.map((row, index) => (
              <div key={`${row.label}-${index}`} className="flex gap-4 rounded-md border border-transparent px-3 py-1.5 hover:border-orange-500/35 hover:bg-white/[0.04]">
                <span className="w-[112px] shrink-0 text-slate-600">[{row.label}]</span>
                <span className={cn(row.tone === "ready" && "text-status-ready", row.tone === "warning" && "text-status-warning", row.tone === "critical" && "text-status-critical", row.tone === "active" && "text-cyan-300", row.tone === "muted" && "text-slate-500")}>
                  {row.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

export function dockRows(tab: BottomDockTabId, model: OperatorRuntimeModel) {
  const endpoint = model.activeDevice?.mavlink_endpoint ?? "not configured";
  const mapName = model.activeMap?.areaName ?? "none selected";
  if (tab === "system-status") {
    return [
      { label: "EDGE_API", value: model.edgeConnectionState === "online" ? "live" : "unavailable", tone: model.edgeConnectionState === "online" ? "ready" : "warning" },
      { label: "DEVICE", value: model.activeDevice?.name ?? "no active device", tone: model.activeDevice ? "ready" : "warning" },
      { label: "RUNTIME", value: model.activeDevice?.remote_project_path ?? "runtime path not configured", tone: model.activeDevice?.remote_project_path ? "ready" : "warning" },
      { label: "CAMERA", value: model.activeDevice?.camera_profile ?? "unavailable", tone: model.activeDevice?.camera_profile ? "ready" : "muted" },
      { label: "MAP", value: mapName, tone: model.activeMap ? "active" : "warning" },
    ];
  }
  if (tab === "parameters") {
    return [
      { label: "DEVICE", value: model.activeDevice?.name ?? "none", tone: model.activeDevice ? "ready" : "warning" },
      { label: "MAV_ENDPOINT", value: endpoint, tone: model.activeDevice?.mavlink_endpoint ? "ready" : "warning" },
      { label: "ACTIVE_MAP", value: mapName, tone: model.activeMap ? "active" : "warning" },
      { label: "EDGE_STATE", value: model.edgeConnectionState, tone: model.edgeConnectionState === "online" ? "ready" : "warning" },
      { label: "PIPELINE", value: `${DEFAULT_PIPELINE_CONFIG.pipeline} (shadow target)`, tone: "muted" },
    ];
  }
  if (tab === "messages") {
    return [
      { label: "MESSAGE", value: "No live MAVLink message stream is attached in browser fallback mode.", tone: "warning" },
      { label: "ROUTE", value: "Connect the Pi runtime, then use MAVLink source control.", tone: "muted" },
    ];
  }
  if (tab === "ekf-init") {
    return [
      { label: "EKF_INIT", value: model.activeDevice ? "waiting for MAVLink EKF evidence" : "disabled until device is connected", tone: model.activeDevice ? "warning" : "muted" },
      { label: "GPS", value: "no live sample", tone: "muted" },
      { label: "VISION", value: "no live sample", tone: "muted" },
    ];
  }
  if (tab === "console") {
    return [
      { label: "CONSOLE", value: "Console commands are available from connected system tools only.", tone: "warning" },
      { label: "SAFETY", value: "No PX4 parameters are auto-changed from this shell.", tone: "ready" },
    ];
  }
  return [
    { label: "EDGE", value: model.edgeConnectionState, tone: model.edgeConnectionState === "online" ? "ready" : "warning" },
    { label: "MAP_CACHE", value: `${model.localMaps.length} local / ${model.edgeMaps.length} edge`, tone: model.localMaps.length || model.edgeMaps.length ? "ready" : "warning" },
    { label: "ACTIVE_MAP", value: mapName, tone: model.activeMap ? "active" : "warning" },
    { label: "RUNTIME", value: model.activeDevice?.remote_project_path ?? "runtime path not configured", tone: model.activeDevice?.remote_project_path ? "ready" : "warning" },
  ];
}
