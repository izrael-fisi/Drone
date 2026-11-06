import type { GeoJSONSource, LngLatBoundsLike, Map as MapLibreMap, StyleSpecification, TransformConstrainFunction } from "maplibre-gl";

/** Geometry helpers, constants and the base style for the shared map. */

export function pointsBounds(points: [number, number][]) {
  const lats = points.map(([lat]) => lat);
  const lons = points.map(([, lon]) => lon);
  return {
    lat_min: Math.min(...lats),
    lat_max: Math.max(...lats),
    lon_min: Math.min(...lons),
    lon_max: Math.max(...lons),
  };
}


export function rectangleFromCorners(start: [number, number], end: [number, number]): [number, number][] {
  const latMin = Math.min(start[0], end[0]);
  const latMax = Math.max(start[0], end[0]);
  const lonMin = Math.min(start[1], end[1]);
  const lonMax = Math.max(start[1], end[1]);
  return [
    [latMin, lonMin],
    [latMin, lonMax],
    [latMax, lonMax],
    [latMax, lonMin],
  ];
}

export type MapApi = {
  zoomIn: () => void;
  zoomOut: () => void;
  recenter: () => void;
  focus: (center: [number, number], zoom: number) => void;
  fitPoints: (points: [number, number][], maxZoom?: number) => void;
};

export type MapViewport = {
  center: [number, number];
  zoom: number;
};


export const MAP_MIN_ZOOM = 0;
export const WEB_MERCATOR_LAT_LIMIT = 85.05112878;
export const MAP_MAX_ZOOM = 23;
export const PLACEHOLDER_ORG_COUNTRY_CENTER: [number, number] = [39.8283, -98.5795];
export const PLACEHOLDER_ORG_COUNTRY_ZOOM = 3;

export type MapGeoJsonFeature = {
  type: "Feature";
  properties: Record<string, string | number | boolean | null>;
  geometry:
    | { type: "Point"; coordinates: [number, number] }
    | { type: "LineString"; coordinates: [number, number][] }
    | { type: "Polygon"; coordinates: [Array<[number, number]>] };
};

export type MapGeoJsonCollection = {
  type: "FeatureCollection";
  features: MapGeoJsonFeature[];
};

export type MapLibreRuntime = typeof import("maplibre-gl") & {
  supported?: (options?: { failIfMajorPerformanceCaveat?: boolean }) => boolean;
};

export const EMPTY_GEOJSON: MapGeoJsonCollection = { type: "FeatureCollection", features: [] };
export const VECTOR_LABEL_GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
export const VECTOR_LABEL_SOURCE_URL = "https://tiles.openfreemap.org/planet";

export function vectorLabelTextField(): ["coalesce", ["get", string], ["get", string], ["get", string]] {
  return ["coalesce", ["get", "name_en"], ["get", "name:latin"], ["get", "name"]];
}

export const SATELLITE_STYLE: StyleSpecification = {
  version: 8,
  glyphs: VECTOR_LABEL_GLYPHS,
  projection: { type: "globe" },
  sky: {
    "sky-color": "#02050a",
    "horizon-color": "#05080c",
    "fog-color": "#02050a",
    "sky-horizon-blend": 0.18,
    "horizon-fog-blend": 0.9,
    "atmosphere-blend": 0.22,
  },
  sources: {
    imagery: {
      type: "raster",
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      maxzoom: MAP_MAX_ZOOM,
    },
    openmaptiles: {
      type: "vector",
      url: VECTOR_LABEL_SOURCE_URL,
    },
  },
  layers: [
    {
      id: "background",
      type: "background",
      paint: {
        "background-color": "#02050a",
      },
    },
    {
      id: "imagery",
      type: "raster",
      source: "imagery",
      paint: {
        "raster-brightness-min": 0,
        "raster-brightness-max": 0.72,
        "raster-contrast": 0.08,
        "raster-saturation": -0.22,
        "raster-fade-duration": 140,
        "raster-resampling": "linear",
      },
    },
    {
      id: "operator-country-boundary",
      type: "line",
      source: "openmaptiles",
      "source-layer": "boundary",
      filter: ["all", ["==", ["get", "admin_level"], 2], ["!=", ["get", "maritime"], 1], ["!=", ["get", "disputed"], 1]],
      paint: {
        "line-color": "rgba(224, 228, 226, 0.54)",
        "line-opacity": ["interpolate", ["linear"], ["zoom"], 0, 0.28, 3, 0.48, 7, 0.7],
        "line-width": ["interpolate", ["linear"], ["zoom"], 0, 0.5, 4, 0.9, 9, 1.4],
      },
    },
    {
      id: "operator-state-boundary",
      type: "line",
      source: "openmaptiles",
      "source-layer": "boundary",
      minzoom: 4,
      filter: ["all", ["==", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]],
      paint: {
        "line-color": "rgba(188, 195, 192, 0.35)",
        "line-opacity": ["interpolate", ["linear"], ["zoom"], 4, 0.18, 8, 0.46],
        "line-width": ["interpolate", ["linear"], ["zoom"], 4, 0.35, 9, 0.9],
        "line-dasharray": [2, 2],
      },
    },
    {
      id: "operator-country-label",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      filter: ["==", ["get", "class"], "country"],
      layout: {
        "text-field": vectorLabelTextField(),
        "text-font": ["Noto Sans Bold"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 0, 10, 2, 13, 5, 18, 8, 22],
        "text-max-width": 9,
        "symbol-sort-key": ["coalesce", ["get", "rank"], 99],
        "text-allow-overlap": false,
        "text-ignore-placement": false,
      },
      paint: {
        "text-color": "#f1f5f2",
        "text-halo-color": "rgba(2, 5, 10, 0.9)",
        "text-halo-width": 2,
        "text-halo-blur": 0.7,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 0, 0.88, 7, 0.72, 9, 0],
      },
    },
    {
      id: "operator-state-label",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      minzoom: 4,
      maxzoom: 9,
      filter: ["==", ["get", "class"], "state"],
      layout: {
        "text-field": vectorLabelTextField(),
        "text-font": ["Noto Sans Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 7, 14, 9, 16],
        "text-transform": "uppercase",
        "text-letter-spacing": 0.08,
        "text-max-width": 10,
        "symbol-sort-key": ["coalesce", ["get", "rank"], 99],
      },
      paint: {
        "text-color": "#cfd8d4",
        "text-halo-color": "rgba(2, 5, 10, 0.88)",
        "text-halo-width": 1.6,
        "text-halo-blur": 0.6,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 4, 0.52, 7, 0.76, 9, 0],
      },
    },
    {
      id: "operator-city-label",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      minzoom: 3,
      filter: ["==", ["get", "class"], "city"],
      layout: {
        "text-field": vectorLabelTextField(),
        "text-font": ["Noto Sans Regular"],
        "text-size": ["interpolate", ["exponential", 1.12], ["zoom"], 3, 10, 6, 13, 10, 18, 14, 22],
        "text-max-width": 9,
        "symbol-sort-key": ["coalesce", ["get", "rank"], 99],
        "text-allow-overlap": false,
        "text-ignore-placement": false,
      },
      paint: {
        "text-color": "#f2f7f4",
        "text-halo-color": "rgba(2, 5, 10, 0.92)",
        "text-halo-width": 1.8,
        "text-halo-blur": 0.55,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.78, 5, 0.95],
      },
    },
    {
      id: "operator-town-label",
      type: "symbol",
      source: "openmaptiles",
      "source-layer": "place",
      minzoom: 6,
      filter: ["==", ["get", "class"], "town"],
      layout: {
        "text-field": vectorLabelTextField(),
        "text-font": ["Noto Sans Regular"],
        "text-size": ["interpolate", ["exponential", 1.1], ["zoom"], 6, 10, 10, 14, 14, 18],
        "text-max-width": 9,
        "symbol-sort-key": ["coalesce", ["get", "rank"], 99],
      },
      paint: {
        "text-color": "#dde5e1",
        "text-halo-color": "rgba(2, 5, 10, 0.9)",
        "text-halo-width": 1.5,
        "text-halo-blur": 0.5,
        "text-opacity": ["interpolate", ["linear"], ["zoom"], 6, 0.64, 8, 0.86],
      },
    },
  ],
};

export function preferredMapRenderer(): "leaflet" | "maplibre" {
  return "maplibre";
}

export function wrapMapLongitude(lon: number) {
  return ((((lon + 180) % 360) + 360) % 360) - 180;
}

export function clampMapLatitude(lat: number) {
  return Math.max(-WEB_MERCATOR_LAT_LIMIT, Math.min(WEB_MERCATOR_LAT_LIMIT, lat));
}

export function wrapMapLatitude(lat: number) {
  return clampMapLatitude(lat);
}

export function clampMapZoom(zoom: number) {
  return Math.max(MAP_MIN_ZOOM, Math.min(MAP_MAX_ZOOM, zoom));
}

export function normalizeMapCenter(center: [number, number]): [number, number] {
  return [wrapMapLatitude(center[0]), wrapMapLongitude(center[1])];
}

export const constrainGlobeTransform: TransformConstrainFunction = (lngLat, zoom) => {
  const center = lngLat.wrap();
  center.lat = clampMapLatitude(center.lat);
  return { center, zoom: clampMapZoom(zoom) };
};

export function toLngLat(point: [number, number]): [number, number] {
  return [wrapMapLongitude(point[1]), wrapMapLatitude(point[0])];
}

export function pointFeature(id: string, point: [number, number], properties: MapGeoJsonFeature["properties"] = {}): MapGeoJsonFeature {
  return {
    type: "Feature",
    properties: { id, ...properties },
    geometry: { type: "Point", coordinates: toLngLat(point) },
  };
}

export function lineFeature(id: string, points: [number, number][], properties: MapGeoJsonFeature["properties"] = {}): MapGeoJsonFeature | null {
  if (points.length < 2) return null;
  return {
    type: "Feature",
    properties: { id, ...properties },
    geometry: { type: "LineString", coordinates: points.map(toLngLat) },
  };
}

export function polygonFeature(id: string, points: [number, number][], properties: MapGeoJsonFeature["properties"] = {}): MapGeoJsonFeature | null {
  if (points.length < 3) return null;
  const closed = [...points, points[0]].map(toLngLat);
  return {
    type: "Feature",
    properties: { id, ...properties },
    geometry: { type: "Polygon", coordinates: [closed] },
  };
}

export function collection(features: Array<MapGeoJsonFeature | null | undefined>): MapGeoJsonCollection {
  return {
    type: "FeatureCollection",
    features: features.filter((feature): feature is MapGeoJsonFeature => Boolean(feature)),
  };
}


export function mapBoundsFromPoints(points: [number, number][]): LngLatBoundsLike | null {
  if (points.length === 0) return null;
  const bounds = pointsBounds(points);
  return [
    [wrapMapLongitude(bounds.lon_min), wrapMapLatitude(bounds.lat_min)],
    [wrapMapLongitude(bounds.lon_max), wrapMapLatitude(bounds.lat_max)],
  ];
}


export function mapLibreSource(map: MapLibreMap, sourceId: string): GeoJSONSource | null {
  const source = map.getSource(sourceId);
  return source && "setData" in source ? (source as GeoJSONSource) : null;
}

