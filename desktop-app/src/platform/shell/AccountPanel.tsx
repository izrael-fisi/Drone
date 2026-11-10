import { CircleNotchIcon as Loader2, HardDrivesIcon as Server, UserCircleIcon as UserRound } from "@phosphor-icons/react";
import { useBackendStore } from "../api/backend";
import { useEffect, useState } from "react";
import { useAppStore } from "../../lib/store";
import { cmd } from "../../lib/tauri";
import type { Profile } from "../../lib/types";
import { cn } from "../../lib/utils";
import { proxigo } from "../../lib/proxigo";
import { profileWithDefaults } from "../profile";
import { CompactField, PanelAction, PanelStack, SectionLabel } from "../ui";

export function AccountPanel() {
  const { profile, setProfile, proxigoSession, setProxigoSession, cloudAccount, setCloudAccount, lastUsageEvent } = useAppStore();
  const resolvedProfile = profileWithDefaults(profile);
  const backendConnection = useBackendStore((state) => state.connection);
  const backendDiscovery = useBackendStore((state) => state.discovery);
  const backendOffline = useBackendStore((state) => state.offline);

  // Local profile fields
  const [name, setName] = useState(resolvedProfile.name);
  const [email, setEmail] = useState(resolvedProfile.email);
  const [org, setOrg] = useState(resolvedProfile.org);
  const [saving, setSaving] = useState(false);
  const [profileMessage, setProfileMessage] = useState<string | null>(null);

  // Proxigo cloud auth fields
  const [cloudEmail, setCloudEmail] = useState("");
  const [cloudPassword, setCloudPassword] = useState("");
  const [moduleSerial, setModuleSerial] = useState(resolvedProfile.proxigo_module_serial ?? "");
  const [cloudLoading, setCloudLoading] = useState(false);
  const [cloudError, setCloudError] = useState<string | null>(null);

  useEffect(() => {
    setName(resolvedProfile.name);
    setEmail(resolvedProfile.email);
    setOrg(resolvedProfile.org);
    setModuleSerial(resolvedProfile.proxigo_module_serial ?? "");
  }, [resolvedProfile.email, resolvedProfile.name, resolvedProfile.org, resolvedProfile.proxigo_module_serial]);

  // Auto-fill org name from Proxigo cloud when logged in to an org
  useEffect(() => {
    const orgName = cloudAccount?.org?.org_name;
    if (!orgName) return;
    setOrg(orgName);
    cmd.saveProfile({ ...resolvedProfile, org: orgName })
      .then(() => setProfile({ ...resolvedProfile, org: orgName }))
      .catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cloudAccount?.org?.org_name]);

  // Auto-select first active module serial when account loads and none is saved yet
  useEffect(() => {
    if (!cloudAccount) return;
    const activeModules = cloudAccount.modules.filter((m) => m.status === "active");
    if (!activeModules.length) return;
    const firstSerial = activeModules[0].serial;
    // Auto-select if nothing is set, or if the saved serial isn't in the account anymore
    const savedSerial = resolvedProfile.proxigo_module_serial;
    const savedValid = savedSerial && cloudAccount.modules.some((m) => m.serial === savedSerial);
    if (savedValid) {
      setModuleSerial(savedSerial!);
      return;
    }
    setModuleSerial(firstSerial);
    const updated: Profile = { ...resolvedProfile, proxigo_module_serial: firstSerial };
    cmd.saveProfile(updated).then(() => setProfile(updated)).catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cloudAccount?.modules]);

  const saveLocalProfile = async () => {
    setSaving(true);
    setProfileMessage(null);
    const updated: Profile = {
      ...resolvedProfile,
      email: email.trim(),
      name: name.trim() || "Operator",
      org: org.trim() || "Drone Vision Nav",
    };
    try {
      await cmd.saveProfile(updated);
      setProfile(updated);
      setProfileMessage("saved");
    } catch (error) {
      setProfileMessage(String(error));
    } finally {
      setSaving(false);
    }
  };

  const handleLogin = async () => {
    setCloudLoading(true);
    setCloudError(null);
    try {
      const session = await proxigo.login(cloudEmail.trim(), cloudPassword);
      setProxigoSession(session);
      const account = await proxigo.getAccount(session);
      setCloudAccount(account);

      const updated: Profile = {
        ...resolvedProfile,
        proxigo_access_token: session.access_token,
        proxigo_refresh_token: session.refresh_token,
        proxigo_token_expires_at: session.expires_at,
        proxigo_user_id: session.user_id,
        proxigo_email: session.email,
        proxigo_module_serial: moduleSerial.trim() || resolvedProfile.proxigo_module_serial,
      };
      await cmd.saveProfile(updated);
      setProfile(updated);
      setCloudPassword("");
    } catch (err) {
      setCloudError(err instanceof Error ? err.message : String(err));
    } finally {
      setCloudLoading(false);
    }
  };

  const handleLogout = async () => {
    setProxigoSession(null);
    setCloudAccount(null);
    const updated: Profile = {
      ...resolvedProfile,
      proxigo_access_token: undefined,
      proxigo_refresh_token: undefined,
      proxigo_token_expires_at: undefined,
      proxigo_user_id: undefined,
      proxigo_email: undefined,
    };
    await cmd.saveProfile(updated);
    setProfile(updated);
  };


  const [lastRefreshResult, setLastRefreshResult] = useState<string | null>(null);
  const [apiTestResult, setApiTestResult] = useState<string | null>(null);
  const [apiTestLoading, setApiTestLoading] = useState(false);

  const refreshAccount = async () => {
    if (!proxigoSession) return;
    setCloudLoading(true);
    setLastRefreshResult(null);
    try {
      const account = await proxigo.withSession(
        proxigoSession,
        async (fresh) => {
          setProxigoSession(fresh);
          if (profile) {
            const updated = { ...profile, proxigo_access_token: fresh.access_token, proxigo_refresh_token: fresh.refresh_token, proxigo_token_expires_at: fresh.expires_at };
            await cmd.saveProfile(updated);
            setProfile(updated);
          }
        },
        (s) => proxigo.getAccount(s)
      );
      setCloudAccount(account);
      const orgCtxLocal = account.org;
      const used = orgCtxLocal ? orgCtxLocal.org_km2_used : account.km2_used;
      const limit = orgCtxLocal ? orgCtxLocal.org_km2_limit : account.km2_limit;
      setLastRefreshResult(`✓ ${used.toFixed(2)} / ${limit} km²  ·  ${account.modules.length} module(s)`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setCloudError(msg);
      setLastRefreshResult(`✗ ${msg}`);
    } finally {
      setCloudLoading(false);
    }
  };

  const testCloudApi = async () => {
    if (!proxigoSession || !profile?.proxigo_module_serial) return;
    setApiTestLoading(true);
    setApiTestResult(null);
    try {
      let activeSession = proxigoSession;
      const result = await proxigo.withSession(
        proxigoSession,
        async (fresh) => {
          activeSession = fresh;
          setProxigoSession(fresh);
          if (profile) {
            const updated = { ...profile, proxigo_access_token: fresh.access_token, proxigo_refresh_token: fresh.refresh_token, proxigo_token_expires_at: fresh.expires_at };
            setProfile(updated);
            cmd.saveProfile(updated).catch(() => {});
          }
        },
        (s) => proxigo.reportMapDownload(s, 0.01, profile!.proxigo_module_serial!)
      );
      setApiTestResult(`✓ API OK — total this month: ${result.total_km2_this_month.toFixed(3)} km²`);
      proxigo.getAccount(activeSession).then(setCloudAccount).catch(() => {});
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setApiTestResult(`✗ API FAILED: ${msg}`);
    } finally {
      setApiTestLoading(false);
    }
  };

  const planLabel: Record<string, string> = { starter: "Starter", pro: "Pro", enterprise: "Enterprise" };
  const orgCtx = cloudAccount?.org ?? null;
  // Effective quota: org pool if in org, personal otherwise
  const displayKm2Used      = orgCtx ? orgCtx.org_km2_used      : (cloudAccount?.km2_used      ?? 0);
  const displayKm2Limit     = orgCtx ? orgCtx.org_km2_limit     : (cloudAccount?.km2_limit     ?? 0);
  const displayKm2Remaining = orgCtx ? orgCtx.org_km2_remaining : (cloudAccount?.km2_remaining ?? 0);

  return (
    <PanelStack title="Account" subtitle={resolvedProfile.org || "Organization not set"}>
      {/* Local profile */}
      <div className="flex items-center gap-3 rounded-lg border border-white/10 bg-bg-card p-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-orange-500/35 bg-orange-500/10 text-orange-200">
          <UserRound size={18} />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold text-slate-100">{resolvedProfile.name}</span>
          <span className="block truncate text-xs text-slate-500">{resolvedProfile.email || "No email set"}</span>
        </span>
      </div>
      <CompactField label="Name" value={name} onChange={setName} placeholder="Operator name" />
      <CompactField label="Email" value={email} onChange={setEmail} placeholder="operator@example.com" />
      <CompactField
        label={orgCtx?.org_name ? "Organization (synced)" : "Organization"}
        value={org}
        onChange={orgCtx?.org_name ? undefined : setOrg}
        placeholder="Organization name"
      />
      <PanelAction
        label={saving ? "Saving…" : "Save Local Profile"}
        detail={profileMessage ?? "Persist operator name and organization"}
        onClick={saveLocalProfile}
        disabled={saving}
      />

      {/* Proxigo cloud account */}
      <SectionLabel>{backendDiscovery?.name ?? "Proxigo Cloud"}</SectionLabel>
      <div className="flex items-center gap-2 rounded-md border border-border bg-bg-card px-3 py-2 text-[11px]">
        <Server size={12} className="shrink-0 text-slate-500" />
        <span className="min-w-0 flex-1 truncate text-slate-400">
          {backendConnection?.kind === "self_hosted" ? backendConnection.base_url : "Proxigo Cloud"}
          {backendOffline && <span className="text-yellow-400"> · offline</span>}
        </span>
        <span className="shrink-0 text-slate-600">Sign out to change server</span>
      </div>
      {proxigoSession && cloudAccount ? (
        <>
          {/* Account identity + quota */}
          <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/8 p-3 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <span className="block truncate text-xs font-semibold text-emerald-300">{cloudAccount.email}</span>
                {orgCtx && (
                  <span className="block text-[11px] text-slate-400">{orgCtx.org_name} · {orgCtx.role}</span>
                )}
              </div>
              {(orgCtx || cloudAccount.plan) && (
                <span className="shrink-0 rounded border border-orange-500/30 bg-orange-500/10 px-1.5 py-0.5 text-[11px] font-medium text-orange-300">
                  {orgCtx
                    ? (planLabel[orgCtx.org_plan] ?? orgCtx.org_plan)
                    : (planLabel[cloudAccount.plan!] ?? cloudAccount.plan)}
                </span>
              )}
            </div>

            {/* Quota numbers — always show if there's usage or a limit */}
            {(displayKm2Limit > 0 || displayKm2Used > 0) && (
              <div className="space-y-0.5">
                <div className="flex justify-between text-[11px]">
                  <span className="text-slate-400">{orgCtx ? "Org pool this month" : "Quota this month"}</span>
                </div>
                <div className="flex gap-3 text-xs tabular-nums">
                  <span><span className="text-slate-100">{displayKm2Used.toFixed(2)}</span> <span className="text-slate-500">km² used</span></span>
                  {displayKm2Limit > 0 && (<>
                    <span className="text-slate-600">/</span>
                    <span><span className="text-slate-100">{displayKm2Limit}</span> <span className="text-slate-500">km² limit</span></span>
                    <span className="ml-auto text-emerald-400">{displayKm2Remaining.toFixed(1)} <span className="text-slate-500">left</span></span>
                  </>)}
                </div>
                {orgCtx?.my_km2_allowance != null && (
                  <div className="text-[11px] text-slate-500">
                    Your allowance: {orgCtx.my_km2_used.toFixed(2)} / {orgCtx.my_km2_allowance} km²
                  </div>
                )}
              </div>
            )}

            {cloudAccount.modules.length > 0 && (
              <div className="text-[11px] text-slate-400">
                {cloudAccount.modules.length} module{cloudAccount.modules.length !== 1 ? "s" : ""}
                {" · "}{cloudAccount.modules.filter((m) => m.status === "active").length} active
              </div>
            )}
          </div>

          {/* Org member usage — admins only */}
          {orgCtx?.role === "admin" && orgCtx.members && orgCtx.members.length > 0 && (
            <>
              <SectionLabel>Team Usage</SectionLabel>
              <div className="rounded-lg bg-white/[0.035] overflow-hidden">
                {orgCtx.members.map((member, i) => (
                  <div
                    key={member.user_id}
                    className={cn(
                      "px-3 py-2 text-[11px]",
                      i > 0 && "border-t border-border"
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-slate-300">
                        {member.user_id === cloudAccount.user_id ? "You" : member.user_id.slice(0, 8) + "…"}
                      </span>
                      <span className="shrink-0 text-slate-400">
                        {member.km2_used.toFixed(1)} km²
                        {member.km2_allowance != null && (
                          <span className="text-slate-600"> / {member.km2_allowance}</span>
                        )}
                      </span>
                    </div>
                    <div className="text-slate-600 mt-0.5">{member.role}</div>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Active module selector */}
          <div>
            <label className="block rounded-md border border-border bg-bg-card px-3 py-2">
              <span className="block text-[11px] font-semibold text-slate-500">
                Active Module
              </span>
              {cloudAccount.modules.length > 0 ? (
                <select
                  value={moduleSerial}
                  onChange={(e) => {
                    const serial = e.target.value;
                    setModuleSerial(serial);
                    const updated: Profile = { ...resolvedProfile, proxigo_module_serial: serial || undefined };
                    cmd.saveProfile(updated).then(() => setProfile(updated)).catch(() => {});
                  }}
                  className="mt-1 h-7 w-full bg-transparent text-sm text-slate-100 outline-none"
                >
                  {cloudAccount.modules.map((m) => (
                    <option key={m.serial} value={m.serial} className="bg-zinc-900">
                      {m.serial}{m.nickname ? ` · ${m.nickname}` : ""}{m.status !== "active" ? ` (${m.status})` : ""}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="mt-1 block text-sm text-slate-500">No modules registered</span>
              )}
            </label>
          </div>
          <PanelAction
            label={cloudLoading ? "Refreshing…" : "Refresh Account"}
            detail="Reload modules and quota"
            onClick={refreshAccount}
            disabled={cloudLoading}
          />
          {lastRefreshResult && (
            <div className={`rounded px-2.5 py-2 text-[11px] break-all ${lastRefreshResult.startsWith("✓") ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>
              {lastRefreshResult}
            </div>
          )}
          <PanelAction
            label={apiTestLoading ? "Testing…" : "Test Cloud Connection"}
            detail="Posts 0.01 km² to verify the usage API works end-to-end"
            onClick={testCloudApi}
            disabled={apiTestLoading || !profile?.proxigo_module_serial}
          />
          {apiTestResult && (
            <div className={`rounded px-2.5 py-2 text-[11px] break-all ${apiTestResult.startsWith("✓") ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>
              {apiTestResult}
            </div>
          )}

          {/* Last usage event from actual map downloads — persists across tab switches */}
          {lastUsageEvent && (
            <div className={`rounded px-2.5 py-2 text-[11px] break-all space-y-0.5 ${lastUsageEvent.status === "ok" ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>
              <div className="font-semibold">{lastUsageEvent.status === "ok" ? "Last download recorded" : "Last download failed"}</div>
              <div className="opacity-80">{lastUsageEvent.message}</div>
              <div className="text-[11px] opacity-50">{new Date(lastUsageEvent.timestamp).toLocaleTimeString()}</div>
            </div>
          )}

          <PanelAction label="Sign Out of Proxigo" detail={`Logged in as ${cloudAccount.email}`} onClick={handleLogout} />
        </>
      ) : (
        <>
          <div className="rounded-lg border border-white/10 bg-bg-card p-3 space-y-2">
            <p className="text-[11px] text-slate-400">
              Sign in to your proxigo.us account to track map download usage and manage your subscription.
            </p>
            <CompactField
              label="Email"
              value={cloudEmail}
              onChange={setCloudEmail}
              placeholder="you@example.com"
            />
            <CompactField
              label="Password"
              value={cloudPassword}
              onChange={setCloudPassword}
              placeholder="••••••••"
              secret
            />
            {cloudError && (
              <div className="rounded border border-red-500/20 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-400">
                {cloudError}
              </div>
            )}
            <button
              type="button"
              onClick={handleLogin}
              disabled={cloudLoading || !cloudEmail || !cloudPassword}
              className={cn(
                "flex w-full items-center justify-center gap-2 rounded-md border px-3 py-2 text-xs font-medium transition-colors",
                cloudLoading || !cloudEmail || !cloudPassword
                  ? "cursor-not-allowed border-border bg-bg-card text-slate-500 opacity-55"
                  : "border-orange-500/40 bg-orange-500/10 text-orange-200 hover:bg-orange-500/20"
              )}
            >
              {cloudLoading ? <><Loader2 size={13} className="animate-spin" /> Signing in…</> : "Sign in to Proxigo"}
            </button>
          </div>
        </>
      )}
    </PanelStack>
  );
}
