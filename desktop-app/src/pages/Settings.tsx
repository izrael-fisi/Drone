import { open } from "@tauri-apps/plugin-dialog";
import {
  Activity,
  ArrowLeft,
  Building2,
  FolderOpen,
  Lock,
  Radio,
  RefreshCw,
  Save,
  Search,
  Server,
  Settings as SettingsIcon,
  Shield,
  Trash2,
  WifiOff,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import { cn } from "../lib/utils";

type SettingsGroupId = "general" | "device" | "mav" | "organizations" | "diagnostics";

const SETTINGS_GROUPS: Array<{ id: SettingsGroupId; label: string; Icon: LucideIcon }> = [
  { id: "general", label: "General", Icon: SettingsIcon },
  { id: "device", label: "Device", Icon: Server },
  { id: "mav", label: "MAV", Icon: Radio },
  { id: "organizations", label: "Organizations", Icon: Building2 },
  { id: "diagnostics", label: "Diagnostics", Icon: Activity },
];

const SEARCH_ROWS: Array<{ label: string; detail: string; group: SettingsGroupId; keywords?: string }> = [
  { label: "Light Mode", detail: "Theme", group: "general" },
  { label: "Imagery API Keys", detail: "Mapbox and Bing keys", group: "general", keywords: "maps satellite" },
  { label: "Parameter File", detail: "Local params YAML", group: "general", keywords: "yaml config" },
  { label: "Device URL", detail: "Edge API address", group: "device", keywords: "host connection" },
  { label: "Device Mode", detail: "Single camera or VIO", group: "device", keywords: "cyclops micro vps" },
  { label: "Recording on Boot", detail: "Runtime recording service", group: "device" },
  { label: "SSH Credentials", detail: "Username and password", group: "device" },
  { label: "MAVProxy Configuration", detail: "Device-side MAVLink routing", group: "mav" },
  { label: "VPS MAVLink Parameters", detail: "MAVLink parameter bridge", group: "mav" },
  { label: "Organization", detail: "Shared maps and recordings", group: "organizations" },
  { label: "Remote Support", detail: "Support session access", group: "diagnostics" },
  { label: "Service Diagnostics", detail: "Runtime logs", group: "diagnostics" },
  { label: "Lockdown Diagnostics", detail: "Security scan and cleanup", group: "diagnostics" },
];

function isSettingsGroupId(value: string | null): value is SettingsGroupId {
  return SETTINGS_GROUPS.some((group) => group.id === value);
}

export function Settings() {
  const navigate = useNavigate();
  const location = useLocation();
  const { profile, setProfile, devices, activeDeviceId } = useAppStore();
  const activeDevice = devices.find((device) => device.id === activeDeviceId);
  const [activeGroup, setActiveGroup] = useState<SettingsGroupId>("general");
  const [searchQuery, setSearchQuery] = useState("");
  const [mapboxKey, setMapboxKey] = useState(profile?.mapbox_key ?? "");
  const [bingKey, setBingKey] = useState(profile?.bing_key ?? "");
  const [savingKeys, setSavingKeys] = useState(false);
  const [keyMessage, setKeyMessage] = useState<string | null>(null);
  const [configPath, setConfigPath] = useState("");
  const [configSectionCount, setConfigSectionCount] = useState<number | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);

  useEffect(() => {
    const group = new URLSearchParams(location.search).get("group");
    if (isSettingsGroupId(group)) setActiveGroup(group);
  }, [location.search]);

  useEffect(() => {
    setMapboxKey(profile?.mapbox_key ?? "");
    setBingKey(profile?.bing_key ?? "");
  }, [profile?.bing_key, profile?.mapbox_key]);

  const selectGroup = (group: SettingsGroupId) => {
    setActiveGroup(group);
    navigate(`/settings?group=${group}`, { replace: true });
  };

  const searchResults = useMemo(() => {
    const normalized = searchQuery.trim().toLowerCase();
    if (!normalized) return [];
    return SEARCH_ROWS.filter((row) =>
      `${row.label} ${row.detail} ${row.group} ${row.keywords ?? ""}`.toLowerCase().includes(normalized),
    );
  }, [searchQuery]);

  const saveKeys = async () => {
    if (!profile) return;
    setSavingKeys(true);
    setKeyMessage(null);
    const updated = {
      ...profile,
      mapbox_key: mapboxKey.trim() || undefined,
      bing_key: bingKey.trim() || undefined,
    };
    try {
      await cmd.saveProfile(updated);
      setProfile(updated);
      setKeyMessage("Saved");
      window.setTimeout(() => setKeyMessage(null), 2200);
    } catch (error) {
      setKeyMessage(String(error));
    } finally {
      setSavingKeys(false);
    }
  };

  const loadConfig = async (path: string) => {
    try {
      const data = await cmd.readYamlConfig(path);
      const count = Object.keys(data).filter((key) => {
        const value = (data as Record<string, unknown>)[key];
        return value && typeof value === "object" && !Array.isArray(value);
      }).length;
      setConfigSectionCount(count);
      setConfigError(null);
    } catch (error) {
      setConfigSectionCount(null);
      setConfigError(String(error));
    }
  };

  const pickConfig = async () => {
    const file = await open({
      filters: [{ name: "YAML config", extensions: ["yaml", "yml"] }],
      title: "Open params.yaml",
    });
    if (file) {
      const path = file as string;
      setConfigPath(path);
      await loadConfig(path);
    }
  };

  return (
    <div className="flex h-full flex-col bg-bg-base text-slate-200">
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <button
          type="button"
          onClick={() => navigate("/home")}
          className="operator-shell-button h-9 w-9 rounded-md"
          title="Back"
        >
          <ArrowLeft size={18} />
        </button>
        <SettingsIcon size={19} className="text-cyan-500" />
        <h1 className="text-base font-semibold text-slate-100">Settings</h1>
      </div>

      <div className="flex min-h-0 flex-1 justify-center">
        <div className="flex w-full max-w-5xl">
          <aside className="w-72 shrink-0 border-r border-border bg-bg-base/40 p-4">
            <div className="relative">
              <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-600" />
              <input
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                className="input-field h-9 pl-9 text-sm"
                placeholder="Search settings..."
              />
            </div>

            <div className="mt-6 flex items-center gap-3 border-b border-border pb-4">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-xs font-semibold text-slate-300">
                {(profile?.name ?? "DV").slice(0, 2).toUpperCase()}
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-slate-100">{profile?.name ?? "Operator"}</div>
                <div className="truncate text-xs text-slate-500">{profile?.org ?? "Drone Vision Nav"}</div>
              </div>
            </div>

            <nav className="mt-4 grid gap-1">
              {SETTINGS_GROUPS.map(({ id, label, Icon }) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => selectGroup(id)}
                  className={cn(
                    "flex h-10 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                    activeGroup === id
                      ? "bg-cyan-500/12 text-cyan-500"
                      : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-100",
                  )}
                >
                  <Icon size={16} />
                  <span>{label}</span>
                </button>
              ))}
            </nav>
          </aside>

          <main className="min-w-0 flex-1 overflow-y-auto p-6">
            {searchResults.length > 0 && (
              <section className="mb-6 rounded-md border border-border bg-bg-card p-2">
                {searchResults.map((row) => (
                  <button
                    key={`${row.group}-${row.label}`}
                    type="button"
                    onClick={() => selectGroup(row.group)}
                    className="flex w-full items-center justify-between gap-4 rounded-md px-3 py-2 text-left hover:bg-cyan-500/10"
                  >
                    <span>
                      <span className="block text-sm font-medium text-slate-200">{row.label}</span>
                      <span className="block text-xs text-slate-500">{row.detail}</span>
                    </span>
                    <span className="text-[10px] uppercase tracking-[0.08em] text-slate-600">{row.group}</span>
                  </button>
                ))}
              </section>
            )}

            {activeGroup === "general" && (
              <GeneralSettings
                profileName={profile?.name ?? "Operator"}
                mapboxKey={mapboxKey}
                bingKey={bingKey}
                savingKeys={savingKeys}
                keyMessage={keyMessage}
                configPath={configPath}
                configSectionCount={configSectionCount}
                configError={configError}
                setMapboxKey={setMapboxKey}
                setBingKey={setBingKey}
                setConfigPath={setConfigPath}
                saveKeys={saveKeys}
                pickConfig={pickConfig}
                loadConfig={loadConfig}
              />
            )}
            {activeGroup === "device" && <DeviceSettings activeDevice={activeDevice} />}
            {activeGroup === "mav" && <MavSettings activeDevice={activeDevice} configSectionCount={configSectionCount} />}
            {activeGroup === "organizations" && <OrganizationSettings />}
            {activeGroup === "diagnostics" && <DiagnosticsSettings activeDevice={activeDevice} />}
          </main>
        </div>
      </div>
    </div>
  );
}

function GeneralSettings({
  profileName,
  mapboxKey,
  bingKey,
  savingKeys,
  keyMessage,
  configPath,
  configSectionCount,
  configError,
  setMapboxKey,
  setBingKey,
  setConfigPath,
  saveKeys,
  pickConfig,
  loadConfig,
}: {
  profileName: string;
  mapboxKey: string;
  bingKey: string;
  savingKeys: boolean;
  keyMessage: string | null;
  configPath: string;
  configSectionCount: number | null;
  configError: string | null;
  setMapboxKey: (value: string) => void;
  setBingKey: (value: string) => void;
  setConfigPath: (value: string) => void;
  saveKeys: () => void;
  pickConfig: () => void;
  loadConfig: (path: string) => Promise<void>;
}) {
  return (
    <SettingsSection title="General" subtitle="Theme, operator profile, and local app inputs">
      <SettingCard title="Light Mode" detail="Dark operator mode is active">
        <ToggleControl checked={false} disabled label="Light Mode" />
      </SettingCard>
      <SettingCard title="Operator" detail={profileName}>
        <StatusPill tone="ready">Loaded</StatusPill>
      </SettingCard>
      <SettingCard title="Imagery API Keys" detail="Used by map preparation when premium imagery is selected">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Mapbox API Key" value={mapboxKey} onChange={setMapboxKey} placeholder="pk.eyJ1..." secret />
          <Field label="Bing Maps API Key" value={bingKey} onChange={setBingKey} placeholder="Bing key..." secret />
        </div>
        <div className="mt-3 flex items-center justify-end gap-3">
          {keyMessage && <span className="text-xs text-slate-500">{keyMessage}</span>}
          <button type="button" onClick={saveKeys} disabled={savingKeys} className="btn-secondary">
            {savingKeys ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
            Save Keys
          </button>
        </div>
      </SettingCard>
      <SettingCard title="Parameter File" detail={configSectionCount === null ? "No YAML loaded" : `${configSectionCount} sections loaded`}>
        <div className="flex gap-2">
          <input
            className="input-field flex-1 text-xs font-mono"
            value={configPath}
            onChange={(event) => setConfigPath(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && configPath && loadConfig(configPath)}
            placeholder="Path to params.yaml..."
          />
          <button type="button" onClick={pickConfig} className="btn-secondary px-3" title="Browse">
            <FolderOpen size={15} />
          </button>
          <button type="button" onClick={() => configPath && loadConfig(configPath)} disabled={!configPath} className="btn-secondary px-3" title="Refresh">
            <RefreshCw size={15} />
          </button>
        </div>
        {configError && <div className="mt-3 rounded-md border border-status-warning/30 bg-status-warning/10 px-3 py-2 text-xs text-status-warning">{configError}</div>}
      </SettingCard>
    </SettingsSection>
  );
}

function DeviceSettings({ activeDevice }: { activeDevice?: { name: string; host?: string; username?: string; remote_project_path?: string; mavlink_endpoint?: string } }) {
  const connected = Boolean(activeDevice?.remote_project_path);
  return (
    <SettingsSection title="Device Settings" subtitle="Edge device connection and runtime state">
      <div className="grid gap-3 md:grid-cols-2">
        <MetricCard Icon={connected ? Activity : WifiOff} label="Connection" value={connected ? "Connected" : "Disconnected"} tone={connected ? "ready" : "muted"} />
        <MetricCard Icon={Lock} label="Lockdown" value={connected ? "Available" : "Unavailable"} tone={connected ? "ready" : "muted"} />
      </div>
      <SettingCard title="Recording on Boot" detail="Runtime recording service">
        <StatusPill tone={connected ? "warning" : "muted"}>{connected ? "Not set" : "Offline"}</StatusPill>
      </SettingCard>
      <SettingCard title="Device Mode" detail="Select a device mode to preview the app">
        <div className="grid gap-2 md:grid-cols-2">
          <ChoiceButton label="Cyclops" detail="Single-camera EKF" active={false} disabled={!connected} />
          <ChoiceButton label="Micro VPS" detail="Multi-camera VIO" active={false} disabled={!connected} />
        </div>
      </SettingCard>
      <SettingCard title="Device URL" detail="Network address for the edge API">
        <ReadOnlyInput value={activeDevice?.host ? `http://${activeDevice.host}:5000` : "not configured"} />
      </SettingCard>
      <SettingCard title="SSH Username" detail="Used by local diagnostics and setup commands">
        <ReadOnlyInput value={activeDevice?.username ?? "pi"} />
      </SettingCard>
      <SettingCard title="Storage" detail="Detected devices require a live edge API">
        <StatusPill tone={connected ? "warning" : "muted"}>{connected ? "Not configured" : "Device offline"}</StatusPill>
      </SettingCard>
    </SettingsSection>
  );
}

function MavSettings({ activeDevice, configSectionCount }: { activeDevice?: { mavlink_endpoint?: string }; configSectionCount: number | null }) {
  const connected = Boolean(activeDevice?.mavlink_endpoint);
  return (
    <SettingsSection title="MAV Settings" subtitle="MAVLink routing and VPS parameters">
      <SettingCard title="MAVProxy Configuration" detail="Device-side MAVLink routing settings">
        <StatusRow label="Endpoint" value={activeDevice?.mavlink_endpoint ?? "not configured"} healthy={connected} />
        <StatusRow label="Primary route" value={connected ? "available" : "connect device to configure"} healthy={connected} />
      </SettingCard>
      <SettingCard title="VPS MAVLink Parameters" detail={connected ? "Connected to MAVLink" : "MAVLink not connected"}>
        <div className="flex min-h-[140px] items-center justify-center rounded-md border border-border bg-bg-base text-center">
          <div>
            <Radio size={28} className="mx-auto mb-2 text-slate-600" />
            <div className="text-sm font-medium text-slate-300">{connected ? "Ready to request parameters" : "MAVProxy not connected"}</div>
            <div className="mt-1 text-xs text-slate-500">
              {configSectionCount === null ? "No local parameter file loaded" : `${configSectionCount} local parameter sections loaded`}
            </div>
          </div>
        </div>
      </SettingCard>
    </SettingsSection>
  );
}

function OrganizationSettings() {
  const [orgName, setOrgName] = useState("");
  return (
    <SettingsSection title="Organization" subtitle="Shared maps and recordings">
      <SettingCard title="Create an Organization" detail="Cloud sharing is not configured in this build">
        <div className="flex gap-2">
          <input className="input-field flex-1" value={orgName} onChange={(event) => setOrgName(event.target.value)} placeholder="Organization name" />
          <button type="button" disabled className="btn-secondary">Create</button>
        </div>
      </SettingCard>
    </SettingsSection>
  );
}

function DiagnosticsSettings({ activeDevice }: { activeDevice?: { host?: string; remote_project_path?: string } }) {
  const connected = Boolean(activeDevice?.remote_project_path);
  return (
    <SettingsSection title="Diagnostics" subtitle="Remote support access and service diagnostics">
      <SettingCard title="Remote Support" detail={activeDevice?.host ? `Device IP: ${activeDevice.host}` : "Device unavailable"}>
        <StatusPill tone={connected ? "warning" : "muted"}>{connected ? "Not running" : "Offline"}</StatusPill>
        <button type="button" disabled className="btn-secondary mt-3 w-full justify-center">Enable Remote Support</button>
      </SettingCard>
      <SettingCard title="Service Diagnostics" detail={connected ? "Ready" : "Device offline"}>
        <div className="rounded-md border border-border bg-bg-base px-3 py-6 text-center font-mono text-xs text-slate-600">
          No recent service logs
        </div>
      </SettingCard>
      <SettingCard title="Lockdown Diagnostics" detail="Security scan and cleanup">
        <div className="rounded-md border border-status-warning/40 bg-status-warning/10 p-4">
          <div className="flex items-start gap-3">
            <Shield size={18} className="mt-0.5 text-status-warning" />
            <div>
              <div className="text-sm font-semibold text-slate-100">Device unavailable</div>
              <div className="mt-1 text-xs text-slate-400">Reconnect the device to scan for sensitive log artifacts.</div>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-3 gap-2">
            <Stage label="Scan" active />
            <Stage label="Review" />
            <Stage label="Fix" />
          </div>
        </div>
        <button type="button" disabled className="btn-secondary mt-3 w-full justify-center text-status-warning">
          <Trash2 size={14} />
          Delete all logs
        </button>
      </SettingCard>
    </SettingsSection>
  );
}

function SettingsSection({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <section className="animate-fade-in">
      <div className="mb-6">
        <h2 className="text-xl font-semibold text-slate-100">{title}</h2>
        <p className="mt-1 text-sm text-slate-400">{subtitle}</p>
      </div>
      <div className="grid gap-4">{children}</div>
    </section>
  );
}

function SettingCard({ title, detail, children }: { title: string; detail: string; children: ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-bg-card p-4">
      <div className="mb-3">
        <div className="text-sm font-semibold text-slate-100">{title}</div>
        <div className="mt-0.5 text-xs text-slate-500">{detail}</div>
      </div>
      {children}
    </div>
  );
}

function MetricCard({ Icon, label, value, tone }: { Icon: LucideIcon; label: string; value: string; tone: "ready" | "muted" | "warning" }) {
  return (
    <div className="rounded-md border border-border bg-bg-card p-4">
      <div className="mb-6 flex items-center justify-between">
        <Icon size={18} className={tone === "ready" ? "text-status-ready" : tone === "warning" ? "text-status-warning" : "text-slate-600"} />
        <StatusPill tone={tone}>{tone === "ready" ? "Online" : "Offline"}</StatusPill>
      </div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-sm font-semibold text-slate-100">{value}</div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  secret = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  secret?: boolean;
}) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <input
        type={secret ? "password" : "text"}
        className="input-field"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}

function ReadOnlyInput({ value }: { value: string }) {
  return <input readOnly className="input-field font-mono text-xs text-slate-400" value={value} />;
}

function ChoiceButton({ label, detail, active, disabled }: { label: string; detail: string; active: boolean; disabled: boolean }) {
  return (
    <button
      type="button"
      disabled={disabled}
      className={cn(
        "rounded-md border px-4 py-3 text-left transition-colors",
        active ? "border-cyan-500 bg-cyan-500/10" : "border-border bg-bg-base",
        disabled && "cursor-not-allowed opacity-55",
      )}
    >
      <span className="block text-sm font-semibold text-slate-100">{label}</span>
      <span className="mt-1 block text-xs text-slate-500">{detail}</span>
    </button>
  );
}

function ToggleControl({ checked, disabled, label }: { checked: boolean; disabled?: boolean; label: string }) {
  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={checked}
      className={cn("flex items-center gap-3 text-sm text-slate-300", disabled && "cursor-not-allowed opacity-55")}
    >
      <span className={cn("flex h-6 w-11 items-center rounded-full border px-0.5 transition-colors", checked ? "border-cyan-500 bg-cyan-500/30" : "border-border bg-bg-base")}>
        <span className={cn("h-4 w-4 rounded-full bg-slate-500 transition-transform", checked && "translate-x-5 bg-cyan-500")} />
      </span>
      {label}
    </button>
  );
}

function StatusRow({ label, value, healthy }: { label: string; value: string; healthy: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 border border-border bg-bg-base px-3 py-2">
      <span className="text-xs text-slate-500">{label}</span>
      <span className={cn("truncate text-right font-mono text-xs", healthy ? "text-status-ready" : "text-status-warning")}>{value}</span>
    </div>
  );
}

function StatusPill({ tone, children }: { tone: "ready" | "warning" | "muted"; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]",
        tone === "ready" && "border-status-ready/30 bg-status-ready/10 text-status-ready",
        tone === "warning" && "border-status-warning/30 bg-status-warning/10 text-status-warning",
        tone === "muted" && "border-border bg-bg-base text-slate-500",
      )}
    >
      {children}
    </span>
  );
}

function Stage({ label, active = false }: { label: string; active?: boolean }) {
  return (
    <div className={cn("rounded-md border p-3 text-xs", active ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-500" : "border-border bg-bg-base text-slate-500")}>
      {label}
    </div>
  );
}
