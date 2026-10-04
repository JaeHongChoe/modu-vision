import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, Search } from 'lucide-react';
import { api, resolveApiUrl, type LibraryImage, type LibraryQuery } from '../../services/api';
import { columnsFor, nearEnd, visibleRows } from '../../utils/virtualWindow';

interface Props {
  /** Image ids currently chosen (shown as selected wherever they appear). */
  selectedIds: ReadonlySet<string>;
  onPick: (item: LibraryImage) => void;
  /** Called with the reason when the project has no validated revision to browse. */
  onUnavailable?: (reason: string) => void;
  /** Filters the browser opens with (the user can change them). */
  initialFilters?: Filters;
}

const ROW_HEIGHT = 176;
const MIN_TILE = 150;
const GAP = 10;
const PAGE = 120;
type Filters = Pick<LibraryQuery, 'split' | 'state' | 'workflow_state' | 'tag' | 'product' | 'lot'>;
const metadataLabels = { tag: '태그', product: '제품', lot: 'Lot' } as const;

/**
 * The project's validated images, searched and filtered on the server and paged by cursor. Only the rows on screen are
 * rendered, so a hundred thousand images cost the same as a hundred; the next page loads as the end comes into view.
 */
export const ImageLibraryBrowser: React.FC<Props> = ({ selectedIds, onPick, onUnavailable, initialFilters }) => {
  const [text, setText] = useState('');
  const [query, setQuery] = useState('');
  const [filters, setFilters] = useState<Filters>(initialFilters ?? {});
  const queryIdentity = JSON.stringify([query, filters]);
  const cursorIdentity = useRef<string | null>(null);
  const [items, setItems] = useState<LibraryImage[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);
  const [scannedTo, setScannedTo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const viewport = useRef<HTMLDivElement | null>(null);
  const request = useRef(0);
  const unavailable = useRef(onUnavailable);  // a new callback each parent render must not restart the search
  unavailable.current = onUnavailable;

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(text.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [text]);

  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const measure = () => setSize({ width: element.clientWidth, height: element.clientHeight });
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const load = useCallback(async (from: string | null) => {
    const sequence = ++request.current;
    setLoading(true);
    setError(null);
    try {
      const page = await api.library.images({ q: query || undefined, ...filters, cursor: from, limit: PAGE });
      if (sequence !== request.current) return;  // a newer search answered meanwhile
      setItems((current) => (from ? [...current, ...page.items] : page.items));
      cursorIdentity.current = queryIdentity;
      setCursor(page.next_cursor);
      setFinished(page.next_cursor === null);
      setScannedTo(page.complete_page ? null : page.scanned_to);
    } catch (caught) {
      if (sequence !== request.current) return;
      const status = (caught as { status?: number }).status;
      const reason = caught instanceof Error ? caught.message : String(caught);
      if (status === 409) unavailable.current?.(reason);
      setError(reason);
      setFinished(true);
    } finally {
      if (sequence === request.current) setLoading(false);
    }
  }, [query, filters, queryIdentity]);

  useEffect(() => {
    setItems([]);
    setCursor(null);
    setFinished(false);
    setScrollTop(0);
    if (viewport.current) viewport.current.scrollTop = 0;
    void load(null);
  }, [load]);

  const columns = columnsFor(size.width, MIN_TILE, GAP);
  const rowCount = Math.ceil(items.length / columns);
  const rowWindow = visibleRows({ scrollTop, viewportHeight: size.height, rowHeight: ROW_HEIGHT, rowCount, overscan: 2 });

  useEffect(() => {
    // A filter reset and end-of-list effect can run in the same commit.
    // Only advance a cursor returned for this exact query.
    if (cursorIdentity.current === queryIdentity && !loading && !finished && cursor && nearEnd(rowWindow, rowCount)) void load(cursor);
  }, [loading, finished, cursor, rowWindow.end, rowCount, load, queryIdentity]);

  const rows: LibraryImage[][] = [];
  for (let row = rowWindow.start; row < rowWindow.end; row += 1) rows.push(items.slice(row * columns, row * columns + columns));
  const choose = (key: keyof Filters, value: string) => setFilters((current) => ({ ...current, [key]: value || undefined }));

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex min-w-[220px] flex-1 items-center gap-2 rounded border border-[#2B3547] bg-[#0D1117] px-2 py-1.5">
          <Search className="h-3.5 w-3.5 text-slate-500" aria-hidden />
          <input value={text} onChange={(event) => setText(event.target.value)} aria-label="이미지 검색"
            placeholder="파일 이름이나 폴더로 검색" className="w-full bg-transparent text-xs text-slate-200 outline-none placeholder:text-slate-600" />
        </label>
        {(['tag', 'product', 'lot'] as const).map((key) => (
          <label key={key} className="flex items-center gap-2 rounded border border-[#2B3547] bg-[#0D1117] px-2 py-1.5 text-xs text-slate-300">
            <span>{metadataLabels[key]}</span>
            <input aria-label={metadataLabels[key]} value={filters[key] || ''} onChange={(event) => choose(key, event.target.value)}
              placeholder={`모든 ${metadataLabels[key]}`} className="w-28 bg-transparent text-slate-200 outline-none placeholder:text-slate-600" />
          </label>
        ))}
        <select aria-label="분할" value={filters.split || ''} onChange={(event) => choose('split', event.target.value)}
          className="rounded border border-[#2B3547] bg-[#0D1117] px-2 py-1.5 text-xs text-slate-300">
          <option value="">모든 분할</option><option value="train">train</option><option value="val">val</option><option value="test">test</option>
        </select>
        <select aria-label="이미지 상태" value={filters.state || ''} onChange={(event) => choose('state', event.target.value)}
          className="rounded border border-[#2B3547] bg-[#0D1117] px-2 py-1.5 text-xs text-slate-300">
          <option value="">모든 상태</option><option value="valid">정상</option><option value="invalid">손상·제외</option>
        </select>
        <select aria-label="검토 상태" value={filters.workflow_state || ''} onChange={(event) => choose('workflow_state', event.target.value)}
          className="rounded border border-[#2B3547] bg-[#0D1117] px-2 py-1.5 text-xs text-slate-300">
          <option value="">모든 검토 상태</option><option value="unworked">미작업</option><option value="needs_review">검토 필요</option><option value="approved">승인</option>
        </select>
      </div>
      <div ref={viewport} onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)} role="list" aria-label="데이터 버전 이미지"
        className="relative min-h-[320px] flex-1 overflow-y-auto rounded border border-[#2B3547] bg-[#0D1117]">
        <div style={{ height: rowWindow.totalHeight }} />
        <div className="absolute inset-x-0 top-0 px-2" style={{ transform: `translateY(${rowWindow.offsetTop}px)` }}>
          {rows.map((row, offset) => (
            <div key={rowWindow.start + offset} className="grid pb-[10px]" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: GAP, height: ROW_HEIGHT }}>
              {row.map((item) => {
                const selected = selectedIds.has(item.image_uuid);
                return (
                  <button key={item.image_uuid} type="button" role="listitem" aria-pressed={selected} onClick={() => onPick(item)}
                    title={item.relative_path}
                    className={`relative flex flex-col overflow-hidden rounded border text-left ${selected ? 'border-sky-400 ring-1 ring-sky-400' : 'border-[#2B3547] hover:border-[#4A5A70]'}`}>
                    <img src={resolveApiUrl(`/api/dataset/thumbnail/${encodeURIComponent(item.file_name)}?file_path=${encodeURIComponent(item.file_path)}`)}
                      alt="" loading="lazy" className="h-[110px] w-full bg-black object-contain" />
                    <span className="truncate px-1.5 pt-1 font-mono text-[11px] text-slate-200">{item.file_name}</span>
                    <span className="truncate px-1.5 text-[10px] text-slate-500">{[item.label, item.split, ...item.tags].filter(Boolean).join(' · ') || item.relative_path}</span>
                    {!item.valid && <span className="absolute left-1 top-1 flex items-center gap-1 rounded bg-red-950/90 px-1 text-[10px] text-red-200"><AlertTriangle className="h-3 w-3" aria-hidden />{item.error_code}</span>}
                    {selected && <span className="absolute right-1 top-1 rounded-full bg-sky-500 p-0.5"><Check className="h-3 w-3 text-white" aria-hidden /></span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        {!loading && items.length === 0 && !error && <div className="absolute inset-0 flex items-center justify-center text-xs text-slate-500">조건에 맞는 이미지가 없습니다.</div>}
      </div>
      <div className="flex justify-between text-[11px] text-slate-500" aria-live="polite">
        <span>{items.length.toLocaleString()}장 표시{finished ? ' · 끝' : ' · 아래로 스크롤하면 더 불러옵니다'}</span>
        {loading && <span>{scannedTo ? `조건에 맞는 이미지를 찾는 중 (${scannedTo}까지 확인)` : '불러오는 중'}</span>}
        {error && <span className="text-red-300">{error}</span>}
      </div>
    </div>
  );
};
