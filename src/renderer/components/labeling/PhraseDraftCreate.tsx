import React, { useEffect, useRef, useState } from 'react';
import { api } from '../../services/api';
import { host } from '../../services/hostAdapter';
import { foundationLabelingApi as provider, labelingJobActive, type FoundationBatch, type FoundationSetup } from '../../services/foundationLabelingApi';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useDatasetStore } from '../../stores/useDatasetStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { bumpWorkflowEvidence } from '../../stores/workflowEvidence';
import { useDraftReviewStore } from '../../stores/useDraftReviewStore';
import { labelingScope } from './foundationRequest';
import { Explain } from '../common/Explain';
import { draftError, draftRefusalText } from './draftReview';
import { registerPanelError } from '../../stores/assistContext';
import { useScopedError } from '../common/useScopedError';

/** Design 3.3 ③: in a project with no model yet, a phrase ("scratch, dent") makes the local text model draft every
 *  unlabeled image. Detection drafts are Grounding DINO boxes; segmentation drafts are SAM2 outlines inside those boxes.
 *  The drafts go to the same review queue as every other draft: nothing becomes a label until a person adopts it. */
export type PhraseDraftTask = 'detection' | 'segmentation';

/** Which local models a task's phrase drafts need, and the first one missing (Korean reason), or null when ready. */
export function phraseDraftBlocked(setup: FoundationSetup | null, task: PhraseDraftTask): string | null {
  if (!setup) return '모델 준비 상태를 읽는 중입니다.';
  const text = setup.providers.grounding_dino;
  if (!text.dependency_available) return '백엔드 Python 환경에 transformers가 없습니다. requirements-semantic-labeling.txt를 설치한 뒤 다시 여세요.';
  if (!text.ready) return '문구 모델(Grounding DINO) 폴더를 아직 지정하지 않았거나 그 폴더에 모델 파일이 없습니다. 아래에 폴더를 지정하세요.';
  if (task === 'segmentation' && !setup.providers.foundation.ready) return '영역 분할 초안에는 SAM2 모델 폴더도 필요합니다. 아래에 폴더를 지정하세요.';
  return null;
}

/** The batch request: unlabeled images only, the person's phrase and class, and thresholds suited to a first draft. */
export function phraseDraftRequest(input: { task: PhraseDraftTask; prompt: string; label: string; classIds: Record<string, number>;
  imagePaths: string[]; setup: FoundationSetup }): Record<string, unknown> {
  return {
    backend: input.task === 'detection' ? 'grounding_dino' : 'foundation', prompt: input.prompt.trim(), label: input.label,
    class_ids: input.classIds, image_paths: input.imagePaths, output_geometry: input.task === 'detection' ? 'bbox' : 'polygon',
    // One number serves as the box score and, for outlines, the floor on min(mask IoU, box score): 0.3 keeps faint parts.
    threshold: 0.3, text_threshold: 0.25, max_candidates: 20, device: 'cpu',
    labelset_id: input.setup.labelset_id, labelset_version: input.setup.labelset_version,
  };
}

/** The unlabeled images a batch covers: every page read, images with a draft waiting for review left out, at most
 *  `cap` (the server's limit). `more` says that unlabeled images beyond the cap remain for the next batch. */
export async function collectDraftPaths(page: (offset: number) => Promise<{ items: { file_path: string }[]; total: number }>,
  pending: ReadonlySet<string>, cap = 5000): Promise<{ paths: string[]; more: boolean }> {
  const paths: string[] = [];
  let offset = 0, total = Infinity;
  while (offset < total && paths.length < cap) {
    const read = await page(offset);
    total = read.total;
    if (!read.items.length) break;
    paths.push(...read.items.map((item) => item.file_path).filter((file) => !pending.has(file)));
    offset += read.items.length;
  }
  return { paths: paths.slice(0, cap), more: paths.length > cap || offset < total };
}

const STATUS: Record<string, string> = { queued: '대기', running: '만드는 중', cancelling: '멈추는 중', completed: '끝남', stopped: '멈춤', failed: '실패', interrupted: '앱이 다시 시작되어 멈춤' };

export function phraseBatchLine(batch: FoundationBatch): string {
  return `${STATUS[batch.status] ?? batch.status} · ${batch.processed}/${batch.total}장 · 초안 ${batch.generated}장 · 찾지 못함 ${batch.zero_candidates}장${batch.failed ? ` · 실패 ${batch.failed}장` : ''}`;
}

/** pendingImages: images that already have a draft waiting for review; a new batch skips them instead of drafting twice. */
interface Props { task: PhraseDraftTask; disabled: boolean; pendingImages: ReadonlySet<string>; onRunningChange: (running: boolean) => void }

export const PhraseDraftCreate: React.FC<Props> = ({ task, disabled, pendingImages, onRunningChange }) => {
  const project = useProjectStore((state) => state.project);
  const projectDir = useProjectStore((state) => state.projectDir);
  const folderPath = useDatasetStore((state) => state.folderPath);
  const categories = useAnnotationStore((state) => state.categories);
  const scope = JSON.stringify([labelingScope({ projectDir, project }), folderPath, task]);
  const live = useRef({ scope, revision: 0 });
  if (live.current.scope !== scope) live.current = { scope, revision: live.current.revision + 1 };
  const revision = live.current.revision;
  const isCurrent = () => live.current.scope === scope && live.current.revision === revision;
  const [setup, setSetup] = useState<FoundationSetup | null>(null);
  const [prompt, setPrompt] = useState('');
  // '' = each phrase as typed (a class of the same name is used when the project has one): an empty project's first
  // drafts name their classes; picking a class puts every unmatched phrase under it instead.
  const [label, setLabel] = useState('');
  const [unlabeled, setUnlabeled] = useState<number | null>(null);
  const [batch, setBatch] = useState<FoundationBatch | null>(null);
  const [phrases, setPhrases] = useState<{ text: string; query: string }[]>([]);
  const [textDir, setTextDir] = useState('');
  const [maskDir, setMaskDir] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useScopedError(scope);
  const batchScope = useRef(scope);
  const [notice, setNotice] = useState('');
  const running = !!batch && labelingJobActive(batch.status);

  useEffect(() => { onRunningChange(running); }, [running, onRunningChange]);
  useEffect(() => () => onRunningChange(false), [onRunningChange]);

  // The project's setup, the unlabeled count and a batch still running (the popover may have been closed meanwhile).
  useEffect(() => {
    let active = true;
    setSetup(null); setBatch(null); setUnlabeled(null); setError(''); setPhrases([]);
    if (!folderPath || !project) return () => { active = false; };
    void Promise.all([provider.setup(), provider.batches(), api.dataset.getImages({ folder_path: folderPath, task: project.task, label_status: 'unlabeled', limit: 1 })])
      .then(([next, listed, page]) => {
        if (!active || !isCurrent()) return;
        setSetup(next); setTextDir(next.configuration.model_dir || ''); setMaskDir(next.configuration.mask_model_dir || '');
        setUnlabeled(page.total);
        setBatch(listed.batches.find((item) => labelingJobActive(item.status)) ?? null);
      }).catch((cause) => { if (active && isCurrent()) setError(draftError(cause)); });
    return () => { active = false; };
  }, [scope, folderPath, project]);

  useEffect(() => {
    // The reset above queues setBatch(null); its old render must not publish that batch in the new scope.
    const currentBatch = batchScope.current === scope;
    batchScope.current = scope;
    if (!projectDir || !folderPath) return;
    const text = [...new Set([error, currentBatch && batch?.error ? draftRefusalText(batch.error) : null].filter(Boolean))].join(' · ');
    return registerPanelError('문구 초안', text);
  }, [scope, projectDir, folderPath, error, batch?.error]);

  useEffect(() => {
    if (!batch || !labelingJobActive(batch.status)) return;
    let active = true;
    const timer = window.setInterval(() => {
      void provider.batch(batch.id).then((next) => {
        if (!active || !isCurrent()) return;
        setBatch(next);
        // New drafts, and fewer unlabeled images to say: the footer and the queue read them again.
        if (!labelingJobActive(next.status)) bumpWorkflowEvidence();
      }).catch((cause) => { if (active && isCurrent()) setError(draftError(cause)); });
    }, 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, [batch?.id, batch?.status, scope]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (cause) { if (isCurrent()) setError(draftError(cause)); } finally { if (isCurrent()) setBusy(false); }
  };
  const saveFolder = (field: 'model_dir' | 'mask_model_dir', path: string) => run(async () => {
    const next = await provider.configure({ [field]: path.trim() });
    if (isCurrent()) { setSetup(next); setTextDir(next.configuration.model_dir || ''); setMaskDir(next.configuration.mask_model_dir || ''); }
  });
  const pickFolder = (field: 'model_dir' | 'mask_model_dir') => run(async () => {
    const path = await host.selectFolder({ purpose: 'model-directory', title: field === 'model_dir' ? '문구 모델(Grounding DINO) 폴더' : 'SAM2 모델 폴더' });
    if (path && isCurrent()) { const next = await provider.configure({ [field]: path }); if (isCurrent()) { setSetup(next); setTextDir(next.configuration.model_dir || ''); setMaskDir(next.configuration.mask_model_dir || ''); } }
  });
  const start = () => run(async () => {
    if (!folderPath || !project) return;
    const current = await provider.setup();
    if (!isCurrent()) return;
    setSetup(current);
    const blocked = phraseDraftBlocked(current, task);
    if (blocked) throw new Error(blocked);
    const { paths, more } = await collectDraftPaths((offset) => api.dataset.getImages({ folder_path: folderPath, task: project.task, label_status: 'unlabeled', limit: 500, offset }), pendingImages);
    if (!isCurrent()) return;
    if (!paths.length) throw new Error(pendingImages.size ? '라벨 없는 이미지마다 검토할 초안이 이미 있습니다. 아래 검토 대기에서 먼저 보세요.' : '라벨 없는 이미지가 없습니다. 초안은 라벨 없는 이미지에만 만듭니다.');
    const classIds = Object.fromEntries(categories.filter((c) => c.id > 0 && c.id <= 255).map((c) => [c.name, c.id]));
    const next = await provider.startBatch(phraseDraftRequest({ task, prompt, label, classIds, imagePaths: paths, setup: current }));
    if (!isCurrent()) return;
    setBatch(next);
    setPhrases(next.phrases ?? []);
    if (more) setNotice(`이번에는 ${paths.length.toLocaleString()}장에 만듭니다(한 번에 최대 5,000장). 끝나면 다시 누르면 나머지에 이어서 만듭니다.`);
  });

  const blocked = phraseDraftBlocked(setup, task);
  const canPick = host.can('pickPaths');
  const folderRow = (field: 'model_dir' | 'mask_model_dir', name: string, value: string, setValue: (value: string) => void) => (
    <div className="flex flex-wrap items-center gap-2">
      <label className="min-w-0 flex-1 text-[11px] text-slate-400">{name}
        <input aria-label={`${name} 경로`} value={value} onChange={(event) => setValue(event.target.value)} placeholder="config.json과 model.safetensors가 있는 폴더"
          className="mt-1 w-full rounded border border-slate-600 bg-[#1D2938] px-2 py-1.5 text-xs text-slate-100" />
      </label>
      {canPick && <button type="button" className="workspace-button" disabled={busy || running} onClick={() => void pickFolder(field)}>폴더 선택</button>}
      <button type="button" className="workspace-button" disabled={busy || running || !value.trim()} onClick={() => void saveFolder(field, value)}>경로 저장</button>
    </div>
  );
  const official = setup?.providers.grounding_dino.official_match;
  return <section aria-label="문구로 초안 만들기" className="space-y-2 rounded border border-indigo-900 bg-[#0D1420] p-2">{host.localBrowser?.()&&<p className="mt-2 text-xs text-slate-400">선택한 반입 자료는 프로젝트 작업 폴더에 검증·복사합니다. 원본은 그대로 보관합니다.</p>}
    <p className="font-semibold text-slate-100">문구로 초안 만들기 <span className="font-normal text-slate-400">· 학습한 모델 없이</span></p>
    <Explain text="찾을 대상을 영어 낱말로 적으면 문구 모델이 라벨 없는 이미지마다 초안을 만듭니다. 긁힘·찍힘·균열·얼룩 같은 흔한 한국어 낱말은 영어로 바꿔 읽습니다. 클래스 이름과 같은 낱말은 그 클래스로, 나머지는 적은 이름의 새 클래스로(클래스를 고르면 그 클래스로) 넣습니다. 검토할 초안이 이미 있는 이미지는 건너뜁니다. 한 묶음의 초안을 다 보기 전에 다른 묶음을 만들면, 한쪽에서 채택한 뒤 다른 쪽 초안은 다시 만들어야 하니 한 묶음씩 끝내세요. 초안은 보고 채택해야 라벨이 되고, 채택할 때마다 백업이 남습니다." />
    {blocked && setup && <div className="space-y-2 rounded border border-amber-800 bg-amber-950/20 p-2 text-[11px] text-amber-100">
      <p role="status">{blocked}</p>
      {setup.providers.grounding_dino.dependency_available && <>
        {folderRow('model_dir', '문구 모델(Grounding DINO) 폴더', textDir, setTextDir)}
        {task === 'segmentation' && folderRow('mask_model_dir', 'SAM2 모델 폴더', maskDir, setMaskDir)}
      </>}
    </div>}
    {!blocked && <p className="text-[11px] text-emerald-200" role="status">문구 모델 준비됨{official ? ' · 공식 고정 파일과 같음' : ''}{task === 'segmentation' ? ' · SAM2 준비됨' : ''}</p>}
    <div className="grid grid-cols-[1fr_auto] gap-2">
      <label className="text-[11px] text-slate-400">찾을 대상
        <input aria-label="찾을 대상 문구" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="예: scratch, dent" disabled={busy || running}
          className="mt-1 w-full rounded border border-slate-600 bg-[#1D2938] px-2 py-1.5 text-xs text-slate-100" />
      </label>
      <label className="text-[11px] text-slate-400">클래스
        <select aria-label="문구 초안 클래스" value={label} onChange={(event) => setLabel(event.target.value)} disabled={busy || running}
          className="mt-1 block rounded border border-slate-600 bg-[#1D2938] px-2 py-1.5 text-xs text-slate-100">
          <option value="">문구 이름 그대로</option>
          {categories.filter((c) => c.id > 0).map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}
        </select>
      </label>
    </div>
    <div className="flex flex-wrap items-center gap-2">
      {/* While drafts wait for review, reviewing them is the next step (the footer's 초안 검토), so this one steps back. */}
      <button type="button" className={pendingImages.size ? 'workspace-button' : 'workspace-button workspace-button--primary'} disabled={disabled || busy || running || !!blocked || !prompt.trim() || !unlabeled}
        onClick={() => void start()}>{unlabeled == null ? '라벨 없는 이미지 세는 중…' : `라벨 없는 이미지 ${unlabeled}장에 초안 만들기`}</button>
      {/* One image at a time instead: SAM2 draws the region a click points at (모델 보조, a draft as well). */}
      <button type="button" className="workspace-button" onClick={() => useDraftReviewStore.getState().requestAssist()}>SAM2 클릭으로 그리기</button>
      {running && batch && <button type="button" className="workspace-button" disabled={busy || batch.status === 'cancelling'}
        onClick={() => void run(async () => { const next = await provider.cancelBatch(batch.id); if (isCurrent()) setBatch(next); })}>멈추기</button>}
    </div>
    {phrases.length > 0 && <p className="text-[11px] text-slate-400">찾는 말: {phrases.map((p) => p.text === p.query ? p.text : `${p.text}→${p.query}`).join(', ')}</p>}
    {batch && <p role="status" className="text-[11px] text-slate-200">{phraseBatchLine(batch)}{!running && batch.generated > 0 ? ' · 아래 검토 대기에서 보고 채택하세요.' : ''}</p>}
    {notice && <p role="status" className="text-[11px] text-slate-300">{notice}</p>}
    {batch?.error && <p className="text-[11px] text-red-200">{draftRefusalText(batch.error)}</p>}
    {error && <p role="alert" className="text-[11px] text-red-200">{error}</p>}
  </section>;
};
