import {useState} from 'react';
import {useAssistantTaskStore, useOneclickTaskProgressStore} from '../../stores/useAssistantTaskStore';
import {useAssistantStore} from '../../stores/useAssistantStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {AutoProgressDialog} from '../wizard/AutoProgressDialog';
import {learningTaskProgress, selectLearningTask} from './learningTaskProgress';

/** One compact view of the same records used by the assistant and automatic progression. No polling here. */
export function LearningJourneyPanel() {
  const [open, setOpen] = useState(false);
  const oneclick = useOneclickTaskProgressStore();
  const assistant = useAssistantTaskStore();
  const projectReady = useProjectStore(s => Boolean(s.projectDir && s.project && !s.isProjectBusy));
  const hasData = useDatasetStore(s => s.hasSelectedFolder && s.sourceImages > 0 && !s.isLoading && !s.importError);
  const selected = selectLearningTask(oneclick, assistant);
  const progress = selected?.state.task ? learningTaskProgress(selected.state.task, selected.state.error) : null;
  // Empty stages already explain their one next action. Reserve this strip for prepared data or real work.
  if (!progress && (!projectReady || !hasData)) return null;
  return <>
    <section aria-label="처음 만드는 AI 진행" className="shrink-0 border-b border-[var(--workspace-border)] bg-[var(--workspace-inset)] px-4 py-2 text-sm text-[var(--workspace-text)]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1" role="status" aria-live="polite">
          <p className="font-semibold">{progress?.label || '사진과 라벨을 준비하면, 학습부터 시험 검사까지 이어서 해볼 수 있어요.'}</p>
          {progress ? <p className="mt-0.5 line-clamp-2 break-words text-[var(--workspace-muted)]">{progress.message} <span aria-live="off">· {progress.completed}/{progress.total} 작업 완료</span></p>
            : <p className="mt-0.5 text-[var(--workspace-muted)]">필요한 입력은 진행 중에 알려드립니다. GPT 연결 없이도 사용할 수 있습니다.</p>}
        </div>
        <button type="button" className="workspace-button" disabled={!projectReady || (!progress && !hasData)}
          onClick={() => {if (projectReady && (progress || hasData)) {if (selected?.owner === 'assistant') useAssistantStore.getState().open(); else setOpen(true);}}}>
          {progress ? '진행 내역과 다음 행동 보기' : '학습부터 시험 검사까지 진행…'}
        </button>
      </div>
      {progress && <p className="mt-1 text-xs text-[var(--workspace-muted)]">{progress.detail}</p>}
    </section>
    {open && <AutoProgressDialog onClose={() => setOpen(false)}/>}
  </>;
}
