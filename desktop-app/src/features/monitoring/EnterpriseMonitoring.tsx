import { useEffect, useState } from "react";
import { GlobalView } from "./GlobalView";
import { isSiteOperator, type SiteSession } from "./SiteOperator";
import { EnterpriseAuthenticator } from "./EnterpriseAuthenticator";

const API = "/api/enterprise";
const SITE_ID = /^[a-z0-9][a-z0-9-]{0,79}$/;
type SignInMethod = { id: string; label: string; passwordForm?: boolean };

function sameSites(left: SiteSession[], right: SiteSession[]) {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function EnterpriseMonitoring() {
  if (new URLSearchParams(location.search).get("sign-in") === "mfa") return <EnterpriseAuthenticator />;
  return <EnterpriseSession />;
}

function EnterpriseSession() {
  const [sites, setSites] = useState<SiteSession[]>();
  const [active, setActive] = useState<{ base: string; incidentId?: string; incidentReplay?: boolean }>();
  const [signedIn, setSignedIn] = useState(false);
  const [organization, setOrganization] = useState("");
  const [passwordForm, setPasswordForm] = useState(false);
  const [loginOptions, setLoginOptions] = useState<{ organization: string; methods: SignInMethod[] }>();
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState(() => new URLSearchParams(location.search).get("sign-in") === "failed"
    ? "Enterprise sign-in could not be completed. Check your organization ID, credentials, and site access." : "");

  useEffect(() => {
    const lifetime = new AbortController();
    let timer: number;
    const bus = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("proxigo-enterprise-sign-in") : undefined;
    if (bus) bus.onmessage = () => { setSites(undefined); setActive(undefined); setAttempt((value) => value + 1); };
    function restore(event: PageTransitionEvent) {
      if (event.persisted) { setSites(undefined); setActive(undefined); setAttempt((value) => value + 1); }
    }
    window.addEventListener("pageshow", restore);
    async function refresh() {
      try {
        const signal = AbortSignal.any([lifetime.signal, AbortSignal.timeout(27_000)]);
        const response = await fetch(API + "/session", { signal, cache: "no-store", redirect: "error" });
        if (lifetime.signal.aborted) return;
        if (response.status === 401) {
          setSignedIn(false); setSites([]); setActive(undefined);
          return;
        }
        if (!response.ok) throw new Error("Enterprise site directory is unavailable.");
        const data = await response.json();
        if (lifetime.signal.aborted) return;
        if (typeof data.organizationId !== "string" || !SITE_ID.test(data.organizationId) || !Array.isArray(data.sites)) {
          throw new Error("The organization directory returned an invalid response.");
        }
        const next = data.sites.filter(isSiteOperator).filter((site: SiteSession["operator"]) =>
          site.organization.id === data.organizationId && SITE_ID.test(site.siteId)).map((operator: SiteSession["operator"]): SiteSession => ({
            base: new URL(`${API}/orgs/${operator.organization.id}/sites/${operator.siteId}/`, location.origin).href,
            operator, token: "", enterprise: true,
          }));
        setSignedIn(true); setError("");
        setSites((previous) => previous && sameSites(previous, next) ? previous : next);
      } catch {
        if (!lifetime.signal.aborted) {
          setError("The enterprise site directory is unavailable. Try again.");
          setSites((previous) => previous ?? []);
        }
      } finally {
        if (!lifetime.signal.aborted) timer = window.setTimeout(refresh, 15_000);
      }
    }
    void refresh();
    return () => { lifetime.abort(); window.clearTimeout(timer); bus?.close(); window.removeEventListener("pageshow", restore); };
  }, [attempt]);

  useEffect(() => {
    if (signedIn || !SITE_ID.test(organization)) return;
    const request = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`${API}/login-options?${new URLSearchParams({ organization })}`, {
          cache: "no-store", signal: request.signal, redirect: "error",
        });
        if (!response.ok) throw new Error();
        const { methods } = await response.json();
        if (!Array.isArray(methods) || !methods.length || !methods.every((method) =>
          typeof method?.id === "string" && typeof method.label === "string")) throw new Error();
        if (!request.signal.aborted) setLoginOptions({ organization, methods });
      } catch {
        if (!request.signal.aborted) setLoginOptions({ organization, methods: [] });
      }
    }, 160);
    return () => { window.clearTimeout(timer); request.abort(); };
  }, [organization, signedIn]);

  const current = sites?.find((site) => site.base === active?.base) ?? sites?.[0];
  const methods = loginOptions?.organization === organization ? loginOptions.methods : undefined;

  async function signOut() {
    setBusy(true); setError("");
    try {
      const response = await fetch(API + "/logout", { method: "POST", redirect: "error" });
      if (!response.ok) throw new Error();
      setActive(undefined); setSites([]); setSignedIn(false);
      if (typeof BroadcastChannel !== "undefined") {
        const bus = new BroadcastChannel("proxigo-enterprise-sign-in");
        bus.postMessage("signed-out");
        bus.close();
      }
      setAttempt((value) => value + 1);
    } catch {
      setError("Sign-out failed. Try again.");
    } finally { setBusy(false); }
  }

  const signOutButton = <button className="gv-button" type="button" disabled={busy} onClick={() => void signOut()}>Sign out</button>;
  if (current && sites) return <GlobalView key={JSON.stringify([current.base, current.operator.subject])} enterprise={{
    current, sites, incidentId: current.base === active?.base ? active.incidentId : undefined, incidentReplay: active?.incidentReplay,
    visit: (site, incidentId, replay) => setActive({ base: site.base, incidentId, incidentReplay: replay }),
    onAccessEnded: () => { setSites(undefined); setActive(undefined); setAttempt((value) => value + 1); },
    controls: <div className="gv-enterprise-controls">
      {sites.length > 1 && <select aria-label="Organization site" value={current.base} onChange={(event) => setActive({ base: event.currentTarget.value })}>
        {sites.map((site) => <option key={site.base} value={site.base}>{site.operator.site}</option>)}
      </select>}
      {signOutButton}
      {error && <p role="alert">{error}</p>}
    </div>,
  }} />;

  return <div className="gv-app gv-enterprise-entry">
    <main className="gv-enterprise-card" aria-labelledby="gv-enterprise-heading">
      <img src="/proxigo-mark.png" width={32} height={32} alt="" />
      <h1 id="gv-enterprise-heading">Proxigo Global View</h1>
      {sites === undefined ? <p role="status">Checking your enterprise session…</p>
        : signedIn ? <><p>No sites are currently available to this account.</p>{signOutButton}</>
          : passwordForm ? <form method="post" action={API + "/supabase/password"} className="gv-incident-form">
            <p>Sign in to {organization}</p>
            <input type="hidden" name="organization" value={organization} />
            <label>Email<input type="email" name="email" autoComplete="username" required maxLength={254} /></label>
            <label>Password<input type="password" name="password" autoComplete="current-password" required maxLength={512} /></label>
            <button className="gv-button gv-primary" type="submit">Sign in</button>
            <button className="gv-button" type="button" onClick={() => setPasswordForm(false)}>Back to sign-in options</button>
          </form>
          : <form method="post" action={API + "/login"} className="gv-incident-form">
            <label>Organization ID<input name="organization" autoComplete="organization" autoCapitalize="none" required maxLength={80}
              value={organization} onChange={(event) => {
                setLoginOptions(undefined);
                setOrganization(event.target.value.toLowerCase());
              }} /></label>
            {methods?.length ? methods.map(({ id, label, passwordForm: needsPassword }, index) =>
              <button key={id} className={index === 0 ? "gv-button gv-primary" : "gv-button"}
                type={needsPassword ? "button" : "submit"} name="method" value={id}
                onClick={needsPassword ? () => setPasswordForm(true) : undefined}>{label}</button>) :
              <button className="gv-button gv-primary" type="submit" disabled>Sign in with your organization</button>}
            {methods?.length === 0 && <p role="status">Sign-in options are unavailable. Check your organization ID.</p>}
          </form>}
      {error && <p role="alert">{error}</p>}
    </main>
  </div>;
}
