import { useEffect, useState } from "react";
import {
  Server, HardDrive, Plus, Trash2, Edit2, Wifi, CheckCircle2, XCircle,
  Loader2, Eye, EyeOff, ShieldCheck, ShieldAlert, FolderOpen, KeyRound, Lock,
  Terminal, Play, Square, FileText, ChevronDown, ChevronUp,
  Cable, Cpu, BookOpen, Save, SlidersHorizontal,
} from "lucide-react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { cmd } from "../lib/tauri";
import { useAppStore } from "../lib/store";
import { cn, generateId } from "../lib/utils";
import { ModuleSetup } from "./PiSetup";
import type { Device } from "../lib/types";

type TestResult = {
  ok: boolean;
  msg: string;
  fingerprint?: string;
  fingerprintChanged?: boolean;
};
type TestState = "idle" | "testing" | TestResult;

function shellQuote(value: string) {
  return `'${value.replace(/'/g, "'\"'\"'")}'`;
}

function defaultRemotePath(username = "user") {
  return `/home/${username || "user"}/Drone`;
}

function deviceWithRuntimeDefaults(device: Device): Device {
  const username = device.username ?? "user";
  return {
    ...device,
    remote_project_path: device.remote_project_path ?? defaultRemotePath(username),
    mavlink_endpoint: device.mavlink_endpoint ?? "serial:/dev/ttyAMA0:921600",
    autopilot: device.autopilot ?? "px4",
  };
}

function DeviceConfigurationPanel({
  device,
  devices,
  updateDevice,
}: {
  device: Device;
  devices: Device[];
  updateDevice: (device: Device) => void;
}) {
  const normalized = deviceWithRuntimeDefaults(device);
  const [form, setForm] = useState({
    remotePath: normalized.remote_project_path ?? defaultRemotePath(normalized.username),
    mavlinkEndpoint: normalized.mavlink_endpoint ?? "serial:/dev/ttyAMA0:921600",
    autopilot: normalized.autopilot ?? "px4",
  });
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const next = deviceWithRuntimeDefaults(device);
    setForm({
      remotePath: next.remote_project_path ?? defaultRemotePath(next.username),
      mavlinkEndpoint: next.mavlink_endpoint ?? "serial:/dev/ttyAMA0:921600",
      autopilot: next.autopilot ?? "px4",
    });
    setSaved(false);
  }, [device.id, device.remote_project_path, device.mavlink_endpoint, device.autopilot]);

  const saveConfiguration = async () => {
    const { vision_pipeline: _visionPipeline, feature_method: _featureMethod, ...deviceWithoutPipeline } = device;
    const updated: Device = {
      ...deviceWithoutPipeline,
      remote_project_path: form.remotePath || defaultRemotePath(device.username),
      mavlink_endpoint: form.mavlinkEndpoint,
      autopilot: form.autopilot as "px4" | "ardupilot",
    };
    updateDevice(updated);
    await cmd.saveDevices(devices.map((candidate) => (candidate.id === updated.id ? updated : candidate)));
    setSaved(true);
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Module project path</label>
          <input
            className="input-field font-mono text-xs"
            value={form.remotePath}
            onChange={(e) => {
              setForm((value) => ({ ...value, remotePath: e.target.value }));
              setSaved(false);
            }}
          />
        </div>
        <div>
          <label className="label">MAVLink endpoint</label>
          <input
            className="input-field font-mono text-xs"
            value={form.mavlinkEndpoint}
            onChange={(e) => {
              setForm((value) => ({ ...value, mavlinkEndpoint: e.target.value }));
              setSaved(false);
            }}
            placeholder="serial:/dev/ttyAMA0:921600"
          />
        </div>
      </div>

      <div className="max-w-xs">
        <label className="label">Flight controller</label>
        <div className="grid grid-cols-2 gap-2">
          {(["px4", "ardupilot"] as const).map((value) => (
            <button
              key={value}
              onClick={() => {
                setForm((current) => ({ ...current, autopilot: value }));
                setSaved(false);
              }}
              className={cn(
                "py-2 rounded-lg border text-xs font-medium transition-colors",
                form.autopilot === value ? "bg-cyan-500/10 border-cyan-500/40 text-cyan-400" : "border-border text-slate-400",
              )}
            >
              {value === "px4" ? "PX4" : "ArduPilot"}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button onClick={saveConfiguration} className="btn-primary text-xs py-1.5 px-3">
          <Save size={12} /> Save Configuration
        </button>
        {saved && <span className="text-xs text-emerald-400">Saved</span>}
      </div>
    </div>
  );
}

export function Devices() {
  const { devices, addDevice, updateDevice, removeDevice, activeDeviceId } = useAppStore();
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [testStates, setTestStates] = useState<Record<string, TestState>>({});
  const [showPass, setShowPass] = useState(false);
  const [showPassphrase, setShowPassphrase] = useState(false);
  const [controlOpenId, setControlOpenId] = useState<string | null>(null);
  const [controlTab, setControlTab] = useState<Record<string, "control" | "config" | "setup">>({});
  const [cmdOutputs, setCmdOutputs] = useState<Record<string, string>>({});
  const [cmdRunning, setCmdRunning] = useState<string | null>(null);

  const runPiCommand = async (d: Device, label: string, command: string) => {
    if (!d.host || !d.auth) return;
    setCmdRunning(d.id);
    setCmdOutputs((o) => ({ ...o, [d.id]: `$ ${label}\n` }));
    try {
      const r = await cmd.sshRunCommand(
        d.host, d.port ?? 22, d.username ?? "user", d.auth, command
      );
      const output = [r.stdout, r.stderr].filter(Boolean).join("\n").trim();
      setCmdOutputs((o) => ({
        ...o,
        [d.id]: `$ ${label}\n${output || "(no output)"}\n[exit ${r.exit_code}]`,
      }));
    } catch (e) {
      setCmdOutputs((o) => ({ ...o, [d.id]: `$ ${label}\nERROR: ${e}` }));
    } finally {
      setCmdRunning(null);
    }
  };

  const getTab = (id: string) => controlTab[id] ?? "control";

  const emptyForm = {
    name: "",
    kind: "pi5" as "pi5" | "local",
    host: "",
    port: 22,
    username: "user",
    authMethod: "password" as "password" | "key",
    password: "",
    keyPath: "",
    passphrase: "",
    remotePath: defaultRemotePath("user"),
    mavlinkEndpoint: "serial:/dev/ttyAMA0:921600",
    autopilot: "px4" as "px4" | "ardupilot",
  };
  const [form, setForm] = useState(emptyForm);

  const startAdd = () => { setForm(emptyForm); setEditId(null); setShowForm(true); };

  const startEdit = (d: Device) => {
    setForm({
      name: d.name,
      kind: d.kind as "pi5" | "local",
      host: d.host ?? "",
      port: d.port ?? 22,
      username: d.username ?? "user",
      authMethod: d.auth?.type === "Key" ? "key" : "password",
      password: d.auth?.type === "Password" ? d.auth.password : "",
      keyPath: d.auth?.type === "Key" ? d.auth.key_path : "",
      passphrase: d.auth?.type === "Key" ? (d.auth.passphrase ?? "") : "",
      remotePath: d.remote_project_path ?? defaultRemotePath(d.username ?? "user"),
      mavlinkEndpoint: d.mavlink_endpoint ?? "serial:/dev/ttyAMA0:921600",
      autopilot: d.autopilot ?? "px4",
    });
    setEditId(d.id);
    setShowForm(true);
  };

  const browseForKey = async () => {
    const path = await openDialog({
      title: "Select SSH private key",
      multiple: false,
      filters: [{ name: "Private key", extensions: ["pem", "ppk", "key"] }],
    });
    if (path && typeof path === "string") setForm((f) => ({ ...f, keyPath: path }));
  };

  const saveForm = async () => {
    const auth =
      form.kind === "pi5"
        ? form.authMethod === "password"
          ? { type: "Password" as const, password: form.password }
          : {
              type: "Key" as const,
              key_path: form.keyPath,
              passphrase: form.passphrase || undefined,
            }
        : undefined;

    const existing = editId ? devices.find((x: Device) => x.id === editId) : undefined;
    const username = form.username || "user";
    const device: Device = {
      id: editId ?? generateId(),
      name: form.name || (form.kind === "pi5" ? "Raspberry Pi 5" : "Local Machine"),
      kind: form.kind,
      host: form.kind === "pi5" ? form.host : undefined,
      port: form.kind === "pi5" ? form.port : undefined,
      username: form.kind === "pi5" ? username : undefined,
      auth,
      remote_project_path: form.kind === "pi5" ? existing?.remote_project_path ?? defaultRemotePath(username) : undefined,
      mavlink_endpoint: form.kind === "pi5" ? existing?.mavlink_endpoint ?? "serial:/dev/ttyAMA0:921600" : undefined,
      autopilot: form.kind === "pi5" ? existing?.autopilot ?? "px4" : undefined,
      known_fingerprint: existing?.known_fingerprint,
    };

    editId ? updateDevice(device) : addDevice(device);
    const next = editId
      ? devices.map((x: Device) => (x.id === editId ? device : x))
      : [...devices, device];
    await cmd.saveDevices(next);
    setShowForm(false);
  };

  const deleteDevice = async (id: string) => {
    removeDevice(id);
    await cmd.saveDevices(devices.filter((d: Device) => d.id !== id));
  };

  const testDevice = async (d: Device) => {
    if (!d.host || !d.auth) return;
    setTestStates((s) => ({ ...s, [d.id]: "testing" }));
    try {
      const r = await cmd.testSshConnection(d.host, d.port ?? 22, d.username ?? "user", d.auth);
      const fingerprintChanged =
        r.ok && r.fingerprint != null && d.known_fingerprint != null
          ? r.fingerprint !== d.known_fingerprint
          : false;
      setTestStates((s) => ({
        ...s,
        [d.id]: {
          ok: r.ok && !fingerprintChanged,
          msg: fingerprintChanged ? "Host fingerprint changed — verify the device!" : r.message,
          fingerprint: r.fingerprint,
          fingerprintChanged,
        },
      }));
    } catch (e) {
      setTestStates((s) => ({ ...s, [d.id]: { ok: false, msg: String(e) } }));
    }
  };

  const trustDevice = async (d: Device, fingerprint: string) => {
    const updated = { ...d, known_fingerprint: fingerprint };
    updateDevice(updated);
    await cmd.saveDevices(devices.map((x: Device) => (x.id === d.id ? updated : x)));
    setTestStates((s) => {
      const prev = s[d.id];
      if (!prev || prev === "testing") return s;
      return { ...s, [d.id]: { ...(prev as TestResult), fingerprintChanged: false, ok: true } };
    });
  };

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="section-title">Devices</h1>
          <p className="text-slate-400 text-sm mt-1">Pi5 modules and local targets for deploying maps and models.</p>
        </div>
        <button onClick={startAdd} className="btn-primary">
          <Plus size={15} /> Add Device
        </button>
      </div>

      {devices.length === 0 && !showForm ? (
        <div className="card text-center py-12">
          <Server size={36} className="text-slate-600 mx-auto mb-3" />
          <p className="text-slate-400 text-sm">No devices yet</p>
          <button onClick={startAdd} className="btn-primary mt-4 mx-auto">
            <Plus size={15} /> Add your first device
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {devices.map((d: Device) => {
            const test = testStates[d.id];
            const result = test && test !== "testing" ? (test as TestResult) : null;
            const isActive = d.id === activeDeviceId;
            const isTrusted = !!d.known_fingerprint;
            const remotePath = d.remote_project_path ?? `/home/${d.username ?? "user"}/Drone`;
            const remoteBundle = `/home/${d.username ?? "user"}/drone-data/map_bundles/mission_bundle`;
            const mavlinkEnv = d.mavlink_endpoint
              ? `VISION_NAV_MAVLINK_ENDPOINT=${shellQuote(d.mavlink_endpoint)} `
              : "";
            return (
              <div
                key={d.id}
                className={cn("card space-y-2", isActive && "border-cyan-500/30 bg-cyan-500/5")}
              >
                {/* Header row */}
                <div className="flex items-center gap-4">
                  <div className={cn(
                    "w-10 h-10 rounded-lg border flex items-center justify-center shrink-0",
                    d.kind === "pi5"
                      ? "bg-cyan-500/10 border-cyan-500/20 text-cyan-400"
                      : "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
                  )}>
                    {d.kind === "pi5" ? <Server size={17} /> : <HardDrive size={17} />}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-slate-200">{d.name}</span>
                      {isActive && <span className="badge-cyan text-[10px]">Active</span>}
                      <span className={d.kind === "pi5" ? "badge-cyan text-[10px]" : "badge-green text-[10px]"}>
                        {d.kind === "pi5" ? "Pi5 / SSH" : "Local"}
                      </span>
                      {d.auth?.type === "Key" && (
                        <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-400 border border-amber-400/20 bg-amber-400/10 rounded px-1.5 py-0.5">
                          <KeyRound size={9} /> Key
                        </span>
                      )}
                      {isTrusted && (
                        <span className="inline-flex items-center gap-0.5 text-[10px] text-emerald-400 border border-emerald-400/20 bg-emerald-400/10 rounded px-1.5 py-0.5">
                          <ShieldCheck size={9} /> Trusted
                        </span>
                      )}
                    </div>
                    {d.kind === "pi5" && (
                      <div className="text-[11px] font-mono text-slate-500 mt-0.5">
                        {d.username}@{d.host}:{d.port} → {d.remote_project_path}
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {d.kind === "pi5" && (
                      <>
                        <button
                          onClick={() => testDevice(d)}
                          disabled={test === "testing"}
                          className="btn-secondary text-xs py-1 px-3"
                        >
                          {test === "testing" ? <Loader2 size={12} className="animate-spin" /> : <Wifi size={12} />}
                          Test
                        </button>
                        <button
                          onClick={() => setControlOpenId(controlOpenId === d.id ? null : d.id)}
                          className={cn(
                            "btn-secondary text-xs py-1 px-3",
                            controlOpenId === d.id && "border-violet-500/40 text-violet-400"
                          )}
                        >
                          <Terminal size={12} />
                          {getTab(d.id) === "setup" ? "Setup" : "Control"}
                          {controlOpenId === d.id ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                        </button>
                      </>
                    )}
                    <button
                      onClick={() => useAppStore.getState().setActiveDevice(isActive ? null : d.id)}
                      className={cn("text-xs py-1 px-3", isActive ? "btn-secondary text-cyan-400" : "btn-ghost")}
                    >
                      {isActive ? "Deselect" : "Select"}
                    </button>
                    <button onClick={() => startEdit(d)} className="btn-ghost py-1 px-2">
                      <Edit2 size={13} />
                    </button>
                    <button onClick={() => deleteDevice(d.id)} className="btn-ghost py-1 px-2 text-red-400 hover:text-red-300">
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>

                {/* Pi5 remote control / setup panel */}
                {d.kind === "pi5" && controlOpenId === d.id && (
                  <div className="border-t border-border pt-3 space-y-3">
                    {/* Tab bar */}
                    <div className="flex gap-1">
                      {(["control", "config", "setup"] as const).map((tab) => (
                        <button
                          key={tab}
                          onClick={() => setControlTab((t) => ({ ...t, [d.id]: tab }))}
                          className={cn(
                            "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors",
                            getTab(d.id) === tab
                              ? "bg-bg-elevated text-slate-200 border border-border-strong"
                              : "text-slate-500 hover:text-slate-300"
                          )}
                        >
                          {tab === "control" ? <Terminal size={11} /> : tab === "config" ? <SlidersHorizontal size={11} /> : <BookOpen size={11} />}
                          {tab === "control" ? "Control" : tab === "config" ? "Configuration" : "Module setup"}
                        </button>
                      ))}
                    </div>

                    {/* ── CONTROL TAB ── */}
                    {getTab(d.id) === "control" && (
                      <div className="space-y-3">
                        <div className="flex items-center gap-2 flex-wrap">
                          <button disabled={!!cmdRunning} onClick={() => runPiCommand(d, "status", "systemctl --user is-active drone-vision-nav.service 2>/dev/null || true; pgrep -af 'vision_nav.run_bundle_match_loop|vision-nav-run-bundle-loop' || true")} className="btn-secondary text-xs py-1 px-3">
                            {cmdRunning === d.id ? <Loader2 size={11} className="animate-spin" /> : <Wifi size={11} />}Status
                          </button>
                          <button disabled={!!cmdRunning} onClick={() => runPiCommand(d, "run 30-frame loop", `cd ${shellQuote(remotePath)} && VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ${mavlinkEnv}VISION_NAV_COUNT=30 ./scripts/pi/run_vision_nav_loop.sh`)} className="btn-secondary text-xs py-1 px-3 text-emerald-400 border-emerald-500/20">
                            <Play size={11} />Run Loop
                          </button>
                          <button disabled={!!cmdRunning} onClick={() => runPiCommand(d, "stop loop", "pkill -f 'vision_nav.run_bundle_match_loop|vision-nav-run-bundle-loop' && echo 'Loop stopped' || echo 'No loop running'")} className="btn-secondary text-xs py-1 px-3 text-red-400 border-red-500/20">
                            <Square size={11} />Stop Loop
                          </button>
                          <button disabled={!!cmdRunning} onClick={() => runPiCommand(d, "logs (last 60 lines)", "tail -n 60 $HOME/DroneTransfer/outgoing/runtime-match/matches.jsonl 2>/dev/null || echo '(no match log yet)'")} className="btn-secondary text-xs py-1 px-3">
                            <FileText size={11} />View Logs
                          </button>
                          <button disabled={!!cmdRunning} onClick={() => runPiCommand(d, "systemd status", "systemctl --user status drone-vision-nav.service --no-pager 2>&1 || echo 'service not installed'")} className="btn-secondary text-xs py-1 px-3">
                            <Cpu size={11} />Service
                          </button>
                        </div>
                        {cmdOutputs[d.id] && (
                          <pre className="bg-bg-base border border-border rounded-lg px-3 py-2.5 text-[11px] font-mono text-slate-300 whitespace-pre-wrap max-h-48 overflow-y-auto leading-relaxed">
                            {cmdRunning === d.id ? cmdOutputs[d.id] + "▋" : cmdOutputs[d.id]}
                          </pre>
                        )}
                        {d.mavlink_endpoint && (
                          <div className="flex items-center gap-2 text-[11px] text-slate-500">
                            <Cable size={11} /> MAVLink: <span className="font-mono text-slate-400">{d.mavlink_endpoint}</span>
                            <span className="text-[10px] bg-bg-elevated border border-border rounded px-1.5 py-0.5 text-slate-400">{d.autopilot?.toUpperCase() ?? "PX4"}</span>
                          </div>
                        )}
                      </div>
                    )}

                    {/* ── CONFIGURATION TAB ── */}
                    {getTab(d.id) === "config" && (
                      <DeviceConfigurationPanel device={d} devices={devices} updateDevice={updateDevice} />
                    )}

                    {/* ── MODULE SETUP TAB ── */}
                    {getTab(d.id) === "setup" && (
                      <ModuleSetup initialDeviceId={d.id} embedded />
                    )}
                  </div>
                )}

                {/* Test result panel */}
                {result && (
                  <div className={cn(
                    "rounded-lg px-3 py-2 space-y-1 text-[11px]",
                    result.fingerprintChanged
                      ? "bg-red-500/10 border border-red-500/20"
                      : result.ok
                      ? "bg-emerald-500/10 border border-emerald-500/20"
                      : "bg-red-500/10 border border-red-500/20"
                  )}>
                    <div className={cn(
                      "flex items-center gap-1.5 font-medium",
                      result.fingerprintChanged ? "text-red-400" : result.ok ? "text-emerald-400" : "text-red-400"
                    )}>
                      {result.fingerprintChanged
                        ? <ShieldAlert size={12} />
                        : result.ok
                        ? <CheckCircle2 size={12} />
                        : <XCircle size={12} />}
                      {result.msg}
                    </div>

                    {result.fingerprint && (
                      <div className="font-mono text-slate-400 text-[10px] break-all leading-relaxed">
                        {result.fingerprint}
                      </div>
                    )}

                    {result.ok && result.fingerprint && !isTrusted && (
                      <button
                        onClick={() => trustDevice(d, result.fingerprint!)}
                        className="inline-flex items-center gap-1 text-emerald-400 hover:text-emerald-300 font-medium mt-0.5"
                      >
                        <ShieldCheck size={10} /> Trust this device
                      </button>
                    )}

                    {result.fingerprintChanged && result.fingerprint && (
                      <div className="space-y-0.5 mt-1">
                        <div className="text-slate-500">Known: <span className="font-mono text-slate-400">{d.known_fingerprint}</span></div>
                        <div className="text-slate-500">Got:&nbsp;&nbsp; <span className="font-mono text-red-400">{result.fingerprint}</span></div>
                        <button
                          onClick={() => trustDevice(d, result.fingerprint!)}
                          className="inline-flex items-center gap-1 text-amber-400 hover:text-amber-300 font-medium mt-1"
                        >
                          <ShieldCheck size={10} /> Update trust to new fingerprint
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Add / Edit form */}
      {showForm && (
        <div className="card border-cyan-500/20 space-y-4">
          <h3 className="text-sm font-semibold text-slate-200">
            {editId ? "Edit Device" : "New Device"}
          </h3>

          <div className="flex gap-2">
            {(["pi5", "local"] as const).map((k) => (
              <button
                key={k}
                onClick={() => setForm({ ...form, kind: k })}
                disabled={!!editId}
                className={cn(
                  "flex-1 py-2 rounded-lg border text-sm font-medium transition-colors",
                  form.kind === k
                    ? "bg-cyan-500/10 border-cyan-500/40 text-cyan-400"
                    : "border-border text-slate-400 hover:border-border-strong"
                )}
              >
                {k === "pi5" ? "Raspberry Pi 5" : "Local Machine"}
              </button>
            ))}
          </div>

          <div>
            <label className="label">Name</label>
            <input
              className="input-field"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={form.kind === "pi5" ? "My Pi5" : "This Laptop"}
            />
          </div>

          {form.kind === "pi5" && (
            <>
              <div className="grid grid-cols-3 gap-2">
                <div className="col-span-2">
                  <label className="label">IP address</label>
                  <input className="input-field" value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="192.168.1.100" />
                </div>
                <div>
                  <label className="label">Port</label>
                  <input className="input-field" type="number" value={form.port} onChange={(e) => setForm({ ...form, port: Number(e.target.value) })} />
                </div>
              </div>

              <div>
                <label className="label">Username</label>
                <input
                  className="input-field"
                  value={form.username}
                  onChange={(e) => {
                    const username = e.target.value;
                    setForm({ ...form, username, remotePath: defaultRemotePath(username) });
                  }}
                />
              </div>

              <div>
                <label className="label">Auth method</label>
                <div className="flex gap-2">
                  {(["password", "key"] as const).map((m) => (
                    <button
                      key={m}
                      onClick={() => setForm({ ...form, authMethod: m })}
                      className={cn(
                        "flex-1 py-2 rounded-lg border text-sm font-medium transition-colors flex items-center justify-center gap-1.5",
                        form.authMethod === m
                          ? "bg-cyan-500/10 border-cyan-500/40 text-cyan-400"
                          : "border-border text-slate-400"
                      )}
                    >
                      {m === "password" ? <Lock size={13} /> : <KeyRound size={13} />}
                      {m === "password" ? "Password" : "SSH Key"}
                    </button>
                  ))}
                </div>
              </div>

              {form.authMethod === "password" ? (
                <div>
                  <label className="label">Password</label>
                  <div className="relative">
                    <input
                      className="input-field pr-10"
                      type={showPass ? "text" : "password"}
                      value={form.password}
                      onChange={(e) => setForm({ ...form, password: e.target.value })}
                    />
                    <button onClick={() => setShowPass(!showPass)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                      {showPass ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="space-y-3">
                  <div>
                    <label className="label">SSH private key</label>
                    <div className="flex gap-2">
                      <input
                        className="input-field flex-1 font-mono text-sm"
                        placeholder="~/.ssh/id_rsa"
                        value={form.keyPath}
                        onChange={(e) => setForm({ ...form, keyPath: e.target.value })}
                      />
                      <button
                        onClick={browseForKey}
                        className="btn-secondary px-3 shrink-0"
                        title="Browse for key file"
                      >
                        <FolderOpen size={14} />
                      </button>
                    </div>
                  </div>
                  <div>
                    <label className="label">
                      Passphrase <span className="text-slate-600 font-normal">(if key is encrypted)</span>
                    </label>
                    <div className="relative">
                      <input
                        className="input-field pr-10"
                        type={showPassphrase ? "text" : "password"}
                        placeholder="Leave blank for unencrypted key"
                        value={form.passphrase}
                        onChange={(e) => setForm({ ...form, passphrase: e.target.value })}
                      />
                      <button onClick={() => setShowPassphrase(!showPassphrase)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                        {showPassphrase ? <EyeOff size={14} /> : <Eye size={14} />}
                      </button>
                    </div>
                  </div>
                </div>
              )}

            </>
          )}

          <div className="flex gap-2 pt-1">
            <button onClick={() => setShowForm(false)} className="btn-secondary flex-1 justify-center">Cancel</button>
            <button
              onClick={saveForm}
              disabled={form.kind === "local" && !form.name}
              className="btn-primary flex-1 justify-center"
            >
              {editId ? "Save Changes" : "Add Device"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
