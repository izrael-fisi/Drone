import { create } from "zustand";
import type { PlatformMapApi } from "./types";

interface PlatformMapState {
  /** Camera controls of the shared map, once it has mounted. */
  api: PlatformMapApi | null;
  /** Cursor a product requests while idle (e.g. "crosshair" while placing). */
  cursor: string | null;
  setApi: (api: PlatformMapApi | null) => void;
  setCursor: (cursor: string | null) => void;
}

/** Runtime-only bridge between the shared map host and product modules. */
export const usePlatformMapStore = create<PlatformMapState>((set) => ({
  api: null,
  cursor: null,
  setApi: (api) => set({ api }),
  setCursor: (cursor) => set({ cursor }),
}));
