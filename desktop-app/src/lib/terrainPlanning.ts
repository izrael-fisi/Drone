export type TerrainPlanningConstraints = {
  min_agl_m: number;
  max_terrain_relief_m: number;
  min_agl_to_gsd_ratio: number;
  max_route_segment_m: number;
};

export const TERRAIN_CONSTRAINTS_STORAGE_KEY = "drone_terrain_planning_constraints_v1";

export const DEFAULT_TERRAIN_CONSTRAINTS: TerrainPlanningConstraints = {
  min_agl_m: 20,
  max_terrain_relief_m: 40,
  min_agl_to_gsd_ratio: 40,
  max_route_segment_m: 500,
};

export function normalizeTerrainConstraints(value: Partial<TerrainPlanningConstraints> | null | undefined) {
  const source = value ?? {};
  return {
    min_agl_m: safeNonNegative(source.min_agl_m, DEFAULT_TERRAIN_CONSTRAINTS.min_agl_m),
    max_terrain_relief_m: safeNonNegative(source.max_terrain_relief_m, DEFAULT_TERRAIN_CONSTRAINTS.max_terrain_relief_m),
    min_agl_to_gsd_ratio: safeNonNegative(source.min_agl_to_gsd_ratio, DEFAULT_TERRAIN_CONSTRAINTS.min_agl_to_gsd_ratio),
    max_route_segment_m: safeNonNegative(source.max_route_segment_m, DEFAULT_TERRAIN_CONSTRAINTS.max_route_segment_m),
  };
}

export function loadTerrainConstraints(): TerrainPlanningConstraints {
  try {
    const raw = localStorage.getItem(TERRAIN_CONSTRAINTS_STORAGE_KEY);
    return raw ? normalizeTerrainConstraints(JSON.parse(raw)) : DEFAULT_TERRAIN_CONSTRAINTS;
  } catch {
    return DEFAULT_TERRAIN_CONSTRAINTS;
  }
}

export function saveTerrainConstraints(value: TerrainPlanningConstraints) {
  localStorage.setItem(TERRAIN_CONSTRAINTS_STORAGE_KEY, JSON.stringify(normalizeTerrainConstraints(value)));
}

function safeNonNegative(value: unknown, fallback: number) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) && numberValue >= 0 ? numberValue : fallback;
}
