import {getApiPersistenceIdentity} from '../../services/api';
import React,{useEffect,useState} from 'react';
import {foundationLabelingApi as provider,type FoundationSetup,type FoundationProposal,type RegionExample} from '../../services/foundationLabelingApi';
import {dataWorkbenchScope} from '../../services/dataWorkbench';
import {workflowError} from '../../services/datasetWorkflow';
import {useComputeStore} from '../../stores/useComputeStore';
import {useProjectStore} from '../../stores/useProjectStore';
import {useAnnotationStore} from '../../stores/useAnnotationStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useModelAssistRunStore} from '../../stores/useModelAssistRunStore';
import {regionExample} from './foundationRequest';
const input='rounded border border-slate-600 bg-slate-900 p-2 text-slate-100';
export const KoreanConditionLabeler:React.FC<{disabled:boolean;onCreated:(proposal:FoundationProposal)=>void}>=({disabled,onCreated})=>{
 const project=useProjectStore();const compute=useComputeStore();const {currentImage,activeCategory,annotations,selectedAnnotationId,categories}=useAnnotationStore();const {images}=useDatasetStore();
 const scope=dataWorkbenchScope({...project,...compute,apiTransportIdentity:getApiPersistenceIdentity()});const [loaded,setLoaded]=useState<{scope:string;setup:FoundationSetup}|null>(null);const [prompt,setPrompt]=useState('');const [label,setLabel]=useState(activeCategory.name);
 const [endpoint,setEndpoint]=useState('');const [model,setModel]=useState('');const [env,setEnv]=useState('');const [enabled,setEnabled]=useState(false);
 const [positive,setPositive]=useState<RegionExample[]>([]);const [negative,setNegative]=useState<RegionExample[]>([]);const [examplePath,setExamplePath]=useState('');
 const [threshold,setThreshold]=useState(.5);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 const setup=loaded?.scope===scope?loaded.setup:null;const canConfigure=setup?.can_configure_vlm!==false;const same=()=>dataWorkbenchScope({...useProjectStore.getState(),...useComputeStore.getState(),apiTransportIdentity:getApiPersistenceIdentity()})===scope;
 useEffect(()=>{let current=true;setLoaded(null);setBusy(false);setError('');setPrompt('');setPositive([]);setNegative([]);setExamplePath('');setEndpoint('');setModel('');setEnv('');setEnabled(false);
  void provider.setup().then(next=>{if(current&&same()){setLoaded({scope,setup:next});const config=next.configuration.vlm;setEndpoint(config?.endpoint||'');setModel(config?.model||'');setEnv(config?.api_key_env||'');setEnabled(config?.enabled||false);}}).catch(cause=>{if(current&&same())setError(workflowError(cause));});return()=>{current=false;};
 },[scope]);
 useEffect(()=>setLabel(activeCategory.name),[activeCategory.name]);
 const run=async(action:()=>Promise<void>)=>{if(!same())return;setBusy(true);setError('');try{await action();}catch(cause){if(same())setError(workflowError(cause));}finally{if(same())setBusy(false);}};
 const configure=()=>run(async()=>{if(!canConfigure)return;const next=await provider.configure({vlm:{enabled,endpoint,model,api_key_env:env.trim()||null}});if(same())setLoaded({scope,setup:next});});
 const addExample=(kind:'positive'|'negative',selected:boolean)=>{
  const image=images.find(row=>row.file_path===examplePath);const example=selected&&currentImage?regionExample(currentImage.file_path,annotations.find(a=>a.id===selectedAnnotationId)):image&&image.width&&image.height?{image_path:image.file_path,roi:[0,0,image.width,image.height] as RegionExample['roi']}:null;
  if(!example){setError('이미지 또는 박스·다각형 라벨을 선택하세요.');return;}
  if(positive.length+negative.length>=20){setError('긍정·부정 예시는 총 20개까지 사용하세요.');return;}
  if(kind==='positive')setPositive(old=>[...old,example]);else setNegative(old=>[...old,example]);setError('');
 };
 const generate=()=>run(async()=>{
  if(!currentImage||!setup)return;const imagePath=currentImage.file_path;useModelAssistRunStore.getState().begin();
  try{const current=await provider.setup();if(!same()||useAnnotationStore.getState().currentImage?.file_path!==imagePath)return;setLoaded({scope,setup:current});
   const next=await provider.generate({backend:'vlm',image_path:imagePath,prompt,label,output_geometry:'bbox',threshold,positive_examples:positive,negative_examples:negative,labelset_id:current.labelset_id,labelset_version:current.labelset_version,class_ids:Object.fromEntries(categories.filter(c=>c.id>0&&c.id<=255).map(c=>[c.name,c.id]))});
   if(same()&&useAnnotationStore.getState().currentImage?.file_path===imagePath)onCreated(next);
  }finally{useModelAssistRunStore.getState().end();}
 });
 const supported=project.project?.task==='detection'||project.project?.task==='segmentation';
 return <section aria-label="한국어 조건 VLM 라벨링" className="space-y-3 rounded border border-cyan-800 bg-[#101722] p-3 text-xs">
  <h4 className="font-semibold text-cyan-100">한국어 조건 + 이미지 예시로 후보 찾기</h4><p className="text-slate-300">설정한 이미지 VLM에 검사 조건과 예시를 전송합니다. 결과는 아래 후보 검토에서 선택해 채택하며, 영역 정답으로 자동 승인하지 않습니다.</p>
  <details><summary className="cursor-pointer text-cyan-200">VLM 공급자 설정 · {setup?.providers.vlm?.ready?'연결 설정 준비됨':'설정 필요'}</summary><div className="mt-2 space-y-2">{!canConfigure&&<p className="text-amber-200">서버 관리자만 공급자 주소·키 참조를 설정할 수 있습니다. 저장된 공급자로 후보를 생성하세요.</p>}<label className="block">이미지 Chat Completions 주소<input aria-label="VLM 공급자 주소" disabled={!canConfigure} type="url" value={endpoint} onChange={e=>setEndpoint(e.target.value)} className={`mt-1 w-full ${input}`} placeholder="https://provider.example/v1/chat/completions"/></label><label className="block">이미지 모델 이름<input aria-label="VLM 모델 이름" disabled={!canConfigure} value={model} onChange={e=>setModel(e.target.value)} className={`mt-1 w-full ${input}`}/></label><label className="block">API 키 환경 변수 이름 (선택)<input aria-label="VLM 키 환경 변수 이름" disabled={!canConfigure} value={env} onChange={e=>setEnv(e.target.value)} className={`mt-1 w-full ${input}`} placeholder="MODU_VLM_API_KEY"/></label><p className="text-slate-400">키 값은 backend 환경에 설정합니다. 로컬 공급자는 HTTP를 사용할 수 있으며 외부 공급자는 HTTPS가 필요합니다.</p><label className="flex gap-2"><input type="checkbox" disabled={!canConfigure} checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>이 공급자에 대상 이미지와 선택 예시 전송 활성화</label><button type="button" disabled={busy||!canConfigure} onClick={()=>void configure()} className="rounded border border-cyan-700 px-3 py-2 disabled:opacity-40">VLM 설정 저장</button>{setup?.providers.vlm?.error&&<p className="text-amber-200">{setup.providers.vlm.error}</p>}</div></details>
  <label className="block">한국어 검사 조건<textarea aria-label="한국어 라벨 조건" rows={3} value={prompt} onChange={e=>setPrompt(e.target.value)} placeholder="긴 스크래치만 찾고 인쇄 무늬·먼지·그림자는 제외하세요." className={`mt-1 w-full ${input}`}/></label><label>후보 클래스<input aria-label="한국어 VLM 후보 클래스" value={label} onChange={e=>setLabel(e.target.value)} className={`ml-2 ${input}`}/></label>
  <details><summary className="cursor-pointer text-cyan-200">긍정·부정 이미지 예시 · {positive.length} / {negative.length}</summary><div className="mt-2 space-y-2"><select aria-label="한국어 조건 예시 이미지" value={examplePath} onChange={e=>setExamplePath(e.target.value)} className={`w-full ${input}`}><option value="">현재 데이터 이미지 선택</option>{images.map(image=><option key={image.file_path} value={image.file_path}>{image.file_name}</option>)}</select><div className="flex flex-wrap gap-2">{(['positive','negative'] as const).map(kind=><React.Fragment key={kind}><button type="button" disabled={busy} onClick={()=>addExample(kind,false)} className="rounded border border-slate-600 px-2 py-1">전체 이미지 → {kind==='positive'?'긍정':'부정'} 예시</button><button type="button" disabled={busy} onClick={()=>addExample(kind,true)} className="rounded border border-slate-600 px-2 py-1">{`선택 라벨 → ${kind==='positive'?'긍정':'부정'} 예시`}</button></React.Fragment>)}</div>{(['positive','negative'] as const).map(kind=><ul key={kind}>{(kind==='positive'?positive:negative).map((example,index)=><li className="flex items-center gap-2" key={index}><span>{kind==='positive'?'긍정':'부정'} · {example.image_path.split(/[\\/]/).pop()} · [{example.roi.join(', ')}]</span><button type="button" aria-label={`${kind} VLM 예시 ${index+1} 제거`} onClick={()=>kind==='positive'?setPositive(old=>old.filter((_,i)=>i!==index)):setNegative(old=>old.filter((_,i)=>i!==index))} className="ml-auto text-amber-200">제거</button></li>)}</ul>)}</div></details>
  <label>후보 점수<input aria-label="한국어 VLM 후보 점수" type="number" min="0" max="1" step=".01" value={threshold} onChange={e=>setThreshold(Number(e.target.value))} className={`ml-2 w-20 ${input}`}/></label><button type="button" disabled={disabled||busy||!supported||!currentImage||!setup?.providers.vlm?.ready||!prompt.trim()||!label.trim()} onClick={generate} className="w-full rounded border border-cyan-600 bg-cyan-900 px-3 py-2 font-semibold disabled:opacity-40">한국어 조건 후보 생성</button>
  {!supported&&<p className="text-amber-200">한국어 영역 후보는 객체 검출·영역 분할 라벨링에서 사용합니다.</p>}{busy&&<p role="status">설정한 VLM 공급자 요청 중…</p>}{error&&<p role="alert" className="text-rose-200">{error}</p>}
 </section>;
};
