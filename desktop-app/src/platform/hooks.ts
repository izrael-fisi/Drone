import { useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useShellStore } from "../lib/shellStore";
import { useAppStore } from "../lib/store";
import { useBackendStore } from "./api/backend";
import { resolveEntitlements, type EntitlementResolution } from "./entitlements";
import { OVERVIEW_ID, usePlatformStore } from "./platformStore";
import { PRODUCT_MODULES, productById, productForBottomTab, productForPanel } from "./registry";
import type { PlatformContext, PlatformMapLayer, ProductCommand, ProductModule, ProductStatusSummary } from "./types";

export function useEntitlements(): EntitlementResolution {
  const cloudAccount = useAppStore((state) => state.cloudAccount);
  const serverProducts = useBackendStore((state) => state.discovery?.products);
  return useMemo(() => resolveEntitlements(cloudAccount, PRODUCT_MODULES, serverProducts), [cloudAccount, serverProducts]);
}

export function useEntitledProducts(): ProductModule[] {
  const { entitled } = useEntitlements();
  return useMemo(() => PRODUCT_MODULES.filter((product) => entitled.has(product.id)), [entitled]);
}

/**
 * The product whose rail, bottom dock and tools are showing, or undefined for
 * the cross-product overview. Before the operator picks one, a single-product
 * organization lands directly in that product.
 */
export function useActiveProduct(): ProductModule | undefined {
  const activeProductId = usePlatformStore((state) => state.activeProductId);
  const entitledProducts = useEntitledProducts();
  if (activeProductId === OVERVIEW_ID) return undefined;
  const chosen = entitledProducts.find((product) => product.id === activeProductId);
  if (chosen) return chosen;
  return entitledProducts.length === 1 ? entitledProducts[0] : undefined;
}

/** Status summaries for every registered module (entitled or not), keyed by product id. */
export function useProductStatuses(): Record<string, ProductStatusSummary | undefined> {
  const statuses: Record<string, ProductStatusSummary | undefined> = {};
  for (const product of PRODUCT_MODULES) {
    statuses[product.id] = product.useStatus?.();
  }
  return statuses;
}

/** All layers from entitled products, with resolved visibility. */
export function usePlatformMapLayers(): Array<PlatformMapLayer & { visible: boolean }> {
  const { entitled } = useEntitlements();
  const layerVisibility = usePlatformStore((state) => state.layerVisibility);
  const perProduct = PRODUCT_MODULES.map((product) => product.useMapLayers?.() ?? []);
  return useMemo(
    () =>
      perProduct
        .flat()
        .filter((layer) => entitled.has(layer.productId))
        .map((layer) => ({
          ...layer,
          visible: layer.activeOnly ? true : layerVisibility[layer.id] ?? layer.defaultVisible ?? true,
        })),
    [entitled, layerVisibility, ...perProduct],
  );
}

/**
 * Visible layers that belong on the shared map in the current view: every
 * layer in the Overview; inside a product, its own layers plus the layers it
 * declares in `relatedLayerIds`.
 */
export function useMapLayersInView(): PlatformMapLayer[] {
  const layers = usePlatformMapLayers();
  const activeProduct = useActiveProduct();
  return useMemo(
    () =>
      layers.filter(
        (layer) =>
          layer.visible &&
          (layer.activeOnly ? layer.productId === activeProduct?.id : true) &&
          (!activeProduct ||
            layer.productId === activeProduct.id ||
            Boolean(activeProduct.relatedLayerIds?.includes(layer.id))),
      ),
    [activeProduct, layers],
  );
}

export function useProductCommands(): ProductCommand[] {
  const { entitled } = useEntitlements();
  const perProduct = PRODUCT_MODULES.map((product) => ({ product, commands: product.useCommands?.() ?? [] }));
  return perProduct.filter(({ product }) => entitled.has(product.id)).flatMap(({ commands }) => commands);
}

/** Shell actions bound to the router and dock stores. */
export function usePlatformContext(): PlatformContext {
  const navigate = useNavigate();
  const { resetRightDock, pushRightDock, setRightDockOpen, setBottomDockTab } = useShellStore();
  const setActiveProduct = usePlatformStore((state) => state.setActiveProduct);

  const openPanel = useCallback(
    (panelId: string) => {
      navigate("/home");
      const owner = productForPanel(panelId);
      if (owner) setActiveProduct(owner.id);
      else if (panelId === OVERVIEW_ID) setActiveProduct(OVERVIEW_ID);
      resetRightDock();
      pushRightDock(panelId);
    },
    [navigate, pushRightDock, resetRightDock, setActiveProduct],
  );

  const openBottomTab = useCallback(
    (tabId: string) => {
      navigate("/home");
      const owner = productForBottomTab(tabId);
      if (owner) setActiveProduct(owner.id);
      setBottomDockTab(tabId);
    },
    [navigate, setActiveProduct, setBottomDockTab],
  );

  return useMemo(
    () => ({ openPanel, pushPanel: pushRightDock, closeDock: () => setRightDockOpen(false), openBottomTab }),
    [openBottomTab, openPanel, pushRightDock, setRightDockOpen],
  );
}

/** Switch the shell to a product (or the overview) and open its default panel. */
export function useSwitchProduct() {
  const ctx = usePlatformContext();
  const setActiveProduct = usePlatformStore((state) => state.setActiveProduct);
  return useCallback(
    (productId: string) => {
      setActiveProduct(productId);
      const panelId = productById(productId)?.defaultPanelId ?? OVERVIEW_ID;
      ctx.openPanel(panelId);
    },
    [ctx, setActiveProduct],
  );
}
