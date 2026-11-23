import assert from "node:assert/strict";
import { test } from "node:test";
import { bearingTo, chooseBoundaryTool, insertVertex, moveVertex, placeBoundaryPoint, readCoordinates, removeVertex, undoSketch } from "../src/features/monitoring/configurationModel.ts";
import { boundaryColor, parseLabels, saveDeviceAssignments, validLabels, validateBoundary } from "../src/features/monitoring/monitoringModel.ts";

const vertices = [[-122.31, 37.78], [-122.30, 37.78], [-122.30, 37.79], [-122.31, 37.79]];
function boundary(id, value = vertices, sensorIds = []) { return { id, name: id, vertices: value, sensorIds, enabled: true, kind: "Perimeter" }; }
function draft(value = vertices) {
  return { kind: "boundary", value: boundary("site", value), tool: value.length ? "vertices" : "polygon", corner: null, selectedVertex: null, history: [], labelsText: "" };
}

test("rectangle uses opposite corners and Undo restores the pending first corner", () => {
  const rectangle = chooseBoundaryTool(draft([]), "rectangle");
  const first = placeBoundaryPoint(rectangle, vertices[0]);
  assert.deepEqual(first.corner, vertices[0]);
  const complete = placeBoundaryPoint(first, vertices[2]);
  assert.deepEqual(complete.value.vertices, vertices);
  assert.equal(complete.tool, "vertices");
  assert.equal(validateBoundary(complete.value.vertices), null);
  assert.deepEqual(undoSketch(complete), first);
});

test("insert, drag and remove vertices leave the saved boundary intact until commit", () => {
  const original = draft();
  const inserted = insertVertex(original, 0);
  assert.equal(inserted.value.vertices.length, 5);
  assert.deepEqual(inserted.value.vertices[1], [-122.305, 37.78]);
  const moved = moveVertex(inserted, 1, [-122.305, 37.779]);
  assert.equal(validateBoundary(moved.value.vertices), null);
  assert.deepEqual(moveVertex(original, 0, [-122.295, 37.785]).value.vertices[0], [-122.295, 37.785]);
  assert.deepEqual(removeVertex(moved).value.vertices, vertices);
  assert.deepEqual(undoSketch(moved).value.vertices, inserted.value.vertices);
  assert.deepEqual(original.value.vertices, vertices);
  assert.match(validateBoundary(moveVertex(original, 0, [-122.295, 37.785]).value.vertices), /cross/);
});

test("coordinates require both axes and map aiming uses bearings from true north", () => {
  assert.deepEqual(readCoordinates({ latitude: "0", longitude: "0" }), [0, 0]);
  assert.equal(readCoordinates({ latitude: "37.8", longitude: "" }), null);
  assert.equal(readCoordinates({ latitude: "86", longitude: "10" }), null);
  assert.equal(bearingTo([0, 0], [0, 1]), 0);
  assert.equal(bearingTo([0, 0], [1, 0]), 90);
  assert.equal(bearingTo([0, 0], [0, -1]), 180);
  assert.equal(bearingTo([0, 0], [0, 0]), null);
});

test("saving a device changes both sides of boundary assignments in one update", () => {
  const sensor = { id: "camera", name: "Existing camera" };
  const original = { sensors: [sensor], boundaries: [boundary("west", vertices, ["camera", "rf"]), boundary("east")], alerts: [], groups: [], received: [] };
  const updated = saveDeviceAssignments(original, { ...sensor, name: "Renamed camera" }, ["east", "missing"]);
  assert.deepEqual(updated.sensors.map((device) => device.name), ["Renamed camera"]);
  assert.deepEqual(updated.boundaries.map((zone) => zone.sensorIds), [["rf"], ["camera"]]);
  assert.deepEqual(original.boundaries[0].sensorIds, ["camera", "rf"]);
});

test("changing a saved camera's type clears incoming camera links without rewriting alerts", () => {
  const camera = { id: "camera", modality: "vision" };
  const gate = { id: "gate", modality: "access", linkedCameraId: "camera" };
  const otherGate = { id: "other-gate", modality: "access", linkedCameraId: "other-camera" };
  const alerts = [{ cameraId: "camera", modality: "vision", observation: { sensorId: "camera" } }];
  const original = { sensors: [camera, gate, otherGate], boundaries: [boundary("west", vertices, ["camera"])], alerts, groups: [], received: [] };
  const updated = saveDeviceAssignments(original, { ...camera, modality: "rf" }, ["west"]);
  assert.deepEqual(updated.sensors, [{ ...camera, modality: "rf" }, { ...gate, linkedCameraId: null }, otherGate]);
  assert.equal(original.sensors[1].linkedCameraId, "camera");
  assert.equal(updated.alerts, alerts);
  assert.equal(updated.boundaries[0], original.boundaries[0]);
});

test("boundary colors respect posture and labels are normalized", () => {
  const zone = { ...boundary("site"), mapTone: "copper" };
  assert.equal(boundaryColor(zone), "#c68f72");
  assert.notEqual(boundaryColor(zone, "unassigned"), boundaryColor(zone));
  assert.deepEqual(parseLabels(" West, west , Night"), ["West", "Night"]);
  assert(!validLabels(["X".repeat(33)]));
});
