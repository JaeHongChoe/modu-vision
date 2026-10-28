/** Shared calls behind the screens, automatic progression and approved assistant tasks. */
import { api } from '../api';
import { flowEvaluation } from '../flowEvaluation';
import { announceFlowSaved, requestAutoRun } from '../../stores/autoRunChannel';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useEvaluationStore } from '../../stores/useEvaluationStore';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import type { ActResult, AutoAction } from '../../components/wizard/autoProgress';
import type { FlowEvaluation, FlowEvaluationCohort } from '../flowEvaluationTypes';
import type { VisionTask } from '../../types';

type Fence = () => void;
const unrestricted: Fence = () => {};
export function defaultAppSplit(trainRatio: number | undefined): { train: number; val: number; test: number } {
  const train = Math.round((trainRatio ?? .8) * 100) || 70;
  const val = Math.round((100 - train) / 2);
  return { train, val, test: 100 - train - val };
}
export async function applyAppSplit(train: number, val: number, test: number): Promise<string | null> {
  await useDatasetStore.getState().applySplit(train, val, test);
  return useDatasetStore.getState().splitError || null;
}
export async function evaluateAppModel(jobId: string | undefined, folder: string, task: VisionTask, isCurrent?: () => boolean): Promise<void> {
  await useEvaluationStore.getState().loadEvaluation(jobId, {folderPath: folder, task, ...(isCurrent ? {isCurrent} : {})});
}
export async function saveSingleModelFlow(jobId: string, folder: string, task: VisionTask, check: Fence = unrestricted, onDispatch?: () => void, options?: {expectedVersionId?: string | null; onSaved?: (saved: {version_id: string; pipeline_id: string}) => Promise<void>}) {
  check();
  const pipeline = task === 'detection' ? await api.flowchart.getSingleDetectionTemplate(jobId)
    : await api.flowchart.getSingleSegmentationTemplate(jobId, task);
  check();
  const verified = await api.flowchart.verifyModels({source_dataset_path: folder, models: [{job_id: jobId, task}]});
  check();
  if (verified.verified_job_ids && !verified.verified_job_ids.includes(jobId)) throw new Error('현재 원본에 속한 모델을 확인하지 못했습니다.');
  onDispatch?.();
  const saved = options && 'expectedVersionId' in options
    ? await api.flowchart.savePipeline(pipeline, task, folder, {expectedVersionId: options.expectedVersionId})
    : await api.flowchart.savePipeline(pipeline, task, folder);
  await options?.onSaved?.(saved);
  check();
  await useFlowchartStore.getState().loadPipeline(true, task, folder);
  check();
  announceFlowSaved();
  return saved;
}
export async function evaluateSavedFlow(versionId: string, name: string, check: Fence = unrestricted, hooks?: {cohort?: (cohort: FlowEvaluationCohort) => Promise<void>; evaluated?: (evaluated: FlowEvaluation) => Promise<void>}) {
  check();
  const cohort = await flowEvaluation.freeze(versionId, name, 'folder-labels-v1');
  await hooks?.cohort?.(cohort);
  check();
  const evaluated = await flowEvaluation.run(versionId, cohort.cohort_id);
  await hooks?.evaluated?.(evaluated);
  check();
  return {cohort, evaluated};
}
/** Existing automatic progression keeps its return/stop contract while using the common calls. */
export async function performAutoAppAction(action: AutoAction): Promise<ActResult> {
  const project = useProjectStore.getState(), data = useDatasetStore.getState(), task = project.task, folder = data.folderPath;
  if (action === 'split') {
    const split = defaultAppSplit(data.trainRatio);
    return applyAppSplit(split.train / 100, split.val / 100, split.test / 100);
  }
  if (action === 'train') return requestAutoRun('train');
  if (action === 'inspect') return requestAutoRun('inspect');
  if (action === 'evaluate') {
    await evaluateAppModel(undefined, folder, task);
    return useEvaluationStore.getState().errorMessage || null;
  }
  if (action === 'saveFlow') {
    const training = useTrainingStore.getState();
    const jobId = useEvaluationStore.getState().jobId || (training.isCurrentData ? training.jobId : null);
    if (!jobId) return {back: 3, reason: '검사 흐름에 넣을 현재 데이터의 모델이 없습니다'};
    await saveSingleModelFlow(jobId, folder, task);
    return null;
  }
  const versionId = useFlowchartStore.getState().baseVersionId;
  if (!versionId) return {person: '저장한 검사 흐름 버전을 찾지 못했습니다. 5단계에서 저장 버전을 확인하세요.', step: 5, target: 'flow-area-edit'};
  const {cohort, evaluated} = await evaluateSavedFlow(versionId, '자동 진행 시험 이미지');
  if (!evaluated.coverage.known) return {person: '정답이 확인된 시험 이미지가 없어 검사 흐름을 평가하지 못했습니다. 시험 이미지의 라벨을 정답으로 확정하세요(정답 확인은 사람이 합니다).', step: 5, target: 'flow-area-evaluate',
    confirm: {kind: 'truth', cohortId: cohort.cohort_id, versionId, images: cohort.count}};
  return null;
}
