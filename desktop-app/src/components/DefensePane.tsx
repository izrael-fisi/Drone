import type { ReactNode } from "react";
import { cn } from "../lib/utils";

type Tone = "active" | "ready" | "warning" | "critical" | "offline";

const TONE_TEXT: Record<Tone, string> = {
  active: "text-status-active",
  ready: "text-status-ready",
  warning: "text-status-warning",
  critical: "text-status-critical",
  offline: "text-slate-500",
};

const TONE_BG: Record<Tone, string> = {
  active: "bg-status-active",
  ready: "bg-status-ready",
  warning: "bg-status-warning",
  critical: "bg-status-critical",
  offline: "bg-slate-500",
};

export function DefensePane({
  children,
  right,
}: {
  children: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="ops-screen-bg relative flex h-full min-h-[calc(100vh-96px)] overflow-hidden animate-fade-in">
      <div
        className="pointer-events-none absolute inset-0 z-0 opacity-[0.04]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(104, 199, 230, 0.28) 1px, transparent 1px), linear-gradient(90deg, rgba(104, 199, 230, 0.2) 1px, transparent 1px)",
          backgroundSize: "56px 56px",
        }}
      />
      <main className="z-10 flex min-w-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
        {children}
      </main>
      {right && (
        <aside className="panel-3d-right z-10 flex h-full w-[320px] shrink-0 flex-col border-l border-border bg-bg-surface/95">
          {right}
        </aside>
      )}
    </div>
  );
}

export function DefenseHeader({
  eyebrow,
  title,
  subtitle,
  action,
}: {
  eyebrow: string;
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-1 flex items-end justify-between border-b border-border pb-3">
      <div>
        <div className="font-data-mono text-[10px] uppercase tracking-[0.18em] text-slate-500">{eyebrow}</div>
        <h1 className="font-headline-lg text-headline-lg font-semibold tracking-tight text-slate-100">
          {title}
        </h1>
        {subtitle && <p className="font-data-mono text-data-mono text-slate-500">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function DefenseSection({
  title,
  icon,
  children,
  className,
}: {
  title: string;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("glass-panel rounded-none p-3", className)}>
      <h2 className="font-label-caps text-label-caps mb-3 flex items-center gap-2 border-b border-border pb-2 text-slate-300">
        {icon}
        {title}
      </h2>
      {children}
    </section>
  );
}

export function DefenseMetric({
  label,
  value,
  detail,
  tone = "active",
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className="border border-border bg-bg-surface p-3 shadow-depth-inset">
      <div className="mb-2 flex items-center gap-2">
        <span className={cn("h-2 w-1.5", TONE_BG[tone], TONE_TEXT[tone])} />
        <div className="font-label-caps text-label-caps text-slate-500">{label}</div>
      </div>
      <div className={cn("font-data-mono text-lg font-bold leading-tight", TONE_TEXT[tone])}>{value}</div>
      {detail && <div className="mt-1 truncate font-data-mono text-[10px] text-slate-500">{detail}</div>}
    </div>
  );
}

export function DefenseListItem({
  label,
  detail,
  tone = "active",
  action,
}: {
  label: ReactNode;
  detail?: ReactNode;
  tone?: Tone;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 border border-border bg-bg-card px-3 py-2">
      <span className={cn("h-3 w-1.5", TONE_BG[tone])} />
      <div className="min-w-0 flex-1">
        <div className="truncate font-data-mono text-xs text-slate-200">{label}</div>
        {detail && <div className="truncate font-data-mono text-[10px] text-slate-500">{detail}</div>}
      </div>
      {action}
    </div>
  );
}

export function DefenseRightPanel({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <>
      <div className="border-b border-border bg-bg-card p-4">
        <h3 className="font-label-caps text-label-caps text-slate-300">{title}</h3>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-4">{children}</div>
    </>
  );
}
