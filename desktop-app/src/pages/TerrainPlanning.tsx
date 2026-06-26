import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Gauge, Mountain, Save, SplitSquareHorizontal } from "lucide-react";
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
import { loadTerrainConstraints, saveTerrainConstraints } from "../lib/terrainPlanning";
import type { TerrainPlanningConstraints } from "../lib/terrainPlanning";

function constraintStatus(value: number, target: string, ready: boolean) {
  return { target, ready, value };
}

export function TerrainPlanning() {
  const { regions } = useAppStore();
  const availableRegions = regions.filter((region) => region.last_downloaded || region.source === "uploaded" || region.source === "folder");
  const [selectedRegionId, setSelectedRegionId] = useState(() => availableRegions[0]?.id ?? regions[0]?.id ?? "");
  const selectedRegion = regions.find((region) => region.id === selectedRegionId) ?? availableRegions[0] ?? regions[0];
  const [constraints, setConstraints] = useState<TerrainPlanningConstraints>(() => loadTerrainConstraints());
  const [saved, setSaved] = useState(false);
  const pipeline = useMemo(() => loadPipelineConfig(), []);

  const gsd = selectedRegion?.gsd_m_per_px ?? null;
  const aglToGsd = gsd ? constraints.min_agl_m / gsd : null;
  const georefConfidence = selectedRegion?.georef_confidence ?? 0;
  const checks = [
    constraintStatus(constraints.min_agl_m, ">= 20 m", constraints.min_agl_m >= 20),
    constraintStatus(constraints.max_terrain_relief_m, "<= 40 m", constraints.max_terrain_relief_m <= 40),
    constraintStatus(constraints.min_agl_to_gsd_ratio, ">= 40x", constraints.min_agl_to_gsd_ratio >= 40),
    constraintStatus(constraints.max_route_segment_m, "<= 500 m", constraints.max_route_segment_m <= 500),
  ];

  const update = (key: keyof TerrainPlanningConstraints, value: number) => {
    setConstraints((current) => {
      const next = {
        ...current,
        [key]: Number.isFinite(value) && value >= 0 ? value : current[key],
      };
      saveTerrainConstraints(next);
      return next;
    });
    setSaved(false);
  };

  const save = () => {
    saveTerrainConstraints(constraints);
    setSaved(true);
  };

  return (
    <DefensePane
      right={
        <DefenseRightPanel title="TERRAIN CONSTRAINTS">
          <div className="flex justify-center border-b border-white/10 pb-4">
            <div className="holo-core" />
          </div>
          <div>
            <label className="label">Map source</label>
            <select className="input-field" value={selectedRegion?.id ?? ""} onChange={(event) => setSelectedRegionId(event.target.value)}>
              {regions.length === 0 && <option value="">No maps available</option>}
              {regions.map((region) => <option key={region.id} value={region.id}>{region.name}</option>)}
            </select>
          </div>
          <NumberControl label="Min AGL m" value={constraints.min_agl_m} onChange={(value) => update("min_agl_m", value)} />
          <NumberControl label="Max relief m" value={constraints.max_terrain_relief_m} onChange={(value) => update("max_terrain_relief_m", value)} />
          <NumberControl label="Min AGL/GSD" value={constraints.min_agl_to_gsd_ratio} onChange={(value) => update("min_agl_to_gsd_ratio", value)} />
          <NumberControl label="Max segment m" value={constraints.max_route_segment_m} onChange={(value) => update("max_route_segment_m", value)} />
          <button onClick={save} className="w-full rounded border border-status-active bg-status-active/10 px-3 py-2 font-label-caps text-label-caps text-status-active">
            <Save size={14} className="inline mr-2" /> {saved ? "SAVED" : "SAVE DEFAULTS"}
          </button>
        </DefenseRightPanel>
      }
    >
      <DefenseHeader
        eyebrow="TERRAIN PLANNING"
        title="AGL, GSD, RELIEF, ROUTE RISK"
        subtitle="constraints shared with Mission Planner bundle exports"
      />

      <div className="grid grid-cols-4 gap-3">
        <DefenseMetric label="SELECTED MAP" value={selectedRegion?.name ?? "NO MAP"} detail={selectedRegion?.source ?? "none"} tone={selectedRegion ? "ready" : "warning"} />
        <DefenseMetric label="GSD" value={gsd ? gsd.toFixed(2) : "n/a"} detail="meters per pixel" tone={gsd ? "active" : "warning"} />
        <DefenseMetric label="AGL/GSD" value={aglToGsd ? `${aglToGsd.toFixed(0)}x` : "n/a"} detail={`target ${constraints.min_agl_to_gsd_ratio}x`} tone={aglToGsd && aglToGsd >= constraints.min_agl_to_gsd_ratio ? "ready" : "warning"} />
        <DefenseMetric label="GEOREF" value={`${Math.round(georefConfidence * 100)}%`} detail={selectedRegion?.georef_crs ?? "unknown CRS"} tone={georefConfidence >= 0.7 ? "ready" : "warning"} />
      </div>

      <div className="grid grid-cols-[1fr_360px] gap-4">
        <DefenseSection title="TERRAIN PROFILE PREVIEW" icon={<Mountain size={14} />} className="relative min-h-[460px] overflow-hidden">
          <div className="absolute inset-0 opacity-20" style={{
            backgroundImage: "linear-gradient(to right, #1e293b 1px, transparent 1px), linear-gradient(to bottom, #1e293b 1px, transparent 1px)",
            backgroundSize: "40px 40px",
          }} />
          <svg className="absolute inset-0 h-full w-full" preserveAspectRatio="none" viewBox="0 0 1000 520">
            <path d="M0 430 C 160 360, 220 455, 350 330 S 560 260, 700 305 S 850 410, 1000 285 L1000 520 L0 520 Z" fill="#111827" stroke="#334155" strokeWidth="2" />
            <path d="M0 300 C 160 250, 240 325, 360 220 S 560 155, 720 205 S 870 315, 1000 195" fill="none" stroke="#00E5FF" strokeWidth="3" strokeDasharray="8 10" opacity="0.85" />
            <path d="M120 276 L260 256 L405 215 L540 185 L700 205 L850 255" fill="none" stroke="#22C55E" strokeWidth="2" opacity="0.7" />
            <circle cx="120" cy="276" r="8" fill="#00E5FF" />
            <circle cx="405" cy="215" r="8" fill="#F59E0B" />
            <circle cx="700" cy="205" r="8" fill="#00E5FF" />
            <circle cx="850" cy="255" r="8" fill="#EF4444" />
          </svg>
          <div className="absolute bottom-4 left-4 right-4 grid grid-cols-4 gap-3">
            <OverlayMetric label="Map" value={selectedRegion?.name ?? "No map"} />
            <OverlayMetric label="Min AGL" value={`${constraints.min_agl_m} m`} />
            <OverlayMetric label="Relief cap" value={`${constraints.max_terrain_relief_m} m`} />
            <OverlayMetric label="Segment cap" value={`${constraints.max_route_segment_m} m`} />
          </div>
        </DefenseSection>

        <DefenseSection title="RISK CHECKS" icon={<SplitSquareHorizontal size={14} />}>
          <div className="space-y-2">
            {checks.map((check) => (
              <DefenseListItem
                key={check.target}
                label={check.target}
                detail={check.value}
                tone={check.ready ? "ready" : "warning"}
                action={check.ready ? <CheckCircle2 size={14} className="text-status-ready" /> : <AlertTriangle size={14} className="text-status-warning" />}
              />
            ))}
          </div>
          <div className="mt-4 rounded-lg border border-status-active/20 bg-black/30 p-3">
            <div className="font-label-caps text-label-caps text-secondary">Runtime profile</div>
            <div className="mt-1 font-data-mono text-sm text-white">{pipeline.pipeline} / {pipeline.featureMethod.toUpperCase()}</div>
            <p className="mt-2 font-data-mono text-[10px] text-white/40">
              These defaults are included in new mission bundles and Mission Planner terrain metadata.
            </p>
          </div>
        </DefenseSection>
      </div>

      <DefenseSection title="ROUTE SEGMENTATION RULES" icon={<Gauge size={14} />}>
        <div className="grid grid-cols-4 gap-3">
          <DefenseMetric label="MIN AGL" value={`${constraints.min_agl_m} m`} tone={constraints.min_agl_m >= 20 ? "ready" : "warning"} />
          <DefenseMetric label="RELIEF MAX" value={`${constraints.max_terrain_relief_m} m`} tone={constraints.max_terrain_relief_m <= 40 ? "ready" : "warning"} />
          <DefenseMetric label="AGL/GSD MIN" value={`${constraints.min_agl_to_gsd_ratio}x`} tone={constraints.min_agl_to_gsd_ratio >= 40 ? "ready" : "warning"} />
          <DefenseMetric label="SEGMENT MAX" value={`${constraints.max_route_segment_m} m`} tone={constraints.max_route_segment_m <= 500 ? "ready" : "warning"} />
        </div>
      </DefenseSection>
    </DefensePane>
  );
}

function NumberControl({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <div>
      <label className="label">{label}</label>
      <input className="input-field" type="number" min={0} value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </div>
  );
}

function OverlayMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="ops-map-overlay rounded-lg p-3">
      <div className="font-label-caps text-label-caps text-white/45">{label}</div>
      <div className="truncate font-data-mono text-sm font-bold text-status-active">{value}</div>
    </div>
  );
}
