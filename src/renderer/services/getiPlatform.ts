import {getApiDiagnosticScope, getProjectContext, request, type ProjectConfig, type ProjectContext} from './api';
import type {FixedInputRecipe} from '../components/training/inputPreparation';

export interface PlatformDatasetReceipt {
  id: string; revision?: string; project_id: string; geti_project_id: string; task: string;
  source_task?: string;
  dataset_version_id: string; manifest_sha256: string; dataset_fingerprint: string;
  labelset_id: string; split_sha256: string; recipe_sha256?: string; preparation_id?: string;
  input_recipe?: FixedInputRecipe | null;
  counts: {training: number; validation: number; testing: number; total: number};
  source_mapping: Array<{relative_path: string; sha256: string; media_id: string; subset: string; label?: string}>;
  class_mapping: unknown; created_at: string;
}
/** The immutable basis of receipt-scoped review, media and prediction state. */
function receiptIdentity(receipt:PlatformDatasetReceipt | null | undefined):string | null {
  return receipt ? JSON.stringify([receipt.project_id,receipt.geti_project_id,receipt.id,receipt.revision ?? null,
    receipt.dataset_version_id,receipt.manifest_sha256,receipt.dataset_fingerprint,receipt.labelset_id,
    receipt.split_sha256,receipt.recipe_sha256 ?? null,receipt.task,receipt.source_task ?? null]) : null;
}
export interface PlatformJob {
  id: string; platform_job_id?: string; operation_id?: string; submission_id?: string; status: string; progress: number | null;
  message?: string; error?: string; model_id?: string; architecture_id?: string;
  created_at?: string; finished_at?: string; worker_exited?: boolean;
  cancel_requested?: boolean;
}
export interface PlatformModelVariant {
  id: string; format: string; precision: string; weights_size: number; evaluations: unknown[];
  prediction_supported?: boolean;
}
export interface PlatformModel {
  id: string; name: string; architecture: string; task: string; variants: PlatformModelVariant[];
  dataset_revision_id?: string;
}
export interface PlatformArchitecture {
  id: string; name: string; task: string; weights_ready?: boolean; license?: string | {name:string;url?:string}; supported: boolean;
}
export function platformLicenseName(architecture:PlatformArchitecture | undefined):string {
  const value = architecture?.license;
  return (typeof value === 'string' ? value : value && typeof value.name === 'string' ? value.name : '').slice(0,200);
}
export function platformLicenseUrl(architecture:PlatformArchitecture | undefined):string | null {
  const value = architecture?.license;
  if (!value || typeof value === 'string' || typeof value.url !== 'string' || value.url.length > 2000) return null;
  try {const url = new URL(value.url);return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;} catch {return null;}
}
export interface PlatformStatus {
  available: boolean;
  runtime: {state: 'unavailable' | 'stopped' | 'starting' | 'running' | 'error'; message?: string; degraded?: boolean};
  project: {name: string; task: string; supported: boolean; linked: boolean; target_task_options?: string[]; binding_state?: string; binding_message?: string};
  dataset: PlatformDatasetReceipt | null; jobs: PlatformJob[]; models: PlatformModel[];
  architectures: PlatformArchitecture[]; native_tasks: string[];
  permissions: {can_prepare: boolean; can_train: boolean; can_adopt: boolean};
}
export interface PlatformPrediction {
  task?: string; model_id?: string; variant_id?: string; label?: string; confidence?: number;
  predictions?: unknown[]; annotations?: unknown[]; result?: unknown;
  source_sha256?: string; recipe_sha256?: string;
}
export interface PlatformSourceImage {
  image_uuid: string; relative_path: string; file_name: string; source_path: string;
  sha256: string; transferred: boolean;
}
interface PlatformSourcePage {
  items: PlatformSourceImage[]; pagination: {offset:number;limit:number;total:number};
}
export type PlatformActionKind = 'refresh' | 'import' | 'native' | 'start' | 'prepare' | 'weights' | 'train' | 'results';
export interface PlatformNextAction {kind: PlatformActionKind; label: string; reason: string; disabled?: boolean}
export function platformTrainingTask(status:PlatformStatus,targetTask?:string):string {
  if (status.dataset?.task) return status.dataset.task;
  return status.project.task === 'segmentation' && targetTask === 'instance_segmentation' && status.project.target_task_options?.includes(targetTask) ? targetTask : status.project.task;
}
export function platformTargetSupported(status:PlatformStatus,targetTask?:string):boolean {
  return status.project.supported || platformTrainingTask(status,targetTask) === 'instance_segmentation' && status.project.task === 'segmentation';
}
export function platformJobActive(job: PlatformJob): boolean {
  if (job.cancel_requested && job.worker_exited !== true) return true;
  return !['completed', 'done', 'succeeded', 'failed', 'rejected', 'cancelled', 'canceled', 'aborted', 'interrupted'].includes(job.status.toLowerCase())
    || (['cancelled', 'canceled', 'aborted'].includes(job.status.toLowerCase()) && job.worker_exited !== true);
}
export function platformJobLabel(job: PlatformJob): string {
  if (job.cancel_requested && job.worker_exited !== true) return '중단 요청 · 종료 확인 중';
  const labels: Record<string, string> = {queued:'학습 대기',pending:'학습 대기',running:'학습 중',training:'학습 중',completed:'학습 완료',done:'학습 완료',succeeded:'학습 완료',failed:'학습 오류',rejected:'학습 요청 거절됨',submission_unconfirmed:'제출 결과 확인 필요',cancel_requested:'중단 요청 · 종료 확인 중',cancelling:'중단 요청 · 종료 확인 중',stopping:'중단 요청 · 종료 확인 중',cancelled:'중단됨',canceled:'중단됨',aborted:'중단됨',interrupted:'학습 중단'};
  if (['cancelled', 'canceled', 'aborted'].includes(job.status.toLowerCase()) && job.worker_exited !== true) return '중단 요청 · 작업자 종료 확인 필요';
  return labels[job.status.toLowerCase()] ?? '작업 상태 확인 필요';
}
export function platformNextAction(status: PlatformStatus | null, options: {hasSource: boolean; architectureId?: string; uncertain?: boolean; targetTask?: string; inputRecipeOperations?:number}): PlatformNextAction {
  if (!status) return {kind:'refresh',label:'상태 다시 확인',reason:'현재 프로젝트의 준비 상태를 읽어 오세요.'};
  if (status.runtime.degraded) return {kind:'refresh',label:'저장 상태 다시 확인',reason:'일부 기록을 새로 읽지 못했습니다. 표시된 기록을 보존하고 현재 상태를 다시 확인합니다.'};
  if (!platformTargetSupported(status,options.targetTask)) return {kind:'native',label:'기존 모델 도구 열기',reason:status.project.task === 'segmentation' ? '시맨틱 분할은 Geti의 개체별 분할과 다릅니다. 기존 분할 도구를 사용하세요.' : '이 작업은 기존 모델 도구에서 준비하고 실행할 수 있습니다.'};
  if (options.uncertain || status.jobs.some(job => ['submission_unconfirmed', 'unknown', 'uncertain'].includes(job.status))) return {kind:'refresh',label:'제출 결과 확인',reason:'실행 여부가 확인될 때까지 학습을 다시 제출하지 않습니다.'};
  if (status.jobs.some(platformJobActive)) return {kind:'refresh',label:'진행 상태 확인',reason:'진행 중인 작업을 확인하세요. 중단 요청과 작업자 종료는 따로 표시합니다.'};
  if (!options.hasSource) return {kind:'import',label:'사진 준비 화면 열기',reason:'현재 프로젝트에 사진을 가져오고 라벨과 학습·검증·시험 분할을 준비하세요.'};
  const trainingTask = platformTrainingTask(status,options.targetTask);
  if (trainingTask === 'instance_segmentation' && (options.inputRecipeOperations ?? 0) > 0) return {kind:'native',label:'전처리 편집 화면 열기',reason:'개체별 분할에는 저장한 고정 전처리를 적용할 수 없습니다. 전처리를 비우거나 기존 시맨틱 분할 경로를 사용하세요.'};
  if (!status.available || status.runtime.state === 'unavailable') return {kind:'refresh',label:'실행 환경 다시 확인',reason:status.runtime.message || '학습 환경을 사용할 수 없습니다. 설치 상태를 확인하고 다시 읽어 오세요.'};
  if (status.runtime.state !== 'running') return {kind:'start',label:'학습 환경 시작',reason:status.runtime.message || '현재 계정과 프로젝트에 연결된 학습 환경을 시작합니다.',disabled:!status.permissions.can_prepare};
  if (!status.dataset) return {kind:'prepare',label:'학습용 데이터 준비',reason:'저장한 데이터 버전과 분할을 복사해 이번 학습의 기준으로 고정합니다.',disabled:!status.permissions.can_prepare};
  if (status.models.length) return {kind:'results',label:'모델 결과 확인',reason:'저장된 모델, 평가 기록과 사진 예측을 확인하세요.'};
  const architecture = status.architectures.find(row => row.id === options.architectureId && row.supported && row.task === trainingTask)
    ?? status.architectures.find(row => row.supported && row.task === trainingTask);
  if (!architecture) return {kind:'native',label:'기존 모델 도구 열기',reason:'이 환경에서 지원하는 모델 구조를 찾지 못했습니다. 기존 모델군도 사용할 수 있습니다.'};
  if (architecture.weights_ready !== true) return {kind:'weights',label:'모델 가중치 준비',reason:'선택한 공개 모델의 가중치를 내려받습니다. 표시된 라이선스와 출처를 먼저 확인하세요.',disabled:!status.permissions.can_train};
  return {kind:'train',label:'학습 시작',reason:'고정된 데이터와 선택한 모델 설정으로 학습 작업 한 건을 제출합니다.',disabled:!status.permissions.can_train};
}

type Project = Pick<ProjectConfig, 'id' | 'name' | 'task' | 'source_dataset_dir' | 'active_labelset_id'>;
export interface PlatformWorkspaceState {
  status: PlatformStatus | null; loading: boolean; busy: string | null; error: string; notice: string;
  uncertain: boolean; architectureId: string; epochs: string; epochsError: string;
  modelId: string; variantId: string; images: PlatformSourceImage[]; imageId: string;
  imageLoading: boolean; imageError: string; nextImageOffset: number | null; imagesLoaded: boolean;
  prediction: PlatformPrediction | null; adoptionReview: boolean;
  adopted: {dataset_version_id: string; labelset_id: string} | null; jobLimit: number;
  targetTask: string;
}
function errorText(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  if (cause && typeof cause === 'object') {
    const value = cause as {message?: unknown; detail?: unknown};
    if (typeof value.message === 'string') return value.message;
    if (typeof value.detail === 'string') return value.detail;
  }
  return '요청 결과를 확인하지 못했습니다. 상태를 다시 확인하세요.';
}
function operationId(): string { return crypto.randomUUID(); }
function refused(cause: unknown): boolean {
  const code = cause && typeof cause === 'object' ? (cause as {status?:unknown}).status : null;
  return typeof code === 'number' && code >= 400 && code < 500;
}
const prefix = '/api/platform/geti';
function withinSource(path:string, root:string):boolean {
  const normal = (value:string) => value.replace(/\\/g,'/').replace(/\/+$/,'');
  let candidate = normal(path), source = normal(root);
  if (!candidate.startsWith('/') && !/^[A-Za-z]:\//.test(candidate)) return false;
  if (candidate.split('/').some(part => part === '.' || part === '..')) return false;
  if (/^[A-Za-z]:\//.test(source) || source.startsWith('//')) {candidate=candidate.toLowerCase();source=source.toLowerCase();}
  return !!source && candidate.startsWith(source + '/');
}

/** An in-memory view of the server ledger. It never schedules or resumes a mutation on mount. */
export class GetiPlatformController {
  private value: PlatformWorkspaceState = {status:null,loading:false,busy:null,error:'',notice:'',uncertain:false,
    architectureId:'',epochs:'5',epochsError:'',modelId:'',variantId:'',images:[],imageId:'',imageLoading:false,
    imageError:'',nextImageOffset:null,imagesLoaded:false,prediction:null,adoptionReview:false,adopted:null,jobLimit:10,targetTask:''};
  private listeners = new Set<() => void>();
  private abort = new AbortController();
  private receiptAbort = new AbortController();
  private predictionAbort = new AbortController();
  private receiptEpoch = 0;
  private predictionRead = 0;
  private context: ProjectContext | null;
  private diagnosticScope: string;
  private pendingSubmission: string | null = null;
  private pendingJobId: string | null = null;
  private prepareOperation: string | null = null;
  private adoptionOperation: string | null = null;
  private disposed = false;
  private imageRead = 0;
  private downloadUrls = new Set<string>();
  constructor(private options: {project: Project; inputRecipe: FixedInputRecipe | null; isCurrent: () => boolean}) {
    this.context = getProjectContext(); this.diagnosticScope = getApiDiagnosticScope();
  }
  getSnapshot = (): PlatformWorkspaceState => this.value;
  subscribe = (listener: () => void): (() => void) => {this.listeners.add(listener);return () => {this.listeners.delete(listener);};};
  private current(): boolean {return !this.disposed && this.options.isCurrent() && this.diagnosticScope === getApiDiagnosticScope();}
  private update(change: Partial<PlatformWorkspaceState>): void {
    if (!this.current()) return;
    this.value = {...this.value, ...change}; this.listeners.forEach(listener => listener());
  }
  private call<T>(path: string, method = 'GET', data?: unknown, blob = false, signal = this.abort.signal): Promise<T> {
    if (!this.current()) return Promise.reject(new Error('계정이나 프로젝트가 바뀌었습니다. 현재 작업을 다시 확인하세요.'));
    return request<T>(path, {method,projectContext:this.context,signal,
      ...(data === undefined ? {} : {body:JSON.stringify(data)}),...(blob ? {responseType:'blob' as const} : {})});
  }
  dispose(): void {
    this.disposed = true; this.abort.abort(); this.receiptAbort.abort(); this.predictionAbort.abort(); this.listeners.clear();
    this.downloadUrls.forEach(url => URL.revokeObjectURL(url));this.downloadUrls.clear();
  }
  private sameReceipt(epoch:number, identity:string | null):boolean {
    return this.current() && epoch === this.receiptEpoch && identity === receiptIdentity(this.value.status?.dataset);
  }
  private retirePrediction():void {
    this.predictionRead += 1;this.predictionAbort.abort();this.predictionAbort = new AbortController();
  }
  private applyStatus(status: PlatformStatus): void {
    if (!this.current()) return;
    const changedReceipt = receiptIdentity(this.value.status?.dataset) !== receiptIdentity(status.dataset);
    const retired:Partial<PlatformWorkspaceState> = {};
    if (changedReceipt) {
      this.receiptEpoch += 1;this.imageRead += 1;
      this.receiptAbort.abort();this.receiptAbort = new AbortController();this.retirePrediction();
      this.adoptionOperation = null;
      Object.assign(retired,{adoptionReview:false,adopted:null,images:[],imageId:'',imageLoading:false,
        imageError:'',nextImageOffset:null,imagesLoaded:false,prediction:null,notice:''});
    }
    const targetTask = status.dataset?.source_task === 'segmentation' && status.dataset.task === 'instance_segmentation' ? status.dataset.task
      : changedReceipt && !status.dataset ? '' : this.value.targetTask;
    const task = platformTrainingTask(status,targetTask);
    const architectureId = status.architectures.some(row => row.id === this.value.architectureId && row.supported && row.task === task)
      ? this.value.architectureId : status.architectures.find(row => row.supported && row.task === task)?.id ?? '';
    const model = (changedReceipt ? undefined : status.models.find(row => row.id === this.value.modelId)) ?? status.models[0];
    const sameModel = !changedReceipt && model?.id === this.value.modelId;
    const variantId = sameModel && model?.variants.some(row => row.id === this.value.variantId) ? this.value.variantId : model?.variants[0]?.id ?? '';
    if (!changedReceipt && (model?.id !== this.value.modelId || variantId !== this.value.variantId)) {
      this.retirePrediction();retired.prediction = null;
    }
    const reconciled = (!!this.pendingSubmission || !!this.pendingJobId) && status.jobs.some(job =>
      (job.submission_id === this.pendingSubmission || job.operation_id === this.pendingSubmission || job.id === this.pendingJobId) && !['submission_unconfirmed','unknown','uncertain'].includes(job.status));
    if (reconciled) {this.pendingSubmission = null;this.pendingJobId = null;}
    this.update({...retired,status,architectureId,targetTask,modelId:model?.id ?? '',variantId,
      ...(reconciled ? {uncertain:false,error:'',notice:'서버에 저장된 학습 제출 결과를 확인했습니다.'} : {})});
  }
  async refresh(): Promise<void> {
    if (!this.current() || this.value.loading || this.value.busy) return;
    this.update({loading:true,...(!this.value.uncertain ? {error:''} : {})});
    try {const status = await this.call<PlatformStatus>(`${prefix}/status`);if (this.current()) this.applyStatus(status);}
    catch (cause) {this.update({error:errorText(cause)});}
    finally {this.update({loading:false});}
  }
  private async mutate(kind: string, action: () => Promise<void>, resultCurrent?:() => boolean): Promise<void> {
    if (!this.current() || this.value.busy || this.value.loading) return;
    const recovery = ['job','cancel','export','navigate','recover'].includes(kind);
    if (!recovery && (this.value.error || this.value.status?.runtime.degraded)) return;
    this.update({busy:kind,...(recovery ? {} : {error:''}),notice:''});
    try {await action();}
    catch (cause) {if (!resultCurrent || resultCurrent()) this.update({error:errorText(cause)});}
    finally {this.update({busy:null});}
  }
  async start(): Promise<void> {
    if (!this.value.status?.permissions.can_prepare || !platformTargetSupported(this.value.status,this.value.targetTask) || !this.value.status.available || ['running','starting','unavailable'].includes(this.value.status.runtime.state)) return;
    await this.mutate('start', async () => {
      const status = await this.call<PlatformStatus>(`${prefix}/start`, 'POST', {});
      this.applyStatus(status);this.update({notice:status.runtime.state === 'running' ? '학습 환경을 시작했습니다.' : '학습 환경의 시작 상태를 확인하세요.'});
    });
  }
  async prepare(): Promise<void> {
    const status = this.value.status;
    if (!status?.permissions.can_prepare || !platformTargetSupported(status,this.value.targetTask) || status.runtime.state !== 'running' || !this.options.project.source_dataset_dir || this.value.uncertain || status.jobs.some(platformJobActive)) return;
    const targetTask = platformTrainingTask(status,this.value.targetTask);
    if (targetTask === 'instance_segmentation' && this.options.inputRecipe?.operations.length) {this.update({error:'개체별 분할은 저장한 고정 전처리를 지원하지 않습니다. 전처리를 비우거나 기존 시맨틱 분할 도구를 사용하세요.'});return;}
    let resultEpoch = this.receiptEpoch, resultIdentity = receiptIdentity(status.dataset);
    await this.mutate('prepare', async () => {
      this.prepareOperation ??= operationId();
      try {
        const dataset = await this.call<PlatformDatasetReceipt>(`${prefix}/prepare`, 'POST', {operation_id:this.prepareOperation,
          ...(targetTask === 'instance_segmentation' && status.project.task === 'segmentation' ? {target_task:targetTask} : {}),
          ...(this.options.inputRecipe ? {input_recipe:this.options.inputRecipe} : {})});
        this.prepareOperation = null;
        if (!this.sameReceipt(resultEpoch,resultIdentity)) return;
        this.applyStatus({...status,dataset,models:[],project:{...status.project,linked:true,supported:true,binding_state:undefined,binding_message:undefined}});
        resultEpoch = this.receiptEpoch;resultIdentity = receiptIdentity(dataset);
        this.update({notice:'학습용 복사본과 고정된 분할을 준비했습니다.'});
        if (targetTask !== status.project.task) {
          const next = await this.call<PlatformStatus>(`${prefix}/status`);
          if (this.sameReceipt(resultEpoch,resultIdentity)) this.applyStatus(next);
        }
      } catch (cause) {if (refused(cause)) this.prepareOperation = null;throw cause;}
    },() => this.sameReceipt(resultEpoch,resultIdentity));
  }
  selectTargetTask(target:string | null):void {
    const status = this.value.status;
    if (!status || this.value.busy || this.value.loading || this.value.uncertain || status.dataset || status.project.task !== 'segmentation') return;
    if (target !== null && (target !== 'instance_segmentation' || !status.project.target_task_options?.includes(target))) return;
    this.update({targetTask:target ?? '',architectureId:'',error:''});
    this.prepareOperation = null;
  }
  chooseArchitecture(id: string): void {
    if (this.value.busy || this.value.uncertain || !this.value.status?.architectures.some(row => row.id === id && row.supported && row.task === platformTrainingTask(this.value.status!,this.value.targetTask))) return;
    this.update({architectureId:id,epochsError:''});
  }
  setEpochs(epochs: string): void {if (!this.value.busy) this.update({epochs,epochsError:''});}
  async weights(): Promise<void> {
    const status = this.value.status, architecture = status?.architectures.find(row => row.id === this.value.architectureId && row.supported);
    if (!status?.permissions.can_train || !platformTargetSupported(status,this.value.targetTask) || !architecture || this.value.uncertain || status.jobs.some(platformJobActive)) return;
    await this.mutate('weights', async () => {
      const result = await this.call<{ready:boolean;architecture_id:string;message?:string}>(`${prefix}/weights`, 'POST', {operation_id:operationId(),architecture_id:architecture.id});
      this.update({status:{...status,architectures:status.architectures.map(row => row.id === result.architecture_id ? {...row,weights_ready:result.ready} : row)},notice:result.message || (result.ready ? '선택한 모델 가중치를 준비했습니다.' : '가중치 준비가 완료되지 않았습니다. 상세 안내를 확인하세요.')});
    });
  }
  async train(): Promise<void> {
    const status = this.value.status, architecture = status?.architectures.find(row => row.id === this.value.architectureId && row.supported && row.task === platformTrainingTask(status,this.value.targetTask));
    if (!status?.permissions.can_train || !platformTargetSupported(status,this.value.targetTask) || status.runtime.state !== 'running' || !status.dataset || !architecture || architecture.weights_ready !== true || this.value.uncertain || status.jobs.some(platformJobActive)) return;
    const maxEpochs = Number(this.value.epochs);
    if (!/^\d+$/.test(this.value.epochs) || !Number.isSafeInteger(maxEpochs) || maxEpochs < 1 || maxEpochs > 1000) {
      this.update({epochsError:'학습 횟수를 1~1,000 사이의 정수로 입력하세요.'});return;
    }
    await this.mutate('train', async () => {
      const id = operationId();this.pendingSubmission = id;
      try {
        const job = await this.call<PlatformJob>(`${prefix}/train`, 'POST', {operation_id:id,architecture_id:architecture.id,max_epochs:maxEpochs});
        if (job.status !== 'submission_unconfirmed') this.pendingSubmission = null;
        else this.pendingJobId = job.id;
        this.update({status:{...status,jobs:[job,...status.jobs.filter(row => row.id !== job.id)]},notice:'학습 작업을 제출했습니다. 완료 여부는 진행 상태에서 확인하세요.',uncertain:job.status === 'submission_unconfirmed'});
      } catch (cause) {
        if (refused(cause)) this.pendingSubmission = null;
        else {this.update({uncertain:true});throw new Error(`학습 제출 결과를 확인하지 못했습니다. 저장 상태를 확인하세요. ${errorText(cause)}`);}
        throw cause;
      }
    });
  }
  async refreshJob(id: string): Promise<void> {
    if (!this.value.status?.jobs.some(row => row.id === id)) return;
    await this.mutate('job', async () => {
      const job = await this.call<PlatformJob>(`${prefix}/jobs/${encodeURIComponent(id)}`);
      const status = this.value.status;if (status) this.update({status:{...status,jobs:status.jobs.map(row => row.id === id ? job : row)}});
    });
  }
  showMoreJobs(): void {this.update({jobLimit:this.value.jobLimit + 10});}
  async navigate(step:number, open:(step:number) => void | Promise<boolean | void>): Promise<void> {
    if (!Number.isInteger(step) || step < 1 || step > 6) return;
    await this.mutate('navigate', async () => {
      if (await open(step) === false) throw new Error('화면을 열지 못했습니다. 현재 편집 내용이 저장됐는지 기존 작업대에서 확인하세요.');
    });
  }
  async cancel(id: string): Promise<void> {
    const job = this.value.status?.jobs.find(row => row.id === id);
    if (!this.value.status?.permissions.can_train || !job || !platformJobActive(job) || job.cancel_requested || job.status === 'submission_unconfirmed' || ['cancel_requested','cancelling','stopping'].includes(job.status)) return;
    await this.mutate('cancel', async () => {
      const next = await this.call<PlatformJob>(`${prefix}/jobs/${encodeURIComponent(id)}/cancel`, 'POST', {});
      const status = this.value.status;if (status) this.update({status:{...status,jobs:status.jobs.map(row => row.id === id ? next : row)},notice:'중단을 요청했습니다. 작업자 종료가 확인될 때까지 진행 상태를 확인하세요.'});
    });
  }
  async recover(): Promise<void> {
    const status = this.value.status;
    if (!status?.permissions.can_train || !(this.value.uncertain || status.jobs.some(job => ['submission_unconfirmed','unknown','uncertain'].includes(job.status)))) return;
    await this.mutate('recover', async () => {
      const next = await this.call<PlatformStatus>(`${prefix}/recover`,'POST',{});
      this.applyStatus(next);
      if (next.runtime.state === 'stopped' && !next.jobs.some(platformJobActive)) {
        this.pendingSubmission = null;this.pendingJobId = null;
        this.update({uncertain:false,error:'',notice:'실행 환경과 미확인 작업의 종료를 확인했습니다. 새 학습은 별도로 시작해야 합니다.'});
      } else this.update({notice:'실행 환경 정리 결과를 확인했습니다. 종료가 확인되지 않은 작업은 계속 표시합니다.'});
    });
  }
  selectModel(id: string): void {
    if (this.value.busy) return;
    const model = this.value.status?.models.find(row => row.id === id);if (!model) return;
    this.retirePrediction();this.update({modelId:id,variantId:model.variants[0]?.id ?? '',prediction:null});
  }
  selectVariant(id: string): void {
    if (!this.value.busy && this.value.status?.models.find(row => row.id === this.value.modelId)?.variants.some(row => row.id === id)) {
      this.retirePrediction();this.update({variantId:id,prediction:null});
    }
  }
  selectImage(id: string): void {if (!this.value.busy) {this.retirePrediction();this.update({imageId:this.value.images.some(row => row.image_uuid === id) ? id : '',prediction:null});}}
  async loadImages(more = false): Promise<void> {
    if (!this.current() || this.value.imageLoading || !this.value.status?.dataset) return;
    const receipt = this.value.status.dataset, generation = ++this.imageRead;
    const epoch = this.receiptEpoch, identity = receiptIdentity(receipt);
    const sameRead = () => this.sameReceipt(epoch,identity) && generation === this.imageRead;
    this.update({imageLoading:true,imageError:''});
    try {
      const offset = more ? this.value.nextImageOffset ?? 0 : 0;
      const query = new URLSearchParams({offset:String(offset),limit:'100'});
      const page = await this.call<PlatformSourcePage>(`${prefix}/images?${query}`,'GET',undefined,false,this.receiptAbort.signal);
      if (!sameRead()) return;
      const pagination = page.pagination;
      if (!Array.isArray(page.items) || !pagination || pagination.offset !== offset
        || !Number.isSafeInteger(pagination.limit) || pagination.limit < 1 || pagination.limit > 100
        || !Number.isSafeInteger(pagination.total) || pagination.total < offset + page.items.length || page.items.length > pagination.limit) {
        throw new Error('사진 목록의 페이지 기준이 달라졌습니다. 사진 목록을 다시 확인하세요.');
      }
      const allowed = new Map(receipt.source_mapping.map(row => [row.relative_path,row]));
      const items = page.items.filter(row => typeof row.image_uuid === 'string' && !!row.image_uuid && typeof row.sha256 === 'string' && !!row.sha256
        && typeof row.source_path === 'string' && withinSource(row.source_path,this.options.project.source_dataset_dir || '')
        && (row.transferred === true ? allowed.get(row.relative_path)?.sha256 === row.sha256 && allowed.get(row.relative_path)?.media_id === row.image_uuid : row.transferred === false && !allowed.has(row.relative_path)));
      items.sort((a,b) => Number(b.transferred && allowed.get(b.relative_path)?.subset === 'testing') - Number(a.transferred && allowed.get(a.relative_path)?.subset === 'testing'));
      const images = more ? [...this.value.images,...items.filter(row => !this.value.images.some(old => old.image_uuid === row.image_uuid))] : items;
      const next = page.pagination.offset + page.pagination.limit;
      const nextImageOffset = Number.isSafeInteger(next) && next > page.pagination.offset && next < page.pagination.total ? next : null;
      this.retirePrediction();
      this.update({images,nextImageOffset,imagesLoaded:true,imageId:images.some(row => row.image_uuid === this.value.imageId) ? this.value.imageId : images[0]?.image_uuid ?? '',prediction:null});
    } catch (cause) {if (sameRead()) this.update({imageError:errorText(cause)});}
    finally {if (sameRead()) this.update({imageLoading:false});}
  }
  async predict(): Promise<void> {
    const model = this.value.status?.models.find(row => row.id === this.value.modelId);
    const image = this.value.images.find(row => row.image_uuid === this.value.imageId);
    if (!this.value.status?.permissions.can_train || !model?.variants.some(row => row.id === this.value.variantId && row.prediction_supported !== false) || !image || this.value.uncertain) return;
    const epoch = this.receiptEpoch, identity = receiptIdentity(this.value.status.dataset), variantId = this.value.variantId;
    let generation = this.predictionRead;
    const samePrediction = () => this.sameReceipt(epoch,identity) && generation === this.predictionRead
      && this.value.modelId === model.id && this.value.variantId === variantId && this.value.imageId === image.image_uuid;
    await this.mutate('predict', async () => {
      generation = ++this.predictionRead;
      const prediction = await this.call<PlatformPrediction>(`${prefix}/predict`, 'POST', {model_id:model.id,variant_id:variantId,image_path:image.source_path},false,this.predictionAbort.signal);
      if (samePrediction()) this.update({prediction,notice:'선택한 사진의 예측 응답을 받았습니다. 이 결과는 독립 시험 평가나 정답 채택이 아닙니다.'});
    },samePrediction);
  }
  async exportModel(): Promise<void> {
    const model = this.value.status?.models.find(row => row.id === this.value.modelId);
    const variant = model?.variants.find(row => row.id === this.value.variantId);if (!model || !variant) return;
    await this.mutate('export', async () => {
      const blob = await this.call<Blob>(`${prefix}/models/${encodeURIComponent(model.id)}/export/${encodeURIComponent(variant.id)}`, 'GET', undefined, true);
      if (!this.current()) return;
      const url = URL.createObjectURL(blob);this.downloadUrls.add(url);
      const anchor = document.createElement('a');anchor.href=url;anchor.download=`model-${model.id.replace(/[^a-zA-Z0-9_-]/g,'_')}-${variant.id.replace(/[^a-zA-Z0-9_-]/g,'_')}.zip`;anchor.rel='noreferrer';anchor.click();
      this.update({notice:'모델 파일을 브라우저 다운로드로 전달했습니다. 파일 저장은 다운로드 목록에서 확인하세요.'});
      // Keep the object URL alive while the browser takes ownership; release on close as well.
      setTimeout(() => {URL.revokeObjectURL(url);this.downloadUrls.delete(url);},60_000);
    });
  }
  reviewAdoption(value: boolean): void {if (!this.value.busy && this.value.status?.permissions.can_adopt) this.update({adoptionReview:value});}
  async adoptLabels(): Promise<void> {
    const receipt = this.value.status?.dataset;
    if (!this.value.status?.permissions.can_adopt || !this.value.adoptionReview || !receipt?.revision || this.value.uncertain || this.value.adopted || this.value.status.jobs.some(platformJobActive)) return;
    const epoch = this.receiptEpoch, identity = receiptIdentity(receipt);
    await this.mutate('adopt', async () => {
      const id = this.adoptionOperation ??= operationId();
      try {
        const adopted = await this.call<{dataset_version_id:string;labelset_id:string}>(`${prefix}/adopt-labels`, 'POST', {operation_id:id,expected_revision:receipt.revision});
        if (!this.sameReceipt(epoch,identity)) return;
        this.adoptionOperation = null;
        this.update({adopted,adoptionReview:false,notice:'검토한 라벨을 새 라벨셋과 데이터 버전으로 저장했습니다. 라벨 화면에서 선택해 확인하세요.'});
      } catch (cause) {if (this.sameReceipt(epoch,identity) && refused(cause)) this.adoptionOperation = null;throw cause;}
    },() => this.sameReceipt(epoch,identity));
  }
  async useAdoptedLabels(activate:(id:string) => Promise<boolean>): Promise<void> {
    if (!this.value.adopted || !this.value.status?.permissions.can_adopt) return;
    const id = this.value.adopted.labelset_id;
    await this.mutate('activate', async () => {
      if (!await activate(id)) throw new Error('새 라벨을 선택하지 못했습니다. 기존 편집 저장과 프로젝트 상태를 확인한 뒤 다시 선택하세요.');
      this.update({notice:'채택한 라벨셋을 선택했습니다.'});
    });
  }
}
