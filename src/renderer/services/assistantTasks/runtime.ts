/** The task supervisor's local adapter. No credentials, pixels, arbitrary code, truth/adoption or deployment actions. */
import { api, request, getApiPersistenceIdentity, getProjectContext, getProjectContextGeneration } from '../api';
import { host } from '../hostAdapter';
import { flowEvaluation, type FlowEvaluation, type ImageTruth } from '../flowEvaluation';
import { foundationLabelingApi } from '../foundationLabelingApi';
import { currentAssistContext } from '../../stores/assistContext';
import { useProjectStore } from '../../stores/useProjectStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useTrainingStore } from '../../stores/useTrainingStore';
import { currentEvaluationGuidanceSource, useEvaluationStore } from '../../stores/useEvaluationStore';
import { useComputeStore } from '../../stores/useComputeStore';
import { useFlowchartStore } from '../../stores/useFlowchartStore';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useInspectionRunStore } from '../../stores/useInspectionRunStore';
import { requestTaskAutoRun, taskAutoRunSettings } from '../../stores/autoRunChannel';
import { bumpWorkflowEvidence } from '../../stores/workflowEvidence';
import { focusWorkflowTarget, type WorkflowStep } from '../../components/wizard/workflowReadiness';
import { applyAppSplit, evaluateAppModel, evaluateSavedFlow, saveSingleModelFlow } from './appActions';
import { taskActionCatalog as pureTaskActionCatalog, validateTaskAction } from './catalog';
import type { TaskActionResult, TaskStep, TaskControl } from './types';
import {diagnosticError, diagnosticText} from '../assistantDiagnostics';
import {validateInputRecipe,inputPreparationScope,type FixedInputRecipe} from '../../components/training/inputPreparation';

type Control = TaskControl;
const message = (cause: unknown) => diagnosticError(cause).message;
const human = (text: string, receipt?: Record<string, unknown>, reasonCode?: TaskActionResult['reasonCode']): TaskActionResult => ({kind: 'waiting_human', reasonCode:reasonCode??(['failed','stopped','aborted','interrupted'].includes(String(receipt?.status))?'failed':'waiting_input'), message: text, receipt: receipt ? {not_started: false, ...receipt} : {not_started: true}});
const uncertain = (text = '실행 결과를 저장 기록에서 확인해야 합니다. 확인 전 같은 작업을 다시 실행하지 않습니다.', receipt?: Record<string, unknown>): TaskActionResult => ({kind: 'needs_reconciliation', reasonCode:'needs_reconciliation', message: text, receipt: {...receipt, not_started: false}});
const completed = (receipt: Record<string, unknown>): TaskActionResult => ({kind: 'completed', receipt: {...receipt, not_started: false}});
const jobs = new Set(['queued','preparing','transferring','running','stopping','syncing','cancelling']);
/** Existing result reads only. Keep diagnostic samples separate from authority/identity evidence. */
function resultDiagnostics(source: string, errors: unknown[] = [], reasons: unknown[] = [], nextAction?: unknown) {
  return {source, errors: errors.filter(value => value !== null && value !== undefined && value !== '').slice(0, 3).map(value => {
    const safe = diagnosticError(value); return {...safe, message: diagnosticText(safe.message, 240) || '작업 오류를 확인하세요.'};
  }), reasons: reasons.slice(0, 3).map(value => diagnosticText(value, 160)).filter((value): value is string => Boolean(value)),
  next_action: diagnosticText(nextAction, 200)};
}
const diagnosticMessage = (detail: ReturnType<typeof resultDiagnostics>, fallback: string) => detail.errors[0]?.message || detail.reasons[0] || fallback;
function actorAuthority() {
  const context = getProjectContext();
  return JSON.stringify([getApiPersistenceIdentity(), context?.workspace_id ?? null, context?.actor_id ?? null, context?.mode ?? null]);
}
function projectAuthority(transient = true) {
  const state = useProjectStore.getState(), project = state.project;
  return JSON.stringify([actorAuthority(), transient ? getProjectContextGeneration() : null, getProjectContext(), state.projectDir, project?.id,
    project?.registration_id ?? null, project?.workspace_id ?? null, project?.project_dir, project?.annotations_dir,
    project?.active_labelset_id || 'default', state.task]);
}
/** Opaque to the provider. Progress, active screen, counts and job status are deliberately excluded. */
export function taskRuntimeBinding(): string {
  const project = useProjectStore.getState(), data = useDatasetStore.getState(), compute = useComputeStore.getState(), training = useTrainingStore.getState();
  return JSON.stringify([projectAuthority(false), project.project?.source_dataset_dir, data.folderPath, data.datasetKey,
    compute.selectedProfileId, compute.profiles.find(row => row.id === compute.selectedProfileId) ?? null,
    training.preset, training.nextDevice, training.nextBatchSize,training.inputRecipeScope??null,training.inputRecipeSha256??null]);
}
let authorityRevision = 0;
let observedBinding: string | null = null;
function observeAuthority() {
  const next = taskRuntimeBinding();
  if (observedBinding !== null && observedBinding !== next) authorityRevision += 1;
  observedBinding = next;
}
for (const store of [useProjectStore, useDatasetStore, useTrainingStore, useComputeStore]) store.subscribe?.(observeAuthority);
/** Transient A→B→A protection is never persisted as task authority. */
export function taskRuntimeGeneration(): string {
  observeAuthority();
  return JSON.stringify([authorityRevision, getProjectContextGeneration(), useComputeStore.getState().transportRevision]);
}
function trainingStartAuthority() {
  const state = useProjectStore.getState(), data = useDatasetStore.getState(), compute = useComputeStore.getState();
  return JSON.stringify([projectAuthority(), state.project?.source_dataset_dir, data.folderPath, data.datasetKey,
    compute.selectedProfileId, compute.profiles.find(row => row.id === compute.selectedProfileId) ?? null,
    compute.transportRevision, useTrainingStore.getState().preset]);
}
export function taskActionCatalog() { return pureTaskActionCatalog(); }
function authorityCurrent(control: Control): boolean {return control.isAuthorityCurrent?.() !== false;}
function requireActive(control: Control) {
  if (!authorityCurrent(control)) throw new Error('도우미 연결·계정이 바뀌었습니다. 현재 작업에서 다시 확인하세요.');
  if (!control.isCurrent()) throw new Error('프로젝트·자료·연결 또는 설정이 바뀌었습니다. 현재 작업에서 다시 확인하세요.');
  if (control.isPaused()) throw new Error('작업 진행을 일시정지했습니다. 이미 시작된 학습·검사는 해당 화면에서 계속 확인할 수 있습니다.');
}
function requireProject(source = true) {
  const state = useProjectStore.getState(), project = state.project, data = useDatasetStore.getState(), context = getProjectContext();
  if (!project || !state.projectDir || state.projectDir !== project.project_dir || state.task !== project.task || state.isProjectBusy
    || (context && (context.project_id !== project.id || (project.workspace_id && project.workspace_id !== context.workspace_id))))
    throw new Error('현재 프로젝트 선택을 먼저 완료하세요.');
  if (source && (!data.hasSelectedFolder || data.isLoading || data.isSplitting || data.importError || data.sourceSaveError
    || !project.source_dataset_dir || project.source_dataset_dir !== data.folderPath || data.datasetKey !== `${data.folderPath}\0${state.task}`
    || data.lastImportedKey !== data.datasetKey)) throw new Error('1단계에서 현재 프로젝트의 원본 가져오기와 경로 저장을 완료하세요.');
}
function fence(control: Control, source = true) {
  requireActive(control); requireProject(source);
  const binding = taskRuntimeBinding(), generation = taskRuntimeGeneration();
  return (beforeWrite = true) => {
    if (!authorityCurrent(control) || !control.isCurrent() || binding !== taskRuntimeBinding() || generation !== taskRuntimeGeneration()) throw new Error('실행 중 프로젝트·자료·연결 또는 설정이 바뀌었습니다. 저장 기록을 확인하세요.');
    if (beforeWrite && control.isPaused()) throw new Error('작업 진행을 일시정지했습니다.');
    requireProject(source);
  };
}
async function navigate(step: WorkflowStep, control: Control) {
  requireActive(control);
  await useProjectStore.getState().setStep(step);
  requireActive(control);
  if (useProjectStore.getState().activeStep !== step) throw new Error('현재 편집을 저장한 뒤 작업 화면으로 이동하세요.');
  // React effects publish the mounted screen's guarded task handler after navigation.
  await new Promise<void>(resolve => setTimeout(resolve, 0));
  requireActive(control);
}
function localScopeReceipt() {
  const state = useProjectStore.getState(), data = useDatasetStore.getState();
  return {project_id: state.project?.id, registration_id: state.project?.registration_id ?? null,
    project_dir: state.projectDir, workspace_id: getProjectContext()?.workspace_id ?? state.project?.workspace_id ?? null,
    actor_id: getProjectContext()?.actor_id ?? null, source_dataset_path: data.folderPath, labelset_id: state.project?.active_labelset_id || 'default', task: state.task};
}
function matchesScope(scope: {project_id?: string; source_dataset_path?: string; labelset_id?: string; task?: string} | null | undefined) {
  const state = useProjectStore.getState(), data = useDatasetStore.getState();
  const identity = scope as Record<string, unknown> | null | undefined;
  if (identity && ((Object.hasOwn(identity, 'registration_id') && identity.registration_id !== (state.project?.registration_id ?? null))
    || (identity.project_dir !== undefined && identity.project_dir !== state.projectDir)
    || (identity.actor_id !== undefined && identity.actor_id !== (getProjectContext()?.actor_id ?? null))
    || (identity.workspace_id !== undefined && identity.workspace_id !== (getProjectContext()?.workspace_id ?? state.project?.workspace_id ?? null)))) return false;
  return Boolean(scope && scope.project_id === state.project?.id && scope.source_dataset_path === data.folderPath
    && scope.labelset_id === (state.project?.active_labelset_id || 'default') && scope.task === state.task);
}
function safeText(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  return value.replace(/(?:[A-Za-z]:[\\/]|\/)[^\s<>'"]+/g, '[로컬 경로]').slice(0, 400);
}
/** Semantic state only: paths, authority tokens, raw images, model arrays and backend objects never enter this view. */
export function inspectTaskScreen(): Record<string, unknown> {
  const state = useProjectStore.getState(), data = useDatasetStore.getState(), training = useTrainingStore.getState(), compute = useComputeStore.getState();
  const ui = currentAssistContext();
  return {step: state.activeStep, task: state.task, project_selected: Boolean(state.project), status: ui?.status || 'unknown',
    blocker: safeText(ui?.blocker), next_action: ui ? {label: safeText(ui.nextAction.label), step: ui.nextAction.step, target: ui.nextAction.target} : null,
    images: data.hasSelectedFolder ? data.totalImages : 0, unlabeled: data.hasSelectedFolder ? data.unlabeledImages : 0,
    split: {...data.split}, split_state: data.splitState, training: {status: training.status, preset: training.preset,
      device: compute.selectedProfileId ? 'selected_server' : training.nextDevice || 'auto', batch_size: training.nextBatchSize},
    flow_saved: Boolean(useFlowchartStore.getState().baseVersionId),
    errors: (ui?.errors || []).slice(0, 5).map(row => ({source: safeText(row.source), text: safeText(row.text)})),
    human_steps: ['초안 채택·라벨 검수', '정답 확정', '모델·검사 흐름 최종 승인', '운영 배포']};
}
async function draftAction(args: Record<string, unknown>, control: Control): Promise<TaskActionResult> {
  const check = fence(control); await navigate(2, control); check();
  const state = useProjectStore.getState(), data = useDatasetStore.getState(), scopeReceipt = localScopeReceipt();
  if (args.mode === 'phrase' || typeof args.prompt === 'string') {
    if (state.task !== 'detection' && state.task !== 'segmentation') return human('문구 초안은 객체 검출·영역 분할에서 사용할 수 있습니다. 분류 라벨은 사람이 확인하세요.');
    if (typeof args.prompt !== 'string' || !args.prompt.trim()) return human('찾을 대상 문구를 계획에 적어 주세요. 예: scratch, dent');
    const setup = await foundationLabelingApi.setup(); check();
    if (!setup.providers.grounding_dino.dependency_available) return human('문구 초안에 필요한 transformers가 없습니다. 2단계 문구 초안의 모델 준비 안내를 확인하세요.');
    if (!setup.providers.grounding_dino.ready || (state.task === 'segmentation' && !setup.providers.foundation.ready))
      return human('2단계 문구 초안에서 Grounding DINO 모델 폴더와 필요한 SAM2 모델을 먼저 준비하세요. 준비하지 않은 모델을 설치하거나 대신 실행하지 않습니다.');
    if (setup.labelset_id !== (state.project?.active_labelset_id || 'default')) return human('문구 초안의 라벨 세트가 현재 작업과 다릅니다. 2단계를 다시 여세요.');
    const listed = await api.labelSuggestions.list(); check();
    const pending = new Set(listed.suggestions.filter(row => row.candidates.length > 0 && !['accepted','rejected','dismissed'].includes(row.status)).map(row => row.image_path));
    const paths: string[] = []; let offset = 0, total = Infinity;
    while (offset < total && paths.length < 5000) {
      const page = await api.dataset.getImages({folder_path: data.folderPath, task: state.task, label_status: 'unlabeled', limit: 500, offset}); check();
      total = page.total; if (!page.items.length) break;
      paths.push(...page.items.map(row => row.file_path).filter(file => !pending.has(file))); offset += page.items.length;
    }
    if (!paths.length) return human('초안을 새로 만들 라벨 없는 이미지가 없습니다. 2단계에서 기존 초안을 먼저 검토하세요.');
    check();
    const batch = await foundationLabelingApi.startBatch({backend: state.task === 'detection' ? 'grounding_dino' : 'foundation', prompt: args.prompt.trim(), label: '',
      class_ids: Object.fromEntries(useAnnotationStore.getState().categories.filter(row => row.id > 0 && row.id <= 255).map(row => [row.name, row.id])),
      image_paths: paths.slice(0, 5000), output_geometry: state.task === 'detection' ? 'bbox' : 'polygon', threshold: typeof args.threshold === 'number' ? args.threshold : .3, text_threshold: .25,
      max_candidates: 20, device: 'cpu', labelset_id: setup.labelset_id, labelset_version: setup.labelset_version});
    const receipt = {...scopeReceipt, not_started: false, mode: 'phrase', batch_id: batch.id, total: batch.total, adopted: false};
    try {await control.checkpoint?.(receipt); check(false);} catch (cause) {return uncertain(message(cause), receipt);}
    return {kind: 'waiting_job', receipt, message: '문구 초안을 만들고 있습니다. 생성 뒤 채택·검수는 사람이 합니다.'};
  }
  const models = await api.labelSuggestions.models(); check();
  const model = models.models.find(row => row.task === state.task && row.data_state !== 'changed' && (!args.job_id || row.job_id === args.job_id));
  if (!model) return human('현재 원본에 맞는 완료 모델이 없습니다. 첫 프로젝트라면 객체 검출·영역 분할의 문구 초안으로 계획을 바꾸거나 사람이 첫 라벨을 만드세요.');
  const threshold = typeof args.threshold === 'number' ? args.threshold : .5;
  check(); const batch = await api.labelSuggestions.startBatch({job_id: model.job_id, threshold});
  const receipt = {...scopeReceipt, not_started: false, mode: 'trained', batch_id: batch.id, job_id: model.job_id, total: batch.total, threshold, adopted: false};
  try {await control.checkpoint?.(receipt); check(false);} catch (cause) {return uncertain(message(cause), receipt);}
  return {kind: 'waiting_job', receipt, message: '완료 모델로 초안을 만들고 있습니다. 채택·검수는 사람이 합니다.'};
}
export async function executeTaskAction(step: TaskStep, control: Control): Promise<TaskActionResult> {
  let dispatched = false;
  let operationReceipt: Record<string, unknown> | undefined;
  try {
    const args = validateTaskAction(step.action, step.args); requireActive(control);
    if (step.action === 'open_step') {
      await navigate(args.step as WorkflowStep, control);
      if (args.target) focusWorkflowTarget(String(args.target));
      return completed({step: args.step, target: args.target ?? null});
    }
    if (step.action === 'create_project') {
      const before = useProjectStore.getState(), actor = actorAuthority(), epoch = getProjectContextGeneration();
      if (before.isProjectBusy || useAnnotationStore.getState().isDirty || useFlowchartStore.getState().pipelineDirty || useTrainingStore.getState().isTraining || useInspectionRunStore.getState().isRunning)
        return human('진행 중인 작업과 저장하지 않은 편집을 현재 화면에서 먼저 정리하세요.');
      const previousId = before.project?.id;
      dispatched = true;
      const ok = await before.createProject({name: String(args.name), task: args.task as typeof before.task, ...(args.description ? {description: String(args.description)} : {})});
      const after = useProjectStore.getState(), project = after.project;
      if (!authorityCurrent(control) || actorAuthority() !== actor || getProjectContextGeneration() !== epoch + 1 || !ok || !project || project.id === previousId || project.name !== args.name || project.task !== args.task
        || after.projectDir !== project.project_dir || after.isProjectBusy || after.projectError || getProjectContext()?.project_id !== project.id)
        return uncertain('프로젝트 생성 결과와 현재 선택을 다시 확인해야 합니다. 생성 요청을 다시 보내지 않습니다.');
      return {...completed({project_id: project.id, registration_id: project.registration_id ?? null, task: project.task, created: true}), bindingAfter: taskRuntimeBinding()};
    }
    if (step.action === 'import_dataset') {
      requireProject(false);
      const identity = projectAuthority(), state = useProjectStore.getState(), project = state.project!;
      if (useAnnotationStore.getState().isDirty || useFlowchartStore.getState().pipelineDirty || useTrainingStore.getState().isTraining || useInspectionRunStore.getState().isRunning)
        return human('진행 중인 작업과 저장하지 않은 편집을 먼저 정리하세요.');
      const chosen = await host.selectFolder({purpose: 'dataset-source', title: '학습 자료 폴더 선택'});
      requireActive(control);
      if (identity !== projectAuthority()) return human('폴더를 고르는 동안 프로젝트 선택이 바뀌었습니다. 현재 작업에서 다시 선택하세요.');
      if (!chosen) return human('자료 폴더 선택을 취소했습니다. 1단계에서 사용할 폴더를 선택한 뒤 이어 가세요.');
      const expected = {project_id: project.id, source_dataset_dir: project.source_dataset_dir, task: project.task,
        annotations_dir: project.annotations_dir, active_labelset_id: project.active_labelset_id || 'default'};
      dispatched = true;
      await useDatasetStore.getState().importFolder(chosen, state.task, undefined, false,
        () => identity === projectAuthority() && authorityCurrent(control) && !control.isPaused(), expected);
      if (!authorityCurrent(control) || identity !== projectAuthority()) return uncertain('자료를 가져오는 동안 프로젝트 권한·등록 위치가 바뀌었습니다. 현재 원본을 다시 확인하세요.');
      requireProject();
      const after = useDatasetStore.getState();
      if (after.totalImages < 1) return {...completed({...localScopeReceipt(), images: 0, imported: true}), bindingAfter: taskRuntimeBinding(), message: '자료를 가져왔지만 학습에 쓸 이미지가 없습니다. 다음 작업 전에 1단계에서 자료를 확인하세요.'};
      return {...completed({...localScopeReceipt(), images: after.totalImages, imported: true}), bindingAfter: taskRuntimeBinding()};
    }
    const check = fence(control);
    if (step.action === 'configure_training') {
      const training = useTrainingStore.getState(), compute = useComputeStore.getState();
      if (training.isTraining) return human('학습 중에는 설정을 바꾸지 않습니다. 3단계에서 현재 작업을 확인하세요.');
      if (compute.selectedProfileId && args.device !== undefined) return human('선택 서버는 등록된 장치를 사용합니다. 로컬 장치를 바꾸려면 3단계에서 실행 위치를 직접 선택하세요.');
      check();
      if (args.preset) training.setPreset(args.preset as 'fast' | 'precision');
      training.setNextSettings({...(args.device ? {device: String(args.device)} : {}), ...(args.batch_size !== undefined ? {batchSize: Number(args.batch_size)} : {})});
      const after = useTrainingStore.getState();
      return {...completed({settings: {preset: after.preset, device: after.nextDevice || 'auto', batch_size: after.nextBatchSize,
        compute_profile_id: compute.selectedProfileId ?? null}}), bindingAfter: taskRuntimeBinding()};
    }
    if (step.action === 'save_split') {
      await navigate(1, control); check();
      const data = useDatasetStore.getState();
      if (data.splitSupported === false) return human(data.splitUnavailableReason || '이 자료는 1단계의 원본 분할을 사용합니다.');
      dispatched = true;
      const error = await applyAppSplit(Number(args.train_percent) / 100, Number(args.val_percent) / 100, Number(args.test_percent) / 100);
      check(false); if (error) return uncertain(error, {...localScopeReceipt(), split_requested: true});
      const after = useDatasetStore.getState();
      if (after.splitState !== 'saved') return uncertain('분할 저장 상태를 확인하지 못했습니다. 1단계에서 저장 기록을 확인하세요.');
      const saved = await api.dataset.currentSummary({folder_path: after.folderPath, task: useProjectStore.getState().task}); check(false);
      if (saved.split_state !== 'saved' || JSON.stringify(saved.split) !== JSON.stringify(after.split)) return uncertain('저장된 분할과 화면의 개수가 다릅니다.');
      return completed({...localScopeReceipt(), split: {...saved.split}, ratios: {train: Number(args.train_percent) / 100, val: Number(args.val_percent) / 100, test: Number(args.test_percent) / 100}, split_state: 'saved'});
    }
    if(step.action==='configure_input_recipe'){
      await navigate(3,control);check();
      const state=useProjectStore.getState(),data=useDatasetStore.getState();
      if(useTrainingStore.getState().isTraining)return human('학습 중에는 고정 입력 규칙을 바꾸지 않습니다.',undefined,'waiting_resource');
      const recipe=validateInputRecipe(args.input_recipe as FixedInputRecipe,state.task);
      const selected=useAnnotationStore.getState().currentImage?.file_path;
      const imagePath=selected||(await api.dataset.getImages({folder_path:data.folderPath,task:state.task,limit:1,offset:0})).items[0]?.file_path;check();
      if(!imagePath)return human('미리 확인할 현재 프로젝트의 이미지가 필요합니다.');
      const settings=taskAutoRunSettings('train'),size=(settings?.config_overrides as Record<string,unknown>|undefined)?.image_size??256;
      const imageSize=Array.isArray(size)?size:[size,size];
      const preview=await request<{input_recipe?:FixedInputRecipe;input_recipe_sha256?:string;source_sha256?:string;prepared_size?:number[]}>('/api/training/input-preparation/preview',{
        method:'POST',body:JSON.stringify({task:state.task,image_path:imagePath,input_recipe:recipe,image_size:imageSize})});check();
      const clearing=recipe.operations.length===0;
      const recipeMatches=clearing?preview.input_recipe===undefined&&preview.input_recipe_sha256===undefined
        :JSON.stringify(preview.input_recipe)===JSON.stringify(recipe)&&Boolean(preview.input_recipe_sha256&&/^[a-f0-9]{64}$/.test(preview.input_recipe_sha256));
      if(!recipeMatches
        ||!preview.source_sha256||!/^[a-f0-9]{64}$/.test(preview.source_sha256)
        ||JSON.stringify(preview.prepared_size)!==JSON.stringify(imageSize))return human('실제 입력 미리보기의 규칙·크기·해시가 요청과 다릅니다.');
      const scope=inputPreparationScope({apiIdentity:getApiPersistenceIdentity(),projectId:state.project!.id,labelsetId:state.project!.active_labelset_id||'default',source:data.folderPath,task:state.task});
      if(typeof window!=='undefined'&&window.localStorage){
        const key=`modu.input-preparation.v1:${getApiPersistenceIdentity()}:${scope}`;let history:unknown[]=[];
        try{const saved=JSON.parse(localStorage.getItem(key)||'[]');if(Array.isArray(saved))history=saved;}catch{/* first valid saved version */}
        localStorage.setItem(key,JSON.stringify([...history.slice(-19),{recipe,sha256:preview.input_recipe_sha256??null,saved_at:new Date().toISOString()}]));
      }
      useTrainingStore.getState().setInputRecipe(scope,clearing?null:recipe,preview.input_recipe_sha256??null);
      return{...completed({...localScopeReceipt(),input_recipe_sha256:preview.input_recipe_sha256??null,preview_source_sha256:preview.source_sha256,input_recipe:recipe,input_recipe_cleared:clearing,previewed:true}),bindingAfter:taskRuntimeBinding()};
    }
    if (step.action === 'generate_label_drafts') {dispatched = true; return await draftAction(args, control);}
    if (step.action === 'start_training') {
      if (useTrainingStore.getState().isTraining) return human('기존 학습이 진행 중입니다. 다른 작업을 중단하거나 새 학습을 겹쳐 시작하지 않습니다.',undefined,'waiting_resource');
      await navigate(3, control); check();
      const startAuthority = trainingStartAuthority(), initial = useTrainingStore.getState();
      const before = {device: initial.nextDevice, batch: initial.nextBatchSize}, bindingBefore = taskRuntimeBinding(), startedScope = localScopeReceipt();
      const preparedSettings = taskAutoRunSettings('train');
      let unrelatedChange = false, settingTransitions = 0;
      let lastSettings = JSON.stringify(before);
      const settingsMatch = (settings: Record<string, unknown> | null | undefined) => {
        const live = useTrainingStore.getState(), overrides = settings?.config_overrides as Record<string, unknown> | undefined;
        const expectedDevice = settings?.device === 'auto' ? null : settings?.device;
        return (live.nextDevice === before.device || live.nextDevice === settings?.device || live.nextDevice === expectedDevice)
          && (live.nextBatchSize === before.batch || live.nextBatchSize === overrides?.batch_size);
      };
      const watch = () => {
        const live = useTrainingStore.getState(), signature = JSON.stringify({device: live.nextDevice, batch: live.nextBatchSize});
        if (signature !== lastSettings) {settingTransitions++; lastSettings = signature;}
        if (trainingStartAuthority() !== startAuthority || !settingsMatch(preparedSettings) || settingTransitions > 1) unrelatedChange = true;
      };
      const cleanups = [useProjectStore, useDatasetStore, useTrainingStore, useComputeStore].map(store => store.subscribe?.(watch));
      try {
        dispatched = true; const result = await requestTaskAutoRun('train', control);
        operationReceipt = result.receipt ? {...startedScope, ...result.receipt} : undefined;
        if (result.error) return human(result.error, operationReceipt);
        if (operationReceipt) operationReceipt.not_started = false;
        const receipt = operationReceipt, jobId = receipt?.job_id;
        if (typeof jobId !== 'string' || !receipt?.settings) return uncertain('시작된 학습의 작업 ID와 실제 설정을 확인하지 못했습니다.', receipt);
        await control.checkpoint?.(receipt);
        const settings = receipt.settings as Record<string, unknown>;
        if (preparedSettings && JSON.stringify(preparedSettings) !== JSON.stringify(settings)) return uncertain('보이는 학습 설정과 실제 시작 설정이 다릅니다.', receipt);
        const currentStart = () => authorityCurrent(control) && !unrelatedChange && trainingStartAuthority() === startAuthority && settingsMatch(settings);
        if (!currentStart()) return uncertain('학습 시작 중 원본·실행 위치·설정이 바뀌었습니다. 저장된 작업 ID로 확인하세요.', receipt);
        if (settings.task !== useProjectStore.getState().task || settings.dataset_path !== useDatasetStore.getState().folderPath
          || settings.preset !== useTrainingStore.getState().preset || (settings.compute_profile_id ?? null) !== (useComputeStore.getState().selectedProfileId ?? null))
          return uncertain('학습 시작 설정의 원본·종류·실행 위치가 현재 작업과 다릅니다.', receipt);
        const job = await api.training.getStatus(jobId);
        if ((job.compute_profile_id ?? null) !== (settings.compute_profile_id ?? null) || !currentStart() || job.job_id !== jobId || (job.task && job.task !== useProjectStore.getState().task)
          || (job.dataset_path && job.dataset_path !== useDatasetStore.getState().folderPath)) return uncertain('학습 작업의 원본·종류·실행 위치가 요청과 다릅니다.', receipt);
        return {kind: 'waiting_job', receipt: {...receipt, not_started: false, status: job.status},
          ...(bindingBefore !== taskRuntimeBinding() ? {bindingAfter: taskRuntimeBinding()} : {}),
          message: '학습 작업을 시작했습니다. 완료 기록을 확인한 뒤 다음 단계로 이어 갑니다.'};
      } finally {for (const cleanup of cleanups) cleanup?.();}
    }
    if (step.action === 'evaluate_model') {
      await navigate(4, control); check();
      const training = useTrainingStore.getState(), state = useProjectStore.getState(), data = useDatasetStore.getState();
      const jobId = typeof args.job_id === 'string' ? args.job_id : control.priorModelJobId
        ?? (training.isCurrentData && training.status === 'completed' ? training.jobId : null);
      if (!jobId) return human('현재 자료로 완료한 모델의 작업 ID가 필요합니다. 3단계에서 학습 완료를 확인하세요.');
      dispatched = true; await evaluateAppModel(jobId, data.folderPath, state.task, () => control.isCurrent()); check(false);
      const evaluated = useEvaluationStore.getState(), source = currentEvaluationGuidanceSource();
      if (evaluated.errorMessage) return uncertain(evaluated.errorMessage, {...localScopeReceipt(), job_id: jobId});
      if (!source || source.jobId !== jobId || evaluated.executionEvidence?.job_id !== jobId || !evaluated.testPredictions.length)
        return uncertain('요청 모델과 현재 원본에 묶인 평가 결과를 확인하지 못했습니다.');
      bumpWorkflowEvidence();
      const history=evaluated.executionEvidence as unknown as {evaluation_id?:unknown;binding?:unknown};
      return completed({...localScopeReceipt(), job_id: jobId, evaluated_at: evaluated.executionEvidence.evaluated_at,
        ...(typeof history.evaluation_id==='string'?{evaluation_id:history.evaluation_id}:{}),
        ...(history.binding&&typeof history.binding==='object'&&!Array.isArray(history.binding)?{evaluation_binding:history.binding}:{}),
        samples: evaluated.testPredictions.length, ...(evaluated.executionEvidence.common_cohort ? {common_cohort: evaluated.executionEvidence.common_cohort} : {})});
    }
    if (step.action === 'save_flow') {
      await navigate(5, control); check();
      const state = useProjectStore.getState(), data = useDatasetStore.getState(), training = useTrainingStore.getState();
      if (useFlowchartStore.getState().pipelineDirty || useFlowchartStore.getState().pipelineIsDraft) return human('5단계에 저장하지 않은 검사 흐름이 있습니다. 현재 편집을 먼저 확인하세요.');
      const jobId = typeof args.job_id === 'string' ? args.job_id : control.priorModelJobId
        ?? (training.isCurrentData && training.status === 'completed' ? training.jobId : null);
      if (!jobId) return human('검사 흐름에 넣을 현재 자료의 완료 모델을 먼저 확인하세요.');
      // Preparation cannot overwrite a manual graph edit or a newly activated version while an await is pending.
      const initialFlow = useFlowchartStore.getState(), initialRevision = initialFlow.flowIdentity?.semantic_revision;
      const scopeReceipt = localScopeReceipt(); let savedVersion: string | null = null;
      const checkFlow = () => {
        check(); const current = useFlowchartStore.getState();
        if (current.pipelineDirty || current.pipelineIsDraft || (current.baseVersionId !== initialFlow.baseVersionId && current.baseVersionId !== savedVersion)
          || (initialRevision !== undefined && current.flowIdentity?.semantic_revision !== initialRevision
            && !(savedVersion && current.baseVersionId === savedVersion && current.flowIdentity.semantic_revision === initialRevision + 1)))
          throw new Error('검사 흐름 편집·저장 버전이 실행 중 바뀌었습니다. 현재 5단계를 확인하세요.');
      };
      const saved = await saveSingleModelFlow(jobId, data.folderPath, state.task, checkFlow, () => {dispatched = true;}, {
        expectedVersionId: initialFlow.baseVersionId ?? null,
        onSaved: async saved => {savedVersion = saved.version_id; operationReceipt = {...scopeReceipt, not_started: false, job_id: jobId, version_id: saved.version_id, pipeline_id: saved.pipeline_id}; await control.checkpoint?.(operationReceipt);},
      });
      dispatched = true; check(false);
      if (!saved.version_id || useFlowchartStore.getState().baseVersionId !== saved.version_id) return uncertain('저장 버전과 5단계의 검사 흐름이 일치하지 않습니다.');
      bumpWorkflowEvidence(); return completed({...localScopeReceipt(), job_id: jobId, version_id: saved.version_id, pipeline_id: saved.pipeline_id});
    }
    if (step.action === 'evaluate_flow') {
      await navigate(5, control); check();
      const flow = useFlowchartStore.getState(), versionId = flow.baseVersionId;
      if (!versionId || flow.pipelineDirty || flow.pipelineIsDraft) return human('5단계에서 사용할 검사 흐름을 저장 버전으로 먼저 준비하세요.');
      const scopeReceipt = localScopeReceipt(), flowKey = JSON.stringify([versionId, flow.flowIdentity?.semantic_revision, flow.pipelineDirty, flow.pipelineIsDraft]);
      const checkFlow = () => {check(); const current = useFlowchartStore.getState();
        if (JSON.stringify([current.baseVersionId, current.flowIdentity?.semantic_revision, current.pipelineDirty, current.pipelineIsDraft]) !== flowKey)
          throw new Error('평가하는 동안 검사 흐름 버전·규칙이 바뀌었습니다. 5단계의 저장 버전을 확인하세요.');};
      dispatched = true; const {cohort, evaluated} = await evaluateSavedFlow(versionId, '도우미 작업 시험 이미지', checkFlow, {
        cohort: async cohort => {operationReceipt = {...scopeReceipt, not_started: false, version_id: versionId, cohort_id: cohort.cohort_id};
          await control.checkpoint?.(operationReceipt);
          if (!matchesScope(cohort.scope)) throw new Error('생성된 시험 묶음의 원본·라벨 세트가 현재 작업과 다릅니다.');},
        evaluated: async evaluated => {operationReceipt = {...operationReceipt, evaluation_id: evaluated.evaluation_id,
          input_sha256: evaluated.input_sha256, graph_sha256: evaluated.graph_sha256, model_sha256: evaluated.model_sha256,
          truth_sha256: evaluated.truth_sha256, record_sha256: evaluated.record_sha256, known: evaluated.coverage.known, unknown: evaluated.coverage.unknown,
          samples: evaluated.coverage.total, truth_review_required: !evaluated.coverage.known || Boolean(evaluated.coverage.unknown)}; await control.checkpoint?.(operationReceipt);},
      }); check(false);
      if (!matchesScope(cohort.scope) || !matchesScope(evaluated.scope) || evaluated.version_id !== versionId || evaluated.cohort_id !== cohort.cohort_id)
        return uncertain('평가가 현재 원본·라벨 세트·저장 버전과 일치하지 않습니다.');
      const detail = resultDiagnostics('flow_evaluation', evaluated.errors?.map(row => row.error), evaluated.validity.reasons);
      const receipt = {...localScopeReceipt(), version_id: versionId, cohort_id: cohort.cohort_id, evaluation_id: evaluated.evaluation_id,
        known: evaluated.coverage.known, unknown: evaluated.coverage.unknown, samples: evaluated.coverage.total, record_sha256: evaluated.record_sha256, input_sha256: evaluated.input_sha256,
        graph_sha256: evaluated.graph_sha256, model_sha256: evaluated.model_sha256, truth_sha256: evaluated.truth_sha256,
        truth_review_required: !evaluated.coverage.known || Boolean(evaluated.coverage.unknown), diagnostics: detail};
      bumpWorkflowEvidence();
      if (evaluated.status !== 'completed' || evaluated.errors?.length) return human(`검사 흐름 평가 오류: ${diagnosticMessage(detail, '실행 오류·실패가 있습니다. 5단계 결과를 확인하세요.')}`, receipt);
      if ((!evaluated.coverage.known || evaluated.coverage.unknown) && evaluated.validity.valid)
        return {...completed({...receipt,quality_status:'input_needed',quality_passed:false}),
          message:'검사 흐름 실행을 마쳤습니다. 독립 정답이 없어 정확도·미검 검증은 정답 입력이 필요하며, 다음 추론 작업은 계속합니다.'};
      if (!evaluated.validity.valid) return human(`검사 흐름 평가 확인: ${diagnosticMessage(detail, '유효성 조건을 5단계에서 확인하세요.')}`, receipt);
      return completed(receipt);
    }
    if (step.action === 'inspect_test') {
      await navigate(6, control); check();
      const inspectionScope = localScopeReceipt();
      dispatched = true; const result = await requestTaskAutoRun('inspect', control); operationReceipt = result.receipt ? {...inspectionScope, ...result.receipt} : undefined; check(false);
      if (result.error) return human(result.error, result.receipt);
      if (typeof result.receipt?.run_id !== 'string') return uncertain('검사 실행 기록 ID를 확인하지 못했습니다. 6단계에서 저장 결과를 확인하세요.', result.receipt);
      return reconcileInspection(operationReceipt!, check);
    }
    if (step.action === 'export_report') {
      await navigate(4, control); check();
      const evaluation = useEvaluationStore.getState(), source = currentEvaluationGuidanceSource();
      if (evaluation.executionEvidence?.common_cohort) return human('선택 코호트 보고서 내보내기는 아직 지원되지 않습니다. 원래 학습 코호트로 대체하지 않습니다.');
      if (!source || !evaluation.jobId || source.jobId !== evaluation.jobId) return human('현재 원본에 묶인 평가를 4단계에서 먼저 확인하세요.');
      dispatched = true; const reportPath = await evaluation.exportReport(args.format as 'html' | 'json'); check(false);
      if (!reportPath) return uncertain('저장한 보고서의 경로를 확인하지 못했습니다.');
      return completed({...localScopeReceipt(), job_id: evaluation.jobId, format: args.format, report_path: reportPath});
    }
    return human('지원하지 않는 작업입니다. 기능 목록을 다시 확인하세요.');
  } catch (cause) { return dispatched ? uncertain(message(cause), operationReceipt) : human(message(cause)); }
}
async function reconcileInspection(receipt: Record<string, unknown>, check: (beforeWrite?: boolean) => void): Promise<TaskActionResult> {
  const run = await api.inspections.getRun(String(receipt.run_id)); check(false);
  const state = useProjectStore.getState(), data = useDatasetStore.getState();
  if (run.run_id !== receipt.run_id || run.scope !== 'test' || run.task !== state.task || (run.canonical_source_folder || run.source_folder) !== data.folderPath
    || run.saved_version_id !== useFlowchartStore.getState().baseVersionId) return uncertain('저장된 검사가 현재 원본·시험 분할·검사 흐름 버전과 일치하지 않습니다.', receipt);
  const result = {...localScopeReceipt(), ...receipt, run_id: run.run_id, scope: run.scope, status: run.status, saved_version_id: run.saved_version_id, samples: run.rows.length,
    errors: run.rows.filter(row => row.state === 'error').length,
    diagnostics: resultDiagnostics('inspection', run.rows.filter(row => row.state === 'error').slice(0, 3).map(row => row.error))};
  if (run.status === 'running') return {kind: 'waiting_job', receipt: result, message: '저장된 시험 검사가 아직 진행 중입니다.'};
  if (run.status !== 'completed' || result.errors) return human(`시험 검사 오류: ${diagnosticMessage(result.diagnostics, '멈춤·오류가 있습니다. 6단계 결과를 확인하세요.')}`, result);
  return completed(result);
}
/** A human truth change is a new evaluation input. Only explicit human resume, current byte/model/graph
 * proof and durable subeffect checkpoints permit this one bounded recomputation. */
async function reconcileFlowEvaluation(step: TaskStep, control: Control, check: (beforeWrite?: boolean) => void): Promise<TaskActionResult> {
  const receipt = {...step.receipt!};
  let latest: Record<string, unknown> = {...receipt, not_started: false};
  let recomputation = receipt.recomputation && typeof receipt.recomputation === 'object'
    ? {...receipt.recomputation as Record<string, unknown>} : null;
  const original = await flowEvaluation.read(String(receipt.evaluation_id)); check(false);
  if (original.evaluation_id !== receipt.evaluation_id || original.version_id !== receipt.version_id || original.cohort_id !== receipt.cohort_id
    || !matchesScope(original.scope)) return uncertain('저장된 검사 흐름 평가의 입력이 현재 작업과 다릅니다.', receipt);
  const originalDiagnostics = resultDiagnostics('flow_evaluation', original.errors?.map(row => row.error), original.validity.reasons);
  receipt.diagnostics = originalDiagnostics;
  for (const key of ['input_sha256','graph_sha256','model_sha256','truth_sha256','record_sha256'] as const)
    if (receipt[key] === undefined && original[key] !== undefined) receipt[key] = original[key];
  if (receipt.truth_review_required === undefined) receipt.truth_review_required = !original.coverage.known || Boolean(original.coverage.unknown);
  latest = {...receipt, not_started: false};
  const matchingResult = (result: FlowEvaluation) => matchesScope(result.scope) && result.version_id === receipt.version_id
    && result.input_sha256 === receipt.input_sha256 && result.graph_sha256 === receipt.graph_sha256 && result.model_sha256 === receipt.model_sha256;
  const finish = async (result: FlowEvaluation): Promise<TaskActionResult> => {
    if (matchingResult(result)) latest = {...latest, diagnostics: resultDiagnostics('flow_evaluation', result.errors?.map(row => row.error), result.validity.reasons)};
    if (!matchingResult(result) || result.status !== 'completed' || result.errors?.length || !result.validity.valid || !result.coverage.known || result.coverage.unknown)
      return uncertain('정답 확인 뒤 평가의 원본·모델·규칙 또는 유효성이 요청과 다릅니다.', latest);
    recomputation = {...recomputation, phase: 'evaluated', new_evaluation_id: result.evaluation_id, known: result.coverage.known, unknown: result.coverage.unknown,
      record_sha256: result.record_sha256};
    latest = {...receipt, not_started: false, recomputation,
      diagnostics: resultDiagnostics('flow_evaluation', result.errors?.map(row => row.error), result.validity.reasons)};
    await control.checkpoint?.(latest);
    return completed(latest);
  };
  try {
    if (recomputation) {
      if (!matchingResult(original) || recomputation.previous_evaluation_id !== receipt.evaluation_id || recomputation.previous_cohort_id !== receipt.cohort_id)
        return uncertain('재평가의 기존 입력 기록이 다릅니다.', latest);
      if (typeof recomputation.new_evaluation_id === 'string') {
        const saved = await flowEvaluation.read(recomputation.new_evaluation_id); check(false);
        if (saved.evaluation_id !== recomputation.new_evaluation_id || saved.cohort_id !== recomputation.new_cohort_id) return uncertain('재평가 저장 ID가 다릅니다.', latest);
        return await finish(saved);
      }
      if (typeof recomputation.new_cohort_id !== 'string') return uncertain('시험 묶음 생성 응답이 끊겼습니다. 5단계에서 저장된 묶음을 확인하세요. 같은 묶음을 다시 만들지 않습니다.', latest);
      // A known cohort identifies the one planned evaluation even when its response disappeared.
      const {evaluations} = await flowEvaluation.history(); check(false);
      const candidates = evaluations.filter(row => row.cohort_id === recomputation!.new_cohort_id && matchingResult(row as FlowEvaluation));
      if (candidates.length === 1) {
        const saved = await flowEvaluation.read(candidates[0].evaluation_id); check(false); return await finish(saved);
      }
      return uncertain('재평가 요청의 저장 결과를 아직 하나로 확인하지 못했습니다. 5단계 기록을 확인하세요. 다시 실행하지 않습니다.', latest);
    }
    if (original.status === 'completed' && !original.errors?.length && original.validity.valid && original.coverage.known && !original.coverage.unknown) return completed(latest);
    if (step.status !== 'waiting_human' || receipt.truth_review_required !== true)
      return human(diagnosticMessage(originalDiagnostics, '5단계에서 시험 이미지 정답과 평가 유효성을 확인하세요.'), receipt);
    if (!control.checkpoint) return human('정답 확인 뒤 자동 재평가의 저장 기록을 만들 수 없습니다. 5단계에서 평가를 다시 실행하고 기록을 확인하세요.', receipt);
    if (!matchingResult(original) || !original.validity.reasons?.length
      || original.validity.reasons.some(reason => !reason.startsWith('truth_or_source_changed:'))
      || !original.records?.length || original.records.length !== original.coverage.total || original.records.length > 5000)
      return uncertain('정답만 바뀌었는지 확인하지 못했습니다. 현재 원본·모델·검사 흐름을 다시 확인하세요.', receipt);
    const flow = useFlowchartStore.getState();
    if (flow.baseVersionId !== receipt.version_id || flow.pipelineDirty || flow.pipelineIsDraft) return uncertain('저장한 검사 흐름 버전이 바뀌었습니다.', receipt);
    const scope = await flowEvaluation.scope(String(receipt.version_id)); check();
    if (!matchesScope(scope) || JSON.stringify(scope.classes) !== JSON.stringify(original.scope.classes)
      || JSON.stringify(scope.class_semantics) !== JSON.stringify(original.scope.class_semantics)) return uncertain('현재 정답 범위·클래스가 이전 평가와 다릅니다.', receipt);
    let changed = 0;
    // Bounded local metadata reads; no image pixels or raw evidence leave the application.
    for (let offset = 0; offset < original.records.length; offset += 8) {
      const rows = original.records.slice(offset, offset + 8);
      const truths = await Promise.all(rows.map(row => flowEvaluation.truth(row.image_path, original.scope))); check();
      for (let index = 0; index < rows.length; index++) {
        const current = truths[index] as ImageTruth & {binding?: {content_hash?: string}};
        if (!matchesScope(current.scope) || current.binding?.content_hash !== rows[index].input_sha256)
          return uncertain('정답 확인 중 원본 이미지의 바이트가 달라졌습니다. 새 입력 계획이 필요합니다.', receipt);
        if (current.invalidated || current.verdict === 'UNKNOWN') return human('아직 정답이 확인되지 않은 시험 이미지가 있습니다. 5단계에서 확인하세요.', receipt);
        if (current.truth_sha256 !== rows[index].truth_sha256) changed++;
      }
    }
    if (!changed) return human('저장된 정답 변경을 아직 확인하지 못했습니다. 5단계에서 정답을 확인하세요.', receipt);
    recomputation = {intent_id: `truth_${globalThis.crypto.randomUUID().replaceAll('-', '')}`, phase: 'freeze_intent',
      previous_cohort_id: receipt.cohort_id, previous_evaluation_id: receipt.evaluation_id, changed_truth_images: changed,
      input_sha256: receipt.input_sha256, graph_sha256: receipt.graph_sha256, model_sha256: receipt.model_sha256};
    latest = {...receipt, not_started: false, recomputation};
    check(); await control.checkpoint(latest); check();
    const cohort = await flowEvaluation.freeze(String(receipt.version_id), '도우미 작업 · 정답 확인 뒤 시험 이미지');
    recomputation = {...recomputation, new_cohort_id: cohort.cohort_id, phase: 'cohort_saved'};
    latest = {...receipt, not_started: false, recomputation};
    await control.checkpoint(latest); check();
    if (!matchesScope(cohort.scope) || cohort.input_sha256 !== original.input_sha256 || cohort.count !== original.coverage.total)
      return uncertain('새 시험 묶음의 원본 바이트·분할이 이전 평가와 다릅니다. 평가를 실행하지 않았습니다.', latest);
    recomputation = {...recomputation, phase: 'evaluation_intent'}; latest = {...receipt, not_started: false, recomputation};
    await control.checkpoint(latest); check();
    const evaluated = await flowEvaluation.run(String(receipt.version_id), cohort.cohort_id);
    recomputation = {...recomputation, new_evaluation_id: evaluated.evaluation_id, phase: 'evaluation_returned'};
    latest = {...receipt, not_started: false, recomputation}; check(false);
    if (evaluated.cohort_id !== cohort.cohort_id) return uncertain('재평가의 시험 묶음 ID가 다릅니다.', latest);
    return await finish(evaluated);
  } catch (cause) {return uncertain(message(cause), latest);}
}
/** Missing identifiers can never authorize another dispatch. */
export async function reconcileTaskAction(step: TaskStep, control: Control): Promise<TaskActionResult> {
  try {
    requireActive(control); const receipt = step.receipt;
    if (!receipt) return uncertain();
    // This receipt is issued only when the adapter proved that no mutation was dispatched. A human resume
    // re-enters the same guarded preparation; ambiguous/started writes never receive this marker.
    const hasExecution = ['job_id','batch_id','run_id','version_id','evaluation_id','report_path'].some(key => typeof receipt[key] === 'string' && Boolean(receipt[key]));
    if (receipt.not_started === true && !hasExecution) {
      if (!control.checkpoint) return human('재개할 작업의 실행 소유권을 저장할 수 없습니다. 현재 화면에서 준비 상태를 확인한 뒤 새 계획을 확인하세요.', receipt);
      // This is an atomic same-attempt claim, not a normal observation. The controller/ledger require the
      // saved not_started proof and one CAS; competing or response-lost claimants never dispatch.
      await control.checkpoint({not_started: false, unstarted_claim: {nonce: globalThis.crypto.randomUUID(), phase: 'claimed'}});
      requireActive(control);
      return executeTaskAction({...step, receipt: {...receipt, not_started: false}}, control);
    }
    const check = fence(control);
    if (receipt.source_dataset_path && !matchesScope(receipt)) return uncertain('저장된 작업의 원본·라벨 세트가 현재 프로젝트와 다릅니다.', receipt);
    if (step.action === 'start_training' && typeof receipt.job_id === 'string') {
      const job = await api.training.getStatus(receipt.job_id); check(false);
      if ((job.compute_profile_id ?? null) !== ((receipt.settings as Record<string, unknown> | undefined)?.compute_profile_id ?? useComputeStore.getState().selectedProfileId ?? null) || job.job_id !== receipt.job_id || (job.task && job.task !== useProjectStore.getState().task)
        || (job.dataset_path && job.dataset_path !== useDatasetStore.getState().folderPath)) return uncertain('저장된 학습 작업의 원본·종류가 현재 작업과 다릅니다.', receipt);
      const detail = resultDiagnostics('training', [job.error || job.result?.error], [job.observation?.cause, job.wait_reason], job.observation?.next_action);
      const latest = {...receipt, not_started: false, status: job.status, diagnostics: detail};
      if (job.status === 'completed') return completed(latest);
      if (jobs.has(job.status)) return {kind: 'waiting_job', receipt: latest, message: '학습 완료 기록을 기다리고 있습니다.'};
      return human(`학습 ${job.status}: ${diagnosticMessage(detail, '완료되지 않았습니다. 3단계에서 중단·오류 상태를 확인하세요.')}`, latest);
    }
    if (step.action === 'generate_label_drafts' && typeof receipt.batch_id === 'string') {
      const batch = receipt.mode === 'phrase' ? await foundationLabelingApi.batch(receipt.batch_id) : await api.labelSuggestions.getBatch(receipt.batch_id); check(false);
      if (batch.id !== receipt.batch_id) return uncertain('초안 작업 ID가 저장 기록과 다릅니다.', receipt);
      const detail = resultDiagnostics('label_drafts', [batch.error, ...(batch.entries || []).filter(row => row.error).slice(0, 3).map(row => row.error)]);
      const latest = {...receipt, not_started: false, status: batch.status, total: batch.total, processed: batch.processed, generated: batch.generated, failed: batch.failed, adopted: false, diagnostics: detail};
      if (jobs.has(batch.status)) return {kind: 'waiting_job', receipt: latest, message: '초안 생성을 기다리고 있습니다.'};
      const data=useDatasetStore.getState();
      if(batch.status==='completed'&&!batch.error&&!batch.failed&&data.totalImages>data.unlabeledImages)
        return completed({...latest,existing_labels_used:true});
      if (batch.status === 'completed' && step.status === 'waiting_human') {
        const entries = batch.entries || [], generated = batch.generated;
        if (generated === 0) return completed({...latest, generated: 0, reviewed: 0, accepted: 0, rejected: 0});
        const listed = await api.labelSuggestions.list(); check(false);
        const proposalIds = new Set(entries.map(row => row.proposal_id).filter((id): id is string => typeof id === 'string'));
        const reviewed = listed.suggestions.filter(row => proposalIds.has(row.id) || row.batch_id === batch.id);
        if (reviewed.length >= generated && reviewed.every(row => row.status === 'accepted' || row.status === 'rejected')) {
          bumpWorkflowEvidence();
          return completed({...latest, generated, reviewed: reviewed.length, accepted: reviewed.filter(row => row.status === 'accepted').length,
            rejected: reviewed.filter(row => row.status === 'rejected').length, human_review_verified: true, adopted: false});
        }
      }
      bumpWorkflowEvidence(); return human(batch.status === 'completed' ? '초안을 만들었습니다. 2단계에서 사람이 보고 채택·검수한 뒤 이어 가세요.' : `초안 작업 오류: ${diagnosticMessage(detail, '작업이 멈췄습니다. 2단계에서 작업 기록을 확인하세요.')}`, latest);
    }
    if (step.action === 'inspect_test' && typeof receipt.run_id === 'string') return reconcileInspection(receipt, check);
    if (step.action === 'evaluate_flow' && typeof receipt.evaluation_id !== 'string' && typeof receipt.cohort_id === 'string' && typeof receipt.version_id === 'string') {
      const {evaluations} = await flowEvaluation.history(); check(false);
      const candidates = evaluations.filter(row => row.cohort_id === receipt.cohort_id && row.version_id === receipt.version_id && matchesScope(row.scope));
      if (candidates.length !== 1) return uncertain('저장된 시험 묶음의 평가 결과를 하나로 확인하지 못했습니다. 평가를 다시 실행하지 않습니다.', receipt);
      return reconcileFlowEvaluation({...step, receipt: {...receipt, evaluation_id: candidates[0].evaluation_id}}, control, check);
    }
    if (step.action === 'evaluate_flow' && typeof receipt.evaluation_id === 'string') return reconcileFlowEvaluation(step, control, check);
    if (step.action === 'evaluate_model' && typeof receipt.job_id === 'string') {
      const evaluation = useEvaluationStore.getState(), source = currentEvaluationGuidanceSource();
      if (source?.jobId === receipt.job_id && evaluation.executionEvidence?.evaluated_at === receipt.evaluated_at) return completed(receipt);
    }
    if (step.action === 'save_flow' && typeof receipt.version_id === 'string') {
      const {pipelines} = await api.flowchart.listPipelines(useDatasetStore.getState().folderPath); check(false);
      if (pipelines.some(row => row.version_id === receipt.version_id && row.is_active)) return completed(receipt);
    }
    return uncertain(undefined, receipt);
  } catch (cause) {return uncertain(message(cause), step.receipt || undefined);}
}
