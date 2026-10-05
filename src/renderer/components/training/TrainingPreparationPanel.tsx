import {useEffect,useRef,useState} from 'react';
import {CheckCircle,RefreshCw} from 'lucide-react';
import {request,getApiPersistenceIdentity,getProjectContextGeneration,subscribeProjectContext} from '../../services/api';
import {host} from '../../services/hostAdapter';
import type {ModelFamily} from '../../services/modelTrainingProgram';
import type {TrainingPreset} from '../../types';
import {useProjectStore} from '../../stores/useProjectStore';
import {useDatasetStore} from '../../stores/useDatasetStore';
import {useComputeStore} from '../../stores/useComputeStore';
import {familyPreparation} from './trainingWorkflow';
import {programButton} from './ProgramWorkbenchControls';
import {TeamTrainingReadiness} from './TeamTrainingReadiness';

type Readiness={ready:boolean;model:string;execution_verified:boolean;dependencies:{missing:string[]};runtime:{available:boolean;device:string;reason:string};weights:{state:string;filename?:string;content_verified:boolean};next_actions:string[];selected_checkpoint_path?:string|null};
export function TrainingPreparationPanel({family,model,preset='fast',checkpoint='',device='cpu',config={},datasetPath,warmStartJobId,onCheckpointChange}:{family:ModelFamily;model?:string;preset?:TrainingPreset;checkpoint?:string;device?:'cpu'|'cuda'|'mps';config?:Record<string,unknown>;datasetPath?:string;warmStartJobId?:string;onCheckpointChange?:(path:string)=>void}) {
  const {project,projectDir,setStep}=useProjectStore();const dataset=useDatasetStore();const compute=useComputeStore();
  const [checked,setChecked]=useState<{scope:string;value:Readiness}|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const [epoch,setEpoch]=useState(getProjectContextGeneration);
  const generation=getProjectContextGeneration(),identity=getApiPersistenceIdentity();
  const source=project?.source_dataset_dir || '';const labelset=project?.active_labelset_id || 'default';
  const scope=`${epoch}\n${generation}\n${projectDir}\n${source}\n${labelset}\n${identity}\n${compute.transportRevision}\n${compute.selectedProfileId}\n${family}\n${model}\n${preset}\n${checkpoint}\n${device}\n${datasetPath}\n${warmStartJobId}\n${JSON.stringify(config)}`;
  const check=checked?.scope===scope?checked.value:null;
  const current=useRef(scope);current.current=scope;const prep=familyPreparation[family];
  const same=()=>current.current===scope&&getProjectContextGeneration()===generation&&getApiPersistenceIdentity()===identity&&useProjectStore.getState().projectDir===projectDir&&useProjectStore.getState().project?.source_dataset_dir===source&&(useProjectStore.getState().project?.active_labelset_id||'default')===labelset&&useComputeStore.getState().transportRevision===compute.transportRevision&&useComputeStore.getState().selectedProfileId===compute.selectedProfileId;
  useEffect(()=>subscribeProjectContext(()=>setEpoch(getProjectContextGeneration())),[]);
  const inspect=async()=>{
    if(!same())return;
    setBusy(true);setError('');setChecked(null);
    try {
      let selected=model;
      if(!selected){const catalog=await request<{families:Array<{task:string;default_architecture:string}>}>('/api/models/capabilities');selected=catalog.families.find(row=>row.task===family)?.default_architecture;}
      if(!same())return;
      if(!selected)throw new Error('선택 모델의 구조를 확인하지 못했습니다.');
      const result=await request<Readiness>('/api/training-workspace/readiness',{method:'POST',body:JSON.stringify({task:family,model:selected,preset,device,config_overrides:config,...(warmStartJobId?{warm_start_job_id:warmStartJobId}:{}),...(datasetPath?{family_dataset_path:datasetPath}:{}),...(compute.selectedProfileId?{compute_profile_id:compute.selectedProfileId}:{}),...(checkpoint?{pretrained_checkpoint:checkpoint}:{})})});
      if(same()){setChecked({scope,value:result});if(result.selected_checkpoint_path&&result.selected_checkpoint_path!==checkpoint)onCheckpointChange?.(result.selected_checkpoint_path);}
    }catch(cause){if(same())setError(cause instanceof Error?cause.message:String(cause));}
    finally{if(same())setBusy(false);}
  };
  const importFile=async()=>{
    if(!same())return;
    setBusy(true);setError('');
    try {
      if(!host.can('pickPaths'))throw new Error('데스크톱 앱의 파일 선택 기능을 사용하거나 아래 상세 경로 설정을 사용하세요.');
      const shared=await host.shared.connection();
      if(!same())return;
      if(shared)throw new Error('공유 서버에서는 서버 저장소의 가중치 경로를 상세 설정에 지정하세요. 로컬 파일을 서버 경로로 사용하지 않습니다.');
      const path=await host.selectFile({title:'공식 사전학습 가중치 가져오기',filters:[{name:'사전학습 파일',extensions:['pt','pth','safetensors']}]});
      if(!path||!same())return;
      const result=await request<{pretrained_checkpoint:string;sha256:string;content_verified:boolean}>('/api/training-workspace/import-weights',{method:'POST',body:JSON.stringify({task:family,model:check?.model||model,preset,device,pretrained_checkpoint:path})});
      if(same()){onCheckpointChange?.(result.pretrained_checkpoint);setChecked(null);}
    }catch(cause){if(same())setError(cause instanceof Error?cause.message:String(cause));}
    finally{if(same())setBusy(false);}
  };
  useEffect(()=>{setChecked(null);setError('');setBusy(false);},[scope]);
  return <section aria-label="공통 모델 준비" className="rounded-xl border border-slate-600 bg-[#101A28] p-4 text-sm text-slate-200">
    <h2 className="flex items-center gap-2 font-semibold"><CheckCircle className="h-4 w-4 text-cyan-300" />{prep.label} 준비</h2>
    <ol className="mt-3 grid gap-2 md:grid-cols-3">
      <li className="rounded border border-slate-700 p-3"><strong>1. 프로젝트 이미지</strong><p className="mt-1 text-slate-300">{source?`${project?.name} · 원본 ${dataset.sourceImages||dataset.totalImages}장 · 학습 대상 ${dataset.totalImages}장`:'프로젝트에 원본 이미지를 연결하세요.'}</p><button type="button" onClick={()=>void setStep(1)} className={`${programButton} mt-2`}>데이터 확인</button></li>
      <li className="rounded border border-slate-700 p-3"><strong>2. 정답·시험 분리</strong><p className="mt-1 text-slate-300">{prep.truth}</p><button type="button" onClick={()=>void setStep(2)} className={`${programButton} mt-2`}>정답 검토</button></li>
      <li className="rounded border border-slate-700 p-3"><strong>3. 모델·실행 환경</strong><p className="mt-1 text-slate-300">{compute.selectedProfileId?`새 학습: ${compute.profiles.find(row=>row.id===compute.selectedProfileId)?.name || '선택 서버'}`:`이 컴퓨터 · ${device.toUpperCase()}`}</p><button type="button" disabled={busy||!source} onClick={()=>void inspect()} className={`${programButton} mt-2`}><RefreshCw className="mr-1 inline h-3 w-3" />{busy?'확인 중…':compute.selectedProfileId?'서버 준비 검사':'로컬 준비 검사'}</button></li>
    </ol>
    <TeamTrainingReadiness />
    <p className="mt-3 text-slate-300">다음 행동: {prep.route}</p><p className="mt-1 text-slate-400">평가: {prep.evaluation}</p>
    {compute.selectedProfileId&&<p className="mt-2 text-slate-400">선택 서버의 모델별 준비 상태를 확인합니다. 학습 작업을 등록하거나 모델을 다운로드하지 않습니다.</p>}
    {check&&<div role="status" className="mt-3 rounded border border-slate-600 p-3"><strong className={check.ready?'text-emerald-300':'text-amber-200'}>{check.ready?'준비 항목 확인됨':'준비 필요'}</strong><p className="mt-1">장치: {check.runtime.available?check.runtime.device:check.runtime.reason} · 가중치: {({file_available:'파일 있음 · 학습 시 구조 검증',not_required:'별도 사전학습 파일 불필요',transfer_ready:'프로젝트 파일 해시 확인 · 학습 시 서버로 전송',server_file_available:'서버 캐시 파일 있음 · 학습 시 구조 검증',parent_verified:'부모 모델 해시·호환 구조 확인'} as Record<string,string>)[check.weights.state]||'준비 필요'}</p>{check.dependencies.missing.length>0&&<p className="mt-1 text-amber-200">설치 필요: {check.dependencies.missing.join(', ')}</p>}{check.weights.state==='missing'&&<p className="mt-1">공식 사전학습 파일을 준비하고 아래 상세 설정에서 가져오세요. 접근 승인이 필요한 모델은 제공처 승인 절차를 완료하세요.</p>}{check.next_actions.length>0&&<ul className="mt-2 list-disc pl-5 text-amber-200">{check.next_actions.map(action=><li key={action}>{action}</li>)}</ul>}<p className="mt-1 text-slate-400">실제 학습·추론 실행과 모델 품질 승인은 아직 확인하지 않았습니다.</p></div>}
    {onCheckpointChange&&<button type="button" disabled={busy||!(check?.model||model)} onClick={()=>void importFile()} className={`${programButton} mt-3`}>사전학습 파일 선택·프로젝트에 가져오기</button>}
    {check?.dependencies.missing.length ? <details className="mt-3 rounded border border-slate-600 p-3"><summary className="cursor-pointer text-slate-300">실행 환경 준비 방법</summary><p className="mt-2">패키지된 앱은 필요한 의존성을 포함한 앱 버전을 설치하세요. 개발·서버 실행은 앱에 설정된 Python 환경에 아래 패키지를 설치한 뒤 준비 검사를 다시 실행하세요. 공유·원격 환경은 서버 관리 화면의 연결 검사에서 해당 Python/Docker 환경을 확인하세요.</p><code className="mt-2 block break-all text-slate-200">python -m pip install {check.dependencies.missing.map(name=>name==='PIL'?'Pillow':name).join(' ')}</code><button type="button" onClick={()=>void host.openLink('https://pytorch.org/get-started/locally/')} className={`${programButton} mt-2`}>PyTorch 공식 설치 안내</button></details>:null}
    {error&&<p role="alert" className="mt-3 text-rose-200">{error}</p>}
  </section>;
}
