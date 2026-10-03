import {useEffect,useRef,useState} from 'react';
import {request} from '../../services/api';

type Preflight={kind:string;platform:string;registration_prerequisites_passed:boolean;session0_acceptance:string;hardware_acceptance:string;checks:Record<string,{passed:boolean;requirement:string;evidence:string}>};
const button='rounded border border-slate-600 px-3 py-2 disabled:opacity-40';
const labels:Record<string,string>={elevation:'관리자 등록 권한',service_account:'전용 서비스 계정',programdata_acl:'ProgramData 접근 보호',project_access:'프로젝트 접근',network_credentials:'서비스 계정의 네트워크 접근',session0_gpu_camera_readiness:'로그인 전 GPU·카메라 실행'};

export function WindowsServiceSetupPanel({scopeKey,approved}:{scopeKey:string;approved:boolean}) {
  const [account,setAccount]=useState(''),[network,setNetwork]=useState(false),[warmup,setWarmup]=useState('');
  const [preflight,setPreflight]=useState<Preflight|null>(null),[prepared,setPrepared]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const generation=useRef(0);
  useEffect(()=>{generation.current++;setAccount('');setNetwork(false);setWarmup('');setPreflight(null);setPrepared(false);setBusy(false);setError('');setNotice('');},[scopeKey]);
  const change=()=>{generation.current++;setPreflight(null);setPrepared(false);setNotice('');setBusy(false);};
  const run=async(action:'preflight'|'prepare'|'activate')=>{
    const started=generation.current;setBusy(true);setError('');setNotice('');
    try {
      const result=await request<Preflight & {status?:string}>('/api/runtime-services/scm/'+action,{method:'POST',...(action==='activate'?{}:{body:JSON.stringify({service_account:account.trim(),network_required:network,warmup_image:warmup.trim()||null})})});
      if(started!==generation.current)return;
      if(action!=='activate')setPreflight(result);
      if(action==='prepare'){setPrepared(true);setNotice('서비스 등록 파일을 준비했습니다. OS 등록은 별도 실행입니다.');}
      if(action==='activate')setNotice('SCM 등록 응답을 확인했습니다. 로그인 전 부팅과 GPU·카메라 실행은 대상 장비에서 별도로 검증하세요.');
    } catch(cause){if(started===generation.current)setError(cause instanceof Error?cause.message:String(cause));}
    finally{if(started===generation.current)setBusy(false);}
  };
  return <details className="mt-3 space-y-2 rounded border border-slate-600 p-3" aria-label="Windows 시스템 검사 서비스"><summary className="cursor-pointer font-semibold">Windows SCM · 로그인 전 검사 서비스</summary>
    <p className="mt-2 text-slate-400">승인한 패키지와 전용 서비스 계정이 필요합니다. 등록에는 관리자 권한이 필요하며, Studio의 일반 사용자 실행과 로그인 자동 시작 작업은 별도입니다.</p>
    <div className="flex flex-wrap items-center gap-2"><input aria-label="Windows SCM 전용 계정" placeholder="NT SERVICE\\서비스 이름 또는 DOMAIN\\gMSA$" className="min-w-72 rounded bg-slate-800 p-2" value={account} disabled={busy} onChange={e=>{change();setAccount(e.target.value);}}/><input aria-label="Windows SCM GPU 준비 이미지" placeholder="GPU 준비 검증 이미지의 전체 경로" className="min-w-72 rounded bg-slate-800 p-2" value={warmup} disabled={busy} onChange={e=>{change();setWarmup(e.target.value);}}/><label><input type="checkbox" checked={network} disabled={busy} onChange={e=>{change();setNetwork(e.target.checked);}}/> 네트워크 장비 접근 필요</label></div>
    <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={busy||!account.trim()} onClick={()=>void run('preflight')}>등록 조건 확인</button><button type="button" className={button} disabled={busy||!approved||!account.trim()} onClick={()=>void run('prepare')}>SCM 등록 파일 준비</button><button type="button" className={button} disabled={busy||!approved||!prepared||!preflight?.registration_prerequisites_passed} onClick={()=>void run('activate')}>관리자 권한으로 SCM 등록 실행</button></div>
    {preflight&&<ul>{Object.entries(preflight.checks).map(([key,value])=><li key={key} className={value.passed?'text-emerald-300':'text-amber-200'}>{labels[key]||key}: {value.passed?'조건 확인':'추가 확인 필요'} · {value.requirement}</li>)}</ul>}
    {busy&&<p role="status">서비스 등록 조건 확인 중…</p>}{notice&&<p role="status" className="text-emerald-300">{notice}</p>}{error&&<p role="alert" className="text-red-300">{error}</p>}
  </details>;
}
