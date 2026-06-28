import type { Device, FlightRecord, Region } from "./types";

export type EdgeConnectionState = "online" | "offline" | "unknown" | "error";

export interface OperatorLocalMap {
  workflowId: string;
  name: string;
  path: string;
  sizeMb?: number;
  state: "local" | "built" | "stale" | "failed";
}

export interface OperatorEdgeMap {
  name: string;
  active: boolean;
  uploadedAt?: string;
  sourcePath?: string;
  state: "uploaded" | "active" | "missing" | "failed";
}

export interface OperatorMapRequest {
  id: string;
  workflowId: string;
  areaName: string;
  kind: "pending" | "completed";
  lifecycle: string;
  source: string;
  local?: OperatorLocalMap;
  edge?: OperatorEdgeMap;
  isActive: boolean;
  bounds: {
    latMin: number;
    latMax: number;
    lonMin: number;
    lonMax: number;
  };
  homePosition?: {
    lat: number;
    lon: number;
    altM?: number | null;
  };
}

export interface OperatorRuntimeModel {
  edgeConnectionState: EdgeConnectionState;
  activeDevice?: Device;
  activeMap?: OperatorMapRequest;
  mapRequests: OperatorMapRequest[];
  localMaps: OperatorLocalMap[];
  edgeMaps: OperatorEdgeMap[];
  completedMaps: OperatorMapRequest[];
  pendingMaps: OperatorMapRequest[];
  flights: FlightRecord[];
}

function mapState(region: Region) {
  return region.lifecycle_state ?? (region.last_downloaded ? "local" : "stale");
}

function mapWorkflowId(region: Region) {
  return region.id || region.output_path || region.name;
}

export function adaptRegionToOperatorMap(region: Region): OperatorMapRequest {
  const workflowId = mapWorkflowId(region);
  const lifecycle = mapState(region);
  const isActive = lifecycle === "active" || Boolean(region.active_bundle_path);
  const local: OperatorLocalMap | undefined = region.last_downloaded || lifecycle === "built" || lifecycle === "local"
    ? {
        workflowId,
        name: region.name,
        path: region.output_path,
        sizeMb: region.file_size_mb,
        state: lifecycle === "failed" ? "failed" : lifecycle === "stale" ? "stale" : lifecycle === "built" ? "built" : "local",
      }
    : undefined;
  const edge: OperatorEdgeMap | undefined = region.active_bundle_path || lifecycle === "uploaded" || lifecycle === "active"
    ? {
        name: region.name,
        active: isActive,
        uploadedAt: region.runtime_state?.uploaded_at ?? region.last_downloaded,
        sourcePath: region.active_bundle_path,
        state: lifecycle === "failed" ? "failed" : lifecycle === "active" ? "active" : "uploaded",
      }
    : undefined;

  return {
    id: region.id,
    workflowId,
    areaName: region.name,
    kind: local || edge ? "completed" : "pending",
    lifecycle,
    source: region.source ?? "folder",
    local,
    edge,
    isActive,
    bounds: {
      latMin: region.lat_min,
      latMax: region.lat_max,
      lonMin: region.lon_min,
      lonMax: region.lon_max,
    },
    homePosition: region.home_position
      ? {
          lat: region.home_position.lat,
          lon: region.home_position.lon,
          altM: region.home_position.alt_m,
        }
      : undefined,
  };
}

export function deriveOperatorRuntimeModel({
  devices,
  regions,
  activeDeviceId,
}: {
  devices: Device[];
  regions: Region[];
  activeDeviceId: string | null;
}): OperatorRuntimeModel {
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const edgeConnectionState: EdgeConnectionState = activeDevice
    ? activeDevice.remote_project_path
      ? "online"
      : "unknown"
    : "offline";
  const mapRequests = regions.map(adaptRegionToOperatorMap);
  const activeMap =
    mapRequests.find((map) => map.isActive) ??
    mapRequests.find((map) => map.local || map.edge) ??
    mapRequests[0];
  const localMaps = mapRequests.flatMap((map) => (map.local ? [map.local] : []));
  const edgeMaps = mapRequests.flatMap((map) => (map.edge ? [map.edge] : []));

  return {
    edgeConnectionState,
    activeDevice,
    activeMap,
    mapRequests,
    localMaps,
    edgeMaps,
    completedMaps: mapRequests.filter((map) => map.kind === "completed"),
    pendingMaps: mapRequests.filter((map) => map.kind === "pending"),
    flights: [],
  };
}
