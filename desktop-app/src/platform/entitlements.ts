import type { CloudAccount } from "../lib/proxigo";
import type { ProductModule } from "./types";

export type EntitlementSource = "account" | "server" | "cached" | "legacy-default" | "dev-override";

const CACHE_KEY = "proxigo.platform.entitlements.v1";

export interface EntitlementResolution {
  /** Product ids the signed-in organization may use. */
  entitled: Set<string>;
  source: EntitlementSource;
}

/**
 * Resolve which products the signed-in Proxigo organization may use.
 *
 * Client-side gating only controls what the shell shows; product backends and
 * the Proxigo API must still enforce access server-side.
 *
 * - `CloudAccount.entitled_products` (entitlement keys) is authoritative when present.
 * - Accounts without that field predate multi-product licensing and receive the
 *   modules marked `legacyDefault` (Macula VPS).
 * - While the account cannot be fetched (offline field use, API outage) the
 *   last entitlements seen on this machine apply, else the legacy default, so
 *   an operator is never locked out of a product they were already using.
 * - A self-hosted server may license products for the whole installation in
 *   its discovery document (`products`); that applies when the account does
 *   not list entitlements itself.
 * - In dev builds, `VITE_PLATFORM_PRODUCTS=macula_vps,other_key` overrides both.
 */
export function resolveEntitlements(
  account: CloudAccount | null,
  products: readonly ProductModule[],
  serverProducts?: readonly string[],
): EntitlementResolution {
  const byKey = (keys: Iterable<string>) => {
    const wanted = new Set(Array.from(keys, (key) => key.trim().toLowerCase()).filter(Boolean));
    return new Set(products.filter((product) => wanted.has(product.entitlementKey)).map((product) => product.id));
  };

  const override = import.meta.env.DEV ? (import.meta.env.VITE_PLATFORM_PRODUCTS as string | undefined) : undefined;
  if (override) {
    return { entitled: byKey(override.split(",")), source: "dev-override" };
  }

  if (account && Array.isArray(account.entitled_products)) {
    writeCachedKeys(account.entitled_products);
    return { entitled: byKey(account.entitled_products), source: "account" };
  }

  if (serverProducts) {
    writeCachedKeys([...serverProducts]);
    return { entitled: byKey(serverProducts), source: "server" };
  }

  if (!account) {
    const cached = readCachedKeys();
    if (cached) return { entitled: byKey(cached), source: "cached" };
  }

  return {
    entitled: new Set(products.filter((product) => product.legacyDefault).map((product) => product.id)),
    source: "legacy-default",
  };
}

function readCachedKeys(): string[] | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(CACHE_KEY) ?? "null") as unknown;
    return Array.isArray(parsed) && parsed.every((key) => typeof key === "string") ? parsed : null;
  } catch {
    return null;
  }
}

function writeCachedKeys(keys: string[]) {
  try {
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(keys));
  } catch {
    // Cache is a convenience for offline starts; ignore storage failures.
  }
}
