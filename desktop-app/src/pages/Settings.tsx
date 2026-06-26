import { useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { ChevronDown, ChevronRight, FolderOpen, Key, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { DefenseHeader, DefenseListItem, DefenseMetric, DefensePane, DefenseRightPanel, DefenseSection } from "../components/DefensePane";
import { useAppStore } from "../lib/store";
import { cmd } from "../lib/tauri";
import { cn } from "../lib/utils";

interface YamlObject {
  [key: string]: YamlValue;
}

type YamlValue = string | number | boolean | null | YamlValue[] | YamlObject;

const SECTION_LABELS: Record<string, string> = {
  camera: "Camera",
  imu: "IMU",
  tracker: "Tracker",
  matcher: "Matcher",
  fix_quality: "Fix Quality",
  region_map: "Region Map",
  eskf: "ESKF",
  mavlink: "MAVLink",
  logging: "Logging",
};

export function Settings() {
  const { profile, setProfile } = useAppStore();
  const [mapboxKey, setMapboxKey] = useState(profile?.mapbox_key ?? "");
  const [bingKey, setBingKey] = useState(profile?.bing_key ?? "");
  const [savingKeys, setSavingKeys] = useState(false);
  const [keysSaved, setKeysSaved] = useState(false);
  const [configPath, setConfigPath] = useState("");
  const [config, setConfig] = useState<Record<string, YamlValue> | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const saveKeys = async () => {
    if (!profile) return;
    setSavingKeys(true);
    setKeysSaved(false);
    const updated = {
      ...profile,
      mapbox_key: mapboxKey.trim() || undefined,
      bing_key: bingKey.trim() || undefined,
    };
    try {
      await cmd.saveProfile(updated);
      setProfile(updated);
      setKeysSaved(true);
      setTimeout(() => setKeysSaved(false), 2500);
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingKeys(false);
    }
  };

  const loadConfig = async (path: string) => {
    try {
      const data = await cmd.readYamlConfig(path);
      setConfig(data as Record<string, YamlValue>);
      const init: Record<string, boolean> = {};
      Object.keys(data).forEach((key) => { init[key] = key === "camera" || key === "matcher"; });
      setExpanded(init);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  const pickConfig = async () => {
    const file = await open({
      filters: [{ name: "YAML config", extensions: ["yaml", "yml"] }],
      title: "Open params.yaml",
    });
    if (file) {
      setConfigPath(file as string);
      loadConfig(file as string);
    }
  };

  const saveConfig = async () => {
    if (!config || !configPath) return;
    setSaving(true);
    setSaved(false);
    try {
      await cmd.writeYamlConfig(configPath, config as Record<string, unknown>);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const updateValue = (section: string, key: string, raw: string) => {
    if (!config) return;
    const sectionData = config[section] as Record<string, YamlValue>;
    const existing = sectionData[key];
    let parsed: YamlValue = raw;
    if (typeof existing === "number") parsed = Number(raw);
    else if (typeof existing === "boolean") parsed = raw === "true";
    else if (Array.isArray(existing)) {
      try { parsed = JSON.parse(raw); } catch { parsed = raw; }
    }
    setConfig({ ...config, [section]: { ...sectionData, [key]: parsed } });
    setSaved(false);
  };

  const toggle = (key: string) => setExpanded((current) => ({ ...current, [key]: !current[key] }));
  const sectionCount = config
    ? Object.keys(config).filter((key) => typeof config[key] === "object" && !Array.isArray(config[key]) && config[key] !== null).length
    : 0;

  return (
    <DefensePane
      right={
        <DefenseRightPanel title="CONFIG STATUS">
          <div className="flex justify-center border-b border-white/10 pb-4">
            <div className="holo-core" />
          </div>
          <DefenseListItem label="Operator profile" detail={profile?.name ?? "not loaded"} tone={profile ? "ready" : "warning"} />
          <DefenseListItem label="Mapbox key" detail={mapboxKey ? "configured" : "not configured"} tone={mapboxKey ? "ready" : "warning"} />
          <DefenseListItem label="Bing key" detail={bingKey ? "configured" : "not configured"} tone={bingKey ? "ready" : "warning"} />
          <DefenseListItem label="YAML config" detail={configPath || "no file loaded"} tone={config ? "ready" : "warning"} />
          <DefenseListItem label="Storage policy" detail="manual cleanup controls pending pane" tone="warning" />
        </DefenseRightPanel>
      }
    >
      <DefenseHeader
        eyebrow="SETTINGS"
        title="CONFIGURATION AND DATA CONTROL"
        subtitle="imagery keys // repo paths // params yaml // retention"
        action={config && (
          <button onClick={saveConfig} disabled={saving} className="border border-status-active bg-status-active/10 px-4 py-2 font-label-caps text-label-caps text-status-active transition-colors hover:bg-status-active hover:text-[#05070A]">
            {saving ? <RefreshCw size={14} className="inline mr-2 animate-spin" /> : <Save size={14} className="inline mr-2" />}
            {saved ? "SAVED" : "SAVE YAML"}
          </button>
        )}
      />

      <div className="grid grid-cols-4 gap-3">
        <DefenseMetric label="PROFILE" value={profile?.name ?? "NONE"} detail={profile?.org ?? "operator"} tone={profile ? "ready" : "warning"} />
        <DefenseMetric label="MAPBOX" value={mapboxKey ? "SET" : "MISSING"} detail="imagery provider" tone={mapboxKey ? "ready" : "warning"} />
        <DefenseMetric label="BING" value={bingKey ? "SET" : "MISSING"} detail="imagery provider" tone={bingKey ? "ready" : "warning"} />
        <DefenseMetric label="PARAM SECTIONS" value={sectionCount} detail={configPath || "no yaml loaded"} tone={config ? "active" : "warning"} />
      </div>

      <DefenseSection title="IMAGERY API KEYS" icon={<Key size={14} />}>
        <div className="grid grid-cols-2 gap-4">
          <SecretField label="Mapbox API Key" value={mapboxKey} onChange={setMapboxKey} placeholder="pk.eyJ1..." />
          <SecretField label="Bing Maps API Key" value={bingKey} onChange={setBingKey} placeholder="Bing key..." />
        </div>
        <div className="mt-4 flex items-center justify-between border-t border-white/10 pt-3">
          <p className="font-data-mono text-[10px] text-white/40">Used for premium satellite layers in Map Library.</p>
          <button onClick={saveKeys} disabled={savingKeys || !profile} className="btn-secondary">
            {savingKeys ? <RefreshCw size={13} className="animate-spin" /> : <Save size={13} />}
            {keysSaved ? "Saved" : "Save Keys"}
          </button>
        </div>
      </DefenseSection>

      <DefenseSection title="PARAMETER FILE" icon={<FolderOpen size={14} />}>
        <div className="flex gap-2">
          <input
            className="input-field flex-1 text-xs font-mono"
            value={configPath}
            onChange={(event) => setConfigPath(event.target.value)}
            placeholder="Path to params.yaml..."
            onKeyDown={(event) => event.key === "Enter" && configPath && loadConfig(configPath)}
          />
          <button onClick={pickConfig} className="btn-secondary px-3"><FolderOpen size={15} /></button>
          {configPath && <button onClick={() => loadConfig(configPath)} className="btn-secondary px-3"><RefreshCw size={15} /></button>}
        </div>
        {error && <div className="mt-3 rounded border border-status-critical/30 bg-red-500/10 px-3 py-2 font-data-mono text-xs text-status-critical">{error}</div>}
      </DefenseSection>

      {!config ? (
        <DefenseSection title="WAITING FOR YAML" icon={<ShieldCheck size={14} />}>
          <div className="py-10 text-center">
            <FolderOpen size={32} className="mx-auto mb-3 text-white/25" />
            <p className="font-data-mono text-sm text-white">Open params.yaml or params_rpi5.yaml to edit runtime parameters.</p>
            <button onClick={pickConfig} className="mt-4 border border-status-active bg-status-active/10 px-4 py-2 font-label-caps text-label-caps text-status-active">
              BROWSE
            </button>
          </div>
        </DefenseSection>
      ) : (
        <div className="space-y-2">
          {Object.entries(config)
            .filter(([key]) => typeof config[key] === "object" && !Array.isArray(config[key]) && config[key] !== null)
            .map(([section, sectionVal]) => {
              const label = SECTION_LABELS[section] ?? section;
              const isOpen = expanded[section];
              const fields = Object.entries(sectionVal as Record<string, YamlValue>);
              return (
                <div key={section} className="glass-panel overflow-hidden">
                  <button onClick={() => toggle(section)} className="w-full flex items-center gap-3 px-5 py-3.5 hover:bg-status-active/5 transition-colors text-left">
                    <span className="font-label-caps text-label-caps text-status-active">{label}</span>
                    <span className="font-data-mono text-[10px] text-white/40 ml-auto">{fields.length} params</span>
                    {isOpen ? <ChevronDown size={14} className="text-white/40" /> : <ChevronRight size={14} className="text-white/40" />}
                  </button>
                  {isOpen && (
                    <div className="border-t border-white/10 px-5 py-4 grid grid-cols-2 gap-x-6 gap-y-3">
                      {fields.map(([key, val]) => (
                        <YamlField key={key} section={section} fieldKey={key} value={val} onChange={updateValue} />
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
        </div>
      )}
    </DefensePane>
  );
}

function SecretField({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder: string }) {
  return (
    <div>
      <label className="label">{label}</label>
      <input className="input-field text-xs font-mono" type="password" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </div>
  );
}

function YamlField({
  section,
  fieldKey,
  value,
  onChange,
}: {
  section: string;
  fieldKey: string;
  value: YamlValue;
  onChange: (section: string, key: string, raw: string) => void;
}) {
  return (
    <div>
      <label className="label">{fieldKey}</label>
      {typeof value === "boolean" ? (
        <div className="flex gap-2">
          {[true, false].map((candidate) => (
            <button
              key={String(candidate)}
              onClick={() => onChange(section, fieldKey, String(candidate))}
              className={cn(
                "flex-1 py-1.5 rounded border font-data-mono text-xs transition-colors",
                value === candidate ? "bg-status-active/10 border-status-active/40 text-status-active" : "border-white/10 text-white/45",
              )}
            >
              {String(candidate)}
            </button>
          ))}
        </div>
      ) : Array.isArray(value) ? (
        <input className="input-field text-xs font-mono" value={JSON.stringify(value)} onChange={(event) => onChange(section, fieldKey, event.target.value)} />
      ) : (
        <input
          className="input-field text-sm"
          type={typeof value === "number" ? "number" : "text"}
          value={String(value ?? "")}
          onChange={(event) => onChange(section, fieldKey, event.target.value)}
          step={typeof value === "number" && String(value).includes(".") ? "any" : undefined}
        />
      )}
    </div>
  );
}
