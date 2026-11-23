import type { Boundary, Sensor, Workspace } from "./monitoringModel.ts";

export const REPORT_INTERVAL = { min: 5, max: 3600, default: 60 };
const MISSED_REPORTS = 3;
const MAX_CLOCK_SKEW_MS = 30_000;
export const HEALTH_LABELS = {
  online: "Online", offline: "Offline", stale: "Stopped reporting", unknown: "No reports",
} as const;
export type HealthStatus = keyof typeof HEALTH_LABELS;
export type HealthReport = { type: "health"; sensorId: string; occurredAt: string; state: "online" | "offline" };
export type ApplianceHealth = { status: HealthStatus; lastReport?: string; staleAt?: number; simulated?: boolean };
export type HealthOutage = { startedAt: string; recoveredAt?: string; cause: "offline" | "stale" };
export type MaintenanceWindow = { startsAt: string; endsAt: string; reason: string };

export function getHealthScope(sensors: Sensor[], boundaries: Boundary[], health: ReadonlyMap<string, ApplianceHealth>, status: HealthStatus) {
  const devices = sensors.filter((sensor) => health.get(sensor.id)?.status === status);
  const ids = new Set(devices.map((sensor) => sensor.id));
  return { devices, boundaries: boundaries.filter((boundary) => boundary.sensorIds.some((id) => ids.has(id))) };
}

const DEMO_HEALTH: Record<string, HealthStatus> = {
  "cam-01": "online", "cam-02": "online", "rf-01": "stale", "gate-01": "offline",
};

export function validReportInterval(seconds: number) {
  return Number.isInteger(seconds) && seconds >= REPORT_INTERVAL.min && seconds <= REPORT_INTERVAL.max;
}

export function getApplianceHealth(appliance: Pick<Sensor, "id" | "demo" | "healthReport" | "reportIntervalSeconds" | "healthOutages">, now: number): ApplianceHealth {
  const report = appliance.healthReport;
  if (!report) {
    const status = appliance.demo ? DEMO_HEALTH[appliance.id] ?? "unknown" : "unknown";
    return { status, simulated: appliance.demo };
  }
  if (report.state === "offline") return { status: "offline", lastReport: report.occurredAt };
  const outage = appliance.healthOutages?.[0];
  if (outage && !outage.recoveredAt) return { status: "stale", lastReport: report.occurredAt };
  const interval = appliance.reportIntervalSeconds ?? REPORT_INTERVAL.default;
  const staleAt = Date.parse(report.occurredAt) + interval * MISSED_REPORTS * 1000;
  return { status: now >= staleAt ? "stale" : "online", lastReport: report.occurredAt, staleAt };
}

export function getOutageHistory(sensor: Sensor, now: number): HealthOutage[] {
  const history = sensor.healthOutages ?? [];
  if (!sensor.healthReport || (history[0] && !history[0].recoveredAt)) return history;
  const health = getApplianceHealth(sensor, now);
  if (health.status === "offline") return [{ startedAt: sensor.healthReport.occurredAt, cause: "offline" }, ...history];
  if (health.status === "stale" && health.staleAt !== undefined) {
    return [{ startedAt: new Date(health.staleAt).toISOString(), cause: "stale" }, ...history];
  }
  return history;
}

export function parseHealthReport(value: unknown, now: number): HealthReport {
  const fields = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const { type, sensorId, occurredAt, state } = fields;
  if (type !== "health" || typeof sensorId !== "string" || !sensorId.trim() || sensorId.length > 128
    || typeof occurredAt !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/.test(occurredAt)
    || !Number.isFinite(Date.parse(occurredAt)) || (state !== "online" && state !== "offline")) {
    throw new Error("A health report needs type health, a device ID, an ISO timestamp with timezone, and an online or offline state.");
  }
  if (Date.parse(occurredAt) > now + MAX_CLOCK_SKEW_MS) throw new Error("The health report timestamp is in the future.");
  return { type, sensorId, occurredAt: new Date(occurredAt).toISOString(), state };
}

export function isNewerReport(report: HealthReport, previous?: HealthReport) {
  return !previous || Date.parse(report.occurredAt) > Date.parse(previous.occurredAt);
}

export function recordHealthReport(workspace: Workspace, report: HealthReport): Workspace {
  const sensor = workspace.sensors.find((device) => device.id === report.sensorId);
  if (!sensor) throw new Error("Unknown device ID: " + report.sensorId);
  if (!isNewerReport(report, sensor.healthReport)) return workspace;
  const observedAt = Date.parse(report.occurredAt);
  let history = getOutageHistory(sensor, observedAt);
  const open = history[0] && !history[0].recoveredAt ? history[0] : undefined;
  if (report.state === "online" && open) {
    history = observedAt > Date.parse(open.startedAt)
      ? [{ ...open, recoveredAt: report.occurredAt }, ...history.slice(1)] : history.slice(1);
  } else if (report.state === "offline" && (!open || observedAt <= Date.parse(open.startedAt))) {
    history = [{ startedAt: report.occurredAt, cause: "offline" }, ...(open ? history.slice(1) : history)];
  }
  return { ...workspace, sensors: workspace.sensors.map((device) => device === sensor
    ? { ...device, healthReport: report, healthOutages: history.length ? history.slice(0, 50) : undefined }
    : device) };
}

export function activeMaintenance(sensor: Sensor, now: number) {
  return sensor.maintenanceWindows?.find((window) => Date.parse(window.startsAt) <= now && now < Date.parse(window.endsAt));
}

export function scheduleMaintenance(workspace: Workspace, sensorId: string, starts: number, ends: number, description: string, now: number): Workspace {
  const sensor = workspace.sensors.find((device) => device.id === sensorId);
  if (!sensor) throw new Error("Choose a registered device.");
  const reason = description.trim();
  const start = Math.max(starts, now);
  if (!Number.isFinite(new Date(starts).getTime()) || !Number.isFinite(new Date(ends).getTime())
    || starts < Math.floor(now / 60_000) * 60_000 || ends <= start) {
    throw new Error("Choose a start at or after the current minute and an end after the start.");
  }
  if (!reason || reason.length > 200) throw new Error("Enter a maintenance reason up to 200 characters.");
  const windows = sensor.maintenanceWindows ?? [];
  if (windows.some((window) => start < Date.parse(window.endsAt) && Date.parse(window.startsAt) < ends)) {
    throw new Error("This device already has maintenance during part of that window.");
  }
  const next = { startsAt: new Date(start).toISOString(), endsAt: new Date(ends).toISOString(), reason };
  return { ...workspace, sensors: workspace.sensors.map((device) => device === sensor
    ? { ...device, maintenanceWindows: [...windows, next] }
    : device) };
}

export function stopMaintenance(workspace: Workspace, sensorId: string, startsAt: string, now: number): Workspace {
  const sensor = workspace.sensors.find((device) => device.id === sensorId);
  const target = sensor?.maintenanceWindows?.find((window) => window.startsAt === startsAt);
  if (!sensor || !target || Date.parse(target.endsAt) <= now) return workspace;
  const maintenanceWindows = sensor.maintenanceWindows?.flatMap((window) => {
    if (window !== target) return [window];
    return Date.parse(window.startsAt) < now ? [{ ...window, endsAt: new Date(now).toISOString() }] : [];
  });
  return { ...workspace, sensors: workspace.sensors.map((device) => device === sensor ? { ...device, maintenanceWindows } : device) };
}

export async function fetchHealthReport(url: string, sensorId: string, signal: AbortSignal): Promise<HealthReport> {
  const request = new AbortController();
  const abort = () => request.abort();
  if (signal.aborted) abort();
  signal.addEventListener("abort", abort, { once: true });
  const timeout = setTimeout(abort, 8000);
  try {
    const response = await fetch(url, { signal: request.signal, cache: "no-store", credentials: "omit", redirect: "error" });
    if (!response.ok) throw new Error(`Health source returned HTTP ${response.status}.`);
    const text = await response.text();
    if (text.length > 32_768) throw new Error("The health response is too large.");
    let body: unknown;
    try { body = JSON.parse(text); }
    catch { throw new Error("The health source did not return valid JSON."); }
    const report = parseHealthReport(body, Date.now());
    if (report.sensorId !== sensorId) throw new Error("The source reported a different device ID.");
    return report;
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener("abort", abort);
  }
}
