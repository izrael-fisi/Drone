import { useEffect, useState } from "react";
import { useAppStore } from "../../lib/store";
import { cmd } from "../../lib/tauri";
import type { Profile } from "../../lib/types";
import { useEntitledProducts } from "../hooks";
import { profileWithDefaults } from "../profile";
import { CompactField, PanelAction, PanelStack, SectionLabel, SettingLine, StateCard, StatusPill } from "../ui";

export function GeneralSettingsPanel() {
  const { profile, setProfile } = useAppStore();
  const entitledProducts = useEntitledProducts();
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
        <StateCard label="Organization" value={resolvedProfile.org || "None"} detail="operator profile" tone={resolvedProfile.org ? "green" : "muted"} />
        <StateCard label="Products" value={entitledProducts.length} detail="licensed in this app" tone={entitledProducts.length ? "orange" : "muted"} />
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
