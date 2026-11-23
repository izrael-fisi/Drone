import assert from "node:assert/strict";
import { test } from "node:test";
import { evaluateObservation, parseObservation } from "../src/features/monitoring/monitoringModel.ts";
import { incidentTimeline, recordingForEvent } from "../src/features/monitoring/replayTimeline.ts";

const origin = Date.parse("2026-11-22T18:00:00Z");
const stamp = (seconds) => new Date(origin + seconds * 1000).toISOString();
const gate = { id: "gate", name: "Gate contact", modality: "access", position: [0.05, 0.05], linkedCameraId: "camera" };
const camera = { id: "camera", name: "Yard camera", modality: "vision" };
const rf = { id: "rf", name: "Radio receiver", modality: "rf" };
const yard = { id: "yard", name: "Yard", enabled: true, kind: "Perimeter", sensorIds: ["gate", "camera", "rf"],
  vertices: [[0, 0], [0, 0.1], [0.1, 0.1], [0.1, 0]] };
const base = { sensors: [gate, camera, rf], boundaries: [yard, { ...yard, id: "dock", name: "Dock" }], alerts: [], groups: [], received: [] };
const recording = { cameraId: "camera", url: "https://video.example.test/yard.mp4", format: "mp4", eventOffsetSeconds: 2, durationSeconds: 10 };
function event(id, sensorId, seconds, extra = {}) {
  return parseObservation({ id, sensorId, occurredAt: stamp(seconds), summary: id, ...extra });
}
function ingest(workspace, incoming) {
  return evaluateObservation(workspace, incoming, "Imported sample", true);
}

test("replay merges legacy boundary alerts and accepted telemetry into one chronological entry per source event", () => {
  const trigger = ingest(base, event("gate opened", "gate", 10));
  const unlocated = ingest(trigger.workspace, event("RF signal unlocated", "rf", 3));
  const outside = ingest(unlocated.workspace, event("Outside yard", "rf", 17, { position: [0.2, 0.2] }));
  const old = ingest(outside.workspace, event("Prior shift", "rf", -901));
  const [anchor] = trigger.created;
  assert.equal(unlocated.created.length, 0);
  assert.equal(outside.created.length, 0);
  assert.deepEqual(incidentTimeline(old.workspace, anchor).map(({ observation }) => observation.summary),
    ["RF signal unlocated", "gate opened", "Outside yard"]);
  const legacy = incidentTimeline({ alerts: trigger.workspace.alerts }, anchor);
  assert.deepEqual(legacy[0].boundaries.map(({ id }) => id), ["yard", "dock"]);
  assert.deepEqual(legacy[0].observation.position, gate.position);
  assert.equal(ingest(old.workspace, event("gate opened", "gate", 20)).workspace, old.workspace);
});

test("an event seeks in its own clip or a matching clip with known duration, and preserves gaps", () => {
  const first = ingest(base, event("camera recorded", "camera", 10, { position: [0.05, 0.05], recording }));
  const second = ingest(first.workspace, event("RF during clip", "rf", 12, { position: [0.05, 0.05] }));
  const third = ingest(second.workspace, event("RF outside clip", "rf", 18, { position: [0.05, 0.05] }));
  const fourth = ingest(third.workspace, event("Unlocated RF", "rf", 12));
  const timeline = incidentTimeline(fourth.workspace, first.created[0]);
  assert.deepEqual(recordingForEvent(timeline.find((ev) => ev.observation.id === "camera recorded"), timeline), recording);
  assert.deepEqual(recordingForEvent(timeline.find((ev) => ev.observation.id === "RF during clip"), timeline),
    { ...recording, eventOffsetSeconds: 4 });
  assert.equal(recordingForEvent(timeline.find((ev) => ev.observation.id === "RF outside clip"), timeline), undefined);
  assert.equal(recordingForEvent(timeline.find((ev) => ev.observation.id === "Unlocated RF"), timeline), undefined);
  assert.throws(() => event("bad clip", "camera", 20, { recording: { ...recording, durationSeconds: 2 } }), /duration/);
});
