import { CrosshairIcon as Crosshair, GpsFixIcon as LocateFixed, CircleNotchIcon as Loader2, ArrowClockwiseIcon as RefreshCw, ArrowCounterClockwiseIcon as RotateCcw } from "@phosphor-icons/react";
import { cn } from "../../lib/utils";
import { usePlatformMapStore } from "../../platform/mapStore";
import type { PlatformContext } from "../../platform/types";
import { EmptyLine, PanelStack, SectionLabel } from "../../platform/ui";
import { ACCENT, ADVISORY_NOTE, AIRSPACE_CLASS_COLORS, FLIGHT_CATEGORY_COLORS, PANEL_BRIEFING, uasCeilingColor } from "./constants";
import { formatPoint, LEVEL_COLORS, useVpsCoverageAt } from "./shared";
import type { WeatherHour } from "./sources";
import { DEFAULT_LIMITS, useAirspaceStore, useAssessment, type CheckLevel, type FlightLimits } from "./store";

function BriefMapCenterButton() {
  const brief = useAirspaceStore((state) => state.brief);
  return (
    <button
      type="button"
      onClick={() => {
        const view = usePlatformMapStore.getState().api?.getView();
        if (view) brief(view.center);
      }}
      className="flex h-8 items-center gap-1.5 rounded-md px-2.5 text-[13px] font-medium transition-colors hover:bg-white/[0.06]"
      style={{ color: ACCENT }}
    >
      <Crosshair size={15} /> Brief map center
    </button>
  );
}

function SourceLine({ label, loading, error, count }: { label: string; loading: boolean; error: string | null; count?: number | null }) {
  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-slate-400">{label}</span>
      <span className={cn("truncate text-right tabular-nums", error ? "text-red-400" : "text-slate-500")} title={error ?? undefined}>
        {loading ? <Loader2 size={12} className="inline animate-spin" /> : error ? "Failed" : count != null ? count : "OK"}
      </span>
    </div>
  );
}

const VERDICT_TEXT: Record<CheckLevel, string> = {
  go: "Go",
  caution: "Caution",
  nogo: "No-go",
  info: "No verdict",
};

/** Wind forecast as bars against the gust limit: the briefing's one visual signature. */
function WindStrip({ hours, maxGustMs, maxWindMs }: { hours: WeatherHour[]; maxGustMs: number; maxWindMs: number }) {
  const scale = Math.max(maxGustMs * 1.25, ...hours.map((hour) => hour.gustMs));
  const limitTop = 100 - (maxGustMs / scale) * 100;
  return (
    <div>
      <div className="relative flex h-20 items-end gap-[3px]" role="img" aria-label="Wind and gust forecast for the next 12 hours">
        <div className="pointer-events-none absolute inset-x-0 border-t border-dashed border-red-400/50" style={{ top: `${limitTop}%` }} />
        {hours.map((hour) => {
          const gustPct = Math.min(100, (hour.gustMs / scale) * 100);
          const windPct = Math.min(100, (hour.windMs / scale) * 100);
          const color = hour.gustMs > maxGustMs || hour.windMs > maxWindMs
            ? "#EF4444"
            : hour.gustMs > maxGustMs * 0.8 || hour.windMs > maxWindMs * 0.8
              ? "#EAB308"
              : "#94A3B8";
          return (
            <div
              key={hour.time}
              className="relative flex-1 rounded-sm"
              style={{ height: `${Math.max(gustPct, 3)}%`, backgroundColor: `${color}40` }}
              title={`${hour.time.slice(11, 16)}  wind ${hour.windMs.toFixed(1)} m/s, gust ${hour.gustMs.toFixed(1)} m/s, rain ${hour.precipitationProbabilityPct ?? 0}%`}
            >
              <div className="absolute inset-x-0 bottom-0 rounded-sm" style={{ height: `${(windPct / Math.max(gustPct, 1)) * 100}%`, backgroundColor: color }} />
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex gap-[3px] text-[11px] tabular-nums text-slate-500">
        {hours.map((hour, index) => (
          <span key={hour.time} className="flex-1 text-center">{index % 3 === 0 ? hour.time.slice(11, 13) : ""}</span>
        ))}
      </div>
      <p className="mt-1.5 text-xs text-slate-500">
        Solid bar wind, light bar gusts, dashed line your {maxGustMs} m/s gust limit. Hours in local time.
      </p>
    </div>
  );
}

export function BriefingPanel() {
  const point = useAirspaceStore((state) => state.point);
  const briefedAt = useAirspaceStore((state) => state.briefedAt);
  const weather = useAirspaceStore((state) => state.weather);
  const airspace = useAirspaceStore((state) => state.airspace);
  const uasGrid = useAirspaceStore((state) => state.uasGrid);
  const metars = useAirspaceStore((state) => state.metars);
  const limits = useAirspaceStore((state) => state.limits);
  const refresh = useAirspaceStore((state) => state.refresh);
  const assessment = useAssessment();
  const coverage = useVpsCoverageAt(point);

  if (!point) {
    return (
      <PanelStack title="Briefing" subtitle="Click anywhere on the map to brief that location." action={<BriefMapCenterButton />}>
        <EmptyLine text={ADVISORY_NOTE} />
      </PanelStack>
    );
  }

  const flagged = assessment.checks.filter((check) => check.level === "caution" || check.level === "nogo");
  const verdictColor = LEVEL_COLORS[assessment.overall];

  return (
    <PanelStack title="Briefing" subtitle={formatPoint(point)} action={<BriefMapCenterButton />}>
      <div className="flex items-start gap-3 pb-2">
        <div className="min-w-0 flex-1">
          {assessment.pending ? (
            <div className="flex items-center gap-2 text-2xl font-semibold text-slate-300">
              <Loader2 size={20} className="animate-spin" /> Checking
            </div>
          ) : (
            <div className="text-2xl font-semibold leading-tight" style={{ color: verdictColor }}>{VERDICT_TEXT[assessment.overall]}</div>
          )}
          <p className="mt-0.5 text-[13px] text-slate-400">
            {assessment.pending
              ? "Waiting for weather and airspace sources."
              : flagged.length
                ? `${flagged.map((check) => check.label).join(", ")} ${flagged.length === 1 ? "needs" : "need"} attention.`
                : "All checks within your limits."}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5 pt-1">
          <button type="button" onClick={refresh} title="Refresh briefing" className="flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:bg-white/[0.06] hover:text-slate-100">
            <RefreshCw size={16} className={assessment.pending ? "animate-spin" : undefined} />
          </button>
          <button
            type="button"
            onClick={() => usePlatformMapStore.getState().api?.focus(point, 14)}
            title="Show on map"
            className="flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:bg-white/[0.06] hover:text-slate-100"
          >
            <LocateFixed size={16} />
          </button>
        </div>
      </div>

      <div className="divide-y divide-white/[0.06] rounded-lg bg-white/[0.035]">
        {assessment.checks.map((check) => (
          <div key={check.id} className="px-3 py-2.5">
            <div className="flex items-baseline gap-2.5">
              <span className="h-2 w-2 shrink-0 translate-y-[-1px] rounded-full" style={{ backgroundColor: LEVEL_COLORS[check.level] }} />
              <span className="text-[13px] text-slate-300">{check.label}</span>
              <span className="ml-auto truncate text-[13.5px] font-medium tabular-nums text-slate-100">{check.value}</span>
            </div>
            {check.detail && <p className="mt-0.5 pl-[18px] text-xs leading-snug text-slate-500 [text-wrap:pretty]">{check.detail}</p>}
          </div>
        ))}
      </div>

      {coverage && (
        <p className="flex items-start gap-2 px-1 pt-1 text-[13px] leading-snug text-slate-400">
          <Crosshair size={15} className="mt-0.5 shrink-0 text-[#FF6600]" />
          {coverage.length
            ? `Inside Macula VPS map coverage (${coverage.join(", ")}).`
            : "No Macula VPS map covers this location."}
        </p>
      )}

      {weather.data && weather.data.hours.length > 0 && (
        <>
          <SectionLabel>Next 12 hours</SectionLabel>
          <WindStrip hours={weather.data.hours.slice(0, 12)} maxGustMs={limits.maxGustMs} maxWindMs={limits.maxWindMs} />
        </>
      )}

      <SectionLabel>Sources</SectionLabel>
      <div className="space-y-1 px-1">
        <SourceLine label="Weather, Open-Meteo" loading={weather.loading} error={weather.error} />
        <SourceLine label="METAR stations, aviationweather.gov" loading={metars.loading} error={metars.error} count={metars.data?.length} />
        <SourceLine label="Airspace, FAA" loading={airspace.loading} error={airspace.error} count={airspace.data?.length} />
        <SourceLine label="LAANC grid, FAA" loading={uasGrid.loading} error={uasGrid.error} count={uasGrid.data?.length} />
      </div>
      <p className="px-1 pt-2 text-xs leading-snug text-slate-500">
        {briefedAt ? `Updated ${new Date(briefedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}. ` : ""}
        {ADVISORY_NOTE}
      </p>
    </PanelStack>
  );
}

export function AirspacePanel() {
  const point = useAirspaceStore((state) => state.point);
  const airspace = useAirspaceStore((state) => state.airspace);
  const assessment = useAssessment();
  const hereIds = new Set(assessment.airspaceHere.map((area) => area.id));
  const areas = [...(airspace.data ?? [])].sort((a, b) =>
    Number(hereIds.has(b.id)) - Number(hereIds.has(a.id)) || a.airspaceClass.localeCompare(b.airspaceClass) || a.name.localeCompare(b.name),
  );

  return (
    <PanelStack title="Airspace" subtitle="Class B, C, D and E surface areas within 30 km">
      {!point && <EmptyLine text="Click the map to load airspace around a location." />}
      {airspace.loading && <EmptyLine text="Loading FAA airspace…" />}
      {airspace.error && <EmptyLine text={airspace.error} />}
      {point && airspace.data?.length === 0 && <EmptyLine text="No controlled airspace near this location (or outside FAA coverage)." />}
      {assessment.uasCeilingHere && (
        <div className="flex items-center gap-2 rounded-md border border-border bg-bg-card px-3 py-2 text-xs">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: uasCeilingColor(assessment.uasCeilingHere.ceilingFt) }} />
          <span className="text-slate-400">LAANC ceiling at briefing point</span>
          <span className="ml-auto font-data-mono text-slate-100">{assessment.uasCeilingHere.ceilingFt} ft</span>
        </div>
      )}
      {areas.map((area) => (
        <div
          key={area.id}
          className={cn("rounded-md border bg-bg-card px-3 py-2", hereIds.has(area.id) ? "border-yellow-500/40" : "border-border")}
        >
          <div className="flex items-center gap-2">
            <span
              className="rounded px-1.5 py-0.5 text-[11px] font-semibold"
              style={{ backgroundColor: `${AIRSPACE_CLASS_COLORS[area.airspaceClass] ?? "#94A3B8"}33`, color: AIRSPACE_CLASS_COLORS[area.airspaceClass] ?? "#94A3B8" }}
            >
              {area.airspaceClass}
            </span>
            <span className="min-w-0 flex-1 truncate text-sm text-slate-100">{area.name}</span>
            {hereIds.has(area.id) && <span className="text-[11px] text-yellow-400">Here</span>}
          </div>
          <div className="mt-1 font-data-mono text-[11px] text-slate-500">{area.floor} to {area.ceiling}</div>
        </div>
      ))}
    </PanelStack>
  );
}

export function StationsPanel() {
  const point = useAirspaceStore((state) => state.point);
  const metars = useAirspaceStore((state) => state.metars);
  const selectedStationId = useAirspaceStore((state) => state.selectedStationId);
  const selectStation = useAirspaceStore((state) => state.selectStation);

  const distanceKm = (target: [number, number]) => {
    if (!point) return 0;
    const dLat = (target[0] - point[0]) * 111.32;
    const dLon = (target[1] - point[1]) * 111.32 * Math.cos((point[0] * Math.PI) / 180);
    return Math.hypot(dLat, dLon);
  };
  const stations = [...(metars.data ?? [])].sort((a, b) =>
    a.id === selectedStationId ? -1 : b.id === selectedStationId ? 1 : distanceKm(a.position) - distanceKm(b.position),
  );

  return (
    <PanelStack title="Weather Stations" subtitle="METAR observations within 80 km">
      {!point && <EmptyLine text="Click the map to load nearby stations." />}
      {metars.loading && <EmptyLine text="Loading METARs…" />}
      {metars.error && <EmptyLine text={metars.error} />}
      {point && metars.data?.length === 0 && <EmptyLine text="No reporting stations near this location." />}
      {stations.map((station) => {
        const color = FLIGHT_CATEGORY_COLORS[station.flightCategory ?? ""] ?? "#94A3B8";
        return (
          <button
            key={station.id}
            type="button"
            onClick={() => {
              selectStation(station.id);
              usePlatformMapStore.getState().api?.focus(station.position, 11);
            }}
            className={cn(
              "w-full rounded-md border bg-bg-card px-3 py-2 text-left transition-colors hover:bg-white/[0.04]",
              station.id === selectedStationId ? "border-violet-400/50" : "border-border",
            )}
          >
            <div className="flex items-center gap-2">
              <span className="font-data-mono text-sm font-semibold text-slate-100">{station.id}</span>
              <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold" style={{ backgroundColor: `${color}26`, color }}>
                {station.flightCategory ?? "n/a"}
              </span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-slate-500">{station.name}</span>
              <span className="shrink-0 text-[11px] text-slate-500">{distanceKm(station.position).toFixed(0)} km</span>
            </div>
            <div className="mt-1 break-words font-data-mono text-[11px] leading-relaxed text-slate-400">{station.raw}</div>
          </button>
        );
      })}
    </PanelStack>
  );
}

const LIMIT_FIELDS: Array<{ key: keyof FlightLimits; label: string; unit: string; step: number }> = [
  { key: "maxWindMs", label: "Max sustained wind", unit: "m/s", step: 0.5 },
  { key: "maxGustMs", label: "Max gust", unit: "m/s", step: 0.5 },
  { key: "minVisibilityM", label: "Min visibility", unit: "m", step: 500 },
  { key: "maxPrecipitationMm", label: "Max precipitation", unit: "mm", step: 0.1 },
  { key: "minTemperatureC", label: "Min temperature", unit: "°C", step: 1 },
  { key: "maxTemperatureC", label: "Max temperature", unit: "°C", step: 1 },
];

export function LimitsPanel({ ctx }: { ctx: PlatformContext }) {
  const limits = useAirspaceStore((state) => state.limits);
  const setLimits = useAirspaceStore((state) => state.setLimits);
  const resetLimits = useAirspaceStore((state) => state.resetLimits);
  const isDefault = LIMIT_FIELDS.every(({ key }) => limits[key] === DEFAULT_LIMITS[key]);

  return (
    <PanelStack
      title="Flight Limits"
      subtitle="Thresholds for the go / no-go briefing on this computer"
      action={
        <button
          type="button"
          disabled={isDefault}
          onClick={resetLimits}
          className="operator-shell-button flex h-8 items-center gap-1.5 rounded-md border border-white/10 px-2.5 text-xs disabled:opacity-40"
        >
          <RotateCcw size={12} /> Defaults
        </button>
      }
    >
      {LIMIT_FIELDS.map(({ key, label, unit, step }) => (
        <label key={key} className="flex items-center justify-between gap-3 rounded-md border border-border bg-bg-card px-3 py-2">
          <span className="text-sm text-slate-200">{label}</span>
          <span className="flex items-center gap-1.5">
            <input
              type="number"
              step={step}
              value={limits[key]}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (Number.isFinite(value)) setLimits({ [key]: value });
              }}
              className="w-20 rounded border border-white/10 bg-black/30 px-2 py-1 text-right font-data-mono text-sm text-slate-100 outline-none focus:border-violet-400/60"
            />
            <span className="w-8 text-xs text-slate-500">{unit}</span>
          </span>
        </label>
      ))}
      <p className="px-1 text-[11px] text-slate-600">
        Use the limits from your aircraft's manual and your operations manual. The briefing updates immediately.
      </p>
      <button type="button" onClick={() => ctx.openPanel(PANEL_BRIEFING)} className="text-xs text-violet-300 hover:text-violet-200">
        Back to briefing
      </button>
    </PanelStack>
  );
}
