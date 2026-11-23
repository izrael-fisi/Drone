import assert from "node:assert/strict";
import { test } from "node:test";
import { alertLabelPosition, groupAlertPins, mapAlertScope } from "../src/features/monitoring/mapAlerts.ts";

const gate = { id: "gate", position: [5, 6] };
const lot = [5.01, 6];
const event = (id, status, position, sensorId = "gate", zoneId = "access", severity = "Medium") => ({
  id, status, zoneId, severity, observation: { position, sensorId, summary: id },
});

test("map alert visibility prioritizes new alerts and keeps a selected historical alert visible", () => {
  const current = event("pending", "new", gate.position);
  const investigating = event("reviewed", "acknowledged", gate.position);
  const historic = event("closed", "resolved", gate.position);
  const elsewhere = event("movement", "new", lot, "rf", "yard");
  const alerts = [current, investigating, historic, elsewhere];
  assert.deepEqual(mapAlertScope(alerts), [current, elsewhere]);
  assert.deepEqual(mapAlertScope(alerts, historic), [current, historic]);
  assert.deepEqual(mapAlertScope(alerts, undefined, { devices: new Set(["rf"]), boundaries: new Set(["yard"]) }), [elsewhere]);
});

test("colocated alerts have one labeled pin, prioritize selected or high-priority alerts and retain device placement", () => {
  const first = event("open gate", "new", gate.position);
  const high = event("forced gate", "new", gate.position, "gate", "access", "High");
  const moving = event("movement", "new", lot, "gate", "yard", "High");
  const pins = groupAlertPins([first, high, moving], [gate]);
  assert.deepEqual(pins.atDevice.get(gate.id), { alert: high, count: 2 });
  assert.deepEqual(pins.standalone, [{ alert: moving, count: 1 }]);
  assert.deepEqual(groupAlertPins([first, high], [gate], first.id).atDevice.get(gate.id), { alert: first, count: 2 });
  assert.deepEqual(groupAlertPins([first, high], []).standalone, [{ alert: high, count: 2 }]);
});

test("labels flip at viewport edges and avoid other markers and labels when there is room", () => {
  const viewport = { left: 6, top: 6, width: 408, height: 788 };
  const label = { width: 126, height: 30 };
  const firstPin = { left: 348, top: 390, width: 28, height: 28 };
  const first = alertLabelPosition(firstPin, label, viewport, []);
  assert.deepEqual(first, { left: 216, top: 389, ...label });
  const nextPin = { left: 254, top: 423, width: 28, height: 28 };
  const next = alertLabelPosition(nextPin, label, viewport, [first]);
  assert.deepEqual(next, { left: 288, top: 422, ...label });
});
