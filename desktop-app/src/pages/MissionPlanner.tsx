import { useEffect, useMemo, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { CircleMarker, MapContainer, Polyline, TileLayer, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import {
  CheckCircle2,
  Cpu,
  Crosshair,
  FolderOpen,
  HardDrive,
  Loader2,
  Map as MapIcon,
  Play,
  RadioTower,
  Route,
  Server,
  ShieldCheck,
  Trash2,
  Upload as UploadIcon,
} from "lucide-react";
import { cmd } from "../lib/tauri";
import { useAppStore } from "../lib/store";
import { cn } from "../lib/utils";
import type {
  BuildDroneBundleResult,
  Device,
  FeatureMethod,
  UploadProgress,
  VisionPipeline,
} from "../lib/types";

type UploadPayload = UploadProgress;
type Waypoint = { lat: number; lon: number };

const DEFAULT_LOCAL_REPO = "/Users/izzyfisi/Documents/DRONE";

function shellQuote(value: string) {
  return `'${value.replace(/'/g, "'\"'\"'")}'`;
}

function defaultRemoteBundleDir(device?: Device) {
  const user = device?.username || "user";
  return `/home/${user}/drone-data/map_bundles/mission_bundle`;
}

function defaultRemoteProjectPath(device?: Device) {
  const user = device?.username || "user";
  return device?.remote_project_path || `/home/${user}/Drone`;
}

function missionCenter(region?: { lat_min: number; lat_max: number; lon_min: number; lon_max: number }): [number, number] {
  if (!region) return [37.775, -122.418];
  return [(region.lat_min + region.lat_max) / 2, (region.lon_min + region.lon_max) / 2];
}

function missionBounds(region?: { lat_min: number; lat_max: number; lon_min: number; lon_max: number }): [[number, number], [number, number]] | undefined {
  if (!region) return undefined;
  return [[region.lat_min, region.lon_min], [region.lat_max, region.lon_max]];
}

function waypointDistanceM(a: Waypoint, b: Waypoint): number {
  const latCenter = ((a.lat + b.lat) / 2) * Math.PI / 180;
  const north = (b.lat - a.lat) * 111_320;
  const east = (b.lon - a.lon) * 111_320 * Math.cos(latCenter);
  return Math.sqrt(north * north + east * east);
}

function missionDistanceM(waypoints: Waypoint[]): number {
  return waypoints.slice(1).reduce((sum, waypoint, index) => sum + waypointDistanceM(waypoints[index], waypoint), 0);
}

function surveyPath(region: { lat_min: number; lat_max: number; lon_min: number; lon_max: number }): Waypoint[] {
  const latPad = (region.lat_max - region.lat_min) * 0.12;
  const lonPad = (region.lon_max - region.lon_min) * 0.12;
  const south = region.lat_min + latPad;
  const north = region.lat_max - latPad;
  const west = region.lon_min + lonPad;
  const east = region.lon_max - lonPad;
  const lane1 = south + (north - south) * 0.25;
  const lane2 = south + (north - south) * 0.5;
  const lane3 = south + (north - south) * 0.75;
  return [
    { lat: lane1, lon: west },
    { lat: lane1, lon: east },
    { lat: lane2, lon: east },
    { lat: lane2, lon: west },
    { lat: lane3, lon: west },
    { lat: lane3, lon: east },
  ];
}

function MissionMap({
  region,
  waypoints,
  onAddWaypoint,
}: {
  region?: { lat_min: number; lat_max: number; lon_min: number; lon_max: number };
  waypoints: Waypoint[];
  onAddWaypoint: (waypoint: Waypoint) => void;
}) {
  const bounds = missionBounds(region);
  const center = missionCenter(region);

  function ClickLayer() {
    useMapEvents({
      click(event) {
        onAddWaypoint({ lat: event.latlng.lat, lon: event.latlng.lng });
      },
    });
    return null;
  }

  return (
    <MapContainer
      center={center}
      zoom={region ? 16 : 13}
      bounds={bounds}
      className="w-full h-full"
      scrollWheelZoom
    >
      <TileLayer
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        attribution="© Esri"
        maxZoom={20}
        maxNativeZoom={19}
      />
      <TileLayer
        url="https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
        attribution=""
        maxZoom={20}
        opacity={0.85}
      />
      <ClickLayer />
      {waypoints.length > 1 && (
        <Polyline
          positions={waypoints.map((waypoint) => [waypoint.lat, waypoint.lon])}
          pathOptions={{ color: "#06B6D4", weight: 3 }}
        />
      )}
      {waypoints.map((waypoint, index) => (
        <CircleMarker
          key={`${waypoint.lat}-${waypoint.lon}-${index}`}
          center={[waypoint.lat, waypoint.lon]}
          radius={6}
          pathOptions={{ color: "#22D3EE", fillColor: "#0891B2", fillOpacity: 0.9, weight: 2 }}
        />
      ))}
    </MapContainer>
  );
}

export function MissionPlanner() {
  const { devices, regions, activeDeviceId, setActiveDevice } = useAppStore();
  const activeDevice = devices.find((d) => d.id === activeDeviceId);
  const downloadedRegions = regions.filter((r) => r.last_downloaded);

  const [selectedRegionId, setSelectedRegionId] = useState(downloadedRegions[0]?.id ?? "");
  const selectedRegion = useMemo(
    () => regions.find((r) => r.id === selectedRegionId),
    [regions, selectedRegionId],
  );

  const [repoPath, setRepoPath] = useState(
    () => localStorage.getItem("drone_repo_path") || DEFAULT_LOCAL_REPO,
  );
  const [bundleOutputDir, setBundleOutputDir] = useState("");
  const [pipeline, setPipeline] = useState<VisionPipeline>(
    (activeDevice?.vision_pipeline as VisionPipeline | undefined) || "classical",
  );
  const [featureMethod, setFeatureMethod] = useState<FeatureMethod>(
    (activeDevice?.feature_method as FeatureMethod | undefined) || "orb",
  );
  const [maxFeatures, setMaxFeatures] = useState(3000);
  const [remoteBundleDir, setRemoteBundleDir] = useState(defaultRemoteBundleDir(activeDevice));
  const [enableMavlink, setEnableMavlink] = useState(false);
  const [missionAltitudeM, setMissionAltitudeM] = useState(35);
  const [missionSpeedMps, setMissionSpeedMps] = useState(4);
  const [waypoints, setWaypoints] = useState<Waypoint[]>([]);

  const [building, setBuilding] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [cmdRunning, setCmdRunning] = useState(false);
  const [fileProgress, setFileProgress] = useState<Record<string, number>>({});
  const [bundleResult, setBundleResult] = useState<BuildDroneBundleResult | null>(null);
  const [commandOutput, setCommandOutput] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRemoteBundleDir(defaultRemoteBundleDir(activeDevice));
    if (activeDevice?.vision_pipeline) setPipeline(activeDevice.vision_pipeline);
    if (activeDevice?.feature_method) setFeatureMethod(activeDevice.feature_method);
  }, [activeDevice?.id]);

  const effectiveBundleDir =
    bundleOutputDir ||
    (selectedRegion ? `${selectedRegion.output_path.replace(/[\\/]$/, "")}/mission_bundle` : "");

  const pickRepo = async () => {
    const dir = await open({ directory: true, multiple: false, title: "Select Drone repo folder" });
    if (dir && typeof dir === "string") {
      setRepoPath(dir);
      localStorage.setItem("drone_repo_path", dir);
    }
  };

  const pickBundleOutput = async () => {
    const dir = await open({ directory: true, multiple: false, title: "Select mission_bundle output folder" });
    if (dir && typeof dir === "string") setBundleOutputDir(dir);
  };

  const buildBundle = async () => {
    if (!selectedRegion || !effectiveBundleDir || !repoPath) return;
    setBuilding(true);
    setError(null);
    setCommandOutput("");
    try {
      const result = await cmd.buildDroneBundle({
        region_dir: selectedRegion.output_path,
        output_dir: effectiveBundleDir,
        repo_path: repoPath,
        pipeline,
        feature_method: featureMethod,
        max_features: maxFeatures,
      });
      setBundleResult(result);
      setCommandOutput([result.command, result.stdout, result.stderr].filter(Boolean).join("\n"));
    } catch (e) {
      setError(String(e));
    } finally {
      setBuilding(false);
    }
  };

  const uploadBundle = async () => {
    if (!activeDevice || activeDevice.kind !== "pi5" || !activeDevice.host || !activeDevice.auth || !bundleResult) return;
    setUploading(true);
    setError(null);
    setFileProgress({});
    const unlisten = await listen<UploadPayload>("upload-progress", (e) => {
      setFileProgress((p) => ({ ...p, [e.payload.file]: e.payload.percent }));
    });
    try {
      await cmd.sshUploadDirectory(
        activeDevice.host,
        activeDevice.port ?? 22,
        activeDevice.username ?? "user",
        activeDevice.auth,
        bundleResult.bundle_dir,
        remoteBundleDir,
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(false);
      unlisten();
    }
  };

  const runPiCommand = async (label: string, command: string) => {
    if (!activeDevice || activeDevice.kind !== "pi5" || !activeDevice.host || !activeDevice.auth) return;
    setCmdRunning(true);
    setError(null);
    setCommandOutput(`$ ${label}\n`);
    try {
      const result = await cmd.sshRunCommand(
        activeDevice.host,
        activeDevice.port ?? 22,
        activeDevice.username ?? "user",
        activeDevice.auth,
        command,
      );
      const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
      setCommandOutput(`$ ${label}\n${output || "(no output)"}\n[exit ${result.exit_code}]`);
    } catch (e) {
      setError(String(e));
    } finally {
      setCmdRunning(false);
    }
  };

  const remoteProject = defaultRemoteProjectPath(activeDevice);
  const mavlinkEnv = enableMavlink && activeDevice?.mavlink_endpoint
    ? `VISION_NAV_MAVLINK_ENDPOINT=${shellQuote(activeDevice.mavlink_endpoint)} `
    : "";

  if (!activeDevice) {
    return (
      <div className="p-6 flex flex-col items-center justify-center h-full animate-fade-in">
        <Server size={40} className="text-slate-600 mb-4" />
        <h2 className="section-title mb-2">No Device Selected</h2>
        <p className="text-slate-400 text-sm text-center mb-6">
          Select a runtime module before planning and deploying a mission.
        </p>
        <div className="flex gap-3">
          {devices.map((d) => (
            <button key={d.id} onClick={() => setActiveDevice(d.id)} className="btn-secondary">
              {d.kind === "pi5" ? <Server size={14} /> : <HardDrive size={14} />}
              {d.name}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      <div>
        <h1 className="section-title">Mission Planner</h1>
        <p className="text-slate-400 text-sm mt-1">
          Choose the flight area, sketch the drone path, build the vision bundle, and validate the runtime module.
        </p>
      </div>

      <div className="grid grid-cols-[1.2fr_0.8fr] gap-6">
        <div className="card p-0 overflow-hidden h-[420px]">
          <MissionMap
            region={selectedRegion}
            waypoints={waypoints}
            onAddWaypoint={(waypoint) => setWaypoints((current) => [...current, waypoint])}
          />
        </div>
        <div className="card space-y-4">
          <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
            <Route size={14} className="text-cyan-400" /> Flight Path
          </h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Altitude m</label>
              <input className="input-field" type="number" value={missionAltitudeM} onChange={(e) => setMissionAltitudeM(Number(e.target.value))} />
            </div>
            <div>
              <label className="label">Speed m/s</label>
              <input className="input-field" type="number" value={missionSpeedMps} onChange={(e) => setMissionSpeedMps(Number(e.target.value))} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => selectedRegion && setWaypoints(surveyPath(selectedRegion))}
              disabled={!selectedRegion}
              className="btn-secondary justify-center"
            >
              <Crosshair size={13} /> Survey Path
            </button>
            <button onClick={() => setWaypoints([])} className="btn-secondary justify-center text-red-400 border-red-500/20">
              <Trash2 size={13} /> Clear
            </button>
          </div>
          <div className="rounded-lg border border-border bg-bg-card p-3 space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-slate-400">Waypoints</span>
              <span className="text-slate-200 font-medium">{waypoints.length}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-slate-400">Path length</span>
              <span className="text-slate-200 font-medium">{(missionDistanceM(waypoints) / 1000).toFixed(2)} km</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-slate-400">Est. flight time</span>
              <span className="text-slate-200 font-medium">
                {missionSpeedMps > 0 ? `${Math.ceil(missionDistanceM(waypoints) / missionSpeedMps / 60)} min` : "n/a"}
              </span>
            </div>
          </div>
          <div className="max-h-32 overflow-y-auto space-y-1">
            {waypoints.length === 0 ? (
              <p className="text-xs text-slate-500">Click the map to add waypoints, or generate a survey path from the selected map source.</p>
            ) : waypoints.map((waypoint, index) => (
              <div key={`${waypoint.lat}-${waypoint.lon}-${index}`} className="flex justify-between text-[11px] font-mono text-slate-500">
                <span>WP{index + 1}</span>
                <span>{waypoint.lat.toFixed(6)}, {waypoint.lon.toFixed(6)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-[1.1fr_0.9fr] gap-6">
        <div className="space-y-4">
          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <MapIcon size={14} className="text-cyan-400" /> Flight Area / Map Source
            </h3>
            {downloadedRegions.length === 0 ? (
              <p className="text-xs text-slate-500">Download an area or import your own map from Maps first.</p>
            ) : (
              <div className="space-y-2">
                {downloadedRegions.map((region) => (
                  <button
                    key={region.id}
                    onClick={() => {
                      setSelectedRegionId(region.id);
                      setBundleResult(null);
                    }}
                    className={cn(
                      "w-full text-left rounded-lg border p-3 transition-colors",
                      selectedRegionId === region.id
                        ? "border-cyan-500/40 bg-cyan-500/5"
                        : "border-border hover:border-border-strong",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <MapIcon size={13} className="text-cyan-400 shrink-0" />
                      <span className="text-sm font-medium text-slate-200">{region.name}</span>
                      <span className="text-[10px] bg-bg-elevated border border-border rounded px-1.5 py-0.5 text-slate-500">
                        {region.source === "uploaded" ? "Uploaded" : region.source === "folder" ? "Folder" : "Tiles"}
                      </span>
                      {selectedRegionId === region.id && <CheckCircle2 size={13} className="text-cyan-400 ml-auto" />}
                    </div>
                    <div className="text-[11px] text-slate-500 font-mono mt-1 truncate">{region.output_path}</div>
                    {region.gsd_m_per_px != null && (
                      <div className="text-[11px] text-slate-500 mt-1">{region.gsd_m_per_px.toFixed(2)} m/px</div>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="card space-y-4">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <Cpu size={14} className="text-cyan-400" /> Vision Pipeline
            </h3>
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={() => setPipeline("classical")}
                className={cn(
                  "rounded-lg border p-3 text-left transition-colors",
                  pipeline === "classical" ? "border-emerald-500/40 bg-emerald-500/5" : "border-border",
                )}
              >
                <div className="text-sm font-medium text-slate-200">Classical</div>
                <div className="text-xs text-slate-500 mt-1">ORB or AKAZE for low-compute runtime modules.</div>
              </button>
              <button
                onClick={() => setPipeline("neural")}
                className={cn(
                  "rounded-lg border p-3 text-left transition-colors",
                  pipeline === "neural" ? "border-violet-500/40 bg-violet-500/5" : "border-border",
                )}
              >
                <div className="text-sm font-medium text-slate-200">SuperPoint + LightGlue</div>
                <div className="text-xs text-slate-500 mt-1">Optional high-compute path for GPU-class devices.</div>
              </button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Classical feature method</label>
                <select
                  className="input-field"
                  value={featureMethod}
                  onChange={(e) => setFeatureMethod(e.target.value as FeatureMethod)}
                >
                  <option value="orb">ORB</option>
                  <option value="akaze">AKAZE</option>
                  <option value="sift">SIFT</option>
                </select>
              </div>
              <div>
                <label className="label">Max features</label>
                <input
                  className="input-field"
                  type="number"
                  min={500}
                  step={500}
                  value={maxFeatures}
                  onChange={(e) => setMaxFeatures(Number(e.target.value))}
                />
              </div>
            </div>
          </div>

          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <FolderOpen size={14} className="text-cyan-400" /> Local Build Paths
            </h3>
            <div>
              <label className="label">Drone repo path</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 font-mono text-xs" value={repoPath} onChange={(e) => setRepoPath(e.target.value)} />
                <button onClick={pickRepo} className="btn-secondary px-3"><FolderOpen size={14} /></button>
              </div>
            </div>
            <div>
              <label className="label">Bundle output directory</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 font-mono text-xs" value={effectiveBundleDir} onChange={(e) => setBundleOutputDir(e.target.value)} />
                <button onClick={pickBundleOutput} className="btn-secondary px-3"><FolderOpen size={14} /></button>
              </div>
            </div>
            <button
              onClick={buildBundle}
              disabled={!selectedRegion || !repoPath || building}
              className="btn-primary w-full justify-center"
            >
              {building ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />}
              Build Mission Bundle
            </button>
          </div>
        </div>

        <div className="space-y-4">
          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <UploadIcon size={14} className="text-cyan-400" /> Module Deployment
            </h3>
            <div>
              <label className="label">Remote bundle directory</label>
              <input
                className="input-field font-mono text-xs"
                value={remoteBundleDir}
                onChange={(e) => setRemoteBundleDir(e.target.value)}
              />
            </div>
            <button
              onClick={uploadBundle}
              disabled={!bundleResult || activeDevice.kind !== "pi5" || uploading}
              className="btn-primary w-full justify-center"
            >
              {uploading ? <Loader2 size={15} className="animate-spin" /> : <UploadIcon size={15} />}
              Upload Mission Bundle
            </button>
            {uploading && Object.keys(fileProgress).length > 0 && (
              <div className="space-y-2 max-h-40 overflow-y-auto">
                {Object.entries(fileProgress).map(([file, pct]) => (
                  <div key={file}>
                    <div className="flex justify-between text-[11px] text-slate-400 mb-1">
                      <span className="truncate font-mono">{file}</span>
                      <span>{pct.toFixed(0)}%</span>
                    </div>
                    <div className="h-1.5 bg-bg-elevated rounded-full overflow-hidden">
                      <div className="h-full bg-cyan-500 rounded-full transition-all" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card space-y-3">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <RadioTower size={14} className="text-cyan-400" /> Runtime And MAVLink
            </h3>
            <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <div>
                <div className="text-sm text-slate-200">Send MAVLink vision messages</div>
                <div className="text-[11px] text-slate-500 font-mono">{activeDevice.mavlink_endpoint || "No endpoint configured"}</div>
              </div>
              <button
                onClick={() => setEnableMavlink((v) => !v)}
                className={cn(
                  "w-11 h-6 rounded-full border transition-colors relative",
                  enableMavlink ? "bg-cyan-500/20 border-cyan-500/50" : "bg-bg-elevated border-border",
                )}
              >
                <span
                  className={cn(
                    "absolute top-0.5 h-5 w-5 rounded-full bg-slate-300 transition-transform",
                    enableMavlink ? "translate-x-5" : "translate-x-0.5",
                  )}
                />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <button
                disabled={activeDevice.kind !== "pi5" || cmdRunning}
                onClick={() => runPiCommand(
                  "validate bundle",
                  `cd ${shellQuote(remoteProject)} && VISION_NAV_BUNDLE=${shellQuote(remoteBundleDir)} ./scripts/pi/validate_vision_nav_bundle.sh`,
                )}
                className="btn-secondary justify-center"
              >
                {cmdRunning ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />}
                Validate
              </button>
              <button
                disabled={activeDevice.kind !== "pi5" || cmdRunning}
                onClick={() => runPiCommand(
                  enableMavlink ? "run loop with mavlink" : "run loop",
                  `cd ${shellQuote(remoteProject)} && VISION_NAV_BUNDLE=${shellQuote(remoteBundleDir)} ${mavlinkEnv}VISION_NAV_COUNT=30 ./scripts/pi/run_vision_nav_loop.sh`,
                )}
                className="btn-secondary justify-center text-emerald-400 border-emerald-500/20"
              >
                <Play size={13} />
                Run 30 Frames
              </button>
            </div>
          </div>

          {bundleResult && (
            <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2 text-emerald-400 text-sm">
              <CheckCircle2 size={15} /> Bundle ready: <span className="font-mono text-xs truncate">{bundleResult.bundle_dir}</span>
            </div>
          )}

          {error && (
            <div className="bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 text-red-400 text-xs whitespace-pre-wrap">
              {error}
            </div>
          )}

          {commandOutput && (
            <pre className="bg-bg-base border border-border rounded-lg px-3 py-2.5 text-[11px] font-mono text-slate-300 whitespace-pre-wrap max-h-72 overflow-y-auto leading-relaxed">
              {cmdRunning ? commandOutput + "..." : commandOutput}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
