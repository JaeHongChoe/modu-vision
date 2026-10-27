/** What the built-in assistant may know about the screen, and nothing more (docs/product-direction-easy-labeling-agent.md
 * §4.4). Built from the footer's published picture (stores/assistContext.ts: step, state, blocker, next action, the
 * step's number line, panel errors) plus counts from the stores. Never sent: the snapshot's `scope` (it holds the
 * project and source folders), full paths (a file is named by its file name), image pixels, user or reviewer names,
 * server addresses. The main process redacts every outgoing text once more (src/main/assistant/redact.ts).
 *
 * Dependency-free (types only) so node tests load it alone. */
import type { AssistSnapshot } from '../stores/assistContext';
import type { AssistantMessage } from '../../types/electron';

export const STEP_NAMES: Record<number, string> = {1: '데이터 준비', 2: '라벨 달기', 3: '모델 학습', 4: '결과 평가', 5: '검사 흐름 만들기', 6: '검사 실행'};

/** Counts and states read from the stores at the moment a question is sent. */
export interface AssistantFacts {
  task: string | null;
  view: 'simple' | 'full';
  shared: boolean;
  images: number;
  unlabeled: number;
  classes: Record<string, number>;
  split: { train: number; val: number; test: number };
  splitState: string | null;
  trainingStatus: string | null;
  /** The image open in step 2, as a file path; only its file name leaves. */
  currentImage: string | null;
}

export function fileName(value: string | null | undefined): string {
  if (!value) return '';
  const parts = String(value).split(/[\\/]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : '';
}

/** Absolute, home and Windows paths inside a sentence become their file names; addresses become [주소]. */
export function reducePaths(text: string): string {
  return String(text ?? '')
    .replace(/\b[a-z][a-z0-9+.-]*:\/\/[^\s"'<>)]*/gi, match => /^file:/i.test(match) ? fileName(match) : '[주소]')
    .replace(/(^|[\s"'(\[=:,])((?:[A-Za-z]:[\\/]|\\\\|~[\\/])[^\s"'<>|?*]*)/g, (_m, lead: string, value: string) => lead + fileName(value))
    .replace(/(^|[\s"'(\[=:,])(\/(?:[^\s/"'()\[\],<>]+\/)+[^\s/"'()\[\],<>]*)/g, (_m, lead: string, value: string) => lead + fileName(value))
    .replace(/\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b/g, '[주소]')
    .replace(/\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b/g, '[이메일]');
}

const clip = (text: string, limit: number) => (text.length > limit ? `${text.slice(0, limit - 1)}…` : text);

export function minimalAssistContext(snapshot: AssistSnapshot | null, facts: AssistantFacts): Record<string, unknown> {
  const classes = Object.entries(facts.classes || {}).slice(0, 30);
  return {
    ...(snapshot ? {
      step: snapshot.stage,
      step_name: STEP_NAMES[snapshot.stage] ?? null,
      status: snapshot.status,
      blocker: snapshot.blocker ? clip(reducePaths(snapshot.blocker), 300) : null,
      next_action: { label: clip(reducePaths(snapshot.nextAction.label), 120), step: snapshot.nextAction.step },
      progress: snapshot.progress,
      panel_errors: snapshot.errors.slice(0, 5).map(item => ({ source: clip(item.source, 60), text: clip(reducePaths(item.text), 240) })),
    } : { step: null, status: 'unknown' }),
    mode: facts.shared ? 'shared_server' : 'this_computer',
    view: facts.view,
    task: facts.task,
    images: facts.images,
    unlabeled: facts.unlabeled,
    classes: Object.fromEntries(classes.map(([name, count]) => [clip(name, 60), count])),
    ...(Object.keys(facts.classes || {}).length > classes.length ? { classes_truncated: true } : {}),
    split: facts.split,
    split_state: facts.splitState,
    training_status: facts.trainingStatus,
    current_image: fileName(facts.currentImage) || null,
  };
}

/** Observations come from allowlisted projections, never raw stores. Budget the complete question before IPC.
 * Keep error/task evidence ahead of optional class counts and progress; details can be reread through tools. */
export function withAssistantObservations(base:Record<string,unknown>,diagnostics:Record<string,unknown>,execution?:Record<string,unknown>):Record<string,unknown> {
  const bounded=(value:Record<string,unknown>,limit:number,tool:string)=>JSON.stringify(value).length<=limit?value
    :{available:false,truncated:true,next_read:tool};
  const observations={diagnostics:bounded(diagnostics,2000,'read_app_diagnostics'),
    ...(execution?{execution:bounded(execution,3400,'read_task_status')}:{})};
  let value:Record<string,unknown>={...base,...observations};
  if(JSON.stringify(value).length<=11000)return value;
  value={...value,classes:{},classes_truncated:true,progress:null,context_truncated:true};
  if(JSON.stringify(value).length<=11000)return value;
  return {step:base.step??null,status:base.status??'unknown',task:base.task??null,
    blocker:typeof base.blocker==='string'?clip(reducePaths(base.blocker),300):null,
    ...observations,context_truncated:true,next_read:'inspect_app_screen'};
}

/** The question text of a person message; earlier images are named, never resent. */
const questionOf = (message: AssistantMessage) => (message.role === 'user' ? message.text : '');

/** Older turns (a turn starts at a person's question) are dropped once the history passes `maxChars` or `maxTurns`,
 * keeping whole turns so every tool call keeps its result; the dropped questions become one short summary line at the
 * front. Provider replay blocks are kept only for the current turn (a later system context would not match them). */
export function trimConversation(messages: AssistantMessage[], options: { maxChars?: number; maxTurns?: number } = {}): AssistantMessage[] {
  const maxChars = options.maxChars ?? 30_000, maxTurns = options.maxTurns ?? 8;
  const turns: AssistantMessage[][] = [];
  for (const message of messages) {
    if (message.role === 'user' || !turns.length) turns.push([message]);
    else turns[turns.length - 1].push(message);
  }
  const size = (turn: AssistantMessage[]) => turn.reduce((sum, message) => sum + JSON.stringify(message).length, 0);
  const kept: AssistantMessage[][] = [];
  let used = 0;
  for (let index = turns.length - 1; index >= 0; index--) {
    const cost = size(turns[index]);
    if (kept.length && (kept.length >= maxTurns || used + cost > maxChars)) break;
    kept.unshift(turns[index]);
    used += cost;
  }
  const dropped = turns.slice(0, turns.length - kept.length).map(turn => clip(questionOf(turn[0]).replace(/\s+/g, ' '), 80)).filter(Boolean);
  const flat = kept.flat();
  const lastQuestion = flat.map(item => item.role).lastIndexOf('user');
  const result = flat.map((message, index): AssistantMessage => (message.role === 'assistant' && index < lastQuestion && message.replay !== undefined
    ? { role: 'assistant', text: message.text, ...(message.toolCalls ? { toolCalls: message.toolCalls } : {}) } : message));
  if (dropped.length && result[0]?.role === 'user') {
    result[0] = { role: 'user', text: clip(`(앞선 질문 ${dropped.length}개 요약: ${dropped.join(' / ')})\n${result[0].text}`, 12_000) };
  }
  return result;
}

/** A short, path-free summary of a tool call for the run log and the panel. */
export function argumentSummary(args: Record<string, unknown>): string {
  const parts = Object.entries(args || {}).slice(0, 6).map(([key, value]) => {
    const shown = typeof value === 'string' ? fileName(reducePaths(value)) : typeof value === 'number' || typeof value === 'boolean' ? String(value) : value === null ? 'null' : '…';
    return `${key}=${clip(shown, 40)}`;
  });
  return clip(parts.join(', '), 200);
}

/** Canonical JSON (sorted keys) for hashing tool arguments. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value ?? null);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  return `{${Object.keys(value as Record<string, unknown>).sort().map(key => `${JSON.stringify(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`).join(',')}}`;
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

export function randomHex(bytes: number): string {
  const values = new Uint8Array(bytes);
  globalThis.crypto.getRandomValues(values);
  return [...values].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
