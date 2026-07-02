const SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL as string) ?? "";
const SUPABASE_ANON_KEY = (import.meta.env.VITE_SUPABASE_ANON_KEY as string) ?? "";
const PROXIGO_API_URL = (import.meta.env.VITE_PROXIGO_API_URL as string) ?? "https://proxigo.us";

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
}

export async function setMemberAllowance(
  session: ProxigoSession,
  userId: string,
  km2Allowance: number | null
): Promise<void> {
  const res = await fetch(`${(import.meta.env.VITE_PROXIGO_API_URL as string) ?? "https://proxigo.us"}/api/org`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    },
    body: JSON.stringify({ user_id: userId, km2_allowance: km2Allowance }),
  });
  if (!res.ok) {
    const d = await res.json();
    throw new Error(d.error ?? "Failed to update allowance");
  }
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

async function supabasePost(path: string, body: unknown): Promise<Response> {
  return fetch(`${SUPABASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: SUPABASE_ANON_KEY,
    },
    body: JSON.stringify(body),
  });
}

function apiHeaders(accessToken: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${accessToken}`,
  };
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
    const res = await supabasePost("/auth/v1/token?grant_type=password", { email, password });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error_description ?? data.message ?? "Login failed");
    }
    return sessionFromData(data);
  },

  async refreshSession(session: ProxigoSession): Promise<ProxigoSession> {
    const res = await supabasePost("/auth/v1/token?grant_type=refresh_token", {
      refresh_token: session.refresh_token,
    });
    const data = await res.json();
    if (!res.ok) throw new Error("Session refresh failed — please log in again.");
    return sessionFromData(data);
  },

  isExpired(session: ProxigoSession): boolean {
    return Date.now() >= session.expires_at - 60_000; // 1 min buffer
  },

  async getAccount(session: ProxigoSession): Promise<CloudAccount> {
    const res = await fetch(`${PROXIGO_API_URL}/api/account`, {
      headers: apiHeaders(session.access_token),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? "Failed to fetch account");
    return data as CloudAccount;
  },

  async getUsage(session: ProxigoSession): Promise<UsageSummary> {
    const res = await fetch(`${PROXIGO_API_URL}/api/usage`, {
      headers: apiHeaders(session.access_token),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? "Failed to fetch usage");
    return data as UsageSummary;
  },

  async getOrgMaps(session: ProxigoSession): Promise<OrgMapRegion[]> {
    const res = await fetch(`${PROXIGO_API_URL}/api/org/maps`, {
      headers: apiHeaders(session.access_token),
    });
    if (!res.ok) return [];
    return res.json();
  },

  async publishOrgMap(
    session: ProxigoSession,
    region: { name: string; lat_min: number; lat_max: number; lon_min: number; lon_max: number; zoom: number; source?: string; location_label?: string }
  ): Promise<OrgMapRegion> {
    const res = await fetch(`${PROXIGO_API_URL}/api/org/maps`, {
      method: "POST",
      headers: apiHeaders(session.access_token),
      body: JSON.stringify(region),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? "Failed to publish map");
    return data;
  },

  async removeOrgMap(session: ProxigoSession, id: string): Promise<void> {
    const res = await fetch(`${PROXIGO_API_URL}/api/org/maps`, {
      method: "DELETE",
      headers: apiHeaders(session.access_token),
      body: JSON.stringify({ id }),
    });
    if (!res.ok) {
      const data = await res.json();
      throw new Error(data.error ?? "Failed to remove org map");
    }
  },

  async reportMapDownload(
    session: ProxigoSession,
    km2: number,
    moduleSerial: string,
    sessionId?: string,
    bbox?: { lat_min: number; lat_max: number; lon_min: number; lon_max: number },
    locationLabel?: string
  ): Promise<{ ok: boolean; total_km2_this_month: number }> {
    const res = await fetch(`${PROXIGO_API_URL}/api/usage`, {
      method: "POST",
      headers: apiHeaders(session.access_token),
      body: JSON.stringify({
        km2,
        module_serial:  moduleSerial,
        session_id:     sessionId,
        location_label: locationLabel,
        lat_min:        bbox?.lat_min,
        lat_max:        bbox?.lat_max,
        lon_min:        bbox?.lon_min,
        lon_max:        bbox?.lon_max,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error ?? "Failed to report usage");
    return data;
  },
};
