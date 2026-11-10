import { invoke, isTauri } from "@tauri-apps/api/core";
import { create } from "zustand";
import { ApiError, requestJson } from "./http";

/**
 * Which Proxigo server the desktop app talks to, and what that server
 * supports.
 *
 * - Proxigo Cloud: settings come from this build's VITE_* variables.
 * - Self-hosted (on-prem / private cloud): the operator enters the server's
 *   address; everything else is read from `{base}/.well-known/proxigo`.
 *
 * The connection itself is stored by Rust (`backend.json`) because Rust also
 * applies its TLS settings and keys session tokens to it.
 */

export type BackendKind = "proxigo_cloud" | "self_hosted";

export interface BackendConnection {
  id: string;
  kind: BackendKind;
  label: string;
  base_url?: string | null;
  ca_cert_path?: string | null;
}

export type BackendAuth =
  | { kind: "supabase"; url: string; anonKey: string }
  | { kind: "oidc"; issuer: string; clientId: string };

export interface BackendDiscovery {
  /** Display name, e.g. "Proxigo Cloud" or "ACME Proxigo". */
  name: string;
  apiVersion: string;
  /** Root for `/api/...` paths. */
  apiBaseUrl: string;
  auth: BackendAuth;
  /** Product entitlement keys licensed for the whole server (on-prem license), if declared. */
  products?: string[];
}

/** Wire format of `GET {base}/.well-known/proxigo`. See docs/proxigo-api.md. */
interface DiscoveryDocument {
  name?: string;
  api_version?: string;
  api_base_url?: string;
  auth?: { kind?: string; url?: string; anon_key?: string; issuer?: string; client_id?: string };
  products?: unknown;
}

const CLOUD_CONNECTION: BackendConnection = { id: "proxigo-cloud", kind: "proxigo_cloud", label: "Proxigo Cloud" };
const DISCOVERY_CACHE_PREFIX = "proxigo.backend.discovery.v1:";

function cloudDiscovery(): BackendDiscovery {
  const supabaseUrl = (import.meta.env.VITE_SUPABASE_URL as string | undefined) ?? "";
  const anonKey = (import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined) ?? "";
  const apiBaseUrl = (import.meta.env.VITE_PROXIGO_API_URL as string | undefined) || "https://proxigo.us";
  if (!supabaseUrl || !anonKey) {
    throw new ApiError(
      "Proxigo Cloud sign-in is not configured in this build: set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in desktop-app/.env (see .env.example) and restart.",
      "config",
    );
  }
  return {
    name: "Proxigo Cloud",
    apiVersion: "1",
    apiBaseUrl: apiBaseUrl.replace(/\/+$/, ""),
    auth: { kind: "supabase", url: supabaseUrl.replace(/\/+$/, ""), anonKey },
  };
}

function parseDiscovery(doc: DiscoveryDocument, baseUrl: string): BackendDiscovery {
  const auth = doc.auth ?? {};
  let parsedAuth: BackendAuth;
  if (auth.kind === "supabase" && auth.url && auth.anon_key) {
    parsedAuth = { kind: "supabase", url: auth.url.replace(/\/+$/, ""), anonKey: auth.anon_key };
  } else if (auth.kind === "oidc" && auth.issuer && auth.client_id) {
    parsedAuth = { kind: "oidc", issuer: auth.issuer, clientId: auth.client_id };
  } else {
    throw new ApiError("The server's discovery document has no usable auth section.", "parse");
  }
  return {
    name: doc.name?.trim() || new URL(baseUrl).host,
    apiVersion: doc.api_version ?? "1",
    apiBaseUrl: (doc.api_base_url || baseUrl).replace(/\/+$/, ""),
    auth: parsedAuth,
    products: Array.isArray(doc.products) ? doc.products.filter((item): item is string => typeof item === "string") : undefined,
  };
}

function readCachedDiscovery(connectionId: string): BackendDiscovery | null {
  try {
    const raw = window.localStorage.getItem(DISCOVERY_CACHE_PREFIX + connectionId);
    return raw ? (JSON.parse(raw) as BackendDiscovery) : null;
  } catch {
    return null;
  }
}

function writeCachedDiscovery(connectionId: string, discovery: BackendDiscovery) {
  try {
    window.localStorage.setItem(DISCOVERY_CACHE_PREFIX + connectionId, JSON.stringify(discovery));
  } catch {
    // Offline fallback only.
  }
}

async function discover(connection: BackendConnection): Promise<{ discovery: BackendDiscovery; offline: boolean }> {
  if (connection.kind === "proxigo_cloud") return { discovery: cloudDiscovery(), offline: false };
  const baseUrl = connection.base_url;
  if (!baseUrl) throw new ApiError("No server address configured.", "config");
  try {
    const response = await requestJson<DiscoveryDocument>({
      url: `${baseUrl}/.well-known/proxigo`,
      backendTls: true,
      timeoutMs: 10_000,
    });
    if (!response.ok || !response.data) {
      throw new ApiError(
        `${baseUrl} is not a Proxigo server (discovery returned HTTP ${response.status}).`,
        "http",
        response.status,
      );
    }
    const discovery = parseDiscovery(response.data, baseUrl);
    writeCachedDiscovery(connection.id, discovery);
    return { discovery, offline: false };
  } catch (error) {
    // Field use: keep working with the last known server settings.
    const cached = error instanceof ApiError && error.kind === "network" ? readCachedDiscovery(connection.id) : null;
    if (cached) return { discovery: cached, offline: true };
    throw error;
  }
}

interface BackendState {
  connection: BackendConnection | null;
  discovery: BackendDiscovery | null;
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;
  /** True when discovery came from cache because the server was unreachable. */
  offline: boolean;
  /** Load the saved connection and run discovery. */
  load: () => Promise<void>;
  /** Save a new connection (signs the session out server-side) and run discovery. */
  connect: (connection: Pick<BackendConnection, "kind" | "label" | "base_url" | "ca_cert_path">) => Promise<void>;
}

export const useBackendStore = create<BackendState>((set) => {
  const runDiscovery = async (connection: BackendConnection) => {
    set({ connection, status: "loading", error: null });
    try {
      const { discovery, offline } = await discover(connection);
      set({ discovery, offline, status: "ready" });
    } catch (error) {
      set({ discovery: null, offline: false, status: "error", error: error instanceof Error ? error.message : String(error) });
    }
  };

  return {
    connection: null,
    discovery: null,
    status: "idle",
    error: null,
    offline: false,
    load: async () => {
      let connection = CLOUD_CONNECTION;
      if (isTauri()) {
        try {
          connection = await invoke<BackendConnection>("backend_get_connection");
        } catch (error) {
          console.warn("Could not read backend connection; using Proxigo Cloud", error);
        }
      }
      await runDiscovery(connection);
    },
    connect: async (next) => {
      let connection: BackendConnection = next.kind === "proxigo_cloud" ? CLOUD_CONNECTION : { id: "", ...next };
      if (isTauri()) {
        try {
          connection = await invoke<BackendConnection>("backend_set_connection", { connection: { id: "", ...next } });
        } catch (error) {
          set({ status: "error", error: String(error), discovery: null });
          return;
        }
      }
      await runDiscovery(connection);
    },
  };
});

/** Discovery for the active connection, or a clear error if not connected. */
export function requireDiscovery(): BackendDiscovery {
  const { discovery, error } = useBackendStore.getState();
  if (!discovery) throw new ApiError(error ?? "Not connected to a Proxigo server yet.", "config");
  return discovery;
}
