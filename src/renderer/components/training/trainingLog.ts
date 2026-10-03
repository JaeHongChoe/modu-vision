// The training screen's log (app-flow QA finding: progress was shown but no log): what happened to the current job, in
// order, from what the training store already holds: the job, each finished epoch's losses, and how the job ended.
import type { LossPoint } from '../../stores/useTrainingStore';

export type TrainingLogInput = {
  jobId: string | null; status: string; jobPhase: string | null; totalEpochs: number; lossHistory: LossPoint[];
  startError: string | null; jobStatusError: string | null; stopError: string | null; bestMetric: number | null;
};

const ENDED: Record<string, string> = {
  completed: '학습 완료', aborted: '학습 중단됨', cancelled: '학습 취소됨', stopped: '학습 중단됨', failed: '학습 실패',
  interrupted: '앱 종료로 중단됨', disconnected: '작업과 연결이 끊김', unverified: '상태를 확인하지 못함',
};

/** The log lines of the current job, oldest first; empty before any job. */
export function trainingLogLines(input: TrainingLogInput): string[] {
  const lines: string[] = [];
  if (input.startError && !input.jobId) return [`시작 실패 · ${input.startError}`];
  if (!input.jobId) return lines;
  lines.push(`작업 ${input.jobId} 시작`);
  if (input.jobPhase && !['running', 'completed', 'failed', 'aborted', 'cancelled', 'stopped'].includes(input.jobPhase)) lines.push(`단계 · ${input.jobPhase}`);
  const total = input.totalEpochs > 0 ? `/${input.totalEpochs}` : '';
  for (const point of input.lossHistory) {
    const val = point.valLoss !== null && Number.isFinite(point.valLoss) ? ` · 검증 손실 ${point.valLoss.toFixed(4)}` : '';
    lines.push(`epoch ${point.epoch}${total} · 학습 손실 ${point.trainLoss.toFixed(4)}${val}`);
  }
  const ended = ENDED[input.status];
  if (ended) {
    const best = input.status === 'completed' && input.bestMetric !== null ? ` · 저장 지표 ${input.bestMetric.toFixed(4)}` : '';
    const reason = input.jobStatusError || input.startError;
    lines.push(`${ended}${best}${input.status !== 'completed' && reason ? ` · ${reason}` : ''}`);
  }
  if (input.stopError) lines.push(`중단 요청 확인 실패 · ${input.stopError}`);
  return lines;
}
