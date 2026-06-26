import { useMemo, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { AlertTriangle, CheckCircle2, Cpu, FolderOpen, Layers3, Loader2, Map, PackageCheck, ShieldCheck, Upload } from "lucide-react";
import {
  DefenseHeader,
  DefenseListItem,
  DefenseMetric,
  DefensePane,
  DefenseRightPanel,
  DefenseSection,
} from "../components/DefensePane";
import { loadPipelineConfig } from "../lib/pipelineConfig";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import type { BuildDroneBundleResult, CameraProfileId, RuntimeProfileId } from "../lib/types";
import { cn } from "../lib/utils";

const DEFAULT_LOCAL_REPO = "";
const RUNTIME_PROFILES = [
  { id: "pi5_full", label: "Pi 5 full" },
  { id: "pi5_low_memory", label: "Pi 5 low memory" },
  { id: "desktop_high_compute", label: "Desktop high compute" },
] as const;
const CAMERA_PROFILES = [
  { id: "rgb_global_shutter", label: "RGB global shutter" },
  { id: "rgb_rolling_shutter", label: "RGB rolling shutter" },
  { id: "thermal_low_res", label: "Thermal low-res" },
  { id: "eo_generic", label: "EO generic" },
] as const;

function defaultBundleOutput(regionPath?: string) {
  return regionPath ? `${regionPath.replace(/[\\/]$/, "")}/mission_bundle` : "";
}

function statusTone(status?: string) {
  if (status === "passed") return "ready";
  if (status === "failed") return "critical";
  if (status === "degraded") return "warning";
  return "active";
}

export function MissionBundleBuilder() {
  const { activeDeviceId, devices, regions, updateRegion } = useAppStore();
  const downloadedRegions = regions.filter((region) => region.last_downloaded || region.source === "uploaded" || region.source === "folder");
  const [selectedRegionId, setSelectedRegionId] = useState(() => downloadedRegions[0]?.id ?? regions[0]?.id ?? "");
  const selectedRegion = regions.find((region) => region.id === selectedRegionId) ?? downloadedRegions[0] ?? regions[0];
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const pipeline = useMemo(() => loadPipelineConfig(), []);
  const [repoPath, setRepoPath] = useState(() => localStorage.getItem("drone_repo_path") || DEFAULT_LOCAL_REPO);
  const [outputDir, setOutputDir] = useState(() => defaultBundleOutput(selectedRegion?.output_path));
  const [building, setBuilding] = useState(false);
  const [result, setResult] = useState<BuildDroneBundleResult | null>(null);
  const [message, setMessage] = useState("$ bundle builder ready");
  const [runtimeProfile, setRuntimeProfile] = useState<RuntimeProfileId>(selectedRegion?.runtime_profile ?? activeDevice?.runtime_profile ?? "pi5_full");
  const [cameraProfile, setCameraProfile] = useState<CameraProfileId>(selectedRegion?.camera_profile ?? activeDevice?.camera_profile ?? "rgb_global_shutter");
  const effectiveOutputDir = outputDir || defaultBundleOutput(selectedRegion?.output_path);

  const pickOutputDir = async () => {
    try {
      const dir = await open({ directory: true, multiple: false, title: "Select mission bundle output folder" });
      if (typeof dir === "string") setOutputDir(dir);
    } catch (error) {
      setMessage(`$ select output\n${error}`);
    }
  };

  const pickRepoPath = async () => {
    try {
      const dir = await open({ directory: true, multiple: false, title: "Select Drone repo path" });
      if (typeof dir === "string") {
        setRepoPath(dir);
        localStorage.setItem("drone_repo_path", dir);
      }
    } catch (error) {
      setMessage(`$ select repo\n${error}`);
    }
  };

  const build = async () => {
    if (!selectedRegion || !effectiveOutputDir || !repoPath) {
      setMessage("$ build mission bundle\nMissing map source, repo path, or output directory.");
      return;
    }
    setBuilding(true);
    setResult(null);
    setMessage(`$ build mission bundle\nmap=${selectedRegion.name}\noutput=${effectiveOutputDir}`);
    try {
      const built = await cmd.buildDroneBundle({
        region_dir: selectedRegion.output_path,
        output_dir: effectiveOutputDir,
        repo_path: repoPath,
        pipeline: pipeline.pipeline,
        feature_method: pipeline.featureMethod,
        max_features: pipeline.maxFeatures,
        runtime_profile: runtimeProfile,
        camera_profile: cameraProfile,
        hardware_profile: activeDevice?.hardware_profile,
      });
      setResult(built);
      updateRegion({
        ...selectedRegion,
        lifecycle_state: built.geospatial_health?.status === "failed" ? "failed" : "built",
        active_bundle_path: effectiveOutputDir,
        active_bundle_state: "configured",
        feature_count: built.terrain_feature_count ?? selectedRegion.feature_count,
        estimated_pi_runtime_cost: built.geospatial_health?.map_quality?.estimated_pi_runtime_cost ?? selectedRegion.estimated_pi_runtime_cost,
        runtime_profile: runtimeProfile,
        camera_profile: cameraProfile,
      });
      setMessage(
        `$ build mission bundle\nstatus=${built.geospatial_health?.status ?? "built"}\n` +
          `tiles=${built.terrain_tile_count ?? "n/a"} features=${built.terrain_feature_count ?? "n/a"}\n` +
          `manifest=${built.manifest_path}`,
      );
    } catch (error) {
      setMessage(`$ build mission bundle\n${error}`);
    } finally {
      setBuilding(false);
    }
  };

  const healthStatus = result?.geospatial_health?.status ?? "not built";
  const readiness = [
    { label: "MAP SOURCE", ok: Boolean(selectedRegion), detail: selectedRegion?.name ?? "none selected" },
    { label: "LIFECYCLE", ok: ["built", "uploaded", "active"].includes(selectedRegion?.lifecycle_state ?? ""), detail: selectedRegion?.lifecycle_state ?? "local" },
    { label: "GEOREF", ok: (selectedRegion?.georef_confidence ?? 0) >= 0.7, detail: `${Math.round((selectedRegion?.georef_confidence ?? 0) * 100)}% confidence` },
    { label: "PROFILE", ok: true, detail: `${runtimeProfile} / ${cameraProfile}` },
  ];

  return (
    <DefensePane
      right={
        <DefenseRightPanel title="BUNDLE HEALTH">
          <div className="flex justify-center border-b border-white/10 pb-4">
            <div className="holo-core" />
          </div>
          <DefenseMetric label="STATUS" value={healthStatus.toUpperCase()} detail={result?.manifest_path ?? "manifest pending"} tone={statusTone(result?.geospatial_health?.status)} />
          <DefenseListItem label="STAC manifest" detail={result?.stac_manifest_path ?? "pending"} tone={result?.stac_manifest_path ? "ready" : "warning"} />
          <DefenseListItem label="Tile index" detail={result?.terrain_index_path ?? "pending"} tone={result?.terrain_index_path ? "ready" : "warning"} />
          <DefenseListItem label="Pi runtime cost" detail={result?.geospatial_health?.map_quality?.estimated_pi_runtime_cost ?? selectedRegion?.estimated_pi_runtime_cost ?? "unknown"} tone="active" />
          <DefenseListItem label="Lifecycle" detail={selectedRegion?.lifecycle_state ?? "local"} tone={selectedRegion?.lifecycle_state === "active" ? "ready" : selectedRegion?.lifecycle_state === "failed" ? "critical" : "warning"} />
          <DefenseListItem label="Active bundle" detail={selectedRegion?.active_bundle_path ?? effectiveOutputDir ?? "pending"} tone={selectedRegion?.active_bundle_path ? "active" : "warning"} />
          <button disabled={!result} className="w-full border border-status-active/40 bg-status-active/10 px-3 py-2 font-label-caps text-label-caps text-status-active disabled:opacity-40">
            <Upload size={14} className="inline mr-2" /> UPLOAD VIA MISSION PLANNER
          </button>
        </DefenseRightPanel>
      }
    >
      <DefenseHeader
        eyebrow="MISSION BUNDLE BUILDER"
        title="MAP-TO-RUNTIME PACKAGE CONTROL"
        subtitle="terrain tiles // feature index // manifest // checksums"
        action={
          <button onClick={build} disabled={building || !selectedRegion} className="border border-status-active bg-status-active/10 px-4 py-2 font-label-caps text-label-caps text-status-active transition-colors hover:bg-status-active hover:text-[#05070A] disabled:opacity-40">
            {building ? <Loader2 size={14} className="inline mr-2 animate-spin" /> : <PackageCheck size={14} className="inline mr-2" />}
            BUILD BUNDLE
          </button>
        }
      />

      <div className="grid grid-cols-4 gap-3">
        {readiness.map((item) => (
          <DefenseMetric key={item.label} label={item.label} value={item.ok ? "READY" : "REVIEW"} detail={item.detail} tone={item.ok ? "ready" : "warning"} />
        ))}
      </div>

      <div className="grid grid-cols-[1fr_360px] gap-4">
        <DefenseSection title="PACKAGE INPUTS" icon={<PackageCheck size={14} />}>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Map source</label>
              <select
                className="input-field"
                value={selectedRegion?.id ?? ""}
                onChange={(event) => {
                  const next = regions.find((region) => region.id === event.target.value);
                  setSelectedRegionId(event.target.value);
                  if (next) setOutputDir(defaultBundleOutput(next.output_path));
                  setResult(null);
                }}
              >
                {regions.length === 0 && <option value="">No maps available</option>}
                {regions.map((region) => <option key={region.id} value={region.id}>{region.name}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Runtime profile</label>
              <select className="input-field" value={runtimeProfile} onChange={(event) => setRuntimeProfile(event.target.value as RuntimeProfileId)}>
                {RUNTIME_PROFILES.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Camera profile</label>
              <select className="input-field" value={cameraProfile} onChange={(event) => setCameraProfile(event.target.value as CameraProfileId)}>
                {CAMERA_PROFILES.map((profile) => <option key={profile.id} value={profile.id}>{profile.label}</option>)}
              </select>
            </div>
            <PathField label="Repo path" value={repoPath} onChange={setRepoPath} onPick={pickRepoPath} />
            <PathField label="Bundle output" value={effectiveOutputDir} onChange={setOutputDir} onPick={pickOutputDir} />
          </div>
        </DefenseSection>

        <DefenseSection title="FEATURE PACKAGE" icon={<Cpu size={14} />}>
          <div className="grid grid-cols-1 gap-2">
            <DefenseListItem label="Pipeline" detail={pipeline.pipeline} tone="active" />
            <DefenseListItem label="Feature method" detail={pipeline.featureMethod.toUpperCase()} tone="active" />
            <DefenseListItem label="Max features" detail={pipeline.maxFeatures.toLocaleString()} tone="active" />
          </div>
        </DefenseSection>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <DefenseSection title="MAP METADATA" icon={<Map size={14} />}>
          <div className="grid grid-cols-2 gap-2">
            <DefenseMetric label="GSD" value={selectedRegion?.gsd_m_per_px ? `${selectedRegion.gsd_m_per_px.toFixed(2)}` : "n/a"} detail="m/px" tone="active" />
            <DefenseMetric label="TILES" value={result?.terrain_tile_count ?? selectedRegion?.tile_count ?? "n/a"} detail="terrain index" tone="active" />
          </div>
        </DefenseSection>
        <DefenseSection title="FEATURE INDEX" icon={<Layers3 size={14} />}>
          <DefenseMetric label="FEATURES" value={result?.terrain_feature_count?.toLocaleString() ?? "NOT BUILT"} detail={result?.features_path ?? "awaiting build"} tone={result ? "ready" : "warning"} />
        </DefenseSection>
        <DefenseSection title="BUILD GATE" icon={<ShieldCheck size={14} />}>
          <div className={cn("border p-3 text-xs", result?.geospatial_health?.status === "failed" ? "border-status-critical/40 bg-red-500/5 text-status-critical" : "border-border bg-bg-card text-white/60")}>
            {result?.geospatial_health?.status === "passed" ? <CheckCircle2 size={14} className="inline mr-2 text-status-ready" /> : <AlertTriangle size={14} className="inline mr-2 text-status-warning" />}
            {result ? `Bundle generated at ${result.bundle_dir}` : "Build a bundle to generate checksums, STAC metadata, tile index, and feature map."}
          </div>
        </DefenseSection>
      </div>

      <pre className="glass-panel min-h-28 whitespace-pre-wrap p-3 font-data-mono text-[11px] text-status-active/80">{message}</pre>
    </DefensePane>
  );
}

function PathField({ label, value, onChange, onPick }: { label: string; value: string; onChange: (value: string) => void; onPick: () => void }) {
  return (
    <div>
      <label className="label">{label}</label>
      <div className="flex gap-2">
        <input className="input-field flex-1 font-mono text-xs" value={value} onChange={(event) => onChange(event.target.value)} />
        <button onClick={onPick} className="btn-secondary px-3">
          <FolderOpen size={14} />
        </button>
      </div>
    </div>
  );
}
