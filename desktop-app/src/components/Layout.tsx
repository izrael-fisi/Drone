import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Archive,
  Building2,
  Camera,
  ChevronLeft,
  ChevronRight,
  Home,
  Loader2,
  Map as MapIcon,
  MapPin,
  MessageSquare,
  Minus,
  Pencil,
  Radio,
  Search,
  Server,
  Settings as SettingsIcon,
  SlidersHorizontal,
  Square,
  Terminal,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Outlet, useLocation, useNavigate, type NavigateFunction } from "react-router-dom";
import { DroneLogo } from "../App";
import {
  candidateHost,
  candidateName,
  discoveryStatusSummary,
  loadDiscoveryHistory,
  mergeDiscoveryHistory,
  saveDiscoveryHistory,
} from "../lib/discovery";
import { useShellStore, type BottomDockTabId, type MapSearchTarget, type RightDockRoute } from "../lib/shellStore";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import type {
  Device,
  EdgeApiMissionPlannerStatus,
  EdgeApiQGroundControlStatus,
  LocalNetworkHint,
  PiDiscoveryCandidate,
  Profile,
  Region,
} from "../lib/types";
import { cn } from "../lib/utils";
import { deriveOperatorRuntimeModel, type OperatorRuntimeModel } from "../lib/operatorRuntimeAdapter";

const DOCK_LABELS: Record<RightDockRoute, string> = {
  root: "Panel",
  maps: "Maps",
  vehicle: "Device",
  "ground-control": "Ground Control",
  camera: "Camera",
  calibration: "Calibration",
  flights: "Flights",
  settings: "Settings",
  mav: "MAV",
  "diagnostics-settings": "Diagnostics",
  account: "Account",
};

const BOTTOM_TABS: Array<{ id: BottomDockTabId; label: string; Icon: LucideIcon; requiresDevice?: boolean }> = [
  { id: "system-status", label: "System Status", Icon: Activity },
  { id: "diagnostics", label: "Diagnostics", Icon: Activity },
  { id: "parameters", label: "Parameters", Icon: SlidersHorizontal },
  { id: "messages", label: "Messages", Icon: MessageSquare, requiresDevice: true },
  { id: "ekf-init", label: "EKF Init", Icon: Radio, requiresDevice: true },
  { id: "console", label: "Console", Icon: Terminal, requiresDevice: true },
];

function hasTauriWindowRuntime() {
  if (typeof window === "undefined") return false;
  const tauriInternals = (
    window as Window & { __TAURI_INTERNALS__?: { invoke?: unknown; metadata?: unknown } }
  ).__TAURI_INTERNALS__;
  return typeof tauriInternals?.invoke === "function" && Boolean(tauriInternals.metadata);
}

async function runWindowAction(action: "minimize" | "toggleMaximize" | "close") {
  if (!hasTauriWindowRuntime()) return;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  const appWindow = getCurrentWindow();
  await appWindow[action]();
}

export function Layout() {
  const { devices, regions, activeDeviceId, setActiveDevice } = useAppStore();
  const {
    rightDockOpen,
    rightDockStack,
    bottomDockOpen,
    bottomDockTab,
    setRightDockOpen,
    pushRightDock,
    popRightDock,
    resetRightDock,
    setBottomDockOpen,
    setBottomDockTab,
    setMapSearchTarget,
  } = useShellStore();
  const navigate = useNavigate();
  const location = useLocation();
  const settingsMode = location.pathname.startsWith("/settings");
  const model = useMemo(
    () => deriveOperatorRuntimeModel({ devices, regions, activeDeviceId }),
    [activeDeviceId, devices, regions],
  );
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const bottomChromeOffset = bottomDockOpen ? 292 : 68;

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandPaletteOpen(true);
        return;
      }
      if (event.key === "Escape") {
        if (commandPaletteOpen) {
          setCommandPaletteOpen(false);
          return;
        }
        resetRightDock();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [commandPaletteOpen, resetRightDock]);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg-base text-slate-200">
      <header
        className="relative flex h-9 shrink-0 items-center justify-between border-b border-border bg-bg-base pl-2"
        data-tauri-drag-region
      >
        <button
          type="button"
          onClick={() => {
            navigate("/home");
            resetRightDock();
          }}
          className="flex h-full min-w-0 items-center gap-2 px-2"
          data-tauri-drag-region="false"
        >
          <DroneLogo size={18} />
          <span className="font-sans text-[11px] font-semibold uppercase tracking-[0.08em] text-white/80">
            Drone Vision
          </span>
        </button>
        <div className="flex h-full items-center" data-tauri-drag-region="false">
          <button type="button" className="h-9 w-[46px] operator-shell-button" onClick={() => runWindowAction("minimize")} title="Minimize">
            <Minus size={13} />
          </button>
          <button type="button" className="h-9 w-[46px] operator-shell-button" onClick={() => runWindowAction("toggleMaximize")} title="Maximize">
            <Square size={12} />
          </button>
          <button type="button" className="h-9 w-[46px] operator-shell-button hover:bg-red-700 hover:text-white" onClick={() => runWindowAction("close")} title="Close">
            <X size={14} />
          </button>
        </div>
      </header>

      <section className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="relative min-h-0 flex-1 overflow-hidden bg-bg-base">
          <main className="absolute inset-0 overflow-hidden">
            <div className="h-full overflow-auto">
              <Outlet />
            </div>
          </main>

          {!settingsMode && (
            <div
              className={cn(
                "absolute left-3 z-[1300] transition-[width] duration-200",
                rightDockOpen ? "w-[416px]" : "w-14",
              )}
              style={{
                top: 16,
                bottom: bottomChromeOffset,
              }}
            >
              <RightDock
                open={rightDockOpen}
                stack={rightDockStack}
                model={model}
                regions={regions}
                onOpenChange={setRightDockOpen}
                pushRightDock={pushRightDock}
                popRightDock={popRightDock}
                resetRightDock={resetRightDock}
                setBottomDockTab={setBottomDockTab}
              />
            </div>
          )}

          {!settingsMode && (
            <BottomDock
              open={bottomDockOpen}
              tab={bottomDockTab}
              model={model}
              onOpenChange={setBottomDockOpen}
              onTabChange={setBottomDockTab}
            />
          )}

          {!settingsMode && (
            <SpotlightSearchButton
              open={commandPaletteOpen}
              bottomOffset={bottomChromeOffset}
              onOpen={() => setCommandPaletteOpen(true)}
            />
          )}

          <GlobalCommandPalette
            open={commandPaletteOpen}
            bottomOffset={bottomChromeOffset}
            model={model}
            regions={regions}
            devices={devices}
            onClose={() => setCommandPaletteOpen(false)}
            navigate={navigate}
            resetRightDock={resetRightDock}
            pushRightDock={pushRightDock}
            setBottomDockTab={setBottomDockTab}
            setMapSearchTarget={setMapSearchTarget}
            setActiveDevice={setActiveDevice}
          />
        </div>
      </section>
    </div>
  );
}

type CommandEntry = {
  id: string;
  label: string;
  detail: string;
  group: string;
  keywords?: string;
  Icon: LucideIcon;
  disabled?: boolean;
  action: () => void;
};

type LocationResult = {
  id: string;
  label: string;
  detail: string;
  lat: number;
  lon: number;
};

type PopularLocation = LocationResult & {
  aliases?: string[];
  popularity: number;
};

type ArcGisGeocodeCandidate = {
  address?: string;
  score?: number;
  location?: {
    x?: number;
    y?: number;
  };
  attributes?: {
    LongLabel?: string;
    ShortLabel?: string;
    Addr_type?: string;
    Type?: string;
    PlaceName?: string;
    City?: string;
    Region?: string;
    RegionAbbr?: string;
    Country?: string;
    CountryCode?: string;
    Rank?: number;
    PopRank?: number;
  };
};

type ArcGisGeocodeResponse = {
  candidates?: ArcGisGeocodeCandidate[];
};

const POPULAR_LOCATION_HINTS: Record<string, string[]> = {
  "san francisco": ["san francisco", "california", "ca", "united states", "usa"],
  "new york": ["new york", "ny", "united states", "usa"],
  "los angeles": ["los angeles", "california", "ca", "united states", "usa"],
  chicago: ["chicago", "illinois", "il", "united states", "usa"],
  houston: ["houston", "texas", "tx", "united states", "usa"],
  phoenix: ["phoenix", "arizona", "az", "united states", "usa"],
  philadelphia: ["philadelphia", "pennsylvania", "pa", "united states", "usa"],
  "san antonio": ["san antonio", "texas", "tx", "united states", "usa"],
  "san diego": ["san diego", "california", "ca", "united states", "usa"],
  dallas: ["dallas", "texas", "tx", "united states", "usa"],
  "san jose": ["san jose", "california", "ca", "united states", "usa"],
  austin: ["austin", "texas", "tx", "united states", "usa"],
  jacksonville: ["jacksonville", "florida", "fl", "united states", "usa"],
  "fort worth": ["fort worth", "texas", "tx", "united states", "usa"],
  columbus: ["columbus", "ohio", "oh", "united states", "usa"],
  charlotte: ["charlotte", "north carolina", "nc", "united states", "usa"],
  indianapolis: ["indianapolis", "indiana", "in", "united states", "usa"],
  seattle: ["seattle", "washington", "wa", "united states", "usa"],
  denver: ["denver", "colorado", "co", "united states", "usa"],
  boston: ["boston", "massachusetts", "ma", "united states", "usa"],
  "washington dc": ["washington", "district of columbia", "dc", "united states", "usa"],
  washington: ["washington", "district of columbia", "dc", "united states", "usa"],
  portland: ["portland", "oregon", "or", "united states", "usa"],
  paris: ["paris", "france"],
  london: ["london", "england", "united kingdom", "uk"],
  tokyo: ["tokyo", "japan"],
  berlin: ["berlin", "germany"],
  rome: ["rome", "italy"],
  madrid: ["madrid", "spain"],
  sydney: ["sydney", "new south wales", "australia"],
  melbourne: ["melbourne", "victoria", "australia"],
  toronto: ["toronto", "ontario", "canada"],
  vancouver: ["vancouver", "british columbia", "canada"],
  montreal: ["montreal", "quebec", "canada"],
};

const POPULAR_LOCATION_AUTOCOMPLETE: PopularLocation[] = [
  { id: "popular-dubai", label: "Dubai", detail: "Dubai, United Arab Emirates", lat: 25.2048, lon: 55.2708, aliases: ["dxb", "dub"], popularity: 1000 },
  { id: "popular-shanghai", label: "Shanghai", detail: "Shanghai, China", lat: 31.2304, lon: 121.4737, aliases: ["sha", "shanghai china"], popularity: 995 },
  { id: "popular-new-york", label: "New York", detail: "New York, United States", lat: 40.7128, lon: -74.006, aliases: ["nyc", "new york city"], popularity: 990 },
  { id: "popular-london", label: "London", detail: "England, United Kingdom", lat: 51.5072, lon: -0.1276, aliases: ["lhr", "london uk"], popularity: 985 },
  { id: "popular-tokyo", label: "Tokyo", detail: "Tokyo, Japan", lat: 35.6762, lon: 139.6503, aliases: ["tokyo japan"], popularity: 980 },
  { id: "popular-paris", label: "Paris", detail: "Ile-de-France, France", lat: 48.8566, lon: 2.3522, aliases: ["paris france"], popularity: 975 },
  { id: "popular-singapore", label: "Singapore", detail: "Singapore", lat: 1.3521, lon: 103.8198, aliases: ["sin"], popularity: 970 },
  { id: "popular-hong-kong", label: "Hong Kong", detail: "Hong Kong, China", lat: 22.3193, lon: 114.1694, aliases: ["hk", "hkg"], popularity: 965 },
  { id: "popular-beijing", label: "Beijing", detail: "Beijing, China", lat: 39.9042, lon: 116.4074, aliases: ["pek"], popularity: 960 },
  { id: "popular-seoul", label: "Seoul", detail: "Seoul, South Korea", lat: 37.5665, lon: 126.978, aliases: ["sel"], popularity: 955 },
  { id: "popular-los-angeles", label: "Los Angeles", detail: "California, United States", lat: 34.0522, lon: -118.2437, aliases: ["la", "lax"], popularity: 950 },
  { id: "popular-san-francisco", label: "San Francisco", detail: "California, United States", lat: 37.7749, lon: -122.4194, aliases: ["sf", "sfo", "san francisco ca"], popularity: 945 },
  { id: "popular-mumbai", label: "Mumbai", detail: "Maharashtra, India", lat: 19.076, lon: 72.8777, aliases: ["bom"], popularity: 940 },
  { id: "popular-delhi", label: "Delhi", detail: "Delhi, India", lat: 28.6139, lon: 77.209, aliases: ["new delhi", "del"], popularity: 935 },
  { id: "popular-bangkok", label: "Bangkok", detail: "Bangkok, Thailand", lat: 13.7563, lon: 100.5018, aliases: ["bkk"], popularity: 930 },
  { id: "popular-istanbul", label: "Istanbul", detail: "Istanbul, Turkey", lat: 41.0082, lon: 28.9784, aliases: ["ist"], popularity: 925 },
  { id: "popular-berlin", label: "Berlin", detail: "Berlin, Germany", lat: 52.52, lon: 13.405, aliases: ["berlin germany"], popularity: 920 },
  { id: "popular-rome", label: "Rome", detail: "Lazio, Italy", lat: 41.9028, lon: 12.4964, aliases: ["roma"], popularity: 915 },
  { id: "popular-madrid", label: "Madrid", detail: "Madrid, Spain", lat: 40.4168, lon: -3.7038, aliases: ["madrid spain"], popularity: 910 },
  { id: "popular-sydney", label: "Sydney", detail: "New South Wales, Australia", lat: -33.8688, lon: 151.2093, aliases: ["syd"], popularity: 905 },
  { id: "popular-melbourne", label: "Melbourne", detail: "Victoria, Australia", lat: -37.8136, lon: 144.9631, aliases: ["mel"], popularity: 900 },
  { id: "popular-toronto", label: "Toronto", detail: "Ontario, Canada", lat: 43.6532, lon: -79.3832, aliases: ["yyz"], popularity: 895 },
  { id: "popular-vancouver", label: "Vancouver", detail: "British Columbia, Canada", lat: 49.2827, lon: -123.1207, aliases: ["yvr"], popularity: 890 },
  { id: "popular-chicago", label: "Chicago", detail: "Illinois, United States", lat: 41.8781, lon: -87.6298, aliases: ["ord"], popularity: 885 },
  { id: "popular-miami", label: "Miami", detail: "Florida, United States", lat: 25.7617, lon: -80.1918, aliases: ["mia"], popularity: 880 },
  { id: "popular-seattle", label: "Seattle", detail: "Washington, United States", lat: 47.6062, lon: -122.3321, aliases: ["sea"], popularity: 875 },
  { id: "popular-boston", label: "Boston", detail: "Massachusetts, United States", lat: 42.3601, lon: -71.0589, aliases: ["bos"], popularity: 870 },
  { id: "popular-washington-dc", label: "Washington DC", detail: "District of Columbia, United States", lat: 38.9072, lon: -77.0369, aliases: ["dc", "washington"], popularity: 865 },
  { id: "popular-mexico-city", label: "Mexico City", detail: "Mexico", lat: 19.4326, lon: -99.1332, aliases: ["cdmx", "mex"], popularity: 860 },
  { id: "popular-sao-paulo", label: "Sao Paulo", detail: "Sao Paulo, Brazil", lat: -23.5558, lon: -46.6396, aliases: ["sao"], popularity: 855 },
  { id: "popular-buenos-aires", label: "Buenos Aires", detail: "Argentina", lat: -34.6037, lon: -58.3816, aliases: ["buenos"], popularity: 850 },
  { id: "popular-cairo", label: "Cairo", detail: "Cairo, Egypt", lat: 30.0444, lon: 31.2357, aliases: ["cai"], popularity: 845 },
  { id: "popular-johannesburg", label: "Johannesburg", detail: "Gauteng, South Africa", lat: -26.2041, lon: 28.0473, aliases: ["jnb", "joburg"], popularity: 840 },
  { id: "popular-lagos", label: "Lagos", detail: "Lagos, Nigeria", lat: 6.5244, lon: 3.3792, aliases: ["los nigeria"], popularity: 835 },
  { id: "popular-nairobi", label: "Nairobi", detail: "Nairobi, Kenya", lat: -1.2921, lon: 36.8219, aliases: ["nbo"], popularity: 830 },
  { id: "popular-houston", label: "Houston", detail: "Texas, United States", lat: 29.7604, lon: -95.3698, aliases: ["iah"], popularity: 825 },
  { id: "popular-dallas", label: "Dallas", detail: "Texas, United States", lat: 32.7767, lon: -96.797, aliases: ["dfw"], popularity: 820 },
];

function normalizeLocationText(value: string) {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/\b(st|saint)\b/g, "saint")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function popularLocationAutocomplete(query: string) {
  const normalizedQuery = normalizeLocationText(query);
  if (normalizedQuery.length < 2) return [];

  return POPULAR_LOCATION_AUTOCOMPLETE.map((location) => {
    const label = normalizeLocationText(location.label);
    const terms = [location.label, location.detail, ...(location.aliases ?? [])].map(normalizeLocationText);
    const matchesWordPrefix = terms.some((term) => term.split(" ").some((part) => part.startsWith(normalizedQuery)));
    const matchesTermPrefix = terms.some((term) => term.startsWith(normalizedQuery));
    const matchesTerm = terms.some((term) => term.includes(normalizedQuery));
    if (!matchesWordPrefix && !matchesTermPrefix && !matchesTerm) return null;

    const score =
      location.popularity +
      (label.startsWith(normalizedQuery) ? 600 : 0) +
      (matchesTermPrefix ? 360 : 0) +
      (matchesWordPrefix ? 180 : 0) +
      (matchesTerm ? 40 : 0);

    return { location, score };
  })
    .filter((match): match is { location: PopularLocation; score: number } => Boolean(match))
    .sort((left, right) => right.score - left.score)
    .slice(0, 5)
    .map(({ location }) => location);
}

function mergeLocationResults(primary: LocationResult[], secondary: LocationResult[]) {
  const seen = new Set<string>();

  return [...primary, ...secondary]
    .filter((result) => {
      const key = `${normalizeLocationText(result.label)}|${result.lat.toFixed(3)}|${result.lon.toFixed(3)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 5);
}

function locationCandidateText(candidate: ArcGisGeocodeCandidate) {
  const attributes = candidate.attributes;
  return [
    candidate.address,
    attributes?.LongLabel,
    attributes?.ShortLabel,
    attributes?.PlaceName,
    attributes?.City,
    attributes?.Region,
    attributes?.RegionAbbr,
    attributes?.Country,
    attributes?.CountryCode,
  ]
    .filter(Boolean)
    .join(" ");
}

function locationPopularityScore(candidate: ArcGisGeocodeCandidate, query: string) {
  const normalizedQuery = normalizeLocationText(query);
  const attributes = candidate.attributes;
  const label = normalizeLocationText(attributes?.ShortLabel ?? attributes?.PlaceName ?? candidate.address ?? "");
  const placeName = normalizeLocationText(attributes?.PlaceName ?? attributes?.City ?? "");
  const detail = normalizeLocationText(locationCandidateText(candidate));
  const type = normalizeLocationText(`${attributes?.Addr_type ?? ""} ${attributes?.Type ?? ""}`);
  const rank = attributes?.PopRank ?? attributes?.Rank;
  let score = candidate.score ?? 0;

  if (label === normalizedQuery) score += 260;
  if (placeName === normalizedQuery) score += 220;
  if (label.startsWith(`${normalizedQuery} `)) score += 90;
  if (detail.includes(normalizedQuery)) score += 30;
  if (/\b(locality|city|municipality|populated place|metro area|admin1|admin2)\b/.test(type)) score += 90;
  if (/\b(point address|street address|street name|postal|poi)\b/.test(type)) score -= 80;
  if (detail.includes("united states") || detail.includes(" usa ")) score += 12;
  if (typeof rank === "number" && Number.isFinite(rank)) score += Math.max(0, 90 - rank);

  const hints = POPULAR_LOCATION_HINTS[normalizedQuery];
  if (hints) {
    const hintMatches = hints.filter((hint) => detail.includes(normalizeLocationText(hint))).length;
    if (hintMatches >= Math.min(2, hints.length)) score += 280 + hintMatches * 120;
  }

  return score;
}

function SpotlightSearchButton({
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
          "pointer-events-auto flex h-12 w-full items-center gap-3 rounded-full border px-4 text-left shadow-2xl backdrop-blur-xl transition-all",
          open
            ? "border-cyan-500/45 bg-bg-base/80 text-slate-100 shadow-[0_18px_60px_rgba(255,102,0,0.16)]"
            : "border-white/15 bg-white/[0.075] text-slate-300 shadow-[0_18px_60px_rgba(0,0,0,0.42)] hover:border-cyan-500/40 hover:bg-bg-base/72 hover:text-slate-100",
        )}
        title="Search"
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-white/10 bg-bg-base/55 text-cyan-400">
          <Search size={16} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">Search places, maps, panels, settings</span>
          <span className="block truncate text-[11px] text-slate-500">Location lookup and operator shortcuts</span>
        </span>
      </button>
    </div>
  );
}

function GlobalCommandPalette({
  open,
  bottomOffset,
  model,
  regions,
  devices,
  onClose,
  navigate,
  resetRightDock,
  pushRightDock,
  setBottomDockTab,
  setMapSearchTarget,
  setActiveDevice,
}: {
  open: boolean;
  bottomOffset: number;
  model: OperatorRuntimeModel;
  regions: Region[];
  devices: Device[];
  onClose: () => void;
  navigate: NavigateFunction;
  resetRightDock: () => void;
  pushRightDock: (route: RightDockRoute) => void;
  setBottomDockTab: (tab: BottomDockTabId) => void;
  setMapSearchTarget: (target: MapSearchTarget | null) => void;
  setActiveDevice: (id: string | null) => void;
}) {
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

  const openPanel = (route: RightDockRoute) => {
    navigate("/home");
    resetRightDock();
    if (route !== "root") pushRightDock(route);
  };

  const entries = useMemo<CommandEntry[]>(() => {
    const panelEntries: CommandEntry[] = [
      {
        id: "home",
        label: "Home",
        detail: "Map-first operator surface",
        group: "Pages",
        Icon: Home,
        action: () => {
          navigate("/home");
          resetRightDock();
        },
      },
      { id: "panel-maps", label: "Manage Maps", detail: "Map library, active map, edge cache", group: "Panels", Icon: MapIcon, action: () => openPanel("maps") },
      { id: "panel-vehicle", label: "Manage Vehicles", detail: "Vehicle config and runtime state", group: "Panels", Icon: Server, action: () => openPanel("vehicle") },
      { id: "panel-ground-control", label: "Ground Control", detail: "QGroundControl, Mission Planner, ArduPilot compatibility", group: "Panels", Icon: Radio, action: () => openPanel("ground-control") },
      { id: "panel-camera", label: "Manage Cameras", detail: "Camera config and vision pipeline", group: "Panels", Icon: Camera, action: () => openPanel("camera") },
      { id: "panel-calibration", label: "Calibration", detail: "Guided camera capture", group: "Panels", Icon: Camera, action: () => openPanel("calibration") },
      { id: "panel-flights", label: "View All Flights", detail: "Recordings, sync, playback evidence", group: "Panels", Icon: Archive, action: () => openPanel("flights") },
    ];

    const bottomEntries: CommandEntry[] = [
      { id: "tab-system-status", label: "System Status", detail: "Bottom panel", group: "Bottom Dock", Icon: Activity, action: () => { navigate("/home"); setBottomDockTab("system-status"); } },
      { id: "tab-diagnostics", label: "Diagnostics", detail: "Bottom panel", group: "Bottom Dock", Icon: Activity, action: () => { navigate("/home"); setBottomDockTab("diagnostics"); } },
      { id: "tab-parameters", label: "Parameters", detail: "Bottom panel", group: "Bottom Dock", Icon: SlidersHorizontal, action: () => { navigate("/home"); setBottomDockTab("parameters"); } },
      { id: "tab-messages", label: "Messages", detail: "Requires active device", group: "Bottom Dock", Icon: MessageSquare, disabled: !model.activeDevice, action: () => { navigate("/home"); setBottomDockTab("messages"); } },
      { id: "tab-ekf", label: "EKF Init", detail: "Requires active device", group: "Bottom Dock", Icon: Radio, disabled: !model.activeDevice, action: () => { navigate("/home"); setBottomDockTab("ekf-init"); } },
      { id: "tab-console", label: "Console", detail: "Requires active device", group: "Bottom Dock", Icon: Terminal, disabled: !model.activeDevice, action: () => { navigate("/home"); setBottomDockTab("console"); } },
    ];

    const settingsEntries: CommandEntry[] = [
      { id: "settings-general", label: "General Settings", detail: "Theme, profile, imagery keys", group: "Settings", Icon: SettingsIcon, action: () => openPanel("settings") },
      { id: "settings-device", label: "Device Settings", detail: "Connection, recording, device mode, SSH", group: "Settings", Icon: Server, action: () => openPanel("vehicle") },
      { id: "settings-ground-control", label: "Ground Control Settings", detail: "QGC, Mission Planner, ArduPilot compatibility", group: "Settings", Icon: Radio, action: () => openPanel("ground-control") },
      { id: "settings-mav", label: "MAV Settings", detail: "MAVProxy and VPS parameters", group: "Settings", Icon: Radio, action: () => openPanel("mav") },
      { id: "settings-org", label: "Account", detail: "Operator profile and organization", group: "Settings", Icon: UserRound, action: () => openPanel("account") },
      { id: "settings-diagnostics", label: "Diagnostics Settings", detail: "Remote support and security scan", group: "Settings", Icon: Activity, action: () => openPanel("diagnostics-settings") },
    ];

    const mapEntries = regions.slice(0, 10).map<CommandEntry>((region) => ({
      id: `map-${region.id}`,
      label: region.name,
      detail: region.lifecycle_state ? `Map - ${region.lifecycle_state}` : "Map",
      group: "Maps",
      keywords: `${region.output_path} ${region.location_label ?? ""}`,
      Icon: MapIcon,
      action: () => openPanel("maps"),
    }));

    const deviceEntries = devices.slice(0, 10).map<CommandEntry>((device) => ({
      id: `device-${device.id}`,
      label: device.name,
      detail: device.host ? `Device - ${device.host}` : "Device",
      group: "Devices",
      keywords: `${device.mavlink_endpoint ?? ""} ${device.remote_project_path ?? ""}`,
      Icon: Server,
      action: () => {
        setActiveDevice(device.id);
        openPanel("vehicle");
      },
    }));

    return [...panelEntries, ...bottomEntries, ...settingsEntries, ...mapEntries, ...deviceEntries];
  }, [devices, model.activeDevice, navigate, pushRightDock, regions, resetRightDock, setActiveDevice, setBottomDockTab]);

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
              <div className="flex items-center gap-2 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-600">
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
                  <span className="shrink-0 text-[10px] uppercase tracking-[0.08em] text-slate-600">Place</span>
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
                  <span className="shrink-0 text-[10px] uppercase tracking-[0.08em] text-slate-600">{entry.group}</span>
                </button>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

function RightDock({
  open,
  stack,
  model,
  regions,
  onOpenChange,
  pushRightDock,
  popRightDock,
  resetRightDock,
  setBottomDockTab,
}: {
  open: boolean;
  stack: RightDockRoute[];
  model: OperatorRuntimeModel;
  regions: Region[];
  onOpenChange: (open: boolean) => void;
  pushRightDock: (route: RightDockRoute) => void;
  popRightDock: () => void;
  resetRightDock: () => void;
  setBottomDockTab: (tab: BottomDockTabId) => void;
}) {
  const visibleStack = stack.filter((item) => item !== "root");
  const route = visibleStack[visibleStack.length - 1] ?? "maps";
  const routeActive = (nextRoute: RightDockRoute) =>
    route === nextRoute || (nextRoute === "camera" && route === "calibration");
  const openRoute = (nextRoute: RightDockRoute) => {
    if (open && routeActive(nextRoute)) {
      onOpenChange(false);
      return;
    }
    onOpenChange(true);
    resetRightDock();
    pushRightDock(nextRoute);
  };

  return (
    <aside className="flex h-full overflow-hidden rounded-lg border border-white/10 bg-bg-base/60 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl">
      <div className="flex h-full w-14 shrink-0 flex-col items-center bg-white/[0.035] py-2">
        <div className="flex flex-col gap-1">
          <SidebarRailButton Icon={MapIcon} label="Maps" active={open && route === "maps"} onClick={() => openRoute("maps")} />
          <SidebarRailButton Icon={Server} label="Device" active={open && route === "vehicle"} onClick={() => openRoute("vehicle")} />
          <SidebarRailButton Icon={Radio} label="Ground Control" active={open && route === "ground-control"} onClick={() => openRoute("ground-control")} />
          <SidebarRailButton Icon={Camera} label="Camera" active={open && (route === "camera" || route === "calibration")} onClick={() => openRoute("camera")} />
          <SidebarRailButton Icon={Archive} label="Flights" active={open && route === "flights"} onClick={() => openRoute("flights")} />
          <SidebarRailButton Icon={SettingsIcon} label="Settings" active={open && route === "settings"} onClick={() => openRoute("settings")} />
          <SidebarRailButton Icon={Radio} label="MAV Settings" active={open && route === "mav"} onClick={() => openRoute("mav")} />
          <SidebarRailButton Icon={Activity} label="Diagnostics Settings" active={open && route === "diagnostics-settings"} onClick={() => openRoute("diagnostics-settings")} />
          <SidebarRailButton Icon={UserRound} label="Account" active={open && route === "account"} onClick={() => openRoute("account")} />
        </div>
      </div>

      {open && (
        <section className="flex h-full w-[360px] flex-col overflow-hidden border-l border-white/10 bg-black/90">
          <div className="flex h-11 shrink-0 items-center gap-2 border-b border-white/10 px-3">
            {visibleStack.length > 1 && (
              <button type="button" onClick={popRightDock} className="operator-shell-button h-7 w-7 rounded-md">
                <ChevronLeft size={15} />
              </button>
            )}
            <div className="min-w-0 flex-1 truncate text-left text-sm font-semibold text-slate-100">
              {visibleStack.map((item, index) => (
                <span key={`${item}-${index}`}>
                  {index > 0 && <span className="mx-1 text-slate-700">/</span>}
                  <span className={index === visibleStack.length - 1 ? "text-slate-100" : "text-slate-500"}>
                    {DOCK_LABELS[item]}
                  </span>
                </span>
              ))}
              {visibleStack.length === 0 && DOCK_LABELS[route]}
            </div>
            <button type="button" onClick={() => onOpenChange(false)} className="operator-shell-button h-7 w-7 rounded-md" title="Collapse pane">
              <ChevronLeft size={15} />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-3">
            {route === "maps" && (
              <MapsPanel model={model} regions={regions} />
            )}

            {route === "vehicle" && (
              <DeviceSettingsPanel model={model} setBottomDockTab={setBottomDockTab} />
            )}

            {route === "ground-control" && (
              <GroundControlPanel model={model} setBottomDockTab={setBottomDockTab} />
            )}

            {route === "camera" && (
              <CameraPanel model={model} pushRightDock={pushRightDock} />
            )}

            {route === "calibration" && (
              <CalibrationPanel model={model} />
            )}

            {route === "flights" && (
              <FlightsPanel model={model} />
            )}

            {route === "settings" && (
              <GeneralSettingsPanel model={model} />
            )}

            {route === "mav" && (
              <MavSettingsPanel model={model} setBottomDockTab={setBottomDockTab} />
            )}

            {route === "diagnostics-settings" && (
              <DiagnosticsSettingsPanel model={model} setBottomDockTab={setBottomDockTab} />
            )}

            {route === "account" && (
              <AccountPanel />
            )}
          </div>
        </section>
      )}
    </aside>
  );
}

function SidebarRailButton({
  Icon,
  label,
  active,
  onClick,
}: {
  Icon: LucideIcon;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-10 w-10 items-center justify-center rounded-md border transition-colors",
        active
          ? "border-orange-500/75 bg-orange-500/18 text-orange-300"
          : "border-transparent text-slate-500 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-200",
      )}
      title={label}
    >
      <Icon size={17} />
    </button>
  );
}

type MapScope = "all" | "local" | "organization";

function isOrganizationMap(region: Region) {
  const marker = `${region.source ?? ""} ${region.output_path ?? ""} ${region.location_label ?? ""} ${region.active_bundle_path ?? ""}`.toLowerCase();
  return marker.includes("org://") || marker.includes("organization") || marker.includes("shared") || marker.includes("cloud");
}

function mapMatchesScope(region: Region, scope: MapScope) {
  if (scope === "all") return true;
  const org = isOrganizationMap(region);
  return scope === "organization" ? org : !org;
}

function MapLibraryCountButton({
  label,
  value,
  active,
  onClick,
}: {
  label: string;
  value: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-lg border p-3 text-left transition-colors",
        active
          ? "border-orange-500/65 bg-orange-500/14 text-orange-100"
          : "border-white/10 bg-bg-card text-slate-300 hover:border-orange-500/35 hover:bg-white/[0.045]",
      )}
    >
      <span className="block text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">{label}</span>
      <span className="mt-1 block font-mono text-xl font-semibold text-slate-100">{value}</span>
      <span className="mt-0.5 block truncate text-[10px] text-slate-600">click to browse</span>
    </button>
  );
}

function MapsPanel({ model, regions }: { model: OperatorRuntimeModel; regions: Region[] }) {
  const { updateRegion, removeRegion } = useAppStore();
  const [openLibrary, setOpenLibrary] = useState<MapScope | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<MapScope>("all");
  const localMaps = regions.filter((region) => !isOrganizationMap(region));
  const organizationMaps = regions.filter(isOrganizationMap);
  const visibleMaps = regions.filter((region) => {
    const normalized = query.trim().toLowerCase();
    const matchesQuery = !normalized || `${region.name} ${region.output_path} ${region.location_label ?? ""}`.toLowerCase().includes(normalized);
    return matchesQuery && mapMatchesScope(region, filter);
  });

  const openScope = (scope: MapScope) => {
    setOpenLibrary((current) => (current === scope ? null : scope));
    setFilter(scope);
    setQuery("");
  };

  const persistRegions = async (nextRegions: Region[]) => {
    try {
      await cmd.saveRegions(nextRegions);
    } catch (error) {
      console.warn("Failed to persist map library update", error);
    }
  };

  const renameMap = async (region: Region) => {
    const nextName = window.prompt("Rename map", region.name)?.trim();
    if (!nextName || nextName === region.name) return;
    const updated = { ...region, name: nextName };
    updateRegion(updated);
    await persistRegions(regions.map((item) => (item.id === region.id ? updated : item)));
  };

  const deleteMap = async (region: Region) => {
    if (!window.confirm(`Delete ${region.name}?`)) return;
    removeRegion(region.id);
    await persistRegions(regions.filter((item) => item.id !== region.id));
  };

  return (
    <PanelStack title="Maps" subtitle={model.activeMap?.areaName ?? "No map selected"}>
      <div className="grid grid-cols-2 gap-2">
        <MapLibraryCountButton
          label="Local Maps"
          value={localMaps.length}
          active={openLibrary === "local"}
          onClick={() => openScope("local")}
        />
        <MapLibraryCountButton
          label="Organization Maps"
          value={organizationMaps.length}
          active={openLibrary === "organization"}
          onClick={() => openScope("organization")}
        />
      </div>
      {openLibrary && (
        <div className="rounded-lg border border-white/10 bg-black/55 p-2">
          <div className="flex items-center gap-2 border-b border-white/10 pb-2">
            <Search size={14} className="text-slate-500" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="h-8 min-w-0 flex-1 bg-transparent text-xs text-slate-200 outline-none placeholder:text-slate-600"
              placeholder="Search saved maps..."
            />
          </div>
          <div className="mt-2 grid grid-cols-3 gap-1">
            {(["all", "local", "organization"] as MapScope[]).map((scope) => (
              <button
                key={scope}
                type="button"
                onClick={() => setFilter(scope)}
                className={cn(
                  "h-7 rounded-md border px-2 text-[10px] uppercase tracking-[0.08em] transition-colors",
                  filter === scope
                    ? "border-orange-500/65 bg-orange-500/15 text-orange-200"
                    : "border-white/10 text-slate-500 hover:text-slate-200",
                )}
              >
                {scope === "organization" ? "Org" : scope}
              </button>
            ))}
          </div>
          <div className="mt-2 max-h-64 overflow-y-auto">
            {visibleMaps.length === 0 ? (
              <EmptyLine text="No saved maps match" />
            ) : (
              visibleMaps.map((region) => (
                <div key={region.id} className="flex items-center gap-2 border-b border-white/5 px-2 py-2 last:border-b-0">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-slate-200">{region.name}</div>
                    <div className="truncate font-mono text-[10px] text-slate-600">{isOrganizationMap(region) ? "organization" : "local"} / {region.lifecycle_state ?? "saved"}</div>
                  </div>
                  <button type="button" onClick={() => renameMap(region)} className="operator-shell-button h-7 w-7 rounded-md" title="Rename map">
                    <Pencil size={13} />
                  </button>
                  <button type="button" onClick={() => deleteMap(region)} className="operator-shell-button h-7 w-7 rounded-md hover:text-red-300" title="Delete map">
                    <Trash2 size={13} />
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      )}
      {model.pendingMaps.length > 0 && (
        <>
          <SectionLabel>Pending</SectionLabel>
          {model.pendingMaps.slice(0, 4).map((map) => (
            <MapRow key={map.id} name={map.areaName} detail="request pending local build/upload" state={map.lifecycle} active={false} />
          ))}
        </>
      )}
      <StateCard
        label="Home Position"
        value={model.activeMap?.homePosition ? `${model.activeMap.homePosition.lat.toFixed(5)}, ${model.activeMap.homePosition.lon.toFixed(5)}` : "Not set"}
        detail={regions.length ? "stored on map record when available" : "create a map first"}
        tone={model.activeMap?.homePosition ? "green" : "muted"}
      />
      <div className="grid grid-cols-1 gap-2 pt-2">
        <PanelAction label="Create Map" detail="Use the map workflow backend when configured" disabled />
        <PanelAction label="Upload To Edge" detail="Unavailable until an edge device is connected" disabled />
        <PanelAction label="Set Active Map" detail="Requires an uploaded edge map" disabled />
      </div>
    </PanelStack>
  );
}

function DeviceSettingsPanel({
  model,
  setBottomDockTab,
}: {
  model: OperatorRuntimeModel;
  setBottomDockTab: (tab: BottomDockTabId) => void;
}) {
  const { devices, addDevice, updateDevice, setActiveDevice } = useAppStore();
  const device = model.activeDevice;
  const connected = model.edgeConnectionState === "online";
  const [deviceName, setDeviceName] = useState(device?.name ?? "Companion Compute");
  const [ipAddress, setIpAddress] = useState(device?.host ?? "");
  const [sshPort, setSshPort] = useState(String(device?.port ?? 22));
  const [companionUser, setCompanionUser] = useState(device?.username ?? "pi");
  const [password, setPassword] = useState(device?.auth?.type === "Password" ? device.auth.password : "");
  const [runtimePath, setRuntimePath] = useState(device?.remote_project_path ?? "");
  const [mavlinkEndpoint, setMavlinkEndpoint] = useState(device?.mavlink_endpoint ?? "");
  const [saving, setSaving] = useState(false);
  const [heartbeatChecking, setHeartbeatChecking] = useState(false);
  const [connectionMessage, setConnectionMessage] = useState<string | null>(null);
  const [edgeApiState, setEdgeApiState] = useState<"unknown" | "online" | "offline">("unknown");
  const [newDeviceOpen, setNewDeviceOpen] = useState(false);
  const [newDeviceIp, setNewDeviceIp] = useState("");
  const [newDeviceUsername, setNewDeviceUsername] = useState("pi");
  const [newDevicePassword, setNewDevicePassword] = useState("");
  const [newDeviceSaving, setNewDeviceSaving] = useState(false);
  const [newDeviceMode, setNewDeviceMode] = useState<"automatic" | "custom">("automatic");
  const [discovering, setDiscovering] = useState(false);
  const [discoveryCandidates, setDiscoveryCandidates] = useState<PiDiscoveryCandidate[]>(() => loadDiscoveryHistory());
  const [networkHints, setNetworkHints] = useState<LocalNetworkHint[]>([]);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);

  useEffect(() => {
    setDeviceName(device?.name ?? "Companion Compute");
    setIpAddress(device?.host ?? "");
    setSshPort(String(device?.port ?? 22));
    setCompanionUser(device?.username ?? "pi");
    setPassword(device?.auth?.type === "Password" ? device.auth.password : "");
    setRuntimePath(device?.remote_project_path ?? "");
    setMavlinkEndpoint(device?.mavlink_endpoint ?? "");
    setConnectionMessage(null);
    setEdgeApiState("unknown");
  }, [device?.auth, device?.host, device?.id, device?.mavlink_endpoint, device?.name, device?.port, device?.remote_project_path, device?.username]);

  useEffect(() => {
    if (!newDeviceOpen || newDeviceMode !== "automatic") return;
    cmd.localNetworkHints().then(setNetworkHints).catch(() => setNetworkHints([]));
  }, [newDeviceMode, newDeviceOpen]);

  const promptForPassword = () => {
    const nextPassword = window.prompt("Password for companion compute user", "");
    if (nextPassword === null) return;
    setPassword(nextPassword);
    setConnectionMessage(nextPassword ? "password staged" : "password cleared");
  };
  const edgeApiUrl = ipAddress.trim() ? `http://${ipAddress.trim()}:5000` : "";

  const saveConnection = async () => {
    const parsedPort = Number.parseInt(sshPort, 10);
    const nextDevice: Device = {
      ...(device ?? {
        id: `device-${Date.now()}`,
        kind: "pi5",
      }),
      name: deviceName.trim() || "Companion Compute",
      host: ipAddress.trim() || undefined,
      port: Number.isFinite(parsedPort) ? parsedPort : undefined,
      username: companionUser.trim() || undefined,
      auth: password ? { type: "Password", password } : device?.auth,
      remote_project_path: runtimePath.trim() || undefined,
      mavlink_endpoint: mavlinkEndpoint.trim() || undefined,
      autopilot: device?.autopilot ?? "px4",
      vision_pipeline: device?.vision_pipeline ?? "classical",
      feature_method: device?.feature_method ?? "orb",
    };
    const existing = devices.some((item) => item.id === nextDevice.id);
    const nextDevices = existing
      ? devices.map((item) => (item.id === nextDevice.id ? nextDevice : item))
      : [...devices, nextDevice];

    setSaving(true);
    setConnectionMessage(null);
    try {
      await cmd.saveDevices(nextDevices);
      if (existing) updateDevice(nextDevice);
      else addDevice(nextDevice);
      setActiveDevice(nextDevice.id);
      if (nextDevice.host) {
        try {
          const api = await cmd.edgeApiDevice(`http://${nextDevice.host}:5000`);
          setEdgeApiState(api.ok ? "online" : "offline");
          setConnectionMessage(api.ok ? `edge API online: ${api.hostname ?? nextDevice.host}` : "saved; edge API unavailable");
        } catch {
          setEdgeApiState("offline");
          setConnectionMessage("saved; edge API offline");
        }
      } else {
        setConnectionMessage("saved and selected");
      }
    } catch (error) {
      setConnectionMessage(String(error));
    } finally {
      setSaving(false);
    }
  };

  const addNewDeviceFromConnection = async ({
    host,
    port,
    username,
    password: nextPassword,
    name,
  }: {
    host: string;
    port: number;
    username: string;
    password?: string;
    name: string;
  }) => {
    if (!host) return;
    const existingDevice = devices.find((item) => item.kind === "pi5" && item.host === host && (item.port ?? 22) === port);
    const nextDevice: Device = {
      ...(existingDevice ?? {}),
      id: `device-${Date.now()}`,
      name,
      kind: "pi5",
      host,
      port,
      username,
      auth: nextPassword ? { type: "Password", password: nextPassword } : undefined,
      remote_project_path: `/home/${username}/Drone`,
      mavlink_endpoint: "serial:/dev/ttyACM0:921600",
      autopilot: "px4",
      vision_pipeline: "classical",
      feature_method: "orb",
    };
    const nextDevices = existingDevice
      ? devices.map((item) => (item.id === existingDevice.id ? { ...nextDevice, id: existingDevice.id } : item))
      : [...devices, nextDevice];
    const savedDevice = existingDevice ? { ...nextDevice, id: existingDevice.id } : nextDevice;

    setNewDeviceSaving(true);
    setConnectionMessage(null);
    try {
      await cmd.saveDevices(nextDevices);
      if (existingDevice) updateDevice(savedDevice);
      else addDevice(savedDevice);
      setActiveDevice(savedDevice.id);
      setNewDeviceOpen(false);
      setNewDeviceIp("");
      setNewDeviceUsername("pi");
      setNewDevicePassword("");
      try {
        const api = await cmd.edgeApiDevice(`http://${host}:5000`);
        setEdgeApiState(api.ok ? "online" : "offline");
        setConnectionMessage(api.ok ? `edge API online: ${api.hostname ?? host}` : "device saved; edge API unavailable");
      } catch {
        setEdgeApiState("offline");
        setConnectionMessage("device saved; edge API offline");
      }
    } catch (error) {
      setConnectionMessage(String(error));
    } finally {
      setNewDeviceSaving(false);
    }
  };

  const connectNewDevice = async () => {
    const host = newDeviceIp.trim();
    const username = newDeviceUsername.trim() || "pi";
    await addNewDeviceFromConnection({
      host,
      port: 22,
      username,
      password: newDevicePassword,
      name: `Companion ${host}`,
    });
  };

  const connectDiscoveredDevice = async (candidate: PiDiscoveryCandidate) => {
    const username = newDeviceUsername.trim() || "pi";
    await addNewDeviceFromConnection({
      host: candidateHost(candidate),
      port: candidate.port,
      username,
      password: newDevicePassword,
      name: candidateName(candidate),
    });
  };

  const runDiscovery = async () => {
    setDiscovering(true);
    setDiscoveryError(null);
    try {
      const seedHosts = devices
        .filter((item) => item.kind === "pi5" && item.host)
        .map((item) => item.host!)
        .concat(newDeviceIp.trim() ? [newDeviceIp.trim()] : []);
      const candidates = await cmd.discoverPiDevices(seedHosts, 22);
      const hints = await cmd.localNetworkHints().catch(() => networkHints);
      const next = mergeDiscoveryHistory(discoveryCandidates, candidates);
      setDiscoveryCandidates(next);
      setNetworkHints(hints);
      saveDiscoveryHistory(next);
    } catch (error) {
      setDiscoveryError(String(error));
    } finally {
      setDiscovering(false);
    }
  };

  const requestHeartbeat = async () => {
    if (!mavlinkEndpoint.trim()) {
      setConnectionMessage("MAVLink endpoint required for heartbeat");
      setBottomDockTab("system-status");
      return;
    }
    if (!edgeApiUrl) {
      setConnectionMessage("Edge API URL required for heartbeat");
      setBottomDockTab("system-status");
      return;
    }
    setHeartbeatChecking(true);
    setConnectionMessage("checking heartbeat");
    try {
      const result = await cmd.edgeApiMavlinkHeartbeat(edgeApiUrl, mavlinkEndpoint.trim(), 4);
      if (result.ok && result.connected) {
        setEdgeApiState("online");
        setConnectionMessage(`heartbeat ok: system ${result.target_system ?? "?"}`);
        setBottomDockTab("messages");
      } else {
        setConnectionMessage(result.message ?? "heartbeat not found");
        setBottomDockTab("system-status");
      }
    } catch (error) {
      setEdgeApiState("offline");
      setConnectionMessage(`edge API offline: ${String(error)}`);
      setBottomDockTab("system-status");
    } finally {
      setHeartbeatChecking(false);
    }
  };

  const discoverySummary = discoveryStatusSummary(discoveryCandidates, networkHints);

  return (
    <PanelStack
      title="Device Settings"
      subtitle={device?.name ?? "No device selected"}
      action={
        <button
          type="button"
          onClick={() => setNewDeviceOpen((current) => !current)}
          className="h-8 rounded-md border border-orange-500/70 bg-orange-500 px-3 text-xs font-semibold text-black shadow-[0_8px_28px_rgba(255,102,0,0.22)] transition-colors hover:bg-orange-400"
        >
          New Device+
        </button>
      }
    >
      {newDeviceOpen && (
        <div className="rounded-lg border border-orange-500/35 bg-black/70 p-3 shadow-[0_14px_44px_rgba(0,0,0,0.36)]">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-slate-100">New Device</div>
              <div className="text-xs text-slate-500">
                {newDeviceMode === "automatic" ? discoverySummary.label : "Add companion compute credentials"}
              </div>
            </div>
            <span className="rounded-full border border-orange-500/30 bg-orange-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-orange-200">
              Pi
            </span>
          </div>

          <div className="mb-3 grid rounded-full border border-white/10 bg-bg-base/80 p-1">
            <div className="grid grid-cols-2 gap-1">
              {(["automatic", "custom"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setNewDeviceMode(mode)}
                  className={cn(
                    "h-8 rounded-full text-xs font-semibold capitalize transition-colors",
                    newDeviceMode === mode
                      ? "bg-orange-500 text-black shadow-[0_8px_24px_rgba(255,102,0,0.22)]"
                      : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-200",
                  )}
                >
                  {mode}
                </button>
              ))}
            </div>
          </div>

          {newDeviceMode === "automatic" ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <CompactField label="Username" value={newDeviceUsername} onChange={setNewDeviceUsername} placeholder="pi" />
                <CompactField label="Password" value={newDevicePassword} onChange={setNewDevicePassword} placeholder="Password" secret />
              </div>
              <div className="rounded-lg border border-white/10 bg-bg-card p-3">
                <div className="flex items-center gap-3">
                  <Activity size={15} className={discoverySummary.status === "ready" ? "text-status-ready" : "text-orange-300"} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-semibold text-slate-200">{discoverySummary.label}</div>
                    <div className="truncate text-[10px] text-slate-500">{discoverySummary.detail}</div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={runDiscovery}
                  disabled={discovering}
                  className="mt-3 flex h-8 w-full items-center justify-center gap-2 rounded-md border border-orange-500/70 bg-orange-500 text-xs font-semibold text-black transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:opacity-45"
                >
                  {discovering ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                  {discovering ? "Scanning" : "Scan Network"}
                </button>
              </div>
              {discoveryError && (
                <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                  {discoveryError}
                </div>
              )}
              <div className="max-h-52 space-y-2 overflow-y-auto">
                {discoveryCandidates.length === 0 ? (
                  <EmptyLine text="No discovered devices yet" />
                ) : (
                  discoveryCandidates.map((candidate) => (
                    <div key={`${candidate.host}-${candidate.port}`} className="rounded-lg border border-white/10 bg-bg-card p-2">
                      <div className="flex items-center gap-2">
                        <Server size={14} className={candidate.ssh_open ? "text-status-ready" : "text-slate-500"} />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-xs font-semibold text-slate-200">{candidateName(candidate)}</div>
                          <div className="truncate font-mono text-[10px] text-slate-500">
                            {candidateHost(candidate)}:{candidate.port} / {candidate.ssh_open ? "ssh open" : "ssh closed"}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => connectDiscoveredDevice(candidate)}
                          disabled={newDeviceSaving}
                          className="h-7 rounded-md border border-orange-500/60 bg-orange-500 px-2 text-[11px] font-semibold text-black transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:opacity-45"
                        >
                          Connect
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <CompactField label="IP Address" value={newDeviceIp} onChange={setNewDeviceIp} placeholder="192.168.1.42" />
              <CompactField label="Username" value={newDeviceUsername} onChange={setNewDeviceUsername} placeholder="pi" />
              <CompactField label="Password" value={newDevicePassword} onChange={setNewDevicePassword} placeholder="Password" secret />
            </div>
          )}

          <div className="mt-3 grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => {
                setNewDeviceOpen(false);
                setNewDeviceIp("");
                setNewDeviceUsername("pi");
                setNewDevicePassword("");
              }}
              className="h-8 rounded-md border border-border bg-bg-card text-xs font-medium text-slate-400 transition-colors hover:border-white/20 hover:text-slate-100"
            >
              Cancel
            </button>
            {newDeviceMode === "custom" ? (
              <button
                type="button"
                onClick={connectNewDevice}
                disabled={newDeviceSaving || !newDeviceIp.trim()}
                className="h-8 rounded-md border border-orange-500/70 bg-orange-500 text-xs font-semibold text-black transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:opacity-45"
              >
                {newDeviceSaving ? "Connecting" : "Connect"}
              </button>
            ) : (
              <button
                type="button"
                onClick={runDiscovery}
                disabled={discovering}
                className="h-8 rounded-md border border-orange-500/70 bg-orange-500 text-xs font-semibold text-black transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:opacity-45"
              >
                {discovering ? "Scanning" : "Scan"}
              </button>
            )}
          </div>
        </div>
      )}
      <StateCard label="Connection" value={connected ? "Online" : "Offline"} detail={ipAddress || "no IP address"} tone={connected ? "green" : "muted"} />
      <SectionLabel>Connection</SectionLabel>
      <CompactField label="Device Name" value={deviceName} onChange={setDeviceName} placeholder="Companion Compute" />
      <CompactField label="IP Address" value={ipAddress} onChange={setIpAddress} placeholder="192.168.1.42" />
      <div className="grid grid-cols-2 gap-2">
        <CompactField label="SSH Port" value={sshPort} onChange={setSshPort} placeholder="22" />
        <div className="space-y-2">
          <CompactField label="Username" value={companionUser} onChange={setCompanionUser} placeholder="pi" />
          <button
            type="button"
            onClick={promptForPassword}
            className="h-8 w-full rounded-md border border-border bg-bg-card px-3 text-left text-xs font-medium text-slate-300 transition-colors hover:border-orange-500/35 hover:bg-white/[0.04] hover:text-slate-100"
          >
            Password
            <span className="float-right font-mono text-[10px] text-slate-600">{password ? "set" : "unset"}</span>
          </button>
        </div>
      </div>
      <CompactField label="Runtime Path" value={runtimePath} onChange={setRuntimePath} placeholder="/home/pi/Drone" />
      <CompactField label="MAVLink Endpoint" value={mavlinkEndpoint} onChange={setMavlinkEndpoint} placeholder="udp:14550" />
      <StatusRow label="Device URL" value={edgeApiUrl || "not configured"} healthy={Boolean(edgeApiUrl)} />
      <StatusRow label="Edge API" value={edgeApiState === "online" ? "online" : edgeApiState === "offline" ? "offline" : "not checked"} healthy={edgeApiState === "online"} />
      <PanelAction label={saving ? "Connecting" : "Connect"} detail={connectionMessage ?? "Save and select this companion compute"} onClick={saveConnection} disabled={saving || !ipAddress.trim()} />
      <PanelAction label={heartbeatChecking ? "Checking Heartbeat" : "Heartbeat"} detail={mavlinkEndpoint.trim() ? "Probe MAVLink through Edge API" : "Set MAVLink endpoint first"} onClick={requestHeartbeat} disabled={heartbeatChecking || !mavlinkEndpoint.trim()} />
      <PanelAction label="Recording On Boot" detail={connected ? "Runtime service not configured" : "Connect device to configure"} disabled />
      <PanelAction label="Storage" detail={connected ? "No storage scan attached" : "Device offline"} disabled />
      <PanelAction label="System Status" detail="Open bottom status tab" onClick={() => setBottomDockTab("system-status")} />
    </PanelStack>
  );
}

function GroundControlPanel({
  model,
  setBottomDockTab,
}: {
  model: OperatorRuntimeModel;
  setBottomDockTab: (tab: BottomDockTabId) => void;
}) {
  const { devices, updateDevice } = useAppStore();
  const device = model.activeDevice;
  const edgeApiUrl = device?.host ? `http://${device.host}:5000` : "";
  const [qgcStatus, setQgcStatus] = useState<EdgeApiQGroundControlStatus | null>(null);
  const [missionPlannerStatus, setMissionPlannerStatus] = useState<EdgeApiMissionPlannerStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [launching, setLaunching] = useState<"qgc" | "mission-planner" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const setAutopilot = async (autopilot: NonNullable<Device["autopilot"]>) => {
    if (!device) {
      setMessage("Select a device before changing autopilot compatibility");
      return;
    }
    const nextDevice: Device = { ...device, autopilot };
    const nextDevices = devices.map((item) => (item.id === device.id ? nextDevice : item));
    await cmd.saveDevices(nextDevices);
    updateDevice(nextDevice);
    setMessage(`Autopilot compatibility set to ${autopilot === "ardupilot" ? "ArduPilot" : "PX4"}`);
  };

  const refreshGroundControl = async () => {
    if (!edgeApiUrl) {
      setMessage("Connect a companion computer before checking ground-control tools");
      return;
    }
    setChecking(true);
    setMessage("checking ground-control tools");
    try {
      const [qgc, missionPlanner] = await Promise.all([
        cmd.edgeApiQGroundControlStatus(edgeApiUrl),
        cmd.edgeApiMissionPlannerStatus(edgeApiUrl),
      ]);
      setQgcStatus(qgc);
      setMissionPlannerStatus(missionPlanner);
      setMessage("ground-control status refreshed");
    } catch (error) {
      setMessage(`Ground-control API unavailable: ${String(error)}`);
    } finally {
      setChecking(false);
    }
  };

  const launchQGroundControl = async () => {
    if (!edgeApiUrl) {
      setMessage("Connect a companion computer before launching QGroundControl");
      return;
    }
    setLaunching("qgc");
    setMessage("launching QGroundControl");
    try {
      const result = await cmd.edgeApiQGroundControlLaunch(edgeApiUrl, true);
      if (result.status) setQgcStatus(result.status);
      setMessage(result.ok && result.launched ? `QGroundControl launched${result.pid ? `: PID ${result.pid}` : ""}` : result.message ?? "QGroundControl launch did not start");
    } catch (error) {
      setMessage(`QGroundControl launch failed: ${String(error)}`);
    } finally {
      setLaunching(null);
    }
  };

  const launchMissionPlanner = async () => {
    if (!edgeApiUrl) {
      setMessage("Connect a companion computer before launching Mission Planner");
      return;
    }
    setLaunching("mission-planner");
    setMessage("launching Mission Planner");
    try {
      const result = await cmd.edgeApiMissionPlannerLaunch(edgeApiUrl, true);
      if (result.status) setMissionPlannerStatus(result.status);
      setMessage(result.ok && result.launched ? `Mission Planner launched${result.pid ? `: PID ${result.pid}` : ""}` : result.message ?? "Mission Planner launch did not start");
    } catch (error) {
      setMessage(`Mission Planner launch failed: ${String(error)}`);
    } finally {
      setLaunching(null);
    }
  };

  const qgcInstalled = Boolean(qgcStatus?.installed);
  const qgcCanLaunch = Boolean(qgcStatus?.launch_available);
  const missionPlannerInstalled = Boolean(missionPlannerStatus?.installed);
  const missionPlannerCanLaunch = Boolean(missionPlannerStatus?.launch_available);
  const qgcDisplayLabel = displayLabel(qgcStatus?.display);
  const missionPlannerDisplayLabel = displayLabel(missionPlannerStatus?.display);
  const serialOwner =
    qgcStatus?.serial_users ||
    missionPlannerStatus?.serial_users ||
    "";

  return (
    <PanelStack title="Ground Control" subtitle={device?.name ?? "No active device"}>
      <StateCard
        label="Autopilot"
        value={device?.autopilot === "ardupilot" ? "ArduPilot" : "PX4"}
        detail={device?.mavlink_endpoint ?? "MAVLink endpoint not configured"}
        tone={device ? "orange" : "muted"}
      />
      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => setAutopilot("px4")}
          disabled={!device}
          className={cn(
            "h-9 rounded-md border px-3 text-xs font-semibold transition-colors",
            device?.autopilot !== "ardupilot"
              ? "border-orange-500/70 bg-orange-500 text-black"
              : "border-border bg-bg-card text-slate-400 hover:border-orange-500/35 hover:text-slate-100",
            !device && "cursor-not-allowed opacity-50",
          )}
        >
          PX4
        </button>
        <button
          type="button"
          onClick={() => setAutopilot("ardupilot")}
          disabled={!device}
          className={cn(
            "h-9 rounded-md border px-3 text-xs font-semibold transition-colors",
            device?.autopilot === "ardupilot"
              ? "border-orange-500/70 bg-orange-500 text-black"
              : "border-border bg-bg-card text-slate-400 hover:border-orange-500/35 hover:text-slate-100",
            !device && "cursor-not-allowed opacity-50",
          )}
        >
          ArduPilot
        </button>
      </div>

      <SectionLabel>Tools</SectionLabel>
      <StateCard
        label="QGroundControl"
        value={qgcStatus ? (qgcInstalled ? (qgcStatus.running ? "Running" : "Installed") : "Not Found") : "Unknown"}
        detail={qgcStatus?.executable_path ?? qgcStatus?.appimage_path ?? "refresh to inspect device"}
        tone={qgcInstalled ? "orange" : "muted"}
      />
      <StateCard
        label="Mission Planner"
        value={missionPlannerStatus ? (missionPlannerInstalled ? (missionPlannerStatus.running ? "Running" : "Installed") : "Not Found") : "Unknown"}
        detail={missionPlannerStatus?.executable_path ?? missionPlannerStatus?.install_path ?? "Windows native / Linux Mono"}
        tone={missionPlannerInstalled ? "orange" : "muted"}
      />
      <StatusRow label="QGC Display" value={qgcDisplayLabel} healthy={Boolean(qgcStatus?.display?.available)} />
      <StatusRow label="Mission Planner Display" value={missionPlannerDisplayLabel} healthy={Boolean(missionPlannerStatus?.display?.available)} />
      <StatusRow label="Serial owner" value={serialOwner ? "in use" : "not reported"} healthy={!serialOwner} />
      <PanelAction
        label={checking ? "Checking GCS" : "Refresh GCS"}
        detail={message ?? "Detect QGroundControl, Mission Planner, display, and serial owner"}
        onClick={refreshGroundControl}
        disabled={checking || !edgeApiUrl}
      />
      <PanelAction
        label={launching === "qgc" ? "Launching QGC" : "Launch QGroundControl"}
        detail={qgcCanLaunch ? "Stops telemetry bridge before launch" : qgcStatus?.message ?? "Refresh status first"}
        onClick={launchQGroundControl}
        disabled={launching !== null || !qgcCanLaunch}
      />
      <PanelAction
        label={launching === "mission-planner" ? "Launching Mission Planner" : "Launch Mission Planner"}
        detail={missionPlannerCanLaunch ? "Linux launch uses Mono; Windows native remains recommended" : missionPlannerStatus?.message ?? "Refresh status first"}
        onClick={launchMissionPlanner}
        disabled={launching !== null || !missionPlannerCanLaunch}
      />

      <SectionLabel>Compatibility</SectionLabel>
      <StatusRow label="QGroundControl" value="PX4 and ArduPilot MAVLink" healthy />
      <StatusRow label="Mission Planner" value="ArduPilot primary GCS" healthy={device?.autopilot === "ardupilot"} />
      <StatusRow label="Plan files" value=".plan import plus ArduPilot waypoint path" healthy />
      <PanelAction label="MAVLink Parameters" detail="Open bottom parameter dock" onClick={() => setBottomDockTab("parameters")} />
      <PanelAction label="MAVLink Messages" detail="Open bottom message stream" onClick={() => setBottomDockTab("messages")} disabled={!device} />
    </PanelStack>
  );
}

function CameraPanel({
  model,
  pushRightDock,
}: {
  model: OperatorRuntimeModel;
  pushRightDock: (route: RightDockRoute) => void;
}) {
  const device = model.activeDevice;
  return (
    <PanelStack title="Camera" subtitle="Vision pipeline">
      <StatusRow label="Pipeline" value={device?.vision_pipeline ?? "classical default"} healthy />
      <StatusRow label="Feature method" value={device?.feature_method?.toUpperCase() ?? "ORB"} healthy />
      <StatusRow label="Camera profile" value={device?.camera_profile ?? "not selected"} healthy={Boolean(device?.camera_profile)} />
      <StatusRow label="Export state" value="No live camera export listener in browser fallback" healthy={false} />
      <PanelAction label="Create Camera Config" detail="Camera config editor is not attached to this shell yet" disabled />
      <PanelAction label="Calibration" detail="Open calibration drill-down" onClick={() => pushRightDock("calibration")} />
    </PanelStack>
  );
}

function CalibrationPanel({ model }: { model: OperatorRuntimeModel }) {
  const device = model.activeDevice;
  const [capturing, setCapturing] = useState(false);
  const [capturePreview, setCapturePreview] = useState<string | null>(null);
  const [capturePath, setCapturePath] = useState<string | null>(null);
  const [captureMessage, setCaptureMessage] = useState<string | null>(null);
  const [captureOk, setCaptureOk] = useState(false);
  const hasCaptureConfig = Boolean(
    device?.host
    && device?.username
    && device?.auth
    && device?.remote_project_path,
  );

  const startCaptureTest = async () => {
    if (!device?.host || !device?.username || !device?.auth || !device?.remote_project_path) {
      setCaptureOk(false);
      setCaptureMessage("Select a device with host, username, auth, and runtime path first.");
      return;
    }
    setCapturing(true);
    setCaptureOk(false);
    setCaptureMessage("capturing frame");
    try {
      const frame = await cmd.sshCaptureCameraFrame(
        device.host,
        device.port ?? 22,
        device.username,
        device.auth,
        device.remote_project_path,
        960,
        720,
        1000,
      );
      setCapturePreview(`data:${frame.mime_type};base64,${frame.base64_data}`);
      setCapturePath(frame.remote_path);
      setCaptureOk(true);
      const output = [frame.stdout, frame.stderr].filter(Boolean).join(" ").trim();
      setCaptureMessage(output ? `captured ${frame.remote_path}: ${output}` : `captured ${frame.remote_path}`);
    } catch (error) {
      setCapturePreview(null);
      setCapturePath(null);
      setCaptureOk(false);
      setCaptureMessage(String(error));
    } finally {
      setCapturing(false);
    }
  };

  return (
    <PanelStack title="Calibration" subtitle="Guided capture">
      <StatusRow label="Device" value={device?.name ?? "No active device"} healthy={Boolean(device)} />
      <StatusRow label="Capture set" value={captureOk ? "Frame captured" : capturing ? "Capturing" : "Not started"} healthy={captureOk} />
      <StatusRow label="Intrinsics" value="Manual / pending capture" healthy={false} />
      <StatusRow label="Distortion model" value="Use camera pipeline defaults" healthy={false} />
      <PanelAction
        label={capturing ? "Capturing" : "Start Capture"}
        detail={hasCaptureConfig ? "Capture a test frame over SSH" : "Connect a configured desktop device first"}
        onClick={startCaptureTest}
        disabled={capturing || !hasCaptureConfig}
      />
      {capturePreview && (
        <div className="overflow-hidden rounded-lg border border-border bg-bg-card">
          <img src={capturePreview} alt="Latest camera calibration capture" className="aspect-video w-full object-cover" />
          <div className="border-t border-border px-3 py-2 font-mono text-[10px] text-slate-500">
            {capturePath ?? "camera-preview/latest.jpg"}
          </div>
        </div>
      )}
      {captureMessage && (
        <div
          className={cn(
            "whitespace-pre-wrap break-words rounded-lg border px-3 py-2 text-xs",
            captureOk
              ? "border-status-ready/30 bg-status-ready/10 text-status-ready"
              : "border-status-warning/30 bg-status-warning/10 text-status-warning",
          )}
        >
          {captureMessage}
        </div>
      )}
    </PanelStack>
  );
}

function FlightsPanel({ model }: { model: OperatorRuntimeModel }) {
  return (
    <PanelStack title="Flights" subtitle="Logs and playback">
      <div className="grid grid-cols-2 gap-2">
        <StateCard label="On Device" value={model.activeDevice ? "Ready" : "Offline"} detail="requires runtime sync" tone={model.activeDevice ? "green" : "muted"} />
        <StateCard label="Local" value={model.flights.length} detail="indexed records" tone={model.flights.length ? "orange" : "muted"} />
      </div>
      <PanelAction label="Sync Recordings" detail="Requires edge recording service" disabled />
      <StatusRow label="GPS vs vision" value="Read from support bundle summaries" healthy={false} />
      <StatusRow label="Notes" value="Stored with local flight records when present" healthy={false} />
      <StatusRow label="MCAP export" value="Shown when bundle evidence exists" healthy={false} />
    </PanelStack>
  );
}

function profileWithDefaults(profile: Profile | null): Profile {
  return {
    accent_color: "#FF6600",
    email: "",
    name: "Operator",
    onboarding_complete: true,
    org: "Drone Vision Nav",
    ...profile,
  };
}

function GeneralSettingsPanel({ model }: { model: OperatorRuntimeModel }) {
  const { profile, setProfile } = useAppStore();
  const resolvedProfile = profileWithDefaults(profile);
  const [mapboxKey, setMapboxKey] = useState(resolvedProfile.mapbox_key ?? "");
  const [bingKey, setBingKey] = useState(resolvedProfile.bing_key ?? "");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    setMapboxKey(resolvedProfile.mapbox_key ?? "");
    setBingKey(resolvedProfile.bing_key ?? "");
  }, [resolvedProfile.bing_key, resolvedProfile.mapbox_key]);

  const saveKeys = async () => {
    setSaving(true);
    setMessage(null);
    const updated: Profile = {
      ...resolvedProfile,
      mapbox_key: mapboxKey.trim() || undefined,
      bing_key: bingKey.trim() || undefined,
    };
    try {
      await cmd.saveProfile(updated);
      setProfile(updated);
      setMessage("saved");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <PanelStack title="Settings" subtitle="General app controls">
      <div className="grid grid-cols-2 gap-2">
        <StateCard label="Device" value={model.activeDevice?.name ?? "None"} detail="active target" tone={model.activeDevice ? "green" : "muted"} />
        <StateCard label="Map" value={model.activeMap?.areaName ?? "None"} detail="active map" tone={model.activeMap ? "orange" : "muted"} />
      </div>
      <SectionLabel>Theme</SectionLabel>
      <SettingLine label="Operator mode" detail="Operator-grade dark interface">
        <StatusPill tone="ready">Dark</StatusPill>
      </SettingLine>
      <SectionLabel>Imagery API Keys</SectionLabel>
      <CompactField label="Mapbox API Key" value={mapboxKey} onChange={setMapboxKey} placeholder="pk.eyJ1..." secret />
      <CompactField label="Bing Maps API Key" value={bingKey} onChange={setBingKey} placeholder="Bing key..." secret />
      <PanelAction label={saving ? "Saving Keys" : "Save Keys"} detail={message ?? "Persist imagery keys locally"} onClick={saveKeys} disabled={saving} />
      <SectionLabel>Parameter File</SectionLabel>
      <SettingLine label="Local params YAML" detail="Loaded by the full settings backend">
        <StatusPill tone="muted">Not loaded</StatusPill>
      </SettingLine>
    </PanelStack>
  );
}

function MavSettingsPanel({
  model,
  setBottomDockTab,
}: {
  model: OperatorRuntimeModel;
  setBottomDockTab: (tab: BottomDockTabId) => void;
}) {
  const endpoint = model.activeDevice?.mavlink_endpoint;
  const connected = Boolean(endpoint);

  return (
    <PanelStack title="MAV Settings" subtitle="MAVLink routing and VPS parameters">
      <StatusRow label="Endpoint" value={endpoint ?? "not configured"} healthy={connected} />
      <StatusRow label="Primary route" value={connected ? "available" : "connect device to configure"} healthy={connected} />
      <StatusRow label="Heartbeat" value={connected ? "waiting for stream" : "offline"} healthy={false} />
      <PanelAction label="Parameters" detail="Open bottom parameters dock" onClick={() => setBottomDockTab("parameters")} />
      <PanelAction label="Messages" detail={connected ? "Open message stream" : "Requires active MAVLink endpoint"} onClick={() => setBottomDockTab("messages")} disabled={!connected} />
      <PanelAction label="Console" detail={connected ? "Open command console" : "Requires active MAVLink endpoint"} onClick={() => setBottomDockTab("console")} disabled={!connected} />
    </PanelStack>
  );
}

function DiagnosticsSettingsPanel({
  model,
  setBottomDockTab,
}: {
  model: OperatorRuntimeModel;
  setBottomDockTab: (tab: BottomDockTabId) => void;
}) {
  const connected = model.edgeConnectionState === "online";
  const host = model.activeDevice?.host;

  return (
    <PanelStack title="Diagnostics Settings" subtitle="Remote support and service evidence">
      <div className="grid grid-cols-2 gap-2">
        <StateCard label="Edge API" value={connected ? "Live" : "Offline"} detail={host ?? "no host"} tone={connected ? "green" : "muted"} />
        <StateCard label="Runtime" value={model.activeDevice?.remote_project_path ? "Path Set" : "No Path"} detail="service root" tone={model.activeDevice?.remote_project_path ? "orange" : "muted"} />
      </div>
      <SectionLabel>Support</SectionLabel>
      <SettingLine label="Remote support" detail={host ? `Device IP ${host}` : "Device unavailable"}>
        <StatusPill tone={connected ? "warning" : "muted"}>{connected ? "Stopped" : "Offline"}</StatusPill>
      </SettingLine>
      <SettingLine label="Lockdown diagnostics" detail="Security scan and cleanup">
        <StatusPill tone="muted">Not run</StatusPill>
      </SettingLine>
      <PanelAction label="Diagnostics Dock" detail="Open bottom diagnostics output" onClick={() => setBottomDockTab("diagnostics")} />
      <PanelAction label="Enable Remote Support" detail="Requires connected edge runtime" disabled />
      <PanelAction label="Delete All Logs" detail="Disabled until lockdown scan completes" disabled />
    </PanelStack>
  );
}

function AccountPanel() {
  const { profile, setProfile, regions } = useAppStore();
  const resolvedProfile = profileWithDefaults(profile);
  const [name, setName] = useState(resolvedProfile.name);
  const [email, setEmail] = useState(resolvedProfile.email);
  const [org, setOrg] = useState(resolvedProfile.org);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const organizationMaps = regions.filter(isOrganizationMap);

  useEffect(() => {
    setName(resolvedProfile.name);
    setEmail(resolvedProfile.email);
    setOrg(resolvedProfile.org);
  }, [resolvedProfile.email, resolvedProfile.name, resolvedProfile.org]);

  const saveAccount = async () => {
    setSaving(true);
    setMessage(null);
    const updated: Profile = {
      ...resolvedProfile,
      email: email.trim(),
      name: name.trim() || "Operator",
      org: org.trim() || "Drone Vision Nav",
    };
    try {
      await cmd.saveProfile(updated);
      setProfile(updated);
      setMessage("saved");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <PanelStack title="Account" subtitle={resolvedProfile.org || "Organization not set"}>
      <div className="flex items-center gap-3 rounded-lg border border-white/10 bg-bg-card p-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-orange-500/35 bg-orange-500/10 text-orange-200">
          <UserRound size={18} />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold text-slate-100">{resolvedProfile.name}</span>
          <span className="block truncate text-xs text-slate-500">{resolvedProfile.email || "No email set"}</span>
        </span>
      </div>
      <CompactField label="Name" value={name} onChange={setName} placeholder="Operator name" />
      <CompactField label="Email" value={email} onChange={setEmail} placeholder="operator@example.com" />
      <CompactField label="Organization" value={org} onChange={setOrg} placeholder="Organization name" />
      <PanelAction label={saving ? "Saving Account" : "Save Account"} detail={message ?? "Persist operator and organization"} onClick={saveAccount} disabled={saving} />
      <SectionLabel>Organization</SectionLabel>
      <div className="rounded-lg border border-white/10 bg-bg-card p-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
          <Building2 size={15} className="text-orange-300" />
          {org || "Drone Vision Nav"}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <StateCard label="Org Maps" value={organizationMaps.length} detail="shared library" tone={organizationMaps.length ? "orange" : "muted"} />
          <StateCard label="Recordings" value="0" detail="cloud sync off" tone="muted" />
        </div>
      </div>
      <PanelAction label="Create Organization" detail="Cloud sharing is not configured in this build" disabled />
    </PanelStack>
  );
}

function PanelStack({
  title,
  subtitle,
  children,
  action,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-100">{title}</div>
          <div className="truncate text-xs text-slate-500">{subtitle}</div>
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function PanelAction({
  label,
  detail,
  onClick,
  disabled = false,
}: {
  label: string;
  detail: string;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex w-full items-center justify-between gap-3 rounded-md border border-border bg-bg-card px-3 py-2 text-left",
        disabled
          ? "cursor-not-allowed opacity-55"
          : "hover:border-cyan-500/45 hover:bg-cyan-500/5",
      )}
    >
      <span className="min-w-0">
        <span className="block truncate text-sm text-slate-100">{label}</span>
        <span className="block truncate text-xs text-slate-500">{detail}</span>
      </span>
      <ChevronRight size={14} className="text-slate-500" />
    </button>
  );
}

function SettingLine({ label, detail, children }: { label: string; detail: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-bg-card px-3 py-2">
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium text-slate-100">{label}</span>
        <span className="block truncate text-xs text-slate-500">{detail}</span>
      </span>
      <span className="shrink-0">{children}</span>
    </div>
  );
}

function CompactField({
  label,
  value,
  onChange,
  placeholder,
  secret = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  secret?: boolean;
}) {
  return (
    <label className="block rounded-md border border-border bg-bg-card px-3 py-2">
      <span className="block text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">{label}</span>
      <input
        type={secret ? "password" : "text"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="mt-1 h-7 w-full bg-transparent text-sm text-slate-100 outline-none placeholder:text-slate-700"
      />
    </label>
  );
}

function StatusPill({ tone, children }: { tone: "ready" | "warning" | "muted"; children: ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]",
        tone === "ready" && "border-status-ready/30 bg-status-ready/10 text-status-ready",
        tone === "warning" && "border-status-warning/30 bg-status-warning/10 text-status-warning",
        tone === "muted" && "border-white/10 bg-white/[0.035] text-slate-500",
      )}
    >
      {children}
    </span>
  );
}

function displayLabel(display?: { available: boolean; display?: string | null; wayland_display?: string | null }) {
  if (!display) return "not checked";
  if (!display.available) return "no display";
  return display.wayland_display || display.display || "available";
}

function StatusRow({ label, value, healthy }: { label: string; value: string; healthy: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-bg-card px-3 py-2">
      <span className="text-xs text-slate-500">{label}</span>
      <span className={cn("truncate text-right font-mono text-xs", healthy ? "text-status-ready" : "text-status-warning")}>{value}</span>
    </div>
  );
}

function StateCard({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string | number;
  detail: string;
  tone: "orange" | "green" | "muted";
}) {
  return (
    <div className="rounded-lg border border-border bg-bg-card p-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">{label}</div>
      <div className={cn("mt-1 truncate font-mono text-sm font-semibold", tone === "green" ? "text-status-ready" : tone === "orange" ? "text-cyan-500" : "text-slate-400")}>{value}</div>
      <div className="mt-0.5 truncate text-xs text-slate-500">{detail}</div>
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="pt-2 text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-500">{children}</div>;
}

function EmptyLine({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-border bg-bg-card/60 px-3 py-3 text-center text-xs text-slate-500">{text}</div>;
}

function MapRow({ name, detail, state, active }: { name: string; detail: string; state: string; active: boolean }) {
  return (
    <div className={cn("rounded-lg border bg-bg-card p-3", active ? "border-cyan-500/45" : "border-border")}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-medium text-slate-100">{name}</span>
        <LifecycleBadge value={active ? "active" : state} />
      </div>
      <div className="mt-1 truncate font-mono text-[11px] text-slate-500">{detail}</div>
    </div>
  );
}

function LifecycleBadge({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const ready = normalized === "active" || normalized === "uploaded" || normalized === "built" || normalized === "local";
  const failed = normalized === "failed";
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase",
        ready && "border-status-ready/30 bg-status-ready/10 text-status-ready",
        failed && "border-status-critical/30 bg-red-500/10 text-status-critical",
        !ready && !failed && "border-status-warning/30 bg-yellow-500/10 text-status-warning",
      )}
    >
      {value}
    </span>
  );
}

function BottomDock({
  open,
  tab,
  model,
  onOpenChange,
  onTabChange,
}: {
  open: boolean;
  tab: BottomDockTabId;
  model: OperatorRuntimeModel;
  onOpenChange: (open: boolean) => void;
  onTabChange: (tab: BottomDockTabId) => void;
}) {
  const visibleTabs = BOTTOM_TABS.filter((item) => !item.requiresDevice || model.activeDevice);
  const effectiveTab = visibleTabs.some((item) => item.id === tab) ? tab : "system-status";
  const activeTab = visibleTabs.find((item) => item.id === effectiveTab) ?? visibleTabs[0];
  const ActiveTabIcon = activeTab?.Icon;
  const rows = dockRows(effectiveTab, model);

  return (
    <section
      className={cn(
        "absolute bottom-3 right-3 z-[1200] flex items-stretch justify-end transition-[width,height] duration-200",
        open ? "w-[calc(100%-24px)]" : "w-14",
      )}
      style={{ height: open ? 260 : Math.min(360, visibleTabs.length * 44 + 8) }}
    >
      {open && (
        <div className="h-full min-w-0 flex-1 overflow-hidden rounded-l-lg border border-r-0 border-white/10 bg-black/80 shadow-[0_18px_70px_rgba(0,0,0,0.48)] ring-1 ring-white/5 backdrop-blur-2xl">
          <div className="flex h-12 items-center gap-2 border-b border-white/10 px-4">
            {ActiveTabIcon && <ActiveTabIcon size={15} className="text-orange-300" />}
            <span className="text-sm font-semibold text-slate-100">{activeTab?.label ?? "Status"}</span>
            <span className="ml-auto font-data-mono text-[10px] uppercase tracking-[0.12em] text-slate-600">
              {effectiveTab}
            </span>
          </div>
          <div className="h-[calc(100%-48px)] overflow-y-auto p-4 font-mono text-xs">
            {rows.map((row, index) => (
              <div key={`${row.label}-${index}`} className="flex gap-4 rounded-md border border-transparent px-3 py-1.5 hover:border-orange-500/35 hover:bg-white/[0.04]">
                <span className="w-[112px] shrink-0 text-slate-600">[{row.label}]</span>
                <span className={cn(row.tone === "ready" && "text-status-ready", row.tone === "warning" && "text-status-warning", row.tone === "critical" && "text-status-critical", row.tone === "active" && "text-cyan-300", row.tone === "muted" && "text-slate-500")}>
                  {row.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div
        className={cn(
          "flex h-full w-14 shrink-0 flex-col items-center gap-1 border border-white/10 bg-bg-base/60 p-1 shadow-[0_18px_70px_rgba(0,0,0,0.48)] ring-1 ring-white/5 backdrop-blur-xl",
          open ? "rounded-r-lg" : "rounded-lg",
        )}
      >
        {visibleTabs.map(({ id, label, Icon }) => {
          return (
            <button
              key={id}
              type="button"
              onClick={() => {
                if (open && effectiveTab === id) {
                  onOpenChange(false);
                  return;
                }
                onTabChange(id);
                onOpenChange(true);
              }}
              className={cn(
                "flex h-10 w-10 items-center justify-center rounded-md border text-xs transition-colors",
                open && effectiveTab === id
                  ? "border-orange-500/75 bg-orange-500/18 text-orange-300"
                  : "border-transparent text-slate-500 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-300",
              )}
              title={label}
            >
              <Icon size={14} />
            </button>
          );
        })}
      </div>
    </section>
  );
}

function dockRows(tab: BottomDockTabId, model: OperatorRuntimeModel) {
  const endpoint = model.activeDevice?.mavlink_endpoint ?? "not configured";
  const mapName = model.activeMap?.areaName ?? "none selected";
  if (tab === "system-status") {
    return [
      { label: "EDGE_API", value: model.edgeConnectionState === "online" ? "live" : "unavailable", tone: model.edgeConnectionState === "online" ? "ready" : "warning" },
      { label: "DEVICE", value: model.activeDevice?.name ?? "no active device", tone: model.activeDevice ? "ready" : "warning" },
      { label: "RUNTIME", value: model.activeDevice?.remote_project_path ?? "runtime path not configured", tone: model.activeDevice?.remote_project_path ? "ready" : "warning" },
      { label: "CAMERA", value: model.activeDevice?.camera_profile ?? "unavailable", tone: model.activeDevice?.camera_profile ? "ready" : "muted" },
      { label: "MAP", value: mapName, tone: model.activeMap ? "active" : "warning" },
    ];
  }
  if (tab === "parameters") {
    return [
      { label: "DEVICE", value: model.activeDevice?.name ?? "none", tone: model.activeDevice ? "ready" : "warning" },
      { label: "MAV_ENDPOINT", value: endpoint, tone: model.activeDevice?.mavlink_endpoint ? "ready" : "warning" },
      { label: "ACTIVE_MAP", value: mapName, tone: model.activeMap ? "active" : "warning" },
      { label: "EDGE_STATE", value: model.edgeConnectionState, tone: model.edgeConnectionState === "online" ? "ready" : "warning" },
      { label: "PIPELINE", value: model.activeDevice?.vision_pipeline ?? "classical default", tone: "active" },
    ];
  }
  if (tab === "messages") {
    return [
      { label: "MESSAGE", value: "No live MAVLink message stream is attached in browser fallback mode.", tone: "warning" },
      { label: "ROUTE", value: "Connect the Pi runtime, then use MAVLink source control.", tone: "muted" },
    ];
  }
  if (tab === "ekf-init") {
    return [
      { label: "EKF_INIT", value: model.activeDevice ? "waiting for MAVLink EKF evidence" : "disabled until device is connected", tone: model.activeDevice ? "warning" : "muted" },
      { label: "GPS", value: "no live sample", tone: "muted" },
      { label: "VISION", value: "no live sample", tone: "muted" },
    ];
  }
  if (tab === "console") {
    return [
      { label: "CONSOLE", value: "Console commands are available from connected system tools only.", tone: "warning" },
      { label: "SAFETY", value: "No PX4 parameters are auto-changed from this shell.", tone: "ready" },
    ];
  }
  return [
    { label: "EDGE", value: model.edgeConnectionState, tone: model.edgeConnectionState === "online" ? "ready" : "warning" },
    { label: "MAP_CACHE", value: `${model.localMaps.length} local / ${model.edgeMaps.length} edge`, tone: model.localMaps.length || model.edgeMaps.length ? "ready" : "warning" },
    { label: "ACTIVE_MAP", value: mapName, tone: model.activeMap ? "active" : "warning" },
    { label: "RUNTIME", value: model.activeDevice?.remote_project_path ?? "runtime path not configured", tone: model.activeDevice?.remote_project_path ? "ready" : "warning" },
  ];
}
