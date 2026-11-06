import { useState } from "react";
import type { BottomDockTabId } from "../../../lib/shellStore";
import { useAppStore } from "../../../lib/store";
import { cmd } from "../../../lib/tauri";
import type { Device, EdgeApiMissionPlannerStatus, EdgeApiQGroundControlStatus } from "../../../lib/types";
import { cn } from "../../../lib/utils";
import type { OperatorRuntimeModel } from "../../../lib/operatorRuntimeAdapter";
import { PanelAction, PanelStack, SectionLabel, StateCard, StatusRow, displayLabel } from "../../../platform/ui";

export function GroundControlPanel({
  model,
  setBottomDockTab,
}: {
  model: OperatorRuntimeModel;
  setBottomDockTab: (tab: BottomDockTabId) => void;
}) {
  const { devices, updateDevice } = useAppStore();
  const device = model.activeDevice;
  const edgeApiUrl = device?.host ? `http://${device.host}:5000` : "";
  const [qgcStatus, setQgcStatus] = useState<EdgeApiQGroundControlStatus | null>(null);
  const [missionPlannerStatus, setMissionPlannerStatus] = useState<EdgeApiMissionPlannerStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [launching, setLaunching] = useState<"qgc" | "mission-planner" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const setAutopilot = async (autopilot: NonNullable<Device["autopilot"]>) => {
    if (!device) {
      setMessage("Select a device before changing autopilot compatibility");
      return;
    }
    const nextDevice: Device = { ...device, autopilot };
    const nextDevices = devices.map((item) => (item.id === device.id ? nextDevice : item));
    await cmd.saveDevices(nextDevices);
    updateDevice(nextDevice);
    setMessage(`Autopilot compatibility set to ${autopilot === "ardupilot" ? "ArduPilot" : "PX4"}`);
  };

  const refreshGroundControl = async () => {
    if (!edgeApiUrl) {
      setMessage("Connect a companion computer before checking ground-control tools");
      return;
    }
    setChecking(true);
    setMessage("checking ground-control tools");
    try {
      const [qgc, missionPlanner] = await Promise.all([
        cmd.edgeApiQGroundControlStatus(edgeApiUrl),
        cmd.edgeApiMissionPlannerStatus(edgeApiUrl),
      ]);
      setQgcStatus(qgc);
      setMissionPlannerStatus(missionPlanner);
      setMessage("ground-control status refreshed");
    } catch (error) {
      setMessage(`Ground-control API unavailable: ${String(error)}`);
    } finally {
      setChecking(false);
    }
  };

  const launchQGroundControl = async () => {
    if (!edgeApiUrl) {
      setMessage("Connect a companion computer before launching QGroundControl");
      return;
    }
    setLaunching("qgc");
    setMessage("launching QGroundControl");
    try {
      const result = await cmd.edgeApiQGroundControlLaunch(edgeApiUrl, true);
      if (result.status) setQgcStatus(result.status);
      setMessage(result.ok && result.launched ? `QGroundControl launched${result.pid ? `: PID ${result.pid}` : ""}` : result.message ?? "QGroundControl launch did not start");
    } catch (error) {
      setMessage(`QGroundControl launch failed: ${String(error)}`);
    } finally {
      setLaunching(null);
    }
  };

  const launchMissionPlanner = async () => {
    if (!edgeApiUrl) {
      setMessage("Connect a companion computer before launching Mission Planner");
      return;
    }
    setLaunching("mission-planner");
    setMessage("launching Mission Planner");
    try {
      const result = await cmd.edgeApiMissionPlannerLaunch(edgeApiUrl, true);
      if (result.status) setMissionPlannerStatus(result.status);
      setMessage(result.ok && result.launched ? `Mission Planner launched${result.pid ? `: PID ${result.pid}` : ""}` : result.message ?? "Mission Planner launch did not start");
    } catch (error) {
      setMessage(`Mission Planner launch failed: ${String(error)}`);
    } finally {
      setLaunching(null);
    }
  };

  const qgcInstalled = Boolean(qgcStatus?.installed);
  const qgcCanLaunch = Boolean(qgcStatus?.launch_available);
  const missionPlannerInstalled = Boolean(missionPlannerStatus?.installed);
  const missionPlannerCanLaunch = Boolean(missionPlannerStatus?.launch_available);
  const qgcDisplayLabel = displayLabel(qgcStatus?.display);
  const missionPlannerDisplayLabel = displayLabel(missionPlannerStatus?.display);
  const serialOwner =
    qgcStatus?.serial_users ||
    missionPlannerStatus?.serial_users ||
    "";

  return (
    <PanelStack title="Ground Control" subtitle={device?.name ?? "No active device"}>
      <StateCard
        label="Autopilot"
        value={device?.autopilot === "ardupilot" ? "ArduPilot" : "PX4"}
        detail={device?.mavlink_endpoint ?? "MAVLink endpoint not configured"}
        tone={device ? "orange" : "muted"}
      />
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => setAutopilot("px4")}
          disabled={!device}
          className={cn(
            "h-9 rounded-md border px-3 text-xs font-semibold transition-colors",
            device?.autopilot !== "ardupilot"
              ? "border-orange-500/70 bg-orange-500 text-black"
              : "border-border bg-bg-card text-slate-400 hover:border-orange-500/35 hover:text-slate-100",
            !device && "cursor-not-allowed opacity-50",
          )}
        >
          PX4
        </button>
        <button
          type="button"
          onClick={() => setAutopilot("ardupilot")}
          disabled={!device}
          className={cn(
            "h-9 rounded-md border px-3 text-xs font-semibold transition-colors",
            device?.autopilot === "ardupilot"
              ? "border-orange-500/70 bg-orange-500 text-black"
              : "border-border bg-bg-card text-slate-400 hover:border-orange-500/35 hover:text-slate-100",
            !device && "cursor-not-allowed opacity-50",
          )}
        >
          ArduPilot
        </button>
      </div>

      <SectionLabel>Tools</SectionLabel>
      <StateCard
        label="QGroundControl"
        value={qgcStatus ? (qgcInstalled ? (qgcStatus.running ? "Running" : "Installed") : "Not Found") : "Unknown"}
        detail={qgcStatus?.executable_path ?? qgcStatus?.appimage_path ?? "refresh to inspect device"}
        tone={qgcInstalled ? "orange" : "muted"}
      />
      <StateCard
        label="Mission Planner"
        value={missionPlannerStatus ? (missionPlannerInstalled ? (missionPlannerStatus.running ? "Running" : "Installed") : "Not Found") : "Unknown"}
        detail={missionPlannerStatus?.executable_path ?? missionPlannerStatus?.install_path ?? "Windows native / Linux Mono"}
        tone={missionPlannerInstalled ? "orange" : "muted"}
      />
      <StatusRow label="QGC Display" value={qgcDisplayLabel} healthy={Boolean(qgcStatus?.display?.available)} />
      <StatusRow label="Mission Planner Display" value={missionPlannerDisplayLabel} healthy={Boolean(missionPlannerStatus?.display?.available)} />
      <StatusRow label="Serial owner" value={serialOwner ? "in use" : "not reported"} healthy={!serialOwner} />
      <StatusRow label="Live telemetry" value="MAVLink position / PX4 + ArduPilot" healthy={Boolean(device?.mavlink_endpoint)} />
      <PanelAction
        label={checking ? "Checking GCS" : "Refresh GCS"}
        detail={message ?? "Detect QGroundControl, Mission Planner, display, and serial owner"}
        onClick={refreshGroundControl}
        disabled={checking || !edgeApiUrl}
      />
      <PanelAction
        label={launching === "qgc" ? "Launching QGC" : "Launch QGroundControl"}
        detail={qgcCanLaunch ? "Stops telemetry bridge before launch" : qgcStatus?.message ?? "Refresh status first"}
        onClick={launchQGroundControl}
        disabled={launching !== null || !qgcCanLaunch}
      />
      <PanelAction
        label={launching === "mission-planner" ? "Launching Mission Planner" : "Launch Mission Planner"}
        detail={missionPlannerCanLaunch ? "Linux launch uses Mono; Windows native remains recommended" : missionPlannerStatus?.message ?? "Refresh status first"}
        onClick={launchMissionPlanner}
        disabled={launching !== null || !missionPlannerCanLaunch}
      />

      <SectionLabel>Compatibility</SectionLabel>
      <StatusRow label="QGroundControl" value="PX4 and ArduPilot MAVLink" healthy />
      <StatusRow label="Mission Planner" value="ArduPilot primary GCS" healthy={device?.autopilot === "ardupilot"} />
      <StatusRow label="Plan files" value=".plan import plus ArduPilot waypoint path" healthy />
      <PanelAction label="MAVLink Parameters" detail="Open bottom parameter dock" onClick={() => setBottomDockTab("parameters")} />
      <PanelAction label="MAVLink Messages" detail="Open bottom message stream" onClick={() => setBottomDockTab("messages")} disabled={!device} />
    </PanelStack>
  );
}
