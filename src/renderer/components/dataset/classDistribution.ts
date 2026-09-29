import type { VisionTask } from '../../types';

export function classDistributionStats(
  task: VisionTask,
  classes: Record<string, number>,
  imageCount: number,
) {
  const objectCount = Object.values(classes).reduce((total, count) => total + Math.max(0, count), 0);
  const countUnit = task === 'detection' ? 'objects' : 'images';
  const denominator = countUnit === 'objects' ? objectCount : imageCount;

  return {
    imageCount,
    objectCount,
    countUnit,
    sharePercent: (count: number) => denominator > 0 ? Math.round((count / denominator) * 100) : 0,
  };
}
