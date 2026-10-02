export type FlowWorkspaceTab='edit'|'test'|'evaluate'|'release';
export interface FlowWorkspaceIdentity {
  revision:number; draft:string; selectedVersion?:string; activeVersion?:string;
  models:string[]; resultLabel?:string; lastImage?:string; nextImage?:string; inspection?:string; evaluation?:string; approval:string;
}
export interface FlowIdentityInput {
  revision:number; draft:string; dirty:boolean; isDraft:boolean;
  savedVersions:Array<{version_id:string; is_active?:boolean; pipeline_hash?:string}>; selectedVersionId:string;
  modelNodes:Array<{id:string; job:string; task:string}>;
  resultLabel?:string; inspection?:string; lastImage?:string; nextImage?:string;
  evaluation?:{version_id:string; cohort_id:string; graph_sha256?:string|null; validity:{valid:boolean}}|null;
  /** Only an approval readback already matched to this scope, version and semantic graph. */
  approval?:{status:'ready'|'selection_required'|'blocked'; models:Array<{job_id:string; checkpoint_sha256:string}>}|null;
}
export const APPROVAL_LABELS = {none:'미조회', blocked:'근거 차단', selection_required:'승인 revision 선택 필요', ready:'조회 시점의 revision 검증됨'} as const;

/** The persistent identities, derived from receipts only: an evaluation counts for the selected saved version only
 *  when its version and graph hash match and it is valid; an approval label never follows from an evaluation. */
export function flowWorkspaceIdentity(input:FlowIdentityInput):FlowWorkspaceIdentity&{exactSaved:boolean;evaluationCurrent:boolean} {
  const selected=input.savedVersions.find(row=>row.version_id===input.selectedVersionId);
  const exactSaved=Boolean(selected&&!input.dirty&&!input.isDraft);
  const evaluation=input.evaluation;
  const evaluationCurrent=Boolean(exactSaved&&evaluation&&evaluation.version_id===input.selectedVersionId&&selected?.pipeline_hash
    &&evaluation.graph_sha256===selected.pipeline_hash&&evaluation.validity.valid);
  const hashes=new Map((input.approval?.models||[]).map(model=>[model.job_id,model.checkpoint_sha256]));
  return {
    revision:input.revision, draft:input.draft, selectedVersion:input.selectedVersionId||undefined,
    activeVersion:input.savedVersions.find(row=>row.is_active)?.version_id,
    models:input.modelNodes.map(node=>`${node.id}: ${node.job} (${node.task})${hashes.get(node.job)?` · sha256 ${hashes.get(node.job)}`:''}`),
    resultLabel:input.resultLabel, inspection:input.inspection, lastImage:input.lastImage, nextImage:input.nextImage,
    evaluation:evaluation?`${evaluationCurrent?'현재 저장 버전 근거 유효':'이전·재확인 필요'} · ${evaluation.version_id} · ${evaluation.cohort_id}`:undefined,
    approval:APPROVAL_LABELS[input.approval?.status||'none'],
    exactSaved, evaluationCurrent,
  };
}

const areas:Array<[FlowWorkspaceTab,string]>=[['edit','편집'],['test','테스트'],['evaluate','일괄 평가'],['release','배포']];
const item='min-w-0 truncate';
/** Presentation only: receipts remain owned by the existing execution/evaluation paths. Two lines; full IDs expand. */
export function FlowEditorWorkspace({area,onAreaChange,identity}:{area:FlowWorkspaceTab;onAreaChange:(value:FlowWorkspaceTab)=>void;identity:FlowWorkspaceIdentity}) {
  return <header className="sticky top-0 z-30 shrink-0 border-b border-slate-700 bg-[#111923] px-4 py-2">
    <div role="tablist" aria-label="플로우 작업 영역" className="flex gap-1">{areas.map(([key,label],index)=><button key={key} id={`flow-area-${key}`} role="tab" aria-controls={`flow-panel-${key}`} aria-selected={area===key} tabIndex={area===key?0:-1} onClick={()=>onAreaChange(key)} onKeyDown={event=>{
      const next=event.key==='ArrowRight'?(index+1)%areas.length:event.key==='ArrowLeft'?(index+areas.length-1)%areas.length:event.key==='Home'?0:event.key==='End'?areas.length-1:null;
      if(next===null)return;event.preventDefault();onAreaChange(areas[next][0]);event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
    }} className={`rounded px-4 py-2 text-sm font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-300 ${area===key?'bg-cyan-900 text-cyan-100':'text-slate-400 hover:bg-slate-800'}`}>{label}</button>)}</div>
    <section aria-label="플로우 식별 정보" className="mt-1 text-xs text-slate-300">
      <p className="flex gap-x-4 overflow-hidden whitespace-nowrap"><span className={item}>편집 r{identity.revision} · {identity.draft}</span><span className={item}>저장 {identity.selectedVersion||'미선택'}</span><span className={item}>활성 {identity.activeVersion||'없음'}</span><span className={item}>모델 {identity.models.length}개</span><button type="button" onClick={()=>onAreaChange('test')} title={identity.inspection} className={`${item} text-left text-cyan-200 hover:underline`}>{identity.resultLabel||'검사 결과'}</button></p>
      <p className="flex gap-x-4 overflow-hidden whitespace-nowrap text-slate-400"><span className={item} title={identity.evaluation}>평가 {identity.evaluation?.split(' · ')[0]||'근거 미조회'}</span><span className={item}>승인 {identity.approval}</span><span className={item}>배포 · 대상 적용 응답 확인 필요</span><span className={item} title={identity.nextImage}>다음 이미지 {identity.nextImage||'미선택'}</span></p>
      <details><summary className="cursor-pointer text-cyan-300">모델·이미지·실행 식별 정보</summary><dl className="mt-2 grid gap-1 break-all text-xs">
        <div><dt className="inline text-slate-400">모델: </dt><dd className="inline">{identity.models.join(' / ')||'미연결'}</dd></div>
        <div><dt className="inline text-slate-400">평가: </dt><dd className="inline">{identity.evaluation||'근거 미조회'}</dd></div>
        <div><dt className="inline text-slate-400">마지막 검사: </dt><dd className="inline">{identity.inspection||'없음'}</dd></div>
        <div><dt className="inline text-slate-400">마지막 이미지: </dt><dd className="inline">{identity.lastImage||'없음'}</dd></div>
        <div><dt className="inline text-slate-400">다음 이미지: </dt><dd className="inline">{identity.nextImage||'미선택'}</dd></div>
      </dl></details>
    </section>
  </header>;
}
