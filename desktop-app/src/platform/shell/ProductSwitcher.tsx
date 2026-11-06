import { Check, ChevronDown, Globe2, Lock } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "../../lib/utils";
import { useActiveProduct, useEntitlements, useSwitchProduct } from "../hooks";
import { OVERVIEW_ID } from "../platformStore";
import { PRODUCT_MODULES } from "../registry";
import { PRODUCT_CATEGORY_LABELS } from "../types";
import { PLATFORM_ACCENT } from "../ui";

/** Title-bar product picker: the global overview plus each Proxigo product. */
export function ProductSwitcher() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const activeProduct = useActiveProduct();
  const { entitled } = useEntitlements();
  const switchProduct = useSwitchProduct();

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const choose = (id: string) => {
    setOpen(false);
    switchProduct(id);
  };

  const CurrentIcon = activeProduct?.Icon ?? Globe2;
  const accent = activeProduct?.accent ?? PLATFORM_ACCENT;

  return (
    <div ref={rootRef} className="relative h-full" data-tauri-drag-region="false">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="my-1 flex h-[calc(100%-8px)] items-center gap-2 rounded-md border px-2.5 text-[11px] font-semibold text-slate-100 transition-colors hover:brightness-125"
        style={{ borderColor: `${accent}59`, backgroundColor: `${accent}1f` }}
        title="Switch product"
      >
        <CurrentIcon size={13} style={{ color: accent }} />
        <span>{activeProduct?.name ?? "Global Overview"}</span>
        <ChevronDown size={12} className="text-slate-500" />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-[3000] mt-1 w-72 overflow-hidden rounded-lg border border-white/15 bg-bg-base/95 p-1 shadow-[0_24px_90px_rgba(0,0,0,0.62)] backdrop-blur-2xl">
          <SwitcherRow
            label="Overview"
            detail="All products, status and shared map layers"
            Icon={Globe2}
            accent={PLATFORM_ACCENT}
            selected={!activeProduct}
            onClick={() => choose(OVERVIEW_ID)}
          />
          <div className="my-1 h-px bg-white/10" />
          {PRODUCT_MODULES.map((product) => {
            const licensed = entitled.has(product.id);
            return (
              <SwitcherRow
                key={product.id}
                label={product.name}
                detail={licensed ? PRODUCT_CATEGORY_LABELS[product.category] : "Not licensed for this organization"}
                Icon={product.Icon}
                accent={product.accent}
                selected={activeProduct?.id === product.id}
                locked={!licensed}
                onClick={() => choose(product.id)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

function SwitcherRow({
  label,
  detail,
  Icon,
  accent,
  selected,
  locked,
  onClick,
}: {
  label: string;
  detail: string;
  Icon: typeof Globe2;
  accent: string;
  selected: boolean;
  locked?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={locked}
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors",
        locked ? "cursor-not-allowed opacity-45" : "hover:bg-white/[0.06]",
        selected && "bg-white/[0.05]",
      )}
    >
      <Icon size={15} className="shrink-0" style={{ color: accent }} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-slate-100">{label}</span>
        <span className="block truncate text-[11px] text-slate-500">{detail}</span>
      </span>
      {locked ? <Lock size={12} className="text-slate-600" /> : selected ? <Check size={13} className="text-slate-300" /> : null}
    </button>
  );
}
