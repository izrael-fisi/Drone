import type { Alert, Position, Sensor } from "./monitoringModel.ts";

export type AlertPin = { alert: Alert; count: number };
type Box = { left: number; top: number; width: number; height: number };

export function alertLabelPosition(pin: Box, label: Pick<Box, "width" | "height">, viewport: Box, obstacles: Box[]) {
  const { width, height } = label;
  const x = pin.left + (pin.width - width) / 2;
  const y = pin.top + (pin.height - height) / 2;
  const gap = 6;
  const candidates = [
    [pin.left + pin.width + gap, y], [pin.left - width - gap, y],
    [x, pin.top - height - gap], [x, pin.top + pin.height + gap],
  ];
  const overlap = (position: Box, obstacle: Box) => {
    const width = Math.max(0, Math.min(position.left + position.width, obstacle.left + obstacle.width) - Math.max(position.left, obstacle.left));
    const height = Math.max(0, Math.min(position.top + position.height, obstacle.top + obstacle.height) - Math.max(position.top, obstacle.top));
    return width * height;
  };
  let best = { left: x, top: y, width, height };
  let bestScore = Infinity;
  for (const [left, top] of candidates) {
    const box = {
      left: Math.max(viewport.left, Math.min(left, viewport.left + viewport.width - width)),
      top: Math.max(viewport.top, Math.min(top, viewport.top + viewport.height - height)), width, height,
    };
    const score = overlap(box, pin) * 100 + obstacles.reduce((total, obstacle) => total + overlap(box, obstacle), 0);
    if (score === 0) return box;
    if (score < bestScore) {
      best = box;
      bestScore = score;
    }
  }
  return best;
}

export function sameMapPoint(a: Position, b: Position) {
  return a[0] === b[0] && a[1] === b[1];
}

export function mapAlertScope(alerts: Alert[], selected?: Alert, scope?: { devices: ReadonlySet<string>; boundaries: ReadonlySet<string> }) {
  return alerts.filter((alert) => {
    if (alert.id === selected?.id) return true;
    if (alert.status !== "new") return false;
    if (selected) return sameMapPoint(alert.observation.position, selected.observation.position);
    return !scope || scope.devices.has(alert.observation.sensorId) || scope.boundaries.has(alert.zoneId);
  });
}

export function groupAlertPins(alerts: Alert[], devices: Sensor[], selected?: string) {
  const positions = new Map<string, Alert[]>();
  for (const alert of alerts) {
    const key = alert.observation.position.join(",");
    const existing = positions.get(key);
    if (existing) existing.push(alert);
    else positions.set(key, [alert]);
  }
  const atDevice = new Map<string, AlertPin>();
  const standalone: AlertPin[] = [];
  const devicesById = new Map(devices.map((device) => [device.id, device]));
  for (const group of positions.values()) {
    const alert = group.find((entry) => entry.id === selected)
      ?? group.find((entry) => entry.severity === "High") ?? group[0];
    const pin = { alert, count: group.length };
    const device = devicesById.get(alert.observation.sensorId);
    if (device && sameMapPoint(device.position, alert.observation.position)) atDevice.set(device.id, pin);
    else standalone.push(pin);
  }
  return { atDevice, standalone };
}
