interface Storage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}
export const evaluationTasks = {
  classification: '분류', patch_classification: '패치 분류', detection: '객체 검출',
  segmentation: '영역 분할', anomaly: '이상 탐지', ocr: 'OCR',
  rotated_detection: '회전 객체 검출', rotation: '정방향 보정', enhancement: '이미지 개선', defect_gan: '결함 생성',
};
export interface EvaluationView {
  task: string;
  taskExplicit: boolean;
  labelsetId: string;
  group: 'product' | 'lot' | 'ground_truth';
}
function read(storage: Storage, key: string): unknown {
  try { const raw = storage.getItem(key); return raw ? JSON.parse(raw) : null; }
  catch { return null; }
}
export function rememberEvaluationPreference(storage: Storage, key: string, value: unknown): void {
  try { storage.setItem(key, JSON.stringify(value)); } catch { /* Viewing remains available without preference storage. */ }
}
export function readEvaluationView(storage: Storage, key: string, task: string): EvaluationView {
  const fallback: EvaluationView = { task, taskExplicit: false, labelsetId: '', group: 'product' };
  const saved = read(storage, key) as Partial<EvaluationView> & { version?: unknown } | null;
  if (!saved || saved.version !== 1 || !saved.task || !Object.hasOwn(evaluationTasks, saved.task)
      || typeof saved.labelsetId !== 'string' || saved.labelsetId.length > 128
      || !['product', 'lot', 'ground_truth'].includes(saved.group || '')
      || typeof saved.taskExplicit !== 'boolean') return fallback;
  return { task: saved.taskExplicit ? saved.task : task, taskExplicit: saved.taskExplicit,
    labelsetId: saved.labelsetId, group: saved.group! };
}
export function readEvaluationOpen(storage: Storage, key: string): boolean {
  const saved = read(storage, key) as { version?: unknown; opened?: unknown } | null;
  return saved?.version === 1 && saved.opened === true;
}
export function readEvaluationChoice(storage: Storage, key: string): string | null {
  const saved = read(storage, key) as { version?: unknown; evaluation_id?: unknown } | null;
  return saved?.version === 1 && typeof saved.evaluation_id === 'string'
    && /^[A-Za-z0-9_-]{1,128}$/.test(saved.evaluation_id) ? saved.evaluation_id : null;
}
