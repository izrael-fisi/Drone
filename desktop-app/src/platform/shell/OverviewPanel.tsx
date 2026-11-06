import { ChevronRight, Eye, EyeOff, Lock } from "lucide-react";
import { cn } from "../../lib/utils";
import { useEntitlements, usePlatformMapLayers, useProductStatuses, useSwitchProduct } from "../hooks";
import { usePlatformStore } from "../platformStore";
import { PRODUCT_MODULES } from "../registry";
import { PRODUCT_CATEGORY_LABELS, type ProductHealth } from "../types";
import { EmptyLine, PanelStack, SectionLabel, StateCard } from "../ui";

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
  const layers = usePlatformMapLayers().filter((layer) => !layer.activeOnly);
  const setLayerVisible = usePlatformStore((state) => state.setLayerVisible);

  const available = PRODUCT_MODULES.filter((product) => entitled.has(product.id));
  const locked = PRODUCT_MODULES.filter((product) => !entitled.has(product.id));
  const attention = available.filter((product) => {
    const health = statuses[product.id]?.health;
    return health === "attention" || health === "offline";
  }).length;

  return (
    <PanelStack title="Overview" subtitle="All products and integrations">
      <div className="grid grid-cols-2 gap-2">
        <StateCard label="Products" value={available.length} detail={SOURCE_LABEL[source]} tone={available.length ? "orange" : "muted"} />
        <StateCard label="Attention" value={attention} detail="need operator action" tone={attention ? "orange" : "green"} />
      </div>

      <SectionLabel>Your Products</SectionLabel>
      {available.length === 0 && <EmptyLine text="No products are licensed for this organization." />}
      {available.map((product) => {
        const status = statuses[product.id];
        const Icon = product.Icon;
        return (
          <button
            key={product.id}
            type="button"
            onClick={() => switchProduct(product.id)}
            className="group w-full rounded-md border border-white/10 bg-white/[0.03] p-3 text-left transition-colors hover:border-white/20 hover:bg-white/[0.06]"
          >
            <div className="flex items-center gap-3">
              <span
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border"
                style={{ borderColor: `${product.accent}66`, backgroundColor: `${product.accent}1f`, color: product.accent }}
              >
                <Icon size={16} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold text-slate-100">{product.name}</span>
                <span className="block truncate text-[11px] text-slate-500">{PRODUCT_CATEGORY_LABELS[product.category]}</span>
              </span>
              {status && (
                <span className="flex shrink-0 items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-slate-400">
                  <span className={cn("h-1.5 w-1.5 rounded-full", HEALTH_CLASS[status.health])} />
                  {HEALTH_LABEL[status.health]}
                </span>
              )}
              <ChevronRight size={14} className="shrink-0 text-slate-600 group-hover:text-slate-300" />
            </div>
            {status && <div className="mt-2 truncate text-xs text-slate-400">{status.headline}</div>}
            {status?.metrics && status.metrics.length > 0 && (
              <div className="mt-2 flex gap-4">
                {status.metrics.map((metric) => (
                  <span key={metric.label} className="text-[11px] text-slate-500">
                    <span className="font-data-mono text-slate-200">{metric.value}</span> {metric.label}
                  </span>
                ))}
              </div>
            )}
          </button>
        );
      })}

      <SectionLabel>Map Layers</SectionLabel>
      {layers.length === 0 && <EmptyLine text="Products have not published map layers." />}
      {layers.map((layer) => (
        <button
          key={layer.id}
          type="button"
          onClick={() => setLayerVisible(layer.id, !layer.visible)}
          className="flex w-full items-center gap-3 rounded-md border border-white/10 px-3 py-2 text-left transition-colors hover:bg-white/[0.04]"
        >
          <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: layer.color, opacity: layer.visible ? 1 : 0.3 }} />
          <span className="min-w-0 flex-1">
            <span className={cn("block truncate text-sm", layer.visible ? "text-slate-200" : "text-slate-500")}>{layer.label}</span>
            <span className="block truncate text-[11px] text-slate-600">{layer.features.length} features</span>
          </span>
          {layer.visible ? <Eye size={14} className="text-slate-400" /> : <EyeOff size={14} className="text-slate-600" />}
        </button>
      ))}

      {locked.length > 0 && (
        <>
          <SectionLabel>More From Proxigo</SectionLabel>
          {locked.map((product) => {
            const Icon = product.Icon;
            return (
              <div key={product.id} className="flex items-center gap-3 rounded-md border border-dashed border-white/10 px-3 py-2 opacity-70">
                <Icon size={15} className="shrink-0 text-slate-500" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-slate-300">{product.name}</span>
                  <span className="block truncate text-[11px] text-slate-600">{product.tagline}</span>
                </span>
                <Lock size={13} className="shrink-0 text-slate-600" />
              </div>
            );
          })}
        </>
      )}
    </PanelStack>
  );
}
