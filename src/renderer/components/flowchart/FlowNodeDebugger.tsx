import {useEffect,useState} from 'react';
import type {FlowNode,FlowchartPipeline,FlowchartExecutionResult,FlowchartExecutionStep} from '../../types';
import {nodeEvidenceText,activeInputArtifacts,artifactPage,roiTrace} from './flowWorkspace';

type Artifact=NonNullable<FlowchartExecutionStep['artifacts']>[number];
function ArtifactBrowser({title,rows,onSelect}:{title:string;rows:Artifact[];onSelect:(id:string)=>void}) {
  const [query,setQuery]=useState('');const [page,setPage]=useState(0);
  const view=artifactPage(rows,query,page);
  return <section className="space-y-2">
    <div className="flex items-center justify-between"><h4 className="font-semibold text-slate-200">{title} · {rows.length}개</h4>
      <span className="text-slate-400">검색 결과 {view.total}개</span></div>
    <input aria-label={`${title} ROI 검색`} value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}
      placeholder="ROI ID · 클래스 · 판정 검색" className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5"/>
    {!view.total&&<p className="text-slate-400">표시할 영역이 없습니다.</p>}
    <div className="grid grid-cols-2 gap-2">{view.rows.map((r,i)=><figure key={`${r.roi_id}:${i}`} className="rounded border border-slate-700 p-1.5">
      <button className="w-full text-left" onClick={()=>onSelect(r.roi_id)} title="이 ROI의 실행 경로 보기">
        <img src={r.image} alt={`${title} ${r.roi_id}`} className="max-h-36 w-full object-contain"/>
        {r.mask&&<img src={r.mask} alt={`${r.roi_id} 검사 마스크`} className="max-h-36 w-full object-contain"/>}
        <figcaption className="mt-1 break-all text-sky-200">{r.roi_id}<span className="block text-slate-400">영역 [{r.bbox.join(', ')}] px</span></figcaption>
      </button><details className="mt-1"><summary className="cursor-pointer text-slate-400">측정·판정 값</summary>
        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all text-[11px]">{JSON.stringify(r.evidence||{},null,2)}</pre></details>
    </figure>)}</div>
    {view.pages>1&&<div className="flex items-center justify-between"><button disabled={view.page===0} onClick={()=>setPage(view.page-1)} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40">이전</button>
      <span>{view.page+1} / {view.pages}</span><button disabled={view.page+1===view.pages} onClick={()=>setPage(view.page+1)} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40">다음</button></div>}
  </section>;
}

export function FlowNodeDebugger({node,pipeline,result}:{node:FlowNode;pipeline:FlowchartPipeline;result:FlowchartExecutionResult|null}) {
  const [roi,setRoi]=useState('');
  useEffect(()=>setRoi(''),[node.id,result]);
  const step=result?.execution_steps.find(s=>s.node_id===node.id);
  if(!step)return <section className="rounded border border-slate-700 p-3 text-xs text-slate-400">실행 후 이 노드의 입력·출력과 판정 근거가 표시됩니다. 그래프나 이미지를 수정하면 이전 근거는 지워집니다.</section>;
  const inputs=result?activeInputArtifacts(node.id,pipeline,result):[];
  const trace=result&&roi?roiTrace(roi,pipeline,result):[];
  return <section aria-label="선택 노드 실행 근거" className="rounded border border-slate-700 bg-slate-950/30 p-3 space-y-3 text-xs">
    <h4 className="font-bold text-sky-200">실행 근거 · {node.data.label}</h4>
    <p className="text-slate-400">{result?.execution_target||'local'} · {result?.execution_device||'—'} · 최종 {result?.final_verdict}</p>
    {nodeEvidenceText(node,step).map((line,i)=><p key={i}>{line}</p>)}
    {node.data.node_type==='decision'&&<p className="text-amber-200">{result?.rejection_reason}</p>}
    <ArtifactBrowser key={`in:${node.id}`} title="입력" rows={inputs} onSelect={setRoi}/>
    <ArtifactBrowser key={`out:${node.id}`} title="출력" rows={step.artifacts||[]} onSelect={setRoi}/>
    {roi&&<section className="rounded border border-sky-900 p-2 space-y-1"><h5 className="font-semibold text-sky-200">ROI 경로 · {roi}</h5>
      <p className="text-slate-400">이번 실행에서 관측된 동일·파생 ROI ID와 선택 연결을 표시합니다.</p>
      {trace.map(row=><div key={row.node_id} className="border-l-2 border-sky-700 pl-2"><p>{row.name} · {row.status} · {row.input_count} → {row.output_count}</p>
        {!!row.next.length&&<p className="text-slate-400">연결: {row.next.join(', ')}</p>}</div>)}
      {!trace.length&&<p className="text-slate-400">이 실행에서 해당 영역의 기록이 없습니다.</p>}</section>}
  </section>;
}
