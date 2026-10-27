/** Memory-only failure journal. Raw requests, responses, headers and authority never enter its rows. */
import {getApiDiagnosticScope, subscribeApiRequestFailures} from './api';
import {diagnosticError, type SafeDiagnosticError} from './assistantDiagnostics';

export interface ApiDiagnosticFailure extends SafeDiagnosticError {
  at: string; operation: string; kind: 'http' | 'network' | 'json' | 'context';
  historical: true; current_error_confirmed: false;
}
const rows: ApiDiagnosticFailure[] = [];
let scope: string | null = null;
const operations: Array<[RegExp, string]> = [
  [/^\/api\/training-workspace\//, 'training_workspace'], [/^\/api\/training\//, 'training'],
  [/^\/api\/dataset\//, 'dataset'], [/^\/api\/annotations?\//, 'annotation'], [/^\/api\/evaluation\//, 'evaluation'],
  [/^\/api\/flow/, 'flow'], [/^\/api\/inspections?\//, 'inspection'], [/^\/api\/compute\//, 'compute'],
  [/^\/api\/assistant/, 'assistant'], [/^\/api\/project\//, 'project'], [/^\/api\/accounts\//, 'account'],
  [/^\/api\/label/, 'label_drafts'], [/^\/api\/foundation/, 'foundation_labeling'], [/^\/api\/provenance\//, 'provenance'],
];
subscribeApiRequestFailures(event => {
  if (event.scope !== getApiDiagnosticScope()) return;
  if (scope !== event.scope) {scope = event.scope; rows.length = 0;}
  const error = diagnosticError(event.cause);
  const rawMessage = event.cause instanceof Error ? event.cause.message : '';
  const kind = error.status ? 'http' : event.cause instanceof SyntaxError ? 'json'
    : /context|문맥|프로젝트 선택|연결 상태가 바뀌|세션.*만료|다시 로그인/i.test(rawMessage) ? 'context' : 'network';
  rows.push({at: new Date().toISOString(), operation: operations.find(([pattern]) => pattern.test(event.path.split('?')[0]))?.[1] || 'app_request', kind,
    historical: true, current_error_confirmed: false, ...error});
  if (rows.length > 40) rows.splice(0, rows.length - 40);
});

/** The private scope is supplied by the caller, and is never included in the returned DTO. */
export function apiDiagnosticFailures(currentScope: string): ApiDiagnosticFailure[] {
  if (scope !== currentScope) {scope = currentScope; rows.length = 0;}
  return rows.map(row => ({...row}));
}
