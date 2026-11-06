export const AIRSPACE_WEATHER_ID = "airspace-weather";
export const ACCENT = "#A78BFA";

export const PANEL_BRIEFING = "airspace-briefing";
export const PANEL_AIRSPACE = "airspace-areas";
export const PANEL_STATIONS = "airspace-stations";
export const PANEL_LIMITS = "airspace-limits";

export const LAYER_CONTROLLED = `${AIRSPACE_WEATHER_ID}.controlled`;
export const LAYER_UAS_GRID = `${AIRSPACE_WEATHER_ID}.uas-ceilings`;
export const LAYER_METAR = `${AIRSPACE_WEATHER_ID}.metar`;
export const LAYER_POINT = `${AIRSPACE_WEATHER_ID}.briefing-point`;

/** VPS layer shown in this product's view: where mapped GNSS-denied flight is possible. */
export const VPS_COVERAGE_LAYER_ID = "macula-vps.map-coverage";

export const AIRSPACE_CLASS_COLORS: Record<string, string> = {
  B: "#3B82F6",
  C: "#EC4899",
  D: "#06B6D4",
  E: "#A78BFA",
};

export const FLIGHT_CATEGORY_COLORS: Record<string, string> = {
  VFR: "#22C55E",
  MVFR: "#3B82F6",
  IFR: "#EF4444",
  LIFR: "#D946EF",
};

export function uasCeilingColor(ceilingFt: number) {
  if (ceilingFt <= 0) return "#EF4444";
  if (ceilingFt <= 100) return "#F97316";
  if (ceilingFt <= 200) return "#EAB308";
  return "#22C55E";
}

export const ADVISORY_NOTE =
  "Advisory only. TFRs and NOTAMs are not included. Confirm with official sources (FAA B4UFLY / LAANC) before flight.";
