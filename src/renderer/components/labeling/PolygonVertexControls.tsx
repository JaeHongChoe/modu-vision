import { ChevronLeft, ChevronRight, MousePointer, Plus, Minus } from 'lucide-react';

interface Props {
  count: number;
  selectedIndex: number | null;
  disabled: boolean;
  message: string | null;
  onSelect: (index: number | null) => void;
  onInsert: () => void;
  onDelete: () => void;
}

/** Pointer precision has a button/keyboard equivalent; all edits remain undoable. */
export function PolygonVertexControls({count, selectedIndex, disabled, message, onSelect, onInsert, onDelete}: Props) {
  return (
    <div role="group" aria-label="다각형 점 편집" data-testid="polygon-point-controls"
      data-polygon-point-controls className="workspace-record p-2 m-2 shrink-0"
      onKeyDown={event => {
        // A native button owns Space. With no point selected, Delete here must
        // never fall through to the canvas's whole-annotation delete shortcut.
        if (event.code === 'Space' || (selectedIndex === null && ['Delete', 'Backspace'].includes(event.key))) {
          event.stopPropagation();
        }
      }}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold">점 편집</span>
        <button type="button" className="workspace-button" disabled={disabled} aria-label="이전 점" title="이전 점"
          onClick={() => onSelect(selectedIndex === null ? count - 1 : (selectedIndex + count - 1) % count)}><ChevronLeft className="w-4 h-4" /></button>
        <span className="tabular-nums min-w-[5rem]" aria-live="polite">{selectedIndex === null ? `선택 전 / ${count}점` : `${selectedIndex + 1} / ${count}점`}</span>
        <button type="button" className="workspace-button" disabled={disabled} aria-label="다음 점" title="다음 점"
          onClick={() => onSelect(selectedIndex === null ? 0 : (selectedIndex + 1) % count)}><ChevronRight className="w-4 h-4" /></button>
        <button type="button" className="workspace-button" disabled={disabled || selectedIndex === null} aria-label="변에 점 추가"
          title="선택한 점 다음 변의 중간에 점을 추가합니다" onClick={onInsert}><Plus className="w-4 h-4" /></button>
        <button type="button" className="workspace-button" disabled={disabled || selectedIndex === null || count <= 3} aria-label="점 삭제"
          title={count <= 3 ? '다각형은 최소 3점을 유지해야 합니다' : '선택한 점만 삭제합니다. 실행 취소로 되돌릴 수 있습니다'}
          onClick={onDelete}><Minus className="w-4 h-4" /></button>
        <button type="button" className="workspace-button" disabled={disabled} aria-pressed={selectedIndex === null}
          aria-label="도형 선택" title="점 선택 해제 · 도형 선택"
          onClick={() => onSelect(null)}><MousePointer className="w-4 h-4" /></button>
      </div>
      <p className="text-sm mt-1" role="status">
        {message || (count <= 3 ? '최소 3점 · 방향키로 이동 · Shift+변 클릭으로 추가'
          : '점을 선택해 방향키로 이동 · Shift+변 클릭으로 점 추가 · Delete로 선택한 점 삭제')}
      </p>
    </div>
  );
}
