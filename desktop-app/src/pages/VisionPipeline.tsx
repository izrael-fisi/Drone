import { type ReactNode, useEffect, useState } from "react";
import { CheckCircle2, Cpu, Database, Map, Save, ShieldCheck } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { loadPipelineConfig, savePipelineConfig, type PipelineConfig } from "../lib/pipelineConfig";
import { useAppStore } from "../lib/store";

export function VisionPipelinePage() {
  const navigate = useNavigate();
  const { activeDeviceId, devices, regions } = useAppStore();
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const activeMap = regions.find((region) => region.lifecycle_state === "active") ?? regions.find((region) => region.active_bundle_state === "active");
  const [config, setConfig] = useState<PipelineConfig>(() => loadPipelineConfig());
  const [saved, setSaved] = useState(false);

  useEffect(() => setSaved(false), [config]);

  const update = <K extends keyof PipelineConfig>(key: K, value: PipelineConfig[K]) => {
    setConfig((current) => ({ ...current, [key]: value }));
  };

  const apply = () => {
    savePipelineConfig(config);
    setSaved(true);
  };

  const readiness = [
    { label: "Model release", value: config.modelReleaseId, ok: !config.modelReleaseId.endsWith("unassigned") },
    { label: "Map release", value: activeMap?.name ?? config.mapReleaseId, ok: Boolean(activeMap ?? !config.mapReleaseId.endsWith("unassigned")) },
    { label: "Calibration", value: config.calibrationId, ok: config.calibrationId.length > 0 },
    { label: "Runtime target", value: activeDevice?.name ?? "No device selected", ok: Boolean(activeDevice) },
  ];

  return (
    <div className="ops-screen-bg relative h-full min-h-[calc(100vh-96px)] overflow-hidden animate-fade-in">
      <main className="relative z-10 mx-auto flex max-w-6xl flex-col gap-4 p-4">
        <header className="flex items-end justify-between border-b border-border pb-3">
          <div>
            <h1 className="font-headline-lg text-headline-lg font-semibold text-slate-100">VISION NAVIGATION</h1>
            <p className="font-data-mono text-data-mono text-slate-500">LEARNED CROSS-VIEW SHADOW PROFILE</p>
          </div>
          <button onClick={apply} className="btn-primary gap-2"><Save size={14} /> {saved ? "SAVED" : "SAVE PROFILE"}</button>
        </header>

        <section className="glass-panel grid gap-px border-border bg-border md:grid-cols-3">
          <ProfileCell icon={<Map size={16} />} label="GLOBAL SEARCH" value="MegaLoc" detail="Bounded tile retrieval" />
          <ProfileCell icon={<Database size={16} />} label="LOCAL EVIDENCE" value="XFeat + LighterGlue" detail="Learned correspondence" />
          <ProfileCell icon={<Cpu size={16} />} label="TEMPORAL MODEL" value="Cross-view student" detail="BEV, bearing, hypotheses" />
        </section>

        <section className="grid gap-4 lg:grid-cols-[1.25fr_.75fr]">
          <div className="glass-panel space-y-4 p-4">
            <div className="flex items-center justify-between border-b border-border pb-3">
              <div>
                <h2 className="font-label-caps text-label-caps text-slate-300">ACTIVE PROFILE</h2>
                <p className="mt-1 text-sm text-slate-400">The vehicle has one approved learned navigation profile.</p>
              </div>
              <span className="border border-status-active/40 bg-status-active/10 px-2 py-1 font-data-mono text-[10px] text-status-active">SHADOW ONLY</span>
            </div>
            <InfoRow label="Profile" value="pi5_crossview_nav_v1" />
            <InfoRow label="Geometry" value="3D PnP primary, restricted homography secondary" />
            <InfoRow label="PX4 output" value="Disabled until shadow validation is promoted" />
            <label className="block space-y-1.5">
              <span className="font-label-caps text-label-caps text-slate-500">MODEL RELEASE ID</span>
              <input value={config.modelReleaseId} onChange={(event) => update("modelReleaseId", event.target.value)} className="input-field w-full font-mono text-xs" />
            </label>
            <label className="block space-y-1.5">
              <span className="font-label-caps text-label-caps text-slate-500">CALIBRATION ID</span>
              <input value={config.calibrationId} onChange={(event) => update("calibrationId", event.target.value)} className="input-field w-full font-mono text-xs" />
            </label>
            <label className="block space-y-1.5">
              <span className="font-label-caps text-label-caps text-slate-500">INFERENCE PROVIDER</span>
              <select value={config.inferenceProvider} onChange={(event) => update("inferenceProvider", event.target.value as PipelineConfig["inferenceProvider"])} className="input-field w-full font-mono text-xs">
                <option value="onnxruntime_cpu">ONNX Runtime CPU</option>
                <option value="hailo_ai_hat_optional">Hailo AI HAT 2 (validate first)</option>
              </select>
            </label>
          </div>

          <aside className="glass-panel space-y-3 p-4">
            <div className="flex items-center gap-2 border-b border-border pb-3"><ShieldCheck size={16} className="text-cyan-400" /><h2 className="font-label-caps text-label-caps text-slate-300">RELEASE READINESS</h2></div>
            {readiness.map((item) => (
              <div key={item.label} className="border border-border bg-bg-surface px-3 py-2.5">
                <div className="flex items-center justify-between gap-2"><span className="text-xs text-slate-400">{item.label}</span><CheckCircle2 size={14} className={item.ok ? "text-status-active" : "text-status-warning"} /></div>
                <div className="mt-1 truncate font-mono text-xs text-slate-200">{item.value}</div>
              </div>
            ))}
            <button onClick={() => navigate("/mission-bundle-builder")} className="btn-secondary w-full">OPEN BUNDLE BUILDER</button>
            <p className="text-xs leading-5 text-slate-500">Cloud teacher releases are recorded as provenance. They are not selectable vehicle algorithms.</p>
          </aside>
        </section>
      </main>
    </div>
  );
}

function ProfileCell({ icon, label, value, detail }: { icon: ReactNode; label: string; value: string; detail: string }) {
  return <div className="bg-bg-surface p-4"><div className="flex items-center gap-2 text-cyan-400">{icon}<span className="font-label-caps text-label-caps">{label}</span></div><div className="mt-3 text-sm font-medium text-slate-100">{value}</div><div className="mt-1 text-xs text-slate-500">{detail}</div></div>;
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return <div className="flex items-start justify-between gap-5 border-b border-border/60 py-2"><span className="font-label-caps text-label-caps text-slate-500">{label}</span><span className="max-w-[65%] text-right font-mono text-xs text-slate-200">{value}</span></div>;
}
