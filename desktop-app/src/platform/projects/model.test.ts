import { describe, expect, it } from "vitest";
import { areaM2, circle, destination, parseDocument, viewCone } from "./model";

const OAKLAND: [number, number] = [37.8044, -122.2712];

function distanceM(a: [number, number], b: [number, number]) {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[0] - a[0]);
  const dLon = toRad(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLon / 2) ** 2;
  return 2 * 6_371_008.8 * Math.asin(Math.sqrt(h));
}

describe("site geometry", () => {
  it("moves the requested distance in the requested direction", () => {
    const north = destination(OAKLAND, 0, 100);
    expect(distanceM(OAKLAND, north)).toBeCloseTo(100, 0);
    expect(north[0]).toBeGreaterThan(OAKLAND[0]);
    expect(north[1]).toBeCloseTo(OAKLAND[1], 6);
    const east = destination(OAKLAND, 90, 100);
    expect(east[1]).toBeGreaterThan(OAKLAND[1]);
  });

  it("draws a camera cone from the camera out to its range", () => {
    const cone = viewCone(OAKLAND, 90, 60, 50);
    expect(cone[0]).toEqual(OAKLAND);
    for (const point of cone.slice(1)) expect(distanceM(OAKLAND, point)).toBeCloseTo(50, 0);
    // Facing east: every arc point is east of the camera.
    for (const point of cone.slice(1)) expect(point[1]).toBeGreaterThan(OAKLAND[1]);
  });

  it("measures a 100 m square as about one hectare", () => {
    const east = destination(OAKLAND, 90, 100);
    const square: [number, number][] = [OAKLAND, east, destination(east, 0, 100), destination(OAKLAND, 0, 100)];
    expect(areaM2(square)).toBeGreaterThan(9_900);
    expect(areaM2(square)).toBeLessThan(10_100);
  });

  it("closes a sensor range circle at the given radius", () => {
    const ring = circle(OAKLAND, 40, 12);
    expect(ring).toHaveLength(12);
    for (const point of ring) expect(distanceM(OAKLAND, point)).toBeCloseTo(40, 0);
  });
});

describe("projects file", () => {
  it("returns an empty document for a missing or unreadable file", () => {
    expect(parseDocument(null)).toEqual({ version: 1, activeProjectId: null, projects: [] });
    expect(parseDocument("garbage").projects).toEqual([]);
  });

  it("keeps good projects and items and drops malformed ones", () => {
    const doc = parseDocument({
      version: 1,
      activeProjectId: "missing",
      projects: [
        {
          id: "p1",
          name: "Oakland yard",
          view: { center: OAKLAND, zoom: 17 },
          items: [
            { id: "c1", kind: "camera", name: "Gate cam", position: OAKLAND },
            { id: "bad1", kind: "camera", name: "No position" },
            { id: "g1", kind: "geofence", name: "Yard", polygon: [OAKLAND, OAKLAND, OAKLAND] },
            { id: "bad2", kind: "geofence", name: "Two corners", polygon: [OAKLAND, OAKLAND] },
            { id: "bad3", kind: "laser", name: "Unknown kind", position: OAKLAND },
          ],
        },
        { id: "p2", name: "No view" },
      ],
    });
    expect(doc.projects.map((p) => p.id)).toEqual(["p1"]);
    expect(doc.projects[0].items.map((i) => i.id)).toEqual(["c1", "g1"]);
    // An unknown active id falls back to the first project.
    expect(doc.activeProjectId).toBe("p1");
  });
});
