import { WarningIcon as AlertTriangle, ArrowsLeftRightIcon as ArrowLeftRight, DownloadSimpleIcon as Download, CircleNotchIcon as Loader2, PencilSimpleIcon as Pencil, MagnifyingGlassIcon as Search, TrashIcon as Trash2 } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { useAppStore } from "../../../lib/store";
import { cmd } from "../../../lib/tauri";
import type { Region } from "../../../lib/types";
import { cn } from "../../../lib/utils";
import type { OperatorRuntimeModel } from "../../../lib/operatorRuntimeAdapter";
import { proxigo } from "../../../lib/proxigo";
import { EmptyLine, MapRow, PanelAction, PanelStack, SectionLabel, StateCard } from "../../../platform/ui";

export type MapScope = "all" | "local" | "organization";



function MapLibraryCountButton({
  label,
  value,
  active,
  onClick,
}: {
  label: string;
  value: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-lg border p-3 text-left transition-colors",
        active
          ? "border-orange-500/65 bg-orange-500/14 text-orange-100"
          : "border-white/10 bg-bg-card text-slate-300 hover:border-orange-500/35 hover:bg-white/[0.045]",
      )}
    >
      <span className="block text-[11px] font-semibold text-slate-500">{label}</span>
      <span className="mt-1 block text-xl font-semibold tabular-nums text-slate-100">{value}</span>
      <span className="mt-0.5 block truncate text-[11px] text-slate-600">click to browse</span>
    </button>
  );
}

export function MapsPanel({ model, regions, onDownloadOrgMap }: { model: OperatorRuntimeModel; regions: Region[]; onDownloadOrgMap: () => void }) {
  const { updateRegion, removeRegion, orgMapRegions, setOrgMapRegions, addOrgMapRegion, removeOrgMapRegion, proxigoSession, cloudAccount, setPendingOrgMapDownload } = useAppStore();
  const [openLibrary, setOpenLibrary] = useState<MapScope | null>(null);
  const [query, setQuery] = useState("");
  const [_filter, setFilter] = useState<MapScope>("all");
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [transferring, setTransferring] = useState<string | null>(null);
  const localMaps = regions;
  const organizationMaps = orgMapRegions;
  void transferring; // used inside JSX closures

  // Sync org maps from server when logged in to an org
  useEffect(() => {
    if (!proxigoSession || !cloudAccount?.org) {
      setOrgMapRegions([]);
      return;
    }
    proxigo.getOrgMaps(proxigoSession).then(setOrgMapRegions).catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proxigoSession, cloudAccount?.org?.org_id]);

  const openScope = (scope: MapScope) => {
    setOpenLibrary((current) => (current === scope ? null : scope));
    setFilter(scope);
    setQuery("");
    setPendingDeleteId(null);
  };

  const persistRegions = async (nextRegions: Region[]) => {
    try {
      await cmd.saveRegions(nextRegions);
    } catch (error) {
      console.warn("Failed to persist map library update", error);
    }
  };

  const renameMap = async (region: Region) => {
    const nextName = window.prompt("Rename map", region.name)?.trim();
    if (!nextName || nextName === region.name) return;
    const updated = { ...region, name: nextName };
    updateRegion(updated);
    await persistRegions(regions.map((item) => (item.id === region.id ? updated : item)));
  };

  const confirmDelete = async (region: Region) => {
    removeRegion(region.id);
    setPendingDeleteId(null);
    await persistRegions(regions.filter((item) => item.id !== region.id));
  };

  const publishToOrg = async (region: Region) => {
    if (!proxigoSession) return;
    setTransferring(region.id);
    try {
      const orgMap = await proxigo.publishOrgMap(proxigoSession, {
        name: region.name,
        lat_min: region.lat_min,
        lat_max: region.lat_max,
        lon_min: region.lon_min,
        lon_max: region.lon_max,
        zoom: region.zoom,
        source: typeof region.source === "string" ? region.source : undefined,
        location_label: region.location_label,
      });
      addOrgMapRegion(orgMap);
    } catch (err) {
      console.warn("[org maps] publish failed:", err);
    } finally {
      setTransferring(null);
    }
  };

  const removeFromOrg = async (orgMap: import("../../../lib/proxigo").OrgMapRegion) => {
    if (!proxigoSession) return;
    setTransferring(orgMap.id);
    try {
      await proxigo.removeOrgMap(proxigoSession, orgMap.id);
      removeOrgMapRegion(orgMap.id);
      setPendingDeleteId(null);
    } catch (err) {
      console.warn("[org maps] remove failed:", err);
    } finally {
      setTransferring(null);
    }
  };

  return (
    <PanelStack title="Maps" subtitle={model.activeMap?.areaName ?? "No map selected"}>
      <div className="grid grid-cols-2 gap-2">
        <MapLibraryCountButton
          label="Local Maps"
          value={localMaps.length}
          active={openLibrary === "local"}
          onClick={() => openScope("local")}
        />
        <MapLibraryCountButton
          label="Organization Maps"
          value={organizationMaps.length}
          active={openLibrary === "organization"}
          onClick={() => openScope("organization")}
        />
      </div>
      {openLibrary && (
        <div className="rounded-lg border border-white/10 bg-black/55 p-2 space-y-2">
          <div className="flex items-center gap-2 border-b border-white/10 pb-2">
            <Search size={14} className="text-slate-500" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="h-8 min-w-0 flex-1 bg-transparent text-xs text-slate-200 outline-none placeholder:text-slate-600"
              placeholder="Search maps..."
            />
          </div>

          {/* Local maps */}
          {(openLibrary === "local" || openLibrary === "organization" ? false : true) || openLibrary === "local" ? (
            <div>
              <div className="px-1 pb-1 text-[11px] font-semibold text-slate-600">Local</div>
              <div className="max-h-48 overflow-y-auto">
                {localMaps.filter(r => !query || r.name.toLowerCase().includes(query.toLowerCase())).length === 0 ? (
                  <EmptyLine text="No local maps" />
                ) : localMaps.filter(r => !query || r.name.toLowerCase().includes(query.toLowerCase())).map((region) => {
                  const isPendingDelete = pendingDeleteId === region.id;
                  const isBusy = transferring === region.id;
                  return (
                    <div key={region.id} className="border-b border-white/5 last:border-b-0">
                      {isPendingDelete ? (
                        <div className="px-2 py-2 space-y-1.5 rounded bg-zinc-800/60">
                          <div className="flex items-center gap-1.5 text-[11px] font-medium text-red-300">
                            <AlertTriangle size={11} className="shrink-0" />
                            Delete &ldquo;{region.name}&rdquo; from your local library?
                          </div>
                          <div className="flex gap-1.5">
                            <button type="button" onClick={() => confirmDelete(region)}
                              className="flex-1 rounded bg-red-500/20 px-2 py-1 text-[11px] font-medium text-red-300 hover:bg-red-500/30 transition-colors">
                              Delete
                            </button>
                            <button type="button" onClick={() => setPendingDeleteId(null)}
                              className="flex-1 rounded bg-white/5 px-2 py-1 text-[11px] text-slate-400 hover:bg-white/10 transition-colors">
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex items-center gap-2 px-2 py-2">
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-xs font-medium text-slate-200">{region.name}</div>
                            {region.location_label && <div className="truncate text-[11px] text-slate-500">{region.location_label}</div>}
                          </div>
                          <button type="button" onClick={() => renameMap(region)}
                            className="operator-shell-button h-7 w-7 rounded-md" title="Rename">
                            <Pencil size={13} />
                          </button>
                          {proxigoSession && cloudAccount?.org && (
                            <button type="button" onClick={() => publishToOrg(region)} disabled={isBusy}
                              className="operator-shell-button h-7 w-7 rounded-md hover:text-violet-300 disabled:opacity-40" title="Publish to Org">
                              {isBusy ? <Loader2 size={13} className="animate-spin" /> : <ArrowLeftRight size={13} />}
                            </button>
                          )}
                          <button type="button" onClick={() => setPendingDeleteId(region.id)}
                            className="operator-shell-button h-7 w-7 rounded-md hover:text-red-300" title="Delete">
                            <Trash2 size={13} />
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {/* Org maps */}
          {(openLibrary === "local" || openLibrary === "organization" ? false : true) || openLibrary === "organization" ? (
            <div>
              <div className="px-1 pb-1 text-[11px] font-semibold text-violet-400/70">Organization</div>
              <div className="max-h-48 overflow-y-auto">
                {!cloudAccount?.org ? (
                  <EmptyLine text="Sign in to an org to see shared maps" />
                ) : organizationMaps.filter(m => !query || m.name.toLowerCase().includes(query.toLowerCase())).length === 0 ? (
                  <EmptyLine text="No org maps — publish a local map to share with your team" />
                ) : organizationMaps.filter(m => !query || m.name.toLowerCase().includes(query.toLowerCase())).map((orgMap) => {
                  const isPendingDelete = pendingDeleteId === orgMap.id;
                  const isBusy = transferring === orgMap.id;
                  const canRemove = cloudAccount.org?.role === "admin" || orgMap.created_by === cloudAccount.user_id;
                  return (
                    <div key={orgMap.id} className="border-b border-white/5 last:border-b-0">
                      {isPendingDelete ? (
                        <div className="px-2 py-2 space-y-1.5 rounded bg-red-500/10 border border-red-500/25">
                          <div className="flex items-center gap-1.5 text-[11px] font-medium text-red-300">
                            <AlertTriangle size={11} className="shrink-0" />
                            Remove &ldquo;{orgMap.name}&rdquo; from org? All team members will lose access.
                          </div>
                          <div className="flex gap-1.5">
                            <button type="button" onClick={() => removeFromOrg(orgMap)} disabled={isBusy}
                              className="flex-1 rounded bg-red-500/20 px-2 py-1 text-[11px] font-medium text-red-300 hover:bg-red-500/30 transition-colors disabled:opacity-40">
                              {isBusy ? "Removing…" : "Remove from Org"}
                            </button>
                            <button type="button" onClick={() => setPendingDeleteId(null)}
                              className="flex-1 rounded bg-white/5 px-2 py-1 text-[11px] text-slate-400 hover:bg-white/10 transition-colors">
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex items-center gap-2 px-2 py-2">
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-xs font-medium text-slate-200">{orgMap.name}</div>
                            <div className="truncate font-mono text-[11px] text-slate-600">
                              {orgMap.location_label ?? `${orgMap.lat_min.toFixed(3)}, ${orgMap.lon_min.toFixed(3)}`} · Z{orgMap.zoom}
                            </div>
                          </div>
                          <button
                            type="button"
                            title="Download to local library"
                            onClick={() => { setPendingOrgMapDownload(orgMap); onDownloadOrgMap(); }}
                            className="operator-shell-button h-7 w-7 rounded-md hover:text-emerald-300"
                          >
                            <Download size={13} />
                          </button>
                          {canRemove && (
                            <button type="button" onClick={() => setPendingDeleteId(orgMap.id)}
                              className="operator-shell-button h-7 w-7 rounded-md hover:text-red-300" title="Remove from Org">
                              <Trash2 size={13} />
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}
        </div>
      )}
      {model.pendingMaps.length > 0 && (
        <>
          <SectionLabel>Pending</SectionLabel>
          {model.pendingMaps.slice(0, 4).map((map) => (
            <MapRow key={map.id} name={map.areaName} detail="request pending local build/upload" state={map.lifecycle} active={false} />
          ))}
        </>
      )}
      <StateCard
        label="Home Position"
        value={model.activeMap?.homePosition ? `${model.activeMap.homePosition.lat.toFixed(5)}, ${model.activeMap.homePosition.lon.toFixed(5)}` : "Not set"}
        detail={regions.length ? "stored on map record when available" : "create a map first"}
        tone={model.activeMap?.homePosition ? "green" : "muted"}
      />
      <div className="grid grid-cols-1 gap-2 pt-2">
        <PanelAction label="Create Map" detail="Use the map workflow backend when configured" disabled />
        <PanelAction label="Upload To Edge" detail="Unavailable until an edge device is connected" disabled />
        <PanelAction label="Set Active Map" detail="Requires an uploaded edge map" disabled />
      </div>
    </PanelStack>
  );
}
