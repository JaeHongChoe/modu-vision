import {useRef,useState} from 'react';
import {productDeliveryApi,type MigrationPreview} from '../../services/productDeliveryApi';
const button='rounded border border-slate-600 px-3 py-2 disabled:opacity-40';
export function ProjectCompatibilityPanel(){
 const [directory,setDirectory]=useState(''),[preview,setPreview]=useState<MigrationPreview|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');const generation=useRef(0);
 const action=async(operation:(started:number)=>Promise<void>)=>{const started=++generation.current;setBusy(true);setError('');try{await operation(started);}catch(cause){if(generation.current===started)setError(String((cause as Error).message||cause));}finally{if(generation.current===started)setBusy(false);}};
 return <fieldset className="space-y-3 rounded border border-slate-700 p-3"><legend>다른 프로젝트 열기 전 형식 검사</legend><p className="text-slate-400">미래 버전은 파일을 바꾸지 않고 차단합니다. 구형 매니페스트 변환은 원본 백업과 복구 기록을 먼저 남깁니다.</p>
 <button type="button" className={button} disabled={busy} onClick={()=>void action(async started=>{const selected=await window.api.selectFolder({title:'project.json이 있는 프로젝트'});if(!selected||generation.current!==started)return;setDirectory(selected);setPreview(null);const result=await productDeliveryApi.previewMigration(selected);if(generation.current===started)setPreview(result);})}>프로젝트 선택·검사</button>
 {preview&&<><p>형식 {preview.schema_version??'구형 · schema 없음'} → 지원 형식 {preview.current_schema_version} · {preview.migration_required?'백업 후 변환 필요':'현재 형식 확인됨'}</p><button type="button" className={button} disabled={busy||!preview.migration_required} onClick={()=>void action(async started=>{const result=await productDeliveryApi.applyMigration(directory,preview.manifest_sha256);if(generation.current===started)setPreview(result);})}>원본 백업·형식 변환</button>{preview.backup_relative_path&&<p role="status">변환 완료 · 원본 매니페스트 백업 보관됨</p>}<details className="break-all text-xs text-slate-400"><summary>프로젝트·변환 기록</summary><p>{directory}</p><p>{preview.manifest_sha256}</p><p>{preview.backup_relative_path}</p></details></>}
 {busy&&<p role="status">프로젝트 형식 확인 중…</p>}{error&&<p role="alert" className="break-all text-red-300">{error}</p>}
 </fieldset>;
}
