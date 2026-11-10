import { CaretRightIcon as ChevronRight, LockSimpleIcon as Lock } from "@phosphor-icons/react";
import { cn } from "../../lib/utils";
import { useEntitlements, useProductStatuses, useSwitchProduct } from "../hooks";
import { PRODUCT_MODULES } from "../registry";
import type { ProductHealth } from "../types";
import { EmptyLine, PanelStack, SectionLabel } from "../ui";

const HEALTH_LABEL: Record<ProductHealth, string> = {
  ready: "Ready",
  attention: "Attention",
  offline: "Offline",
  idle: "Not set up",
};

const HEALTH_CLASS: Record<ProductHealth, string> = {
  ready: "bg-status-ready",
  attention: "bg-status-warning",
  offline: "bg-status-critical",
  idle: "bg-slate-600",
};

const SOURCE_LABEL = {
  account: "Organization license",
  server: "Server license",
  cached: "Cached license (offline)",
  "legacy-default": "Default license",
  "dev-override": "Dev override",
} as const;

/** Cross-product global overview: every product's state plus shared map layers. */
export function OverviewPanel() {
  const { entitled, source } = useEntitlements();
  const statuses = useProductStatuses();
  const switchProduct = useSwitchProduct();

  const available = PRODUCT_MODULES.filter((product) => entitled.has(product.id));
  const locked = PRODUCT_MODULES.filter((product) => !entitled.has(product.id));
  const attention = available.filter((product) => {
    const health = statuses[product.id]?.health;
    return health === "attention" || health === "offline";
  }).length;

  const summary = [
    `${available.length} ${available.length === 1 ? "product" : "products"}`,
    attention ? `${attention} need${attention === 1 ? "s" : ""} attention` : "nothing needs attention",
    SOURCE_LABEL[source].toLowerCase(),
  ].join(" · ");

  return (
    <PanelStack title="Overview" subtitle={summary}>
      <SectionLabel>Your products</SectionLabel>
      {available.length === 0 && <EmptyLine text="No products are licensed for this organization." />}
      <div className="divide-y divide-white/[0.06] rounded-lg bg-white/[0.035]">
        {available.map((product) => {
          const status = statuses[product.id];
          const Icon = product.Icon;
          return (
            <button
              key={product.id}
              type="button"
              onClick={() => switchProduct(product.id)}
              className="group block w-full px-3 py-3 text-left transition-colors first:rounded-t-lg last:rounded-b-lg hover:bg-white/[0.04]"
            >
              <div className="flex items-center gap-2.5">
                <Icon size={20} weight="duotone" className="shrink-0" style={{ color: product.accent }} />
                <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-100">{product.name}</span>
                {status && (
                  <span className="flex shrink-0 items-center gap-1.5 text-xs text-slate-400">
                    <span className={cn("h-1.5 w-1.5 rounded-full", HEALTH_CLASS[status.health])} />
                    {HEALTH_LABEL[status.health]}
                  </span>
                )}
                <ChevronRight size={14} className="shrink-0 text-slate-600 transition-transform group-hover:translate-x-0.5 group-hover:text-slate-300" />
              </div>
              {status && <p className="mt-1 truncate pl-[30px] text-[13px] text-slate-400">{status.headline}</p>}
              {status?.metrics && status.metrics.length > 0 && (
                <p className="mt-0.5 pl-[30px] text-xs text-slate-500">
                  {status.metrics.map((metric, index) => (
                    <span key={metric.label}>
                      {index > 0 && " · "}
                      <span className="tabular-nums text-slate-300">{metric.value}</span> {metric.label.toLowerCase()}
                    </span>
                  ))}
                </p>
              )}
            </button>
          );
        })}
      </div>

      {locked.length > 0 && (
        <>
          <SectionLabel>More from Proxigo</SectionLabel>
          {locked.map((product) => {
            const Icon = product.Icon;
            return (
              <div key={product.id} className="flex items-center gap-2.5 px-3 py-2 text-slate-500">
                <Icon size={18} className="shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-slate-300">{product.name}</span>
                  <span className="block truncate text-xs">{product.tagline}</span>
                </span>
                <Lock size={14} className="shrink-0" />
              </div>
            );
          })}
        </>
      )}
    </PanelStack>
  );
}
