import { Check, CircleHelp, TimerOff, Unplug } from "lucide-react";
import { HEALTH_LABELS, type ApplianceHealth, type HealthStatus } from "./applianceHealth";

export const healthIcons = { online: Check, offline: Unplug, stale: TimerOff, unknown: CircleHelp };

export function healthText(health: ApplianceHealth) {
  return HEALTH_LABELS[health.status] + (health.simulated ? " · Simulated" : "");
}

export function HealthIndicator({ health }: { health: ApplianceHealth }) {
  const Icon = healthIcons[health.status];
  return <span className="gv-health-status" data-health={health.status}><Icon size={12} aria-hidden="true" />{healthText(health)}</span>;
}

export function HealthLegend() {
  return <ul className="gv-health-legend" aria-label="Device health legend">
    {(Object.keys(HEALTH_LABELS) as HealthStatus[]).map((status) => <li key={status}>
      <HealthIndicator health={{ status }} />
    </li>)}
  </ul>;
}
