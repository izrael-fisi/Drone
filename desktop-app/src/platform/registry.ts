import { airspaceWeatherProduct } from "../products/airspace-weather";
import { maculaVpsProduct } from "../products/macula-vps";
import type { ProductModule } from "./types";

/**
 * Every product module compiled into this build, in display order.
 *
 * To add a product: create `src/products/<id>/index.tsx` exporting a
 * `ProductModule`, append it here, and give it an entitlement key that the
 * Proxigo API returns in `CloudAccount.entitled_products`. See
 * docs/desktop-platform.md.
 *
 * This list must stay static at runtime: the shell calls each module's hooks
 * in this order on every render.
 */
export const PRODUCT_MODULES: readonly ProductModule[] = [maculaVpsProduct, airspaceWeatherProduct];

assertUniqueIds();

export function productById(id: string | null | undefined): ProductModule | undefined {
  return PRODUCT_MODULES.find((product) => product.id === id);
}

export function productForPanel(panelId: string | undefined): ProductModule | undefined {
  if (!panelId) return undefined;
  return PRODUCT_MODULES.find((product) => product.panels.some((panel) => panel.id === panelId));
}

export function productForBottomTab(tabId: string): ProductModule | undefined {
  return PRODUCT_MODULES.find((product) => product.bottomDock?.tabIds.includes(tabId));
}

function assertUniqueIds() {
  const seen = new Map<string, string>();
  const claim = (id: string, owner: string) => {
    const existing = seen.get(id);
    if (existing) throw new Error(`Platform id "${id}" is registered by both ${existing} and ${owner}`);
    seen.set(id, owner);
  };
  for (const product of PRODUCT_MODULES) {
    claim(`product:${product.id}`, product.id);
    for (const panel of product.panels) claim(`panel:${panel.id}`, product.id);
  }
}
