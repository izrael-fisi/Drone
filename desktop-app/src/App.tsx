import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { cmd } from "./lib/tauri";
import { useAppStore } from "./lib/store";
import { useShellStore, type BottomDockTabId, type RightDockRoute } from "./lib/shellStore";
import { Dashboard } from "./pages/Dashboard";
import { Onboarding } from "./pages/Onboarding";
import { Settings } from "./pages/Settings";

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
          <span className="text-slate-400 text-sm">Loading...</span>
        </div>
      </div>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/home" replace />} />
        <Route path="/onboarding" element={<Onboarding />} />
        <Route element={<Layout />}>
          <Route path="/home" element={<Dashboard />} />
          <Route path="/dashboard" element={<OpenHomeSurface />} />
          <Route path="/navigation-panel" element={<OpenHomeSurface />} />
          <Route path="/flights" element={<OpenHomeSurface rightDock="flights" />} />
          <Route path="/flight-review" element={<OpenHomeSurface rightDock="flights" />} />
          <Route path="/history" element={<OpenHomeSurface rightDock="flights" />} />
          <Route path="/maps" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/maps/planner" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/maps/bundles" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/maps/terrain" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/mission-planner" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/mission-bundle-builder" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/bundle-builder" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/bundle" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/mission-bundle" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/terrain" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/terrain-planning" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/upload" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/mavlink" element={<OpenHomeSurface bottomDock="diagnostics" />} />
          <Route path="/system" element={<OpenHomeSurface rightDock="vehicle" />} />
          <Route path="/system/module-setup" element={<OpenSettingsGroup group="device" />} />
          <Route path="/system/vision" element={<OpenHomeSurface rightDock="camera" />} />
          <Route path="/system/status" element={<OpenHomeSurface bottomDock="system-status" />} />
          <Route path="/devices" element={<OpenHomeSurface rightDock="vehicle" />} />
          <Route path="/vehicle-manager" element={<OpenHomeSurface rightDock="vehicle" />} />
          <Route path="/pi-setup" element={<OpenSettingsGroup group="device" />} />
          <Route path="/module-setup" element={<OpenSettingsGroup group="device" />} />
          <Route path="/vision-pipeline" element={<OpenHomeSurface rightDock="camera" />} />
          <Route path="/camera-vision" element={<OpenHomeSurface rightDock="camera" />} />
          <Route path="/models" element={<OpenHomeSurface rightDock="camera" />} />
          <Route path="/system-status" element={<OpenHomeSurface bottomDock="system-status" />} />
          <Route path="/diagnostics" element={<OpenHomeSurface bottomDock="diagnostics" />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/home" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

function OpenHomeSurface({
  rightDock,
  bottomDock,
}: {
  rightDock?: RightDockRoute;
  bottomDock?: BottomDockTabId;
}) {
  const { resetRightDock, pushRightDock, setBottomDockTab } = useShellStore();

  useEffect(() => {
    if (rightDock) {
      resetRightDock();
      if (rightDock !== "root") pushRightDock(rightDock);
    } else {
      resetRightDock();
    }
    if (bottomDock) setBottomDockTab(bottomDock);
  }, [bottomDock, pushRightDock, resetRightDock, rightDock, setBottomDockTab]);

  return <Navigate to="/home" replace />;
}

function OpenSettingsGroup({ group }: { group: string }) {
  return <Navigate to={`/settings?group=${encodeURIComponent(group)}`} replace />;
}

export function DroneLogo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      <circle cx="16" cy="16" r="15" stroke="#FF6600" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="6" fill="#FF6600" fillOpacity="0.15" stroke="#FF6600" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="2.5" fill="#FF6600" />
      <line x1="16" y1="1" x2="16" y2="8" stroke="#FF6600" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="16" y1="24" x2="16" y2="31" stroke="#FF6600" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="1" y1="16" x2="8" y2="16" stroke="#FF6600" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="24" y1="16" x2="31" y2="16" stroke="#FF6600" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
