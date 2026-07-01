import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { cmd } from "./lib/tauri";
import { useAppStore } from "./lib/store";
import { useShellStore, type BottomDockTabId, type RightDockRoute } from "./lib/shellStore";
import { Dashboard } from "./pages/Dashboard";
import { Onboarding } from "./pages/Onboarding";
import { Settings } from "./pages/Settings";
import { proxigo, type ProxigoSession } from "./lib/proxigo";
import type { Profile } from "./lib/types";

export default function App() {
  const { profile, setProfile, setDevices, setRegions, setProxigoSession, setCloudAccount, proxigoSession } = useAppStore();
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([cmd.loadProfile(), cmd.loadDevices(), cmd.loadRegions()])
      .then(async ([p, d, r]) => {
        setProfile(p);
        setDevices(d);
        setRegions(r);

        // Restore Proxigo session from persisted tokens
        if (p.proxigo_access_token && p.proxigo_refresh_token && p.proxigo_user_id && p.proxigo_email) {
          let session: ProxigoSession = {
            access_token: p.proxigo_access_token,
            refresh_token: p.proxigo_refresh_token,
            expires_at: p.proxigo_token_expires_at ?? 0,
            user_id: p.proxigo_user_id,
            email: p.proxigo_email,
          };
          try {
            if (proxigo.isExpired(session)) {
              session = await proxigo.refreshSession(session);
              const updated = {
                ...p,
                proxigo_access_token: session.access_token,
                proxigo_refresh_token: session.refresh_token,
                proxigo_token_expires_at: session.expires_at,
              };
              await cmd.saveProfile(updated);
              setProfile(updated);
            }
            setProxigoSession(session);
            const account = await proxigo.getAccount(session);
            setCloudAccount(account);
          } catch {
            // Token invalid — user will need to log in again
          }
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [setProfile, setDevices, setRegions, setProxigoSession, setCloudAccount]);

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

  // Always require Proxigo login before showing any part of the app
  if (!proxigoSession) {
    return (
      <ProxigoLoginScreen
        profile={profile ?? { name: "", email: "", org: "", accent_color: "#FF6600", onboarding_complete: false }}
        onLogin={(session) => {
          setProxigoSession(session);
          proxigo.getAccount(session).then(setCloudAccount).catch(() => {});
        }}
        setProfile={setProfile}
      />
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
          <Route path="/missions" element={<OpenHomeSurface rightDock="missions" />} />
          <Route path="/mission" element={<OpenHomeSurface rightDock="missions" />} />
          <Route path="/mission-planner" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/mission-bundle-builder" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/bundle-builder" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/bundle" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/mission-bundle" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/terrain" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/terrain-planning" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/upload" element={<OpenHomeSurface rightDock="maps" />} />
          <Route path="/mavlink" element={<OpenHomeSurface bottomDock="diagnostics" />} />
          <Route path="/ground-control" element={<OpenHomeSurface rightDock="ground-control" />} />
          <Route path="/gcs" element={<OpenHomeSurface rightDock="ground-control" />} />
          <Route path="/qgroundcontrol" element={<OpenHomeSurface rightDock="ground-control" />} />
          <Route path="/mission-planner-gcs" element={<OpenHomeSurface rightDock="ground-control" />} />
          <Route path="/ardupilot" element={<OpenHomeSurface rightDock="ground-control" />} />
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

function ProxigoLoginScreen({
  profile,
  onLogin,
  setProfile,
}: {
  profile: Profile;
  onLogin: (session: ProxigoSession) => void;
  setProfile: (p: Profile) => void;
}) {
  const [email, setEmail] = useState(profile.proxigo_email ?? "");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLogin = async () => {
    if (!email.trim() || !password) return;
    setLoading(true);
    setError(null);
    try {
      const session = await proxigo.login(email.trim(), password);
      const updated = {
        ...profile,
        proxigo_access_token: session.access_token,
        proxigo_refresh_token: session.refresh_token,
        proxigo_token_expires_at: session.expires_at,
        proxigo_user_id: session.user_id,
        proxigo_email: session.email,
      };
      await cmd.saveProfile(updated);
      setProfile(updated);
      onLogin(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: { key: string }) => {
    if (e.key === "Enter") handleLogin();
  };

  return (
    <div className="flex h-screen bg-bg-base items-center justify-center">
      {/* Background grid */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage:
            "linear-gradient(#FF6600 1px, transparent 1px), linear-gradient(90deg, #FF6600 1px, transparent 1px)",
          backgroundSize: "40px 40px",
        }}
      />

      <div className="relative z-10 w-full max-w-sm px-6 animate-fade-in">
        <div className="flex flex-col items-center mb-8">
          <DroneLogo size={48} />
          <h1 className="mt-4 text-2xl font-bold text-slate-100">Sign in to Proxigo</h1>
          <p className="text-slate-400 text-sm mt-1 text-center">
            Your account tracks map download quota and subscription status.
          </p>
        </div>

        <div className="card space-y-4">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1.5">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="you@example.com"
              autoFocus
              className="w-full rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-orange-500/50 transition-colors"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1.5">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="••••••••"
              className="w-full rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-orange-500/50 transition-colors"
            />
          </div>

          {error && (
            <div className="rounded border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              {error}
            </div>
          )}

          <button
            onClick={handleLogin}
            disabled={loading || !email.trim() || !password}
            className="btn-primary w-full justify-center"
          >
            {loading ? (
              <>
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                Signing in…
              </>
            ) : (
              "Sign in"
            )}
          </button>
        </div>

        <p className="mt-4 text-center text-[11px] text-slate-600">
          Need an account?{" "}
          <a
            href="https://proxigo.us"
            target="_blank"
            rel="noreferrer"
            className="text-orange-500 hover:text-orange-400 transition-colors"
          >
            Sign up at proxigo.us
          </a>
        </p>
      </div>
    </div>
  );
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
