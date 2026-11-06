import { create } from "zustand";

/** "overview" is the cross-product global view; any other value is a product id. */
export const OVERVIEW_ID = "overview";

const STORAGE_KEY = "proxigo.platform.v1";

interface PersistedPlatformState {
  activeProductId: string | null;
  layerVisibility: Record<string, boolean>;
}

function readPersisted(): PersistedPlatformState {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<PersistedPlatformState>;
      return {
        activeProductId: typeof parsed.activeProductId === "string" ? parsed.activeProductId : null,
        layerVisibility: parsed.layerVisibility && typeof parsed.layerVisibility === "object" ? parsed.layerVisibility : {},
      };
    }
  } catch {
    // Storage unavailable (private window, blocked site data); fall back to defaults.
  }
  return { activeProductId: null, layerVisibility: {} };
}

function writePersisted(state: PersistedPlatformState) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Per-viewer convenience only; ignore storage failures.
  }
}

interface PlatformState extends PersistedPlatformState {
  /** null until the operator picks a product; the shell then chooses a default. */
  setActiveProduct: (id: string) => void;
  setLayerVisible: (layerId: string, visible: boolean) => void;
}

export const usePlatformStore = create<PlatformState>((set, get) => ({
  ...readPersisted(),
  setActiveProduct: (activeProductId) => {
    if (get().activeProductId === activeProductId) return;
    set({ activeProductId });
    writePersisted({ activeProductId, layerVisibility: get().layerVisibility });
  },
  setLayerVisible: (layerId, visible) => {
    const layerVisibility = { ...get().layerVisibility, [layerId]: visible };
    set({ layerVisibility });
    writePersisted({ activeProductId: get().activeProductId, layerVisibility });
  },
}));
