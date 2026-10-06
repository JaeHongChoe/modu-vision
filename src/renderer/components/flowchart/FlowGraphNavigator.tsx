import {useEffect, useState, type RefObject} from 'react';
import type {FlowNode} from '../../types';
import {
  computeFlowchartMinimap, findFlowNodes, flowNodeScrollTarget, minimapScrollTarget,
  FLOW_NODE_HEIGHT, FLOW_NODE_WIDTH, type FlowchartViewport,
} from './flowchartViewport';

type Node = FlowNode & {position: {x: number; y: number}};
type Props = {
  nodes: readonly Node[];
  viewport: FlowchartViewport;
  canvasRef: RefObject<HTMLDivElement>;
  selectedNodeId: string | null;
  onSelect: (id: string) => void;
  disabled: boolean;
};

/** Navigation changes selection and scroll only. Graph edits remain in the editor. */
export function FlowGraphNavigator({nodes, viewport, canvasRef, selectedNodeId, onSelect, disabled}: Props) {
  const [query, setQuery] = useState('');
  const [view, setView] = useState({width: 1, height: 1, left: 0, top: 0});
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const measure = () => setView(previous => {
      const next = {width: canvas.clientWidth, height: canvas.clientHeight, left: canvas.scrollLeft, top: canvas.scrollTop};
      return Object.keys(next).every(key => next[key as keyof typeof next] === previous[key as keyof typeof previous]) ? previous : next;
    });
    measure();
    canvas.addEventListener('scroll', measure, {passive: true});
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    return () => {canvas.removeEventListener('scroll', measure); observer.disconnect();};
  }, [canvasRef]);
  const matches = query.trim() ? findFlowNodes(nodes, query) : [];
  const map = computeFlowchartMinimap(viewport, view);
  const jump = (node: Node) => {
    const canvas = canvasRef.current;
    if (disabled || !canvas) return;
    onSelect(node.id);
    canvas.scrollTo({...flowNodeScrollTarget(node, viewport, {width: canvas.clientWidth, height: canvas.clientHeight}), behavior: 'auto'});
  };
  const pan = (point: {x: number; y: number}) => {
    const canvas = canvasRef.current;
    if (disabled || !canvas) return;
    canvas.scrollTo({...minimapScrollTarget(point, map, viewport, {width: canvas.clientWidth, height: canvas.clientHeight}), behavior: 'auto'});
  };
  return <div aria-label="플로우 탐색" className="flex shrink-0 items-center gap-3 border-b border-slate-700 bg-[#101722] px-3 py-2">
    <div className="min-w-0 flex-1 text-xs">
      <label className="block max-w-lg text-slate-300">
        노드 찾기
        <input type="search" aria-label="플로우 노드 검색" value={query} disabled={disabled || !nodes.length}
          onChange={event => setQuery(event.target.value)} placeholder="이름 · 노드 종류 · ID · 모델 ID"
          className="mt-1 w-full rounded border border-slate-600 bg-[#1A212E] px-2 py-1.5 text-slate-100 outline-none focus:border-sky-400" />
      </label>
      {query.trim() ? <>
        <p role="status" className="mt-1 text-slate-400">{matches.length ? `${matches.length}개 노드 · 선택하면 해당 위치로 이동` : '일치하는 노드가 없습니다.'}</p>
        <ul aria-label="노드 검색 결과" className="mt-1 max-h-20 overflow-auto">
          {matches.map(node => <li key={node.id}>
            <button type="button" disabled={disabled} onClick={() => jump(node)} aria-pressed={selectedNodeId === node.id}
              className="w-full rounded px-2 py-1 text-left text-sky-200 hover:bg-slate-800 focus-visible:outline focus-visible:outline-sky-400">
              {node.data.label} <span className="text-slate-400">· {node.id} · {node.data.node_type}</span>
            </button>
          </li>)}
        </ul>
      </> : <p className="mt-1 text-slate-400">{nodes.length}개 노드 · 미니맵에서 위치를 선택하거나 방향키로 이동하세요.</p>}
    </div>
    <svg viewBox={`0 0 ${map.width} ${map.height}`} width={map.width} height={map.height} role="group"
      aria-label="플로우 미니맵" aria-disabled={disabled} tabIndex={disabled ? -1 : 0}
      className="shrink-0 rounded border border-slate-600 bg-[#0B0E14] focus-visible:outline focus-visible:outline-sky-400"
      onClick={event => {const rect = event.currentTarget.getBoundingClientRect(); pan({x: (event.clientX - rect.left) * map.width / rect.width, y: (event.clientY - rect.top) * map.height / rect.height});}}
      onKeyDown={event => {
        if (disabled || event.target !== event.currentTarget) return;
        const shifts: Record<string, {left: number; top: number}> = {
          ArrowLeft: {left: -view.width * .65, top: 0}, ArrowRight: {left: view.width * .65, top: 0},
          ArrowUp: {left: 0, top: -view.height * .65}, ArrowDown: {left: 0, top: view.height * .65},
        };
        if (shifts[event.key]) {event.preventDefault(); canvasRef.current?.scrollBy({...shifts[event.key], behavior: 'auto'});}
      }}>
      {nodes.map(node => <rect key={node.id} role="button" tabIndex={disabled ? -1 : 0} aria-disabled={disabled}
        aria-label={`미니맵에서 ${node.data.label} 보기`} aria-pressed={selectedNodeId === node.id}
        x={map.offsetX + (viewport.offsetX + node.position.x * viewport.scale) * map.scale}
        y={map.offsetY + (viewport.offsetY + node.position.y * viewport.scale) * map.scale}
        width={FLOW_NODE_WIDTH * viewport.scale * map.scale} height={FLOW_NODE_HEIGHT * viewport.scale * map.scale}
        fill={selectedNodeId === node.id ? '#38bdf8' : '#64748b'} stroke="#0f172a" strokeWidth=".5"
        onClick={event => {event.stopPropagation(); jump(node);}}
        onKeyDown={event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); event.stopPropagation(); jump(node);}}}>
        <title>{node.data.label} · {node.id}</title>
      </rect>)}
      <rect x={map.visible.x} y={map.visible.y} width={map.visible.width} height={map.visible.height}
        fill="none" stroke="#e2e8f0" strokeWidth="1" pointerEvents="none" />
    </svg>
  </div>;
}
