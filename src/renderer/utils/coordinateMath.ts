/**
 * src/renderer/utils/coordinateMath.ts
 * Precision coordinate transforms, boundary clamping, and 8-handle hit testing.
 */

import { BBox, HandlePosition, HandleType, Point, ViewTransform } from '../types';

export function clientToViewport(clientX: number, clientY: number, canvasRect: DOMRect): Point {
  return {
    x: clientX - canvasRect.left,
    y: clientY - canvasRect.top,
  };
}

export function viewportToImage(pt: Point, transform: ViewTransform): Point {
  return {
    x: (pt.x - transform.offsetX) / transform.scale,
    y: (pt.y - transform.offsetY) / transform.scale,
  };
}

export function imageToViewport(pt: Point, transform: ViewTransform): Point {
  return {
    x: pt.x * transform.scale + transform.offsetX,
    y: pt.y * transform.scale + transform.offsetY,
  };
}

export function clampPointToImage(pt: Point, imgW: number, imgH: number): Point {
  return {
    x: Math.max(0, Math.min(imgW, pt.x)),
    y: Math.max(0, Math.min(imgH, pt.y)),
  };
}

export function sanitizeBBox(
  p1: Point,
  p2: Point,
  imgW: number,
  imgH: number,
  minDimension: number = 1
): BBox | null {
  if (!Number.isFinite(p1.x) || !Number.isFinite(p1.y) || !Number.isFinite(p2.x) || !Number.isFinite(p2.y)) {
    return null;
  }
  const xmin = Math.max(0, Math.min(imgW, Math.min(p1.x, p2.x)));
  const xmax = Math.max(0, Math.min(imgW, Math.max(p1.x, p2.x)));
  const ymin = Math.max(0, Math.min(imgH, Math.min(p1.y, p2.y)));
  const ymax = Math.max(0, Math.min(imgH, Math.max(p1.y, p2.y)));

  if (xmax - xmin < minDimension || ymax - ymin < minDimension) {
    return null;
  }
  return { xmin, ymin, xmax, ymax };
}

export function calculateSmoothZoomAtPoint(
  cursorViewport: Point,
  currentTransform: ViewTransform,
  factor: number,
  minScale: number = 0.01,
  maxScale: number = 40.0
): ViewTransform {
  const currentScale = Number.isFinite(currentTransform.scale) && currentTransform.scale > 0
    ? currentTransform.scale
    : 1.0;
  const nextScale = Math.max(minScale, Math.min(maxScale, currentScale * factor));

  if (Math.abs(nextScale - currentScale) < 1e-6) {
    return { ...currentTransform, scale: nextScale };
  }

  const ratio = nextScale / currentScale;
  const nextOffsetX = cursorViewport.x - (cursorViewport.x - currentTransform.offsetX) * ratio;
  const nextOffsetY = cursorViewport.y - (cursorViewport.y - currentTransform.offsetY) * ratio;

  return {
    scale: nextScale,
    offsetX: Number.isFinite(nextOffsetX) ? nextOffsetX : currentTransform.offsetX,
    offsetY: Number.isFinite(nextOffsetY) ? nextOffsetY : currentTransform.offsetY,
  };
}

export function calculateZoomAtPoint(
  cursorViewport: Point,
  currentTransform: ViewTransform,
  zoomDelta: number,
  minScale: number = 0.01,
  maxScale: number = 40.0
): ViewTransform {
  const zoomFactor = 1.15;
  const multiplier = zoomDelta > 0 ? zoomFactor : 1 / zoomFactor;
  return calculateSmoothZoomAtPoint(cursorViewport, currentTransform, multiplier, minScale, maxScale);
}

export function calculateCenterZoom(
  currentTransform: ViewTransform,
  factor: number,
  viewW: number,
  viewH: number,
  minScale: number = 0.01,
  maxScale: number = 40.0
): ViewTransform {
  const center: Point = { x: viewW / 2, y: viewH / 2 };
  return calculateSmoothZoomAtPoint(center, currentTransform, factor, minScale, maxScale);
}

export function calculateFitToScreen(
  viewW: number,
  viewH: number,
  imgW: number,
  imgH: number,
  padding: number = 32
): ViewTransform {
  if (imgW <= 0 || imgH <= 0 || viewW <= 0 || viewH <= 0) {
    return { scale: 1, offsetX: 0, offsetY: 0 };
  }
  const availW = Math.max(viewW - padding * 2, 20);
  const availH = Math.max(viewH - padding * 2, 20);
  const scale = Math.max(0.01, Math.min(availW / imgW, availH / imgH, 2.5));
  const offsetX = (viewW - imgW * scale) / 2;
  const offsetY = (viewH - imgH * scale) / 2;
  return { scale, offsetX, offsetY };
}

export function calculateActualSize(viewW: number, viewH: number, imgW: number, imgH: number): ViewTransform {
  return {
    scale: 1.0,
    offsetX: (viewW - imgW) / 2,
    offsetY: (viewH - imgH) / 2,
  };
}

export function getBBoxHandles(bbox: BBox, transform: ViewTransform): HandlePosition[] {
  const pMin = imageToViewport({ x: bbox.xmin, y: bbox.ymin }, transform);
  const pMax = imageToViewport({ x: bbox.xmax, y: bbox.ymax }, transform);
  const pMidX = (pMin.x + pMax.x) / 2;
  const pMidY = (pMin.y + pMax.y) / 2;

  return [
    { type: 'nw', x: pMin.x, y: pMin.y, cursor: 'nwse-resize' },
    { type: 'n',  x: pMidX,  y: pMin.y, cursor: 'ns-resize' },
    { type: 'ne', x: pMax.x, y: pMin.y, cursor: 'nesw-resize' },
    { type: 'e',  x: pMax.x, y: pMidY,  cursor: 'ew-resize' },
    { type: 'se', x: pMax.x, y: pMax.y, cursor: 'nwse-resize' },
    { type: 's',  x: pMidX,  y: pMax.y, cursor: 'ns-resize' },
    { type: 'sw', x: pMin.x, y: pMax.y, cursor: 'nesw-resize' },
    { type: 'w',  x: pMin.x, y: pMidY,  cursor: 'ew-resize' },
  ];
}

export function hitTestHandle(
  cursorViewport: Point,
  bbox: BBox,
  transform: ViewTransform,
  handleRadius: number = 6
): HandlePosition | null {
  const handles = getBBoxHandles(bbox, transform);
  for (const h of handles) {
    const dx = cursorViewport.x - h.x;
    const dy = cursorViewport.y - h.y;
    if (Math.hypot(dx, dy) <= handleRadius + 2) {
      return h;
    }
  }
  return null;
}

export function hitTestBBox(cursorImg: Point, bbox: BBox): boolean {
  return (
    cursorImg.x >= bbox.xmin &&
    cursorImg.x <= bbox.xmax &&
    cursorImg.y >= bbox.ymin &&
    cursorImg.y <= bbox.ymax
  );
}

export function resizeBBoxWithHandle(
  originalBBox: BBox,
  handle: HandleType,
  currentImgPoint: Point,
  imgW: number,
  imgH: number
): BBox {
  const pt = clampPointToImage(currentImgPoint, imgW, imgH);
  let { xmin, ymin, xmax, ymax } = originalBBox;

  switch (handle) {
    case 'nw': xmin = pt.x; ymin = pt.y; break;
    case 'n':  ymin = pt.y; break;
    case 'ne': xmax = pt.x; ymin = pt.y; break;
    case 'e':  xmax = pt.x; break;
    case 'se': xmax = pt.x; ymax = pt.y; break;
    case 's':  ymax = pt.y; break;
    case 'sw': xmin = pt.x; ymax = pt.y; break;
    case 'w':  xmin = pt.x; break;
  }

  return {
    xmin: Math.max(0, Math.min(xmin, xmax)),
    ymin: Math.max(0, Math.min(ymin, ymax)),
    xmax: Math.min(imgW, Math.max(xmin, xmax)),
    ymax: Math.min(imgH, Math.max(ymin, ymax)),
  };
}

export function moveBBox(originalBBox: BBox, deltaImg: Point, imgW: number, imgH: number): BBox {
  const w = originalBBox.xmax - originalBBox.xmin;
  const h = originalBBox.ymax - originalBBox.ymin;

  let newXmin = originalBBox.xmin + deltaImg.x;
  let newYmin = originalBBox.ymin + deltaImg.y;

  newXmin = Math.max(0, Math.min(imgW - w, newXmin));
  newYmin = Math.max(0, Math.min(imgH - h, newYmin));

  return {
    xmin: newXmin,
    ymin: newYmin,
    xmax: newXmin + w,
    ymax: newYmin + h,
  };
}

export function pointInPolygon(pt: Point, points: Point[]): boolean {
  if (points.length < 3) return false;
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const xi = points[i].x, yi = points[i].y;
    const xj = points[j].x, yj = points[j].y;
    const intersect = yi > pt.y !== yj > pt.y && pt.x < ((xj - xi) * (pt.y - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

export function hitTestPolygonVertex(
  cursorViewport: Point,
  points: Point[],
  transform: ViewTransform,
  radius: number = 8
): number | null {
  for (let i = 0; i < points.length; i++) {
    const vp = imageToViewport(points[i], transform);
    if (Math.hypot(cursorViewport.x - vp.x, cursorViewport.y - vp.y) <= radius) {
      return i;
    }
  }
  return null;
}

/**
 * Hit test for a rotated bounding box.
 * Transforms point into box's local coordinate space and checks against [-w/2, w/2] x [-h/2, h/2].
 */
export function hitTestRotatedBBox(
  pt: Point,
  center: Point,
  width: number,
  height: number,
  angle: number
): boolean {
  const dx = pt.x - center.x;
  const dy = pt.y - center.y;
  const rad = -((angle || 0) * Math.PI) / 180;
  const localX = dx * Math.cos(rad) - dy * Math.sin(rad);
  const localY = dx * Math.sin(rad) + dy * Math.cos(rad);
  return Math.abs(localX) <= width / 2 && Math.abs(localY) <= height / 2;
}

/**
 * Hit test for the rotation knob handle located at (0, -height/2 - 20) in local space.
 */
export function hitTestRotatedHandle(
  pt: Point,
  center: Point,
  _width: number,
  height: number,
  angle: number,
  handleRadius: number = 8
): boolean {
  const dx = pt.x - center.x;
  const dy = pt.y - center.y;
  const rad = -((angle || 0) * Math.PI) / 180;
  const localX = dx * Math.cos(rad) - dy * Math.sin(rad);
  const localY = dx * Math.sin(rad) + dy * Math.cos(rad);

  const knobX = 0;
  const knobY = -height / 2 - 20;

  return Math.hypot(localX - knobX, localY - knobY) <= handleRadius;
}

/**
 * Calculates the 4 corner coordinates of a rotated rectangle clockwise:
 * top-left, top-right, bottom-right, bottom-left.
 */
export function calcRotatedCorners(
  center: Point,
  width: number,
  height: number,
  angle: number
): Point[] {
  const rad = ((angle || 0) * Math.PI) / 180;
  const hw = width / 2;
  const hh = height / 2;

  const localCorners: [number, number][] = [
    [-hw, -hh],
    [hw, -hh],
    [hw, hh],
    [-hw, hh],
  ];

  const cosA = Math.cos(rad);
  const sinA = Math.sin(rad);

  return localCorners.map(([lx, ly]) => ({
    x: center.x + lx * cosA - ly * sinA,
    y: center.y + lx * sinA + ly * cosA,
  }));
}
