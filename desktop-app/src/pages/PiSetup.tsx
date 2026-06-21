import { useEffect, useMemo, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { open as openDialog, save as saveDialog } from "@tauri-apps/plugin-dialog";
import { writeTextFile } from "@tauri-apps/plugin-fs";
import {
  Archive,
  CheckCircle2,
  Copy,
  Camera,
  Eye,
  EyeOff,
  FolderOpen,
  HardDriveUpload,
  KeyRound,
  Loader2,
  Lock,
  Save,
  Server,
  ShieldCheck,
  TestTube2,
  Terminal,
  Wifi,
  XCircle,
} from "lucide-react";
import { cmd } from "../lib/tauri";
import { useAppStore } from "../lib/store";
import { cn, generateId } from "../lib/utils";
import {
  candidateHost,
  candidateName,
  discoveryTroubleshooting,
  loadDiscoveryHistory,
  mergeDiscoveryHistory,
  networkHintLabel,
  saveDiscoveryHistory,
} from "../lib/discovery";
import { SupportBundleList } from "../components/SupportBundleList";
import type { Device, LocalNetworkHint, PiDiscoveryCandidate, SupportBundleFile, UploadProgress } from "../lib/types";

const DEFAULT_LOCAL_REPO = "/Users/izzyfisi/Documents/DRONE";
const HOST_SUGGESTIONS = ["dronecompute.local", "raspberrypi.local", "192.168.1.158"];
const SUPPORT_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/support-bundles";
const MODULE_SETUP_HANDOFF_KEY = "drone_module_setup_handoff";

type AuthForm = "password" | "key";
type StepStatus = "idle" | "running" | "passed" | "failed";

interface SetupResult {
  status: StepStatus;
  output?: string;
  exitCode?: number;
}

interface PiForm {
  name: string;
  host: string;
  port: number;
  username: string;
  authMethod: AuthForm;
  password: string;
  keyPath: string;
  passphrase: string;
  remotePath: string;
  mavlinkEndpoint: string;
}

interface SetupStep {
  id: string;
  title: string;
  detail: string;
  command?: () => string;
  requiresSudo?: boolean;
  recommended?: boolean;
}

interface ModuleSetupHandoff {
  version: number;
  source: "mission-planner";
  action: "bench-report";
  created_at: string;
  device_id: string;
  device_name?: string;
  remote_bundle_dir?: string;
  local_bundle_dir?: string;
  region_id?: string | null;
  region_name?: string | null;
  plan_fingerprint?: string;
  mission_plan_state?: string;
  built_at?: string | null;
  uploaded_at?: string | null;
}

function shellQuote(value: string) {
  return `'${value.replace(/'/g, "'\"'\"'")}'`;
}

function safeReportName(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "module";
}

function parseSupportBundleZip(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_SUPPORT_ZIP__="))
    ?.replace("__VISION_NAV_SUPPORT_ZIP__=", "");
}

function readModuleSetupHandoff(): ModuleSetupHandoff | null {
  try {
    const raw = sessionStorage.getItem(MODULE_SETUP_HANDOFF_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ModuleSetupHandoff;
    if (parsed.source !== "mission-planner" || parsed.action !== "bench-report") return null;
    return parsed;
  } catch {
    return null;
  }
}

function benchReportCommand(remoteProject: string, remoteBundle: string, mavlinkEndpoint: string) {
  const mavlinkEnv = mavlinkEndpoint ? `VISION_NAV_MAVLINK_ENDPOINT=${shellQuote(mavlinkEndpoint)} ` : "";
  return [
    `cd ${shellQuote(remoteProject)}`,
    "set +e",
    `VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ./scripts/pi/validate_terrain_bundle.sh`,
    "validate_exit=$?",
    `VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ${mavlinkEnv}./scripts/pi/create_support_bundle.sh`,
    "support_exit=$?",
    `latest=$(ls -t "$HOME/DroneTransfer/outgoing/support-bundles/"*.zip 2>/dev/null | head -n 1)`,
    `test -n "$latest" && echo "__VISION_NAV_SUPPORT_ZIP__=$latest"`,
    `if [ "$support_exit" -ne 0 ]; then exit "$support_exit"; fi`,
    `if [ -z "$latest" ]; then exit 1; fi`,
    `exit "$validate_exit"`,
  ].join("; ");
}

function defaultRemotePath(username: string) {
  return `/home/${username || "user"}/Drone`;
}

function defaultForm(): PiForm {
  return {
    name: "Raspberry Pi 5",
    host: "",
    port: 22,
    username: "user",
    authMethod: "password",
    password: "",
    keyPath: "",
    passphrase: "",
    remotePath: "/home/user/Drone",
    mavlinkEndpoint: "serial:/dev/ttyAMA0:921600",
  };
}

function formFromDevice(device: Device): PiForm {
  const username = device.username ?? "user";
  return {
    name: device.name,
    host: device.host ?? "",
    port: device.port ?? 22,
    username,
    authMethod: device.auth?.type === "Key" ? "key" : "password",
    password: device.auth?.type === "Password" ? device.auth.password : "",
    keyPath: device.auth?.type === "Key" ? device.auth.key_path : "",
    passphrase: device.auth?.type === "Key" ? device.auth.passphrase ?? "" : "",
    remotePath: device.remote_project_path ?? defaultRemotePath(username),
    mavlinkEndpoint: device.mavlink_endpoint ?? "serial:/dev/ttyAMA0:921600",
  };
}

function StatusIcon({ status }: { status: StepStatus }) {
  if (status === "running") return <Loader2 size={14} className="animate-spin text-cyan-400" />;
  if (status === "passed") return <CheckCircle2 size={14} className="text-emerald-400" />;
  if (status === "failed") return <XCircle size={14} className="text-red-400" />;
  return <Terminal size={14} className="text-slate-600" />;
}

interface ModuleSetupProps {
  initialDeviceId?: string;
  embedded?: boolean;
}

export function ModuleSetup({ initialDeviceId, embedded = false }: ModuleSetupProps) {
  const { devices, addDevice, updateDevice, setActiveDevice, activeDeviceId } = useAppStore();
  const piDevices = devices.filter((device) => device.kind === "pi5");
  const activePi = piDevices.find((device) => device.id === (initialDeviceId ?? activeDeviceId)) ?? piDevices[0];
  const [selectedDeviceId, setSelectedDeviceId] = useState(activePi?.id ?? "new");
  const [form, setForm] = useState<PiForm>(() => (activePi ? formFromDevice(activePi) : defaultForm()));
  const [showPassword, setShowPassword] = useState(false);
  const [showPassphrase, setShowPassphrase] = useState(false);
  const [showSudoPassword, setShowSudoPassword] = useState(false);
  const [sudoPassword, setSudoPassword] = useState("");
  const [repoPath, setRepoPath] = useState(() => localStorage.getItem("drone_repo_path") || DEFAULT_LOCAL_REPO);
  const [testing, setTesting] = useState(false);
  const [connectionResult, setConnectionResult] = useState<{ ok: boolean; message: string; fingerprint?: string } | null>(null);
  const [runningStep, setRunningStep] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, SetupResult>>({});
  const [selectedOutputId, setSelectedOutputId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [cameraPreview, setCameraPreview] = useState<string | null>(null);
  const [cameraPreviewPath, setCameraPreviewPath] = useState<string | null>(null);
  const [cameraAutoRefresh, setCameraAutoRefresh] = useState(false);
  const [capturingCamera, setCapturingCamera] = useState(false);
  const [supportBundles, setSupportBundles] = useState<SupportBundleFile[]>([]);
  const [setupReportPath, setSetupReportPath] = useState<string | null>(null);
  const [setupHandoff, setSetupHandoff] = useState<ModuleSetupHandoff | null>(() => readModuleSetupHandoff());
  const [discovering, setDiscovering] = useState(false);
  const [discoveryCandidates, setDiscoveryCandidates] = useState<PiDiscoveryCandidate[]>(() => loadDiscoveryHistory());
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [networkHints, setNetworkHints] = useState<LocalNetworkHint[]>([]);
  const [error, setError] = useState<string | null>(null);

  const selectedDevice = useMemo(
    () => piDevices.find((device) => device.id === selectedDeviceId),
    [piDevices, selectedDeviceId],
  );

  useEffect(() => {
    if (selectedDevice) {
      setForm(formFromDevice(selectedDevice));
      setConnectionResult(null);
      setError(null);
    }
  }, [selectedDeviceId]);

  const refreshSupportBundles = async () => {
    try {
      setSupportBundles(await cmd.listSupportBundles(SUPPORT_DOWNLOAD_DIR));
    } catch {
      setSupportBundles([]);
    }
  };

  useEffect(() => {
    refreshSupportBundles();
    cmd.localNetworkHints().then(setNetworkHints).catch(() => setNetworkHints([]));
  }, []);

  const clearSetupHandoff = () => {
    sessionStorage.removeItem(MODULE_SETUP_HANDOFF_KEY);
    setSetupHandoff(null);
  };

  const auth = (): NonNullable<Device["auth"]> | null => {
    if (form.authMethod === "password") {
      if (!form.password) return null;
      return { type: "Password", password: form.password };
    }
    if (!form.keyPath) return null;
    return {
      type: "Key",
      key_path: form.keyPath,
      passphrase: form.passphrase || undefined,
    };
  };

  const connectionReady = !!form.host && !!form.username && !!auth();
  const remoteProject = form.remotePath || defaultRemotePath(form.username);
  const defaultRemoteBundle = `/home/${form.username || "user"}/drone-data/map_bundles/mission_bundle`;
  const activeHandoff = setupHandoff?.device_id === selectedDeviceId ? setupHandoff : null;
  const remoteBundle = activeHandoff?.remote_bundle_dir || defaultRemoteBundle;

  const setResult = (id: string, result: SetupResult) => {
    setResults((prev) => ({ ...prev, [id]: result }));
    setSelectedOutputId(id);
  };

  const browseForKey = async () => {
    const path = await openDialog({
      title: "Select SSH private key",
      multiple: false,
      filters: [{ name: "Private key", extensions: ["pem", "ppk", "key"] }],
    });
    if (path && typeof path === "string") setForm((value) => ({ ...value, keyPath: path }));
  };

  const browseForRepo = async () => {
    const path = await openDialog({ title: "Select local Drone repo", directory: true, multiple: false });
    if (path && typeof path === "string") {
      setRepoPath(path);
      localStorage.setItem("drone_repo_path", path);
    }
  };

  const useDiscoveredDevice = (candidate: PiDiscoveryCandidate) => {
    const username = form.username || "user";
    setSelectedDeviceId("new");
    setForm((value) => ({
      ...value,
      name: candidateName(candidate),
      host: candidateHost(candidate),
      port: candidate.port,
      username,
      remotePath: defaultRemotePath(username),
    }));
    setConnectionResult(null);
    setError(null);
  };

  const runDiscovery = async () => {
    setDiscovering(true);
    setDiscoveryError(null);
    try {
      const seedHosts = piDevices
        .filter((device) => device.host)
        .map((device) => device.host!)
        .concat(form.host ? [form.host] : []);
      const candidates = await cmd.discoverPiDevices(seedHosts, 22);
      const hints = await cmd.localNetworkHints().catch(() => networkHints);
      const next = mergeDiscoveryHistory(discoveryCandidates, candidates);
      setDiscoveryCandidates(next);
      setNetworkHints(hints);
      saveDiscoveryHistory(next);
    } catch (err) {
      setDiscoveryError(String(err));
    } finally {
      setDiscovering(false);
    }
  };

  const testConnection = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Enter the module host, username, and SSH authentication first.");
      return;
    }
    setTesting(true);
    setError(null);
    setConnectionResult(null);
    try {
      const result = await cmd.testSshConnection(form.host, form.port, form.username, resolvedAuth);
      setConnectionResult({
        ok: result.ok,
        message: result.message,
        fingerprint: result.fingerprint,
      });
    } catch (err) {
      setConnectionResult({ ok: false, message: String(err) });
    } finally {
      setTesting(false);
    }
  };

  const saveConnection = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Testable SSH details are required before saving the module.");
      return;
    }
    const id = selectedDevice?.id ?? generateId();
    const device: Device = {
      id,
      name: form.name || "Raspberry Pi 5",
      kind: "pi5",
      host: form.host,
      port: form.port,
      username: form.username,
      auth: resolvedAuth,
      remote_project_path: remoteProject,
      mavlink_endpoint: form.mavlinkEndpoint,
      autopilot: selectedDevice?.autopilot ?? "px4",
      known_fingerprint: connectionResult?.fingerprint ?? selectedDevice?.known_fingerprint,
    };
    const next = selectedDevice
      ? devices.map((candidate) => (candidate.id === id ? device : candidate))
      : [...devices, device];
    selectedDevice ? updateDevice(device) : addDevice(device);
    setActiveDevice(id);
    setSelectedDeviceId(id);
    await cmd.saveDevices(next);
  };

  const runRemote = async (id: string, label: string, command: string) => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before running setup checks.");
      return;
    }
    setRunningStep(id);
    setError(null);
    setResult(id, { status: "running", output: `$ ${label}\n` });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        command,
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      setResult(id, {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ ${label}\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
    } catch (err) {
      setResult(id, { status: "failed", output: `$ ${label}\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const createBenchReport = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before creating a bench report.");
      return;
    }
    setRunningStep("bench-report");
    setError(null);
    setResult("bench-report", { status: "running", output: "$ create bench report\n" });
    try {
      const result = await cmd.sshRunCommand(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        benchReportCommand(remoteProject, remoteBundle, form.mavlinkEndpoint),
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      const remoteZip = parseSupportBundleZip(output);
      if (!remoteZip) {
        setResult("bench-report", {
          status: "failed",
          output: `$ create bench report\n${output || "(no output)"}\n[exit ${result.exit_code}]`,
          exitCode: result.exit_code,
        });
        return;
      }

      setResult("bench-report", {
        status: "running",
        output: `$ create bench report\n${output}\n\n$ download bench report\nDownloading ${remoteZip}...`,
      });
      const downloaded = await cmd.sshDownloadFile(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteZip,
        SUPPORT_DOWNLOAD_DIR,
      );
      setResult("bench-report", {
        status: result.exit_code === 0 ? "passed" : "failed",
        output: `$ create bench report\n${output}\n\n$ download bench report\nSaved to ${downloaded.local_path}\n[${downloaded.bytes_received} bytes]\n[exit ${result.exit_code}]`,
        exitCode: result.exit_code,
      });
      await refreshSupportBundles();
    } catch (err) {
      setResult("bench-report", { status: "failed", output: `$ create bench report\nERROR: ${err}` });
    } finally {
      setRunningStep(null);
    }
  };

  const syncProject = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before installing project files.");
      return false;
    }
    setRunningStep("sync");
    setError(null);
    setUploadProgress(null);
    setResult("sync", { status: "running", output: `$ module file sync\n${repoPath} -> ${remoteProject}` });
    const unlisten = await listen<UploadProgress>("upload-progress", (event) => {
      setUploadProgress(event.payload);
    });
    try {
      await cmd.sshUploadProject(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        repoPath,
        remoteProject,
      );
      setResult("sync", {
        status: "passed",
        output: `$ module file sync\nUploaded runtime module files to ${remoteProject}\n[exit 0]`,
        exitCode: 0,
      });
      return true;
    } catch (err) {
      setResult("sync", { status: "failed", output: `$ module file sync\nERROR: ${err}` });
      return false;
    } finally {
      unlisten();
      setRunningStep(null);
    }
  };

  const installModule = async () => {
    const synced = await syncProject();
    if (!synced) return;
    if (!auth() || !form.host) return;
    await runRemote(
      "bootstrap",
      "Module Installation",
      `cd ${shellQuote(remoteProject)} && chmod +x scripts/pi/*.sh && ${sudoPrefix()}./scripts/pi/bootstrap_pi5.sh`,
    );
  };

  const captureCameraFrame = async () => {
    const resolvedAuth = auth();
    if (!resolvedAuth || !form.host) {
      setError("Connect to the module over SSH before opening the camera view.");
      return;
    }
    setCapturingCamera(true);
    setError(null);
    try {
      const frame = await cmd.sshCaptureCameraFrame(
        form.host,
        form.port,
        form.username,
        resolvedAuth,
        remoteProject,
        960,
        720,
        1000,
      );
      setCameraPreview(`data:${frame.mime_type};base64,${frame.base64_data}`);
      setCameraPreviewPath(frame.remote_path);
      setResult("camera-preview", {
        status: "passed",
        output: `$ camera view test\nCaptured preview frame from ${frame.remote_path}\n${[frame.stdout, frame.stderr].filter(Boolean).join("\n").trim()}\n[exit 0]`,
        exitCode: 0,
      });
    } catch (err) {
      setResult("camera-preview", { status: "failed", output: `$ camera view test\nERROR: ${err}` });
    } finally {
      setCapturingCamera(false);
    }
  };

  useEffect(() => {
    if (!cameraAutoRefresh) return;
    let cancelled = false;
    let timer: number | undefined;

    const loop = async () => {
      await captureCameraFrame();
      if (!cancelled) timer = window.setTimeout(loop, 2500);
    };

    loop();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [
    cameraAutoRefresh,
    form.host,
    form.port,
    form.username,
    form.authMethod,
    form.password,
    form.keyPath,
    form.passphrase,
    remoteProject,
  ]);

  const sudoPrefix = () => {
    if (!sudoPassword) return "";
    return `printf ${shellQuote(`${sudoPassword}\n`)} | sudo -S -p '' -v && `;
  };

  const steps: SetupStep[] = [
    {
      id: "identity",
      title: "Wi-Fi SSH Identity",
      detail: "Confirms the app is talking to the expected module on the local network.",
      command: () => "whoami && hostname && hostname -I && printf '\\n'; sed -n '1,6p' /etc/os-release",
      recommended: true,
    },
    {
      id: "repo",
      title: "Project Files",
      detail: "Checks that the runtime repo exists at the configured module path.",
      command: () =>
        `cd ${shellQuote(remoteProject)} && test -d src/vision_nav && test -x scripts/pi/bootstrap_pi5.sh && pwd && echo 'Drone repo files ready'`,
      recommended: true,
    },
    {
      id: "bootstrap",
      title: "Module Installation",
      detail: "Installs runtime dependencies and service support. Requires sudo and a reboot afterward.",
      command: () =>
        `cd ${shellQuote(remoteProject)} && chmod +x scripts/pi/*.sh && ${sudoPrefix()}./scripts/pi/bootstrap_pi5.sh`,
      requiresSudo: true,
    },
    {
      id: "verify",
      title: "Verify System Setup",
      detail: "Checks SSH, Docker, transfer folders, and the Python vision environment.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/verify_pi_setup.sh`,
      recommended: true,
    },
    {
      id: "camera",
      title: "Camera Health",
      detail: "Captures from the module camera and measures image quality when camera tools are available.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/check_global_shutter_camera.sh`,
      recommended: true,
    },
    {
      id: "time-sync",
      title: "Time Sync",
      detail: "Checks system clock plausibility and NTP synchronization for timestamped external-vision output.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/check_time_sync.sh`,
      recommended: true,
    },
    {
      id: "mavlink",
      title: "MAVLink Endpoint",
      detail: "Validates MAVLink endpoint syntax and serial-device access. Set probe mode for live telemetry.",
      command: () =>
        `cd ${shellQuote(remoteProject)} && VISION_NAV_MAVLINK_ENDPOINT=${shellQuote(form.mavlinkEndpoint)} ./scripts/pi/check_mavlink_endpoint.sh`,
      recommended: true,
    },
    {
      id: "calibration-capture",
      title: "Calibration Capture",
      detail: "Captures a short chessboard image set for down-camera calibration when the target is ready.",
      command: () =>
        `cd ${shellQuote(remoteProject)} && CALIBRATION_COUNT=8 CALIBRATION_DELAY_S=1 ./scripts/pi/capture_calibration_set.sh`,
    },
    {
      id: "smoke",
      title: "Vision Smoke Test",
      detail: "Builds and matches a synthetic feature-map pair on the module.",
      command: () => `cd ${shellQuote(remoteProject)} && ./scripts/pi/smoke_test_vision.sh`,
      recommended: true,
    },
    {
      id: "runtime",
      title: "Runtime Bundle Check",
      detail: "Validates the currently deployed mission bundle if one exists.",
      command: () =>
        `cd ${shellQuote(remoteProject)} && VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ./scripts/pi/validate_terrain_bundle.sh`,
    },
    {
      id: "bench-report",
      title: "Bench Report",
      detail: "Validates the deployed terrain bundle, creates a Pi support bundle, and downloads it.",
    },
  ];

  const runSetupStep = async (step: SetupStep) => {
    if (step.id === "bench-report") {
      await createBenchReport();
      return;
    }
    if (!step.command) return;
    await runRemote(step.id, step.title, step.command());
  };

  const runRecommendedChecks = async () => {
    for (const step of steps.filter((candidate) => candidate.recommended)) {
      await runSetupStep(step);
    }
  };

  const saveSetupReport = async () => {
    const stepResults = steps.map((step) => {
      const result = results[step.id];
      return {
        id: step.id,
        title: step.title,
        status: result?.status ?? "idle",
        exit_code: result?.exitCode ?? null,
        output: result?.output ?? null,
      };
    });
    const statusCounts = stepResults.reduce<Record<string, number>>((acc, result) => {
      acc[result.status] = (acc[result.status] ?? 0) + 1;
      return acc;
    }, {});
    const stepIds = new Set(steps.map((step) => step.id));
    const extraResults = Object.entries(results)
      .filter(([id]) => !stepIds.has(id))
      .map(([id, result]) => ({
        id,
        status: result.status,
        exit_code: result.exitCode ?? null,
        output: result.output ?? null,
      }));
    const report = {
      version: "0.1.0",
      generated_at: new Date().toISOString(),
      device: {
        name: form.name,
        host: form.host,
        port: form.port,
        username: form.username,
        auth_method: form.authMethod,
        remote_project_path: remoteProject,
        remote_bundle_path: remoteBundle,
        mavlink_endpoint: form.mavlinkEndpoint,
        ssh_fingerprint: connectionResult?.fingerprint ?? selectedDevice?.known_fingerprint ?? null,
      },
      mission_planner_handoff: activeHandoff,
      local: {
        repo_path: repoPath,
        support_bundle_download_dir: SUPPORT_DOWNLOAD_DIR,
      },
      discovery: {
        candidates: discoveryCandidates.slice(0, 8),
        network_hints: networkHints,
      },
      connection_result: connectionResult
        ? {
            ok: connectionResult.ok,
            message: connectionResult.message,
            fingerprint: connectionResult.fingerprint ?? null,
          }
        : null,
      camera_preview_remote_path: cameraPreviewPath,
      status_counts: statusCounts,
      steps: stepResults,
      extra_results: extraResults,
      downloaded_support_bundles: supportBundles.slice(0, 5),
    };
    const defaultPath = `drone-module-setup-${safeReportName(form.host || form.name)}-${new Date().toISOString().slice(0, 10)}.json`;
    const path = await saveDialog({
      title: "Save module setup report",
      defaultPath,
      filters: [{ name: "JSON", extensions: ["json"] }],
    });
    if (path && typeof path === "string") {
      await writeTextFile(path, JSON.stringify(report, null, 2) + "\n");
      setSetupReportPath(path);
    }
  };

  const output = selectedOutputId ? results[selectedOutputId]?.output : undefined;

  return (
    <div className={cn(embedded ? "space-y-5" : "p-6 space-y-6 animate-fade-in")}>
      {!embedded && (
        <div>
          <h1 className="section-title">Module Setup</h1>
          <p className="text-slate-400 text-sm mt-1">
            Connect to the runtime module over local Wi-Fi, install it, and run vision-system checks.
          </p>
        </div>
      )}

      <div className="grid grid-cols-[0.95fr_1.05fr] gap-6">
        <div className="space-y-4">
          {embedded ? (
            <div className="card space-y-3">
              <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
                <Wifi size={15} className="text-cyan-400" /> Saved SSH Connection
              </h3>
              {connectionReady ? (
                <div className="rounded-lg border border-border bg-bg-card px-3 py-2">
                  <div className="text-xs text-slate-400">Using saved device connection</div>
                  <div className="font-mono text-xs text-slate-200 mt-1">{form.username}@{form.host}:{form.port}</div>
                </div>
              ) : (
                <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  Edit this device and add SSH connection details before installing the module.
                </div>
              )}
              {error && (
                <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                  {error}
                </div>
              )}
            </div>
          ) : (
          <div className="card space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
                <Wifi size={15} className="text-cyan-400" /> SSH Connection
              </h3>
              {!embedded && (
                <select
                  className="input-field max-w-52 text-xs"
                  value={selectedDeviceId}
                  onChange={(event) => setSelectedDeviceId(event.target.value)}
                >
                  <option value="new">New module</option>
                  {piDevices.map((device) => (
                    <option key={device.id} value={device.id}>{device.name}</option>
                  ))}
                </select>
              )}
            </div>

            <div className="grid grid-cols-3 gap-2">
              {HOST_SUGGESTIONS.map((host) => (
                <button
                  key={host}
                  onClick={() => setForm((value) => ({ ...value, host }))}
                  className={cn(
                    "rounded-lg border px-2 py-2 text-[11px] font-mono transition-colors",
                    form.host === host ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-300" : "border-border text-slate-500 hover:text-slate-300",
                  )}
                >
                  {host}
                </button>
              ))}
            </div>

            <div className="rounded-lg border border-border bg-bg-card p-3 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-200 flex items-center gap-2">
                    <Wifi size={14} className="text-cyan-400" /> Discovery
                  </div>
                  <p className="text-[11px] text-slate-500 mt-0.5">Scan saved hosts, mDNS names, and local SSH neighbors.</p>
                </div>
                <button onClick={runDiscovery} disabled={discovering} className="btn-secondary text-xs py-1 px-3">
                  {discovering ? <Loader2 size={11} className="animate-spin" /> : <Wifi size={11} />}
                  Scan
                </button>
              </div>
              {discoveryError && (
                <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                  {discoveryError}
                </div>
              )}
              {networkHints.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {networkHints.slice(0, 3).map((hint) => (
                    <span key={`${hint.interface_name}-${hint.ipv4}`} className="badge-cyan text-[10px]">
                      {networkHintLabel(hint)}
                    </span>
                  ))}
                </div>
              )}
              {discoveryCandidates.length > 0 && (
                <div className="space-y-2">
                  {discoveryCandidates.slice(0, 4).map((candidate) => (
                    <div key={`${candidate.host}:${candidate.port}`} className="flex items-center gap-2 rounded-lg border border-border/70 bg-bg-base px-2 py-1.5">
                      <span className={candidate.ssh_open ? "badge-green text-[10px]" : "badge-red text-[10px]"}>
                        {candidate.ssh_open ? "SSH" : "offline"}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="font-mono text-[11px] text-slate-300 truncate">{candidateHost(candidate)}</div>
                        <div className="text-[10px] text-slate-500 truncate">{candidate.source} · {candidate.message}</div>
                      </div>
                      <button onClick={() => useDiscoveredDevice(candidate)} className="btn-secondary text-xs py-1 px-2">
                        Use
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {discoveryTroubleshooting(discoveryCandidates, networkHints).length > 0 && (
                <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 space-y-1">
                  {discoveryTroubleshooting(discoveryCandidates, networkHints).map((item) => (
                    <div key={item} className="text-[11px] text-amber-200">{item}</div>
                  ))}
                </div>
              )}
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <label className="label">Module hostname or IP</label>
                <input
                  className="input-field"
                  value={form.host}
                  onChange={(event) => setForm({ ...form, host: event.target.value })}
                  placeholder="dronecompute.local or 192.168.1.x"
                />
              </div>
              <div>
                <label className="label">SSH port</label>
                <input
                  className="input-field"
                  type="number"
                  value={form.port}
                  onChange={(event) => setForm({ ...form, port: Number(event.target.value) })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Device name</label>
                <input
                  className="input-field"
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </div>
              <div>
                <label className="label">Username</label>
                <input
                  className="input-field"
                  value={form.username}
                  onChange={(event) => {
                    const username = event.target.value;
                    setForm({ ...form, username, remotePath: defaultRemotePath(username) });
                  }}
                />
              </div>
            </div>

            <div>
              <label className="label">Auth method</label>
              <div className="grid grid-cols-2 gap-2">
                {(["password", "key"] as const).map((method) => (
                  <button
                    key={method}
                    onClick={() => setForm({ ...form, authMethod: method })}
                    className={cn(
                      "py-2 rounded-lg border text-sm font-medium transition-colors flex items-center justify-center gap-1.5",
                      form.authMethod === method
                        ? "bg-cyan-500/10 border-cyan-500/40 text-cyan-400"
                        : "border-border text-slate-400",
                    )}
                  >
                    {method === "password" ? <Lock size={13} /> : <KeyRound size={13} />}
                    {method === "password" ? "Password" : "SSH Key"}
                  </button>
                ))}
              </div>
            </div>

            {form.authMethod === "password" ? (
              <div>
                <label className="label">SSH password</label>
                <div className="relative">
                  <input
                    className="input-field pr-10"
                    type={showPassword ? "text" : "password"}
                    value={form.password}
                    onChange={(event) => setForm({ ...form, password: event.target.value })}
                  />
                  <button onClick={() => setShowPassword(!showPassword)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                    {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="label">SSH private key</label>
                  <div className="flex gap-2">
                    <input
                      className="input-field flex-1 font-mono text-xs"
                      value={form.keyPath}
                      onChange={(event) => setForm({ ...form, keyPath: event.target.value })}
                      placeholder="~/.ssh/id_ed25519"
                    />
                    <button onClick={browseForKey} className="btn-secondary px-3">
                      <FolderOpen size={14} />
                    </button>
                  </div>
                </div>
                <div>
                  <label className="label">Key passphrase</label>
                  <div className="relative">
                    <input
                      className="input-field pr-10"
                      type={showPassphrase ? "text" : "password"}
                      value={form.passphrase}
                      onChange={(event) => setForm({ ...form, passphrase: event.target.value })}
                    />
                    <button onClick={() => setShowPassphrase(!showPassphrase)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                      {showPassphrase ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                </div>
              </div>
            )}

            <div className="grid grid-cols-[1fr_auto_auto] gap-2">
              <button onClick={testConnection} disabled={!connectionReady || testing} className="btn-primary justify-center">
                {testing ? <Loader2 size={15} className="animate-spin" /> : <Wifi size={15} />}
                Test Wi-Fi SSH
              </button>
              <button onClick={saveConnection} disabled={!connectionReady} className="btn-secondary px-3">
                <Save size={14} /> Save
              </button>
              <button onClick={() => navigator.clipboard.writeText(`${form.username}@${form.host}`)} className="btn-secondary px-3">
                <Copy size={14} />
              </button>
            </div>

            {connectionResult && (
              <div className={cn(
                "rounded-lg border px-3 py-2 text-xs space-y-1",
                connectionResult.ok ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300" : "border-red-500/20 bg-red-500/10 text-red-300",
              )}>
                <div className="flex items-center gap-2">
                  {connectionResult.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                  <span>{connectionResult.message}</span>
                </div>
                {connectionResult.fingerprint && (
                  <div className="font-mono text-[10px] text-slate-400 break-all">{connectionResult.fingerprint}</div>
                )}
              </div>
            )}

            {error && (
              <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {error}
              </div>
            )}
          </div>
          )}

          {activeHandoff && (
            <div className="card space-y-3 border-cyan-500/30 bg-cyan-500/5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
                    <Archive size={15} className="text-cyan-400" /> Mission Bundle Handoff
                  </h3>
                  <p className="text-[11px] text-slate-500 mt-1">
                    Mission Planner uploaded this bundle for bench validation.
                  </p>
                </div>
                <button onClick={clearSetupHandoff} className="btn-ghost text-xs py-1 px-2">
                  Clear
                </button>
              </div>
              <div className="rounded-lg border border-border bg-bg-card px-3 py-2 space-y-1">
                <div className="flex justify-between gap-3 text-[11px]">
                  <span className="text-slate-500">Region</span>
                  <span className="text-slate-300 truncate">{activeHandoff.region_name || "n/a"}</span>
                </div>
                <div className="flex justify-between gap-3 text-[11px]">
                  <span className="text-slate-500">Uploaded</span>
                  <span className="text-slate-300">{activeHandoff.uploaded_at ? new Date(activeHandoff.uploaded_at).toLocaleString() : "n/a"}</span>
                </div>
                <div className="text-[10px] font-mono text-slate-500 truncate">{remoteBundle}</div>
              </div>
              <button onClick={createBenchReport} disabled={!connectionReady || !!runningStep} className="btn-primary w-full justify-center">
                {runningStep === "bench-report" ? <Loader2 size={14} className="animate-spin" /> : <Archive size={14} />}
                Create Bench Report
              </button>
            </div>
          )}

          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <HardDriveUpload size={15} className="text-cyan-400" /> Module Installation
            </h3>
            <div>
              <label className="label">Local Drone repo</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 font-mono text-xs" value={repoPath} onChange={(event) => setRepoPath(event.target.value)} />
                <button onClick={browseForRepo} className="btn-secondary px-3"><FolderOpen size={14} /></button>
              </div>
            </div>
            <div>
              <label className="label">Module project path</label>
              <input
                className="input-field font-mono text-xs"
                value={remoteProject}
                onChange={(event) => setForm({ ...form, remotePath: event.target.value })}
              />
            </div>
            <button onClick={installModule} disabled={!connectionReady || !!runningStep || !sudoPassword} className="btn-primary w-full justify-center">
              {runningStep === "sync" || runningStep === "bootstrap" ? <Loader2 size={14} className="animate-spin" /> : <HardDriveUpload size={14} />}
              Install Module
            </button>
            {uploadProgress && runningStep === "sync" && (
              <div className="space-y-1">
                <div className="flex justify-between text-[10px] text-slate-500">
                  <span className="truncate">{uploadProgress.file}</span>
                  <span>{uploadProgress.percent.toFixed(0)}%</span>
                </div>
                <div className="h-2 bg-bg-elevated rounded-full overflow-hidden">
                  <div className="h-full bg-cyan-500 rounded-full" style={{ width: `${uploadProgress.percent}%` }} />
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-4">
          <div className="card space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
                <ShieldCheck size={15} className="text-cyan-400" /> Checks And Tests
              </h3>
              <div className="flex items-center gap-2">
                <button onClick={saveSetupReport} disabled={Object.keys(results).length === 0} className="btn-secondary text-xs py-1.5 px-3">
                  <Save size={12} /> Save Report
                </button>
                <button onClick={runRecommendedChecks} disabled={!connectionReady || !!runningStep} className="btn-secondary text-xs py-1.5 px-3">
                  <TestTube2 size={12} /> Run Checks/Tests
                </button>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Sudo password</label>
                <div className="relative">
                  <input
                    className="input-field pr-10"
                    type={showSudoPassword ? "text" : "password"}
                    value={sudoPassword}
                    onChange={(event) => setSudoPassword(event.target.value)}
                    placeholder="only needed for bootstrap"
                  />
                  <button onClick={() => setShowSudoPassword(!showSudoPassword)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                    {showSudoPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
              <div>
                <label className="label">Runtime bundle path</label>
                <input className="input-field font-mono text-xs" value={remoteBundle} readOnly />
              </div>
            </div>

            <div className="rounded-lg border border-border bg-bg-card p-3 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-200 flex items-center gap-2">
                    <Camera size={14} className="text-cyan-400" /> Camera View Test
                  </div>
                  <p className="text-[11px] text-slate-500 mt-0.5">Capture a live preview frame from the module camera over SSH.</p>
                </div>
                <div className="flex gap-2">
                  <button onClick={captureCameraFrame} disabled={!connectionReady || capturingCamera} className="btn-secondary text-xs py-1 px-3">
                    {capturingCamera ? <Loader2 size={11} className="animate-spin" /> : <Camera size={11} />}
                    Refresh View
                  </button>
                  <button
                    onClick={() => setCameraAutoRefresh((value) => !value)}
                    disabled={!connectionReady}
                    className={cn(
                      "btn-secondary text-xs py-1 px-3",
                      cameraAutoRefresh && "border-emerald-500/30 text-emerald-400",
                    )}
                  >
                    {cameraAutoRefresh ? <EyeOff size={11} /> : <Eye size={11} />}
                    {cameraAutoRefresh ? "Stop Live View" : "Start Live View"}
                  </button>
                </div>
              </div>
              {cameraPreview ? (
                <div className="space-y-2">
                  <img src={cameraPreview} alt="Module camera preview" className="w-full aspect-video object-contain bg-black rounded border border-border" />
                  {cameraPreviewPath && <div className="text-[10px] font-mono text-slate-500">{cameraPreviewPath}</div>}
                </div>
              ) : (
                <div className="aspect-video rounded border border-dashed border-border bg-bg-base flex items-center justify-center text-xs text-slate-600">
                  No camera frame captured yet
                </div>
              )}
            </div>

            <div className="space-y-2">
              {steps.map((step) => {
                const result = results[step.id];
                const status = result?.status ?? "idle";
                return (
                  <div key={step.id} className="rounded-lg border border-border bg-bg-card px-3 py-2.5">
                    <div className="flex items-start gap-3">
                      <StatusIcon status={status} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-slate-200">{step.title}</span>
                          {step.requiresSudo && (
                            <span className="text-[10px] border border-amber-500/20 bg-amber-500/10 text-amber-400 rounded px-1.5 py-0.5">sudo</span>
                          )}
                        </div>
                        <p className="text-[11px] text-slate-500 mt-0.5">{step.detail}</p>
                      </div>
                      <button
                        onClick={() => runSetupStep(step)}
                        disabled={!connectionReady || !!runningStep || (step.requiresSudo && !sudoPassword)}
                        className="btn-secondary text-xs py-1 px-2 shrink-0"
                      >
                        {runningStep === step.id ? <Loader2 size={11} className="animate-spin" /> : step.id === "bench-report" ? <Archive size={11} /> : <Terminal size={11} />}
                        Run
                      </button>
                      {runningStep === step.id && <Loader2 size={12} className="animate-spin text-cyan-400 shrink-0 mt-0.5" />}
                    </div>
                    {result?.output && selectedOutputId !== step.id && (
                      <button onClick={() => setSelectedOutputId(step.id)} className="mt-2 text-[10px] text-cyan-400 hover:text-cyan-300">
                        Show output
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <Server size={15} className="text-cyan-400" /> Latest Output
            </h3>
            {setupReportPath && (
              <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">
                Setup report saved to <span className="font-mono">{setupReportPath}</span>
              </div>
            )}
            {output ? (
              <pre className="bg-bg-base border border-border rounded-lg px-3 py-2.5 text-[11px] font-mono text-slate-300 whitespace-pre-wrap max-h-80 overflow-y-auto leading-relaxed">
                {runningStep && results[selectedOutputId ?? ""]?.status === "running" ? `${output}▋` : output}
              </pre>
            ) : (
              <div className="rounded-lg border border-dashed border-border px-3 py-8 text-center">
                <Terminal size={20} className="text-slate-600 mx-auto mb-2" />
                <p className="text-xs text-slate-500">Run a setup check to see command output.</p>
              </div>
            )}
            <SupportBundleList bundles={supportBundles} downloadDir={SUPPORT_DOWNLOAD_DIR} />
          </div>
        </div>
      </div>
    </div>
  );
}
