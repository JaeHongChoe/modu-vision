import {useState} from 'react';
import {useComputeStore} from '../../stores/useComputeStore';
import {getApiPersistenceIdentity} from '../../services/api';
import {defaultTrainingScheduling,trainingSchedulingOptions} from './TrainingSchedulingSettings';

export type TrainingRuntimeOptions = {max_runtime_s?: number; queue?: boolean; priority?: number};
export function trainingRuntimeOptions(minutes: string): TrainingRuntimeOptions {
  const {max_runtime_s}=trainingSchedulingOptions({...defaultTrainingScheduling,runtimeMinutes:minutes},false);
  return max_runtime_s===undefined?{}:{max_runtime_s};
}

export function useTrainingRuntime(projectScope: string) {
  const compute=useComputeStore();
  const scope=JSON.stringify([projectScope,compute.selectedProfileId,compute.transportRevision,getApiPersistenceIdentity()]);
  const [setting,setSetting]=useState({scope,value:'',queue:true,priority:'0'});
  // A changed project/transport renders an empty budget immediately, before effects run.
  const value=setting.scope===scope?setting.value:'';
  const queue=setting.scope===scope?setting.queue:true;
  const priority=setting.scope===scope?setting.priority:'0';
  const getOptions=()=>trainingSchedulingOptions({runtimeMinutes:value,queue,priority},false);
  let error:string|null=null;
  try {getOptions();} catch(cause) {error=cause instanceof Error?cause.message:String(cause);}
  return {value,queue,priority,error,onChange:(value:string)=>setSetting({scope,value,queue,priority}),
    onQueueChange:(queue:boolean)=>setSetting({scope,value,queue,priority}),
    onPriorityChange:(priority:string)=>setSetting({scope,value,queue,priority}),getOptions};
}

export function TrainingRuntimeSettings({value,onChange,queue=true,priority='0',onQueueChange,onPriorityChange,error,disabled}:{
  value:string;onChange:(value:string)=>void;queue?:boolean;priority?:string;
  onQueueChange?:(value:boolean)=>void;onPriorityChange?:(value:string)=>void;error:string|null;disabled:boolean}) {
  return <details className="my-3 rounded border border-slate-600 bg-[#111C2A] p-3 text-sm text-slate-200">
    <summary className="cursor-pointer">학습 시간 제한·대기열</summary>
    <label className="mt-3 block">학습 시간 제한 (분)<input aria-label="학습 시간 제한 (분)" type="number" min="0.001" max="10080" step="any"
      value={value} disabled={disabled} onChange={event=>onChange(event.target.value)} placeholder="비워 두면 제한 없음"
      className="mt-1 w-full rounded border border-slate-600 bg-[#0B1520] p-2 disabled:opacity-40" /></label>
    <label className="mt-3 block">학습 대기열 우선순위<input aria-label="학습 대기열 우선순위" type="number" min="-10" max="10" step="1"
      value={priority} disabled={disabled} onChange={event=>onPriorityChange?.(event.target.value)}
      className="mt-1 w-full rounded border border-slate-600 bg-[#0B1520] p-2 disabled:opacity-40" /></label>
    <label className="mt-3 flex items-center gap-2"><input type="checkbox" aria-label="장치가 사용 중이면 대기열에 넣기" checked={queue}
      disabled={disabled} onChange={event=>onQueueChange?.(event.target.checked)} />장치가 사용 중이면 대기열에 넣기</label>
    <p className="mt-2 text-xs text-slate-400">같은 실행 위치의 전문 모델 대기열에서 높은 우선순위를 먼저 실행합니다. 다른 학습의 장치 예약도 확인합니다. 대기열을 끄면 장치 사용 중인 요청은 거절합니다.</p>
    <p className="mt-2 text-xs text-slate-400">장치 사용을 시작한 뒤 시간을 계산합니다. 초과하면 현재 작업에 취소를 요청하며, 학습기가 취소를 확인할 때까지 종료가 지연될 수 있습니다. 앱의 백엔드가 종료되거나 서버 연결이 끊기면 제한·종료 확인이 지연될 수 있습니다.</p>
    {error&&<p role="alert" className="mt-2 text-amber-300">{error}</p>}
  </details>;
}
