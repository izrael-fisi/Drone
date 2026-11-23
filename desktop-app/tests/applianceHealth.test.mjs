import assert from "node:assert/strict";
import { test } from "node:test";
import { activeMaintenance, getApplianceHealth, getHealthScope, getOutageHistory, parseHealthReport, recordHealthReport,
  scheduleMaintenance, stopMaintenance, validReportInterval } from "../src/features/monitoring/applianceHealth.ts";

const now = Date.parse("2026-11-22T18:40:00Z");
const sensor = { id: "west-gate", demo: false, reportIntervalSeconds: 10 };
const baseReport = { type: "health", sensorId: sensor.id, state: "online", occurredAt: new Date(now).toISOString() };
const report = (patch = {}) => parseHealthReport({ ...baseReport, ...patch }, now);

test("reporting health distinguishes unknown, measured, overdue, and explicitly offline devices", () => {
  assert.equal(getApplianceHealth({ ...sensor, cameraFeedUrl: "https://example.test/live.m3u8" }, now).status, "unknown");
  assert.deepEqual(getApplianceHealth({ id: "rf-01", demo: true }, now), { status: "stale", simulated: true });
  const online = { ...sensor, healthReport: report() };
  assert.equal(getApplianceHealth(online, now + 29_999).status, "online");
  assert.equal(getApplianceHealth(online, now + 30_000).status, "stale");
  assert.equal(getApplianceHealth({ ...online, demo: true }, now).simulated, undefined);
  assert.equal(getApplianceHealth({ ...sensor, healthReport: report({ state: "offline" }) }, now + 90_000).status, "offline");
});

test("health imports apply to a registered device and cannot roll back its newest report", () => {
  const workspace = { sensors: [sensor], alerts: [], boundaries: [], received: [] };
  const latest = recordHealthReport(workspace, report());
  assert.equal(latest.sensors[0].healthReport.state, "online");
  assert.equal(workspace.sensors[0].healthReport, undefined);
  assert.equal(recordHealthReport(latest, report({ state: "offline" })), latest);
  const older = report({ occurredAt: "2026-11-22T10:39:59-08:00", state: "offline" });
  assert.equal(recordHealthReport(latest, older), latest);
  const next = recordHealthReport(latest, report({ occurredAt: "2026-11-22T10:40:01-08:00", state: "offline" }));
  assert.equal(next.sensors[0].healthReport.occurredAt, "2026-11-22T18:40:01.000Z");
  assert.equal(next.sensors[0].healthReport.state, "offline");
  assert.throws(() => recordHealthReport(workspace, report({ sensorId: "unknown" })), /Unknown device/);
});

test("health report schemas reject unsupported state, ambiguous clocks, and excessive clock skew", () => {
  for (const patch of [{ state: "stale" }, { occurredAt: "2026-11-22T18:40:00" }, { sensorId: " " }, { type: "event" }]) {
    assert.throws(() => report(patch), /A health report needs/);
  }
  assert.throws(() => report({ occurredAt: "2026-11-22T18:40:31Z" }), /in the future/);
  assert.equal(validReportInterval(5), true);
  assert.equal(validReportInterval(3600), true);
  for (const seconds of [0, 4, 3601, Infinity, 10.5]) assert.equal(validReportInterval(seconds), false);
});

test("reporting gaps and explicit outages keep a single start and record recovery", () => {
  const base = { sensors: [sensor], alerts: [], boundaries: [], received: [] };
  const stamp = (seconds) => new Date(now + seconds * 1000).toISOString();
  const apply = (workspace, state, seconds) => recordHealthReport(workspace, { ...baseReport, state, occurredAt: stamp(seconds) });
  const online = apply(base, "online", 0);
  assert.deepEqual(getOutageHistory(online.sensors[0], now + 29_999), []);
  assert.deepEqual(getOutageHistory(online.sensors[0], now + 30_000), [{ startedAt: stamp(30), cause: "stale" }]);
  assert.deepEqual(getOutageHistory(apply(online, "online", 30).sensors[0], now + 30_000), []);
  const offline = apply(online, "offline", 40);
  const repeated = apply(offline, "offline", 50);
  assert.deepEqual(getOutageHistory(repeated.sensors[0], now + 50_000), [{ startedAt: stamp(30), cause: "stale" }]);
  const recovered = apply(repeated, "online", 70);
  assert.deepEqual(getOutageHistory(recovered.sensors[0], now + 70_000), [{ startedAt: stamp(30), cause: "stale", recoveredAt: stamp(70) }]);
  const explicit = apply(recovered, "offline", 71);
  assert.deepEqual(getOutageHistory(explicit.sensors[0], now + 71_000)[0], { startedAt: stamp(71), cause: "offline" });
  assert.equal(apply(explicit, "online", 70), explicit);
  assert.deepEqual(getOutageHistory({ id: "gate-01", demo: true }, now), []);
});

test("health scope includes all matching appliances and each assigned boundary once", () => {
  const devices = [sensor, { id: "yard-rf" }, { id: "camera" }];
  const areas = [{ id: "yard", enabled: true, sensorIds: [sensor.id, "yard-rf", "camera"] },
    { id: "spare", enabled: false, sensorIds: ["yard-rf"] }, { id: "unaffected", sensorIds: ["camera"] }];
  const health = new Map([[sensor.id, { status: "offline" }], ["yard-rf", { status: "offline" }], ["camera", { status: "online" }]]);
  assert.deepEqual(getHealthScope(devices, areas, health, "offline"), { devices: devices.slice(0, 2), boundaries: areas.slice(0, 2) });
  assert.deepEqual(getHealthScope(devices, areas, health, "unknown"), { devices: [], boundaries: [] });
});

test("maintenance starts and expires separately from measured health and can be ended or canceled", () => {
  const offline = { ...sensor, healthReport: report({ state: "offline" }) };
  const base = { sensors: [offline], alerts: [], boundaries: [], received: [] };
  const start = now + 60_000;
  const end = start + 60_000;
  const scheduled = scheduleMaintenance(base, sensor.id, start, end, "  Replace power supply  ", now);
  const window = scheduled.sensors[0].maintenanceWindows[0];
  assert.equal(window.reason, "Replace power supply");
  assert.equal(activeMaintenance(scheduled.sensors[0], start - 1), undefined);
  assert.equal(activeMaintenance(scheduled.sensors[0], start), window);
  assert.equal(activeMaintenance(scheduled.sensors[0], end), undefined);
  assert.equal(getApplianceHealth(scheduled.sensors[0], start).status, "offline");
  assert.throws(() => scheduleMaintenance(scheduled, sensor.id, start, end, "Duplicate", now), /already has maintenance/);
  assert.throws(() => scheduleMaintenance(base, sensor.id, now - 60_000, end, "Past start", now), /Choose a start/);
  assert.throws(() => scheduleMaintenance(base, sensor.id, start, start, "Invalid end", now), /Choose a start/);
  assert.throws(() => scheduleMaintenance(base, sensor.id, start, end, "  ", now), /reason/);
  const canceled = stopMaintenance(scheduled, sensor.id, window.startsAt, now);
  assert.deepEqual(canceled.sensors[0].maintenanceWindows, []);
  const ended = stopMaintenance(scheduled, sensor.id, window.startsAt, start + 1000);
  assert.equal(ended.sensors[0].maintenanceWindows[0].endsAt, new Date(start + 1000).toISOString());
  assert.equal(activeMaintenance(ended.sensors[0], start + 1000), undefined);
  assert.equal(stopMaintenance(scheduled, sensor.id, window.startsAt, end), scheduled);
});
