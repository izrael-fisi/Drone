import { useEffect, useState } from "react";
import { CheckIcon as Check, CaretDownIcon as ChevronDown, DownloadSimpleIcon as Download, StackIcon as Layers, MapTrifoldIcon as MapIcon, PencilSimpleIcon as Pencil, PathIcon as Route, FloppyDiskIcon as Save, ScissorsIcon as Scissors, ArrowUUpLeftIcon as Undo2, XIcon as X } from "@phosphor-icons/react";
import type { MapProvider, MapProviderId, MapUsageEstimate, Region, SavedMission } from "../../../lib/types";
import { cn, formatMegabytes } from "../../../lib/utils";
import { MAP_MAX_ZOOM } from "../../../platform/map/geo";
import { isDownloadProviderReady, ledClass, providerNeedsMissingKey, providerShortLabel, toneClass, type CutInstallProgress, type CutShape, type MapSelectionValue, type MapToolMode } from "./helpers";
/** Macula VPS map controls: map picker, map-cut and mission tools, estimate card, HUD rows. */

export function MapRightControls({
  regions,
  activeMap,
  selectedMap,
  onMapChange,
  onOpenMapsPanel,
  mode,
  cutShape,
  borderPointCount,
  waypointCount,
  drawingMessage,
  onModeChange,
  onSaveBorder,
  onSaveMission,
  onClearDrawing,
  canSaveMission,
  missionDraftActive,
  editingMissionName,
  onStartMissionDraft,
  onEditMission,
  onUndoWaypoint,
  cutEstimate,
  cutEstimateLoading,
  cutEstimateError,
  cutEstimateZoom,
  onCutEstimateZoomChange,
  cutInstalling,
  cutInstallProgress,
  multiLayerMap,
  onMultiLayerMapChange,
  providers,
  selectedProviderIds,
  apiKeys,
  onToggleProvider,
  fallbackCutAreaKm2,
  cutPreviewUrl,
  mapLimitWarnings,
  mapLimitOverride,
  onMapLimitOverrideChange,
  cutSaveBlocked,
  missions,
  missionMode,
  missionModeDisabled,
  missionCreateRequired,
  activeMapName,
  selectedMissionIds,
  onMissionModeToggle,
  onToggleMissionSelection,
  onSelectAllMissions,
  onClearMissionSelection,
}: {
  regions: Region[];
  activeMap?: Region;
  selectedMap: MapSelectionValue;
  onMapChange: (id: MapSelectionValue) => void;
  onOpenMapsPanel: () => void;
  mode: MapToolMode;
  cutShape: CutShape;
  borderPointCount: number;
  waypointCount: number;
  drawingMessage: string | null;
  onModeChange: (mode: MapToolMode) => void;
  onSaveBorder: () => void;
  onSaveMission: () => void;
  onClearDrawing: () => void;
  canSaveMission: boolean;
  missionDraftActive: boolean;
  editingMissionName?: string;
  onStartMissionDraft: () => void;
  onEditMission: (missionId: string) => void;
  onUndoWaypoint: () => void;
  cutEstimate: MapUsageEstimate | null;
  cutEstimateLoading: boolean;
  cutEstimateError: string | null;
  cutEstimateZoom: number;
  onCutEstimateZoomChange: (zoom: number) => void;
  cutInstalling: boolean;
  cutInstallProgress: CutInstallProgress | null;
  multiLayerMap: boolean;
  onMultiLayerMapChange: (enabled: boolean) => void;
  providers: MapProvider[];
  selectedProviderIds: MapProviderId[];
  apiKeys: Record<string, string>;
  onToggleProvider: (providerId: MapProviderId) => void;
  fallbackCutAreaKm2: number;
  cutPreviewUrl: string | null;
  mapLimitWarnings: string[];
  mapLimitOverride: boolean;
  onMapLimitOverrideChange: (enabled: boolean) => void;
  cutSaveBlocked: boolean;
  missions: SavedMission[];
  missionMode: boolean;
  missionModeDisabled: boolean;
  missionCreateRequired: boolean;
  activeMapName?: string;
  selectedMissionIds: string[];
  onMissionModeToggle: () => void;
  onToggleMissionSelection: (missionId: string) => void;
  onSelectAllMissions: () => void;
  onClearMissionSelection: () => void;
}) {
  const [mapMenuOpen, setMapMenuOpen] = useState(false);
  const [missionMenuOpen, setMissionMenuOpen] = useState(false);
  const [missionCreateOpen, setMissionCreateOpen] = useState(false);
  const [missionQuery, setMissionQuery] = useState("");
  const selectedLabel = selectedMap === "world" ? "World Map" : activeMap?.name ?? "Uploaded map missing";
  const drawingActive = mode !== "idle";
  const cutDrawingActive = mode === "box" || mode === "polygon";
  const selectedCount = selectedMissionIds.length;
  const missionButtonLabel = missionMode
    ? "Mission Mode"
    : missionModeDisabled
    ? missionCreateRequired
      ? "Mission Required"
      : "Select Map First"
    : "Mission Mode";
  const missionSelectorDisabled = missionModeDisabled || missionCreateRequired;
  const filteredMissions = missions.filter((mission) => {
    const normalized = missionQuery.trim().toLowerCase();
    return !normalized || `${mission.name} ${mission.map_label ?? ""}`.toLowerCase().includes(normalized);
  });

  useEffect(() => {
    if (drawingActive) setMissionMenuOpen(false);
  }, [drawingActive]);

  useEffect(() => {
    if (missionCreateRequired || missionDraftActive) setMissionCreateOpen(true);
  }, [missionCreateRequired, missionDraftActive]);

  return (
    <section className="absolute right-4 top-4 z-[1150] flex max-h-[calc(100%-2rem)] w-[264px] flex-col gap-2 overflow-y-auto pr-1 [scrollbar-width:thin] [scrollbar-color:rgba(255,102,0,0.45)_rgba(255,255,255,0.08)]">
      <div className="rounded-lg border border-white/10 bg-[#07090b]/90 p-2 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl">
        <div className="mb-2 flex items-center gap-2 px-1">
          <Layers size={14} className="text-cyan-400" />
          <span className="text-[11px] font-semibold text-slate-500">
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
            <span className="text-[11px] text-slate-600">
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
              <div className="border-t border-white/10 px-3 py-1.5 text-[11px] font-semibold text-slate-600">
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
            label="Box Cut"
            active={mode === "box"}
            disabled={missionMode}
            onClick={() => {
              if (missionMode) return;
              setMapMenuOpen(false);
              onModeChange(mode === "box" ? "idle" : "box");
            }}
          />
          <ToolButton
            Icon={MapIcon}
            label="Polygon"
            active={mode === "polygon"}
            disabled={missionMode}
            onClick={() => {
              if (missionMode) return;
              setMapMenuOpen(false);
              onModeChange(mode === "polygon" ? "idle" : "polygon");
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
              <span>
                {mode === "box"
                  ? "Drag map to size box cut"
                  : mode === "polygon"
                  ? "Click boundary points in any order"
                  : "Click map to plot path"}
              </span>
              <span className="font-data-mono text-orange-300">
                {cutDrawingActive ? borderPointCount : waypointCount} pts
              </span>
            </div>
            {drawingMessage && (
              <div className="mt-2 rounded border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[11px] text-orange-200">
                {drawingMessage}
              </div>
            )}
            <div className="mt-2 flex gap-2">
              {cutDrawingActive && (
                <button
                  type="button"
                  onClick={onSaveBorder}
                  disabled={(cutShape === "box" ? borderPointCount !== 4 : borderPointCount < 3) || cutSaveBlocked || cutInstalling}
                  className="flex h-7 flex-1 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.035] text-[11px] text-slate-300 transition-colors hover:border-orange-500/55 hover:text-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Check size={12} />
                  {cutInstalling ? "Installing" : "Save Cut"}
                </button>
              )}
              <button
                type="button"
                onClick={onClearDrawing}
                disabled={cutInstalling}
                className="flex h-7 flex-1 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.035] text-[11px] text-slate-400 transition-colors hover:border-red-500/45 hover:text-red-200 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <X size={12} />
                Clear
              </button>
            </div>
            {cutInstallProgress && (
              <CutInstallProgressMeter progress={cutInstallProgress} />
            )}
            {cutDrawingActive && (
              <MapCutEstimateCard
                estimate={cutEstimate}
                loading={cutEstimateLoading}
                error={cutEstimateError}
                cutShape={cutShape}
                fallbackAreaKm2={fallbackCutAreaKm2}
                zoom={cutEstimateZoom}
                onZoomChange={onCutEstimateZoomChange}
                multiLayerMap={multiLayerMap}
                onMultiLayerMapChange={onMultiLayerMapChange}
                providers={providers}
                selectedProviderIds={selectedProviderIds}
                apiKeys={apiKeys}
                onToggleProvider={onToggleProvider}
                previewUrl={cutPreviewUrl}
                pointCount={borderPointCount}
                mapLimitWarnings={mapLimitWarnings}
                mapLimitOverride={mapLimitOverride}
                onMapLimitOverrideChange={onMapLimitOverrideChange}
              />
            )}
          </div>
        )}
        {!drawingActive && cutInstallProgress && (
          <CutInstallProgressMeter progress={cutInstallProgress} />
        )}
      </div>

      <div className="rounded-lg border border-white/10 bg-[#07090b]/90 p-2 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl">
        <button
          type="button"
          onClick={() => {
            if (missionModeDisabled) return;
            setMissionMenuOpen(false);
            onMissionModeToggle();
          }}
          disabled={missionModeDisabled}
          className={cn(
            "flex h-9 w-full items-center justify-center rounded-md border px-3 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-45",
            missionMode
              ? "border-orange-500 bg-orange-500 text-black shadow-[0_10px_32px_rgba(255,102,0,0.24)]"
              : missionCreateRequired
              ? "border-orange-500/70 bg-orange-500/24 text-orange-100 hover:bg-orange-500/34"
              : "border-orange-500/70 bg-orange-500/18 text-orange-100 hover:bg-orange-500/28",
          )}
        >
          {missionButtonLabel}
        </button>
        {!missionMode && (!missionModeDisabled || missionCreateRequired) ? (
          <div className="mt-2 rounded-md border border-white/10 bg-black/45">
            <button
              type="button"
              onClick={() => setMissionCreateOpen((current) => !current)}
              disabled={!activeMapName}
              className="flex h-8 w-full items-center justify-between gap-2 px-2 text-left text-xs text-slate-200 transition-colors hover:text-orange-100 disabled:cursor-not-allowed disabled:opacity-45"
            >
              <span className="flex min-w-0 items-center gap-2">
                <Route size={13} className="text-orange-300" />
                <span className="truncate">{editingMissionName ? "Edit Mission" : "Create Mission"}</span>
              </span>
              <ChevronDown size={13} className={cn("text-slate-500 transition-transform", missionCreateOpen && "rotate-180")} />
            </button>
            {missionCreateOpen && (
              <div className="space-y-2 border-t border-white/10 p-2">
                {editingMissionName && (
                  <div className="rounded border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[11px] text-orange-100">
                    Editing {editingMissionName}. Select another saved map before saving to move it.
                  </div>
                )}
                <ToolButton
                  Icon={Route}
                  label="Waypoint"
                  active={mode === "waypoint"}
                  disabled={missionMode || !activeMapName}
                  onClick={() => {
                    setMissionMenuOpen(false);
                    onStartMissionDraft();
                  }}
                />
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={onUndoWaypoint}
                    disabled={waypointCount === 0 || missionMode}
                    className="flex h-7 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.035] text-[11px] text-slate-400 transition-colors hover:border-orange-500/45 hover:text-orange-200 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Undo2 size={12} />
                    Undo
                  </button>
                  <button
                    type="button"
                    onClick={onSaveMission}
                    disabled={!canSaveMission || missionMode}
                    className="flex h-7 items-center justify-center gap-1 rounded-md border border-orange-500/45 bg-orange-500/18 text-[11px] text-orange-100 transition-colors hover:bg-orange-500/26 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.035] disabled:text-slate-600"
                  >
                    <Save size={12} />
                    {editingMissionName ? "Update" : "Save"}
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : null}
        <div className="relative mt-2">
          <button
            type="button"
            onClick={() => {
              if (missionSelectorDisabled) return;
              setMissionMenuOpen((current) => !current);
            }}
            disabled={missionSelectorDisabled}
            className="flex h-9 w-full items-center gap-2 rounded-md border border-white/10 bg-black/55 px-2 text-left text-xs text-slate-200 outline-none transition-colors hover:border-orange-500/55 disabled:cursor-not-allowed disabled:opacity-45"
          >
            <Route size={13} className="text-orange-300" />
            <span className="min-w-0 flex-1 truncate">
              {missionModeDisabled
                ? "Select saved map"
                : missionCreateRequired
                ? "No mission saved"
                : selectedCount
                ? `${selectedCount} mission${selectedCount === 1 ? "" : "s"} selected`
                : "Select missions"}
            </span>
            <span className="text-[11px] text-slate-600">
              Filter
            </span>
          </button>
          {missionMenuOpen && (
            <div className="mt-2 w-full overflow-hidden rounded-lg border border-white/10 bg-[#050607]/95 shadow-2xl ring-1 ring-black/70">
              <div className="border-b border-white/10 p-2">
                <input
                  value={missionQuery}
                  onChange={(event) => setMissionQuery(event.target.value)}
                  className="h-8 w-full rounded-md border border-white/10 bg-black/75 px-2 text-xs text-slate-200 outline-none placeholder:text-slate-500 focus:border-orange-500/55"
                  placeholder="Filter missions..."
                />
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={onSelectAllMissions}
                    className="h-7 rounded-md border border-white/10 bg-black/55 text-[11px] text-slate-300 transition-colors hover:border-orange-500/45 hover:text-orange-200"
                  >
                    All
                  </button>
                  <button
                    type="button"
                    onClick={onClearMissionSelection}
                    className="h-7 rounded-md border border-white/10 bg-black/55 text-[11px] text-slate-400 transition-colors hover:border-red-500/45 hover:text-red-200"
                  >
                    Clear
                  </button>
                </div>
              </div>
              <div className="max-h-52 overflow-y-auto py-1">
                {filteredMissions.length === 0 ? (
                  <div className="px-3 py-3 text-xs text-slate-600">No saved missions</div>
                ) : (
                  filteredMissions.map((mission) => {
                    const selected = selectedMissionIds.includes(mission.id);
                    return (
                      <div
                        key={mission.id}
                        className={cn(
                          "flex w-full items-center gap-1 px-2 py-1.5 text-left text-xs transition-colors hover:bg-white/[0.06]",
                          selected ? "text-orange-200" : "text-slate-300",
                        )}
                      >
                        <button
                          type="button"
                          onClick={() => onToggleMissionSelection(mission.id)}
                          className="flex min-w-0 flex-1 items-center gap-2 rounded px-1 py-1 text-left"
                        >
                          <span className={cn("h-3 w-3 rounded-sm border", selected ? "border-orange-500 bg-orange-500" : "border-white/20 bg-white/[0.03]")} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate">{mission.name}</span>
                            <span className="block truncate text-[11px] text-slate-600">{mission.map_label ?? "World Map"}</span>
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setMissionMenuOpen(false);
                            onEditMission(mission.id);
                          }}
                          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.035] text-slate-500 transition-colors hover:border-orange-500/45 hover:text-orange-200"
                          title="Edit mission"
                        >
                          <Pencil size={12} />
                        </button>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          )}
        </div>
        {missionMode && (
          <div className="mt-2 rounded-md border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[11px] text-orange-100">
            Live mission overlays active for {activeMapName ?? "selected map"}
          </div>
        )}
        {!missionMode && missionModeDisabled && (
          <div className="mt-2 rounded-md border border-white/10 bg-black/45 px-2 py-1.5 text-[11px] text-slate-500">
            Select or install a saved map before Mission Mode.
          </div>
        )}
        {!missionMode && !missionModeDisabled && missionCreateRequired && (
          <div className="mt-2 rounded-md border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[11px] text-orange-100">
            No mission saved for {activeMapName ?? "this map"}. Use Create Mission to plot waypoints, then Save Mission.
          </div>
        )}
      </div>

    </section>
  );
}


function CutInstallProgressMeter({ progress }: { progress: CutInstallProgress }) {
  const percent = Math.max(0, Math.min(100, progress.percent));
  const toneClass = progress.tone === "success"
    ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-200"
    : progress.tone === "error"
    ? "border-red-500/25 bg-red-500/10 text-red-200"
    : "border-orange-500/25 bg-orange-500/10 text-orange-100";
  const barClass = progress.tone === "success" ? "bg-emerald-400" : progress.tone === "error" ? "bg-red-400" : "bg-orange-500";

  return (
    <div className={cn("mt-2 rounded-md border px-2 py-2", toneClass)}>
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="truncate">{progress.label}</span>
        <span className="shrink-0 font-data-mono">
          {progress.current != null && progress.total != null
            ? `${progress.current.toLocaleString()} / ${progress.total.toLocaleString()}`
            : `${Math.round(percent)}%`}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-black/45">
        <div
          className={cn("h-full rounded-full transition-all duration-200", barClass)}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

function MapCutEstimateCard({
  estimate,
  loading,
  error,
  cutShape,
  fallbackAreaKm2,
  zoom,
  onZoomChange,
  multiLayerMap,
  onMultiLayerMapChange,
  providers,
  selectedProviderIds,
  apiKeys,
  onToggleProvider,
  previewUrl,
  pointCount,
  mapLimitWarnings,
  mapLimitOverride,
  onMapLimitOverrideChange,
}: {
  estimate: MapUsageEstimate | null;
  loading: boolean;
  error: string | null;
  cutShape: CutShape;
  fallbackAreaKm2: number;
  zoom: number;
  onZoomChange: (zoom: number) => void;
  multiLayerMap: boolean;
  onMultiLayerMapChange: (enabled: boolean) => void;
  providers: MapProvider[];
  selectedProviderIds: MapProviderId[];
  apiKeys: Record<string, string>;
  onToggleProvider: (providerId: MapProviderId) => void;
  previewUrl: string | null;
  pointCount: number;
  mapLimitWarnings: string[];
  mapLimitOverride: boolean;
  onMapLimitOverrideChange: (enabled: boolean) => void;
}) {
  const providerMax = Math.max(...(estimate?.provider_breakdown.map((item) => item.estimated_source_mb) ?? [1]), 1);
  const ready = cutShape === "box" ? pointCount === 4 : pointCount >= 3;
  const resolutionLabel = estimate ? `${estimate.gsd_m_per_px.toFixed(2)} m/px` : "n/a";
  const zoomLabel = multiLayerMap ? `Z15-Z${zoom}` : `Z${zoom}`;
  const [resolutionOpen, setResolutionOpen] = useState(true);
  const [providersOpen, setProvidersOpen] = useState(true);
  const [estimateOpen, setEstimateOpen] = useState(false);
  return (
    <div className="mt-2 rounded-md border border-white/10 bg-white/[0.035] p-2">
      <button
        type="button"
        onClick={() => setResolutionOpen((current) => !current)}
        className="mb-2 flex w-full items-center justify-between gap-2 rounded border border-white/10 bg-black/25 px-2 py-1.5 text-left transition-colors hover:border-orange-500/35"
      >
        <span className="text-[11px] font-semibold text-slate-500">
          Download Resolution
        </span>
        <span className="flex items-center gap-1.5">
          <span className="rounded border border-orange-500/30 bg-orange-500/10 px-1.5 py-0.5 text-[11px] capitalize text-orange-200">
            {zoomLabel}
          </span>
          <ChevronDown size={13} className={cn("text-slate-500 transition-transform", resolutionOpen && "rotate-180")} />
        </span>
      </button>
      {resolutionOpen && (
        <div className="mb-2 rounded border border-white/10 bg-black/30 px-2 py-2">
          <label className="flex items-center justify-between text-[11px] text-slate-500">
            <span>Tile zoom</span>
            <span className="font-data-mono text-orange-200">Z{zoom}</span>
          </label>
          <input
            type="range"
            min={15}
            max={MAP_MAX_ZOOM}
            step={1}
            value={zoom}
            onChange={(event) => onZoomChange(Number(event.target.value))}
            className="mt-2 w-full accent-orange-500"
          />
          <div className="mt-1 flex justify-between text-[11px] text-slate-600">
            <span>faster</span>
            <span>higher detail</span>
          </div>
        </div>
      )}
      <div className="mb-2 rounded border border-white/10 bg-black/30">
        <button
          type="button"
          onClick={() => setProvidersOpen((current) => !current)}
          className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left transition-colors hover:bg-white/[0.035]"
        >
          <span className="text-[11px] font-semibold text-slate-500">
            Map Providers
          </span>
          <span className="flex items-center gap-1.5">
            <span className="font-data-mono text-[11px] text-slate-600">
              {selectedProviderIds.length || 0} selected
            </span>
            <ChevronDown size={13} className={cn("text-slate-500 transition-transform", providersOpen && "rotate-180")} />
          </span>
        </button>
        {providersOpen && (
          <div className="space-y-1 border-t border-white/10 px-2 py-2">
            {providers.length ? providers.map((provider) => {
              const selected = selectedProviderIds.includes(provider.id);
              const keyMissing = providerNeedsMissingKey(provider, apiKeys);
              const readyProvider = isDownloadProviderReady(provider, apiKeys);
              return (
                <label
                  key={provider.id}
                  className={cn(
                    "flex items-center gap-2 rounded border px-2 py-1.5 text-[11px]",
                    selected
                      ? "border-orange-500/35 bg-orange-500/10 text-orange-100"
                      : readyProvider
                      ? "border-white/10 bg-white/[0.025] text-slate-300"
                      : "border-white/5 bg-white/[0.015] text-slate-600",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={!readyProvider}
                    onChange={() => onToggleProvider(provider.id)}
                    className="accent-orange-500 disabled:opacity-40"
                  />
                  <span className="min-w-0 flex-1 truncate">{provider.label}</span>
                  <span className={cn("shrink-0 font-data-mono", keyMissing ? "text-amber-400" : "text-slate-500")}>
                    {keyMissing ? "key" : `Z${provider.max_native_zoom}`}
                  </span>
                </label>
              );
            }) : (
              <div className="rounded border border-white/10 bg-white/[0.025] px-2 py-1.5 text-[11px] text-slate-600">
                No download providers available.
              </div>
            )}
            {providers.length > 0 && selectedProviderIds.length === 0 && (
              <div className="rounded border border-orange-500/25 bg-orange-500/10 px-2 py-1.5 text-[11px] text-orange-200">
                Select at least one ready provider to calculate the download estimate.
              </div>
            )}
          </div>
        )}
      </div>
      {!ready ? (
        <div className="rounded border border-white/10 bg-black/35 px-2 py-2 text-[11px] text-slate-500">
          {cutShape === "box" ? "Drag a box to calculate size." : "Place at least 3 boundary points to calculate size."}
        </div>
      ) : error ? (
        <div className="rounded border border-red-500/25 bg-red-500/10 px-2 py-2 text-[11px] text-red-200">
          Estimate unavailable: {error}
        </div>
      ) : (
        <>
          <button
            type="button"
            onClick={() => setEstimateOpen((current) => !current)}
            className="mb-2 flex w-full items-center justify-between rounded border border-white/10 bg-black/25 px-2 py-1.5 text-left transition-colors hover:border-orange-500/35"
          >
            <span className="text-[11px] font-semibold text-slate-500">
              Download Estimate
            </span>
            <ChevronDown size={13} className={cn("text-slate-500 transition-transform", estimateOpen && "rotate-180")} />
          </button>
          {estimateOpen && (
            <>
          <div className="mb-2 overflow-hidden rounded-md border border-white/10 bg-black/35">
            {previewUrl ? (
              <div className="relative aspect-video">
                <img
                  src={previewUrl}
                  alt={`Satellite resolution preview at zoom ${zoom}`}
                  className="h-full w-full object-cover"
                  draggable={false}
                />
                <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.12)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.12)_1px,transparent_1px)] bg-[length:32px_32px]" />
                <div className="absolute bottom-1 left-1 rounded bg-black/70 px-1.5 py-0.5 font-data-mono text-[11px] text-slate-200">
                  Preview {resolutionLabel}
                </div>
              </div>
            ) : (
              <div className="flex aspect-video items-center justify-center px-3 text-center text-[11px] text-slate-600">
                Draw a cut to preview selected tile resolution.
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <EstimateMetric label="Area" value={`${(estimate?.area_km2 ?? fallbackAreaKm2).toFixed(2)} km2`} active />
            <EstimateMetric label="Download" value={estimate ? formatMegabytes(estimate.estimated_source_mb) : loading ? "..." : "n/a"} active />
            <EstimateMetric label="Tiles" value={estimate ? `${estimate.tile_count.toLocaleString()} @ ${zoomLabel}` : zoomLabel} />
            <EstimateMetric label="Resolution" value={resolutionLabel} />
          </div>
          <div className="mt-2 rounded border border-white/10 bg-black/30 px-2 py-2">
            <div className="mb-1 flex items-center justify-between text-[11px]">
              <span className="text-slate-500">Disk size</span>
              <span className="font-data-mono text-slate-300">{estimate ? formatMegabytes(estimate.estimated_disk_mb) : loading ? "calculating" : "n/a"}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-orange-500 transition-all"
                style={{ width: `${Math.max(8, Math.min(100, ((estimate?.estimated_disk_mb ?? 0) / Math.max(estimate?.estimated_disk_mb ?? 1, 1)) * 100))}%` }}
              />
            </div>
          </div>
          <label className="mt-2 flex items-start gap-2 rounded border border-white/10 bg-black/30 px-2 py-2 text-[11px] text-slate-300">
            <input
              type="checkbox"
              checked={multiLayerMap}
              onChange={(event) => onMultiLayerMapChange(event.target.checked)}
              className="mt-0.5 accent-orange-500"
            />
            <span>
              <span className="block font-medium text-slate-200">Multi-Layer Map</span>
              <span className="block text-slate-500">
                Include every zoom level from Z15 through Z{zoom}; estimate updates with the extra tile layers.
              </span>
            </span>
          </label>
          {mapLimitWarnings.length > 0 && (
            <label className="mt-2 flex items-start gap-2 rounded border border-orange-500/25 bg-orange-500/10 px-2 py-2 text-[11px] text-orange-100">
              <input
                type="checkbox"
                checked={mapLimitOverride}
                onChange={(event) => onMapLimitOverrideChange(event.target.checked)}
                className="mt-0.5 accent-orange-500"
              />
              <span>
                <span className="block font-medium">Override map download warning</span>
                {mapLimitWarnings.map((warning) => (
                  <span key={warning} className="mt-1 block text-orange-200/85">{warning}</span>
                ))}
              </span>
            </label>
          )}
          <div className="mt-2 space-y-1">
            {(estimate?.provider_breakdown.length ? estimate.provider_breakdown : []).map((provider) => (
              <div key={provider.provider_id} className="grid grid-cols-[54px_1fr_52px] items-center gap-2 text-[11px]">
                <span className="truncate text-slate-500">{providerShortLabel(provider.provider_id)}</span>
                <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                  <div
                    className={cn("h-full rounded-full", provider.key_required ? "bg-amber-400" : provider.overzoomed ? "bg-slate-500" : "bg-orange-500")}
                    style={{ width: `${Math.max(6, Math.min(100, (provider.estimated_source_mb / providerMax) * 100))}%` }}
                  />
                </div>
                <span className="text-right font-data-mono text-slate-400">
                  {provider.key_required ? "key" : provider.overzoomed ? "over" : formatMegabytes(provider.estimated_source_mb)}
                </span>
              </div>
            ))}
            {loading && !estimate && (
              <div className="rounded border border-white/10 bg-black/30 px-2 py-1.5 text-[11px] text-slate-500">
                Calculating provider coverage and tile footprint...
              </div>
            )}
          </div>
          </>
          )}
        </>
      )}
    </div>
  );
}

function EstimateMetric({ label, value, active = false }: { label: string; value: string; active?: boolean }) {
  return (
    <div className="rounded border border-white/10 bg-black/30 px-2 py-1.5">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={cn("mt-0.5 truncate font-data-mono text-[11px]", active ? "text-orange-200" : "text-slate-300")}>{value}</div>
    </div>
  );
}

function ToolButton({
  Icon,
  label,
  active,
  onClick,
  disabled = false,
}: {
  Icon: typeof Scissors;
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex h-8 items-center justify-center gap-1 rounded-md border px-2 text-[11px] transition-colors",
        active
          ? "border-orange-500/75 bg-orange-500/18 text-orange-200"
          : "border-white/10 bg-white/[0.035] text-slate-400 hover:border-orange-500/55 hover:text-slate-100",
        disabled && "cursor-not-allowed opacity-45 hover:border-white/10 hover:text-slate-400",
      )}
    >
      <Icon size={12} />
      <span className="truncate">{label}</span>
    </button>
  );
}

export function HudRow({
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
    <div className="rounded-lg bg-white/[0.035]/80 px-3 py-2">
      <div className="flex items-center gap-2">
        <span className={cn("ops-led", ledClass(tone))} />
        <Icon size={13} className={toneClass(tone)} />
        <span className="text-[11px] font-semibold text-slate-500">{label}</span>
      </div>
      <div className={cn("mt-1 truncate font-data-mono text-sm font-semibold", toneClass(tone))}>{value}</div>
      <div className="truncate font-data-mono text-[11px] text-slate-500">{detail}</div>
    </div>
  );
}

