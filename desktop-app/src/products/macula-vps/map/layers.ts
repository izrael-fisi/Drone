import { useMemo } from "react";
import { useAppStore } from "../../../lib/store";
import type { SavedMission } from "../../../lib/types";
import type { LatLon, PlatformMapFeature, PlatformMapLayer } from "../../../platform/types";
import { orderPolygonPoints, positionLatLon, positionTone, regionPolygon } from "./helpers";
import { useVpsMapStore } from "./store";

const ORANGE = "#FF6600";
const AMBER = "#F59E0B";
const DARK = "#0B0C0D";

/** A cut/mission border: an area once it has 3 points, a dashed line before that, plus its corner points. */
function borderFeatures(id: string, points: LatLon[], color: string, fillOpacity: number, labelCorners: boolean): PlatformMapFeature[] {
  if (!points.length) return [];
  const outline: PlatformMapFeature = points.length >= 3
    ? { id: `${id}-area`, kind: "area", positions: points, color, style: { fillOpacity, width: 2, dashed: true } }
    : { id: `${id}-line`, kind: "line", positions: points, color, style: { width: 2, dashed: true } };
  return [
    outline,
    ...points.map((position, index): PlatformMapFeature => ({
      id: `${id}-corner-${index}`,
      kind: "point",
      position,
      color,
      label: labelCorners ? `Cut ${index + 1}` : undefined,
      style: { radius: 5, fillColor: DARK, fillOpacity: 0.95, width: 2 },
    })),
  ];
}

/** Waypoint path with Start / WP n / Land markers. */
function waypointFeatures(id: string, waypoints: LatLon[]): PlatformMapFeature[] {
  return [
    ...(waypoints.length > 1 ? [{ id: `${id}-path`, kind: "line", positions: waypoints, color: ORANGE, style: { width: 3 } } as PlatformMapFeature] : []),
    ...waypoints.map((position, index): PlatformMapFeature => {
      const isStart = index === 0;
      const isLand = waypoints.length > 1 && index === waypoints.length - 1;
      const color = isStart ? "#63D706" : isLand ? "#EF4444" : ORANGE;
      return {
        id: `${id}-wp-${index}`,
        kind: "point",
        position,
        color,
        label: isStart ? "Start" : isLand ? "Land" : `WP ${index + 1}`,
        style: { radius: 7, fillColor: color, width: 3, permanentLabel: true },
      };
    }),
  ];
}

function missionFeatures(missions: SavedMission[]): PlatformMapFeature[] {
  return missions.flatMap((mission, index) => [
    ...borderFeatures(`mission-${mission.id}`, mission.border_points, index % 2 === 0 ? ORANGE : AMBER, 0.08, false),
    ...waypointFeatures(`mission-${mission.id}`, mission.waypoints),
  ]);
}

/**
 * Macula VPS working layers on the shared map: active map outline, map-cut
 * and waypoint drafts, selected missions and the live aircraft. `activeOnly`,
 * so they are drawn only in the Macula VPS view.
 */
export function useVpsWorkingLayers(productId: string): PlatformMapLayer[] {
  const regions = useAppStore((state) => state.regions);
  const activeDevice = useAppStore((state) => state.devices.find((device) => device.id === state.activeDeviceId));
  const selectedMap = useVpsMapStore((state) => state.selectedMap);
  const borderPoints = useVpsMapStore((state) => state.borderPoints);
  const borderCutShape = useVpsMapStore((state) => state.borderCutShape);
  const waypoints = useVpsMapStore((state) => state.waypoints);
  const missionMode = useVpsMapStore((state) => state.missionMode);
  const savedMissions = useVpsMapStore((state) => state.savedMissions);
  const selectedMissionIds = useVpsMapStore((state) => state.selectedMissionIds);
  const position = useVpsMapStore((state) => state.position);

  return useMemo(() => {
    const activeMap = selectedMap !== "world" ? regions.find((region) => region.id === selectedMap) : undefined;
    const activeMapPolygon = regionPolygon(activeMap);

    const missions = missionMode && activeMap
      ? savedMissions.filter((mission) => mission.map_id === activeMap.id && selectedMissionIds.includes(mission.id))
      : [];
    const draft = missionMode
      ? []
      : [
          ...borderFeatures("draft", borderCutShape === "polygon" ? orderPolygonPoints(borderPoints) : borderPoints, ORANGE, 0.1, true),
          ...waypointFeatures("draft", waypoints),
        ];

    const aircraftPosition = positionLatLon(position);
    const tone = positionTone(position);
    const aircraft: PlatformMapFeature[] = activeDevice && aircraftPosition && (tone === "ready" || tone === "active")
      ? [{
          id: "aircraft",
          kind: "point",
          position: aircraftPosition,
          label: activeDevice.name,
          color: tone === "ready" ? "#63D706" : ORANGE,
          style: { radius: 7, fillColor: tone === "ready" ? "#2E8F49" : "#B84A00", fillOpacity: 0.95, width: 3, halo: true },
        }]
      : [];

    const layer = (id: string, label: string, features: PlatformMapFeature[]): PlatformMapLayer => ({
      id: `${productId}.${id}`,
      productId,
      label,
      color: ORANGE,
      features,
      activeOnly: true,
    });
    return [
      layer("active-map", "Active VPS map", activeMapPolygon.length
        ? [{ id: "active-map", kind: "area", positions: activeMapPolygon, label: activeMap?.name, style: { fillOpacity: 0.08, width: 2, dashed: false } }]
        : []),
      layer("missions", "Selected missions", missionFeatures(missions)),
      layer("draft", "Map cut and waypoint draft", draft),
      layer("aircraft", "Aircraft", aircraft),
    ];
  }, [activeDevice, borderCutShape, borderPoints, missionMode, position, productId, regions, savedMissions, selectedMap, selectedMissionIds, waypoints]);
}
