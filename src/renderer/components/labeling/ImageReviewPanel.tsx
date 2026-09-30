import React, { useEffect, useState } from 'react';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { datasetWorkflow, workflowError, type ReviewState } from '../../services/datasetWorkflow';
const states: Record<ReviewState,string> = { unworked: '미작업', needs_review: '검토 필요', approved: '승인됨' };
export const ImageReviewPanel: React.FC = () => {
  const { metadata, currentImage, isDirty, isSaving, reviewerName, setReviewerName, setMetadata, loadAnnotationsForCurrent } = useAnnotationStore();
  const projectDir = useProjectStore(s => s.projectDir);
  const [open,setOpen] = useState(false); const [busy,setBusy] = useState(false); const [error,setError] = useState('');
  const [product,setProduct] = useState(''); const [lot,setLot] = useState(''); const [group,setGroup] = useState(''); const [tags,setTags] = useState('');
  useEffect(() => { setProduct(metadata?.product || '');setLot(metadata?.lot || '');setGroup(metadata?.group || '');setTags(metadata?.tags.join(', ') || '');setError(''); },[metadata?.revision,currentImage?.file_path,projectDir]);
  const save = async (state?: ReviewState) => {
    if (!metadata || !reviewerName.trim()) { setError('작업자·검토자 이름을 입력하세요.');return; }
    const path = currentImage?.file_path; setBusy(true);setError('');
    try {
      const next = await datasetWorkflow.edit(metadata,reviewerName,{product,lot,group,tags:tags.split(',').map(t=>t.trim()).filter(Boolean),...(state ? {workflow_state:state} : {})});
      if (useProjectStore.getState().projectDir === projectDir && useAnnotationStore.getState().currentImage?.file_path === path) setMetadata(next);
    } catch (cause) { setError(workflowError(cause)); } finally {setBusy(false);}
  };
  return <div className="relative z-[65] shrink-0 border-b border-slate-700 bg-[#101722] text-xs">
    <div className="flex items-center gap-3 px-4 py-2"><button type="button" onClick={()=>setOpen(!open)} aria-expanded={open} className="rounded border border-slate-600 px-2 py-1 font-semibold text-cyan-200">이미지 정보·검토</button>
      <span className={metadata?.workflow_state==='approved'?'text-emerald-300':'text-amber-300'}>{metadata ? states[metadata.workflow_state] : '이미지 선택'}</span>
      {metadata?.reviewer && <span>검토자 {metadata.reviewer}</span>}<span className="truncate text-slate-500">{metadata?.product} {metadata?.lot} {metadata?.group}</span>
      {isDirty && <span className="text-amber-300">라벨 수정 중</span>}
    </div>
    {open && <section aria-label="이미지 정보와 검토 기록" className="absolute left-3 top-full z-50 mt-1 max-h-[60vh] w-[560px] max-w-[95vw] space-y-3 overflow-y-auto rounded border border-slate-600 bg-[#151D2A] p-4 shadow-2xl">
      {!metadata && <p>이미지를 선택하면 출처와 검토 기록을 표시합니다.</p>}
      {metadata && <><div className="grid grid-cols-2 gap-2">
        <label>작업자·검토자<input aria-label="작업자·검토자 이름" value={reviewerName} onChange={e=>setReviewerName(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2" /></label>
        <label>자유 태그 (쉼표 구분)<input value={tags} onChange={e=>setTags(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2" /></label>
        <label>제품<input value={product} onChange={e=>setProduct(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2" /></label>
        <label>로트<input value={lot} onChange={e=>setLot(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2" /></label>
        <label>그룹<input value={group} onChange={e=>setGroup(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-900 p-2" /></label>
      </div><div className="flex flex-wrap gap-2">
        <button disabled={busy || isSaving} onClick={()=>void save()} className="rounded border border-cyan-700 px-3 py-2 disabled:opacity-40">정보 저장</button>
        <button disabled={busy || isDirty || isSaving} onClick={()=>void save('needs_review')} className="rounded border border-amber-700 px-3 py-2 disabled:opacity-40">검토 요청</button>
        <button disabled={busy || isDirty || isSaving} onClick={()=>void save('approved')} className="rounded bg-emerald-700 px-3 py-2 disabled:opacity-40">라벨 승인</button>
        <button disabled={busy || isSaving} onClick={()=>{ if(isDirty) useAnnotationStore.setState({isDirty:false}); void loadAnnotationsForCurrent(); }} className="rounded border border-slate-600 px-3 py-2 disabled:opacity-40">{isDirty ? "현재 편집을 버리고 최신 라벨 불러오기" : "최신 라벨 불러오기"}</button>
      </div><p className="text-slate-400">승인 후 라벨·원본 이미지가 변경되면 검토가 다시 필요합니다. 다른 작업자의 변경이 있으면 저장을 중지합니다.</p>
      <details><summary className="cursor-pointer text-slate-300">출처·변경 기록 ({metadata.audit.length})</summary>
        <dl className="mt-2 space-y-1 break-all text-[10px] text-slate-400"><dt>이미지 UUID</dt><dd>{metadata.image_uuid}</dd><dt>원본 경로</dt><dd>{metadata.file_path}</dd><dt>내용 해시 · 버전 {metadata.content_version} · 수정 {metadata.revision}</dt><dd>{metadata.content_hash}</dd></dl>
        <ul className="mt-2 space-y-1">{metadata.audit.slice().reverse().map(item=><li key={item.id} className="rounded bg-slate-900 p-2 text-[10px]">{new Date(item.at).toLocaleString('ko-KR')} · {item.actor} · {({registered:'이미지 등록',review:'검토 상태 변경',metadata_edited:'이미지 정보 수정',annotation_changed:'라벨 수정',external_annotation_changed:'외부 라벨 변경',source_changed:'원본 변경',version_restored:'버전 복원'} as Record<string,string>)[item.action] || item.action} · 수정 {item.revision}</li>)}</ul>
      </details></>}
      {error && <p role="alert" className="rounded border border-red-700 p-2 text-red-200">{error}</p>}
    </section>}
  </div>;
};
