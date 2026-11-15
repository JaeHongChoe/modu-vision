/** Embedded SDK, memory only. Never register a global provider, detect resources or export over a transport. */
import {ROOT_CONTEXT, SpanStatusCode, trace, type SpanContext} from '@opentelemetry/api';
import {AlwaysOnSampler, TracerProvider, type ReadableSpan, type SpanExporter} from '@opentelemetry/sdk-trace';
import {emptyResource} from '@opentelemetry/resources';

const OPERATIONS = new Set(['assistant.read_app_command','assistant.jobs','api.app_request','api.training','api.dataset','api.annotation','api.evaluation','api.flow','api.inspection','api.compute','api.assistant','api.project','api.account','api.provenance','api.local_trace','api.catalog','api.models']);
export type TraceOutcome = 'ok' | 'error' | 'cancelled';
export interface LocalTraceRow {
  operation: string; trace_id: string; span_id: string; parent_span_id: string | null;
  start_ms: number; end_ms: number; duration_ms: number; status: TraceOutcome; code: number | null;
  coverage: 'explicit_parent' | 'unlinked';
}
export interface LocalTraceHandle {readonly traceId: string; readonly spanId: string; readonly scopeToken: string; readonly traceparent: string; readonly linked: boolean; end(status: TraceOutcome, code?: number): void}
export interface LocalTraceDocument {sdk: string; spans: LocalTraceRow[]; dropped_spans: number; dropped_scopes: number; export_errors: number; capacity: number; max_export_bytes: number; async_jobs: 'unlinked'}
const millis = (time: [number, number]) => time[0] * 1000 + time[1] / 1e6;

export function createLocalTrace(identity: () => string) {
  type Bucket = {rows: LocalTraceRow[]; dropped: number};
  const buckets = new Map<string, Bucket>(), parents = new WeakMap<LocalTraceHandle, SpanContext>();
  let previous: string | undefined, active = '', droppedScopes = 0, exportErrors = 0;
  // An epoch is independent of account/source values. Returning to A after B never reopens A's history.
  function activate(): string {
    const next = identity();
    if (next !== previous) {
      previous = next;active = globalThis.crypto.randomUUID().replace(/-/g,'');buckets.set(active, {rows: [], dropped: 0});
      if (buckets.size > 8) {buckets.delete(buckets.keys().next().value!);droppedScopes++;}
    }
    return active;
  }
  const exporter: SpanExporter = {
    export(spans, callback) {
      try {
        for (const span of spans) {
          const bucket = buckets.get(String(span.attributes['local.scope']));if (!bucket) continue;
          const context = span.spanContext(), linked = span.parentSpanContext?.spanId || null;
          bucket.rows.push({operation: OPERATIONS.has(span.name) ? span.name : 'api.app_request', trace_id: context.traceId, span_id: context.spanId, parent_span_id: linked,
            start_ms: millis(span.startTime), end_ms: millis(span.endTime), duration_ms: millis(span.duration),
            status: span.attributes['local.outcome'] === 'cancelled' ? 'cancelled' : span.status.code === SpanStatusCode.ERROR ? 'error' : 'ok',
            code: typeof span.attributes['local.code'] === 'number' ? span.attributes['local.code'] : null, coverage: linked ? 'explicit_parent' : 'unlinked'});
          if (bucket.rows.length > 256) {bucket.rows.shift();bucket.dropped++;}
        }
        callback({code: 0});
      } catch {exportErrors++;try {callback({code: 1});} catch { /* observation only */ }}
    },
    shutdown: async () => {},
  };
  const provider = new TracerProvider({resource: emptyResource(), sampler: new AlwaysOnSampler(),
    spanLimits: {attributeCountLimit: 3, attributeValueLengthLimit: 64, eventCountLimit: 0, linkCountLimit: 0, attributePerEventCountLimit: 0, attributePerLinkCountLimit: 0},
    spanProcessors: [{onStart() {}, onEnd(span: ReadableSpan) {try {exporter.export([span], () => {});} catch {exportErrors++;}}, forceFlush: async () => {}, shutdown: async () => {}}]});
  const tracer = provider.getTracer('modu-local', '1');
  return {
    scopeToken: activate,
    begin(operation: string, parent?: LocalTraceHandle | null): LocalTraceHandle {
      const token = activate(), parentContext = parent && parent.scopeToken === token ? parents.get(parent) : undefined;
      const span = tracer.startSpan(OPERATIONS.has(operation) ? operation : 'api.app_request', {attributes: {'local.scope': token}},
        parentContext ? trace.setSpanContext(ROOT_CONTEXT, parentContext) : ROOT_CONTEXT);
      const context = span.spanContext();let ended = false;
      const handle: LocalTraceHandle = {traceId: context.traceId, spanId: context.spanId, scopeToken: token,
        traceparent: `00-${context.traceId}-${context.spanId}-01`, linked: Boolean(parentContext), end(status, code) {
          if (ended) return;ended = true;
          try {span.setAttribute('local.outcome', status);if (Number.isInteger(code) && code! >= 100 && code! <= 599) span.setAttribute('local.code', code!);
            span.setStatus({code: status === 'ok' ? SpanStatusCode.OK : SpanStatusCode.ERROR});span.end();} catch {exportErrors++;}
        }};
      parents.set(handle, context);return handle;
    },
    read(): LocalTraceDocument {
      const bucket = buckets.get(activate())!;
      const doc: LocalTraceDocument = {sdk: 'opentelemetry-js-2.12.0', spans: bucket.rows.map(row => ({...row})), dropped_spans: bucket.dropped,
        dropped_scopes: droppedScopes, export_errors: exportErrors, capacity: 256, max_export_bytes: 1048576, async_jobs: 'unlinked'};
      while (new TextEncoder().encode(JSON.stringify(doc)).byteLength > doc.max_export_bytes && doc.spans.length) {doc.spans.shift();doc.dropped_spans++;}
      return doc;
    },
  };
}
let source = () => 'unselected';
export function setLocalTraceScopeSource(next: () => string): void {source = next;}
export const localTrace = createLocalTrace(() => source());
/** Only fixed operation classes survive; the incoming string is never stored. */
export function localTraceOperation(path: string): string {
  const value = path.split('?')[0];
  const group = /^\/api\/(training(?:-workspace)?|dataset|annotations?|evaluation|flow[^/]*|inspections?|compute|assistant[^/]*|project|accounts|provenance|local-trace|automation\/catalog|models)(?:\/|$)/.exec(value)?.[1];
  if (!group) return 'api.app_request';
  return 'api.' + (group.startsWith('training') ? 'training' : group.startsWith('flow') ? 'flow' : group.startsWith('inspection') ? 'inspection'
    : group.startsWith('annotation') ? 'annotation' : group.startsWith('assistant') ? 'assistant' : group === 'accounts' ? 'account' : group === 'local-trace' ? 'local_trace' : group === 'automation/catalog' ? 'catalog' : group);
}
