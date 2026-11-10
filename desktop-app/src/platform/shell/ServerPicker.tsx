import { open } from "@tauri-apps/plugin-dialog";
import { CloudIcon as Cloud, CircleNotchIcon as Loader2, HardDrivesIcon as Server, ShieldCheckIcon as ShieldCheck, WifiSlashIcon as WifiOff } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { cn } from "../../lib/utils";
import { useBackendStore, type BackendKind } from "../api/backend";

const inputClass =
  "w-full rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-orange-500/50 transition-colors";

/**
 * Choose the Proxigo server before signing in: Proxigo Cloud or the
 * organization's own (on-prem / private cloud) server.
 */
export function ServerPicker({ onPendingChange }: { onPendingChange?: (pending: boolean) => void }) {
  const { connection, discovery, status, error, offline, connect } = useBackendStore();
  const [kind, setKind] = useState<BackendKind>(connection?.kind ?? "proxigo_cloud");
  const [url, setUrl] = useState(connection?.base_url ?? "");
  const [caPath, setCaPath] = useState(connection?.ca_cert_path ?? "");

  useEffect(() => {
    if (!connection) return;
    setKind(connection.kind);
    setUrl(connection.base_url ?? "");
    setCaPath(connection.ca_cert_path ?? "");
  }, [connection]);

  const busy = status === "loading";
  const selfHostedDirty =
    kind === "self_hosted" &&
    (connection?.kind !== "self_hosted" || url.trim().replace(/\/+$/, "") !== (connection.base_url ?? "") || caPath.trim() !== (connection.ca_cert_path ?? ""));

  // The picker shows a server other than the connected one: sign-in must wait for Connect.
  const pending = kind !== connection?.kind || selfHostedDirty;
  useEffect(() => onPendingChange?.(pending), [onPendingChange, pending]);

  const chooseKind = (next: BackendKind) => {
    setKind(next);
    if (next === "proxigo_cloud" && connection?.kind !== "proxigo_cloud") {
      void connect({ kind: "proxigo_cloud", label: "Proxigo Cloud" });
    }
  };

  const browseCa = async () => {
    try {
      const picked = await open({ multiple: false, filters: [{ name: "CA certificate (PEM)", extensions: ["pem", "crt", "cer"] }] });
      if (typeof picked === "string") setCaPath(picked);
    } catch (pickError) {
      console.warn("Certificate picker failed", pickError);
    }
  };

  return (
    <div className="space-y-2">
      <label className="block text-xs font-semibold text-slate-500">Server</label>
      <div className="grid grid-cols-2 gap-1 rounded-md border border-border bg-bg-elevated p-1">
        {([
          ["proxigo_cloud", "Proxigo Cloud", Cloud],
          ["self_hosted", "Organization server", Server],
        ] as const).map(([value, label, Icon]) => (
          <button
            key={value}
            type="button"
            onClick={() => chooseKind(value)}
            className={cn(
              "flex items-center justify-center gap-1.5 rounded px-2 py-1.5 text-xs font-medium transition-colors",
              kind === value ? "bg-white/10 text-slate-100" : "text-slate-500 hover:text-slate-300",
            )}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      {kind === "self_hosted" && (
        <div className="space-y-2">
          <input
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://proxigo.your-company.com"
            className={inputClass}
          />
          <div className="flex gap-2">
            <input
              value={caPath}
              onChange={(event) => setCaPath(event.target.value)}
              placeholder="Private CA certificate (optional)"
              className={cn(inputClass, "text-xs")}
            />
            <button type="button" onClick={browseCa} className="btn-secondary shrink-0 px-3 text-xs">
              Browse
            </button>
          </div>
          <button
            type="button"
            disabled={busy || !url.trim() || !selfHostedDirty}
            onClick={() => void connect({ kind: "self_hosted", label: "", base_url: url.trim(), ca_cert_path: caPath.trim() || null })}
            className="btn-secondary w-full justify-center text-xs"
          >
            Connect
          </button>
        </div>
      )}

      <div className="flex items-start gap-2 text-[11px]">
        {busy ? (
          <><Loader2 size={12} className="mt-0.5 animate-spin text-slate-400" /><span className="text-slate-400">Connecting…</span></>
        ) : status === "error" ? (
          <span className="text-red-400">{error}</span>
        ) : discovery && !selfHostedDirty ? (
          <>
            {offline ? <WifiOff size={12} className="mt-0.5 text-yellow-400" /> : <ShieldCheck size={12} className="mt-0.5 text-status-ready" />}
            <span className="text-slate-400">
              {offline ? "Offline - using saved settings for " : "Connected to "}
              <span className="text-slate-200">{discovery.name}</span>
              {discovery.auth.kind === "oidc" && " (single sign-on is not supported in this version yet)"}
            </span>
          </>
        ) : null}
      </div>
    </div>
  );
}
