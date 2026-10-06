import React, {useLayoutEffect, useEffect, useRef, useState} from 'react';
import {ChevronLeft, ChevronRight} from 'lucide-react';

export const INSPECTOR_PREFERENCE_KEY = 'modu-vision:flow-inspector-layout:v1';
type Preference = {width: number; collapsed: boolean};
type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem'>;
const DEFAULT: Preference = {width: 340, collapsed: false};
const finiteWidth = (value: number) => Number.isFinite(value) ? value : DEFAULT.width;

/** App-local layout only: no project, graph, draft or model identity is stored. */
export function readInspectorPreference(storage?: Storage): Preference {
  try {
    const value = JSON.parse(storage?.getItem(INSPECTOR_PREFERENCE_KEY) || 'null');
    if (value?.version !== 1 || typeof value.width !== 'number' || !Number.isFinite(value.width)
        || typeof value.collapsed !== 'boolean') return {...DEFAULT};
    return {width: boundedInspectorWidth(value.width), collapsed: value.collapsed};
  } catch { return {...DEFAULT}; }
}
export function writeInspectorPreference(storage: Storage | undefined, value: Preference) {
  try { storage?.setItem(INSPECTOR_PREFERENCE_KEY, JSON.stringify({version: 1, ...value})); }
  catch { /* Layout remains usable when storage is full or unavailable. */ }
}
export function inspectorWidthBounds(rowWidth = 1200) {
  // The palette takes 160 px; retain at least 300 px of graph at normal widths.
  return {min: 260, max: Math.max(260, Math.min(560, finiteWidth(rowWidth) - 460))};
}
export function boundedInspectorWidth(width: number, rowWidth = 1200) {
  const {min, max} = inspectorWidthBounds(rowWidth);
  return Math.round(Math.max(min, Math.min(max, finiteWidth(width))));
}
function browserStorage(): Storage | undefined {
  try { return globalThis.localStorage; } catch { return undefined; }
}

export function FlowInspectorPanel({children, language, storage = browserStorage()}: {
  children: React.ReactNode; language: string; storage?: Storage;
}) {
  const root = useRef<HTMLElement>(null);
  const drag = useRef<{id: number; x: number; width: number} | null>(null);
  const [preference, setPreference] = useState(() => readInspectorPreference(storage));
  const [rowWidth, setRowWidth] = useState(1200);
  useLayoutEffect(() => {
    const row = root.current?.parentElement;
    if (!row) return;
    const measure = () => { if (row.clientWidth > 0) setRowWidth(row.clientWidth); };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure); observer.observe(row);
    return () => observer.disconnect();
  }, []);
  useEffect(() => writeInspectorPreference(storage, preference), [storage, preference]);
  const width = boundedInspectorWidth(preference.width, rowWidth);
  const {min, max} = inspectorWidthBounds(rowWidth);
  const setWidth = (value: number) => setPreference(previous => ({...previous, width: boundedInspectorWidth(value, rowWidth)}));
  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  const ko = language === 'ko';
  const toggleLabel = preference.collapsed ? (ko ? '속성 패널 펼치기' : 'Expand properties panel') : (ko ? '속성 패널 접기' : 'Collapse properties panel');
  return <aside ref={root} aria-label={ko ? '플로우 속성 패널' : 'Flow properties panel'}
    className="flex min-h-0 shrink-0 border-l border-[#2B3547] bg-[#131822]" style={{width: preference.collapsed ? 44 : width}}>
    {!preference.collapsed && <div role="separator" tabIndex={0} aria-label={ko ? '속성 패널 크기 조절' : 'Resize properties panel'}
      aria-orientation="vertical" aria-valuemin={min} aria-valuemax={max} aria-valuenow={width}
      className="w-2 shrink-0 cursor-col-resize bg-slate-800/40 hover:bg-sky-700/60 focus:bg-sky-700/60 focus:outline-none"
      style={{touchAction: 'none'}}
      onKeyDown={event => {
        const next = event.key === 'ArrowLeft' ? width + 20 : event.key === 'ArrowRight' ? width - 20
          : event.key === 'Home' ? min : event.key === 'End' ? max : null;
        if (next !== null) { event.preventDefault(); setWidth(next); }
      }}
      onPointerDown={event => {
        if (event.button !== 0 || drag.current) return;
        event.preventDefault(); drag.current = {id: event.pointerId, x: event.clientX, width};
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={event => { const start = drag.current; if (start?.id === event.pointerId) setWidth(start.width + start.x - event.clientX); }}
      onPointerUp={endDrag} onPointerCancel={endDrag}
      onLostPointerCapture={event => {if (drag.current?.id === event.pointerId) drag.current = null;}} />}
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-700 p-1.5">
        {!preference.collapsed && <span className="px-2 text-xs text-slate-400">{ko ? '속성 패널' : 'Properties'}</span>}
        <button type="button" aria-label={toggleLabel} title={toggleLabel} aria-expanded={!preference.collapsed}
          className="rounded p-1.5 text-slate-200 hover:bg-slate-700 focus:outline-sky-400"
          onClick={() => {drag.current = null; setPreference(previous => ({...previous, collapsed: !previous.collapsed}));}}>
          {preference.collapsed ? <ChevronLeft className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
      </div>
      {/* Keep fields mounted: collapsing must not discard an uncommitted input. */}
      <div data-flow-inspector-content={true} className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-5"
        style={{display: preference.collapsed ? 'none' : undefined}}>{children}</div>
    </div>
  </aside>;
}
