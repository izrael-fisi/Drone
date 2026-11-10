import { create } from "zustand";
import type { LatLon } from "./types";

/** "overview" is the cross-product global view; any other value is a product id. */
export const OVERVIEW_ID = "overview";

const STORAGE_KEY = "proxigo.platform.v1";

export interface SavedMapView {
  center: LatLon;
  zoom: number;
}

/** Per-machine shell state that survives restarts. */
interface PersistedPlatformState {
  activeProductId: string | null;
  layerVisibility: Record<string, boolean>;
  sidebarCollapsed: boolean;
  /** Last map position, restored on launch. */
  mapView: SavedMapView | null;
}

const DEFAULTS: PersistedPlatformState = { activeProductId: null, layerVisibility: {}, sidebarCollapsed: false, mapView: null };

function isMapView(value: unknown): value is SavedMapView {
  const view = value as SavedMapView | null;
  return Boolean(
    view &&
      Array.isArray(view.center) &&
      view.center.length === 2 &&
      view.center.every((n) => typeof n === "number" && Number.isFinite(n)) &&
      typeof view.zoom === "number" &&
      Number.isFinite(view.zoom),
  );
}

function readPersisted(): PersistedPlatformState {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null") as Partial<PersistedPlatformState> | null;
    if (parsed) {
      return {
        activeProductId: typeof parsed.activeProductId === "string" ? parsed.activeProductId : null,
        layerVisibility: parsed.layerVisibility && typeof parsed.layerVisibility === "object" ? parsed.layerVisibility : {},
        sidebarCollapsed: parsed.sidebarCollapsed === true,
        mapView: isMapView(parsed.mapView) ? parsed.mapView : null,
      };
    }
  } catch {
    // Storage unavailable or corrupt; fall back to defaults.
  }
  return DEFAULTS;
}

function writePersisted(state: PersistedPlatformState) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Per-machine convenience only.
  }
}

interface PlatformState extends PersistedPlatformState {
  setActiveProduct: (id: string) => void;
  setLayerVisible: (layerId: string, visible: boolean) => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setMapView: (view: SavedMapView) => void;
}

export const usePlatformStore = create<PlatformState>((set, get) => {
  const persist = (patch: Partial<PersistedPlatformState>) => {
    set(patch);
    const { activeProductId, layerVisibility, sidebarCollapsed, mapView } = get();
    writePersisted({ activeProductId, layerVisibility, sidebarCollapsed, mapView });
  };
  return {
    ...readPersisted(),
    setActiveProduct: (activeProductId) => {
      if (get().activeProductId !== activeProductId) persist({ activeProductId });
    },
    setLayerVisible: (layerId, visible) => persist({ layerVisibility: { ...get().layerVisibility, [layerId]: visible } }),
    setSidebarCollapsed: (sidebarCollapsed) => persist({ sidebarCollapsed }),
    setMapView: (mapView) => persist({ mapView }),
  };
});
