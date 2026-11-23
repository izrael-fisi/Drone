import assert from "node:assert/strict";
import { test } from "node:test";
import { recordHealthReport } from "../src/features/monitoring/applianceHealth.ts";
import { gatewayBoundaries, normalizeGatewayId, reconcileGatewayIncidents } from "../src/features/monitoring/gatewayIncidents.ts";

const origin = Date.parse("2026-11-23T00:00:00Z");
const millis = (seconds) => origin + seconds * 1000;
const timestamp = (seconds) => new Date(millis(seconds)).toISOString();
const base = {
  sensors: [
    { id: "cam", name: "Camera", gatewayId: "east", reportIntervalSeconds: 5 },
    { id: "rf", name: "Receiver", gatewayId: "east", reportIntervalSeconds: 5 },
    { id: "gate", name: "Gate", gatewayId: "east", reportIntervalSeconds: 20 },
    { id: "remote", name: "Remote camera", gatewayId: "west", reportIntervalSeconds: 5 },
    { id: "unassigned", name: "Unassigned sensor" },
  ],
  boundaries: [
    { id: "yard", name: "Yard", sensorIds: ["cam", "rf"] },
    { id: "access", name: "Access road", enabled: false, sensorIds: ["gate", "rf"] },
  ],
  groups: [], alerts: [], received: [],
};
function ingest(workspace, sensorId, state, at) {
  return reconcileGatewayIncidents(recordHealthReport(workspace, { type: "health", sensorId, state, occurredAt: timestamp(at) }), millis(at));
}

test("a gateway ID is explicit and an isolated, simulated, or unrelated device outage opens no gateway incident", () => {
  assert.equal(normalizeGatewayId(" East-GW_02 "), "east-gw_02");
  assert.equal(normalizeGatewayId("East Gateway"), null);
  let workspace = ingest(base, "cam", "offline", 0);
  workspace = ingest(workspace, "remote", "offline", 5);
  workspace = ingest(workspace, "unassigned", "offline", 10);
  workspace = ingest(workspace, "rf", "offline", 121);
  assert.equal(workspace.gatewayIncidents, undefined);
  assert.equal(reconcileGatewayIncidents(workspace, millis(130)), workspace);
});

test("an open gateway outage tracks new members, partial recovery, a flap, and final recovery in one record", () => {
  let workspace = ingest(base, "cam", "offline", 0);
  workspace = ingest(workspace, "rf", "offline", 5);
  const [opened] = workspace.gatewayIncidents;
  assert.deepEqual(opened.devices.map((device) => device.sensorId), ["cam", "rf"]);
  assert.deepEqual(gatewayBoundaries(opened).map(({ id, affected }) => [id, affected]), [["yard", 2], ["access", 1]]);
  workspace = ingest(workspace, "gate", "offline", 6);
  workspace = ingest(workspace, "rf", "online", 7);
  assert.equal(workspace.gatewayIncidents.length, 1);
  assert.equal(workspace.gatewayIncidents[0].id, opened.id);
  assert.equal(workspace.gatewayIncidents[0].devices.find((member) => member.sensorId === "rf").recoveredAt, timestamp(7));
  assert.deepEqual(gatewayBoundaries(workspace.gatewayIncidents[0]).map(({ id, affected }) => [id, affected]), [["yard", 1], ["access", 1]]);
  workspace = ingest(workspace, "rf", "offline", 8);
  assert.equal(workspace.gatewayIncidents[0].devices.find((member) => member.sensorId === "rf").recoveredAt, undefined);
  workspace = ingest(workspace, "cam", "online", 9);
  workspace = ingest(workspace, "rf", "online", 10);
  workspace = ingest(workspace, "gate", "online", 11);
  assert.equal(workspace.gatewayIncidents[0].recoveredAt, timestamp(11));
  assert.equal(workspace.gatewayIncidents[0].id, opened.id);
  assert.deepEqual(workspace.alerts, base.alerts);
  workspace = ingest(workspace, "cam", "offline", 12);
  workspace = ingest(workspace, "rf", "offline", 13);
  assert.equal(workspace.gatewayIncidents.length, 2);
  assert.notEqual(workspace.gatewayIncidents[0].id, opened.id);
  assert.equal(workspace.gatewayIncidents[1].recoveredAt, timestamp(11));
});

test("heartbeat expiry groups a shared reporting loss and waits for each member to come back online", () => {
  let workspace = ingest(base, "cam", "online", 0);
  workspace = ingest(workspace, "rf", "online", 1);
  workspace = ingest(workspace, "gate", "online", 2);
  workspace = reconcileGatewayIncidents(workspace, millis(16));
  assert.equal(workspace.gatewayIncidents[0].startedAt, timestamp(15));
  assert.equal(workspace.gatewayIncidents[0].devices.length, 2);
  workspace = reconcileGatewayIncidents(workspace, millis(63));
  assert.equal(workspace.gatewayIncidents[0].devices.length, 3);
  assert.equal(reconcileGatewayIncidents(workspace, millis(64)), workspace);
  workspace = ingest(workspace, "cam", "online", 64);
  assert.equal(workspace.gatewayIncidents[0].recoveredAt, undefined);
  assert.equal(workspace.gatewayIncidents[0].devices[0].recoveredAt, timestamp(64));
});
