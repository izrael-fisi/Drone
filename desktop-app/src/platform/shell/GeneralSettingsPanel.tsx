import { useEffect, useState } from "react";
import { useAppStore } from "../../lib/store";
import { cmd } from "../../lib/tauri";
import type { Profile } from "../../lib/types";
import { profileWithDefaults } from "../profile";
import { CompactField, PanelAction, PanelStack, SectionLabel } from "../ui";

function parseLimit(value: string): number | undefined {
  const parsed = Number(value.trim());
  return value.trim() && Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

/** App-wide settings: imagery keys and map download limits, stored in the local profile. */
export function GeneralSettingsPanel() {
  const { profile, setProfile } = useAppStore();
  const resolvedProfile = profileWithDefaults(profile);
  const [mapboxKey, setMapboxKey] = useState(resolvedProfile.mapbox_key ?? "");
  const [bingKey, setBingKey] = useState(resolvedProfile.bing_key ?? "");
  const [maxAreaKm2, setMaxAreaKm2] = useState(resolvedProfile.max_map_area_km2?.toString() ?? "");
  const [maxSizeGb, setMaxSizeGb] = useState((resolvedProfile.max_map_download_size_gb ?? 20).toString());
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    setMapboxKey(resolvedProfile.mapbox_key ?? "");
    setBingKey(resolvedProfile.bing_key ?? "");
    setMaxAreaKm2(resolvedProfile.max_map_area_km2?.toString() ?? "");
    setMaxSizeGb((resolvedProfile.max_map_download_size_gb ?? 20).toString());
  }, [resolvedProfile.bing_key, resolvedProfile.mapbox_key, resolvedProfile.max_map_area_km2, resolvedProfile.max_map_download_size_gb]);

  const save = async () => {
    setSaving(true);
    setMessage(null);
    const updated: Profile = {
      ...resolvedProfile,
      mapbox_key: mapboxKey.trim() || undefined,
      bing_key: bingKey.trim() || undefined,
      max_map_area_km2: parseLimit(maxAreaKm2),
      max_map_download_size_gb: parseLimit(maxSizeGb) ?? 20,
    };
    try {
      await cmd.saveProfile(updated);
      setProfile(updated);
      setMessage("Saved");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <PanelStack title="Settings" subtitle="Imagery keys and map download limits for this computer.">
      <SectionLabel>Imagery keys</SectionLabel>
      <CompactField label="Mapbox API key" value={mapboxKey} onChange={setMapboxKey} placeholder="pk.eyJ1…" secret />
      <CompactField label="Bing Maps API key" value={bingKey} onChange={setBingKey} placeholder="Bing key" secret />
      <SectionLabel>Map download limits</SectionLabel>
      <CompactField label="Max map area (km²)" value={maxAreaKm2} onChange={setMaxAreaKm2} placeholder="No limit" />
      <CompactField label="Max download size (GB)" value={maxSizeGb} onChange={setMaxSizeGb} placeholder="20" />
      <p className="px-1 text-xs text-slate-500">Map cuts above these limits warn before installing; you can override per cut.</p>
      <PanelAction label={saving ? "Saving…" : "Save settings"} detail={message ?? "Stored in the local operator profile"} onClick={save} disabled={saving} />
    </PanelStack>
  );
}
