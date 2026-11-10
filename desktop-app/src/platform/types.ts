import type { Icon as IconType } from "@phosphor-icons/react";
import type { ComponentType } from "react";

/**
 * Proxigo platform contracts.
 *
 * The desktop shell (header, global map, dock rail, command palette, account,
 * settings) is product-agnostic. Each product or integration registers a
 * `ProductModule` describing what it contributes to that shell. Macula VPS is
 * the first module; future drone products, third-party integrations and
 * analytics products plug in the same way.
 */

export type ProductCategory = "navigation" | "vehicle" | "integration" | "analytics";

export const PRODUCT_CATEGORY_LABELS: Record<ProductCategory, string> = {
  navigation: "Navigation",
  vehicle: "Vehicles & Robotics",
  integration: "Integrations",
  analytics: "Data & Analytics",
};

export type ProductHealth = "ready" | "attention" | "offline" | "idle";

export interface ProductStatusSummary {
  health: ProductHealth;
  /** One-line summary shown on the global overview. */
  headline: string;
  metrics?: Array<{ label: string; value: string | number }>;
}

/** Shell actions available to product panels and commands. */
export interface PlatformContext {
  /** Open a dock panel by id (platform or any entitled product). */
  openPanel: (panelId: string) => void;
  /** Push a drill-down panel on top of the current dock stack. */
  pushPanel: (panelId: string) => void;
  closeDock: () => void;
  /** Open a tab in the active product's bottom dock. */
  openBottomTab: (tabId: string) => void;
}

export interface ProductPanel {
  /** Globally unique dock route id. */
  id: string;
  label: string;
  Icon: IconType;
  /** Hidden panels are reachable as drill-downs/commands but get no rail button. */
  showInRail?: boolean;
  /** Rail button that should appear active while this drill-down is open. */
  railParent?: string;
  Component: ComponentType<{ ctx: PlatformContext }>;
  /** Optional command-palette entry for this panel. */
  command?: { label: string; detail: string; keywords?: string };
}

export type LatLon = [number, number];

/** Optional per-feature drawing overrides; unset fields use the defaults. */
export interface PlatformFeatureStyle {
  /** Point/area fill; defaults to the feature color. */
  fillColor?: string;
  fillOpacity?: number;
  /** Outline (points, areas) or line width in px. */
  width?: number;
  dashed?: boolean;
  /** Point radius in px. */
  radius?: number;
  /** Soft ring around a point, e.g. a live vehicle. */
  halo?: boolean;
  /** Show the label permanently next to a point (Leaflet renderer). */
  permanentLabel?: boolean;
}

/** `color` overrides the layer color for one feature (e.g. by category). */
export type PlatformMapFeature =
  | { id: string; kind: "point"; position: LatLon; label?: string; color?: string; style?: PlatformFeatureStyle }
  | { id: string; kind: "line" | "area"; positions: LatLon[]; label?: string; color?: string; style?: PlatformFeatureStyle };

/** A click on the shared map, with the product point feature under it if any. */
export interface PlatformMapClick {
  position: LatLon;
  hit?: { layerId: string; featureId: string };
}

/** A drawing tool a product enables on the shared map while it is active. */
export interface PlatformMapTool {
  /** "point": each click adds a point. "box": drag a rectangle. */
  mode: "point" | "box";
  /** Preview color for box drags. */
  color?: string;
  onPoint?: (point: LatLon) => void;
  /** Rectangle corners, clockwise from south-west. */
  onBox?: (corners: LatLon[]) => void;
}

/** Camera controls for the shared map, available to products. */
export interface PlatformMapApi {
  focus: (center: LatLon, zoom: number) => void;
  fitPoints: (points: LatLon[], maxZoom?: number) => void;
  /** Current map center and zoom. */
  getView: () => { center: LatLon; zoom: number };
}

/** Props for a product's on-map tools, rendered only while the product is active. */
export interface ProductMapOverlayProps {
  ctx: PlatformContext;
  /** Tailwind `left-*` class that keeps top-left HUDs clear of the dock. */
  leftInsetClass: string;
}

/** Renderer-agnostic overlay a product draws on the shared global map. */
export interface PlatformMapLayer {
  /** Globally unique layer id. */
  id: string;
  productId: string;
  label: string;
  color: string;
  features: PlatformMapFeature[];
  defaultVisible?: boolean;
  /**
   * Working state (drafts, live vehicle): drawn only while the owning product
   * is active, never in the Overview, and not offered as a layer toggle.
   */
  activeOnly?: boolean;
  /** Shown in every view (platform data such as site layouts), not just its product's. */
  global?: boolean;
  /** Always drawn, never offered as a toggle (e.g. a shape being drawn). */
  internal?: boolean;
}

export interface ProductBottomDockProps {
  open: boolean;
  tab: string;
  onOpenChange: (open: boolean) => void;
  onTabChange: (tab: string) => void;
}

export interface ProductCommand {
  id: string;
  label: string;
  detail: string;
  group: string;
  keywords?: string;
  Icon: IconType;
  disabled?: boolean;
  run: (ctx: PlatformContext) => void;
}

export interface ProductModule {
  id: string;
  name: string;
  shortName: string;
  tagline: string;
  category: ProductCategory;
  Icon: IconType;
  /** Hex accent used for the product's badge and map layers. */
  accent: string;
  /** Key matched against `CloudAccount.entitled_products`. */
  entitlementKey: string;
  /**
   * Entitled for Proxigo accounts that predate product entitlements (the
   * account payload has no `entitled_products` field).
   */
  legacyDefault?: boolean;
  panels: ProductPanel[];
  /** Panel opened when the operator switches to this product. */
  defaultPanelId?: string;
  bottomDock?: {
    tabIds: readonly string[];
    defaultTabId: string;
    Component: ComponentType<ProductBottomDockProps>;
  };
  /**
   * Layers from other products that are relevant inside this product's view.
   * The shared map shows only the active product's own layers plus these; the
   * Overview shows every layer.
   */
  relatedLayerIds?: readonly string[];
  /** On-map tools and HUD for this product, shown only while it is active. */
  MapOverlay?: ComponentType<ProductMapOverlayProps>;
  /**
   * Map clicks while this product is active, and clicks on this product's
   * point features from the Overview.
   */
  onMapClick?: (click: PlatformMapClick, ctx: PlatformContext) => void;
  // Hooks below are called unconditionally for every registered module, in
  // registry order, so each module must always (or never) define them.
  useStatus?: () => ProductStatusSummary;
  useMapLayers?: () => PlatformMapLayer[];
  useCommands?: () => ProductCommand[];
  /**
   * Always-mounted work that must continue while another product is in view
   * (e.g. live telemetry). Must do nothing when `entitled` is false.
   */
  useBackground?: (entitled: boolean) => void;
}
