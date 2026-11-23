import type { StyleSpecification } from "maplibre-gl";

export const MAP_MAX_ZOOM = 23;

const VECTOR_LABEL_GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const VECTOR_LABEL_SOURCE_URL = "https://tiles.openfreemap.org/planet";

function vectorLabelTextField(): ["coalesce", ["get", string], ["get", string], ["get", string]] {
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
      attribution: "Imagery © Esri and contributors",
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
