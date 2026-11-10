import { useMemo } from "react";
import type { PlatformMapFeature, PlatformMapLayer } from "../types";
import { circle, KINDS, viewCone, type PlacedItem } from "./model";
import { useProjectsStore } from "./store";

export const PROJECTS_LAYER_ID = "projects.sites";
const PROJECTS_DRAFT_LAYER_ID = "projects.draft";

function itemFeatures(item: PlacedItem, selected: boolean): PlatformMapFeature[] {
  const { color, geometry } = KINDS[item.kind];
  const emphasis = selected ? 1 : 0;
  if (geometry === "area" && item.polygon) {
    const keepout = item.kind === "keepout";
    return [{
      id: `${item.id}#area`,
      kind: "area",
      positions: item.polygon,
      label: item.name,
      color,
      style: { fillOpacity: keepout ? 0.18 : 0.08, width: 2 + emphasis, dashed: !keepout },
    }];
  }
  if (!item.position) return [];
  const coverage: PlatformMapFeature[] = [];
  if (item.kind === "camera" && item.rangeM) {
    coverage.push({
      id: `${item.id}#cone`,
      kind: "area",
      positions: viewCone(item.position, item.headingDeg ?? 0, item.fovDeg ?? 90, item.rangeM),
      color,
      style: { fillOpacity: selected ? 0.2 : 0.12, width: 1, dashed: false },
    });
  }
  if (item.kind === "sensor" && item.rangeM) {
    coverage.push({ id: `${item.id}#range`, kind: "area", positions: circle(item.position, item.rangeM), color, style: { fillOpacity: 0.07, width: 1, dashed: true } });
  }
  return [
    ...coverage,
    {
      id: item.id,
      kind: "point",
      position: item.position,
      label: item.name,
      color,
      style: { radius: 6 + emphasis * 2, width: 2 + emphasis, halo: item.kind === "launch" || selected },
    },
  ];
}

/**
 * Site layouts from every project, shown in all views (they are physical
 * placements, not product data), plus the area being drawn right now.
 */
export function useProjectLayers(): PlatformMapLayer[] {
  const projects = useProjectsStore((state) => state.projects);
  const selectedItemId = useProjectsStore((state) => state.selectedItemId);
  const placing = useProjectsStore((state) => state.placing);

  return useMemo(() => {
    const sites: PlatformMapLayer = {
      id: PROJECTS_LAYER_ID,
      productId: "projects",
      label: "Site layouts",
      color: "#E2E8F0",
      global: true,
      features: projects.flatMap((project) => project.items.flatMap((item) => itemFeatures(item, item.id === selectedItemId))),
    };
    const draftColor = placing ? KINDS[placing.kind].color : "#E2E8F0";
    const draftPoints = placing?.points ?? [];
    const draft: PlatformMapLayer = {
      id: PROJECTS_DRAFT_LAYER_ID,
      productId: "projects",
      label: "Drawing",
      color: draftColor,
      global: true,
      internal: true,
      features: [
        ...(draftPoints.length >= 3
          ? [{ id: "draft-area", kind: "area" as const, positions: draftPoints, color: draftColor, style: { fillOpacity: 0.12, width: 2, dashed: true } }]
          : draftPoints.length === 2
            ? [{ id: "draft-line", kind: "line" as const, positions: draftPoints, color: draftColor, style: { width: 2, dashed: true } }]
            : []),
        ...draftPoints.map((position, index) => ({
          id: `draft-${index}`,
          kind: "point" as const,
          position,
          color: draftColor,
          style: { radius: 5, fillColor: "#0B0C0D", width: 2 },
        })),
      ],
    };
    return [sites, draft];
  }, [placing, projects, selectedItemId]);
}
