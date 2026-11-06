import { CloudSun, Crosshair, Gauge, Layers, RadioTower, RefreshCw, Shield } from "lucide-react";
import { useMemo } from "react";
import { usePlatformMapStore } from "../../platform/mapStore";
import type {
  PlatformContext,
  PlatformMapClick,
  PlatformMapFeature,
  PlatformMapLayer,
  ProductCommand,
  ProductModule,
  ProductStatusSummary,
} from "../../platform/types";
import {
  ACCENT,
  AIRSPACE_CLASS_COLORS,
  AIRSPACE_WEATHER_ID,
  FLIGHT_CATEGORY_COLORS,
  LAYER_CONTROLLED,
  LAYER_METAR,
  LAYER_POINT,
  LAYER_UAS_GRID,
  PANEL_AIRSPACE,
  PANEL_BRIEFING,
  PANEL_LIMITS,
  PANEL_STATIONS,
  uasCeilingColor,
  VPS_COVERAGE_LAYER_ID,
} from "./constants";
import { AirspaceMapOverlay } from "./MapOverlay";
import { AirspacePanel, BriefingPanel, LimitsPanel, StationsPanel } from "./panels";
import { levelLabel, useAirspaceStore, useAssessment } from "./store";

function useAirspaceStatus(): ProductStatusSummary {
  const point = useAirspaceStore((state) => state.point);
  const metars = useAirspaceStore((state) => state.metars.data);
  const airspace = useAirspaceStore((state) => state.airspace.data);
  const assessment = useAssessment();
  if (!point) {
    return { health: "idle", headline: "Click the map in Airspace & Weather to brief a location" };
  }
  const wind = assessment.checks.find((check) => check.id === "wind");
  const headline = assessment.pending
    ? "Loading briefing…"
    : `${levelLabel(assessment.overall)}${wind ? ` · wind ${wind.value}` : ""}`;
  return {
    health: assessment.pending ? "idle" : assessment.overall === "go" ? "ready" : assessment.overall === "info" ? "idle" : "attention",
    headline,
    metrics: [
      { label: "Airspace", value: airspace?.length ?? "-" },
      { label: "Stations", value: metars?.length ?? "-" },
    ],
  };
}

function useAirspaceMapLayers(): PlatformMapLayer[] {
  const point = useAirspaceStore((state) => state.point);
  const airspace = useAirspaceStore((state) => state.airspace.data);
  const uasGrid = useAirspaceStore((state) => state.uasGrid.data);
  const metars = useAirspaceStore((state) => state.metars.data);

  return useMemo(() => {
    const controlled: PlatformMapFeature[] = (airspace ?? []).flatMap((area) =>
      area.rings.map((ring, index) => ({
        id: `${area.id}-${index}`,
        kind: "area" as const,
        positions: ring,
        label: `${area.name} (${area.floor} to ${area.ceiling})`,
        color: AIRSPACE_CLASS_COLORS[area.airspaceClass],
      })),
    );
    const grid: PlatformMapFeature[] = (uasGrid ?? []).flatMap((cell) =>
      cell.rings.map((ring, index) => ({
        id: `${cell.id}-${index}`,
        kind: "area" as const,
        positions: ring,
        label: `LAANC ${cell.ceilingFt} ft AGL`,
        color: uasCeilingColor(cell.ceilingFt),
      })),
    );
    const stations: PlatformMapFeature[] = (metars ?? []).map((station) => ({
      id: station.id,
      kind: "point" as const,
      position: station.position,
      label: `${station.id} ${station.flightCategory ?? ""}`.trim(),
      color: FLIGHT_CATEGORY_COLORS[station.flightCategory ?? ""] ?? "#94A3B8",
    }));
    return [
      { id: LAYER_CONTROLLED, productId: AIRSPACE_WEATHER_ID, label: "Controlled airspace (FAA)", color: AIRSPACE_CLASS_COLORS.B, features: controlled, defaultVisible: true },
      { id: LAYER_UAS_GRID, productId: AIRSPACE_WEATHER_ID, label: "LAANC ceilings (FAA)", color: "#EAB308", features: grid, defaultVisible: true },
      { id: LAYER_METAR, productId: AIRSPACE_WEATHER_ID, label: "Weather stations (METAR)", color: FLIGHT_CATEGORY_COLORS.VFR, features: stations, defaultVisible: true },
      {
        id: LAYER_POINT,
        productId: AIRSPACE_WEATHER_ID,
        label: "Briefing point",
        color: ACCENT,
        features: point ? [{ id: "point", kind: "point" as const, position: point, label: "Briefing point" }] : [],
        defaultVisible: true,
      },
    ];
  }, [airspace, metars, point, uasGrid]);
}

function useAirspaceCommands(): ProductCommand[] {
  return useMemo<ProductCommand[]>(() => [
    {
      id: "airspace-brief-center",
      label: "Brief Map Center",
      detail: "Airspace and weather at the center of the map",
      group: "Airspace & Weather",
      keywords: "weather wind airspace laanc go no-go",
      Icon: Crosshair,
      run: (ctx) => {
        const view = usePlatformMapStore.getState().api?.getView();
        if (view) useAirspaceStore.getState().brief(view.center);
        ctx.openPanel(PANEL_BRIEFING);
      },
    },
    {
      id: "airspace-refresh",
      label: "Refresh Briefing",
      detail: "Reload weather and airspace for the briefing point",
      group: "Airspace & Weather",
      Icon: RefreshCw,
      run: (ctx) => {
        useAirspaceStore.getState().refresh();
        ctx.openPanel(PANEL_BRIEFING);
      },
    },
  ], []);
}

function handleMapClick(click: PlatformMapClick, ctx: PlatformContext) {
  const store = useAirspaceStore.getState();
  if (click.hit?.layerId === LAYER_METAR) {
    store.selectStation(click.hit.featureId);
    ctx.openPanel(PANEL_STATIONS);
    return;
  }
  store.brief(click.position);
  ctx.openPanel(PANEL_BRIEFING);
}

export const airspaceWeatherProduct: ProductModule = {
  id: AIRSPACE_WEATHER_ID,
  name: "Airspace & Weather",
  shortName: "Airspace",
  tagline: "Controlled airspace, LAANC ceilings and flight weather for any location",
  category: "integration",
  Icon: CloudSun,
  accent: ACCENT,
  entitlementKey: "airspace_weather",
  defaultPanelId: PANEL_BRIEFING,
  // Shows where Macula VPS maps exist relative to airspace.
  relatedLayerIds: [VPS_COVERAGE_LAYER_ID],
  panels: [
    {
      id: PANEL_BRIEFING,
      label: "Briefing",
      Icon: Gauge,
      command: { label: "Flight Briefing", detail: "Go / no-go for the briefing point", keywords: "weather wind airspace" },
      Component: BriefingPanel,
    },
    {
      id: PANEL_AIRSPACE,
      label: "Airspace",
      Icon: Layers,
      command: { label: "Airspace", detail: "Controlled airspace and LAANC ceilings near the briefing point", keywords: "faa class b c d laanc" },
      Component: AirspacePanel,
    },
    {
      id: PANEL_STATIONS,
      label: "Weather Stations",
      Icon: RadioTower,
      command: { label: "Weather Stations", detail: "Nearby METAR observations", keywords: "metar taf weather" },
      Component: StationsPanel,
    },
    {
      id: PANEL_LIMITS,
      label: "Flight Limits",
      Icon: Shield,
      command: { label: "Flight Limits", detail: "Wind, visibility and temperature thresholds" },
      Component: LimitsPanel,
    },
  ],
  MapOverlay: AirspaceMapOverlay,
  onMapClick: handleMapClick,
  useStatus: useAirspaceStatus,
  useMapLayers: useAirspaceMapLayers,
  useCommands: useAirspaceCommands,
};
