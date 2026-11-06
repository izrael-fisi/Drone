import { create } from "zustand";
import type { LatLon, PlatformMapApi, PlatformMapTool } from "./types";

interface PlatformMapState {
  /** Camera controls of the shared map, once it has mounted. */
  api: PlatformMapApi | null;
  /** Cursor a product requests while no tool is active (e.g. "crosshair"). */
  cursor: string | null;
  /** Drawing tool of the active product; map clicks go to it instead of `onMapClick`. */
  tool: PlatformMapTool | null;
  /** Points the Recenter button fits, e.g. the active product's working area. */
  home: LatLon[] | null;
  setApi: (api: PlatformMapApi | null) => void;
  setCursor: (cursor: string | null) => void;
  setTool: (tool: PlatformMapTool | null) => void;
  setHome: (home: LatLon[] | null) => void;
}

/** Runtime-only bridge between the shared map host and product modules. */
export const usePlatformMapStore = create<PlatformMapState>((set) => ({
  api: null,
  cursor: null,
  tool: null,
  home: null,
  setApi: (api) => set({ api }),
  setCursor: (cursor) => set({ cursor }),
  setTool: (tool) => set({ tool }),
  setHome: (home) => set({ home }),
}));
