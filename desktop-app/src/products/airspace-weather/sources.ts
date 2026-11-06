import { ApiError, requestJson } from "../../platform/api/http";
import type { LatLon } from "../../platform/types";

/**
 * Third-party data sources for Airspace & Weather. All public and keyless:
 *
 * - Weather: Open-Meteo forecast API (global).
 * - METAR observations: aviationweather.gov Data API (global stations, best in the US).
 * - Controlled airspace and UAS facility map ceilings: FAA ArcGIS open data (US only).
 *
 * Requests go through the platform HTTP layer (Rust), not the webview. A
 * Proxigo backend can later proxy and cache these; only the URLs change.
 */

const OPEN_METEO = "https://api.open-meteo.com/v1/forecast";
const AVIATION_WEATHER = "https://aviationweather.gov/api/data/metar";
const FAA_ARCGIS = "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services";

export interface Bbox {
  latMin: number;
  latMax: number;
  lonMin: number;
  lonMax: number;
}

export function bboxAround([lat, lon]: LatLon, radiusKm: number): Bbox {
  const dLat = radiusKm / 111.32;
  const dLon = radiusKm / (111.32 * Math.max(0.05, Math.cos((lat * Math.PI) / 180)));
  return {
    latMin: Math.max(-90, lat - dLat),
    latMax: Math.min(90, lat + dLat),
    lonMin: Math.max(-180, lon - dLon),
    lonMax: Math.min(180, lon + dLon),
  };
}

async function getJson<T>(url: string, source: string, timeoutMs = 20_000): Promise<T> {
  const response = await requestJson<T>({ url, timeoutMs });
  if (!response.ok) throw new ApiError(`${source} returned HTTP ${response.status}`, "http", response.status, url);
  const data = response.data as unknown as { error?: { message?: string } } | null;
  if (data && typeof data === "object" && "error" in data && data.error) {
    throw new ApiError(`${source}: ${data.error.message ?? "request failed"}`, "http", response.status, url);
  }
  return response.data;
}

// ---------------------------------------------------------------- Weather

export interface WeatherNow {
  time: string;
  temperatureC: number;
  windMs: number;
  gustMs: number;
  windDirDeg: number;
  precipitationMm: number;
  cloudCoverPct: number;
  visibilityM: number;
  weatherCode: number;
}

export interface WeatherHour {
  time: string;
  windMs: number;
  gustMs: number;
  precipitationProbabilityPct: number;
  visibilityM: number;
}

export interface WeatherReport {
  now: WeatherNow;
  hours: WeatherHour[];
  timezone: string;
}

interface OpenMeteoResponse {
  timezone: string;
  current: {
    time: string;
    temperature_2m: number;
    wind_speed_10m: number;
    wind_gusts_10m: number;
    wind_direction_10m: number;
    precipitation: number;
    cloud_cover: number;
    visibility: number;
    weather_code: number;
  };
  hourly: {
    time: string[];
    wind_speed_10m: number[];
    wind_gusts_10m: number[];
    precipitation_probability: number[];
    visibility: number[];
  };
}

export async function fetchWeather([lat, lon]: LatLon): Promise<WeatherReport> {
  const params = new URLSearchParams({
    latitude: lat.toFixed(4),
    longitude: lon.toFixed(4),
    current: "temperature_2m,wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation,cloud_cover,visibility,weather_code",
    hourly: "wind_speed_10m,wind_gusts_10m,precipitation_probability,visibility",
    wind_speed_unit: "ms",
    forecast_hours: "12",
    timezone: "auto",
  });
  const data = await getJson<OpenMeteoResponse>(`${OPEN_METEO}?${params}`, "Open-Meteo");
  const c = data.current;
  const h = data.hourly;
  return {
    timezone: data.timezone,
    now: {
      time: c.time,
      temperatureC: c.temperature_2m,
      windMs: c.wind_speed_10m,
      gustMs: c.wind_gusts_10m,
      windDirDeg: c.wind_direction_10m,
      precipitationMm: c.precipitation,
      cloudCoverPct: c.cloud_cover,
      visibilityM: c.visibility,
      weatherCode: c.weather_code,
    },
    hours: h.time.map((time, index) => ({
      time,
      windMs: h.wind_speed_10m[index],
      gustMs: h.wind_gusts_10m[index],
      precipitationProbabilityPct: h.precipitation_probability[index],
      visibilityM: h.visibility[index],
    })),
  };
}

// ---------------------------------------------------------------- METAR

export interface MetarStation {
  id: string;
  name: string;
  position: LatLon;
  flightCategory: string | null;
  raw: string;
  observedAt: string | null;
  windKt: number | null;
  visibility: string | null;
}

interface MetarJson {
  icaoId: string;
  name?: string;
  lat: number;
  lon: number;
  fltCat?: string | null;
  rawOb?: string;
  reportTime?: string;
  wspd?: number | null;
  visib?: string | number | null;
}

export async function fetchMetars(bbox: Bbox): Promise<MetarStation[]> {
  const box = [bbox.latMin, bbox.lonMin, bbox.latMax, bbox.lonMax].map((value) => value.toFixed(3)).join(",");
  const data = await getJson<MetarJson[] | null>(`${AVIATION_WEATHER}?bbox=${box}&format=json`, "aviationweather.gov");
  return (data ?? [])
    .filter((item) => Number.isFinite(item.lat) && Number.isFinite(item.lon))
    .map((item) => ({
      id: item.icaoId,
      name: item.name ?? item.icaoId,
      position: [item.lat, item.lon] as LatLon,
      flightCategory: item.fltCat ?? null,
      raw: item.rawOb ?? "",
      observedAt: item.reportTime ?? null,
      windKt: item.wspd ?? null,
      visibility: item.visib == null ? null : String(item.visib),
    }));
}

// ---------------------------------------------------------------- FAA ArcGIS

interface GeoJsonFeatureCollection<P> {
  features?: Array<{
    id?: number | string;
    properties: P;
    geometry: { type: "Polygon"; coordinates: number[][][] } | { type: "MultiPolygon"; coordinates: number[][][][] } | null;
  }>;
}

/** Outer rings as [lat, lon] lists (holes are ignored for display and hit tests). */
function outerRings(geometry: NonNullable<GeoJsonFeatureCollection<unknown>["features"]>[number]["geometry"]): LatLon[][] {
  if (!geometry) return [];
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  return polygons
    .map((polygon) => (polygon[0] ?? []).map(([lon, lat]) => [lat, lon] as LatLon))
    .filter((ring) => ring.length >= 3);
}

function arcgisQuery(layerPath: string, bbox: Bbox, where: string, outFields: string, maxRecords: number) {
  const params = new URLSearchParams({
    where,
    geometry: [bbox.lonMin, bbox.latMin, bbox.lonMax, bbox.latMax].map((value) => value.toFixed(5)).join(","),
    geometryType: "esriGeometryEnvelope",
    inSR: "4326",
    spatialRel: "esriSpatialRelIntersects",
    outFields,
    outSR: "4326",
    geometryPrecision: "5",
    maxAllowableOffset: "0.0002",
    resultRecordCount: String(maxRecords),
    f: "geojson",
  });
  return `${FAA_ARCGIS}/${layerPath}/query?${params}`;
}

export interface AirspaceArea {
  id: string;
  name: string;
  /** B, C, D or E (surface area). */
  airspaceClass: string;
  localType: string;
  floor: string;
  ceiling: string;
  rings: LatLon[][];
}

interface AirspaceProps {
  OBJECTID?: number;
  NAME: string;
  CLASS: string | null;
  LOCAL_TYPE: string | null;
  LOWER_VAL: number | null;
  UPPER_VAL: number | null;
  LOWER_UOM: string | null;
  UPPER_UOM: string | null;
  LOWER_CODE: string | null;
  UPPER_CODE: string | null;
}

function altitudeLabel(value: number | null, uom: string | null, code: string | null) {
  if (value == null || value <= -9998) return "unlimited";
  if (value === 0 && (code === "SFC" || !code)) return "surface";
  if (uom === "FL") return `FL${value}`;
  return `${value.toLocaleString()} ft ${code ?? ""}`.trim();
}

/** Airspace that matters near the ground: Class B, C, D and Class E surface areas. */
export async function fetchControlledAirspace(bbox: Bbox): Promise<AirspaceArea[]> {
  const where = "CLASS IN ('B','C','D') OR LOCAL_TYPE IN ('CLASS_E2','CLASS_E3','CLASS_E4')";
  const url = arcgisQuery(
    "Class_Airspace/FeatureServer/0",
    bbox,
    where,
    "OBJECTID,NAME,CLASS,LOCAL_TYPE,LOWER_VAL,UPPER_VAL,LOWER_UOM,UPPER_UOM,LOWER_CODE,UPPER_CODE",
    500,
  );
  const data = await getJson<GeoJsonFeatureCollection<AirspaceProps>>(url, "FAA airspace", 30_000);
  return (data.features ?? []).flatMap((feature, index) => {
    const props = feature.properties;
    const rings = outerRings(feature.geometry);
    if (!rings.length) return [];
    return [{
      id: String(props.OBJECTID ?? feature.id ?? index),
      name: props.NAME,
      airspaceClass: props.CLASS ?? "E",
      localType: props.LOCAL_TYPE ?? "",
      floor: altitudeLabel(props.LOWER_VAL, props.LOWER_UOM, props.LOWER_CODE),
      ceiling: altitudeLabel(props.UPPER_VAL, props.UPPER_UOM, props.UPPER_CODE),
      rings,
    }];
  });
}

export interface UasGridCell {
  id: string;
  /** Max altitude in ft AGL the FAA may auto-authorize via LAANC. */
  ceilingFt: number;
  airport: string;
  rings: LatLon[][];
}

interface UasProps {
  OBJECTID?: number;
  CEILING: number;
  APT1_FAAID?: string | null;
  APT1_NAME?: string | null;
}

/** FAA UAS Facility Map grid: LAANC altitude ceilings near airports. */
export async function fetchUasGrid(bbox: Bbox): Promise<UasGridCell[]> {
  const url = arcgisQuery("FAA_UAS_FacilityMap_Data/FeatureServer/0", bbox, "1=1", "OBJECTID,CEILING,APT1_FAAID,APT1_NAME", 1000);
  const data = await getJson<GeoJsonFeatureCollection<UasProps>>(url, "FAA UAS facility map", 30_000);
  return (data.features ?? []).flatMap((feature, index) => {
    const rings = outerRings(feature.geometry);
    if (!rings.length || !Number.isFinite(feature.properties.CEILING)) return [];
    const props = feature.properties;
    return [{
      id: String(props.OBJECTID ?? feature.id ?? index),
      ceilingFt: props.CEILING,
      airport: [props.APT1_FAAID, props.APT1_NAME].filter(Boolean).join(" "),
      rings,
    }];
  });
}

export function pointInRing([lat, lon]: LatLon, ring: LatLon[]) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [latI, lonI] = ring[i];
    const [latJ, lonJ] = ring[j];
    if ((latI > lat) !== (latJ > lat) && lon < ((lonJ - lonI) * (lat - latI)) / (latJ - latI) + lonI) {
      inside = !inside;
    }
  }
  return inside;
}

/** Rough check for the FAA datasets' coverage (US states and territories). */
export function likelyInFaaCoverage([lat, lon]: LatLon) {
  const conus = lat > 24 && lat < 50 && lon > -125.5 && lon < -66;
  const alaska = lat > 51 && lat < 72 && (lon < -129 || lon > 172);
  const hawaii = lat > 18 && lat < 23 && lon > -161 && lon < -154;
  const caribbean = lat > 17 && lat < 19 && lon > -68 && lon < -64;
  return conus || alaska || hawaii || caribbean;
}
