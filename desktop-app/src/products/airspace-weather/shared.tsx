import { useMemo } from "react";
import { usePlatformMapLayers } from "../../platform/hooks";
import type { LatLon } from "../../platform/types";
import { VPS_COVERAGE_LAYER_ID } from "./constants";
import { pointInRing } from "./sources";
import type { CheckLevel } from "./store";

export const LEVEL_COLORS: Record<CheckLevel, string> = {
  go: "#22C55E",
  caution: "#EAB308",
  nogo: "#EF4444",
  info: "#94A3B8",
};

export function formatPoint([lat, lon]: LatLon) {
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

/**
 * Related VPS data: names of Macula VPS maps covering a location, read from
 * the VPS platform layer. null when the organization has no VPS license.
 */
export function useVpsCoverageAt(point: LatLon | null): string[] | null {
  const layers = usePlatformMapLayers();
  const coverage = layers.find((layer) => layer.id === VPS_COVERAGE_LAYER_ID);
  return useMemo(() => {
    if (!coverage) return null;
    if (!point) return [];
    return coverage.features
      .filter((feature) => feature.kind === "area" && pointInRing(point, feature.positions))
      .map((feature) => feature.label ?? feature.id);
  }, [coverage, point]);
}
