import { useMemo } from "react";
import { create } from "zustand";
import type { LatLon } from "../../platform/types";
import {
  bboxAround,
  fetchControlledAirspace,
  fetchMetars,
  fetchUasGrid,
  fetchWeather,
  likelyInFaaCoverage,
  pointInRing,
  type AirspaceArea,
  type MetarStation,
  type UasGridCell,
  type WeatherReport,
} from "./sources";

/** Operator flight limits used for the go/no-go assessment (per machine). */
export interface FlightLimits {
  maxWindMs: number;
  maxGustMs: number;
  minVisibilityM: number;
  maxPrecipitationMm: number;
  minTemperatureC: number;
  maxTemperatureC: number;
}

export const DEFAULT_LIMITS: FlightLimits = {
  maxWindMs: 10,
  maxGustMs: 13,
  minVisibilityM: 5000,
  maxPrecipitationMm: 0.5,
  minTemperatureC: -10,
  maxTemperatureC: 40,
};

const LIMITS_KEY = "proxigo.airspace-weather.limits.v1";
const AIRSPACE_RADIUS_KM = 30;
const UAS_GRID_RADIUS_KM = 5;
const METAR_RADIUS_KM = 80;

function readLimits(): FlightLimits {
  try {
    const raw = window.localStorage.getItem(LIMITS_KEY);
    if (raw) return { ...DEFAULT_LIMITS, ...(JSON.parse(raw) as Partial<FlightLimits>) };
  } catch {
    // Use defaults.
  }
  return DEFAULT_LIMITS;
}

type SourceKey = "weather" | "airspace" | "uasGrid" | "metars";

interface SourceState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

const emptySource = <T,>(): SourceState<T> => ({ data: null, loading: false, error: null });

interface AirspaceWeatherState {
  point: LatLon | null;
  briefedAt: number | null;
  weather: SourceState<WeatherReport>;
  airspace: SourceState<AirspaceArea[]>;
  uasGrid: SourceState<UasGridCell[]>;
  metars: SourceState<MetarStation[]>;
  selectedStationId: string | null;
  limits: FlightLimits;
  /** Load weather, airspace and observations for a location. */
  brief: (point: LatLon) => void;
  refresh: () => void;
  selectStation: (id: string | null) => void;
  setLimits: (patch: Partial<FlightLimits>) => void;
  resetLimits: () => void;
}

let requestSeq = 0;

export const useAirspaceStore = create<AirspaceWeatherState>((set, get) => ({
  point: null,
  briefedAt: null,
  weather: emptySource(),
  airspace: emptySource(),
  uasGrid: emptySource(),
  metars: emptySource(),
  selectedStationId: null,
  limits: readLimits(),

  brief: (point) => {
    const seq = ++requestSeq;
    const inFaa = likelyInFaaCoverage(point);
    set({
      point,
      briefedAt: Date.now(),
      weather: { ...emptySource(), loading: true },
      airspace: { ...emptySource(), loading: inFaa, data: inFaa ? null : [] },
      uasGrid: { ...emptySource(), loading: inFaa, data: inFaa ? null : [] },
      metars: { ...emptySource(), loading: true },
    });

    const run = <K extends SourceKey>(key: K, load: () => Promise<NonNullable<AirspaceWeatherState[K]["data"]>>) => {
      load()
        .then((data) => {
          if (seq === requestSeq) set({ [key]: { data, loading: false, error: null } } as Partial<AirspaceWeatherState>);
        })
        .catch((error: unknown) => {
          if (seq === requestSeq) {
            set({ [key]: { data: null, loading: false, error: error instanceof Error ? error.message : String(error) } } as Partial<AirspaceWeatherState>);
          }
        });
    };

    run("weather", () => fetchWeather(point));
    run("metars", () => fetchMetars(bboxAround(point, METAR_RADIUS_KM)));
    if (inFaa) {
      run("airspace", () => fetchControlledAirspace(bboxAround(point, AIRSPACE_RADIUS_KM)));
      run("uasGrid", () => fetchUasGrid(bboxAround(point, UAS_GRID_RADIUS_KM)));
    }
  },

  refresh: () => {
    const point = get().point;
    if (point) get().brief(point);
  },

  selectStation: (selectedStationId) => set({ selectedStationId }),

  setLimits: (patch) => {
    const limits = { ...get().limits, ...patch };
    set({ limits });
    try {
      window.localStorage.setItem(LIMITS_KEY, JSON.stringify(limits));
    } catch {
      // Per-machine preference only.
    }
  },

  resetLimits: () => {
    set({ limits: DEFAULT_LIMITS });
    try {
      window.localStorage.removeItem(LIMITS_KEY);
    } catch {
      // Ignore.
    }
  },
}));

// ---------------------------------------------------------------- Assessment

export type CheckLevel = "go" | "caution" | "nogo" | "info";

export interface BriefingCheck {
  id: string;
  label: string;
  value: string;
  level: CheckLevel;
  detail?: string;
}

export interface Assessment {
  overall: CheckLevel;
  checks: BriefingCheck[];
  /** Airspace areas containing the briefing point. */
  airspaceHere: AirspaceArea[];
  uasCeilingHere: UasGridCell | null;
  pending: boolean;
}

const LEVEL_RANK: Record<CheckLevel, number> = { info: 0, go: 1, caution: 2, nogo: 3 };

export function levelLabel(level: CheckLevel) {
  return level === "nogo" ? "No-go" : level === "caution" ? "Caution" : level === "go" ? "Go" : "Info";
}

export function assess(state: Pick<AirspaceWeatherState, "point" | "weather" | "airspace" | "uasGrid" | "limits">): Assessment {
  const { point, weather, airspace, uasGrid, limits } = state;
  const checks: BriefingCheck[] = [];
  const pending = weather.loading || airspace.loading || uasGrid.loading;
  if (!point) return { overall: "info", checks, airspaceHere: [], uasCeilingHere: null, pending: false };

  const now = weather.data?.now;
  if (now) {
    const wind = now.windMs;
    checks.push({
      id: "wind",
      label: "Wind",
      value: `${wind.toFixed(1)} m/s from ${Math.round(now.windDirDeg)}°`,
      level: wind > limits.maxWindMs ? "nogo" : wind > limits.maxWindMs * 0.8 ? "caution" : "go",
      detail: `Limit ${limits.maxWindMs} m/s`,
    });
    checks.push({
      id: "gust",
      label: "Gusts",
      value: `${now.gustMs.toFixed(1)} m/s`,
      level: now.gustMs > limits.maxGustMs ? "nogo" : now.gustMs > limits.maxGustMs * 0.8 ? "caution" : "go",
      detail: `Limit ${limits.maxGustMs} m/s`,
    });
    checks.push({
      id: "visibility",
      label: "Visibility",
      value: now.visibilityM >= 10_000 ? `${(now.visibilityM / 1000).toFixed(0)} km` : `${Math.round(now.visibilityM)} m`,
      level: now.visibilityM < limits.minVisibilityM ? "nogo" : "go",
      detail: `Minimum ${limits.minVisibilityM} m`,
    });
    checks.push({
      id: "precipitation",
      label: "Precipitation",
      value: `${now.precipitationMm.toFixed(1)} mm`,
      level: now.precipitationMm > limits.maxPrecipitationMm ? "nogo" : now.precipitationMm > 0 ? "caution" : "go",
      detail: `Limit ${limits.maxPrecipitationMm} mm`,
    });
    checks.push({
      id: "temperature",
      label: "Temperature",
      value: `${now.temperatureC.toFixed(0)} °C`,
      level: now.temperatureC < limits.minTemperatureC || now.temperatureC > limits.maxTemperatureC ? "caution" : "go",
      detail: `Range ${limits.minTemperatureC} to ${limits.maxTemperatureC} °C`,
    });
  } else if (weather.error) {
    checks.push({ id: "weather", label: "Weather", value: "Unavailable", level: "caution", detail: weather.error });
  }

  const airspaceHere = (airspace.data ?? []).filter((area) => area.rings.some((ring) => pointInRing(point, ring)));
  const uasCeilingHere = (uasGrid.data ?? []).find((cell) => cell.rings.some((ring) => pointInRing(point, ring))) ?? null;
  const inFaa = likelyInFaaCoverage(point);

  if (!inFaa) {
    checks.push({ id: "airspace", label: "Airspace", value: "No data", level: "info", detail: "FAA airspace data covers the US only. Check local authority maps." });
  } else if (airspace.error) {
    checks.push({ id: "airspace", label: "Airspace", value: "Unavailable", level: "caution", detail: airspace.error });
  } else if (airspace.data) {
    const surface = airspaceHere.filter((area) => area.floor === "surface");
    const classes = [...new Set(airspaceHere.map((area) => area.airspaceClass))].sort();
    checks.push({
      id: "airspace",
      label: "Airspace",
      value: classes.length ? `Class ${classes.join(", ")}` : "Class G",
      level: surface.length ? "caution" : "go",
      detail: surface.length
        ? "Controlled from the surface: FAA authorization (LAANC or DroneZone) required."
        : classes.length
          ? "Controlled airspace overhead; its floor is above the surface here."
          : "Uncontrolled. Part 107 limit 400 ft AGL.",
    });
  }

  if (inFaa && uasGrid.data && uasCeilingHere) {
    checks.push({
      id: "laanc",
      label: "LAANC ceiling",
      value: `${uasCeilingHere.ceilingFt} ft AGL`,
      level: uasCeilingHere.ceilingFt === 0 ? "nogo" : "caution",
      detail: uasCeilingHere.ceilingFt === 0
        ? `No automatic authorization near ${uasCeilingHere.airport}; needs further FAA coordination.`
        : `Automatic authorization up to ${uasCeilingHere.ceilingFt} ft near ${uasCeilingHere.airport}.`,
    });
  } else if (uasGrid.error) {
    checks.push({ id: "laanc", label: "LAANC ceiling", value: "Unavailable", level: "info", detail: uasGrid.error });
  }

  // Never show a verdict built from partial data.
  const overall = pending || !checks.length
    ? "info"
    : checks.reduce<CheckLevel>((worst, check) => (LEVEL_RANK[check.level] > LEVEL_RANK[worst] ? check.level : worst), "go");
  return { overall, checks, airspaceHere, uasCeilingHere, pending };
}

export function useAssessment(): Assessment {
  const point = useAirspaceStore((state) => state.point);
  const weather = useAirspaceStore((state) => state.weather);
  const airspace = useAirspaceStore((state) => state.airspace);
  const uasGrid = useAirspaceStore((state) => state.uasGrid);
  const limits = useAirspaceStore((state) => state.limits);
  return useMemo(() => assess({ point, weather, airspace, uasGrid, limits }), [point, weather, airspace, uasGrid, limits]);
}
