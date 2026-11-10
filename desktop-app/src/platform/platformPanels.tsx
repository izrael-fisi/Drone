import { GlobeHemisphereWestIcon as Globe2, GearSixIcon as SettingsIcon, MapPinAreaIcon, StackIcon, UserCircleIcon as UserRound } from "@phosphor-icons/react";
import { LAYERS_PANEL_ID, PROJECTS_PANEL_ID } from "./projects/constants";
import { ProjectsPanel } from "./projects/ProjectsPanel";
import { LayersPanel } from "./shell/LayersPanel";
import { OVERVIEW_ID } from "./platformStore";
import { PRODUCT_MODULES } from "./registry";
import { AccountPanel } from "./shell/AccountPanel";
import { GeneralSettingsPanel } from "./shell/GeneralSettingsPanel";
import { OverviewPanel } from "./shell/OverviewPanel";
import type { ProductModule, ProductPanel } from "./types";

/** Dock panels owned by the Proxigo shell rather than any one product. */
export const PLATFORM_PANELS: readonly ProductPanel[] = [
  {
    id: OVERVIEW_ID,
    label: "Overview",
    Icon: Globe2,
    command: { label: "Overview", detail: "All products, status and map layers", keywords: "global platform products" },
    Component: OverviewPanel,
  },
  {
    id: PROJECTS_PANEL_ID,
    label: "Projects",
    Icon: MapPinAreaIcon,
    command: { label: "Projects", detail: "Site layouts: cameras, sensors, geofences, keep-out zones", keywords: "site place camera sensor geofence keep out layout" },
    Component: ProjectsPanel,
  },
  {
    id: LAYERS_PANEL_ID,
    label: "Layers",
    Icon: StackIcon,
    command: { label: "Map layers", detail: "Show or hide map layers", keywords: "visibility overlay" },
    Component: LayersPanel,
  },
  {
    id: "settings",
    label: "Settings",
    Icon: SettingsIcon,
    command: { label: "General settings", detail: "Theme, profile, imagery keys" },
    Component: GeneralSettingsPanel,
  },
  {
    id: "account",
    label: "Account",
    Icon: UserRound,
    command: { label: "Account", detail: "Operator profile, organization and Proxigo sign-in" },
    Component: AccountPanel,
  },
];

for (const panel of PLATFORM_PANELS) {
  const owner = PRODUCT_MODULES.find((product) => product.panels.some((item) => item.id === panel.id));
  if (owner) throw new Error(`Panel id "${panel.id}" is reserved by the platform shell but registered by ${owner.id}`);
}

/** Find a dock panel among platform panels and the given (entitled) products. */
export function findPanel(panelId: string, products: readonly ProductModule[]): ProductPanel | undefined {
  return (
    PLATFORM_PANELS.find((panel) => panel.id === panelId) ??
    products.flatMap((product) => product.panels).find((panel) => panel.id === panelId)
  );
}
