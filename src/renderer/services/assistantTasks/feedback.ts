/** Provider-facing task observations. Receipts and authority remain local. */
import {diagnosticError, diagnosticText} from '../assistantDiagnostics';
import type {AssistantTask, TaskStep} from './types';

interface FeedbackOptions {
  contextMatches: boolean;
  controllerError?: unknown;
  readError?: unknown;
  fresh?: boolean;
  source?: 'cache' | 'ledger';
  observedAt?: string;
}
const MAX_CHARS = 3400;
const record = (value: unknown): Record<string, unknown> | null => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const identifier = (value: unknown) => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value) ? value : null;
const number = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;
const timestamp = (value: unknown) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T[\d:.+-]+Z?$/.test(value) ? value.slice(0, 40) : null;
const boundedError = (value: unknown) => {
  const safe = diagnosticError(value);
  return {code: safe.code, message: diagnosticText(safe.message, 180), status: safe.status,
    ...(safe.severity ? {severity: safe.severity} : {}),
    ...(safe.cause ? {cause: diagnosticText(safe.cause, 80)} : {}),
    ...(safe.suggested_action ? {suggested_action: diagnosticText(safe.suggested_action, 100)} : {})};
};
const minimalError = (value: unknown) => {const safe = diagnosticError(value); return {code: safe.code, message: diagnosticText(safe.message, 120), status: safe.status};};

/** Each observed diagnostic was already bounded at collection; project it again at the provider boundary. */
function diagnostics(value: unknown): Record<string, unknown> | null {
  const source = record(value); if (!source) return null;
  const result: Record<string, unknown> = {};
  if (['training','label_drafts','flow_evaluation','inspection','model_evaluation'].includes(String(source.source))) result.source = source.source;
  if (Array.isArray(source.errors)) result.errors = source.errors.slice(0, 3).map(boundedError);
  if (Array.isArray(source.reasons)) result.reasons = source.reasons.slice(0, 3).map(reason => diagnosticText(reason, 120)).filter(Boolean);
  const next = diagnosticText(source.next_action, 160); if (next) result.next_action = next;
  return Object.keys(result).length ? result : null;
}

function savedResult(value: unknown): Record<string, unknown> | null {
  const receipt = record(value); if (!receipt) return null;
  const result: Record<string, unknown> = {};
  for (const key of ['job_id','batch_id','run_id','version_id','pipeline_id','cohort_id','evaluation_id']) {
    const id = identifier(receipt[key]); if (id) result[key] = id;
  }
  for (const key of ['images','total','samples','known','unknown','processed','generated','succeeded','failed','reviewed','accepted','rejected','errors']) {
    const count = number(receipt[key]); if (count !== null) result[key] = count;
  }
  for (const key of ['not_started','human_review_verified','truth_review_required','quality_passed','adopted','imported','created']) if (typeof receipt[key] === 'boolean') result[key] = receipt[key];
  if(['waiting_input','waiting_resource','waiting_job','failed','needs_reconciliation'].includes(String(receipt.reason_code)))result.reason_code=receipt.reason_code;
  if(['input_needed','not_qualified','qualified'].includes(String(receipt.quality_status)))result.quality_status=receipt.quality_status;
  const quality=record(receipt.quality_assessment);
  if(quality&&['pending','qualified','not_qualified','input_needed'].includes(String(quality.status)))result.quality_assessment={
    status:quality.status,decision_source:'policy',...(identifier(quality.assessment_id)?{assessment_id:identifier(quality.assessment_id)}:{}),
    reasons:(Array.isArray(quality.reasons)?quality.reasons:[]).slice(0,3).map(value=>diagnosticText(value,180)).filter(Boolean)};
  if (['idle','queued','preparing','transferring','running','stopping','syncing','cancelling','completed','completed_with_errors','failed','aborted','interrupted','stopped'].includes(String(receipt.status))) result.status = receipt.status;
  if (typeof receipt.report_path === 'string' && receipt.report_path) result.report_saved = true;
  const settings = record(receipt.settings);
  if (settings) {
    const safe: Record<string, unknown> = {};
    if (settings.preset === 'fast' || settings.preset === 'precision') safe.preset = settings.preset;
    if (['auto','cpu','mps','cuda'].includes(String(settings.device))) safe.device = settings.device;
    const batch = number(settings.batch_size ?? record(settings.config_overrides)?.batch_size); if (batch !== null) safe.batch_size = batch;
    if (Object.keys(safe).length) result.settings = safe;
  }
  const split = record(receipt.split);
  if (split) result.split = Object.fromEntries(['train','val','test'].flatMap(key => number(split[key]) === null ? [] : [[key, split[key]]]));
  const detail = diagnostics(receipt.diagnostics); if (detail) result.diagnostics = detail;
  const recomputation = record(receipt.recomputation);
  if (recomputation) {
    // The immutable receipt keeps its original evaluation. Only the runtime's verified completion phase proves
    // the replacement result; an intent or returned response alone cannot claim that the changed truth passed.
    result.historical_result = Object.fromEntries(['evaluation_id','cohort_id','samples','known','unknown','truth_review_required','status','errors']
      .filter(key => Object.hasOwn(result, key)).map(key => {const value = result[key]; delete result[key]; return [key, value];}));
    const evaluation = identifier(recomputation.new_evaluation_id), cohort = identifier(recomputation.new_cohort_id), known = number(recomputation.known), unknown = number(recomputation.unknown);
    const verified = recomputation.phase === 'evaluated' && evaluation !== null && cohort !== null && known !== null && known > 0 && unknown === 0
      && recomputation.previous_evaluation_id === receipt.evaluation_id && recomputation.previous_cohort_id === receipt.cohort_id;
    result.reevaluation_verified = verified;
    if (verified) result.current_result = {evaluation_id: evaluation, cohort_id: cohort, known, unknown, truth_review_required: false, status: 'completed'};
  }
  return Object.keys(result).length ? result : null;
}

const stepSummary = (step: TaskStep, index: number) => ({index: index + 1, action: step.action, status: step.status,
  ...(step.error ? {error: diagnosticText(step.error, 180)} : {}), ...(step.receipt ? {result: savedResult(step.receipt)} : {})});

function compactResult(full: Record<string, unknown>, action: string): Record<string, unknown> {
  const detail = record(full.diagnostics);
  const identities = action === 'evaluate_flow' ? ['evaluation_id','cohort_id','version_id'] : action === 'inspect_test' ? ['run_id']
    : action === 'generate_label_drafts' ? ['batch_id','job_id'] : action === 'save_flow' ? ['version_id','pipeline_id','job_id'] : ['job_id','evaluation_id'];
  return {...Object.fromEntries([...identities,'status','not_started','reason_code','quality_status','quality_passed','quality_assessment','truth_review_required','reevaluation_verified','current_result','historical_result']
    .filter(key => Object.hasOwn(full, key)).map(key => [key, full[key]])),
    ...(detail ? {diagnostics: {source: detail.source, errors: Array.isArray(detail.errors) ? detail.errors.slice(0, 1).map(minimalError) : [],
      reasons: Array.isArray(detail.reasons) ? detail.reasons.slice(0, 1).map(reason => diagnosticText(reason, 80)) : []}} : {}), details_omitted: true};
}

/** Always complete JSON below the conversation tool limit; custom goal/labels/args never enter the DTO. */
export function taskFeedback(task: AssistantTask | null, options: FeedbackOptions): Record<string, unknown> {
  const base: Record<string, unknown> = {loaded: Boolean(task), fresh: options.fresh === true, source: options.source ?? 'cache',
    observed_at: timestamp(options.observedAt) ?? new Date().toISOString(), current_context_matches: options.contextMatches, untrusted_text: true};
  if (options.contextMatches) Object.assign(base, {controller_error: options.controllerError ? boundedError(options.controllerError) : null,
    ...(options.readError ? {read_error: boundedError(options.readError)} : {})});
  if (!task) return base;
  Object.assign(base, {task_id: identifier(task.id), revision: task.revision, status: task.status, ledger_updated_at: timestamp(task.updated_at)});
  if (!options.contextMatches) return {...base, execution_evidence_withheld: true};
  const active = task.steps.findIndex(step => step.id === task.current_step_id);
  Object.assign(base, {current_step: active < 0 ? null : {id: identifier(task.steps[active].id), index: active + 1, action: task.steps[active].action, status: task.steps[active].status},
    counts: {total: task.steps.length, completed: task.steps.filter(step => step.status === 'completed').length,
      pending: task.steps.filter(step => step.status === 'pending').length},
    message: diagnosticText(task.message, 220), execution_requires_person: task.status === 'draft' || task.status === 'waiting_human'});
  // The current failing action has first priority, then verified reevaluations. Older steps cannot crowd them out.
  const summaries = task.steps.map(stepSummary);
  const order = summaries.map((_step, index) => index).sort((a, b) => Number(b === active) - Number(a === active)
    || Number(Boolean(summaries[b].result?.current_result)) - Number(Boolean(summaries[a].result?.current_result)));
  const steps: Array<ReturnType<typeof stepSummary>> = [];
  base.steps = steps; base.steps_omitted = task.steps.length;
  for (const index of order) {
    const candidate = summaries[index];
    steps.push(candidate); base.steps_omitted = task.steps.length - steps.length;
    const priority = index === active || (active < 0 && Boolean(candidate.result?.current_result));
    if (priority && JSON.stringify(base).length > MAX_CHARS) {
      if (candidate.result) candidate.result = compactResult(candidate.result, candidate.action);
      if (candidate.error) candidate.error = diagnosticText(candidate.error, 120);
      if (JSON.stringify(base).length > MAX_CHARS) {
        if (base.controller_error) base.controller_error = minimalError(base.controller_error);
        if (base.read_error) base.read_error = minimalError(base.read_error);
        base.message = diagnosticText(base.message, 120);
      }
    }
    if (JSON.stringify(base).length > MAX_CHARS) {steps.pop(); base.steps_omitted = task.steps.length - steps.length;}
  }
  steps.sort((a,b) => a.index - b.index);
  return base;
}
