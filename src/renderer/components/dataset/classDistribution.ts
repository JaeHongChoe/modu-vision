import type { VisionTask } from '../../types';

export function classDistributionStats(
  task: VisionTask,
  classes: Record<string, number>,
  imageCount: number,
  unit?: 'images' | 'objects',
) {
  const objectCount = Object.values(classes).reduce((total, count) => total + Math.max(0, count), 0);
  const countUnit = unit ?? (task === 'detection' ? 'objects' : 'images');
  const denominator = countUnit === 'objects' ? objectCount : imageCount;

  return {
    imageCount,
    objectCount,
    countUnit,
    sharePercent: (count: number) => denominator > 0 ? Math.round((count / denominator) * 100) : 0,
  };
}
