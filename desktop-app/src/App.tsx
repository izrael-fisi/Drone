import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { cmd } from "./lib/tauri";
import { useAppStore } from "./lib/store";
import { Dashboard } from "./pages/Dashboard";
import { Devices } from "./pages/Devices";
import { FlightReview } from "./pages/FlightReview";
import { Maps } from "./pages/Maps";
import { MissionBundleBuilder } from "./pages/MissionBundleBuilder";
import { MissionPlanner } from "./pages/MissionPlanner";
import { Onboarding } from "./pages/Onboarding";
import { ModuleSetup } from "./pages/PiSetup";
import { Settings } from "./pages/Settings";
import { SystemStatus } from "./pages/SystemStatus";
import { TerrainPlanning } from "./pages/TerrainPlanning";
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
          <Route path="/mission-bundle-builder" element={<MissionBundleBuilder />} />
          <Route path="/bundle-builder" element={<MissionBundleBuilder />} />
          <Route path="/bundle" element={<MissionBundleBuilder />} />
          <Route path="/mission-bundle" element={<MissionBundleBuilder />} />
          <Route path="/terrain" element={<TerrainPlanning />} />
          <Route path="/terrain-planning" element={<TerrainPlanning />} />
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
