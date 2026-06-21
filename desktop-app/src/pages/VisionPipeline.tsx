import { useEffect, useState } from "react";
import { Cpu, Download, FolderOpen, Save, Settings2, SlidersHorizontal } from "lucide-react";
import { open } from "@tauri-apps/plugin-dialog";
import { cn } from "../lib/utils";
import { loadPipelineConfig, savePipelineConfig } from "../lib/pipelineConfig";
import type { PipelineConfig } from "../lib/pipelineConfig";
import type { FeatureMethod } from "../lib/types";

const DOWNLOAD_URLS = {
  superpoint: "https://github.com/cvg/LightGlue/releases/download/v0.1_arxiv/superpoint_v1.pth",
  lightglue: "https://github.com/cvg/LightGlue/releases/download/v0.1_arxiv/lightglue_v0.1_disk.pth",
};
const FEATURE_METHODS: { value: FeatureMethod; label: string }[] = [
  { value: "orb", label: "ORB" },
  { value: "akaze", label: "AKAZE" },
  { value: "sift", label: "SIFT" },
];

export function VisionPipelinePage() {
  const [config, setConfig] = useState<PipelineConfig>(() => loadPipelineConfig());
  const [saved, setSaved] = useState(false);

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
  };

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="section-title">Vision Pipeline</h1>
          <p className="text-slate-400 text-sm mt-1">
            Configure the default feature pipeline used when building mission bundles.
          </p>
        </div>
        <button onClick={save} className="btn-primary">
          <Save size={15} /> Save Pipeline
        </button>
      </div>

      <div className="grid grid-cols-[1fr_0.85fr] gap-6">
        <div className="space-y-4">
          <div className="card space-y-4">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <SlidersHorizontal size={15} className="text-cyan-400" /> Pipeline Mode
            </h3>
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={() => update("pipeline", "classical")}
                className={cn(
                  "rounded-lg border p-4 text-left transition-colors",
                  config.pipeline === "classical" ? "border-emerald-500/40 bg-emerald-500/5" : "border-border",
                )}
              >
                <div className="text-sm font-medium text-slate-200">Classical CPU</div>
                <div className="text-xs text-slate-500 mt-1">Best default for Raspberry Pi 5 and low-cost compute.</div>
              </button>
              <button
                onClick={() => update("pipeline", "neural")}
                className={cn(
                  "rounded-lg border p-4 text-left transition-colors",
                  config.pipeline === "neural" ? "border-violet-500/40 bg-violet-500/5" : "border-border",
                )}
              >
                <div className="text-sm font-medium text-slate-200">SuperPoint + LightGlue</div>
                <div className="text-xs text-slate-500 mt-1">Optional high-compute path for GPU-class devices.</div>
              </button>
            </div>
          </div>

          <div className="card space-y-4">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <Cpu size={15} className="text-cyan-400" /> Classical Feature Settings
            </h3>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="label">Feature method</label>
                <div className="grid grid-cols-3 gap-1 rounded-lg border border-border bg-bg-surface p-1">
                  {FEATURE_METHODS.map((method) => (
                    <button
                      key={method.value}
                      type="button"
                      onClick={() => update("featureMethod", method.value)}
                      className={cn(
                        "h-8 rounded-md text-xs font-medium transition-colors",
                        config.featureMethod === method.value
                          ? "bg-cyan-500/10 text-cyan-300 border border-cyan-500/30"
                          : "text-slate-400 hover:text-slate-200 hover:bg-bg-elevated border border-transparent",
                      )}
                    >
                      {method.label}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="label">Max features</label>
                <input
                  className="input-field"
                  type="number"
                  min={500}
                  step={500}
                  value={config.maxFeatures}
                  onChange={(e) => update("maxFeatures", Number(e.target.value))}
                />
              </div>
              <div>
                <label className="label">Min matches</label>
                <input
                  className="input-field"
                  type="number"
                  min={4}
                  value={config.minMatches}
                  onChange={(e) => update("minMatches", Number(e.target.value))}
                />
              </div>
            </div>
            <div>
              <label className="label flex items-center justify-between">
                <span>Matcher ratio</span>
                <span className="text-cyan-400 font-mono">{config.matcherRatio.toFixed(2)}</span>
              </label>
              <input
                type="range"
                min={0.5}
                max={0.95}
                step={0.01}
                value={config.matcherRatio}
                onChange={(e) => update("matcherRatio", Number(e.target.value))}
                className="w-full"
              />
            </div>
          </div>

          <div className="card space-y-4">
            <h3 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <Settings2 size={15} className="text-cyan-400" /> Neural Model Weights
            </h3>
            <div>
              <label className="label">SuperPoint weights</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 text-xs font-mono" value={config.superpointPath} readOnly />
                <button onClick={() => pickWeights("superpointPath")} className="btn-secondary px-3">
                  <FolderOpen size={14} />
                </button>
              </div>
            </div>
            <div>
              <label className="label">LightGlue weights</label>
              <div className="flex gap-2">
                <input className="input-field flex-1 text-xs font-mono" value={config.lightgluePath} readOnly />
                <button onClick={() => pickWeights("lightgluePath")} className="btn-secondary px-3">
                  <FolderOpen size={14} />
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="space-y-4">
          <div className="card space-y-3 border-emerald-500/20 bg-emerald-500/5">
            <h3 className="text-sm font-medium text-slate-200">Recommended Starter Profile</h3>
            <div className="text-xs text-slate-400 space-y-2">
              <p>Use Classical CPU with ORB for Raspberry Pi 5 and early field validation.</p>
              <p>Switch to SuperPoint + LightGlue only after the map/camera loop is stable and the compute target can sustain it.</p>
            </div>
          </div>

          <div className="card space-y-3">
            <div className="flex items-center gap-2">
              <Download size={15} className="text-cyan-400" />
              <span className="text-sm font-medium text-slate-200">Official Weight Downloads</span>
            </div>
            <div className="space-y-2">
              {Object.entries(DOWNLOAD_URLS).map(([key, url]) => (
                <a
                  key={key}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="block rounded-lg border border-border bg-bg-card p-3 hover:border-border-strong transition-colors"
                >
                  <div className="text-xs font-medium text-slate-300 capitalize">{key}</div>
                  <div className="text-[10px] font-mono text-slate-500 truncate mt-1">{url.split("/").pop()}</div>
                </a>
              ))}
            </div>
          </div>

          <div className="card space-y-2">
            <h3 className="text-sm font-medium text-slate-200">Current Defaults</h3>
            <div className="text-xs text-slate-400 space-y-1">
              <div>Mode: <span className="text-slate-200">{config.pipeline === "classical" ? "Classical CPU" : "SuperPoint + LightGlue"}</span></div>
              <div>Feature method: <span className="text-slate-200">{config.featureMethod.toUpperCase()}</span></div>
              <div>Max features: <span className="text-slate-200">{config.maxFeatures.toLocaleString()}</span></div>
              <div>Min matches: <span className="text-slate-200">{config.minMatches}</span></div>
            </div>
            {saved && <div className="text-xs text-emerald-400 pt-2">Pipeline configuration saved.</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
