import { Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { CircleMarker, MapContainer, Pane, Polygon, Polyline, TileLayer, Tooltip, useMap } from "react-leaflet";
import { SAVED_MISSIONS_CHANGED, loadSavedMissions, missionBoundsFromParts, missionCenter, removeSavedMission } from "../../../lib/missions";
import { useShellStore } from "../../../lib/shellStore";
import type { DronePositionUpdate, SavedMission } from "../../../lib/types";
import { EmptyLine, PanelStack, StatusRow } from "../../../platform/ui";

export type MissionPreviewBounds = [[number, number], [number, number]];

export function clampCoordinate(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

export function missionPreviewBounds(mission: SavedMission): MissionPreviewBounds {
  const bounds = mission.bounds ?? missionBoundsFromParts(mission.border_points, mission.waypoints);
  if (!bounds) return [[-85, -180], [85, 180]];
  const centerLat = (bounds.lat_min + bounds.lat_max) / 2;
  const centerLon = (bounds.lon_min + bounds.lon_max) / 2;
  const cosLat = Math.max(0.08, Math.abs(Math.cos(centerLat * Math.PI / 180)));
  let latSpan = Math.max(bounds.lat_max - bounds.lat_min, 0.025);
  let lonSpan = Math.max(bounds.lon_max - bounds.lon_min, 0.025);
  const widthKm = lonSpan * 111.32 * cosLat;
  const heightKm = latSpan * 111.32;
  const targetAspect = 1.08;

  if (widthKm / Math.max(heightKm, 0.001) < targetAspect) {
    lonSpan = Math.max(lonSpan, (heightKm * targetAspect) / (111.32 * cosLat));
  } else if (widthKm / Math.max(heightKm, 0.001) > targetAspect) {
    latSpan = Math.max(latSpan, widthKm / (targetAspect * 111.32));
  }

  const latHalfSpan = Math.max(latSpan * 0.78, 0.025);
  const lonHalfSpan = Math.max(lonSpan * 0.78, 0.025);
  return [
    [
      clampCoordinate(centerLat - latHalfSpan, -85, 85),
      clampCoordinate(centerLon - lonHalfSpan, -180, 180),
    ],
    [
      clampCoordinate(centerLat + latHalfSpan, -85, 85),
      clampCoordinate(centerLon + lonHalfSpan, -180, 180),
    ],
  ];
}

export function missionTooltipPlacement(point: [number, number], center: [number, number]) {
  const horizontal = Math.abs(point[1] - center[1]) >= Math.abs(point[0] - center[0]);
  if (horizontal) {
    return point[1] <= center[1]
      ? { direction: "right" as const, offset: [8, 0] as [number, number] }
      : { direction: "left" as const, offset: [-8, 0] as [number, number] };
  }
  return point[0] <= center[0]
    ? { direction: "top" as const, offset: [0, -8] as [number, number] }
    : { direction: "bottom" as const, offset: [0, 8] as [number, number] };
}

export function livePositionLatLon(position: DronePositionUpdate | null): [number, number] | null {
  const lat = position?.lat_lon?.lat;
  const lon = position?.lat_lon?.lon;
  return typeof lat === "number" && Number.isFinite(lat) && typeof lon === "number" && Number.isFinite(lon)
    ? [lat, lon]
    : null;
}

export function MissionPanel() {
  const { selectedMissionId, setSelectedMissionId, livePosition } = useShellStore();
  const [missions, setMissions] = useState<SavedMission[]>(() => loadSavedMissions());
  const selectedMission = missions.find((mission) => mission.id === selectedMissionId) ?? missions[0];

  useEffect(() => {
    const refreshMissions = () => setMissions(loadSavedMissions());
    window.addEventListener(SAVED_MISSIONS_CHANGED, refreshMissions);
    window.addEventListener("storage", refreshMissions);
    return () => {
      window.removeEventListener(SAVED_MISSIONS_CHANGED, refreshMissions);
      window.removeEventListener("storage", refreshMissions);
    };
  }, []);

  useEffect(() => {
    if (missions.length === 0) {
      if (selectedMissionId) setSelectedMissionId(null);
      return;
    }
    if (!selectedMissionId || !missions.some((mission) => mission.id === selectedMissionId)) {
      setSelectedMissionId(missions[0].id);
    }
  }, [missions, selectedMissionId, setSelectedMissionId]);

  const deleteSelectedMission = () => {
    if (!selectedMission) return;
    if (!window.confirm(`Delete ${selectedMission.name}?`)) return;
    const nextMissions = removeSavedMission(selectedMission.id);
    setMissions(nextMissions);
    setSelectedMissionId(nextMissions[0]?.id ?? null);
  };

  return (
    <PanelStack
      title="Mission"
      subtitle={selectedMission ? selectedMission.map_label ?? "Saved operator geometry" : "No mission selected"}
      action={selectedMission && (
        <button type="button" onClick={deleteSelectedMission} className="operator-shell-button h-8 w-8 rounded-md hover:text-red-300" title="Delete mission">
          <Trash2 size={14} />
        </button>
      )}
    >
      <label className="block rounded-lg border border-border bg-bg-card px-3 py-2">
        <span className="block text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">Mission</span>
        <select
          value={selectedMission?.id ?? ""}
          onChange={(event) => setSelectedMissionId(event.target.value || null)}
          disabled={missions.length === 0}
          className="mt-1 h-8 w-full bg-transparent text-sm text-slate-100 outline-none disabled:text-slate-600"
        >
          {missions.length === 0 ? (
            <option value="">No saved missions</option>
          ) : (
            missions.map((mission) => (
              <option key={mission.id} value={mission.id}>
                {mission.name}
              </option>
            ))
          )}
        </select>
      </label>

      <MissionPreview mission={selectedMission} livePosition={livePosition} />

      {selectedMission ? (
        <>
          <div className="grid grid-cols-2 gap-2">
            <MissionStat label="Map Cut" value={selectedMission.border_points.length >= 3 ? "Saved" : "Open"} detail={`${selectedMission.border_points.length} border pts`} />
            <MissionStat label="Waypoints" value={selectedMission.waypoints.length} detail="path points" />
          </div>
          <StatusRow label="Source map" value={selectedMission.map_label ?? "World Map"} healthy />
          <StatusRow label="Updated" value={new Date(selectedMission.updated_at).toLocaleString()} healthy />
        </>
      ) : (
        <EmptyLine text="Draw a map border or waypoint path, then use Save Mission from the map controls." />
      )}
    </PanelStack>
  );
}

export function MissionPreview({
  mission,
  livePosition,
}: {
  mission?: SavedMission;
  livePosition: DronePositionUpdate | null;
}) {
  if (!mission) {
    return (
      <div className="mission-dot-grid flex h-80 items-center justify-center rounded-lg border border-border p-4">
        <div className="rounded-lg border border-border bg-black/80 px-4 py-3 text-center text-xs text-slate-500">
          No mission preview
        </div>
      </div>
    );
  }

  const bounds = missionPreviewBounds(mission);
  const center = missionCenter(mission);
  const hasBorder = mission.border_points.length >= 3;
  const aircraftPosition = livePositionLatLon(livePosition);

  return (
    <div className="mission-dot-grid rounded-lg border border-border p-2.5">
      <div className="h-80 min-h-[280px] overflow-hidden rounded-lg border border-border bg-black shadow-inner">
        <MapContainer
          key={`${mission.id}-${mission.updated_at}`}
          center={center}
          zoom={Math.max(3, Math.min(16, mission.zoom))}
          minZoom={3}
          maxZoom={23}
          className="mission-map mission-preview-map h-full w-full"
          scrollWheelZoom
          attributionControl={false}
          zoomControl={false}
        >
          <MissionPreviewViewport mission={mission} bounds={bounds} />
          <TileLayer
            url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
            maxZoom={23}
            maxNativeZoom={23}
          />
          <Pane name={`mission-preview-labels-${mission.id}`} className="mission-map-label-pane" style={{ zIndex: 420 }}>
            <TileLayer
              url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
              maxZoom={23}
              maxNativeZoom={23}
              opacity={0.92}
            />
          </Pane>
          {mission.border_points.length > 0 && (
            <>
              {hasBorder ? (
                <Polygon
                  positions={mission.border_points}
                  pathOptions={{ color: "#FF6600", fillColor: "#FF6600", fillOpacity: 0.1, dashArray: "8 8", weight: 2 }}
                />
              ) : (
                <Polyline positions={mission.border_points} pathOptions={{ color: "#FF6600", dashArray: "8 8", weight: 2 }} />
              )}
              {mission.border_points.map((point, index) => (
                <CircleMarker
                  key={`mission-border-${index}-${point[0]}-${point[1]}`}
                  center={point}
                  radius={4}
                  pathOptions={{ color: "#FF6600", fillColor: "#0B0C0D", fillOpacity: 0.95, weight: 2 }}
                />
              ))}
            </>
          )}
          {mission.waypoints.length > 0 && (
            <>
              {mission.waypoints.length > 1 && (
                <Polyline positions={mission.waypoints} pathOptions={{ color: "#FF6600", weight: 3, opacity: 0.9 }} />
              )}
              {mission.waypoints.map((point, index) => {
                const isStart = index === 0;
                const isLand = mission.waypoints.length > 1 && index === mission.waypoints.length - 1;
                const color = isStart ? "#63D706" : isLand ? "#EF4444" : "#FF6600";
                const label = isStart ? "Start" : isLand ? "Land" : `WP ${index + 1}`;
                const placement = missionTooltipPlacement(point, center);
                return (
                  <CircleMarker
                    key={`mission-waypoint-${index}-${point[0]}-${point[1]}`}
                    center={point}
                    radius={6}
                    pathOptions={{ color, fillColor: color, fillOpacity: 0.92, weight: 3 }}
                  >
                    <Tooltip direction={placement.direction} offset={placement.offset} opacity={0.95} permanent>
                      {label}
                    </Tooltip>
                  </CircleMarker>
                );
              })}
            </>
          )}
          {aircraftPosition && (
            <CircleMarker
              center={aircraftPosition}
              radius={8}
              pathOptions={{ color: "#63D706", fillColor: "#2E8F49", fillOpacity: 0.95, weight: 3 }}
            >
              <Tooltip direction={missionTooltipPlacement(aircraftPosition, center).direction} offset={missionTooltipPlacement(aircraftPosition, center).offset} opacity={0.95} permanent>
                Aircraft
              </Tooltip>
            </CircleMarker>
          )}
        </MapContainer>
      </div>
    </div>
  );
}

export function MissionPreviewViewport({ mission, bounds }: { mission: SavedMission; bounds: MissionPreviewBounds }) {
  const map = useMap();
  const south = bounds[0][0];
  const west = bounds[0][1];
  const north = bounds[1][0];
  const east = bounds[1][1];

  useEffect(() => {
    const fitPreview = () => {
      map.invalidateSize(false);
      map.fitBounds(bounds, {
        paddingTopLeft: [34, 34],
        paddingBottomRight: [34, 48],
        maxZoom: Math.max(13, Math.min(17, mission.zoom)),
        animate: false,
      });
    };

    fitPreview();
    const animationFrame = window.requestAnimationFrame(fitPreview);
    const timer = window.setTimeout(fitPreview, 180);
    const container = map.getContainer();
    const observer = typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(fitPreview)
      : null;
    observer?.observe(container);

    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.clearTimeout(timer);
      observer?.disconnect();
    };
  }, [east, map, mission.id, mission.updated_at, mission.zoom, north, south, west]);

  return null;
}

export function MissionStat({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return (
    <div className="rounded-lg border border-border bg-bg-card p-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">{label}</div>
      <div className="mt-1 font-mono text-sm font-semibold text-orange-300">{value}</div>
      <div className="mt-0.5 truncate text-xs text-slate-500">{detail}</div>
    </div>
  );
}
