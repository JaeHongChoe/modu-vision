import type { VisionTask } from '../../types';
import { modelChoices } from './modelTrainingOptions';

export interface TrainingStartSummary {
  model: string;
  weights: string;
  device?: string;
  settings?: string;
  /** Exact local invocation snapshot. Never send this object to an assistant provider. */
  executionSettings?: Record<string, unknown>;
}

export interface TrainingSummaryProfile { id: string; name: string; gpu_selector?: string | null }
export interface TrainingSummaryScopeInput {
  task: string; folderPath: string; datasetKey: string | null; preset: string;
  nextDevice?: string | null; nextBatchSize?: number | null;
  selectedProfileId?: string | null; profile?: TrainingSummaryProfile;
  transportRevision: number; computeLoaded: boolean; computeLoading: boolean; computeLoadError: string | null;
}

/** The existing task authority includes project, API identity and context generation. Settings and the worker are
 * included here so a dialog never reads a selection published before one of them changed. */
export function trainingSummaryScope(authority: string, input: TrainingSummaryScopeInput): string {
  return JSON.stringify([authority, input.task, input.folderPath, input.datasetKey, input.preset,
    input.nextDevice ?? null, input.nextBatchSize ?? null, input.selectedProfileId ?? null, input.profile ?? null,
    input.transportRevision, input.computeLoaded, input.computeLoading, input.computeLoadError]);
}

let published: { scope: string; value: TrainingStartSummary } | null = null;
const listeners = new Set<() => void>();
const notify = () => { for (const listener of listeners) listener(); };

/** A mounted step-3 screen owns its current selection; an old effect cannot clear a newer publication. */
export function publishTrainingSummary(scope: string, value: TrainingStartSummary): () => void {
  const record = { scope, value: JSON.parse(JSON.stringify(value)) as TrainingStartSummary };
  published = record; notify();
  return () => { if (published === record) { published = null; notify(); } };
}

export function currentTrainingSummary(scope: string): TrainingStartSummary | null {
  return published?.scope === scope ? JSON.parse(JSON.stringify(published.value)) as TrainingStartSummary : null;
}

export function subscribeTrainingSummary(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function describeTrainingSummary(input: {
  task: VisionTask; model: string; checkpoint?: string; warmStartJobId?: string;
  resume?: { checkpoint_path: string; device: string } | null; options?: Record<string, unknown>;
}): TrainingStartSummary {
  const name = (file: string) => file.split(/[\\/]/).pop() || '지정 파일';
  const weights = input.resume ? `중단 작업 ${name(input.resume.checkpoint_path)}에서 이어 학습`
    : input.warmStartJobId ? `완료 모델 …${input.warmStartJobId.slice(-8)}에서 이어 학습`
      : input.checkpoint?.trim() ? `지정 파일 ${name(input.checkpoint.trim())}` : '기본 가중치';
  const epochs = input.options?.epochs;
  return {
    model: modelChoices[input.task].find(choice => choice.value === input.model)?.label || input.model,
    weights: `${weights} · 준비 확인 필요`,
    ...(input.resume ? { device: input.resume.device.toUpperCase() } : {}),
    ...(typeof epochs === 'number' && Number.isFinite(epochs) ? { settings: `학습 ${epochs}회` } : {}),
  };
}

export function trainingSummaryLine(input: {
  task?: string | null; preset?: string; device?: string | null;
  selectedProfileId?: string | null; profile?: TrainingSummaryProfile; computeLoaded?: boolean;
  live?: TrainingStartSummary | null;
}): string {
  const choices = modelChoices[input.task as VisionTask];
  const model = input.live?.model || choices?.[0]?.label || '모델 확인 필요';
  const preset = input.preset === 'fast' ? '빠른 시험 학습' : input.preset === 'precision' ? '정밀 후보 학습' : '확인 필요';
  const device = input.computeLoaded === false ? '실행 위치 확인 필요'
    : input.selectedProfileId ? input.profile ? `서버 ${input.profile.name}${input.profile.gpu_selector ? ` / GPU ${input.profile.gpu_selector}` : ' / 장치 확인 필요'}` : '서버 설정 확인 필요'
      : input.live?.device || (!input.device || input.device === 'auto' ? '자동 감지' : input.device.toUpperCase());
  return `${input.live ? '3단계 현재 선택' : '3단계 기본 선택'}: 모델 ${model} · 프리셋 ${preset} · 장치 ${device} · `
    + `가중치 ${input.live?.weights || '기본 가중치 · 준비 확인 필요'}${input.live?.settings ? ` · ${input.live.settings}` : ''}`;
}
