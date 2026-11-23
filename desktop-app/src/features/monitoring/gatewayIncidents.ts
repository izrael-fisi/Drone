import { getApplianceHealth, getOutageHistory } from "./applianceHealth.ts";
import type { Boundary, Sensor, Workspace } from "./monitoringModel.ts";

export type GatewayMember = {
  sensorId: string;
  name: string;
  affectedAt: string;
  recoveredAt?: string;
  boundaries: Array<{ id: string; name: string }>;
};
export type GatewayIncident = {
  id: string;
  gatewayId: string;
  startedAt: string;
  recoveredAt?: string;
  devices: GatewayMember[];
};

const CORRELATION_MS = 120_000;
const RECOVERED_LIMIT = 50;
export const NO_GATEWAY_INCIDENTS: GatewayIncident[] = [];
export const RECOVERED_BOUNDARY_COLOR = "#9cb7a4";

export function normalizeGatewayId(value: string) {
  const id = value.trim().toLowerCase();
  return /^[a-z0-9][a-z0-9._-]{0,63}$/.test(id) ? id : null;
}

export function gatewayBoundaries(incident: GatewayIncident) {
  const results = new Map<string, { id: string; name: string; affected: number }>();
  for (const device of incident.devices) {
    for (const boundary of device.boundaries) {
      const entry = results.get(boundary.id) ?? { ...boundary, affected: 0 };
      if (!device.recoveredAt) entry.affected++;
      results.set(boundary.id, entry);
    }
  }
  return [...results.values()];
}

function captureMember(sensor: Sensor, affectedAt: string, boundaries: Boundary[]): GatewayMember {
  return {
    sensorId: sensor.id, name: sensor.name, affectedAt,
    boundaries: boundaries.filter((boundary) => boundary.sensorIds.includes(sensor.id))
      .map(({ id, name }) => ({ id, name })),
  };
}

export function reconcileGatewayIncidents(workspace: Workspace, now: number): Workspace {
  const incidents = workspace.gatewayIncidents ?? NO_GATEWAY_INCIDENTS;
  const groups = new Map<string, GatewayMember[]>();
  const online = new Map<string, string>();
  for (const sensor of workspace.sensors) {
    if (!sensor.healthReport) continue;
    const health = getApplianceHealth(sensor, now);
    if (health.status === "online") online.set(sensor.id, sensor.healthReport.occurredAt);
    if (!sensor.gatewayId || (health.status !== "offline" && health.status !== "stale")) continue;
    const outage = getOutageHistory(sensor, now)[0];
    if (!outage || outage.recoveredAt) continue;
    const candidates = groups.get(sensor.gatewayId) ?? [];
    candidates.push(captureMember(sensor, outage.startedAt, workspace.boundaries));
    groups.set(sensor.gatewayId, candidates);
  }

  const updated = incidents.map((incident) => {
    if (incident.recoveredAt) return incident;
    const candidates = groups.get(incident.gatewayId) ?? [];
    groups.delete(incident.gatewayId);
    const byId = new Map(candidates.map((member) => [member.sensorId, member]));
    const devices = incident.devices.map((member) => {
      if (byId.has(member.sensorId)) {
        byId.delete(member.sensorId);
        return member.recoveredAt ? { ...member, recoveredAt: undefined } : member;
      }
      const heartbeat = online.get(member.sensorId);
      if (heartbeat && !member.recoveredAt) {
        return { ...member, recoveredAt: heartbeat < member.affectedAt ? member.affectedAt : heartbeat };
      }
      return member;
    });
    for (const member of byId.values()) {
      if (Date.parse(member.affectedAt) >= Date.parse(incident.startedAt) - CORRELATION_MS) devices.push(member);
    }
    if (devices.length === incident.devices.length && devices.every((member, index) => member === incident.devices[index])) return incident;
    const startedAt = devices.reduce((earliest, member) => member.affectedAt < earliest ? member.affectedAt : earliest, incident.startedAt);
    const recoveredAt = devices.every((member) => member.recoveredAt)
      ? devices.reduce((latest, member) => member.recoveredAt && member.recoveredAt > latest ? member.recoveredAt : latest, startedAt) : undefined;
    return { ...incident, startedAt, devices, recoveredAt };
  });

  const opened: GatewayIncident[] = [];
  for (const [gatewayId, candidates] of groups) {
    candidates.sort((a, b) => a.affectedAt.localeCompare(b.affectedAt));
    const first = candidates.findIndex((member, index) => {
      const next = candidates[index + 1];
      return next && Date.parse(next.affectedAt) - Date.parse(member.affectedAt) <= CORRELATION_MS;
    });
    if (first < 0) continue;
    const startedAt = candidates[first].affectedAt;
    const devices = candidates.slice(first);
    opened.push({ id: JSON.stringify([gatewayId, startedAt]), gatewayId, startedAt, devices });
  }
  if (!opened.length && updated.every((incident, index) => incident === incidents[index])) return workspace;
  const all = [...opened, ...updated].sort((a, b) => b.startedAt.localeCompare(a.startedAt));
  return {
    ...workspace, gatewayIncidents: [...all.filter((incident) => !incident.recoveredAt),
      ...all.filter((incident) => incident.recoveredAt).slice(0, RECOVERED_LIMIT)],
  };
}
