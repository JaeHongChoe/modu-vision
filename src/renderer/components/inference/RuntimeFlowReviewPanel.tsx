import {useEffect,useRef,useState} from 'react';
import {getProjectContextGeneration,subscribeProjectContext} from '../../services/api';
import {wholeFlowApproval,type RuntimeFlowPreview} from '../../services/wholeFlowApproval';

export function RuntimeFlowReviewPanel({packagePath}:{packagePath:string}) {
  const [authority,setAuthority]=useState(getProjectContextGeneration());
  const [preview,setPreview]=useState<RuntimeFlowPreview|null>(null),[error,setError]=useState('');
  const [reviewer,setReviewer]=useState(''),[reason,setReason]=useState(''),[reviewed,setReviewed]=useState(false);
  const [busy,setBusy]=useState(false),[notice,setNotice]=useState('');
  const generation=useRef(0),currentPath=useRef(packagePath);currentPath.current=packagePath;
  useEffect(()=>subscribeProjectContext(()=>{generation.current++;setPreview(null);setReviewed(false);
    setReviewer('');setReason('');setNotice('');setError('');setBusy(false);setAuthority(getProjectContextGeneration());}),[]);
  const read=async()=>{
    const active=await wholeFlowApproval.active();
    if(!active?.validity.valid)throw new Error('최신 정답과 공정 기준으로 전체 흐름 검토를 먼저 저장하세요.');
    const value=await wholeFlowApproval.runtimePreview(active.revision_id,packagePath);
    if(value.base_revision_id!==active.revision_id||value.device_accepted!==false)
      throw new Error('변환 패키지와 전체 흐름 검토의 연결을 다시 확인하세요.');
    return value;
  };
  useEffect(()=>{
    const epoch=++generation.current,started=authority;setPreview(null);setReviewed(false);setBusy(true);setError('');setNotice('');
    read().then(value=>{if(generation.current===epoch&&getProjectContextGeneration()===started)setPreview(value);})
      .catch(cause=>{if(generation.current===epoch&&getProjectContextGeneration()===started)setError(String(cause));})
      .finally(()=>{if(generation.current===epoch&&getProjectContextGeneration()===started)setBusy(false);});
    return()=>{generation.current++;};
  },[packagePath,authority]);
  const submit=async()=>{
    const epoch=generation.current,started=authority;
    if(!preview||!reviewed||busy||currentPath.current!==packagePath||getProjectContextGeneration()!==started)return;
    setBusy(true);setError('');setNotice('');
    try{
      const fresh=await read();
      if(generation.current!==epoch||currentPath.current!==packagePath||getProjectContextGeneration()!==started)return;
      if(JSON.stringify(fresh)!==JSON.stringify(preview))throw new Error('검토 근거가 변경되었습니다. 패키지를 다시 열어 최신 결과를 확인하세요.');
      const saved=await wholeFlowApproval.reviewRuntime(preview.base_revision_id,{package_path:packagePath,
        device:'openvino:CPU',reviewer,reason,holdout_reviewed:true,expected_revision:preview.review_revision_id});
      if(generation.current===epoch&&getProjectContextGeneration()===started){
        setReviewed(false);setNotice('변환 후 전체 흐름 검토를 저장했습니다. 실행 서비스 적용과 장비 검증은 별도로 진행하세요.');
        setPreview({...preview,review_revision_id:saved.revision_id,review_valid:true});
      }
    }catch(cause){if(generation.current===epoch&&getProjectContextGeneration()===started){setError(String(cause));setReviewed(false);}}
    finally{if(generation.current===epoch&&getProjectContextGeneration()===started)setBusy(false);}
  };
  return <fieldset aria-label="변환 후 전체 흐름 검토" className="grid gap-2 rounded border border-cyan-800 p-3">
    <legend>변환 후 전체 흐름 검토</legend>
    <p>위의 원본·변환 결과를 확인하고 같은 시험 정답과 공정 기준으로 다시 검토합니다.</p>
    {preview&&<><p>정상 {preview.metrics.normal_count} · 불량 {preview.metrics.defect_count} · 미검 {(100*preview.metrics.escape_rate).toFixed(2)}% · 과검 {(100*preview.metrics.overkill_rate).toFixed(2)}% · 검토 {(100*preview.metrics.review_rate).toFixed(2)}%</p>
      <p>공정 기준 {preview.policy.policy_id} · revision {preview.policy.revision}: 미검 ≤ {(100*preview.policy.maximum_escape_rate).toFixed(2)}% · 과검 ≤ {(100*preview.policy.maximum_overkill_rate).toFixed(2)}% · 검토 ≤ {(100*preview.policy.maximum_review_rate).toFixed(2)}%</p>
      <table><thead><tr><th>시험 이미지</th><th>검토 정답</th><th>변환 후 판정</th></tr></thead><tbody>{preview.outputs.map(row=><tr key={row.image_sha256}><td title={row.image_sha256}>{row.relative_path}</td><td>{row.truth}</td><td>{row.decision}</td></tr>)}</tbody></table>
      {preview.review_valid&&<p className="text-emerald-200">저장된 변환 후 검토가 현재 근거와 일치합니다.</p>}</>}
    <label>검토자<input aria-label="변환 전체 흐름 검토자" value={reviewer} onChange={e=>setReviewer(e.target.value)} className="bg-slate-900"/></label>
    <label>검토 이유<textarea aria-label="변환 전체 흐름 검토 이유" value={reason} onChange={e=>setReason(e.target.value)} className="w-full bg-slate-900"/></label>
    <label><input aria-label="변환 전체 흐름 직접 검토" type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/>시험 정답과 변환 후 전체 흐름 결과를 직접 검토했습니다.</label>
    <button type="button" disabled={!preview||!reviewed||busy||!reviewer.trim()||reason.trim().length<8} onClick={submit} className="rounded border border-cyan-700 p-2 disabled:opacity-50">변환 전체 흐름 검토 저장</button>
    {busy&&<p role="status">검토 근거 확인 중…</p>}{notice&&<p role="status" className="text-emerald-200">{notice}</p>}{error&&<p role="alert" className="text-rose-300">{error}</p>}
  </fieldset>;
}
