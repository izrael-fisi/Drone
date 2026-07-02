import { useEffect, useMemo, useRef, useState, MutableRefObject } from "react";
import { MapContainer, TileLayer, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet-draw";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { exists, readTextFile } from "@tauri-apps/plugin-fs";
import { homeDir, join } from "@tauri-apps/api/path";
import {
  Download, FileImage, FolderOpen, Layers, Info, CheckCircle2, Loader2, X, FolderInput, Upload,
  Mountain, Search, ShieldAlert, ClipboardCheck,
} from "lucide-react";
import { cmd } from "../lib/tauri";
import { useAppStore } from "../lib/store";
import { generateId, cn, formatMegabytes } from "../lib/utils";
import { proxigo } from "../lib/proxigo";
import type {
  BBox,
  DownloadProgress,
  MapCoverageSurvey,
  MapProvider,
  MapProviderId,
  MapUsageEstimate,
  Region,
} from "../lib/types";

const ESRI_SATELLITE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const USGS_IMAGERY = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}";
const ESRI_LABELS =
  "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}";

function bboxAreaKm2(bbox: BBox): number {
  const latCenter = ((bbox.lat_min + bbox.lat_max) / 2) * (Math.PI / 180);
  const ns = (bbox.lat_max - bbox.lat_min) * 111.32;
  const ew = (bbox.lon_max - bbox.lon_min) * 111.32 * Math.cos(latCenter);
  return Math.abs(ns * ew);
}

function bboxFromPoints(points: [number, number][]): BBox {
  const lats = points.map(([lat]) => lat);
  const lons = points.map(([, lon]) => lon);
  return {
    lat_min: Math.min(...lats),
    lat_max: Math.max(...lats),
    lon_min: Math.min(...lons),
    lon_max: Math.max(...lons),
  };
}

function rectanglePoints(bounds: L.LatLngBounds): [number, number][] {
  return [
    [bounds.getSouth(), bounds.getWest()],
    [bounds.getSouth(), bounds.getEast()],
    [bounds.getNorth(), bounds.getEast()],
    [bounds.getNorth(), bounds.getWest()],
  ];
}

function polygonAreaKm2(points: [number, number][]): number {
  if (points.length < 3) return 0;
  const latCenter = points.reduce((sum, [lat]) => sum + lat, 0) / points.length;
  const cosLat = Math.max(1e-9, Math.abs(Math.cos(latCenter * Math.PI / 180)));
  const projected = points.map(([lat, lon]) => [lon * 111.32 * cosLat, lat * 111.32]);
  let area = 0;
  for (let index = 0; index < projected.length; index += 1) {
    const [x1, y1] = projected[index];
    const [x2, y2] = projected[(index + 1) % projected.length];
    area += x1 * y2 - x2 * y1;
  }
  return Math.abs(area / 2);
}

function orderPolygonPoints(points: [number, number][]) {
  if (points.length < 3) return points;
  const centerLat = points.reduce((sum, [lat]) => sum + lat, 0) / points.length;
  const centerLon = points.reduce((sum, [, lon]) => sum + lon, 0) / points.length;
  const cosLat = Math.max(1e-9, Math.abs(Math.cos(centerLat * Math.PI / 180)));
  return [...points].sort((a, b) => {
    const angleA = Math.atan2(a[0] - centerLat, (a[1] - centerLon) * cosLat);
    const angleB = Math.atan2(b[0] - centerLat, (b[1] - centerLon) * cosLat);
    return angleA - angleB;
  });
}

type DrawMode = "rectangle" | "polygon";
type CutShape = "box" | "polygon";

type CutSelection = {
  bbox: BBox;
  cutShape: CutShape;
  polygonPoints: [number, number][];
};

const FALLBACK_PROVIDERS: MapProvider[] = [
  {
    id: "usgs-imagery",
    label: "USGS Imagery",
    kind: "raster",
    url_template: USGS_IMAGERY,
    tile_scheme: "arcgis",
    attribution: "USGS National Map",
    min_zoom: 0,
    max_native_zoom: 23,
    max_zoom: 23,
    requires_api_key: false,
    coverage_mode: "survey-required-us",
    default_priority: 10,
    enabled: true,
    notes: "Free U.S. imagery. Coverage varies; survey before large downloads.",
    average_tile_kb: 95,
  },
  {
    id: "esri-world-imagery",
    label: "Esri World Imagery",
    kind: "raster",
    url_template: ESRI_SATELLITE,
    tile_scheme: "arcgis",
    attribution: "Esri World Imagery",
    min_zoom: 0,
    max_native_zoom: 23,
    max_zoom: 23,
    requires_api_key: false,
    coverage_mode: "global-fallback",
    default_priority: 20,
    enabled: true,
    notes: "Global satellite fallback. High zoom quality varies by location.",
    average_tile_kb: 80,
  },
];

const BUILT_IN_PROVIDER_LABELS: Record<string, string> = {
  "openfreemap-vector": "Vector labels",
  "usgs-imagery": "USGS",
  "esri-world-imagery": "Esri",
  "mapbox-satellite": "Mapbox",
  "bing-aerial": "Bing",
  "custom-zxy": "Custom ZXY",
  "custom-arcgis": "Custom ArcGIS",
  "custom-wmts": "WMTS",
  pmtiles: "PMTiles",
};

const DRAW_MODES: { mode: DrawMode; label: string; hint: string }[] = [
  { mode: "rectangle", label: "Box Cut", hint: "Drag a box over the map area to save" },
  { mode: "polygon",   label: "N-gon",   hint: "Click boundary points in any order; the outline is cleaned before saving" },
];

const MAP_FILE_EXTENSIONS = ["png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "gif"];
const ELEVATION_FILE_EXTENSIONS = ["tif", "tiff"];
const EARTH_RADIUS_M = 6378137;

function pixelToLatLon(
  originLat: number,
  originLon: number,
  gsdMPerPx: number,
  originPixelX: number,
  originPixelY: number,
  rotationDeg: number,
  xPx: number,
  yPx: number,
): { lat: number; lon: number } {
  const dx = (xPx - originPixelX) * gsdMPerPx;
  const dy = (yPx - originPixelY) * gsdMPerPx;
  const theta = rotationDeg * Math.PI / 180;
  const east = dx * Math.cos(theta) - (-dy) * Math.sin(theta);
  const north = dx * Math.sin(theta) + (-dy) * Math.cos(theta);
  const lat = originLat + (north / EARTH_RADIUS_M) * (180 / Math.PI);
  const lon = originLon + (east / (EARTH_RADIUS_M * Math.max(Math.cos(originLat * Math.PI / 180), 1e-9))) * (180 / Math.PI);
  return { lat, lon };
}

function bboxFromGeoref(
  originLat: number,
  originLon: number,
  gsdMPerPx: number,
  widthPx: number,
  heightPx: number,
  originPixelX = 0,
  originPixelY = 0,
  rotationDeg = 0,
): BBox {
  const corners = [
    pixelToLatLon(originLat, originLon, gsdMPerPx, originPixelX, originPixelY, rotationDeg, 0, 0),
    pixelToLatLon(originLat, originLon, gsdMPerPx, originPixelX, originPixelY, rotationDeg, widthPx, 0),
    pixelToLatLon(originLat, originLon, gsdMPerPx, originPixelX, originPixelY, rotationDeg, widthPx, heightPx),
    pixelToLatLon(originLat, originLon, gsdMPerPx, originPixelX, originPixelY, rotationDeg, 0, heightPx),
  ];
  const lats = corners.map((corner) => corner.lat);
  const lons = corners.map((corner) => corner.lon);
  return {
    lat_min: Math.min(...lats),
    lat_max: Math.max(...lats),
    lon_min: Math.min(...lons),
    lon_max: Math.max(...lons),
  };
}

function isTiffPath(path: string): boolean {
  return /\.(tif|tiff)$/i.test(path);
}

function sourceFromMetadata(value: unknown): Region["source"] {
  if (typeof value !== "string") return "folder";
  if (value === "esri") return "esri-world-imagery";
  if (value === "mapbox") return "mapbox-satellite";
  if (value === "bing") return "bing-aerial";
  if (
    value === "uploaded" ||
    value === "folder" ||
    value === "usgs-imagery" ||
    value === "esri-world-imagery" ||
    value === "mapbox-satellite" ||
    value === "bing-aerial" ||
    value.startsWith("custom-") ||
    value === "pmtiles"
  ) {
    return value;
  }
  return "folder";
}

function defaultImportedOutputPath(filePath: string): string {
  const sep = Math.max(filePath.lastIndexOf("/"), filePath.lastIndexOf("\\"));
  const parent = sep >= 0 ? filePath.slice(0, sep) : ".";
  const filename = sep >= 0 ? filePath.slice(sep + 1) : filePath;
  const stem = filename.replace(/\.[^.]+$/, "") || "uploaded-map";
  return `${parent}/${stem}_drone_region`;
}

function slugifyPathSegment(value: string): string {
  return (value || "flight-region")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || "flight-region";
}

// ── Bing Maps custom tile layer (quadkey addressing) ──────────────────────────
function canonicalProviderId(id: string): MapProviderId {
  if (id === "esri") return "esri-world-imagery";
  if (id === "mapbox") return "mapbox-satellite";
  if (id === "bing") return "bing-aerial";
  return id as MapProviderId;
}

function providerShortLabel(id: string) {
  return BUILT_IN_PROVIDER_LABELS[id] ?? id;
}

function providerApiKeys(profile: ReturnType<typeof useAppStore.getState>["profile"]): Record<string, string> {
  const mapbox = profile?.mapbox_key?.trim() ?? "";
  const bing = profile?.bing_key?.trim() ?? "";
  return {
    "mapbox-satellite": mapbox,
    mapbox,
    "bing-aerial": bing,
    bing,
  };
}

function providerNeedsMissingKey(provider: MapProvider | undefined, apiKeys: Record<string, string>) {
  if (!provider?.requires_api_key) return false;
  return !(apiKeys[provider.id] || apiKeys[String(provider.id).replace(/-(satellite|aerial)$/, "")]);
}

function providerPreviewUrl(providerId: string, apiKey: string) {
  const id = canonicalProviderId(providerId);
  if (id === "usgs-imagery") return USGS_IMAGERY;
  if (id === "mapbox-satellite" && apiKey) {
    return `https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}.jpg90?access_token=${apiKey}`;
  }
  return ESRI_SATELLITE;
}

function providerPreviewAttribution(provider: MapProvider | undefined) {
  return provider?.attribution ?? "Esri World Imagery";
}

function buildProviderOrder(
  selectedProviderId: MapProviderId,
  providers: MapProvider[],
  apiKeys: Record<string, string>,
) {
  const selected = canonicalProviderId(selectedProviderId);
  const ids = new Set<MapProviderId>();
  const addIfReady = (id: MapProviderId) => {
    const provider = providers.find((item) => item.id === id);
    if (!provider || provider.kind === "vector" || !provider.enabled || !provider.url_template) return;
    if (providerNeedsMissingKey(provider, apiKeys)) return;
    ids.add(id);
  };
  addIfReady(selected);
  addIfReady("usgs-imagery");
  addIfReady("esri-world-imagery");
  addIfReady("mapbox-satellite");
  addIfReady("bing-aerial");
  return Array.from(ids);
}

function surveyResultForZoom(survey: MapCoverageSurvey | null, zoom: number) {
  if (!survey) return [];
  const exact = survey.provider_results.filter((result) => result.zoom === zoom);
  if (exact.length) return exact;
  return survey.provider_results.filter((result) => result.zoom === survey.max_zoom);
}

function formatBytesMb(value: number) {
  return formatMegabytes(value);
}

function isDownloadProviderReady(provider: MapProvider, apiKeys: Record<string, string>) {
  return provider.kind !== "vector" && provider.enabled && Boolean(provider.url_template) && !providerNeedsMissingKey(provider, apiKeys);
}

function sumSelectedProviderTotals(estimate: MapUsageEstimate): MapUsageEstimate {
  if (!estimate.provider_breakdown.length) return estimate;
  return {
    ...estimate,
    estimated_source_mb: estimate.provider_breakdown.reduce((sum, provider) => sum + provider.estimated_source_mb, 0),
    estimated_disk_mb: estimate.provider_breakdown.reduce((sum, provider) => sum + provider.estimated_disk_mb, 0),
  };
}

function combineLayeredEstimates(estimates: MapUsageEstimate[]): MapUsageEstimate {
  const ordered = [...estimates].sort((a, b) => a.zoom - b.zoom);
  const highest = ordered[ordered.length - 1];
  if (!highest) throw new Error("No layer estimates available");
  const providerBreakdown = new Map<string, MapUsageEstimate["provider_breakdown"][number]>();
  for (const estimate of ordered) {
    for (const provider of estimate.provider_breakdown) {
      const current = providerBreakdown.get(provider.provider_id);
      if (!current) {
        providerBreakdown.set(provider.provider_id, { ...provider });
      } else {
        current.tile_count += provider.tile_count;
        current.estimated_source_mb += provider.estimated_source_mb;
        current.estimated_disk_mb += provider.estimated_disk_mb;
        current.gsd_m_per_px = provider.gsd_m_per_px;
        current.overzoomed = current.overzoomed || provider.overzoomed;
        current.key_required = current.key_required || provider.key_required;
        current.enabled = current.enabled && provider.enabled;
      }
    }
  }
  const tileCount = ordered.reduce((sum, estimate) => sum + estimate.tile_count, 0);
  return {
    ...highest,
    tile_count: tileCount,
    estimated_source_mb: Array.from(providerBreakdown.values()).reduce((sum, provider) => sum + provider.estimated_source_mb, 0),
    estimated_disk_mb: Array.from(providerBreakdown.values()).reduce((sum, provider) => sum + provider.estimated_disk_mb, 0),
    too_large: ordered.some((estimate) => estimate.too_large),
    over_100_km2: ordered.some((estimate) => estimate.over_100_km2),
    warnings: Array.from(new Set([
      ...ordered.flatMap((estimate) => estimate.warnings),
      `Multi-layer map includes Z${ordered[0].zoom}-Z${highest.zoom}. Size estimate includes every selected zoom level.`,
    ])),
    provider_breakdown: Array.from(providerBreakdown.values()),
  };
}

function BingTileLayer({ apiKey }: { apiKey: string }) {
  const map = useMap();
  useEffect(() => {
    const BingLayer = (L.TileLayer as any).extend({
      getTileUrl(c: any) {
        let qk = "";
        for (let i = c.z; i > 0; i--) {
          let d = 0;
          const m = 1 << (i - 1);
          if (c.x & m) d += 1;
          if (c.y & m) d += 2;
          qk += d;
        }
        const s = (Math.abs(c.x) + Math.abs(c.y)) % 4;
        return `https://t${s}.ssl.ak.tiles.virtualearth.net/tiles/a${qk}.jpeg?g=7&token=${apiKey}`;
      },
    });
    const layer = new BingLayer("", { maxZoom: 20, attribution: "© Microsoft / Bing Maps" });
    layer.addTo(map);
    return () => { map.removeLayer(layer); };
  }, [map, apiKey]);
  return null;
}

// ── Draw handler — direct Leaflet API, no leaflet-draw toolbar ───────────────
// Rectangle: manual mousedown/mousemove/mouseup (L.Draw.Rectangle is unreliable
//            in WebView2 due to pointer-capture behaviour on Windows).
// Triangle/Polygon: L.Draw.Polygon with click-to-place vertices.
// drawKey increments each time a mode button is clicked, forcing a fresh session.
function DrawControlInner({
  onSelectionChange,
  onInvalidPolygon,
  featureGroupRef,
  mode,
  drawKey,
}: {
  onSelectionChange: (selection: CutSelection | null) => void;
  onInvalidPolygon: (message: string) => void;
  featureGroupRef: MutableRefObject<L.FeatureGroup | null>;
  mode: DrawMode;
  drawKey: number;
}) {
  const map = useMap();
  const handlerRef = useRef<{ disable: () => void } | null>(null);

  useEffect(() => {
    if (!featureGroupRef.current) {
      featureGroupRef.current = L.featureGroup().addTo(map);
    }
    const fg = featureGroupRef.current;
    const shapeStyle = { color: "#FF6600", weight: 2, fillOpacity: 0.12 };

    handlerRef.current?.disable();

    if (mode === "rectangle") {
      const container = map.getContainer();
      container.style.cursor = "crosshair";

      let startLatLng: L.LatLng | null = null;
      let previewRect: L.Rectangle | null = null;

      const onMouseDown = (e: L.LeafletMouseEvent) => {
        startLatLng = e.latlng;
        map.dragging.disable();
        fg.clearLayers();
        onSelectionChange(null);
      };

      const onMouseMove = (e: L.LeafletMouseEvent) => {
        if (!startLatLng) return;
        if (previewRect) fg.removeLayer(previewRect);
        previewRect = L.rectangle(
          [
            [startLatLng.lat, startLatLng.lng],
            [e.latlng.lat, e.latlng.lng],
          ],
          shapeStyle,
        );
        fg.addLayer(previewRect);
      };

      const onMouseUp = (e: L.LeafletMouseEvent) => {
        if (!startLatLng) return;
        map.dragging.enable();
        const bounds = L.latLngBounds(startLatLng, e.latlng);
        if (bounds.getNorth() !== bounds.getSouth()) {
          const points = rectanglePoints(bounds);
          onSelectionChange({
            bbox: bboxFromPoints(points),
            cutShape: "box",
            polygonPoints: points,
          });
        }
        startLatLng = null;
        previewRect = null;
      };

      map.on("mousedown", onMouseDown);
      map.on("mousemove", onMouseMove);
      map.on("mouseup", onMouseUp);

      handlerRef.current = {
        disable: () => {
          map.off("mousedown", onMouseDown);
          map.off("mousemove", onMouseMove);
          map.off("mouseup", onMouseUp);
          map.dragging.enable();
          container.style.cursor = "";
        },
      };
    } else {
      const handler = new (L.Draw as any).Polygon(map, {
        shapeOptions: shapeStyle,
        allowIntersection: true,
        showArea: false,
      });
      handler.enable();

      const onDrawStart = () => undefined;
      const onDrawVertex = () => undefined;
      const onCreate = (e: any) => {
        const latLngs = (e.layer.getLatLngs?.()[0] ?? []) as L.LatLng[];
        const points = orderPolygonPoints(latLngs.map((point) => [point.lat, point.lng] as [number, number]));
        if (points.length < 3) {
          fg.clearLayers();
          onSelectionChange(null);
          onInvalidPolygon("Place at least 3 points before saving an n-gon boundary.");
          return;
        }
        fg.clearLayers();
        fg.addLayer(L.polygon(points, shapeStyle));
        onSelectionChange({
          bbox: bboxFromPoints(points),
          cutShape: "polygon",
          polygonPoints: points,
        });
      };

      map.on(L.Draw.Event.DRAWSTART,  onDrawStart);
      map.on(L.Draw.Event.DRAWVERTEX, onDrawVertex);
      map.on(L.Draw.Event.CREATED,    onCreate);

      handlerRef.current = {
        disable: () => {
          handler.disable();
          map.off(L.Draw.Event.DRAWSTART,  onDrawStart);
          map.off(L.Draw.Event.DRAWVERTEX, onDrawVertex);
          map.off(L.Draw.Event.CREATED,    onCreate);
        },
      };
    }

    return () => { handlerRef.current?.disable(); };
  }, [map, mode, drawKey, onSelectionChange, onInvalidPolygon, featureGroupRef]);

  return null;
}

// ── Main Maps page ────────────────────────────────────────────────────────────
export function Maps() {
  const { profile, regions, setRegions, addRegion, proxigoSession, cloudAccount, setCloudAccount } = useAppStore();
  const featureGroupRef = useRef<L.FeatureGroup | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const estimateRef = useRef<HTMLDivElement>(null);

  const [providers,  setProviders]  = useState<MapProvider[]>(FALLBACK_PROVIDERS);
  const [source,     setSource]     = useState<MapProviderId>("usgs-imagery");
  const [selectedProviderIds,setSelectedProviderIds]= useState<MapProviderId[]>([]);
  const [drawMode,   setDrawMode]   = useState<DrawMode>("rectangle");
  const [drawKey,    setDrawKey]    = useState(0);
  const [bbox,       setBbox]       = useState<BBox | null>(null);
  const [cutShape,   setCutShape]   = useState<CutShape>("box");
  const [polygonPoints,setPolygonPoints]= useState<[number, number][]>([]);
  const [zoom,       setZoom]       = useState(18);
  const [multiLayerMap,setMultiLayerMap]= useState(false);
  const [regionName, setRegionName] = useState("Flight Region");
  const [outputDir,  setOutputDir]  = useState("");
  const [defaultOutputRoot,setDefaultOutputRoot]= useState("");
  const [customOutputDir,setCustomOutputDir]= useState(false);
  const [estimate,   setEstimate]   = useState<MapUsageEstimate | null>(null);
  const [estimating, setEstimating] = useState(false);
  const [coverageSurvey,setCoverageSurvey]= useState<MapCoverageSurvey | null>(null);
  const [surveying,  setSurveying]  = useState(false);
  const [confirmLargeArea,setConfirmLargeArea]= useState(false);
  const [downloading,setDownloading]= useState(false);
  const [importingMap,setImportingMap]= useState(false);
  const [progress,   setProgress]   = useState<DownloadProgress | null>(null);
  const [done,       setDone]       = useState(false);
  const [doneMessage,setDoneMessage]= useState("Map source added to library.");
  const [error,      setError]      = useState<string | null>(null);
  const [mapFilePath,setMapFilePath]= useState("");
  const [mapImportName,setMapImportName]= useState("Uploaded Map");
  const [mapImportOutputDir,setMapImportOutputDir]= useState("");
  const [customMapImportOutput,setCustomMapImportOutput]= useState(false);
  const [mapOriginLat,setMapOriginLat]= useState("");
  const [mapOriginLon,setMapOriginLon]= useState("");
  const [mapGsd,setMapGsd]= useState("");
  const [mapRotationDeg,setMapRotationDeg]= useState("0");
  const [elevationRegionId,setElevationRegionId]= useState("");
  const [demFilePath,setDemFilePath]= useState("");
  const [dsmFilePath,setDsmFilePath]= useState("");
  const [importingElevation,setImportingElevation]= useState(false);
  const [usageReportStatus,setUsageReportStatus]= useState<"idle"|"reporting"|"ok"|"error">("idle");
  const [usageReportError,setUsageReportError]= useState<string|null>(null);

  const apiKeys = useMemo(() => providerApiKeys(profile), [profile]);
  const sourceConfig = providers.find((provider) => provider.id === canonicalProviderId(source)) ?? providers[0];
  const activeApiKey = apiKeys[sourceConfig?.id ?? ""] ?? "";
  const readyDownloadProviders = useMemo(
    () => providers.filter((provider) => provider.kind !== "vector" && provider.enabled && Boolean(provider.url_template)),
    [providers],
  );
  const providerOrder = useMemo(
    () => selectedProviderIds.filter((id) => {
      const provider = readyDownloadProviders.find((item) => item.id === canonicalProviderId(id));
      return provider && isDownloadProviderReady(provider, apiKeys);
    }),
    [apiKeys, readyDownloadProviders, selectedProviderIds],
  );
  const missingKey = providerNeedsMissingKey(sourceConfig, apiKeys);
  const surveyRows = surveyResultForZoom(coverageSurvey, zoom);
  const maxMapAreaKm2 = profile?.max_map_area_km2;
  const maxDownloadSizeGb = profile?.max_map_download_size_gb ?? 20;
  // Cloud quota — use org pool when in an org, personal limit otherwise
  const orgCtx = cloudAccount?.org ?? null;
  const cloudKm2Used   = orgCtx ? orgCtx.org_km2_used   : (cloudAccount?.km2_used   ?? 0);
  const cloudKm2Limit  = orgCtx ? orgCtx.org_km2_limit  : (cloudAccount?.km2_limit  ?? 0);
  const cloudKm2Remaining = cloudKm2Limit > 0 ? Math.max(0, cloudKm2Limit - cloudKm2Used) : null;
  // Per-member allowance (org only): cap effective remaining by personal allowance if set
  const myAllowanceRemaining = orgCtx?.my_km2_allowance != null
    ? Math.max(0, orgCtx.my_km2_allowance - orgCtx.my_km2_used)
    : null;
  const effectiveRemaining = myAllowanceRemaining !== null
    ? Math.min(cloudKm2Remaining ?? Infinity, myAllowanceRemaining)
    : cloudKm2Remaining;
  const exceedsCloudQuota = effectiveRemaining !== null && estimate !== null && estimate.area_km2 > effectiveRemaining;
  const mapLimitWarnings = useMemo(() => {
    if (!estimate) return [];
    const warnings: string[] = [];
    if (typeof maxMapAreaKm2 === "number" && maxMapAreaKm2 > 0 && estimate.area_km2 > maxMapAreaKm2) {
      warnings.push(`Map area ${estimate.area_km2.toFixed(2)} km² exceeds settings limit ${maxMapAreaKm2.toFixed(2)} km².`);
    }
    if (typeof maxDownloadSizeGb === "number" && maxDownloadSizeGb > 0 && estimate.estimated_disk_mb > maxDownloadSizeGb * 1024) {
      warnings.push(`Estimated disk size ${formatMegabytes(estimate.estimated_disk_mb)} exceeds settings limit ${formatMegabytes(maxDownloadSizeGb * 1024)}.`);
    }
    return warnings;
  }, [estimate, maxDownloadSizeGb, maxMapAreaKm2]);
  const requiresDownloadConfirmation = Boolean(estimate && (estimate.over_100_km2 || estimate.too_large || mapLimitWarnings.length > 0));
  const downloadBlocked = !bbox || !outputDir || downloading || !providerOrder.length || exceedsCloudQuota || (requiresDownloadConfirmation && !confirmLargeArea && !exceedsCloudQuota);
  const currentMode = DRAW_MODES.find((d) => d.mode === drawMode)!;
  const elevationRegion = regions.find((region) => region.id === elevationRegionId) ?? regions[0];

  useEffect(() => {
    cmd.listMapProviders()
      .then((items) => {
        const rasterReady = items.filter((provider) => provider.kind !== "vector");
        setProviders(rasterReady.length ? items : FALLBACK_PROVIDERS);
      })
      .catch(() => setProviders(FALLBACK_PROVIDERS));
  }, []);

  useEffect(() => {
    if (!readyDownloadProviders.length) return;
    setSelectedProviderIds((current) => {
      const validCurrent = current.filter((id) => {
        const provider = readyDownloadProviders.find((item) => item.id === canonicalProviderId(id));
        return provider && isDownloadProviderReady(provider, apiKeys);
      });
      if (validCurrent.length) return validCurrent;
      const defaults = buildProviderOrder(source, readyDownloadProviders, apiKeys)
        .filter((id) => id === "usgs-imagery" || id === "esri-world-imagery");
      if (defaults.length) return defaults;
      return readyDownloadProviders
        .filter((provider) => isDownloadProviderReady(provider, apiKeys))
        .slice(0, 1)
        .map((provider) => provider.id);
    });
  }, [apiKeys, readyDownloadProviders, source]);

  useEffect(() => {
    if (!requiresDownloadConfirmation) setConfirmLargeArea(false);
  }, [requiresDownloadConfirmation]);

  // Fetch cloud account on mount and refresh every 60s to keep org usage current
  useEffect(() => {
    if (!proxigoSession) return;
    proxigo.getAccount(proxigoSession).then(setCloudAccount).catch(() => {});
    const id = setInterval(() => {
      proxigo.getAccount(proxigoSession).then(setCloudAccount).catch(() => {});
    }, 60_000);
    return () => clearInterval(id);
  }, [proxigoSession, setCloudAccount]);

  // Auto-scroll panel to estimate section when bbox is drawn
  useEffect(() => {
    if (bbox && estimateRef.current && panelRef.current) {
      setTimeout(() => {
        const panel = panelRef.current!;
        const el = estimateRef.current!;
        panel.scrollTo({ top: el.offsetTop - 8, behavior: "smooth" });
      }, 150);
    }
  }, [bbox]);

  useEffect(() => {
    if (!bbox) {
      setEstimate(null);
      setEstimating(false);
      setCoverageSurvey(null);
      setConfirmLargeArea(false);
      return;
    }
    if (!providerOrder.length) {
      setEstimate(null);
      setEstimating(false);
      setCoverageSurvey(null);
      setConfirmLargeArea(false);
      return;
    }
    setEstimate(null);
    setEstimating(true);
    const zoomLevels = multiLayerMap
      ? Array.from({ length: zoom - 15 + 1 }, (_, index) => 15 + index)
      : [zoom];
    let cancelled = false;
    Promise.all(zoomLevels.map((zoomLevel) => cmd.estimateMapUsage({
        bbox,
        zoom: zoomLevel,
        cut_shape: cutShape,
        polygon_points: cutShape === "polygon" ? polygonPoints : undefined,
        provider_ids: providerOrder,
        api_keys: apiKeys,
      })))
      .then((items) => {
        if (!cancelled) {
          setEstimate(multiLayerMap ? combineLayeredEstimates(items) : sumSelectedProviderTotals(items[0]));
          setEstimating(false);
        }
      })
      .catch((err) => {
        if (!cancelled) { console.error(err); setEstimating(false); }
      });
    return () => { cancelled = true; };
  }, [bbox, zoom, multiLayerMap, cutShape, polygonPoints, providerOrder, apiKeys]);

  useEffect(() => {
    const loadDefaultOutputRoot = async () => {
      try {
        const home = await homeDir();
        const root = await join(home, "DroneVisionNav", "maps");
        setDefaultOutputRoot(root);
      } catch {
        setDefaultOutputRoot("~/DroneVisionNav/maps");
      }
    };
    loadDefaultOutputRoot();
  }, []);

  useEffect(() => {
    if (!defaultOutputRoot || customOutputDir) return;
    setOutputDir(`${defaultOutputRoot}/${slugifyPathSegment(regionName)}`);
  }, [defaultOutputRoot, regionName, customOutputDir]);

  useEffect(() => {
    if (!defaultOutputRoot || customMapImportOutput || !mapFilePath) return;
    setMapImportOutputDir(`${defaultOutputRoot}/${slugifyPathSegment(mapImportName || "uploaded-map")}`);
  }, [defaultOutputRoot, mapImportName, mapFilePath, customMapImportOutput]);

  useEffect(() => {
    if (!regions.length) {
      setElevationRegionId("");
      return;
    }
    if (!regions.some((region) => region.id === elevationRegionId)) {
      setElevationRegionId(regions[0].id);
    }
  }, [regions, elevationRegionId]);

  const handleSourceChange = (s: MapProviderId) => {
    const provider = providers.find((item) => item.id === canonicalProviderId(s));
    setSource(s);
    setCoverageSurvey(null);
    setConfirmLargeArea(false);
    if (provider && zoom > provider.max_zoom) setZoom(provider.max_zoom);
  };

  const toggleDownloadProvider = (providerId: MapProviderId) => {
    setSelectedProviderIds((current) => (
      current.includes(providerId)
        ? current.filter((id) => id !== providerId)
        : [...current, providerId]
    ));
    setCoverageSurvey(null);
    setConfirmLargeArea(false);
  };

  // Clicking a mode button always triggers a fresh draw session (drawKey++) and
  // clears the existing selection, even if the mode hasn't changed.
  const handleModeChange = (m: DrawMode) => {
    setDrawMode(m);
    setDrawKey((k) => k + 1);
    setBbox(null);
    setCutShape(m === "polygon" ? "polygon" : "box");
    setPolygonPoints([]);
    setDone(false);
    setError(null);
    featureGroupRef.current?.clearLayers();
  };

  const clearSelection = () => {
    setBbox(null);
    setCutShape(drawMode === "polygon" ? "polygon" : "box");
    setPolygonPoints([]);
    setEstimate(null);
    setCoverageSurvey(null);
    setConfirmLargeArea(false);
    setDone(false);
    setError(null);
    featureGroupRef.current?.clearLayers();
    // Re-enable drawing after clearing
    setDrawKey((k) => k + 1);
  };

  const handlePickDir = async () => {
    const dir = await open({ directory: true, multiple: false, title: "Select output folder" });
    if (dir) {
      setOutputDir(dir as string);
      setCustomOutputDir(true);
    }
  };

  const handlePickMapFile = async () => {
    const file = await open({
      multiple: false,
      title: "Select map image",
      filters: [{ name: "Map/image files", extensions: MAP_FILE_EXTENSIONS }],
    });
    if (!file || typeof file !== "string") return;
    setMapFilePath(file);
    const filename = file.split(/[/\\]/).pop() ?? "Uploaded Map";
    const stem = filename.replace(/\.[^.]+$/, "") || "Uploaded Map";
    if (!mapImportName || mapImportName === "Uploaded Map") setMapImportName(stem);
    if (!mapImportOutputDir && !defaultOutputRoot) setMapImportOutputDir(defaultImportedOutputPath(file));
    setDone(false);
    setError(null);
  };

  const handlePickMapOutputDir = async () => {
    const dir = await open({ directory: true, multiple: false, title: "Select imported map output folder" });
    if (dir && typeof dir === "string") {
      setMapImportOutputDir(dir);
      setCustomMapImportOutput(true);
    }
  };

  const pickElevationFile = async (kind: "dem" | "dsm") => {
    const file = await open({
      multiple: false,
      title: kind === "dem" ? "Select DEM GeoTIFF" : "Select DSM GeoTIFF",
      filters: [{ name: "Elevation GeoTIFF", extensions: ELEVATION_FILE_EXTENSIONS }],
    });
    if (!file || typeof file !== "string") return;
    if (kind === "dem") setDemFilePath(file);
    else setDsmFilePath(file);
    setDone(false);
    setError(null);
  };

  const importFromFolder = async () => {
    const dir = await open({ directory: true, multiple: false, title: "Select region folder (must contain metadata.json)" });
    if (!dir) return;
    const folder = dir as string;
    try {
      const text = await readTextFile(`${folder}/metadata.json`);
      if (!(await exists(`${folder}/satellite.png`))) {
        throw new Error("Selected folder is missing satellite.png");
      }
      const meta = JSON.parse(text);
      if (meta.origin_lat == null || meta.origin_lon == null || !meta.gsd_m_per_px || !meta.width_px || !meta.height_px) {
        throw new Error("metadata.json is missing required fields (origin_lat, origin_lon, gsd_m_per_px, width_px, height_px)");
      }
      const {
        origin_lat,
        origin_lon,
        gsd_m_per_px,
        width_px,
        height_px,
        origin_pixel_x = 0,
        origin_pixel_y = 0,
        rotation_deg = 0,
        georef_source,
        georef_confidence,
        georef_crs,
        zoom: z = 0,
        source: src = "folder",
        cut_shape,
        polygon_points,
        elevation_assets,
      } = meta;
      const bbox = bboxFromGeoref(
        origin_lat,
        origin_lon,
        gsd_m_per_px,
        width_px,
        height_px,
        origin_pixel_x,
        origin_pixel_y,
        rotation_deg,
      );
      const centerLat = (bbox.lat_min + bbox.lat_max) / 2;
      const centerLon = (bbox.lon_min + bbox.lon_max) / 2;
      const locationLabel = await reverseGeocode(centerLat, centerLon);
      const folderName = folder.split(/[/\\]/).pop() ?? "Imported Region";
      const region: Region = {
        id: generateId(),
        name: folderName,
        ...bbox,
        zoom: z,
        source: sourceFromMetadata(src),
        cut_shape: cut_shape === "polygon" ? "polygon" : cut_shape === "box" ? "box" : undefined,
        polygon_points: Array.isArray(polygon_points) ? polygon_points : undefined,
        output_path: folder,
        last_downloaded: new Date().toISOString(),
        gsd_m_per_px,
        georef_source,
        georef_confidence,
        georef_crs,
        location_label: locationLabel,
        elevation_dem_path: elevation_assets?.dem,
        elevation_dsm_path: elevation_assets?.dsm,
        elevation_asset_count: Number(Boolean(elevation_assets?.dem)) + Number(Boolean(elevation_assets?.dsm)),
        lifecycle_state: "local",
      };
      addRegion(region);
      await cmd.saveRegions([...regions, region]);
      setDone(true);
      setDoneMessage("Existing map folder imported into the map library.");
      setError(null);
    } catch (e) {
      setError(`Import failed: ${e}`);
    }
  };

  const handleImportElevationAssets = async () => {
    const target = elevationRegion;
    if (!target) {
      setError("Add or import a map source before attaching elevation assets.");
      return;
    }
    if (!demFilePath && !dsmFilePath) {
      setError("Choose a DEM or DSM GeoTIFF first.");
      return;
    }
    setImportingElevation(true);
    setDone(false);
    setError(null);
    try {
      const result = await cmd.importElevationAssets({
        region_dir: target.output_path,
        dem_path: demFilePath || undefined,
        dsm_path: dsmFilePath || undefined,
      });
      const next = regions.map((region) => region.id === target.id
        ? {
            ...region,
            elevation_dem_path: result.dem_path,
            elevation_dsm_path: result.dsm_path,
            elevation_asset_count: result.asset_count,
          }
        : region
      );
      setRegions(next);
      await cmd.saveRegions(next);
      setElevationRegionId(target.id);
      setDone(true);
      setDoneMessage(`Elevation assets attached to ${target.name}.`);
    } catch (e) {
      setError(String(e));
    } finally {
      setImportingElevation(false);
    }
  };

  const handleImportMapFile = async () => {
    const originLat = Number(mapOriginLat);
    const originLon = Number(mapOriginLon);
    const gsd = Number(mapGsd);
    const rotationDeg = Number(mapRotationDeg || "0");
    if (!mapFilePath || !mapImportOutputDir) {
      setError("Choose a map file and output folder first.");
      return;
    }
    const hasManualGeoref = !!mapOriginLat.trim() || !!mapOriginLon.trim() || !!mapGsd.trim();
    if (hasManualGeoref) {
      if (!Number.isFinite(originLat) || originLat < -90 || originLat > 90) {
        setError("Origin latitude must be between -90 and 90.");
        return;
      }
      if (!Number.isFinite(originLon) || originLon < -180 || originLon > 180) {
        setError("Origin longitude must be between -180 and 180.");
        return;
      }
      if (!Number.isFinite(gsd) || gsd <= 0) {
        setError("GSD must be greater than zero.");
        return;
      }
    } else if (!isTiffPath(mapFilePath)) {
      setError("Enter origin latitude, origin longitude, and GSD for non-GeoTIFF map images.");
      return;
    }
    if (!Number.isFinite(rotationDeg)) {
      setError("Rotation must be a valid number.");
      return;
    }

    setImportingMap(true);
    setDone(false);
    setError(null);
    try {
      const result = await cmd.importMapFile({
        map_path: mapFilePath,
        output_dir: mapImportOutputDir,
        name: mapImportName || "Uploaded Map",
        origin_lat: hasManualGeoref ? originLat : undefined,
        origin_lon: hasManualGeoref ? originLon : undefined,
        gsd_m_per_px: hasManualGeoref ? gsd : undefined,
        origin_pixel_x: hasManualGeoref ? 0 : undefined,
        origin_pixel_y: hasManualGeoref ? 0 : undefined,
        rotation_deg: hasManualGeoref ? rotationDeg : undefined,
      });
      const bbox = bboxFromGeoref(
        result.origin_lat,
        result.origin_lon,
        result.gsd_m_per_px,
        result.width_px,
        result.height_px,
        result.origin_pixel_x,
        result.origin_pixel_y,
        result.rotation_deg,
      );
      const centerLat = (bbox.lat_min + bbox.lat_max) / 2;
      const centerLon = (bbox.lon_min + bbox.lon_max) / 2;
      const locationLabel = await reverseGeocode(centerLat, centerLon);
      const region: Region = {
        id: generateId(),
        name: mapImportName || "Uploaded Map",
        ...bbox,
        zoom: 0,
        source: "uploaded",
        output_path: result.output_dir,
        last_downloaded: new Date().toISOString(),
        gsd_m_per_px: result.gsd_m_per_px,
        georef_source: result.georef_source,
        georef_confidence: result.georef_confidence,
        georef_crs: result.georef_crs,
        location_label: locationLabel,
        lifecycle_state: "local",
      };
      addRegion(region);
      await cmd.saveRegions([...regions, region]);
      setDone(true);
      setDoneMessage(
        result.georef_source === "geotiff_embedded"
          ? `GeoTIFF georeference detected (${result.georef_crs ?? "CRS unknown"}), converted, and added to the map library.`
          : "Uploaded map converted with manual georeference and added to the map library."
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setImportingMap(false);
    }
  };

  const reverseGeocode = async (lat: number, lon: number): Promise<string | undefined> => {
    try {
      const res = await fetch(
        `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json&zoom=10`,
        { headers: { "Accept-Language": "en", "User-Agent": "Drone Vision Nav Desktop/0.1" } }
      );
      const j = await res.json();
      const a = j.address ?? {};
      const city = a.city ?? a.town ?? a.village ?? a.county ?? a.state_district ?? a.state ?? "";
      const country = a.country ?? "";
      return city && country ? `${city}, ${country}` : country || city || undefined;
    } catch {
      return undefined;
    }
  };

  const handleSurveyCoverage = async () => {
    if (!bbox || !providerOrder.length) return;
    setSurveying(true);
    setError(null);
    setCoverageSurvey(null);
    try {
      const survey = await cmd.surveyMapCoverage({
        bbox,
        min_zoom: Math.max(0, zoom - 2),
        max_zoom: zoom,
        cut_shape: cutShape,
        polygon_points: cutShape === "polygon" ? polygonPoints : undefined,
        provider_ids: providerOrder,
        sample_budget: estimate?.tile_count && estimate.tile_count < 24 ? estimate.tile_count : 24,
        api_keys: apiKeys,
      });
      setCoverageSurvey(survey);
    } catch (e) {
      setError(`Coverage survey failed: ${e}`);
    } finally {
      setSurveying(false);
    }
  };

  const handleDownload = async () => {
    if (!bbox || !outputDir || !providerOrder.length) return;
    setDownloading(true);
    setDone(false);
    setError(null);
    setProgress(null);
    const unlisten = await listen<DownloadProgress>("tile-progress", (e) => setProgress(e.payload));
    try {
      const result = await cmd.downloadMapRegion({
        bbox,
        zoom,
        min_zoom: multiLayerMap ? 15 : zoom,
        multi_layer_map: multiLayerMap,
        output_dir: outputDir,
        cut_shape: cutShape,
        polygon_points: cutShape === "polygon" ? polygonPoints : undefined,
        provider_ids: providerOrder,
        api_keys: apiKeys,
        coverage_survey: coverageSurvey,
        confirm_over_100_km2: confirmLargeArea,
        allow_large_tile_count: confirmLargeArea,
      });
      const centerLat = (bbox.lat_min + bbox.lat_max) / 2;
      const centerLon = (bbox.lon_min + bbox.lon_max) / 2;
      const locationLabel = await reverseGeocode(centerLat, centerLon);
      const region: Region = {
        id: generateId(),
        name: regionName || "Unnamed Region",
        ...bbox,
        zoom,
        source,
        cut_shape: cutShape,
        polygon_points: polygonPoints,
        output_path: outputDir,
        last_downloaded: new Date().toISOString(),
        tile_count: result.tile_count,
        gsd_m_per_px: result.gsd_m_per_px,
        georef_source: result.georef_source,
        georef_confidence: result.georef_confidence,
        georef_crs: result.georef_crs,
        file_size_mb: result.actual_mb ?? estimate?.estimated_disk_mb,
        min_zoom: multiLayerMap ? 15 : zoom,
        zoom_levels: multiLayerMap ? Array.from({ length: zoom - 15 + 1 }, (_, index) => 15 + index) : [zoom],
        multi_layer_map: multiLayerMap,
        location_label: locationLabel,
        lifecycle_state: "local",
      };
      addRegion(region);
      const next = [...regions, region];
      await cmd.saveRegions(next);
      setDone(true);
      setDoneMessage("Satellite mosaic saved and added to the map library.");

      // Report usage to Proxigo cloud
      const moduleSerial = profile?.proxigo_module_serial;
      if (proxigoSession && moduleSerial && estimate) {
        setUsageReportStatus("reporting");
        setUsageReportError(null);

        // Optimistically update the displayed quota immediately
        if (cloudAccount) {
          const delta = estimate.area_km2;
          const updated = orgCtx
            ? {
                ...cloudAccount,
                org: {
                  ...orgCtx,
                  org_km2_used: orgCtx.org_km2_used + delta,
                  org_km2_remaining: Math.max(0, orgCtx.org_km2_remaining - delta),
                  my_km2_used: orgCtx.my_km2_used + delta,
                },
              }
            : {
                ...cloudAccount,
                km2_used: cloudAccount.km2_used + delta,
                km2_remaining: Math.max(0, cloudAccount.km2_remaining - delta),
              };
          setCloudAccount(updated);
        }

        proxigo.reportMapDownload(
          proxigoSession,
          estimate.area_km2,
          moduleSerial,
          region.id,
          bbox,
          locationLabel ?? undefined
        )
          .then(() => {
            setUsageReportStatus("ok");
            // Confirm with real server values
            return proxigo.getAccount(proxigoSession).then(setCloudAccount).catch(() => {});
          })
          .catch((err) => {
            const msg = err instanceof Error ? err.message : String(err);
            setUsageReportStatus("error");
            setUsageReportError(msg);
            // Roll back optimistic update on failure
            proxigo.getAccount(proxigoSession).then(setCloudAccount).catch(() => {});
            console.warn("[proxigo] usage report failed:", msg);
          });
      } else if (proxigoSession && !moduleSerial) {
        setUsageReportStatus("error");
        setUsageReportError("No module serial set in Account — go to Account tab and set your Proxigo Module Serial.");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setDownloading(false);
      unlisten();
    }
  };

  return (
    <div className="ops-screen-bg flex h-full animate-fade-in gap-3 overflow-hidden p-3">
      {/* Map */}
      <div className="glass-panel panel-3d-center relative flex-1 overflow-hidden border border-border">
        <div className="glass-panel absolute left-4 top-4 z-[650] px-3 py-2">
          <div className="font-label-caps text-label-caps text-slate-300">MAP SOURCE PLANNER</div>
          <div className="font-data-mono text-[10px] text-slate-500">survey // estimate // patch // download</div>
        </div>
        <MapContainer center={[37.775, -122.418]} zoom={14} minZoom={3} className="w-full h-full" zoomControl>
          {canonicalProviderId(source) !== "bing-aerial" || missingKey ? (
            <TileLayer
              url={providerPreviewUrl(source, activeApiKey)}
              attribution={providerPreviewAttribution(sourceConfig)}
              maxZoom={sourceConfig?.max_zoom ?? 23}
              maxNativeZoom={sourceConfig?.max_native_zoom ?? 23}
            />
          ) : (
            <BingTileLayer apiKey={activeApiKey} />
          )}
          {/* Labels / roads / city names overlay — free, no key, always shown */}
          <TileLayer url={ESRI_LABELS} attribution="" maxZoom={23} opacity={0.85} />

          <DrawControlInner
            onSelectionChange={(selection) => {
              setBbox(selection?.bbox ?? null);
              setCutShape(selection?.cutShape ?? (drawMode === "polygon" ? "polygon" : "box"));
              setPolygonPoints(selection?.polygonPoints ?? []);
              setCoverageSurvey(null);
              setConfirmLargeArea(false);
            }}
            onInvalidPolygon={setError}
            featureGroupRef={featureGroupRef}
            mode={drawMode}
            drawKey={drawKey}
          />
        </MapContainer>

        {/* Floating hint */}
        {!bbox && (
          <div className="glass-panel pointer-events-none absolute bottom-6 left-1/2 -translate-x-1/2 px-4 py-2 font-data-mono text-xs text-slate-400">
            {currentMode.hint}
          </div>
        )}
      </div>

      {/* Side panel */}
      <div ref={panelRef} className="glass-panel panel-3d-right flex w-80 flex-col overflow-y-auto">
        <div className="border-b border-border bg-bg-card px-5 py-4">
          <h2 className="font-label-caps text-label-caps text-slate-300">MAP SOURCE PLANNER</h2>
          <p className="mt-1 font-data-mono text-[10px] text-slate-500">
            Choose providers, survey coverage, then patch the best available imagery.
          </p>
        </div>

        {/* Proxigo quota — always visible */}
        <div className="border-b border-border bg-bg-card px-4 py-3 space-y-2">
          {/* Header row */}
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-widest text-orange-400">
              {orgCtx ? orgCtx.org_name : "Proxigo Quota"}
            </span>
            {cloudAccount && (orgCtx || cloudAccount.plan) && (
              <span className="rounded border border-orange-500/30 bg-orange-500/10 px-1.5 py-0.5 text-[10px] font-medium text-orange-300">
                {orgCtx
                  ? `${orgCtx.org_plan.charAt(0).toUpperCase() + orgCtx.org_plan.slice(1)} · ${orgCtx.role}`
                  : cloudAccount.plan!.charAt(0).toUpperCase() + cloudAccount.plan!.slice(1)}
              </span>
            )}
          </div>

          {/* Quota numbers */}
          {cloudAccount ? (
            <>
              <div className="grid grid-cols-3 gap-1 text-center">
                <div className="rounded bg-bg-elevated px-2 py-1.5">
                  <div className="text-xs font-bold font-mono text-slate-100">{cloudKm2Used.toFixed(1)}</div>
                  <div className="text-[9px] text-slate-500 mt-0.5">used</div>
                </div>
                <div className="rounded bg-bg-elevated px-2 py-1.5">
                  <div className="text-xs font-bold font-mono text-slate-100">{cloudKm2Limit > 0 ? cloudKm2Limit : "—"}</div>
                  <div className="text-[9px] text-slate-500 mt-0.5">km² plan</div>
                </div>
                <div className={cn("rounded px-2 py-1.5", exceedsCloudQuota ? "bg-red-500/15" : "bg-emerald-500/10")}>
                  <div className={cn("text-xs font-bold font-mono", exceedsCloudQuota ? "text-red-400" : "text-emerald-400")}>
                    {cloudKm2Limit > 0 ? (effectiveRemaining ?? 0).toFixed(1) : "∞"}
                  </div>
                  <div className="text-[9px] text-slate-500 mt-0.5">remaining</div>
                </div>
              </div>

              {/* Per-member allowance */}
              {orgCtx?.my_km2_allowance != null && (
                <div className="text-[10px] text-slate-400">
                  Your allowance: <span className="text-slate-200 font-mono">{orgCtx.my_km2_used.toFixed(1)} / {orgCtx.my_km2_allowance} km²</span>
                </div>
              )}

              {/* No plan warning */}
              {cloudKm2Limit === 0 && !orgCtx && (
                <div className="text-[10px] text-amber-400">
                  No active plan — usage is unlimited but untracked. Subscribe at proxigo.us.
                </div>
              )}

              {/* Usage report status */}
              {usageReportStatus === "reporting" && (
                <div className="flex items-center gap-1.5 text-[10px] text-slate-400">
                  <Loader2 size={10} className="animate-spin shrink-0" /> Reporting usage…
                </div>
              )}
              {usageReportStatus === "ok" && (
                <div className="flex items-center gap-1.5 text-[10px] text-emerald-400">
                  <CheckCircle2 size={10} className="shrink-0" /> Usage recorded
                </div>
              )}
              {usageReportStatus === "error" && (
                <div className="text-[10px] text-red-400">
                  ⚠ Usage not recorded: {usageReportError}
                </div>
              )}

              {/* Impact of current selection */}
              {estimate && cloudKm2Limit > 0 && effectiveRemaining !== null && (
                <div className={cn(
                  "rounded px-2.5 py-1.5 text-[10px] font-medium",
                  exceedsCloudQuota
                    ? "bg-red-500/15 text-red-400"
                    : "bg-bg-elevated text-slate-400"
                )}>
                  {exceedsCloudQuota
                    ? `⚠ Selection (${estimate.area_km2.toFixed(1)} km²) exceeds remaining quota`
                    : `This download: ${estimate.area_km2.toFixed(1)} km² → ${(effectiveRemaining - estimate.area_km2).toFixed(1)} km² left after`}
                </div>
              )}
            </>
          ) : proxigoSession ? (
            <div className="text-[10px] text-slate-500 animate-pulse">Fetching quota…</div>
          ) : (
            <div className="text-[10px] text-slate-500">Sign in to Proxigo to see your quota</div>
          )}
        </div>

        <div className="p-5 space-y-5 flex-1">
          {/* Region name */}
          <div>
            <label className="label">Region name</label>
            <input
              className="input-field"
              value={regionName}
              onChange={(e) => setRegionName(e.target.value)}
              placeholder="Flight Region"
            />
          </div>

          {/* Imagery source */}
          <div>
            <label className="label">Primary imagery provider</label>
            <div className="space-y-1 mt-1">
              {providers.map((provider) => {
                const keyMissing = providerNeedsMissingKey(provider, apiKeys);
                const directReady = provider.kind !== "vector" && provider.enabled && Boolean(provider.url_template);
                return (
                  <button
                    key={provider.id}
                    onClick={() => directReady && handleSourceChange(provider.id)}
                    disabled={!directReady}
                    className={cn(
                      "flex w-full items-start justify-between gap-3 border px-3 py-2.5 text-left text-xs transition-colors",
                      sourceConfig?.id === provider.id
                        ? "border-orange-500/40 bg-orange-500/10 text-orange-200"
                        : directReady
                        ? "border-border bg-bg-card text-slate-400 hover:border-slate-600 hover:text-slate-300"
                        : "border-border bg-bg-card/60 text-slate-600"
                    )}
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 font-medium">
                        <span>{provider.label}</span>
                        {keyMissing && <span className="text-[10px] text-amber-400">key required</span>}
                      </div>
                      <div className="mt-0.5 line-clamp-2 text-[10px] opacity-70">{provider.notes}</div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase text-slate-400">
                        {provider.kind}
                      </span>
                      <span className={cn("text-[10px] font-mono", keyMissing ? "text-amber-400" : "text-slate-300")}>
                        Z{provider.max_native_zoom}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
              <div className="border border-border bg-bg-card px-2 py-1.5">
                <div className="text-slate-500">Priority</div>
                <div className="mt-0.5 truncate font-mono text-slate-300">
                  {providerOrder.map(providerShortLabel).join(" > ") || "none"}
                </div>
              </div>
              <div className="border border-border bg-bg-card px-2 py-1.5">
                <div className="text-slate-500">Coverage</div>
                <div className="mt-0.5 truncate font-mono text-slate-300">{sourceConfig?.coverage_mode ?? "unknown"}</div>
              </div>
            </div>
            {missingKey && (
              <div className="mt-2 border border-amber-500/20 bg-amber-500/10 px-2.5 py-2 text-[10px] text-amber-400">
                API key missing. Preview falls back to Esri; this provider stays unavailable for downloads until a key is configured.
              </div>
            )}
            <div className="mt-3 border border-border bg-bg-card p-2.5">
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                  Download Providers
                </span>
                <span className="font-mono text-[10px] text-slate-500">
                  {providerOrder.length} active
                </span>
              </div>
              <div className="space-y-1">
                {readyDownloadProviders.map((provider) => {
                  const selected = selectedProviderIds.includes(provider.id);
                  const keyMissing = providerNeedsMissingKey(provider, apiKeys);
                  const readyProvider = isDownloadProviderReady(provider, apiKeys);
                  return (
                    <label
                      key={provider.id}
                      className={cn(
                        "flex items-center gap-2 border px-2 py-1.5 text-[10px]",
                        selected
                          ? "border-orange-500/35 bg-orange-500/10 text-orange-100"
                          : readyProvider
                          ? "border-border bg-bg-elevated text-slate-300"
                          : "border-border bg-bg-elevated/60 text-slate-600",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={selected}
                        disabled={!readyProvider}
                        onChange={() => toggleDownloadProvider(provider.id)}
                        className="accent-orange-500 disabled:opacity-40"
                      />
                      <span className="min-w-0 flex-1 truncate">{provider.label}</span>
                      <span className={cn("shrink-0 font-mono", keyMissing ? "text-amber-400" : "text-slate-500")}>
                        {keyMissing ? "key" : `Z${provider.max_native_zoom}`}
                      </span>
                    </label>
                  );
                })}
              </div>
              {!providerOrder.length && (
                <div className="mt-2 border border-orange-500/20 bg-orange-500/10 px-2 py-1.5 text-[10px] text-orange-200">
                  Select at least one ready provider to estimate or download this map.
                </div>
              )}
              <div className="mt-2 text-[10px] text-slate-500">
                Estimates add the checked providers together so the download box reflects the selected source set.
              </div>
            </div>
          </div>

          {/* Selection tool */}
          <div>
            <label className="label">Selection tool</label>
            <div className="grid grid-cols-2 gap-1 mt-1">
              {DRAW_MODES.map(({ mode, label }) => (
                <button
                  key={mode}
                  onClick={() => handleModeChange(mode)}
                  className={cn(
                    "border py-2 text-xs font-medium transition-colors",
                    drawMode === mode
                      ? "bg-cyan-500/10 border-cyan-500/30 text-cyan-300"
                      : "bg-bg-card border-border text-slate-400 hover:text-slate-300 hover:border-slate-600"
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-slate-600 mt-1.5">
              {drawMode === "polygon"
                ? "Click boundary points in any order. The saved outline is auto-ordered into an n-gon."
                : "Tiles are downloaded for the dragged box area."}
            </p>
          </div>

          {/* Zoom level */}
          <div>
            <label className="label flex items-center justify-between">
              <span>Zoom level</span>
              <span className="text-orange-400 font-mono">{zoom}</span>
            </label>
            <input
              type="range"
              min={15}
              max={sourceConfig?.max_zoom ?? 23}
              step={1}
              value={zoom}
              onChange={(e) => {
                setZoom(Number(e.target.value));
                setCoverageSurvey(null);
              }}
              className="w-full mt-2"
            />
            <div className="flex justify-between text-[10px] text-slate-500 mt-1">
              <span>15 (1.2 m/px)</span>
              <span>Z{sourceConfig?.max_zoom ?? 23}</span>
            </div>
          </div>

          {/* BBox / selection info */}
          {bbox ? (
            <div ref={estimateRef} className="space-y-2 border border-border bg-bg-card p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-slate-300">Selected Region</span>
                <button onClick={clearSelection} className="text-slate-500 hover:text-slate-300">
                  <X size={13} />
                </button>
              </div>
              <div className="text-[11px] font-mono text-slate-400 space-y-0.5">
                <div>Lat {bbox.lat_min.toFixed(5)} → {bbox.lat_max.toFixed(5)}</div>
                <div>Lon {bbox.lon_min.toFixed(5)} → {bbox.lon_max.toFixed(5)}</div>
              </div>
              <div className="grid grid-cols-2 gap-2 text-[10px]">
                <div className="bg-bg-elevated px-2 py-1.5">
                  <div className="text-slate-500">Shape</div>
                  <div className="mt-0.5 font-mono uppercase text-slate-200">
                    {cutShape === "polygon" ? `${polygonPoints.length}-gon` : "box"}
                  </div>
                </div>
                <div className="bg-bg-elevated px-2 py-1.5">
                  <div className="text-slate-500">Tile footprint</div>
                  <div className="mt-0.5 font-mono text-slate-200">{bboxAreaKm2(bbox).toFixed(2)} km²</div>
                </div>
              </div>
              <div className="bg-bg-elevated px-2 py-1.5 text-center">
                <span className={cn("text-lg font-bold font-mono", exceedsCloudQuota ? "text-red-400" : "text-orange-400")}>
                  {(estimate?.area_km2 ?? (cutShape === "polygon" ? polygonAreaKm2(polygonPoints) : bboxAreaKm2(bbox))).toFixed(2)}
                </span>
                <span className="text-xs text-slate-400 ml-1">km² cut area</span>
              </div>
              {(estimating || estimate) && (
                <div className="border-t border-border pt-2 space-y-2">
                  {estimating && !estimate && (
                    <div className="flex items-center gap-2 text-[10px] text-slate-500">
                      <Loader2 size={11} className="animate-spin shrink-0" />
                      Calculating download estimate…
                    </div>
                  )}
                  {estimate && (<>
                  {estimate.warnings.map((warning) => (
                    <div
                      key={warning}
                      className={cn(
                        "flex gap-2 border px-2.5 py-2 text-[10px]",
                        estimate.over_100_km2
                          ? "border-orange-500/25 bg-orange-500/10 text-orange-300"
                          : "border-red-500/20 bg-red-500/10 text-red-400"
                      )}
                    >
                      <ShieldAlert size={12} className="mt-0.5 shrink-0" />
                      <span>{warning}</span>
                    </div>
                  ))}
                  {mapLimitWarnings.map((warning) => (
                    <div
                      key={warning}
                      className="flex gap-2 border border-orange-500/25 bg-orange-500/10 px-2.5 py-2 text-[10px] text-orange-300"
                    >
                      <ShieldAlert size={12} className="mt-0.5 shrink-0" />
                      <span>{warning}</span>
                    </div>
                  ))}
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-400">Tiles</span>
                    <span className={cn("font-medium", estimate.too_large ? "text-red-400" : "text-slate-200")}>
                      {estimate.tile_count.toLocaleString()} ({multiLayerMap ? `Z15-Z${zoom}` : `${estimate.nx}×${estimate.ny}`})
                    </span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-400">Source / disk</span>
                    <span className="text-slate-200 font-medium">
                      {formatBytesMb(estimate.estimated_source_mb)} / {formatBytesMb(estimate.estimated_disk_mb)}
                    </span>
                  </div>
                  <label className="flex items-start gap-2 border border-border bg-bg-elevated px-2.5 py-2 text-[10px] text-slate-400">
                    <input
                      type="checkbox"
                      className="mt-0.5 accent-orange-500"
                      checked={multiLayerMap}
                      onChange={(event) => {
                        setMultiLayerMap(event.target.checked);
                        setCoverageSurvey(null);
                        setConfirmLargeArea(false);
                      }}
                    />
                    <span>
                      <span className="block font-medium text-slate-200">Multi-Layer Map</span>
                      <span className="block text-slate-500">
                        Include Z15-Z{zoom}; estimate and download include every selected zoom layer.
                      </span>
                    </span>
                  </label>
                  <div className="text-[10px] text-slate-500">
                    Download size is based on the tile footprint and zoom; n-gon edges are saved and masked in the output.
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-400">GSD</span>
                    <span className="text-slate-200 font-medium">{estimate.gsd_m_per_px.toFixed(3)} m/px</span>
                  </div>
                  {estimate.provider_breakdown.length > 0 && (
                    <div className="space-y-1 border-t border-border pt-2">
                      {estimate.provider_breakdown.map((item) => (
                        <div key={item.provider_id} className="flex items-center justify-between gap-2 text-[10px]">
                          <span className="truncate text-slate-400">{item.label}</span>
                          <span className={cn("shrink-0 font-mono", item.key_required ? "text-amber-400" : "text-slate-300")}>
                            {item.key_required ? "key" : item.overzoomed ? "overzoom" : formatBytesMb(item.estimated_source_mb)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  {requiresDownloadConfirmation && (
                    <label className="flex items-start gap-2 border border-orange-500/20 bg-orange-500/10 px-2.5 py-2 text-[10px] text-orange-200">
                      <input
                        type="checkbox"
                        className="mt-0.5 accent-orange-500"
                        checked={confirmLargeArea}
                        onChange={(event) => setConfirmLargeArea(event.target.checked)}
                      />
                      <span>I understand this map cut may exceed warning limits and want to continue.</span>
                    </label>
                  )}
                  </>)}
                  <button
                    onClick={handleSurveyCoverage}
                    disabled={surveying || !providerOrder.length}
                    className="btn-secondary w-full justify-center text-xs"
                  >
                    {surveying ? (
                      <><Loader2 size={13} className="animate-spin" /> Surveying Coverage</>
                    ) : (
                      <><Search size={13} /> Survey Coverage</>
                    )}
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="border border-dashed border-border bg-bg-card p-4 text-center">
              <Layers size={20} className="text-slate-600 mx-auto mb-2" />
              <p className="text-xs text-slate-500">{currentMode.hint}</p>
            </div>
          )}

          {coverageSurvey && (
            <div className="space-y-3 border border-border bg-bg-card p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <ClipboardCheck size={14} className="text-orange-400" />
                  <span className="text-xs font-medium text-slate-300">Coverage Survey</span>
                </div>
                <span className="font-mono text-[10px] text-slate-500">Z{coverageSurvey.min_zoom}-{coverageSurvey.max_zoom}</span>
              </div>
              <div className="rounded border border-border bg-bg-elevated px-2 py-1.5 text-[10px] text-slate-400">
                Recommended:{" "}
                <span className="font-mono text-slate-200">
                  {coverageSurvey.recommended_provider_order.map(providerShortLabel).join(" > ") || "none"}
                </span>
              </div>
              <div className="space-y-1">
                {surveyRows.map((row) => (
                  <div key={`${row.provider_id}-${row.zoom}`} className="grid grid-cols-[1fr_auto] gap-2 border border-border bg-bg-elevated px-2 py-1.5 text-[10px]">
                    <div className="min-w-0">
                      <div className="truncate text-slate-300">{row.label}</div>
                      <div className="font-mono text-slate-500">
                        valid {row.valid_count}/{row.sampled_count} · low {row.low_detail_count} · missing {row.missing_count}
                      </div>
                    </div>
                    <div className={cn(
                      "text-right font-mono",
                      row.classification === "valid" || row.classification === "available"
                        ? "text-emerald-400"
                        : row.classification === "low-detail"
                        ? "text-amber-400"
                        : "text-red-400"
                    )}>
                      {(row.quality_score * 100).toFixed(0)}%
                      <div className="uppercase text-slate-500">{row.classification}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Output folder */}
          <div>
            <label className="label flex items-center justify-between">
              <span>Output folder</span>
              <span className="text-[10px] text-slate-500">{customOutputDir ? "Custom" : "Default"}</span>
            </label>
            <div className="flex gap-2">
              <input
                className="input-field flex-1 text-xs font-mono"
                value={outputDir}
                onChange={(e) => {
                  setOutputDir(e.target.value);
                  setCustomOutputDir(true);
                }}
                placeholder="Choose folder…"
              />
              {customOutputDir && (
                <button
                  onClick={() => setCustomOutputDir(false)}
                  className="btn-secondary px-3 text-[10px]"
                >
                  Default
                </button>
              )}
              <button onClick={handlePickDir} className="btn-secondary px-3">
                <FolderOpen size={15} />
              </button>
            </div>
          </div>

          {/* Progress */}
          {downloading && progress && (
            <div className="space-y-2">
              <div className="flex justify-between text-xs text-slate-400">
                <span>Downloading tiles…</span>
                <span>{progress.current} / {progress.total}</span>
              </div>
              <div className="h-2 overflow-hidden bg-bg-elevated">
                <div
                  className="h-full bg-orange-500 transition-all duration-200"
                  style={{ width: `${progress.percent}%` }}
                />
              </div>
            </div>
          )}

          {done && (
            <div className="flex items-center gap-2 border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-400">
              <CheckCircle2 size={15} />
              {doneMessage}
            </div>
          )}
          {error && (
            <div className="border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              {error}
            </div>
          )}

          <div className="flex items-start gap-1.5 border border-border bg-bg-card p-2.5 text-[10px] text-slate-500">
            <Info size={11} className="mt-0.5 shrink-0 text-orange-500" />
            <span>
              Preview uses {sourceConfig?.label ?? "selected provider"}. Downloads patch missing or low-detail tiles from the checked set:
              {" "}{providerOrder.map(providerShortLabel).join(" > ") || "no provider selected"}, and save a coverage manifest next to the mosaic.
            </span>
          </div>

          <div className="space-y-3 border border-border bg-bg-card p-3">
            <div className="flex items-center gap-2">
              <FileImage size={14} className="text-cyan-400" />
              <span className="text-xs font-medium text-slate-300">Upload Your Own Map</span>
            </div>
            <p className="text-[10px] text-slate-500">
              Supported: PNG, JPEG, TIFF/GeoTIFF image, BMP, WebP, GIF. GeoTIFF WGS84, Web Mercator, and UTM metadata is detected automatically; manual origin/GSD fields override it.
            </p>
            <div>
              <label className="label">Map file</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 text-xs font-mono" value={mapFilePath} readOnly placeholder="Choose map image..." />
                <button onClick={handlePickMapFile} className="btn-secondary px-3">
                  <FolderOpen size={14} />
                </button>
              </div>
            </div>
            <div>
              <label className="label">Map name</label>
              <input className="input-field text-xs" value={mapImportName} onChange={(e) => setMapImportName(e.target.value)} />
            </div>
            <div>
              <label className="label flex items-center justify-between">
                <span>Imported map folder</span>
                <span className="text-[10px] text-slate-500">{customMapImportOutput ? "Custom" : "Default"}</span>
              </label>
              <div className="flex gap-2">
                <input
                  className="input-field flex-1 text-xs font-mono"
                  value={mapImportOutputDir}
                  onChange={(e) => {
                    setMapImportOutputDir(e.target.value);
                    setCustomMapImportOutput(true);
                  }}
                  placeholder="Folder for normalized map source..."
                />
                {customMapImportOutput && (
                  <button
                    onClick={() => setCustomMapImportOutput(false)}
                    className="btn-secondary px-3 text-[10px]"
                  >
                    Default
                  </button>
                )}
                <button onClick={handlePickMapOutputDir} className="btn-secondary px-3">
                  <FolderOpen size={14} />
                </button>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label">Origin lat override</label>
                <input className="input-field text-xs" value={mapOriginLat} onChange={(e) => setMapOriginLat(e.target.value)} placeholder="top-left latitude" />
              </div>
              <div>
                <label className="label">Origin lon override</label>
                <input className="input-field text-xs" value={mapOriginLon} onChange={(e) => setMapOriginLon(e.target.value)} placeholder="top-left longitude" />
              </div>
              <div>
                <label className="label">GSD override m/px</label>
                <input className="input-field text-xs" value={mapGsd} onChange={(e) => setMapGsd(e.target.value)} placeholder="0.20" />
              </div>
              <div>
                <label className="label">Rotation deg</label>
                <input className="input-field text-xs" value={mapRotationDeg} onChange={(e) => setMapRotationDeg(e.target.value)} placeholder="0" />
              </div>
            </div>
            <button
              onClick={handleImportMapFile}
              disabled={importingMap || !mapFilePath || !mapImportOutputDir}
              className="btn-secondary w-full justify-center text-xs"
            >
              {importingMap ? <><Loader2 size={13} className="animate-spin" /> Importing...</> : <><Upload size={13} /> Import Map File</>}
            </button>
          </div>

          <div className="space-y-3 border border-border bg-bg-card p-3">
            <div className="flex items-center gap-2">
              <Mountain size={14} className="text-cyan-400" />
              <span className="text-xs font-medium text-slate-300">Attach Elevation Assets</span>
            </div>
            <p className="text-[10px] text-slate-500">
              Optional DEM/DSM GeoTIFFs are copied into the selected map source and carried into terrain mission bundles.
            </p>
            <div>
              <label className="label">Map source</label>
              <select
                className="input-field text-xs"
                value={elevationRegion?.id ?? ""}
                onChange={(e) => setElevationRegionId(e.target.value)}
                disabled={!regions.length}
              >
                {regions.length ? regions.map((region) => (
                  <option key={region.id} value={region.id}>
                    {region.name}
                  </option>
                )) : (
                  <option value="">No saved maps</option>
                )}
              </select>
            </div>
            <div>
              <label className="label">DEM GeoTIFF</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 text-xs font-mono" value={demFilePath} readOnly placeholder="Optional terrain elevation..." />
                <button onClick={() => pickElevationFile("dem")} className="btn-secondary px-3">
                  <FolderOpen size={14} />
                </button>
              </div>
            </div>
            <div>
              <label className="label">DSM GeoTIFF</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 text-xs font-mono" value={dsmFilePath} readOnly placeholder="Optional surface elevation..." />
                <button onClick={() => pickElevationFile("dsm")} className="btn-secondary px-3">
                  <FolderOpen size={14} />
                </button>
              </div>
            </div>
            {elevationRegion && (
              <div className="space-y-1 bg-bg-elevated px-2.5 py-2 text-[10px] text-slate-400">
                <div className="flex justify-between gap-2">
                  <span>Attached assets</span>
                  <span className="text-slate-200">{elevationRegion.elevation_asset_count ?? 0}</span>
                </div>
                {elevationRegion.elevation_dem_path && (
                  <div className="font-mono truncate">DEM {elevationRegion.elevation_dem_path}</div>
                )}
                {elevationRegion.elevation_dsm_path && (
                  <div className="font-mono truncate">DSM {elevationRegion.elevation_dsm_path}</div>
                )}
              </div>
            )}
            <button
              onClick={handleImportElevationAssets}
              disabled={importingElevation || !elevationRegion || (!demFilePath && !dsmFilePath)}
              className="btn-secondary w-full justify-center text-xs"
            >
              {importingElevation ? <><Loader2 size={13} className="animate-spin" /> Attaching...</> : <><Mountain size={13} /> Attach Elevation Assets</>}
            </button>
          </div>
        </div>

        <div className="p-5 border-t border-border space-y-2">
          <button
            onClick={handleDownload}
            disabled={downloadBlocked}
            className="btn-primary w-full justify-center"
          >
            {downloading ? (
              <><Loader2 size={15} className="animate-spin" /> Downloading…</>
            ) : (
              <><Download size={15} /> Download Mosaic</>
            )}
          </button>
          {exceedsCloudQuota && (
            <div className="text-center text-[10px] text-red-400">
              Quota exceeded — upgrade your plan at proxigo.us to download this area.
            </div>
          )}
          {requiresDownloadConfirmation && !confirmLargeArea && !exceedsCloudQuota && (
            <div className="text-center text-[10px] text-orange-300">
              Confirm the map download warning before starting.
            </div>
          )}
          {!providerOrder.length && (
            <div className="text-center text-[10px] text-orange-300">
              Select at least one ready download provider.
            </div>
          )}
          {missingKey && (
            <div className="text-center text-[10px] text-amber-400">
              The preview provider needs a key; checked ready providers can still download.
            </div>
          )}
          <button
            onClick={importFromFolder}
            className="btn-secondary w-full justify-center text-xs"
          >
            <FolderInput size={13} /> Import existing folder…
          </button>
        </div>
      </div>
    </div>
  );
}
