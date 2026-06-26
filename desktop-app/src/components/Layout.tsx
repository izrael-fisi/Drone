import {
  Activity,
  Archive,
  LayoutDashboard,
  Map,
  Radio,
  Server,
  Upload,
  Settings,
  ChevronRight,
  X,
  Minus,
  Square,
  Search,
  Battery,
  Wifi,
} from "lucide-react";
import { useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { DroneLogo } from "../App";
import { useAppStore } from "../lib/store";
import { cn } from "../lib/utils";

const NAV = [
  { to: "/dashboard", label: "Start", Icon: LayoutDashboard, matches: ["/navigation-panel"] },
  { to: "/maps", label: "Map", Icon: Map, matches: [] },
  { to: "/mission-planner", label: "Mission", Icon: Upload, matches: ["/mission-bundle-builder", "/bundle-builder", "/bundle", "/mission-bundle", "/terrain", "/terrain-planning"] },
  { to: "/devices", label: "Drone", Icon: Server, matches: ["/vehicle-manager", "/pi-setup", "/module-setup"] },
  { to: "/system-status", label: "Fly", Icon: Radio, matches: ["/diagnostics"] },
  { to: "/flight-review", label: "Review", Icon: Archive, matches: ["/history"] },
];

const APP_VERSION = "0.1.0";

const ADVANCED_COMMANDS = [
  { label: "Camera settings", detail: "Feature matching and vision defaults", to: "/camera-vision" },
  { label: "Build bundle", detail: "Package map and mission files", to: "/mission-bundle-builder" },
  { label: "Terrain limits", detail: "AGL and route constraints", to: "/terrain" },
  { label: "Settings", detail: "App preferences and keys", to: "/settings" },
];

const SEARCH_COMMANDS = [
  ...NAV.map((item) => ({ label: item.label, detail: "Operator path", to: item.to })),
  ...ADVANCED_COMMANDS,
];

function pageLabel(pathname: string) {
  return (
    NAV.find((item) => item.to === pathname || item.matches.includes(pathname))?.label ??
    ADVANCED_COMMANDS.find((item) => item.to === pathname)?.label ??
    "Drone Vision"
  );
}

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
  const { profile, devices, regions, activeDeviceId } = useAppStore();
  const location = useLocation();
  const navigate = useNavigate();
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const downloadedMapCount = regions.filter((region) => region.last_downloaded).length;
  const [searchQuery, setSearchQuery] = useState("");
  const [recording, setRecording] = useState(() => localStorage.getItem("drone_recording_enabled") === "true");

  const commandResults = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return [];
    return SEARCH_COMMANDS
      .filter((command) => `${command.label} ${command.detail}`.toLowerCase().includes(query))
      .slice(0, 5);
  }, [searchQuery]);

  const runSearch = (to?: string) => {
    const target = to || commandResults[0]?.to;
    if (!target) return;
    navigate(target);
    setSearchQuery("");
  };

  const toggleRecording = () => {
    setRecording((current) => {
      localStorage.setItem("drone_recording_enabled", String(!current));
      return !current;
    });
  };

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg-base text-slate-200">
      <header
        className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-border bg-[#04070B] px-4"
        data-tauri-drag-region
      >
        <div className="flex min-w-0 items-center gap-3">
          <button type="button" onClick={() => navigate("/dashboard")} className="flex items-center gap-2" data-tauri-drag-region="false">
            <DroneLogo size={25} />
            <span className="font-mono text-sm font-bold uppercase tracking-[0.12em] text-slate-100">Drone Vision</span>
          </button>
        </div>

        <div className="flex items-center gap-2">
          <Wifi size={15} className={activeDevice ? "text-cyan-400" : "text-slate-600"} />
          <Battery size={15} className={activeDevice ? "text-emerald-300" : "text-slate-600"} />
          <button
            onClick={() => runWindowAction("minimize")}
            className="w-7 h-7 flex items-center justify-center rounded-none hover:bg-bg-elevated text-slate-500 hover:text-slate-300 transition-colors"
          >
            <Minus size={12} />
          </button>
          <button
            onClick={() => runWindowAction("toggleMaximize")}
            className="w-7 h-7 flex items-center justify-center rounded-none hover:bg-bg-elevated text-slate-500 hover:text-slate-300 transition-colors"
          >
            <Square size={11} />
          </button>
          <button
            onClick={() => runWindowAction("close")}
            className="w-7 h-7 flex items-center justify-center rounded-none hover:bg-red-500/20 text-slate-500 hover:text-red-400 transition-colors"
          >
            <X size={13} />
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <aside className="flex w-[178px] shrink-0 flex-col border-r border-border bg-[#070B11]">
          <div className="border-b border-border px-3 py-3">
            <div className="flex items-center gap-2">
              <div className="flex h-7 w-7 items-center justify-center border border-border bg-bg-surface">
                <span className="font-mono text-[9px] font-bold text-slate-400">01</span>
              </div>
              <div className="min-w-0">
                <div className="font-mono text-xs font-bold uppercase tracking-[0.1em] text-slate-200">Path</div>
                <div className="truncate font-mono text-[9px] font-bold uppercase tracking-[0.08em] text-slate-600">Plan to flight</div>
              </div>
            </div>
          </div>

        <nav className="flex-1 overflow-y-auto py-2">
          {NAV.map(({ to, label, Icon, matches }, index) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "mb-0.5 flex items-center gap-2 border-l-2 px-3 py-2.5 font-mono text-[10px] font-bold uppercase tracking-[0.06em] transition-colors group",
                  isActive || matches.includes(location.pathname)
                    ? "border-cyan-500 bg-bg-elevated text-cyan-300"
                    : "border-transparent text-slate-500 hover:bg-bg-surface hover:text-slate-200"
                )
              }
            >
              {({ isActive }) => {
                const active = isActive || matches.includes(location.pathname);
                return (
                <>
                  <Icon size={14} className={active ? "text-cyan-400" : "text-slate-600 group-hover:text-slate-400"} />
                  <span className="w-3 text-[9px] text-slate-600">{index + 1}</span>
                  {label}
                  {active && <ChevronRight size={12} className="ml-auto text-cyan-500" />}
                </>
                );
              }}
            </NavLink>
          ))}
        </nav>

          <div className="border-t border-border p-3 space-y-2">
            <button type="button" onClick={() => setSearchQuery((q) => q || "camera")} className="btn-secondary w-full justify-center text-[11px]">
              <Search size={14} /> Find
            </button>
            <div className="grid grid-cols-1 gap-2">
              <button type="button" className="btn-ghost justify-center text-[11px]" onClick={() => navigate("/settings")}>
                <Settings size={13} /> Settings
              </button>
              <button type="button" className="btn-ghost justify-center text-[11px]" onClick={() => navigate("/camera-vision")}>
                Advanced
              </button>
            </div>
            <div className="flex items-center gap-2 pt-2">
              <div
                className="flex h-7 w-7 shrink-0 items-center justify-center border border-border text-xs font-bold text-slate-100"
                style={{ background: profile?.accent_color ?? "#06B6D4" }}
              >
                {profile?.name?.charAt(0)?.toUpperCase() ?? "?"}
              </div>
              <div className="min-w-0">
                <div className="text-xs font-medium text-slate-200 truncate">{profile?.name}</div>
                <div className="text-[10px] text-slate-500 truncate">{profile?.org || "Drone Vision Nav"}</div>
              </div>
            </div>
          </div>
        </aside>

      {/* Main content + custom title bar */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Custom title bar */}
        <div
          className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-border bg-bg-surface px-3"
          data-tauri-drag-region
        >
          <div className="flex min-w-0 items-center gap-3">
            <div className="hidden md:block min-w-32">
              <div className="font-mono text-xs font-semibold uppercase tracking-[0.1em] text-slate-200 leading-tight">{pageLabel(location.pathname)}</div>
              <div className="font-mono text-[10px] text-slate-600 leading-tight">Drone Vision {APP_VERSION}</div>
            </div>
            <form
              className="relative hidden lg:block w-[360px]"
              onSubmit={(event) => {
                event.preventDefault();
                runSearch();
              }}
            >
              <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Find page or setting"
                className="h-7 w-full rounded-none border border-border bg-bg-base pl-8 pr-3 text-xs text-slate-300 placeholder:text-slate-600 outline-none transition-colors focus:border-cyan-500/60 focus:ring-0"
              />
              {commandResults.length > 0 && (
                <div className="absolute left-0 right-0 top-9 z-[900] overflow-hidden rounded-none border border-border bg-bg-surface shadow-2xl">
                  {commandResults.map((command) => (
                    <button
                      key={`${command.label}-${command.to}`}
                      type="button"
                      onClick={() => runSearch(command.to)}
                      className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-bg-elevated"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-xs font-medium text-slate-200">{command.label}</span>
                        <span className="block truncate text-[10px] text-slate-500">{command.detail}</span>
                      </span>
                      <ChevronRight size={12} className="text-slate-500" />
                    </button>
                  ))}
                </div>
              )}
            </form>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={toggleRecording}
              className={cn(
                "hidden h-7 items-center gap-2 border px-2.5 font-mono text-xs xl:flex",
                recording
                  ? "border-red-500/45 bg-red-500/10 text-red-300"
                  : "border-border bg-bg-base text-slate-500 hover:border-cyan-500/40 hover:text-slate-300",
              )}
            >
              <span className={cn("h-2 w-2 rounded-full", recording ? "bg-red-400" : "bg-slate-600")} />
              REC
            </button>
            <div className="hidden h-7 items-center gap-2 rounded-none border border-border bg-bg-base px-2.5 text-xs xl:flex">
              <Activity size={13} className={activeDevice ? "text-emerald-300" : "text-slate-600"} />
              <span className={cn("ops-led", activeDevice ? "ops-led-ready" : "ops-led-offline")} />
              <span className="max-w-32 truncate font-mono text-slate-300">{activeDevice?.name ?? "No device"}</span>
            </div>
            <button
              type="button"
              onClick={() => navigate(activeDevice ? "/system-status" : "/devices")}
              className="hidden h-7 items-center gap-2 border border-border bg-bg-base px-2.5 font-mono text-xs text-slate-300 hover:border-cyan-500/40 hover:text-cyan-300 xl:flex"
            >
              {activeDevice ? "Status" : "Connect"}
            </button>
            <div className="hidden h-7 items-center gap-2 rounded-none border border-border bg-bg-base px-2.5 text-xs xl:flex">
              <Map size={13} className="text-cyan-400" />
              <span className="font-mono text-slate-300">{downloadedMapCount} maps</span>
            </div>
          </div>
        </div>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      </div>
    </div>
  );
}
