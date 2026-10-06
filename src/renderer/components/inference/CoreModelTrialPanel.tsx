import {useEffect,useRef,useState} from 'react';
import {executeModelRecipe,getExecutionContextIdentity} from '../../services/modelExecution';
import {useProjectStore} from '../../stores/useProjectStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {ProjectImagePicker} from '../training/ProjectImagePicker';
import {ModelExecutionEvidence} from '../training/ModelExecutionEvidence';
import type {VisionTask} from '../../types';
type Trial={overlay_base64:string;source_sha256:string;confidence_score:number;predictions:unknown;execution:{device:string;compute_profile_name:string|null;evidence_sha256:string}};
export function CoreModelTrialPanel({task,jobId,device,setDevice}:{task:VisionTask;jobId:string;device:'cpu'|'mps'|'cuda';setDevice:(value:'cpu'|'mps'|'cuda')=>void}){
 useProjectStore();const compute=useComputeStore();const scope=[getExecutionContextIdentity(),compute.selectedProfileId,task,jobId].join('\n');const current=useRef(scope);current.current=scope;
 const [image,setImage]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[record,setRecord]=useState<{scope:string;result:Trial}|null>(null);
 useEffect(()=>{setImage('');setError('');setBusy(false);setRecord(null);if(compute.selectedProfileId&&device==='mps')setDevice('cpu');},[scope]);
 async function predict(){const started=scope;setBusy(true);setError('');try{const result=await executeModelRecipe<Trial>(task,'predict',{job_id:jobId,image_path:image,device});if(current.current===started)setRecord({scope:started,result});}catch(e){if(current.current===started)setError(e instanceof Error?e.message:String(e));}finally{if(current.current===started)setBusy(false);}}
 const result=record?.scope===scope?record.result:null,profile=compute.profiles.find(row=>row.id===compute.selectedProfileId);
 return <section aria-label="일반 모델 단일 실행" className="rounded border border-slate-700 p-4 space-y-3">
  <h3>단일 모델 시험 실행 · {task}</h3><p className="text-sm text-slate-400">실행 위치 {compute.selectedProfileId?profile?.name||'선택 서버':'이 컴퓨터'} · 저장한 검사 플로우와 생산 품질 판정은 별도로 확인하세요.</p>
  <label>단일 모델 실행 장치<select aria-label="단일 모델 실행 장치" value={device} onChange={e=>setDevice(e.target.value as typeof device)} className="mx-3 rounded border border-slate-600 bg-slate-900 p-2"><option value="cpu">CPU</option>{!compute.selectedProfileId&&<option value="mps">Metal MPS</option>}<option value="cuda">CUDA</option></select></label>
  <ProjectImagePicker label="단일 모델 원본 이미지" value={image} disabled={busy} onSelect={row=>setImage(row.file_path)} />
  <button type="button" disabled={!jobId||!image||busy} onClick={()=>void predict()} className="rounded border border-slate-600 p-2 disabled:opacity-40">단일 모델 예측 실행</button>
  {error&&<p role="alert" className="text-rose-300">{error}</p>}
  {result&&<div><p>실제 실행 {result.execution.compute_profile_name||'이 컴퓨터'} · {result.execution.device} · 점수 {result.confidence_score.toFixed(4)}</p><img alt="단일 모델 예측 미리 보기" src={result.overlay_base64} className="max-h-96 max-w-full"/><details><summary>구조화 결과 미리 보기 (최대 8,000자)</summary><pre className="overflow-auto">{JSON.stringify(result.predictions,null,2).slice(0,8000)}</pre></details><p className="break-all">원본 SHA256 {result.source_sha256}</p></div>}
  <ModelExecutionEvidence task={task} jobId={jobId}/>
 </section>;
}
