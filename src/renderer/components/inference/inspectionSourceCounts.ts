/** Inspection counts describe the image library, never task-specific training eligibility. */
export type InspectionSourceCounts = { all: number; train: number; val: number; test: number };
export type InspectionCountState =
  | { key: string; state: 'loading' }
  | { key: string; state: 'ready'; counts: InspectionSourceCounts }
  | { key: string; state: 'error'; message: string };
export interface InspectionCountContext {
  sourceReady: boolean; sourceKey: string; folderPath: string; task: string; projectDir?: string | null;
  project: { id: string; project_dir: string; source_dataset_dir: string | null; task: string; active_labelset_id?: string } | null;
  apiScope: string;
}
export function inspectionCountKey(context: InspectionCountContext): string | null {
  const project = context.project;
  if (!context.sourceReady || !project || project.project_dir !== context.projectDir
    || project.source_dataset_dir !== context.folderPath || project.task !== context.task) return null;
  return JSON.stringify([context.sourceKey, project.id, project.project_dir, project.source_dataset_dir,
    project.task, project.active_labelset_id || 'default', context.apiScope]);
}
export function inspectionCounts(value: unknown, labelset: string): InspectionSourceCounts {
  const data = value as {total?: unknown; labelset_id?: unknown; assignments?: Record<string, {count?: unknown}>} | null;
  if (!data || data.labelset_id !== labelset) throw new Error('현재 라벨 세트의 이미지 수를 다시 확인하세요.');
  const count = (value: unknown): number => {
    if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0)
      throw new Error('검사할 이미지 수가 유효하지 않습니다. 다시 확인하세요.');
    return value;
  };
  const all = count(data.total);
  const partitions = Object.fromEntries(['train', 'val', 'test', 'not_used', 'not_split']
    .map(name => [name, count(data.assignments?.[name]?.count)]));
  if (Object.values(partitions).reduce((total, value) => total + value, 0) !== all)
    throw new Error('전체 이미지 수와 분할별 이미지 수가 다릅니다. 다시 확인하세요.');
  return {all, train: partitions.train, val: partitions.val, test: partitions.test};
}
export function startInspectionCountRead(options: {
  key: string; labelset: string; isCurrent: () => boolean;
  read: (signal: AbortSignal) => Promise<unknown>; publish: (value: InspectionCountState) => void;
}): {cancel: () => void; settled: Promise<void>} {
  const controller = new AbortController(), deadline = Date.now() + 10_000;
  let active = true, rejectCancelled: (error: Error) => void = () => {};
  const cancelled = new Promise<never>((_, reject) => { rejectCancelled = reject; });
  const timer = setTimeout(() => {
    controller.abort(); rejectCancelled(new Error('검사할 이미지 수 확인 시간이 초과되었습니다. 다시 시도하세요.'));
  }, 10_000);
  const isCurrent = () => active && options.isCurrent();
  options.publish({key: options.key, state: 'loading'});
  const settled = (async () => {
    try {
      const value = await Promise.race([options.read(controller.signal), cancelled]);
      if (!isCurrent()) return;
      if (Date.now() > deadline) throw new Error('검사할 이미지 수 확인 시간이 초과되었습니다. 다시 시도하세요.');
      const counts = inspectionCounts(value, options.labelset);
      if (isCurrent()) options.publish({key: options.key, state: 'ready', counts});
    } catch {
      if (isCurrent()) options.publish({key: options.key, state: 'error',
        message: '검사할 이미지 수를 확인하지 못했습니다. 다시 시도하세요.'});
    } finally { clearTimeout(timer); }
  })();
  return {settled, cancel: () => {active = false; controller.abort(); rejectCancelled(new Error('Image count read cancelled'));}};
}
