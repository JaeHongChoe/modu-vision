import React, { useCallback } from 'react';
import type { LabelSuggestion } from '../../services/api';
import type { ProjectContext } from '../../services/api';
import { ScopedDatasetThumbnail } from '../common/ScopedDatasetThumbnail';
import { draftSourceText, topConfidence } from './draftReview';

const fileName = (path: string) => path.split(/[\\/]/).pop() || path;

interface Props {
  drafts: LabelSuggestion[];
  /** Candidate ids the person chose per draft; a draft without an entry shows every candidate. */
  choices: Map<string, Set<string>>;
  excluded: Set<string>;
  /** Drafts whose tile image has shown on this page: only these count as seen. */
  loaded: Set<string>;
  authority: string;
  context: ProjectContext | null;
  current: () => boolean;
  disabled: boolean;
  onToggle: (id: string) => void;
  onLoaded: (id: string) => void;
  onOpen: (draft: LabelSuggestion) => void;
}

/** The chosen candidates drawn over a small tile: strokes keep a screen width, so a 160px tile still shows them. */
function Overlay({ draft, selected }: { draft: LabelSuggestion; selected: Set<string> | undefined }) {
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${draft.image_width} ${draft.image_height}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      {draft.candidates.filter((item) => !selected || selected.has(item.id)).map((item) => {
        const annotation = item.annotation;
        if (annotation.type === 'brush_mask' && annotation.mask_rle) return <image key={item.id} href={annotation.mask_rle} x={0} y={0} width={draft.image_width} height={draft.image_height} opacity={.55} />;
        if (annotation.type === 'bbox' && annotation.bbox) {
          const [x1, y1, x2, y2] = annotation.bbox;
          return <rect key={item.id} x={x1} y={y1} width={x2 - x1} height={y2 - y1} fill="rgba(34,211,238,.12)" stroke="#22d3ee" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />;
        }
        if (annotation.type === 'polygon' && annotation.polygon?.length) {
          return <polygon key={item.id} points={annotation.polygon.map(([x, y]) => `${x},${y}`).join(' ')} fill="rgba(245,158,11,.2)" stroke="#f59e0b" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />;
        }
        return null;
      })}
    </svg>
  );
}

/** The class names a tile adopts, once it has shown: a classification or anomaly draft is one whole-image tag the
 *  overlay cannot draw, and a box or outline does not say its class, so both are named in words. */
function tileLabels(draft: LabelSuggestion, selected: Set<string> | undefined): { tags: string[]; shapes: string[] } {
  const chosen = draft.candidates.filter((item) => !selected || selected.has(item.id));
  const distinct = (rows: typeof chosen) => [...new Set(rows.map((item) => item.annotation.label).filter(Boolean))];
  return { tags: distinct(chosen.filter((item) => item.annotation.type === 'tag')), shapes: distinct(chosen.filter((item) => item.annotation.type !== 'tag')) };
}

/** A page of drafts as tiles. A tile is a button: pressed means "included in this page's adoption". A click never
 *  moves the canvas; '열기' does. Arrow keys move between tiles and stay inside the grid. */
export const DraftGrid: React.FC<Props> = ({ drafts, choices, excluded, loaded, authority, context, current, disabled, onToggle, onLoaded, onOpen }) => {
  const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'];
    if (!keys.includes(event.key)) return;
    const tiles = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button[data-draft-tile]'));
    const index = tiles.indexOf(document.activeElement as HTMLButtonElement);
    if (index < 0) return;
    event.preventDefault();
    event.stopPropagation();
    const columns = Math.max(1, Math.round(event.currentTarget.clientWidth / Math.max(1, tiles[0]?.offsetWidth || 1)));
    const step = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : event.key === 'ArrowUp' ? -columns : columns;
    tiles[Math.min(tiles.length - 1, Math.max(0, index + step))]?.focus();
  }, []);
  return (
    <div role="group" aria-label="초안 격자" onKeyDown={onKeyDown} className="grid gap-2" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))' }}>
      {drafts.map((draft) => {
        const included = !excluded.has(draft.id);
        const seen = loaded.has(draft.id);
        const labels = tileLabels(draft, choices.get(draft.id));
        return (
          <div key={draft.id} className={`relative flex flex-col overflow-hidden rounded-lg border ${included ? 'border-emerald-700' : 'border-slate-700 opacity-60'} bg-[#0B0E14]`}>
            <button type="button" data-draft-tile aria-pressed={included} aria-label={`${fileName(draft.image_path)} 이 페이지 채택에 포함`} disabled={disabled}
              onClick={() => onToggle(draft.id)} title={included ? '누르면 이 페이지 채택에서 뺍니다' : '누르면 이 페이지 채택에 넣습니다'}
              className="relative block w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400" style={{ aspectRatio: `${draft.image_width} / ${draft.image_height}` }}>
              <ScopedDatasetThumbnail path={`/api/dataset/thumbnail/${encodeURIComponent(draft.image_id)}?file_path=${encodeURIComponent(draft.image_path)}&size=256`}
                authority={authority} context={context} current={current} alt="모델 라벨 제안 미리보기" className="absolute inset-0 h-full w-full object-contain"
                onLoad={() => onLoaded(draft.id)} />
              {seen && <Overlay draft={draft} selected={choices.get(draft.id)} />}
              {seen && labels.tags.length > 0 && <span className="absolute left-1 top-1 max-w-[calc(100%-8px)] truncate rounded bg-cyan-950 px-1.5 py-0.5 text-[11px] font-semibold text-cyan-100" title={labels.tags.join(' · ')}>{labels.tags.join(' · ')}</span>}
              {!seen && <span className="absolute inset-x-0 bottom-0 bg-black/60 px-1 py-0.5 text-[11px] text-slate-200">불러오는 중 · 보인 뒤 채택 가능</span>}
            </button>
            <div className="flex items-center justify-between gap-1 px-1.5 py-1 text-[11px] text-slate-300">
              <span className="min-w-0 truncate" title={fileName(draft.image_path)}>{fileName(draft.image_path)}</span>
              <span className="shrink-0 text-slate-400">{draft.candidates.length}개 · {Math.round(topConfidence(draft) * 100)}%</span>
            </div>
            {seen && labels.shapes.length > 0 && <p className="truncate px-1.5 text-[11px] text-cyan-200" title={labels.shapes.join(' · ')}>{labels.shapes.join(' · ')}</p>}
            <div className="flex items-center justify-between gap-1 px-1.5 pb-1 text-[11px]">
              <span className="min-w-0 truncate text-slate-500" title={draftSourceText(draft)}>{draftSourceText(draft)}</span>
              <button type="button" onClick={() => onOpen(draft)} disabled={disabled} className="shrink-0 rounded border border-slate-600 px-1.5 py-0.5 text-slate-200 hover:bg-slate-700 disabled:opacity-40">열기</button>
            </div>
          </div>
        );
      })}
    </div>
  );
};
