import { ChevronLeft, Globe2, Settings as SettingsIcon, UserRound } from "lucide-react";
import { useShellStore } from "../../lib/shellStore";
import { useActiveProduct, useEntitledProducts, useSwitchProduct } from "../hooks";
import { findPanel } from "../platformPanels";
import { OVERVIEW_ID } from "../platformStore";
import { productForPanel } from "../registry";
import type { PlatformContext } from "../types";
import { EmptyLine, SidebarRailButton } from "../ui";

/**
 * Left dock: a rail plus a stacked panel area. The overview rail lists the
 * organization's products; inside a product the rail shows that product's
 * panels. Settings and Account are always available at the bottom.
 */
export function PlatformDock({ ctx }: { ctx: PlatformContext }) {
  const { rightDockOpen: open, rightDockStack: stack, setRightDockOpen, popRightDock } = useShellStore();
  const activeProduct = useActiveProduct();
  const entitledProducts = useEntitledProducts();
  const switchProduct = useSwitchProduct();

  const visibleStack = stack.filter((item) => item !== "root");
  const route = visibleStack[visibleStack.length - 1] ?? activeProduct?.defaultPanelId ?? OVERVIEW_ID;
  const panel = findPanel(route, entitledProducts);
  const railRoute = panel?.railParent ?? route;
  const panelOwner = productForPanel(route);

  const toggleRoute = (panelId: string) => {
    if (open && railRoute === panelId) {
      setRightDockOpen(false);
      return;
    }
    ctx.openPanel(panelId);
  };

  const PanelComponent = panel?.Component;

  return (
    <aside
      className="flex h-full overflow-hidden rounded-lg border bg-bg-base/60 shadow-2xl ring-1 ring-white/5 backdrop-blur-xl"
      style={{ borderColor: activeProduct ? `${activeProduct.accent}59` : "rgba(255,255,255,0.1)" }}
    >
      <div className="flex h-full w-14 shrink-0 flex-col items-center bg-white/[0.035] py-2">
        <div className="flex flex-col gap-1">
          <SidebarRailButton Icon={Globe2} label="Overview" active={open && railRoute === OVERVIEW_ID} onClick={() => toggleRoute(OVERVIEW_ID)} />
        </div>
        <div className="my-2 h-px w-8 bg-white/10" />
        {activeProduct && (
          <div
            className="mb-2 flex w-10 flex-col items-center gap-0.5 rounded-md border py-1.5"
            style={{ borderColor: `${activeProduct.accent}66`, backgroundColor: `${activeProduct.accent}1a`, color: activeProduct.accent }}
            title={activeProduct.name}
          >
            <activeProduct.Icon size={15} />
            <span className="max-w-full truncate px-0.5 text-[8px] font-bold uppercase tracking-[0.06em]">{activeProduct.shortName}</span>
          </div>
        )}
        <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
          {activeProduct
            ? activeProduct.panels
                .filter((item) => item.showInRail !== false)
                .map((item) => (
                  <SidebarRailButton
                    key={item.id}
                    Icon={item.Icon}
                    label={item.label}
                    active={open && railRoute === item.id}
                    accent={activeProduct.accent}
                    onClick={() => toggleRoute(item.id)}
                  />
                ))
            : entitledProducts.map((product) => (
                <SidebarRailButton
                  key={product.id}
                  Icon={product.Icon}
                  label={`Open ${product.name}`}
                  active={false}
                  onClick={() => switchProduct(product.id)}
                />
              ))}
        </div>
        <div className="mt-2 flex flex-col gap-1 border-t border-white/10 pt-2">
          <SidebarRailButton Icon={SettingsIcon} label="Settings" active={open && railRoute === "settings"} onClick={() => toggleRoute("settings")} />
          <SidebarRailButton Icon={UserRound} label="Account" active={open && railRoute === "account"} onClick={() => toggleRoute("account")} />
        </div>
      </div>

      {open && (
        <section className="flex h-full w-[360px] flex-col overflow-hidden border-l border-white/10 bg-black/90">
          <div className="flex h-11 shrink-0 items-center gap-2 border-b border-white/10 px-3">
            {visibleStack.length > 1 && (
              <button type="button" onClick={popRightDock} className="operator-shell-button h-7 w-7 rounded-md">
                <ChevronLeft size={15} />
              </button>
            )}
            {panelOwner && (
              <span
                className="flex h-6 shrink-0 items-center gap-1 rounded border px-1.5 text-[10px] font-semibold uppercase tracking-[0.06em]"
                style={{ borderColor: `${panelOwner.accent}59`, color: panelOwner.accent }}
              >
                <panelOwner.Icon size={11} />
                {panelOwner.shortName}
              </span>
            )}
            <div className="min-w-0 flex-1 truncate text-left text-sm font-semibold text-slate-100">
              {visibleStack.map((item, index) => (
                <span key={`${item}-${index}`}>
                  {index > 0 && <span className="mx-1 text-slate-700">/</span>}
                  <span className={index === visibleStack.length - 1 ? "text-slate-100" : "text-slate-500"}>
                    {findPanel(item, entitledProducts)?.label ?? item}
                  </span>
                </span>
              ))}
              {visibleStack.length === 0 && (panel?.label ?? "Panel")}
            </div>
            <button type="button" onClick={() => setRightDockOpen(false)} className="operator-shell-button h-7 w-7 rounded-md" title="Collapse pane">
              <ChevronLeft size={15} />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-3">
            {PanelComponent ? (
              <PanelComponent ctx={ctx} />
            ) : (
              <EmptyLine text="This panel belongs to a product that is not licensed for your organization." />
            )}
          </div>
        </section>
      )}
    </aside>
  );
}
