import type { SavedMission } from "./types";

const SAVED_MISSIONS_KEY = "drone_saved_missions_v1";

export const SAVED_MISSIONS_CHANGED = "drone-saved-missions-changed";

export function createSavedMissionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `mission-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function missionBounds(points: [number, number][]) {
  if (points.length === 0) return undefined;
  const lats = points.map(([lat]) => lat);
  const lons = points.map(([, lon]) => lon);
  return {
    lat_min: Math.min(...lats),
    lat_max: Math.max(...lats),
    lon_min: Math.min(...lons),
    lon_max: Math.max(...lons),
  };
}

export function missionBoundsFromParts(borderPoints: [number, number][], waypoints: [number, number][]) {
  return missionBounds([...borderPoints, ...waypoints]);
}

export function missionCenter(mission: SavedMission): [number, number] {
  if (!mission.bounds) return mission.center;
  return [
    (mission.bounds.lat_min + mission.bounds.lat_max) / 2,
    (mission.bounds.lon_min + mission.bounds.lon_max) / 2,
  ];
}

function isPoint(value: unknown): value is [number, number] {
  return (
    Array.isArray(value)
    && value.length === 2
    && value.every((item) => typeof item === "number" && Number.isFinite(item))
  );
}

function normalizeMission(value: unknown): SavedMission | null {
  if (!value || typeof value !== "object") return null;
  const mission = value as Partial<SavedMission>;
  if (!mission.id || !mission.name || !isPoint(mission.center)) return null;
  return {
    id: mission.id,
    name: mission.name,
    created_at: mission.created_at ?? new Date().toISOString(),
    updated_at: mission.updated_at ?? mission.created_at ?? new Date().toISOString(),
    source: mission.source ?? "dashboard",
    map_id: mission.map_id,
    map_label: mission.map_label,
    center: mission.center,
    zoom: typeof mission.zoom === "number" && Number.isFinite(mission.zoom) ? mission.zoom : 12,
    border_points: Array.isArray(mission.border_points) ? mission.border_points.filter(isPoint) : [],
    waypoints: Array.isArray(mission.waypoints) ? mission.waypoints.filter(isPoint) : [],
    bounds: mission.bounds,
  };
}

export function loadSavedMissions(): SavedMission[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(SAVED_MISSIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(normalizeMission)
      .filter((mission): mission is SavedMission => Boolean(mission))
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
  } catch {
    return [];
  }
}

export function saveSavedMissions(missions: SavedMission[]) {
  if (typeof window === "undefined") return;
  const nextMissions = missions.slice().sort((left, right) => right.updated_at.localeCompare(left.updated_at));
  window.localStorage.setItem(SAVED_MISSIONS_KEY, JSON.stringify(nextMissions));
  window.dispatchEvent(new CustomEvent<SavedMission[]>(SAVED_MISSIONS_CHANGED, { detail: nextMissions }));
}

export function upsertSavedMission(mission: SavedMission) {
  const existing = loadSavedMissions();
  const nextMissions = [mission, ...existing.filter((item) => item.id !== mission.id)];
  saveSavedMissions(nextMissions);
  return nextMissions;
}

export function removeSavedMission(id: string) {
  const nextMissions = loadSavedMissions().filter((mission) => mission.id !== id);
  saveSavedMissions(nextMissions);
  return nextMissions;
}
