import {useEffect,useState} from 'react';
import {api,request,resolveApiUrl,getApiPersistenceIdentity,getProjectContextGeneration,type FlowModelCatalogItem,type SavedFlowVersion} from '../../services/api';
import {useProjectStore} from '../../stores/useProjectStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useFlowchartStore} from '../../stores/useFlowchartStore';
import type {FlowchartPipeline,ImageMeta,FlowchartExecutionResult} from '../../types';
import {insertSubgraph,flowTestSetStorageKey,debugRunCacheText} from './flowWorkspace';
import {compatibleModelNode,modelScoreBinding} from './modelFlowHandoff';
import {useComputeStore} from '../../stores/useComputeStore';
import {ImageLibraryBrowser} from '../common/ImageLibraryBrowser';
import {inspectionPreview,inspectionThumbnail} from '../inference/inspectionImage';
import {applyResolution,legacyStorageKey,offeredLegacyPaths,parseSavedTestSet,pickTestImage,preserveLegacyPaths,testImageFrom,toggleTestImage,unresolvedReason,TEST_SET_LIMIT,type TestImage,type UnresolvedTestImage} from './flowTestSet';
interface Template {template_id:string;name:string;kind:'flow'|'subgraph';pipeline:FlowchartPipeline;models:Array<{node_id:string;name:string;task:string}>;classes:{names:string[];ids:number[]};classes_by_node?:Record<string,{names:string[];ids:number[]}>;ports:{inputs:Array<{node_id:string;payload_type:string}>;outputs:Array<{node_id:string;payload_type:string}>}}
interface Comparison {comparison_id:string;name:string;created_at:string;status:string;schema_version?:number;integrity?:string;record_sha256?:string;quality_approved?:boolean;comparison_scope?:'whole_flow'|'debug_partial';stop_node_id?:string|null;cohort_sha256?:string;model_bindings?:{a:Array<{node_id:string;job_id:string;task:string;checkpoint_sha256:string}>;b:Array<{node_id:string;job_id:string;task:string;checkpoint_sha256:string}>};rows:Array<{image_path:string;file_name:string;image_sha256:string;error?:string;result_a?:FlowchartExecutionResult;result_b?:FlowchartExecutionResult;difference?:{verdict_changed:boolean;roi_changed:boolean;changed_nodes:string[];verdict_a:string;verdict_b:string;reason_a:string;reason_b:string}}>}
function verifiedComparison(record:Comparison){return record.schema_version===2&&record.integrity==='verified'&&/^[a-f0-9]{64}$/.test(record.record_sha256||'');}
export function FlowWorkspacePanel({versions,models,onOpenImage,area}:{area?:'edit'|'test'|'evaluate'|'release';versions:SavedFlowVersion[];models:FlowModelCatalogItem[];onOpenImage:()=>void}) {
  const project=useProjectStore(s=>s.project);const folder=useDatasetStore(s=>s.folderPath);const task=useProjectStore(s=>s.task);const labelset=project?.active_labelset_id;const transport=useComputeStore(s=>s.transportRevision);const profile=useComputeStore(s=>s.selectedProfileId);const backendIdentity=getApiPersistenceIdentity();
  const {pipeline,replacePipeline,setSelectedImage,selectNode,isRunning}=useFlowchartStore();
  const [templates,setTemplates]=useState<Template[]>([]);const [selected,setSelected]=useState('');const [name,setName]=useState('내 검사 흐름');const [subset,setSubset]=useState<string[]>([]);
  const [mapping,setMapping]=useState<Record<string,string>>({});const [classMapping,setClassMapping]=useState<Record<string,string>>({});
  const [images,setImages]=useState<ImageMeta[]>([]);const [testImages,setTestImages]=useState<string[]>([]);const [va,setVa]=useState('');const [vb,setVb]=useState('');
  const [target,setTarget]=useState<'local'|'selected_compute'>('local');const [device,setDevice]=useState('cpu');
  const [loadedContext,setLoadedContext]=useState<string|null>(null);
  const [history,setHistory]=useState<Comparison[]>([]);const [comparison,setComparison]=useState<Comparison|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  // Test images are chosen from the validated revision and kept by identity; without an accepted revision the older
  // path list of the first 200 images is offered.
  const [testSet,setTestSet]=useState<TestImage[]>([]);const [library,setLibrary]=useState<'available'|'unavailable'>('available');
  // Saved images the current revision does not confirm stay saved (and listed) instead of vanishing after one load.
  const [unresolved,setUnresolved]=useState<UnresolvedTestImage[]>([]);
  // Whether the legacy listing answered: only then can a saved path be called unlisted (and offered for removal).
  const [listing,setListing]=useState<'loading'|'ready'|'failed'>('loading');
  const [listingRetry,setListingRetry]=useState(0);
  const [listingError,setListingError]=useState('');
  const storageKey=project?flowTestSetStorageKey({projectId:project.id,projectDir:project.project_dir,source:folder,task,labelset,computeProfileId:profile,apiIdentity:backendIdentity}):null;
  const [invalidHistory,setInvalidHistory]=useState<Array<{comparison_id:string;reason:string}>>([]);
  const [debugNode,setDebugNode]=useState('');
  const contextGeneration=getProjectContextGeneration();
  const context=JSON.stringify([storageKey,transport,contextGeneration]);
  // Plain paths saved by older versions, from both keys; offered whenever the panel falls back to the legacy list.
  const savedLegacyPaths=()=>{if(!storageKey)return [];try{return offeredLegacyPaths(localStorage,storageKey);}catch{return [];}};
  useEffect(()=>{let active=true;setLoadedContext(null);setTarget('local');setDevice('cpu');setTemplates([]);setImages([]);setHistory([]);setInvalidHistory([]);setDebugNode('');setComparison(null);setTestImages([]);setTestSet([]);setUnresolved([]);setListing('loading');setLibrary('available');setSelected('');setVa('');setVb('');setSubset([]);setError('');setNotice('');setBusy(false);
    if(project&&folder&&storageKey)Promise.all([request<{templates:Template[]}>('/api/flow-workspace/templates'),request<{comparisons:Comparison[];invalid?:Array<{comparison_id:string;reason:string}>}>('/api/flow-workspace/comparisons')]).then(async([t,h])=>{if(!active)return;setTemplates(t.templates);setHistory(h.comparisons);setInvalidHistory(h.invalid||[]);
      let raw:string|null=null;try{raw=localStorage.getItem(storageKey);}catch{}
      const {saved,legacyPaths}=parseSavedTestSet(raw);
      // asked even when only older paths are saved: the answer says whether a validated revision exists (409 = legacy list)
      try{const resolved=saved.length||legacyPaths||savedLegacyPaths().length?await api.library.resolve(saved):null;if(!active)return;if(legacyPaths){try{preserveLegacyPaths(localStorage,storageKey,raw);}catch{}}const {kept,unresolved:pending,notice:dropped}=applyResolution(saved,resolved?.results||[],legacyPaths);setTestSet(kept);setUnresolved(pending);if(dropped)setNotice(dropped);}
      catch(e){if(!active)return;
        if((e as {status?:number}).status!==409){
          // Saved identities that could not be checked are never overwritten: saving stays off until a load succeeds.
          if(saved.length)throw e;
          // Nothing but older paths is saved: keep them under their own key and let new picks be saved.
          try{preserveLegacyPaths(localStorage,storageKey,raw);}catch{}
          setError(e instanceof Error?e.message:String(e));setLoadedContext(context);return;}
        setLibrary('unavailable');setTestImages(savedLegacyPaths());}
      setLoadedContext(context);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};
  },[context]);
  // The identity set and the legacy path list are saved under separate keys, so neither mode erases the other's entries.
  useEffect(()=>{if(!storageKey||loadedContext!==context)return;try{if(library==='available')localStorage.setItem(storageKey,JSON.stringify([...testSet,...unresolved]));else localStorage.setItem(legacyStorageKey(storageKey),JSON.stringify(testImages));}catch{}},[testImages,testSet,unresolved,library,context,loadedContext]);
  useEffect(()=>{if(library!=='unavailable'||!project||!folder||!storageKey)return;let active=true;setListing('loading');setListingError('');api.dataset.getImages({folder_path:folder,task,limit:200}).then(imgs=>{if(!active)return;setImages(imgs.items);setListing('ready');}).catch(e=>{if(active){setListingError(e instanceof Error?e.message:String(e));setListing('failed');}});return()=>{active=false;};},[library,context,listingRetry]);
  // Saved older paths outside the first 200 listed images stay saved; only the listed ones are shown and compared. Until
  // the listing has answered no saved path counts as unlisted, so nothing can be removed for being "not listed".
  const listedTestImages=listing==='ready'?testImages.filter(path=>images.some(image=>image.file_path===path)):[];
  const unlistedTestImages=listing==='ready'?testImages.filter(path=>!listedTestImages.includes(path)):[];
  const testPaths=library==='available'?testSet.map(image=>image.file_path):listedTestImages;
  const current=()=>getProjectContextGeneration()===contextGeneration&&useProjectStore.getState().project?.id===project?.id&&useProjectStore.getState().project?.project_dir===project?.project_dir&&useDatasetStore.getState().folderPath===folder&&useProjectStore.getState().task===task&&useProjectStore.getState().project?.active_labelset_id===labelset&&useComputeStore.getState().transportRevision===transport&&useComputeStore.getState().selectedProfileId===profile&&getApiPersistenceIdentity()===backendIdentity;
  const action=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');setNotice('');try{await fn();}catch(e){if(current())setError(e instanceof Error?e.message:String(e));}finally{if(current())setBusy(false);}};
  const save=()=>action(async()=>{if(!project||!pipeline)return;const r=await request<Template>('/api/flow-workspace/templates',{method:'POST',body:JSON.stringify({project_id:project.id,name,pipeline,...(subset.length?{node_ids:subset}:{})})});if(current()){setTemplates(t=>[r,...t]);setNotice('템플릿을 저장했습니다. 다른 프로젝트에서 모델·클래스를 매핑해 사용할 수 있습니다.');}});
  const chosen=templates.find(t=>t.template_id===selected);
  const classFields=chosen?Object.entries(chosen.classes_by_node||{'':chosen.classes}).flatMap(([id,c])=>[...c.names.map(v=>({key:`${id?`${id}:`:''}name:${v}`,label:`${chosen.pipeline.nodes.find(n=>n.id===id)?.data.label||'전체'} · 이름 ${v}`,numeric:false})),...c.ids.map(v=>({key:`${id?`${id}:`:''}id:${v}`,label:`${chosen.pipeline.nodes.find(n=>n.id===id)?.data.label||'전체'} · ID ${v}`,numeric:true}))]):[];
  const apply=()=>action(async()=>{
    if(!project||!chosen||!pipeline)return;
    if(useFlowchartStore.getState().isRunning||useFlowchartStore.getState().isSaving||useProjectStore.getState().isProjectBusy)throw new Error('실행·저장이 끝난 뒤 템플릿을 적용하세요.');
    const original=useFlowchartStore.getState().pipeline;
    if(chosen.kind==='flow'&&!window.confirm('모델·클래스 매핑을 확인했습니다. 현재 전체 그래프를 새 초안으로 교체할까요? 저장된 버전은 유지됩니다.'))return;
    const r=await request<{kind:string;pipeline:FlowchartPipeline;ports:Template['ports']}>(`/api/flow-workspace/templates/${selected}/map`,{method:'POST',body:JSON.stringify({project_id:project.id,models:mapping,classes:classMapping})});
    const live=useFlowchartStore.getState();
    if(!current())return;  // another project or context: its graph is not this template's target
    if(live.pipeline!==original||live.isRunning||live.isSaving||useProjectStore.getState().isProjectBusy)
      throw new Error('적용하는 동안 그래프가 바뀌었거나 실행 중입니다. 현재 그래프를 확인한 뒤 다시 적용하세요.');
    if(r.kind!==chosen.kind)throw new Error('템플릿 종류가 바뀌었습니다. 다시 확인하세요.');
    const mapped={...r.pipeline,nodes:r.pipeline.nodes.map(node=>{
      if(!node.data.model_job_id)return node;
      const model=models.find(item=>item.job_id===node.data.model_job_id);
      if(!model||!compatibleModelNode(node,model))throw new Error('매핑한 모델의 현재 호환 정보를 확인하세요.');
      return {...node,data:{...node.data,...modelScoreBinding(model)}};
    })};
    replacePipeline(r.kind==='flow'?mapped:insertSubgraph(live.pipeline!,mapped,`module-${Date.now()}`));
    setNotice(r.kind==='flow'?'모델·클래스를 매핑한 초안으로 열었습니다. 연결을 확인하고 저장하세요.':`부분 흐름을 추가했습니다. 입력 ${r.ports.inputs.length}개·출력 ${r.ports.outputs.length}개 경계 포트를 기존 노드와 연결하세요.`);
  });
  const compare=()=>action(async()=>{if(!project)return;const r=await request<Comparison>('/api/flow-workspace/comparisons',{method:'POST',body:JSON.stringify({project_id:project.id,version_a:va,version_b:vb,image_paths:testPaths,device,execution_target:target,compute_profile_id:target==='selected_compute'?profile:null,stop_node_id:debugNode.trim()||null})});if(current()){setComparison(r);setHistory(h=>[r,...h]);setNotice(`동일한 고정 이미지와 원본 해시로 비교 결과를 저장했습니다. 실행 대상: ${target==='local'?'현재 백엔드':profile} · ${device}`);}});
  return <details hidden={Boolean(area&&area!=='edit'&&area!=='evaluate')} className="shrink-0 border-t border-slate-700 bg-[#131822] text-xs">
    <summary className="cursor-pointer px-4 py-2 font-semibold text-sky-200">{area==='edit'?'내 템플릿 · 모델·클래스 매핑':area==='evaluate'?'고정 테스트 세트 · 플로우 버전 A/B 비교':'고정 테스트 세트 · 플로우 버전 비교 · 내 템플릿'}</summary>
    <div className={`grid ${area?'grid-cols-1':'grid-cols-2'} gap-4 px-4 pb-3 max-h-[42vh] overflow-auto`}>
      <section hidden={area==='evaluate'} className="space-y-2"><h3 className="text-sm font-bold">재사용할 검사 흐름</h3><div className="flex gap-2"><input aria-label="템플릿 이름" value={name} onChange={e=>setName(e.target.value)} className="flex-1 rounded bg-slate-800 p-2"/><button disabled={!pipeline||busy||isRunning} onClick={save} className="rounded border border-sky-700 p-2">{subset.length?'선택 노드 저장':'전체 흐름 저장'}</button></div>
        <details><summary className="cursor-pointer text-slate-400">부분 흐름으로 저장할 노드 선택</summary><div className="flex flex-wrap gap-2 mt-2">{pipeline?.nodes.filter(n=>!['input','decision','output'].includes(n.data.node_type)).map(n=><label key={n.id}><input type="checkbox" checked={subset.includes(n.id)} onChange={e=>setSubset(s=>e.target.checked?[...s,n.id]:s.filter(x=>x!==n.id))}/> {n.data.label}</label>)}</div></details>
        <select aria-label="내 템플릿 선택" value={selected} onChange={e=>{setSelected(e.target.value);setMapping({});setClassMapping({});}} className="w-full rounded bg-slate-800 p-2"><option value="">저장한 템플릿 선택</option>{templates.map(t=><option key={t.template_id} value={t.template_id}>{t.name} · {t.kind==='flow'?'전체':'부분'} 흐름</option>)}</select>
        {chosen&&<div className="space-y-2">{chosen.models.map(m=><label className="block" key={m.node_id}>{m.name} · {m.task}<select aria-label={`${m.name} 템플릿 모델 매핑`} value={mapping[m.node_id]||''} onChange={e=>setMapping(v=>({...v,[m.node_id]:e.target.value}))} className="w-full rounded bg-slate-800 p-2"><option value="">현재 프로젝트의 호환 모델 선택</option>{models.filter(v=>v.task===m.task).map(v=><option key={v.job_id} value={v.job_id}>{v.label}</option>)}</select></label>)}{classFields.map(c=><label key={c.key} className="flex items-center gap-2">{c.label} → <input aria-label={`${c.label} 매핑`} placeholder={c.numeric?'대상 클래스 ID':'대상 클래스 이름'} value={classMapping[c.key]||''} onChange={e=>setClassMapping(v=>({...v,[c.key]:e.target.value}))} className="flex-1 rounded bg-slate-800 p-2"/></label>)}<button disabled={busy||isRunning} onClick={apply} className="rounded border border-sky-700 p-2">모델·클래스 매핑 후 초안으로 열기</button></div>}
      </section>
      <section hidden={area==='edit'} className="space-y-2"><h3 className="text-sm font-bold">같은 이미지로 저장 버전 A/B 비교</h3><div className="grid grid-cols-2 gap-2">{[[va,setVa,'A'],[vb,setVb,'B']].map(([value,setter,label])=><label key={String(label)}>버전 {String(label)}<select aria-label={`비교 버전 ${label}`} value={value as string} onChange={e=>(setter as (v:string)=>void)(e.target.value)} className="w-full rounded bg-slate-800 p-2"><option value="">저장 버전 선택</option>{versions.map(v=><option key={v.version_id} value={v.version_id}>{v.name} · {new Date(v.saved_at).toLocaleString('ko-KR')}</option>)}</select></label>)}</div>
        {library==='available'?<details><summary className="cursor-pointer">고정 테스트 이미지 {testSet.length}/{TEST_SET_LIMIT}{unresolved.length?` · 확인 필요 ${unresolved.length}`:''} · 검증된 데이터 버전에서 선택</summary>
          {testSet.length>0&&<ul aria-label="선택한 고정 테스트 이미지" className="mt-2 flex flex-wrap gap-1">{testSet.map(image=><li key={image.image_uuid}><button type="button" title="선택 해제" onClick={()=>setTestSet(set=>toggleTestImage(set,image))} className="rounded border border-sky-800 px-1.5 py-0.5 font-mono text-[10px]">{image.relative_path} ✕</button></li>)}</ul>}
          {unresolved.length>0&&<ul aria-label="확인이 필요한 고정 테스트 이미지" className="mt-2 flex flex-wrap gap-1">{unresolved.map(image=><li key={image.image_uuid}><button type="button" title="저장 목록에서 빼기" onClick={()=>setUnresolved(rows=>rows.filter(row=>row.image_uuid!==image.image_uuid))} className="rounded border border-amber-700 px-1.5 py-0.5 font-mono text-[10px] text-amber-200">{image.relative_path} · {unresolvedReason(image)} ✕</button></li>)}</ul>}
          <div className="mt-2 flex h-[360px] flex-col"><ImageLibraryBrowser selectedIds={new Set(testSet.map(image=>image.image_uuid))} initialFilters={{state:'valid'}} onPick={item=>{if(!item.valid){setNotice(`잘못된 이미지는 고정 테스트 비교에 쓸 수 없습니다: ${item.relative_path}`);return;}setNotice('');const picked=testImageFrom(item);setTestSet(set=>pickTestImage(set,unresolved,picked).testSet);setUnresolved(rows=>pickTestImage([],rows,picked).unresolved);}} onUnavailable={()=>{setTestImages(savedLegacyPaths());setLibrary('unavailable');}}/></div>
          {testSet.length+unresolved.length>=TEST_SET_LIMIT&&<p className="text-amber-200">최대 {TEST_SET_LIMIT}장까지 저장합니다(확인 필요 포함). 다른 이미지를 고르려면 선택을 해제하거나 확인 필요 항목을 빼세요.</p>}</details>
        :<details><summary className="cursor-pointer">고정 테스트 이미지 저장 {testImages.length}/20{listing==='loading'?' · 이미지 목록 확인 중':listing==='failed'?' · 이미지 목록을 불러오지 못해 저장 경로를 확인할 수 없음(저장은 유지)':unlistedTestImages.length?` · 비교 ${listedTestImages.length}장 · 목록 밖 ${unlistedTestImages.length}장`:''} · 선택 (검증된 데이터 버전이 없어 처음 200장만 보이며 경로로 기억됩니다)</summary>
          {listing==='failed'&&<div role="alert" className="mt-2 flex items-center gap-2 text-amber-300"><span>{listingError}</span>
            <button type="button" onClick={()=>setListingRetry(value=>value+1)} className="rounded border border-amber-700 px-2 py-1">테스트 이미지 목록 다시 불러오기</button></div>}
          {/* Saved paths the first 200 images do not include (deleted, renamed or further in the folder) stay saved and
              count toward the 20 places; each can be removed here, so they never lock the list. */}
          {unlistedTestImages.length>0&&<div className="mt-2 space-y-1"><div className="flex items-center gap-2 text-amber-200"><span>목록에 보이지 않는 저장 경로 {unlistedTestImages.length}장 · 비교에서 제외됩니다.</span>
            <button type="button" onClick={()=>setTestImages(paths=>paths.filter(path=>listedTestImages.includes(path)))} className="rounded border border-amber-700 px-1.5 py-0.5">모두 빼기</button></div>
            <ul aria-label="목록에 보이지 않는 저장 경로" className="flex flex-wrap gap-1">{unlistedTestImages.map(path=><li key={path}><button type="button" title="저장 목록에서 빼기" onClick={()=>setTestImages(paths=>paths.filter(item=>item!==path))} className="rounded border border-amber-700 px-1.5 py-0.5 font-mono text-[10px] text-amber-200">{path} ✕</button></li>)}</ul></div>}
          <div className="grid grid-cols-3 gap-2 max-h-44 overflow-auto mt-2">{listing==='ready'&&images.map(im=><label key={im.file_path} className="truncate"><input type="checkbox" checked={testImages.includes(im.file_path)} disabled={!testImages.includes(im.file_path)&&testImages.length>=20} onChange={e=>setTestImages(s=>e.target.checked?[...s,im.file_path]:s.filter(x=>x!==im.file_path))}/>{inspectionThumbnail(im.thumbnail_url)?<img src={resolveApiUrl(inspectionThumbnail(im.thumbnail_url)!)} alt={im.file_name} className="h-12 w-full object-contain"/>:<span className="text-slate-400">썸네일을 읽을 수 없습니다.</span>}{im.file_name}</label>)}</div></details>}
        <div className="grid grid-cols-2 gap-2"><label>비교 실행 대상<select aria-label="플로우 비교 실행 대상" value={target} onChange={e=>{setTarget(e.target.value as 'local'|'selected_compute');setDevice(e.target.value==='local'?'cpu':'cuda');}} className="w-full rounded bg-slate-800 p-2"><option value="local">현재 백엔드</option><option value="selected_compute" disabled={!profile}>선택한 계산 서버</option></select></label><label>대상 장치<select aria-label="플로우 비교 장치" value={device} onChange={e=>setDevice(e.target.value)} className="w-full rounded bg-slate-800 p-2"><option value="cpu">CPU</option><option value="cuda">CUDA GPU</option>{target==='local'&&<option value="mps">Apple MPS</option>}</select></label></div><p className="text-slate-400">선택한 대상에서 두 버전을 실행합니다. 지원하지 않는 장치는 오류로 안내합니다. 공유 백엔드에 연결했다면 그 서버를 기준으로 실행합니다.</p>
        <label className="block">선택 노드까지 부분 비교 (비우면 전체 실행)<input aria-label="비교 중단 노드 ID" value={debugNode} maxLength={200} onChange={e=>setDebugNode(e.target.value)} placeholder="두 저장 버전에 있는 노드 ID" className="ml-2 rounded bg-slate-800 p-2"/></label>
        <button disabled={busy||isRunning||!va||!vb||va===vb||!testPaths.length||(target==='selected_compute'&&!profile)} onClick={compare} className="rounded border border-sky-700 p-2">{busy?'실행 중…':'선택 대상에서 비교 실행·결과 저장'}</button>
        <select aria-label="저장 비교 결과" value={comparison?.comparison_id||''} onChange={e=>setComparison(history.find(h=>h.comparison_id===e.target.value)||null)} className="w-full rounded bg-slate-800 p-2"><option value="">이전 비교 결과 다시 열기</option>{history.map(h=><option value={h.comparison_id} key={h.comparison_id}>{new Date(h.created_at).toLocaleString('ko-KR')} · {h.rows.length}장 · {h.status} · {verifiedComparison(h)?'무결성 확인':'해시 미기록'}</option>)}</select>
        {invalidHistory.length>0&&<p role="alert" className="text-amber-200">무결성을 확인할 수 없는 저장 비교 {invalidHistory.length}개를 목록에서 제외했습니다. 원래 기록은 보존돼 있습니다.</p>}
        {comparison&&<p role="status" className="break-all text-slate-300">{verifiedComparison(comparison)?`저장 근거 무결성 확인 · ${comparison.record_sha256}`:'이전 비교 기록 · 해시 미기록 · 무결성 재검증 필요'} · 모델 품질 승인과 배포 적격성은 별도 확인하세요.</p>}
        {comparison&&<details><summary>고정 모델·입력 근거</summary><p className="break-all">입력 목록 해시: {comparison.cohort_sha256||'이전 기록: 미기록'}</p>{comparison.model_bindings?(['a','b'] as const).map(side=><div key={side}><h4>버전 {side.toUpperCase()} · 실행 범위 모델 {comparison.model_bindings![side].length}개</h4>{comparison.model_bindings![side].map(model=><p className="break-all" key={model.node_id}>{model.node_id} · {model.job_id} · {model.task} · {model.checkpoint_sha256}</p>)}</div>):<p>이전 기록: 모델 해시 미기록</p>}</details>}
        {comparison?.comparison_scope==='debug_partial'&&<p className="text-amber-200">부분 실행 · {comparison.stop_node_id}까지 · 최종 OK 판정이 아님 · 전체 플로우 평가가 별도로 필요합니다.</p>}
        {comparison?.rows.map(row=><div key={row.image_path} className="rounded border border-slate-700 p-2"><button className="text-sky-200 underline" onClick={()=>{setSelectedImage({source:'dataset',imagePath:row.image_path,fileName:row.file_name,thumbnailUrl:`/api/dataset/thumbnail/preview?file_path=${encodeURIComponent(row.image_path)}`});if(row.difference?.changed_nodes[0])selectNode(row.difference.changed_nodes[0]);onOpenImage();}}>{row.file_name}</button>{row.error?<p role="alert" className="text-amber-300">{row.error}</p>:<><p>{row.difference?.verdict_a} → {row.difference?.verdict_b} · {row.difference?.roi_changed?'ROI/영역 판정 변경':'ROI 동일'}</p><p className="text-slate-400">A: {row.difference?.reason_a}<br/>B: {row.difference?.reason_b}</p><p>변경 노드: {row.difference?.changed_nodes.join(', ')||'없음'}</p><details><summary>중간 이미지와 원본 해시</summary><p className="break-all">{row.image_sha256}</p><div className="grid grid-cols-2 gap-2">{[row.result_a,row.result_b].map((r,i)=>{const image=inspectionPreview(r?.annotated_image,undefined);return <div key={i}>{r?.debug_cache&&<p>{debugRunCacheText(r)}</p>}{r?.annotated_image&&(image?<img alt={`버전 ${i?'B':'A'} 검사 결과`} src={image} className="w-full object-contain"/>:<p className="text-slate-400">결과 이미지를 읽을 수 없습니다.</p>)}{r?.execution_steps.map(s=><p key={s.node_id}>{s.name}: {s.branch_verdict||s.status} ({s.input_count??0} → {s.output_count??0})</p>)}</div>;})}</div></details></>}</div>)}
      </section>
    </div>{error&&<p role="alert" className="px-4 pb-3 text-amber-300">{error}</p>}{notice&&<p role="status" className="px-4 pb-3 text-sky-200">{notice}</p>}
  </details>;
}
