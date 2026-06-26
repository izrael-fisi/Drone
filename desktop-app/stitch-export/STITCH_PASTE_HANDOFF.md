# Stitch Paste Handoff: Drone Vision Desktop App

Use this as the authoritative product/design handoff for redesigning the Drone Vision desktop app in Stitch.

Important: Stitch design output should improve the app, not replace working workflows with static placeholders. Preserve the route hierarchy, user flows, and data-pipeline expectations below.

# Drone Vision Desktop App Requirements

This README is the design and product handoff for importing the desktop app into
Stitch. Stitch panes may be used as visual references, but the app should remain
fully usable and connected to the existing data pipeline. Do not treat any static
pane template as authoritative if it conflicts with the working app behavior.

## Product Goal

Drone Vision is a ground-control and mission-planning desktop app for a
GNSS-denied terrain-vision navigation drone.

The app helps an operator:

- prepare local georeferenced maps
- configure the camera and feature-matching pipeline
- create a flight mission and terrain bundle
- connect to a Raspberry Pi runtime computer
- launch and validate the onboard vision navigation loop
- monitor GPS versus terrain-vision position telemetry
- review bench and field evidence after tests

The app is hardware-first. ROS 2 and SITL controls should not be first-class
operator surfaces.

## App Architecture

The desktop app is a Tauri + React + TypeScript application.

Primary folders:

- `src/`: React UI, routing, pages, app store, and Tauri command wrappers
- `src-tauri/`: Rust backend commands for map import, SSH, bundle build,
  telemetry, support bundle handling, and local command wrappers
- `public/`: static public assets
- `dist/`: built static preview output

Stitch should preserve the app as a navigable operations console rather than a
set of isolated static screens.

## Navigation Requirements

The left navigation must stay available across the main app:

- Ops Console
- Map Library
- Mission Planner
- Vehicle Manager
- Camera & Vision
- Diagnostics
- Flight Review
- Settings

Route aliases should still work because operator language varies:

- `/navigation-panel` aliases `/dashboard`
- `/vehicle-manager` aliases `/devices`
- `/camera-vision` aliases `/vision-pipeline`
- `/mission-bundle-builder` aliases `/mission-planner`
- `/diagnostics` aliases `/system-status`
- `/history` aliases `/flight-review`

The app must use native pane transitions and real route navigation. Do not embed
the working app as an iframe.

## Core Page Requirements

### Ops Console

Purpose: operator home and launch surface.

Must show:

- active device status
- downloaded map count
- saved region count
- readiness checklist
- quick action cards for maps, vision pipeline, devices, and mission planning
- saved regions with edit/delete/plan actions

### Map Library

Purpose: prepare map data for GNSS-denied localization.

Must support:

- drawing a map area
- estimating tile count and storage
- downloading satellite map sources
- importing local map folders
- importing/uploading common image formats
- handling GeoTIFF metadata when available
- showing georeference quality and map readiness
- preserving map lifecycle states such as local, built, uploaded, active, stale,
  or failed when implemented

Supported map/image inputs should include PNG, JPEG/JPG, TIFF/GeoTIFF, BMP,
WebP, and GIF.

### Mission Planner

Purpose: plan the operational mission and build the runtime bundle.

Must support:

- manual Takeoff, Waypoint, and Land placement
- Mission, GeoFence, Rally, and Vision Map planning layers
- map-backed planning without auto-loading large maps on first entry
- mission item reorder/delete/edit
- QGroundControl-style `.plan` import/export compatibility
- bundle build and validation
- bundle upload to the active Raspberry Pi profile
- GNSS-denied readiness checks
- terrain planning constraints
- live position display when telemetry is available

Mission Planner should consume the selected Vision Pipeline defaults. It should
not duplicate editable feature/matcher controls.

### Vehicle Manager

Purpose: manage runtime devices and Raspberry Pi setup.

Must support:

- add/edit/delete runtime device profiles
- active device selection
- SSH host, port, username, and auth configuration
- Raspberry Pi discovery over local Wi-Fi
- project sync/install commands
- Pi dependency checks
- camera health checks
- MAVLink endpoint checks
- bundle upload and validation
- prop-off hardware bench workflow

### Camera & Vision

Purpose: single source of truth for vision pipeline configuration.

Must support:

- classical CPU pipeline
- ORB / AKAZE / SIFT feature method options
- optional neural SuperPoint / LightGlue mode
- max features
- matcher ratio
- minimum matches
- model weight paths

This page is the only editable vision configuration surface. Other pages may
show summaries but should not create duplicate controls.

### Diagnostics

Purpose: live readiness and position telemetry.

Must support:

- active device readiness
- downloaded map readiness
- selected vision pipeline summary
- UDP position telemetry listener
- GPS versus terrain-vision source display
- confidence and covariance diagnostics
- source fallback status

Default position telemetry port: `17660`.

### Flight Review

Purpose: review evidence after bench or field runs.

Must support:

- downloaded support bundle list
- support bundle health summary
- evidence/report summaries
- local reveal/delete/extract actions where available
- storage and pass/fail counts

### Settings

Purpose: app-level configuration.

Must support:

- imagery API keys
- app preferences
- repo/path defaults
- storage and download locations
- YAML config viewing/editing when available

## Data Pipeline Requirements

The app must stay wired to the current project data pipeline:

```text
Map Library
  -> selected map source
  -> Mission Planner
  -> mission bundle build
  -> terrain tile index and feature map
  -> Raspberry Pi upload
  -> onboard terrain vision runtime
  -> GPS / vision position telemetry
  -> Diagnostics and Mission Planner live position
  -> Flight Review support bundles
```

The desktop app should continue to use the Tauri command wrapper in
`src/lib/tauri.ts`. Browser preview may use safe local fallback data, but the
packaged Tauri app must use real backend commands.

## Runtime Position Requirement

The drone position should be sent from the runtime module to the ground station.

Source priority:

1. Use healthy GPS when available.
2. Use GNSS-denied terrain-vision position when GPS is unavailable, weak,
   spoofed, jammed, or degraded.
3. Use degraded GPS only if terrain vision is unavailable.

The UI must make the active source obvious and show confidence/health state.

## Visual Requirements

The app should feel like a professional ground-control operations console:

- dark cockpit-like UI
- dense but readable operational layout
- rectangular controls
- visible status LEDs/badges
- cyan active navigation
- green/amber/red health semantics
- monospaced telemetry values
- no marketing-style hero pages
- no static placeholder panes where a working workflow exists

The UI should be optimized for repeat use by an operator, not for a landing-page
presentation.

## Nonfunctional Requirements

- First entry into Mission Planner must feel fast.
- Large map mosaics should load only after user selection.
- Browser/dev preview should not crash when Tauri APIs are unavailable.
- Tauri runtime actions should be lazy or guarded.
- The app should build with `npm run build`.
- The Rust backend should pass `cargo check` and `cargo test`.
- The project preflight should pass with `./scripts/dev/local_preflight.sh`.
- Static Stitch design changes must not break route navigation or the data
  pipeline.

## Stitch Import Guidance

Use the included files as follows:

- `desktop-app-source/`: implementation source of truth for panes, routing,
  components, styles, and backend command contracts
- `desktop-app-static-build/`: static built output for quick visual inspection
- `README.md`: this requirements document

When redesigning in Stitch:

- keep the current navigation hierarchy
- preserve all active workflows
- make controls interactive, not just decorative
- keep Camera & Vision as the only editable pipeline configuration page
- keep Mission Planner focused on mission, map, bundle, and live position
- keep Vehicle Manager focused on device and Raspberry Pi setup
- keep Diagnostics focused on system and telemetry health
- keep Flight Review focused on support bundle history

## Current Verification Snapshot

The current app has been checked with:

```bash
npm run build
cd src-tauri && cargo check && cargo test
./scripts/dev/local_preflight.sh
```

The production preview rendered the native shell and the core routes without
iframes.

---

# Current App File Tree

```text
src-tauri/src/commands/config_cmd.rs
src-tauri/src/commands/discovery.rs
src-tauri/src/commands/drone.rs
src-tauri/src/commands/mod.rs
src-tauri/src/commands/profile.rs
src-tauri/src/commands/satellite.rs
src-tauri/src/commands/ssh.rs
src-tauri/src/commands/telemetry.rs
src-tauri/src/lib.rs
src-tauri/src/main.rs
src/App.tsx
src/components/Layout.tsx
src/components/SupportBundleList.tsx
src/index.css
src/lib/discovery.ts
src/lib/pipelineConfig.ts
src/lib/store.ts
src/lib/tauri.ts
src/lib/types.ts
src/lib/utils.ts
src/main.tsx
src/pages/Dashboard.tsx
src/pages/Devices.tsx
src/pages/FlightReview.tsx
src/pages/Maps.tsx
src/pages/MissionPlanner.tsx
src/pages/Onboarding.tsx
src/pages/PiSetup.tsx
src/pages/Settings.tsx
src/pages/SystemStatus.tsx
src/pages/VisionPipeline.tsx
src/vite-env.d.ts
```

---

# Key Source Files For Stitch Context

The following files define the current navigation, pane structure, app styling, and desktop bridge behavior.

## src/App.tsx

```tsx
import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { cmd } from "./lib/tauri";
import { useAppStore } from "./lib/store";
import { Dashboard } from "./pages/Dashboard";
import { Devices } from "./pages/Devices";
import { FlightReview } from "./pages/FlightReview";
import { Maps } from "./pages/Maps";
import { MissionPlanner } from "./pages/MissionPlanner";
import { Onboarding } from "./pages/Onboarding";
import { ModuleSetup } from "./pages/PiSetup";
import { Settings } from "./pages/Settings";
import { SystemStatus } from "./pages/SystemStatus";
import { VisionPipelinePage } from "./pages/VisionPipeline";

export default function App() {
  const { setProfile, setDevices, setRegions } = useAppStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([cmd.loadProfile(), cmd.loadDevices(), cmd.loadRegions()])
      .then(([p, d, r]) => {
        setProfile(p);
        setDevices(d);
        setRegions(r);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [setProfile, setDevices, setRegions]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg-base">
        <div className="flex flex-col items-center gap-4 animate-fade-in">
          <DroneLogo size={48} />
          <span className="text-slate-400 text-sm">Loading…</span>
        </div>
      </div>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/onboarding" element={<Onboarding />} />
        <Route element={<Layout />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/navigation-panel" element={<Dashboard />} />
          <Route path="/maps" element={<Maps />} />
          <Route path="/mission-planner" element={<MissionPlanner />} />
          <Route path="/mission-bundle-builder" element={<MissionPlanner />} />
          <Route path="/bundle" element={<Navigate to="/mission-bundle-builder" replace />} />
          <Route path="/mission-bundle" element={<Navigate to="/mission-bundle-builder" replace />} />
          <Route path="/upload" element={<Navigate to="/mission-planner" replace />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/vehicle-manager" element={<Devices />} />
          <Route path="/pi-setup" element={<ModuleSetup />} />
          <Route path="/module-setup" element={<ModuleSetup />} />
          <Route path="/vision-pipeline" element={<VisionPipelinePage />} />
          <Route path="/camera-vision" element={<VisionPipelinePage />} />
          <Route path="/models" element={<Navigate to="/camera-vision" replace />} />
          <Route path="/system-status" element={<SystemStatus />} />
          <Route path="/diagnostics" element={<Navigate to="/system-status" replace />} />
          <Route path="/flight-review" element={<FlightReview />} />
          <Route path="/history" element={<Navigate to="/flight-review" replace />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export function DroneLogo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      <circle cx="16" cy="16" r="15" stroke="#06B6D4" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="6" fill="#06B6D4" fillOpacity="0.15" stroke="#06B6D4" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="2.5" fill="#06B6D4" />
      <line x1="16" y1="1" x2="16" y2="8" stroke="#06B6D4" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="16" y1="24" x2="16" y2="31" stroke="#06B6D4" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="1" y1="16" x2="8" y2="16" stroke="#06B6D4" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="24" y1="16" x2="31" y2="16" stroke="#06B6D4" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
```

## src/components/Layout.tsx

```tsx
import {
  Activity,
  Archive,
  LayoutDashboard,
  Map,
  Cpu,
  Radio,
  Server,
  Upload,
  Settings,
  ChevronRight,
  X,
  Minus,
  Square,
  Search,
} from "lucide-react";
import { useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { DroneLogo } from "../App";
import { useAppStore } from "../lib/store";
import { cn } from "../lib/utils";

const NAV = [
  { to: "/dashboard", label: "Ops Console", Icon: LayoutDashboard, matches: ["/navigation-panel"] },
  { to: "/maps", label: "Map Library", Icon: Map, matches: [] },
  { to: "/mission-planner", label: "Mission Planner", Icon: Upload, matches: ["/mission-bundle-builder"] },
  { to: "/devices", label: "Vehicle Manager", Icon: Server, matches: ["/vehicle-manager", "/pi-setup", "/module-setup"] },
  { to: "/camera-vision", label: "Camera & Vision", Icon: Cpu, matches: ["/vision-pipeline", "/models"] },
  { to: "/system-status", label: "Diagnostics", Icon: Radio, matches: ["/diagnostics"] },
  { to: "/flight-review", label: "Flight Review", Icon: Archive, matches: ["/history"] },
  { to: "/settings", label: "Settings", Icon: Settings, matches: [] },
];

const APP_VERSION = "0.1.0";

const SEARCH_COMMANDS = [
  ...NAV.map((item) => ({ label: item.label, detail: "Open page", to: item.to })),
  { label: "Map Library", detail: "Manage downloaded and uploaded maps", to: "/maps" },
  { label: "Runtime Devices", detail: "Raspberry Pi and MAVLink setup", to: "/devices" },
  { label: "Mission Bundle", detail: "Build and upload the active mission", to: "/mission-bundle-builder" },
  { label: "Camera Calibration", detail: "Open camera and vision setup", to: "/camera-vision" },
  { label: "Position Telemetry", detail: "Monitor GPS and vision position fallback", to: "/system-status" },
  { label: "Support Bundles", detail: "Review field and bench evidence", to: "/flight-review" },
];

function pageLabel(pathname: string) {
  return NAV.find((item) => item.to === pathname || item.matches.includes(pathname))?.label ?? "Drone Vision";
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
  const [recordingArmed, setRecordingArmed] = useState(() => localStorage.getItem("drone_recording_armed") === "true");

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
    setRecordingArmed((current) => {
      const next = !current;
      localStorage.setItem("drone_recording_armed", String(next));
      return next;
    });
  };

  return (
    <div className="flex h-screen bg-bg-base overflow-hidden">
      {/* Sidebar */}
      <aside className="w-56 flex flex-col bg-bg-surface border-r border-border shrink-0">
        {/* Titlebar / Logo */}
        <div
          className="flex items-center gap-3 px-4 py-4 border-b border-border"
          data-tauri-drag-region
        >
          <DroneLogo size={28} />
          <div>
            <div className="text-sm font-bold text-slate-100 leading-none">Drone Vision</div>
            <div className="text-[10px] text-slate-500 mt-0.5">GNSS-Denied Nav</div>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto py-3 px-2">
          {NAV.map(({ to, label, Icon, matches }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-none mb-0.5 text-sm font-medium transition-colors group",
                  isActive || matches.includes(location.pathname)
                    ? "bg-cyan-500/10 text-cyan-400 border border-cyan-500/40"
                    : "text-slate-400 hover:text-slate-200 hover:bg-bg-elevated"
                )
              }
            >
              {({ isActive }) => {
                const active = isActive || matches.includes(location.pathname);
                return (
                <>
                  <Icon size={16} className={active ? "text-cyan-400" : "text-slate-500 group-hover:text-slate-400"} />
                  {label}
                  {active && <ChevronRight size={12} className="ml-auto text-cyan-500" />}
                </>
                );
              }}
            </NavLink>
          ))}
        </nav>

        {/* Profile */}
        <div className="border-t border-border px-3 py-3 flex items-center gap-2">
          <div
            className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0"
            style={{ background: profile?.accent_color ?? "#06B6D4" }}
          >
            {profile?.name?.charAt(0)?.toUpperCase() ?? "?"}
          </div>
          <div className="min-w-0">
            <div className="text-xs font-medium text-slate-200 truncate">{profile?.name}</div>
            <div className="text-[10px] text-slate-500 truncate">{profile?.org || "Drone Vision Nav"}</div>
          </div>
        </div>
      </aside>

      {/* Main content + custom title bar */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Custom title bar */}
        <div
          className="flex h-12 items-center justify-between gap-3 border-b border-border bg-bg-surface px-3 shrink-0"
          data-tauri-drag-region
        >
          <div className="flex min-w-0 items-center gap-3">
            <div className="hidden md:block min-w-32">
              <div className="font-mono text-xs font-semibold text-slate-200 leading-tight">{pageLabel(location.pathname)}</div>
              <div className="font-mono text-[10px] text-slate-500 leading-tight">Drone Vision {APP_VERSION}</div>
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
                placeholder="Search pages, settings, mission tools"
                className="h-8 w-full rounded-none border border-border bg-bg-base pl-8 pr-3 text-xs text-slate-200 placeholder:text-slate-600 outline-none transition-colors focus:border-cyan-500/60 focus:ring-1 focus:ring-cyan-500/20"
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
                "hidden sm:flex h-8 items-center gap-2 rounded-none border px-2.5 text-xs font-medium transition-colors",
                recordingArmed
                  ? "border-red-500/40 bg-red-500/10 text-red-300"
                  : "border-border bg-bg-surface text-slate-400 hover:text-slate-200",
              )}
              title="Local recording readiness marker"
            >
              <span className={cn("ops-led", recordingArmed ? "ops-led-critical" : "ops-led-offline")} />
              {recordingArmed ? "Recording" : "Record"}
            </button>
            <div className="hidden xl:flex h-8 items-center gap-2 rounded-none border border-border bg-bg-base px-2.5 text-xs">
              <Activity size={13} className={activeDevice ? "text-emerald-400" : "text-slate-500"} />
              <span className={cn("ops-led", activeDevice ? "ops-led-ready" : "ops-led-offline")} />
              <span className="max-w-32 truncate font-mono text-slate-300">{activeDevice?.name ?? "No device"}</span>
            </div>
            <div className="hidden xl:flex h-8 items-center gap-2 rounded-none border border-border bg-bg-base px-2.5 text-xs">
              <Map size={13} className="text-cyan-400" />
              <span className="font-mono text-slate-300">{downloadedMapCount} maps</span>
            </div>
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
        </div>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
```

## src/pages/Dashboard.tsx

```tsx
import { Map, Cpu, Server, Upload, ArrowRight, CheckCircle2, Clock, XCircle, Pencil, Trash2, Check, MapPin } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useState } from "react";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import { formatDate, cn } from "../lib/utils";

export function Dashboard() {
  const { profile, devices, regions, activeDeviceId, updateRegion, removeRegion } = useAppStore();
  const navigate = useNavigate();
  const activeDevice = devices.find((d) => d.id === activeDeviceId);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");

  const startEditName = (id: string, current: string) => {
    setEditingId(id);
    setEditingName(current);
  };

  const commitEditName = async (id: string) => {
    const trimmed = editingName.trim();
    if (!trimmed) { setEditingId(null); return; }
    const r = regions.find((x) => x.id === id);
    if (!r) { setEditingId(null); return; }
    const updated = { ...r, name: trimmed };
    updateRegion(updated);
    const next = regions.map((x) => (x.id === id ? updated : x));
    await cmd.saveRegions(next);
    setEditingId(null);
  };

  const deleteRegion = async (id: string) => {
    removeRegion(id);
    const next = regions.filter((x) => x.id !== id);
    await cmd.saveRegions(next);
  };

  const QUICK_ACTIONS = [
    { label: "Download Region", desc: "Select & fetch satellite tiles", icon: Map, to: "/maps", color: "cyan" },
    { label: "Vision Pipeline", desc: "Configure matching defaults", icon: Cpu, to: "/vision-pipeline", color: "violet" },
    { label: "Manage Devices", desc: "Pi5 and local targets", icon: Server, to: "/devices", color: "emerald" },
    { label: "Mission Planner", desc: "Plan flight area, path, and bundle", icon: Upload, to: "/mission-planner", color: "amber" },
  ] as const;

  const COLOR_MAP = {
    cyan: "text-cyan-400 bg-cyan-500/10 border-cyan-500/20",
    violet: "text-violet-400 bg-violet-500/10 border-violet-500/20",
    emerald: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
    amber: "text-amber-400 bg-amber-500/10 border-amber-500/20",
  };

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-slate-100">
          Good to see you, {profile?.name?.split(" ")[0] ?? "pilot"}
        </h1>
        <p className="text-slate-400 text-sm mt-1">
          {profile?.org ? `${profile.org} · ` : ""}Drone GNSS-Denied Vision Navigation
        </p>
      </div>

      {/* Status bar */}
      <div className="flex items-center gap-4 bg-bg-card border border-border rounded-xl px-5 py-3">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-400">Active device:</span>
          {activeDevice ? (
            <span className="badge-cyan">{activeDevice.name}</span>
          ) : (
            <span className="text-slate-500 text-xs">None selected</span>
          )}
        </div>
        <div className="w-px h-4 bg-border" />
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <span>{regions.length} region{regions.length !== 1 ? "s" : ""}</span>
        </div>
        <div className="w-px h-4 bg-border" />
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <span>{devices.length} device{devices.length !== 1 ? "s" : ""}</span>
        </div>
        <div className="ml-auto">
          <button onClick={() => navigate("/devices")} className="btn-ghost text-xs py-1 px-2">
            Manage <ArrowRight size={12} />
          </button>
        </div>
      </div>

      {/* Readiness checklist */}
      {(() => {
        const hasDevice = devices.length > 0;
        const hasActive = !!activeDeviceId;
        const hasRegion = regions.some((r) => r.last_downloaded);
        const checks = [
          { label: "Device configured", ok: hasDevice && hasActive, hint: !hasDevice ? "Add a device" : "Set active device", to: "/devices" },
          { label: "Region downloaded", ok: hasRegion, hint: "Download satellite tiles", to: "/maps" },
          { label: "Ready to plan mission", ok: hasActive && hasRegion, hint: "Device + region needed", to: "/mission-planner" },
        ];
        return (
          <div className="grid grid-cols-3 gap-3">
            {checks.map(({ label, ok, hint, to }) => (
              <button
                key={label}
                onClick={() => navigate(to)}
                className={cn(
                  "card text-left transition-all hover:border-border-strong",
                  ok ? "border-emerald-500/20" : "border-border"
                )}
              >
                <div className="flex items-center gap-2 mb-1">
                  {ok
                    ? <CheckCircle2 size={13} className="text-emerald-400 shrink-0" />
                    : <XCircle size={13} className="text-slate-600 shrink-0" />}
                  <span className={cn("text-xs font-medium", ok ? "text-emerald-400" : "text-slate-400")}>
                    {label}
                  </span>
                </div>
                {!ok && <p className="text-[11px] text-slate-600 pl-[21px]">{hint} →</p>}
              </button>
            ))}
          </div>
        );
      })()}

      {/* Quick actions */}
      <div>
        <h2 className="section-title mb-3">Quick Actions</h2>
        <div className="grid grid-cols-2 gap-3">
          {QUICK_ACTIONS.map(({ label, desc, icon: Icon, to, color }) => (
            <button
              key={to}
              onClick={() => navigate(to)}
              className="card text-left hover:border-border-strong transition-all group flex items-start gap-4"
            >
              <div className={`w-10 h-10 rounded-lg border flex items-center justify-center shrink-0 ${COLOR_MAP[color]}`}>
                <Icon size={18} />
              </div>
              <div>
                <div className="font-medium text-slate-200 text-sm group-hover:text-white transition-colors">{label}</div>
                <div className="text-xs text-slate-500 mt-0.5">{desc}</div>
              </div>
              <ArrowRight size={14} className="text-slate-600 group-hover:text-slate-400 ml-auto mt-1 transition-colors" />
            </button>
          ))}
        </div>
      </div>

      {/* Recent regions */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="section-title">Saved Regions</h2>
          <button onClick={() => navigate("/maps")} className="btn-ghost text-xs py-1 px-2">
            + New region
          </button>
        </div>

        {regions.length === 0 ? (
          <div className="card text-center py-10">
            <Map size={32} className="text-slate-600 mx-auto mb-3" />
            <p className="text-slate-400 text-sm">No regions yet</p>
            <p className="text-slate-500 text-xs mt-1">Draw a bounding box on the map to get started</p>
            <button onClick={() => navigate("/maps")} className="btn-primary mt-4 mx-auto">
              Open Map <ArrowRight size={14} />
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            {regions.map((r) => (
              <div key={r.id} className="card group py-3 px-4 space-y-2">
                {/* Row 1: icon + name (editable) + date + actions */}
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center shrink-0">
                    <Map size={14} className="text-cyan-400" />
                  </div>

                  {editingId === r.id ? (
                    <input
                      autoFocus
                      className="input-field flex-1 text-sm py-0.5 h-7"
                      value={editingName}
                      onChange={(e) => setEditingName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitEditName(r.id);
                        if (e.key === "Escape") setEditingId(null);
                      }}
                      onBlur={() => commitEditName(r.id)}
                    />
                  ) : (
                    <span className="flex-1 text-sm font-medium text-slate-200 truncate">{r.name}</span>
                  )}

                  <div className="flex items-center gap-1 shrink-0">
                    {editingId === r.id ? (
                      <button onClick={() => commitEditName(r.id)} className="btn-ghost py-1 px-1.5">
                        <Check size={12} className="text-emerald-400" />
                      </button>
                    ) : (
                      <button onClick={() => startEditName(r.id, r.name)} className="btn-ghost py-1 px-1.5 opacity-0 group-hover:opacity-100 hover:!opacity-100">
                        <Pencil size={11} />
                      </button>
                    )}
                    <button onClick={() => navigate("/mission-planner")} className="btn-ghost py-1 px-1.5 text-xs">
                      <Upload size={12} />
                    </button>
                    <button onClick={() => deleteRegion(r.id)} className="btn-ghost py-1 px-1.5 text-red-400/60 hover:text-red-400">
                      <Trash2 size={11} />
                    </button>
                  </div>
                </div>

                {/* Row 2: metadata chips */}
                <div className="flex items-center gap-3 pl-11 flex-wrap">
                  {r.location_label && (
                    <span className="flex items-center gap-1 text-[11px] text-slate-400">
                      <MapPin size={10} className="text-cyan-500 shrink-0" />
                      {r.location_label}
                    </span>
                  )}
                  {!r.location_label && (
                    <span className="text-[11px] text-slate-500 font-mono">
                      {r.lat_min.toFixed(3)}, {r.lon_min.toFixed(3)}
                    </span>
                  )}
                  {r.gsd_m_per_px != null && (
                    <span className="text-[11px] text-slate-500">
                      <span className="text-slate-400 font-medium">{r.gsd_m_per_px.toFixed(2)} m/px</span>
                      {" "}resolution
                    </span>
                  )}
                  {r.file_size_mb != null && (
                    <span className="text-[11px] text-slate-500">
                      ~<span className="text-slate-400 font-medium">{r.file_size_mb.toFixed(0)} MB</span>
                    </span>
                  )}
                  {r.zoom && (
                    <span className="text-[10px] bg-bg-elevated border border-border rounded px-1.5 py-0.5 text-slate-500 font-mono">
                      Z{r.zoom}
                    </span>
                  )}
                  <div className="ml-auto">
                    {r.last_downloaded ? (
                      <div className="flex items-center gap-1.5 badge-green">
                        <CheckCircle2 size={10} />
                        {formatDate(r.last_downloaded)}
                      </div>
                    ) : (
                      <div className="badge-yellow">
                        <Clock size={10} />
                        Pending
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );

// File truncated for Stitch paste handoff. Full file is in the unzipped export folder.
```

## src/pages/MissionPlanner.tsx

```tsx
import { useEffect, useMemo, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { open, save as saveDialog } from "@tauri-apps/plugin-dialog";
import { readFile, readTextFile, writeTextFile } from "@tauri-apps/plugin-fs";
import { Link, useNavigate } from "react-router-dom";
import { CircleMarker, ImageOverlay, MapContainer, Polygon, Polyline, useMap, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import {
  AlertTriangle,
  Archive,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ClipboardCheck,
  Crosshair,
  Cpu,
  Download,
  FileInput,
  Flag,
  FolderOpen,
  HardDrive,
  Layers3,
  Loader2,
  Map as MapIcon,
  Navigation,
  Play,
  PlaneTakeoff,
  PanelBottom,
  RadioTower,
  Route,
  ScanSearch,
  Server,
  ShieldCheck,
  Terminal,
  Trash2,
  Upload as UploadIcon,
} from "lucide-react";
import { cmd } from "../lib/tauri";
import { useAppStore } from "../lib/store";
import { loadPipelineConfig } from "../lib/pipelineConfig";
import type { PipelineConfig } from "../lib/pipelineConfig";
import { cn, generateId } from "../lib/utils";
import { SupportBundleList } from "../components/SupportBundleList";
import type {
  BuildDroneBundleResult,
  Device,
  DronePositionUpdate,
  Region,
  SupportBundleFile,
  UploadProgress,
} from "../lib/types";

type UploadPayload = UploadProgress;
type Waypoint = { lat: number; lon: number };
type BundleElevationHealth = NonNullable<BuildDroneBundleResult["geospatial_health"]>["elevation"];
type BundleTerrainProfile = NonNullable<BuildDroneBundleResult["geospatial_health"]>["terrain_profile"];
type BundleMapQuality = NonNullable<BuildDroneBundleResult["geospatial_health"]>["map_quality"];
type PlanLayer = "mission" | "fence" | "rally" | "vision";
type MissionItemType = "takeoff" | "waypoint" | "land";
type PlanPoint = Waypoint & { id: string };
type MissionPlanStateStatus = "invalid" | "not_built" | "stale_bundle" | "not_uploaded" | "uploaded" | "bundle_ready";
type MissionItem = PlanPoint & {
  type: MissionItemType;
  altitudeM: number;
  speedMps: number;
  holdSec: number;
};
type MissionDefaults = {
  altitudeM: number;
  speedMps: number;
};
type EstimatorHealthState = "unchecked" | "ready" | "degraded";
type GnssDeniedReadiness = {
  satellite_source_disabled: boolean;
  map_position_reset: PlanPoint | null;
  heading_deg: number | null;
  home_position: PlanPoint | null;
  estimator_health: EstimatorHealthState;
  updated_at: string | null;
};
type PlannerReadinessCheck = {
  key: string;
  label: string;
  ok: boolean;
};
type GnssDeniedReadinessExport = GnssDeniedReadiness & {
  status: "ready" | "incomplete";
  checks: Array<{
    name: string;
    label: string;
    status: "passed" | "failed";
  }>;
};
type TerrainPlanningConstraints = {
  min_agl_m: number;
  max_terrain_relief_m: number;
  min_agl_to_gsd_ratio: number;
  max_route_segment_m: number;
};
type RouteSegmentEndpoint = Waypoint & {
  mission_item_id?: string;
  mission_item_index?: number;
  leg_start_index?: number;
  leg_end_index?: number;
  interpolated: boolean;
};
type RouteSegment = {
  id: string;
  sequence: number;
  start: RouteSegmentEndpoint;
  end: RouteSegmentEndpoint;
  distance_m: number;
  cumulative_start_m: number;
  cumulative_end_m: number;
  split_reason: "max_segment_m" | "mission_end";
};
type TerrainPlanningMetadata = {
  constraints: TerrainPlanningConstraints;
  offline_cache: {
    map_path: string | null;
    status: "ready" | "missing" | "not_selected";
  };
  route_segmentation: {
    max_segment_m: number;
    estimated_segment_count: number;
    mission_distance_m: number;
    split_required: boolean;
    longest_segment_m: number;
    segments: RouteSegment[];
  };
};
type TerrainConstraintStatus = "passed" | "failed" | "unknown";
type PlanFileSource = "imported" | "exported";
type PersistedMissionPlannerState = {
  lastBuiltFingerprint?: string | null;
  lastUploadedFingerprint?: string | null;
  lastBuiltAt?: string | null;
  lastUploadedAt?: string | null;
  planFilePath?: string | null;
  planFileFingerprint?: string | null;
  planFileSavedAt?: string | null;
  planFileSource?: PlanFileSource | null;
};
type MissionBounds = [[number, number], [number, number]];
type MissionPlanPayload = {
  version: string;
  groundStation: string;
  activeLayer: PlanLayer;
  region: {
    id: string;
    name: string;
    bounds: { lat_min: number; lat_max: number; lon_min: number; lon_max: number };
    source?: Region["source"];
    output_path: string;
    gsd_m_per_px?: number;
    georef_confidence?: number;
  } | null;
  vehicle: {
    autopilot?: Device["autopilot"];
    mavlink_endpoint?: string;
  };
  mission: {
    altitude_m: number;
    speed_mps: number;
    items: MissionItem[];
  };
  geofence: {
    polygon: PlanPoint[];
  };
  rally_points: PlanPoint[];
  vision: {
    checkpoints: PlanPoint[];
    pipeline: PipelineConfig["pipeline"];
    feature_method: PipelineConfig["featureMethod"];
    max_features: number;
  };
  gnss_denied: GnssDeniedReadinessExport;
  terrain_planning: TerrainPlanningMetadata;
};
type ImportedMissionPlanPayload = Partial<Omit<MissionPlanPayload, "gnss_denied">> & {
  gnss_denied?: GnssDeniedReadiness;
};

const DEFAULT_LOCAL_REPO = "";
const PLAN_VERSION = "0.3.0";
const MISSION_PLANNER_STATE_KEY = "drone_mission_planner_state_v1";
const DEFAULT_MISSION_DEFAULTS: MissionDefaults = {
  altitudeM: 35,
  speedMps: 4,
};
const DEFAULT_GNSS_DENIED_READINESS: GnssDeniedReadiness = {
  satellite_source_disabled: false,
  map_position_reset: null,
  heading_deg: null,
  home_position: null,
  estimator_health: "unchecked",
  updated_at: null,
};
const DEFAULT_TERRAIN_CONSTRAINTS: TerrainPlanningConstraints = {
  min_agl_m: 20,
  max_terrain_relief_m: 40,
  min_agl_to_gsd_ratio: 40,
  max_route_segment_m: 500,
};
const LAYER_META: Record<PlanLayer, { label: string; hint: string; icon: typeof Route }> = {
  mission: { label: "Mission", hint: "Takeoff, waypoints, and landing", icon: Route },
  fence: { label: "GeoFence", hint: "Optional safety boundary", icon: ShieldCheck },
  rally: { label: "Rally", hint: "Emergency rally points", icon: Flag },
  vision: { label: "Vision Map", hint: "Localization checkpoints", icon: ScanSearch },
};

function shellQuote(value: string) {
  return `'${value.replace(/'/g, "'\"'\"'")}'`;
}

function shellEnvValue(value: string) {
  if (/^\$(HOME|PWD)\//.test(value) && !/[\s"'`;&|<>]/.test(value)) return value;
  return shellQuote(value);
}

function runtimeStatusReadCommand(runtimeStatusRoot = "$HOME/DroneTransfer/outgoing/terrain-match") {
  return `VISION_NAV_RUNTIME_STATUS_ROOTS=${shellEnvValue(runtimeStatusRoot)} ./scripts/pi/read_runtime_status.sh`;
}

const SUPPORT_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/support-bundles";
const MODULE_SETUP_HANDOFF_KEY = "drone_module_setup_handoff";
const SUPPORT_EVIDENCE_ENV =
  'VISION_NAV_PX4_PARAMS="$HOME/px4.params" VISION_NAV_ARDUPILOT_PARAMS="$HOME/ardupilot.params" ';

function supportBundleCommand(remoteProject: string, remoteBundle: string, mavlinkEnv: string) {
  return [
    `cd ${shellQuote(remoteProject)}`,
    `VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ${mavlinkEnv}${SUPPORT_EVIDENCE_ENV}./scripts/pi/create_support_bundle.sh`,
    `latest=$(ls -t "$HOME/DroneTransfer/outgoing/support-bundles/"*.zip 2>/dev/null | head -n 1)`,
    `test -n "$latest"`,
    `echo "__VISION_NAV_SUPPORT_ZIP__=$latest"`,
  ].join(" && ");
}

function validateUploadedBundleCommand(remoteProject: string, remoteBundle: string) {
  return `cd ${shellQuote(remoteProject)} && VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ./scripts/pi/validate_terrain_bundle.sh`;
}

function parseSupportBundleZip(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_SUPPORT_ZIP__="))
    ?.replace("__VISION_NAV_SUPPORT_ZIP__=", "");
}

function formatHealthLabel(value?: string | number | null) {
  if (value === undefined || value === null || value === "") return "n/a";
  return String(value).replace(/_/g, " ");
}

function checksumBadgeClass(status?: string) {
  if (status === "passed") return "badge-green";
  if (status === "failed") return "badge-red";
  return "badge-yellow";

// File truncated for Stitch paste handoff. Full file is in the unzipped export folder.
```

## src/pages/Maps.tsx

```tsx
import { useEffect, useRef, useState, MutableRefObject } from "react";
import { MapContainer, TileLayer, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet-draw";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { exists, readTextFile } from "@tauri-apps/plugin-fs";
import { homeDir, join } from "@tauri-apps/api/path";
import {
  Download, FileImage, FolderOpen, Layers, Info, CheckCircle2, Loader2, X, FolderInput, Upload,
  Mountain,
} from "lucide-react";
import { cmd } from "../lib/tauri";
import { useAppStore } from "../lib/store";
import { generateId, cn } from "../lib/utils";
import type { BBox, DownloadProgress, Region, TileEstimate, TileSource } from "../lib/types";

const ESRI_SATELLITE =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const ESRI_LABELS =
  "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}";

function bboxAreaKm2(bbox: BBox): number {
  const latCenter = ((bbox.lat_min + bbox.lat_max) / 2) * (Math.PI / 180);
  const ns = (bbox.lat_max - bbox.lat_min) * 111.32;
  const ew = (bbox.lon_max - bbox.lon_min) * 111.32 * Math.cos(latCenter);
  return Math.abs(ns * ew);
}

type DrawMode = "rectangle" | "triangle" | "polygon";

const SOURCES: Record<TileSource, { label: string; maxZoom: number; free: boolean; description: string }> = {
  esri:   { label: "ESRI World Imagery", maxZoom: 19, free: true,  description: "No API key required. Global coverage." },
  mapbox: { label: "Mapbox Satellite",   maxZoom: 22, free: false, description: "Up to zoom 22. Sharpest imagery." },
  bing:   { label: "Bing Maps Aerial",   maxZoom: 20, free: false, description: "Up to zoom 20. Requires Bing API key." },
};

const DRAW_MODES: { mode: DrawMode; label: string; hint: string }[] = [
  { mode: "rectangle", label: "Rectangle", hint: "Click and drag to select a rectangular region" },
  { mode: "triangle",  label: "Triangle",  hint: "Click 3 corner points — shape closes automatically" },
  { mode: "polygon",   label: "Polygon",   hint: "Click to add points. Click the first point to close" },
];

const MAP_FILE_EXTENSIONS = ["png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "gif"];
const ELEVATION_FILE_EXTENSIONS = ["tif", "tiff"];
const EARTH_RADIUS_M = 6378137;

function pixelToLatLon(
  originLat: number,
  originLon: number,
  gsdMPerPx: number,
  originPixelX: number,
  originPixelY: number,
  rotationDeg: number,
  xPx: number,
  yPx: number,
): { lat: number; lon: number } {
  const dx = (xPx - originPixelX) * gsdMPerPx;
  const dy = (yPx - originPixelY) * gsdMPerPx;
  const theta = rotationDeg * Math.PI / 180;
  const east = dx * Math.cos(theta) - (-dy) * Math.sin(theta);
  const north = dx * Math.sin(theta) + (-dy) * Math.cos(theta);
  const lat = originLat + (north / EARTH_RADIUS_M) * (180 / Math.PI);
  const lon = originLon + (east / (EARTH_RADIUS_M * Math.max(Math.cos(originLat * Math.PI / 180), 1e-9))) * (180 / Math.PI);
  return { lat, lon };
}

function bboxFromGeoref(
  originLat: number,
  originLon: number,
  gsdMPerPx: number,
  widthPx: number,
  heightPx: number,
  originPixelX = 0,
  originPixelY = 0,
  rotationDeg = 0,
): BBox {
  const corners = [
    pixelToLatLon(originLat, originLon, gsdMPerPx, originPixelX, originPixelY, rotationDeg, 0, 0),
    pixelToLatLon(originLat, originLon, gsdMPerPx, originPixelX, originPixelY, rotationDeg, widthPx, 0),
    pixelToLatLon(originLat, originLon, gsdMPerPx, originPixelX, originPixelY, rotationDeg, widthPx, heightPx),
    pixelToLatLon(originLat, originLon, gsdMPerPx, originPixelX, originPixelY, rotationDeg, 0, heightPx),
  ];
  const lats = corners.map((corner) => corner.lat);
  const lons = corners.map((corner) => corner.lon);
  return {
    lat_min: Math.min(...lats),
    lat_max: Math.max(...lats),
    lon_min: Math.min(...lons),
    lon_max: Math.max(...lons),
  };
}

function isTiffPath(path: string): boolean {
  return /\.(tif|tiff)$/i.test(path);
}

function sourceFromMetadata(value: unknown): Region["source"] {
  return value === "esri" || value === "mapbox" || value === "bing" || value === "uploaded"
    ? value
    : "folder";
}

function defaultImportedOutputPath(filePath: string): string {
  const sep = Math.max(filePath.lastIndexOf("/"), filePath.lastIndexOf("\\"));
  const parent = sep >= 0 ? filePath.slice(0, sep) : ".";
  const filename = sep >= 0 ? filePath.slice(sep + 1) : filePath;
  const stem = filename.replace(/\.[^.]+$/, "") || "uploaded-map";
  return `${parent}/${stem}_drone_region`;
}

function slugifyPathSegment(value: string): string {
  return (value || "flight-region")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48) || "flight-region";
}

// ── Bing Maps custom tile layer (quadkey addressing) ──────────────────────────
function BingTileLayer({ apiKey }: { apiKey: string }) {
  const map = useMap();
  useEffect(() => {
    const BingLayer = (L.TileLayer as any).extend({
      getTileUrl(c: any) {
        let qk = "";
        for (let i = c.z; i > 0; i--) {
          let d = 0;
          const m = 1 << (i - 1);
          if (c.x & m) d += 1;
          if (c.y & m) d += 2;
          qk += d;
        }
        const s = (Math.abs(c.x) + Math.abs(c.y)) % 4;
        return `https://t${s}.ssl.ak.tiles.virtualearth.net/tiles/a${qk}.jpeg?g=7&token=${apiKey}`;
      },
    });
    const layer = new BingLayer("", { maxZoom: 20, attribution: "© Microsoft / Bing Maps" });
    layer.addTo(map);
    return () => { map.removeLayer(layer); };
  }, [map, apiKey]);
  return null;
}

// ── Draw handler — direct Leaflet API, no leaflet-draw toolbar ───────────────
// Rectangle: manual mousedown/mousemove/mouseup (L.Draw.Rectangle is unreliable
//            in WebView2 due to pointer-capture behaviour on Windows).
// Triangle/Polygon: L.Draw.Polygon with click-to-place vertices.
// drawKey increments each time a mode button is clicked, forcing a fresh session.
function DrawControlInner({
  onBBoxChange,
  featureGroupRef,
  mode,
  drawKey,
}: {
  onBBoxChange: (b: BBox | null) => void;
  featureGroupRef: MutableRefObject<L.FeatureGroup | null>;
  mode: DrawMode;
  drawKey: number;
}) {
  const map = useMap();
  const handlerRef = useRef<{ disable: () => void } | null>(null);

  useEffect(() => {
    if (!featureGroupRef.current) {
      featureGroupRef.current = L.featureGroup().addTo(map);
    }
    const fg = featureGroupRef.current;
    const shapeStyle = { color: "#06B6D4", weight: 2, fillOpacity: 0.12 };

    handlerRef.current?.disable();

    if (mode === "rectangle") {
      const container = map.getContainer();
      container.style.cursor = "crosshair";

      let startLatLng: L.LatLng | null = null;
      let previewRect: L.Rectangle | null = null;

      const onMouseDown = (e: L.LeafletMouseEvent) => {
        startLatLng = e.latlng;
        map.dragging.disable();
        fg.clearLayers();
        onBBoxChange(null);
      };

      const onMouseMove = (e: L.LeafletMouseEvent) => {
        if (!startLatLng) return;
        if (previewRect) fg.removeLayer(previewRect);
        previewRect = L.rectangle(
          [
            [startLatLng.lat, startLatLng.lng],
            [e.latlng.lat, e.latlng.lng],
          ],
          shapeStyle,
        );
        fg.addLayer(previewRect);
      };

      const onMouseUp = (e: L.LeafletMouseEvent) => {
        if (!startLatLng) return;
        map.dragging.enable();
        const bounds = L.latLngBounds(startLatLng, e.latlng);
        if (bounds.getNorth() !== bounds.getSouth()) {
          onBBoxChange({
            lat_min: bounds.getSouth(),
            lat_max: bounds.getNorth(),
            lon_min: bounds.getWest(),
            lon_max: bounds.getEast(),
          });
        }
        startLatLng = null;
        previewRect = null;
      };

      map.on("mousedown", onMouseDown);
      map.on("mousemove", onMouseMove);
      map.on("mouseup", onMouseUp);

      handlerRef.current = {
        disable: () => {
          map.off("mousedown", onMouseDown);
          map.off("mousemove", onMouseMove);
          map.off("mouseup", onMouseUp);
          map.dragging.enable();
          container.style.cursor = "";
        },
      };
    } else {
      const handler = new (L.Draw as any).Polygon(map, {
        shapeOptions: shapeStyle,
        allowIntersection: false,
        showArea: false,
      });
      handler.enable();

      let vertexCount = 0;
      const onDrawStart = () => { vertexCount = 0; };
      const onDrawVertex = () => {
        if (mode !== "triangle") return;
        vertexCount += 1;
        if (vertexCount >= 3) {
          vertexCount = 0;
          setTimeout(() => handler._finishShape?.(), 50);
        }
      };
      const onCreate = (e: any) => {
        fg.clearLayers();
        fg.addLayer(e.layer);
        const bounds: L.LatLngBounds = e.layer.getBounds();
        onBBoxChange({
          lat_min: bounds.getSouth(),
          lat_max: bounds.getNorth(),
          lon_min: bounds.getWest(),
          lon_max: bounds.getEast(),
        });
      };

      map.on(L.Draw.Event.DRAWSTART,  onDrawStart);
      map.on(L.Draw.Event.DRAWVERTEX, onDrawVertex);

// File truncated for Stitch paste handoff. Full file is in the unzipped export folder.
```

## src/pages/Devices.tsx

```tsx
import { useEffect, useState } from "react";
import {
  Server, HardDrive, Plus, Trash2, Edit2, Wifi, CheckCircle2, XCircle,
  Loader2, Eye, EyeOff, ShieldCheck, ShieldAlert, FolderOpen, KeyRound, Lock,
  Terminal, Play, Square, FileText, ChevronDown, ChevronUp,
  Cable, Cpu, BookOpen, Save, SlidersHorizontal, Archive, Copy,
} from "lucide-react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { cmd } from "../lib/tauri";
import { useAppStore } from "../lib/store";
import { cn, generateId } from "../lib/utils";
import {
  candidateHost,
  candidateName,
  discoveryChecklistText,
  discoveryStatusSummary,
  discoveryTroubleshooting,
  loadDiscoveryHistory,
  mergeDiscoveryHistory,
  networkHintKey,
  networkHintLabel,
  saveDiscoveryHistory,
  selectedNetworkHint,
} from "../lib/discovery";
import { SupportBundleList } from "../components/SupportBundleList";
import { ModuleSetup } from "./PiSetup";
import type { Device, LocalNetworkHint, PiDiscoveryCandidate, SupportBundleFile } from "../lib/types";

type TestResult = {
  ok: boolean;
  msg: string;
  fingerprint?: string;
  fingerprintChanged?: boolean;
};
type TestState = "idle" | "testing" | TestResult;

function shellQuote(value: string) {
  return `'${value.replace(/'/g, "'\"'\"'")}'`;
}

function shellEnvValue(value: string) {
  if (/^\$(HOME|PWD)\//.test(value) && !/[\s"'`;&|<>]/.test(value)) return value;
  return shellQuote(value);
}

function runtimeStatusReadCommand(runtimeStatusRoot = "$HOME/DroneTransfer/outgoing/terrain-match") {
  return `VISION_NAV_RUNTIME_STATUS_ROOTS=${shellEnvValue(runtimeStatusRoot)} ./scripts/pi/read_runtime_status.sh`;
}

const SUPPORT_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/support-bundles";
const MODULE_SETUP_HANDOFF_KEY = "drone_module_setup_handoff";
const SUPPORT_EVIDENCE_ENV =
  'VISION_NAV_PX4_PARAMS="$HOME/px4.params" VISION_NAV_ARDUPILOT_PARAMS="$HOME/ardupilot.params" ';

function readModuleSetupHandoffDeviceId() {
  try {
    const raw = sessionStorage.getItem(MODULE_SETUP_HANDOFF_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { action?: string; device_id?: string };
    if (parsed.action !== "bench-report" || !parsed.device_id) return null;
    return parsed.device_id;
  } catch {
    return null;
  }
}

function supportBundleCommand(remotePath: string, remoteBundle: string, mavlinkEnv: string) {
  return [
    `cd ${shellQuote(remotePath)}`,
    `VISION_NAV_BUNDLE=${shellQuote(remoteBundle)} ${mavlinkEnv}${SUPPORT_EVIDENCE_ENV}./scripts/pi/create_support_bundle.sh`,
    `latest=$(ls -t "$HOME/DroneTransfer/outgoing/support-bundles/"*.zip 2>/dev/null | head -n 1)`,
    `test -n "$latest"`,
    `echo "__VISION_NAV_SUPPORT_ZIP__=$latest"`,
  ].join(" && ");
}

function parseSupportBundleZip(output: string) {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("__VISION_NAV_SUPPORT_ZIP__="))
    ?.replace("__VISION_NAV_SUPPORT_ZIP__=", "");
}

function defaultRemotePath(username = "user") {
  return `/home/${username || "user"}/Drone`;
}

function deviceWithRuntimeDefaults(device: Device): Device {
  const username = device.username ?? "user";
  return {
    ...device,
    remote_project_path: device.remote_project_path ?? defaultRemotePath(username),
    mavlink_endpoint: device.mavlink_endpoint ?? "serial:/dev/ttyAMA0:921600",
    autopilot: device.autopilot ?? "px4",
  };
}

function DeviceConfigurationPanel({
  device,
  devices,
  updateDevice,
}: {
  device: Device;
  devices: Device[];
  updateDevice: (device: Device) => void;
}) {
  const normalized = deviceWithRuntimeDefaults(device);
  const [form, setForm] = useState({
    remotePath: normalized.remote_project_path ?? defaultRemotePath(normalized.username),
    mavlinkEndpoint: normalized.mavlink_endpoint ?? "serial:/dev/ttyAMA0:921600",
    autopilot: normalized.autopilot ?? "px4",
  });
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const next = deviceWithRuntimeDefaults(device);
    setForm({
      remotePath: next.remote_project_path ?? defaultRemotePath(next.username),
      mavlinkEndpoint: next.mavlink_endpoint ?? "serial:/dev/ttyAMA0:921600",
      autopilot: next.autopilot ?? "px4",
    });
    setSaved(false);
  }, [device.id, device.remote_project_path, device.mavlink_endpoint, device.autopilot]);

  const saveConfiguration = async () => {
    const { vision_pipeline: _visionPipeline, feature_method: _featureMethod, ...deviceWithoutPipeline } = device;
    const updated: Device = {
      ...deviceWithoutPipeline,
      remote_project_path: form.remotePath || defaultRemotePath(device.username),
      mavlink_endpoint: form.mavlinkEndpoint,
      autopilot: form.autopilot as "px4" | "ardupilot",
    };
    updateDevice(updated);
    await cmd.saveDevices(devices.map((candidate) => (candidate.id === updated.id ? updated : candidate)));
    setSaved(true);
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Module project path</label>
          <input
            className="input-field font-mono text-xs"
            value={form.remotePath}
            onChange={(e) => {
              setForm((value) => ({ ...value, remotePath: e.target.value }));
              setSaved(false);
            }}
          />
        </div>
        <div>
          <label className="label">MAVLink endpoint</label>
          <input
            className="input-field font-mono text-xs"
            value={form.mavlinkEndpoint}
            onChange={(e) => {
              setForm((value) => ({ ...value, mavlinkEndpoint: e.target.value }));
              setSaved(false);
            }}
            placeholder="serial:/dev/ttyAMA0:921600"
          />
        </div>
      </div>

      <div className="max-w-xs">
        <label className="label">Flight controller</label>
        <div className="grid grid-cols-2 gap-2">
          {(["px4", "ardupilot"] as const).map((value) => (
            <button
              key={value}
              onClick={() => {
                setForm((current) => ({ ...current, autopilot: value }));
                setSaved(false);
              }}
              className={cn(
                "py-2 rounded-lg border text-xs font-medium transition-colors",
                form.autopilot === value ? "bg-cyan-500/10 border-cyan-500/40 text-cyan-400" : "border-border text-slate-400",
              )}
            >
              {value === "px4" ? "PX4" : "ArduPilot"}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button onClick={saveConfiguration} className="btn-primary text-xs py-1.5 px-3">
          <Save size={12} /> Save Configuration
        </button>
        {saved && <span className="text-xs text-emerald-400">Saved</span>}
      </div>
    </div>
  );
}

export function Devices() {
  const { devices, addDevice, updateDevice, removeDevice, activeDeviceId } = useAppStore();
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [testStates, setTestStates] = useState<Record<string, TestState>>({});
  const [showPass, setShowPass] = useState(false);
  const [showPassphrase, setShowPassphrase] = useState(false);
  const [controlOpenId, setControlOpenId] = useState<string | null>(null);
  const [controlTab, setControlTab] = useState<Record<string, "control" | "config" | "setup">>({});
  const [cmdOutputs, setCmdOutputs] = useState<Record<string, string>>({});
  const [cmdRunning, setCmdRunning] = useState<string | null>(null);
  const [supportBundles, setSupportBundles] = useState<SupportBundleFile[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryCandidates, setDiscoveryCandidates] = useState<PiDiscoveryCandidate[]>(() => loadDiscoveryHistory());
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [networkHints, setNetworkHints] = useState<LocalNetworkHint[]>([]);
  const [selectedAdapterKey, setSelectedAdapterKey] = useState("");
  const [discoveryCopied, setDiscoveryCopied] = useState(false);

  const refreshSupportBundles = async () => {
    setSupportBundles(await cmd.listSupportBundles(SUPPORT_DOWNLOAD_DIR));
  };

  useEffect(() => {
    refreshSupportBundles().catch(() => setSupportBundles([]));
    cmd.localNetworkHints().then(setNetworkHints).catch(() => setNetworkHints([]));
  }, []);

  useEffect(() => {
    const handoffDeviceId = readModuleSetupHandoffDeviceId();
    if (!handoffDeviceId || !devices.some((device) => device.id === handoffDeviceId)) return;
    setControlOpenId(handoffDeviceId);
    setControlTab((tabs) => ({ ...tabs, [handoffDeviceId]: "setup" }));
  }, [devices]);

  const runPiCommand = async (d: Device, label: string, command: string) => {
    if (!d.host || !d.auth) return;
    setCmdRunning(d.id);
    setCmdOutputs((o) => ({ ...o, [d.id]: `$ ${label}\n` }));
    try {
      const r = await cmd.sshRunCommand(
        d.host, d.port ?? 22, d.username ?? "user", d.auth, command
      );
      const output = [r.stdout, r.stderr].filter(Boolean).join("\n").trim();
      setCmdOutputs((o) => ({
        ...o,
        [d.id]: `$ ${label}\n${output || "(no output)"}\n[exit ${r.exit_code}]`,
      }));
    } catch (e) {
      setCmdOutputs((o) => ({ ...o, [d.id]: `$ ${label}\nERROR: ${e}` }));
    } finally {
      setCmdRunning(null);
    }
  };

  const createAndDownloadSupportBundle = async (d: Device, remotePath: string, remoteBundle: string, mavlinkEnv: string) => {
    if (!d.host || !d.auth) return;
    setCmdRunning(d.id);
    setCmdOutputs((o) => ({ ...o, [d.id]: "$ create support bundle\n" }));
    try {
      const r = await cmd.sshRunCommand(
        d.host,
        d.port ?? 22,

// File truncated for Stitch paste handoff. Full file is in the unzipped export folder.
```

## src/pages/VisionPipeline.tsx

```tsx
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
```

## src/pages/SystemStatus.tsx

```tsx
import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Cpu, Map, Radio, RefreshCw, Server, Upload } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { loadPipelineConfig } from "../lib/pipelineConfig";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import type { DronePositionUpdate } from "../lib/types";
import { cn } from "../lib/utils";

function statusText(value?: string | null) {
  return value ? value.replace(/_/g, " ") : "unknown";
}

function sourceClass(position: DronePositionUpdate | null) {
  if (position?.source === "gps" && position.status === "accepted") return "badge-green";
  if (position?.source === "vision" && position.status === "accepted") return "badge-cyan";
  if (position?.status === "degraded") return "badge-yellow";
  return "badge-red";
}

export function SystemStatus() {
  const navigate = useNavigate();
  const { activeDeviceId, devices, regions } = useAppStore();
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const downloadedRegions = regions.filter((region) => region.last_downloaded);
  const pipelineConfig = useMemo(() => loadPipelineConfig(), []);
  const [position, setPosition] = useState<DronePositionUpdate | null>(null);
  const [telemetryMessage, setTelemetryMessage] = useState("Waiting for position packets");
  const [refreshing, setRefreshing] = useState(false);
  const port = Number(localStorage.getItem("vision_nav_position_udp_port") || 17660);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        const update = await cmd.receivePositionUpdate(port, 120);
        if (cancelled) return;
        if (update) {
          setPosition(update);
          setTelemetryMessage(`packet ${update.sequence ?? "n/a"} from ${statusText(update.source)}`);
        }
      } catch (error) {
        if (!cancelled) setTelemetryMessage(`telemetry unavailable: ${error}`);
      } finally {
        if (!cancelled) timer = window.setTimeout(poll, 1200);
      }
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [port]);

  const refreshTelemetry = async () => {
    setRefreshing(true);
    try {
      const update = await cmd.receivePositionUpdate(port, 400);
      setPosition(update);
      setTelemetryMessage(update ? `packet ${update.sequence ?? "n/a"} received` : "no packet received");
    } catch (error) {
      setTelemetryMessage(`telemetry unavailable: ${error}`);
    } finally {
      setRefreshing(false);
    }
  };

  const readiness = [
    { label: "Active device", ok: Boolean(activeDevice), detail: activeDevice?.name ?? "none selected", to: "/vehicle-manager" },
    { label: "Downloaded map", ok: downloadedRegions.length > 0, detail: `${downloadedRegions.length} ready`, to: "/maps" },
    { label: "Vision pipeline", ok: true, detail: `${pipelineConfig.pipeline} / ${pipelineConfig.featureMethod}`, to: "/camera-vision" },
    { label: "Position telemetry", ok: Boolean(position), detail: telemetryMessage, to: "/mission-planner" },
  ];

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="section-title">System Status & Diagnostics</h1>
          <p className="text-sm text-slate-400 mt-1">
            Live readiness for the desktop app, runtime module, maps, vision pipeline, and position telemetry.
          </p>
        </div>
        <button onClick={refreshTelemetry} className="btn-secondary" disabled={refreshing}>
          <RefreshCw size={15} className={refreshing ? "animate-spin" : ""} /> Refresh Telemetry
        </button>
      </div>

      <div className="grid grid-cols-4 gap-3">
        {readiness.map((item) => (
          <button
            key={item.label}
            onClick={() => navigate(item.to)}
            className={cn("card text-left hover:border-border-strong", item.ok ? "border-emerald-500/20" : "border-amber-500/20")}
          >
            <div className="flex items-center gap-2 text-xs font-medium">
              <span className={cn("ops-led", item.ok ? "ops-led-ready" : "ops-led-warning")} />
              <span className={item.ok ? "text-emerald-300" : "text-amber-300"}>{item.label}</span>
            </div>
            <div className="mt-2 truncate font-mono text-xs text-slate-400">{item.detail}</div>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-[1fr_0.85fr] gap-4">
        <section className="ops-panel p-4 space-y-3">
          <h2 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
            <Radio size={15} className="text-cyan-400" /> Runtime Position Telemetry
          </h2>
          <div className="grid grid-cols-4 gap-2">
            <div className="ops-tile">
              <div className="ops-label">Source</div>
              <div className={sourceClass(position)}>{statusText(position?.source)}</div>
            </div>
            <div className="ops-tile">
              <div className="ops-label">Status</div>
              <div className="ops-value">{statusText(position?.status)}</div>
            </div>
            <div className="ops-tile">
              <div className="ops-label">Confidence</div>
              <div className="ops-value">{position?.confidence != null ? `${Math.round(position.confidence * 100)}%` : "n/a"}</div>
            </div>
            <div className="ops-tile">
              <div className="ops-label">UDP Port</div>
              <div className="ops-value">{port}</div>
            </div>
          </div>
          <pre className="ops-console min-h-28">
{position
  ? JSON.stringify(position, null, 2)
  : `$ position telemetry\n${telemetryMessage}\nListening on UDP ${port}`}
          </pre>
        </section>

        <section className="ops-panel p-4 space-y-3">
          <h2 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
            <Activity size={15} className="text-cyan-400" /> Operator Shortcuts
          </h2>
          <div className="grid grid-cols-2 gap-2">
            <button className="btn-secondary justify-start" onClick={() => navigate("/vehicle-manager")}>
              <Server size={15} /> Vehicle Manager
            </button>
            <button className="btn-secondary justify-start" onClick={() => navigate("/camera-vision")}>
              <Cpu size={15} /> Camera & Vision
            </button>
            <button className="btn-secondary justify-start" onClick={() => navigate("/maps")}>
              <Map size={15} /> Map Library
            </button>
            <button className="btn-secondary justify-start" onClick={() => navigate("/mission-planner")}>
              <Upload size={15} /> Mission Planner
            </button>
          </div>
          <div className="rounded-none border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-100/80">
            <div className="flex items-center gap-2 font-medium text-amber-300">
              <AlertTriangle size={13} /> Hardware-first mode
            </div>
            <p className="mt-1">
              SITL/ROS surfaces are intentionally hidden. Use Devices, Mission Planner, and Flight Review for real bench and field workflows.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
```

## src/pages/FlightReview.tsx

```tsx
import { useEffect, useMemo, useState } from "react";
import { Archive, Clock, FileDown, RefreshCw } from "lucide-react";
import { SupportBundleList } from "../components/SupportBundleList";
import { cmd } from "../lib/tauri";
import type { SupportBundleFile } from "../lib/types";
import { formatDate } from "../lib/utils";

const SUPPORT_DOWNLOAD_DIR = "~/DroneTransfer/from-pi/support-bundles";

function formatSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

export function FlightReview() {
  const [bundles, setBundles] = useState<SupportBundleFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refreshBundles = async () => {
    setLoading(true);
    setMessage(null);
    try {
      setBundles(await cmd.listSupportBundles(SUPPORT_DOWNLOAD_DIR));
    } catch (error) {
      setMessage(`Could not list support bundles: ${error}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshBundles();
  }, []);

  const totals = useMemo(() => {
    const bytes = bundles.reduce((sum, bundle) => sum + bundle.size_bytes, 0);
    const passed = bundles.filter((bundle) => bundle.summary?.bundle_health_status === "passed").length;
    const failed = bundles.filter((bundle) => bundle.summary?.bundle_health_status === "failed").length;
    return { bytes, failed, passed };
  }, [bundles]);

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="section-title">Flight Review & History</h1>
          <p className="text-sm text-slate-400 mt-1">
            Review downloaded support bundles, runtime logs, evidence, and bench or field reports.
          </p>
        </div>
        <button className="btn-secondary" onClick={refreshBundles} disabled={loading}>
          <RefreshCw size={15} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <div className="ops-tile">
          <div className="ops-label">Bundles</div>
          <div className="ops-value">{bundles.length}</div>
        </div>
        <div className="ops-tile">
          <div className="ops-label">Passed</div>
          <div className="ops-value text-emerald-300">{totals.passed}</div>
        </div>
        <div className="ops-tile">
          <div className="ops-label">Failed</div>
          <div className="ops-value text-red-300">{totals.failed}</div>
        </div>
        <div className="ops-tile">
          <div className="ops-label">Storage</div>
          <div className="ops-value">{formatSize(totals.bytes)}</div>
        </div>
      </div>

      {message && (
        <div className="rounded-none border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-sm text-amber-200">
          {message}
        </div>
      )}

      {bundles.length === 0 ? (
        <div className="card py-12 text-center">
          <Archive size={34} className="mx-auto mb-3 text-slate-600" />
          <div className="text-sm font-medium text-slate-300">No support bundles downloaded yet</div>
          <p className="mt-1 text-xs text-slate-500">
            Create or download support bundles from Mission Planner or Vehicle Manager after bench and field runs.
          </p>
          <div className="mt-4 flex justify-center gap-2 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1"><FileDown size={12} /> {SUPPORT_DOWNLOAD_DIR}</span>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="ops-panel p-3">
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <Clock size={13} />
              Latest bundle: {formatDate(
                bundles[0]?.modified_unix_ms
                  ? new Date(bundles[0].modified_unix_ms).toISOString()
                  : undefined,
              )}
            </div>
          </div>
          <SupportBundleList bundles={bundles} downloadDir={SUPPORT_DOWNLOAD_DIR} onChanged={refreshBundles} />
        </div>
      )}
    </div>
  );
}
```

## src/lib/tauri.ts

```tsx
import type {
  BBox,
  BuildDroneBundleRequest,
  BuildDroneBundleResult,
  CommandResult,
  Device,
  DownloadFileResult,
  DownloadProgress,
  DownloadTilesResult,
  DronePositionUpdate,
  ExtractedSupportBundleArtifact,
  FieldCollectionPlanFile,
  FieldEvidenceReportFile,
  FieldEvidenceTemplateFile,
  FieldLogCaptureReportFile,
  FeatureMethodBenchmarkReportFile,
  ImportElevationAssetsRequest,
  ImportElevationAssetsResult,
  ImportMapFileRequest,
  ImportMapFileResult,
  LocalNetworkHint,
  PiDiscoveryCandidate,
  Profile,
  Px4PrereqReportFile,
  Px4ReceiverReportFile,
  RosbagExportValidationReportFile,
  Region,
  AutonomyEvidenceWorkflowReportFile,
  AutonomyReadinessReportFile,
  SupportBundleFile,
  SupportBundleDetails,
  ThresholdTuningReportFile,
  TileEstimate,
} from "./types";

const DEV_PROFILE: Profile = {
  accent_color: "#06B6D4",
  email: "",
  name: "Izrael",
  onboarding_complete: true,
  org: "Drone Vision Nav",
};

function hasTauriRuntime() {
  if (typeof window === "undefined") return false;
  const tauriInternals = (
    window as Window & { __TAURI_INTERNALS__?: { invoke?: unknown } }
  ).__TAURI_INTERNALS__;
  return typeof tauriInternals?.invoke === "function";
}

function readLocalJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? { ...fallback, ...JSON.parse(raw) } : fallback;
  } catch {
    return fallback;
  }
}

function readLocalArray<T>(key: string): T[] {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function writeLocalJson(key: string, value: unknown) {
  localStorage.setItem(key, JSON.stringify(value));
}

function estimateTilesFallback(args?: Record<string, unknown>): TileEstimate {
  const bbox = args?.bbox as BBox | undefined;
  const zoom = Number(args?.zoom ?? 16);
  const latSpan = bbox ? Math.max(0, bbox.lat_max - bbox.lat_min) : 0;
  const lonSpan = bbox ? Math.max(0, bbox.lon_max - bbox.lon_min) : 0;
  const scale = Math.max(1, 2 ** Math.max(0, zoom - 12));
  const nx = Math.max(1, Math.ceil(lonSpan * scale * 12));
  const ny = Math.max(1, Math.ceil(latSpan * scale * 12));
  const tile_count = nx * ny;
  return {
    estimated_mb: tile_count * 0.18,
    gsd_m_per_px: 156543.03392 / 2 ** zoom,
    nx,
    ny,
    tile_count,
    too_large: tile_count > 5000,
  };
}

async function fallbackInvoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  switch (command) {
    case "load_profile":
      return readLocalJson("drone_dev_profile", DEV_PROFILE) as T;
    case "save_profile":
      writeLocalJson("drone_dev_profile", args?.profile ?? DEV_PROFILE);
      return undefined as T;
    case "load_devices":
      return readLocalArray<Device>("drone_dev_devices") as T;
    case "save_devices":
      writeLocalJson("drone_dev_devices", args?.devices ?? []);
      return undefined as T;
    case "load_regions":
      return readLocalArray<Region>("drone_dev_regions") as T;
    case "save_regions":
      writeLocalJson("drone_dev_regions", args?.regions ?? []);
      return undefined as T;
    case "estimate_tiles":
      return estimateTilesFallback(args) as T;
    case "local_network_hints":
    case "discover_pi_devices":
    case "list_support_bundles":
      return [] as T;
    case "receive_position_update":
      return null as T;
    default:
      throw new Error(`Command ${command} requires the Tauri desktop runtime.`);
  }
}

function invokeCommand<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  if (!hasTauriRuntime()) {
    return fallbackInvoke<T>(command, args);
  }
  return import("@tauri-apps/api/core").then(({ invoke }) => invoke<T>(command, args));
}

export const cmd = {
  loadProfile: () => invokeCommand<Profile>("load_profile"),
  saveProfile: (profile: Profile) => invokeCommand<void>("save_profile", { profile }),
  loadDevices: () => invokeCommand<Device[]>("load_devices"),
  saveDevices: (devices: Device[]) => invokeCommand<void>("save_devices", { devices }),
  loadRegions: () => invokeCommand<Region[]>("load_regions"),
  saveRegions: (regions: Region[]) => invokeCommand<void>("save_regions", { regions }),
  estimateTiles: (bbox: BBox, zoom: number) =>
    invokeCommand<TileEstimate>("estimate_tiles", { bbox, zoom }),
  downloadTiles: (bbox: BBox, zoom: number, outputDir: string, source = "esri", apiKey?: string) =>
    invokeCommand<DownloadTilesResult>("download_tiles", { bbox, zoom, outputDir, source, apiKey }),
  buildDroneBundle: (request: BuildDroneBundleRequest) =>
    invokeCommand<BuildDroneBundleResult>("build_drone_bundle", { request }),
  importMapFile: (request: ImportMapFileRequest) =>
    invokeCommand<ImportMapFileResult>("import_map_file", { request }),
  importElevationAssets: (request: ImportElevationAssetsRequest) =>
    invokeCommand<ImportElevationAssetsResult>("import_elevation_assets", { request }),
  discoverPiDevices: (seedHosts: string[], port = 22) =>
    invokeCommand<PiDiscoveryCandidate[]>("discover_pi_devices", { seedHosts, port }),
  localNetworkHints: () => invokeCommand<LocalNetworkHint[]>("local_network_hints"),
  testSshConnection: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"]
  ) => invokeCommand<{ ok: boolean; message: string; server_banner?: string; fingerprint?: string }>(
    "test_ssh_connection",
    { host, port, username, auth }
  ),
  sshRunCommand: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    command: string
  ) => invokeCommand<CommandResult>("ssh_run_command", { host, port, username, auth, command }),
  sshUploadFiles: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    localPaths: string[],
    remoteDir: string
  ) => invokeCommand<void>("ssh_upload_files", { host, port, username, auth, localPaths, remoteDir }),
  sshUploadDirectory: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    localDir: string,
    remoteDir: string
  ) => invokeCommand<void>("ssh_upload_directory", { host, port, username, auth, localDir, remoteDir }),
  sshUploadProject: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    localDir: string,
    remoteDir: string
  ) => invokeCommand<void>("ssh_upload_project", { host, port, username, auth, localDir, remoteDir }),
  sshDownloadFile: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    remotePath: string,
    localDir: string
  ) => invokeCommand<DownloadFileResult>("ssh_download_file", { host, port, username, auth, remotePath, localDir }),
  sshCaptureCameraFrame: (
    host: string,
    port: number,
    username: string,
    auth: Device["auth"],
    remoteProjectPath: string,
    width: number,
    height: number,
    timeoutMs: number
  ) => invokeCommand<{
    mime_type: string;
    base64_data: string;
    remote_path: string;
    stdout: string;
    stderr: string;
  }>("ssh_capture_camera_frame", {
    host,
    port,
    username,
    auth,
    remoteProjectPath,
    width,
    height,
    timeoutMs,
  }),
  readYamlConfig: (path: string) => invokeCommand<Record<string, unknown>>("read_yaml_config", { path }),
  writeYamlConfig: (path: string, data: Record<string, unknown>) =>
    invokeCommand<void>("write_yaml_config", { path, data }),
  listYamlConfigs: (dir: string) => invokeCommand<string[]>("list_yaml_configs", { dir }),
  listAutonomyReadinessReports: (dir: string) =>
    invokeCommand<AutonomyReadinessReportFile[]>("list_autonomy_readiness_reports", { dir }),
  listAutonomyEvidenceWorkflowReports: (dir: string) =>
    invokeCommand<AutonomyEvidenceWorkflowReportFile[]>("list_autonomy_evidence_workflow_reports", { dir }),
  listFieldEvidenceReports: (dir: string) =>
    invokeCommand<FieldEvidenceReportFile[]>("list_field_evidence_reports", { dir }),
  listFieldCollectionPlans: (dir: string) =>
    invokeCommand<FieldCollectionPlanFile[]>("list_field_collection_plans", { dir }),
  listFieldEvidenceTemplates: (dir: string) =>
    invokeCommand<FieldEvidenceTemplateFile[]>("list_field_evidence_templates", { dir }),
  listFeatureMethodBenchmarkReports: (dir: string) =>
    invokeCommand<FeatureMethodBenchmarkReportFile[]>("list_feature_method_benchmark_reports", { dir }),
  listPx4PrereqReports: (dir: string) =>
    invokeCommand<Px4PrereqReportFile[]>("list_px4_prereq_reports", { dir }),
  listPx4ReceiverReports: (dir: string) =>
    invokeCommand<Px4ReceiverReportFile[]>("list_px4_receiver_reports", { dir }),
  listRosbagExportValidationReports: (dir: string) =>
    invokeCommand<RosbagExportValidationReportFile[]>("list_rosbag_export_validation_reports", { dir }),
  listFieldLogCaptureReports: (dir: string) =>
    invokeCommand<FieldLogCaptureReportFile[]>("list_field_log_capture_reports", { dir }),
  listThresholdTuningReports: (dir: string) =>
    invokeCommand<ThresholdTuningReportFile[]>("list_threshold_tuning_reports", { dir }),
  listSupportBundles: (dir: string) => invokeCommand<SupportBundleFile[]>("list_support_bundles", { dir }),
  revealSupportBundle: (path: string) => invokeCommand<void>("reveal_support_bundle", { path }),
  deleteSupportBundle: (path: string) => invokeCommand<void>("delete_support_bundle", { path }),
  runLocalAutonomyReadinessAudit: (repoDir: string, downloadRoot?: string) =>
    invokeCommand<CommandResult>("run_local_autonomy_readiness_audit", { repoDir, downloadRoot }),
  runLocalPx4SitlPrereqSetup: (repoDir: string, downloadRoot?: string) =>
    invokeCommand<CommandResult>("run_local_px4_sitl_prereq_setup", { repoDir, downloadRoot }),
  runLocalPx4SitlReceiverCapture: (repoDir: string, downloadRoot?: string) =>
    invokeCommand<CommandResult>("run_local_px4_sitl_receiver_capture", { repoDir, downloadRoot }),
  runLocalRosbag2CliReview: (repoDir: string, downloadRoot?: string) =>
    invokeCommand<CommandResult>("run_local_rosbag2_cli_review", { repoDir, downloadRoot }),
  readSupportBundleDetails: (path: string) =>

// File truncated for Stitch paste handoff. Full file is in the unzipped export folder.
```

## src/lib/types.ts

```tsx
export interface Profile {
  name: string;
  email: string;
  org: string;
  accent_color: string;
  onboarding_complete: boolean;
  mapbox_key?: string;
  bing_key?: string;
}

export type TileSource = "esri" | "mapbox" | "bing";
export type MapSource = TileSource | "uploaded" | "folder";

export type DeviceKind = "pi5" | "local";
export type AuthMethod = "password" | "key";
export type VisionPipeline = "classical" | "neural";
export type FeatureMethod = "orb" | "akaze" | "sift";

export interface Device {
  id: string;
  name: string;
  kind: DeviceKind;
  host?: string;
  port?: number;
  username?: string;
  auth?: { type: "Password"; password: string } | { type: "Key"; key_path: string; passphrase?: string };
  remote_project_path?: string;
  known_fingerprint?: string;
  mavlink_endpoint?: string; // e.g. "serial:/dev/ttyAMA0:921600" | "udp:14550" | "tcp:host:port"
  autopilot?: "px4" | "ardupilot";
  vision_pipeline?: VisionPipeline;
  feature_method?: FeatureMethod;
}

export interface Region {
  id: string;
  name: string;
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
  zoom: number;
  source?: MapSource;
  output_path: string;
  last_downloaded?: string;
  tile_count?: number;
  gsd_m_per_px?: number;
  georef_source?: string;
  georef_confidence?: number;
  georef_crs?: string;
  file_size_mb?: number;
  location_label?: string;
  elevation_dem_path?: string;
  elevation_dsm_path?: string;
  elevation_asset_count?: number;
}

export interface ModelSet {
  id: string;
  name: string;
  superpoint_path: string;
  lightglue_path: string;
  is_active: boolean;
  downloaded: boolean;
}

export interface TileEstimate {
  tile_count: number;
  nx: number;
  ny: number;
  estimated_mb: number;
  gsd_m_per_px: number;
  too_large: boolean;
}

export interface DownloadTilesResult {
  mosaic_path: string;
  metadata_path: string;
  width_px: number;
  height_px: number;
  gsd_m_per_px: number;
  origin_lat: number;
  origin_lon: number;
  tile_count: number;
  georef_source: string;
  georef_confidence: number;
  georef_crs: string;
}

export interface DownloadProgress {
  current: number;
  total: number;
  percent: number;
  tile_x: number;
  tile_y: number;
}

export interface UploadProgress {
  file: string;
  bytes_sent: number;
  total_bytes: number;
  percent: number;
}

export interface DronePositionUpdate {
  schema_version: "vision_nav_position_update_v1";
  timestamp_utc?: string;
  sequence?: number;
  status?: "accepted" | "degraded" | "unavailable" | string;
  source?: "gps" | "vision" | "gps_degraded" | "none" | string;
  source_priority?: string;
  lat_lon?: { lat?: number | null; lon?: number | null };
  altitude_m?: number | null;
  local_enu_m?: { x?: number | null; y?: number | null; z?: number | null };
  confidence?: number | null;
  covariance?: Record<string, number | null>;
  gps_health?: {
    healthy?: boolean;
    reason?: string;
    fix_type?: number | null;
    satellites_visible?: number | null;
    eph_m?: number | null;
    h_acc_m?: number | null;
    confidence?: number | null;
  };
  vision_health?: {
    available?: boolean;
    status?: string;
    confidence?: number | null;
    tile_id?: string | null;
    inliers?: number | null;
    reprojection_error_px?: number | null;
  };
}

export interface DownloadFileResult {
  remote_path: string;
  local_path: string;
  bytes_received: number;
}

export interface FieldCollectionPlanCondition {
  condition?: string;
  label?: string;
  expected?: "good_map" | "degraded" | "wrong_map" | string;
  status?: "registered" | "registered_missing_log" | "placeholder" | "missing" | string;
  notes?: string;
  case_name?: string;
  manifest_log_path?: string;
  manifest_log_exists?: boolean;
  source_log?: string;
  legacy_source_log?: string;
  capture_output_dir?: string;
  runtime_status_path?: string;
  field_log_capture_report?: string;
  has_capture_command?: boolean;
  has_preflight_command?: boolean;
  has_preflight_capture_command?: boolean;
  has_metadata_update_command?: boolean;
  has_register_command?: boolean;
  preflight_command?: string;
  preflight_capture_command?: string;
  capture_command?: string;
  metadata_update_command?: string;
  bundle?: string;
  capture_metadata?: Record<string, unknown>;
  register_command?: string;
}

export interface PiDiscoveryCandidate {
  host: string;
  port: number;
  source: "saved" | "mdns" | "arp" | string;
  ssh_open: boolean;
  resolved_ip?: string;
  ssh_banner?: string;
  message: string;
  last_seen_unix_ms: number;
}

export interface LocalNetworkHint {
  interface_name: string;
  ipv4: string;
  network_hint: string;
  source: string;
  likely_active: boolean;
}

export interface SupportBundleFile {
  name: string;
  path: string;
  size_bytes: number;
  modified_unix_ms?: number;
  summary?: {
    bundle_id?: string;
    bundle_health_status?: "passed" | "degraded" | "failed" | string;
    checksum_status?: "missing" | "passed" | "failed" | string;
    covered_file_count?: number;
    elevation_status?: "not_provided" | "passed" | "degraded" | "failed" | string;
    elevation_asset_count?: number;
    vertical_sanity_ready?: boolean;
    map_source?: string;
    source_name?: string;
    georef_source?: string;
    georef_crs?: string;
    georef_confidence?: number;
    replay_gate_status?: "passed" | "failed" | "degraded" | string;
    replay_case_count?: number;
    gnss_denied_plan_status?: "passed" | "failed" | "degraded" | string;
    gnss_denied_plan_check_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    gnss_denied_plan_check_report_count?: number;
    gnss_denied_plan_check_missing_count?: number;
    px4_sitl_evidence_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    px4_sitl_sample_count?: number;
    px4_sitl_prereq_status?: "passed" | "failed" | "degraded" | "not_checked" | "not_provided" | string;
    px4_params_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    px4_ev_ctrl?: number;
    ardupilot_params_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    ardupilot_source_set?: number;
    ardupilot_posxy_source?: number;
    feature_method_benchmark_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    feature_method_benchmark_recommended?: string;
    feature_method_benchmark_report_count?: number;
    field_evidence_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    field_evidence_field_case_count?: number;
    field_evidence_capture_metadata_issue_count?: number;
    field_evidence_report_count?: number;
    field_collection_plan_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    field_collection_plan_registered_count?: number;
    field_collection_plan_required_count?: number;
    field_collection_plan_report_count?: number;
    field_collection_plan_pending_capture_command_count?: number;
    field_collection_plan_pending_metadata_update_command_count?: number;
    field_collection_plan_pending_registration_command_count?: number;
    field_collection_plan_capture_output_dir_count?: number;
    field_collection_plan_runtime_status_path_count?: number;
    field_collection_plan_condition_source_log_count?: number;
    field_capture_preflight_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    field_capture_preflight_report_count?: number;
    field_capture_preflight_ready_for_capture_count?: number;
    field_capture_preflight_ready_for_registration_count?: number;
    field_capture_preflight_failed_check_count?: number;
    field_capture_preflight_degraded_check_count?: number;
    field_capture_preflight_next_action_count?: number;
    field_capture_preflight_blocked_action_count?: number;
    threshold_tuning_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    threshold_tuning_field_case_count?: number;
    threshold_tuning_capture_metadata_issue_count?: number;
    threshold_tuning_report_count?: number;
    rosbag_export_validation_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    rosbag_export_validation_report_count?: number;
    rosbag_export_validation_message_count?: number;
    rosbag_export_validation_topic_count?: number;
    rosbag2_cli_review_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    rosbag2_cli_review_report_count?: number;
    evidence_workflow_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    evidence_workflow_validation_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    evidence_workflow_runtime_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    evidence_workflow_provenance_status?: "passed" | "failed" | "degraded" | "not_provided" | string;
    evidence_workflow_step_count?: number;

// File truncated for Stitch paste handoff. Full file is in the unzipped export folder.
```

## src/index.css

```tsx
@import "leaflet/dist/leaflet.css";
@import "leaflet-draw/dist/leaflet.draw.css";

@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  font-family: Inter, system-ui, -apple-system, sans-serif;
  --ops-bg-base: #0F172A;
  --ops-bg-surface: #1E293B;
  --ops-bg-overlay: #334155;
  --ops-border-subtle: #334155;
  --ops-border-strong: #475569;
  --ops-active: #00E5FF;
  --ops-ready: #22C55E;
  --ops-warning: #F59E0B;
  --ops-critical: #EF4444;
  --ops-offline: #64748B;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  background: var(--ops-bg-base);
  color: #F1F5F9;
  overflow: hidden;
  user-select: none;
}

/* Custom scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--ops-border-subtle); border-radius: 0; }
::-webkit-scrollbar-thumb:hover { background: var(--ops-border-strong); }

/* Allow text selection in inputs */
input, textarea, [contenteditable] { user-select: text; }

/* Leaflet overrides */
.leaflet-container { background: var(--ops-bg-base); font-family: inherit; }
.leaflet-control-attribution { font-size: 10px; opacity: 0.5; }
.leaflet-draw-toolbar a { background-color: #121E35 !important; color: #94A3B8 !important; }
.leaflet-draw-toolbar a:hover { background-color: #172440 !important; color: #22D3EE !important; }

/* Title bar drag region */
[data-tauri-drag-region] { -webkit-app-region: drag; }
[data-tauri-drag-region] button,
[data-tauri-drag-region] a,
[data-tauri-drag-region] input,
[data-tauri-drag-region] select,
[data-tauri-drag-region] textarea { -webkit-app-region: no-drag; }

/* Range input */
input[type="range"] {
  -webkit-appearance: none;
  height: 4px;
  border-radius: 2px;
  background: #1E2E4A;
  outline: none;
}
input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #06B6D4;
  cursor: pointer;
}

@layer components {
  .btn-primary {
    @apply bg-cyan-500 hover:bg-cyan-600 text-slate-950 font-semibold px-4 py-2 rounded-none transition-colors duration-150 flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed;
  }
  .btn-secondary {
    @apply bg-bg-elevated hover:bg-border text-slate-200 font-medium px-4 py-2 rounded-none border border-border-strong transition-colors duration-150 flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed;
  }
  .btn-ghost {
    @apply hover:bg-bg-elevated text-slate-400 hover:text-slate-200 font-medium px-3 py-2 rounded-none transition-colors duration-150 flex items-center gap-2;
  }
  .card {
    @apply bg-bg-card border border-border rounded-none p-5;
  }
  .input-field {
    @apply w-full bg-bg-surface border border-border rounded-none px-3 py-2 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500/30 transition-colors;
  }
  .label {
    @apply block font-mono text-[10px] font-bold text-slate-400 mb-1.5 uppercase tracking-[0.1em];
  }
  .section-title {
    @apply font-mono text-lg font-semibold text-slate-100;
  }
  .badge-cyan {
    @apply inline-flex items-center gap-1 bg-cyan-500/10 text-cyan-400 text-xs font-medium px-2 py-0.5 rounded-full border border-cyan-500/20;
  }
  .badge-green {
    @apply inline-flex items-center gap-1 bg-emerald-500/10 text-emerald-400 text-xs font-medium px-2 py-0.5 rounded-full border border-emerald-500/20;
  }
  .badge-red {
    @apply inline-flex items-center gap-1 bg-red-500/10 text-red-400 text-xs font-medium px-2 py-0.5 rounded-full border border-red-500/20;
  }
  .badge-yellow {
    @apply inline-flex items-center gap-1 bg-yellow-500/10 text-yellow-400 text-xs font-medium px-2 py-0.5 rounded-full border border-yellow-500/20;
  }
  .ops-panel {
    @apply border border-border bg-bg-surface rounded-none;
  }
  .ops-tile {
    @apply border border-border bg-bg-surface rounded-none px-3 py-2;
  }
  .ops-label {
    @apply font-mono text-[10px] font-bold uppercase tracking-[0.1em] text-slate-400;
  }
  .ops-value {
    @apply font-mono text-sm font-semibold text-slate-100 tabular-nums;
  }
  .ops-led {
    @apply inline-block h-2 w-2 shrink-0 bg-slate-500;
  }
  .ops-led-ready {
    background: var(--ops-ready);
  }
  .ops-led-active {
    background: var(--ops-active);
  }
  .ops-led-warning {
    background: var(--ops-warning);
  }
  .ops-led-critical {
    background: var(--ops-critical);
  }
  .ops-led-offline {
    background: var(--ops-offline);
  }
  .ops-map-overlay {
    @apply border border-border-strong bg-bg-overlay/90 rounded-none shadow-none;
  }
  .ops-console {
    @apply bg-bg-base border border-border rounded-none px-3 py-2.5 text-[11px] font-mono text-slate-300 whitespace-pre-wrap leading-relaxed;
  }
}
```


---

# Import Notes

If Stitch supports folder upload, use the unzipped folder instead of this paste handoff:

`desktop-app/stitch-export/drone-vision-desktop-stitch-import/`

If Stitch supports only individual files, upload or paste in this order:

1. `README.md` / `STITCH_IMPORT_README.md`
2. `src/App.tsx`
3. `src/components/Layout.tsx`
4. `src/index.css`
5. `src/pages/Dashboard.tsx`
6. `src/pages/MissionPlanner.tsx`
7. `src/pages/Maps.tsx`
8. `src/pages/Devices.tsx`
9. `src/pages/VisionPipeline.tsx`
10. `src/pages/SystemStatus.tsx`
11. `src/pages/FlightReview.tsx`
12. `src/lib/types.ts`
13. `src/lib/tauri.ts`

For visual reference, the static build is in:

`desktop-app/stitch-export/drone-vision-desktop-stitch-import/desktop-app-static-build/`
