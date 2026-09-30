import { getFlowchartModelTask } from '../flowchart/flowchartStartup';
import type { FlowchartExecutionResult, FlowchartPipeline, FlowModelTask, ImageMeta, VisionTask } from '../../types';

export type BatchScope = 'test' | 'val' | 'train' | 'all';
export type BatchRowState = 'pending' | 'running' | 'OK' | 'NG' | 'REVIEW' | 'error' | 'skipped';
export type BatchStopReason = 'user_stop' | 'source_changed' | null;

export interface BatchInspectionRow {
  image: ImageMeta;
  state: BatchRowState;
  result?: FlowchartExecutionResult;
  image_sha256?: string | null;
  error?: string;
  review?: InspectionReview | null;
  reviews?: InspectionReview[];
}

export interface InspectionReview {
  review_id: string;
  final_verdict: 'OK' | 'NG' | 'REVIEW';
  reason: string;
  reviewer: string;
  created_at: string;
}

export interface BatchInspectionReport {
  run_id?: string;
  source_folder: string;
  /** Server-resolved identity; source_folder retains the original run evidence. */
  canonical_source_folder?: string | null;
  task: VisionTask;
  scope: BatchScope;
  pipeline_id: string;
  pipeline_name: string;
  /** Server-verified identity of this run; absent on legacy pre-provenance runs. */
  saved_version_id?: string | null;
  pipeline_hash?: string;
  model_sha256?: Record<string, string>;
  execution_config?: { execution_target: 'local' | 'selected_compute' | 'model_compute'; device: string;
    compute_profile_id?: string | null; compute_profile_name?: string | null; profile?: {name: string}; };
  status: 'running' | 'completed' | 'stopped';
  rows: BatchInspectionRow[];
}

export interface InspectionHistoryRun extends BatchInspectionReport {
  run_id: string;
  pipeline_hash: string;
  created_at: string;
  updated_at: string;
}

export interface InspectionRunSummary {
  run_id: string;
  source_folder: string;
  task: VisionTask;
  scope: BatchScope;
  pipeline_name: string;
  saved_version_id: string | null;
  pipeline_hash: string;
  model_sha256: Record<string, string>;
  status: 'running' | 'completed' | 'stopped';
  created_at: string;
  total: number;
  counts: Record<string, number>;
}

export interface BatchInspectionSummary {
  total: number;
  processed: number;
  ok: number;
  ng: number;
  review: number;
  errors: number;
  unrun: number;
}

export type BatchFilter = 'all' | 'OK' | 'NG' | 'REVIEW' | 'error' | 'unrun';

export interface BatchInspectionOptions {
  sourceFolder: string;
  task: VisionTask;
  scope: BatchScope;
  pipeline?: FlowchartPipeline;
  stopReason?: () => BatchStopReason;
  onRunCreated?: (runId: string) => void;
  onUpdate?: (report: BatchInspectionReport) => void;
}

export interface BatchSourceState {
  folderPath: string;
  projectDir?: string | null;
  task: VisionTask;
  datasetKey: string | null;
  contextRevision: number;
  hasSelectedFolder: boolean;
  importError: string | null;
  sourceSaveError?: string | null;
  isLoading: boolean;
  isSplitting: boolean;
}

export interface InspectionHistoryContext extends Pick<BatchSourceState, 'folderPath' | 'projectDir' | 'task'> {
  canonicalSourceFolder?: string | null;
  sourceReady?: boolean;
}

export function createInspectionHistoryContext(
  source: BatchSourceState,
  project: { project_dir: string; source_dataset_dir: string | null; task: VisionTask } | null,
): InspectionHistoryContext {
  const sourceReady = isBatchSourceReady(source);
  const canonicalTrusted = sourceReady && !source.sourceSaveError
    && project?.project_dir === source.projectDir && project?.task === source.task;
  return {
    folderPath: source.folderPath, projectDir: source.projectDir, task: source.task,
    canonicalSourceFolder: canonicalTrusted ? project?.source_dataset_dir || null : null,
    sourceReady,
  };
}

export function isBatchSourceReady(source: BatchSourceState): boolean {
  return source.hasSelectedFolder && !source.importError && !source.isLoading && !source.isSplitting
    && source.datasetKey === `${source.folderPath}\0${source.task}`;
}

export function isBatchSourceCurrent(current: BatchSourceState, started: BatchSourceState): boolean {
  return isBatchSourceReady(current)
    && current.folderPath === started.folderPath
    && current.projectDir === started.projectDir
    && current.task === started.task
    && current.contextRevision === started.contextRevision;
}

export function isInspectionHistoryContextCurrent(
  current: InspectionHistoryContext,
  started: InspectionHistoryContext,
): boolean {
  return current.folderPath === started.folderPath
    && current.projectDir === started.projectDir
    && current.task === started.task
    && current.canonicalSourceFolder === started.canonicalSourceFolder
    && current.sourceReady === started.sourceReady;
}

export function inspectionRunMatchesSource(
  run: Pick<BatchInspectionReport, 'source_folder' | 'canonical_source_folder' | 'task'>,
  source: Pick<InspectionHistoryContext, 'folderPath' | 'task' | 'canonicalSourceFolder'>,
): boolean {
  const runCanonical = run.canonical_source_folder;
  const sourceCanonical = source.canonicalSourceFolder;
  const canonicalTrusted = typeof runCanonical === 'string' && runCanonical.length > 0
    && typeof sourceCanonical === 'string' && sourceCanonical.length > 0;
  return run.task === source.task && (canonicalTrusted
    ? runCanonical === sourceCanonical : run.source_folder === source.folderPath);
}

export function batchSourceResetKey(source: BatchSourceState): string {
  return JSON.stringify([
    source.folderPath, source.projectDir, source.task, source.datasetKey, source.contextRevision,
    source.isLoading, source.isSplitting, source.importError,
  ]);
}

export interface BatchInspectionApi {
  getActivePipeline?: (sourceFolder: string) => Promise<FlowchartPipeline>;
  getPipeline: (task: VisionTask, sourceFolder: string) => Promise<FlowchartPipeline>;
  verifyModels: (request: { source_dataset_path: string; models: Array<{ job_id: string; task: FlowModelTask }> }) => Promise<unknown>;
  getImages: (params: {
    folder_path: string;
    task: VisionTask;
    limit: number;
    offset: number;
    split?: string;
  }) => Promise<{ total: number; items: ImageMeta[] }>;
  run: (request: { image_path: string; image_id: string; pipeline: FlowchartPipeline }) => Promise<FlowchartExecutionResult>;
  /** Server executes the saved run graph and writes its own result atomically. */
  executeRow?: (runId: string, imagePath: string) => Promise<FlowchartExecutionResult>;
  createRun?: (report: BatchInspectionReport, pipeline: FlowchartPipeline) => Promise<string>;
  recordRow?: (runId: string, row: BatchInspectionRow) => Promise<unknown>;
  finishRun?: (runId: string, status: 'completed' | 'stopped') => Promise<unknown>;
}

const PAGE_SIZE = 500;

function modelReferences(pipeline: FlowchartPipeline): Array<{ job_id: string; task: FlowModelTask }> {
  const references: Array<{ job_id: string; task: FlowModelTask }> = [];
  for (const node of pipeline.nodes) {
    const task = getFlowchartModelTask(node);
    if (task === null) continue;
    const jobId = node.data.model_job_id;
    if (!jobId) {
      throw new Error(`검사 플로우의 '${node.data.label || node.id}' 모델 연결을 확인하세요.`);
    }
    references.push({ job_id: jobId, task: task as FlowModelTask });
  }
  if (references.length === 0) throw new Error('검사 플로우에 학습 모델이 없습니다. 5단계에서 모델을 연결하고 저장하세요.');
  return references;
}

function verifyResult(item: ImageMeta, value: FlowchartExecutionResult): FlowchartExecutionResult {
  if (!value || value.image_path !== item.file_path || value.image_id !== item.image_id) {
    throw new Error('반환 결과의 원본 이미지 경로 또는 ID가 요청과 다릅니다. 해당 결과를 판정에 사용하지 않았습니다.');
  }
  if (!['OK', 'NG', 'REVIEW'].includes(value.final_verdict)
    || !['success', 'review'].includes(value.status)
    || !Array.isArray(value.execution_steps) || value.execution_steps.length === 0
    || !Array.isArray(value.crops)) {
    throw new Error('검사 결과의 판정 또는 중간 노드 기록이 누락되었습니다. 해당 결과를 판정에 사용하지 않았습니다.');
  }
  return value;
}

async function loadImages(options: BatchInspectionOptions, api: BatchInspectionApi): Promise<ImageMeta[]> {
  const items: ImageMeta[] = [];
  const seen = new Set<string>();
  let expectedTotal: number | null = null;
  while (expectedTotal === null || items.length < expectedTotal) {
    if (options.stopReason?.()) return [];
    const page = await api.getImages({
      folder_path: options.sourceFolder,
      task: options.task,
      limit: PAGE_SIZE,
      offset: items.length,
      split: options.scope === 'all' ? undefined : options.scope,
    });
    if (expectedTotal === null) expectedTotal = page.total;
    if (page.total !== expectedTotal || !Array.isArray(page.items)
      || (page.items.length === 0 && items.length < expectedTotal)) {
      throw new Error('이미지 목록이 읽는 동안 변경되거나 일부 페이지가 누락되었습니다. 데이터셋을 다시 불러오세요.');
    }
    for (const item of page.items) {
      if (!item.file_path || !item.image_id || seen.has(item.file_path)
        || (options.scope !== 'all' && item.split !== options.scope)) {
        throw new Error('이미지 목록에 원본 경로, ID 또는 분할 정보가 없거나 중복되었습니다. 검사를 시작하지 않았습니다.');
      }
      seen.add(item.file_path);
      items.push(item);
    }
    if (items.length > expectedTotal) throw new Error('이미지 목록의 개수가 변했습니다. 검사를 시작하지 않았습니다.');
  }
  if (items.length === 0) {
    throw new Error(options.scope === 'test'
      ? '저장된 test 분할 이미지가 없습니다. 1단계의 분할을 확인하거나 검사 범위를 직접 바꾸세요.'
      : '선택한 범위에 검사할 이미지가 없습니다.');
  }
  return items;
}

export function summarizeBatch(rows: BatchInspectionRow[]): BatchInspectionSummary {
  const summary: BatchInspectionSummary = {
    total: rows.length, processed: 0, ok: 0, ng: 0, review: 0, errors: 0, unrun: 0,
  };
  for (const row of rows) {
    if (row.state === 'OK') { summary.ok += 1; summary.processed += 1; }
    else if (row.state === 'NG') { summary.ng += 1; summary.processed += 1; }
    else if (row.state === 'REVIEW') { summary.review += 1; summary.processed += 1; }
    else if (row.state === 'error') summary.errors += 1;
    else summary.unrun += 1;
  }
  return summary;
}

export function filterBatchRows(rows: BatchInspectionRow[], filter: BatchFilter): BatchInspectionRow[] {
  if (filter === 'all') return rows;
  if (filter === 'unrun') {
    return rows.filter((row) => row.state === 'pending' || row.state === 'running' || row.state === 'skipped');
  }
  return rows.filter((row) => row.state === filter);
}

/** Dispatch a small stop request while the renderer is closing or hot reloading. */
export function stopInspectionRunKeepalive(
  runId: string,
  baseUrl: string,
  send: typeof fetch = fetch,
): Promise<Response> {
  return send(`${baseUrl}/api/inspections/runs/${encodeURIComponent(runId)}/finish`, {
    method: 'PUT',
    keepalive: true,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'stopped' }),
  });
}

/** Own one run ID so unmount/pagehide cannot finish another inspector's run. */
export function createInspectionRunExitGuard(stopRun: (runId: string) => void) {
  let ownedRunId: string | null = null;
  let closed = false;
  let stopSent = false;
  const sendStop = () => {
    if (closed && ownedRunId && !stopSent) {
      stopSent = true;
      stopRun(ownedRunId);
    }
  };
  return {
    created(runId: string) {
      if (ownedRunId && ownedRunId !== runId) throw new Error('Inspection run ownership changed.');
      ownedRunId = runId;
      sendStop();
    },
    close() {
      closed = true;
      sendStop();
    },
    release(runId: string) {
      if (ownedRunId === runId) ownedRunId = null;
    },
  };
}

/** Executes the saved source-scoped flow sequentially so each remote result keeps its original image identity. */
export async function runBatchInspection(
  options: BatchInspectionOptions,
  api: BatchInspectionApi,
): Promise<BatchInspectionReport> {
  let pipeline: FlowchartPipeline;
  if (options.pipeline) {
    pipeline = options.pipeline;
  } else if (api.getActivePipeline) {
    try {
      pipeline = await api.getActivePipeline(options.sourceFolder);
    } catch (error) {
      if ((error as { status?: number })?.status !== 404) throw error;
      pipeline = await api.getPipeline(options.task, options.sourceFolder);
    }
  } else {
    pipeline = await api.getPipeline(options.task, options.sourceFolder);
  }
  if (options.stopReason?.()) throw new Error('검사 시작 전에 중단되었습니다.');
  const models = modelReferences(pipeline);
  await api.verifyModels({ source_dataset_path: options.sourceFolder, models });
  if (options.stopReason?.()) throw new Error('검사 시작 전에 중단되었습니다.');
  const images = await loadImages(options, api);
  if (options.stopReason?.()) throw new Error('검사 시작 전에 중단되었습니다.');

  const report: BatchInspectionReport = {
    source_folder: options.sourceFolder,
    task: options.task,
    scope: options.scope,
    pipeline_id: pipeline.id,
    pipeline_name: pipeline.name,
    status: 'running',
    rows: images.map((image) => ({ image, state: 'pending' })),
  };
  const publish = () => options.onUpdate?.({ ...report, rows: [...report.rows] });
  const stop = async () => {
    report.status = 'stopped';
    report.rows = report.rows.map((row) =>
      row.state === 'pending' || row.state === 'running' ? { image: row.image, state: 'skipped' } : row);
    if (report.run_id && api.finishRun) {
      try {
        await api.finishRun(report.run_id, 'stopped');
      } catch (error) {
        // A pagehide keepalive request may already have stopped this same run.
        if ((error as { status?: number })?.status !== 409) throw error;
      }
    }
    publish();
    return report;
  };
  if (api.createRun) {
    report.run_id = await api.createRun(report, pipeline);
    options.onRunCreated?.(report.run_id);
  }
  if (options.stopReason?.()) return await stop();
  publish();

  try {
    for (let index = 0; index < images.length; index += 1) {
      if (options.stopReason?.()) return await stop();
      const item = images[index];
      report.rows[index] = { image: item, state: 'running' };
      publish();
      try {
        const serverIssued = Boolean(report.run_id && api.executeRow);
        const value = serverIssued
          ? await api.executeRow!(report.run_id!, item.file_path)
          : await api.run({ image_path: item.file_path, image_id: item.image_id, pipeline });
        if (options.stopReason?.() === 'source_changed') return await stop();
        const result = verifyResult(item, value);
        report.rows[index] = { image: item, state: result.final_verdict, result };
      } catch (error) {
        if (options.stopReason?.() === 'source_changed') return await stop();
        report.rows[index] = {
          image: item, state: 'error',
          error: error instanceof Error ? error.message : String(error),
        };
      }
      if (report.run_id && api.recordRow && (!api.executeRow || report.rows[index].state === 'error')) {
        await api.recordRow(report.run_id, report.rows[index]);
      }
      publish();
    }
    report.status = 'completed';
    if (report.run_id && api.finishRun) await api.finishRun(report.run_id, 'completed');
    publish();
    return report;
  } catch (error) {
    if (report.run_id && api.finishRun && report.status === 'running') {
      try { await api.finishRun(report.run_id, 'stopped'); } catch { /* Keep the original error. */ }
    }
    throw error;
  }
}
