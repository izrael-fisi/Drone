import { requireDiscovery } from "../platform/api/backend";
import { ApiError, requestJson } from "../platform/api/http";

/**
 * Proxigo account API. Server addresses and the auth provider come from the
 * active backend connection (Proxigo Cloud or a self-hosted server, see
 * src/platform/api/backend.ts); all requests go through the platform HTTP
 * layer.
 */

export interface ProxigoSession {
  access_token: string;
  refresh_token: string;
  expires_at: number; // unix ms
  user_id: string;
  email: string;
}

export interface OrgMember {
  user_id: string;
  role: "admin" | "member";
  km2_allowance: number | null;
  km2_used: number;
}

export interface OrgContext {
  org_id: string;
  org_name: string;
  org_plan: string;
  role: "admin" | "member";
  org_km2_limit: number;
  org_km2_used: number;
  org_km2_remaining: number;
  my_km2_allowance: number | null;
  my_km2_used: number;
  member_count: number;
  members?: OrgMember[]; // only present for admins
}

export interface CloudAccount {
  user_id: string;
  email: string;
  name: string | null;
  plan: string | null;
  subscription_active: boolean;
  km2_used: number;
  km2_limit: number;
  km2_remaining: number;
  modules: Array<{ serial: string; nickname: string | null; status: string }>;
  org: OrgContext | null;
  /**
   * Platform product entitlement keys for the user's organization (e.g.
   * "macula_vps"). Absent on accounts that predate multi-product licensing;
   * see src/platform/entitlements.ts for the fallback.
   */
  entitled_products?: string[] | null;
}

export interface OrgMapRegion {
  id: string;
  org_id: string;
  created_by: string;
  name: string;
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
  zoom: number;
  source?: string;
  location_label?: string;
  created_at: string;
}

export interface UsageSummary {
  plan: string | null;
  km2_used: number;
  km2_limit: number;
  km2_remaining: number;
}

type ErrorBody = { error?: string; error_description?: string; message?: string; msg?: string } | null;

function errorText(data: unknown, fallback: string) {
  const body = (data && typeof data === "object" ? data : null) as ErrorBody;
  return body?.error_description ?? body?.error ?? body?.message ?? body?.msg ?? fallback;
}

async function authPost(path: string, body: unknown) {
  const discovery = requireDiscovery();
  if (discovery.auth.kind !== "supabase") {
    throw new ApiError(
      `${discovery.name} uses single sign-on (OIDC), which this version of the desktop app does not support yet.`,
      "config",
    );
  }
  return requestJson<Record<string, any>>({
    method: "POST",
    url: `${discovery.auth.url}${path}`,
    headers: { apikey: discovery.auth.anonKey },
    body,
    backendTls: true,
  });
}

/**
 * Call the Proxigo API. Non-2xx responses throw an error whose message
 * includes "(HTTP <status>)" so `withSession` can detect 401s.
 */
async function apiCall<T>(
  session: ProxigoSession,
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  body: unknown,
  fallbackError: string,
): Promise<T> {
  const { apiBaseUrl } = requireDiscovery();
  const response = await requestJson<T>({
    method,
    url: `${apiBaseUrl}${path}`,
    headers: { Authorization: `Bearer ${session.access_token}` },
    body,
    backendTls: true,
  });
  if (!response.ok) {
    throw new ApiError(`${errorText(response.data, fallbackError)} (HTTP ${response.status})`, "http", response.status);
  }
  return response.data;
}

function sessionFromData(data: Record<string, any>): ProxigoSession {
  return {
    access_token: data.access_token,
    refresh_token: data.refresh_token,
    expires_at: Date.now() + data.expires_in * 1000,
    user_id: data.user?.id ?? "",
    email: data.user?.email ?? "",
  };
}

export const proxigo = {
  async login(email: string, password: string): Promise<ProxigoSession> {
    const res = await authPost("/auth/v1/token?grant_type=password", { email, password });
    if (!res.ok) throw new ApiError(errorText(res.data, "Login failed"), "http", res.status);
    return sessionFromData(res.data);
  },

  async refreshSession(session: ProxigoSession): Promise<ProxigoSession> {
    const res = await authPost("/auth/v1/token?grant_type=refresh_token", {
      refresh_token: session.refresh_token,
    });
    if (!res.ok) throw new ApiError("Session refresh failed — please log in again.", "http", res.status);
    return sessionFromData(res.data);
  },

  isExpired(session: ProxigoSession): boolean {
    return Date.now() >= session.expires_at - 60_000; // 1 min buffer
  },

  /**
   * Execute `fn` with a guaranteed-fresh session.
   * - Refreshes before the call if the token is within 60 s of expiry.
   * - On a 401 response, refreshes once and retries.
   * `onRefresh` is called whenever a new session is issued so the caller
   * can persist it (update store + disk).
   */
  async withSession<T>(
    session: ProxigoSession,
    onRefresh: (fresh: ProxigoSession) => void | Promise<void>,
    fn: (s: ProxigoSession) => Promise<T>
  ): Promise<T> {
    let s = session;
    if (this.isExpired(s)) {
      s = await this.refreshSession(s);
      await onRefresh(s);
    }
    try {
      return await fn(s);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("401") || msg.toLowerCase().includes("unauthorized")) {
        s = await this.refreshSession(s);
        await onRefresh(s);
        return fn(s);
      }
      throw err;
    }
  },

  async getAccount(session: ProxigoSession): Promise<CloudAccount> {
    return apiCall<CloudAccount>(session, "GET", "/api/account", undefined, "Failed to fetch account");
  },

  async getUsage(session: ProxigoSession): Promise<UsageSummary> {
    return apiCall<UsageSummary>(session, "GET", "/api/usage", undefined, "Failed to fetch usage");
  },

  async getOrgMaps(session: ProxigoSession): Promise<OrgMapRegion[]> {
    try {
      return (await apiCall<OrgMapRegion[]>(session, "GET", "/api/org/maps", undefined, "Failed to fetch org maps")) ?? [];
    } catch {
      return [];
    }
  },

  async publishOrgMap(
    session: ProxigoSession,
    region: { name: string; lat_min: number; lat_max: number; lon_min: number; lon_max: number; zoom: number; source?: string; location_label?: string }
  ): Promise<OrgMapRegion> {
    return apiCall<OrgMapRegion>(session, "POST", "/api/org/maps", region, "Failed to publish map");
  },

  async removeOrgMap(session: ProxigoSession, id: string): Promise<void> {
    await apiCall<unknown>(session, "DELETE", "/api/org/maps", { id }, "Failed to remove org map");
  },

  async reportMapDownload(
    session: ProxigoSession,
    km2: number,
    moduleSerial: string,
    sessionId?: string,
    bbox?: { lat_min: number; lat_max: number; lon_min: number; lon_max: number },
    locationLabel?: string
  ): Promise<{ ok: boolean; total_km2_this_month: number }> {
    return apiCall(session, "POST", "/api/usage", {
      km2,
      module_serial:  moduleSerial,
      session_id:     sessionId,
      location_label: locationLabel,
      lat_min:        bbox?.lat_min,
      lat_max:        bbox?.lat_max,
      lon_min:        bbox?.lon_min,
      lon_max:        bbox?.lon_max,
    }, "Failed to report usage");
  },
};
