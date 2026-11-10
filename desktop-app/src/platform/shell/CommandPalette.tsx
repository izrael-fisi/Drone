import { HouseIcon as Home, CircleNotchIcon as Loader2, MapPinIcon as MapPin, MagnifyingGlassIcon as Search, type Icon as IconType } from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useShellStore } from "../../lib/shellStore";
import { cn } from "../../lib/utils";
import { useActiveProduct, useEntitledProducts, useProductCommands } from "../hooks";
import { PLATFORM_PANELS } from "../platformPanels";
import type { PlatformContext } from "../types";
import { locationPopularityScore, mergeLocationResults, popularLocationAutocomplete, type ArcGisGeocodeResponse, type LocationResult } from "./locationSearch";

type CommandEntry = {
  id: string;
  label: string;
  detail: string;
  group: string;
  keywords?: string;
  Icon: IconType;
  disabled?: boolean;
  action: () => void;
};

export function SpotlightSearchButton({
  open,
  bottomOffset,
  onOpen,
}: {
  open: boolean;
  bottomOffset: number;
  onOpen: () => void;
}) {
  return (
    <div
      className="pointer-events-none absolute left-1/2 z-[1400] w-[min(520px,calc(100vw-128px))] -translate-x-1/2"
      style={{ bottom: bottomOffset }}
    >
      <button
        type="button"
        onClick={onOpen}
        className={cn(
          "pointer-events-auto flex h-11 w-full items-center gap-3 rounded-full border px-4 text-left shadow-2xl backdrop-blur-xl transition-all",
          open
            ? "border-cyan-500/45 bg-bg-base/80 text-slate-100 shadow-[0_18px_60px_rgba(255,102,0,0.16)]"
            : "border-white/15 bg-white/[0.075] text-slate-300 shadow-[0_18px_60px_rgba(0,0,0,0.42)] hover:border-cyan-500/40 hover:bg-bg-base/72 hover:text-slate-100",
        )}
        title="Search"
      >
        <Search size={18} className="shrink-0 text-slate-300" />
        <span className="min-w-0 flex-1 truncate text-sm">Search places and commands</span>
        <kbd className="shrink-0 rounded border border-white/15 px-1.5 py-0.5 font-sans text-[11px] text-slate-400">Ctrl K</kbd>
      </button>
    </div>
  );
}

/**
 * Global search: places (geocoder), platform panels, and commands contributed
 * by every licensed product module.
 */
export function CommandPalette({
  open,
  bottomOffset,
  ctx,
  onClose,
}: {
  open: boolean;
  bottomOffset: number;
  ctx: PlatformContext;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const { resetRightDock, setMapSearchTarget } = useShellStore();
  const entitledProducts = useEntitledProducts();
  const activeProduct = useActiveProduct();
  const productCommands = useProductCommands();
  const [query, setQuery] = useState("");
  const [locationResults, setLocationResults] = useState<LocationResult[]>([]);
  const [locationLoading, setLocationLoading] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setLocationResults([]);
      setLocationError(null);
    }
  }, [open]);

  useEffect(() => {
    const normalized = query.trim();
    if (!open || normalized.length < 2) {
      setLocationResults([]);
      setLocationLoading(false);
      setLocationError(null);
      return;
    }

    const popularResults = popularLocationAutocomplete(normalized);
    setLocationResults(popularResults);
    setLocationError(null);

    if (normalized.length < 3) {
      setLocationLoading(false);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLocationLoading(true);
      setLocationError(null);
      try {
        const params = new URLSearchParams({
          f: "json",
          SingleLine: normalized,
          maxLocations: "10",
          outFields: "LongLabel,ShortLabel,Addr_type,Type,PlaceName,City,Region,RegionAbbr,Country,CountryCode,Rank,PopRank",
        });
        const response = await fetch(
          `https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates?${params.toString()}`,
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error(`geocoder ${response.status}`);
        const data = (await response.json()) as ArcGisGeocodeResponse;
        const nextResults = (data.candidates ?? [])
          .slice()
          .sort((left, right) => locationPopularityScore(right, normalized) - locationPopularityScore(left, normalized))
          .slice(0, 5)
          .map<LocationResult | null>((candidate, index) => {
            const lat = candidate.location?.y;
            const lon = candidate.location?.x;
            if (typeof lat !== "number" || typeof lon !== "number") return null;
            const label = candidate.attributes?.ShortLabel ?? candidate.address ?? normalized;
            const detail = candidate.attributes?.LongLabel ?? candidate.address ?? "Location";
            return {
              id: `location-${index}-${lat.toFixed(5)}-${lon.toFixed(5)}`,
              label,
              detail,
              lat,
              lon,
            };
          })
          .filter((result): result is LocationResult => Boolean(result));
        setLocationResults(mergeLocationResults(popularResults, nextResults));
      } catch {
        if (!controller.signal.aborted) {
          setLocationResults(popularResults);
          setLocationError(popularResults.length ? null : "Location search unavailable");
        }
      } finally {
        if (!controller.signal.aborted) setLocationLoading(false);
      }
    }, 280);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [open, query]);

  const entries = useMemo<CommandEntry[]>(() => {
    const home: CommandEntry = {
      id: "home",
      label: "Home",
      detail: "Global operations map",
      group: "Pages",
      Icon: Home,
      action: () => {
        navigate("/home");
        resetRightDock();
      },
    };

    const panelEntry = (group: string) => (panel: (typeof PLATFORM_PANELS)[number]): CommandEntry | null =>
      panel.command
        ? {
            id: `panel-${panel.id}`,
            label: panel.command.label,
            detail: panel.command.detail,
            group,
            keywords: panel.command.keywords,
            Icon: panel.Icon,
            action: () => ctx.openPanel(panel.id),
          }
        : null;

    const platformEntries = PLATFORM_PANELS.map(panelEntry("Proxigo"));
    // Active product first so its panels rank above other products' panels.
    const orderedProducts = activeProduct
      ? [activeProduct, ...entitledProducts.filter((product) => product.id !== activeProduct.id)]
      : entitledProducts;
    const productPanelEntries = orderedProducts.flatMap((product) => product.panels.map(panelEntry(product.shortName)));
    const commandEntries = productCommands.map<CommandEntry>((command) => ({
      id: command.id,
      label: command.label,
      detail: command.detail,
      group: command.group,
      keywords: command.keywords,
      Icon: command.Icon,
      disabled: command.disabled,
      action: () => command.run(ctx),
    }));

    return [home, ...productPanelEntries, ...platformEntries, ...commandEntries].filter(
      (entry): entry is CommandEntry => Boolean(entry),
    );
  }, [activeProduct, ctx, entitledProducts, navigate, productCommands, resetRightDock]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return entries;
    return entries.filter((entry) =>
      `${entry.label} ${entry.detail} ${entry.group} ${entry.keywords ?? ""}`.toLowerCase().includes(normalized),
    );
  }, [entries, query]);

  const runEntry = (entry: CommandEntry) => {
    if (entry.disabled) return;
    entry.action();
    onClose();
  };

  const runLocation = (result: LocationResult) => {
    setMapSearchTarget({
      id: result.id,
      label: result.label,
      detail: result.detail,
      lat: result.lat,
      lon: result.lon,
      zoom: 11,
    });
    navigate("/home");
    onClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[2500]" onMouseDown={onClose}>
      <div
        className="absolute left-1/2 flex w-[min(620px,calc(100vw-32px))] -translate-x-1/2 flex-col overflow-hidden rounded-lg border border-white/15 bg-bg-base/85 shadow-[0_24px_90px_rgba(0,0,0,0.62)] ring-1 ring-white/5 backdrop-blur-2xl"
        style={{
          bottom: bottomOffset + 64,
          maxHeight: `min(520px, calc(100vh - ${bottomOffset + 132}px))`,
        }}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex h-14 items-center gap-3 border-b border-white/10 bg-white/[0.035] px-4">
          <Search size={18} className="text-cyan-500" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                if (locationResults[0]) runLocation(locationResults[0]);
                else if (filtered[0]) runEntry(filtered[0]);
              }
              if (event.key === "Escape") onClose();
            }}
            className="h-full flex-1 bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-600"
            placeholder="Search a city, country, map, panel, setting..."
          />
        </div>
        <div className="overflow-y-auto p-2">
          {query.trim().length >= 2 && (
            <div className="mb-2 border-b border-white/10 pb-2">
              <div className="flex items-center gap-2 px-3 py-1.5 text-[11px] font-semibold text-slate-600">
                {locationLoading ? <Loader2 size={12} className="animate-spin" /> : <MapPin size={12} />}
                Locations
              </div>
              {locationResults.map((result) => (
                <button
                  key={result.id}
                  type="button"
                  onClick={() => runLocation(result)}
                  className="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors hover:bg-cyan-500/10 hover:text-slate-100"
                >
                  <MapPin size={16} className="shrink-0 text-cyan-400" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-slate-200">{result.label}</span>
                    <span className="block truncate text-xs text-slate-500">{result.detail}</span>
                  </span>
                  <span className="shrink-0 text-[11px] text-slate-600">Place</span>
                </button>
              ))}
              {!locationLoading && locationResults.length === 0 && (
                <div className="px-3 py-2 text-xs text-slate-600">
                  {locationError ?? "No matching places yet"}
                </div>
              )}
            </div>
          )}
          {filtered.length === 0 && query.trim().length < 2 ? (
            <div className="px-4 py-10 text-center text-sm text-slate-500">No results</div>
          ) : (
            filtered.map((entry) => {
              const Icon = entry.Icon;
              return (
                <button
                  key={entry.id}
                  type="button"
                  disabled={entry.disabled}
                  onClick={() => runEntry(entry)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors",
                    entry.disabled
                      ? "cursor-not-allowed opacity-45"
                      : "hover:bg-cyan-500/10 hover:text-slate-100",
                  )}
                >
                  <Icon size={16} className="shrink-0 text-slate-500" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-slate-200">{entry.label}</span>
                    <span className="block truncate text-xs text-slate-500">{entry.detail}</span>
                  </span>
                  <span className="shrink-0 text-[11px] text-slate-600">{entry.group}</span>
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
