import type { AnnotationItem } from '../../types';

type TargetShape = 'bbox' | 'polygon' | 'mask' | 'rotated_bbox';

export function isShapeConvertible(annotation: AnnotationItem | undefined): boolean {
  if (!annotation || annotation.type === 'tag') return false;
  return Boolean(annotation.bbox || annotation.polygon || annotation.points || annotation.rotated_bbox ||
    (annotation.type === 'brush_mask' && annotation.mask_rle));
}

export function applyConvertedShape(annotation: AnnotationItem, targetType: TargetShape, converted: any): AnnotationItem | null {
  if (targetType === 'mask') {
    if (typeof converted?.mask_rle !== 'string' || !converted.mask_rle.startsWith('data:image/png;base64,')) return null;
    return {
      ...annotation,
      type: 'brush_mask',
      mask_rle: converted.mask_rle,
      bbox: undefined,
      polygon: undefined,
      points: undefined,
      rotated_bbox: undefined,
    };
  }
  if (targetType === 'bbox') {
    if (!Array.isArray(converted?.bbox) || converted.bbox.length !== 4) return null;
    return {
      ...annotation,
      type: 'bbox',
      bbox: converted.bbox,
      polygon: undefined,
      points: undefined,
      rotated_bbox: undefined,
      mask_rle: undefined,
    };
  }
  if (targetType === 'polygon') {
    const poly = converted?.polygon;
    if (!Array.isArray(poly) || poly.length < 3) return null;
    const xs = poly.map((point: [number, number]) => point[0]);
    const ys = poly.map((point: [number, number]) => point[1]);
    return {
      ...annotation,
      type: 'polygon',
      polygon: poly,
      points: poly,
      bbox: converted.bbox || [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
      rotated_bbox: undefined,
      mask_rle: undefined,
    };
  }
  const rbox = converted?.rotated_bbox || (converted?.center && converted?.size
    ? [...converted.center, ...converted.size, converted.angle || 0] : null);
  if (!Array.isArray(rbox) || rbox.length !== 5) return null;
  const rotated = rbox as [number, number, number, number, number];
  return {
    ...annotation,
    type: 'rotated_bbox',
    rotated_bbox: rotated,
    bbox: converted.bbox || [
      rotated[0] - rotated[2] / 2,
      rotated[1] - rotated[3] / 2,
      rotated[0] + rotated[2] / 2,
      rotated[1] + rotated[3] / 2,
    ],
    polygon: undefined,
    points: undefined,
    mask_rle: undefined,
  };
}
