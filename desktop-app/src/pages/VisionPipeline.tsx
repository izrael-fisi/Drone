import { useEffect, useMemo, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { AlertTriangle, Camera, CheckCircle2, Download, FolderOpen, Save, Settings2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { loadPipelineConfig, savePipelineConfig } from "../lib/pipelineConfig";
import type { PipelineConfig } from "../lib/pipelineConfig";
import { useAppStore } from "../lib/store";
import type { FeatureMethod } from "../lib/types";
import { cn } from "../lib/utils";

const DOWNLOAD_URLS = {
  superpoint: "https://github.com/cvg/LightGlue/releases/download/v0.1_arxiv/superpoint_v1.pth",
  lightglue: "https://github.com/cvg/LightGlue/releases/download/v0.1_arxiv/lightglue_v0.1_disk.pth",
};

const FEATURE_METHODS: { value: FeatureMethod; label: string; detail: string }[] = [
  { value: "orb", label: "ORB", detail: "Pi default" },
  { value: "akaze", label: "AKAZE", detail: "Nonlinear" },
  { value: "sift", label: "SIFT", detail: "Desktop" },
];

function runtimeEstimate(config: PipelineConfig) {
  if (config.pipeline === "neural") {
    return {
      piFps: 1.2,
      piLatencyMs: 830,
      gpuFps: 28.4,
      gpuLatencyMs: 35,
      piLoad: 94,
      gpuLoad: 26,
      label: "High compute",
    };
  }
  const featureScale = Math.max(0.75, config.maxFeatures / 3000);
  const methodScale = config.featureMethod === "akaze" ? 0.72 : config.featureMethod === "sift" ? 0.38 : 1;
  const piFps = Math.max(1.1, 8.5 * methodScale / featureScale);
  return {
    piFps,
    piLatencyMs: Math.round(1000 / piFps),
    gpuFps: Math.max(18, 48 * methodScale / Math.sqrt(featureScale)),
    gpuLatencyMs: Math.round(1000 / Math.max(18, 48 * methodScale / Math.sqrt(featureScale))),
    piLoad: Math.min(88, Math.round(38 * featureScale / methodScale)),
    gpuLoad: Math.min(52, Math.round(16 * featureScale / Math.max(0.5, methodScale))),
    label: "Pi ready",
  };
}

export function VisionPipelinePage() {
  const { activeDeviceId, devices, regions } = useAppStore();
  const navigate = useNavigate();
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const [config, setConfig] = useState<PipelineConfig>(() => loadPipelineConfig());
  const [saved, setSaved] = useState(false);
  const [appliedAt, setAppliedAt] = useState<string | null>(null);
  const [calibrationState, setCalibrationState] = useState<"needed" | "captured" | "verified">(
    () => (localStorage.getItem("drone_camera_calibration_state") as "needed" | "captured" | "verified" | null) ?? "needed",
  );
  const estimate = useMemo(() => runtimeEstimate(config), [config]);
  const downloadedMapCount = regions.filter((region) => region.last_downloaded).length;

  useEffect(() => {
    setSaved(false);
  }, [config]);

  const update = <K extends keyof PipelineConfig>(key: K, value: PipelineConfig[K]) => {
    setConfig((current) => ({ ...current, [key]: value }));
  };

  const pickWeights = async (key: "superpointPath" | "lightgluePath") => {
    const file = await open({
      multiple: false,
      filters: [{ name: "PyTorch weights", extensions: ["pth", "pt"] }],
    });
    if (file && typeof file === "string") update(key, file);
  };

  const save = () => {
    savePipelineConfig(config);
    setSaved(true);
    setAppliedAt(new Date().toLocaleTimeString());
  };

  const selectClassicalMethod = (method: FeatureMethod) => {
    setConfig((current) => ({ ...current, pipeline: "classical", featureMethod: method }));
  };

  const updateCalibrationState = (state: "needed" | "captured" | "verified") => {
    setCalibrationState(state);
    localStorage.setItem("drone_camera_calibration_state", state);
  };

  return (
    <div className="ops-screen-bg relative flex h-full min-h-[calc(100vh-96px)] overflow-hidden animate-fade-in">
      <div
        className="pointer-events-none absolute inset-0 z-0 opacity-[0.04]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(104, 199, 230, 0.2) 1px, transparent 1px), linear-gradient(90deg, rgba(104, 199, 230, 0.2) 1px, transparent 1px)",
          backgroundSize: "56px 56px",
        }}
      />

      <main className="z-10 flex flex-1 flex-col gap-4 overflow-y-auto p-4">
        <div className="mb-1 flex items-end justify-between border-b border-border pb-3">
          <div>
            <h1 className="font-headline-lg text-headline-lg font-semibold tracking-tight text-slate-100">
              ALGORITHM INTELLIGENCE
            </h1>
            <p className="font-data-mono text-data-mono text-slate-500">GNSS-DENIED NAVIGATION PARAMS</p>
          </div>
          <div className="flex items-center gap-2">
            {!saved && <span className="h-2 w-2 bg-status-warning shadow-[0_0_8px_#F59E0B]" />}
            {!saved && <span className="font-label-caps text-label-caps text-status-warning">UNSAVED CHANGES</span>}
            <button onClick={save} className="border border-status-active bg-status-active/10 px-4 py-2 font-label-caps text-label-caps text-status-active transition-colors hover:bg-status-active hover:text-[#05070A]">
              <Save size={14} /> APPLY TO TARGET
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <section className="glass-panel p-4">
            <h2 className="font-label-caps text-label-caps mb-4 border-b border-border pb-2 text-slate-300">
              PIPELINE ARCHITECTURE
            </h2>
            <div className="space-y-6">
              <div className={cn(
                "relative overflow-hidden border bg-bg-surface p-3 shadow-depth-inset",
                config.pipeline === "neural" ? "border-status-active/50" : "border-border",
              )}>
                <div className="absolute right-0 top-0 bg-status-active/20 px-2 py-0.5 text-[8px] font-bold text-status-active">
                  NEURAL
                </div>
                <h3 className="font-data-mono text-[10px] text-white/60 mb-2">FEATURE EXTRACTION</h3>
                <label className={cn(
                  "flex cursor-pointer items-center border p-2 transition-colors",
                  config.pipeline === "neural"
                    ? "border-status-active bg-status-active/10"
                    : "border-white/10 bg-[#151F36]/30 hover:border-white/30",
                )}>
                  <input
                    checked={config.pipeline === "neural"}
                    onChange={() => update("pipeline", "neural")}
                    className="form-radio text-status-active bg-[#080B14] border-status-active w-4 h-4"
                    name="extractor"
                    type="radio"
                  />
                  <span className="ml-3 font-data-mono text-data-mono text-white">SuperPoint (Deep)</span>
                </label>
                <h3 className="font-data-mono text-[10px] text-white/60 mt-3 mb-2">FEATURE MATCHING</h3>
                <label className={cn(
                  "flex cursor-pointer items-center border p-2 transition-colors",
                  config.pipeline === "neural"
                    ? "border-status-active bg-status-active/10"
                    : "border-white/10 bg-[#151F36]/30 hover:border-white/30",
                )}>
                  <input
                    checked={config.pipeline === "neural"}
                    onChange={() => update("pipeline", "neural")}
                    className="form-radio text-status-active bg-[#080B14] border-status-active w-4 h-4"
                    name="matcher"
                    type="radio"
                  />
                  <span className="ml-3 font-data-mono text-data-mono text-white">LightGlue (Transformer)</span>
                </label>
              </div>

              <div className={cn(
                "relative border bg-bg-surface p-3 shadow-depth-inset",
                config.pipeline === "classical" ? "border-status-active/40" : "border-border",
              )}>
                <div className="absolute right-0 top-0 bg-white/10 px-2 py-0.5 text-[8px] font-bold text-white/50">
                  CLASSICAL
                </div>
                <h3 className="font-data-mono text-[10px] text-white/50 mb-2">FEATURE EXTRACTION</h3>
                <div className="space-y-2">
                  {FEATURE_METHODS.map((method) => (
                    <label
                      key={method.value}
                      className={cn(
                        "flex cursor-pointer items-center border p-2 transition-colors",
                        config.pipeline === "classical" && config.featureMethod === method.value
                          ? "border-status-active bg-status-active/10 text-white"
                          : "border-white/10 bg-[#151F36]/30 text-white/55 hover:border-white/30 hover:text-white/80",
                      )}
                    >
                      <input
                        checked={config.pipeline === "classical" && config.featureMethod === method.value}
                        onChange={() => selectClassicalMethod(method.value)}
                        className="form-radio text-status-active bg-[#080B14] border-white/30 w-4 h-4"
                        name="extractor"
                        type="radio"
                      />
                      <span className="ml-3 font-data-mono text-data-mono">{method.label}</span>
                      <span className="ml-auto font-data-mono text-[10px] text-white/35">{method.detail}</span>
                    </label>
                  ))}
                </div>
                <h3 className="font-data-mono text-[10px] text-white/50 mt-3 mb-2">FEATURE MATCHING</h3>
                <label className="flex cursor-pointer items-center border border-status-active/30 bg-[#151F36]/30 p-2 text-white/70">
                  <input checked readOnly className="form-radio text-status-active bg-[#080B14] border-white/30 w-4 h-4" name="matcher-classical" type="radio" />
                  <span className="ml-3 font-data-mono text-data-mono">NN + Lowe&apos;s Ratio</span>
                </label>
              </div>
            </div>
          </section>

          <section className="glass-panel relative flex flex-col space-y-7 overflow-hidden p-4">
            <h2 className="font-label-caps text-label-caps z-10 border-b border-border pb-2 text-slate-300">
              HYPERPARAMETERS
            </h2>
            <SliderControl
              label="MAX FEATURES"
              value={config.maxFeatures}
              min={500}
              max={8000}
              step={250}
              display={config.maxFeatures.toLocaleString()}
              onChange={(value) => update("maxFeatures", value)}
            />
            <SliderControl
              label="MATCH CONFIDENCE THRESH"
              value={config.matcherRatio}
              min={0.5}
              max={0.95}
              step={0.01}
              display={config.matcherRatio.toFixed(2)}
              onChange={(value) => update("matcherRatio", value)}
            />
            <SliderControl
              label="MINIMUM VERIFIED MATCHES"
              value={config.minMatches}
              min={4}
              max={80}
              step={1}
              display={String(config.minMatches)}
              onChange={(value) => update("minMatches", value)}
            />
            <div className="z-10 mt-auto border-t border-border pt-4">
              <label className="flex items-center gap-3 p-2 transition-colors hover:bg-bg-surface">
                <input
                  checked={config.pipeline === "neural"}
                  onChange={(event) => update("pipeline", event.target.checked ? "neural" : "classical")}
                  className="form-checkbox h-5 w-5 rounded-none border-status-active bg-[#080B14] text-status-active shadow-none"
                  type="checkbox"
                />
                <span className="font-data-mono text-data-mono text-white">ENABLE SUPERPOINT + LIGHTGLUE HIGH-COMPUTE PATH</span>
              </label>
            </div>
          </section>
        </div>

        <section className="glass-panel min-h-[190px] flex-1 p-4">
          <h2 className="font-label-caps text-label-caps mb-4 border-b border-border pb-2 text-slate-300">
            RUNTIME BENCHMARK ESTIMATE
          </h2>
          <div className="grid grid-cols-2 gap-4">
            <BenchmarkCard
              label="TARGET: RASPBERRY PI 5"
              fps={estimate.piFps}
              latencyMs={estimate.piLatencyMs}
              load={estimate.piLoad}
              warning={config.pipeline === "neural"}
            />
            <BenchmarkCard
              label="TARGET: DESKTOP GPU / HIGH COMPUTE"
              fps={estimate.gpuFps}
              latencyMs={estimate.gpuLatencyMs}
              load={estimate.gpuLoad}
            />
          </div>
        </section>

        <section className="glass-panel p-4">
          <div className="mb-4 flex items-center justify-between border-b border-border pb-2">
            <h2 className="font-label-caps text-label-caps text-slate-300">
              CAMERA CALIBRATION READINESS
            </h2>
            <span className={cn(
              "inline-flex items-center gap-1 border px-2 py-0.5 font-data-mono text-[10px]",
              calibrationState === "verified"
                ? "border-status-ready/30 bg-emerald-500/10 text-status-ready"
                : calibrationState === "captured"
                  ? "border-status-active/30 bg-cyan-500/10 text-status-active"
                  : "border-status-warning/30 bg-yellow-500/10 text-status-warning",
            )}>
              {calibrationState === "verified" ? <CheckCircle2 size={11} /> : <AlertTriangle size={11} />}
              {calibrationState === "verified" ? "VERIFIED" : calibrationState === "captured" ? "CAPTURED" : "NEEDS CAPTURE"}
            </span>
          </div>

          <div className="grid gap-3 lg:grid-cols-3">
            <CalibrationCard
              title="Intrinsics"
              detail="Generate or import the camera matrix and distortion file used by terrain matching."
              status={calibrationState === "needed" ? "needed" : "ready"}
            />
            <CalibrationCard
              title="Mount / Extrinsics"
              detail="Record down-camera orientation, mount rigidity, focus, aperture, and vibration notes."
              status={calibrationState === "verified" ? "ready" : "review"}
            />
            <CalibrationCard
              title="Frame Health"
              detail="Capture a live frame from the Pi and confirm texture, exposure, and blur before flight."
              status={activeDevice ? "review" : "needed"}
            />
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <button onClick={() => updateCalibrationState("needed")} className="btn-secondary text-xs">
              Needs capture
            </button>
            <button onClick={() => updateCalibrationState("captured")} className="btn-secondary text-xs">
              Mark captured
            </button>
            <button onClick={() => updateCalibrationState("verified")} className="btn-secondary text-xs">
              <CheckCircle2 size={13} /> Mark verified
            </button>
            <button onClick={() => navigate("/devices")} className="btn-secondary ml-auto text-xs">
              <Camera size={13} /> Open camera capture
            </button>
          </div>
        </section>
      </main>

      <aside className="z-10 flex h-full w-[300px] flex-col border-l border-border bg-bg-surface/95">
        <div className="border-b border-border bg-bg-card p-4">
          <h3 className="font-label-caps text-label-caps text-slate-300">LIVE TELEMETRY FEED</h3>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          <div className="border border-border bg-bg-surface p-3 shadow-depth-inset">
            <div className="font-label-caps text-label-caps text-white/50 mb-1">VISION STATUS</div>
            <div className="flex items-center gap-3">
              <span className={cn("h-3 w-1.5", config.pipeline === "classical" ? "bg-status-ready text-status-ready" : "bg-status-warning text-status-warning")} />
              <span className="font-data-mono text-data-mono text-white tracking-widest">{estimate.label.toUpperCase()}</span>
            </div>
          </div>

          <div className="space-y-4 border border-border bg-bg-surface p-3 shadow-depth-inset">
            <Metric label="ACTIVE FEATURES" value={config.maxFeatures.toLocaleString()} tone="cyan" />
            <Metric label="MIN MATCHES" value={String(config.minMatches)} tone="green" />
            <Metric label="MATCH RATIO" value={config.matcherRatio.toFixed(2)} tone="white" />
            <Metric label="POSE CONFIDENCE" value={config.matcherRatio >= 0.7 ? "HIGH" : "REVIEW"} tone={config.matcherRatio >= 0.7 ? "green" : "amber"} />
          </div>

          <div className="relative overflow-hidden border border-border p-1 shadow-depth-raised">
            <span className="absolute left-2 top-2 z-10 border border-status-active/30 bg-[#080B14]/90 px-2 py-0.5 font-label-caps text-[8px] text-status-active">
              CAM_0: MATCHES
            </span>
            <div className="relative h-40 overflow-hidden bg-[#020617] opacity-90">
              <div className="absolute inset-0 opacity-20" style={{ backgroundImage: "linear-gradient(rgba(104,199,230,0.12) 1px, transparent 1px), linear-gradient(90deg, rgba(104,199,230,0.12) 1px, transparent 1px)", backgroundSize: "18px 18px" }} />
              {Array.from({ length: 18 }).map((_, index) => (
                <span
                  // deterministic visual scatter for the status pane
                  key={index}
                  className={cn("absolute h-1.5 w-1.5", index % 3 === 0 ? "bg-status-ready" : "bg-status-active")}
                  style={{ left: `${8 + ((index * 37) % 84)}%`, top: `${12 + ((index * 23) % 72)}%` }}
                />
              ))}
            </div>
          </div>

          <div>
            <div className="mb-2 flex items-center gap-2 text-xs text-slate-400">
              <Settings2 size={13} className="text-status-active" /> Weight Paths
            </div>
            <WeightPicker label="SuperPoint" value={config.superpointPath} onPick={() => pickWeights("superpointPath")} />
            <WeightPicker label="LightGlue" value={config.lightgluePath} onPick={() => pickWeights("lightgluePath")} />
          </div>

          <div className="relative h-32 overflow-y-auto border border-border bg-bg-surface p-3 font-data-mono text-[10px] leading-relaxed text-white/50 shadow-depth-inset">
            <div className="absolute left-0 top-0 h-full w-1 bg-status-active/60" />
            <div className="pl-2 space-y-1">
              <LogLine label="PIPE" value={`${config.pipeline} / ${config.featureMethod.toUpperCase()}`} />
              <LogLine label="MAP" value={`${downloadedMapCount} cached map sources available`} />
              <LogLine label="TARGET" value={activeDevice?.name ?? "No active device"} />
              <LogLine label="SAVE" value={saved ? `Applied ${appliedAt ?? ""}` : "Pending apply"} tone={saved ? "ready" : "warning"} />
            </div>
          </div>

          <div className="space-y-2">
            {Object.entries(DOWNLOAD_URLS).map(([key, url]) => (
              <a
                key={key}
                href={url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-2 border border-border bg-bg-card px-3 py-2 text-xs text-slate-300 hover:border-status-active/50"
              >
                <Download size={13} className="text-status-active" />
                <span className="capitalize">{key}</span>
                <span className="ml-auto max-w-28 truncate font-mono text-[10px] text-slate-500">{url.split("/").pop()}</span>
              </a>
            ))}
          </div>
        </div>
      </aside>
    </div>
  );
}

function CalibrationCard({
  title,
  detail,
  status,
}: {
  title: string;
  detail: string;
  status: "ready" | "review" | "needed";
}) {
  const tone = status === "ready" ? "text-status-ready" : status === "review" ? "text-status-active" : "text-status-warning";
  return (
    <div className="border border-border bg-bg-surface p-3 shadow-depth-inset">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="font-data-mono text-sm font-semibold text-slate-100">{title}</div>
        <span className={cn("font-data-mono text-[10px] uppercase", tone)}>
          {status === "ready" ? "ready" : status === "review" ? "review" : "needed"}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-slate-500">{detail}</p>
    </div>
  );
}

function SliderControl({
  label,
  value,
  min,
  max,
  step,
  display,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  display: string;
  onChange: (value: number) => void;
}) {
  return (
    <div className="z-10">
      <div className="flex justify-between mb-2">
        <span className="font-label-caps text-label-caps text-white/80">{label}</span>
        <span className="font-data-mono text-data-mono text-status-active">{display}</span>
      </div>
      <input
        max={max}
        min={min}
        step={step}
        type="range"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full"
      />
    </div>
  );
}

function BenchmarkCard({
  label,
  fps,
  latencyMs,
  load,
  warning = false,
}: {
  label: string;
  fps: number;
  latencyMs: number;
  load: number;
  warning?: boolean;
}) {
  return (
    <div className={cn(
      "relative flex flex-col overflow-hidden border bg-bg-surface p-4 shadow-depth-inset",
      warning ? "border-status-critical/25" : "border-status-active/20",
    )}>
      <span className={cn("font-data-mono text-data-mono mb-2 z-10", warning ? "text-status-critical/80" : "text-status-active")}>
        {label}
      </span>
      <div className="flex items-end justify-between mt-auto mb-4 z-10">
        <div>
          <div className={cn("font-label-caps text-label-caps", warning ? "text-status-critical/80" : "text-status-ready/80")}>EST. FPS</div>
          <div className={cn("font-mono text-3xl font-bold", warning ? "text-status-critical" : "text-status-ready")}>
            {fps.toFixed(1)}<span className="text-sm ml-1">Hz</span>
          </div>
        </div>
        <div className="text-right">
          <div className={cn("font-label-caps text-label-caps", warning ? "text-status-critical/80" : "text-status-ready/80")}>LATENCY</div>
          <div className={cn("font-mono text-3xl font-bold", warning ? "text-status-critical" : "text-status-ready")}>
            {latencyMs}<span className="text-sm ml-1">ms</span>
          </div>
        </div>
      </div>
      <div className="z-10 h-3 w-full overflow-hidden bg-[#0D1322] shadow-depth-inset">
        <div
          className={cn("bar-3d h-full transition-all duration-300", warning ? "bg-status-critical text-status-critical" : "bg-status-ready text-status-ready")}
          style={{ width: `${Math.min(100, Math.max(4, load))}%` }}
        />
      </div>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone: "cyan" | "green" | "amber" | "white" }) {
  const toneClass = tone === "cyan" ? "text-status-active" : tone === "green" ? "text-status-ready" : tone === "amber" ? "text-status-warning" : "text-white";
  return (
    <div className="flex justify-between border-b border-white/5 pb-2 last:border-b-0 last:pb-0">
      <span className="font-label-caps text-label-caps text-white/60">{label}</span>
      <span className={cn("font-data-mono text-data-mono", toneClass)}>{value}</span>
    </div>
  );
}

function WeightPicker({ label, value, onPick }: { label: string; value: string; onPick: () => void }) {
  return (
    <div className="mb-2">
      <div className="font-label-caps text-label-caps text-white/50 mb-1">{label}</div>
      <div className="flex gap-2">
        <input className="input-field flex-1 text-[10px] font-mono" value={value} readOnly />
        <button onClick={onPick} className="btn-secondary px-2">
          <FolderOpen size={13} />
        </button>
      </div>
    </div>
  );
}

function LogLine({ label, value, tone = "normal" }: { label: string; value: string; tone?: "normal" | "ready" | "warning" }) {
  const toneClass = tone === "ready" ? "text-status-ready" : tone === "warning" ? "text-status-warning" : "text-status-active/70";
  return (
    <div>
      <span className="text-white/30">[{label}]</span> <span className={toneClass}>{value}</span>
    </div>
  );
}
