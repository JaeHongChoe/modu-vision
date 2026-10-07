import {useEffect,useRef,useState} from 'react';
import type {PortableUpdateState,PortableUpdateReview,PortableRecoveryAction,PortableLaunchState} from '../../../types/electron';
import {host} from '../../services/hostAdapter';
const button='rounded border border-slate-600 px-3 py-2 disabled:opacity-40';
export function PortableUpdatePanel(){
 const [state,setState]=useState<PortableUpdateState|null>(null),[review,setReview]=useState<PortableUpdateReview|null>(null),[channel,setChannel]=useState<'stable'|'beta'>('stable'),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [launch,setLaunch]=useState<PortableLaunchState|null>(null),[launchRequested,setLaunchRequested]=useState(false);
 const scope=useRef(0);
 const pending=useRef(false);
 useEffect(()=>()=>{scope.current++;},[]);
 const operation=async(action:()=>Promise<void>)=>{if(pending.current)return;pending.current=true;const started=++scope.current;setBusy(true);setError('');try{await action();}catch(cause){if(scope.current===started)setError(String((cause as Error).message||cause));}finally{if(scope.current===started){pending.current=false;setBusy(false);}}};
 const showState=(value:PortableUpdateState)=>{if(!state||value.root!==state.root||value.installation_id!==state.installation_id||value.update_id!==state.update_id||value.database_fence!==state.database_fence){setLaunch(null);setLaunchRequested(false);}if(value.launch_state){setLaunch(value.launch_state);setLaunchRequested(!['absent','exited'].includes(value.launch_state.status));}setState(value);setReview(null);setConfirmed(false);};
 const running=launchRequested||!!launch&&!['absent','exited'].includes(launch.status);
 const readLaunch=async()=>{if(!state?.update_id)return;const started=scope.current;const value=await host.updates.inspectPortableLaunch({installation_id:state.installation_id,update_id:state.update_id,database_fence:state.database_fence});if(scope.current===started)setLaunch(value);};
 const recover=async(action:PortableRecoveryAction)=>{if(!state?.update_id)return;const started=scope.current;setReview(null);setConfirmed(false);const value=await host.updates.recoverPortable(action,{installation_id:state.installation_id,update_id:state.update_id});if(scope.current===started)showState(value);};
 return <section aria-label="별도 portable 앱 업데이트" className="space-y-3 rounded border border-slate-600 p-3">
  <h3 className="font-semibold">별도 portable 앱 업데이트</h3>
  <p className="text-slate-400">중지된 macOS·Linux portable 설치를 선택해 앱과 데이터를 함께 전환합니다. 이 Studio와 현재 사용자 폴더는 선택할 수 없습니다. 설치 앱과 백엔드의 배포자 서명, 고정된 릴리스 키가 필요합니다.</p>
  <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={busy||!host.can('updates')} onClick={()=>void operation(async()=>{const started=scope.current;const value=await host.updates.selectPortableHome();if(value&&scope.current===started)showState(value);})}>portable 설치 폴더 선택</button>
  <button type="button" className={button} disabled={busy||!state} onClick={()=>void operation(async()=>{if(running){await readLaunch();return;}const started=scope.current;const value=await host.updates.inspectPortable();if(scope.current===started)showState(value);})}>portable 상태 다시 읽기</button></div>
  {state&&<><p className="break-all">선택한 설치: {state.root}</p><p role="status">{state.status==='recovery_required'?'중단된 업데이트 · 복구 필요':state.status==='committed'?'앱·데이터 전환 확인됨':'업데이트 준비 확인됨'} · 버전 {state.version} · 데이터 세대 {state.database_fence}</p>
   {state.status!=='recovery_required'&&<><label className="block">portable 릴리스 채널<select aria-label="portable 릴리스 채널" className="ml-2 rounded bg-slate-800 p-2" disabled={busy||running} value={channel} onChange={e=>{setChannel(e.target.value as 'stable'|'beta');setReview(null);setConfirmed(false);}}><option value="stable">안정 버전</option><option value="beta">베타 버전</option></select></label>
   <button type="button" className={button} disabled={busy||running} onClick={()=>void operation(async()=>{setReview(null);setConfirmed(false);const started=scope.current;const value=await host.updates.previewPortable(channel);if(value&&scope.current===started)setReview(value);})}>portable 변경 내용 확인</button></>}
   {state.status==='committed'&&state.update_id&&<div className="space-y-2"><div className="flex flex-wrap gap-2">
    <button type="button" className={button} disabled={busy||running} onClick={()=>void operation(async()=>{const started=scope.current;setReview(null);setConfirmed(false);setLaunchRequested(true);const value=await host.updates.launchPortable({installation_id:state.installation_id,update_id:state.update_id!,database_fence:state.database_fence});if(scope.current===started)setLaunch(value);})}>전환한 portable 앱 시작</button>
    <button type="button" className={button} disabled={busy} onClick={()=>void operation(readLaunch)}>portable 앱 실행 상태 확인</button>
   </div>{launch&&<p role="status">{launch.status==='ready'?'앱과 백엔드의 설치 연결 확인됨':launch.status==='starting'?'앱 시작 중':launch.status==='recovery_required'?'앱 실행 확인·복구 필요':launch.status==='absent'?'앱 실행 기록 없음':launch.status==='exited'?'시작하지 않은 실행 요청 종료됨':'앱 실행 준비 중'}</p>}
   <p className="text-xs text-slate-400">실행 요청 뒤에는 상태를 다시 확인하세요. 기준 이미지 검사와 모델 품질 확인이 별도로 필요합니다.</p></div>}
   {review&&<div className="space-y-2 rounded border border-slate-600 p-3"><p>{review.current_version} → {review.version} · 배포자 {review.publisher}</p><p>{review.application_layout==='darwin-app/v2'?'macOS 앱':'portable 앱'} · 내부 연결 {review.application_link_count}개</p><p>앱 파일 {review.application_file_count}개 · 추가 팩 {review.pack_count}개 · {(review.artifact_bytes/1024/1024).toFixed(1)} MiB</p><p>원본과 이전 세대는 보존됩니다. 복사한 로그인 세션은 폐기되므로 다시 로그인해야 합니다.</p>
    <details className="break-all text-xs text-slate-400"><summary>검토 해시</summary><p>{review.plan_sha256}</p><p>{review.source_sha256}</p></details>
   </div>}
   {(review||state.allowed_recovery.length>0)&&<label className="flex gap-2"><input type="checkbox" aria-label="선택한 portable 설치의 학습·검사가 종료되고 백업을 확인했습니다" disabled={busy} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>선택한 portable 설치의 학습·검사가 종료되고 백업을 확인했습니다.</label>}
   {review&&<button type="button" className={button} disabled={busy||running||!confirmed} onClick={()=>void operation(async()=>{const selected=review,started=scope.current;setReview(null);setConfirmed(false);const value=await host.updates.applyPortable(selected.review_id);if(scope.current===started)showState(value);})}>검토한 portable 업데이트 적용</button>}
   {state.allowed_recovery.length>0&&<div className="space-y-2"><p className="text-slate-400">동일 전환을 마무리하거나, 전환 뒤 새 쓰기를 보존하는 복구를 선택합니다. 예전 버전으로 무조건 되돌리지는 않습니다.</p><div className="flex flex-wrap gap-2">{state.allowed_recovery.map(action=><button type="button" key={action} className={button} disabled={busy||running||!confirmed} onClick={()=>void operation(()=>recover(action))}>{action==='finish'?'같은 업데이트 마무리':action==='forward'?'새 쓰기 보존하며 복구':'데이터 전환 전 설치 취소'}</button>)}</div></div>}
  </>}
  {busy&&<p role="status">portable 설치 확인·전환 중…</p>}{error&&<p role="alert" className="break-all text-red-300">{error} 선택한 설치의 상태를 다시 읽은 뒤 진행하세요.</p>}
 </section>;
}
