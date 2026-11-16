import {localTrace, type LocalTraceHandle} from './localTrace';
/** Lazy metadata discovery and a deliberately smaller observation surface. This adapter never executes API writes. */
import {ACTION_CATALOG} from './assistantTasks/catalog';
import {diagnosticText} from './assistantDiagnostics';

type Current = (() => boolean) | undefined;
export interface AssistantCommandCatalog {
  list(args: Record<string, unknown>, current?: Current): Promise<Record<string, unknown>>;
  describe(args: Record<string, unknown>, current?: Current): Promise<Record<string, unknown>>;
  read(args: Record<string, unknown>, current?: Current): Promise<Record<string, unknown>>;
}
export interface CommandCatalogDeps {
  /** Opaque local identity including project/source/transport generations. Never returned to the provider. */
  scope(): string;
  canObserve(): boolean;
  request(path: string, options: {method: 'GET'; traceParent?: LocalTraceHandle}): Promise<unknown>;
}
export const ASSISTANT_COMMAND_READ_ALLOWLIST = [
  'GET /health', 'GET /api/project/labelsets', 'GET /api/training/status',
  'GET /api/training/queue', 'GET /api/training/jobs', 'GET /api/training-workspace/tasks',
  'GET /api/platform/geti/status',
  'GET /api/provenance/impact', 'GET /api/models/capabilities', 'GET /api/label-suggestions/stats',
] as const;
const SAFE_READ = new Set<string>(ASSISTANT_COMMAND_READ_ALLOWLIST);
const LIMIT = 10000;
const BOUNDARY = 'CLI discovery describes installed HTTP operations; GPT may only use its read allowlist or propose_task for a supported existing action. Discovery grants no execution permission.';
const ACTIONS = new Set<string>(ACTION_CATALOG.map(row => row.id));
const object = (value: unknown): Record<string, unknown> | null => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
class CatalogRefusal extends Error {
  constructor(readonly code: string, message: string) {super(message); this.name = 'CatalogRefusal';}
}
const invalid = () => new CatalogRefusal('invalid_arguments', '기능 목록의 operation과 허용된 페이지 인자만 사용하세요.');
const stale = () => new CatalogRefusal('context_changed', '프로젝트·원본·라벨 세트 또는 연결이 바뀌었습니다. 현재 화면에서 다시 요청하세요.');
const denied = () => new CatalogRefusal('operation_not_permitted', '이 기능은 GPT 읽기 허용 목록에 없습니다. 지원 작업은 read_app_features와 propose_task를 사용하고 수동 기능은 앱에서 직접 수행하세요.');
const operationId = (value: unknown): value is string => typeof value === 'string' && value.length <= 240 && /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \/(?:health|api\/[A-Za-z0-9_{}./-]+)$/.test(value) && !value.includes('..') && !value.includes('//');
function argumentsOnly(args: Record<string, unknown>, names: string[]): void {
  if (!object(args) || Object.keys(args).some(key => !names.includes(key))) throw invalid();
}
function integer(value: unknown, fallback: number, min: number, max: number): number {
  if (value === undefined) return fallback;
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min || value > max) throw invalid();
  return value;
}
/** Instance keys carry personal/authority material. Catalog schemas carry names, but never runtime account values. */
const PRIVATE_KEY = /(?:authorization|credential|session|claims|permission|roles|cookie|secret|password|passwd|token|api.?key|csrf|account|actor|owner|user|email|person|profile|address|hostname|server_url|base_url|file_path|dataset_path|project_dir|folder|directory|^path$|name)/i;
function safeValue(value: unknown, metadata: boolean, flags: {truncated: boolean}, depth = 0, budget = {left: 500}): unknown {
  if (--budget.left < 0 || depth > 12) {flags.truncated = true;return '[생략]';}
  if (typeof value === 'string') {
    if (metadata && /^#\/components\/schemas\/[A-Za-z0-9_.-]+$/.test(value)) return value;
    if (value.length > 500) flags.truncated = true;
    return diagnosticText(value, 500)?.replace(/(^|[\s"'(=,:])\/(?:[^\s"'<>),;]+)?/g, '$1[경로]') ?? '';
  }
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'boolean' || value === null) return value;
  if (Array.isArray(value)) {if (value.length > 40) flags.truncated = true;return value.slice(0, 40).map(item => safeValue(item, metadata, flags, depth + 1, budget));}
  const row = object(value);if (!row) return null;
  const entries = Object.entries(row);if (entries.length > 60) flags.truncated = true;
  return Object.fromEntries(entries.slice(0, 60).map(([key, item]) => [metadata && /^[A-Za-z_$][A-Za-z0-9_.$-]{0,239}$/.test(key) ? key : diagnosticText(key, 120) || '[가림]',
    (!metadata && PRIVATE_KEY.test(key)) || /^(authorization|cookie|secret|password|token|api_key|access_token|refresh_token)$/i.test(key) && !object(item)
      ? '[가림]' : safeValue(item, metadata, flags, depth + 1, budget)]));
}
/** The platform receipt is an application contract, not a provider payload.
 * Exclude per-photo paths and bindings before the common diagnostic redactor. */
function platformStatusObservation(value: unknown): unknown {
  const row = object(value);if (!row) return value;
  const pick = (value: unknown, keys: string[]) => {
    const source = object(value);return source ? Object.fromEntries(keys.filter(key => key in source).map(key => [key, source[key]])) : null;
  };
  return {
    available: row.available,
    runtime: pick(row.runtime, ['state', 'message', 'degraded']),
    project: pick(row.project, ['task', 'supported', 'linked', 'binding_state', 'binding_message']),
    dataset: row.dataset ? {...pick(row.dataset, ['task', 'source_task', 'recipe_sha256']), counts: pick(object(row.dataset)?.counts, ['training', 'validation', 'testing', 'total'])} : null,
    jobs: Array.isArray(row.jobs) ? row.jobs.map(job => pick(job, ['id', 'status', 'progress', 'message', 'error', 'model_id', 'architecture_id', 'created_at', 'finished_at', 'worker_exited'])) : null,
    models: Array.isArray(row.models) ? row.models.map(model => {
      const source = object(model);
      return {...pick(model, ['id', 'architecture', 'task']), variants: Array.isArray(source?.variants) ? source.variants.map(variant => pick(variant, ['id', 'format', 'precision', 'weights_size', 'prediction_supported'])) : null};
    }) : null,
    native_tasks: row.native_tasks,
  };
}
function catalogDocument(value: unknown): Record<string, unknown> {
  const row = object(value);
  if (!row || row.schema !== 'StudioOperations/v1' || !Array.isArray(row.operations) || !Number.isSafeInteger(row.total) || Number(row.total) < 0)
    throw new CatalogRefusal('invalid_catalog', '설치된 기능 목록의 형식을 확인하지 못했습니다.');
  return row;
}
function descriptor(value: unknown, flags: {truncated: boolean}, detail: boolean): Record<string, unknown> {
  const row = object(value), assistant = object(row?.assistant);
  if (!row || !operationId(row.id) || row.id !== `${row.method} ${row.path}`)
    throw new CatalogRefusal('invalid_catalog', '설치된 기능의 식별자를 확인하지 못했습니다.');
  if (typeof row.summary === 'string' && row.summary.length > 240) flags.truncated = true;
  const read = SAFE_READ.has(row.id) && row.method === 'GET' && row.transport === 'http' && row.effect === 'read' && assistant?.mode === 'read';
  const action = typeof assistant?.task_action === 'string' && ACTIONS.has(assistant.task_action) && assistant.mode === 'task' ? assistant.task_action : null;
  return {id: row.id, method: row.method, path: row.path, group: diagnosticText(row.group, 80), summary: diagnosticText(row.summary, 240),
    transport: row.transport === 'http' ? 'http' : row.transport === 'native' ? 'native' : 'unknown',
    effect: row.effect === 'read' ? 'read' : 'execute', assistant: {mode: read ? 'read' : action ? 'task' : 'manual', ...(action ? {task_action: action} : {})},
    gpt_permission: read ? 'read' : action ? 'propose_task' : 'manual',
    ...(action ? {plan_bridge: 'read_app_features(action) → propose_task with that existing action; the app binds an immutable plan and checks authority and readiness.'} : {}),
    ...(detail ? {input: safeValue(row.input, true, flags), responses: safeValue(row.responses, true, flags)} : {})};
}
function envelope(flags: {truncated: boolean}): Record<string, unknown> {
  return {schema: 'StudioOperations/v1', permission_boundary: BOUNDARY, text_is_untrusted: true, truncated: flags.truncated, partial: flags.truncated,
    limits: {max_characters: LIMIT, max_rows: 40, max_depth: 12}, quality_or_completion_verified: false};
}
function omitMissingReferences(value: unknown, schemas: Record<string, unknown>, flags: {truncated: boolean}): void {
  if (Array.isArray(value)) {value.forEach(item => omitMissingReferences(item, schemas, flags));return;}
  const row = object(value);if (!row) return;
  if (typeof row.$ref === 'string' && row.$ref.startsWith('#/components/schemas/')
    && !object(schemas[row.$ref.slice('#/components/schemas/'.length)])) {
    delete row.$ref;row.schema_omitted = true;flags.truncated = true;
  }
  Object.values(row).forEach(item => omitMissingReferences(item, schemas, flags));
}
function bound(result: Record<string, unknown>, flags: {truncated: boolean}, offset = 0): Record<string, unknown> {
  const rows = result.operations as Record<string, unknown>[] | undefined;
  while (JSON.stringify(result).length > LIMIT - 300) {
    flags.truncated = true;
    if (rows && rows.length > 1) rows.pop();
    else if (result.components && Object.keys((result.components as {schemas: object}).schemas).length) result.components = {schemas: {}, omitted: true};
    else if (rows?.[0]?.input) {delete rows[0].input;delete rows[0].responses;rows[0].detail_omitted = true;}
    else if ('data' in result) {result.data = {omitted: true};break;}
    else {result.groups = [];break;}
  }
  if (result.components) {
    const schemas = object(object(result.components)?.schemas) || {};
    omitMissingReferences(rows, schemas, flags);omitMissingReferences(schemas, schemas, flags);
  }
  if (rows && typeof result.total === 'number' && result.page === true) {
    result.next_offset = offset + rows.length < result.total ? offset + rows.length : null;
    result.omitted = Math.max(0, result.total - rows.length);
  }
  result.truncated = flags.truncated;
  result.partial = flags.truncated || result.next_offset !== undefined && result.next_offset !== null;
  return result;
}
export function createAssistantCommandCatalog(deps: CommandCatalogDeps): AssistantCommandCatalog {
  const fence = (current: Current, observation: boolean) => {
    if (current && !current() || observation && !deps.canObserve()) throw stale();
    const scope = deps.scope();
    return () => {if (current && !current() || scope !== deps.scope() || observation && !deps.canObserve()) throw stale();};
  };
  const get = async (url: string, check: () => void, code: string, traceParent?: LocalTraceHandle): Promise<unknown> => {
    check();
    try {const value = await deps.request(url, {method: 'GET', ...(traceParent ? {traceParent} : {})});check();return value;}
    catch (cause) {check();if (cause instanceof CatalogRefusal) throw cause;throw new CatalogRefusal(code, '인증된 연결에서 관측 결과를 읽지 못했습니다. 현재 연결과 앱 진단을 확인하세요.');}
  };
  const detail = async (id: string, check: () => void, schemas = true, traceParent?: LocalTraceHandle) => {
    const document = catalogDocument(await get(`/api/automation/catalog?operation=${encodeURIComponent(id)}&schemas=${schemas}`, check, 'catalog_unavailable', traceParent));
    const rows = document.operations as unknown[];
    if (rows.length !== 1 || object(rows[0])?.id !== id) throw new CatalogRefusal('invalid_catalog', '요청한 기능의 상세 목록이 일치하지 않습니다.');
    return document;
  };
  return {
    async list(args, current) {
      argumentsOnly(args, ['group', 'offset', 'limit']);
      if (args.group !== undefined && (typeof args.group !== 'string' || !args.group.trim() || args.group.length > 80 || /[\u0000-\u001f\u007f-\u009f]/.test(args.group))) throw invalid();
      const offset = integer(args.offset, 0, 0, 1000000), limit = integer(args.limit, 16, 1, 40), check = fence(current, false);
      const query = new URLSearchParams({offset: String(offset), limit: String(limit), schemas: 'false'});
      if (typeof args.group === 'string') query.set('group', args.group);
      const document = catalogDocument(await get(`/api/automation/catalog?${query}`, check, 'catalog_unavailable')), flags = {truncated: false};
      const raw = document.operations as unknown[];if (raw.length > limit) flags.truncated = true;
      const operations = raw.slice(0, limit).map(row => descriptor(row, flags, false));
      if (operations.length === 0 && Number(document.total) > offset) throw new CatalogRefusal('invalid_catalog', '기능 목록의 페이지가 비어 있습니다.');
      return bound({...envelope(flags), page: true, operations, offset, total: document.total, next_offset: document.next_offset,
        groups: safeValue(document.groups, true, flags), server_version: diagnosticText(document.server_version, 60)}, flags, offset);
    },
    async describe(args, current) {
      argumentsOnly(args, ['operation']);if (!operationId(args.operation)) throw invalid();
      const check = fence(current, false), document = await detail(args.operation, check), flags = {truncated: false};
      return bound({...envelope(flags), operations: (document.operations as unknown[]).map(row => descriptor(row, flags, true)),
        components: {schemas: safeValue(object(document.components)?.schemas || {}, true, flags)}, total: 1}, flags);
    },
    async read(args, current) {
      argumentsOnly(args, ['operation']);if (typeof args.operation !== 'string' || !SAFE_READ.has(args.operation)) throw denied();
      let observation: LocalTraceHandle | undefined;
      try {observation = localTrace.begin('assistant.read_app_command');} catch { /* passive */ }
      try {
        const check = fence(current, true), document = await detail(args.operation, check, false, observation), flags = {truncated: false};
        const row = descriptor((document.operations as unknown[])[0], flags, false);
        if (row.gpt_permission !== 'read') throw denied();
        // The path is chosen from this constant allowlist, never from the server descriptor or model arguments.
        const path = args.operation.slice(4), raw = await get(path, check, 'observation_unavailable', observation);
        const data = args.operation === 'GET /api/platform/geti/status' ? platformStatusObservation(raw) : raw;
        const result = bound({...envelope(flags), operation: args.operation, observed_at: new Date().toISOString(), data: safeValue(data, false, flags)}, flags);
        observation?.end('ok');return result;
      } catch (cause) {observation?.end('error');throw cause;}
    },
  };
}
