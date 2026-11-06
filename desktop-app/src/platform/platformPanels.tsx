import { Globe2, Settings as SettingsIcon, UserRound } from "lucide-react";
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
    id: "settings",
    label: "Settings",
    Icon: SettingsIcon,
    command: { label: "General Settings", detail: "Theme, profile, imagery keys" },
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
