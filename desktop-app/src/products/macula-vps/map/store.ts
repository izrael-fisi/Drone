import { create } from "zustand";
import { loadSavedMissions, SAVED_MISSIONS_CHANGED } from "../../../lib/missions";
import type { DronePositionUpdate, MapProviderId, SavedMission } from "../../../lib/types";
import type { CutInstallProgress, CutShape, MapSelectionValue, MapToolMode } from "./helpers";

/**
 * Macula VPS map tool state. Lives outside the map overlay component so
 * drafts, selections and an in-progress map install survive switching to
 * another product and back.
 */
export interface VpsMapState {
  selectedMap: MapSelectionValue;
  toolMode: MapToolMode;
  borderPoints: [number, number][];
  borderCutShape: CutShape;
  waypoints: [number, number][];
  drawingMessage: string | null;
  cutDownloadZoom: number;
  multiLayerMap: boolean;
  selectedProviderIds: MapProviderId[];
  cutLimitOverride: boolean;
  cutInstalling: boolean;
  cutInstallProgress: CutInstallProgress | null;
  savedMissions: SavedMission[];
  missionMode: boolean;
  selectedMissionIds: string[];
  editingMissionId: string | null;
  /** Latest position packet (UDP or edge MAVLink), polled in the background. */
  position: DronePositionUpdate | null;
  telemetryMessage: string;
}

function readMissions(): SavedMission[] {
  try {
    return loadSavedMissions();
  } catch {
    return [];
  }
}

export const useVpsMapStore = create<VpsMapState>(() => ({
  selectedMap: "world",
  toolMode: "idle",
  borderPoints: [],
  borderCutShape: "polygon",
  waypoints: [],
  drawingMessage: null,
  cutDownloadZoom: 18,
  multiLayerMap: false,
  selectedProviderIds: [],
  cutLimitOverride: false,
  cutInstalling: false,
  cutInstallProgress: null,
  savedMissions: readMissions(),
  missionMode: false,
  selectedMissionIds: [],
  editingMissionId: null,
  position: null,
  telemetryMessage: "listening",
}));

if (typeof window !== "undefined") {
  const refreshMissions = () => useVpsMapStore.setState({ savedMissions: readMissions() });
  window.addEventListener(SAVED_MISSIONS_CHANGED, refreshMissions);
  window.addEventListener("storage", refreshMissions);
}

type Updater<T> = T | ((current: T) => T);

const setters = new Map<keyof VpsMapState, (value: never) => void>();

/** Stable setter for one store field, accepting a value or an updater like `setState`. */
function setterFor<K extends keyof VpsMapState>(key: K): (value: Updater<VpsMapState[K]>) => void {
  let setter = setters.get(key) as ((value: Updater<VpsMapState[K]>) => void) | undefined;
  if (!setter) {
    setter = (next) =>
      useVpsMapStore.setState((state) => ({
        [key]: typeof next === "function" ? (next as (current: VpsMapState[K]) => VpsMapState[K])(state[key]) : next,
      }) as Partial<VpsMapState>);
    setters.set(key, setter as (value: never) => void);
  }
  return setter;
}

/** `useState`-shaped access to one store field. */
export function useVpsState<K extends keyof VpsMapState>(key: K): [VpsMapState[K], (value: Updater<VpsMapState[K]>) => void] {
  return [useVpsMapStore((state) => state[key]), setterFor(key)];
}
