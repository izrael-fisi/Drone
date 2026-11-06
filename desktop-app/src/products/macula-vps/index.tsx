import {
  Activity,
  Archive,
  Camera,
  Crosshair,
  Map as MapIcon,
  Radio,
  Route as RouteIcon,
  Server,
  SlidersHorizontal,
} from "lucide-react";
import { useMemo } from "react";
import type { BottomDockTabId } from "../../lib/shellStore";
import { useAppStore } from "../../lib/store";
import type {
  PlatformMapFeature,
  PlatformMapLayer,
  ProductBottomDockProps,
  ProductCommand,
  ProductModule,
  ProductStatusSummary,
} from "../../platform/types";
import { BOTTOM_TABS, BottomDock } from "./BottomDock";
import { useVpsWorkingLayers } from "./map/layers";
import { useVpsTelemetry } from "./map/telemetry";
import { VpsMapTools } from "./map/VpsMapTools";
import { useMaculaRuntimeModel } from "./model";
import { CalibrationPanel, CameraPanel } from "./panels/CameraPanels";
import { DeviceSettingsPanel } from "./panels/DeviceSettingsPanel";
import { DiagnosticsSettingsPanel } from "./panels/DiagnosticsSettingsPanel";
import { FlightsPanel } from "./panels/FlightsPanel";
import { GroundControlPanel } from "./panels/GroundControlPanel";
import { MapsPanel } from "./panels/MapsPanel";
import { MavSettingsPanel } from "./panels/MavSettingsPanel";
import { MissionPanel } from "./panels/MissionPanel";

export const MACULA_VPS_ID = "macula-vps";
const ACCENT = "#FF6600";

function MaculaBottomDock({ open, tab, onOpenChange, onTabChange }: ProductBottomDockProps) {
  const model = useMaculaRuntimeModel();
  return (
    <BottomDock
      open={open}
      tab={tab as BottomDockTabId}
      model={model}
      onOpenChange={onOpenChange}
      onTabChange={onTabChange}
    />
  );
}

function useMaculaStatus(): ProductStatusSummary {
  const model = useMaculaRuntimeModel();
  const devices = useAppStore((state) => state.devices);
  const online = model.edgeConnectionState === "online";
  const health = online ? "ready" : model.activeDevice ? "attention" : devices.length ? "offline" : "idle";
  return {
    health,
    headline: model.activeDevice
      ? `${model.activeDevice.name} - edge ${model.edgeConnectionState}`
      : devices.length
        ? "No active VPS device selected"
        : "No VPS devices configured",
    metrics: [
      { label: "Devices", value: devices.length },
      { label: "Maps", value: model.completedMaps.length },
      { label: "Flights", value: model.flights.length },
    ],
  };
}

function useMaculaMapLayers(): PlatformMapLayer[] {
  const model = useMaculaRuntimeModel();
  const working = useVpsWorkingLayers(MACULA_VPS_ID);
  const coverage = useMemo<PlatformMapLayer>(() => {
    const features: PlatformMapFeature[] = model.completedMaps
      .filter(({ bounds }) =>
        [bounds.latMin, bounds.latMax, bounds.lonMin, bounds.lonMax].every(Number.isFinite) &&
        bounds.latMax > bounds.latMin &&
        bounds.lonMax > bounds.lonMin,
      )
      .map((map) => ({
        id: `vps-map-${map.id}`,
        kind: "area" as const,
        label: map.areaName,
        positions: [
          [map.bounds.latMax, map.bounds.lonMin],
          [map.bounds.latMax, map.bounds.lonMax],
          [map.bounds.latMin, map.bounds.lonMax],
          [map.bounds.latMin, map.bounds.lonMin],
        ],
      }));
    return {
      id: "macula-vps.map-coverage",
      productId: MACULA_VPS_ID,
      label: "VPS map coverage",
      color: ACCENT,
      features,
      defaultVisible: true,
    };
  }, [model.completedMaps]);
  return useMemo(() => [coverage, ...working], [coverage, working]);
}

function useMaculaCommands(): ProductCommand[] {
  const { devices, regions, setActiveDevice } = useAppStore();
  const model = useMaculaRuntimeModel();
  const hasDevice = Boolean(model.activeDevice);

  return useMemo<ProductCommand[]>(() => {
    const bottomTabs = BOTTOM_TABS.map<ProductCommand>((tab) => ({
      id: `macula-tab-${tab.id}`,
      label: tab.label,
      detail: tab.requiresDevice ? "Requires active device" : "Bottom panel",
      group: "Bottom Dock",
      Icon: tab.Icon,
      disabled: tab.requiresDevice && !hasDevice,
      run: (ctx) => ctx.openBottomTab(tab.id),
    }));

    const settings: ProductCommand[] = [
      { id: "macula-settings-device", label: "Device Settings", detail: "Connection, recording, device mode, SSH", group: "Settings", Icon: Server, run: (ctx) => ctx.openPanel("vehicle") },
      { id: "macula-settings-gcs", label: "Ground Control Settings", detail: "QGC, Mission Planner, ArduPilot compatibility", group: "Settings", Icon: Radio, run: (ctx) => ctx.openPanel("ground-control") },
      { id: "macula-settings-mav", label: "MAV Settings", detail: "MAVProxy and VPS parameters", group: "Settings", Icon: Radio, run: (ctx) => ctx.openPanel("mav") },
      { id: "macula-settings-diagnostics", label: "Diagnostics Settings", detail: "Remote support and security scan", group: "Settings", Icon: Activity, run: (ctx) => ctx.openPanel("diagnostics-settings") },
    ];

    const maps = regions.slice(0, 10).map<ProductCommand>((region) => ({
      id: `macula-map-${region.id}`,
      label: region.name,
      detail: region.lifecycle_state ? `Map - ${region.lifecycle_state}` : "Map",
      group: "Maps",
      keywords: `${region.output_path} ${region.location_label ?? ""}`,
      Icon: MapIcon,
      run: (ctx) => ctx.openPanel("maps"),
    }));

    const deviceEntries = devices.slice(0, 10).map<ProductCommand>((device) => ({
      id: `macula-device-${device.id}`,
      label: device.name,
      detail: device.host ? `Device - ${device.host}` : "Device",
      group: "Devices",
      keywords: `${device.mavlink_endpoint ?? ""} ${device.remote_project_path ?? ""}`,
      Icon: Server,
      run: (ctx) => {
        setActiveDevice(device.id);
        ctx.openPanel("vehicle");
      },
    }));

    return [...bottomTabs, ...settings, ...maps, ...deviceEntries];
  }, [devices, hasDevice, regions, setActiveDevice]);
}

export const maculaVpsProduct: ProductModule = {
  id: MACULA_VPS_ID,
  name: "Macula VPS",
  shortName: "Macula",
  tagline: "GNSS-denied visual positioning against preloaded maps",
  category: "navigation",
  Icon: Crosshair,
  accent: ACCENT,
  entitlementKey: "macula_vps",
  legacyDefault: true,
  defaultPanelId: "maps",
  panels: [
    {
      id: "maps",
      label: "Maps",
      Icon: MapIcon,
      command: { label: "Manage Maps", detail: "Map library, active map, edge cache" },
      Component: ({ ctx }) => {
        const model = useMaculaRuntimeModel();
        const regions = useAppStore((state) => state.regions);
        return <MapsPanel model={model} regions={regions} onDownloadOrgMap={ctx.closeDock} />;
      },
    },
    {
      id: "missions",
      label: "Missions",
      Icon: RouteIcon,
      command: { label: "Missions", detail: "Saved map cuts and waypoint overlays" },
      Component: () => <MissionPanel />,
    },
    {
      id: "vehicle",
      label: "Device",
      Icon: Server,
      command: { label: "Manage Vehicles", detail: "Vehicle config and runtime state" },
      Component: ({ ctx }) => <DeviceSettingsPanel model={useMaculaRuntimeModel()} setBottomDockTab={ctx.openBottomTab} />,
    },
    {
      id: "ground-control",
      label: "Ground Control",
      Icon: Radio,
      command: { label: "Ground Control", detail: "QGroundControl, Mission Planner, ArduPilot compatibility" },
      Component: ({ ctx }) => <GroundControlPanel model={useMaculaRuntimeModel()} setBottomDockTab={ctx.openBottomTab} />,
    },
    {
      id: "camera",
      label: "Camera",
      Icon: Camera,
      command: { label: "Manage Cameras", detail: "Camera config and vision pipeline" },
      Component: ({ ctx }) => <CameraPanel model={useMaculaRuntimeModel()} pushRightDock={ctx.pushPanel} />,
    },
    {
      id: "calibration",
      label: "Calibration",
      Icon: Camera,
      showInRail: false,
      railParent: "camera",
      command: { label: "Calibration", detail: "Guided camera capture" },
      Component: () => <CalibrationPanel model={useMaculaRuntimeModel()} />,
    },
    {
      id: "flights",
      label: "Flights",
      Icon: Archive,
      command: { label: "View All Flights", detail: "Recordings, sync, playback evidence" },
      Component: () => <FlightsPanel model={useMaculaRuntimeModel()} />,
    },
    {
      id: "mav",
      label: "MAV Settings",
      Icon: SlidersHorizontal,
      Component: ({ ctx }) => <MavSettingsPanel model={useMaculaRuntimeModel()} setBottomDockTab={ctx.openBottomTab} />,
    },
    {
      id: "diagnostics-settings",
      label: "Diagnostics",
      Icon: Activity,
      Component: ({ ctx }) => <DiagnosticsSettingsPanel model={useMaculaRuntimeModel()} setBottomDockTab={ctx.openBottomTab} />,
    },
  ],
  bottomDock: {
    tabIds: BOTTOM_TABS.map((tab) => tab.id),
    defaultTabId: "system-status",
    Component: MaculaBottomDock,
  },
  MapOverlay: VpsMapTools,
  useBackground: useVpsTelemetry,
  useStatus: useMaculaStatus,
  useMapLayers: useMaculaMapLayers,
  useCommands: useMaculaCommands,
};
