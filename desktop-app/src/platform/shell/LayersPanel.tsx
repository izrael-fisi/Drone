import { EyeIcon, EyeSlashIcon } from "@phosphor-icons/react";
import { cn } from "../../lib/utils";
import { useActiveProduct, usePlatformMapLayers } from "../hooks";
import { usePlatformStore } from "../platformStore";
import { productById } from "../registry";
import { EmptyLine, PanelStack, SectionLabel } from "../ui";

/** Every toggleable map layer, grouped by who publishes it. */
export function LayersPanel() {
  const layers = usePlatformMapLayers().filter((layer) => !layer.activeOnly && !layer.internal);
  const setLayerVisible = usePlatformStore((state) => state.setLayerVisible);
  const activeProduct = useActiveProduct();

  const groups = new Map<string, typeof layers>();
  for (const layer of layers) {
    const key = layer.global ? "Workspace" : productById(layer.productId)?.name ?? layer.productId;
    groups.set(key, [...(groups.get(key) ?? []), layer]);
  }

  return (
    <PanelStack
      title="Layers"
      subtitle={activeProduct ? `Showing ${activeProduct.name} layers and site layouts. The Overview shows all of them.` : "Everything published to the shared map."}
    >
      {layers.length === 0 && <EmptyLine text="No map layers yet." />}
      {[...groups.entries()].map(([group, items]) => (
        <div key={group}>
          <SectionLabel>{group}</SectionLabel>
          <div className="mt-1.5 divide-y divide-white/[0.06] rounded-lg bg-white/[0.035]">
            {items.map((layer) => (
              <button
                key={layer.id}
                type="button"
                aria-pressed={layer.visible}
                onClick={() => setLayerVisible(layer.id, !layer.visible)}
                className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors first:rounded-t-lg last:rounded-b-lg hover:bg-white/[0.04]"
              >
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: layer.color, opacity: layer.visible ? 1 : 0.3 }} />
                <span className={cn("min-w-0 flex-1 truncate text-[13px]", layer.visible ? "text-slate-100" : "text-slate-500")}>{layer.label}</span>
                <span className="shrink-0 text-xs tabular-nums text-slate-500">{layer.features.length}</span>
                {layer.visible ? <EyeIcon size={16} className="shrink-0 text-slate-300" /> : <EyeSlashIcon size={16} className="shrink-0 text-slate-600" />}
              </button>
            ))}
          </div>
        </div>
      ))}
    </PanelStack>
  );
}
