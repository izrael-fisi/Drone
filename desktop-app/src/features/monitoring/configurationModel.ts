import { isPosition, type Boundary, type Position } from "./monitoringModel.ts";

export type BoundaryTool = "polygon" | "rectangle" | "vertices";
interface Sketch {
  tool: BoundaryTool;
  vertices: Position[];
  corner: Position | null;
}

export interface BoundaryDraft {
  kind: "boundary";
  value: Boundary;
  tool: BoundaryTool;
  corner: Position | null;
  history: Sketch[];
  selectedVertex: number | null;
  labelsText: string;
}

export const MAX_VERTICES = 120;

function changeSketch(draft: BoundaryDraft, vertices: Position[], tool = draft.tool, corner: Position | null = null): BoundaryDraft {
  const history = [...draft.history, { tool: draft.tool, vertices: draft.value.vertices, corner: draft.corner }];
  return { ...draft, tool, corner, value: { ...draft.value, vertices }, history, selectedVertex: null };
}

export function chooseBoundaryTool(draft: BoundaryDraft, tool: BoundaryTool): BoundaryDraft {
  if (tool === draft.tool) return draft;
  if (tool === "vertices") return { ...draft, tool, corner: null };
  return changeSketch(draft, [], tool);
}

export function undoSketch(draft: BoundaryDraft): BoundaryDraft {
  const previous = draft.history[draft.history.length - 1];
  return previous ? { ...draft, tool: previous.tool, corner: previous.corner, selectedVertex: null,
    history: draft.history.slice(0, -1), value: { ...draft.value, vertices: previous.vertices } } : draft;
}

export function placeBoundaryPoint(draft: BoundaryDraft, point: Position): BoundaryDraft {
  if (draft.tool === "rectangle") {
    if (!draft.corner) return changeSketch(draft, draft.value.vertices, "rectangle", point);
    const [lng, lat] = draft.corner;
    const [endLng, endLat] = point;
    const vertices: Position[] = [[lng, lat], [endLng, lat], point, [lng, endLat]];
    return changeSketch(draft, vertices, "vertices");
  }
  if (draft.tool !== "polygon" || draft.value.vertices.length >= MAX_VERTICES) return draft;
  return changeSketch(draft, [...draft.value.vertices, point]);
}

export function moveVertex(draft: BoundaryDraft, index: number, point: Position): BoundaryDraft {
  if (draft.tool !== "vertices" || !draft.value.vertices[index]) return draft;
  const vertices = draft.value.vertices.map((vertex, i) => i === index ? point : vertex);
  return { ...changeSketch(draft, vertices), selectedVertex: index };
}

export function insertVertex(draft: BoundaryDraft, index: number): BoundaryDraft {
  const vertices = draft.value.vertices;
  const start = vertices[index];
  if (draft.tool !== "vertices" || !start || vertices.length >= MAX_VERTICES) return draft;
  const end = vertices[(index + 1) % vertices.length];
  const midpoint: Position = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
  return { ...changeSketch(draft, [...vertices.slice(0, index + 1), midpoint, ...vertices.slice(index + 1)]), selectedVertex: index + 1 };
}

export function removeVertex(draft: BoundaryDraft): BoundaryDraft {
  if (draft.selectedVertex === null || draft.value.vertices.length <= 3) return draft;
  return changeSketch(draft, draft.value.vertices.filter((_, index) => index !== draft.selectedVertex));
}

export interface CoordinateDraft { latitude: string; longitude: string }

export function toCoordinateDraft(position: Position | null): CoordinateDraft {
  return { latitude: position?.[1].toString() ?? "", longitude: position?.[0].toString() ?? "" };
}

export function readCoordinates({ longitude, latitude }: CoordinateDraft): Position | null {
  if (!longitude.trim() || !latitude.trim()) return null;
  const position: Position = [Number(longitude), Number(latitude)];
  return isPosition(position) ? position : null;
}

export function bearingTo(from: Position, toward: Position): number | null {
  const radians = Math.PI / 180;
  const longitudeDelta = (toward[0] - from[0]) * radians;
  const latitudeStart = from[1] * radians;
  const latitudeEnd = toward[1] * radians;
  const east = Math.sin(longitudeDelta) * Math.cos(latitudeEnd);
  const north = Math.cos(latitudeStart) * Math.sin(latitudeEnd)
    - Math.sin(latitudeStart) * Math.cos(latitudeEnd) * Math.cos(longitudeDelta);
  if (Math.hypot(east, north) < 1e-10) return null;
  const degrees = Math.atan2(east, north) / radians;
  return ((Math.round(degrees * 100) + 36000) % 36000) / 100;
}
