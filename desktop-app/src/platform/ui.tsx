import { CaretRightIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { cn } from "../lib/utils";

/** Accent for platform-level UI (Overview, Settings, Account), distinct from every product accent. */
export const PLATFORM_ACCENT = "#38BDF8";

/*
 * Panel primitives. Rows are quiet filled bands, not a bordered card per row:
 * hierarchy comes from type weight and spacing, and the dock's glass carries
 * the surface. Labels are sentence case; values use tabular figures.
 */

const ROW = "rounded-md bg-white/[0.035]";

/** Panel body. The dock header already names the panel, so `title` is only the accessible name. */
export function PanelStack({
  title,
  subtitle,
  children,
  action,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section aria-label={title}>
      {(subtitle || action) && (
        <div className="mb-3 flex min-h-8 items-center justify-between gap-3">
          <p className="min-w-0 text-[13px] leading-snug text-slate-400 [text-wrap:pretty]">{subtitle}</p>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      )}
      <div className="space-y-1.5">{children}</div>
    </section>
  );
}

export function PanelAction({
  label,
  detail,
  onClick,
  disabled = false,
}: {
  label: string;
  detail: string;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        ROW,
        "group flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left transition-colors",
        disabled ? "cursor-not-allowed opacity-50" : "hover:bg-white/[0.07]",
      )}
    >
      <span className="min-w-0">
        <span className="block truncate text-[13.5px] font-medium text-slate-100">{label}</span>
        <span className="block truncate text-xs text-slate-400">{detail}</span>
      </span>
      <CaretRightIcon size={14} className="shrink-0 text-slate-500 transition-transform group-hover:translate-x-0.5 group-hover:text-slate-300" />
    </button>
  );
}

export function SettingLine({ label, detail, children }: { label: string; detail: string; children: ReactNode }) {
  return (
    <div className={cn(ROW, "flex items-center justify-between gap-3 px-3 py-2.5")}>
      <span className="min-w-0">
        <span className="block truncate text-[13.5px] font-medium text-slate-100">{label}</span>
        <span className="block truncate text-xs text-slate-400">{detail}</span>
      </span>
      <span className="shrink-0">{children}</span>
    </div>
  );
}

export function CompactField({
  label,
  value,
  onChange,
  placeholder,
  secret = false,
}: {
  label: string;
  value: string;
  onChange?: (value: string) => void;
  placeholder: string;
  secret?: boolean;
}) {
  const readOnly = !onChange;
  return (
    <label className={cn(ROW, "block px-3 py-2 focus-within:bg-white/[0.06]", readOnly && "opacity-70")}>
      <span className="block text-xs font-medium text-slate-400">{label}</span>
      <input
        type={secret ? "password" : "text"}
        value={value}
        readOnly={readOnly}
        onChange={onChange ? (event) => onChange(event.target.value) : undefined}
        placeholder={placeholder}
        className={cn(
          "mt-0.5 h-7 w-full bg-transparent text-sm outline-none placeholder:text-slate-600",
          readOnly ? "cursor-default select-none text-slate-400" : "text-slate-100",
        )}
      />
    </label>
  );
}

export function StatusPill({ tone, children }: { tone: "ready" | "warning" | "muted"; children: ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[11px] font-medium capitalize",
        tone === "ready" && "bg-status-ready/15 text-status-ready",
        tone === "warning" && "bg-status-warning/15 text-status-warning",
        tone === "muted" && "bg-white/[0.06] text-slate-400",
      )}
    >
      {children}
    </span>
  );
}

export function displayLabel(display?: { available: boolean; display?: string | null; wayland_display?: string | null }) {
  if (!display) return "not checked";
  if (!display.available) return "no display";
  return display.wayland_display || display.display || "available";
}

export function StatusRow({ label, value, healthy }: { label: string; value: string; healthy: boolean }) {
  return (
    <div className={cn(ROW, "flex items-center justify-between gap-3 px-3 py-2")}>
      <span className="text-[13px] text-slate-400">{label}</span>
      <span className={cn("truncate text-right text-[13px] font-medium tabular-nums", healthy ? "text-status-ready" : "text-status-warning")}>{value}</span>
    </div>
  );
}

export function StateCard({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string | number;
  detail: string;
  tone: "orange" | "green" | "muted";
}) {
  return (
    <div className={cn(ROW, "px-3 py-2.5")}>
      <div className="text-xs text-slate-400">{label}</div>
      <div
        className={cn(
          "mt-0.5 truncate text-lg font-semibold leading-tight tabular-nums",
          tone === "green" ? "text-status-ready" : tone === "orange" ? "text-[#FF8A3D]" : "text-slate-300",
        )}
      >
        {value}
      </div>
      <div className="mt-0.5 truncate text-xs text-slate-500">{detail}</div>
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <h3 className="pb-0.5 pt-4 text-xs font-semibold text-slate-300 first:pt-0">{children}</h3>;
}

export function EmptyLine({ text }: { text: string }) {
  return <p className="px-1 py-2 text-[13px] leading-snug text-slate-500 [text-wrap:pretty]">{text}</p>;
}

export function MapRow({ name, detail, state, active }: { name: string; detail: string; state: string; active: boolean }) {
  return (
    <div className={cn(ROW, "px-3 py-2.5", active && "bg-[#FF6600]/[0.09]")}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[13.5px] font-medium text-slate-100">{name}</span>
        <LifecycleBadge value={active ? "active" : state} />
      </div>
      <div className="mt-0.5 truncate font-mono text-[11px] text-slate-500">{detail}</div>
    </div>
  );
}

function LifecycleBadge({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const ready = normalized === "active" || normalized === "uploaded" || normalized === "built" || normalized === "local";
  const failed = normalized === "failed";
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[11px] font-medium capitalize",
        ready && "bg-status-ready/15 text-status-ready",
        failed && "bg-red-500/15 text-status-critical",
        !ready && !failed && "bg-yellow-500/15 text-status-warning",
      )}
    >
      {value}
    </span>
  );
}
