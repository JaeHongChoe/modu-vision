import type { AnnotationItem, Point } from '../../types';
import { getAnchorPoint } from '../../vendor/label-studio/getAnchorPoint';

type Vertex = [number, number];

export type PolygonEditResult = {
  updates: Pick<AnnotationItem, 'polygon' | 'points' | 'bbox'>;
  vertexIndex: number;
} | {
  error: 'minimum_points' | 'duplicate_point' | 'invalid_polygon' | 'invalid_vertex';
};

// Numerical tolerance only; no image dimensions, viewport scale or annotation-size policy.
const EPSILON = 1e-9;
const finiteCursor = (point: Point): boolean =>
  !!point && Number.isFinite(point.x) && Number.isFinite(point.y);
const finiteVertex = (point: Vertex): boolean =>
  Array.isArray(point) && point.length === 2 && point.every(Number.isFinite);
const sameVertex = (a: Vertex, b: Vertex): boolean =>
  Math.hypot(a[0] - b[0], a[1] - b[1]) < EPSILON;

function validPolygon(points: Vertex[] | undefined): points is Vertex[] {
  if (!Array.isArray(points) || points.length < 3 || !points.every(finiteVertex)) return false;
  if (points.some((point, index) => points.slice(index + 1).some(other => sameVertex(point, other)))) return false;
  // Translate to the first vertex to avoid cancellation when an image has a large coordinate offset.
  const [originX, originY] = points[0];
  let twiceArea = 0;
  for (let index = 0; index < points.length; index++) {
    const point = points[index];
    const next = points[(index + 1) % points.length];
    twiceArea += (point[0] - originX) * (next[1] - originY) - (next[0] - originX) * (point[1] - originY);
  }
  return Number.isFinite(twiceArea) && Math.abs(twiceArea) > EPSILON;
}

function sourcePolygon(annotation: AnnotationItem): Vertex[] | null {
  const points = annotation.polygon || annotation.points;
  return annotation.type === 'polygon' && validPolygon(points) ? points : null;
}

function validIndex(index: number, points: Vertex[]): boolean {
  return Number.isInteger(index) && index >= 0 && index < points.length;
}

// The pinned upstream function projects to an infinite line. Keep local segment bounds
// and the zero-length guard here so its arithmetic remains independently attributable.
function projectToSegment(start: Vertex, end: Vertex, cursor: Point): Point | null {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return {x: start[0], y: start[1]};
  if (!Number.isFinite(lengthSquared)) return null;
  const t = ((cursor.x - start[0]) * dx + (cursor.y - start[1]) * dy) / lengthSquared;
  if (!Number.isFinite(t)) return null;
  if (t <= 0) return {x: start[0], y: start[1]};
  if (t >= 1) return {x: end[0], y: end[1]};
  const [x, y] = getAnchorPoint({flattenedPoints: [...start, ...end], cursorX: cursor.x, cursorY: cursor.y});
  return Number.isFinite(x) && Number.isFinite(y) ? {x, y} : null;
}

export function findClosestPolygonEdge(
  points: Array<[number, number]>, cursor: Point, maxDistance: number,
): {edgeIndex: number; point: Point} | null {
  if (!Array.isArray(points) || points.length < 2 || !points.every(finiteVertex)
    || !finiteCursor(cursor) || !Number.isFinite(maxDistance) || maxDistance < 0) return null;
  let closest: {edgeIndex: number; point: Point} | null = null;
  let closestDistance = maxDistance;
  for (let edgeIndex = 0; edgeIndex < points.length; edgeIndex++) {
    const point = projectToSegment(points[edgeIndex], points[(edgeIndex + 1) % points.length], cursor);
    if (!point) continue;
    const distance = Math.hypot(cursor.x - point.x, cursor.y - point.y);
    if (distance <= maxDistance && (!closest || distance < closestDistance)) {
      closest = {edgeIndex, point};
      closestDistance = distance;
    }
  }
  return closest;
}

function geometryUpdate(points: Vertex[], vertexIndex: number): PolygonEditResult {
  if (!validPolygon(points)) return {error: 'invalid_polygon'};
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  for (const [x, y] of points) {
    xmin = Math.min(xmin, x); ymin = Math.min(ymin, y);
    xmax = Math.max(xmax, x); ymax = Math.max(ymax, y);
  }
  return {vertexIndex, updates: {
    polygon: points.map(([x, y]) => [x, y]),
    points: points.map(([x, y]) => [x, y]),
    bbox: [xmin, ymin, xmax, ymax],
  }};
}

export function insertPolygonVertex(annotation: AnnotationItem, edgeIndex: number, cursor: Point): PolygonEditResult {
  const points = sourcePolygon(annotation);
  if (!points) return {error: 'invalid_polygon'};
  if (!validIndex(edgeIndex, points)) return {error: 'invalid_vertex'};
  if (!finiteCursor(cursor)) return {error: 'invalid_polygon'};
  const projected = projectToSegment(points[edgeIndex], points[(edgeIndex + 1) % points.length], cursor);
  if (!projected) return {error: 'invalid_polygon'};
  const vertex: Vertex = [projected.x, projected.y];
  if (points.some(point => sameVertex(point, vertex))) return {error: 'duplicate_point'};
  const next = points.slice();
  next.splice(edgeIndex + 1, 0, vertex);
  return geometryUpdate(next, edgeIndex + 1);
}

export function deletePolygonVertex(annotation: AnnotationItem, index: number): PolygonEditResult {
  const points = sourcePolygon(annotation);
  if (!points) return {error: 'invalid_polygon'};
  if (!validIndex(index, points)) return {error: 'invalid_vertex'};
  if (points.length <= 3) return {error: 'minimum_points'};
  const next = points.filter((_, vertexIndex) => vertexIndex !== index);
  return geometryUpdate(next, Math.min(index, next.length - 1));
}

export function movePolygonVertex(annotation: AnnotationItem, index: number, cursor: Point): PolygonEditResult {
  const points = sourcePolygon(annotation);
  if (!points) return {error: 'invalid_polygon'};
  if (!validIndex(index, points)) return {error: 'invalid_vertex'};
  if (!finiteCursor(cursor)) return {error: 'invalid_polygon'};
  const vertex: Vertex = [cursor.x, cursor.y];
  if (points.some((point, vertexIndex) => vertexIndex !== index && sameVertex(point, vertex))) {
    return {error: 'duplicate_point'};
  }
  return geometryUpdate(points.map((point, vertexIndex) => vertexIndex === index ? vertex : point), index);
}
