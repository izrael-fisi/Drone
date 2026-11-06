import { CloudSun, Loader2, MousePointerClick } from "lucide-react";
import { useEffect } from "react";
import { usePlatformMapStore } from "../../platform/mapStore";
import type { ProductMapOverlayProps } from "../../platform/types";
import { ACCENT, PANEL_BRIEFING } from "./constants";
import { formatPoint, LevelBadge } from "./shared";
import { useAirspaceStore, useAssessment } from "./store";

/** Airspace & Weather HUD on the shared map: go/no-go and key numbers for the briefing point. */
export function AirspaceMapOverlay({ ctx }: ProductMapOverlayProps) {
  const point = useAirspaceStore((state) => state.point);
  const weather = useAirspaceStore((state) => state.weather.data?.now);
  const assessment = useAssessment();
  const setCursor = usePlatformMapStore((state) => state.setCursor);

  // Every map click briefs a location while this product is active.
  useEffect(() => {
    setCursor("crosshair");
    return () => setCursor(null);
  }, [setCursor]);

  const airspace = assessment.checks.find((check) => check.id === "airspace");
  const laanc = assessment.checks.find((check) => check.id === "laanc");

  return (
    <section
      className="pointer-events-auto w-[300px] rounded-lg border bg-bg-base/92 p-3 shadow-2xl backdrop-blur-sm"
      style={{ borderColor: `${ACCENT}59` }}
    >
      <div className="flex items-center gap-2">
        <CloudSun size={14} style={{ color: ACCENT }} />
        <span className="font-data-mono text-[10px] font-bold uppercase tracking-[0.14em]" style={{ color: ACCENT }}>
          Airspace & Weather
        </span>
        <span className="ml-auto">
          {assessment.pending ? <Loader2 size={13} className="animate-spin text-slate-400" /> : point && <LevelBadge level={assessment.overall} />}
        </span>
      </div>

      {!point ? (
        <div className="mt-2 flex items-center gap-2 text-xs text-slate-400">
          <MousePointerClick size={13} className="shrink-0" />
          Click the map to brief a location.
        </div>
      ) : (
        <button type="button" onClick={() => ctx.openPanel(PANEL_BRIEFING)} className="mt-2 block w-full text-left">
          <div className="font-data-mono text-[10px] text-slate-500">{formatPoint(point)}</div>
          <div className="mt-2 grid grid-cols-3 gap-2">
            <Metric label="Wind" value={weather ? `${weather.windMs.toFixed(1)}` : "-"} unit="m/s" />
            <Metric label="Gust" value={weather ? `${weather.gustMs.toFixed(1)}` : "-"} unit="m/s" />
            <Metric label="Vis" value={weather ? `${Math.min(99, weather.visibilityM / 1000).toFixed(0)}` : "-"} unit="km" />
          </div>
          {(airspace || laanc) && (
            <div className="mt-2 truncate text-[11px] text-slate-400">
              {[airspace?.value, laanc && `LAANC ${laanc.value}`].filter(Boolean).join(" · ")}
            </div>
          )}
        </button>
      )}
    </section>
  );
}

function Metric({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <div className="rounded border border-white/10 bg-white/[0.03] px-2 py-1">
      <div className="text-[9px] uppercase tracking-[0.08em] text-slate-500">{label}</div>
      <div className="font-data-mono text-sm text-slate-100">
        {value}
        <span className="ml-0.5 text-[10px] text-slate-500">{unit}</span>
      </div>
    </div>
  );
}
