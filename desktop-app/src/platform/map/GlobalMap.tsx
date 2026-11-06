import { LocateFixed, Minus, Plus, type LucideIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Map as MapLibreMap, MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import L from "leaflet";
import { CircleMarker, MapContainer, Pane, Polygon, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import { useShellStore } from "../../lib/shellStore";
import { useActiveProduct, useEntitlements, useMapLayersInView, usePlatformContext } from "../hooks";
import { usePlatformMapStore } from "../mapStore";
import { productById } from "../registry";
import type { LatLon, PlatformMapClick, PlatformMapLayer, PlatformMapTool } from "../types";
import {
  clampMapZoom,
  collection,
  constrainGlobeTransform,
  EMPTY_GEOJSON,
  lineFeature,
  mapBoundsFromPoints,
  mapLibreSource,
  MAP_MAX_ZOOM,
  MAP_MIN_ZOOM,
  normalizeMapCenter,
  PLACEHOLDER_ORG_COUNTRY_CENTER,
  PLACEHOLDER_ORG_COUNTRY_ZOOM,
  pointFeature,
  polygonFeature,
  preferredMapRenderer,
  rectangleFromCorners,
  SATELLITE_STYLE,
  toLngLat,
  WEB_MERCATOR_LAT_LIMIT,
  wrapMapLatitude,
  wrapMapLongitude,
  type MapApi,
  type MapGeoJsonCollection,
  type MapLibreRuntime,
  type MapViewport,
} from "./geo";

/**
 * The shared global map. Product-agnostic: it draws the platform layers in
 * view, runs the active product's drawing tool, routes clicks, and renders the
 * active product's `MapOverlay`.
 */
export function GlobalMap() {
  const rightDockOpen = useShellStore((state) => state.rightDockOpen);
  const mapSearchTarget = useShellStore((state) => state.mapSearchTarget);
  const activeProduct = useActiveProduct();
  const { entitled } = useEntitlements();
  const ctx = usePlatformContext();
  const layers = useMapLayersInView();
  const cursor = usePlatformMapStore((state) => state.cursor);
  const tool = usePlatformMapStore((state) => state.tool);
  const home = usePlatformMapStore((state) => state.home);
  const setPlatformMapApi = usePlatformMapStore((state) => state.setApi);
  const [mapApi, setMapApi] = useState<MapApi | null>(null);
  const [viewport, setViewport] = useState<MapViewport>({ center: PLACEHOLDER_ORG_COUNTRY_CENTER, zoom: PLACEHOLDER_ORG_COUNTRY_ZOOM });
  const viewportRef = useRef(viewport);
  viewportRef.current = viewport;

  // Move only when a new search target arrives; clearing it must not jump back
  // to the default view (products clear it before fitting their own area).
  const [target, setTarget] = useState<{ center: LatLon; zoom: number }>({
    center: PLACEHOLDER_ORG_COUNTRY_CENTER,
    zoom: PLACEHOLDER_ORG_COUNTRY_ZOOM,
  });
  useEffect(() => {
    if (mapSearchTarget) setTarget({ center: [mapSearchTarget.lat, mapSearchTarget.lon], zoom: mapSearchTarget.zoom ?? 14 });
  }, [mapSearchTarget]);
  const center = normalizeMapCenter(target.center);
  const zoom = clampMapZoom(target.zoom);

  useEffect(() => {
    setPlatformMapApi(mapApi ? { focus: mapApi.focus, fitPoints: mapApi.fitPoints, getView: () => viewportRef.current } : null);
  }, [mapApi, setPlatformMapApi]);
  useEffect(() => () => setPlatformMapApi(null), [setPlatformMapApi]);

  const handleClick = useCallback((click: PlatformMapClick) => {
    if (activeProduct) {
      const ownHit = click.hit && layers.some((layer) => layer.id === click.hit?.layerId && layer.productId === activeProduct.id);
      activeProduct.onMapClick?.(ownHit ? click : { position: click.position }, ctx);
      return;
    }
    // Overview: a click on a product's point opens it in that product.
    if (!click.hit) return;
    const layer = layers.find((item) => item.id === click.hit?.layerId);
    const owner = layer ? productById(layer.productId) : undefined;
    if (owner && entitled.has(owner.id)) owner.onMapClick?.(click, ctx);
  }, [activeProduct, ctx, entitled, layers]);

  const ProductMapOverlay = activeProduct?.MapOverlay;
  const leftInsetClass = rightDockOpen ? "left-[444px]" : "left-20";

  return (
    <div className="ops-screen-bg relative h-full min-h-[calc(100vh-127px)] overflow-hidden animate-fade-in">
      <div className="absolute inset-0">
        <MapView
          center={center}
          zoom={zoom}
          layers={layers}
          tool={tool}
          idleCursor={cursor}
          setMapApi={setMapApi}
          onViewportChange={setViewport}
          onMapClick={handleClick}
        />
      </div>
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-bg-base/45 via-transparent to-bg-base/50" />
      {ProductMapOverlay && <ProductMapOverlay ctx={ctx} leftInsetClass={leftInsetClass} />}
      <MapZoomControls
        onZoomIn={() => mapApi?.zoomIn()}
        onZoomOut={() => mapApi?.zoomOut()}
        onRecenter={() => (home?.length ? mapApi?.fitPoints(home, 14) : mapApi?.recenter())}
        zoomReady={Boolean(mapApi)}
      />
    </div>
  );
}

type MapViewProps = {
  center: LatLon;
  zoom: number;
  layers: PlatformMapLayer[];
  /** Active product drawing tool; clicks go to it instead of `onMapClick`. */
  tool: PlatformMapTool | null;
  /** Cursor requested by the active product while no tool is active. */
  idleCursor: string | null;
  setMapApi: (api: MapApi) => void;
  onViewportChange: (viewport: MapViewport) => void;
  onMapClick: (click: PlatformMapClick) => void;
};

function MapView(props: MapViewProps) {
  const [renderer, setRenderer] = useState<"leaflet" | "maplibre">(() => preferredMapRenderer());
  const [mapLibreFailed, setMapLibreFailed] = useState(false);

  if (renderer === "maplibre" && !mapLibreFailed) {
    return <MapLibreView {...props} onRendererFailure={() => {
      setMapLibreFailed(true);
      setRenderer("leaflet");
    }} />;
  }
  return <LeafletView {...props} />;
}

const DEFAULT_TOOL_COLOR = "#FF6600";
const DEFAULT_POINT_STROKE = "#0B0C0D";

function cursorFor(tool: PlatformMapTool | null, idleCursor: string | null) {
  return tool ? "crosshair" : idleCursor ?? "";
}

// ---------------------------------------------------------------- Leaflet

function LeafletView({ center, zoom, layers, tool, idleCursor, setMapApi, onViewportChange, onMapClick }: MapViewProps) {
  return (
    <MapContainer
      center={center}
      zoom={zoom}
      minZoom={MAP_MIN_ZOOM}
      maxZoom={MAP_MAX_ZOOM}
      worldCopyJump
      maxBoundsViscosity={1}
      className="mission-map h-full w-full"
      scrollWheelZoom
      attributionControl={false}
      zoomControl={false}
    >
      <LeafletViewportController center={center} zoom={zoom} setMapApi={setMapApi} onViewportChange={onViewportChange} />
      <LeafletToolController tool={tool} idleCursor={idleCursor} onIdleClick={onMapClick} />
      <TileLayer
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        maxZoom={MAP_MAX_ZOOM}
        updateWhenIdle
        updateWhenZooming={false}
        keepBuffer={2}
      />
      <Pane name="mission-labels" className="mission-map-label-pane" style={{ zIndex: 420 }}>
        <TileLayer
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
          maxZoom={MAP_MAX_ZOOM}
          opacity={0.92}
          updateWhenIdle
          updateWhenZooming={false}
          keepBuffer={1}
        />
      </Pane>
      <LeafletPlatformLayers layers={layers} onPointClick={tool ? undefined : onMapClick} />
    </MapContainer>
  );
}

function LeafletPlatformLayers({ layers, onPointClick }: { layers: PlatformMapLayer[]; onPointClick?: (click: PlatformMapClick) => void }) {
  return (
    <>
      {layers.flatMap((layer) =>
        layer.features.map((feature) => {
          const key = `${layer.id}:${feature.id}`;
          const color = feature.color ?? layer.color;
          const style = feature.style ?? {};
          if (feature.kind === "point") {
            const radius = style.radius ?? 7;
            const tooltip = feature.label
              ? style.permanentLabel
                ? <Tooltip direction="top" offset={[0, -radius]} opacity={0.95} permanent>{feature.label}</Tooltip>
                : <Tooltip sticky>{`${feature.label} - ${layer.label}`}</Tooltip>
              : null;
            return [
              style.halo ? (
                <CircleMarker key={`${key}:halo`} center={feature.position} radius={radius + 7} interactive={false}
                  pathOptions={{ color, fillColor: color, fillOpacity: 0.16, weight: 1 }} />
              ) : null,
              <CircleMarker
                key={key}
                center={feature.position}
                radius={radius}
                bubblingMouseEvents={!onPointClick}
                pathOptions={{
                  color: style.fillColor ? color : DEFAULT_POINT_STROKE,
                  fillColor: style.fillColor ?? color,
                  fillOpacity: style.fillOpacity ?? 0.9,
                  weight: style.width ?? 2,
                }}
                eventHandlers={onPointClick ? {
                  click: () => onPointClick({ position: feature.position, hit: { layerId: layer.id, featureId: feature.id } }),
                } : undefined}
              >
                {tooltip}
              </CircleMarker>,
            ];
          }
          const tooltip = feature.label ? <Tooltip sticky>{`${feature.label} - ${layer.label}`}</Tooltip> : null;
          if (feature.kind === "line") {
            return (
              <Polyline key={key} positions={feature.positions}
                pathOptions={{ color, weight: style.width ?? 2, opacity: 0.9, dashArray: style.dashed ? "8 8" : undefined }}>
                {tooltip}
              </Polyline>
            );
          }
          return (
            <Polygon key={key} positions={feature.positions}
              pathOptions={{
                color,
                fillColor: style.fillColor ?? color,
                fillOpacity: style.fillOpacity ?? 0.06,
                weight: style.width ?? 1.5,
                dashArray: style.dashed ?? true ? "4 4" : undefined,
              }}>
              {tooltip}
            </Polygon>
          );
        }),
      )}
    </>
  );
}

function LeafletViewportController({
  center,
  zoom,
  setMapApi,
  onViewportChange,
}: {
  center: LatLon;
  zoom: number;
  setMapApi: (api: MapApi) => void;
  onViewportChange: (viewport: MapViewport) => void;
}) {
  const map = useMap();
  const [centerLat, centerLon] = center;

  useEffect(() => {
    setMapApi({
      zoomIn: () => map.zoomIn(),
      zoomOut: () => {
        if (map.getZoom() > MAP_MIN_ZOOM) map.zoomOut();
      },
      recenter: () => map.setView(normalizeMapCenter(center), clampMapZoom(zoom), { animate: true }),
      focus: (targetCenter, targetZoom) => map.setView(normalizeMapCenter(targetCenter), clampMapZoom(targetZoom), { animate: true }),
      fitPoints: (points, maxZoom = 14) => {
        if (!points.length) return;
        map.fitBounds(L.latLngBounds(points.map(([lat, lon]) => L.latLng(lat, lon))), {
          animate: true,
          padding: [56, 56],
          maxZoom: clampMapZoom(maxZoom),
        });
      },
    });
  }, [centerLat, centerLon, map, setMapApi, zoom]);

  useEffect(() => {
    map.setMinZoom(MAP_MIN_ZOOM);
    map.setMaxBounds([[-WEB_MERCATOR_LAT_LIMIT, -180], [WEB_MERCATOR_LAT_LIMIT, 180]]);
    if (map.getZoom() < MAP_MIN_ZOOM) map.setZoom(MAP_MIN_ZOOM, { animate: false });
  }, [map]);

  useEffect(() => {
    const updateViewport = () => {
      const current = map.getCenter();
      onViewportChange({ center: normalizeMapCenter([current.lat, current.lng]), zoom: clampMapZoom(map.getZoom()) });
    };
    map.on("moveend", updateViewport);
    map.on("zoomend", updateViewport);
    updateViewport();
    return () => {
      map.off("moveend", updateViewport);
      map.off("zoomend", updateViewport);
    };
  }, [map, onViewportChange]);

  useEffect(() => {
    map.setView(normalizeMapCenter([centerLat, centerLon]), clampMapZoom(zoom), { animate: true });
  }, [centerLat, centerLon, map, zoom]);

  return null;
}

function LeafletToolController({
  tool,
  idleCursor,
  onIdleClick,
}: {
  tool: PlatformMapTool | null;
  idleCursor: string | null;
  onIdleClick: (click: PlatformMapClick) => void;
}) {
  const boxStartRef = useRef<L.LatLng | null>(null);
  const boxPreviewRef = useRef<L.Rectangle | null>(null);
  const boxDragCleanupRef = useRef<(() => void) | null>(null);

  const clearBoxPreview = (map: L.Map) => {
    if (boxPreviewRef.current) {
      map.removeLayer(boxPreviewRef.current);
      boxPreviewRef.current = null;
    }
  };

  const map = useMapEvents({
    click(event) {
      const point: LatLon = [event.latlng.lat, event.latlng.lng];
      if (!tool) onIdleClick({ position: point });
      else if (tool.mode === "point") tool.onPoint?.(point);
    },
    mousedown(event) {
      if (tool?.mode !== "box") return;
      const color = tool.color ?? DEFAULT_TOOL_COLOR;
      L.DomEvent.stop(event.originalEvent);
      boxDragCleanupRef.current?.();
      boxStartRef.current = event.latlng;
      map.dragging.disable();
      const updatePreview = (nativeEvent: MouseEvent) => {
        if (!boxStartRef.current) return;
        const latLng = map.mouseEventToLatLng(nativeEvent);
        clearBoxPreview(map);
        boxPreviewRef.current = L.rectangle(
          [[boxStartRef.current.lat, boxStartRef.current.lng], [latLng.lat, latLng.lng]],
          { color, fillColor: color, fillOpacity: 0.1, dashArray: "8 8", weight: 2 },
        ).addTo(map);
      };
      const finishDrag = (nativeEvent: MouseEvent) => {
        const start = boxStartRef.current;
        boxStartRef.current = null;
        document.removeEventListener("mousemove", updatePreview);
        document.removeEventListener("mouseup", finishDrag);
        boxDragCleanupRef.current = null;
        map.dragging.enable();
        clearBoxPreview(map);
        if (!start) return;
        const end = map.mouseEventToLatLng(nativeEvent);
        if (Math.abs(start.lat - end.lat) < 1e-7 || Math.abs(start.lng - end.lng) < 1e-7) return;
        tool.onBox?.(rectangleFromCorners([start.lat, start.lng], [end.lat, end.lng]));
      };
      document.addEventListener("mousemove", updatePreview);
      document.addEventListener("mouseup", finishDrag);
      boxDragCleanupRef.current = () => {
        document.removeEventListener("mousemove", updatePreview);
        document.removeEventListener("mouseup", finishDrag);
        map.dragging.enable();
      };
    },
  });

  useEffect(() => {
    const container = map.getContainer();
    container.style.cursor = cursorFor(tool, idleCursor);
    return () => {
      container.style.cursor = "";
      boxDragCleanupRef.current?.();
      boxDragCleanupRef.current = null;
      map.dragging.enable();
      clearBoxPreview(map);
      boxStartRef.current = null;
    };
  }, [idleCursor, map, tool]);
  return null;
}

// ---------------------------------------------------------------- MapLibre

function platformLayerCollection(layers: PlatformMapLayer[]): MapGeoJsonCollection {
  return collection(
    layers.flatMap((layer) =>
      layer.features.map((feature) => {
        const id = `${layer.id}:${feature.id}`;
        const style = feature.style ?? {};
        const properties = {
          tint: feature.color ?? layer.color,
          fill: style.fillColor ?? null,
          fillOpacity: style.fillOpacity ?? null,
          width: style.width ?? null,
          dashed: style.dashed ?? feature.kind === "area",
          radius: style.radius ?? null,
          halo: Boolean(style.halo),
          label: feature.label ?? null,
          layer: layer.label,
          layerId: layer.id,
          featureId: feature.id,
        };
        if (feature.kind === "point") return pointFeature(id, feature.position, properties);
        if (feature.kind === "line") return lineFeature(id, feature.positions, properties);
        return polygonFeature(id, feature.positions, properties);
      }),
    ),
  );
}

function MapLibreView({
  center,
  zoom,
  layers,
  tool,
  idleCursor,
  setMapApi,
  onViewportChange,
  onMapClick,
  onRendererFailure,
}: MapViewProps & { onRendererFailure: () => void }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const centerKey = `${center[0].toFixed(6)}:${center[1].toFixed(6)}`;
  const platformData = useMemo(() => platformLayerCollection(layers), [layers]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    let map: MapLibreMap | null = null;

    async function loadMapLibre() {
      try {
        const module = await import("maplibre-gl");
        if (cancelled || !containerRef.current) return;
        const maplibreApi = (module.default ?? module) as unknown as MapLibreRuntime;
        if (typeof maplibreApi.supported === "function" && !maplibreApi.supported()) {
          onRendererFailure();
          return;
        }
        map = new maplibreApi.Map({
          container: containerRef.current,
          style: SATELLITE_STYLE,
          center: toLngLat(center),
          zoom: clampMapZoom(zoom),
          minZoom: MAP_MIN_ZOOM,
          maxZoom: MAP_MAX_ZOOM,
          attributionControl: false,
          renderWorldCopies: false,
          transformConstrain: constrainGlobeTransform,
          fadeDuration: 140,
          refreshExpiredTiles: false,
          maxTileCacheSize: 128,
          maxTileCacheZoomLevels: 4,
          cancelPendingTileRequestsWhileZooming: false,
          collectResourceTiming: false,
          pixelRatio: Math.min(window.devicePixelRatio || 1, 1.5),
          validateStyle: false,
          canvasContextAttributes: {
            antialias: false,
            powerPreference: "high-performance",
            preserveDrawingBuffer: false,
          },
        });
        mapRef.current = map;
        map.dragRotate.disable();
        map.touchZoomRotate.disableRotation();
        map.on("load", () => {
          if (!map) return;
          addPlatformMapLayers(map);
          setReady(true);
        });
        map.on("error", (event) => {
          const message = String(event.error?.message ?? "");
          if (message.toLowerCase().includes("webgl") || message.toLowerCase().includes("context")) {
            console.warn("MapLibre runtime error; falling back to Leaflet", event.error);
            onRendererFailure();
          }
        });
        const updateViewport = () => {
          if (!map) return;
          const current = map.getCenter();
          onViewportChange({ center: normalizeMapCenter([current.lat, current.lng]), zoom: clampMapZoom(map.getZoom()) });
        };
        map.on("moveend", updateViewport);
        map.on("zoomend", updateViewport);
      } catch (error) {
        console.warn("MapLibre initialization failed; falling back to Leaflet", error);
        onRendererFailure();
      }
    }

    void loadMapLibre();
    return () => {
      cancelled = true;
      map?.remove();
      mapRef.current = null;
      setReady(false);
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    setMapApi({
      zoomIn: () => map.zoomIn({ duration: 180 }),
      zoomOut: () => {
        if (map.getZoom() > MAP_MIN_ZOOM) map.zoomOut({ duration: 180 });
      },
      recenter: () => map.easeTo({ center: toLngLat(center), zoom: clampMapZoom(zoom), duration: 220 }),
      focus: (targetCenter, targetZoom) => map.easeTo({ center: toLngLat(normalizeMapCenter(targetCenter)), zoom: clampMapZoom(targetZoom), duration: 260 }),
      fitPoints: (points, maxZoom = 14) => {
        const bounds = mapBoundsFromPoints(points);
        if (!bounds) return;
        map.fitBounds(bounds, { padding: 72, maxZoom: clampMapZoom(maxZoom), duration: 260 });
      },
    });
  }, [centerKey, ready, setMapApi, zoom]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.jumpTo({ center: toLngLat(center), zoom: clampMapZoom(zoom) });
  }, [centerKey, ready, zoom]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    mapLibreSource(map, "platform-features")?.setData(platformData);
  }, [platformData, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let boxStart: LatLon | null = null;
    let stopBoxDrag: (() => void) | null = null;
    const color = tool?.color ?? DEFAULT_TOOL_COLOR;

    const setBoxPreview = (points: LatLon[] | null) => {
      const linePoints = points && points.length >= 3 ? [...points, points[0]] : points;
      mapLibreSource(map, "box-preview-area")?.setData(
        points ? collection([polygonFeature("box-preview", points, { tint: color })]) : EMPTY_GEOJSON,
      );
      mapLibreSource(map, "box-preview-line")?.setData(
        points ? collection([lineFeature("box-preview-line", linePoints ?? [], { tint: color })]) : EMPTY_GEOJSON,
      );
    };
    const pointFromMouseEvent = (event: MouseEvent): LatLon => {
      const rect = map.getCanvas().getBoundingClientRect();
      const lngLat = map.unproject([event.clientX - rect.left, event.clientY - rect.top]);
      return [wrapMapLatitude(lngLat.lat), wrapMapLongitude(lngLat.lng)];
    };
    const cleanupBoxDrag = () => {
      stopBoxDrag?.();
      stopBoxDrag = null;
      boxStart = null;
      map.dragPan.enable();
      setBoxPreview(null);
    };

    const clickHandler = (event: MapMouseEvent) => {
      const position: LatLon = [wrapMapLatitude(event.lngLat.lat), wrapMapLongitude(event.lngLat.lng)];
      if (tool) {
        if (tool.mode === "point") tool.onPoint?.(position);
        return;
      }
      const hit = map.getLayer("platform-points")
        ? map.queryRenderedFeatures(event.point, { layers: ["platform-points"] })[0]
        : undefined;
      const layerId = hit?.properties?.layerId;
      const featureId = hit?.properties?.featureId;
      onMapClick(
        typeof layerId === "string" && typeof featureId === "string"
          ? { position, hit: { layerId, featureId } }
          : { position },
      );
    };
    const mouseDownHandler = (event: MapMouseEvent) => {
      if (tool?.mode !== "box") return;
      event.preventDefault();
      event.originalEvent.stopPropagation();
      cleanupBoxDrag();
      boxStart = [wrapMapLatitude(event.lngLat.lat), wrapMapLongitude(event.lngLat.lng)];
      map.dragPan.disable();
      const mouseMoveHandler = (nativeEvent: MouseEvent) => {
        if (!boxStart) return;
        setBoxPreview(rectangleFromCorners(boxStart, pointFromMouseEvent(nativeEvent)));
      };
      const mouseUpHandler = (nativeEvent: MouseEvent) => {
        const start = boxStart;
        document.removeEventListener("mousemove", mouseMoveHandler);
        document.removeEventListener("mouseup", mouseUpHandler);
        stopBoxDrag = null;
        boxStart = null;
        map.dragPan.enable();
        setBoxPreview(null);
        if (!start) return;
        const end = pointFromMouseEvent(nativeEvent);
        if (Math.abs(start[0] - end[0]) < 1e-7 || Math.abs(start[1] - end[1]) < 1e-7) return;
        tool.onBox?.(rectangleFromCorners(start, end));
      };
      document.addEventListener("mousemove", mouseMoveHandler);
      document.addEventListener("mouseup", mouseUpHandler);
      stopBoxDrag = () => {
        document.removeEventListener("mousemove", mouseMoveHandler);
        document.removeEventListener("mouseup", mouseUpHandler);
      };
    };

    const baseCursor = cursorFor(tool, idleCursor);
    const pointEnter = () => {
      if (!tool && !idleCursor) map.getCanvas().style.cursor = "pointer";
    };
    const pointLeave = () => {
      map.getCanvas().style.cursor = baseCursor;
    };
    map.on("click", clickHandler);
    map.on("mousedown", mouseDownHandler);
    map.on("mouseenter", "platform-points", pointEnter);
    map.on("mouseleave", "platform-points", pointLeave);
    map.getCanvas().style.cursor = baseCursor;
    return () => {
      map.off("click", clickHandler);
      map.off("mousedown", mouseDownHandler);
      map.off("mouseenter", "platform-points", pointEnter);
      map.off("mouseleave", "platform-points", pointLeave);
      map.getCanvas().style.cursor = "";
      cleanupBoxDrag();
    };
  }, [idleCursor, onMapClick, ready, tool]);

  return <div ref={containerRef} className="mission-map h-full w-full" />;
}

function addPlatformMapLayers(map: MapLibreMap) {
  for (const sourceId of ["platform-features", "box-preview-area", "box-preview-line"]) {
    if (!map.getSource(sourceId)) map.addSource(sourceId, { type: "geojson", data: EMPTY_GEOJSON });
  }
  const tint = ["coalesce", ["get", "tint"], "#94A3B8"] as const;
  const isPolygon = ["==", ["geometry-type"], "Polygon"] as const;

  map.addLayer({
    id: "platform-area-fill",
    type: "fill",
    source: "platform-features",
    filter: isPolygon,
    paint: {
      "fill-color": ["coalesce", ["get", "fill"], tint],
      "fill-opacity": ["coalesce", ["get", "fillOpacity"], 0.06],
    },
  } as never);
  map.addLayer({
    id: "platform-lines",
    type: "line",
    source: "platform-features",
    filter: ["any", isPolygon, ["==", ["geometry-type"], "LineString"]],
    paint: {
      "line-color": tint,
      "line-width": ["coalesce", ["get", "width"], ["case", isPolygon, 1.5, 2]],
      "line-opacity": 0.9,
      "line-dasharray": ["case", ["==", ["get", "dashed"], true], ["literal", [2, 2]], ["literal", [1, 0]]],
    },
  } as never);
  map.addLayer({
    id: "platform-halos",
    type: "circle",
    source: "platform-features",
    filter: ["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "halo"], true]],
    paint: {
      "circle-radius": ["+", ["coalesce", ["get", "radius"], 7], 7],
      "circle-color": tint,
      "circle-opacity": 0.16,
      "circle-stroke-color": tint,
      "circle-stroke-width": 1,
    },
  } as never);
  map.addLayer({
    id: "platform-points",
    type: "circle",
    source: "platform-features",
    filter: ["==", ["geometry-type"], "Point"],
    paint: {
      "circle-radius": ["coalesce", ["get", "radius"], 7],
      "circle-color": ["coalesce", ["get", "fill"], tint],
      "circle-opacity": 0.9,
      "circle-stroke-color": ["case", ["has", "fill"], tint, DEFAULT_POINT_STROKE],
      "circle-stroke-width": ["coalesce", ["get", "width"], 2],
    },
  } as never);
  map.addLayer({
    id: "box-preview-fill",
    type: "fill",
    source: "box-preview-area",
    paint: { "fill-color": ["get", "tint"], "fill-opacity": 0.12 },
  });
  map.addLayer({
    id: "box-preview-line",
    type: "line",
    source: "box-preview-line",
    paint: { "line-color": ["get", "tint"], "line-width": 2, "line-opacity": 0.95, "line-dasharray": [2, 2] },
  });
}

// ---------------------------------------------------------------- Controls

function MapZoomControls({
  onZoomIn,
  onZoomOut,
  onRecenter,
  zoomReady,
}: {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onRecenter: () => void;
  zoomReady: boolean;
}) {
  return (
    <div className="absolute bottom-20 right-4 z-[1080] flex w-11 flex-col overflow-hidden rounded-lg border border-white/10 bg-bg-base/45 opacity-55 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl transition-opacity hover:opacity-95">
      <MapControlButton Icon={Plus} label="Zoom in" disabled={!zoomReady} onClick={onZoomIn} />
      <MapControlButton Icon={Minus} label="Zoom out" disabled={!zoomReady} onClick={onZoomOut} />
      <MapControlButton Icon={LocateFixed} label="Recenter" disabled={!zoomReady} onClick={onRecenter} />
    </div>
  );
}

export function MapControlButton({
  Icon,
  label,
  disabled,
  onClick,
}: {
  Icon: LucideIcon;
  label: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex h-11 w-11 items-center justify-center border-b border-white/10 text-slate-300 transition-colors last:border-b-0 hover:bg-cyan-500/10 hover:text-cyan-300 disabled:cursor-not-allowed disabled:opacity-40"
      title={label}
    >
      <Icon size={17} />
    </button>
  );
}
