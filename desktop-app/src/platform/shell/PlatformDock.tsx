import {
  CaretDoubleLeftIcon,
  CaretDoubleRightIcon,
  CaretLeftIcon,
  CaretUpDownIcon,
  GearSixIcon,
  GlobeHemisphereWestIcon,
  MapPinAreaIcon,
  StackIcon,
  UserCircleIcon,
  XIcon,
  type Icon,
} from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useShellStore } from "../../lib/shellStore";
import { cn } from "../../lib/utils";
import { useActiveProduct, useEntitledProducts, useProductStatuses, useSwitchProduct } from "../hooks";
import { findPanel } from "../platformPanels";
import { OVERVIEW_ID, usePlatformStore } from "../platformStore";
import { LAYERS_PANEL_ID, PROJECTS_PANEL_ID } from "../projects/constants";
import { useActiveProject } from "../projects/store";
import { productForPanel } from "../registry";
import type { PlatformContext, ProductHealth } from "../types";
import { EmptyLine, PLATFORM_ACCENT } from "../ui";
import { dockMode } from "./layout";

const HEALTH_DOT: Record<ProductHealth, string> = {
  ready: "bg-status-ready",
  attention: "bg-status-warning",
  offline: "bg-status-critical",
  idle: "bg-slate-500",
};

/** One sidebar entry: icon and label, or icon only with a tooltip when the rail is compact. */
function NavItem({
  Icon,
  label,
  active,
  compact,
  accent = PLATFORM_ACCENT,
  trailing,
  onClick,
}: {
  Icon: Icon;
  label: string;
  active: boolean;
  compact: boolean;
  accent?: string;
  trailing?: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      aria-label={compact ? label : undefined}
      title={compact ? label : undefined}
      className={cn(
        "group flex h-9 shrink-0 items-center rounded-lg text-[13.5px] transition-colors",
        compact ? "w-9 justify-center" : "w-full gap-2.5 px-2.5",
        active ? "text-slate-50" : "text-slate-300 hover:bg-white/[0.06] hover:text-slate-50",
      )}
      style={active ? { backgroundColor: `${accent}26` } : undefined}
    >
      <Icon size={19} weight={active ? "duotone" : "regular"} className="shrink-0" style={active ? { color: accent } : undefined} />
      {!compact && <span className="min-w-0 flex-1 truncate text-left">{label}</span>}
      {!compact && trailing}
    </button>
  );
}

function SectionHeading({ compact, children }: { compact: boolean; children: ReactNode }) {
  if (compact) return <div className="mx-auto my-2 h-px w-6 bg-white/10" aria-hidden />;
  return <div className="px-2.5 pb-1 pt-4 text-xs font-medium text-slate-500">{children}</div>;
}

/**
 * Left sidebar on glass over the map. Labelled when nothing is open; it
 * compacts to an icon rail while a panel is open or when the operator
 * collapses it. Order: site project, workspace views, the active product's
 * tools, then settings and account.
 */
export function PlatformDock({ ctx }: { ctx: PlatformContext }) {
  const { rightDockOpen: open, rightDockStack: stack, setRightDockOpen, popRightDock } = useShellStore();
  const sidebarCollapsed = usePlatformStore((state) => state.sidebarCollapsed);
  const setSidebarCollapsed = usePlatformStore((state) => state.setSidebarCollapsed);
  const activeProduct = useActiveProduct();
  const entitledProducts = useEntitledProducts();
  const statuses = useProductStatuses();
  const switchProduct = useSwitchProduct();
  const project = useActiveProject();

  const compact = dockMode(open, sidebarCollapsed) !== "expanded";
  const visibleStack = stack.filter((item) => item !== "root");
  const route = visibleStack[visibleStack.length - 1] ?? activeProduct?.defaultPanelId ?? OVERVIEW_ID;
  const panel = findPanel(route, entitledProducts);
  const railRoute = open ? panel?.railParent ?? route : null;
  const panelOwner = productForPanel(route);
  const PanelComponent = panel?.Component;
  const parentLabel = visibleStack.length > 1 ? findPanel(visibleStack[visibleStack.length - 2], entitledProducts)?.label : undefined;

  const toggleRoute = (panelId: string) => {
    if (open && railRoute === panelId) setRightDockOpen(false);
    else ctx.openPanel(panelId);
  };

  const productStatus = activeProduct ? statuses[activeProduct.id] : undefined;
  const projectLabel = `Project: ${project?.name ?? "none yet"}`;

  return (
    <aside className="flex h-full overflow-hidden rounded-2xl border border-white/[0.09] bg-[rgba(13,14,16,0.62)] shadow-[0_24px_64px_rgba(0,0,0,0.5),inset_0_1px_0_rgba(255,255,255,0.06)] backdrop-blur-2xl backdrop-saturate-150">
      <nav aria-label="Main" className={cn("flex h-full shrink-0 flex-col py-2.5", compact ? "w-[60px] items-center" : "w-[224px] px-2.5")}>
        {/* Working site: the project new placements go into. */}
        <button
          type="button"
          onClick={() => toggleRoute(PROJECTS_PANEL_ID)}
          title={compact ? projectLabel : undefined}
          aria-label={compact ? projectLabel : undefined}
          aria-current={railRoute === PROJECTS_PANEL_ID ? "page" : undefined}
          className={cn(
            "flex shrink-0 items-center rounded-xl transition-colors hover:bg-white/[0.07]",
            compact ? "h-10 w-10 justify-center" : "w-full gap-2.5 px-2.5 py-2",
            railRoute === PROJECTS_PANEL_ID ? "bg-white/[0.1]" : !compact && "bg-white/[0.04]",
          )}
        >
          <MapPinAreaIcon size={compact ? 20 : 18} weight={railRoute === PROJECTS_PANEL_ID ? "fill" : "duotone"} className="shrink-0 text-slate-200" />
          {!compact && (
            <>
              <span className="min-w-0 flex-1 text-left">
                <span className="block text-[11px] text-slate-500">Project</span>
                <span className="block truncate text-[13.5px] font-medium text-slate-100">{project?.name ?? "No project yet"}</span>
              </span>
              <CaretUpDownIcon size={14} className="shrink-0 text-slate-500" />
            </>
          )}
        </button>

        <div className={cn("mt-2 flex flex-col gap-0.5", compact && "items-center")}>
          <NavItem Icon={GlobeHemisphereWestIcon} label="Overview" compact={compact} active={railRoute === OVERVIEW_ID} onClick={() => toggleRoute(OVERVIEW_ID)} />
          <NavItem Icon={StackIcon} label="Layers" compact={compact} active={railRoute === LAYERS_PANEL_ID} onClick={() => toggleRoute(LAYERS_PANEL_ID)} />
        </div>

        <div className={cn("min-h-0 flex-1 overflow-y-auto", compact && "flex flex-col items-center")}>
          {activeProduct ? (
            <>
              <SectionHeading compact={compact}>
                <span className="flex items-center gap-1.5">
                  <span style={{ color: activeProduct.accent }}>{activeProduct.name}</span>
                  {productStatus && <span className={cn("h-1.5 w-1.5 rounded-full", HEALTH_DOT[productStatus.health])} title={productStatus.headline} />}
                </span>
              </SectionHeading>
              <div className={cn("flex flex-col gap-0.5", compact && "items-center")}>
                {activeProduct.panels
                  .filter((item) => item.showInRail !== false)
                  .map((item) => (
                    <NavItem
                      key={item.id}
                      Icon={item.Icon}
                      label={item.label}
                      compact={compact}
                      accent={activeProduct.accent}
                      active={railRoute === item.id}
                      onClick={() => toggleRoute(item.id)}
                    />
                  ))}
              </div>
            </>
          ) : (
            <>
              <SectionHeading compact={compact}>Products</SectionHeading>
              <div className={cn("flex flex-col gap-0.5", compact && "items-center")}>
                {entitledProducts.map((product) => {
                  const status = statuses[product.id];
                  return (
                    <NavItem
                      key={product.id}
                      Icon={product.Icon}
                      label={product.name}
                      compact={compact}
                      accent={product.accent}
                      active={false}
                      onClick={() => switchProduct(product.id)}
                      trailing={status && <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", HEALTH_DOT[status.health])} title={status.headline} />}
                    />
                  );
                })}
              </div>
            </>
          )}
        </div>

        <div className={cn("flex flex-col gap-0.5 border-t border-white/[0.07] pt-2", compact ? "w-10 items-center" : "w-full")}>
          <NavItem Icon={GearSixIcon} label="Settings" compact={compact} active={railRoute === "settings"} onClick={() => toggleRoute("settings")} />
          <NavItem Icon={UserCircleIcon} label="Account" compact={compact} active={railRoute === "account"} onClick={() => toggleRoute("account")} />
          {!open && (
            <NavItem
              Icon={sidebarCollapsed ? CaretDoubleRightIcon : CaretDoubleLeftIcon}
              label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              compact={compact}
              active={false}
              onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            />
          )}
        </div>
      </nav>

      {open && (
        <section className="flex h-full w-[384px] flex-col overflow-hidden border-l border-white/[0.07] bg-[rgba(8,9,10,0.55)]">
          <header className="flex h-12 shrink-0 items-center gap-1.5 border-b border-white/[0.07] px-2">
            {visibleStack.length > 1 ? (
              <button type="button" onClick={popRightDock} title="Back" className="flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:bg-white/[0.07] hover:text-slate-100">
                <CaretLeftIcon size={16} />
              </button>
            ) : (
              <span className="w-1.5" />
            )}
            <h2 className="min-w-0 flex-1 truncate text-[15px] font-semibold text-slate-100">
              {panelOwner && (
                <span className="font-medium" style={{ color: panelOwner.accent }}>
                  {panelOwner.shortName}
                  <span className="mx-1.5 text-slate-600">/</span>
                </span>
              )}
              {parentLabel && (
                <span className="font-normal text-slate-500">
                  {parentLabel}
                  <span className="mx-1.5 text-slate-600">/</span>
                </span>
              )}
              {panel?.label ?? "Panel"}
            </h2>
            <button type="button" onClick={() => setRightDockOpen(false)} title="Close panel" className="flex h-8 w-8 items-center justify-center rounded-md text-slate-400 hover:bg-white/[0.07] hover:text-slate-100">
              <XIcon size={16} />
            </button>
          </header>
          <div className="flex-1 overflow-y-auto px-3.5 py-3.5">
            {PanelComponent ? <PanelComponent ctx={ctx} /> : <EmptyLine text="This panel belongs to a product that is not licensed for your organization." />}
          </div>
        </section>
      )}
    </aside>
  );
}
