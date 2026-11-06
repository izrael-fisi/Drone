import { Activity, Loader2, Search, Server } from "lucide-react";
import { useEffect, useState } from "react";
import { candidateHost, candidateName, discoveryStatusSummary, loadDiscoveryHistory, mergeDiscoveryHistory, saveDiscoveryHistory } from "../../../lib/discovery";
import type { BottomDockTabId } from "../../../lib/shellStore";
import { useAppStore } from "../../../lib/store";
import { cmd } from "../../../lib/tauri";
import type { Device, LocalNetworkHint, PiDiscoveryCandidate } from "../../../lib/types";
import { cn } from "../../../lib/utils";
import type { OperatorRuntimeModel } from "../../../lib/operatorRuntimeAdapter";
import { CompactField, EmptyLine, PanelAction, PanelStack, SectionLabel, StateCard, StatusRow } from "../../../platform/ui";

export function DeviceSettingsPanel({
  model,
  setBottomDockTab,
}: {
  model: OperatorRuntimeModel;
  setBottomDockTab: (tab: BottomDockTabId) => void;
}) {
  const { devices, addDevice, updateDevice, setActiveDevice } = useAppStore();
  const device = model.activeDevice;
  const connected = model.edgeConnectionState === "online";
  const [deviceName, setDeviceName] = useState(device?.name ?? "Companion Compute");
  const [ipAddress, setIpAddress] = useState(device?.host ?? "");
  const [sshPort, setSshPort] = useState(String(device?.port ?? 22));
  const [companionUser, setCompanionUser] = useState(device?.username ?? "pi");
  const [password, setPassword] = useState(device?.auth?.type === "Password" ? device.auth.password : "");
  const [runtimePath, setRuntimePath] = useState(device?.remote_project_path ?? "");
  const [mavlinkEndpoint, setMavlinkEndpoint] = useState(device?.mavlink_endpoint ?? "");
  const [saving, setSaving] = useState(false);
  const [heartbeatChecking, setHeartbeatChecking] = useState(false);
  const [connectionMessage, setConnectionMessage] = useState<string | null>(null);
  const [edgeApiState, setEdgeApiState] = useState<"unknown" | "online" | "offline">("unknown");
  const [newDeviceOpen, setNewDeviceOpen] = useState(false);
  const [newDeviceIp, setNewDeviceIp] = useState("");
  const [newDeviceUsername, setNewDeviceUsername] = useState("pi");
  const [newDevicePassword, setNewDevicePassword] = useState("");
  const [newDeviceSaving, setNewDeviceSaving] = useState(false);
  const [newDeviceMode, setNewDeviceMode] = useState<"automatic" | "custom">("automatic");
  const [discovering, setDiscovering] = useState(false);
  const [discoveryCandidates, setDiscoveryCandidates] = useState<PiDiscoveryCandidate[]>(() => loadDiscoveryHistory());
  const [networkHints, setNetworkHints] = useState<LocalNetworkHint[]>([]);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);

  useEffect(() => {
    setDeviceName(device?.name ?? "Companion Compute");
    setIpAddress(device?.host ?? "");
    setSshPort(String(device?.port ?? 22));
    setCompanionUser(device?.username ?? "pi");
    setPassword(device?.auth?.type === "Password" ? device.auth.password : "");
    setRuntimePath(device?.remote_project_path ?? "");
    setMavlinkEndpoint(device?.mavlink_endpoint ?? "");
    setConnectionMessage(null);
    setEdgeApiState("unknown");
  }, [device?.auth, device?.host, device?.id, device?.mavlink_endpoint, device?.name, device?.port, device?.remote_project_path, device?.username]);

  useEffect(() => {
    if (!newDeviceOpen || newDeviceMode !== "automatic") return;
    cmd.localNetworkHints().then(setNetworkHints).catch(() => setNetworkHints([]));
  }, [newDeviceMode, newDeviceOpen]);

  const promptForPassword = () => {
    const nextPassword = window.prompt("Password for companion compute user", "");
    if (nextPassword === null) return;
    setPassword(nextPassword);
    setConnectionMessage(nextPassword ? "password staged" : "password cleared");
  };
  const edgeApiUrl = ipAddress.trim() ? `http://${ipAddress.trim()}:5000` : "";

  const saveConnection = async () => {
    const parsedPort = Number.parseInt(sshPort, 10);
    const nextDevice: Device = {
      ...(device ?? {
        id: `device-${Date.now()}`,
        kind: "pi5",
      }),
      name: deviceName.trim() || "Companion Compute",
      host: ipAddress.trim() || undefined,
      port: Number.isFinite(parsedPort) ? parsedPort : undefined,
      username: companionUser.trim() || undefined,
      auth: password ? { type: "Password", password } : device?.auth,
      remote_project_path: runtimePath.trim() || undefined,
      mavlink_endpoint: mavlinkEndpoint.trim() || undefined,
      autopilot: device?.autopilot ?? "px4",
    };
    delete nextDevice.vision_pipeline;
    delete nextDevice.feature_method;
    const existing = devices.some((item) => item.id === nextDevice.id);
    const nextDevices = existing
      ? devices.map((item) => (item.id === nextDevice.id ? nextDevice : item))
      : [...devices, nextDevice];

    setSaving(true);
    setConnectionMessage(null);
    try {
      await cmd.saveDevices(nextDevices);
      if (existing) updateDevice(nextDevice);
      else addDevice(nextDevice);
      setActiveDevice(nextDevice.id);
      if (nextDevice.host) {
        try {
          const api = await cmd.edgeApiDevice(`http://${nextDevice.host}:5000`);
          setEdgeApiState(api.ok ? "online" : "offline");
          setConnectionMessage(api.ok ? `edge API online: ${api.hostname ?? nextDevice.host}` : "saved; edge API unavailable");
        } catch {
          setEdgeApiState("offline");
          setConnectionMessage("saved; edge API offline");
        }
      } else {
        setConnectionMessage("saved and selected");
      }
    } catch (error) {
      setConnectionMessage(String(error));
    } finally {
      setSaving(false);
    }
  };

  const addNewDeviceFromConnection = async ({
    host,
    port,
    username,
    password: nextPassword,
    name,
  }: {
    host: string;
    port: number;
    username: string;
    password?: string;
    name: string;
  }) => {
    if (!host) return;
    const existingDevice = devices.find((item) => item.kind === "pi5" && item.host === host && (item.port ?? 22) === port);
    const nextDevice: Device = {
      ...(existingDevice ?? {}),
      id: `device-${Date.now()}`,
      name,
      kind: "pi5",
      host,
      port,
      username,
      auth: nextPassword ? { type: "Password", password: nextPassword } : undefined,
      remote_project_path: `/home/${username}/Drone`,
      mavlink_endpoint: "serial:/dev/ttyACM0:921600",
      autopilot: "px4",
    };
    delete nextDevice.vision_pipeline;
    delete nextDevice.feature_method;
    const nextDevices = existingDevice
      ? devices.map((item) => (item.id === existingDevice.id ? { ...nextDevice, id: existingDevice.id } : item))
      : [...devices, nextDevice];
    const savedDevice = existingDevice ? { ...nextDevice, id: existingDevice.id } : nextDevice;

    setNewDeviceSaving(true);
    setConnectionMessage(null);
    try {
      await cmd.saveDevices(nextDevices);
      if (existingDevice) updateDevice(savedDevice);
      else addDevice(savedDevice);
      setActiveDevice(savedDevice.id);
      setNewDeviceOpen(false);
      setNewDeviceIp("");
      setNewDeviceUsername("pi");
      setNewDevicePassword("");
      try {
        const api = await cmd.edgeApiDevice(`http://${host}:5000`);
        setEdgeApiState(api.ok ? "online" : "offline");
        setConnectionMessage(api.ok ? `edge API online: ${api.hostname ?? host}` : "device saved; edge API unavailable");
      } catch {
        setEdgeApiState("offline");
        setConnectionMessage("device saved; edge API offline");
      }
    } catch (error) {
      setConnectionMessage(String(error));
    } finally {
      setNewDeviceSaving(false);
    }
  };

  const connectNewDevice = async () => {
    const host = newDeviceIp.trim();
    const username = newDeviceUsername.trim() || "pi";
    await addNewDeviceFromConnection({
      host,
      port: 22,
      username,
      password: newDevicePassword,
      name: `Companion ${host}`,
    });
  };

  const connectDiscoveredDevice = async (candidate: PiDiscoveryCandidate) => {
    const username = newDeviceUsername.trim() || "pi";
    await addNewDeviceFromConnection({
      host: candidateHost(candidate),
      port: candidate.port,
      username,
      password: newDevicePassword,
      name: candidateName(candidate),
    });
  };

  const runDiscovery = async () => {
    setDiscovering(true);
    setDiscoveryError(null);
    try {
      const seedHosts = devices
        .filter((item) => item.kind === "pi5" && item.host)
        .map((item) => item.host!)
        .concat(newDeviceIp.trim() ? [newDeviceIp.trim()] : []);
      const candidates = await cmd.discoverPiDevices(seedHosts, 22);
      const hints = await cmd.localNetworkHints().catch(() => networkHints);
      const next = mergeDiscoveryHistory(discoveryCandidates, candidates);
      setDiscoveryCandidates(next);
      setNetworkHints(hints);
      saveDiscoveryHistory(next);
    } catch (error) {
      setDiscoveryError(String(error));
    } finally {
      setDiscovering(false);
    }
  };

  const requestHeartbeat = async () => {
    if (!mavlinkEndpoint.trim()) {
      setConnectionMessage("MAVLink endpoint required for heartbeat");
      setBottomDockTab("system-status");
      return;
    }
    if (!edgeApiUrl) {
      setConnectionMessage("Edge API URL required for heartbeat");
      setBottomDockTab("system-status");
      return;
    }
    setHeartbeatChecking(true);
    setConnectionMessage("checking heartbeat");
    try {
      const result = await cmd.edgeApiMavlinkHeartbeat(edgeApiUrl, mavlinkEndpoint.trim(), 4);
      if (result.ok && result.connected) {
        setEdgeApiState("online");
        setConnectionMessage(`heartbeat ok: system ${result.target_system ?? "?"}`);
        setBottomDockTab("messages");
      } else {
        setConnectionMessage(result.message ?? "heartbeat not found");
        setBottomDockTab("system-status");
      }
    } catch (error) {
      setEdgeApiState("offline");
      setConnectionMessage(`edge API offline: ${String(error)}`);
      setBottomDockTab("system-status");
    } finally {
      setHeartbeatChecking(false);
    }
  };

  const discoverySummary = discoveryStatusSummary(discoveryCandidates, networkHints);

  return (
    <PanelStack
      title="Device Settings"
      subtitle={device?.name ?? "No device selected"}
      action={
        <button
          type="button"
          onClick={() => setNewDeviceOpen((current) => !current)}
          className="h-8 rounded-md border border-orange-500/70 bg-orange-500 px-3 text-xs font-semibold text-black shadow-[0_8px_28px_rgba(255,102,0,0.22)] transition-colors hover:bg-orange-400"
        >
          New Device+
        </button>
      }
    >
      {newDeviceOpen && (
        <div className="rounded-lg border border-orange-500/35 bg-black/70 p-3 shadow-[0_14px_44px_rgba(0,0,0,0.36)]">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-100">New Device</div>
              <div className="text-xs text-slate-500">
                {newDeviceMode === "automatic" ? discoverySummary.label : "Add companion compute credentials"}
              </div>
            </div>
            <span className="rounded-full border border-orange-500/30 bg-orange-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-orange-200">
              Pi
            </span>
          </div>

          <div className="mb-3 grid rounded-full border border-white/10 bg-bg-base/80 p-1">
            <div className="grid grid-cols-2 gap-1">
              {(["automatic", "custom"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setNewDeviceMode(mode)}
                  className={cn(
                    "h-8 rounded-full text-xs font-semibold capitalize transition-colors",
                    newDeviceMode === mode
                      ? "bg-orange-500 text-black shadow-[0_8px_24px_rgba(255,102,0,0.22)]"
                      : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-200",
                  )}
                >
                  {mode}
                </button>
              ))}
            </div>
          </div>

          {newDeviceMode === "automatic" ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <CompactField label="Username" value={newDeviceUsername} onChange={setNewDeviceUsername} placeholder="pi" />
                <CompactField label="Password" value={newDevicePassword} onChange={setNewDevicePassword} placeholder="Password" secret />
              </div>
              <div className="rounded-lg border border-white/10 bg-bg-card p-3">
                <div className="flex items-center gap-3">
                  <Activity size={15} className={discoverySummary.status === "ready" ? "text-status-ready" : "text-orange-300"} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-semibold text-slate-200">{discoverySummary.label}</div>
                    <div className="truncate text-[10px] text-slate-500">{discoverySummary.detail}</div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={runDiscovery}
                  disabled={discovering}
                  className="mt-3 flex h-8 w-full items-center justify-center gap-2 rounded-md border border-orange-500/70 bg-orange-500 text-xs font-semibold text-black transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:opacity-45"
                >
                  {discovering ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                  {discovering ? "Scanning" : "Scan Network"}
                </button>
              </div>
              {discoveryError && (
                <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                  {discoveryError}
                </div>
              )}
              <div className="max-h-52 space-y-2 overflow-y-auto">
                {discoveryCandidates.length === 0 ? (
                  <EmptyLine text="No discovered devices yet" />
                ) : (
                  discoveryCandidates.map((candidate) => (
                    <div key={`${candidate.host}-${candidate.port}`} className="rounded-lg border border-white/10 bg-bg-card p-2">
                      <div className="flex items-center gap-2">
                        <Server size={14} className={candidate.ssh_open ? "text-status-ready" : "text-slate-500"} />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-xs font-semibold text-slate-200">{candidateName(candidate)}</div>
                          <div className="truncate font-mono text-[10px] text-slate-500">
                            {candidateHost(candidate)}:{candidate.port} / {candidate.ssh_open ? "ssh open" : "ssh closed"}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => connectDiscoveredDevice(candidate)}
                          disabled={newDeviceSaving}
                          className="h-7 rounded-md border border-orange-500/60 bg-orange-500 px-2 text-[11px] font-semibold text-black transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:opacity-45"
                        >
                          Connect
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <CompactField label="IP Address" value={newDeviceIp} onChange={setNewDeviceIp} placeholder="192.168.1.42" />
              <CompactField label="Username" value={newDeviceUsername} onChange={setNewDeviceUsername} placeholder="pi" />
              <CompactField label="Password" value={newDevicePassword} onChange={setNewDevicePassword} placeholder="Password" secret />
            </div>
          )}

          <div className="mt-3 grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => {
                setNewDeviceOpen(false);
                setNewDeviceIp("");
                setNewDeviceUsername("pi");
                setNewDevicePassword("");
              }}
              className="h-8 rounded-md border border-border bg-bg-card text-xs font-medium text-slate-400 transition-colors hover:border-white/20 hover:text-slate-100"
            >
              Cancel
            </button>
            {newDeviceMode === "custom" ? (
              <button
                type="button"
                onClick={connectNewDevice}
                disabled={newDeviceSaving || !newDeviceIp.trim()}
                className="h-8 rounded-md border border-orange-500/70 bg-orange-500 text-xs font-semibold text-black transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:opacity-45"
              >
                {newDeviceSaving ? "Connecting" : "Connect"}
              </button>
            ) : (
              <button
                type="button"
                onClick={runDiscovery}
                disabled={discovering}
                className="h-8 rounded-md border border-orange-500/70 bg-orange-500 text-xs font-semibold text-black transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:opacity-45"
              >
                {discovering ? "Scanning" : "Scan"}
              </button>
            )}
          </div>
        </div>
      )}
      <StateCard label="Connection" value={connected ? "Online" : "Offline"} detail={ipAddress || "no IP address"} tone={connected ? "green" : "muted"} />
      <SectionLabel>Connection</SectionLabel>
      <CompactField label="Device Name" value={deviceName} onChange={setDeviceName} placeholder="Companion Compute" />
      <CompactField label="IP Address" value={ipAddress} onChange={setIpAddress} placeholder="192.168.1.42" />
      <div className="grid grid-cols-2 gap-2">
        <CompactField label="SSH Port" value={sshPort} onChange={setSshPort} placeholder="22" />
        <div className="space-y-2">
          <CompactField label="Username" value={companionUser} onChange={setCompanionUser} placeholder="pi" />
          <button
            type="button"
            onClick={promptForPassword}
            className="h-8 w-full rounded-md border border-border bg-bg-card px-3 text-left text-xs font-medium text-slate-300 transition-colors hover:border-orange-500/35 hover:bg-white/[0.04] hover:text-slate-100"
          >
            Password
            <span className="float-right font-mono text-[10px] text-slate-600">{password ? "set" : "unset"}</span>
          </button>
        </div>
      </div>
      <CompactField label="Runtime Path" value={runtimePath} onChange={setRuntimePath} placeholder="/home/pi/Drone" />
      <CompactField label="MAVLink Endpoint" value={mavlinkEndpoint} onChange={setMavlinkEndpoint} placeholder="udp:14550" />
      <StatusRow label="Device URL" value={edgeApiUrl || "not configured"} healthy={Boolean(edgeApiUrl)} />
      <StatusRow label="Edge API" value={edgeApiState === "online" ? "online" : edgeApiState === "offline" ? "offline" : "not checked"} healthy={edgeApiState === "online"} />
      <PanelAction label={saving ? "Connecting" : "Connect"} detail={connectionMessage ?? "Save and select this companion compute"} onClick={saveConnection} disabled={saving || !ipAddress.trim()} />
      <PanelAction label={heartbeatChecking ? "Checking Heartbeat" : "Heartbeat"} detail={mavlinkEndpoint.trim() ? "Probe MAVLink through Edge API" : "Set MAVLink endpoint first"} onClick={requestHeartbeat} disabled={heartbeatChecking || !mavlinkEndpoint.trim()} />
      <PanelAction label="Recording On Boot" detail={connected ? "Runtime service not configured" : "Connect device to configure"} disabled />
      <PanelAction label="Storage" detail={connected ? "No storage scan attached" : "Device offline"} disabled />
      <PanelAction label="System Status" detail="Open bottom status tab" onClick={() => setBottomDockTab("system-status")} />
    </PanelStack>
  );
}
