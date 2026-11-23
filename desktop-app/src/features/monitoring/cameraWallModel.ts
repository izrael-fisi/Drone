const WALL_PREFERENCES_KEY = "proxigo.global-view.camera-wall.v1";

export const WALL_LAYOUTS = [1, 2, 4, 9] as const;
export type WallLayout = typeof WALL_LAYOUTS[number];

export interface SavedWall {
  name: string;
  cameras: string[];
  layout: WallLayout;
}

export interface WallPreferences {
  pinned: string[];
  layout: WallLayout;
  walls: SavedWall[];
  view: string;
}

function cameraIds(value: unknown): string[] {
  return Array.isArray(value) ? [...new Set(value.filter((id): id is string => typeof id === "string"))] : [];
}

export function wallLayout(value: unknown): WallLayout {
  return WALL_LAYOUTS.find((size) => size === value) ?? 4;
}

export function readCameraWall(persisted = true): WallPreferences {
  const initial: WallPreferences = { pinned: [], layout: 4, walls: [], view: "all" };
  if (!persisted) return initial;
  try {
    const stored = JSON.parse(localStorage.getItem(WALL_PREFERENCES_KEY) ?? "null") as Partial<WallPreferences> | null;
    if (!stored) return initial;
    const walls = Array.isArray(stored.walls) ? stored.walls.filter((view) => typeof view?.name === "string" && view.name.trim())
      .map(({ name, cameras, layout }) => ({ name, cameras: cameraIds(cameras), layout: wallLayout(layout) })) : [];
    const view = stored.view === "pinned" || walls.some(({ name }) => stored.view === wallKey(name)) ? stored.view ?? "all" : "all";
    return { pinned: cameraIds(stored.pinned), layout: wallLayout(stored.layout), walls, view };
  } catch { return initial; }
}

export function wallKey(name: string) {
  return "saved:" + name.toLowerCase();
}

export function saveCameraWall(preferences: WallPreferences) {
  try { localStorage.setItem(WALL_PREFERENCES_KEY, JSON.stringify(preferences)); }
  catch { /* Camera views remain usable for this session. */ }
}
