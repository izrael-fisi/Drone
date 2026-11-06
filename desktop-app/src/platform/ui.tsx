import { ChevronRight, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/utils";

/** Accent for platform-level UI (Overview, Settings, Account), distinct from every product accent. */
export const PLATFORM_ACCENT = "#38BDF8";

export function SidebarRailButton({
  Icon,
  label,
  active,
  onClick,
  accent = PLATFORM_ACCENT,
}: {
  Icon: LucideIcon;
  label: string;
  active: boolean;
  onClick: () => void;
  /** Hex color for the active state; defaults to the platform accent. */
  accent?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-10 w-10 items-center justify-center rounded-md border transition-colors",
        !active && "border-transparent text-slate-500 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-200",
      )}
      style={active ? { borderColor: `${accent}bf`, backgroundColor: `${accent}2e`, color: accent } : undefined}
      title={label}
    >
      <Icon size={17} />
    </button>
  );
}

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
    <div>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-100">{title}</div>
          <div className="truncate text-xs text-slate-500">{subtitle}</div>
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
      <div className="space-y-2">{children}</div>
    </div>
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
        "flex w-full items-center justify-between gap-3 rounded-md border border-border bg-bg-card px-3 py-2 text-left",
        disabled
          ? "cursor-not-allowed opacity-55"
          : "hover:border-cyan-500/45 hover:bg-cyan-500/5",
      )}
    >
      <span className="min-w-0">
        <span className="block truncate text-sm text-slate-100">{label}</span>
        <span className="block truncate text-xs text-slate-500">{detail}</span>
      </span>
      <ChevronRight size={14} className="text-slate-500" />
    </button>
  );
}

export function SettingLine({ label, detail, children }: { label: string; detail: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-bg-card px-3 py-2">
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium text-slate-100">{label}</span>
        <span className="block truncate text-xs text-slate-500">{detail}</span>
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
    <label className={cn("block rounded-md border bg-bg-card px-3 py-2", readOnly ? "border-white/5 opacity-70" : "border-border")}>
      <span className="block text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">{label}</span>
      <input
        type={secret ? "password" : "text"}
        value={value}
        readOnly={readOnly}
        onChange={onChange ? (event) => onChange(event.target.value) : undefined}
        placeholder={placeholder}
        className={cn(
          "mt-1 h-7 w-full bg-transparent text-sm outline-none placeholder:text-slate-700",
          readOnly ? "text-slate-400 cursor-default select-none" : "text-slate-100",
        )}
      />
    </label>
  );
}

export function StatusPill({ tone, children }: { tone: "ready" | "warning" | "muted"; children: ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]",
        tone === "ready" && "border-status-ready/30 bg-status-ready/10 text-status-ready",
        tone === "warning" && "border-status-warning/30 bg-status-warning/10 text-status-warning",
        tone === "muted" && "border-white/10 bg-white/[0.035] text-slate-500",
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
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-bg-card px-3 py-2">
      <span className="text-xs text-slate-500">{label}</span>
      <span className={cn("truncate text-right font-mono text-xs", healthy ? "text-status-ready" : "text-status-warning")}>{value}</span>
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
    <div className="rounded-lg border border-border bg-bg-card p-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">{label}</div>
      <div className={cn("mt-1 truncate font-mono text-sm font-semibold", tone === "green" ? "text-status-ready" : tone === "orange" ? "text-cyan-500" : "text-slate-400")}>{value}</div>
      <div className="mt-0.5 truncate text-xs text-slate-500">{detail}</div>
    </div>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="pt-2 text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-500">{children}</div>;
}

export function EmptyLine({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-border bg-bg-card/60 px-3 py-3 text-center text-xs text-slate-500">{text}</div>;
}

export function MapRow({ name, detail, state, active }: { name: string; detail: string; state: string; active: boolean }) {
  return (
    <div className={cn("rounded-lg border bg-bg-card p-3", active ? "border-cyan-500/45" : "border-border")}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-medium text-slate-100">{name}</span>
        <LifecycleBadge value={active ? "active" : state} />
      </div>
      <div className="mt-1 truncate font-mono text-[11px] text-slate-500">{detail}</div>
    </div>
  );
}

export function LifecycleBadge({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const ready = normalized === "active" || normalized === "uploaded" || normalized === "built" || normalized === "local";
  const failed = normalized === "failed";
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase",
        ready && "border-status-ready/30 bg-status-ready/10 text-status-ready",
        failed && "border-status-critical/30 bg-red-500/10 text-status-critical",
        !ready && !failed && "border-status-warning/30 bg-yellow-500/10 text-status-warning",
      )}
    >
      {value}
    </span>
  );
}
