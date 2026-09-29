import type { VisionTask } from '../types';

/** The import response is authoritative once it has identified the source layout. */
export function isSplitUnavailable(task: VisionTask, splitSupported: boolean | null): boolean {
  if (splitSupported !== null) return !splitSupported;
  return task === 'detection' || task === 'anomaly';
}
