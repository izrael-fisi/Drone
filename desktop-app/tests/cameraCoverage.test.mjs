import assert from "node:assert/strict";
import { test } from "node:test";
import { calculateCameraCoverage, cameraSector } from "../src/features/monitoring/cameraCoverage.ts";
import { containsPoint } from "../src/features/monitoring/monitoringModel.ts";

function covers(multipolygon, point) {
  return multipolygon.some(([outside, ...holes]) => containsPoint(outside, point) && !holes.some((hole) => containsPoint(hole, point)));
}

const view = { bearingDeg: 0, fovDeg: 90, rangeM: 100 };

function camera(id, position, cameraView = view) {
  return { id, position, cameraView, modality: "vision", name: id, groupId: null, labels: [], mapLabel: "", demo: true };
}

function boundary(id, vertices, enabled = true) {
  return { id, vertices, enabled, sensorIds: [], kind: "Perimeter", name: id };
}

test("the sector follows true bearings and respects angle and range", () => {
  const north = cameraSector([0, 0], view);
  assert(covers(north, [0, 0.0006]));
  assert(!covers(north, [0.0006, 0]));
  assert(!covers(north, [0, 0.001]));
  const east = cameraSector([0, 0], { ...view, bearingDeg: 90 });
  assert(covers(east, [0.0006, 0]));
  assert(!covers(east, [0, 0.0006]));
  const panorama = cameraSector([0, 0], { ...view, fovDeg: 360 });
  assert(covers(panorama, [0, -0.0006]));
  assert(covers(panorama, [-0.0006, 0]));
});

test("camera sectors that cross the date line split into valid map polygons", () => {
  const sector = cameraSector([179.9997, 0], { ...view, bearingDeg: 90 });
  assert.equal(sector.length, 2);
  assert(sector.flat(2).every(([lng]) => lng >= -180 && lng <= 180));
  assert(covers(sector, [-179.9998, 0]));
  assert(!covers(sector, [0, 0]));
});

test("overlap and enabled-boundary gaps reflect the union of configured views", () => {
  const site = boundary("site", [[-0.001, -0.001], [0.001, -0.001], [0.001, 0.001], [-0.001, 0.001]]);
  const paused = boundary("paused", [[0.01, 0.01], [0.02, 0.01], [0.02, 0.02], [0.01, 0.02]], false);
  const result = calculateCameraCoverage([camera("a", [-0.0002, 0]), camera("b", [0.0002, 0]), camera("unknown", [0, 0], null)], [site, paused]);
  assert.equal(result.sectors.length, 2);
  assert(covers(result.overlaps, [0, 0.0005]));
  assert(!covers(result.overlaps, [-0.0005, 0.0004]));
  assert(!covers(result.gaps, [-0.0005, 0.0004]));
  assert(covers(result.gaps, [0, -0.0005]));
  assert(!covers(result.gaps, [0.015, 0.015]));
  assert(!result.gapFractionByBoundary.has("paused"));
  assert(result.gapFractionByBoundary.get("site") > 0.5);
});

test("a site without configured cameras is entirely a modeled gap", () => {
  const site = boundary("site", [[0, 0], [0.001, 0], [0.001, 0.001], [0, 0.001]]);
  const result = calculateCameraCoverage([camera("unknown", [0, 0], null)], [site]);
  assert.equal(result.gapFractionByBoundary.get("site"), 1);
  assert.equal(result.overlaps.length, 0);
  assert(covers(result.gaps, [0.0005, 0.0005]));
});

test("a marked obstruction blocks coverage behind the wall even while its boundary monitoring is paused", () => {
  const site = boundary("site", [[-.0003, .0004], [.0003, .0004], [.0003, .0007], [-.0003, .0007]]);
  const wall = { ...boundary("wall", [[-.0005, .0002], [.0005, .0002], [.0005, .00025], [-.0005, .00025]], false), blocksCameraView: true };
  const blocked = calculateCameraCoverage([camera("a", [0, 0])], [site, wall]);
  assert(covers(blocked.sectors[0].geometry, [0, .0001]));
  assert(!covers(blocked.sectors[0].geometry, [0, .0005]));
  assert.equal(blocked.gapFractionByBoundary.get("site"), 1);
});

test("full coverage removes the gap and a distant camera leaves the entire site uncovered", () => {
  const site = boundary("site", [[0, 0.0004], [0, 0], [0.0004, 0], [0.0004, 0.0004]]);
  const panorama = { ...view, fovDeg: 360 };
  const covered = calculateCameraCoverage([camera("near", [0, 0], panorama)], [site]);
  assert.equal(covered.gapFractionByBoundary.get("site"), 0);
  assert.equal(covered.gaps.length, 0);
  const uncovered = calculateCameraCoverage([camera("far", [0, 0.02], panorama)], [site]);
  assert.equal(uncovered.gapFractionByBoundary.get("site"), 1);
});
