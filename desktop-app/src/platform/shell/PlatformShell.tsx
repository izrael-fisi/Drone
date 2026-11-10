import { MinusIcon as Minus, SquareIcon as Square, XIcon as X } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { DroneLogo } from "../../App";
import { useShellStore } from "../../lib/shellStore";
import { cn } from "../../lib/utils";
import { useActiveProduct, useEntitlements, usePlatformContext } from "../hooks";
import { usePlatformStore } from "../platformStore";
import { PRODUCT_MODULES, productForBottomTab, productForPanel } from "../registry";
import { CommandPalette, SpotlightSearchButton } from "./CommandPalette";
import { DOCK_WIDTH_CLASS, dockMode } from "./layout";
import { PlatformDock } from "./PlatformDock";
import { ProductSwitcher } from "./ProductSwitcher";
import { PLATFORM_ACCENT } from "../ui";
import { runWindowAction } from "./windowControls";

/**
 * Proxigo platform shell: title bar with product switcher, the shared global
 * map (routed page), the left dock, the active product's bottom dock and the
 * global command palette.
 */
export function PlatformShell() {
  const {
    rightDockOpen,
    rightDockStack,
    bottomDockOpen,
    bottomDockTab,
    resetRightDock,
    setBottomDockOpen,
    setBottomDockTab,
  } = useShellStore();
  const navigate = useNavigate();
  const ctx = usePlatformContext();
  const sidebarCollapsed = usePlatformStore((state) => state.sidebarCollapsed);
  const activeProduct = useActiveProduct();
  const { entitled } = useEntitlements();
  const setActiveProduct = usePlatformStore((state) => state.setActiveProduct);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);

  // Background work (e.g. VPS telemetry) runs whatever product is in view.
  for (const product of PRODUCT_MODULES) product.useBackground?.(entitled.has(product.id));

  const ProductBottomDock = activeProduct?.bottomDock?.Component;
  const bottomChromeOffset = ProductBottomDock ? (bottomDockOpen ? 292 : 68) : 16;

  // Panels or bottom tabs opened directly (legacy routes, map tools) bring
  // their owning product to the front. Only react to actual changes so an
  // operator who switched to another product is not pulled back.
  const topPanel = rightDockStack[rightDockStack.length - 1];
  const lastTopPanel = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (topPanel === lastTopPanel.current) return;
    lastTopPanel.current = topPanel;
    const owner = productForPanel(topPanel);
    if (owner && entitled.has(owner.id)) setActiveProduct(owner.id);
  }, [entitled, setActiveProduct, topPanel]);

  const openBottomTab = bottomDockOpen ? bottomDockTab : null;
  const lastBottomTab = useRef<string | null>(null);
  useEffect(() => {
    if (openBottomTab === lastBottomTab.current) return;
    lastBottomTab.current = openBottomTab;
    const owner = openBottomTab ? productForBottomTab(openBottomTab) : undefined;
    if (owner && entitled.has(owner.id)) setActiveProduct(owner.id);
  }, [entitled, openBottomTab, setActiveProduct]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandPaletteOpen(true);
        return;
      }
      if (event.key === "Escape") {
        if (commandPaletteOpen) {
          setCommandPaletteOpen(false);
          return;
        }
        resetRightDock();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [commandPaletteOpen, resetRightDock]);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg-base text-slate-200">
      <header
        className="relative flex h-9 shrink-0 items-center justify-between border-b border-border bg-bg-base pl-2"
        data-tauri-drag-region
      >
        <div className="flex h-full min-w-0 items-center" data-tauri-drag-region="false">
          <button
            type="button"
            onClick={() => {
              navigate("/home");
              resetRightDock();
            }}
            className="flex h-full min-w-0 items-center gap-2 px-2"
          >
            <DroneLogo size={18} />
            <span className="text-[13px] font-semibold tracking-[-0.01em] text-white/90">Proxigo</span>
          </button>
          <span className="mx-1 h-4 w-px bg-white/10" />
          <ProductSwitcher />
        </div>
        <div className="flex h-full items-center" data-tauri-drag-region="false">
          <button type="button" className="h-9 w-[46px] operator-shell-button" onClick={() => runWindowAction("minimize")} title="Minimize">
            <Minus size={13} />
          </button>
          <button type="button" className="h-9 w-[46px] operator-shell-button" onClick={() => runWindowAction("toggleMaximize")} title="Maximize">
            <Square size={12} />
          </button>
          <button type="button" className="h-9 w-[46px] operator-shell-button hover:bg-red-700 hover:text-white" onClick={() => runWindowAction("close")} title="Close">
            <X size={14} />
          </button>
        </div>
      </header>
      <div
        className="h-0.5 shrink-0 transition-colors duration-300"
        style={{ backgroundColor: activeProduct?.accent ?? PLATFORM_ACCENT }}
        aria-hidden
      />

      <section className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="relative min-h-0 flex-1 overflow-hidden bg-bg-base">
          <main className="absolute inset-0 overflow-hidden">
            <div className="h-full overflow-auto">
              <Outlet />
            </div>
          </main>

          {(
            <div
              className={cn("absolute left-3 z-[1300] transition-[width] duration-200", DOCK_WIDTH_CLASS[dockMode(rightDockOpen, sidebarCollapsed)])}
              style={{ top: 16, bottom: bottomChromeOffset }}
            >
              <PlatformDock ctx={ctx} />
            </div>
          )}

          {ProductBottomDock && (
            <ProductBottomDock
              open={bottomDockOpen}
              tab={bottomDockTab}
              onOpenChange={setBottomDockOpen}
              onTabChange={setBottomDockTab}
            />
          )}

          {(
            <SpotlightSearchButton
              open={commandPaletteOpen}
              bottomOffset={bottomChromeOffset}
              onOpen={() => setCommandPaletteOpen(true)}
            />
          )}

          <CommandPalette
            open={commandPaletteOpen}
            bottomOffset={bottomChromeOffset}
            ctx={ctx}
            onClose={() => setCommandPaletteOpen(false)}
          />
        </div>
      </section>
    </div>
  );
}
