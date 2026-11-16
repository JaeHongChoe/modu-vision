import React, { useEffect, useId, useRef, useState, type ReactNode } from 'react';

export type TrainingDataBlock = {
  id: string;
  title: string;
  summary: string;
  status: 'ready' | 'attention' | 'optional' | 'pending' | 'working' | 'error';
  content: ReactNode;
};

const statusLabels: Record<TrainingDataBlock['status'], string> = {
  ready: '입력 준비됨',
  attention: '확인 필요',
  optional: '선택 사항',
  pending: '설정 확인',
  working: '진행 중',
  error: '오류',
};

/** Navigation only. Each block's existing owner supplies its readiness and actions. */
export function TrainingDataWorkflow({ scope, blocks }: { scope: string; blocks: TrainingDataBlock[] }) {
  const instanceId = useId();
  const firstId = (blocks.find(block => block.status === 'attention' || block.status === 'error')
    ?? blocks[0])?.id ?? null;
  const [selection, setSelection] = useState({ scope, id: firstId });
  const selectedId = selection.scope === scope && blocks.some(block => block.id === selection.id)
    ? selection.id : firstId;
  const tabRefs = useRef(new Map<string, HTMLButtonElement>());

  useEffect(() => {
    if (selection.scope !== scope || selection.id !== selectedId) {
      setSelection({ scope, id: selectedId });
    }
  }, [scope, selectedId, selection]);
  useEffect(() => {
    if (selectedId !== null) {
      tabRefs.current.get(selectedId)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  }, [scope, selectedId]);

  const ids = (id: string) => {
    const base = `${instanceId}-${encodeURIComponent(id)}`;
    return { tab: `${base}-tab`, panel: `${base}-panel`, summary: `${base}-summary` };
  };
  const select = (id: string) => setSelection({ scope, id });
  const selectAndFocus = (id: string) => {
    select(id);
    const tab = tabRefs.current.get(id);
    tab?.focus({ preventScroll: true });
    tab?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  };
  const navigate = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    let next: number;
    switch (event.key) {
      case 'ArrowRight': next = (index + 1) % blocks.length; break;
      case 'ArrowLeft': next = (index - 1 + blocks.length) % blocks.length; break;
      case 'Home': next = 0; break;
      case 'End': next = blocks.length - 1; break;
      default: return;
    }
    event.preventDefault();
    selectAndFocus(blocks[next].id);
  };

  return (
    <div className="training-data-workflow">
      <div className="training-data-workflow__rail" role="tablist"
        aria-label="학습 데이터 준비 단계" aria-orientation="horizontal">
        {blocks.map((block, index) => {
          const selected = block.id === selectedId;
          const names = ids(block.id);
          return (
            <button key={block.id} type="button" role="tab" id={names.tab}
              aria-controls={names.panel} aria-selected={selected} aria-describedby={names.summary}
              tabIndex={selected ? 0 : -1} data-status={block.status}
              className="workspace-button training-data-workflow__tab"
              ref={element => {
                if (element) tabRefs.current.set(block.id, element);
                else tabRefs.current.delete(block.id);
              }}
              onClick={() => select(block.id)} onKeyDown={event => navigate(event, index)}>
              <span className="training-data-workflow__tab-top">
                <span className="training-data-workflow__number" aria-hidden="true">{index + 1}</span>
                <span className="training-data-workflow__status">{statusLabels[block.status]}</span>
              </span>
              <span className="training-data-workflow__title">{block.title}</span>
              <span className="training-data-workflow__summary" id={names.summary}>{block.summary}</span>
            </button>
          );
        })}
      </div>
      {blocks.length === 0 && (
        <p className="training-data-workflow__empty">준비할 항목이 없습니다.</p>
      )}
      {blocks.map((block, index) => {
        const names = ids(block.id);
        return (
          <section key={block.id} role="tabpanel" id={names.panel} aria-labelledby={names.tab}
            hidden={block.id !== selectedId} tabIndex={0} className="training-data-workflow__panel">
            <header className="training-data-workflow__panel-header">
              <div>
                <h3>{block.title}</h3>
                <p>{block.summary}</p>
              </div>
              <span className="training-data-workflow__status" data-status={block.status}>
                {statusLabels[block.status]}
              </span>
            </header>
            <div className="training-data-workflow__content">{block.content}</div>
            {blocks.length > 1 && (
              <nav className="training-data-workflow__navigation" aria-label="준비 설정 이동">
                {index > 0 ? (
                  <button type="button" className="workspace-button workspace-button--quiet"
                    onClick={() => selectAndFocus(blocks[index - 1].id)}>
                    이전 설정: {blocks[index - 1].title}
                  </button>
                ) : <span />}
                {index < blocks.length - 1 && (
                  <button type="button" className="workspace-button workspace-button--quiet"
                    onClick={() => selectAndFocus(blocks[index + 1].id)}>
                    다음 설정: {blocks[index + 1].title}
                  </button>
                )}
              </nav>
            )}
          </section>
        );
      })}
    </div>
  );
}
