/** The built-in assistant's tools and their permission tiers (docs/product-direction-easy-labeling-agent.md §4.5).
 *
 * read      — status, data changes, evaluation, draft statistics, errors: run at once and logged.
 * navigate  — open a step, fill a value without saving: run at once; the person still presses the button.
 * write     — save a split, choose an image's class, make label drafts, start training: never run without the
 *             in-panel preview and the person's own confirmation (`execute` refuses otherwise); each preview names the
 *             way back.
 * forbidden — approving, deploying, deleting originals or label files, confirming truth, adopting drafts, account or
 *             permission changes, sending files out: never run; the answer is "직접 하세요" and where.
 *
 * Every tool goes through the app's existing stores and services (`ToolDeps`), so the backend's role checks apply as
 * they do to the person's own clicks; nothing here is a new backend power. The deps are injected so node tests drive
 * the registry with fakes; assistantRuntime.ts wires the real stores. */
import type { AssistSnapshot } from '../stores/assistContext';
import type { AssistantToolSpec } from '../../types/electron';
import { STEP_NAMES, fileName, minimalAssistContext, reducePaths, type AssistantFacts } from './assistantContext';

export type ToolTier = 'read' | 'navigate' | 'write' | 'forbidden';
export interface ToolPreview { title: string; lines: string[]; undo: string }
/** `content` goes back to the model (JSON or one sentence); `summary` is the panel's line; `link` names the record the
 *  write produced (an audit action, a job or a batch) for the run log. */
export interface ToolOutcome { ok: boolean; content: string; summary: string; link?: string; errorCode?: string }

export class ToolRefusal extends Error {
  constructor(readonly code: string, message: string) { super(message); this.name = 'ToolRefusal'; }
}

export interface DraftModel { jobId: string; task: string; dataState: 'current' | 'changed'; createdAt: string | null }
export interface ImageChoice { path: string; name: string; currentClass: string | null; folderClass: string | null }
export interface EvaluationFacts { samples: number; escapes: number; overkills: number; accuracy: number | null; threshold: number }
export interface TrainingFacts { isTraining: boolean; status: string | null; preset: string; computeOnServer: boolean; folderReady: boolean }

export interface ToolDeps {
  snapshot(): AssistSnapshot | null;
  facts(): AssistantFacts;
  setStep(step: number): Promise<void>;
  focusTarget(target?: string): void;
  setTrainRatio(ratio: number): void;
  splitSupport(): { supported: boolean | null; reason: string | null };
  applySplit(train: number, val: number, test: number): Promise<{ ok: boolean; error?: string | null }>;
  findImage(name: string): ImageChoice[];
  setImageClass(image: ImageChoice, className: string | null): Promise<{ className: string | null; changed: boolean }>;
  draftModels(): Promise<DraftModel[]>;
  startDraftBatch(jobId: string, threshold: number): Promise<{ id: string; total: number }>;
  draftStats(): Promise<unknown>;
  impact(): Promise<unknown>;
  evaluation(): EvaluationFacts | null;
  training(): TrainingFacts;
  startTraining(): Promise<{ jobId: string | null }>;
}

interface ToolDefinition {
  name: string;
  tier: ToolTier;
  description: string;
  parameters: Record<string, unknown>;
  /** forbidden: where the person does it themselves */
  where?: string;
  run?(args: Record<string, unknown>): Promise<ToolOutcome>;
  preview?(args: Record<string, unknown>): Promise<ToolPreview>;
  execute?(args: Record<string, unknown>): Promise<ToolOutcome>;
}

const NO_ARGS = { type: 'object', properties: {}, additionalProperties: false };
const json = (value: unknown) => JSON.stringify(value);
const ok = (content: unknown, summary: string, link?: string): ToolOutcome => ({ ok: true, content: typeof content === 'string' ? content : json(content), summary, ...(link ? { link } : {}) });

function integer(args: Record<string, unknown>, key: string, min: number, max: number, fallback?: number): number {
  const value = args[key] ?? fallback;
  if (typeof value !== 'number' || !Number.isInteger(value) || value < min || value > max) throw new ToolRefusal('invalid_arguments', `${key}는 ${min}~${max} 정수여야 합니다.`);
  return value;
}

function draftModelChoice(models: DraftModel[], requested: unknown): DraftModel {
  if (typeof requested === 'string' && requested) {
    const found = models.find(model => model.jobId === requested);
    if (!found) throw new ToolRefusal('unknown_model', '그 모델은 이 프로젝트의 초안용 완료 모델 목록에 없습니다. read_draft_stats나 화면의 모델 목록에서 고르세요.');
    return found;
  }
  const chosen = models.find(model => model.dataState === 'current') || models[0];
  if (!chosen) throw new ToolRefusal('no_model', '초안을 만들 완료 모델이 없습니다. 먼저 20장 정도 직접 라벨을 달고 3단계에서 학습하세요.');
  return chosen;
}

export function createAssistantTools(deps: ToolDeps): AssistantToolRegistry {
  const facts = () => deps.facts();
  const definitions: ToolDefinition[] = [
    // ---- read ----
    { name: 'read_status', tier: 'read', parameters: NO_ARGS,
      description: '지금 단계, 상태, 막힌 이유, 다음 행동 하나, 단계의 진행 숫자, 패널 오류를 읽습니다.',
      run: async () => ok(minimalAssistContext(deps.snapshot(), facts()), '지금 상태를 읽었습니다.') },
    { name: 'read_dataset_summary', tier: 'read', parameters: NO_ARGS,
      description: '이미지 수, 라벨 없는 이미지 수, 클래스별 수, 학습/검증/시험 분할을 읽습니다.',
      run: async () => {
        const value = facts();
        return ok({ task: value.task, images: value.images, unlabeled: value.unlabeled, classes: value.classes, split: value.split, split_state: value.splitState }, '데이터 수를 읽었습니다.');
      } },
    { name: 'read_data_changes', tier: 'read', parameters: NO_ARGS,
      description: '데이터가 바뀐 뒤 다시 해야 하는 일(모델·평가·흐름이 현재 데이터와 맞는지)을 읽습니다.',
      run: async () => {
        const impact = await deps.impact() as { models?: { data_state?: string }[]; model_evaluations?: { state?: string }[]; flows?: unknown[]; required_actions?: string[] } | null;
        const count = (rows: { data_state?: string; state?: string }[] | undefined, key: 'data_state' | 'state') => (rows || []).reduce<Record<string, number>>((all, row) => {
          const name = String(row?.[key] || 'unknown'); all[name] = (all[name] || 0) + 1; return all;
        }, {});
        return ok({ models: count(impact?.models, 'data_state'), evaluations: count(impact?.model_evaluations, 'state'),
          flows: (impact?.flows || []).length, required_actions: (impact?.required_actions || []).slice(0, 10) }, '데이터 변경 영향을 읽었습니다.');
      } },
    { name: 'read_evaluation_summary', tier: 'read', parameters: NO_ARGS,
      description: '불러온 평가 결과: 이미지 수, 놓친 불량(escape), 잘못 잡은 정상(overkill), 정확도, 기준 점수.',
      run: async () => {
        const value = deps.evaluation();
        return ok(value ?? { loaded: false, hint: '4단계에서 평가를 실행하거나 불러오면 읽을 수 있습니다.' }, value ? '평가 요약을 읽었습니다.' : '불러온 평가가 없습니다.');
      } },
    { name: 'read_draft_stats', tier: 'read', parameters: NO_ARGS,
      description: '모델별 초안 채택률(채택·거절·채택 뒤 수정)과 기준 신뢰도 추천을 읽습니다.',
      run: async () => {
        const stats = await deps.draftStats() as { models?: Record<string, any>[] } | null;
        const models = (stats?.models || []).slice(0, 10).map(row => ({ job_id: row.job_id, drafts: row.drafts, pending: row.pending, adopted: row.adopted,
          rejected: row.rejected, edited_after: row.edited_after, advice: row.advice ? { reason: row.advice.reason, threshold: row.advice.threshold ?? null, adopted_rate: row.advice.adopted_rate ?? null } : null }));
        const draftable = await deps.draftModels();
        return ok({ models, draft_models: draftable.slice(0, 10).map(model => ({ job_id: model.jobId, data_state: model.dataState })) }, '초안 채택률을 읽었습니다.');
      } },
    { name: 'read_error_details', tier: 'read', parameters: NO_ARGS,
      description: '패널마다 보이는 오류와 지금 막힌 이유를 자세히 읽습니다.',
      run: async () => {
        const snapshot = deps.snapshot();
        return ok({ blocker: snapshot?.blocker ? reducePaths(snapshot.blocker) : null,
          errors: (snapshot?.errors || []).slice(0, 10).map(item => ({ source: item.source, text: reducePaths(item.text).slice(0, 500) })) }, '오류 내용을 읽었습니다.');
      } },
    // ---- navigate / prepare ----
    { name: 'open_step', tier: 'navigate',
      description: '단계 화면을 엽니다(1~6). 버튼은 누르지 않습니다. target은 화면 안 위치 ID(선택).',
      parameters: { type: 'object', properties: { step: { type: 'integer', minimum: 1, maximum: 6 }, target: { type: 'string', pattern: '^[a-z][a-z0-9-]{1,63}$' } }, required: ['step'], additionalProperties: false },
      run: async args => {
        const step = integer(args, 'step', 1, 6);
        const target = typeof args.target === 'string' && /^[a-z][a-z0-9-]{1,63}$/.test(args.target) ? args.target : undefined;
        await deps.setStep(step);deps.focusTarget(target);
        return ok(`${step}단계(${STEP_NAMES[step]}) 화면을 열었습니다. 다음 버튼은 사람이 직접 누릅니다.`, `${step}단계 화면을 열었습니다.`);
      } },
    { name: 'open_next_action', tier: 'navigate', parameters: NO_ARGS,
      description: '하단 안내의 다음 행동이 있는 화면과 위치를 엽니다. 버튼은 누르지 않습니다.',
      run: async () => {
        const next = deps.snapshot()?.nextAction;
        if (!next) throw new ToolRefusal('no_next_action', '지금 다음 행동을 읽지 못했습니다. read_status로 상태를 먼저 확인하세요.');
        await deps.setStep(next.step);deps.focusTarget(next.target);
        return ok(`"${next.label}" 위치(${next.step}단계)를 열었습니다. 사람이 직접 누릅니다.`, `"${next.label}" 위치를 열었습니다.`);
      } },
    { name: 'prefill_split', tier: 'navigate',
      description: '1단계 분할 화면을 열고 학습 비율을 채워 둡니다(저장하지 않음). 저장은 사람이 누릅니다.',
      parameters: { type: 'object', properties: { train_percent: { type: 'integer', minimum: 10, maximum: 95 } }, required: ['train_percent'], additionalProperties: false },
      run: async args => {
        const train = integer(args, 'train_percent', 10, 95);
        const wasOpen = deps.snapshot()?.stage === 1;
        deps.setTrainRatio(train / 100);await deps.setStep(1);deps.focusTarget('workflow-data');
        return ok(wasOpen ? `학습 비율 ${train}%를 기억해 두었지만 이미 열린 분할 막대에는 바로 보이지 않을 수 있습니다. 화면에서 막대를 ${train}%로 맞추고 '분할 저장'을 누르세요.`
          : `1단계를 열고 학습 비율 ${train}%를 채웠습니다. 확인한 뒤 사람이 '분할 저장'을 누릅니다.`, `학습 비율 ${train}%를 채웠습니다(저장 안 함).`);
      } },
    // ---- write (preview + confirmation) ----
    { name: 'save_split', tier: 'write',
      description: '학습/검증/시험 분할을 저장합니다. 사람이 미리보기를 보고 확인해야 실행됩니다. 세 값의 합은 100.',
      parameters: { type: 'object', properties: { train_percent: { type: 'integer', minimum: 10, maximum: 95 }, val_percent: { type: 'integer', minimum: 5, maximum: 50 }, test_percent: { type: 'integer', minimum: 0, maximum: 40 } }, required: ['train_percent', 'val_percent'], additionalProperties: false },
      preview: async args => {
        const train = integer(args, 'train_percent', 10, 95), val = integer(args, 'val_percent', 5, 50), test = integer(args, 'test_percent', 0, 40, 0);
        if (train + val + test !== 100) throw new ToolRefusal('invalid_arguments', '학습·검증·시험 비율의 합은 100이어야 합니다.');
        const support = deps.splitSupport(), value = facts();
        if (support.supported === false) throw new ToolRefusal('split_unavailable', support.reason || '이 데이터는 앱에서 다시 나누지 않습니다. 원본의 train/val/test 폴더를 쓰세요.');
        if (!value.images) throw new ToolRefusal('no_images', '불러온 이미지가 없습니다. 1단계에서 데이터 폴더를 먼저 여세요.');
        return { title: '분할 저장', lines: [`학습 ${train}% · 검증 ${val}% · 시험 ${test}%로 이미지 ${value.images}장을 나눕니다.`,
          `지금: 학습 ${value.split.train} · 검증 ${value.split.val} · 시험 ${value.split.test}장`, '원본 이미지 파일은 옮기지 않고 분할 목록만 저장합니다.'],
          undo: '같은 화면(1단계)에서 다시 나누거나 데이터 버전 복원으로 되돌립니다. 분할을 바꾸면 이전 학습·평가는 다시 확인해야 합니다.' };
      },
      execute: async args => {
        const train = integer(args, 'train_percent', 10, 95), val = integer(args, 'val_percent', 5, 50), test = integer(args, 'test_percent', 0, 40, 0);
        if (train + val + test !== 100) throw new ToolRefusal('invalid_arguments', '학습·검증·시험 비율의 합은 100이어야 합니다.');
        const result = await deps.applySplit(train / 100, val / 100, test / 100);
        if (!result.ok) throw new ToolRefusal('split_failed', result.error || '분할을 저장하지 못했습니다. 1단계 화면의 오류를 확인하세요.');
        const after = facts().split;
        return ok({ saved: true, split: after }, `분할을 저장했습니다(학습 ${after.train} · 검증 ${after.val} · 시험 ${after.test}장).`);
      } },
    { name: 'assign_class', tier: 'write',
      description: '분류 프로젝트에서 이미지 한 장의 클래스를 지정합니다(파일은 옮기지 않음). image는 "current"(2단계에 열린 이미지) 또는 파일 이름. class_name이 null이면 폴더 클래스로 되돌립니다.',
      parameters: { type: 'object', properties: { image: { type: 'string' }, class_name: { type: ['string', 'null'], maxLength: 100 } }, required: ['image', 'class_name'], additionalProperties: false },
      preview: async args => {
        const { image, className } = classChoice(deps, args);
        return { title: '이미지 클래스 지정', lines: [`이미지 ${image.name}: ${image.currentClass ?? '없음'} → ${className ?? `폴더 클래스(${image.folderClass ?? '없음'})`}`,
          '이미지 파일은 옮기지 않습니다. 학습·평가·1단계 수가 이 클래스를 씁니다.'],
          undo: `같은 이미지의 클래스를 다시 ${image.currentClass ?? '원래 값'}로 지정하면 됩니다(2단계 '이 이미지의 클래스'). 변경은 이미지 감사 기록(class_changed)에 남습니다.` };
      },
      execute: async args => {
        const { image, className } = classChoice(deps, args);
        const result = await deps.setImageClass(image, className);
        return ok({ image: image.name, class: result.className, changed: result.changed }, `${image.name}의 클래스를 ${result.className ?? '폴더 클래스'}로 지정했습니다.`, 'class_changed');
      } },
    { name: 'generate_label_drafts', tier: 'write',
      description: '완료 모델로 라벨 없는 이미지의 초안을 만듭니다(라벨 파일은 바뀌지 않음, 채택은 사람이 2단계 초안 검토에서). 사람이 확인해야 실행됩니다.',
      parameters: { type: 'object', properties: { model_job_id: { type: 'string', maxLength: 128 }, threshold: { type: 'number', minimum: 0.05, maximum: 0.95 } }, additionalProperties: false },
      preview: async args => {
        const { model, threshold } = await draftChoice(deps, args), value = facts();
        return { title: '라벨 초안 만들기', lines: [`모델 ${model.jobId}${model.dataState === 'changed' ? ' · 데이터가 바뀐 뒤 학습한 모델 · 평가 근거 아님' : ''}`,
          `기준 신뢰도 ${threshold.toFixed(2)} · 라벨 없는 이미지 약 ${value.unlabeled}장`, '초안은 정답이 아니며 라벨 파일·학습 데이터를 바꾸지 않습니다.'],
          undo: '만들어진 초안은 2단계 초안 검토에서 거절하면 사라지고, 실행 중이면 같은 곳에서 취소할 수 있습니다.' };
      },
      execute: async args => {
        const { model, threshold } = await draftChoice(deps, args);
        const batch = await deps.startDraftBatch(model.jobId, threshold);
        return ok({ batch_id: batch.id, images: batch.total, threshold }, `초안 만들기를 시작했습니다(이미지 ${batch.total}장). 2단계 초안 검토에서 확인하세요.`, `batch:${batch.id}`);
      } },
    { name: 'start_training', tier: 'write', parameters: NO_ARGS,
      description: '3단계 학습 준비가 끝났을 때 학습을 시작합니다. 사람이 미리보기를 보고 확인해야 실행됩니다.',
      preview: async () => {
        const state = trainingReady(deps), value = facts();
        return { title: '학습 시작', lines: [`작업 ${value.task ?? '확인 필요'} · 이미지 ${value.images}장(학습 ${value.split.train} · 검증 ${value.split.val})`,
          `프리셋 ${state.preset} · 계산 위치 ${state.computeOnServer ? '선택한 계산 서버' : '이 컴퓨터'}`],
          undo: '3단계에서 \'학습 중지\'를 누르면 멈춥니다. 기존 모델과 데이터는 바뀌지 않습니다.' };
      },
      execute: async () => {
        trainingReady(deps);
        const started = await deps.startTraining();
        return ok({ started: true, job_id: started.jobId }, '학습을 시작했습니다. 3단계에서 진행을 볼 수 있습니다.', started.jobId ? `job:${started.jobId}` : undefined);
      } },
    // ---- forbidden: the person does these themselves ----
    ...([
      ['approve_model', '모델 승인', '4단계 결과 평가 화면에서 검토한 사람이 직접 승인하세요.'],
      ['approve_flow', '검사 흐름 승인', '5단계 검사 흐름 화면에서 검토한 사람이 직접 승인·활성화하세요.'],
      ['deploy_or_register_service', '배포·서비스 등록', '6단계와 운영 메뉴의 배포·서비스 등록 화면에서 권한 있는 사람이 직접 하세요.'],
      ['delete_original_images', '원본 이미지 삭제', '원본 이미지는 도우미가 지우지 않습니다. 필요하면 백업한 뒤 파일 관리자에서 직접 정리하세요.'],
      ['delete_label_files', '라벨 파일 삭제', '라벨은 2단계 편집 화면에서 도형 단위로 직접 지우세요. 파일 단위 삭제는 열지 않습니다.'],
      ['confirm_ground_truth', '정답 확정', '정답 확정은 검토자가 2단계 검수 화면에서 직접 하세요.'],
      ['adopt_drafts', '초안 채택', '초안 채택은 2단계 초안 검토에서 사람이 미리보기를 보고 직접 누르세요.'],
      ['change_account_or_permissions', '계정·권한 변경', '계정과 권한은 서버 관리(공동 작업 서버) 화면에서 관리자가 직접 바꾸세요.'],
      ['send_files_outside', '외부로 파일 보내기', '내보내기·공유는 해당 내보내기 화면에서 사람이 직접 하세요.'],
    ] as const).map(([name, title, where]): ToolDefinition => ({
      name, tier: 'forbidden', where, parameters: { type: 'object', properties: { request: { type: 'string', maxLength: 200 } }, additionalProperties: false },
      description: `${title}: 도우미가 실행하지 않는 일입니다. 요청받으면 이 도구로 "직접 하세요"와 위치를 알립니다.`,
    })),
  ];
  return new AssistantToolRegistry(definitions);
}

function classChoice(deps: ToolDeps, args: Record<string, unknown>): { image: ImageChoice; className: string | null } {
  if (deps.facts().task !== 'classification') throw new ToolRefusal('not_classification', '이미지별 클래스 지정은 분류 프로젝트에서만 씁니다.');
  if (typeof args.image !== 'string' || !args.image.trim()) throw new ToolRefusal('invalid_arguments', 'image에 "current"나 파일 이름을 넣으세요.');
  const matches = deps.findImage(fileName(args.image.trim()) || args.image.trim());
  if (matches.length !== 1) throw new ToolRefusal(matches.length ? 'ambiguous_image' : 'unknown_image', matches.length
    ? '같은 파일 이름의 이미지가 여러 장입니다. 2단계에서 그 이미지를 연 뒤 image에 "current"를 쓰세요.'
    : '그 이미지를 찾지 못했습니다. 2단계에서 이미지를 연 뒤 image에 "current"를 쓰세요.');
  const raw = args.class_name;
  if (raw !== null && (typeof raw !== 'string' || !raw.trim() || raw.length > 100)) throw new ToolRefusal('invalid_arguments', 'class_name은 1~100자 이름이거나 null(폴더 클래스로)이어야 합니다.');
  return { image: matches[0], className: raw === null ? null : raw.trim() };
}

async function draftChoice(deps: ToolDeps, args: Record<string, unknown>): Promise<{ model: DraftModel; threshold: number }> {
  const threshold = args.threshold === undefined ? 0.5 : args.threshold;
  if (typeof threshold !== 'number' || !Number.isFinite(threshold) || threshold < 0.05 || threshold > 0.95) throw new ToolRefusal('invalid_arguments', 'threshold는 0.05~0.95 사이 숫자여야 합니다.');
  const models = await deps.draftModels();
  return { model: draftModelChoice(models, args.model_job_id), threshold: Math.round(threshold * 100) / 100 };
}

function trainingReady(deps: ToolDeps): TrainingFacts {
  const snapshot = deps.snapshot(), state = deps.training();
  if (state.isTraining) throw new ToolRefusal('already_training', '이미 학습이 진행 중입니다. 3단계에서 진행을 확인하세요.');
  if (snapshot?.stage !== 3 || snapshot.status !== 'ready' || !state.folderReady)
    throw new ToolRefusal('training_not_ready', '3단계 학습 준비가 끝나지 않았습니다. 3단계를 열어 학습 전 점검을 확인한 뒤 다시 요청하세요.');
  return state;
}

export class AssistantToolRegistry {
  private readonly byName: Map<string, ToolDefinition>;
  constructor(private readonly definitions: ToolDefinition[]) {
    this.byName = new Map(definitions.map(tool => [tool.name, tool]));
  }

  specs(): AssistantToolSpec[] {
    return this.definitions.map(tool => ({ name: tool.name, description: tool.description, parameters: tool.parameters }));
  }

  tier(name: string): ToolTier | null { return this.byName.get(name)?.tier ?? null; }

  /** read and navigate tools only; any other tier is refused here, whatever the caller asks. */
  async runAutomatic(name: string, args: Record<string, unknown>): Promise<ToolOutcome> {
    const tool = this.byName.get(name);
    if (!tool || (tool.tier !== 'read' && tool.tier !== 'navigate') || !tool.run) throw new ToolRefusal('not_automatic', '이 도구는 바로 실행할 수 없습니다.');
    return tool.run(args);
  }

  /** The answer for a forbidden tool: it is never run. */
  refusal(name: string): ToolOutcome {
    const tool = this.byName.get(name);
    const where = tool?.tier === 'forbidden' ? tool.where! : '도우미가 쓸 수 있는 도구 목록에 없습니다.';
    return { ok: false, errorCode: 'forbidden', content: `직접 하세요. ${where}`, summary: `직접 하세요: ${where}` };
  }

  async preview(name: string, args: Record<string, unknown>): Promise<ToolPreview> {
    const tool = this.byName.get(name);
    if (!tool || tool.tier !== 'write' || !tool.preview) throw new ToolRefusal('not_write', '이 도구는 미리보기 대상이 아닙니다.');
    return tool.preview(args);
  }

  /** A write tool runs only with the person's confirmation from the panel and a run id for its backend write. */
  async execute(name: string, args: Record<string, unknown>, confirmation: { confirmed: boolean; runId: string } | null | undefined): Promise<ToolOutcome> {
    const tool = this.byName.get(name);
    if (!tool || tool.tier !== 'write' || !tool.execute) throw new ToolRefusal('not_write', '쓰기 도구가 아닙니다.');
    if (confirmation?.confirmed !== true || !/^run_[a-f0-9]{24}$/.test(confirmation.runId)) throw new ToolRefusal('confirmation_required', '사람의 확인 없이 쓰기 도구를 실행하지 않습니다.');
    return tool.execute(args);
  }
}
