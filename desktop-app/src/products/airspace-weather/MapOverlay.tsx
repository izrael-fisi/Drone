import { CloudSunIcon as CloudSun, CircleNotchIcon as Loader2, CursorClickIcon as MousePointerClick } from "@phosphor-icons/react";
import { useEffect } from "react";
import { cn } from "../../lib/utils";
import { usePlatformMapStore } from "../../platform/mapStore";
import type { ProductMapOverlayProps } from "../../platform/types";
import { ACCENT, PANEL_BRIEFING } from "./constants";
import { formatPoint, LEVEL_COLORS } from "./shared";
import { levelLabel, useAirspaceStore, useAssessment } from "./store";

/** Airspace & Weather HUD on the shared map: go/no-go and key numbers for the briefing point. */
export function AirspaceMapOverlay({ ctx, leftInsetClass }: ProductMapOverlayProps) {
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

  const verdictColor = LEVEL_COLORS[assessment.overall];

  return (
    <section
      className={cn("absolute top-3 z-[1100] w-[288px] rounded-xl border border-white/10 bg-bg-base/75 p-3.5 shadow-[0_12px_40px_rgba(0,0,0,0.45)] backdrop-blur-xl transition-[left] duration-200", leftInsetClass)}
    >
      <div className="flex items-center gap-2">
        <CloudSun size={16} weight="duotone" style={{ color: ACCENT }} />
        <span className="text-[13px] font-semibold text-slate-100">Airspace & Weather</span>
        {point && (
          <span className="ml-auto text-[13px] font-semibold" style={{ color: assessment.pending ? undefined : verdictColor }}>
            {assessment.pending ? <Loader2 size={15} className="animate-spin text-slate-400" /> : levelLabel(assessment.overall)}
          </span>
        )}
      </div>

      {!point ? (
        <p className="mt-2 flex items-center gap-2 text-[13px] text-slate-400">
          <MousePointerClick size={15} className="shrink-0" />
          Click the map to brief a location.
        </p>
      ) : (
        <button type="button" onClick={() => ctx.openPanel(PANEL_BRIEFING)} className="mt-1 block w-full rounded-md text-left">
          <div className="text-xs tabular-nums text-slate-500">{formatPoint(point)}</div>
          <div className="mt-3 grid grid-cols-3 gap-3">
            <Metric label="Wind" value={weather ? weather.windMs.toFixed(1) : "–"} unit="m/s" />
            <Metric label="Gust" value={weather ? weather.gustMs.toFixed(1) : "–"} unit="m/s" />
            <Metric label="Visibility" value={weather ? Math.min(99, weather.visibilityM / 1000).toFixed(0) : "–"} unit="km" />
          </div>
          {(airspace || laanc) && (
            <div className="mt-3 truncate border-t border-white/[0.08] pt-2 text-xs text-slate-400">
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
    <div>
      <div className="text-xs text-slate-400">{label}</div>
      <div className="text-lg font-semibold leading-tight tabular-nums text-slate-100">
        {value}
        <span className="ml-1 text-xs font-normal text-slate-500">{unit}</span>
      </div>
    </div>
  );
}
