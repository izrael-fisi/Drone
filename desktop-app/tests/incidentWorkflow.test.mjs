import assert from "node:assert/strict";
import { test } from "node:test";
import { applyIncidentAction, incidentCamera } from "../src/features/monitoring/incidentModel.ts";
import { evaluateObservation, parseObservation } from "../src/features/monitoring/monitoringModel.ts";

const occurredAt = "2026-11-22T18:40:00Z";
const gate = { id: "gate", name: "East gate", modality: "access", position: [-122.29, 37.785], linkedCameraId: "dock" };
const dock = { id: "dock", name: "Dock camera", modality: "vision" };
const spare = { id: "spare", name: "Spare camera", modality: "vision" };
const boundary = { id: "yard", name: "Yard", kind: "Restricted", enabled: true, sensorIds: ["gate", "dock"],
  vertices: [[-122.31, 37.78], [-122.30, 37.78], [-122.30, 37.79], [-122.31, 37.79]] };
const workspace = { sensors: [gate, dock, spare], boundaries: [boundary,
  { ...boundary, id: "paused", enabled: false }, { ...boundary, id: "unassigned", sensorIds: [] }], groups: [], alerts: [], received: [] };
const event = { id: "gate-event", sensorId: "gate", occurredAt, summary: "Unscheduled opening" };
const recording = { cameraId: "dock", format: "mp4", url: "https://video.example.com/clip.mp4", eventOffsetSeconds: 2 };
const importEvent = (value, base = workspace) => evaluateObservation(base, parseObservation(value), "Imported sample");

test("fixed access events use the device location and enabled assignments when the event has no position", () => {
  const result = importEvent(event);
  assert.deepEqual(result.created.map((alert) => alert.zoneId), ["yard"]);
  assert.equal(result.created[0].locationBasis, "device");
  assert.deepEqual(result.created[0].observation.position, gate.position);
  assert.equal(result.created[0].severity, "High");
  assert.deepEqual(importEvent({ ...event, position: gate.position }).created, []);
  assert.equal(importEvent({ ...event, position: [-122.305, 37.785] }).created[0].locationBasis, "reported");
  assert.throws(() => importEvent({ ...event, sensorId: "dock" }), /position is required/);
  const replay = importEvent(event, result.workspace);
  assert.equal(replay.duplicate, true);
  assert.equal(replay.workspace, result.workspace);
});

test("recording must match the camera and incident association survives reassignment", () => {
  const [alert] = importEvent({ ...event, recording }).created;
  assert.deepEqual(alert.observation.recording, recording);
  const sensors = [{ ...gate, linkedCameraId: spare.id }, dock, spare];
  assert.equal(incidentCamera(alert, sensors), dock);
  assert.equal(incidentCamera({ ...alert, cameraId: undefined }, sensors), spare);
  assert.equal(incidentCamera({ ...alert, cameraId: null }, sensors), undefined);
  assert.throws(() => importEvent({ ...event, recording: { ...recording, cameraId: spare.id } }), /linked camera/);
  for (const patch of [{ format: "rtsp" }, { url: "https://viewer:secret@video.example.com/clip.mp4" }, { eventOffsetSeconds: -1 }]) {
    assert.throws(() => parseObservation({ ...event, recording: { ...recording, ...patch } }), /A recording needs/);
  }
  assert.equal(parseObservation({ ...event, recording: { ...recording, eventOffsetSeconds: undefined } }).recording.eventOffsetSeconds, 0);
});

test("a legacy alert never picks up a camera from a differently classified source", () => {
  const [alert] = importEvent({ ...event, sensorId: dock.id, position: [-122.305, 37.785] }).created;
  const sensors = [{ ...dock, modality: "access", linkedCameraId: spare.id }, spare];
  assert.equal(incidentCamera({ ...alert, cameraId: undefined }, sensors), undefined);
  assert.equal(incidentCamera(alert, sensors), undefined);
});

test("operator ownership, notes, and disposition append to incident history", () => {
  const [alert] = importEvent(event).created;
  const act = (current, action) => applyIncidentAction(current, action, occurredAt);
  const acknowledged = act(alert, { kind: "acknowledge" });
  assert.equal(acknowledged.status, "acknowledged");
  assert.equal(act(acknowledged, { kind: "resolve", disposition: "false-alarm" }), acknowledged);
  const owned = act(acknowledged, { kind: "claim", name: "  Alex  " });
  const noted = act(owned, { kind: "note", text: "  Confirmed vendor delivery.  " });
  assert.equal(act(noted, { kind: "resolve", disposition: "toString" }), noted);
  const resolved = act(noted, { kind: "resolve", disposition: "false-alarm" });
  assert.equal(resolved.status, "resolved");
  assert.equal(resolved.disposition, "false-alarm");
  assert.deepEqual(resolved.activity.map((entry) => entry.kind), ["acknowledge", "claim", "note", "resolve"]);
  assert.equal(resolved.activity[2].text, "Confirmed vendor delivery.");
  assert.equal(resolved.activity[2].operator, "Alex");
  assert.equal(act(resolved, { kind: "claim", name: "Riley" }), resolved);
  assert.equal(alert.status, "new");
});
