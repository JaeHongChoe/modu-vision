/** Read-only semantic projections for the assistant. Store observation is not a saved execution receipt. */
import {getApiDiagnosticScope, getProjectContext, getProjectContextGeneration, request} from './api';
import {apiDiagnosticFailures} from './apiDiagnostics';
import {diagnosticError, diagnosticText, type SafeDiagnosticError} from './assistantDiagnostics';
import {assistPanelErrors} from '../stores/assistContext';
import {useProjectStore} from '../stores/useProjectStore';
import {readDatasetAuthority, useDatasetStore} from '../stores/useDatasetStore';
import {useTrainingStore} from '../stores/useTrainingStore';
import {currentEvaluationGuidanceSource, useEvaluationStore} from '../stores/useEvaluationStore';
import {useFlowchartStore} from '../stores/useFlowchartStore';
import {useComputeStore} from '../stores/useComputeStore';
import {useAnnotationStore} from '../stores/useAnnotationStore';
import {useInspectionRunStore} from '../stores/useInspectionRunStore';

export const APP_DIAGNOSTIC_SECTIONS = ['overview', 'project', 'dataset', 'training', 'evaluation', 'flow', 'inference', 'compute', 'requests', 'jobs'] as const;
type Section = typeof APP_DIAGNOSTIC_SECTIONS[number];
type SafeError = SafeDiagnosticError & {source: string; section: string; source_owner: 'unknown'};
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : null;
const safeId = (value: unknown) => typeof value === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(value) ? value : null;
const safeWord = (value: unknown) => diagnosticText(value, 60);
function state() {
  const project = useProjectStore.getState(), data = useDatasetStore.getState(), context = getProjectContext();
  const projectReady = Boolean(project.project && project.projectDir && project.project.project_dir === project.projectDir
    && project.project.task === project.task && !project.isProjectBusy && (!context || context.project_id === project.project.id));
  const sourceReady = Boolean(projectReady && data.hasSelectedFolder && !data.isLoading && !data.isSplitting && !data.importError && !data.sourceSaveError
    && project.project?.source_dataset_dir === data.folderPath && data.datasetKey === `${data.folderPath}\0${project.task}`
    && data.lastImportedKey === data.datasetKey && data.sourceAuthority === readDatasetAuthority());
  return {project, data, context, projectReady, sourceReady};
}
function authority(): string {
  const {project, data, context} = state();
  return JSON.stringify([getApiDiagnosticScope(), getProjectContextGeneration(), context,
    project.projectDir, project.project?.id, project.project?.source_dataset_dir, project.project?.active_labelset_id, project.task,
    data.folderPath, data.datasetKey, data.lastImportedKey, data.sourceAuthority, useComputeStore.getState().transportRevision]);
}
function currentErrors(): SafeError[] {
  const p = useProjectStore.getState(), d = useDatasetStore.getState(), t = useTrainingStore.getState(), e = useEvaluationStore.getState(), f = useFlowchartStore.getState(), c = useComputeStore.getState(), a = useAnnotationStore.getState();
  const rows: SafeError[] = [];
  const add = (section: string, source: string, value: unknown) => {
    if (value === null || value === undefined || value === '') return;
    const error = diagnosticError(value);
    // Store fields do not retain the originating error's target/scope, so never attribute one to the selected profile.
    rows.push({section, source, source_owner: 'unknown', ...error, message: diagnosticText(error.message, 200)!});
  };
  add('project', 'project_selection', p.projectError); add('project', 'active_fault', p.activeError);
  add('dataset', 'dataset_import', d.importError); add('dataset', 'dataset_source_save', d.sourceSaveError); add('dataset', 'dataset_split', d.splitError);
  add('training', 'training_start_or_job', t.startError); add('training', 'training_status_read', t.jobStatusError); add('training', 'training_stop', t.stopError);
  add('evaluation', 'evaluation', e.errorMessage); add('evaluation', 'evaluation_remote_operation', e.remoteOperationError); add('evaluation', 'evaluation_heatmap', e.heatmapError);
  add('flow', 'flow', f.errorMessage); add('compute', 'compute_load', c.loadError); add('compute', 'compute_operation', c.error);
  add('dataset', 'annotation_read', a.annotationLoadError); add('dataset', 'annotation_auto_select', a.autoSelectError);
  for (const panel of assistPanelErrors()) add('panel', diagnosticText(panel.source, 48) || 'panel', panel.text);
  return rows;
}
function base(section: string, max: number) {
  return {section, observed_at: new Date().toISOString(), source_owner: 'unknown', partial: true, text_is_untrusted: true,
    limits: {max_characters: max, max_errors: max === 2000 ? 4 : 8, max_jobs: 6, max_requests: 8, request_history_capacity: 40,
      coverage: 'registered_store_errors_and_scoped_request_failures', raw_logs: false}};
}
/** Keep valid JSON; report omissions rather than truncating serialized text. */
function bounded(result: Record<string, unknown>, max: number): Record<string, unknown> {
  let omitted = typeof result.omitted === 'number' ? result.omitted : 0;
  const lists = ['errors', 'requests', 'jobs'];
  while (JSON.stringify({...result, omitted}).length > max) {
    const key = lists.find(name => Array.isArray(result[name]) && (result[name] as unknown[]).length);
    if (!key) break;
    (result[key] as unknown[]).pop(); omitted++;
  }
  return {...result, omitted};
}
/** Page metadata follows the rows actually emitted, including reductions needed for the JSON budget. */
function paged(result: Record<string, unknown>, field: 'errors' | 'requests' | 'jobs', total: number, offset: number, backendErrors?: number): Record<string, unknown> {
  const fields = backendErrors === undefined ? [field] : [field, 'errors'];
  const update = () => {
    const emitted = (result[field] as unknown[]).length;
    Object.assign(result, {offset, total, next_offset: offset + emitted < total ? offset + emitted : null,
      omitted: Math.max(0, total - emitted), remaining: Math.max(0, total - offset - emitted)});
    if (field === 'errors') result.error_count = total;
    if (backendErrors !== undefined) {
      const count = (result.errors as unknown[]).length;
      Object.assign(result, {error_count: backendErrors, error_offset: offset,
        error_next_offset: offset + count < backendErrors ? offset + count : null,
        errors_omitted: Math.max(0, backendErrors - count), errors_remaining: Math.max(0, backendErrors - offset - count)});
    }
  };
  update();
  while (JSON.stringify(result).length > 3600) {
    const removable = fields.filter(key => (result[key] as unknown[]).length > 1)
      .sort((left, right) => JSON.stringify(result[right]).length - JSON.stringify(result[left]).length);
    if (!removable.length) {
      // Never erase the last row of a nonempty page and strand its cursor. Identity/code/message remain readable.
      for (const key of fields) result[key] = (result[key] as Record<string, unknown>[]).map(row => {
        const compact: Record<string, unknown> = {};
        for (const name of ['source', 'section', 'source_owner', 'at', 'operation', 'kind', 'job_id', 'task', 'status', 'code', 'severity', 'historical', 'current_error_confirmed'])
          if (row[name] !== undefined) compact[name] = row[name];
        for (const name of ['message', 'cause', 'suggested_action']) if (typeof row[name] === 'string') compact[name] = diagnosticText(row[name], 80);
        if (row.error && typeof row.error === 'object') {
          const error = row.error as Record<string, unknown>;
          compact.error = {code: error.code ?? null, message: diagnosticText(error.message, 100), status: error.status ?? null};
        }
        return compact;
      });
      result.compacted = true;
      update();
      break;
    }
    (result[removable[0]] as unknown[]).pop();
    update();
  }
  return result;
}
function projectData() {
  const s = state();
  return {selected: Boolean(s.project.project), state: s.projectReady ? 'current' : 'unknown', source_owner: s.projectReady ? 'current' : 'unknown', task: safeWord(s.project.task),
    step: number(s.project.activeStep), backend_status: safeWord(s.project.backendStatus), busy: Boolean(s.project.isProjectBusy),
    source_state: s.sourceReady ? 'current' : 'unknown'};
}
export function appDiagnosticSummary(): Record<string, unknown> {
  const errors = currentErrors(), requests = apiDiagnosticFailures(getApiDiagnosticScope());
  return bounded({...base('overview', 2000), data: projectData(), error_count: errors.length, recent_request_failure_count: requests.length,
    errors: errors.slice(0, 4).map(row => ({...row, message: diagnosticText(row.message, 130)})), omitted: Math.max(0, errors.length - 4),
    details: 'read_app_diagnostics(section) reads current stores; jobs is a scoped read-only backend snapshot.'}, 2000);
}
function sectionData(section: Section): Record<string, unknown> {
  const s = state(), training = useTrainingStore.getState(), evaluation = useEvaluationStore.getState(), flow = useFlowchartStore.getState(), compute = useComputeStore.getState();
  if (section === 'project') return projectData();
  if (section === 'dataset') return {state: s.sourceReady ? 'current' : 'unknown', source_owner: s.sourceReady ? 'current' : 'unknown', importing: Boolean(s.data.isLoading), splitting: Boolean(s.data.isSplitting),
    images: s.sourceReady ? number(s.data.totalImages) : null, source_images: s.sourceReady ? number(s.data.sourceImages) : null,
    unlabeled: s.sourceReady ? number(s.data.unlabeledImages) : null,
    split: s.sourceReady ? {train: number(s.data.split.train), val: number(s.data.split.val), test: number(s.data.split.test)} : null,
    split_state: s.sourceReady ? safeWord(s.data.splitState) : null, class_count: s.sourceReady ? Object.keys(s.data.classes || {}).length : null,
    unsaved_annotation: Boolean(useAnnotationStore.getState().isDirty)};
  if (section === 'training') return {state: s.sourceReady && training.isCurrentData ? 'current_store_observation' : 'unknown', source_owner: s.sourceReady && training.isCurrentData ? 'current' : 'unknown', status: safeWord(training.status),
    job_id: s.sourceReady && training.isCurrentData ? safeId(training.jobId) : null, source_matches: s.sourceReady && training.isCurrentData,
    epoch: number(training.currentEpoch), total_epochs: number(training.totalEpochs), phase: safeWord(training.jobPhase), queue_position: number(training.jobQueuePosition),
    wait_reason: diagnosticText(training.jobWaitReason), observed_cause: safeWord(training.jobObservation?.cause), next_action: diagnosticText(training.jobObservation?.next_action),
    cancellation: training.jobObservation?.cancel ? {stage: safeWord(training.jobObservation.cancel.stage), complete: training.jobObservation.cancel.complete === true,
      exit_confirmed: training.jobObservation.cancel.exit_confirmed === true, reservation_released: typeof training.jobObservation.cancel.reservation_released === 'boolean' ? training.jobObservation.cancel.reservation_released : null} : null,
    completion_or_quality_verified: false};
  if (section === 'evaluation') {
    const source = currentEvaluationGuidanceSource();
    const owned = Boolean(s.sourceReady && source && source.jobId === evaluation.jobId && source.source.folderPath === s.data.folderPath
      && source.source.task === s.project.task && (source.source.labelsetId || 'default') === (s.project.project?.active_labelset_id || 'default'));
    return {state: owned ? 'current_store_observation' : 'unknown', source_owner: owned ? 'current' : 'unknown', loading: Boolean(evaluation.isLoading),
      job_id: owned ? safeId(evaluation.jobId) : null, predictions_loaded: owned ? evaluation.testPredictions?.length || 0 : null,
      threshold: owned ? number(evaluation.confidenceThreshold) : null, execution_receipt_present: Boolean(owned && evaluation.executionEvidence?.job_id === evaluation.jobId), quality_accepted: false};
  }
  // The flow store does not retain an ownership carrier for baseVersionId; a loaded source alone cannot prove it.
  if (section === 'flow') return {state: 'current_store_observation', source_owner: 'unknown', saved_version_id: null,
    unsaved: Boolean(flow.pipelineDirty), draft: Boolean(flow.pipelineIsDraft), running: Boolean(flow.isRunning), saving: Boolean(flow.isSaving), runtime_deployment_verified: false};
  if (section === 'inference') return {source_owner: 'unknown', running: useInspectionRunStore.getState().isRunning, result: 'unknown', hint: 'Read jobs for persisted inspection results; a running flag is not completion evidence.'};
  if (section === 'compute') return {selected_target: compute.selectedProfileId ? 'selected_server' : 'this_computer', loaded: Boolean(compute.isLoaded), loading: Boolean(compute.isLoading),
    source_owner: 'unknown', probe_in_progress: Boolean(compute.probePendingId), available_profiles: compute.profiles.length, error_owner: 'unknown', selected_target_failure_confirmed: false, connection_probed: false};
  return {};
}
function jobProjection(value: unknown): Record<string, unknown> {
  const row = value && typeof value === 'object' ? value as Record<string, unknown> : {}, rawObservation = row.observation;
  const observation = rawObservation && typeof rawObservation === 'object' ? rawObservation as Record<string, unknown> : {};
  const rawCancel = observation.cancel, cancel = rawCancel && typeof rawCancel === 'object' ? rawCancel as Record<string, unknown> : null;
  return {kind: safeWord(row.kind), job_id: safeId(row.job_id || row.run_id), task: safeWord(row.task), status: safeWord(row.status), phase: safeWord(row.phase),
    current_epoch: number(row.current_epoch), total_epochs: number(row.total_epochs), observed_state: safeWord(observation.state), cause: safeWord(observation.cause),
    next_action: diagnosticText(observation.next_action, 180), retryable: typeof observation.retryable === 'boolean' ? observation.retryable : null,
    error: row.error ? diagnosticError(row.error) : null, cancellation: cancel ? {stage: safeWord(cancel.stage), complete: cancel.complete === true,
      exit_confirmed: cancel.exit_confirmed === true, reservation_released: typeof cancel.reservation_released === 'boolean' ? cancel.reservation_released : null} : null};
}
export async function readAppDiagnostics(section = 'overview', offset = 0): Promise<Record<string, unknown>> {
  if (!Number.isInteger(offset) || offset < 0 || offset > 10000)
    return {...base(section, 3600), state: 'invalid_offset', reason: 'offset must be an integer from 0 to 10000.'};
  if (section === 'overview') return appDiagnosticSummary();
  const head = base(section, 3600);
  if (!(APP_DIAGNOSTIC_SECTIONS as readonly string[]).includes(section)) return {...head, state: 'unsupported_section', sections: [...APP_DIAGNOSTIC_SECTIONS]};
  if (section === 'requests') {
    const rows = apiDiagnosticFailures(getApiDiagnosticScope()).slice().reverse();
    return paged({...head, source_owner: 'current', requests: rows.slice(offset, offset + 8), successes_are_not_recorded: true}, 'requests', rows.length, offset);
  }
  if (section !== 'jobs') {
    const errors = currentErrors().filter(row => row.section === section || row.section === 'panel');
    return paged({...head, data: sectionData(section as Section), errors: errors.slice(offset, offset + 8)}, 'errors', errors.length, offset);
  }
  const empty = {offset, next_offset: null, total: null, omitted: null, jobs: []};
  const before = state();
  if (!before.sourceReady || !before.context || before.context.project_id !== before.project.project?.id)
    return {...head, ...empty, state: 'unavailable', reason: 'Current project/source authority is not confirmed.'};
  const captured = authority();
  try {
    const response = await request<{source_dataset_path?: string; labelset_id?: string; tasks?: unknown[]; errors?: Array<{kind?: unknown; message?: unknown}>}>('/api/training-workspace/tasks', {method: 'GET'});
    if (captured !== authority()) return {...head, ...empty, state: 'stale', reason: 'Project, actor, connection or session changed during the read.'};
    if (response.source_dataset_path !== before.data.folderPath || response.labelset_id !== (before.project.project?.active_labelset_id || 'default'))
      return {...head, ...empty, state: 'unavailable', reason: 'Backend snapshot source or labelset does not match.'};
    const rows = Array.isArray(response.tasks) ? response.tasks : [];
    const backendErrors = Array.isArray(response.errors) ? response.errors : [];
    const errors = backendErrors.slice(offset, offset + 4).map(row => ({source: safeWord(row.kind), ...diagnosticError(row.message)}));
    return paged({...head, observed_at: new Date().toISOString(), source_owner: 'current', state: 'observed', jobs: rows.slice(offset, offset + 6).map(jobProjection),
      errors, observed_from: 'project_scoped_backend_tasks', quality_accepted: false}, 'jobs', rows.length, offset, backendErrors.length);
  } catch (cause) {
    if (captured !== authority()) return {...head, ...empty, state: 'stale'};
    return {...head, ...empty, state: 'unavailable', error: diagnosticError(cause)};
  }
}
